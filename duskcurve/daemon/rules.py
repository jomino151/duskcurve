"""Pure window rule engine: maps focused-window state and user rules to per-connector actions."""

from collections.abc import Sequence
from dataclasses import dataclass
from fnmatch import fnmatch


@dataclass(frozen=True)
class WindowState:
    app_id: str
    fullscreen: bool
    monitor_connector: str


@dataclass(frozen=True)
class Rule:
    action: str  # "disable" | "force_day" | "fixed"
    match_app_glob: str | None = None
    match_fullscreen: bool = False
    action_kelvin: int | None = None
    scope: str = "window_monitor"  # "all" | "window_monitor" | a connector name
    match_unfocused: bool = False  # requires match_fullscreen=True


@dataclass(frozen=True)
class RuleAction:
    kind: str  # "none" | "disable" | "fixed"
    kelvin: float | None = None


NO_ACTION = RuleAction("none")
RULE_ACTIONS = frozenset({"disable", "force_day", "fixed"})


def rule_matches(rule: Rule, window: WindowState) -> bool:
    """Return True iff the rule's app glob and fullscreen constraints both hold for window."""
    app_ok = rule.match_app_glob is None or fnmatch(window.app_id, rule.match_app_glob)
    fullscreen_ok = rule.match_fullscreen is False or window.fullscreen is True
    return app_ok and fullscreen_ok


def _action_for(rule: Rule, day_kelvin: float) -> RuleAction:
    """Construct the RuleAction a winning rule produces.

    Raises:
        ValueError: If `rule.action` is not one of "disable", "force_day", "fixed", or if it
            is "fixed" without `action_kelvin` set.
    """
    if rule.action == "disable":
        return RuleAction("disable")
    if rule.action == "force_day":
        return RuleAction("fixed", float(day_kelvin))
    if rule.action == "fixed":
        if rule.action_kelvin is None:
            raise ValueError("rule action 'fixed' requires action_kelvin to be set")
        return RuleAction("fixed", float(rule.action_kelvin))
    raise ValueError(f"unknown rule action: {rule.action!r}")


def _affected(rule: Rule, window: WindowState, connectors: Sequence[str]) -> list[str]:
    """Return the connectors `rule`'s scope resolves to, given the matching `window`."""
    if rule.scope == "all":
        return list(connectors)
    if rule.scope == "window_monitor":
        return [window.monitor_connector] if window.monitor_connector in connectors else []
    return [rule.scope] if rule.scope in connectors else []


def resolve_rules(
    window: WindowState | None,
    rules: Sequence[Rule],
    connectors: Sequence[str],
    day_kelvin: float = 6500.0,
    unfocused: Sequence[WindowState] = (),
) -> dict[str, RuleAction]:
    """Resolve the first matching rule (if any) into a per-connector action mapping.

    Every connector in `connectors` is always a key of the returned dict, defaulting to
    NO_ACTION. If `window` is None or has an empty app_id, all connectors get NO_ACTION.
    Otherwise the first rule in `rules` for which `rule_matches` is True wins; its scope
    determines which connectors receive the computed action.

    If `unfocused` is non-empty, rules with `match_unfocused=True` may then fill in any
    connector still at NO_ACTION using the fullscreen windows in `unfocused`; a focused
    window's decision is never overwritten. Rules are scanned in order, and within a rule,
    `unfocused` windows are scanned in order, so rule order (not window order) breaks ties
    between competing fullscreen windows.

    Raises:
        ValueError: If a winning rule's action is not one of "disable", "force_day", "fixed".
    """
    result = {connector: NO_ACTION for connector in connectors}

    if window is not None and window.app_id != "":
        winning_rule = None
        for rule in rules:
            if rule_matches(rule, window):
                winning_rule = rule
                break

        if winning_rule is not None:
            action = _action_for(winning_rule, day_kelvin)
            for connector in _affected(winning_rule, window, connectors):
                result[connector] = action

    if not unfocused:
        return result

    for rule in rules:
        if not rule.match_unfocused:
            continue
        for window_ in unfocused:
            if window_.app_id == "" or not window_.fullscreen:
                continue
            if not rule_matches(rule, window_):
                continue
            affected = [c for c in _affected(rule, window_, connectors) if result[c] is NO_ACTION]
            if not affected:
                continue
            action = _action_for(rule, day_kelvin)
            for connector in affected:
                result[connector] = action

    return result
