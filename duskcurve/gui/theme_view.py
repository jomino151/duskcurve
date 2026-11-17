"""The Theme page: automatic light/dark desktop switching plus a manual override.

`_rendering` guards every handler while `_render()` runs; focused rows are not rewritten.
`Adw.EntryRow::apply` skips focus-out, so the custom-time rows also commit on focus-leave.
"""

import dataclasses
import logging
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Adw, GLib, GObject, Gtk  # noqa: E402

from duskcurve.daemon.config import Config  # noqa: E402
from duskcurve.gui.client import DaemonClient  # noqa: E402
from duskcurve.gui.state import AppState  # noqa: E402
from duskcurve.gui.themes import list_gtk_themes, list_shell_themes  # noqa: E402
from duskcurve.theme import ThemeVariant  # noqa: E402

log = logging.getLogger("duskcurve.gui.theme_view")

_EVENT_ANCHORS = ("sunrise", "sunset", "wake", "bedtime")
_ANCHOR_LABELS = [*_EVENT_ANCHORS, "Custom time"]
_CUSTOM_ANCHOR_INDEX = len(_EVENT_ANCHORS)
_DEFAULT_CUSTOM_LIGHT = "07:00"
_DEFAULT_CUSTOM_DARK = "19:00"

_COLOR_SCHEME_LABELS = ["Leave alone", "default", "prefer-light", "prefer-dark"]
_COLOR_SCHEME_VALUES = ["", "default", "prefer-light", "prefer-dark"]

_OFFSET_MIN = -720.0
_OFFSET_MAX = 720.0


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


def _anchor_index(anchor: str) -> int:
    if anchor in _EVENT_ANCHORS:
        return _EVENT_ANCHORS.index(anchor)
    return _CUSTOM_ANCHOR_INDEX


def _ensure_option(labels: list[str], values: list[str], value: str) -> None:
    """Append `value` (with itself as the label) if it is not already an option.

    Keeps an unscanned or hand-edited theme name visible and selected.
    """
    if value not in values:
        values.append(value)
        labels.append(value)


class ThemeView(Gtk.Box):
    def __init__(self, state: AppState, client: DaemonClient) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._state = state
        self._client = client

        self._rendering = False
        self._light_offset_editing = False
        self._dark_offset_editing = False
        self._light_custom_editing = False
        self._dark_custom_editing = False

        self.set_margin_top(12)
        self.set_margin_bottom(12)
        self.set_margin_start(12)
        self.set_margin_end(12)

        # -- header: Light / Dark / Auto ------------------------------------
        header_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header_box.set_halign(Gtk.Align.CENTER)

        self._light_toggle = Gtk.ToggleButton(label="Light")
        self._dark_toggle = Gtk.ToggleButton(label="Dark")
        self._auto_toggle = Gtk.ToggleButton(label="Auto")
        self._dark_toggle.set_group(self._light_toggle)
        self._auto_toggle.set_group(self._light_toggle)
        self._light_toggle.connect("toggled", self._on_light_toggle)
        self._dark_toggle.connect("toggled", self._on_dark_toggle)
        self._auto_toggle.connect("toggled", self._on_auto_toggle)
        header_box.append(self._light_toggle)
        header_box.append(self._dark_toggle)
        header_box.append(self._auto_toggle)
        self.append(header_box)

        page = Adw.PreferencesPage()
        self.append(page)

        # -- Automatic switching ---------------------------------------------
        auto_group = Adw.PreferencesGroup(title="Automatic switching")
        page.add(auto_group)

        self._enabled_row = Adw.SwitchRow(title="Enabled")
        self._enabled_row.connect("notify::active", self._on_enabled_changed)
        auto_group.add(self._enabled_row)

        (
            self._light_anchor_row,
            self._light_offset_row,
            self._light_custom_row,
        ) = self._build_anchor_rows(
            auto_group,
            "Light from",
            self._on_light_anchor_changed,
            self._on_light_offset_changed,
            self._on_light_custom_apply,
            self._on_light_custom_focus_enter,
            self._on_light_custom_focus_leave,
            self._on_light_offset_focus_enter,
            self._on_light_offset_focus_leave,
        )
        (
            self._dark_anchor_row,
            self._dark_offset_row,
            self._dark_custom_row,
        ) = self._build_anchor_rows(
            auto_group,
            "Dark from",
            self._on_dark_anchor_changed,
            self._on_dark_offset_changed,
            self._on_dark_custom_apply,
            self._on_dark_custom_focus_enter,
            self._on_dark_custom_focus_leave,
            self._on_dark_offset_focus_enter,
            self._on_dark_offset_focus_leave,
        )

        # -- Light / Dark variants --------------------------------------------
        self._light_widgets: dict = {}
        light_group = self._build_variant_group("Light", "light", self._light_widgets)
        page.add(light_group)

        self._dark_widgets: dict = {}
        dark_group = self._build_variant_group("Dark", "dark", self._dark_widgets)
        page.add(dark_group)

        self._state.subscribe(self._render)
        self._render()

    # -- construction helpers ---------------------------------------------

    def _build_anchor_rows(
        self,
        group: Adw.PreferencesGroup,
        title: str,
        on_anchor_changed,
        on_offset_changed,
        on_custom_apply,
        on_custom_focus_enter,
        on_custom_focus_leave,
        on_offset_focus_enter,
        on_offset_focus_leave,
    ) -> tuple[Adw.ComboRow, Adw.SpinRow, Adw.EntryRow]:
        anchor_row = Adw.ComboRow(title=title, model=Gtk.StringList.new(_ANCHOR_LABELS))
        anchor_row.connect("notify::selected", on_anchor_changed)
        group.add(anchor_row)

        offset_row = Adw.SpinRow.new_with_range(_OFFSET_MIN, _OFFSET_MAX, 1.0)
        offset_row.set_title("Offset (min)")
        offset_row.connect("notify::value", on_offset_changed)
        offset_focus = Gtk.EventControllerFocus()
        offset_focus.connect("enter", on_offset_focus_enter)
        offset_focus.connect("leave", on_offset_focus_leave)
        offset_row.add_controller(offset_focus)
        group.add(offset_row)

        # Adw.EntryRow::apply fires only with show-apply-button set.
        custom_row = Adw.EntryRow(title="Custom time (HH:MM)")
        custom_row.set_show_apply_button(True)
        custom_row.connect("apply", on_custom_apply)
        custom_focus = Gtk.EventControllerFocus()
        custom_focus.connect("enter", on_custom_focus_enter)
        custom_focus.connect("leave", on_custom_focus_leave)
        custom_row.add_controller(custom_focus)
        group.add(custom_row)

        return anchor_row, offset_row, custom_row

    def _build_variant_group(self, title: str, key: str, widgets: dict) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title=title)

        color_row = Adw.ComboRow(
            title="Color scheme", model=Gtk.StringList.new(list(_COLOR_SCHEME_LABELS))
        )
        color_row.connect("notify::selected", self._on_variant_combo_changed, key, "color_scheme")
        group.add(color_row)
        widgets["color_scheme"] = (
            color_row,
            list(_COLOR_SCHEME_LABELS),
            list(_COLOR_SCHEME_VALUES),
        )

        for field_name, title_text, options in (
            ("gtk_theme", "GTK theme", list_gtk_themes()),
            ("icon_theme", "Icon theme", list_gtk_themes()),
            ("cursor_theme", "Cursor theme", list_gtk_themes()),
            ("shell_theme", "Shell theme", list_shell_themes()),
        ):
            labels = ["Leave alone", *options]
            values = ["", *options]
            row = Adw.ComboRow(title=title_text, model=Gtk.StringList.new(labels))
            row.connect("notify::selected", self._on_variant_combo_changed, key, field_name)
            group.add(row)
            widgets[field_name] = (row, labels, values)

        wallpaper_row = Adw.ActionRow(title="Wallpaper")
        choose_button = Gtk.Button(label="Choose…")
        choose_button.set_valign(Gtk.Align.CENTER)
        choose_button.connect("clicked", self._on_wallpaper_choose, key)
        clear_button = Gtk.Button(label="Clear")
        clear_button.set_valign(Gtk.Align.CENTER)
        clear_button.connect("clicked", self._on_wallpaper_clear, key)
        button_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        button_box.append(choose_button)
        button_box.append(clear_button)
        wallpaper_row.add_suffix(button_box)
        group.add(wallpaper_row)
        widgets["wallpaper_row"] = wallpaper_row

        return group

    # -- rendering ----------------------------------------------------------

    def _render(self) -> None:
        cfg: Config | None = self._state.config
        status = self._state.status

        if cfg is None:
            self.set_sensitive(False)
            return
        self.set_sensitive(True)

        self._rendering = True
        try:
            theme = cfg.theme
            self._enabled_row.set_active(theme.enabled)

            self._render_anchor(
                theme.light_from,
                theme.light_offset_min,
                self._light_anchor_row,
                self._light_offset_row,
                self._light_custom_row,
                self._light_offset_editing,
                self._light_custom_editing,
            )
            self._render_anchor(
                theme.dark_from,
                theme.dark_offset_min,
                self._dark_anchor_row,
                self._dark_offset_row,
                self._dark_custom_row,
                self._dark_offset_editing,
                self._dark_custom_editing,
            )

            self._render_variant(theme.light, self._light_widgets)
            self._render_variant(theme.dark, self._dark_widgets)

            manual = status.theme.manual if status is not None else None
            self._light_toggle.set_active(manual == "light")
            self._dark_toggle.set_active(manual == "dark")
            self._auto_toggle.set_active(manual is None)
        finally:
            self._rendering = False

    @staticmethod
    def _render_anchor(
        anchor: str,
        offset_min: int,
        anchor_row: Adw.ComboRow,
        offset_row: Adw.SpinRow,
        custom_row: Adw.EntryRow,
        offset_editing: bool,
        custom_editing: bool,
    ) -> None:
        anchor_row.set_selected(_anchor_index(anchor))
        is_custom = anchor not in _EVENT_ANCHORS
        offset_row.set_visible(not is_custom)
        custom_row.set_visible(is_custom)
        if not offset_editing:
            offset_row.set_value(offset_min)
        if is_custom and not custom_editing:
            custom_row.set_text(anchor)

    @staticmethod
    def _render_variant(variant: ThemeVariant, widgets: dict) -> None:
        color_row, _color_labels, color_values = widgets["color_scheme"]
        value = variant.color_scheme if variant.color_scheme in color_values else ""
        color_row.set_selected(color_values.index(value))

        for field_name in ("gtk_theme", "icon_theme", "cursor_theme", "shell_theme"):
            row, labels, values = widgets[field_name]
            value = getattr(variant, field_name)
            _ensure_option(labels, values, value)
            if row.get_model().get_n_items() != len(labels):
                row.set_model(Gtk.StringList.new(labels))
            row.set_selected(values.index(value))

        widgets["wallpaper_row"].set_subtitle(variant.wallpaper or "Not set")

    # -- automatic switching: enabled ----------------------------------------

    def _on_enabled_changed(self, row: Adw.SwitchRow, _pspec: GObject.ParamSpec) -> None:
        if self._rendering:
            return
        cfg = self._state.config
        if cfg is None:
            return
        self._state.apply_config(
            dataclasses.replace(cfg, theme=dataclasses.replace(cfg.theme, enabled=row.get_active()))
        )

    # -- automatic switching: light anchor -----------------------------------

    def _on_light_offset_focus_enter(self, _ctrl: Gtk.EventControllerFocus) -> None:
        self._light_offset_editing = True

    def _on_light_offset_focus_leave(self, _ctrl: Gtk.EventControllerFocus) -> None:
        self._light_offset_editing = False

    def _on_light_custom_focus_enter(self, _ctrl: Gtk.EventControllerFocus) -> None:
        self._light_custom_editing = True

    def _on_light_custom_focus_leave(self, _ctrl: Gtk.EventControllerFocus) -> None:
        self._light_custom_editing = False
        self._on_light_custom_apply(self._light_custom_row)

    def _on_light_anchor_changed(self, row: Adw.ComboRow, _pspec: GObject.ParamSpec) -> None:
        self._on_anchor_changed(row, "light", _DEFAULT_CUSTOM_LIGHT)

    def _on_light_offset_changed(self, row: Adw.SpinRow, _pspec: GObject.ParamSpec) -> None:
        self._on_offset_changed(row, "light")

    def _on_light_custom_apply(self, row: Adw.EntryRow) -> None:
        self._on_custom_apply(row, "light")

    # -- automatic switching: dark anchor ------------------------------------

    def _on_dark_offset_focus_enter(self, _ctrl: Gtk.EventControllerFocus) -> None:
        self._dark_offset_editing = True

    def _on_dark_offset_focus_leave(self, _ctrl: Gtk.EventControllerFocus) -> None:
        self._dark_offset_editing = False

    def _on_dark_custom_focus_enter(self, _ctrl: Gtk.EventControllerFocus) -> None:
        self._dark_custom_editing = True

    def _on_dark_custom_focus_leave(self, _ctrl: Gtk.EventControllerFocus) -> None:
        self._dark_custom_editing = False
        self._on_dark_custom_apply(self._dark_custom_row)

    def _on_dark_anchor_changed(self, row: Adw.ComboRow, _pspec: GObject.ParamSpec) -> None:
        self._on_anchor_changed(row, "dark", _DEFAULT_CUSTOM_DARK)

    def _on_dark_offset_changed(self, row: Adw.SpinRow, _pspec: GObject.ParamSpec) -> None:
        self._on_offset_changed(row, "dark")

    def _on_dark_custom_apply(self, row: Adw.EntryRow) -> None:
        self._on_custom_apply(row, "dark")

    # -- automatic switching: shared handlers ---------------------------------

    def _on_anchor_changed(self, row: Adw.ComboRow, key: str, default_custom: str) -> None:
        if self._rendering:
            return
        cfg = self._state.config
        if cfg is None:
            return
        index = row.get_selected()
        if index == Gtk.INVALID_LIST_POSITION:
            return
        from_field = f"{key}_from"
        current = getattr(cfg.theme, from_field)
        if index < _CUSTOM_ANCHOR_INDEX:
            anchor = _EVENT_ANCHORS[index]
        else:
            if current not in _EVENT_ANCHORS:
                return  # already custom -- the entry row owns the value now
            anchor = default_custom
        if anchor == current:
            return
        changes: dict[str, Any] = {from_field: anchor}
        self._state.apply_config(
            dataclasses.replace(cfg, theme=dataclasses.replace(cfg.theme, **changes))
        )

    def _on_offset_changed(self, row: Adw.SpinRow, key: str) -> None:
        if self._rendering:
            return
        cfg = self._state.config
        if cfg is None:
            return
        offset_field = f"{key}_offset_min"
        value = int(row.get_value())
        if value == getattr(cfg.theme, offset_field):
            return
        changes: dict[str, Any] = {offset_field: value}
        self._state.apply_config(
            dataclasses.replace(cfg, theme=dataclasses.replace(cfg.theme, **changes))
        )

    def _on_custom_apply(self, row: Adw.EntryRow, key: str) -> None:
        if self._rendering:
            return
        cfg = self._state.config
        if cfg is None:
            return
        from_field = f"{key}_from"
        current = getattr(cfg.theme, from_field)
        parsed = _parse_hhmm(row.get_text())
        if parsed is None:
            row.set_text(current if current not in _EVENT_ANCHORS else "")
            return
        hour, minute = parsed
        anchor = f"{hour:02d}:{minute:02d}"
        if anchor == current:
            return
        changes: dict[str, Any] = {from_field: anchor}
        self._state.apply_config(
            dataclasses.replace(cfg, theme=dataclasses.replace(cfg.theme, **changes))
        )

    # -- variants -------------------------------------------------------------

    def _on_variant_combo_changed(
        self, row: Adw.ComboRow, _pspec: GObject.ParamSpec, key: str, field_name: str
    ) -> None:
        if self._rendering:
            return
        widgets = self._light_widgets if key == "light" else self._dark_widgets
        _row, _labels, values = widgets[field_name]
        index = row.get_selected()
        if index == Gtk.INVALID_LIST_POSITION or index >= len(values):
            return
        self._write_variant(key, **{field_name: values[index]})

    def _write_variant(self, key: str, **kwargs) -> None:
        cfg = self._state.config
        if cfg is None:
            return
        theme = cfg.theme
        variant = getattr(theme, key)
        new_variant = dataclasses.replace(variant, **kwargs)
        new_theme = dataclasses.replace(theme, **{key: new_variant})
        self._state.apply_config(dataclasses.replace(cfg, theme=new_theme))

    def _on_wallpaper_choose(self, _button: Gtk.Button, key: str) -> None:
        if self._rendering:
            return
        dialog = Gtk.FileDialog()
        root = self.get_root()
        dialog.open(root, None, self._on_wallpaper_chosen, key)

    def _on_wallpaper_chosen(self, dialog: Gtk.FileDialog, result, key: str) -> None:
        try:
            file = dialog.open_finish(result)
        except GLib.Error:
            return  # the user cancelled the dialog
        if file is None:
            return
        path = file.get_path()
        if not path:
            return
        self._write_variant(key, wallpaper=path)

    def _on_wallpaper_clear(self, _button: Gtk.Button, key: str) -> None:
        if self._rendering:
            return
        self._write_variant(key, wallpaper="")

    # -- header: manual override ------------------------------------------

    def _on_light_toggle(self, button: Gtk.ToggleButton) -> None:
        if self._rendering or not button.get_active():
            return
        try:
            self._client.set_theme("light", 0)
        except Exception as exc:
            log.exception("set_theme failed")
            self._state.set_error(str(exc))

    def _on_dark_toggle(self, button: Gtk.ToggleButton) -> None:
        if self._rendering or not button.get_active():
            return
        try:
            self._client.set_theme("dark", 0)
        except Exception as exc:
            log.exception("set_theme failed")
            self._state.set_error(str(exc))

    def _on_auto_toggle(self, button: Gtk.ToggleButton) -> None:
        if self._rendering or not button.get_active():
            return
        try:
            self._client.clear_theme()
        except Exception as exc:
            log.exception("clear_theme failed")
            self._state.set_error(str(exc))
