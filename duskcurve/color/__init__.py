"""Duskcurve color/gamma engine: Kelvin -> gamma-ramp math (pure, no system deps)."""

from duskcurve.color.blackbody import kelvin_to_rgb
from duskcurve.color.blackbody_table import WHITEPOINTS
from duskcurve.color.mired import kelvin_to_mired, lerp_kelvin, mired_to_kelvin
from duskcurve.color.presets import PRESETS, Preset
from duskcurve.color.ramp import build_ramp

__all__ = [
    "PRESETS",
    "WHITEPOINTS",
    "Preset",
    "build_ramp",
    "kelvin_to_mired",
    "kelvin_to_rgb",
    "lerp_kelvin",
    "mired_to_kelvin",
]
