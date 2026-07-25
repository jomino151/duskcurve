"""Keypoint-based colour-temperature curve engine.

Pure stdlib (datetime/zoneinfo/dataclasses/math) + fluxmax.color — no gi/D-Bus/GTK.
All interpolation goes through fluxmax.color.lerp_kelvin (mired-space).
"""

from dataclasses import dataclass
from datetime import datetime

from fluxmax.color import lerp_kelvin

_VALID_EVENTS = frozenset({"sunrise", "sunset", "wake", "bedtime"})


@dataclass(frozen=True)
class ClockAnchor:
    hour: int
    minute: int


@dataclass(frozen=True)
class EventAnchor:
    event: str  # one of {"sunrise","sunset","wake","bedtime"}
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


def _segment_value(a: Keypoint, b: Keypoint, a_min: float, b_min: float, now_min: float) -> float:
    if b_min == a_min:
        return b.kelvin
    t = (now_min - a_min) / (b_min - a_min)
    return lerp_kelvin(a.kelvin, b.kelvin, t, easing=b.easing)


class Curve:
    def __init__(self, keypoints: list[Keypoint]) -> None:
        if not keypoints:
            raise ValueError("Curve requires at least one keypoint")
        self._keypoints = list(keypoints)

    def evaluate(self, now: datetime, ctx: DayContext) -> float:
        resolved = sorted(
            ((_resolve_anchor(kp.anchor, ctx), kp) for kp in self._keypoints),
            key=lambda pair: pair[0],
        )

        if len(resolved) == 1:
            return resolved[0][1].kelvin

        now_min = now.hour * 60 + now.minute + now.second / 60

        for (a_min, a_kp), (b_min, b_kp) in zip(resolved, resolved[1:], strict=False):
            if a_min <= now_min < b_min:
                return _segment_value(a_kp, b_kp, a_min, b_min, now_min)

        # now_min is before the first or after the last keypoint: bracketing
        # segment wraps across midnight from the last keypoint to the first.
        first_min, first_kp = resolved[0]
        last_min, last_kp = resolved[-1]
        b_min_eff = first_min + 1440
        now_eff = now_min + 1440 if now_min < first_min else now_min
        return _segment_value(last_kp, first_kp, last_min, b_min_eff, now_eff)
