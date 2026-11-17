"""Which light/dark mode the desktop should be in at a given instant.

`desired_mode` is stateless, so the answer is right after lock, idle or suspend
without any timers.
"""

from datetime import datetime

from duskcurve.schedule.curve import Anchor, DayContext, anchor_minutes

LIGHT = "light"
DARK = "dark"


def desired_mode(now: datetime, ctx: DayContext, light_at: Anchor, dark_at: Anchor) -> str:
    """LIGHT or DARK for `now`, given the anchors each mode starts at.

    The day is two arcs -- [light, dark) and [dark, light) -- and `now` is in
    exactly one. Each arc's starting instant belongs to that arc.

    Never raises, since it runs on every daemon tick. If both anchors fall on
    the same minute (both arcs empty) the result is DARK; config validation
    rejects the statically detectable form.
    """
    light_m = anchor_minutes(light_at, ctx)
    dark_m = anchor_minutes(dark_at, ctx)
    now_m = now.hour * 60 + now.minute + now.second / 60

    if light_m == dark_m:
        return DARK
    if light_m < dark_m:
        return LIGHT if light_m <= now_m < dark_m else DARK
    return DARK if dark_m <= now_m < light_m else LIGHT
