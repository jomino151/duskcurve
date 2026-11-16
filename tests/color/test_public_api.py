"""Test that the public API surface exports work correctly."""

from duskcurve.color import (
    PRESETS,
    WHITEPOINTS,
    Preset,
    build_ramp,
    kelvin_to_mired,
    kelvin_to_rgb,
    lerp_kelvin,
    mired_to_kelvin,
)


def test_presets_is_exported():
    from duskcurve.color.presets import PRESETS as _direct

    assert PRESETS is _direct


def test_whitepoints_is_exported():
    from duskcurve.color.blackbody_table import WHITEPOINTS as _direct

    assert WHITEPOINTS is _direct


def test_preset_class_is_exported():
    from duskcurve.color.presets import Preset as _direct

    assert Preset is _direct


def test_build_ramp_is_exported():
    from duskcurve.color.ramp import build_ramp as _direct

    assert build_ramp is _direct


def test_kelvin_to_mired_is_exported():
    from duskcurve.color.mired import kelvin_to_mired as _direct

    assert kelvin_to_mired is _direct


def test_kelvin_to_rgb_is_exported():
    from duskcurve.color.blackbody import kelvin_to_rgb as _direct

    assert kelvin_to_rgb is _direct


def test_lerp_kelvin_is_exported():
    from duskcurve.color.mired import lerp_kelvin as _direct

    assert lerp_kelvin is _direct


def test_mired_to_kelvin_is_exported():
    from duskcurve.color.mired import mired_to_kelvin as _direct

    assert mired_to_kelvin is _direct
