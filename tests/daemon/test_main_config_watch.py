"""Tests for the config-file watcher's dedup of external edits, duplicate events and own writes.

`duskcurve.daemon.main` imports `gi` unconditionally, so it is imported directly here.
"""

from duskcurve.daemon.main import _ConfigFileWatchState


def test_is_new_change_true_for_first_differing_text():
    state = _ConfigFileWatchState("v1")
    assert state.is_new_change("v2") is True


def test_is_new_change_false_for_identical_text():
    state = _ConfigFileWatchState("v1")
    assert state.is_new_change("v1") is False


def test_is_new_change_updates_last_seen_so_repeat_calls_are_deduped():
    state = _ConfigFileWatchState("v1")
    assert state.is_new_change("v2") is True
    # A duplicate GFileMonitor event (CREATED then CHANGES_DONE_HINT) for one write is not new.
    assert state.is_new_change("v2") is False


def test_note_current_state_marks_a_self_triggered_write_as_already_seen():
    """The daemon's own write is not a new change when the monitor event arrives."""
    state = _ConfigFileWatchState("v1")

    # The daemon wrote "v2" itself (D-Bus SetConfig) and recorded it as applied.
    state.note_current_state("v2")

    # The monitor's notification for that write arrives afterwards, possibly repeatedly.
    assert state.is_new_change("v2") is False
    assert state.is_new_change("v2") is False

    # A genuinely new external edit is still detected.
    assert state.is_new_change("v3") is True
    assert state.is_new_change("v3") is False
