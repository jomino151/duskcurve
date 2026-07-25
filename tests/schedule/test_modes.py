from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from fluxmax.schedule.curve import Curve, DayContext
from fluxmax.schedule.modes import DEFAULT_CURVE, MODES

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
