"""Tick engine: ties config, curve, overrides, and the display backend together.

PURE core: stdlib (`datetime`, `zoneinfo`) + duskcurve.color / duskcurve.backend /
duskcurve.schedule / duskcurve.daemon.config / duskcurve.daemon.overrides. No
gi/D-Bus/GLib import in this module.
"""

import logging
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from duskcurve.backend import MutterBackend
from duskcurve.color import build_ramp, kelvin_to_mired, mired_to_kelvin
from duskcurve.daemon.config import Config, DisplayConfig
from duskcurve.daemon.overrides import apply_override
from duskcurve.daemon.rules import NO_ACTION, RuleAction, WindowState, resolve_rules
from duskcurve.schedule import (
    MODES,
    ClockAnchor,
    Curve,
    DayContext,
    EventAnchor,
    Keypoint,
    solar_events,
)
from duskcurve.theme import DARK, LIGHT, ThemeVariant, desired_mode

log = logging.getLogger("duskcurve.daemon")

NEUTRAL_KELVIN = 6500.0

# Kelvin gap below which a display counts as having reached its target,
# ending a non-schedule-paced transition and resetting pacing back to
# "schedule". Well under a perceptible step at any temperature.
_CONVERGED_KELVIN = 1.0

# A ramp is only re-sent when its temperature moved by at least this much
# since the last push (well under a perceptible step), or when REPUSH_AFTER
# has passed. Every SetCrtcGamma costs gnome-shell a KMS gamma commit, so
# re-sending an identical ramp every tick is the daemon's main idle cost.
_PUSH_KELVIN = 0.5
# Unchanged ramps are still re-sent this often, to self-heal a gamma table
# reset behind the daemon's back (resume, VT switch, another tool).
REPUSH_AFTER = timedelta(seconds=60)

_EVENT_ANCHORS = frozenset({"sunrise", "sunset", "wake", "bedtime"})


def _parse_anchor(anchor: str, offset_min: int) -> EventAnchor | ClockAnchor:
    if anchor in _EVENT_ANCHORS:
        return EventAnchor(anchor, offset_min)
    hour_str, minute_str = anchor.split(":")
    return ClockAnchor(int(hour_str), int(minute_str))


def _hhmm(value: str) -> tuple[int, int]:
    hour_str, minute_str = value.split(":")
    return int(hour_str), int(minute_str)


class Orchestrator:
    def __init__(
        self,
        backend: MutterBackend,
        config: Config,
        clock: Callable[[], datetime],
        theme_applier=None,
    ) -> None:
        self._backend = backend
        self._config = config
        self._clock = clock
        self._curve = self._build_curve(config)
        self._current_k: dict[str, float] = {}
        self._last_tick_dt: datetime | None = None
        self._paused = False
        # connector -> (kelvin, expiry); connector "" applies to all connectors.
        self._manual: dict[str, tuple[float, datetime | None]] = {}
        self._window: WindowState | None = None
        self._unfocused: tuple[WindowState, ...] = ()
        self._prev_actions: dict[str, RuleAction] = {}
        # Desktop theme. `_theme_mode` is the mode this daemon last APPLIED, not
        # the mode that is desired: None means "nothing applied yet", which is
        # what makes the first tick unconditional (and therefore what makes a
        # daemon started at 23:00 correct at 23:00:02 instead of at sunrise).
        self._theme_applier = theme_applier
        self._theme_mode: str | None = None
        self._theme_manual: tuple[str, datetime | None] | None = None
        # Every temperature change eases toward its target, but the pace
        # depends on WHAT caused the change, not just that it changed:
        # "schedule" (curve drift, the default/fallback), "manual" (a
        # deliberate temp set/clear), "toggle" (pause/unpause -- hardcoded
        # instant; display enable/disable needs no entry at all, see
        # set_config), or "rule" (a window rule engaging or releasing).
        # Set when the cause happens, cleared back to "schedule"
        # once the display converges on its target, so a multi-tick
        # transition keeps its own rate the whole way instead of reverting to
        # the schedule's rate after the first tick.
        self._pacing: dict[str, str] = {}
        # connector -> (kelvin, ramp, pushed_at) of the last SUCCESSFUL push.
        self._pushed: dict[str, tuple[float, tuple[list[int], list[int], list[int]], datetime]] = {}

    def set_config(self, config: Config) -> None:
        # Note: a display's enabled flag needs no pacing entry. `enabled` is an
        # output mask applied after easing (see tick), not a temperature
        # target, so both directions are already instant and the display's
        # eased position keeps tracking the schedule while it is off.
        if config.theme != self._config.theme:
            # Clearing the applied-mode memory makes the next tick re-apply. Without
            # it, editing the dark variant while already dark would silently do
            # nothing until the next sunrise.
            #
            # Deliberately whole-section, not per-variant: editing the LIGHT
            # variant while DARK is active still clears _theme_mode and re-applies
            # dark on the next tick, overwriting a theme the user may have
            # hand-picked in between. Narrowing this to "only clear if the ACTIVE
            # variant changed" would dodge that, but the README already advertises
            # "any [theme] edit re-applies immediately" and a narrower rule is a
            # bigger surprise: a user watching the GUI expects a Save to take
            # effect now, not "maybe, depending on which variant is active
            # tonight". See finding 6 -- do not change this behaviour.
            self._theme_mode = None
        self._config = config
        self._curve = self._build_curve(config)

    def set_paused(self, paused: bool) -> None:
        # Pausing/unpausing is a binary on/off action, not a temperature
        # target -- there is no useful in-between rate, so it is hardcoded to
        # instant ("toggle" pacing, 0 seconds) rather than exposed as a config
        # knob. Do not "fix" this into a configurable duration.
        if paused != self._paused:
            self._mark_pacing_all("toggle")
        self._paused = paused

    def set_manual_temp(self, connector: str, kelvin: int, duration_min: int) -> None:
        if kelvin <= 0:
            raise ValueError(f"kelvin must be positive, got {kelvin}")
        # duration_min <= 0 means "until explicitly cleared": store no expiry at
        # all. Previously this computed an expiry of `now`, which expired on the
        # very next read and made the call a no-op.
        expiry = None if duration_min <= 0 else self._clock() + timedelta(minutes=duration_min)
        self._manual[connector] = (float(kelvin), expiry)
        # A deliberate manual set eases at manual_transition_seconds -- quicker
        # than the schedule's drift, but not instant. This must NOT be
        # confused with a manual temp expiring on its own inside
        # _manual_kelvin, which is not a deliberate action and stays at
        # schedule pacing (nothing marks it here).
        self._mark_pacing(connector, "manual")

    def clear_manual_temp(self, connector: str) -> None:
        self._manual.pop(connector, None)
        # Explicitly clearing (e.g. pressing Resume) is just as deliberate as
        # setting one, so it gets the same manual pacing back to the curve.
        self._mark_pacing(connector, "manual")

    def set_theme(self, mode: str, duration_min: int) -> None:
        if mode not in (LIGHT, DARK):
            raise ValueError(f"theme mode must be {LIGHT!r} or {DARK!r}, got {mode!r}")
        # duration_min <= 0 means "until explicitly cleared": store no expiry at
        # all, the same convention (and the same past bug) as set_manual_temp.
        expiry = None if duration_min <= 0 else self._clock() + timedelta(minutes=duration_min)
        self._theme_manual = (mode, expiry)

    def clear_theme(self) -> None:
        self._theme_manual = None

    def _mark_pacing(self, connector: str, category: str) -> None:
        if connector == "":
            self._mark_pacing_all(category)
        else:
            self._pacing[connector] = category

    def _mark_pacing_all(self, category: str) -> None:
        for display in self._backend.list_displays():
            self._pacing[display.connector] = category

    def set_window_state(
        self, window: WindowState | None, unfocused: Sequence[WindowState] = ()
    ) -> None:
        """Store the focused/fullscreen window state, plus any unfocused fullscreen
        windows elsewhere on the desktop. Does not tick; the caller must."""
        self._window = window
        self._unfocused = tuple(unfocused)

    def on_monitors_changed(self) -> None:
        # A hotplug or mode change can reset a CRTC's gamma table (and change
        # its ramp size), so the next tick must re-send everything.
        self._pushed.clear()
        connectors = {d.connector for d in self._backend.list_displays()}
        for connector in list(self._current_k):
            if connector not in connectors:
                del self._current_k[connector]
        for state in (self._prev_actions, self._pacing, self._manual):
            for connector in list(state):
                # "" is the all-displays manual key, not a real connector, so
                # it must survive; a per-connector override must not, or it
                # silently re-applies when a monitor of that name comes back.
                if connector != "" and connector not in connectors:
                    del state[connector]

    def tick(self) -> None:
        now = self._clock()
        dt = 0.0 if self._last_tick_dt is None else (now - self._last_tick_dt).total_seconds()
        ctx = self._day_context(now)

        displays = self._backend.list_displays()
        connectors = [d.connector for d in displays]
        actions = resolve_rules(
            self._window,
            self._config.rules,
            connectors,
            day_kelvin=self._config.max_kelvin,
            unfocused=self._unfocused,
        )

        for display in displays:
            connector = display.connector
            target_k = self._raw_target(connector, now, ctx, actions)

            # A change in the resolved action -- in either direction, rule
            # engaging or releasing -- starts a rule-paced transition.
            action = actions.get(connector, NO_ACTION)
            if action != self._prev_actions.get(connector, NO_ACTION):
                self._pacing[connector] = "rule"
            self._prev_actions[connector] = action

            category = self._pacing.get(connector, "schedule")
            ts = {
                "schedule": self._config.transition_seconds,
                "manual": self._config.manual_transition_seconds,
                "rule": self._config.rule_transition_seconds,
                "toggle": 0.0,
            }[category]
            # Clamped at both ends. The upper end caps a long tick at "arrive".
            # The lower end matters when the wall clock steps BACKWARDS (an NTP
            # correction, resume from suspend): dt goes negative, and an
            # unclamped negative factor eases away from the target and through
            # mired's singularity at 0, yielding negative Kelvin. A tick that
            # went backwards in time should simply hold.
            factor = 1.0 if ts <= 0 else max(0.0, min(1.0, dt / ts))

            if connector not in self._current_k:
                current_k = target_k
            else:
                current_m = kelvin_to_mired(self._current_k[connector])
                target_m = kelvin_to_mired(target_k)
                current_m += (target_m - current_m) * factor
                current_k = mired_to_kelvin(current_m)

            if abs(current_k - target_k) <= _CONVERGED_KELVIN:
                self._pacing[connector] = "schedule"

            self._current_k[connector] = current_k
            applied_k = self._apply_enabled_mask(connector, current_k)
            self._push(connector, display.ramp_size, applied_k, now)

        self._last_tick_dt = now
        self._tick_theme(now, ctx)

    def _push(self, connector: str, ramp_size: int, kelvin: float, now: datetime) -> None:
        last = self._pushed.get(connector)
        if last is not None and abs(kelvin - last[0]) < _PUSH_KELVIN:
            since = now - last[2]
            # A negative gap is a wall clock stepped backwards: re-push.
            if timedelta(0) <= since < REPUSH_AFTER:
                return
            kelvin, ramp = last[0], last[1]
        else:
            ramp = build_ramp(ramp_size, kelvin)
        self._backend.apply_ramp(connector, *ramp)
        self._pushed[connector] = (kelvin, ramp, now)

    def _theme_manual_mode(self, now: datetime) -> str | None:
        """The manual theme override in force, evicting it if it has expired."""
        entry = self._theme_manual
        if entry is None:
            return None
        mode, expiry = entry
        if expiry is None or now < expiry:
            return mode
        self._theme_manual = None
        return None

    def _tick_theme(self, now: datetime, ctx: DayContext) -> None:
        theme = self._config.theme
        if self._theme_applier is None or not theme.enabled:
            return
        # Paused FREEZES the theme rather than neutralizing it: neutral gamma is a
        # meaningful "filter off" state, but there is no neutral theme. _theme_mode
        # is left untouched, so unpausing re-evaluates and corrects if the day moved on.
        if self._paused:
            return

        mode = self._theme_manual_mode(now)
        if mode is None:
            mode = desired_mode(
                now,
                ctx,
                _parse_anchor(theme.light_from, theme.light_offset_min),
                _parse_anchor(theme.dark_from, theme.dark_offset_min),
            )
        if mode == self._theme_mode:
            return

        variant: ThemeVariant = theme.light if mode == LIGHT else theme.dark
        # The applier logs and reports its own skipped elements; a partly-applied
        # theme still counts as applied, or every tick would retry forever.
        skipped = self._theme_applier.apply(variant)
        if skipped:
            log.warning("theme applied with skipped elements: %s", skipped)
        self._theme_mode = mode

    def status(self) -> dict:
        # Side effect worth knowing about: the per-display manual lookup goes
        # through _manual_entry, which evicts expired entries from
        # self._manual. So this read path mutates state. It is benign -- the
        # same entry would be evicted by the next tick regardless -- but it
        # means status() is not a pure observer.
        now = self._clock()
        displays = self._backend.list_displays()
        actions = resolve_rules(
            self._window,
            self._config.rules,
            [d.connector for d in displays],
            day_kelvin=self._config.max_kelvin,
            unfocused=self._unfocused,
        )

        per_display = []
        for display in displays:
            connector = display.connector
            dc = self._config.displays.get(connector, DisplayConfig())
            # Report what the display is actually showing, not its masked-off
            # eased position -- the GUI renders this as the live temperature.
            current_k = self._apply_enabled_mask(
                connector, self._current_k.get(connector, NEUTRAL_KELVIN)
            )
            action = actions.get(connector, NO_ACTION)
            # Three distinct states the GUI must be able to tell apart: no
            # override (both null), an override that never expires (kelvin set,
            # expiry null), and a timed one (both set).
            manual = self._manual_entry(connector, now)
            manual_expires_in_s = None
            if manual is not None and manual[1] is not None:
                manual_expires_in_s = max(0, round((manual[1] - now).total_seconds()))
            per_display.append(
                {
                    "connector": connector,
                    "current_k": round(current_k),
                    "enabled": dc.enabled,
                    "override": dc.override_kind,
                    "rule_action": {"kind": action.kind, "kelvin": action.kelvin},
                    "manual_k": None if manual is None else round(manual[0]),
                    "manual_expires_in_s": manual_expires_in_s,
                }
            )
        # _theme_manual_mode evicts an expired override as a side effect (same
        # pattern as _manual_entry above), which matters here: _tick_theme
        # returns at the pause guard before it runs, so while paused a status()
        # that read self._theme_manual raw would report a stale "manual: light"
        # forever instead of the None the GUI expects. See finding 5.
        manual_theme_mode = self._theme_manual_mode(now)
        manual_theme_expires_in_s = None
        if manual_theme_mode is not None and self._theme_manual is not None:
            expiry = self._theme_manual[1]
            if expiry is not None:
                manual_theme_expires_in_s = max(0, round((expiry - now).total_seconds()))

        # "windows" is additive alongside "window": the focused entry (if any)
        # comes first with focused=True, then each unfocused fullscreen entry
        # with focused=False. "window" itself keeps its exact current shape.
        windows = []
        if self._window is not None:
            windows.append(
                {
                    "app_id": self._window.app_id,
                    "fullscreen": self._window.fullscreen,
                    "focused": True,
                    "monitor_connector": self._window.monitor_connector,
                }
            )
        for w in self._unfocused:
            windows.append(
                {
                    "app_id": w.app_id,
                    "fullscreen": w.fullscreen,
                    "focused": False,
                    "monitor_connector": w.monitor_connector,
                }
            )

        return {
            "mode": self._config.mode,
            "paused": self._paused,
            "per_display": per_display,
            "next_transition": None,
            "window": None
            if self._window is None
            else {
                "app_id": self._window.app_id,
                "fullscreen": self._window.fullscreen,
                "monitor_connector": self._window.monitor_connector,
            },
            "windows": windows,
            "theme": {
                "enabled": self._config.theme.enabled,
                "mode": self._theme_mode,
                "manual": manual_theme_mode,
                "manual_expires_in_s": manual_theme_expires_in_s,
            },
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_curve(self, cfg: Config) -> Curve:
        if cfg.mode in MODES:
            return MODES[cfg.mode]
        keypoints = [
            Keypoint(
                anchor=_parse_anchor(kp.anchor, kp.offset_min),
                kelvin=kp.kelvin,
                easing=kp.easing,
            )
            for kp in cfg.custom_keypoints
        ]
        return Curve(keypoints)

    def _day_context(self, now: datetime) -> DayContext:
        cfg = self._config
        tz = ZoneInfo(cfg.location.timezone)
        day = now.date()
        ev = solar_events(cfg.location.latitude, cfg.location.longitude, day, tz)
        wake_h, wake_m = _hhmm(cfg.wake)
        bed_h, bed_m = _hhmm(cfg.bedtime)
        wake = datetime(day.year, day.month, day.day, wake_h, wake_m, tzinfo=tz)
        bedtime = datetime(day.year, day.month, day.day, bed_h, bed_m, tzinfo=tz)
        return DayContext(sunrise=ev.sunrise, sunset=ev.sunset, wake=wake, bedtime=bedtime)

    def _apply_enabled_mask(self, connector: str, kelvin: float) -> float:
        """Blank a disabled display to neutral without disturbing its easing.

        `enabled` is a mask over the output, deliberately NOT part of the
        eased target: a disabled display's `_current_k` keeps tracking the
        schedule as if it were on. That is what makes re-enabling mid-
        transition rejoin the other displays where they are now, instead of
        snapping to the transition's endpoint and sitting at a different
        temperature until they catch up.
        """
        dc = self._config.displays.get(connector, DisplayConfig())
        return kelvin if dc.enabled else NEUTRAL_KELVIN

    def _manual_entry(self, connector: str, now: datetime) -> tuple[float, datetime | None] | None:
        """The manual override in force for `connector`, with its expiry.

        A per-connector entry beats the global ("") one. Evicts entries it
        finds expired -- the side effect `status` documents.
        """
        for key in (connector, ""):
            entry = self._manual.get(key)
            if entry is None:
                continue
            kelvin, expiry = entry
            if expiry is None or now < expiry:
                return kelvin, expiry
            del self._manual[key]
        return None

    def _manual_kelvin(self, connector: str, now: datetime) -> float | None:
        entry = self._manual_entry(connector, now)
        return None if entry is None else entry[0]

    def _raw_target(
        self, connector: str, now: datetime, ctx: DayContext, actions: dict[str, RuleAction]
    ) -> float:
        if self._paused:
            return NEUTRAL_KELVIN

        # dc.enabled is deliberately not consulted here -- it masks the output
        # after easing, see _apply_enabled_mask.
        dc = self._config.displays.get(connector, DisplayConfig())

        manual = self._manual_kelvin(connector, now)
        if manual is not None:
            return manual

        action = actions.get(connector, NO_ACTION)
        if action.kind == "disable":
            return NEUTRAL_KELVIN
        if action.kind == "fixed":
            assert action.kelvin is not None  # "fixed" actions always carry a kelvin
            return action.kelvin

        base = self._curve.evaluate(now, ctx)
        return apply_override(base, dc.override_kind, dc.override_value)
