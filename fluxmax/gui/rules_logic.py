"""Pure decision logic behind the Rules page.

PURE module: stdlib + fluxmax.daemon.config / fluxmax.daemon.rules /
fluxmax.gui.protocol only. **No gi import, directly or transitively.**

This is not stylistic. `fluxmax.gui.rules_panel` imports Gtk/Adw, and merely
importing Adw opens a connection to the real session bus. Gio caches that
connection process-wide, so a test module that imports `rules_panel` at
import time -- which pytest does at COLLECTION time, before any test runs --
defeats `Gio.TestDBus.up()` in `tests/gui/test_client.py` and silently
points those tests at the user's actually-running daemon. Keeping the pure
functions here lets tests import them without dragging in Gtk.
`tests/gui/test_no_gi_import.py` enforces it.
"""

from fluxmax.daemon.config import Config
from fluxmax.daemon.rules import Rule, WindowState, resolve_rules
from fluxmax.gui.protocol import Status

# Only used when there is no config to read max_kelvin from (the daemon has
# not answered yet). The daemon resolves force_day with
# day_kelvin=config.max_kelvin -- see Orchestrator.tick/status -- so this
# constant must never be preferred over a live config value.
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
    """Mirror fluxmax.daemon.config._validate_rule's requirements, so a rule
    that would be rejected by the daemon (or blow up resolve_rules) is
    refused here instead."""
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

    Evaluated prefix-wise through `resolve_rules` -- the same function the
    daemon uses -- so the two cannot disagree: the winner for a connector is
    the first rule that, evaluated on its own prefix, produces a non-"none"
    action for it. Connectors with no matching rule (or no focused window)
    are absent from the result.
    """
    winners: dict[str, int] = {}
    if (window is None and not unfocused) or not connectors:
        return winners

    # TWO passes, in the engine's own precedence order -- not one pass over
    # prefixes of the whole thing. Prefix attribution is only sound while a
    # prefix result for a connector is either "none" or the final answer, and
    # that holds for the focused window (plain first-match-wins) but NOT once
    # unfocused windows can fill displays: an unfocused rule early in the list
    # claims a display inside its own short prefix, while in the full rule set
    # a later focused rule claims that display first and the unfocused rule
    # never gets it. Attributing the badge to the early rule would make the
    # panel disagree with the daemon -- the one thing this module must not do.
    remaining = set(connectors)

    # Pass 1: the focused window, resolved against the FULL rule set first, so
    # we learn which connectors it really owns before attributing any of them.
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

    # Pass 2: whatever the focused window left free is exactly what unfocused
    # windows may fill, so prefix scanning is sound again over that subset.
    for i in range(len(rules)):
        if not remaining:
            break
        actions = resolve_rules(window, rules[: i + 1], connectors, day_kelvin, unfocused)
        for connector in list(remaining):
            if actions[connector].kind != "none":
                winners[connector] = i
                remaining.discard(connector)

    return winners
