"""TOML config schema: parsing, validation and serialisation.

`dump_config` is a hand-written serialiser whose output round-trips through `load_config`
to an equal `Config`.
"""

import decimal
import math
import os
import re
import tomllib
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from duskcurve.daemon.rules import RULE_ACTIONS, Rule
from duskcurve.schedule import MODES
from duskcurve.theme import ThemeVariant

_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_EVENT_ANCHORS = frozenset({"sunrise", "sunset", "wake", "bedtime"})
_EASINGS = frozenset({"linear", "smoothstep"})
_OVERRIDE_KINDS = frozenset({"none", "warm_cap", "fixed", "offset"})
_COLOR_SCHEMES = frozenset({"default", "prefer-dark", "prefer-light"})


class ConfigError(ValueError):
    """Raised when a TOML document fails to parse or validate as a Config."""


@dataclass(frozen=True)
class Location:
    latitude: float
    longitude: float
    timezone: str  # IANA name, e.g. "Europe/Berlin"


@dataclass(frozen=True)
class KeypointConfig:
    anchor: str  # "sunrise"|"sunset"|"wake"|"bedtime" (event) OR "HH:MM" (clock)
    kelvin: float
    offset_min: int = 0  # only meaningful for event anchors
    easing: str = "smoothstep"


@dataclass(frozen=True)
class DisplayConfig:
    enabled: bool = True
    override_kind: str = "none"  # "none"|"warm_cap"|"fixed"|"offset"
    override_value: float = 0.0  # kelvin for warm_cap/fixed; mired delta for offset


@dataclass(frozen=True)
class ThemeConfig:
    """When the desktop switches light<->dark, and what each mode looks like.

    Off by default: unlike gamma, this writes settings that are not Duskcurve's
    own and that the user may have chosen deliberately.
    """

    enabled: bool = False
    light_from: str = "sunrise"  # event anchor or "HH:MM", as schedule keypoints
    light_offset_min: int = 0
    dark_from: str = "sunset"
    dark_offset_min: int = 0
    light: ThemeVariant = ThemeVariant("prefer-light", "Yaru", "Yaru")
    dark: ThemeVariant = ThemeVariant("prefer-dark", "Yaru-dark", "Yaru-dark")


@dataclass(frozen=True)
class Config:
    location: Location
    wake: str  # "HH:MM"
    bedtime: str  # "HH:MM"
    mode: str  # a duskcurve.schedule.MODES key OR "custom"
    custom_keypoints: tuple[KeypointConfig, ...] = ()
    min_kelvin: float = 1200.0
    max_kelvin: float = 6500.0
    transition_seconds: float = 20.0
    # Easing time for a window-rule change; 0 = instant.
    rule_transition_seconds: float = 0.0
    # Easing for a manual set or clear: a time constant, not a duration. Each tick closes
    # min(1, dt / seconds) of the gap; with a 2 s tick, 2.0 closes it in one tick, 3.0 in ~8 s.
    manual_transition_seconds: float = 2.0
    tick_interval_seconds: float = 2.0
    displays: dict[str, DisplayConfig] = field(default_factory=dict)  # keyed by connector
    rules: tuple[Rule, ...] = ()
    theme: ThemeConfig = ThemeConfig()


def default_config() -> Config:
    return Config(
        location=Location(52.52, 13.40, "Europe/Berlin"),
        wake="10:00",
        bedtime="23:00",
        mode="reduce_eyestrain",
        min_kelvin=1200.0,
        max_kelvin=6500.0,
    )


# --------------------------------------------------------------------------
# Validation helpers
# --------------------------------------------------------------------------


def _validate_hhmm(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not _HHMM_RE.match(value):
        raise ConfigError(f"{field_name} must be an 'HH:MM' string (00-23:00-59), got {value!r}")
    return value


def _validate_timezone(name: object) -> str:
    if not isinstance(name, str):
        raise ConfigError(f"location.timezone must be a string, got {name!r}")
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ConfigError(f"location.timezone {name!r} is not a valid IANA timezone") from exc
    return name


def _validate_location(raw: dict) -> Location:
    try:
        latitude = float(raw["latitude"])
        longitude = float(raw["longitude"])
        timezone = raw["timezone"]
    except KeyError as exc:
        raise ConfigError(f"[location] missing required field: {exc}") from exc
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"[location] latitude/longitude must be numeric: {exc}") from exc

    if not (-90.0 <= latitude <= 90.0):
        raise ConfigError(f"location.latitude must be in [-90, 90], got {latitude}")
    if not (-180.0 <= longitude <= 180.0):
        raise ConfigError(f"location.longitude must be in [-180, 180], got {longitude}")
    timezone = _validate_timezone(timezone)
    return Location(latitude=latitude, longitude=longitude, timezone=timezone)


def _validate_mode(mode: object) -> str:
    if not isinstance(mode, str):
        raise ConfigError(f"schedule.mode must be a string, got {mode!r}")
    if mode != "custom" and mode not in MODES:
        raise ConfigError(
            f"schedule.mode {mode!r} is not a known mode ({sorted(MODES)} or 'custom')"
        )
    return mode


def _validate_anchor(value: object, label: str) -> str:
    if not isinstance(value, str) or not (value in _EVENT_ANCHORS or _HHMM_RE.match(value)):
        raise ConfigError(f"{label} {value!r} must be one of {sorted(_EVENT_ANCHORS)} or 'HH:MM'")
    return value


def _validate_theme_variant(raw: object, label: str, default: ThemeVariant) -> ThemeVariant:
    if not isinstance(raw, dict):
        raise ConfigError(f"[{label}] must be a table, got {raw!r}")
    values = {}
    for name in (
        "color_scheme",
        "gtk_theme",
        "icon_theme",
        "shell_theme",
        "cursor_theme",
        "wallpaper",
    ):
        value = raw.get(name, getattr(default, name))
        if not isinstance(value, str):
            raise ConfigError(f"{label}.{name} must be a string, got {value!r}")
        values[name] = value

    scheme = values["color_scheme"]
    # "" leaves color-scheme alone; Gio silently rejects values outside the schema enum.
    if scheme and scheme not in _COLOR_SCHEMES:
        raise ConfigError(
            f"{label}.color_scheme {scheme!r} must be '' or one of {sorted(_COLOR_SCHEMES)}"
        )
    wallpaper = values["wallpaper"]
    if wallpaper and not os.path.isabs(wallpaper):
        raise ConfigError(f"{label}.wallpaper must be an absolute path, got {wallpaper!r}")
    return ThemeVariant(**values)


def _validate_theme(raw: dict) -> ThemeConfig:
    default = ThemeConfig()
    enabled = raw.get("enabled", default.enabled)
    if not isinstance(enabled, bool):
        raise ConfigError(f"theme.enabled must be a boolean, got {enabled!r}")

    light_from = _validate_anchor(raw.get("light_from", default.light_from), "theme.light_from")
    dark_from = _validate_anchor(raw.get("dark_from", default.dark_from), "theme.dark_from")

    offsets = {}
    for name, fallback in (
        ("light_offset_min", default.light_offset_min),
        ("dark_offset_min", default.dark_offset_min),
    ):
        value = raw.get(name, fallback)
        if isinstance(value, bool) or not isinstance(value, int):
            raise ConfigError(f"theme.{name} must be an integer, got {value!r}")
        offsets[name] = value

    # Identical anchors leave both arcs of the day empty; desired_mode would sit on dark.
    if light_from == dark_from and offsets["light_offset_min"] == offsets["dark_offset_min"]:
        raise ConfigError("theme.light_from and theme.dark_from must differ")

    return ThemeConfig(
        enabled=enabled,
        light_from=light_from,
        dark_from=dark_from,
        light=_validate_theme_variant(raw.get("light", {}), "theme.light", default.light),
        dark=_validate_theme_variant(raw.get("dark", {}), "theme.dark", default.dark),
        **offsets,
    )


def _validate_keypoint(raw: dict) -> KeypointConfig:
    try:
        anchor = raw["anchor"]
        kelvin = float(raw["kelvin"])
    except KeyError as exc:
        raise ConfigError(f"[[schedule.keypoints]] missing required field: {exc}") from exc
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"[[schedule.keypoints]] kelvin must be numeric: {exc}") from exc

    try:
        offset_min = int(raw.get("offset_min", 0))
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"keypoint offset_min must be an integer: {exc}") from exc
    easing = raw.get("easing", "smoothstep")

    anchor = _validate_anchor(anchor, "keypoint anchor")
    if easing not in _EASINGS:
        raise ConfigError(f"keypoint easing {easing!r} must be one of {sorted(_EASINGS)}")
    if not (math.isfinite(kelvin) and kelvin > 0):
        raise ConfigError(f"keypoint kelvin must be finite and > 0, got {kelvin}")

    return KeypointConfig(anchor=anchor, kelvin=kelvin, offset_min=offset_min, easing=easing)


def _validate_schedule(raw: dict) -> tuple[str, str, str, tuple[KeypointConfig, ...]]:
    try:
        wake = raw["wake"]
        bedtime = raw["bedtime"]
        mode = raw["mode"]
    except KeyError as exc:
        raise ConfigError(f"[schedule] missing required field: {exc}") from exc

    wake = _validate_hhmm(wake, "schedule.wake")
    bedtime = _validate_hhmm(bedtime, "schedule.bedtime")
    mode = _validate_mode(mode)

    raw_keypoints = raw.get("keypoints", [])
    keypoints = tuple(_validate_keypoint(kp) for kp in raw_keypoints)

    if mode == "custom" and not keypoints:
        raise ConfigError("schedule.mode 'custom' requires a non-empty schedule.keypoints list")

    # Keypoints with a built-in mode are allowed: a parked custom curve, kept so
    # switching back to "custom" restores it. dump_config emits them regardless of mode.
    return wake, bedtime, mode, keypoints


def _validate_range(raw: dict) -> tuple[float, float]:
    try:
        min_kelvin = float(raw.get("min_kelvin", 1200.0))
        max_kelvin = float(raw.get("max_kelvin", 6500.0))
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"[range] min_kelvin/max_kelvin must be numeric: {exc}") from exc
    if not (0 < min_kelvin < 100000):
        raise ConfigError(f"range.min_kelvin must be in (0, 100000), got {min_kelvin}")
    if not (0 < max_kelvin < 100000):
        raise ConfigError(f"range.max_kelvin must be in (0, 100000), got {max_kelvin}")
    if not (min_kelvin < max_kelvin):
        raise ConfigError(
            f"range.min_kelvin ({min_kelvin}) must be < range.max_kelvin ({max_kelvin})"
        )
    return min_kelvin, max_kelvin


def _validate_transition(raw: dict) -> tuple[float, float, float]:
    try:
        seconds = float(raw.get("seconds", 20.0))
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"transition.seconds must be numeric: {exc}") from exc
    if seconds < 0:
        raise ConfigError(f"transition.seconds must be >= 0, got {seconds}")

    try:
        rule_seconds = float(raw.get("rule_seconds", 0.0))
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"transition.rule_seconds must be numeric: {exc}") from exc
    if rule_seconds < 0:
        raise ConfigError(f"transition.rule_seconds must be >= 0, got {rule_seconds}")

    try:
        manual_seconds = float(raw.get("manual_seconds", 2.0))
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"transition.manual_seconds must be numeric: {exc}") from exc
    if manual_seconds < 0:
        raise ConfigError(f"transition.manual_seconds must be >= 0, got {manual_seconds}")

    return seconds, rule_seconds, manual_seconds


def _validate_tick(raw: dict) -> float:
    try:
        interval = float(raw.get("interval_seconds", 2.0))
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"tick.interval_seconds must be numeric: {exc}") from exc
    if interval <= 0:
        raise ConfigError(f"tick.interval_seconds must be > 0, got {interval}")
    return interval


def _validate_display(name: str, raw: dict) -> DisplayConfig:
    enabled = raw.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ConfigError(f"displays.{name}.enabled must be a bool, got {enabled!r}")
    override_kind = raw.get("override_kind", "none")
    if override_kind not in _OVERRIDE_KINDS:
        raise ConfigError(
            f"displays.{name}.override_kind {override_kind!r} must be one of "
            f"{sorted(_OVERRIDE_KINDS)}"
        )
    try:
        override_value = float(raw.get("override_value", 0.0))
    except (TypeError, ValueError) as exc:
        raise ConfigError(f"displays.{name}.override_value must be numeric: {exc}") from exc
    if override_kind in ("warm_cap", "fixed") and not (override_value > 0):
        raise ConfigError(
            f"displays.{name}.override_value must be > 0 for override_kind "
            f"{override_kind!r}, got {override_value}"
        )
    return DisplayConfig(
        enabled=enabled, override_kind=override_kind, override_value=override_value
    )


def _validate_displays(raw: dict) -> dict[str, DisplayConfig]:
    return {name: _validate_display(name, section) for name, section in raw.items()}


def _validate_rule(raw: dict) -> Rule:
    action = raw.get("action")
    if not isinstance(action, str) or action not in RULE_ACTIONS:
        raise ConfigError(f"[[rules]] action must be one of {sorted(RULE_ACTIONS)}, got {action!r}")

    action_kelvin = raw.get("action_kelvin")
    if action == "fixed":
        if (
            not isinstance(action_kelvin, int)
            or isinstance(action_kelvin, bool)
            or (action_kelvin <= 0)
        ):
            raise ConfigError(
                f"[[rules]] action_kelvin must be a positive integer when action == 'fixed', "
                f"got {action_kelvin!r}"
            )
    elif action_kelvin is not None:
        raise ConfigError(
            f"[[rules]] action_kelvin is only valid when action == 'fixed' (action is {action!r})"
        )

    match_app_glob = raw.get("match_app_glob")
    if match_app_glob is not None and not isinstance(match_app_glob, str):
        raise ConfigError(f"[[rules]] match_app_glob must be a string, got {match_app_glob!r}")

    match_fullscreen = raw.get("match_fullscreen", False)
    if not isinstance(match_fullscreen, bool):
        raise ConfigError(f"[[rules]] match_fullscreen must be a bool, got {match_fullscreen!r}")

    if match_app_glob is None and match_fullscreen is not True:
        raise ConfigError(
            "[[rules]] must set match_app_glob and/or match_fullscreen = true; "
            "a rule with neither matches every window forever"
        )

    match_unfocused = raw.get("match_unfocused", False)
    if not isinstance(match_unfocused, bool):
        raise ConfigError(f"[[rules]] match_unfocused must be a bool, got {match_unfocused!r}")
    if match_unfocused and match_fullscreen is not True:
        raise ConfigError(
            "[[rules]] match_unfocused = true requires match_fullscreen = true; "
            "only fullscreen windows are reported while unfocused, so the rule "
            "could never match"
        )

    scope = raw.get("scope", "window_monitor")
    if not isinstance(scope, str) or not scope:
        raise ConfigError(f"[[rules]] scope must be a non-empty string, got {scope!r}")

    return Rule(
        action=action,
        match_app_glob=match_app_glob,
        match_fullscreen=match_fullscreen,
        action_kelvin=action_kelvin,
        scope=scope,
        match_unfocused=match_unfocused,
    )


def _validate_rules(raw: list) -> tuple[Rule, ...]:
    return tuple(_validate_rule(rule) for rule in raw)


def load_config(text: str) -> Config:
    """Parse and validate a TOML string, raising ConfigError on bad input."""
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"invalid TOML: {exc}") from exc

    if "location" not in doc:
        raise ConfigError("missing required [location] section")
    if "schedule" not in doc:
        raise ConfigError("missing required [schedule] section")

    location = _validate_location(doc["location"])
    wake, bedtime, mode, keypoints = _validate_schedule(doc["schedule"])
    min_kelvin, max_kelvin = _validate_range(doc.get("range", {}))
    transition_seconds, rule_transition_seconds, manual_transition_seconds = _validate_transition(
        doc.get("transition", {})
    )
    tick_interval_seconds = _validate_tick(doc.get("tick", {}))
    displays = _validate_displays(doc.get("displays", {}))
    rules = _validate_rules(doc.get("rules", []))
    theme = _validate_theme(doc.get("theme", {}))

    return Config(
        location=location,
        wake=wake,
        bedtime=bedtime,
        mode=mode,
        custom_keypoints=keypoints,
        min_kelvin=min_kelvin,
        max_kelvin=max_kelvin,
        transition_seconds=transition_seconds,
        rule_transition_seconds=rule_transition_seconds,
        manual_transition_seconds=manual_transition_seconds,
        tick_interval_seconds=tick_interval_seconds,
        displays=displays,
        rules=rules,
        theme=theme,
    )


# --------------------------------------------------------------------------
# Serialization
# --------------------------------------------------------------------------


def _fmt_float(value: float) -> str:
    """Format a float in fixed-point (never exponent) notation, losslessly.

    Going through Decimal avoids repr()'s exponent form for tiny values (e.g. 1e-08).
    """
    text = format(decimal.Decimal(repr(float(value))), "f")
    if "." not in text:
        text += ".0"
    return text


def _fmt_str(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _fmt_bool(value: bool) -> str:
    return "true" if value else "false"


def _dump_key(key: str) -> str:
    """Quote a TOML key unless it is bare-safe (connector names may contain '.' or similar)."""
    if re.match(r"^[A-Za-z0-9_-]+$", key):
        return key
    return _fmt_str(key)


def dump_config(cfg: Config) -> str:
    """Serialize a Config to its canonical TOML form.

    Guaranteed: load_config(dump_config(cfg)) == cfg.
    """
    lines: list[str] = []

    lines.append("[location]")
    lines.append(f"latitude = {_fmt_float(cfg.location.latitude)}")
    lines.append(f"longitude = {_fmt_float(cfg.location.longitude)}")
    lines.append(f"timezone = {_fmt_str(cfg.location.timezone)}")
    lines.append("")

    lines.append("[schedule]")
    lines.append(f"wake = {_fmt_str(cfg.wake)}")
    lines.append(f"bedtime = {_fmt_str(cfg.bedtime)}")
    lines.append(f"mode = {_fmt_str(cfg.mode)}")
    lines.append("")

    # Emitted regardless of mode so a parked custom curve survives a round trip.
    for kp in cfg.custom_keypoints:
        lines.append("[[schedule.keypoints]]")
        lines.append(f"anchor = {_fmt_str(kp.anchor)}")
        lines.append(f"offset_min = {int(kp.offset_min)}")
        lines.append(f"kelvin = {_fmt_float(kp.kelvin)}")
        lines.append(f"easing = {_fmt_str(kp.easing)}")
        lines.append("")

    lines.append("[range]")
    lines.append(f"min_kelvin = {_fmt_float(cfg.min_kelvin)}")
    lines.append(f"max_kelvin = {_fmt_float(cfg.max_kelvin)}")
    lines.append("")

    lines.append("[transition]")
    lines.append(f"seconds = {_fmt_float(cfg.transition_seconds)}")
    lines.append(f"rule_seconds = {_fmt_float(cfg.rule_transition_seconds)}")
    lines.append(f"manual_seconds = {_fmt_float(cfg.manual_transition_seconds)}")
    lines.append("")

    lines.append("[tick]")
    lines.append(f"interval_seconds = {_fmt_float(cfg.tick_interval_seconds)}")
    lines.append("")

    theme = cfg.theme
    lines.append("[theme]")
    lines.append(f"enabled = {_fmt_bool(theme.enabled)}")
    lines.append(f"light_from = {_fmt_str(theme.light_from)}")
    lines.append(f"light_offset_min = {int(theme.light_offset_min)}")
    lines.append(f"dark_from = {_fmt_str(theme.dark_from)}")
    lines.append(f"dark_offset_min = {int(theme.dark_offset_min)}")
    lines.append("")

    for section, variant in (("light", theme.light), ("dark", theme.dark)):
        lines.append(f"[theme.{section}]")
        lines.append(f"color_scheme = {_fmt_str(variant.color_scheme)}")
        lines.append(f"gtk_theme = {_fmt_str(variant.gtk_theme)}")
        lines.append(f"icon_theme = {_fmt_str(variant.icon_theme)}")
        lines.append(f"shell_theme = {_fmt_str(variant.shell_theme)}")
        lines.append(f"cursor_theme = {_fmt_str(variant.cursor_theme)}")
        lines.append(f"wallpaper = {_fmt_str(variant.wallpaper)}")
        lines.append("")

    for name, disp in cfg.displays.items():
        lines.append(f"[displays.{_dump_key(name)}]")
        lines.append(f"enabled = {_fmt_bool(disp.enabled)}")
        lines.append(f"override_kind = {_fmt_str(disp.override_kind)}")
        lines.append(f"override_value = {_fmt_float(disp.override_value)}")
        lines.append("")

    for rule in cfg.rules:
        lines.append("[[rules]]")
        if rule.match_app_glob is not None:
            lines.append(f"match_app_glob = {_fmt_str(rule.match_app_glob)}")
        lines.append(f"match_fullscreen = {_fmt_bool(rule.match_fullscreen)}")
        if rule.match_unfocused:
            lines.append(f"match_unfocused = {_fmt_bool(rule.match_unfocused)}")
        lines.append(f"action = {_fmt_str(rule.action)}")
        if rule.action_kelvin is not None:
            lines.append(f"action_kelvin = {int(rule.action_kelvin)}")
        lines.append(f"scope = {_fmt_str(rule.scope)}")
        lines.append("")

    return "\n".join(lines).rstrip("\n") + "\n"
