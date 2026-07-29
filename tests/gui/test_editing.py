import pytest

from fluxmax.daemon.config import KeypointConfig, default_config, dump_config, load_config
from fluxmax.gui.editing import (
    config_to_keypoint,
    delete_keypoint,
    effective_keypoints,
    fork_to_custom,
    insert_keypoint,
    keypoint_to_config,
    mode_keypoints,
    set_keypoint_anchor,
    set_keypoint_kelvin,
    shift_keypoint_time,
    with_keypoints,
)
from fluxmax.schedule.curve import ClockAnchor, EventAnchor, Keypoint
from fluxmax.schedule.modes import MODES


def test_event_keypoint_round_trips():
    kp = Keypoint(EventAnchor("sunset", -60), 3400.0, easing="smoothstep")
    kc = keypoint_to_config(kp)
    assert kc == KeypointConfig(anchor="sunset", kelvin=3400.0, offset_min=-60, easing="smoothstep")
    assert config_to_keypoint(kc) == kp


def test_clock_keypoint_round_trips():
    kp = Keypoint(ClockAnchor(22, 30), 2700.0, easing="linear")
    kc = keypoint_to_config(kp)
    assert kc == KeypointConfig(anchor="22:30", kelvin=2700.0, offset_min=0, easing="linear")
    assert config_to_keypoint(kc) == kp


def test_clock_anchor_is_zero_padded():
    assert keypoint_to_config(Keypoint(ClockAnchor(7, 5), 6500)).anchor == "07:05"


def test_config_to_keypoint_ignores_offset_on_a_clock_anchor():
    kc = KeypointConfig(anchor="08:00", kelvin=6500, offset_min=99)
    assert config_to_keypoint(kc).anchor == ClockAnchor(8, 0)


@pytest.mark.parametrize("mode", sorted(MODES))
def test_every_builtin_mode_round_trips_through_config_and_validation(mode):
    # The strongest gate on the conversion: fork each built-in, serialize it the
    # way the GUI will, and prove the daemon's own validator accepts it and the
    # keypoints survive unchanged.
    kps = mode_keypoints(mode)
    assert len(kps) == len(MODES[mode].keypoints)
    cfg = with_keypoints(default_config(), kps)
    reloaded = load_config(dump_config(cfg))
    assert reloaded.mode == "custom"
    assert reloaded.custom_keypoints == kps


@pytest.mark.parametrize("mode", sorted(MODES))
def test_mode_keypoints_match_the_curve_they_came_from(mode):
    assert mode_keypoints(mode) == tuple(keypoint_to_config(k) for k in MODES[mode].keypoints)


def test_mode_keypoints_rejects_an_unknown_mode():
    with pytest.raises(KeyError):
        mode_keypoints("no_such_mode")


def test_effective_keypoints_uses_the_builtin_curve_when_not_custom():
    cfg = default_config()
    assert cfg.mode != "custom"
    assert effective_keypoints(cfg) == mode_keypoints(cfg.mode)


def test_effective_keypoints_uses_custom_keypoints_in_custom_mode():
    kps = (KeypointConfig("08:00", 6500), KeypointConfig("22:00", 2700))
    cfg = with_keypoints(default_config(), kps)
    assert effective_keypoints(cfg) == kps


def test_fork_to_custom_copies_the_builtin_curve():
    cfg = default_config()
    forked = fork_to_custom(cfg)
    assert forked.mode == "custom"
    assert forked.custom_keypoints == mode_keypoints(cfg.mode)


def test_fork_to_custom_is_idempotent():
    once = fork_to_custom(default_config())
    assert fork_to_custom(once) == once


def test_fork_to_custom_preserves_every_other_setting():
    cfg = default_config()
    forked = fork_to_custom(cfg)
    assert forked.location == cfg.location
    assert forked.wake == cfg.wake
    assert forked.bedtime == cfg.bedtime
    assert forked.min_kelvin == cfg.min_kelvin
    assert forked.rules == cfg.rules
    assert forked.displays == cfg.displays


# Finding 1: validation of clock anchor strings
@pytest.mark.parametrize(
    "bad_anchor",
    [
        "25:99",      # hour > 23, minute > 59
        "12:60",      # minute > 59
        "lunchtime",  # not an event name, not HH:MM
        "",           # empty string
        "8",          # no colon
        "8:0:0",      # too many colons
        " 8:00",      # leading whitespace
        "+8:00",      # leading sign
    ],
)
def test_config_to_keypoint_rejects_malformed_clock_anchors(bad_anchor):
    kc = KeypointConfig(anchor=bad_anchor, kelvin=6500)
    with pytest.raises(ValueError) as exc_info:
        config_to_keypoint(kc)
    assert bad_anchor in str(exc_info.value)


def test_config_to_keypoint_accepts_valid_clock_boundaries():
    # Ensure valid boundaries are accepted
    kc_min = KeypointConfig(anchor="00:00", kelvin=6500)
    assert config_to_keypoint(kc_min).anchor == ClockAnchor(0, 0)

    kc_max = KeypointConfig(anchor="23:59", kelvin=6500)
    assert config_to_keypoint(kc_max).anchor == ClockAnchor(23, 59)


# Finding 2: round-trip with mixed anchor types (clock + event)
def test_mixed_anchor_types_round_trip_through_serialization():
    # This test exercises clock anchors in the serialization round-trip,
    # which the built-in modes (all event-anchored) do not cover.
    kps_config = (
        KeypointConfig(anchor="07:05", kelvin=6500, offset_min=0, easing="smoothstep"),
        KeypointConfig(
            anchor="sunset", kelvin=3400.0, offset_min=-60, easing="smoothstep"
        ),
        KeypointConfig(
            anchor="wake", kelvin=5500.0, offset_min=30, easing="smoothstep"
        ),
    )
    cfg = with_keypoints(default_config(), kps_config)
    # Full round-trip through daemon serialization.
    reloaded = load_config(dump_config(cfg))
    assert reloaded.mode == "custom"
    assert reloaded.custom_keypoints == kps_config

    # Also verify that the reverse conversion recovers the original keypoints.
    for original_kc in kps_config:
        kp = config_to_keypoint(original_kc)
        recovered_kc = keypoint_to_config(kp)
        assert recovered_kc == original_kc


BASE = (
    KeypointConfig("wake", 6500.0, 0),
    KeypointConfig("sunset", 3400.0, 60),
    KeypointConfig("22:00", 2700.0, 0),
)


def test_set_kelvin_changes_only_the_target():
    got = set_keypoint_kelvin(BASE, 1, 3000.0, min_kelvin=1200.0, max_kelvin=6500.0)
    assert got[1].kelvin == 3000.0
    assert got[0] == BASE[0] and got[2] == BASE[2]
    assert got[1].anchor == "sunset" and got[1].offset_min == 60


def test_set_kelvin_clamps_to_the_configured_range():
    hot = set_keypoint_kelvin(BASE, 0, 99999.0, min_kelvin=1200.0, max_kelvin=6500.0)
    cold = set_keypoint_kelvin(BASE, 0, 10.0, min_kelvin=1200.0, max_kelvin=6500.0)
    assert hot[0].kelvin == 6500.0
    assert cold[0].kelvin == 1200.0


def test_shift_an_event_anchor_adjusts_the_offset_and_keeps_the_anchor():
    got = shift_keypoint_time(BASE, 1, 30)
    assert got[1].anchor == "sunset"        # still tracks sunset seasonally
    assert got[1].offset_min == 90


def test_shift_an_event_anchor_backwards():
    assert shift_keypoint_time(BASE, 1, -90)[1].offset_min == -30


def test_shift_a_clock_anchor_moves_the_time():
    assert shift_keypoint_time(BASE, 2, 45)[2].anchor == "22:45"


def test_shift_a_clock_anchor_wraps_past_midnight():
    assert shift_keypoint_time(BASE, 2, 150)[2].anchor == "00:30"


def test_shift_a_clock_anchor_wraps_backwards_past_midnight():
    kps = (KeypointConfig("00:20", 2700.0),)
    assert shift_keypoint_time(kps, 0, -30)[0].anchor == "23:50"


def test_shift_by_zero_is_a_no_op():
    assert shift_keypoint_time(BASE, 1, 0) == BASE


def test_set_anchor_switches_a_clock_point_to_an_event():
    got = set_keypoint_anchor(BASE, 2, "bedtime", -30)
    assert got[2].anchor == "bedtime" and got[2].offset_min == -30
    assert got[2].kelvin == BASE[2].kelvin


def test_set_anchor_switches_an_event_point_to_a_clock_and_zeroes_the_offset():
    got = set_keypoint_anchor(BASE, 1, "21:15")
    assert got[1].anchor == "21:15" and got[1].offset_min == 0


def test_insert_places_the_keypoint_at_the_given_index():
    got = insert_keypoint(BASE, 2, minutes=20 * 60 + 30, kelvin=3000.0)
    assert len(got) == 4
    assert got[2] == KeypointConfig("20:30", 3000.0, 0, "smoothstep")
    assert got[0] == BASE[0] and got[1] == BASE[1] and got[3] == BASE[2]


def test_insert_at_the_start_and_at_the_end():
    assert insert_keypoint(BASE, 0, 60, 6500.0)[0].anchor == "01:00"
    assert insert_keypoint(BASE, len(BASE), 60, 6500.0)[-1].anchor == "01:00"


def test_insert_rounds_minutes_to_the_nearest_minute():
    assert insert_keypoint(BASE, 0, 61.6, 6500.0)[0].anchor == "01:02"


def test_insert_wraps_1440_to_midnight():
    assert insert_keypoint(BASE, 0, 1440, 6500.0)[0].anchor == "00:00"


def test_mutations_never_reorder_an_inverted_walk():
    # A walk whose resolved times invert is legal and must survive editing
    # unchanged in ORDER -- sorting it would interleave phases of the cycle.
    inverted = (
        KeypointConfig("wake", 6500.0),
        KeypointConfig("23:00", 2700.0),
        KeypointConfig("06:00", 2700.0),
        KeypointConfig("sunset", 3400.0),
    )
    anchors = [k.anchor for k in shift_keypoint_time(inverted, 1, 30)]
    assert anchors == ["wake", "23:30", "06:00", "sunset"]
    anchors = [
        k.anchor
        for k in set_keypoint_kelvin(inverted, 3, 3000, min_kelvin=1200, max_kelvin=6500)
    ]
    assert anchors == ["wake", "23:00", "06:00", "sunset"]


def test_delete_removes_the_target_and_preserves_order():
    got = delete_keypoint(BASE, 1)
    assert [k.anchor for k in got] == ["wake", "22:00"]


def test_delete_refuses_to_empty_the_curve():
    with pytest.raises(ValueError):
        delete_keypoint((KeypointConfig("wake", 6500.0),), 0)


@pytest.mark.parametrize(
    "fn,args",
    [
        (set_keypoint_kelvin, (99, 3000.0)),
        (shift_keypoint_time, (99, 30)),
        (delete_keypoint, (99,)),
    ],
)
def test_an_out_of_range_index_raises(fn, args):
    with pytest.raises(IndexError):
        if fn is set_keypoint_kelvin:
            fn(BASE, args[0], args[1], min_kelvin=1200.0, max_kelvin=6500.0)
        else:
            fn(BASE, *args)


def test_edited_keypoints_survive_the_daemon_validator():
    base_edited = set_keypoint_kelvin(
        BASE, 0, 6000.0, min_kelvin=1200.0, max_kelvin=6500.0
    )
    edited = shift_keypoint_time(base_edited, 1, 15)
    cfg = with_keypoints(default_config(), edited)
    assert load_config(dump_config(cfg)).custom_keypoints == edited
