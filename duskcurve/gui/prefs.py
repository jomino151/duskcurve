"""The Preferences dialog -- settings configured once rather than touched daily.

Three `Adw.PreferencesGroup`s on a single `Adw.PreferencesPage`: Location,
Temperature, and Timing. This module is gi-dependent (unlike the pure
`state`/`editing`/`protocol`/`geometry` modules it calls into) and is
imported only by widget code, never by `duskcurve.gui.__init__` or a pure
module.

The two guards recur here, matching `display_panel.py` and `schedule_view.py`:

- **Gesture guard**: while a spin row or entry is focused/being edited,
  `_render()` skips pushing a fresh value into that specific widget --
  otherwise the periodic status poll (driven by `AppState` subscribers)
  would fight the user's in-progress interaction.
- **Re-entrancy guard**: `_render()` sets `self._rendering = True` for the
  duration of pushing store values into every widget, and every widget's
  own `notify`/`apply` handler checks that flag first and returns
  immediately if it is set. Since `_render()` itself never calls
  `AppState.apply_config`, and the guarded handlers are the *only* places
  that do, a programmatic push can never be mistaken for a user edit and
  loop back into another outbound call. `AppState.set_error()` notifies
  subscribers too, so a failing `apply_config` inside a handler re-enters
  `_render()` -- the flag is set for the whole handler body (via
  try/finally) precisely so that path is also covered.

min_kelvin/max_kelvin are a coupled pair (the daemon's `_validate_range`
requires `min_kelvin < max_kelvin`). Rather than let the user submit an
invalid pair and bounce off a rejected `apply_config`, each row's
adjustment bounds are re-shaped on every render so it is impossible to
drag one past the other: the min row's upper bound tracks the live
max_kelvin value (minus a small margin) and the max row's lower bound
tracks the live min_kelvin value (plus the same margin).
"""

import dataclasses
import logging
from zoneinfo import available_timezones

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GObject, Gtk  # noqa: E402

from duskcurve.daemon.config import Config  # noqa: E402
from duskcurve.gui.state import AppState  # noqa: E402

log = logging.getLogger("duskcurve.gui.prefs")

# Keep at least this much daylight between min_kelvin and max_kelvin so the
# pair can never be dragged into the invalid min < max boundary case even
# with rounding -- matches the strict inequality in
# duskcurve.daemon.config._validate_range.
_KELVIN_MARGIN = 1.0

# duskcurve.daemon.config._validate_range: 0 < min_kelvin < 100000, 0 < max_kelvin < 100000.
_KELVIN_FLOOR = 1.0
_KELVIN_CEIL = 99999.0

# duskcurve.daemon.config._validate_transition: seconds >= 0, rule_seconds >= 0,
# manual_seconds >= 0.
# duskcurve.daemon.config._validate_tick: interval_seconds > 0.
# No daemon-side upper bound on any of the four; cap the widgets generously
# so the UI stays usable rather than to satisfy a validator.
_TRANSITION_MAX = 3600.0
_TICK_MIN = 0.1
_TICK_MAX = 3600.0

_TIMEZONES = sorted(available_timezones())


class PreferencesDialog(Adw.PreferencesDialog):
    def __init__(self, state: AppState) -> None:
        super().__init__()
        self._state = state

        # Re-entrancy guard: True only while _render() is pushing values into
        # widgets. Every outbound handler below checks this first.
        self._rendering = False
        # Gesture guard state: True while the corresponding widget has focus
        # and the user may be mid-edit.
        self._latitude_editing = False
        self._longitude_editing = False
        self._min_kelvin_editing = False
        self._max_kelvin_editing = False
        self._transition_editing = False
        self._rule_transition_editing = False
        self._manual_transition_editing = False
        self._tick_editing = False

        page = Adw.PreferencesPage()
        self.add(page)

        # -- Location ---------------------------------------------------
        location_group = Adw.PreferencesGroup(title="Location")
        page.add(location_group)

        self._latitude_row = Adw.SpinRow.new_with_range(-90.0, 90.0, 0.5)
        self._latitude_row.set_title("Latitude")
        self._latitude_row.set_digits(2)
        self._latitude_row.connect("notify::value", self._on_latitude_changed)
        lat_focus = Gtk.EventControllerFocus()
        lat_focus.connect("enter", self._on_latitude_focus_enter)
        lat_focus.connect("leave", self._on_latitude_focus_leave)
        self._latitude_row.add_controller(lat_focus)
        location_group.add(self._latitude_row)

        self._longitude_row = Adw.SpinRow.new_with_range(-180.0, 180.0, 0.5)
        self._longitude_row.set_title("Longitude")
        self._longitude_row.set_digits(2)
        self._longitude_row.connect("notify::value", self._on_longitude_changed)
        lon_focus = Gtk.EventControllerFocus()
        lon_focus.connect("enter", self._on_longitude_focus_enter)
        lon_focus.connect("leave", self._on_longitude_focus_leave)
        self._longitude_row.add_controller(lon_focus)
        location_group.add(self._longitude_row)

        # A searchable dropdown makes an invalid timezone unrepresentable --
        # no validation branch or error path needed, unlike the freeform
        # Adw.EntryRow this replaces. Adw.ComboRow (unlike Gtk.DropDown) was
        # verified live against this libadwaita 1.5.0 install to expose an
        # `enable-search` property directly, so it's used here rather than
        # wrapping a Gtk.DropDown in an Adw.ActionRow.
        timezone_model = Gtk.StringList.new(_TIMEZONES)
        self._timezone_row = Adw.ComboRow(title="Timezone", model=timezone_model)
        self._timezone_row.set_enable_search(True)
        self._timezone_row.set_expression(
            Gtk.PropertyExpression.new(Gtk.StringObject, None, "string")
        )
        self._timezone_row.connect("notify::selected", self._on_timezone_changed)
        location_group.add(self._timezone_row)

        # -- Temperature --------------------------------------------------
        temperature_group = Adw.PreferencesGroup(
            title="Temperature",
            description="The warmest and coolest color temperatures the schedule may reach. "
            "Neutral / full daylight is 6500 K.",
        )
        page.add(temperature_group)

        self._min_kelvin_row = Adw.SpinRow.new_with_range(_KELVIN_FLOOR, _KELVIN_CEIL, 50.0)
        self._min_kelvin_row.set_title("Minimum (K)")
        self._min_kelvin_row.set_digits(0)
        self._min_kelvin_row.connect("notify::value", self._on_min_kelvin_changed)
        min_focus = Gtk.EventControllerFocus()
        min_focus.connect("enter", self._on_min_kelvin_focus_enter)
        min_focus.connect("leave", self._on_min_kelvin_focus_leave)
        self._min_kelvin_row.add_controller(min_focus)
        temperature_group.add(self._min_kelvin_row)

        self._max_kelvin_row = Adw.SpinRow.new_with_range(_KELVIN_FLOOR, _KELVIN_CEIL, 50.0)
        self._max_kelvin_row.set_title("Maximum (K)")
        self._max_kelvin_row.set_digits(0)
        self._max_kelvin_row.connect("notify::value", self._on_max_kelvin_changed)
        max_focus = Gtk.EventControllerFocus()
        max_focus.connect("enter", self._on_max_kelvin_focus_enter)
        max_focus.connect("leave", self._on_max_kelvin_focus_leave)
        self._max_kelvin_row.add_controller(max_focus)
        temperature_group.add(self._max_kelvin_row)

        # -- Timing ---------------------------------------------------------
        timing_group = Adw.PreferencesGroup(title="Timing")
        page.add(timing_group)

        self._transition_row = Adw.SpinRow.new_with_range(0.0, _TRANSITION_MAX, 1.0)
        self._transition_row.set_title("Transition (s)")
        self._transition_row.set_subtitle("Easing time for the gradual circadian drift.")
        self._transition_row.set_digits(1)
        self._transition_row.connect("notify::value", self._on_transition_changed)
        transition_focus = Gtk.EventControllerFocus()
        transition_focus.connect("enter", self._on_transition_focus_enter)
        transition_focus.connect("leave", self._on_transition_focus_leave)
        self._transition_row.add_controller(transition_focus)
        timing_group.add(self._transition_row)

        self._rule_transition_row = Adw.SpinRow.new_with_range(0.0, _TRANSITION_MAX, 1.0)
        self._rule_transition_row.set_title("Rule transition (s)")
        self._rule_transition_row.set_subtitle(
            "Easing time for a change driven by a window rule. A rule reacts to a deliberate "
            "user action (going fullscreen), not the gradual drift Transition is tuned for -- "
            "sharing one easing time made a working rule engine feel broken on real hardware, "
            "so this defaults to 0 (instant)."
        )
        self._rule_transition_row.set_digits(1)
        self._rule_transition_row.connect("notify::value", self._on_rule_transition_changed)
        rule_transition_focus = Gtk.EventControllerFocus()
        rule_transition_focus.connect("enter", self._on_rule_transition_focus_enter)
        rule_transition_focus.connect("leave", self._on_rule_transition_focus_leave)
        self._rule_transition_row.add_controller(rule_transition_focus)
        timing_group.add(self._rule_transition_row)

        self._manual_transition_row = Adw.SpinRow.new_with_range(0.0, _TRANSITION_MAX, 1.0)
        self._manual_transition_row.set_title("Manual transition (s)")
        self._manual_transition_row.set_subtitle(
            "Easing time for a deliberate temperature change (slider, quick-set), as distinct "
            "from the schedule's gradual drift -- quick, but not snap-instant like a pause or "
            "display toggle."
        )
        self._manual_transition_row.set_digits(1)
        self._manual_transition_row.connect("notify::value", self._on_manual_transition_changed)
        manual_transition_focus = Gtk.EventControllerFocus()
        manual_transition_focus.connect("enter", self._on_manual_transition_focus_enter)
        manual_transition_focus.connect("leave", self._on_manual_transition_focus_leave)
        self._manual_transition_row.add_controller(manual_transition_focus)
        timing_group.add(self._manual_transition_row)

        self._tick_row = Adw.SpinRow.new_with_range(_TICK_MIN, _TICK_MAX, 0.5)
        self._tick_row.set_title("Tick interval (s)")
        self._tick_row.set_subtitle("How often the daemon re-evaluates the schedule.")
        self._tick_row.set_digits(1)
        self._tick_row.connect("notify::value", self._on_tick_changed)
        tick_focus = Gtk.EventControllerFocus()
        tick_focus.connect("enter", self._on_tick_focus_enter)
        tick_focus.connect("leave", self._on_tick_focus_leave)
        self._tick_row.add_controller(tick_focus)
        timing_group.add(self._tick_row)

        # The window builds a fresh dialog on every Preferences activation, so
        # a dropped unsubscribe handle would leak one permanent subscriber per
        # open -- each of them re-rendering a destroyed dialog on every status
        # poll forever, silently, because AppState._notify swallows subscriber
        # exceptions. Adw.Dialog::closed (verified against libadwaita 1.5.0)
        # is the teardown hook.
        self._unsubscribe = self._state.subscribe(self._render)
        self.connect("closed", self._on_closed)
        self._render()

    def _on_closed(self, _dialog: Adw.Dialog) -> None:
        unsubscribe, self._unsubscribe = self._unsubscribe, None
        if unsubscribe is not None:
            unsubscribe()

    # -- rendering ------------------------------------------------------

    def _render(self) -> None:
        cfg: Config | None = self._state.config
        if cfg is None:
            self.set_sensitive(False)
            return
        self.set_sensitive(True)

        self._rendering = True
        try:
            if not self._latitude_editing:
                self._latitude_row.set_value(cfg.location.latitude)
            if not self._longitude_editing:
                self._longitude_row.set_value(cfg.location.longitude)
            try:
                tz_index = _TIMEZONES.index(cfg.location.timezone)
            except ValueError:
                tz_index = Gtk.INVALID_LIST_POSITION
            self._timezone_row.set_selected(tz_index)

            # Reshape the min/max adjustments so neither row can be dragged
            # past the other -- see module docstring.
            min_adjustment = self._min_kelvin_row.get_adjustment()
            min_adjustment.set_upper(max(_KELVIN_FLOOR, cfg.max_kelvin - _KELVIN_MARGIN))
            max_adjustment = self._max_kelvin_row.get_adjustment()
            max_adjustment.set_lower(min(_KELVIN_CEIL, cfg.min_kelvin + _KELVIN_MARGIN))

            if not self._min_kelvin_editing:
                self._min_kelvin_row.set_value(cfg.min_kelvin)
            if not self._max_kelvin_editing:
                self._max_kelvin_row.set_value(cfg.max_kelvin)

            if not self._transition_editing:
                self._transition_row.set_value(cfg.transition_seconds)
            if not self._rule_transition_editing:
                self._rule_transition_row.set_value(cfg.rule_transition_seconds)
            if not self._manual_transition_editing:
                self._manual_transition_row.set_value(cfg.manual_transition_seconds)
            if not self._tick_editing:
                self._tick_row.set_value(cfg.tick_interval_seconds)
        finally:
            self._rendering = False

    # -- location ---------------------------------------------------------

    def _on_latitude_focus_enter(self, _ctrl: Gtk.EventControllerFocus) -> None:
        self._latitude_editing = True

    def _on_latitude_focus_leave(self, _ctrl: Gtk.EventControllerFocus) -> None:
        self._latitude_editing = False

    def _on_longitude_focus_enter(self, _ctrl: Gtk.EventControllerFocus) -> None:
        self._longitude_editing = True

    def _on_longitude_focus_leave(self, _ctrl: Gtk.EventControllerFocus) -> None:
        self._longitude_editing = False

    def _on_latitude_changed(self, row: Adw.SpinRow, _pspec: GObject.ParamSpec) -> None:
        if self._rendering:
            return
        self._write_location(latitude=row.get_value())

    def _on_longitude_changed(self, row: Adw.SpinRow, _pspec: GObject.ParamSpec) -> None:
        if self._rendering:
            return
        self._write_location(longitude=row.get_value())

    def _on_timezone_changed(self, row: Adw.ComboRow, _pspec: GObject.ParamSpec) -> None:
        if self._rendering:
            return
        index = row.get_selected()
        if index == Gtk.INVALID_LIST_POSITION:
            return
        self._write_location(timezone=_TIMEZONES[index])

    def _write_location(self, **kwargs) -> None:
        cfg = self._state.config
        if cfg is None:
            return
        new_location = dataclasses.replace(cfg.location, **kwargs)
        self._state.apply_config(dataclasses.replace(cfg, location=new_location))

    # -- temperature --------------------------------------------------------

    def _on_min_kelvin_focus_enter(self, _ctrl: Gtk.EventControllerFocus) -> None:
        self._min_kelvin_editing = True

    def _on_min_kelvin_focus_leave(self, _ctrl: Gtk.EventControllerFocus) -> None:
        self._min_kelvin_editing = False

    def _on_max_kelvin_focus_enter(self, _ctrl: Gtk.EventControllerFocus) -> None:
        self._max_kelvin_editing = True

    def _on_max_kelvin_focus_leave(self, _ctrl: Gtk.EventControllerFocus) -> None:
        self._max_kelvin_editing = False

    def _on_min_kelvin_changed(self, row: Adw.SpinRow, _pspec: GObject.ParamSpec) -> None:
        if self._rendering:
            return
        cfg = self._state.config
        if cfg is None:
            return
        self._state.apply_config(dataclasses.replace(cfg, min_kelvin=row.get_value()))

    def _on_max_kelvin_changed(self, row: Adw.SpinRow, _pspec: GObject.ParamSpec) -> None:
        if self._rendering:
            return
        cfg = self._state.config
        if cfg is None:
            return
        self._state.apply_config(dataclasses.replace(cfg, max_kelvin=row.get_value()))

    # -- timing ---------------------------------------------------------

    def _on_transition_focus_enter(self, _ctrl: Gtk.EventControllerFocus) -> None:
        self._transition_editing = True

    def _on_transition_focus_leave(self, _ctrl: Gtk.EventControllerFocus) -> None:
        self._transition_editing = False

    def _on_rule_transition_focus_enter(self, _ctrl: Gtk.EventControllerFocus) -> None:
        self._rule_transition_editing = True

    def _on_rule_transition_focus_leave(self, _ctrl: Gtk.EventControllerFocus) -> None:
        self._rule_transition_editing = False

    def _on_manual_transition_focus_enter(self, _ctrl: Gtk.EventControllerFocus) -> None:
        self._manual_transition_editing = True

    def _on_manual_transition_focus_leave(self, _ctrl: Gtk.EventControllerFocus) -> None:
        self._manual_transition_editing = False

    def _on_tick_focus_enter(self, _ctrl: Gtk.EventControllerFocus) -> None:
        self._tick_editing = True

    def _on_tick_focus_leave(self, _ctrl: Gtk.EventControllerFocus) -> None:
        self._tick_editing = False

    def _on_transition_changed(self, row: Adw.SpinRow, _pspec: GObject.ParamSpec) -> None:
        if self._rendering:
            return
        cfg = self._state.config
        if cfg is None:
            return
        self._state.apply_config(dataclasses.replace(cfg, transition_seconds=row.get_value()))

    def _on_rule_transition_changed(self, row: Adw.SpinRow, _pspec: GObject.ParamSpec) -> None:
        if self._rendering:
            return
        cfg = self._state.config
        if cfg is None:
            return
        self._state.apply_config(dataclasses.replace(cfg, rule_transition_seconds=row.get_value()))

    def _on_manual_transition_changed(self, row: Adw.SpinRow, _pspec: GObject.ParamSpec) -> None:
        if self._rendering:
            return
        cfg = self._state.config
        if cfg is None:
            return
        self._state.apply_config(
            dataclasses.replace(cfg, manual_transition_seconds=row.get_value())
        )

    def _on_tick_changed(self, row: Adw.SpinRow, _pspec: GObject.ParamSpec) -> None:
        if self._rendering:
            return
        cfg = self._state.config
        if cfg is None:
            return
        self._state.apply_config(dataclasses.replace(cfg, tick_interval_seconds=row.get_value()))
