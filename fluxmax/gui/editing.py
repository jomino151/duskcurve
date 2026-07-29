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
        hour, _, minute = kc.anchor.partition(":")
        anchor = ClockAnchor(int(hour), int(minute))
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
