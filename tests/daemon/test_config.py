from dataclasses import replace

import pytest

from fluxmax.daemon.config import (
    Config,
    ConfigError,
    DisplayConfig,
    KeypointConfig,
    Location,
    default_config,
    dump_config,
    load_config,
)
from fluxmax.daemon.rules import Rule


def test_default_config_values():
    cfg = default_config()
    assert cfg.location == Location(52.52, 13.40, "Europe/Berlin")
    assert cfg.wake == "10:00"
    assert cfg.bedtime == "23:00"
    assert cfg.mode == "reduce_eyestrain"
    assert cfg.min_kelvin == 1200.0
    assert cfg.max_kelvin == 6500.0
    assert cfg.transition_seconds == 20.0
    assert cfg.tick_interval_seconds == 2.0
    assert cfg.custom_keypoints == ()
    assert cfg.displays == {}


def test_default_config_round_trips_through_dump_and_load():
    cfg = default_config()
    assert load_config(dump_config(cfg)) == cfg


def test_custom_mode_with_two_keypoints_round_trips():
    cfg = Config(
        location=Location(52.52, 13.40, "Europe/Berlin"),
        wake="10:00",
        bedtime="23:00",
        mode="custom",
        custom_keypoints=(
            KeypointConfig(anchor="wake", kelvin=6500, offset_min=0, easing="smoothstep"),
            KeypointConfig(anchor="22:30", kelvin=2700, offset_min=0, easing="linear"),
        ),
    )
    text = dump_config(cfg)
    assert load_config(text) == cfg


def test_display_override_round_trips():
    cfg = Config(
        location=Location(52.52, 13.40, "Europe/Berlin"),
        wake="10:00",
        bedtime="23:00",
        mode="reduce_eyestrain",
        displays={
            "DP-2": DisplayConfig(enabled=True, override_kind="warm_cap", override_value=3400.0)
        },
    )
    text = dump_config(cfg)
    loaded = load_config(text)
    assert loaded == cfg
    assert loaded.displays["DP-2"].override_kind == "warm_cap"
    assert loaded.displays["DP-2"].override_value == 3400.0


def test_missing_optional_sections_fall_back_to_defaults():
    text = """
[location]
latitude = 52.52
longitude = 13.40
timezone = "Europe/Berlin"

[schedule]
wake = "10:00"
bedtime = "23:00"
mode = "reduce_eyestrain"
"""
    cfg = load_config(text)
    assert cfg.min_kelvin == 1200.0
    assert cfg.max_kelvin == 6500.0
    assert cfg.transition_seconds == 20.0
    assert cfg.tick_interval_seconds == 2.0
    assert cfg.displays == {}


def _base_toml(**overrides):
    fields = {
        "latitude": 52.52,
        "longitude": 13.40,
        "timezone": "Europe/Berlin",
        "wake": "10:00",
        "bedtime": "23:00",
        "mode": "reduce_eyestrain",
    }
    fields.update(overrides)
    return f"""
[location]
latitude = {fields["latitude"]}
longitude = {fields["longitude"]}
timezone = "{fields["timezone"]}"

[schedule]
wake = "{fields["wake"]}"
bedtime = "{fields["bedtime"]}"
mode = "{fields["mode"]}"
"""


def test_bad_latitude_rejected():
    with pytest.raises(ConfigError):
        load_config(_base_toml(latitude=91.0))


def test_bad_longitude_rejected():
    with pytest.raises(ConfigError):
        load_config(_base_toml(longitude=-181.0))


def test_bad_timezone_rejected():
    with pytest.raises(ConfigError):
        load_config(_base_toml(timezone="Not/A_Zone"))


def test_malformed_wake_rejected():
    with pytest.raises(ConfigError):
        load_config(_base_toml(wake="25:00"))


def test_malformed_bedtime_rejected():
    with pytest.raises(ConfigError):
        load_config(_base_toml(bedtime="9:00"))


def test_unknown_mode_rejected():
    with pytest.raises(ConfigError):
        load_config(_base_toml(mode="not_a_real_mode"))


def test_custom_mode_with_empty_keypoints_rejected():
    with pytest.raises(ConfigError):
        load_config(_base_toml(mode="custom"))


def test_custom_mode_with_bad_anchor_rejected():
    text = _base_toml(mode="custom") + """
[[schedule.keypoints]]
anchor = "not_an_event_or_clock"
kelvin = 6500
"""
    with pytest.raises(ConfigError):
        load_config(text)


def test_custom_mode_with_bad_easing_rejected():
    text = _base_toml(mode="custom") + """
[[schedule.keypoints]]
anchor = "wake"
kelvin = 6500
easing = "bounce"
"""
    with pytest.raises(ConfigError):
        load_config(text)


def test_bad_override_kind_rejected():
    text = _base_toml() + """
[displays.DP-2]
enabled = true
override_kind = "not_a_kind"
override_value = 100.0
"""
    with pytest.raises(ConfigError):
        load_config(text)


def test_min_kelvin_gte_max_kelvin_rejected():
    text = _base_toml() + """
[range]
min_kelvin = 6500
max_kelvin = 6500
"""
    with pytest.raises(ConfigError):
        load_config(text)


def test_negative_transition_seconds_rejected():
    text = _base_toml() + """
[transition]
seconds = -1.0
"""
    with pytest.raises(ConfigError):
        load_config(text)


def test_non_positive_tick_interval_rejected():
    text = _base_toml() + """
[tick]
interval_seconds = 0.0
"""
    with pytest.raises(ConfigError):
        load_config(text)


def test_config_error_is_value_error():
    assert issubclass(ConfigError, ValueError)


# --- M6 final review, C1: a hand-authored custom curve must survive while a
# built-in mode is selected. Keypoints alongside a non-custom mode are
# therefore *accepted* (they are the parked fork) and dump_config emits them
# regardless of mode. Only "custom with no keypoints" stays invalid.


def test_non_custom_mode_with_populated_keypoints_loads():
    text = (
        _base_toml(mode="reduce_eyestrain")
        + """
[[schedule.keypoints]]
anchor = "wake"
kelvin = 6500
"""
    )
    cfg = load_config(text)
    assert cfg.mode == "reduce_eyestrain"
    assert cfg.custom_keypoints == (
        KeypointConfig(anchor="wake", kelvin=6500.0, offset_min=0, easing="smoothstep"),
    )


def test_parked_keypoints_survive_round_trip_with_non_custom_mode():
    cfg = Config(
        location=Location(52.52, 13.40, "Europe/Berlin"),
        wake="10:00",
        bedtime="23:00",
        mode="classic",
        custom_keypoints=(
            KeypointConfig(anchor="wake", kelvin=6500, offset_min=0, easing="smoothstep"),
            KeypointConfig(anchor="22:30", kelvin=2700, offset_min=-15, easing="linear"),
        ),
    )
    round_tripped = load_config(dump_config(cfg))
    assert round_tripped == cfg
    assert round_tripped.mode == "classic"
    assert len(round_tripped.custom_keypoints) == 2


def test_custom_mode_without_keypoints_still_rejected():
    text = _base_toml(mode="custom")
    with pytest.raises(ConfigError):
        load_config(text)


def test_gui_fork_edit_switch_away_and_back_preserves_the_edited_curve():
    """The exact GUI journey from the M6 review: fork a built-in to custom,
    edit a keypoint, switch the mode dropdown to a built-in, let the config go
    through the daemon (dump -> load), then switch back to custom."""
    forked = Config(
        location=Location(52.52, 13.40, "Europe/Berlin"),
        wake="10:00",
        bedtime="23:00",
        mode="custom",
        custom_keypoints=(
            KeypointConfig(anchor="wake", kelvin=6500, offset_min=0, easing="smoothstep"),
            KeypointConfig(anchor="sunset", kelvin=4000, offset_min=0, easing="smoothstep"),
            KeypointConfig(anchor="bedtime", kelvin=2700, offset_min=0, easing="smoothstep"),
        ),
    )
    edited = replace(
        forked,
        custom_keypoints=(
            forked.custom_keypoints[0],
            replace(forked.custom_keypoints[1], kelvin=3300.0),
            forked.custom_keypoints[2],
        ),
    )

    # Switch the dropdown to a built-in; the GUI keeps custom_keypoints.
    switched_away = replace(edited, mode="classic")
    persisted = load_config(dump_config(switched_away))
    assert persisted.mode == "classic"
    assert persisted.custom_keypoints == edited.custom_keypoints

    # Switch back to custom: the fork is still there, edit included.
    switched_back = replace(persisted, mode="custom")
    reloaded = load_config(dump_config(switched_back))
    assert reloaded.mode == "custom"
    assert reloaded.custom_keypoints == edited.custom_keypoints
    assert reloaded.custom_keypoints[1].kelvin == 3300.0


# --- Review fix: IMPORTANT 2 -- non-numeric values in numeric-coerced fields
# must raise ConfigError, not a bare ValueError/TypeError leaking out of
# float()/int().


def test_range_min_kelvin_non_numeric_raises_config_error():
    text = (
        _base_toml()
        + """
[range]
min_kelvin = "abc"
max_kelvin = 6500
"""
    )
    with pytest.raises(ConfigError):
        load_config(text)


def test_transition_seconds_non_numeric_raises_config_error():
    text = (
        _base_toml()
        + """
[transition]
seconds = "abc"
"""
    )
    with pytest.raises(ConfigError):
        load_config(text)


def test_tick_interval_seconds_non_numeric_raises_config_error():
    text = (
        _base_toml()
        + """
[tick]
interval_seconds = "abc"
"""
    )
    with pytest.raises(ConfigError):
        load_config(text)


def test_display_override_value_non_numeric_raises_config_error():
    text = (
        _base_toml()
        + """
[displays.DP-2]
override_value = "abc"
"""
    )
    with pytest.raises(ConfigError):
        load_config(text)


def test_keypoint_offset_min_non_numeric_raises_config_error():
    text = (
        _base_toml(mode="custom")
        + """
[[schedule.keypoints]]
anchor = "wake"
kelvin = 6500
offset_min = "abc"
"""
    )
    with pytest.raises(ConfigError):
        load_config(text)


# --- Review fix: MINOR -- previously-untested-but-enforced rules.


def test_keypoint_kelvin_zero_rejected():
    text = (
        _base_toml(mode="custom")
        + """
[[schedule.keypoints]]
anchor = "wake"
kelvin = 0
"""
    )
    with pytest.raises(ConfigError):
        load_config(text)


def test_keypoint_kelvin_negative_rejected():
    text = (
        _base_toml(mode="custom")
        + """
[[schedule.keypoints]]
anchor = "wake"
kelvin = -100
"""
    )
    with pytest.raises(ConfigError):
        load_config(text)


def test_keypoint_kelvin_nan_rejected():
    text = (
        _base_toml(mode="custom")
        + """
[[schedule.keypoints]]
anchor = "wake"
kelvin = nan
"""
    )
    with pytest.raises(ConfigError):
        load_config(text)


def test_keypoint_kelvin_infinite_rejected():
    text = (
        _base_toml(mode="custom")
        + """
[[schedule.keypoints]]
anchor = "wake"
kelvin = inf
"""
    )
    with pytest.raises(ConfigError):
        load_config(text)


def test_min_kelvin_at_or_below_zero_rejected():
    text = (
        _base_toml()
        + """
[range]
min_kelvin = 0
max_kelvin = 6500
"""
    )
    with pytest.raises(ConfigError):
        load_config(text)


def test_max_kelvin_at_or_above_100000_rejected():
    text = (
        _base_toml()
        + """
[range]
min_kelvin = 1200
max_kelvin = 100000
"""
    )
    with pytest.raises(ConfigError):
        load_config(text)


def test_override_value_zero_rejected_for_warm_cap():
    text = (
        _base_toml()
        + """
[displays.DP-2]
override_kind = "warm_cap"
override_value = 0.0
"""
    )
    with pytest.raises(ConfigError):
        load_config(text)


def test_override_value_zero_rejected_for_fixed():
    text = (
        _base_toml()
        + """
[displays.DP-2]
override_kind = "fixed"
override_value = 0.0
"""
    )
    with pytest.raises(ConfigError):
        load_config(text)


def test_display_enabled_non_bool_rejected():
    text = (
        _base_toml()
        + """
[displays.DP-2]
enabled = "yes"
override_kind = "none"
"""
    )
    with pytest.raises(ConfigError):
        load_config(text)


def test_no_rules_section_yields_empty_tuple():
    cfg = load_config(dump_config(default_config()))
    assert cfg.rules == ()


def test_rules_round_trip():
    cfg = replace(
        default_config(),
        rules=(
            Rule(action="disable", match_fullscreen=True, scope="window_monitor"),
            Rule(action="fixed", action_kelvin=3000, match_app_glob="org.gimp.*", scope="all"),
            Rule(action="force_day", match_app_glob="*blender*", scope="DP-2"),
        ),
    )
    assert load_config(dump_config(cfg)) == cfg


def test_rule_parsed_from_hand_written_toml():
    text = dump_config(default_config()) + """
[[rules]]
match_app_glob = "steam_app_*"
match_fullscreen = true
action = "disable"
scope = "all"
"""
    cfg = load_config(text)
    assert cfg.rules == (
        Rule(
            action="disable",
            match_app_glob="steam_app_*",
            match_fullscreen=True,
            action_kelvin=None,
            scope="all",
        ),
    )


@pytest.mark.parametrize(
    "block",
    [
        '[[rules]]\nmatch_fullscreen = true\n',                                # no action
        '[[rules]]\naction = "explode"\nmatch_fullscreen = true\n',            # unknown action
        '[[rules]]\naction = "fixed"\nmatch_fullscreen = true\n',              # fixed w/o kelvin
        '[[rules]]\naction = "fixed"\naction_kelvin = 0\nmatch_fullscreen = true\n',
        '[[rules]]\naction = "fixed"\naction_kelvin = true\nmatch_fullscreen = true\n',   # bool
        '[[rules]]\naction = "disable"\naction_kelvin = 3000\nmatch_fullscreen = true\n',
        '[[rules]]\naction = "disable"\n',                                     # matches everything
        '[[rules]]\naction = "disable"\nmatch_fullscreen = false\n',           # matches everything
        '[[rules]]\naction = "disable"\nmatch_app_glob = 12\n',                # glob not a string
        '[[rules]]\naction = "disable"\nmatch_fullscreen = "yes"\n',           # not a bool
        '[[rules]]\naction = "disable"\nmatch_fullscreen = true\nscope = ""\n',
        '[[rules]]\naction = "disable"\nmatch_fullscreen = true\nscope = 3\n',
    ],
)
def test_invalid_rule_blocks_are_rejected(block):
    with pytest.raises(ConfigError):
        load_config(dump_config(default_config()) + "\n" + block)


def test_rule_transition_seconds_defaults_to_instant():
    assert default_config().rule_transition_seconds == 0.0
    assert load_config(dump_config(default_config())).rule_transition_seconds == 0.0


def test_rule_transition_seconds_round_trips():
    cfg = replace(default_config(), rule_transition_seconds=3.5)
    assert load_config(dump_config(cfg)) == cfg


def test_rule_transition_seconds_rejects_negative():
    text = dump_config(default_config()).replace(
        "[transition]\nseconds = 20.0", "[transition]\nseconds = 20.0\nrule_seconds = -1.0"
    )
    with pytest.raises(ConfigError):
        load_config(text)


def test_manual_transition_seconds_defaults_to_three():
    assert default_config().manual_transition_seconds == 3.0
    assert load_config(dump_config(default_config())).manual_transition_seconds == 3.0


def test_manual_transition_seconds_round_trips():
    cfg = replace(default_config(), manual_transition_seconds=5.5)
    assert load_config(dump_config(cfg)) == cfg


def test_manual_transition_seconds_rejects_negative():
    text = dump_config(default_config()).replace(
        "[transition]\nseconds = 20.0", "[transition]\nseconds = 20.0\nmanual_seconds = -1.0"
    )
    with pytest.raises(ConfigError):
        load_config(text)


def test_manual_transition_seconds_non_numeric_raises_config_error():
    text = (
        _base_toml()
        + """
[transition]
manual_seconds = "abc"
"""
    )
    with pytest.raises(ConfigError):
        load_config(text)


def test_config_written_before_manual_transition_seconds_still_loads():
    """A TOML document with no [transition].manual_seconds key at all (as
    written by a pre-change build) must still load, defaulting the field."""
    text = dump_config(default_config()).replace("manual_seconds = 3.0\n", "")
    assert "manual_seconds" not in text
    cfg = load_config(text)
    assert cfg.manual_transition_seconds == 3.0
