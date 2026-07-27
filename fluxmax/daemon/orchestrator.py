"""Tick engine: ties config, curve, overrides, and the display backend together.

PURE core: stdlib (`datetime`, `zoneinfo`) + fluxmax.color / fluxmax.backend /
fluxmax.schedule / fluxmax.daemon.config / fluxmax.daemon.overrides. No
gi/D-Bus/GLib import in this module.
"""

from collections.abc import Callable
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fluxmax.backend import MutterBackend
from fluxmax.color import build_ramp, kelvin_to_mired, mired_to_kelvin
from fluxmax.daemon.config import Config, DisplayConfig
from fluxmax.daemon.overrides import apply_override
from fluxmax.schedule import (
    MODES,
    ClockAnchor,
    Curve,
    DayContext,
    EventAnchor,
    Keypoint,
    solar_events,
)

NEUTRAL_KELVIN = 6500.0

_EVENT_ANCHORS = frozenset({"sunrise", "sunset", "wake", "bedtime"})


def _parse_anchor(anchor: str, offset_min: int):
    if anchor in _EVENT_ANCHORS:
        return EventAnchor(anchor, offset_min)
    hour_str, minute_str = anchor.split(":")
    return ClockAnchor(int(hour_str), int(minute_str))


def _hhmm(value: str) -> tuple[int, int]:
    hour_str, minute_str = value.split(":")
    return int(hour_str), int(minute_str)


class Orchestrator:
    def __init__(
        self, backend: MutterBackend, config: Config, clock: Callable[[], datetime]
    ) -> None:
        self._backend = backend
        self._config = config
        self._clock = clock
        self._curve = self._build_curve(config)
        self._current_k: dict[str, float] = {}
        self._last_tick_dt: datetime | None = None
        self._paused = False
        # connector -> (kelvin, expiry); connector "" applies to all connectors.
        self._manual: dict[str, tuple[float, datetime]] = {}

    def set_config(self, config: Config) -> None:
        self._config = config
        self._curve = self._build_curve(config)

    def set_paused(self, paused: bool) -> None:
        self._paused = paused

    def set_manual_temp(self, connector: str, kelvin: int, duration_min: int) -> None:
        if kelvin <= 0:
            raise ValueError(f"kelvin must be positive, got {kelvin}")
        expiry = self._clock() + timedelta(minutes=duration_min)
        self._manual[connector] = (float(kelvin), expiry)

    def clear_manual_temp(self, connector: str) -> None:
        self._manual.pop(connector, None)

    def on_monitors_changed(self) -> None:
        connectors = {d.connector for d in self._backend.list_displays()}
        for connector in list(self._current_k):
            if connector not in connectors:
                del self._current_k[connector]

    def tick(self) -> None:
        now = self._clock()
        dt = 0.0 if self._last_tick_dt is None else (now - self._last_tick_dt).total_seconds()
        ctx = self._day_context(now)
        ts = self._config.transition_seconds
        factor = 1.0 if ts <= 0 else min(1.0, dt / ts)

        for display in self._backend.list_displays():
            connector = display.connector
            target_k = self._raw_target(connector, now, ctx)

            if connector not in self._current_k:
                current_k = target_k
            else:
                current_m = kelvin_to_mired(self._current_k[connector])
                target_m = kelvin_to_mired(target_k)
                current_m += (target_m - current_m) * factor
                current_k = mired_to_kelvin(current_m)

            self._current_k[connector] = current_k
            red, green, blue = build_ramp(display.ramp_size, current_k)
            self._backend.apply_ramp(connector, red, green, blue)

        self._last_tick_dt = now

    def status(self) -> dict:
        per_display = []
        for display in self._backend.list_displays():
            connector = display.connector
            dc = self._config.displays.get(connector, DisplayConfig())
            current_k = self._current_k.get(connector, NEUTRAL_KELVIN)
            per_display.append(
                {
                    "connector": connector,
                    "current_k": round(current_k),
                    "enabled": dc.enabled,
                    "override": dc.override_kind,
                }
            )
        return {
            "mode": self._config.mode,
            "paused": self._paused,
            "per_display": per_display,
            "next_transition": None,
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

    def _manual_kelvin(self, connector: str, now: datetime) -> float | None:
        entry = self._manual.get(connector)
        if entry is not None:
            kelvin, expiry = entry
            if now < expiry:
                return kelvin
            del self._manual[connector]

        entry = self._manual.get("")
        if entry is not None:
            kelvin, expiry = entry
            if now < expiry:
                return kelvin
            del self._manual[""]

        return None

    def _raw_target(self, connector: str, now: datetime, ctx: DayContext) -> float:
        if self._paused:
            return NEUTRAL_KELVIN

        dc = self._config.displays.get(connector, DisplayConfig())
        if not dc.enabled:
            return NEUTRAL_KELVIN

        manual = self._manual_kelvin(connector, now)
        if manual is not None:
            return manual

        base = self._curve.evaluate(now, ctx)
        return apply_override(base, dc.override_kind, dc.override_value)
