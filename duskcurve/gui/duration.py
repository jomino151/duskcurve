"""Manual-override duration choices and their human-readable rendering.

Stdlib only. Shared by the global and per-display manual scales. `duration_min == 0` means
"until cleared".
"""

# Index 0 is what a freshly built ComboRow selects, so it is the "until cleared" default.
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

    `Adw.ComboRow.get_selected()` returns `Gtk.INVALID_LIST_POSITION` (0xFFFFFFFF) when nothing
    is selected. Anything out of range falls back to 0, "until cleared".
    """
    if 0 <= index < len(DURATION_MINUTES):
        return DURATION_MINUTES[index]
    return 0


def format_remaining(seconds: int) -> str:
    """Render seconds-until-expiry at minute granularity.

    Minute granularity, since the GUI polls every couple of seconds. Anything under a minute,
    including a negative value from a poll just past expiry, reads "under a minute left".
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

    Three states: no override, an override with no expiry, and a timed one.
    """
    if not active:
        return "Overrides the schedule until cleared."
    if expires_in_s is None:
        return "Manual override active — until cleared."
    return f"Manual override active — {format_remaining(expires_in_s)}."
