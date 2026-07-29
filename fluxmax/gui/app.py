"""GUI entry point: `fluxmax-gui` console script.

Like `window.py`, this module is allowed `gi` at module scope -- it and
`window.py` sit outside the gi-free contract enforced by
`tests/gui/test_no_gi_import.py` (never imported from `fluxmax.gui.__init__`
or a pure module).

`AppState` and `DaemonClient` have a circular constructor dependency:
`AppState(apply_fn=...)` needs the client's `set_config`, and `DaemonClient`
needs callbacks that feed the state back in. `_build_state_and_client()`
resolves it with a one-element list used as a mutable box: the client is
built first with closures that read `state_box[0]` at call time rather than
at closure-creation time, `AppState` is built second and dropped into the
box immediately after, and only then is anything capable of invoking a
callback (`client.start()`, called by the caller after this returns). No
callback fires before the box is filled.
"""

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio  # noqa: E402

from fluxmax.gui.client import DaemonClient  # noqa: E402
from fluxmax.gui.state import AppState  # noqa: E402
from fluxmax.gui.window import FluxMaxWindow  # noqa: E402

APPLICATION_ID = "org.fluxmax.gui"


def _build_state_and_client() -> tuple[AppState, DaemonClient]:
    state_box: list[AppState | None] = [None]

    def on_status(payload: str) -> None:
        state_box[0].set_status_text(payload)

    def on_config(payload: str) -> None:
        state_box[0].set_config_text(payload)

    def on_connected(connected: bool) -> None:
        state_box[0].set_connected(connected)

    client = DaemonClient(on_status=on_status, on_config=on_config, on_connected=on_connected)
    state = AppState(apply_fn=client.set_config)
    state_box[0] = state
    return state, client


class FluxMaxApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(
            application_id=APPLICATION_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS
        )

    def do_activate(self) -> None:
        window = self.props.active_window
        if window is None:
            state, client = _build_state_and_client()
            client.start()
            window = FluxMaxWindow(application=self, state=state, client=client)
        window.present()


def main() -> int:
    return FluxMaxApplication().run(sys.argv)
