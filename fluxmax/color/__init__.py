"""flux-max color/gamma engine: Kelvin -> gamma-ramp math (pure, no system deps)."""
from fluxmax.color.blackbody import kelvin_to_rgb
from fluxmax.color.blackbody_table import WHITEPOINTS
from fluxmax.color.mired import kelvin_to_mired, lerp_kelvin, mired_to_kelvin
from fluxmax.color.presets import PRESETS, Preset
from fluxmax.color.ramp import build_ramp

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
