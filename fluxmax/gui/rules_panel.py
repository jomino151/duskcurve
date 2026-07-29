"""The Rules view -- app/window rules that override the schedule.

Wraps a `Gtk.ListBox` of the config's `rules` tuple, an add/edit
`Adw.Dialog`, and an `Adw.Toast`-based undo for deletes. This module is
gi-dependent (unlike the pure `state`/`editing`/`protocol`/`geometry`
modules it calls into) and is imported only by widget code, never by
`fluxmax.gui.__init__` or a pure module.

Order is semantics, not presentation: `resolve_rules` (the same function
the daemon's orchestrator calls every tick) is first-match-wins, so the
position of a rule in the tuple decides whether it ever fires. The list
shows each rule's 1-based position, offers move-up/move-down (`move_rule`),
and marks whichever rule is currently winning for the focused window so
the ordering's effect is visible rather than implicit.

Two guards recur here, matching `schedule_view.py` and `display_panel.py`:

- **Gesture guard**: while the add/edit `Adw.Dialog` is open, `_render()`
  returns immediately without touching the list -- the dialog is modal, so
  nothing the user can do reaches the underlying rows anyway, but a status
  poll arriving mid-edit must not tear down the list under the dialog's
  feet (or, more subtly, reorder rows whose 0-based index the dialog has
  already captured as the one it is editing).
- **Re-entrancy guard**: `_render()` sets `self._rendering = True` for the
  duration of rebuilding the list, and every button's `clicked` handler
  checks that flag first and returns immediately if it is set. `_render()`
  itself never calls `AppState.apply_config` -- the guarded handlers are
  the only places that do -- so a rebuild can never be mistaken for a user
  action and loop back into another outbound call. `AppState.set_error()`
  notifies subscribers too, so a failed apply from inside a handler
  re-enters `_render()`; the flag covers the whole handler body via
  try/finally precisely so that path is also guarded.

Match highlighting is best-effort: if `resolve_rules` raises (a
malformed rule already on disk, from an older or hand-edited config) the
list still renders, just without a highlighted winner -- this is a
render-time computation, not a user action, so failures are logged and
swallowed rather than routed to `AppState.set_error`, which would notify
subscribers and re-enter `_render()` on every poll.
"""

import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GObject, Gtk  # noqa: E402

from fluxmax.daemon.config import Config  # noqa: E402
from fluxmax.daemon.rules import Rule, WindowState, resolve_rules  # noqa: E402
from fluxmax.gui.editing import (  # noqa: E402
    add_rule,
    delete_rule,
    move_rule,
    replace_rule,
    with_rules,
)
from fluxmax.gui.protocol import Status  # noqa: E402
from fluxmax.gui.state import AppState  # noqa: E402

log = logging.getLogger("fluxmax.gui.rules_panel")

_ACTIONS = ("disable", "force_day", "fixed")
_BUILTIN_SCOPES = ("all", "window_monitor")
_DAY_KELVIN = 6500.0


def _summarize(rule: Rule) -> str:
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

    if rule.action == "disable":
        action = "disable filter"
    elif rule.action == "force_day":
        action = f"full daylight ({_DAY_KELVIN:.0f}K)"
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


def _window_state(status: Status | None) -> WindowState | None:
    if status is None or status.window is None:
        return None
    w = status.window
    return WindowState(
        app_id=w.app_id, fullscreen=w.fullscreen, monitor_connector=w.monitor_connector
    )


def _rule_validation_error(
    app_glob: str | None, fullscreen: bool, action: str, kelvin: int
) -> str | None:
    """Mirror fluxmax.daemon.config._validate_rule's requirements, so a rule
    that would be rejected by the daemon (or blow up resolve_rules) is
    refused here instead."""
    if app_glob is None and not fullscreen:
        return (
            "Set an app glob and/or enable “fullscreen only” -- otherwise this "
            "rule matches every window forever."
        )
    if action == "fixed" and kelvin <= 0:
        return "A fixed action needs a positive kelvin value."
    return None


def _winning_indices(
    rules: tuple[Rule, ...], window: WindowState | None, connectors: tuple[str, ...]
) -> dict[str, int]:
    """Map each connector to the index of the rule currently controlling it.

    Evaluated prefix-wise through `resolve_rules` -- the same function the
    daemon uses -- so the two cannot disagree: the winner for a connector is
    the first rule that, evaluated on its own prefix, produces a non-"none"
    action for it. Connectors with no matching rule (or no focused window)
    are absent from the result.
    """
    winners: dict[str, int] = {}
    if window is None or not connectors:
        return winners
    remaining = set(connectors)
    for i in range(len(rules)):
        if not remaining:
            break
        actions = resolve_rules(window, rules[: i + 1], connectors, _DAY_KELVIN)
        for connector in list(remaining):
            if actions[connector].kind != "none":
                winners[connector] = i
                remaining.discard(connector)
    return winners


class _RuleDialog(Adw.Dialog):
    """Add/edit dialog for a single rule. `on_save(rule, index)` is called
    with a validated `Rule`; `index` is None for a new rule."""

    def __init__(self, connectors: tuple[str, ...], rule: Rule | None, index: int | None, on_save):
        super().__init__()
        self._on_save = on_save
        self._index = index
        self.set_title("Edit rule" if rule is not None else "Add rule")
        self.set_content_width(420)

        toolbar_view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        toolbar_view.add_top_bar(header)

        cancel_button = Gtk.Button(label="Cancel")
        cancel_button.connect("clicked", lambda _b: self.close())
        header.pack_start(cancel_button)

        save_button = Gtk.Button(label="Save")
        save_button.add_css_class("suggested-action")
        save_button.connect("clicked", self._on_save_clicked)
        header.pack_end(save_button)

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        page.set_margin_top(12)
        page.set_margin_bottom(12)
        page.set_margin_start(12)
        page.set_margin_end(12)

        group = Adw.PreferencesGroup()

        self._glob_row = Adw.EntryRow(title="App ID glob (empty = any app)")
        if rule is not None and rule.match_app_glob:
            self._glob_row.set_text(rule.match_app_glob)
        group.add(self._glob_row)

        self._fullscreen_row = Adw.SwitchRow(title="Fullscreen only")
        self._fullscreen_row.set_active(rule.match_fullscreen if rule is not None else False)
        group.add(self._fullscreen_row)

        action_model = Gtk.StringList.new(list(_ACTIONS))
        self._action_row = Adw.ComboRow(title="Action", model=action_model)
        self._action_row.set_selected(_ACTIONS.index(rule.action) if rule is not None else 0)
        self._action_row.connect("notify::selected", self._on_action_changed)
        group.add(self._action_row)

        self._kelvin_row = Adw.SpinRow.new_with_range(1000, 20000, 50)
        self._kelvin_row.set_title("Kelvin")
        self._kelvin_row.set_digits(0)
        default_kelvin = (
            rule.action_kelvin if rule is not None and rule.action_kelvin is not None else 6500
        )
        self._kelvin_row.set_value(default_kelvin)
        group.add(self._kelvin_row)

        scope_labels = list(_BUILTIN_SCOPES) + list(connectors)
        scope_model = Gtk.StringList.new(scope_labels)
        self._scope_row = Adw.ComboRow(title="Scope", model=scope_model)
        if rule is not None and rule.scope in scope_labels:
            self._scope_row.set_selected(scope_labels.index(rule.scope))
        else:
            self._scope_row.set_selected(scope_labels.index("window_monitor"))
        group.add(self._scope_row)

        self._scope_labels = scope_labels
        self._update_kelvin_sensitivity()

        page.append(group)

        self._error_label = Gtk.Label(label="")
        self._error_label.set_wrap(True)
        self._error_label.set_xalign(0)
        self._error_label.add_css_class("error")
        self._error_label.set_visible(False)
        page.append(self._error_label)

        toolbar_view.set_content(page)
        self.set_child(toolbar_view)

    def _on_action_changed(self, _row: Adw.ComboRow, _pspec: GObject.ParamSpec) -> None:
        self._update_kelvin_sensitivity()

    def _update_kelvin_sensitivity(self) -> None:
        action = _ACTIONS[self._action_row.get_selected()]
        self._kelvin_row.set_sensitive(action == "fixed")

    def show_apply_error(self, message: str) -> None:
        self._error_label.set_label(message)
        self._error_label.set_visible(True)

    def _on_save_clicked(self, _button: Gtk.Button) -> None:
        try:
            app_glob_text = self._glob_row.get_text().strip()
            app_glob = app_glob_text if app_glob_text else None
            fullscreen = self._fullscreen_row.get_active()
            action = _ACTIONS[self._action_row.get_selected()]
            kelvin = round(self._kelvin_row.get_value())
            scope = self._scope_labels[self._scope_row.get_selected()]

            error = _rule_validation_error(app_glob, fullscreen, action, kelvin)
            if error is not None:
                self.show_apply_error(error)
                return

            rule = Rule(
                action=action,
                match_app_glob=app_glob,
                match_fullscreen=fullscreen,
                action_kelvin=kelvin if action == "fixed" else None,
                scope=scope,
            )
            self._on_save(rule, self._index, self)
        except Exception as exc:
            log.exception("rule dialog save failed")
            self.show_apply_error(str(exc))


class RulesView(Gtk.Box):
    def __init__(self, state: AppState) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._state = state

        # Re-entrancy guard: True only while _render() is rebuilding the
        # list. Every button handler below checks this first.
        self._rendering = False
        # Gesture guard: True while the add/edit dialog is open.
        self._dialog_open = False

        self.set_margin_top(12)
        self.set_margin_bottom(12)
        self.set_margin_start(12)
        self.set_margin_end(12)

        header = Gtk.Label(
            label=(
                "Rules are checked top to bottom -- the first matching rule wins, "
                "the rest are ignored."
            )
        )
        header.set_wrap(True)
        header.set_xalign(0)
        header.add_css_class("dim-label")
        self.append(header)

        self._toast_overlay = Adw.ToastOverlay()
        self._toast_overlay.set_vexpand(True)

        scroller = Gtk.ScrolledWindow()
        scroller.set_vexpand(True)
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)

        self._list_box = Gtk.ListBox()
        self._list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self._list_box.add_css_class("boxed-list")
        self._list_box.set_valign(Gtk.Align.START)
        scroller.set_child(self._list_box)
        self._toast_overlay.set_child(scroller)
        self.append(self._toast_overlay)

        add_button = Gtk.Button(label="Add rule")
        add_button.set_halign(Gtk.Align.START)
        add_button.connect("clicked", self._on_add_clicked)
        self.append(add_button)

        self._state.subscribe(self._render)
        self._render()

    # -- rendering ------------------------------------------------------

    def _render(self) -> None:
        cfg = self._state.config
        if cfg is None:
            self.set_sensitive(False)
            return
        self.set_sensitive(True)

        if self._dialog_open:
            return

        self._rendering = True
        try:
            self._rebuild_rows(cfg)
        finally:
            self._rendering = False

    def _rebuild_rows(self, cfg: Config) -> None:
        child = self._list_box.get_first_child()
        while child is not None:
            next_child = child.get_next_sibling()
            self._list_box.remove(child)
            child = next_child

        status = self._state.status
        connectors = tuple(d.connector for d in status.displays) if status is not None else ()
        window = _window_state(status)

        try:
            winners = _winning_indices(cfg.rules, window, connectors)
        except Exception:
            # Best-effort highlighting only; a malformed rule already on disk
            # must not prevent the list from rendering, and must not call
            # AppState.set_error() here -- that would notify subscribers and
            # re-enter _render() on every subsequent poll.
            log.exception("could not resolve rules for match highlighting")
            winners = {}

        for index, rule in enumerate(cfg.rules):
            row = self._build_row(index, rule, len(cfg.rules), winners)
            self._list_box.append(row)

    def _build_row(
        self, index: int, rule: Rule, count: int, winners: dict[str, int]
    ) -> Adw.ActionRow:
        matching = sorted(connector for connector, i in winners.items() if i == index)

        row = Adw.ActionRow(title=f"{index + 1}. {_summarize(rule)}")
        if matching:
            row.set_subtitle("Currently active on: " + ", ".join(matching))
            row.add_css_class("accent")

        up_button = Gtk.Button(icon_name="go-up-symbolic")
        up_button.set_valign(Gtk.Align.CENTER)
        up_button.set_sensitive(index > 0)
        up_button.connect("clicked", self._on_move_clicked, index, -1)
        row.add_suffix(up_button)

        down_button = Gtk.Button(icon_name="go-down-symbolic")
        down_button.set_valign(Gtk.Align.CENTER)
        down_button.set_sensitive(index < count - 1)
        down_button.connect("clicked", self._on_move_clicked, index, 1)
        row.add_suffix(down_button)

        edit_button = Gtk.Button(icon_name="document-edit-symbolic")
        edit_button.set_valign(Gtk.Align.CENTER)
        edit_button.connect("clicked", self._on_edit_clicked, index)
        row.add_suffix(edit_button)

        delete_button = Gtk.Button(icon_name="user-trash-symbolic")
        delete_button.set_valign(Gtk.Align.CENTER)
        delete_button.connect("clicked", self._on_delete_clicked, index)
        row.add_suffix(delete_button)

        return row

    # -- add / edit -------------------------------------------------------

    def _on_add_clicked(self, _button: Gtk.Button) -> None:
        if self._rendering:
            return
        self._open_dialog(rule=None, index=None)

    def _on_edit_clicked(self, _button: Gtk.Button, index: int) -> None:
        if self._rendering:
            return
        cfg = self._state.config
        if cfg is None or not 0 <= index < len(cfg.rules):
            return
        self._open_dialog(rule=cfg.rules[index], index=index)

    def _open_dialog(self, rule: Rule | None, index: int | None) -> None:
        status = self._state.status
        connectors = tuple(d.connector for d in status.displays) if status is not None else ()
        dialog = _RuleDialog(connectors, rule, index, self._on_dialog_save)
        dialog.connect("closed", self._on_dialog_closed)
        self._dialog_open = True
        dialog.present(self)

    def _on_dialog_closed(self, _dialog: Adw.Dialog) -> None:
        self._dialog_open = False
        self._render()

    def _on_dialog_save(self, rule: Rule, index: int | None, dialog: _RuleDialog) -> None:
        cfg = self._state.config
        if cfg is None:
            dialog.show_apply_error("Not connected to the daemon.")
            return
        try:
            if index is None:
                new_rules = add_rule(cfg.rules, rule)
            else:
                new_rules = replace_rule(cfg.rules, index, rule)
        except IndexError:
            new_rules = add_rule(cfg.rules, rule)

        try:
            ok = self._state.apply_config(with_rules(cfg, new_rules))
        except Exception as exc:
            log.exception("apply_config raised while saving a rule")
            dialog.show_apply_error(str(exc))
            return

        if ok:
            dialog.close()
        else:
            message = self._state.last_error or "The daemon rejected this rule."
            dialog.show_apply_error(message)

    # -- move / delete ------------------------------------------------------

    def _on_move_clicked(self, _button: Gtk.Button, index: int, delta: int) -> None:
        if self._rendering:
            return
        cfg = self._state.config
        if cfg is None:
            return
        try:
            new_rules = move_rule(cfg.rules, index, delta)
        except IndexError:
            return
        try:
            self._state.apply_config(with_rules(cfg, new_rules))
        except Exception as exc:
            log.exception("apply_config raised while moving a rule")
            self._state.set_error(str(exc))

    def _on_delete_clicked(self, _button: Gtk.Button, index: int) -> None:
        if self._rendering:
            return
        cfg = self._state.config
        if cfg is None or not 0 <= index < len(cfg.rules):
            return
        previous_rules = cfg.rules
        try:
            new_rules = delete_rule(cfg.rules, index)
        except IndexError:
            return
        try:
            ok = self._state.apply_config(with_rules(cfg, new_rules))
        except Exception as exc:
            log.exception("apply_config raised while deleting a rule")
            self._state.set_error(str(exc))
            return
        if ok:
            self._show_undo_toast(previous_rules)

    def _show_undo_toast(self, previous_rules: tuple[Rule, ...]) -> None:
        toast = Adw.Toast(title="Rule deleted")
        toast.set_button_label("Undo")
        toast.connect("button-clicked", self._on_undo_delete, previous_rules)
        self._toast_overlay.add_toast(toast)

    def _on_undo_delete(self, _toast: Adw.Toast, previous_rules: tuple[Rule, ...]) -> None:
        cfg = self._state.config
        if cfg is None:
            return
        try:
            self._state.apply_config(with_rules(cfg, previous_rules))
        except Exception as exc:
            log.exception("apply_config raised while undoing a rule delete")
            self._state.set_error(str(exc))
