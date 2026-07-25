import pytest

from fluxmax.color.ramp import build_ramp


def test_lengths_match_size():
    r, g, b = build_ramp(256, 6500)
    assert len(r) == len(g) == len(b) == 256


def test_values_are_uint16():
    for chan in build_ramp(1024, 3400):
        assert all(0 <= v <= 65535 for v in chan)
        assert all(isinstance(v, int) for v in chan)


def test_endpoints_start_at_zero():
    r, g, b = build_ramp(256, 3400)
    assert r[0] == g[0] == b[0] == 0


def test_red_channel_identity_at_daylight():
    # 6500 K white point red == 1.0, so the red ramp is a plain linear 0..65535 ramp
    r, _, _ = build_ramp(256, 6500)
    expected = [round(i / 255 * 65535) for i in range(256)]
    assert r == expected


def test_warmer_reduces_blue_relative_to_red():
    r, _, b = build_ramp(256, 3400)
    assert b[-1] < r[-1]


def test_brightness_scales_top_end():
    r, _, _ = build_ramp(256, 6500, brightness=0.5)
    assert r[-1] == pytest.approx(round(65535 * 0.5), abs=1)


def test_size_below_two_raises():
    with pytest.raises(ValueError, match="ramp size must be >= 2"):
        build_ramp(1, 6500)


def test_brightness_above_one_clamps_to_max():
    r, _, _ = build_ramp(256, 6500, brightness=2.0)
    assert max(r) == 65535
    assert all(0 <= v <= 65535 for v in r)
