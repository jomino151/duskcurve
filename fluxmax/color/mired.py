"""Mired (micro-reciprocal-degree) conversions and mired-space interpolation."""


def kelvin_to_mired(kelvin: float) -> float:
    return 1_000_000.0 / kelvin


def mired_to_kelvin(mired: float) -> float:
    return 1_000_000.0 / mired


def _ease(t: float, easing: str) -> float:
    t = min(max(t, 0.0), 1.0)
    if easing == "linear":
        return t
    if easing == "smoothstep":
        return t * t * (3.0 - 2.0 * t)
    raise ValueError(f"unknown easing: {easing}")


def lerp_kelvin(k1: float, k2: float, t: float, easing: str = "smoothstep") -> float:
    """Interpolate from k1 to k2 by t (0..1) in mired space with the given easing."""
    te = _ease(t, easing)
    m1, m2 = kelvin_to_mired(k1), kelvin_to_mired(k2)
    return mired_to_kelvin(m1 + (m2 - m1) * te)
