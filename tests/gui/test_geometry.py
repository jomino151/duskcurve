from datetime import datetime

import pytest

from duskcurve.daemon.config import KeypointConfig
from duskcurve.gui.geometry import (
    Viewport,
    hit_test,
    insert_index,
    kelvin_to_y,
    keypoint_points,
    minutes_to_x,
    segment_index,
    x_to_minutes,
    y_to_kelvin,
)
from duskcurve.schedule.curve import DayContext

VP = Viewport(width=1000.0, height=500.0, min_kelvin=1200.0, max_kelvin=6500.0, margin=20.0)


def _ctx():
    d = datetime(2026, 7, 29)
    return DayContext(
        sunrise=d.replace(hour=5, minute=30),
        sunset=d.replace(hour=21, minute=0),
        wake=d.replace(hour=10, minute=0),
        bedtime=d.replace(hour=23, minute=0),
    )


def test_midnight_maps_to_the_left_edge_of_the_plot_area():
    assert minutes_to_x(0, VP) == pytest.approx(20.0)


def test_end_of_day_maps_to_the_right_edge_of_the_plot_area():
    assert minutes_to_x(1440, VP) == pytest.approx(980.0)


def test_noon_maps_to_the_horizontal_centre():
    assert minutes_to_x(720, VP) == pytest.approx(500.0)


def test_max_kelvin_is_at_the_top_and_min_at_the_bottom():
    assert kelvin_to_y(6500.0, VP) == pytest.approx(20.0)
    assert kelvin_to_y(1200.0, VP) == pytest.approx(480.0)


@pytest.mark.parametrize("minutes", [0.0, 1.0, 375.0, 720.0, 1439.0, 1440.0])
def test_x_round_trips(minutes):
    assert x_to_minutes(minutes_to_x(minutes, VP), VP) == pytest.approx(minutes)


@pytest.mark.parametrize("kelvin", [1200.0, 2700.0, 3400.0, 6500.0])
def test_kelvin_round_trips(kelvin):
    assert y_to_kelvin(kelvin_to_y(kelvin, VP), VP) == pytest.approx(kelvin)


def test_x_to_minutes_clamps_outside_the_plot_area():
    assert x_to_minutes(-500.0, VP) == 0.0
    assert x_to_minutes(99999.0, VP) == 1440.0


def test_y_to_kelvin_clamps_outside_the_plot_area():
    assert y_to_kelvin(-500.0, VP) == 6500.0
    assert y_to_kelvin(99999.0, VP) == 1200.0


def test_keypoint_points_are_in_authored_order():
    kps = (
        KeypointConfig("wake", 6500.0),
        KeypointConfig("sunset", 3400.0, 60),
        KeypointConfig("23:00", 2700.0),
    )
    points = keypoint_points(kps, _ctx(), VP)
    assert len(points) == 3
    assert points[0][0] == pytest.approx(minutes_to_x(600, VP))     # wake 10:00
    assert points[1][0] == pytest.approx(minutes_to_x(1320, VP))    # sunset 21:00 + 60
    assert points[2][1] == pytest.approx(kelvin_to_y(2700.0, VP))


def test_keypoint_points_wrap_a_walk_that_runs_past_midnight():
    # wake-30 resolves to 09:30 but the walk pushes it a whole day forward;
    # the drawn x must still land inside the 24h axis.
    kps = (KeypointConfig("wake", 6500.0), KeypointConfig("wake", 2700.0, -30))
    points = keypoint_points(kps, _ctx(), VP)
    for x, _y in points:
        assert 20.0 - 1e-9 <= x <= 980.0 + 1e-9


def test_hit_test_finds_the_nearest_point_within_the_radius():
    points = ((100.0, 100.0), (300.0, 200.0))
    assert hit_test(points, 104.0, 103.0, radius=12.0) == 0
    assert hit_test(points, 298.0, 201.0, radius=12.0) == 1


def test_hit_test_returns_none_outside_the_radius():
    assert hit_test(((100.0, 100.0),), 200.0, 200.0, radius=12.0) is None


def test_hit_test_picks_the_nearer_of_two_overlapping_candidates():
    points = ((100.0, 100.0), (106.0, 100.0))
    assert hit_test(points, 105.0, 100.0, radius=20.0) == 1


def test_hit_test_on_an_empty_list():
    assert hit_test((), 10.0, 10.0) is None


def test_segment_index_inside_the_walk():
    walk = [600.0, 1320.0, 1380.0]
    assert segment_index(walk, 700.0) == 0
    assert segment_index(walk, 1330.0) == 1


def test_segment_index_on_the_wrapping_last_segment():
    walk = [600.0, 1320.0, 1380.0]
    assert segment_index(walk, 1400.0) == 2      # after the last keypoint
    assert segment_index(walk, 100.0) == 2       # before the first, i.e. wrapped round


def test_segment_index_exactly_on_a_keypoint_belongs_to_the_segment_it_starts():
    walk = [600.0, 1320.0, 1380.0]
    assert segment_index(walk, 600.0) == 0
    assert segment_index(walk, 1320.0) == 1


def test_segment_index_with_a_single_keypoint_is_always_zero():
    assert segment_index([600.0], 100.0) == 0
    assert segment_index([600.0], 900.0) == 0


def test_segment_index_tolerates_a_zero_length_segment():
    # walk_minutes clamps inversions into zero-length segments; they must not
    # trap the lookup.
    walk = [600.0, 1320.0, 1320.0, 1400.0]
    assert segment_index(walk, 1350.0) == 2


def test_insert_index_is_one_past_the_segment():
    walk = [600.0, 1320.0, 1380.0]
    assert insert_index(walk, 700.0) == 1
    assert insert_index(walk, 1400.0) == 3
