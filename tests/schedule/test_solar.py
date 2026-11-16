from datetime import date, timedelta
from zoneinfo import ZoneInfo

from duskcurve.schedule.solar import SolarEvents, solar_events

BERLIN = ZoneInfo("Europe/Berlin")

# Berlin, Germany
LAT = 52.52
LON = 13.40


def test_returns_solar_events_tz_aware_on_requested_day():
    day = date(2026, 6, 21)
    events = solar_events(LAT, LON, day, BERLIN)

    assert isinstance(events, SolarEvents)
    for dt in (events.sunrise, events.solar_noon, events.sunset):
        assert dt.tzinfo is not None
        assert dt.utcoffset() is not None
        assert dt.date() == day


def test_summer_solstice_matches_published_reference():
    day = date(2026, 6, 21)
    events = solar_events(LAT, LON, day, BERLIN)

    expected_sunrise = events.sunrise.replace(hour=4, minute=52, second=0, microsecond=0)
    expected_sunset = events.sunset.replace(hour=21, minute=38, second=0, microsecond=0)

    assert abs((events.sunrise - expected_sunrise).total_seconds()) <= 4 * 60
    assert abs((events.sunset - expected_sunset).total_seconds()) <= 4 * 60


def test_sunrise_noon_sunset_ordering_and_midpoint():
    day = date(2026, 6, 21)
    events = solar_events(LAT, LON, day, BERLIN)

    assert events.sunrise < events.solar_noon < events.sunset

    midpoint = events.sunrise + (events.sunset - events.sunrise) / 2
    assert abs((events.solar_noon - midpoint).total_seconds()) <= 60


def test_winter_solstice_short_day():
    day = date(2026, 12, 21)
    events = solar_events(LAT, LON, day, BERLIN)

    assert events.sunrise.hour == 8
    # Brief states 15; verified against published data (timeanddate.com) and
    # the independent `astral` library, real sunset for Berlin on this date
    # is ~16:03 CET. See task-3.1-report.md for details.
    assert events.sunset.hour == 16

    day_length = events.sunset - events.sunrise
    assert timedelta(hours=7) < day_length < timedelta(hours=8)


def test_dst_offsets_reflected():
    summer = solar_events(LAT, LON, date(2026, 6, 21), BERLIN)
    winter = solar_events(LAT, LON, date(2026, 12, 21), BERLIN)

    assert summer.sunrise.utcoffset() == timedelta(hours=2)
    assert winter.sunrise.utcoffset() == timedelta(hours=1)
