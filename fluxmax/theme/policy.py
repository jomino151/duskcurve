"""Which light/dark mode the desktop should be in at a given instant.

PURE: stdlib datetime + fluxmax.schedule.curve. No gi/D-Bus/GTK/filesystem.

`desired_mode` is a pure function of the current instant: it has no memory, no
notion of a transition having "happened", and no way to be late. Ask it at
20:39 and it says light; ask it at 22:41 after a two-hour suspend and it says
dark. That property -- not any scheduling machinery -- is what makes the theme
survive lock, idle and suspend. See the M8 design doc section 2.1 for the
timer-based design this replaces and the four ways it failed.
"""

from datetime import datetime

from fluxmax.schedule.curve import Anchor, DayContext, anchor_minutes

LIGHT = "light"
DARK = "dark"


def desired_mode(now: datetime, ctx: DayContext, light_at: Anchor, dark_at: Anchor) -> str:
    """LIGHT or DARK for `now`, given the anchors each mode starts at.

    The day is two arcs -- [light, dark) and [dark, light) -- and `now` is in
    exactly one. Each arc's starting instant belongs to that arc.

    TOTAL BY DESIGN: never raises. This runs on every daemon tick, where an
    exception would be logged every two seconds forever. The degenerate case
    (both anchors on the same minute, so both arcs are empty) resolves to DARK;
    config validation rejects the statically detectable form of it.
    """
    light_m = anchor_minutes(light_at, ctx)
    dark_m = anchor_minutes(dark_at, ctx)
    now_m = now.hour * 60 + now.minute + now.second / 60

    if light_m == dark_m:
        return DARK
    if light_m < dark_m:
        return LIGHT if light_m <= now_m < dark_m else DARK
    return DARK if dark_m <= now_m < light_m else LIGHT
