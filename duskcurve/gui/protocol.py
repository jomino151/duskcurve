"""Parsing for the org.duskcurve.Daemon status JSON.

PURE module: stdlib only, no gi, no D-Bus. Tolerant by design -- unknown
keys are ignored and missing optional keys fall back to defaults, so a
newer daemon never breaks an older GUI.
"""

import json
from dataclasses import dataclass

NEUTRAL_KELVIN = 6500


@dataclass(frozen=True)
class DisplayStatus:
    connector: str
    current_k: int
    enabled: bool
    override: str
    rule_kind: str
    rule_kelvin: float | None
    manual_k: int | None
    # Seconds until the manual override expires, or None both when there is no
    # override and when there is one that never expires -- read it together
    # with manual_k to tell those two apart. Defaulted so a daemon predating
    # the field still parses.
    manual_expires_in_s: int | None = None

    @property
    def live_kelvin(self) -> int:
        """The temperature this display is showing *right now*.

        Always `current_k`, never `manual_k`. The distinction matters because
        `manual_k` is the manual override's *target*: the display eases toward
        it over `manual_transition_seconds`, so during that ease the two differ
        by the whole remaining gap. A readout that shows `manual_k` therefore
        sits frozen on the endpoint -- consistently warmer or cooler than the
        panel, depending on which way the change went -- while the screen is
        still visibly transitioning.

        Controls are the exception and deliberately do NOT use this: the manual
        slider tracks `manual_k` so its handle stays where the user dropped it
        instead of springing back and crawling toward the target.
        """
        return self.current_k


@dataclass(frozen=True)
class ThemeStatus:
    enabled: bool = False
    mode: str | None = None
    manual: str | None = None
    manual_expires_in_s: int | None = None

    @classmethod
    def from_raw(cls, raw: object) -> "ThemeStatus":
        if not isinstance(raw, dict):
            return cls()
        return cls(
            enabled=bool(raw.get("enabled", False)),
            mode=raw.get("mode") if isinstance(raw.get("mode"), str) else None,
            manual=raw.get("manual") if isinstance(raw.get("manual"), str) else None,
            manual_expires_in_s=(
                raw["manual_expires_in_s"]
                if isinstance(raw.get("manual_expires_in_s"), int)
                else None
            ),
        )


@dataclass(frozen=True)
class WindowStatus:
    app_id: str
    fullscreen: bool
    monitor_connector: str
    focused: bool = True


@dataclass(frozen=True)
class Status:
    mode: str
    paused: bool
    displays: tuple[DisplayStatus, ...]
    window: WindowStatus | None
    next_transition: str | None
    theme: ThemeStatus = ThemeStatus()
    windows: tuple[WindowStatus, ...] = ()

    def display(self, connector: str) -> "DisplayStatus | None":
        for d in self.displays:
            if d.connector == connector:
                return d
        return None


def _parse_display(raw: object) -> DisplayStatus | None:
    if not isinstance(raw, dict):
        return None
    connector = raw.get("connector")
    if not isinstance(connector, str) or connector == "":
        return None
    action = raw.get("rule_action")
    if not isinstance(action, dict):
        action = {}
    kelvin = action.get("kelvin")
    manual = raw.get("manual_k")

    # Tolerant coercion: fall back to defaults on type errors.
    try:
        current_k = round(float(raw.get("current_k", NEUTRAL_KELVIN)))
    except (TypeError, ValueError):
        current_k = NEUTRAL_KELVIN

    if manual is None:
        manual_k = None
    else:
        try:
            manual_k = round(float(manual))
        except (TypeError, ValueError):
            manual_k = None

    expires = raw.get("manual_expires_in_s")
    if expires is None:
        manual_expires_in_s = None
    else:
        try:
            manual_expires_in_s = round(float(expires))
        except (TypeError, ValueError):
            manual_expires_in_s = None

    if kelvin is None:
        rule_kelvin = None
    else:
        try:
            rule_kelvin = float(kelvin)
        except (TypeError, ValueError):
            rule_kelvin = None

    return DisplayStatus(
        connector=connector,
        current_k=current_k,
        enabled=bool(raw.get("enabled", True)),
        override=str(raw.get("override", "none")),
        rule_kind=str(action.get("kind", "none")),
        rule_kelvin=rule_kelvin,
        manual_k=manual_k,
        manual_expires_in_s=manual_expires_in_s,
    )


def _parse_window(raw: object) -> WindowStatus | None:
    if not isinstance(raw, dict):
        return None
    app_id = str(raw.get("app_id", ""))
    if app_id == "":
        return None
    return WindowStatus(
        app_id=app_id,
        fullscreen=bool(raw.get("fullscreen", False)),
        monitor_connector=str(raw.get("monitor_connector", "")),
        focused=bool(raw.get("focused", True)),
    )


def parse_status(json_text: str) -> Status:
    """Parse a status payload. Raises ValueError on malformed input."""
    try:
        raw = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"status payload is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"status payload must be a JSON object, got {type(raw).__name__}")

    entries = raw.get("per_display")
    displays: tuple[DisplayStatus, ...] = ()
    if isinstance(entries, list):
        displays = tuple(d for d in (_parse_display(e) for e in entries) if d is not None)

    next_transition = raw.get("next_transition")
    window = _parse_window(raw.get("window"))

    windows_entries = raw.get("windows")
    windows: tuple[WindowStatus, ...]
    if isinstance(windows_entries, list):
        windows = tuple(w for w in (_parse_window(e) for e in windows_entries) if w is not None)
    else:
        windows = (window,) if window is not None else ()

    return Status(
        mode=str(raw.get("mode", "")),
        paused=bool(raw.get("paused", False)),
        displays=displays,
        window=window,
        next_transition=None if next_transition is None else str(next_transition),
        theme=ThemeStatus.from_raw(raw.get("theme")),
        windows=windows,
    )
