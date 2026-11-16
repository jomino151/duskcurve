from datetime import datetime
from zoneinfo import ZoneInfo

from duskcurve.schedule.curve import ClockAnchor, DayContext, EventAnchor
from duskcurve.theme import DARK, LIGHT, ThemeVariant, desired_mode

BERLIN = ZoneInfo("Europe/Berlin")


def _dt(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 8, 16, hour, minute, second, tzinfo=BERLIN)


def _ctx() -> DayContext:
    # Real Berlin values for 2026-08-16: sunrise 06:00, sunset 20:40.
    return DayContext(
        sunrise=_dt(6, 0),
        sunset=_dt(20, 40),
        wake=_dt(10, 0),
        bedtime=_dt(23, 0),
    )


SUNRISE = EventAnchor("sunrise", 0)
SUNSET = EventAnchor("sunset", 0)


def test_midday_is_light_and_deep_night_is_dark():
    ctx = _ctx()
    assert desired_mode(_dt(13, 0), ctx, SUNRISE, SUNSET) == LIGHT
    assert desired_mode(_dt(3, 0), ctx, SUNRISE, SUNSET) == DARK


def test_boundaries_are_inclusive_at_the_start_of_each_arc():
    ctx = _ctx()
    # The instant an arc begins already belongs to that arc.
    assert desired_mode(_dt(6, 0), ctx, SUNRISE, SUNSET) == LIGHT
    assert desired_mode(_dt(5, 59, 59), ctx, SUNRISE, SUNSET) == DARK
    assert desired_mode(_dt(20, 40), ctx, SUNRISE, SUNSET) == DARK
    assert desired_mode(_dt(20, 39, 59), ctx, SUNRISE, SUNSET) == LIGHT


def test_whole_day_walk_flips_exactly_twice():
    ctx = _ctx()
    modes = [desired_mode(_dt(m // 60, m % 60), ctx, SUNRISE, SUNSET) for m in range(1440)]
    flips = sum(1 for a, b in zip(modes, modes[1:], strict=False) if a != b)
    assert flips == 2
    assert modes[0] == DARK and modes[-1] == DARK


def test_offsets_shift_the_boundaries():
    ctx = _ctx()
    late = EventAnchor("sunset", 30)  # dark starts at 21:10
    assert desired_mode(_dt(20, 45), ctx, SUNRISE, late) == LIGHT
    assert desired_mode(_dt(21, 15), ctx, SUNRISE, late) == DARK
    early = EventAnchor("sunrise", -45)  # light starts at 05:15
    assert desired_mode(_dt(5, 30), ctx, early, SUNSET) == LIGHT


def test_clock_anchors_that_wrap_past_midnight():
    ctx = _ctx()
    light_at, dark_at = ClockAnchor(6, 0), ClockAnchor(3, 0)
    assert desired_mode(_dt(4, 0), ctx, light_at, dark_at) == DARK
    assert desired_mode(_dt(12, 0), ctx, light_at, dark_at) == LIGHT
    # 01:00 is after yesterday's 06:00 light start and before today's 03:00 dark start.
    assert desired_mode(_dt(1, 0), ctx, light_at, dark_at) == LIGHT


def test_identical_anchors_return_dark_without_raising():
    # Degenerate config: both arcs are empty. It must be TOTAL -- this runs inside a
    # 2-second loop, where an exception would spam the log forever. Config validation
    # rejects the statically detectable form of this (Task 3).
    ctx = _ctx()
    same = ClockAnchor(12, 0)
    assert desired_mode(_dt(9, 0), ctx, same, same) == DARK


def test_dst_spring_forward_day_still_flips_exactly_twice():
    # 2026-03-29 is Europe/Berlin's spring-forward day: the local wall clock
    # jumps from 02:00 straight to 03:00, so 02:xx does not exist as a local
    # time. Anchors here straddle that missing hour (light starts 01:30, dark
    # starts 03:30) to make sure desired_mode's clock-anchor comparisons still
    # behave sanely across the gap instead of raising or double-flipping.
    def dt(hour: int, minute: int) -> datetime:
        return datetime(2026, 3, 29, hour, minute, tzinfo=BERLIN)

    ctx = DayContext(
        sunrise=dt(6, 0),
        sunset=dt(19, 0),
        wake=dt(10, 0),
        bedtime=dt(23, 0),
    )
    light_at, dark_at = ClockAnchor(1, 30), ClockAnchor(3, 30)

    modes = [desired_mode(dt(m // 60, m % 60), ctx, light_at, dark_at) for m in range(1440)]
    flips = sum(1 for a, b in zip(modes, modes[1:], strict=False) if a != b)
    assert flips == 2
    assert modes[0] == DARK and modes[-1] == DARK


def test_theme_variant_defaults_to_all_empty():
    v = ThemeVariant()
    assert (v.color_scheme, v.gtk_theme, v.icon_theme) == ("", "", "")
    assert (v.shell_theme, v.cursor_theme, v.wallpaper) == ("", "", "")
