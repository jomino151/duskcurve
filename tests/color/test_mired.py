import pytest

from fluxmax.color.mired import kelvin_to_mired, lerp_kelvin, mired_to_kelvin


def test_kelvin_mired_roundtrip():
    assert mired_to_kelvin(kelvin_to_mired(6500)) == pytest.approx(6500)


def test_known_mired_values():
    assert kelvin_to_mired(6500) == pytest.approx(153.846, abs=1e-3)
    assert kelvin_to_mired(2700) == pytest.approx(370.370, abs=1e-3)


def test_lerp_endpoints():
    assert lerp_kelvin(6500, 2700, 0.0, "linear") == pytest.approx(6500)
    assert lerp_kelvin(6500, 2700, 1.0, "linear") == pytest.approx(2700)


def test_lerp_linear_midpoint_is_mired_mean():
    mid_mired = (kelvin_to_mired(6500) + kelvin_to_mired(2700)) / 2
    assert lerp_kelvin(6500, 2700, 0.5, "linear") == pytest.approx(mired_to_kelvin(mid_mired))


def test_smoothstep_equals_linear_at_half():
    assert lerp_kelvin(6500, 2700, 0.5, "smoothstep") == pytest.approx(
        lerp_kelvin(6500, 2700, 0.5, "linear")
    )


def test_smoothstep_eases_near_ends():
    # smoothstep(0.1) < 0.1, so result is closer to k1 than linear would be
    assert lerp_kelvin(6500, 2700, 0.1, "smoothstep") > lerp_kelvin(6500, 2700, 0.1, "linear")
