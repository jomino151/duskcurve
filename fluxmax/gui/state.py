"""The GUI's single source of local truth.

PURE module: no gi. The daemon owns the real state; this mirrors the last
config and status it reported. Views render from here and never mutate it
directly -- they build a new Config and call apply_config().

Subscription uses plain callables rather than GObject signals so the store
stays gi-free and directly testable.

last_error ownership: set by any failed parse (set_config_text, set_status_text)
or rejected apply_config; cleared only by a successful apply_config or an
explicit clear_error(). This ensures errors persist until shown to the user.
"""

import logging
from collections.abc import Callable

from fluxmax.daemon.config import Config, dump_config, load_config
from fluxmax.gui.protocol import Status, parse_status

log = logging.getLogger("fluxmax.gui.state")


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
        # A raising subscriber must not stop the others from re-rendering, and
        # must never propagate into a D-Bus callback.
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
        """Acknowledge that an error has been shown; clear it so the next one
        is visible."""
        self.last_error = None
        self._notify()

    def set_error(self, message: str) -> None:
        """Record an error that originated outside the parse/apply paths above
        (e.g. a failed D-Bus call a view made directly, bypassing apply_config).
        Mirror of clear_error(): sets last_error and notifies subscribers."""
        self.last_error = message
        self._notify()

    # -- outbound -------------------------------------------------------

    def apply_config(self, cfg: Config) -> bool:
        """Send cfg to the daemon. Optimistic: store and notify first,
        revert on failure."""
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
