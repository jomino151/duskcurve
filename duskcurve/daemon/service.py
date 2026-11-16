"""D-Bus control service for the Duskcurve daemon.

SYSTEM module: handler logic in `DaemonService` is plain str/bool/int and is
fully unit-testable without a bus. Only `INTERFACE_XML` and
`export_on_connection` touch `gi`/`Gio`.
"""

import json
import os
import tempfile
from collections.abc import Callable

from duskcurve.daemon.config import Config, ConfigError, dump_config, load_config
from duskcurve.daemon.orchestrator import Orchestrator

BUS_NAME = "org.duskcurve.Daemon"
OBJECT_PATH = "/org/duskcurve/Daemon"

INTERFACE_XML = """
<node>
  <interface name="org.duskcurve.Daemon">
    <method name="GetStatus">
      <arg type="s" name="json" direction="out"/>
    </method>
    <method name="GetConfig">
      <arg type="s" name="toml" direction="out"/>
    </method>
    <method name="SetConfig">
      <arg type="s" name="toml" direction="in"/>
    </method>
    <method name="SetPaused">
      <arg type="b" name="paused" direction="in"/>
    </method>
    <method name="SetManualTemp">
      <arg type="s" name="connector" direction="in"/>
      <arg type="i" name="kelvin" direction="in"/>
      <arg type="i" name="duration_min" direction="in"/>
    </method>
    <method name="ClearManualTemp">
      <arg type="s" name="connector" direction="in"/>
    </method>
    <method name="ReloadConfig">
    </method>
    <method name="SetTheme">
      <arg type="s" name="mode" direction="in"/>
      <arg type="i" name="duration_min" direction="in"/>
    </method>
    <method name="ClearTheme">
    </method>
    <signal name="StatusChanged">
      <arg type="s" name="json"/>
    </signal>
  </interface>
</node>
"""


class DaemonService:
    """Plain (bus-free) handlers backing the org.duskcurve.Daemon interface."""

    def __init__(
        self,
        orchestrator: Orchestrator,
        config_path: str,
        on_status_changed: Callable[[str], None] | None = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._config_path = config_path
        self._on_status_changed = on_status_changed
        with open(config_path, encoding="utf-8") as f:
            self._config: Config = load_config(f.read())

    def _emit_status_changed(self) -> None:
        if self._on_status_changed is not None:
            self._on_status_changed(self.get_status())

    def get_status(self) -> str:
        return json.dumps(self._orchestrator.status())

    def get_config(self) -> str:
        return dump_config(self._config)

    def set_config(self, toml_text: str) -> None:
        cfg = load_config(toml_text)  # raises ConfigError before any mutation on invalid input

        directory = os.path.dirname(self._config_path) or "."
        fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".duskcurve-config-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(toml_text)
            os.replace(tmp_path, self._config_path)
        except BaseException:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
            raise

        self._orchestrator.set_config(cfg)
        self._config = cfg
        self._emit_status_changed()

    def set_paused(self, paused: bool) -> None:
        self._orchestrator.set_paused(paused)
        self._emit_status_changed()

    def set_manual_temp(self, connector: str, kelvin: int, duration_min: int) -> None:
        self._orchestrator.set_manual_temp(connector, kelvin, duration_min)
        self._emit_status_changed()

    def clear_manual_temp(self, connector: str) -> None:
        self._orchestrator.clear_manual_temp(connector)
        self._emit_status_changed()

    def set_theme(self, mode: str, duration_min: int) -> None:
        self._orchestrator.set_theme(mode, duration_min)
        self._emit_status_changed()

    def clear_theme(self) -> None:
        self._orchestrator.clear_theme()
        self._emit_status_changed()

    def reload_config(self) -> None:
        with open(self._config_path, encoding="utf-8") as f:
            cfg = load_config(f.read())
        self._orchestrator.set_config(cfg)
        self._config = cfg
        self._emit_status_changed()


def export_on_connection(service: DaemonService, connection) -> int:
    """Register `service` on `connection` under OBJECT_PATH. Returns the registration id."""
    from gi.repository import Gio, GLib

    node_info = Gio.DBusNodeInfo.new_for_xml(INTERFACE_XML)
    interface_info = node_info.interfaces[0]

    def handle_method_call(
        _connection,
        _sender,
        _object_path,
        _interface_name,
        method_name,
        parameters,
        invocation,
    ) -> None:
        try:
            if method_name == "GetStatus":
                result = service.get_status()
                invocation.return_value(GLib.Variant("(s)", (result,)))
            elif method_name == "GetConfig":
                result = service.get_config()
                invocation.return_value(GLib.Variant("(s)", (result,)))
            elif method_name == "SetConfig":
                (toml_text,) = parameters.unpack()
                service.set_config(toml_text)
                invocation.return_value(None)
            elif method_name == "SetPaused":
                (paused,) = parameters.unpack()
                service.set_paused(paused)
                invocation.return_value(None)
            elif method_name == "SetManualTemp":
                connector, kelvin, duration_min = parameters.unpack()
                service.set_manual_temp(connector, kelvin, duration_min)
                invocation.return_value(None)
            elif method_name == "ClearManualTemp":
                (connector,) = parameters.unpack()
                service.clear_manual_temp(connector)
                invocation.return_value(None)
            elif method_name == "ReloadConfig":
                service.reload_config()
                invocation.return_value(None)
            elif method_name == "SetTheme":
                mode, duration_min = parameters.unpack()
                service.set_theme(mode, duration_min)
                invocation.return_value(None)
            elif method_name == "ClearTheme":
                service.clear_theme()
                invocation.return_value(None)
            else:
                invocation.return_error_literal(
                    Gio.dbus_error_quark(),
                    Gio.DBusError.UNKNOWN_METHOD,
                    f"Unknown method {method_name}",
                )
        except (ConfigError, ValueError) as exc:
            invocation.return_error_literal(
                Gio.dbus_error_quark(), Gio.DBusError.INVALID_ARGS, str(exc)
            )

    return connection.register_object(
        OBJECT_PATH,
        interface_info,
        handle_method_call,
        None,
        None,
    )
