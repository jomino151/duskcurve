"""Client for the org.duskcurve.Daemon D-Bus service.

SYSTEM module: the daemon may be absent (not started, crashed, restarting) --
a normal operating state the GUI shows as a status page, not an error path.
Only start()/stop() and the call helpers touch gi/Gio, and only via lazy
imports inside the methods, so `import duskcurve.gui` never pulls in gi.
"""

import logging
from collections.abc import Callable
from typing import Any

from duskcurve.daemon.service import BUS_NAME, OBJECT_PATH

DAEMON_BUS_NAME = BUS_NAME
DAEMON_OBJECT_PATH = OBJECT_PATH
DAEMON_INTERFACE = "org.duskcurve.Daemon"

log = logging.getLogger("duskcurve.gui.client")


class DaemonClient:
    def __init__(
        self,
        on_status: Callable[[str], None],
        on_config: Callable[[str], None],
        on_connected: Callable[[bool], None],
    ) -> None:
        self._on_status = on_status
        self._on_config = on_config
        self._on_connected = on_connected
        self._connection = None
        self._watch_id: int | None = None
        self._subscription_id: int | None = None
        self._poll_id: int | None = None

    # -- lifecycle ------------------------------------------------------

    def start(self) -> None:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio

        connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._connection = connection

        def on_appeared(_connection, _name, _owner) -> None:
            self._safe(self._on_connected, True)
            self.refresh()

        def on_vanished(_connection, _name) -> None:
            self.stop_polling()
            self._safe(self._on_connected, False)

        def on_status_changed(_c, _sender, _path, _iface, _signal, parameters) -> None:
            try:
                (payload,) = parameters.unpack()
            except Exception:
                log.exception("could not decode a StatusChanged payload")
                return
            self._safe(self._on_status, payload)
            # StatusChanged fires exactly on the events that can change the
            # config -- including an external TOML edit the daemon's file
            # watcher picked up -- so refetch it. The poll path does not.
            self._refetch_config()

        self._watch_id = Gio.bus_watch_name_on_connection(
            connection,
            DAEMON_BUS_NAME,
            Gio.BusNameWatcherFlags.NONE,
            on_appeared,
            on_vanished,
        )
        self._subscription_id = connection.signal_subscribe(
            DAEMON_BUS_NAME,
            DAEMON_INTERFACE,
            "StatusChanged",
            DAEMON_OBJECT_PATH,
            None,
            Gio.DBusSignalFlags.NONE,
            on_status_changed,
        )

    def stop(self) -> None:
        self.stop_polling()
        if self._connection is not None:
            if self._subscription_id is not None:
                self._connection.signal_unsubscribe(self._subscription_id)
                self._subscription_id = None
            if self._watch_id is not None:
                import gi

                gi.require_version("Gio", "2.0")
                from gi.repository import Gio

                Gio.bus_unwatch_name(self._watch_id)
                self._watch_id = None
            self._connection = None

    # -- calls ----------------------------------------------------------

    def _call(self, method: str, parameters, reply_type: str | None = None) -> Any:
        import gi

        gi.require_version("GLib", "2.0")
        gi.require_version("Gio", "2.0")
        from gi.repository import Gio, GLib

        if self._connection is None:
            raise RuntimeError("DaemonClient.start() has not been called")
        return self._connection.call_sync(
            DAEMON_BUS_NAME,
            DAEMON_OBJECT_PATH,
            DAEMON_INTERFACE,
            method,
            parameters,
            GLib.VariantType.new(reply_type) if reply_type else None,
            Gio.DBusCallFlags.NONE,
            5000,
            None,
        )

    def _variant(self, fmt: str, values: tuple) -> Any:
        import gi

        gi.require_version("GLib", "2.0")
        from gi.repository import GLib

        return GLib.Variant(fmt, values)

    def refresh(self) -> None:
        self.refresh_status()
        self._refetch_config()

    def refresh_status(self) -> None:
        try:
            (payload,) = self._call("GetStatus", None, "(s)").unpack()
        except Exception:
            log.exception("GetStatus failed")
            return
        self._safe(self._on_status, payload)

    def _refetch_config(self) -> None:
        try:
            (payload,) = self._call("GetConfig", None, "(s)").unpack()
        except Exception:
            log.exception("GetConfig failed")
            return
        self._safe(self._on_config, payload)

    def set_config(self, toml_text: str) -> None:
        """Send a config. Propagates the daemon's rejection to the caller."""
        self._call("SetConfig", self._variant("(s)", (toml_text,)))

    def set_paused(self, paused: bool) -> None:
        self._call("SetPaused", self._variant("(b)", (paused,)))

    def set_manual_temp(self, connector: str, kelvin: int, duration_min: int) -> None:
        self._call(
            "SetManualTemp", self._variant("(sii)", (connector, int(kelvin), int(duration_min)))
        )

    def clear_manual_temp(self, connector: str) -> None:
        self._call("ClearManualTemp", self._variant("(s)", (connector,)))

    def set_theme(self, mode: str, duration_min: int) -> None:
        self._call("SetTheme", self._variant("(si)", (mode, int(duration_min))))

    def clear_theme(self) -> None:
        self._call("ClearTheme", None)

    # -- polling --------------------------------------------------------

    def start_polling(self, interval_seconds: float) -> None:
        """Poll GetStatus. StatusChanged is event-driven -- the daemon's tick
        emits nothing -- so without this the temperature readout would sit
        frozen while the curve drifts."""
        if self._poll_id is not None:
            return
        import gi

        gi.require_version("GLib", "2.0")
        from gi.repository import GLib

        def tick() -> bool:
            self.refresh_status()
            return GLib.SOURCE_CONTINUE

        self._poll_id = GLib.timeout_add_seconds(max(1, int(interval_seconds)), tick)

    def stop_polling(self) -> None:
        if self._poll_id is None:
            return
        import gi

        gi.require_version("GLib", "2.0")
        from gi.repository import GLib

        GLib.source_remove(self._poll_id)
        self._poll_id = None

    # -- helpers --------------------------------------------------------

    def _safe(self, callback, payload) -> None:
        """Never let a GUI callback's exception escape into a Gio callback."""
        try:
            callback(payload)
        except Exception:
            log.exception("a DaemonClient callback raised")
