"""NOAA solar-position algorithm: sunrise, solar noon and sunset for a day and place."""

import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, tzinfo


@dataclass(frozen=True)
class SolarEvents:
    sunrise: datetime
    sunset: datetime
    solar_noon: datetime


def solar_events(lat: float, lon: float, day: date, tz: tzinfo) -> SolarEvents:
    """Compute sunrise, solar noon and sunset for `day` at (lat, lon) in `tz`.

    `lat`/`lon` are in degrees, longitude east-positive. Refraction and solar
    radius are folded into the 90.833 degree zenith. Raises `ValueError` during
    midnight sun or polar night, when the hour angle has no solution.
    """
    noon_local = datetime(day.year, day.month, day.day, 12, tzinfo=tz)
    offset = noon_local.utcoffset()
    assert offset is not None  # a datetime with a real tz has an offset
    tz_hours = offset.total_seconds() / 3600

    year, month = day.year, day.month
    if month <= 2:
        year -= 1
        month += 12
    a = year // 100
    b = 2 - a + a // 4
    jd = (
        math.floor(365.25 * (year + 4716))
        + math.floor(30.6001 * (month + 1))
        + day.day
        + b
        - 1524.5
    )
    jd += (12 - tz_hours) / 24
    t = (jd - 2451545.0) / 36525

    l0 = (280.46646 + t * (36000.76983 + 0.0003032 * t)) % 360
    m = 357.52911 + t * (35999.05029 - 0.0001537 * t)
    e = 0.016708634 - t * (0.000042037 + 0.0000001267 * t)

    m_rad = math.radians(m)
    c = (
        math.sin(m_rad) * (1.914602 - t * (0.004817 + 0.000014 * t))
        + math.sin(2 * m_rad) * (0.019993 - 0.000101 * t)
        + math.sin(3 * m_rad) * 0.000289
    )

    l_true = l0 + c
    lam = l_true - 0.00569 - 0.00478 * math.sin(math.radians(125.04 - 1934.136 * t))
    lam_rad = math.radians(lam)

    eps0 = 23 + (26 + (21.448 - t * (46.815 + t * (0.00059 - 0.001813 * t))) / 60) / 60
    eps = eps0 + 0.00256 * math.cos(math.radians(125.04 - 1934.136 * t))
    eps_rad = math.radians(eps)

    decl_rad = math.asin(math.sin(eps_rad) * math.sin(lam_rad))

    y = math.tan(eps_rad / 2) ** 2
    l0_rad = math.radians(l0)
    eqtime = 4 * math.degrees(
        y * math.sin(2 * l0_rad)
        - 2 * e * math.sin(m_rad)
        + 4 * e * y * math.sin(m_rad) * math.cos(2 * l0_rad)
        - 0.5 * y * y * math.sin(4 * l0_rad)
        - 1.25 * e * e * math.sin(2 * m_rad)
    )

    lat_rad = math.radians(lat)
    ha = math.degrees(
        math.acos(
            math.cos(math.radians(90.833)) / (math.cos(lat_rad) * math.cos(decl_rad))
            - math.tan(lat_rad) * math.tan(decl_rad)
        )
    )

    noon_min = 720 - 4 * lon - eqtime + tz_hours * 60
    sunrise_min = noon_min - 4 * ha
    sunset_min = noon_min + 4 * ha

    midnight = datetime.combine(day, time(0), tz)

    def _at(minutes: float) -> datetime:
        return midnight + timedelta(seconds=round(minutes * 60))

    return SolarEvents(
        sunrise=_at(sunrise_min),
        sunset=_at(sunset_min),
        solar_noon=_at(noon_min),
    )
