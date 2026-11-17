import pytest

from duskcurve.color.blackbody import kelvin_to_rgb


def test_1000k_is_deep_red():
    r, g, b = kelvin_to_rgb(1000)
    assert r == 1.0
    assert b == 0.0


def test_all_channels_in_unit_range():
    for k in range(1000, 10001, 250):
        for c in kelvin_to_rgb(k):
            assert 0.0 <= c <= 1.0


def test_blue_is_monotonic_non_decreasing():
    blues = [kelvin_to_rgb(k)[2] for k in range(1500, 6600, 100)]
    assert blues == sorted(blues)


def test_exact_row_returned_at_multiple_of_100():
    from duskcurve.color.blackbody_table import WHITEPOINTS

    assert kelvin_to_rgb(3400) == WHITEPOINTS[3400]


def test_interpolates_linearly_between_rows():
    from duskcurve.color.blackbody_table import WHITEPOINTS

    lo, hi = WHITEPOINTS[3400], WHITEPOINTS[3500]
    mid = kelvin_to_rgb(3450)
    for m, a, b in zip(mid, lo, hi, strict=True):
        assert m == pytest.approx((a + b) / 2, abs=1e-9)


def test_clamps_out_of_range():
    assert kelvin_to_rgb(500) == kelvin_to_rgb(1000)
    assert kelvin_to_rgb(20000) == kelvin_to_rgb(10000)
