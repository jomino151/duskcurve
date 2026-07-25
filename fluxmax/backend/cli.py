"""fluxmax-apply: set a single monitor's color temperature (manual/dev tool)."""
import argparse
import sys

from fluxmax.backend.mutter import MutterBackend
from fluxmax.color import build_ramp


def _default_backend() -> MutterBackend:
    from fluxmax.backend.gio_transport import GioMutterTransport
    return MutterBackend(GioMutterTransport())


def main(argv: list[str] | None = None, backend: MutterBackend | None = None) -> int:
    p = argparse.ArgumentParser(prog="fluxmax-apply")
    p.add_argument("--connector", help="output name, e.g. DP-2")
    p.add_argument("--temp", type=int, help="color temperature in Kelvin")
    p.add_argument("--brightness", type=float, default=1.0)
    p.add_argument("--list", action="store_true", help="list displays and exit")
    args = p.parse_args(argv)

    be = backend or _default_backend()

    if args.list:
        for d in be.list_displays():
            print(f"{d.connector}\tcrtc={d.crtc_id}\tramp={d.ramp_size}")
        return 0

    if not args.connector or args.temp is None:
        print("error: --connector and --temp are required", file=sys.stderr)
        return 2

    display = next((d for d in be.list_displays() if d.connector == args.connector), None)
    if display is None:
        print(f"unknown connector: {args.connector}", file=sys.stderr)
        return 1

    red, green, blue = build_ramp(display.ramp_size, args.temp, args.brightness)
    be.apply_ramp(args.connector, red, green, blue)
    print(f"applied {args.temp}K to {args.connector}")
    return 0
