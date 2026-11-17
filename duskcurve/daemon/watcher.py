"""Consumer for the org.duskcurve.WindowWatcher D-Bus service.

The extension owning that name is often absent; that is normal and reports `on_change(None, ())`.
Protocol negotiation is in `_negotiate`. Only `start()` imports `gi`, lazily.
"""

import logging
from collections.abc import Callable, Sequence
from typing import Any

from duskcurve.daemon.rules import WindowState

WATCHER_BUS_NAME = "org.duskcurve.WindowWatcher"
WATCHER_OBJECT_PATH = "/org/duskcurve/WindowWatcher"
WATCHER_INTERFACE = "org.duskcurve.WindowWatcher"

log = logging.getLogger("duskcurve.daemon.watcher")


def decode_focus_payload(app_id: str, fullscreen: bool, connector: str) -> WindowState | None:
    """Decode a FocusChanged/GetState payload into a WindowState, or None if no window."""
    if app_id.strip() == "":
        return None
    return WindowState(app_id=app_id, fullscreen=bool(fullscreen), monitor_connector=connector)


def decode_windows_payload(
    entries: Sequence[tuple[str, bool, bool, str]],
) -> tuple[WindowState | None, tuple[WindowState, ...]]:
    """Decode a GetWindows/WindowsChanged payload into (focused, unfocused).

    Entries are ``(app_id, fullscreen, focused, monitor_connector)``. Blank app_ids are
    dropped. The first focused entry is the focused window; the rest go to ``unfocused``
    in payload order.
    """
    focused: WindowState | None = None
    unfocused: list[WindowState] = []
    for app_id, fullscreen, is_focused, connector in entries:
        state = decode_focus_payload(app_id, fullscreen, connector)
        if state is None:
            continue
        if is_focused and focused is None:
            focused = state
        else:
            unfocused.append(state)
    return focused, tuple(unfocused)


class WindowWatcher:
    """Watches org.duskcurve.WindowWatcher on the session bus and reports focus changes."""

    def __init__(
        self, on_change: Callable[[WindowState | None, tuple[WindowState, ...]], None]
    ) -> None:
        self._on_change = on_change
        self._connection = None
        self._watch_id: int | None = None
        self._subscription_ids: list[int] = []
        self._modern = False

    def _deliver(self, focused: WindowState | None, unfocused: tuple[WindowState, ...]) -> None:
        try:
            self._on_change(focused, unfocused)
        except Exception:
            log.exception("WindowWatcher: on_change callback raised")

    def _negotiate(self, call) -> None:
        """Pick the protocol on name-appeared.

        ``call(method_name)`` returns the unpacked result: entry tuples for ``GetWindows``,
        ``(app_id, fullscreen, connector)`` for ``GetState``.
        """
        try:
            entries = call("GetWindows")
            focused, unfocused = decode_windows_payload(entries)
        except Exception as exc:
            log.info(
                "WindowWatcher: GetWindows unavailable, falling back to legacy protocol: %s", exc
            )
            self._modern = False
            try:
                app_id, fullscreen, connector = call("GetState")
                state = decode_focus_payload(app_id, fullscreen, connector)
            except Exception:
                log.exception("WindowWatcher: GetState() failed on name-appeared")
                self._deliver(None, ())
                return
            self._deliver(state, ())
            return
        self._modern = True
        self._deliver(focused, unfocused)

    def _handle_windows_changed(self, entries) -> None:
        if not self._modern:
            return
        try:
            focused, unfocused = decode_windows_payload(entries)
        except Exception:
            log.exception("WindowWatcher: failed to decode WindowsChanged payload")
            focused, unfocused = None, ()
        self._deliver(focused, unfocused)

    def _handle_focus_changed(self, app_id, fullscreen, connector) -> None:
        if self._modern:
            return
        try:
            state = decode_focus_payload(app_id, fullscreen, connector)
        except Exception:
            log.exception("WindowWatcher: failed to decode FocusChanged payload")
            state = None
        self._deliver(state, ())

    def start(self) -> None:
        import gi

        gi.require_version("Gio", "2.0")
        from gi.repository import Gio

        connection = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self._connection = connection

        def call(method_name: str) -> Any:
            result = connection.call_sync(
                WATCHER_BUS_NAME,
                WATCHER_OBJECT_PATH,
                WATCHER_INTERFACE,
                method_name,
                None,
                None,
                Gio.DBusCallFlags.NONE,
                1000,
                None,
            )
            unpacked = result.unpack()
            return unpacked[0] if len(unpacked) == 1 else unpacked

        def on_appeared(_connection, _name, _name_owner) -> None:
            self._negotiate(call)

        def on_vanished(_connection, _name) -> None:
            self._modern = False
            self._deliver(None, ())

        def on_focus_changed(
            _connection,
            _sender_name,
            _object_path,
            _interface_name,
            _signal_name,
            parameters,
        ) -> None:
            try:
                app_id, fullscreen, connector = parameters.unpack()
            except Exception:
                log.exception("WindowWatcher: failed to unpack FocusChanged payload")
                return
            self._handle_focus_changed(app_id, fullscreen, connector)

        def on_windows_changed(
            _connection,
            _sender_name,
            _object_path,
            _interface_name,
            _signal_name,
            parameters,
        ) -> None:
            try:
                entries = parameters.unpack()[0]
            except Exception:
                log.exception("WindowWatcher: failed to unpack WindowsChanged payload")
                return
            self._handle_windows_changed(entries)

        self._subscription_ids = [
            connection.signal_subscribe(
                WATCHER_BUS_NAME,
                WATCHER_INTERFACE,
                "FocusChanged",
                WATCHER_OBJECT_PATH,
                None,
                Gio.DBusSignalFlags.NONE,
                on_focus_changed,
            ),
            connection.signal_subscribe(
                WATCHER_BUS_NAME,
                WATCHER_INTERFACE,
                "WindowsChanged",
                WATCHER_OBJECT_PATH,
                None,
                Gio.DBusSignalFlags.NONE,
                on_windows_changed,
            ),
        ]
        self._watch_id = Gio.bus_watch_name_on_connection(
            connection,
            WATCHER_BUS_NAME,
            Gio.BusNameWatcherFlags.NONE,
            on_appeared,
            on_vanished,
        )

    def stop(self) -> None:
        if self._connection is not None:
            for subscription_id in self._subscription_ids:
                self._connection.signal_unsubscribe(subscription_id)
            self._subscription_ids = []
            if self._watch_id is not None:
                import gi

                gi.require_version("Gio", "2.0")
                from gi.repository import Gio

                Gio.bus_unwatch_name(self._watch_id)
                self._watch_id = None
            self._connection = None
