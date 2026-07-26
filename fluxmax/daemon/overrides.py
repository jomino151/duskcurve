"""Per-display override application logic."""

from fluxmax.color import kelvin_to_mired, mired_to_kelvin


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
        # warm_cap means never warmer (lower K) than value
        # max(target_kelvin, value) keeps target if cooler, uses value if target is warmer
        return max(target_kelvin, value)
    elif kind == "fixed":
        # fixed ignores target and returns value directly
        return value
    elif kind == "offset":
        # offset applies a mired-space delta to target
        # positive mired delta = warmer (lower K)
        mired_target = kelvin_to_mired(target_kelvin)
        mired_adjusted = mired_target + value
        return mired_to_kelvin(mired_adjusted)
    else:
        raise ValueError(f"unknown override kind: {kind!r}")
