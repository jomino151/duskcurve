"""Kelvin -> normalized RGB white point, from redshift's blackbody table."""

from duskcurve.color.blackbody_table import WHITEPOINTS

_MIN_K = 1000
_MAX_K = 10000
_STEP = 100


def kelvin_to_rgb(kelvin: float) -> tuple[float, float, float]:
    """Interpolate the blackbody white point for `kelvin`, clamped to 1000-10000 K."""
    k = min(max(kelvin, _MIN_K), _MAX_K)
    lo = int(k // _STEP) * _STEP
    if lo == k or lo == _MAX_K:
        return WHITEPOINTS[int(k)] if k in WHITEPOINTS else WHITEPOINTS[lo]
    hi = lo + _STEP
    frac = (k - lo) / _STEP
    a, b = WHITEPOINTS[lo], WHITEPOINTS[hi]
    r, g, bl = (x + (y - x) * frac for x, y in zip(a, b, strict=True))
    return r, g, bl
