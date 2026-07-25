"""Named color-temperature presets (f.lux set)."""
from typing import NamedTuple


class Preset(NamedTuple):
    kelvin: int
    name: str


PRESETS: list[Preset] = [
    Preset(1200, "Ember"),
    Preset(1900, "Candle"),
    Preset(2300, "Dim Incandescent"),
    Preset(2700, "Incandescent"),
    Preset(3400, "Halogen"),
    Preset(4200, "Fluorescent"),
    Preset(5500, "Sunlight"),
    Preset(6500, "Daylight"),
]
