"""Transport seam for Mutter's DisplayConfig D-Bus, plus an in-memory fake.

The real transport (GioMutterTransport) lives in gio_transport.py and imports gi;
this module stays gi-free so the backend logic can be unit-tested without a bus.
"""
from collections.abc import Callable
from typing import Protocol, runtime_checkable


class MutterError(Exception):
    """A Mutter DisplayConfig call failed."""


class StaleSerialError(MutterError):
    """SetCrtcGamma/GetCrtcGamma was rejected because the serial was stale."""


@runtime_checkable
class MutterTransport(Protocol):
    def call(self, method: str, args: tuple) -> tuple:
        """Invoke a DisplayConfig method; return its unpacked result tuple."""
        ...

    def subscribe_monitors_changed(self, cb: Callable[[], None]) -> None:
        """Register a callback fired when Mutter emits MonitorsChanged."""
        ...


def _identity(size: int) -> list[int]:
    denom = size - 1
    return [round(i / denom * 65535) for i in range(size)]


class FakeMutterTransport:
    """In-memory stand-in shaped exactly like the real unpacked D-Bus results.

    Mirrors the target machine: outputs DP-2 (crtc id 0) and HDMI-1 (crtc id 1),
    each driven, with a configurable ramp size. `current_crtc` fields are indexes
    into the crtcs list, matching Mutter's GetResources semantics.
    """

    def __init__(self, ramp_size: int = 256):
        self.serial = 3
        self._ramp_size = ramp_size
        # crtc: (id, winsys, x, y, w, h, current_mode, transform, transforms, props)
        self._crtcs = [
            [0, 0, 1920, 0, 1920, 1080, 0, 0, [0], {}],
            [1, 0, 0, 0, 1920, 1080, 0, 0, [0], {}],
        ]
        # output: (id, winsys, current_crtc_index, possible_crtcs, name, modes, clones, props)
        self._outputs = [
            [0, 0, 0, [0], "DP-2", [0], [], {}],
            [1, 0, 1, [1], "HDMI-1", [0], [], {}],
        ]
        self._modes = [[0, 0, 1920, 1080, 60.0, 0]]
        self._gamma: dict[int, tuple[list[int], list[int], list[int]]] = {
            0: (_identity(ramp_size), _identity(ramp_size), _identity(ramp_size)),
            1: (_identity(ramp_size), _identity(ramp_size), _identity(ramp_size)),
        }
        self.set_calls: list[tuple] = []
        self._mc_cb: Callable[[], None] | None = None
        self._force_stale = False

    def call(self, method: str, args: tuple) -> tuple:
        if method == "GetResources":
            return (self.serial, self._crtcs, self._outputs, self._modes, 65535, 65535)
        if method == "GetCrtcGamma":
            _serial, crtc = args
            r, g, b = self._gamma[crtc]
            return (list(r), list(g), list(b))
        if method == "SetCrtcGamma":
            serial, crtc, red, green, blue = args
            if self._force_stale or serial != self.serial:
                self._force_stale = False
                raise StaleSerialError("stale serial")
            self._gamma[crtc] = (list(red), list(green), list(blue))
            self.set_calls.append((serial, crtc, list(red), list(green), list(blue)))
            return ()
        raise MutterError(f"unknown method: {method}")

    def subscribe_monitors_changed(self, cb: Callable[[], None]) -> None:
        self._mc_cb = cb

    # --- test helpers ---
    def emit_monitors_changed(self, new_serial: int | None = None) -> None:
        if new_serial is not None:
            self.serial = new_serial
        if self._mc_cb is not None:
            self._mc_cb()

    def force_stale_once(self) -> None:
        self._force_stale = True

    def disable_output(self, name: str) -> None:
        for out in self._outputs:
            if out[4] == name:
                out[2] = -1
