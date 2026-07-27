import subprocess
import sys

import pytest

from fluxmax.daemon.rules import (
    NO_ACTION,
    Rule,
    RuleAction,
    WindowState,
    resolve_rules,
    rule_matches,
)

CONNECTORS = ["DP-2", "HDMI-1"]
FULLSCREEN_RULE = Rule(action="disable", match_fullscreen=True, scope="window_monitor")


def _win(app_id="org.example.App", fullscreen=False, connector="DP-2"):
    return WindowState(app_id=app_id, fullscreen=fullscreen, monitor_connector=connector)


def test_no_window_yields_all_none():
    assert resolve_rules(None, [FULLSCREEN_RULE], CONNECTORS) == {
        "DP-2": NO_ACTION,
        "HDMI-1": NO_ACTION,
    }


def test_empty_app_id_is_treated_as_no_window():
    assert resolve_rules(_win(app_id=""), [FULLSCREEN_RULE], CONNECTORS) == {
        "DP-2": NO_ACTION,
        "HDMI-1": NO_ACTION,
    }


def test_fullscreen_rule_disables_only_the_window_monitor():
    got = resolve_rules(_win(fullscreen=True, connector="DP-2"), [FULLSCREEN_RULE], CONNECTORS)
    assert got == {"DP-2": RuleAction("disable"), "HDMI-1": NO_ACTION}


def test_fullscreen_rule_ignores_a_non_fullscreen_window():
    got = resolve_rules(_win(fullscreen=False), [FULLSCREEN_RULE], CONNECTORS)
    assert got == {"DP-2": NO_ACTION, "HDMI-1": NO_ACTION}


def test_glob_match_with_scope_all():
    rule = Rule(action="disable", match_app_glob="org.gimp.*", scope="all")
    got = resolve_rules(_win(app_id="org.gimp.GIMP"), [rule], CONNECTORS)
    assert got == {"DP-2": RuleAction("disable"), "HDMI-1": RuleAction("disable")}


def test_glob_that_does_not_match_leaves_everything_alone():
    rule = Rule(action="disable", match_app_glob="org.gimp.*", scope="all")
    got = resolve_rules(_win(app_id="org.mozilla.firefox"), [rule], CONNECTORS)
    assert got == {"DP-2": NO_ACTION, "HDMI-1": NO_ACTION}


def test_scope_can_name_a_specific_connector():
    rule = Rule(action="disable", match_app_glob="*", scope="HDMI-1")
    got = resolve_rules(_win(connector="DP-2"), [rule], CONNECTORS)
    assert got == {"DP-2": NO_ACTION, "HDMI-1": RuleAction("disable")}


def test_unknown_connector_in_scope_affects_nothing():
    rule = Rule(action="disable", match_app_glob="*", scope="DVI-0")
    got = resolve_rules(_win(), [rule], CONNECTORS)
    assert got == {"DP-2": NO_ACTION, "HDMI-1": NO_ACTION}


def test_window_on_an_unknown_monitor_affects_nothing():
    got = resolve_rules(_win(fullscreen=True, connector=""), [FULLSCREEN_RULE], CONNECTORS)
    assert got == {"DP-2": NO_ACTION, "HDMI-1": NO_ACTION}


def test_first_matching_rule_wins_globally():
    first = Rule(action="disable", match_fullscreen=True, scope="window_monitor")
    second = Rule(action="fixed", action_kelvin=3000, match_app_glob="*", scope="all")
    got = resolve_rules(_win(fullscreen=True, connector="DP-2"), [first, second], CONNECTORS)
    assert got == {"DP-2": RuleAction("disable"), "HDMI-1": NO_ACTION}


def test_force_day_uses_the_day_kelvin_argument():
    rule = Rule(action="force_day", match_app_glob="*", scope="all")
    got = resolve_rules(_win(), [rule], CONNECTORS, day_kelvin=5800.0)
    assert got == {"DP-2": RuleAction("fixed", 5800.0), "HDMI-1": RuleAction("fixed", 5800.0)}


def test_fixed_uses_the_rule_kelvin():
    rule = Rule(action="fixed", action_kelvin=3000, match_app_glob="*", scope="all")
    got = resolve_rules(_win(), [rule], CONNECTORS)
    assert got["DP-2"] == RuleAction("fixed", 3000.0)


def test_empty_rule_list_yields_all_none():
    assert resolve_rules(_win(), [], CONNECTORS) == {"DP-2": NO_ACTION, "HDMI-1": NO_ACTION}


def test_unknown_action_raises():
    rule = Rule(action="explode", match_app_glob="*", scope="all")
    with pytest.raises(ValueError, match="unknown rule action"):
        resolve_rules(_win(), [rule], CONNECTORS)


def test_fixed_action_without_kelvin_raises():
    # Config validation prevents this from arising via load_config(), but a
    # programmatically constructed Rule(action="fixed") with no action_kelvin
    # must fail loudly here rather than raising a bare TypeError deeper down
    # (float(None)) when tick() calls resolve_rules().
    rule = Rule(action="fixed", match_app_glob="*", scope="all")
    with pytest.raises(ValueError, match="action_kelvin"):
        resolve_rules(_win(), [rule], CONNECTORS)


@pytest.mark.parametrize(
    ("rule", "window", "expected"),
    [
        (Rule(action="disable"), _win(), True),
        (Rule(action="disable", match_fullscreen=True), _win(fullscreen=True), True),
        (Rule(action="disable", match_fullscreen=True), _win(fullscreen=False), False),
        (Rule(action="disable", match_app_glob="steam_app_*"),
         _win(app_id="steam_app_570"), True),
        (Rule(action="disable", match_app_glob="steam_app_*"), _win(app_id="steam"), False),
    ],
)
def test_rule_matches(rule, window, expected):
    assert rule_matches(rule, window) is expected


def test_rules_module_imports_no_gi():
    code = (
        "import sys; import fluxmax.daemon.rules; "
        "assert not any(m == 'gi' or m.startswith('gi.') for m in sys.modules), "
        "'fluxmax.daemon.rules pulled in gi'"
    )
    subprocess.run([sys.executable, "-c", code], check=True)
