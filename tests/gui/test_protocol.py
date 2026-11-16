import json

import pytest

from duskcurve.gui.protocol import DisplayStatus, WindowStatus, parse_status

FULL = """
{
  "mode": "reduce_eyestrain",
  "paused": false,
  "per_display": [
    {"connector": "DP-2", "current_k": 3400, "enabled": true, "override": "none",
     "rule_action": {"kind": "none", "kelvin": null}, "manual_k": null},
    {"connector": "HDMI-1", "current_k": 2700, "enabled": false, "override": "warm_cap",
     "rule_action": {"kind": "fixed", "kelvin": 6500.0}, "manual_k": 2200}
  ],
  "next_transition": null,
  "window": {"app_id": "org.gimp.GIMP", "fullscreen": true, "monitor_connector": "DP-2"}
}
"""


def test_parses_a_full_payload():
    st = parse_status(FULL)
    assert st.mode == "reduce_eyestrain"
    assert st.paused is False
    assert len(st.displays) == 2
    assert st.displays[0] == DisplayStatus(
        connector="DP-2", current_k=3400, enabled=True, override="none",
        rule_kind="none", rule_kelvin=None, manual_k=None,
    )
    assert st.displays[1].rule_kind == "fixed"
    assert st.displays[1].rule_kelvin == 6500.0
    assert st.displays[1].manual_k == 2200
    assert st.window == WindowStatus("org.gimp.GIMP", True, "DP-2")


def test_parses_the_manual_expiry():
    st = parse_status(
        '{"per_display": [{"connector": "DP-2", "current_k": 3000, "manual_k": 2500,'
        ' "manual_expires_in_s": 1500}]}'
    )
    assert st.display("DP-2").manual_expires_in_s == 1500


def test_missing_manual_expiry_falls_back_to_none():
    """An older daemon has no such key; the GUI must simply show no countdown
    rather than breaking."""
    assert parse_status(FULL).display("HDMI-1").manual_expires_in_s is None


def test_non_numeric_manual_expiry_falls_back_to_none():
    st = parse_status(
        '{"per_display": [{"connector": "DP-2", "manual_k": 2500,'
        ' "manual_expires_in_s": "soon"}]}'
    )
    assert st.display("DP-2").manual_expires_in_s is None


def test_theme_status_non_numeric_manual_expiry_falls_back_to_none():
    """Same tolerant-parsing rule as DisplayStatus, but for the top-level
    theme block: a wrong-typed manual_expires_in_s must not be trusted."""
    st = parse_status('{"theme": {"enabled": true, "manual_expires_in_s": "soon"}}')
    assert st.theme.manual_expires_in_s is None


def test_live_kelvin_is_the_eased_value_not_the_manual_target():
    """manual_k is the *target* of a manual override; the display eases toward
    it over manual_transition_seconds. A readout must show current_k, or it
    sits frozen on the endpoint -- off by the whole remaining gap -- while the
    panel is still visibly transitioning."""
    st = parse_status(FULL)
    warm = st.display("HDMI-1")
    assert warm.manual_k == 2200  # where it is headed
    assert warm.current_k == 2700  # where it is right now
    assert warm.live_kelvin == 2700

    plain = st.display("DP-2")
    assert plain.manual_k is None
    assert plain.live_kelvin == plain.current_k == 3400


def test_display_lookup_by_connector():
    st = parse_status(FULL)
    assert st.display("HDMI-1").current_k == 2700
    assert st.display("DP-9") is None


def test_null_window_means_no_focused_window():
    st = parse_status('{"mode":"classic","paused":false,"per_display":[],"window":null}')
    assert st.window is None


def test_unknown_keys_are_ignored():
    st = parse_status(
        '{"mode":"classic","paused":true,"per_display":['
        '{"connector":"DP-2","current_k":3000,"future_key":42}],'
        '"brand_new_top_level":"whatever"}'
    )
    assert st.paused is True
    assert st.displays[0].connector == "DP-2"


def test_missing_optional_keys_fall_back_to_defaults():
    st = parse_status('{"per_display":[{"connector":"DP-2"}]}')
    assert st.mode == ""
    assert st.paused is False
    assert st.next_transition is None
    assert st.window is None
    d = st.displays[0]
    assert d.current_k == 6500        # neutral
    assert d.enabled is True
    assert d.override == "none"
    assert d.rule_kind == "none"
    assert d.rule_kelvin is None
    assert d.manual_k is None


def test_entries_without_a_connector_are_dropped():
    st = parse_status('{"per_display":[{"connector":""},{"current_k":3000},{"connector":"DP-2"}]}')
    assert [d.connector for d in st.displays] == ["DP-2"]


def test_missing_per_display_yields_no_displays():
    assert parse_status('{"mode":"classic"}').displays == ()


@pytest.mark.parametrize("bad", ["", "not json", "[1,2,3]", '"a string"', "null", "42"])
def test_malformed_payloads_raise_value_error(bad):
    with pytest.raises(ValueError):
        parse_status(bad)


def test_current_k_is_coerced_to_int():
    st = parse_status('{"per_display":[{"connector":"DP-2","current_k":3400.7}]}')
    assert st.displays[0].current_k == 3401


def test_non_numeric_current_k_falls_back_to_neutral():
    st = parse_status('{"per_display":[{"connector":"DP-2","current_k":[1,2]}]}')
    assert st.displays[0].current_k == 6500
    st = parse_status('{"per_display":[{"connector":"DP-2","current_k":{"bad":"value"}}]}')
    assert st.displays[0].current_k == 6500


def test_non_numeric_manual_k_falls_back_to_none():
    st = parse_status('{"per_display":[{"connector":"DP-2","manual_k":[1,2]}]}')
    assert st.displays[0].manual_k is None
    st = parse_status('{"per_display":[{"connector":"DP-2","manual_k":{"bad":"value"}}]}')
    assert st.displays[0].manual_k is None


def test_non_numeric_rule_kelvin_falls_back_to_none():
    st = parse_status('{"per_display":[{"connector":"DP-2","rule_action":{"kelvin":[1,2]}}]}')
    assert st.displays[0].rule_kelvin is None
    bad_kelvin = '{"per_display":[{"connector":"DP-2","rule_action":{"kelvin":' \
                 '{"bad":"value"}}}]}'
    st = parse_status(bad_kelvin)
    assert st.displays[0].rule_kelvin is None


def test_empty_app_id_in_window_yields_none():
    st = parse_status('{"per_display":[],"window":{"app_id":"","fullscreen":false}}')
    assert st.window is None


def test_theme_status_is_parsed():
    status = parse_status(
        json.dumps(
            {
                "mode": "reduce_eyestrain",
                "paused": False,
                "per_display": [],
                "theme": {
                    "enabled": True,
                    "mode": "dark",
                    "manual": "light",
                    "manual_expires_in_s": 900,
                },
            }
        )
    )
    assert status.theme.enabled is True
    assert status.theme.mode == "dark"
    assert status.theme.manual == "light"
    assert status.theme.manual_expires_in_s == 900


def test_status_without_a_theme_block_still_parses():
    # An older daemon does not send it. The GUI must default, never raise.
    status = parse_status(json.dumps({"mode": "classic", "paused": False, "per_display": []}))
    assert status.theme.enabled is False
    assert status.theme.mode is None


def test_windows_are_parsed():
    status = parse_status(json.dumps({
        "mode": "classic", "paused": False, "per_display": [],
        "window": {"app_id": "gimp", "fullscreen": False, "monitor_connector": "HDMI-1"},
        "windows": [
            {"app_id": "gimp", "fullscreen": False, "focused": True,
             "monitor_connector": "HDMI-1"},
            {"app_id": "mpv", "fullscreen": True, "focused": False,
             "monitor_connector": "DP-2"},
        ],
    }))
    assert status.windows == (
        WindowStatus("gimp", False, "HDMI-1", True),
        WindowStatus("mpv", True, "DP-2", False),
    )


def test_absent_windows_is_derived_from_window():
    status = parse_status(json.dumps({
        "mode": "classic", "paused": False, "per_display": [],
        "window": {"app_id": "gimp", "fullscreen": False, "monitor_connector": "HDMI-1"},
    }))
    assert status.windows == (WindowStatus("gimp", False, "HDMI-1", True),)


def test_absent_windows_and_no_window_gives_an_empty_tuple():
    status = parse_status(json.dumps({
        "mode": "classic", "paused": False, "per_display": [],
    }))
    assert status.windows == ()


def test_malformed_window_entries_are_dropped():
    status = parse_status(json.dumps({
        "mode": "classic", "paused": False, "per_display": [],
        "windows": ["nonsense", {"app_id": ""},
                    {"app_id": "mpv", "fullscreen": True, "focused": False,
                     "monitor_connector": "DP-2"}],
    }))
    assert status.windows == (WindowStatus("mpv", True, "DP-2", False),)


def test_wrongly_typed_windows_value_falls_back_instead_of_crashing():
    # An older or buggy daemon could send anything here. The parser must be as
    # tolerant as _parse_display is about per_display: fall back to the
    # single-window key rather than raise.
    status = parse_status(json.dumps({
        "mode": "classic", "paused": False, "per_display": [],
        "window": {"app_id": "gimp", "fullscreen": False, "monitor_connector": "HDMI-1"},
        "windows": "nonsense",
    }))
    assert status.windows == (WindowStatus("gimp", False, "HDMI-1", True),)

    status = parse_status(json.dumps({
        "mode": "classic", "paused": False, "per_display": [], "windows": {"a": 1},
    }))
    assert status.windows == ()
