"""Pure config mutation for the GUI.

PURE module: no gi. Every edit the user makes is expressed here as a
function from an old Config to a new one, so the widgets never mutate
anything and every rule is unit-tested.
"""

import dataclasses
from collections.abc import Sequence

from fluxmax.daemon.config import Config, KeypointConfig
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
