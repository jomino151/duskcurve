import subprocess
import sys

import pytest

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


def test_importing_fluxmax_daemon_watcher_does_not_pull_in_gi():
    # Subprocess (not a source-text scan) so this proves the actual runtime
    # behavior: importing fluxmax.daemon.watcher itself -- not just
    # fluxmax.daemon, which does not import watcher at all -- stays gi-free
    # until WindowWatcher.start() is called.
    code = (
        "import sys; import fluxmax.daemon.watcher; "
        "assert not any(m == 'gi' or m.startswith('gi.') for m in sys.modules), "
        "'fluxmax.daemon.watcher pulled in gi'"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_importing_fluxmax_daemon_does_not_pull_in_gi():
    code = (
        "import sys; import fluxmax.daemon; "
        "assert not any(m == 'gi' or m.startswith('gi.') for m in sys.modules), "
        "'fluxmax.daemon pulled in gi'"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


# ----------------------------------------------------------------------
# Producer/consumer integration test over a real (private) D-Bus.
#
# Everything in WindowWatcher.start() -- the name watch, signal_subscribe's
# argument order, the GetState call_sync, and result.unpack() against the
# extension's actual (sbs) XML -- has never executed against a real bus.
# This is the one hop where the extension and the daemon, built separately
# from the same frozen contract, could silently disagree. The stub server
# below exports the interface XML read verbatim out of the extension's own
# extension/focused-window@fluxmax/dbus.js, not retyped from the plan, so a
# real mismatch would actually be caught here.
# ----------------------------------------------------------------------

import pathlib  # noqa: E402
import threading  # noqa: E402

# This module also holds plain unit tests (decode_focus_payload, WindowState,
# construction) that must keep running even where gi/Gio is unavailable, so
# -- unlike test_main_smoke.py, which is entirely D-Bus tests -- we do not
# pytest.importorskip() at module level. Only the fixture below (and thus the
# one test that depends on it) skips if this import fails.
try:
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib

    _GI_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover -- environment dependent
    Gio = None  # type: ignore[assignment]
    GLib = None  # type: ignore[assignment]
    _GI_IMPORT_ERROR = exc

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_DBUS_JS = _REPO_ROOT / "extension" / "focused-window@fluxmax" / "dbus.js"


def _load_iface_xml_from_extension() -> str:
    """Extract IFACE_XML verbatim from the extension's dbus.js source.

    Reading it out of the real producer's source (rather than retyping the
    XML here from the plan/spec) is the point of this test: it proves the
    daemon's consumer actually agrees with what the extension actually
    exports, not with someone's memory of the contract.
    """
    text = _DBUS_JS.read_text(encoding="utf-8")
    marker = "export const IFACE_XML = `"
    start = text.index(marker) + len(marker)
    end = text.index("`;", start)
    return text[start:end]


@pytest.fixture
def test_bus_address():
    if _GI_IMPORT_ERROR is not None:
        pytest.skip(f"gi/Gio not available in this environment: {_GI_IMPORT_ERROR}")

    test_bus = None
    try:
        test_bus = Gio.TestDBus.new(Gio.TestDBusFlags.NONE)
        test_bus.up()
    except GLib.Error:
        test_bus = None

    if test_bus is None:
        pytest.skip("no private (Gio.TestDBus) bus available in this environment")

    try:
        yield test_bus.get_bus_address()
    finally:
        test_bus.down()


def _pump_until(predicate, timeout_iterations=2000):
    """Iterate the default main context until predicate() is true or we give up.

    A bare `while not predicate(): ctx.iteration(True)` could hang the test
    suite forever if the expected event never arrives (e.g. a contract
    mismatch swallowed by an exception handler); bounding the iterations
    turns that into a normal, fast test failure instead.
    """
    ctx = GLib.MainContext.default()
    for _ in range(timeout_iterations):
        if predicate():
            return True
        ctx.iteration(True)
    return False


def _export_stub_producer(connection, iface_xml, get_state_seed):
    """Export a minimal org.fluxmax.WindowWatcher object that answers GetState()
    with `get_state_seed` and otherwise does nothing -- standing in for the
    real extension/focused-window@fluxmax service.js."""
    node_info = Gio.DBusNodeInfo.new_for_xml(iface_xml)
    interface_info = node_info.interfaces[0]

    def handle_method_call(
        _connection, _sender, _object_path, _interface_name, method_name, _parameters, invocation
    ):
        if method_name == "GetState":
            invocation.return_value(GLib.Variant("(sbs)", get_state_seed))
        else:
            invocation.return_error_literal(
                Gio.dbus_error_quark(),
                Gio.DBusError.UNKNOWN_METHOD,
                f"Unknown method {method_name}",
            )

    return connection.register_object(
        WATCHER_OBJECT_PATH, interface_info, handle_method_call, None, None
    )


class _ProducerThread(threading.Thread):
    """Runs the stub org.fluxmax.WindowWatcher producer on its own thread with
    its own private GMainContext.

    This mirrors reality -- the real producer (the GNOME Shell extension) is a
    wholly separate OS process with its own independent main loop -- and it is
    load-bearing for this test, not decoration: WindowWatcher.start()'s
    on_appeared handler makes a *synchronous* connection.call_sync() GetState()
    call from the consumer's default-context dispatch. If the producer's
    incoming-message handling were serviced by that same default context
    (e.g. by exporting it on the main thread instead), the synchronous call
    would deadlock/time out -- there would be nothing left to pump that
    context and let the producer's handler run while call_sync blocks
    waiting on it. A separate thread + context sidesteps that entirely, just
    as a separate process would.
    """

    def __init__(self, bus_address: str, iface_xml: str, get_state_seed: tuple) -> None:
        super().__init__(daemon=True)
        self._bus_address = bus_address
        self._iface_xml = iface_xml
        self._get_state_seed = get_state_seed
        self._context = GLib.MainContext.new()
        self._loop = GLib.MainLoop.new(self._context, False)
        self._ready = threading.Event()
        self._error: Exception | None = None
        self.connection = None
        self.registration_id = None

    def run(self) -> None:
        self._context.push_thread_default()
        try:
            flags = (
                Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
                | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
            )
            self.connection = Gio.DBusConnection.new_for_address_sync(
                self._bus_address, flags, None, None
            )
            self.registration_id = _export_stub_producer(
                self.connection, self._iface_xml, self._get_state_seed
            )
            owned: list[str] = []
            Gio.bus_own_name_on_connection(
                self.connection,
                WATCHER_BUS_NAME,
                Gio.BusNameOwnerFlags.NONE,
                lambda _c, n: owned.append(n),
                None,
            )
            for _ in range(2000):
                if owned:
                    break
                self._context.iteration(True)
            if not owned:
                raise AssertionError(f"producer thread timed out acquiring {WATCHER_BUS_NAME}")
        except Exception as exc:  # noqa: BLE001 -- surfaced to the test thread below
            self._error = exc
        finally:
            self._ready.set()

        if self._error is None:
            self._loop.run()

    def wait_ready(self, timeout: float = 5.0) -> None:
        if not self._ready.wait(timeout):
            raise AssertionError("producer thread setup timed out")
        if self._error is not None:
            raise self._error

    def stop(self) -> None:
        self._loop.quit()
        self.join(timeout=5.0)


def test_producer_consumer_round_trip_over_live_dbus(test_bus_address):
    iface_xml = _load_iface_xml_from_extension()

    producer = _ProducerThread(test_bus_address, iface_xml, ("org.gimp.GIMP", True, "DP-2"))
    producer.start()
    producer.wait_ready()

    received: list[WindowState | None] = []
    watcher = WindowWatcher(received.append)
    try:
        watcher.start()

        # GetState() seed fetched on name-appeared.
        assert _pump_until(lambda: len(received) >= 1), "timed out waiting for GetState seed"
        assert received[0] == WindowState(
            app_id="org.gimp.GIMP", fullscreen=True, monitor_connector="DP-2"
        )

        # A live FocusChanged signal. emit_signal() is documented thread-safe
        # and does not require the producer thread's context to be pumped --
        # only *receiving* (GetState above) does.
        del received[:]
        producer.connection.emit_signal(
            None,
            WATCHER_OBJECT_PATH,
            WATCHER_INTERFACE,
            "FocusChanged",
            GLib.Variant("(sbs)", ("org.mozilla.firefox", False, "HDMI-1")),
        )
        assert _pump_until(lambda: len(received) >= 1), "timed out waiting for FocusChanged"
        assert received[0] == WindowState(
            app_id="org.mozilla.firefox", fullscreen=False, monitor_connector="HDMI-1"
        )

        # An empty app_id decodes to "no focused window".
        del received[:]
        producer.connection.emit_signal(
            None,
            WATCHER_OBJECT_PATH,
            WATCHER_INTERFACE,
            "FocusChanged",
            GLib.Variant("(sbs)", ("", False, "")),
        )
        assert _pump_until(lambda: len(received) >= 1), "timed out waiting for empty FocusChanged"
        assert received[0] is None
    finally:
        watcher.stop()
        producer.stop()
