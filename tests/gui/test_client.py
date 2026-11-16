import gc
import subprocess
import sys

import pytest

from duskcurve.gui.client import (
    DAEMON_BUS_NAME,
    DAEMON_INTERFACE,
    DAEMON_OBJECT_PATH,
    DaemonClient,
)


def test_contract_constants_match_the_daemon():
    from duskcurve.daemon.service import BUS_NAME, OBJECT_PATH

    assert DAEMON_BUS_NAME == BUS_NAME == "org.duskcurve.Daemon"
    assert DAEMON_OBJECT_PATH == OBJECT_PATH == "/org/duskcurve/Daemon"
    assert DAEMON_INTERFACE == "org.duskcurve.Daemon"


def test_construction_does_no_dbus_work():
    seen = []
    client = DaemonClient(seen.append, seen.append, seen.append)
    assert seen == []
    client.stop()  # safe without start()
    client.stop_polling()  # safe without start_polling()


def test_importing_the_client_does_not_pull_in_gi():
    code = (
        "import sys; import duskcurve.gui.client; "
        "assert not any(m == 'gi' or m.startswith('gi.') for m in sys.modules), "
        "'duskcurve.gui.client pulled in gi'"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


# ----------------------------------------------------------------------
# Integration over a real (private) D-Bus.
#
# The stub daemon exports the INTERFACE_XML from duskcurve/daemon/service.py,
# so a signature mismatch between the GUI and the daemon is caught here.
# ----------------------------------------------------------------------

try:
    import gi

    gi.require_version("Gio", "2.0")
    from gi.repository import Gio, GLib

    _GI_IMPORT_ERROR: Exception | None = None
except Exception as exc:  # pragma: no cover -- environment dependent
    Gio = None  # type: ignore[assignment]
    GLib = None  # type: ignore[assignment]
    _GI_IMPORT_ERROR = exc


import threading  # noqa: E402
import time  # noqa: E402


def _pump_until(predicate, timeout=5.0):
    """Iterate the default main context until predicate() is true or the deadline passes.

    A periodic guard source keeps iteration(True) from blocking forever when the
    expected event never arrives. Returns False on timeout; never raises.
    """
    ctx = GLib.MainContext.default()
    deadline = time.monotonic() + timeout
    guard_id = GLib.timeout_add(20, lambda: True)
    try:
        while time.monotonic() < deadline:
            if predicate():
                return True
            ctx.iteration(True)
        return predicate()
    finally:
        GLib.source_remove(guard_id)


class _StubDaemon(threading.Thread):
    """Stub org.duskcurve.Daemon producer on its own thread and GMainContext.

    DaemonClient uses synchronous call_sync(), which does not pump the callee's
    context; a stub sharing the client's thread would never answer and every call
    would time out.
    """

    def __init__(self, bus_address: str, interface_info) -> None:
        super().__init__(daemon=True)
        self._bus_address = bus_address
        self._interface_info = interface_info
        self._main_context = GLib.MainContext.new()
        self._loop = GLib.MainLoop.new(self._main_context, False)
        self._ready = threading.Event()
        self._error: Exception | None = None
        self._owner_id: int | None = None
        self._quit_lock = threading.Lock()
        self._quit_called = False
        self.connection = None
        self.registration_id = None
        self.calls: list[tuple[str, tuple]] = []
        self.status_payload = '{"mode":"off","paused":false,"per_display":[]}'
        self.config_payload = "# stub config\n"
        self._reject_message: str | None = None

    def reset(self) -> None:
        self.calls.clear()
        self.status_payload = '{"mode":"off","paused":false,"per_display":[]}'
        self.config_payload = "# stub config\n"
        self._reject_message = None

    def emit_status_changed(self, json_payload: str) -> None:
        # emit_signal is thread-safe and needs no pumping of the producer context.
        self.connection.emit_signal(
            None,
            DAEMON_OBJECT_PATH,
            DAEMON_INTERFACE,
            "StatusChanged",
            GLib.Variant("(s)", (json_payload,)),
        )

    def reject_set_config(self, message: str) -> None:
        self._reject_message = message

    def _handle_method_call(
        self,
        _connection,
        _sender,
        _object_path,
        _interface_name,
        method_name,
        parameters,
        invocation,
    ):
        self.calls.append((method_name, parameters.unpack()))
        if method_name == "GetStatus":
            invocation.return_value(GLib.Variant("(s)", (self.status_payload,)))
        elif method_name == "GetConfig":
            invocation.return_value(GLib.Variant("(s)", (self.config_payload,)))
        elif method_name == "SetConfig":
            if self._reject_message is not None:
                invocation.return_error_literal(
                    Gio.dbus_error_quark(), Gio.DBusError.INVALID_ARGS, self._reject_message
                )
            else:
                invocation.return_value(None)
        elif method_name in ("SetPaused", "SetManualTemp", "ClearManualTemp", "ReloadConfig"):
            invocation.return_value(None)
        else:
            invocation.return_error_literal(
                Gio.dbus_error_quark(),
                Gio.DBusError.UNKNOWN_METHOD,
                f"Unknown method {method_name}",
            )

    def _mark_ready(self, _user_data=None) -> bool:
        # Runs as an idle source inside self._loop.run(), so the loop is genuinely
        # iterating by the time _ready is set (see run()).
        self._ready.set()
        return GLib.SOURCE_REMOVE

    def run(self) -> None:
        self._main_context.push_thread_default()
        try:
            flags = (
                Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
                | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
            )
            self.connection = Gio.DBusConnection.new_for_address_sync(
                self._bus_address, flags, None, None
            )
            self.registration_id = self.connection.register_object(
                DAEMON_OBJECT_PATH, self._interface_info, self._handle_method_call, None, None
            )
            owned: list[str] = []
            self._owner_id = Gio.bus_own_name_on_connection(
                self.connection,
                DAEMON_BUS_NAME,
                Gio.BusNameOwnerFlags.NONE,
                lambda _c, n: owned.append(n),
                None,
            )
            for _ in range(2000):
                if owned:
                    break
                self._main_context.iteration(True)
            if not owned:
                raise AssertionError(f"stub daemon timed out acquiring {DAEMON_BUS_NAME}")
        except Exception as exc:  # noqa: BLE001 -- surfaced to the test thread below
            self._error = exc
            self._ready.set()
            self._main_context.pop_thread_default()
            return

        # _ready is set from an idle source, not here: GMainLoop.quit() before run()
        # starts is a no-op, so an early quit would be lost and the thread would
        # outlive the fixture.
        idle_source = GLib.idle_source_new()
        idle_source.set_callback(self._mark_ready)
        idle_source.attach(self._main_context)

        self._loop.run()

        # Clean up on the thread that owns the connection: release the name,
        # unexport the object and close the connection.
        try:
            if self._owner_id is not None:
                Gio.bus_unown_name(self._owner_id)
                self._owner_id = None
            if self.registration_id is not None:
                self.connection.unregister_object(self.registration_id)
                self.registration_id = None
            if self.connection is not None and not self.connection.is_closed():
                self.connection.close_sync(None)
        except Exception:  # noqa: BLE001 -- best-effort teardown, never crash the thread
            pass
        finally:
            self._main_context.pop_thread_default()

    def wait_ready(self, timeout: float = 5.0) -> None:
        if not self._ready.wait(timeout):
            raise AssertionError("stub daemon thread setup timed out")
        if self._error is not None:
            raise self._error

    def request_quit(self) -> None:
        """Quit the loop at most once; safe to call from any thread."""
        with self._quit_lock:
            if self._quit_called:
                return
            self._quit_called = True
            self._loop.quit()

    def stop_thread(self, timeout: float = 5.0) -> None:
        # Quit only once the loop is confirmed running.
        if not self._ready.is_set():
            self._ready.wait(timeout)
        self.request_quit()
        self.join(timeout=timeout)
        if self.is_alive():
            raise AssertionError(
                "stub daemon thread did not exit within the timeout -- it would have "
                "outlived this fixture and crashed a later test module at interpreter "
                "shutdown"
            )


@pytest.fixture(scope="module")
def _dbus_environment():
    """Bring up the private bus and the stub daemon once for the whole module."""
    if _GI_IMPORT_ERROR is not None:
        pytest.skip(f"gi/Gio not available: {_GI_IMPORT_ERROR}")

    from duskcurve.daemon.service import INTERFACE_XML

    test_bus = None
    try:
        test_bus = Gio.TestDBus.new(Gio.TestDBusFlags.NONE)
        test_bus.up()
    except GLib.Error:
        test_bus = None
    if test_bus is None:
        pytest.skip("no private (Gio.TestDBus) bus available in this environment")

    # up() redirects the session bus, so DaemonClient reaches this private bus.
    node_info = Gio.DBusNodeInfo.new_for_xml(INTERFACE_XML)
    interface_info = node_info.interfaces[0]

    stub = _StubDaemon(test_bus.get_bus_address(), interface_info)
    stub.start()
    stub.wait_ready()

    try:
        yield stub
    finally:
        stub.stop_thread()
        # bus_get_sync(SESSION) is a process-wide singleton that DaemonClient.stop()
        # leaves open. Close it before the bus goes away so GDBus cannot deliver
        # late events to a dead connection, and later modules get a fresh one.
        try:
            shared = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            if not shared.is_closed():
                shared.close_sync(None)
        except GLib.Error:
            pass
        else:
            # TestDBus.down() waits up to ~30s for the singleton's refcount to reach
            # zero; drop the local reference and collect so it finalises at once.
            del shared
        gc.collect()
        test_bus.down()


@pytest.fixture
def stub_daemon(_dbus_environment):
    """Per-test view of the module-scoped stub, with call history and payloads reset.

    The bus stays up for the whole module: cycling it per test would leave the
    cached session connection pointing at a torn-down bus.
    """
    _dbus_environment.reset()
    yield _dbus_environment


def test_connects_and_primes_config_and_status(stub_daemon):
    statuses, configs, connected = [], [], []
    client = DaemonClient(statuses.append, configs.append, connected.append)
    client.start()
    assert _pump_until(lambda: connected != []), "timed out waiting to connect"
    assert connected[-1] is True
    assert len(statuses) == 1 and len(configs) == 1
    client.stop()


def test_status_changed_delivers_the_payload_and_refetches_config(stub_daemon):
    statuses, configs = [], []
    client = DaemonClient(statuses.append, configs.append, lambda _c: None)
    client.start()
    # Let the initial connect settle first so the baseline excludes its fetch.
    assert _pump_until(lambda: len(configs) >= 1), "timed out waiting for initial connect"
    before = len(configs)
    stub_daemon.emit_status_changed('{"mode":"classic","paused":true,"per_display":[]}')
    assert _pump_until(lambda: len(configs) > before), "timed out waiting for config refetch"
    assert '"classic"' in statuses[-1]
    assert len(configs) == before + 1  # the signal triggers a config refetch
    client.stop()


def test_each_setter_marshals_its_arguments(stub_daemon):
    client = DaemonClient(lambda _s: None, lambda _c: None, lambda _b: None)
    client.start()
    client.set_paused(True)
    client.set_manual_temp("DP-2", 2700, 0)
    client.clear_manual_temp("DP-2")
    client.set_config("# a toml document\n")
    names = [name for name, _args in stub_daemon.calls]
    assert "SetPaused" in names
    assert ("SetManualTemp", ("DP-2", 2700, 0)) in stub_daemon.calls
    assert ("ClearManualTemp", ("DP-2",)) in stub_daemon.calls
    assert ("SetConfig", ("# a toml document\n",)) in stub_daemon.calls
    client.stop()


def test_set_config_raises_when_the_daemon_rejects_it(stub_daemon):
    stub_daemon.reject_set_config("bedtime must be an 'HH:MM' string")
    client = DaemonClient(lambda _s: None, lambda _c: None, lambda _b: None)
    client.start()
    with pytest.raises(Exception) as excinfo:
        client.set_config("broken")
    assert "bedtime" in str(excinfo.value)
    client.stop()


def test_polling_refetches_status_but_not_config(stub_daemon):
    statuses, configs = [], []
    client = DaemonClient(statuses.append, configs.append, lambda _b: None)
    client.start()
    # Let the initial connect settle before taking baselines.
    assert _pump_until(lambda: len(statuses) >= 1 and len(configs) >= 1), (
        "timed out waiting for initial connect"
    )
    status_before, config_before = len(statuses), len(configs)
    client.start_polling(1)
    assert _pump_until(lambda: len(statuses) > status_before, timeout=3.0), (
        "timed out waiting for a poll tick"
    )
    assert len(statuses) > status_before
    assert len(configs) == config_before  # the poll path never calls GetConfig
    client.stop_polling()
    client.stop()


def test_start_polling_twice_does_not_create_two_timers(stub_daemon):
    client = DaemonClient(lambda _s: None, lambda _c: None, lambda _b: None)
    client.start()
    client.start_polling(1)
    first = client._poll_id
    client.start_polling(1)
    assert client._poll_id == first
    client.stop_polling()
    assert client._poll_id is None
    client.stop_polling()  # idempotent
    client.stop()


def test_a_raising_callback_does_not_escape_into_gio(stub_daemon, caplog):
    def boom(_payload):
        raise RuntimeError("view exploded")

    client = DaemonClient(boom, boom, lambda _b: None)
    client.start()  # must not raise
    # Pump so the connect's refresh() and the emitted signal below actually run;
    # the predicate is never true, so each call simply runs out its timeout.
    _pump_until(lambda: False, timeout=0.3)
    stub_daemon.emit_status_changed('{"mode":"classic","paused":false,"per_display":[]}')
    _pump_until(lambda: False, timeout=0.3)
    client.stop()
    # The captured log record's traceback holds the session-bus singleton alive,
    # which would stall TestDBus.down() in fixture teardown; drop it now.
    caplog.clear()
