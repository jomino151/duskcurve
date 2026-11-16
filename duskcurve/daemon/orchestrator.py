"""Tick engine: ties config, curve, overrides, rules and the display backend together.

No gi, D-Bus or GLib imports, so it runs against a fake backend and a fake clock.
"""

import logging
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from duskcurve.backend import MutterBackend, MutterError
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

# Kelvin gap below which a display has reached its target and pacing resets to "schedule".
_CONVERGED_KELVIN = 1.0

# A ramp is re-sent only when its temperature moved this much or REPUSH_AFTER has
# passed: every SetCrtcGamma costs gnome-shell a KMS gamma commit.
_PUSH_KELVIN = 0.5
# Unchanged ramps are still re-sent this often, to recover from a gamma table reset
# behind our back (resume, VT switch, another tool).
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
        # Desktop theme. `_theme_mode` is the mode last applied, not the desired one;
        # None (nothing applied yet) makes the first tick apply unconditionally.
        self._theme_applier = theme_applier
        self._theme_mode: str | None = None
        self._theme_manual: tuple[str, datetime | None] | None = None
        # Easing pace per connector: "schedule" (default), "manual", "toggle" (instant) or
        # "rule". Reset to "schedule" on convergence, so a multi-tick transition keeps its rate.
        self._pacing: dict[str, str] = {}
        # connector -> (kelvin, ramp, pushed_at) of the last SUCCESSFUL push.
        self._pushed: dict[str, tuple[float, tuple[list[int], list[int], list[int]], datetime]] = {}

    def set_config(self, config: Config) -> None:
        # `enabled` needs no pacing entry: it masks the output after easing.
        if config.theme != self._config.theme:
            # Forget the applied mode so the next tick re-applies. Whole-section on purpose: any
            # [theme] edit, even to the inactive variant, takes effect immediately.
            self._theme_mode = None
        self._config = config
        self._curve = self._build_curve(config)

    def set_paused(self, paused: bool) -> None:
        # Pause is on/off, so it is always instant ("toggle" pacing).
        if paused != self._paused:
            self._mark_pacing_all("toggle")
        self._paused = paused

    def set_manual_temp(self, connector: str, kelvin: int, duration_min: int) -> None:
        if kelvin <= 0:
            raise ValueError(f"kelvin must be positive, got {kelvin}")
        # duration_min <= 0 means "until cleared": no expiry.
        expiry = None if duration_min <= 0 else self._clock() + timedelta(minutes=duration_min)
        self._manual[connector] = (float(kelvin), expiry)
        # A manual set eases at manual_transition_seconds. A manual temp expiring on
        # its own (in _manual_kelvin) is not marked and stays at schedule pacing.
        self._mark_pacing(connector, "manual")

    def clear_manual_temp(self, connector: str) -> None:
        self._manual.pop(connector, None)
        # Clearing eases back to the curve at manual pace too.
        self._mark_pacing(connector, "manual")

    def set_theme(self, mode: str, duration_min: int) -> None:
        if mode not in (LIGHT, DARK):
            raise ValueError(f"theme mode must be {LIGHT!r} or {DARK!r}, got {mode!r}")
        # duration_min <= 0 means "until cleared": no expiry.
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
        """Store the focused window and any unfocused fullscreen windows. Does not tick."""
        self._window = window
        self._unfocused = tuple(unfocused)

    def on_monitors_changed(self) -> None:
        # A hotplug or mode change can reset a CRTC's gamma and ramp size: re-send all.
        self._pushed.clear()
        connectors = {d.connector for d in self._backend.list_displays()}
        for connector in list(self._current_k):
            if connector not in connectors:
                del self._current_k[connector]
        for state in (self._prev_actions, self._pacing, self._manual):
            for connector in list(state):
                # "" is the all-displays manual key and must survive; a stale
                # per-connector entry must not re-apply if that monitor returns.
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

            # A rule engaging or releasing starts a rule-paced transition.
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
            # Clamped to [0, 1]. A negative dt (wall clock stepped backwards) must
            # hold: a negative factor would pass through mired 0 into negative Kelvin.
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
            try:
                self._push(connector, display.ramp_size, applied_k, now)
            except MutterError as exc:
                log.warning("could not set the gamma ramp on %s: %s", connector, exc)

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
        # Pause freezes the theme (there is no neutral theme). _theme_mode is kept, so
        # unpausing re-evaluates.
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
        # A partly applied theme still counts as applied, or every tick would retry.
        skipped = self._theme_applier.apply(variant)
        if skipped:
            log.warning("theme applied with skipped elements: %s", skipped)
        self._theme_mode = mode

    def status(self) -> dict:
        # Not a pure read: _manual_entry evicts expired manual entries.
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
            # Report what the display shows, not its masked-off eased position.
            current_k = self._apply_enabled_mask(
                connector, self._current_k.get(connector, NEUTRAL_KELVIN)
            )
            action = actions.get(connector, NO_ACTION)
            # No override: both null. Open-ended: kelvin set, expiry null. Timed: both set.
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
        # Goes through _theme_manual_mode so an expired override is evicted even while
        # paused, when _tick_theme returns before reaching it.
        manual_theme_mode = self._theme_manual_mode(now)
        manual_theme_expires_in_s = None
        if manual_theme_mode is not None and self._theme_manual is not None:
            expiry = self._theme_manual[1]
            if expiry is not None:
                manual_theme_expires_in_s = max(0, round((expiry - now).total_seconds()))

        # "windows": the focused entry first (if any), then each unfocused fullscreen one.
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

        `enabled` masks the output rather than the eased target, so a disabled display keeps
        tracking the schedule and rejoins the others mid-transition when re-enabled.
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

        # dc.enabled is not consulted: it masks the output after easing.
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
