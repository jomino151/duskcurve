"""The Schedule view -- the app's main page.

Wraps the curve editor with mode selection, wake/bedtime, and manual
temperature controls. This module is gi-dependent (unlike the pure
`state`/`editing`/`protocol`/`geometry` modules it calls into) and is
imported only by widget code, never by `fluxmax.gui.__init__` or a pure
module.

Two guards recur through every interactive control here:

- **Gesture guard**: while a wake/bedtime entry is being edited, `_render()`
  skips pushing a fresh value into that specific widget -- otherwise the
  periodic status poll (driven by `AppState` subscribers) would fight the
  user's in-progress interaction. The manual-temperature scale carries the
  equivalent guard (plus its own dirty tracking) inside `ManualTempScale`,
  which `display_panel.py` shares.
- **Re-entrancy guard**: `_render()` sets `self._rendering = True` for the
  duration of pushing store values into widgets, and every widget's own
  `changed`/`notify` handler checks that flag first and returns immediately
  if it is set. Since `_render()` itself never calls `AppState.apply_config`
  or a `DaemonClient` method, and the guarded handlers are the *only* places
  that do, a programmatic push can never be mistaken for a user edit and
  loop back into another outbound call.
"""

import dataclasses
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GObject, Gtk  # noqa: E402

from fluxmax.color import PRESETS  # noqa: E402
from fluxmax.daemon.config import Config  # noqa: E402
from fluxmax.gui.client import DaemonClient  # noqa: E402
from fluxmax.gui.curve_editor import CurveEditor  # noqa: E402
from fluxmax.gui.editing import effective_keypoints, fork_to_custom, with_keypoints  # noqa: E402
from fluxmax.gui.manual_temp import ManualTempScale  # noqa: E402
from fluxmax.gui.state import AppState  # noqa: E402
from fluxmax.schedule.curve import DayContext  # noqa: E402
from fluxmax.schedule.modes import MODES  # noqa: E402
from fluxmax.schedule.solar import solar_events  # noqa: E402

log = logging.getLogger("fluxmax.gui.schedule_view")


def _parse_hhmm(text: str) -> tuple[int, int] | None:
    """Parse an "HH:MM" string, returning None (not raising) on anything malformed."""
    parts = text.strip().split(":")
    if len(parts) != 2:
        return None
    hour_str, minute_str = parts
    if not (hour_str.isdigit() and minute_str.isdigit()):
        return None
    hour, minute = int(hour_str), int(minute_str)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def build_day_context(cfg: Config, now: datetime) -> DayContext:
    """Today's DayContext, built the same way Orchestrator._day_context does:
    solar events for the configured location plus the configured wake/bedtime
    clock times, all on the given day."""
    tz = ZoneInfo(cfg.location.timezone)
    day = now.date()
    ev = solar_events(cfg.location.latitude, cfg.location.longitude, day, tz)
    wake_h, wake_m = _parse_hhmm(cfg.wake) or (0, 0)
    bed_h, bed_m = _parse_hhmm(cfg.bedtime) or (0, 0)
    wake = datetime(day.year, day.month, day.day, wake_h, wake_m, tzinfo=tz)
    bedtime = datetime(day.year, day.month, day.day, bed_h, bed_m, tzinfo=tz)
    return DayContext(sunrise=ev.sunrise, sunset=ev.sunset, wake=wake, bedtime=bedtime)


class ScheduleView(Gtk.Box):
    def __init__(self, state: AppState, client: DaemonClient) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._state = state
        self._client = client

        # Re-entrancy guard: True only while _render() is pushing values into
        # widgets. Every outbound handler below checks this first.
        self._rendering = False
        # Gesture guard state.
        self._wake_editing = False
        self._bedtime_editing = False
        # Fork-to-custom banner: shown once per session.
        self._fork_banner_shown = False

        self._mode_labels: list[str] = sorted(MODES) + ["custom"]

        self.set_margin_top(12)
        self.set_margin_bottom(12)
        self.set_margin_start(12)
        self.set_margin_end(12)

        self._banner = Adw.Banner(
            title="The built-in curve was copied to an editable custom one.",
            button_label="Got it",
        )
        self._banner.connect("button-clicked", self._on_banner_dismiss)
        self.append(self._banner)

        # -- mode / wake / bedtime -----------------------------------------
        schedule_group = Adw.PreferencesGroup(title="Schedule")

        mode_model = Gtk.StringList.new(self._mode_labels)
        self._mode_row = Adw.ComboRow(title="Mode", model=mode_model)
        self._mode_row.connect("notify::selected", self._on_mode_changed)
        schedule_group.add(self._mode_row)

        self._wake_row = Adw.EntryRow(title="Wake (HH:MM)")
        self._wake_row.connect("apply", self._on_wake_apply)
        focus = Gtk.EventControllerFocus()
        focus.connect("enter", self._on_wake_focus_enter)
        focus.connect("leave", self._on_wake_focus_leave)
        self._wake_row.add_controller(focus)
        schedule_group.add(self._wake_row)

        self._bedtime_row = Adw.EntryRow(title="Bedtime (HH:MM)")
        self._bedtime_row.connect("apply", self._on_bedtime_apply)
        focus = Gtk.EventControllerFocus()
        focus.connect("enter", self._on_bedtime_focus_enter)
        focus.connect("leave", self._on_bedtime_focus_leave)
        self._bedtime_row.add_controller(focus)
        schedule_group.add(self._bedtime_row)

        self.append(schedule_group)

        # -- curve editor -----------------------------------------------------
        self._curve_editor = CurveEditor(self._on_keypoints_changed)
        curve_frame = Gtk.Frame()
        curve_frame.set_child(self._curve_editor)
        self.append(curve_frame)

        # -- manual temperature -------------------------------------------
        manual_group = Adw.PreferencesGroup(
            title="Manual temperature", description="Overrides the schedule until cleared."
        )

        scale_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        scale_box.set_margin_top(6)
        scale_box.set_margin_bottom(6)
        scale_box.set_margin_start(12)
        scale_box.set_margin_end(12)

        self._scale = ManualTempScale(self._commit_manual_temp, lambda: self._rendering)
        scale_box.append(self._scale)

        self._chip_box = Gtk.FlowBox()
        self._chip_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self._chip_box.set_max_children_per_line(8)
        for preset in PRESETS:
            chip = Gtk.Button(label=f"{preset.name}\n{preset.kelvin}K")
            chip.connect("clicked", self._on_preset_clicked, preset.kelvin)
            self._chip_box.append(chip)
        scale_box.append(self._chip_box)

        self._resume_button = Gtk.Button(label="Resume schedule")
        self._resume_button.set_halign(Gtk.Align.START)
        self._resume_button.set_sensitive(False)
        self._resume_button.connect("clicked", self._on_resume_clicked)
        scale_box.append(self._resume_button)

        manual_group.add(scale_box)
        self.append(manual_group)

        self._state.subscribe(self._render)
        self._render()

    # -- rendering ----------------------------------------------------------

    def _render(self) -> None:
        cfg = self._state.config
        status = self._state.status

        if cfg is None:
            self.set_sensitive(False)
            return
        self.set_sensitive(True)

        self._rendering = True
        try:
            try:
                index = self._mode_labels.index(cfg.mode)
            except ValueError:
                index = 0
            self._mode_row.set_selected(index)

            if not self._wake_editing:
                self._wake_row.set_text(cfg.wake)
            if not self._bedtime_editing:
                self._bedtime_row.set_text(cfg.bedtime)

            now = datetime.now(ZoneInfo(cfg.location.timezone))
            ctx = build_day_context(cfg, now)
            self._curve_editor.set_curve(
                effective_keypoints(cfg), ctx, cfg.min_kelvin, cfg.max_kelvin, now
            )

            self._scale.render(
                cfg.min_kelvin, cfg.max_kelvin, self._display_kelvin(status, cfg)
            )
        finally:
            self._rendering = False

        self._resume_button.set_sensitive(
            status is not None and any(d.manual_k is not None for d in status.displays)
        )

    @staticmethod
    def _display_kelvin(status, cfg: Config) -> float:
        if status is not None and status.displays:
            d = status.displays[0]
            return float(d.manual_k if d.manual_k is not None else d.current_k)
        return (cfg.min_kelvin + cfg.max_kelvin) / 2

    # -- mode ------------------------------------------------------------

    def _on_mode_changed(self, combo: Adw.ComboRow, _pspec: GObject.ParamSpec) -> None:
        if self._rendering:
            return
        cfg = self._state.config
        if cfg is None:
            return
        mode = self._mode_labels[combo.get_selected()]
        if mode == cfg.mode:
            return
        was_custom = cfg.mode == "custom"
        new_cfg = fork_to_custom(cfg) if mode == "custom" else dataclasses.replace(cfg, mode=mode)
        if self._state.apply_config(new_cfg) and mode == "custom" and not was_custom:
            self._show_fork_banner()

    # -- curve -------------------------------------------------------------

    def _on_keypoints_changed(self, kps) -> None:
        if self._rendering:
            return
        cfg = self._state.config
        if cfg is None:
            return
        was_custom = cfg.mode == "custom"
        new_cfg = with_keypoints(cfg, kps)
        if self._state.apply_config(new_cfg) and not was_custom:
            self._show_fork_banner()

    def _show_fork_banner(self) -> None:
        if self._fork_banner_shown:
            return
        self._fork_banner_shown = True
        self._banner.set_revealed(True)

    def _on_banner_dismiss(self, _banner: Adw.Banner) -> None:
        self._banner.set_revealed(False)

    # -- wake / bedtime ------------------------------------------------------

    def _on_wake_focus_enter(self, _ctrl: Gtk.EventControllerFocus) -> None:
        self._wake_editing = True

    def _on_wake_focus_leave(self, _ctrl: Gtk.EventControllerFocus) -> None:
        self._wake_editing = False

    def _on_bedtime_focus_enter(self, _ctrl: Gtk.EventControllerFocus) -> None:
        self._bedtime_editing = True

    def _on_bedtime_focus_leave(self, _ctrl: Gtk.EventControllerFocus) -> None:
        self._bedtime_editing = False

    def _on_wake_apply(self, row: Adw.EntryRow) -> None:
        if self._rendering:
            return
        cfg = self._state.config
        if cfg is None:
            return
        parsed = _parse_hhmm(row.get_text())
        if parsed is None:
            row.set_text(cfg.wake)
            return
        hour, minute = parsed
        self._state.apply_config(dataclasses.replace(cfg, wake=f"{hour:02d}:{minute:02d}"))

    def _on_bedtime_apply(self, row: Adw.EntryRow) -> None:
        if self._rendering:
            return
        cfg = self._state.config
        if cfg is None:
            return
        parsed = _parse_hhmm(row.get_text())
        if parsed is None:
            row.set_text(cfg.bedtime)
            return
        hour, minute = parsed
        self._state.apply_config(dataclasses.replace(cfg, bedtime=f"{hour:02d}:{minute:02d}"))

    # -- manual temperature --------------------------------------------------

    def _on_preset_clicked(self, _button: Gtk.Button, kelvin: int) -> None:
        if self._rendering:
            return
        self._scale.commit_explicit(float(kelvin))

    def _commit_manual_temp(self, kelvin: float) -> None:
        try:
            self._client.set_manual_temp("", round(kelvin), 0)
        except Exception as exc:
            log.exception("set_manual_temp failed")
            self._state.set_error(str(exc))

    def _on_resume_clicked(self, _button: Gtk.Button) -> None:
        try:
            self._client.clear_manual_temp("")
        except Exception as exc:
            log.exception("clear_manual_temp failed")
            self._state.set_error(str(exc))
