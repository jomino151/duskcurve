"""The Displays view -- per-monitor controls.

Renders one `Adw.PreferencesGroup` per connector reported in
`AppState.status.displays`. This module is gi-dependent (unlike the pure
`state`/`editing`/`protocol`/`geometry` modules it calls into) and is
imported only by widget code, never by `fluxmax.gui.__init__` or a pure
module.

Groups are rebuilt only when the *set* of connectors changes; a routine
status poll instead updates the existing widgets in place, matching
`schedule_view.py`. The same two guards recur here, but per-connector since
each display has independent sliders and entries:

- **Gesture guard**: while a display's value spin row is being edited,
  `_DisplayGroup.render()` skips pushing a fresh value into that specific
  widget -- otherwise the periodic status poll (driven by `AppState`
  subscribers) would fight the user's in-progress interaction. The
  manual-temperature scale carries the equivalent guard (plus its own dirty
  tracking) inside `ManualTempScale`, which `schedule_view.py` shares.
- **Re-entrancy guard**: `DisplayView._render()` sets `self._rendering =
  True` for the duration of pushing store values into every group's
  widgets, and every widget's own `notify`/`clicked` handler checks that
  flag first and returns immediately if it is set. Since `_render()` itself
  never calls `AppState.apply_config` or a `DaemonClient` method, and the
  guarded handlers are the *only* places that do, a programmatic push can
  never be mistaken for a user edit and loop back into another outbound
  call. `AppState.set_error()` notifies subscribers too, so a failing
  D-Bus call inside a handler re-enters `_render()` -- the flag is set for
  the whole handler body (via try/finally) precisely so that path is also
  covered.
"""

import dataclasses
import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GObject, Gtk  # noqa: E402

from fluxmax.daemon.config import Config, DisplayConfig  # noqa: E402
from fluxmax.gui.client import DaemonClient  # noqa: E402
from fluxmax.gui.duration import (  # noqa: E402
    DURATION_LABELS,
    duration_for_index,
    manual_description,
)
from fluxmax.gui.manual_temp import ManualTempScale  # noqa: E402
from fluxmax.gui.protocol import DisplayStatus  # noqa: E402
from fluxmax.gui.state import AppState  # noqa: E402

log = logging.getLogger("fluxmax.gui.display_panel")

_OVERRIDE_KINDS = ["none", "warm_cap", "fixed", "offset"]
_KELVIN_KINDS = ("warm_cap", "fixed")
_OFFSET_MIN = -200.0
_OFFSET_MAX = 200.0

# "warm_cap" reads as a ceiling but is a floor on Kelvin: warmer means *lower*
# K, so capping warmth is a max(). That inversion has misled readers of the
# code and of this panel, so the row says which direction it clamps rather
# than leaving the name to carry it.
_VALUE_SUBTITLES = {
    "warm_cap": (
        "Never warmer than this. The display follows the schedule until it "
        "reaches this value, then stops there — so it does nothing while the "
        "schedule is still cooler than the value."
    ),
    "fixed": "Ignores the schedule entirely; this display stays at this value.",
    "offset": (
        "Shifts the whole schedule by a mired delta. Positive is warmer, "
        "negative is cooler."
    ),
}


class _DisplayGroup:
    """Widgets and per-connector guard state for one display's
    `Adw.PreferencesGroup`. Owned by a `DisplayView`, which it reaches back
    into for the store, the client, and the shared re-entrancy flag."""

    def __init__(self, connector: str, view: "DisplayView") -> None:
        self.connector = connector
        self._view = view
        self.value_editing = False

        self.group = Adw.PreferencesGroup(title=connector)

        self.badge = Gtk.Label()
        self.badge.add_css_class("accent")
        self.badge.set_visible(False)
        self.group.set_header_suffix(self.badge)

        self.enabled_row = Adw.SwitchRow(title="Enabled")
        self.enabled_row.connect("notify::active", self._on_enabled_changed)
        self.group.add(self.enabled_row)

        kind_model = Gtk.StringList.new(_OVERRIDE_KINDS)
        self.kind_row = Adw.ComboRow(title="Override", model=kind_model)
        self.kind_row.connect("notify::selected", self._on_kind_changed)
        self.group.add(self.kind_row)

        self.value_row = Adw.SpinRow.new_with_range(0, 20000, 1)
        self.value_row.set_title("Value")
        self.value_row.set_digits(0)
        self.value_row.connect("notify::value", self._on_value_changed)
        value_focus = Gtk.EventControllerFocus()
        value_focus.connect("enter", self._on_value_focus_enter)
        value_focus.connect("leave", self._on_value_focus_leave)
        self.value_row.add_controller(value_focus)
        self.group.add(self.value_row)

        # A control, not a readout -- never written from status, so no
        # re-entrancy guard is needed (unlike the scale below).
        self.duration_row = Adw.ComboRow(
            title="Manual duration", model=Gtk.StringList.new(list(DURATION_LABELS))
        )
        self.duration_row.connect("notify::selected", self._on_duration_changed)
        self.group.add(self.duration_row)

        manual_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        manual_box.set_margin_top(6)
        manual_box.set_margin_bottom(6)
        manual_box.set_margin_start(12)
        manual_box.set_margin_end(12)

        self.scale = ManualTempScale(self._commit_manual_temp, lambda: self._view._rendering)
        manual_box.append(self.scale)

        self.resume_button = Gtk.Button(label="Resume")
        self.resume_button.set_sensitive(False)
        self.resume_button.connect("clicked", self._on_resume_clicked)
        manual_box.append(self.resume_button)

        self.group.add(manual_box)

    # -- rendering --------------------------------------------------------

    def render(self, cfg: Config, display_cfg: DisplayConfig, status: DisplayStatus) -> None:
        description = f"Current: {status.live_kelvin} K"
        if status.manual_k is not None:
            description += f" · {manual_description(True, status.manual_expires_in_s)}"
        self.group.set_description(description)

        self.enabled_row.set_active(display_cfg.enabled)

        try:
            kind_index = _OVERRIDE_KINDS.index(display_cfg.override_kind)
        except ValueError:
            kind_index = 0
        self.kind_row.set_selected(kind_index)

        self._shape_value_row(display_cfg.override_kind, cfg)
        if not self.value_editing:
            self.value_row.set_value(display_cfg.override_value)

        if status.rule_kind == "fixed":
            kelvin_text = (
                f"{status.rule_kelvin:.0f}" if status.rule_kelvin is not None else "?"
            )
            self.badge.set_label(f"Rule: forced to {kelvin_text} K")
            self.badge.set_visible(True)
        elif status.rule_kind == "disable":
            self.badge.set_label("Rule: filter disabled")
            self.badge.set_visible(True)
        else:
            self.badge.set_visible(False)

        live = status.manual_k if status.manual_k is not None else status.current_k
        self.scale.render(cfg.min_kelvin, cfg.max_kelvin, live)

        self.resume_button.set_sensitive(status.manual_k is not None)

    def _shape_value_row(self, kind: str, cfg: Config) -> None:
        adjustment = self.value_row.get_adjustment()
        if kind in _KELVIN_KINDS:
            adjustment.set_lower(cfg.min_kelvin)
            adjustment.set_upper(cfg.max_kelvin)
            self.value_row.set_title("Value (K)")
            self.value_row.set_sensitive(True)
        elif kind == "offset":
            adjustment.set_lower(_OFFSET_MIN)
            adjustment.set_upper(_OFFSET_MAX)
            self.value_row.set_title("Value (mired delta)")
            self.value_row.set_sensitive(True)
        else:
            self.value_row.set_title("Value")
            self.value_row.set_sensitive(False)
        self.value_row.set_subtitle(_VALUE_SUBTITLES.get(kind, ""))

    # -- enabled / kind / value --------------------------------------------

    def _on_enabled_changed(self, row: Adw.SwitchRow, _pspec: GObject.ParamSpec) -> None:
        if self._view._rendering:
            return
        self._write(enabled=row.get_active())

    def _on_kind_changed(self, row: Adw.ComboRow, _pspec: GObject.ParamSpec) -> None:
        if self._view._rendering:
            return
        cfg = self._view._state.config
        if cfg is None:
            return
        new_kind = _OVERRIDE_KINDS[row.get_selected()]
        current = cfg.displays.get(self.connector, DisplayConfig())
        if new_kind == current.override_kind:
            return
        default_value = self._default_value_for(new_kind, current, cfg)
        # The reshape must be inside the guard, not before it: changing the
        # adjustment's bounds clamps the current value and emits
        # notify::value, which would otherwise reach _on_value_changed and
        # send a second, wrong SetConfig carrying the *old* override_kind
        # with the newly clamped number.
        self._view._rendering = True
        try:
            self._shape_value_row(new_kind, cfg)
            self.value_row.set_value(default_value)
        finally:
            self._view._rendering = False
        self._write(override_kind=new_kind, override_value=default_value)

    @staticmethod
    def _default_value_for(new_kind: str, current: DisplayConfig, cfg: Config) -> float:
        # The same raw number means different things per kind (kelvin vs. a
        # mired delta), so switching kinds must not carry the old number
        # across unchanged -- only within the same unit family.
        if new_kind in _KELVIN_KINDS:
            if current.override_kind in _KELVIN_KINDS:
                return current.override_value
            return (cfg.min_kelvin + cfg.max_kelvin) / 2
        return 0.0

    def _on_value_changed(self, row: Adw.SpinRow, _pspec: GObject.ParamSpec) -> None:
        if self._view._rendering:
            return
        self._write(override_value=row.get_value())

    def _on_value_focus_enter(self, _ctrl: Gtk.EventControllerFocus) -> None:
        self.value_editing = True

    def _on_value_focus_leave(self, _ctrl: Gtk.EventControllerFocus) -> None:
        self.value_editing = False

    def _write(self, **kwargs) -> None:
        cfg = self._view._state.config
        if cfg is None:
            return
        current = cfg.displays.get(self.connector, DisplayConfig())
        new_display_cfg = dataclasses.replace(current, **kwargs)
        new_cfg = dataclasses.replace(
            cfg, displays={**cfg.displays, self.connector: new_display_cfg}
        )
        self._view._state.apply_config(new_cfg)

    # -- manual temperature -------------------------------------------------

    def _on_duration_changed(self, _row: Adw.ComboRow, _pspec: GObject.ParamSpec) -> None:
        # Re-arm an override already running on this display -- see the twin
        # in schedule_view. Changing only the duration otherwise sends nothing.
        if self._view._rendering:
            return
        status = self._view._state.status
        if status is None:
            return
        current = next((d for d in status.displays if d.connector == self.connector), None)
        if current is None or current.manual_k is None:
            return
        self._commit_manual_temp(float(current.manual_k))

    def _commit_manual_temp(self, kelvin: float) -> None:
        duration_min = duration_for_index(self.duration_row.get_selected())
        try:
            self._view._client.set_manual_temp(self.connector, round(kelvin), duration_min)
        except Exception as exc:
            log.exception("set_manual_temp failed for %s", self.connector)
            self._view._state.set_error(str(exc))

    def _on_resume_clicked(self, _button: Gtk.Button) -> None:
        try:
            self._view._client.clear_manual_temp(self.connector)
        except Exception as exc:
            log.exception("clear_manual_temp failed for %s", self.connector)
            self._view._state.set_error(str(exc))


class DisplayView(Gtk.Box):
    def __init__(self, state: AppState, client: DaemonClient) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._state = state
        self._client = client

        # Re-entrancy guard: True only while _render() is pushing values into
        # widgets. Every outbound handler on every _DisplayGroup checks this
        # first.
        self._rendering = False
        self._groups: dict[str, _DisplayGroup] = {}

        self.set_margin_top(12)
        self.set_margin_bottom(12)
        self.set_margin_start(12)
        self.set_margin_end(12)

        self._scroller = Gtk.ScrolledWindow()
        self._scroller.set_vexpand(True)
        self._scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._groups_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._scroller.set_child(self._groups_box)
        self.append(self._scroller)

        self._state.subscribe(self._render)
        self._render()

    # -- rendering ----------------------------------------------------------

    def _render(self) -> None:
        cfg = self._state.config
        if cfg is None:
            self.set_sensitive(False)
            return
        self.set_sensitive(True)

        status = self._state.status
        displays = status.displays if status is not None else ()
        connectors = tuple(d.connector for d in displays)
        if set(connectors) != set(self._groups):
            self._rebuild_groups(connectors)

        self._rendering = True
        try:
            for d in displays:
                group = self._groups.get(d.connector)
                if group is None:
                    continue
                display_cfg = cfg.displays.get(d.connector, DisplayConfig())
                group.render(cfg, display_cfg, d)
        finally:
            self._rendering = False

    def _rebuild_groups(self, connectors: tuple[str, ...]) -> None:
        for group in self._groups.values():
            self._groups_box.remove(group.group)
        self._groups = {}
        for connector in connectors:
            group = _DisplayGroup(connector, self)
            self._groups_box.append(group.group)
            self._groups[connector] = group
