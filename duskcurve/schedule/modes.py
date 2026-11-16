"""Default colour-temperature curve and named schedule modes.

Pure stdlib + duskcurve.schedule.curve — no gi/D-Bus/GTK.
"""

from duskcurve.schedule.curve import Curve, EventAnchor, Keypoint

# Reduce Eyestrain-style 6-keypoint curve (spec table, §6):
# wake -> plateau at 6500K, ease down around sunset to 3400K, hold, ease
# further down before bedtime to 2700K, hold overnight until just before wake.
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

# Later/warmer wind-down: keep the 6500K day plateau longer, hold at a
# mid-warm 3800K through the evening, and only reach 2700K an hour after
# bedtime. Warms back up shortly before wake.
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

# Classic two-level curve: 6500K during the day, a single eased
# drop to 2700K after sunset, held flat overnight, then a quick eased jump
# back to 6500K right at wake. The wake-1 keypoint pins the overnight
# segment at a constant 2700K so it's a true hold rather than a gradual
# ramp all night toward the wake keypoint.
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
