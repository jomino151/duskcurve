"""Enumerate monitors and drive per-CRTC gamma via a MutterTransport."""

from collections.abc import Callable
from dataclasses import dataclass

from duskcurve.backend.transport import (
    MutterError,
    MutterTransport,
    StaleSerialError,
)
from duskcurve.color import build_ramp


@dataclass(frozen=True)
class Display:
    connector: str  # e.g. "DP-2"
    crtc_id: int  # id passed to Get/SetCrtcGamma
    ramp_size: int  # gamma ramp length for this CRTC


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
        """Store exactly one callback; a second call replaces the first, it does not add one."""
        self._user_mc_cb = cb

    def _on_monitors_changed(self) -> None:
        self._refresh()
        if self._user_mc_cb is not None:
            self._user_mc_cb()

    def get_gamma(self, connector: str) -> tuple[list[int], list[int], list[int]]:
        d = self._display(connector)
        r, g, b = self._t.call("GetCrtcGamma", (self._serial, d.crtc_id))
        return list(r), list(g), list(b)

    def apply_ramp(
        self,
        connector: str,
        red: list[int],
        green: list[int],
        blue: list[int],
    ) -> None:
        """Retries once on StaleSerialError after a refresh; a second consecutive
        StaleSerialError propagates to the caller."""
        d = self._display(connector)
        try:
            self._t.call("SetCrtcGamma", (self._serial, d.crtc_id, red, green, blue))
        except StaleSerialError:
            self._refresh()
            d = self._display(connector)
            self._t.call("SetCrtcGamma", (self._serial, d.crtc_id, red, green, blue))

    def reset(self, connector: str) -> None:
        size = self._display(connector).ramp_size
        red, green, blue = build_ramp(size, 6500)  # 6500K whitepoint (1,1,1) => identity ramp
        self.apply_ramp(connector, red, green, blue)
