"""Consumer for the org.fluxmax.WindowWatcher D-Bus service.

SYSTEM module: the GNOME Shell extension that owns this bus name will
frequently be absent (not installed, disabled, or the shell restarted) — that
is a normal operating state, not an error path. Graceful degradation to
``on_change(None)`` is the default, not an exception.

Only `start()` touches `gi`/`Gio`, and only via a lazy import inside the
method, so that `import fluxmax.daemon` never pulls in `gi`.
"""

import logging
from collections.abc import Callable

from fluxmax.daemon.rules import WindowState

WATCHER_BUS_NAME = "org.fluxmax.WindowWatcher"
WATCHER_OBJECT_PATH = "/org/fluxmax/WindowWatcher"
WATCHER_INTERFACE = "org.fluxmax.WindowWatcher"

log = logging.getLogger("fluxmax.daemon.watcher")


def decode_focus_payload(app_id: str, fullscreen: bool, connector: str) -> WindowState | None:
    """Decode a FocusChanged/GetState payload into a WindowState, or None if no window."""
    if app_id.strip() == "":
        return None
    return WindowState(app_id=app_id, fullscreen=bool(fullscreen), monitor_connector=connector)


class WindowWatcher:
    """Watches org.fluxmax.WindowWatcher on the session bus and reports focus changes."""

    def __init__(self, on_change: Callable[[WindowState | None], None]) -> None:
        self._on_change = on_change
        self._connection = None
        self._watch_id: int | None = None
        self._subscription_id: int | None = None

    def start(self) -> None:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio

        connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._connection = connection

        def on_appeared(_connection, _name, _name_owner):
            try:
                try:
                    result = connection.call_sync(
                        WATCHER_BUS_NAME,
                        WATCHER_OBJECT_PATH,
                        WATCHER_INTERFACE,
                        "GetState",
                        None,
                        None,
                        Gio.DBusCallFlags.NONE,
                        1000,
                        None,
                    )
                    app_id, fullscreen, connector = result.unpack()
                    state = decode_focus_payload(app_id, fullscreen, connector)
                except Exception:
                    log.exception("WindowWatcher: GetState() failed on name-appeared")
                    state = None
                self._on_change(state)
            except Exception:
                log.exception("WindowWatcher: on_change callback raised on name-appeared")

        def on_vanished(_connection, _name):
            try:
                self._on_change(None)
            except Exception:
                log.exception("WindowWatcher: on_change callback raised on name-vanished")

        def on_focus_changed(
            _connection,
            _sender_name,
            _object_path,
            _interface_name,
            _signal_name,
            parameters,
        ):
            try:
                try:
                    app_id, fullscreen, connector = parameters.unpack()
                    state = decode_focus_payload(app_id, fullscreen, connector)
                except Exception:
                    log.exception("WindowWatcher: failed to decode FocusChanged payload")
                    state = None
                self._on_change(state)
            except Exception:
                log.exception("WindowWatcher: on_change callback raised on FocusChanged")

        self._watch_id = Gio.bus_watch_name_on_connection(
            connection,
            WATCHER_BUS_NAME,
            Gio.BusNameWatcherFlags.NONE,
            on_appeared,
            on_vanished,
        )
        self._subscription_id = connection.signal_subscribe(
            WATCHER_BUS_NAME,
            WATCHER_INTERFACE,
            "FocusChanged",
            WATCHER_OBJECT_PATH,
            None,
            Gio.DBusSignalFlags.NONE,
            on_focus_changed,
        )

    def stop(self) -> None:
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
