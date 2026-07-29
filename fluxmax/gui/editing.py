"""Pure config mutation for the GUI.

PURE module: no gi. Every edit the user makes is expressed here as a
function from an old Config to a new one, so the widgets never mutate
anything and every rule is unit-tested.
"""

import dataclasses
from collections.abc import Sequence

from fluxmax.daemon.config import Config, KeypointConfig
from fluxmax.daemon.rules import Rule
from fluxmax.schedule.curve import ClockAnchor, EventAnchor, Keypoint
from fluxmax.schedule.modes import MODES

EVENT_ANCHORS = ("sunrise", "sunset", "wake", "bedtime")


def keypoint_to_config(kp: Keypoint) -> KeypointConfig:
    if isinstance(kp.anchor, ClockAnchor):
        return KeypointConfig(
            anchor=f"{kp.anchor.hour:02d}:{kp.anchor.minute:02d}",
            kelvin=float(kp.kelvin),
            offset_min=0,
            easing=kp.easing,
        )
    return KeypointConfig(
        anchor=kp.anchor.event,
        kelvin=float(kp.kelvin),
        offset_min=kp.anchor.offset_min,
        easing=kp.easing,
    )


def config_to_keypoint(kc: KeypointConfig) -> Keypoint:
    if kc.anchor in EVENT_ANCHORS:
        anchor = EventAnchor(kc.anchor, kc.offset_min)
    else:
        # Validate clock anchor format: HH:MM with hour 0-23, minute 0-59.
        if ":" not in kc.anchor:
            raise ValueError(
                f"Clock anchor must be 'HH:MM', got: {kc.anchor!r}"
            )
        parts = kc.anchor.split(":")
        if len(parts) != 2:
            raise ValueError(
                f"Clock anchor must be 'HH:MM', got: {kc.anchor!r}"
            )
        hour_str, minute_str = parts
        # Reject stray whitespace or signs.
        if hour_str != hour_str.strip() or minute_str != minute_str.strip():
            raise ValueError(
                f"Clock anchor must be 'HH:MM' with no whitespace, got: {kc.anchor!r}"
            )
        if hour_str.startswith(("+", "-")) or minute_str.startswith(("+", "-")):
            raise ValueError(
                f"Clock anchor must be 'HH:MM' with no signs, got: {kc.anchor!r}"
            )
        try:
            hour = int(hour_str)
            minute = int(minute_str)
        except ValueError as e:
            raise ValueError(
                f"Clock anchor must be 'HH:MM' with numeric values, got: {kc.anchor!r}"
            ) from e
        if not (0 <= hour <= 23):
            raise ValueError(
                f"Clock anchor hour must be 0-23, got: {hour} in {kc.anchor!r}"
            )
        if not (0 <= minute <= 59):
            raise ValueError(
                f"Clock anchor minute must be 0-59, got: {minute} in {kc.anchor!r}"
            )
        anchor = ClockAnchor(hour, minute)
    return Keypoint(anchor, float(kc.kelvin), easing=kc.easing)


def mode_keypoints(mode: str) -> tuple[KeypointConfig, ...]:
    """A built-in mode's keypoints as config objects. Raises KeyError if unknown."""
    return tuple(keypoint_to_config(kp) for kp in MODES[mode].keypoints)


def effective_keypoints(cfg: Config) -> tuple[KeypointConfig, ...]:
    """The keypoints currently in force -- custom ones, or the selected mode's."""
    if cfg.mode == "custom":
        return tuple(cfg.custom_keypoints)
    return mode_keypoints(cfg.mode)


def with_keypoints(cfg: Config, kps: Sequence[KeypointConfig]) -> Config:
    return dataclasses.replace(cfg, mode="custom", custom_keypoints=tuple(kps))


def fork_to_custom(cfg: Config) -> Config:
    """Copy whatever curve is in force into custom_keypoints and switch to custom mode."""
    return with_keypoints(cfg, effective_keypoints(cfg))


def _check_index(kps: Sequence[KeypointConfig], index: int) -> None:
    if not 0 <= index < len(kps):
        raise IndexError(f"keypoint index {index} out of range (0..{len(kps) - 1})")


def _minutes_to_hhmm(minutes: float) -> str:
    total = round(minutes) % 1440
    return f"{total // 60:02d}:{total % 60:02d}"


def _hhmm_to_minutes(hhmm: str) -> int:
    hour, _, minute = hhmm.partition(":")
    return int(hour) * 60 + int(minute)


def set_keypoint_kelvin(
    kps: Sequence[KeypointConfig],
    index: int,
    kelvin: float,
    *,
    min_kelvin: float,
    max_kelvin: float,
) -> tuple[KeypointConfig, ...]:
    _check_index(kps, index)
    clamped = max(min_kelvin, min(max_kelvin, float(kelvin)))
    out = list(kps)
    out[index] = dataclasses.replace(out[index], kelvin=clamped)
    return tuple(out)


def shift_keypoint_time(
    kps: Sequence[KeypointConfig], index: int, delta_min: int
) -> tuple[KeypointConfig, ...]:
    """Move a keypoint in time.

    An event-anchored keypoint keeps its anchor and moves its offset, so it
    goes on tracking sunset (etc.) seasonally. Only an explicit anchor change
    converts between anchor kinds -- never a drag.
    """
    _check_index(kps, index)
    out = list(kps)
    kc = out[index]
    if kc.anchor in EVENT_ANCHORS:
        out[index] = dataclasses.replace(
            kc, offset_min=kc.offset_min + int(delta_min)
        )
    else:
        out[index] = dataclasses.replace(
            kc, anchor=_minutes_to_hhmm(_hhmm_to_minutes(kc.anchor) + delta_min)
        )
    return tuple(out)


def set_keypoint_anchor(
    kps: Sequence[KeypointConfig], index: int, anchor: str, offset_min: int = 0
) -> tuple[KeypointConfig, ...]:
    _check_index(kps, index)
    out = list(kps)
    out[index] = dataclasses.replace(
        out[index],
        anchor=anchor,
        offset_min=int(offset_min) if anchor in EVENT_ANCHORS else 0,
    )
    return tuple(out)


def insert_keypoint(
    kps: Sequence[KeypointConfig], index: int, minutes: float, kelvin: float
) -> tuple[KeypointConfig, ...]:
    """Insert a clock-anchored keypoint AT index.

    The index comes from which drawn segment the user clicked (see
    fluxmax.gui.geometry.insert_index) -- never from sorting, which would
    interleave keypoints from different phases of the day.
    """
    out = list(kps)
    out.insert(
        index, KeypointConfig(_minutes_to_hhmm(minutes), float(kelvin), 0, "smoothstep")
    )
    return tuple(out)


def delete_keypoint(
    kps: Sequence[KeypointConfig], index: int
) -> tuple[KeypointConfig, ...]:
    _check_index(kps, index)
    if len(kps) <= 1:
        raise ValueError("a curve needs at least one keypoint")
    out = list(kps)
    del out[index]
    return tuple(out)


def _check_rule_index(rules: Sequence[Rule], index: int) -> None:
    if not 0 <= index < len(rules):
        raise IndexError(f"rule index {index} out of range (0..{len(rules) - 1})")


def add_rule(rules: Sequence[Rule], rule: Rule) -> tuple[Rule, ...]:
    return (*tuple(rules), rule)


def replace_rule(rules: Sequence[Rule], index: int, rule: Rule) -> tuple[Rule, ...]:
    _check_rule_index(rules, index)
    out = list(rules)
    out[index] = rule
    return tuple(out)


def delete_rule(rules: Sequence[Rule], index: int) -> tuple[Rule, ...]:
    _check_rule_index(rules, index)
    out = list(rules)
    del out[index]
    return tuple(out)


def move_rule(rules: Sequence[Rule], index: int, delta: int) -> tuple[Rule, ...]:
    """Move a rule up or down. Order is semantics: resolve_rules is first-match-wins."""
    _check_rule_index(rules, index)
    target = max(0, min(len(rules) - 1, index + delta))
    if target == index:
        return tuple(rules)
    out = list(rules)
    out.insert(target, out.pop(index))
    return tuple(out)


def with_rules(cfg: Config, rules: Sequence[Rule]) -> Config:
    return dataclasses.replace(cfg, rules=tuple(rules))
