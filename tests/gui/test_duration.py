import pytest

from fluxmax.gui.duration import (
    DURATION_CHOICES,
    DURATION_LABELS,
    DURATION_MINUTES,
    duration_for_index,
    format_remaining,
    manual_description,
)


def test_duration_for_index_maps_every_valid_selection():
    for i, minutes in enumerate(DURATION_MINUTES):
        assert duration_for_index(i) == minutes


def test_duration_for_index_survives_no_selection():
    """Adw.ComboRow.get_selected() returns Gtk.INVALID_LIST_POSITION
    (0xFFFFFFFF) when nothing is selected. Indexing with it would raise
    IndexError inside a GTK signal handler, escaping the view's error
    handling entirely -- fall back to the behaviour-preserving 0 instead."""
    assert duration_for_index(0xFFFFFFFF) == 0
    assert duration_for_index(len(DURATION_MINUTES)) == 0
    assert duration_for_index(-1) == 0


def test_until_cleared_is_the_first_choice_so_it_is_the_default():
    """Index 0 is what a fresh ComboRow selects, and it must mean "no change
    from today's behaviour" -- 0 is the daemon's "until explicitly cleared"."""
    assert DURATION_CHOICES[0] == ("Until cleared", 0)
    assert DURATION_MINUTES[0] == 0


def test_labels_and_minutes_stay_index_aligned():
    """The GUI selects by index, so a drift between the two tuples would send
    the wrong duration silently."""
    assert len(DURATION_LABELS) == len(DURATION_MINUTES) == len(DURATION_CHOICES)
    for i, (label, minutes) in enumerate(DURATION_CHOICES):
        assert DURATION_LABELS[i] == label
        assert DURATION_MINUTES[i] == minutes


def test_durations_are_positive_and_ascending_after_until_cleared():
    rest = DURATION_MINUTES[1:]
    assert all(m > 0 for m in rest)
    assert list(rest) == sorted(rest)


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "under a minute left"),
        (1, "under a minute left"),
        (59, "under a minute left"),
        (60, "1 min left"),
        (119, "1 min left"),
        (120, "2 min left"),
        (3599, "59 min left"),
        (3600, "1 h left"),
        (3660, "1 h 1 min left"),
        (7500, "2 h 5 min left"),
        (7200, "2 h left"),
    ],
)
def test_format_remaining_boundaries(seconds, expected):
    assert format_remaining(seconds) == expected


def test_format_remaining_treats_negative_as_expiring():
    """A clock skew or a poll landing just past expiry must not render a
    negative countdown."""
    assert format_remaining(-5) == "under a minute left"


def test_manual_description_when_no_override_is_active():
    assert manual_description(False, None) == "Overrides the schedule until cleared."


def test_manual_description_for_an_override_with_no_expiry():
    assert manual_description(True, None) == "Manual override active — until cleared."


def test_manual_description_counts_down_when_the_override_expires():
    assert manual_description(True, 1500) == "Manual override active — 25 min left."
