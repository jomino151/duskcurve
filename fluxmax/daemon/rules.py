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


def resolve_rules(
    window: WindowState | None,
    rules: Sequence[Rule],
    connectors: Sequence[str],
    day_kelvin: float = 6500.0,
) -> dict[str, RuleAction]:
    """Resolve the first matching rule (if any) into a per-connector action mapping.

    Every connector in `connectors` is always a key of the returned dict, defaulting to
    NO_ACTION. If `window` is None or has an empty app_id, all connectors get NO_ACTION.
    Otherwise the first rule in `rules` for which `rule_matches` is True wins; its scope
    determines which connectors receive the computed action.

    Raises:
        ValueError: If the winning rule's action is not one of "disable", "force_day", "fixed".
    """
    result = {connector: NO_ACTION for connector in connectors}

    if window is None or window.app_id == "":
        return result

    winning_rule = None
    for rule in rules:
        if rule_matches(rule, window):
            winning_rule = rule
            break

    if winning_rule is None:
        return result

    if winning_rule.scope == "all":
        affected = list(connectors)
    elif winning_rule.scope == "window_monitor":
        affected = [window.monitor_connector] if window.monitor_connector in connectors else []
    else:
        affected = [winning_rule.scope] if winning_rule.scope in connectors else []

    if winning_rule.action == "disable":
        action = RuleAction("disable")
    elif winning_rule.action == "force_day":
        action = RuleAction("fixed", float(day_kelvin))
    elif winning_rule.action == "fixed":
        action = RuleAction("fixed", float(winning_rule.action_kelvin))
    else:
        raise ValueError(f"unknown rule action: {winning_rule.action!r}")

    for connector in affected:
        result[connector] = action

    return result
