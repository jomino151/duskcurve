"""Default colour-temperature curve and the named schedule modes."""

from duskcurve.schedule.curve import Curve, EventAnchor, Keypoint

# 6500K until an hour before sunset, easing to 3400K an hour after it, then
# to 2700K by bedtime, held overnight until just before wake.
DEFAULT_CURVE = Curve(
    [
        Keypoint(EventAnchor("wake", 0), 6500),
        Keypoint(EventAnchor("sunset", -60), 6500),
        Keypoint(EventAnchor("sunset", 60), 3400),
        Keypoint(EventAnchor("bedtime", -90), 3400),
        Keypoint(EventAnchor("bedtime", 0), 2700),
        Keypoint(EventAnchor("wake", -30), 2700),
    ]
)

# Later wind-down: 6500K until after sunset, 3800K through the evening, 2700K
# only an hour after bedtime.
WORKING_LATE_CURVE = Curve(
    [
        Keypoint(EventAnchor("wake", 0), 6500),
        Keypoint(EventAnchor("sunset", 30), 6500),
        Keypoint(EventAnchor("sunset", 90), 3800),
        Keypoint(EventAnchor("bedtime", 0), 3800),
        Keypoint(EventAnchor("bedtime", 60), 2700),
        Keypoint(EventAnchor("wake", -30), 2700),
    ]
)

# Two levels: 6500K by day, one eased drop to 2700K after sunset. The wake-1
# keypoint holds the night flat instead of ramping towards wake.
CLASSIC_CURVE = Curve(
    [
        Keypoint(EventAnchor("wake", 0), 6500),
        Keypoint(EventAnchor("sunset", 0), 6500),
        Keypoint(EventAnchor("sunset", 90), 2700),
        Keypoint(EventAnchor("wake", -1), 2700),
    ]
)

MODES: dict[str, Curve] = {
    "recommended": DEFAULT_CURVE,
    "reduce_eyestrain": DEFAULT_CURVE,
    "working_late": WORKING_LATE_CURVE,
    "classic": CLASSIC_CURVE,
}
