"""Lightweight smoke test for the live D-Bus dispatch used by duskcurve.daemon.main.

Task 4.4's reviewer flagged that `service.export_on_connection`'s live dispatch
(GLib.Variant pack/unpack, ConfigError -> D-Bus error mapping) is untested by
the pure `DaemonService` unit tests in test_service.py, which never touch a
real bus. This test exercises exactly that path: a real GDBusConnection with
DaemonService exported, called over the wire.

Uses an isolated private bus via Gio.TestDBus when available (no dependency on
or interference with any real session bus / real duskcurve daemon). Falls back
to the real session bus only if TestDBus can't come up and
$DBUS_SESSION_BUS_ADDRESS is set. Skips cleanly (not a failure) if neither is
reachable in this environment.
"""

import os
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from duskcurve.backend import FakeMutterTransport, MutterBackend  # noqa: E402
from duskcurve.daemon.config import default_config, dump_config  # noqa: E402
from duskcurve.daemon.orchestrator import Orchestrator  # noqa: E402
from duskcurve.daemon.service import (  # noqa: E402
    BUS_NAME,
    OBJECT_PATH,
    DaemonService,
    export_on_connection,
)

_INTERFACE = "org.duskcurve.Daemon"


@pytest.fixture
def bus_address():
    test_bus = None
    try:
        test_bus = Gio.TestDBus.new(Gio.TestDBusFlags.NONE)
        test_bus.up()
    except GLib.Error:
        test_bus = None

    if test_bus is not None:
        try:
            yield test_bus.get_bus_address()
        finally:
            test_bus.down()
        return

    session_addr = os.environ.get("DBUS_SESSION_BUS_ADDRESS")
    if not session_addr:
        pytest.skip("no private (Gio.TestDBus) or session D-Bus reachable in this environment")
    yield session_addr


def _connect(address: str) -> "Gio.DBusConnection":
    flags = (
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
        | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
    )
    return Gio.DBusConnection.new_for_address_sync(address, flags, None, None)


def _own_name_and_wait(conn: "Gio.DBusConnection") -> None:
    owned = []
    Gio.bus_own_name_on_connection(
        conn, BUS_NAME, Gio.BusNameOwnerFlags.NONE, lambda _c, name: owned.append(name), None
    )
    ctx = GLib.MainContext.default()
    for _ in range(1000):
        if owned:
            return
        ctx.iteration(True)
    raise AssertionError("timed out waiting to acquire bus name")


def _call(conn, method, parameters, reply_type):
    """Issue an async D-Bus call and pump a fresh MainLoop until it completes.

    A blocking call_sync() would deadlock here: `conn` is also hosting the
    exported service on this same thread's default main context, so the
    incoming method-call message needs that context to keep spinning in order
    to reach our own handler and reply. Async call() + a nested MainLoop lets
    the default context do exactly that.
    """
    result = {}
    loop = GLib.MainLoop()

    def _done(source, async_result, _data):
        try:
            result["value"] = source.call_finish(async_result)
        except GLib.Error as exc:
            result["error"] = exc
        loop.quit()

    conn.call(
        BUS_NAME,
        OBJECT_PATH,
        _INTERFACE,
        method,
        parameters,
        reply_type,
        Gio.DBusCallFlags.NONE,
        5000,
        None,
        _done,
        None,
    )
    loop.run()
    return result


def test_get_config_round_trips_over_live_dbus_dispatch(tmp_path, bus_address):
    cfg = default_config()
    config_path = tmp_path / "config.toml"
    config_path.write_text(dump_config(cfg))

    conn = _connect(bus_address)
    backend = MutterBackend(FakeMutterTransport())
    orch = Orchestrator(backend, cfg, clock=lambda: datetime.now(ZoneInfo("UTC")))
    service = DaemonService(orch, str(config_path))
    export_on_connection(service, conn)
    _own_name_and_wait(conn)

    result = _call(conn, "GetConfig", None, GLib.VariantType("(s)"))
    assert "error" not in result, result.get("error")
    (toml_text,) = result["value"].unpack()
    assert toml_text == dump_config(cfg)


def test_set_config_round_trips_and_persists_over_live_dbus_dispatch(tmp_path, bus_address):
    from dataclasses import replace

    cfg = default_config()
    config_path = tmp_path / "config.toml"
    config_path.write_text(dump_config(cfg))

    conn = _connect(bus_address)
    backend = MutterBackend(FakeMutterTransport())
    orch = Orchestrator(backend, cfg, clock=lambda: datetime.now(ZoneInfo("UTC")))

    status_events = []

    def on_status_changed(status_json):
        status_events.append(status_json)

    service = DaemonService(orch, str(config_path), on_status_changed=on_status_changed)
    export_on_connection(service, conn)
    _own_name_and_wait(conn)

    modified = replace(cfg, wake="06:30")
    modified_toml = dump_config(modified)

    result = _call(conn, "SetConfig", GLib.Variant("(s)", (modified_toml,)), None)
    assert "error" not in result, result.get("error")
    assert config_path.read_text() == modified_toml
    assert len(status_events) == 1


def test_invalid_set_config_maps_to_dbus_invalid_args_error(tmp_path, bus_address):
    cfg = default_config()
    config_path = tmp_path / "config.toml"
    config_path.write_text(dump_config(cfg))

    conn = _connect(bus_address)
    backend = MutterBackend(FakeMutterTransport())
    orch = Orchestrator(backend, cfg, clock=lambda: datetime.now(ZoneInfo("UTC")))
    service = DaemonService(orch, str(config_path))
    export_on_connection(service, conn)
    _own_name_and_wait(conn)

    result = _call(conn, "SetConfig", GLib.Variant("(s)", ("not valid toml [[[",)), None)
    assert "error" in result
    exc = result["error"]
    assert Gio.DBusError.get_remote_error(exc) == "org.freedesktop.DBus.Error.InvalidArgs"
    assert "invalid TOML" in exc.message

    # Config on disk and in-process state are untouched by the failed call.
    assert config_path.read_text() == dump_config(cfg)


def test_invalid_set_manual_temp_maps_to_dbus_invalid_args_error(tmp_path, bus_address):
    cfg = default_config()
    config_path = tmp_path / "config.toml"
    config_path.write_text(dump_config(cfg))

    conn = _connect(bus_address)
    backend = MutterBackend(FakeMutterTransport())
    orch = Orchestrator(backend, cfg, clock=lambda: datetime.now(ZoneInfo("UTC")))
    service = DaemonService(orch, str(config_path))
    export_on_connection(service, conn)
    _own_name_and_wait(conn)

    result = _call(
        conn,
        "SetManualTemp",
        GLib.Variant("(sii)", ("DP-2", 0, 10)),
        None,
    )
    assert "error" in result
    exc = result["error"]
    assert Gio.DBusError.get_remote_error(exc) == "org.freedesktop.DBus.Error.InvalidArgs"

    # The rejected kelvin value must not have been stored: a subsequent tick
    # must not raise (e.g. ZeroDivisionError from kelvin_to_mired(0)).
    orch.tick()
