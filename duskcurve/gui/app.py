"""GUI entry point: the `duskcurve-gui` console script.

Like `window.py`, it imports `gi` at module scope; the gi-free modules never import it.
"""

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, Gio  # noqa: E402

from duskcurve.gui.client import DaemonClient  # noqa: E402
from duskcurve.gui.state import AppState  # noqa: E402
from duskcurve.gui.window import DuskcurveWindow  # noqa: E402

APPLICATION_ID = "org.duskcurve.gui"


def _build_state_and_client() -> tuple[AppState, DaemonClient]:
    # AppState needs client.set_config and the client needs callbacks into AppState.
    # The callbacks read `state_box` at call time; none fires before client.start().
    state_box: list[AppState | None] = [None]

    def on_status(payload: str) -> None:
        assert state_box[0] is not None
        state_box[0].set_status_text(payload)

    def on_config(payload: str) -> None:
        assert state_box[0] is not None
        state_box[0].set_config_text(payload)

    def on_connected(connected: bool) -> None:
        assert state_box[0] is not None
        state_box[0].set_connected(connected)

    client = DaemonClient(on_status=on_status, on_config=on_config, on_connected=on_connected)
    state = AppState(apply_fn=client.set_config)
    state_box[0] = state
    return state, client


class DuskcurveApplication(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=APPLICATION_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_activate(self) -> None:
        window = self.props.active_window
        if window is None:
            state, client = _build_state_and_client()
            client.start()
            window = DuskcurveWindow(application=self, state=state, client=client)
        window.present()


def main() -> int:
    return DuskcurveApplication().run(sys.argv)
