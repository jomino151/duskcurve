import pytest

from fluxmax.gui.protocol import DisplayStatus, WindowStatus, parse_status

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
