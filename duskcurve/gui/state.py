"""The GUI's single source of local truth.

No gi. Mirrors the last config and status the daemon reported; views build a new Config and
call apply_config() rather than mutating it. Subscribers are plain callables.

`last_error` is set by a failed parse or a rejected apply, and cleared only by a successful
apply_config() or clear_error(), so an error persists until shown.
"""

import logging
from collections.abc import Callable

from duskcurve.daemon.config import Config, dump_config, load_config
from duskcurve.gui.protocol import Status, parse_status

log = logging.getLogger("duskcurve.gui.state")


class AppState:
    def __init__(self, apply_fn: Callable[[str], None]) -> None:
        self._apply_fn = apply_fn
        self._subscribers: list[Callable[[], None]] = []
        self.config: Config | None = None
        self.status: Status | None = None
        self.connected: bool = False
        self.last_error: str | None = None

    # -- subscription ---------------------------------------------------

    def subscribe(self, callback: Callable[[], None]) -> Callable[[], None]:
        self._subscribers.append(callback)

        def unsubscribe() -> None:
            try:
                self._subscribers.remove(callback)
            except ValueError:
                pass

        return unsubscribe

    def _notify(self) -> None:
        # A raising subscriber must not stop the others or reach a D-Bus callback.
        for callback in list(self._subscribers):
            try:
                callback()
            except Exception:
                log.exception("AppState subscriber raised")

    # -- inbound --------------------------------------------------------

    def set_config_text(self, toml_text: str) -> None:
        try:
            self.config = load_config(toml_text)
        except Exception as exc:
            log.exception("could not parse config from the daemon")
            self.last_error = str(exc)
        self._notify()

    def set_status_text(self, json_text: str) -> None:
        try:
            self.status = parse_status(json_text)
        except Exception as exc:
            log.exception("could not parse status from the daemon")
            self.last_error = str(exc)
        self._notify()

    def set_connected(self, connected: bool) -> None:
        self.connected = connected
        self._notify()

    def clear_error(self) -> None:
        """Acknowledge that the error has been shown."""
        self.last_error = None
        self._notify()

    def set_error(self, message: str) -> None:
        """Record an error from outside the parse/apply paths, e.g. a direct D-Bus call."""
        self.last_error = message
        self._notify()

    # -- outbound -------------------------------------------------------

    def apply_config(self, cfg: Config) -> bool:
        """Send cfg to the daemon. Optimistic: store and notify first, revert on failure."""
        previous = self.config
        self.config = cfg
        self._notify()
        try:
            self._apply_fn(dump_config(cfg))
        except Exception as exc:
            log.exception("the daemon rejected a config")
            self.config = previous
            self.last_error = str(exc)
            self._notify()
            return False
        self.last_error = None
        return True
