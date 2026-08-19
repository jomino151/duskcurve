"""flux-max daemon entry point.

SYSTEM module: wires the pure core (fluxmax.daemon.config, .orchestrator,
.service) to the real world -- the real Mutter D-Bus backend, GNOME Night
Light takeover, the org.fluxmax.Daemon D-Bus service, config-file watching,
the org.fluxmax.WindowWatcher subscription (focused-window rules), and the
GLib main loop. Verified by import + the M4 milestone-E manual/systemd run,
not by a full behavioral unit test (see tests/daemon/test_main_smoke.py for
the one live-dispatch smoke test that *is* covered).
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

from fluxmax.backend import MutterBackend  # noqa: E402
from fluxmax.backend.desktop_theme import GioSettingsWriter, ThemeApplier  # noqa: E402
from fluxmax.backend.gio_transport import GioMutterTransport  # noqa: E402
from fluxmax.backend.nightlight import night_light_enabled, set_night_light  # noqa: E402
from fluxmax.daemon.config import default_config, dump_config, load_config  # noqa: E402
from fluxmax.daemon.orchestrator import Orchestrator  # noqa: E402
from fluxmax.daemon.rules import WindowState  # noqa: E402
from fluxmax.daemon.service import (  # noqa: E402
    BUS_NAME,
    OBJECT_PATH,
    DaemonService,
    export_on_connection,
)
from fluxmax.daemon.watcher import WindowWatcher  # noqa: E402

log = logging.getLogger("fluxmax.daemon")

_INTERFACE_NAME = "org.fluxmax.Daemon"


def _config_path() -> str:
    """~/.config/fluxmax/config.toml, honoring $XDG_CONFIG_HOME if set."""
    config_home = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config"
    )
    return os.path.join(config_home, "fluxmax", "config.toml")


def _ensure_config_file(config_path: str) -> None:
    """Write a default config into place if none exists yet."""
    if os.path.exists(config_path):
        return
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        f.write(dump_config(default_config()))
    log.info("no config found; wrote default config to %s", config_path)


def _build_backend() -> MutterBackend:
    """Real MutterBackend over the real session-bus transport (mirrors fluxmax-apply)."""
    return MutterBackend(GioMutterTransport())


class _ConfigFileWatchState:
    """Dedup state for the config-file watcher (pure, no gi -- unit-testable directly).

    Tracks the config-file text the daemon has already accounted for -- either
    because it just wrote it itself (a D-Bus SetConfig) or already reloaded it
    -- so a GFileMonitor notification carrying that exact same content is
    recognized as already-applied rather than triggering a second, redundant
    `service.reload_config()` (and a duplicate StatusChanged emission).
    """

    def __init__(self, initial_text: str) -> None:
        self._last_seen = initial_text

    def note_current_state(self, text: str) -> None:
        """Call whenever the daemon itself is the reason the file now reads
        `text` (e.g. right after any DaemonService mutation), so a subsequent
        file-monitor notification for that same content is not mistaken for a
        new external edit."""
        self._last_seen = text

    def is_new_change(self, text: str) -> bool:
        """True (and records `text` as seen) iff `text` differs from what was
        last seen -- i.e. this is a genuine, not-yet-applied change."""
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

        # Night Light takeover is the LAST step of construction: everything above
        # can fail without having touched Night Light at all, so nothing needs
        # restoring in that case. (main() additionally guards the setup calls
        # that happen *after* this constructor returns -- own_bus_name(),
        # watch_config_file(), etc. -- restoring Night Light if any of those
        # fail post-takeover.)
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
        """Bare StatusChanged emitter -- no config-mirror side effects.

        Use this (not `_emit_status_changed`) for any caller whose status
        change was NOT caused by the daemon itself writing the config file --
        e.g. a window-focus event. See `_emit_status_changed` for why the two
        must stay separate.
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
        # DaemonService fires this after every mutation (SetConfig, ReloadConfig,
        # SetPaused, ...). Refresh our own config mirror here so the orchestrator's
        # `clock` closure (which reads self.config.location.timezone) picks up a
        # changed timezone/location instead of being stuck with the config from
        # daemon startup.
        self.config = load_config(self.service.get_config())

        # Re-read the actual on-disk bytes (not dump_config(self.config) -- a
        # SetConfig writes the client's exact submitted text, which need not be
        # byte-identical to our canonical serialization) and tell the config-file
        # watcher this is the current, already-applied state. Without this, the
        # GFileMonitor "changed" event that a SetConfig's write triggers would be
        # seen as a *new* external edit and cause a redundant reload_config() +
        # a second StatusChanged for the same change.
        #
        # CAUTION: this bookkeeping must ONLY run for changes the daemon itself
        # wrote to the config file (SetConfig et al). A window-focus event is
        # NOT such a change -- it must call `_emit_status_signal` directly
        # instead of this method. If a focus change ran this method, it would
        # stamp `note_current_state` with whatever the config file currently
        # contains, which can race a pending-but-not-yet-applied external edit
        # (the user just saved new rules; the GFileMonitor "changed"
        # notification for that edit hasn't fired yet) and cause that edit's
        # `reload_config()` to be silently skipped as "already applied". Do
        # not "simplify" this back into one method.
        try:
            with open(self.config_path, encoding="utf-8") as f:
                self._config_watch.note_current_state(f.read())
        except OSError:
            log.exception("could not re-read %s after a config change", self.config_path)

        self._emit_status_signal(status_json)

    # ------------------------------------------------------------------
    # Window watcher
    # ------------------------------------------------------------------

    def _on_window_changed(self, window: "WindowState | None") -> None:
        """A focus/fullscreen/monitor change arrived from the shell extension."""
        self.orchestrator.set_window_state(window)
        # Apply immediately rather than waiting up to tick_interval_seconds -- a
        # fullscreen game should not sit warm for two seconds after launching.
        try:
            self.orchestrator.tick()
        except Exception:
            log.exception("tick after a window change failed")
        # Bare signal emit only -- NOT _emit_status_changed(), which would also
        # mirror the on-disk config into the file-watcher's dedup state. A
        # window event has nothing to do with the config file; running that
        # bookkeeping here could mask a just-saved, not-yet-reloaded external
        # edit (see _emit_status_changed's docstring).
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
            # Either our own atomic write (e.g. a SetConfig via D-Bus, already
            # accounted for in _emit_status_changed) or a duplicate notification
            # for the same change -- nothing new to reload.
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

        # Stop the window watcher first, before displays are reset: otherwise a
        # late FocusChanged callback could re-apply gamma via _on_window_changed
        # after (or interleaved with) teardown below.
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

    # Night Light is taken over as the LAST step of _Daemon.__init__, so if
    # construction itself fails, Night Light was never touched -- `daemon` stays
    # unbound and there's nothing to restore. But own_bus_name()/watch_config_file()/
    # install_signal_handlers() run *after* a successfully constructed daemon (Night
    # Light already off); if any of those raise, restore Night Light before bailing
    # out rather than leaving it permanently disabled.
    daemon: _Daemon | None = None
    try:
        daemon = _Daemon()
        daemon.own_bus_name()
        daemon.watch_config_file()
        try:
            daemon.watcher.start()
        except Exception:
            # The window watcher is optional by design (graceful degradation:
            # window rules go inert, the schedule keeps running unchanged) --
            # unlike own_bus_name()/watch_config_file() above, its failure
            # must not abort daemon startup or revert Night Light.
            log.exception("window watcher failed to start; window rules will be inert")
        # Apply gamma once immediately, rather than waiting for the first
        # timer tick (up to tick_interval_seconds away): otherwise displays
        # sit at neutral 6500 K -- a brief flash -- for that whole interval
        # right after Night Light was just disabled above.
        daemon.tick_cb()
        GLib.timeout_add_seconds(
            max(1, int(daemon.config.tick_interval_seconds)), daemon.tick_cb
        )
        daemon.install_signal_handlers()
    except Exception:
        log.exception("failed to initialize flux-max daemon")
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
