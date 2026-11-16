"""Daemon entry point.

Wires the orchestrator to Mutter, D-Bus, the config file and the GLib main loop.
"""

import logging
import os
import signal
from datetime import datetime
from zoneinfo import ZoneInfo

import gi

gi.require_version("Gio", "2.0")
gi.require_version("GLib", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

from duskcurve.backend import MutterBackend  # noqa: E402
from duskcurve.backend.desktop_theme import GioSettingsWriter, ThemeApplier  # noqa: E402
from duskcurve.backend.gio_transport import GioMutterTransport  # noqa: E402
from duskcurve.backend.nightlight import night_light_enabled, set_night_light  # noqa: E402
from duskcurve.daemon.config import default_config, dump_config, load_config  # noqa: E402
from duskcurve.daemon.orchestrator import Orchestrator  # noqa: E402
from duskcurve.daemon.rules import WindowState  # noqa: E402
from duskcurve.daemon.service import (  # noqa: E402
    BUS_NAME,
    OBJECT_PATH,
    DaemonService,
    export_on_connection,
)
from duskcurve.daemon.watcher import WindowWatcher  # noqa: E402

log = logging.getLogger("duskcurve.daemon")

_INTERFACE_NAME = "org.duskcurve.Daemon"


def _config_path() -> str:
    """~/.config/duskcurve/config.toml, honoring $XDG_CONFIG_HOME if set."""
    config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config"
    )
    return os.path.join(config_home, "duskcurve", "config.toml")


def _ensure_config_file(config_path: str) -> None:
    """Write a default config into place if none exists yet."""
    if os.path.exists(config_path):
        return
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(dump_config(default_config()))
    log.info("no config found; wrote default config to %s", config_path)


def _build_backend() -> MutterBackend:
    """MutterBackend over the session-bus transport."""
    return MutterBackend(GioMutterTransport())


class _ConfigFileWatchState:
    """Tracks the config text already applied, so a file-monitor event for it is skipped."""

    def __init__(self, initial_text: str) -> None:
        self._last_seen = initial_text

    def note_current_state(self, text: str) -> None:
        """Record `text` as applied after the daemon itself wrote it."""
        self._last_seen = text

    def is_new_change(self, text: str) -> bool:
        """True, and record `text`, if it differs from the last seen text."""
        if text == self._last_seen:
            return False
        self._last_seen = text
        return True


class _Daemon:
    """Owns the daemon's live state so lifecycle/signal callbacks can reach it."""

    def __init__(self) -> None:
        self.config_path = _config_path()
        _ensure_config_file(self.config_path)
        with open(self.config_path, encoding="utf-8") as f:
            config_text = f.read()
        self.config = load_config(config_text)

        self.backend = _build_backend()
        self.orchestrator = Orchestrator(
            self.backend,
            self.config,
            clock=lambda: datetime.now(ZoneInfo(self.config.location.timezone)),
            theme_applier=ThemeApplier(GioSettingsWriter()),
        )

        self.service = DaemonService(
            self.orchestrator, self.config_path, on_status_changed=self._emit_status_changed
        )
        self.backend.subscribe_monitors_changed(self.orchestrator.on_monitors_changed)
        self.watcher = WindowWatcher(self._on_window_changed)

        self._connection: Gio.DBusConnection | None = None
        self._registration_id: int | None = None
        self._own_name_id: int | None = None
        self._file_monitor: Gio.FileMonitor | None = None
        self._config_watch = _ConfigFileWatchState(config_text)
        self._shutting_down = False

        self.loop = GLib.MainLoop()

        # Night Light is taken over last, so a failure above has nothing to restore.
        self.night_light_was_enabled = night_light_enabled()
        set_night_light(False)

    # ------------------------------------------------------------------
    # D-Bus service lifecycle
    # ------------------------------------------------------------------

    def own_bus_name(self) -> None:
        self._own_name_id = Gio.bus_own_name(
            Gio.BusType.SESSION,
            BUS_NAME,
            Gio.BusNameOwnerFlags.NONE,
            self._on_bus_acquired,
            None,
            self._on_name_lost,
        )

    def _on_bus_acquired(self, connection: Gio.DBusConnection, _name: str) -> None:
        self._connection = connection
        self._registration_id = export_on_connection(self.service, connection)
        log.info("acquired %s, exported service at %s", BUS_NAME, OBJECT_PATH)

    def _on_name_lost(self, _connection: "Gio.DBusConnection | None", name: str) -> None:
        log.warning("could not acquire or lost bus name %s (another instance running?)", name)

    def _emit_status_signal(self, status_json: str) -> None:
        """Emit StatusChanged without touching the config mirror.

        For status changes not caused by a config write, such as window focus.
        """
        if self._connection is None:
            return
        self._connection.emit_signal(
            None,
            OBJECT_PATH,
            _INTERFACE_NAME,
            "StatusChanged",
            GLib.Variant("(s)", (status_json,)),
        )

    def _emit_status_changed(self, status_json: str) -> None:
        # Fired by DaemonService after every mutation. Refresh the config mirror so
        # the orchestrator's clock follows a changed timezone.
        self.config = load_config(self.service.get_config())

        # Record the on-disk bytes (a SetConfig writes the client's exact text, not our
        # serialisation) as applied, so the file-monitor event for our own write is
        # skipped. Only for daemon-written changes: a window event must use
        # _emit_status_signal, or it could mask a just-saved external edit.
        try:
            with open(self.config_path, encoding="utf-8") as f:
                self._config_watch.note_current_state(f.read())
        except OSError:
            log.exception("could not re-read %s after a config change", self.config_path)

        self._emit_status_signal(status_json)

    # ------------------------------------------------------------------
    # Window watcher
    # ------------------------------------------------------------------

    def _on_window_changed(
        self, window: "WindowState | None", unfocused: "tuple[WindowState, ...]" = ()
    ) -> None:
        """A focus/fullscreen/monitor change arrived from the shell extension."""
        self.orchestrator.set_window_state(window, unfocused)
        # Apply now rather than at the next tick, so a fullscreen game is not warm for seconds.
        try:
            self.orchestrator.tick()
        except Exception:
            log.exception("tick after a window change failed")
        # Not _emit_status_changed: a window event is unrelated to the config file.
        self._emit_status_signal(self.service.get_status())

    # ------------------------------------------------------------------
    # Config file watch
    # ------------------------------------------------------------------

    def watch_config_file(self) -> None:
        gfile = Gio.File.new_for_path(self.config_path)
        self._file_monitor = gfile.monitor(Gio.FileMonitorFlags.NONE, None)
        self._file_monitor.connect("changed", self._on_config_file_changed)

    def _on_config_file_changed(self, _monitor, _file, _other_file, _event_type) -> None:
        try:
            with open(self.config_path, encoding="utf-8") as f:
                text = f.read()
        except OSError:
            log.exception("could not read %s after a change notification", self.config_path)
            return

        if not self._config_watch.is_new_change(text):
            # Our own write or a duplicate notification: nothing new to reload.
            return

        try:
            self.service.reload_config()
        except Exception:
            log.exception("failed to reload config from %s", self.config_path)

    # ------------------------------------------------------------------
    # Tick timer
    # ------------------------------------------------------------------

    def tick_cb(self) -> bool:
        try:
            self.orchestrator.tick()
        except Exception:
            log.exception("tick failed")
        return GLib.SOURCE_CONTINUE

    # ------------------------------------------------------------------
    # Signals / shutdown
    # ------------------------------------------------------------------

    def install_signal_handlers(self) -> None:
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, self._on_shutdown_signal)
        GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGTERM, self._on_shutdown_signal)

    def _on_shutdown_signal(self) -> bool:
        log.info("received shutdown signal")
        self.shutdown()
        return GLib.SOURCE_REMOVE

    def shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True

        # Stop the watcher first so a late focus callback cannot re-apply gamma mid-teardown.
        try:
            self.watcher.stop()
        except Exception:
            log.exception("failed to stop the window watcher")

        for display in self.backend.list_displays():
            try:
                self.backend.reset(display.connector)
            except Exception:
                log.exception("failed to reset display %s to identity", display.connector)

        try:
            set_night_light(self.night_light_was_enabled)
        except Exception:
            log.exception("failed to restore Night Light state")

        if self._registration_id is not None and self._connection is not None:
            self._connection.unregister_object(self._registration_id)
        if self._own_name_id is not None:
            Gio.bus_unown_name(self._own_name_id)

        if self.loop.is_running():
            self.loop.quit()


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )

    # Night Light is disabled at the end of _Daemon.__init__. If a later setup step
    # raises, restore it rather than leave it off.
    daemon: _Daemon | None = None
    try:
        daemon = _Daemon()
        daemon.own_bus_name()
        daemon.watch_config_file()
        try:
            daemon.watcher.start()
        except Exception:
            # Optional: without it window rules go inert and the schedule still runs.
            log.exception("window watcher failed to start; window rules will be inert")
        # Apply gamma now, or displays sit at neutral until the first timer tick.
        daemon.tick_cb()
        GLib.timeout_add_seconds(max(1, int(daemon.config.tick_interval_seconds)), daemon.tick_cb)
        daemon.install_signal_handlers()
    except Exception:
        log.exception("failed to initialize Duskcurve daemon")
        if daemon is not None:
            try:
                set_night_light(daemon.night_light_was_enabled)
            except Exception:
                log.exception("failed to restore Night Light state after a failed startup")
        return 1

    try:
        daemon.loop.run()
    finally:
        daemon.shutdown()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
