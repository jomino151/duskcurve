"""Real MutterTransport over the session bus via PyGObject/Gio."""
import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from fluxmax.backend.transport import MutterError, StaleSerialError  # noqa: E402

_NAME = "org.gnome.Mutter.DisplayConfig"
_PATH = "/org/gnome/Mutter/DisplayConfig"
_IFACE = "org.gnome.Mutter.DisplayConfig"
_IN_SIG = {
    "GetResources": None,
    "GetCrtcGamma": "(uu)",
    "SetCrtcGamma": "(uuaqaqaq)",
}


class GioMutterTransport:
    def __init__(self, connection: "Gio.DBusConnection | None" = None):
        self._conn = connection or Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def call(self, method: str, args: tuple) -> tuple:
        sig = _IN_SIG[method]
        params = GLib.Variant(sig, args) if sig is not None else None
        try:
            result = self._conn.call_sync(
                _NAME, _PATH, _IFACE, method, params, None,
                Gio.DBusCallFlags.NONE, -1, None,
            )
        except GLib.Error as e:
            msg = e.message
            if "stale" in msg.lower() or "serial" in msg.lower():
                raise StaleSerialError(msg) from e
            raise MutterError(msg) from e
        return result.unpack()

    def subscribe_monitors_changed(self, cb) -> None:
        self._conn.signal_subscribe(
            _NAME, _IFACE, "MonitorsChanged", _PATH, None,
            Gio.DBusSignalFlags.NONE, lambda *a: cb(), None,
        )
