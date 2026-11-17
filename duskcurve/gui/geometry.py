"""Curve editor geometry: time/temperature <-> pixels, hit-testing, segments.

No gi or cairo, so it is testable without a display.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass

from duskcurve.daemon.config import KeypointConfig
from duskcurve.gui.editing import config_to_keypoint
from duskcurve.schedule.curve import DayContext, walk_minutes

MINUTES_PER_DAY = 1440.0


@dataclass(frozen=True)
class Viewport:
    width: float
    height: float
    min_kelvin: float
    max_kelvin: float
    margin: float = 24.0

    @property
    def left(self) -> float:
        return self.margin

    @property
    def right(self) -> float:
        return self.width - self.margin

    @property
    def top(self) -> float:
        return self.margin

    @property
    def bottom(self) -> float:
        return self.height - self.margin

    @property
    def plot_width(self) -> float:
        return max(1e-9, self.right - self.left)

    @property
    def plot_height(self) -> float:
        return max(1e-9, self.bottom - self.top)


def minutes_to_x(minutes: float, vp: Viewport) -> float:
    return vp.left + (minutes / MINUTES_PER_DAY) * vp.plot_width


def x_to_minutes(x: float, vp: Viewport) -> float:
    frac = (x - vp.left) / vp.plot_width
    return max(0.0, min(1.0, frac)) * MINUTES_PER_DAY


def kelvin_to_y(kelvin: float, vp: Viewport) -> float:
    span = max(1e-9, vp.max_kelvin - vp.min_kelvin)
    frac = (kelvin - vp.min_kelvin) / span
    return vp.bottom - frac * vp.plot_height  # max_kelvin at the top


def y_to_kelvin(y: float, vp: Viewport) -> float:
    frac = (vp.bottom - y) / vp.plot_height
    frac = max(0.0, min(1.0, frac))
    return vp.min_kelvin + frac * (vp.max_kelvin - vp.min_kelvin)


def keypoint_points(
    kps: Sequence[KeypointConfig], ctx: DayContext, vp: Viewport
) -> tuple[tuple[float, float], ...]:
    """One (x, y) per keypoint, in authored order.

    Times come from walk_minutes, the walk Curve.evaluate uses, so points sit where the
    engine evaluates them. The walk can exceed 1440; wrap into the visible 24 hours.
    """
    walk = walk_minutes([config_to_keypoint(k) for k in kps], ctx)
    return tuple(
        (minutes_to_x(m % MINUTES_PER_DAY, vp), kelvin_to_y(float(kc.kelvin), vp))
        for kc, m in zip(kps, walk, strict=True)
    )


def hit_test(
    points: Sequence[tuple[float, float]], x: float, y: float, radius: float = 12.0
) -> int | None:
    """Index of the nearest point within `radius` pixels, else None."""
    best: int | None = None
    best_distance = radius
    for i, (px, py) in enumerate(points):
        distance = math.hypot(px - x, py - y)
        if distance <= best_distance:
            best = i
            best_distance = distance
    return best


def segment_index(walk: Sequence[float], minutes: float) -> int:
    """Index of the segment containing `minutes`.

    Segment i runs from walk[i] to walk[i+1]; the last segment wraps from the
    final keypoint round to walk[0] + a day. A time before the first keypoint
    is on that wrapping segment, having come round from the previous day.
    """
    n = len(walk)
    if n <= 1:
        return 0
    base = walk[0]
    candidate = minutes if minutes >= base else minutes + MINUTES_PER_DAY
    for i in range(n - 1):
        if walk[i] <= candidate < walk[i + 1]:
            return i
    return n - 1


def insert_index(walk: Sequence[float], minutes: float) -> int:
    """Where a new keypoint clicked at `minutes` belongs in the authored order."""
    return segment_index(walk, minutes) + 1
