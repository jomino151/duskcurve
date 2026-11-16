"""The Preferences dialog: Location, Temperature and Timing settings.

`_rendering` guards every handler while store values are pushed into widgets; focused rows are
not rewritten. Each render reshapes the min/max kelvin bounds so `min < max` cannot be violated.
"""

import dataclasses
import logging
from collections.abc import Callable
from zoneinfo import available_timezones

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GObject, Gtk  # noqa: E402

from duskcurve.daemon.config import Config  # noqa: E402
from duskcurve.gui.state import AppState  # noqa: E402

log = logging.getLogger("duskcurve.gui.prefs")

# Gap kept between min_kelvin and max_kelvin; the daemon's _validate_range is strict.
_KELVIN_MARGIN = 1.0

# duskcurve.daemon.config._validate_range: 0 < min_kelvin < 100000, 0 < max_kelvin < 100000.
_KELVIN_FLOOR = 1.0
_KELVIN_CEIL = 99999.0

# Daemon validators require transition seconds >= 0 and tick interval > 0, with no upper
# bound; the caps below only keep the widgets usable.
_TRANSITION_MAX = 3600.0
_TICK_MIN = 0.1
_TICK_MAX = 3600.0

_TIMEZONES = sorted(available_timezones())


class PreferencesDialog(Adw.PreferencesDialog):
    def __init__(self, state: AppState) -> None:
        super().__init__()
        self._state = state

        self._rendering = False
        # True while the matching widget has focus.
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

        # A searchable dropdown makes an invalid timezone unrepresentable. Adw.ComboRow
        # exposes `enable-search` directly, unlike Gtk.DropDown.
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

        # A fresh dialog is built per activation, so unsubscribe on ::closed or each open
        # leaks a subscriber that re-renders a destroyed dialog on every poll.
        self._unsubscribe: Callable[[], None] | None = self._state.subscribe(self._render)
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

            # Reshape so neither row can be dragged past the other.
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
