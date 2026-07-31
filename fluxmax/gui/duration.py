"""Manual-override duration choices and their human-readable rendering.

PURE module: stdlib only, no gi, no D-Bus. Both the global manual scale
(`schedule_view`) and the per-display one (`display_panel`) offer the same
duration list and render the same countdown, so the list and the formatting
live here rather than being duplicated per view.

The daemon's `SetManualTemp` takes `duration_min`, where 0 means "until
explicitly cleared" rather than "expire immediately" -- hence 0 being a
legitimate, and the default, choice below.
"""

# Ordered; index 0 is what a freshly built ComboRow selects, so it must be the
# behaviour-preserving option ("until cleared" -- what the GUI always sent
# before a duration control existed).
DURATION_CHOICES: tuple[tuple[str, int], ...] = (
    ("Until cleared", 0),
    ("15 min", 15),
    ("30 min", 30),
    ("1 hour", 60),
    ("2 hours", 120),
)

DURATION_LABELS: tuple[str, ...] = tuple(label for label, _ in DURATION_CHOICES)
DURATION_MINUTES: tuple[int, ...] = tuple(minutes for _, minutes in DURATION_CHOICES)

_SECONDS_PER_MINUTE = 60
_SECONDS_PER_HOUR = 3600


def duration_for_index(index: int) -> int:
    """Map a ComboRow selection to `duration_min`, tolerating "no selection".

    `Adw.ComboRow.get_selected()` returns `Gtk.INVALID_LIST_POSITION`
    (0xFFFFFFFF) when nothing is selected, so indexing the tuple with it
    raises `IndexError` -- inside a GTK signal handler, where it escapes
    instead of reaching the view's error toast. Anything out of range falls
    back to 0, the behaviour-preserving "until cleared" choice.
    """
    if 0 <= index < len(DURATION_MINUTES):
        return DURATION_MINUTES[index]
    return 0


def format_remaining(seconds: int) -> str:
    """Render seconds-until-expiry at minute granularity.

    Minute granularity on purpose: the GUI polls at `tick_interval_seconds`
    (2 s by default), so a per-second countdown would visibly stutter, and the
    number is not precise enough to be worth that. Anything under a minute --
    including a negative value, which a poll landing just past expiry or a
    clock skew can produce -- collapses to "under a minute left" rather than
    rendering a misleading 0 or a negative.
    """
    if seconds < _SECONDS_PER_MINUTE:
        return "under a minute left"
    if seconds < _SECONDS_PER_HOUR:
        return f"{seconds // _SECONDS_PER_MINUTE} min left"
    hours = seconds // _SECONDS_PER_HOUR
    minutes = (seconds % _SECONDS_PER_HOUR) // _SECONDS_PER_MINUTE
    if minutes == 0:
        return f"{hours} h left"
    return f"{hours} h {minutes} min left"


def manual_description(active: bool, expires_in_s: int | None) -> str:
    """The line describing the manual-override state, for a group description.

    Three states, deliberately distinct: no override, an override with no
    expiry, and a timed one. The middle case is why this is not just a
    countdown -- an override that never expires is invisible state the user
    should still be told about.
    """
    if not active:
        return "Overrides the schedule until cleared."
    if expires_in_s is None:
        return "Manual override active — until cleared."
    return f"Manual override active — {format_remaining(expires_in_s)}."
