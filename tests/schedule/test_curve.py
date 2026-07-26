from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from fluxmax.color import lerp_kelvin
from fluxmax.schedule.curve import ClockAnchor, Curve, DayContext, EventAnchor, Keypoint

BERLIN = ZoneInfo("Europe/Berlin")
DAY = date(2026, 7, 25)


def _dt(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(DAY.year, DAY.month, DAY.day, hour, minute, second, tzinfo=BERLIN)


CTX = DayContext(
    sunrise=_dt(6, 0),
    sunset=_dt(20, 0),
    wake=_dt(10, 0),
    bedtime=_dt(23, 0),
)


def test_clock_anchor_resolution_at_start_and_end():
    curve = Curve(
        [
            Keypoint(ClockAnchor(8, 0), 6500),
            Keypoint(ClockAnchor(20, 0), 3400),
        ]
    )
    assert curve.evaluate(_dt(8, 0), CTX) == pytest.approx(6500)
    assert curve.evaluate(_dt(20, 0), CTX) == pytest.approx(3400)


def test_midpoint_of_transition_is_strictly_between_and_matches_lerp():
    curve = Curve(
        [
            Keypoint(ClockAnchor(8, 0), 6500),
            Keypoint(ClockAnchor(20, 0), 3400),
        ]
    )
    result = curve.evaluate(_dt(14, 0), CTX)
    expected = lerp_kelvin(6500, 3400, 0.5, "smoothstep")
    assert result == pytest.approx(expected)
    assert 3400 < result < 6500


def test_flat_hold_between_equal_temp_keypoints():
    curve = Curve(
        [
            Keypoint(ClockAnchor(10, 0), 3400),
            Keypoint(ClockAnchor(14, 0), 3400),
        ]
    )
    for hour in (10, 11, 12, 13, 14):
        assert curve.evaluate(_dt(hour, 0), CTX) == pytest.approx(3400)


def test_event_anchor_resolves_sunset_with_offset():
    curve = Curve(
        [
            Keypoint(ClockAnchor(8, 0), 6500),
            Keypoint(EventAnchor("sunset", -60), 3400),
        ]
    )
    # sunset is 20:00, offset -60 -> resolves to 19:00. Landing exactly on
    # that keypoint's minute should return its kelvin (t=0 into the segment).
    assert curve.evaluate(_dt(19, 0), CTX) == pytest.approx(3400)


def test_wrap_around_overnight_segment():
    curve = Curve(
        [
            Keypoint(ClockAnchor(22, 0), 2700),
            Keypoint(ClockAnchor(6, 0), 6500),
        ]
    )
    result = curve.evaluate(_dt(2, 0), CTX)
    # 22:00 -> 06:00 is an 8h overnight segment; 02:00 is 4h in => t=0.5
    expected = lerp_kelvin(2700, 6500, 0.5, "smoothstep")
    assert result == pytest.approx(expected)


def test_list_order_defines_cycle_even_when_event_anchor_inverts_absolute_time():
    # List order is the source of truth for the cyclic walk. Keypoint 3
    # (bedtime, resolving to 21:30) lands chronologically *before* keypoint 2
    # (sunset+60, resolving to 22:00) -- an inversion that would scramble a
    # sort-by-resolved-minute implementation (it would reorder 21:30 ahead of
    # 22:00, producing a spurious bump back up to 3400 after already reaching
    # 2700). The new forward-monotonic-cycle evaluate must instead clamp the
    # inverted keypoint to a zero-length segment and keep descending.
    ctx = DayContext(
        sunrise=_dt(6, 0),
        sunset=_dt(21, 0),
        wake=_dt(8, 0),
        bedtime=_dt(21, 30),
    )
    curve = Curve(
        [
            Keypoint(ClockAnchor(20, 0), 6500),
            Keypoint(EventAnchor("sunset", 60), 3400),  # sunset+60 -> 22:00
            Keypoint(EventAnchor("bedtime", 0), 2700),  # bedtime -> 21:30 (earlier!)
            Keypoint(ClockAnchor(23, 0), 2700),
        ]
    )
    # The 20:00 -> 22:00 stretch must be strictly non-increasing; no bump.
    samples = [_dt(20, 0), _dt(20, 30), _dt(21, 0), _dt(21, 30), _dt(22, 0), _dt(22, 30)]
    values = [curve.evaluate(now, ctx) for now in samples]
    for prev, cur in zip(values, values[1:], strict=False):
        assert cur <= prev + 1e-9, f"evening temperature increased: {values}"
    # Endpoints still resolve to their own kelvin.
    assert curve.evaluate(_dt(20, 0), ctx) == pytest.approx(6500)
    assert curve.evaluate(_dt(22, 30), ctx) == pytest.approx(2700)
    # By 22:00 (the clamped/collapsed keypoints' shared instant) we've
    # already reached the destination temperature -- not a garbled
    # in-between value from a re-sorted segment.
    assert curve.evaluate(_dt(22, 0), ctx) == pytest.approx(2700)


def test_single_keypoint_returns_its_kelvin_everywhere():
    curve = Curve([Keypoint(ClockAnchor(12, 0), 5000)])
    assert curve.evaluate(_dt(0, 0), CTX) == pytest.approx(5000)
    assert curve.evaluate(_dt(23, 59), CTX) == pytest.approx(5000)


def test_empty_keypoint_list_raises_value_error():
    with pytest.raises(ValueError):
        Curve([])


def test_bad_event_name_raises_value_error():
    curve = Curve([Keypoint(EventAnchor("noon", 0), 5000), Keypoint(ClockAnchor(8, 0), 6500)])
    with pytest.raises(ValueError):
        curve.evaluate(_dt(9, 0), CTX)
