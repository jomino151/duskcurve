"""duskcurve-apply: set the color temperature of one display or all of them."""

import argparse
import sys

from duskcurve.backend.mutter import MutterBackend
from duskcurve.color import build_ramp


def _default_backend() -> MutterBackend:
    from duskcurve.backend.gio_transport import GioMutterTransport

    return MutterBackend(GioMutterTransport())


def main(argv: list[str] | None = None, backend: MutterBackend | None = None) -> int:
    p = argparse.ArgumentParser(prog="duskcurve-apply")
    p.add_argument("--connector", help="output name, e.g. DP-2; omit to apply to every display")
    p.add_argument("--temp", type=int, help="color temperature in Kelvin")
    p.add_argument("--brightness", type=float, default=1.0)
    p.add_argument("--list", action="store_true", help="list displays and exit")
    args = p.parse_args(argv)

    be = backend or _default_backend()

    if args.list:
        for d in be.list_displays():
            print(f"{d.connector}\tcrtc={d.crtc_id}\tramp={d.ramp_size}")
        return 0

    if args.temp is None:
        print("error: --temp is required", file=sys.stderr)
        return 2

    displays = be.list_displays()
    if args.connector:
        displays = [d for d in displays if d.connector == args.connector]
        if not displays:
            print(f"unknown connector: {args.connector}", file=sys.stderr)
            return 1

    for display in displays:
        red, green, blue = build_ramp(display.ramp_size, args.temp, args.brightness)
        be.apply_ramp(display.connector, red, green, blue)
        print(f"applied {args.temp}K to {display.connector}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
