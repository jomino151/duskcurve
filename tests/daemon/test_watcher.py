import pathlib
import subprocess
import sys

import fluxmax.daemon.watcher as watcher_mod
from fluxmax.daemon.rules import WindowState
from fluxmax.daemon.watcher import (
    WATCHER_BUS_NAME,
    WATCHER_INTERFACE,
    WATCHER_OBJECT_PATH,
    WindowWatcher,
    decode_focus_payload,
)


def test_contract_constants_are_frozen():
    assert WATCHER_BUS_NAME == "org.fluxmax.WindowWatcher"
    assert WATCHER_OBJECT_PATH == "/org/fluxmax/WindowWatcher"
    assert WATCHER_INTERFACE == "org.fluxmax.WindowWatcher"


def test_decode_empty_app_id_means_no_window():
    assert decode_focus_payload("", False, "DP-2") is None
    assert decode_focus_payload("   ", False, "DP-2") is None


def test_decode_builds_a_window_state():
    assert decode_focus_payload("org.gimp.GIMP", True, "DP-2") == WindowState(
        app_id="org.gimp.GIMP", fullscreen=True, monitor_connector="DP-2"
    )


def test_decode_coerces_fullscreen_to_bool():
    got = decode_focus_payload("app", 1, "")
    assert got.fullscreen is True
    assert got.monitor_connector == ""


def test_construction_does_no_dbus_work():
    seen = []
    w = WindowWatcher(seen.append)
    assert seen == []
    w.stop()          # safe without start()


def test_module_does_not_import_gi_at_module_level():
    src = pathlib.Path(watcher_mod.__file__).read_text()
    head = src.split("class WindowWatcher", 1)[0]
    module_level = [
        line for line in head.splitlines()
        if line.startswith("import gi") or line.startswith("from gi")
    ]
    assert module_level == [], f"gi must be imported lazily, found: {module_level}"


def test_importing_fluxmax_daemon_does_not_pull_in_gi():
    code = (
        "import sys; import fluxmax.daemon; "
        "assert not any(m == 'gi' or m.startswith('gi.') for m in sys.modules), "
        "'fluxmax.daemon pulled in gi'"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
