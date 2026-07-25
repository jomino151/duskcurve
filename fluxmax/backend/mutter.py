"""Enumerate monitors and drive per-CRTC gamma via a MutterTransport."""
from collections.abc import Callable
from dataclasses import dataclass

from fluxmax.backend.transport import MutterError, MutterTransport


@dataclass(frozen=True)
class Display:
    connector: str      # e.g. "DP-2"
    crtc_id: int        # id passed to Get/SetCrtcGamma
    ramp_size: int      # gamma ramp length for this CRTC


class MutterBackend:
    def __init__(self, transport: MutterTransport):
        self._t = transport
        self._serial: int = 0
        self._displays: dict[str, Display] = {}
        self._user_mc_cb: Callable[[], None] | None = None
        self._refresh()
        self._t.subscribe_monitors_changed(self._on_monitors_changed)

    def _refresh(self) -> None:
        serial, crtcs, outputs, _modes, _mw, _mh = self._t.call("GetResources", ())
        self._serial = serial
        displays: dict[str, Display] = {}
        for out in outputs:
            current_crtc = out[2]
            name = out[4]
            if current_crtc < 0:
                continue
            crtc_id = crtcs[current_crtc][0]
            red, _g, _b = self._t.call("GetCrtcGamma", (serial, crtc_id))
            displays[name] = Display(connector=name, crtc_id=crtc_id, ramp_size=len(red))
        self._displays = displays

    def list_displays(self) -> list[Display]:
        return list(self._displays.values())

    def _display(self, connector: str) -> Display:
        d = self._displays.get(connector)
        if d is None:
            raise MutterError(f"unknown display: {connector}")
        return d

    def subscribe_monitors_changed(self, cb: Callable[[], None]) -> None:
        self._user_mc_cb = cb

    def _on_monitors_changed(self) -> None:
        self._refresh()
        if self._user_mc_cb is not None:
            self._user_mc_cb()
