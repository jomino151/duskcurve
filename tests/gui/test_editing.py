import pytest

from fluxmax.daemon.config import KeypointConfig, default_config, dump_config, load_config
from fluxmax.gui.editing import (
    config_to_keypoint,
    effective_keypoints,
    fork_to_custom,
    keypoint_to_config,
    mode_keypoints,
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
