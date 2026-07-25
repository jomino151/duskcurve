"""Build per-channel gamma ramps from a color temperature."""
from fluxmax.color.blackbody import kelvin_to_rgb

_MAX = 65535


def build_ramp(
    size: int, kelvin: float, brightness: float = 1.0
) -> tuple[list[int], list[int], list[int]]:
    """A linear 0..1 base ramp scaled by the blackbody white point and brightness."""
    if size < 2:
        raise ValueError(f"ramp size must be >= 2, got {size}")
    wr, wg, wb = kelvin_to_rgb(kelvin)
    denom = size - 1
    out: list[list[int]] = []
    for weight in (wr, wg, wb):
        scale = weight * brightness
        out.append([min(_MAX, round((i / denom) * scale * _MAX)) for i in range(size)])
    return out[0], out[1], out[2]
