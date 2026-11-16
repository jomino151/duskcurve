"""Keypoint-based colour-temperature curve, interpolated in mired space.

Depends only on the standard library and duskcurve.color.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from duskcurve.color import lerp_kelvin

_VALID_EVENTS = frozenset({"sunrise", "sunset", "wake", "bedtime"})


@dataclass(frozen=True)
class ClockAnchor:
    hour: int
    minute: int


@dataclass(frozen=True)
class EventAnchor:
    event: str  # "sunrise" | "sunset" | "wake" | "bedtime"
    offset_min: int = 0


Anchor = ClockAnchor | EventAnchor


@dataclass(frozen=True)
class Keypoint:
    anchor: Anchor
    kelvin: float
    easing: str = "smoothstep"


@dataclass(frozen=True)
class DayContext:
    sunrise: datetime
    sunset: datetime
    wake: datetime
    bedtime: datetime


def _resolve_anchor(anchor: Anchor, ctx: DayContext) -> float:
    """Resolve an anchor to minutes-past-local-midnight, normalized mod 1440."""
    if isinstance(anchor, ClockAnchor):
        minutes = anchor.hour * 60 + anchor.minute
    else:
        if anchor.event not in _VALID_EVENTS:
            raise ValueError(f"unknown event anchor: {anchor.event!r}")
        dt = getattr(ctx, anchor.event)
        minutes = dt.hour * 60 + dt.minute + dt.second / 60 + anchor.offset_min
    return minutes % 1440


def anchor_minutes(anchor: Anchor, ctx: DayContext) -> float:
    """Resolve an anchor to minutes past local midnight, mod 1440."""
    return _resolve_anchor(anchor, ctx)


def walk_minutes(keypoints: Sequence[Keypoint], ctx: DayContext) -> list[float]:
    """Resolve keypoints to a monotonic non-decreasing walk around the day.

    Keypoints are an ordered walk, not a sortable set: event anchors move
    seasonally and can invert. Each time is pushed forward by whole days until it is at or after
    the first keypoint's time, then clamped up to the running time (a
    zero-length segment) rather than reordered.
    """
    if not keypoints:
        return []
    base = _resolve_anchor(keypoints[0].anchor, ctx)
    times: list[float] = []
    running: float | None = None
    for kp in keypoints:
        m = _resolve_anchor(kp.anchor, ctx)
        while m < base:
            m += 1440
        if running is not None and m < running:
            m = running
        running = m
        times.append(m)
    return times


class Curve:
    def __init__(self, keypoints: list[Keypoint]) -> None:
        if not keypoints:
            raise ValueError("Curve requires at least one keypoint")
        self._keypoints = list(keypoints)

    @property
    def keypoints(self) -> tuple[Keypoint, ...]:
        return tuple(self._keypoints)

    def evaluate(self, now: datetime, ctx: DayContext) -> float:
        keypoints = self._keypoints
        if len(keypoints) == 1:
            return keypoints[0].kelvin

        times = walk_minutes(keypoints, ctx)

        now_min = now.hour * 60 + now.minute + now.second / 60
        n = len(keypoints)
        for cand in (now_min, now_min + 1440):
            for i in range(n):
                a_t = times[i]
                if i + 1 < n:
                    b_t = times[i + 1]
                    b_kp = keypoints[i + 1]
                else:
                    b_t = times[0] + 1440
                    b_kp = keypoints[0]
                if a_t <= cand < b_t:
                    if b_t == a_t:
                        return b_kp.kelvin
                    t = (cand - a_t) / (b_t - a_t)
                    return lerp_kelvin(keypoints[i].kelvin, b_kp.kelvin, t, easing=b_kp.easing)

        return keypoints[0].kelvin
