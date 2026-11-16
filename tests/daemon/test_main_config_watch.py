"""Unit tests for duskcurve.daemon.main._ConfigFileWatchState.

This is the dedup logic behind the config-file watcher wired in `_Daemon`:
distinguishing a genuine external edit from a duplicate GFileMonitor
notification, or from the daemon's own D-Bus-triggered write (SetConfig),
which must NOT cause a second `service.reload_config()` + a duplicate
StatusChanged emission.

`duskcurve.daemon.main` is a SYSTEM module that imports `gi` unconditionally
(consistent with duskcurve.backend.nightlight / duskcurve.backend.gio_transport),
so this test imports it directly rather than importorskip-ing -- same
precedent as tests/backend/test_nightlight.py.
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
    # A duplicate GFileMonitor event (e.g. CREATED then CHANGES_DONE_HINT) for
    # the same underlying write must not be treated as a second new change.
    assert state.is_new_change("v2") is False


def test_note_current_state_marks_a_self_triggered_write_as_already_seen():
    """Simulates the actual bug: DaemonService.set_config() writes new text to
    disk and fires on_status_changed *before* the GFileMonitor "changed" event
    for that same write is delivered. _emit_status_changed calls
    note_current_state(<the bytes now on disk>) synchronously in response to
    the mutation; by the time the file-monitor callback later reads the same
    bytes, is_new_change() must recognize them as already applied and return
    False -- otherwise reload_config() (and a second StatusChanged) fires
    again for a change the daemon already made itself."""
    state = _ConfigFileWatchState("v1")

    # The daemon just wrote "v2" itself (e.g. via a D-Bus SetConfig) and
    # immediately recorded it as the current, already-applied state.
    state.note_current_state("v2")

    # The GFileMonitor "changed" notification for that exact write arrives
    # afterwards (possibly more than once) -- it must be a no-op.
    assert state.is_new_change("v2") is False
    assert state.is_new_change("v2") is False

    # A genuinely new external edit is still detected.
    assert state.is_new_change("v3") is True
    assert state.is_new_change("v3") is False
