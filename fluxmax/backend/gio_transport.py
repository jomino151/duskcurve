"""Real MutterTransport over the session bus via PyGObject/Gio."""
import array

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


_AQ = GLib.VariantType("aq")


def _aq(values: list[int]) -> GLib.Variant:
    # Wrap the raw uint16 bytes directly. GLib.Variant("aq", list) boxes each
    # element individually: ~70 ms for a 3 x 4096 SetCrtcGamma, vs < 1 ms here.
    # array("H") is native-endian, which is GVariant's in-memory format.
    data = GLib.Bytes.new(array.array("H", values).tobytes())
    return GLib.Variant.new_from_bytes(_AQ, data, True)


def _params(method: str, args: tuple) -> "GLib.Variant | None":
    if method == "SetCrtcGamma":
        serial, crtc, red, green, blue = args
        return GLib.Variant.new_tuple(
            GLib.Variant.new_uint32(serial),
            GLib.Variant.new_uint32(crtc),
            _aq(red),
            _aq(green),
            _aq(blue),
        )
    sig = _IN_SIG[method]
    return GLib.Variant(sig, args) if sig is not None else None


class GioMutterTransport:
    def __init__(self, connection: "Gio.DBusConnection | None" = None):
        self._conn = connection or Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def call(self, method: str, args: tuple) -> tuple:
        params = _params(method, args)
        try:
            result = self._conn.call_sync(
                _NAME, _PATH, _IFACE, method, params, None,
                Gio.DBusCallFlags.NONE, -1, None,
            )
        except GLib.Error as e:
            msg = e.message
            low = msg.lower()
            if "stale" in low or "serial" in low:
                raise StaleSerialError(msg) from e
            raise MutterError(msg) from e
        return result.unpack()

    def subscribe_monitors_changed(self, cb) -> None:
        self._sub_id = self._conn.signal_subscribe(
            _NAME, _IFACE, "MonitorsChanged", _PATH, None,
            Gio.DBusSignalFlags.NONE, lambda *a: cb(), None,
        )
