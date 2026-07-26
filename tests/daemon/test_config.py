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
