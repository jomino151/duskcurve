"""flux-max daemon entry point.

SYSTEM module: wires the pure core (fluxmax.daemon.config, .orchestrator,
.service) to the real world -- the real Mutter D-Bus backend, GNOME Night
Light takeover, the org.fluxmax.Daemon D-Bus service, config-file watching,
and the GLib main loop. Verified by import + the M4 milestone-E
manual/systemd run, not by a full behavioral unit test (see
tests/daemon/test_main_smoke.py for the one live-dispatch smoke test that
*is* covered).
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
from fluxmax.backend.gio_transport import GioMutterTransport  # noqa: E402
from fluxmax.backend.nightlight import night_light_enabled, set_night_light  # noqa: E402
from fluxmax.daemon.config import Config, default_config, dump_config, load_config  # noqa: E402
from fluxmax.daemon.orchestrator import Orchestrator  # noqa: E402
from fluxmax.daemon.service import (  # noqa: E402
    BUS_NAME,
    OBJECT_PATH,
    DaemonService,
    export_on_connection,
)

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


def _read_config(config_path: str) -> Config:
    with open(config_path, encoding="utf-8") as f:
        return load_config(f.read())


def _build_backend() -> MutterBackend:
    """Real MutterBackend over the real session-bus transport (mirrors fluxmax-apply)."""
    return MutterBackend(GioMutterTransport())


class _Daemon:
    """Owns the daemon's live state so lifecycle/signal callbacks can reach it."""

    def __init__(self) -> None:
        self.config_path = _config_path()
        _ensure_config_file(self.config_path)
        self.config = _read_config(self.config_path)

        self.backend = _build_backend()
        self.orchestrator = Orchestrator(
            self.backend,
            self.config,
            clock=lambda: datetime.now(ZoneInfo(self.config.location.timezone)),
        )

        # Night Light takeover: remember the prior setting so we can restore it on exit.
        self.night_light_was_enabled = night_light_enabled()
        set_night_light(False)

        self.service = DaemonService(
            self.orchestrator, self.config_path, on_status_changed=self._emit_status_changed
        )
        self.backend.subscribe_monitors_changed(self.orchestrator.on_monitors_changed)

        self._connection: Gio.DBusConnection | None = None
        self._registration_id: int | None = None
        self._own_name_id: int | None = None
        self._file_monitor: Gio.FileMonitor | None = None
        self._last_seen_config_text = dump_config(self.config)
        self._shutting_down = False

        self.loop = GLib.MainLoop()

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

    def _emit_status_changed(self, status_json: str) -> None:
        # DaemonService fires this after every mutation (SetConfig, ReloadConfig,
        # SetPaused, ...). Refresh our own config mirror here so the orchestrator's
        # `clock` closure (which reads self.config.location.timezone) picks up a
        # changed timezone/location instead of being stuck with the config from
        # daemon startup.
        self.config = load_config(self.service.get_config())
        if self._connection is None:
            return
        self._connection.emit_signal(
            None,
            OBJECT_PATH,
            _INTERFACE_NAME,
            "StatusChanged",
            GLib.Variant("(s)", (status_json,)),
        )

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

        if text == self._last_seen_config_text:
            # Either our own atomic write (e.g. a SetConfig via D-Bus) or a duplicate
            # notification for the same change -- nothing new to reload.
            return
        self._last_seen_config_text = text

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

    try:
        daemon = _Daemon()
    except Exception:
        log.exception("failed to initialize flux-max daemon")
        return 1

    daemon.own_bus_name()
    daemon.watch_config_file()
    GLib.timeout_add_seconds(max(1, int(daemon.config.tick_interval_seconds)), daemon.tick_cb)
    daemon.install_signal_handlers()

    try:
        daemon.loop.run()
    finally:
        daemon.shutdown()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
