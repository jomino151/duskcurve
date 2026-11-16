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
# Everything in DaemonClient.start() -- the name watch, signal_subscribe's
# argument order, the call_sync marshalling for each method, and
# result.unpack() against the daemon's actual INTERFACE_XML -- otherwise
# never executes against a bus. The stub below exports the XML read
# verbatim from duskcurve/daemon/service.py, so a signature mismatch between
# the GUI and the daemon is caught here rather than on hardware.
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
    """Iterate the default main context until predicate() is true or a
    wall-clock deadline elapses.

    Copied from tests/daemon/test_watcher.py:142 -- see that module for the
    full rationale. A bare `while not predicate(): ctx.iteration(True)`
    could hang forever if the expected event never arrives, since
    iteration(True) blocks until *some* source is ready and nothing
    guarantees one ever will be. A periodic guard timeout source keeps the
    context always having something to dispatch, so the wall-clock deadline
    below actually gets enforced. Returns False (never hangs, never raises)
    on timeout, so callers can assert on it with their own message.
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
    """Runs the stub org.duskcurve.Daemon producer on its own thread with its
    own private GMainContext, standing in for the real daemon process.

    This mirrors tests/daemon/test_watcher.py's _ProducerThread and is
    load-bearing for the same reason documented there: DaemonClient's calls
    (GetStatus, SetConfig, ...) are *synchronous* connection.call_sync()
    calls. GDBusConnection.call_sync() waits on a private GMainContext it
    creates for the duration of the call -- it does NOT pump whatever
    context the callee's connection.register_object() dispatches on. A stub
    registered on the same thread/context as the calling client would
    therefore never get a chance to run its handler while call_sync blocks
    waiting for a reply, and every call would time out (this was observed
    directly: every bus test failed with "Timeout was reached" until the
    stub was moved to its own thread with its own continuously-running
    GMainLoop). A separate thread + context sidesteps that entirely, just
    as the real daemon being a separate OS process would.
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
        # Documented thread-safe -- does not require the producer thread's
        # context to be pumped, unlike receiving a call.
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
        # Runs as an idle source dispatched from inside self._loop.run(),
        # so by the time this fires the loop is genuinely iterating -- not
        # merely about to. See the "quit-before-run race" note below for why
        # that distinction is the whole point of this method existing.
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

        # IMPORTANT (this was the segfault): do not set self._ready here.
        # Setting it right after acquiring the name -- before self._loop.run()
        # below has actually been entered -- lets the test thread's
        # stop_thread() race ahead and call self._loop.quit() *before* run()
        # starts. GMainLoop.quit() on a loop that is not yet running is a
        # no-op (it clears a "keep running" flag that run() unconditionally
        # sets back to true when it starts), so the quit request is silently
        # lost, run() spins forever, and this thread never exits. The join()
        # in stop_thread() would then time out while the thread stays alive,
        # holding a live GDBusConnection into a torn-down TestDBus and racing
        # CPython's interpreter shutdown against gi._gi -- observed as a
        # segfault when a later test module's teardown runs. Scheduling an
        # idle source on self._main_context and only setting self._ready from
        # within it guarantees run() has genuinely started dispatching
        # before any caller can conclude "ready" and proceed to quit().
        idle_source = GLib.idle_source_new()
        idle_source.set_callback(self._mark_ready)
        idle_source.attach(self._main_context)

        self._loop.run()

        # Cleanup happens on this thread (the one that owns the connection),
        # after run() returns, so nothing outlives the fixture: release the
        # owned name, unexport the object, and close the connection before
        # this thread -- and therefore this GMainContext -- goes away.
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
        """Idempotent, thread-safe: GLib.MainLoop.quit() is safe to call
        from another thread, but calling it more than once (or before the
        loop is confirmed running -- see wait_ready()) is exactly the
        footgun this whole class exists to avoid, so guard it explicitly."""
        with self._quit_lock:
            if self._quit_called:
                return
            self._quit_called = True
            self._loop.quit()

    def stop_thread(self, timeout: float = 5.0) -> None:
        # wait_ready() already guarantees self._loop.run() has started by
        # the time this is reachable in normal use, but assert it again
        # here rather than trusting call order blindly.
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
    """Bring up the private bus and start the stub daemon's producer thread
    exactly once for the whole module (see stub_daemon below for why calls
    are reset per test instead of tearing this down and back up per test)."""
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

    # Gio.TestDBus.up() redirects the session bus for this process, so
    # DaemonClient.start()'s plain Gio.bus_get_sync(SESSION) lands on this
    # private test bus for the remainder of the module.
    node_info = Gio.DBusNodeInfo.new_for_xml(INTERFACE_XML)
    interface_info = node_info.interfaces[0]

    stub = _StubDaemon(test_bus.get_bus_address(), interface_info)
    stub.start()
    stub.wait_ready()

    try:
        yield stub
    finally:
        stub.stop_thread()
        # Every DaemonClient.start() in this module called
        # Gio.bus_get_sync(SESSION), which is a process-wide *singleton*
        # connection cached by GDBus -- not a fresh connection per call. Each
        # DaemonClient.stop() only unsubscribes/unwatches on it; the
        # singleton itself stays open, still pointing at this private test
        # bus. If we tear the test bus down while that singleton is still
        # open, GDBus can deliver a late async event (e.g. a NameOwnerChanged
        # completion or connection-closed notification) against a connection
        # whose underlying bus process is already gone -- observed as a
        # segfault, sometimes attributed to whatever unrelated code happened
        # to be running when the delayed callback landed (e.g. a later test
        # module's fixture teardown). Explicitly closing the singleton here
        # drops GDBus's cached reference to it, so the next
        # Gio.bus_get_sync(SESSION) call anywhere else in the process (after
        # this module is done) is forced to establish a brand new connection
        # instead of reusing this dead one.
        try:
            shared = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            if not shared.is_closed():
                shared.close_sync(None)
        except GLib.Error:
            pass
        else:
            # Gio.TestDBus.down() tracks the singleton connection with a
            # weak pointer and blocks (with an internal ~30s watchdog)
            # waiting for its refcount to reach zero before actually
            # stopping the dbus-daemon subprocess -- precisely to avoid the
            # segfault this whole fixture was rewritten to prevent. Closing
            # it is not enough on its own: Python's refcounting still holds
            # it alive via the local `shared` name until it goes out of
            # scope, so down() would sit in that 30s wait every single run
            # (observed directly) and then log a GLib-CRITICAL
            # "g_main_loop_quit: ... ref_count > 0" from *inside* its own
            # C implementation when the watchdog gives up. Dropping the
            # reference and forcing collection here lets the GObject
            # finalize immediately, so down() proceeds without waiting.
            del shared
        gc.collect()
        test_bus.down()


@pytest.fixture
def stub_daemon(_dbus_environment):
    """Per-test view of the module-scoped stub daemon, with call history and
    canned payloads reset so tests stay isolated from each other.

    Gio.bus_get_sync() returns a process-wide singleton connection per bus
    type, cached across calls; DaemonClient.start() relies on that to reach
    the private bus. Cycling Gio.TestDBus up()/down() once per test left
    later tests' Gio.bus_get_sync(SESSION) calls returning a defunct cached
    connection from a torn-down private bus. Bringing the bus (and the
    producer thread) up once per module and resetting only the stub's
    recorded state between tests avoids that.
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
    # Wait for the initial connect (name-appeared -> refresh()) to settle
    # before taking the "before" baseline, so it isolates the signal's own
    # marginal effect rather than double-counting the connect's own fetch.
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
    # Settle the initial connect before taking baselines, for the same
    # reason as test_status_changed_delivers_the_payload_and_refetches_config.
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
    # Pump so the connect's refresh() (which invokes boom() via
    # on_status/on_config) and the emitted signal below actually run --
    # otherwise this test would exercise nothing. Bounded, never hangs: it
    # is expected to exhaust its timeout since nothing here ever makes the
    # predicate true.
    _pump_until(lambda: False, timeout=0.3)
    stub_daemon.emit_status_changed('{"mode":"classic","paused":false,"per_display":[]}')
    _pump_until(lambda: False, timeout=0.3)
    client.stop()
    # DaemonClient._safe() logs the caught exception via log.exception(),
    # whose LogRecord carries the full exc_info traceback -- including the
    # _safe() frame's locals, which hold `self` and therefore
    # self._connection, the process-wide Gio.bus_get_sync(SESSION) singleton.
    # pytest's log capture retains that record (for potential failure
    # reporting) past the end of this test function, keeping the singleton
    # connection alive long after client.stop() runs. Left alone, that live
    # reference is still there when the module-scoped _dbus_environment
    # fixture tries to close the singleton and tear the private bus down:
    # Gio.TestDBus.down() tracks it with a weak pointer and blocks for ~30s
    # waiting for a refcount that never reaches zero (observed directly).
    # Clearing the capture here, before any fixture teardown starts, drops
    # the reference immediately.
    caplog.clear()
