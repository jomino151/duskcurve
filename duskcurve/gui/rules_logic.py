"""Decision logic behind the Rules page.

No gi import, directly or transitively: importing Adw opens the real session bus, and Gio
caches that connection process-wide, which would defeat a test D-Bus.
"""

from duskcurve.daemon.config import Config
from duskcurve.daemon.rules import Rule, WindowState, resolve_rules
from duskcurve.gui.protocol import Status

# Fallback for before the daemon has answered; the daemon itself uses config.max_kelvin.
_DEFAULT_DAY_KELVIN = 6500.0


def _day_kelvin(cfg: Config | None) -> float:
    return _DEFAULT_DAY_KELVIN if cfg is None else cfg.max_kelvin


def _summarize(rule: Rule, day_kelvin: float) -> str:
    """A human-readable one-liner, e.g. "Fullscreen apps -> disable filter on
    that display" or "org.gimp.* -> full daylight on all displays"."""
    if rule.match_app_glob and rule.match_fullscreen:
        subject = f"{rule.match_app_glob} (fullscreen)"
    elif rule.match_app_glob:
        subject = rule.match_app_glob
    elif rule.match_fullscreen:
        subject = "Fullscreen apps"
    else:
        subject = "Any window"

    if rule.match_unfocused:
        subject += " (even unfocused)"

    if rule.action == "disable":
        action = "disable filter"
    elif rule.action == "force_day":
        action = f"full daylight ({day_kelvin:.0f}K)"
    elif rule.action == "fixed":
        kelvin = rule.action_kelvin if rule.action_kelvin is not None else "?"
        action = f"force {kelvin}K"
    else:
        action = rule.action

    if rule.scope == "all":
        scope = "on all displays"
    elif rule.scope == "window_monitor":
        scope = "on that display"
    else:
        scope = f"on {rule.scope}"

    return f"{subject} → {action} {scope}"


def _window_states(
    status: Status | None,
) -> tuple[WindowState | None, tuple[WindowState, ...]]:
    """The focused window and any unfocused-but-tracked windows, from `status`.

    Built from `status.windows` when present; falls back to `status.window`
    (older daemon payloads, or a single-window snapshot) when `windows` is
    empty, treating that lone window as focused."""
    if status is None:
        return None, ()
    if status.windows:
        focused = None
        unfocused: list[WindowState] = []
        for w in status.windows:
            state = WindowState(
                app_id=w.app_id, fullscreen=w.fullscreen, monitor_connector=w.monitor_connector
            )
            if w.focused and focused is None:
                focused = state
            else:
                unfocused.append(state)
        return focused, tuple(unfocused)
    if status.window is None:
        return None, ()
    w = status.window
    focused = WindowState(
        app_id=w.app_id, fullscreen=w.fullscreen, monitor_connector=w.monitor_connector
    )
    return focused, ()


def _rule_validation_error(
    app_glob: str | None, fullscreen: bool, unfocused: bool, action: str, kelvin: int
) -> str | None:
    """Mirror daemon.config._validate_rule so the daemon never sees a rule it would reject."""
    if app_glob is None and not fullscreen:
        return (
            "Set an app glob and/or enable “fullscreen only” -- otherwise this "
            "rule matches every window forever."
        )
    if unfocused and not fullscreen:
        return "“Also when not focused” requires “fullscreen only”."
    if action == "fixed" and kelvin <= 0:
        return "A fixed action needs a positive kelvin value."
    return None


def _winning_indices(
    rules: tuple[Rule, ...],
    window: WindowState | None,
    connectors: tuple[str, ...],
    day_kelvin: float,
    unfocused: tuple[WindowState, ...] = (),
) -> dict[str, int]:
    """Map each connector to the index of the rule currently controlling it.

    Evaluated prefix-wise through the daemon's own `resolve_rules`: the winner is the first
    rule whose prefix yields a non-"none" action. Unmatched connectors are absent.
    """
    winners: dict[str, int] = {}
    if (window is None and not unfocused) or not connectors:
        return winners

    # Two passes in the engine's precedence order. Prefix attribution is sound for the
    # focused window (first match wins) but not once unfocused windows fill displays: an
    # early unfocused rule wins its short prefix yet loses to a later focused rule overall.
    remaining = set(connectors)

    # Pass 1: resolve the focused window against the full rule set to find what it owns.
    focused_actions = resolve_rules(window, rules, connectors, day_kelvin)
    focused_owned = {c for c in remaining if focused_actions[c].kind != "none"}
    for i in range(len(rules)):
        if not focused_owned:
            break
        actions = resolve_rules(window, rules[: i + 1], connectors, day_kelvin)
        for connector in list(focused_owned):
            if actions[connector].kind != "none":
                winners[connector] = i
                focused_owned.discard(connector)
                remaining.discard(connector)

    # Pass 2: connectors left free are what unfocused windows may fill.
    for i in range(len(rules)):
        if not remaining:
            break
        actions = resolve_rules(window, rules[: i + 1], connectors, day_kelvin, unfocused)
        for connector in list(remaining):
            if actions[connector].kind != "none":
                winners[connector] = i
                remaining.discard(connector)

    return winners
