import pytest

from duskcurve.color import kelvin_to_mired, mired_to_kelvin
from duskcurve.daemon.overrides import apply_override


def test_none_returns_target_exactly():
    assert apply_override(6500, "none", 0) == 6500
    assert apply_override(2700, "none", 5000) == 2700
    assert apply_override(3400, "none", 999) == 3400


def test_warm_cap_caps_at_value():
    # warm_cap means never warmer (lower K) than the value
    # 2700 is warmer than 3400, so it gets capped to 3400
    assert apply_override(2700, "warm_cap", 3400) == 3400
    # 6500 is cooler than 3400, so it stays at 6500
    assert apply_override(6500, "warm_cap", 3400) == 6500


def test_fixed_returns_value():
    assert apply_override(6500, "fixed", 3000) == 3000
    assert apply_override(2700, "fixed", 5000) == 5000
    assert apply_override(4000, "fixed", 4000) == 4000


def test_offset_positive_mired_lowers_kelvin():
    # Positive mired offset = warmer (lower K)
    target = 6500
    mired_delta = 100  # positive offset
    expected = mired_to_kelvin(kelvin_to_mired(target) + mired_delta)
    assert apply_override(target, "offset", mired_delta) == pytest.approx(expected)


def test_offset_negative_mired_raises_kelvin():
    # Negative mired offset = cooler (higher K)
    target = 6500
    mired_delta = -100  # negative offset
    expected = mired_to_kelvin(kelvin_to_mired(target) + mired_delta)
    assert apply_override(target, "offset", mired_delta) == pytest.approx(expected)


def test_unknown_kind_raises_value_error():
    with pytest.raises(ValueError):
        apply_override(6500, "unknown", 3000)
    with pytest.raises(ValueError):
        apply_override(2700, "not_a_kind", 5000)
