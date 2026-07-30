"""Parsing for the org.fluxmax.Daemon status JSON.

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


@dataclass(frozen=True)
class WindowStatus:
    app_id: str
    fullscreen: bool
    monitor_connector: str


@dataclass(frozen=True)
class Status:
    mode: str
    paused: bool
    displays: tuple[DisplayStatus, ...]
    window: WindowStatus | None
    next_transition: str | None

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
    return Status(
        mode=str(raw.get("mode", "")),
        paused=bool(raw.get("paused", False)),
        displays=displays,
        window=_parse_window(raw.get("window")),
        next_transition=None if next_transition is None else str(next_transition),
    )
