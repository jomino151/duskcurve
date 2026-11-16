"""Per-display override application logic."""

from duskcurve.color import kelvin_to_mired, mired_to_kelvin


def apply_override(target_kelvin: float, kind: str, value: float) -> float:
    """Apply an override to a target Kelvin temperature.

    Args:
        target_kelvin: The target color temperature in Kelvin.
        kind: The type of override ("none", "warm_cap", "fixed", "offset").
        value: The override value; interpretation depends on kind.

    Returns:
        The adjusted Kelvin temperature.

    Raises:
        ValueError: If kind is not recognized.
    """
    if kind == "none":
        return target_kelvin
    elif kind == "warm_cap":
        # Never warmer (lower K) than value.
        return max(target_kelvin, value)
    elif kind == "fixed":
        return value
    elif kind == "offset":
        # Mired-space delta; positive is warmer.
        mired_target = kelvin_to_mired(target_kelvin)
        mired_adjusted = mired_target + value
        return mired_to_kelvin(mired_adjusted)
    else:
        raise ValueError(f"unknown override kind: {kind!r}")
