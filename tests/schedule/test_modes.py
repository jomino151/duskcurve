from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from duskcurve.schedule.curve import Curve, DayContext
from duskcurve.schedule.modes import DEFAULT_CURVE, MODES
from duskcurve.schedule.solar import solar_events

BERLIN = ZoneInfo("Europe/Berlin")
DAY = date(2026, 7, 25)


def _dt(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(DAY.year, DAY.month, DAY.day, hour, minute, second, tzinfo=BERLIN)


# Summer-ish DayContext: sunrise 05:00, sunset 21:00, wake 10:00, bedtime 23:00.
CTX = DayContext(
    sunrise=_dt(5, 0),
    sunset=_dt(21, 0),
    wake=_dt(10, 0),
    bedtime=_dt(23, 0),
)

REQUIRED_MODE_KEYS = {"recommended", "reduce_eyestrain", "working_late", "classic"}


def test_modes_has_exactly_the_four_required_keys():
    assert set(MODES.keys()) == REQUIRED_MODE_KEYS


def test_modes_values_are_all_curves():
    for name, curve in MODES.items():
        assert isinstance(curve, Curve), f"MODES[{name!r}] is not a Curve"


def test_default_curve_is_a_curve():
    assert isinstance(DEFAULT_CURVE, Curve)


def test_default_curve_midday_is_day_plateau():
    # 13:00 is well within the wake(10:00) -> sunset-60(20:00) plateau.
    assert DEFAULT_CURVE.evaluate(_dt(13, 0), CTX) == pytest.approx(6500)


def test_default_curve_deep_overnight_is_night_hold():
    # 03:00 is deep in the bedtime(23:00) -> wake-30(09:30) overnight hold.
    assert DEFAULT_CURVE.evaluate(_dt(3, 0), CTX) == pytest.approx(2700)


def test_default_curve_mid_sunset_transition_is_strictly_between():
    # sunset-60 = 20:00, sunset+60 = 22:00; 21:00 sits strictly inside.
    result = DEFAULT_CURVE.evaluate(_dt(21, 0), CTX)
    assert 3400 < result < 6500


@pytest.mark.parametrize("mode_name", sorted(REQUIRED_MODE_KEYS))
def test_every_mode_evaluates_without_raising_across_a_full_day_sweep(mode_name):
    curve = MODES[mode_name]
    for hour in range(24):
        value = curve.evaluate(_dt(hour, 0), CTX)
        assert isinstance(value, float)


def test_classic_midday_is_day_plateau():
    assert MODES["classic"].evaluate(_dt(13, 0), CTX) == pytest.approx(6500)


def test_classic_deep_overnight_is_night_hold():
    assert MODES["classic"].evaluate(_dt(3, 0), CTX) == pytest.approx(2700)


# --- Regression: cross-module bug where evaluate() sorted keypoints by
# resolved absolute time instead of walking the list in authored order.
# With real solar events, event anchors can invert relative to each other
# (e.g. bedtime shortly after sunset, or a high-latitude late sunset), which
# scrambled the curve: midday read cold, overnight read wrong, and the
# evening oscillated back up before settling.


def test_default_curve_with_real_solar_events_repro_config():
    # Repro: wake 07:00, bedtime 22:00, real Berlin summer solstice sunset
    # (~21:33), which lands *after* bedtime -- the inversion that broke the
    # old sort-by-resolved-time evaluate.
    day = date(2026, 6, 21)
    se = solar_events(52.52, 13.40, day, BERLIN)
    wake = datetime(day.year, day.month, day.day, 7, 0, tzinfo=BERLIN)
    bedtime = datetime(day.year, day.month, day.day, 22, 0, tzinfo=BERLIN)
    ctx = DayContext(sunrise=se.sunrise, sunset=se.sunset, wake=wake, bedtime=bedtime)

    def _at(hour, minute=0):
        return datetime(day.year, day.month, day.day, hour, minute, tzinfo=BERLIN)

    # Midday plateau must read the day temperature, not a scrambled value.
    assert DEFAULT_CURVE.evaluate(_at(13, 0), ctx) == pytest.approx(6500, abs=5)
    # Deep overnight must read the night temperature.
    assert DEFAULT_CURVE.evaluate(_at(3, 0), ctx) == pytest.approx(2700, abs=5)

    # The evening wind-down (20:00 -> sunset+60 ~= 22:33) must be
    # monotonically non-increasing -- no oscillation back up.
    sample_times = [
        _at(20, 0),
        _at(20, 30),
        _at(21, 0),
        _at(21, 30),
        _at(22, 0),
        _at(22, 20),
        _at(22, 33),
    ]
    values = [DEFAULT_CURVE.evaluate(t, ctx) for t in sample_times]
    for prev, cur in zip(values, values[1:], strict=False):
        assert cur <= prev + 1e-6, f"evening temperature increased: {values}"


def test_default_curve_high_latitude_late_sunset_day_plateau_not_dragged_down():
    # High-latitude case: sunset 23:10, bedtime 00:30 (just after midnight),
    # wake 07:00. bedtime resolves earlier in the day than sunset+60, another
    # inversion. The daytime plateau must stay at 6500, not get dragged down
    # by a scrambled ordering.
    day = date(2026, 6, 21)
    wake = datetime(day.year, day.month, day.day, 7, 0, tzinfo=BERLIN)
    sunrise = datetime(day.year, day.month, day.day, 3, 30, tzinfo=BERLIN)
    sunset = datetime(day.year, day.month, day.day, 23, 10, tzinfo=BERLIN)
    bedtime = datetime(day.year, day.month, day.day, 0, 30, tzinfo=BERLIN)
    ctx = DayContext(sunrise=sunrise, sunset=sunset, wake=wake, bedtime=bedtime)

    def _at(hour, minute=0):
        return datetime(day.year, day.month, day.day, hour, minute, tzinfo=BERLIN)

    assert DEFAULT_CURVE.evaluate(_at(13, 0), ctx) == pytest.approx(6500, abs=5)
