import subprocess
import sys

import pytest

from duskcurve.daemon.rules import (
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
        "import sys; import duskcurve.daemon.rules; "
        "assert not any(m == 'gi' or m.startswith('gi.') for m in sys.modules), "
        "'duskcurve.daemon.rules pulled in gi'"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


UNFOCUSED_RULE = Rule(
    action="disable", match_fullscreen=True, match_unfocused=True, scope="window_monitor"
)


def test_unfocused_window_is_ignored_without_match_unfocused():
    # FULLSCREEN_RULE has match_unfocused=False (the default).
    got = resolve_rules(
        None, [FULLSCREEN_RULE], CONNECTORS,
        unfocused=[_win(fullscreen=True, connector="DP-2")],
    )
    assert got == {"DP-2": NO_ACTION, "HDMI-1": NO_ACTION}


def test_unfocused_fullscreen_window_disables_its_own_monitor():
    got = resolve_rules(
        None, [UNFOCUSED_RULE], CONNECTORS,
        unfocused=[_win(fullscreen=True, connector="DP-2")],
    )
    assert got == {"DP-2": RuleAction("disable"), "HDMI-1": NO_ACTION}


def test_unfocused_window_that_is_not_fullscreen_never_matches():
    # Belt and braces: the engine must not trust the extension to filter.
    got = resolve_rules(
        None, [UNFOCUSED_RULE], CONNECTORS,
        unfocused=[_win(fullscreen=False, connector="DP-2")],
    )
    assert got == {"DP-2": NO_ACTION, "HDMI-1": NO_ACTION}


def test_unfocused_window_with_empty_app_id_is_skipped():
    got = resolve_rules(
        None, [UNFOCUSED_RULE], CONNECTORS,
        unfocused=[_win(app_id="", fullscreen=True, connector="DP-2")],
    )
    assert got == {"DP-2": NO_ACTION, "HDMI-1": NO_ACTION}


def test_focused_decision_is_never_overwritten_by_an_unfocused_window():
    focused_rule = Rule(action="fixed", action_kelvin=3000, match_app_glob="org.gimp.*",
                        scope="all")
    got = resolve_rules(
        _win(app_id="org.gimp.GIMP", connector="HDMI-1"),
        [focused_rule, UNFOCUSED_RULE],
        CONNECTORS,
        unfocused=[_win(fullscreen=True, connector="DP-2")],
    )
    # The focused rule scoped "all" claimed both connectors first.
    assert got == {"DP-2": RuleAction("fixed", 3000.0), "HDMI-1": RuleAction("fixed", 3000.0)}


def test_unfocused_fills_only_connectors_the_focused_window_left_alone():
    focused_rule = Rule(action="force_day", match_app_glob="org.gimp.*",
                        scope="window_monitor")
    got = resolve_rules(
        _win(app_id="org.gimp.GIMP", connector="HDMI-1"),
        [focused_rule, UNFOCUSED_RULE],
        CONNECTORS,
        unfocused=[_win(fullscreen=True, connector="DP-2")],
    )
    assert got == {"DP-2": RuleAction("disable"), "HDMI-1": RuleAction("fixed", 6500.0)}


def test_unfocused_scope_all_claims_every_free_connector():
    rule = Rule(action="disable", match_fullscreen=True, match_unfocused=True, scope="all")
    got = resolve_rules(
        None, [rule], CONNECTORS, unfocused=[_win(fullscreen=True, connector="DP-2")]
    )
    assert got == {"DP-2": RuleAction("disable"), "HDMI-1": RuleAction("disable")}


def test_unfocused_scope_named_connector_ignores_the_window_monitor():
    rule = Rule(action="disable", match_fullscreen=True, match_unfocused=True, scope="HDMI-1")
    got = resolve_rules(
        None, [rule], CONNECTORS, unfocused=[_win(fullscreen=True, connector="DP-2")]
    )
    assert got == {"DP-2": NO_ACTION, "HDMI-1": RuleAction("disable")}


def test_unfocused_scope_unknown_connector_affects_nothing():
    rule = Rule(action="disable", match_fullscreen=True, match_unfocused=True, scope="VGA-9")
    got = resolve_rules(
        None, [rule], CONNECTORS, unfocused=[_win(fullscreen=True, connector="DP-2")]
    )
    assert got == {"DP-2": NO_ACTION, "HDMI-1": NO_ACTION}


def test_rule_order_beats_window_order_for_two_unfocused_windows():
    # Both windows are on DP-2. Window order says mpv first; rule order says
    # the firefox rule first. Rule order must win.
    firefox_rule = Rule(action="fixed", action_kelvin=4000, match_app_glob="firefox",
                        match_fullscreen=True, match_unfocused=True, scope="window_monitor")
    mpv_rule = Rule(action="disable", match_app_glob="mpv", match_fullscreen=True,
                    match_unfocused=True, scope="window_monitor")
    got = resolve_rules(
        None,
        [firefox_rule, mpv_rule],
        CONNECTORS,
        unfocused=[_win(app_id="mpv", fullscreen=True, connector="DP-2"),
                   _win(app_id="firefox", fullscreen=True, connector="DP-2")],
    )
    assert got["DP-2"] == RuleAction("fixed", 4000.0)


def test_window_order_breaks_ties_within_one_rule():
    got = resolve_rules(
        None,
        [UNFOCUSED_RULE],
        CONNECTORS,
        unfocused=[_win(app_id="mpv", fullscreen=True, connector="DP-2"),
                   _win(app_id="firefox", fullscreen=True, connector="HDMI-1")],
    )
    assert got == {"DP-2": RuleAction("disable"), "HDMI-1": RuleAction("disable")}


def test_unfocused_fixed_action_without_kelvin_still_raises():
    bad = Rule(action="fixed", match_fullscreen=True, match_unfocused=True)
    with pytest.raises(ValueError):
        resolve_rules(None, [bad], CONNECTORS,
                      unfocused=[_win(fullscreen=True, connector="DP-2")])


def test_empty_unfocused_is_identical_to_omitting_it():
    win = _win(fullscreen=True, connector="DP-2")
    assert resolve_rules(win, [FULLSCREEN_RULE], CONNECTORS, 6500.0, ()) == resolve_rules(
        win, [FULLSCREEN_RULE], CONNECTORS
    )


def test_unfocused_fullscreen_recheck_does_not_rely_on_the_rule_glob():
    # The engine must not trust the extension to publish only fullscreen
    # windows as unfocused. config.py rejects match_unfocused without
    # match_fullscreen, so this Rule can only be built directly -- which is
    # exactly the point: rules.py enforces the invariant on its own, and this
    # test is the only thing that proves the explicit re-check in
    # resolve_rules is load-bearing rather than dead weight behind
    # rule_matches' own fullscreen constraint.
    rule = Rule(action="disable", match_app_glob="mpv", match_unfocused=True,
                match_fullscreen=False, scope="window_monitor")
    got = resolve_rules(
        None, [rule], CONNECTORS,
        unfocused=[_win(app_id="mpv", fullscreen=False, connector="DP-2")],
    )
    assert got == {"DP-2": NO_ACTION, "HDMI-1": NO_ACTION}
