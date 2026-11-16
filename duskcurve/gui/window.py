"""The main application window: assembles the four views around the store.

Like `app.py`, it imports `gi` at module scope. `_render()` is the `AppState` subscriber and
`state.clear_error()` notifies it again, so `_rendering` makes that nested render a no-op.
The one-time formatting banner is driven by wrapping `state.apply_config` and watching for
the first success; views look the method up on the instance, so they use the wrapper.
"""

import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")

from gi.repository import Adw, Gio, GObject, Gtk  # noqa: E402

from duskcurve.daemon.config import Config  # noqa: E402
from duskcurve.gui.client import DaemonClient  # noqa: E402
from duskcurve.gui.display_panel import DisplayView  # noqa: E402
from duskcurve.gui.prefs import PreferencesDialog  # noqa: E402
from duskcurve.gui.rules_panel import RulesView  # noqa: E402
from duskcurve.gui.schedule_view import ScheduleView  # noqa: E402
from duskcurve.gui.state import AppState  # noqa: E402
from duskcurve.gui.theme_view import ThemeView  # noqa: E402

log = logging.getLogger("duskcurve.gui.window")

_APP_NAME = "Duskcurve"
_APP_VERSION = "0.1.0"
_DEFAULT_TICK_SECONDS = 2.0
_NARROW_WIDTH = "550sp"

# Adw.ApplicationWindow has no minimum size, so the window could shrink below what its content
# fits in (AdwToolbarView "exceeds height" warnings). 360 is the conventional single-column
# width, under the narrow breakpoint; taller content scrolls per view.
_MIN_WIDTH = 360
_MIN_HEIGHT = 480


_FORMATTING_BANNER_TEXT = (
    "Duskcurve now owns this config file's formatting -- hand-written comments "
    "and layout will not survive the next save."
)


def _scrolled(child: Gtk.Widget) -> Gtk.ScrolledWindow:
    """Wrap `child` so a too-short window scrolls instead of clipping it.

    Vertical-only: horizontal-policy NEVER keeps a view page from ever
    scrolling sideways, matching how every page here lays out (a single
    vertical column of rows/groups).
    """
    scrolled = Gtk.ScrolledWindow()
    scrolled.set_child(child)
    scrolled.set_vexpand(True)
    scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    return scrolled


class DuskcurveWindow(Adw.ApplicationWindow):
    def __init__(self, application: Adw.Application, state: AppState, client: DaemonClient) -> None:
        super().__init__(application=application, title=_APP_NAME)
        self.set_default_size(900, 700)
        self.set_size_request(_MIN_WIDTH, _MIN_HEIGHT)

        self._state = state
        self._client = client

        self._rendering = False
        self._formatting_banner_shown = False
        # A second close-request must not re-run teardown.
        self._closing = False

        self._install_apply_success_hook()

        # -- header bar -------------------------------------------------
        header = Adw.HeaderBar()

        self._pause_button = Gtk.ToggleButton(icon_name="media-playback-pause-symbolic")
        self._pause_button.set_tooltip_text("Pause the schedule")
        self._pause_button.connect("toggled", self._on_pause_toggled)
        header.pack_start(self._pause_button)

        self._temp_label = Gtk.Label(label="")
        self._temp_label.add_css_class("dim-label")
        header.pack_start(self._temp_label)

        self._view_stack = Adw.ViewStack()
        self._schedule_view = ScheduleView(state, client)
        self._display_view = DisplayView(state, client)
        self._rules_view = RulesView(state)
        self._theme_view = ThemeView(state, client)
        self._view_stack.add_titled_with_icon(
            _scrolled(self._schedule_view), "schedule", "Schedule", "alarm-symbolic"
        )
        self._view_stack.add_titled_with_icon(
            _scrolled(self._display_view), "displays", "Displays", "video-display-symbolic"
        )
        self._view_stack.add_titled_with_icon(
            _scrolled(self._rules_view), "rules", "Rules", "preferences-system-symbolic"
        )
        self._view_stack.add_titled_with_icon(
            _scrolled(self._theme_view), "theme", "Theme", "weather-clear-night-symbolic"
        )

        self._view_switcher = Adw.ViewSwitcher(
            stack=self._view_stack, policy=Adw.ViewSwitcherPolicy.WIDE
        )
        header.set_title_widget(self._view_switcher)

        self._narrow_title = Adw.WindowTitle(title=_APP_NAME)

        menu = Gio.Menu()
        menu.append("Preferences", "win.preferences")
        menu.append("About Duskcurve", "win.about")
        menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic")
        menu_button.set_menu_model(menu)
        header.pack_end(menu_button)

        self._install_actions()

        # -- narrow-width switcher bar ------------------------------------
        self._view_switcher_bar = Adw.ViewSwitcherBar(stack=self._view_stack)

        condition = Adw.BreakpointCondition.parse(f"max-width: {_NARROW_WIDTH}")
        breakpoint_ = Adw.Breakpoint.new(condition)
        breakpoint_.add_setter(header, "title-widget", self._narrow_title)
        breakpoint_.add_setter(self._view_switcher_bar, "reveal", True)
        self.add_breakpoint(breakpoint_)

        # -- formatting banner ----------------------------------------------
        self._banner = Adw.Banner(title=_FORMATTING_BANNER_TEXT, button_label="Got it")
        self._banner.connect("button-clicked", self._on_banner_dismiss)

        # -- disconnected status page -----------------------------------
        self._status_page = Adw.StatusPage(
            icon_name="dialog-warning-symbolic",
            title="Duskcurve daemon not running",
            description=(
                "The Duskcurve session daemon is not reachable over D-Bus. Check its "
                "status with `systemctl --user status duskcurve-daemon`, or start it with "
                "`systemctl --user start duskcurve-daemon`. This page updates automatically "
                "once the daemon appears."
            ),
        )
        retry_button = Gtk.Button(label="Retry")
        retry_button.set_halign(Gtk.Align.CENTER)
        retry_button.add_css_class("pill")
        retry_button.add_css_class("suggested-action")
        retry_button.connect("clicked", self._on_retry_clicked)
        self._status_page.set_child(retry_button)

        # -- body: swaps between the view stack and the status page ------
        self._body_stack = Gtk.Stack()
        self._body_stack.add_named(self._view_stack, "content")
        self._body_stack.add_named(self._status_page, "disconnected")

        content_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        content_box.append(self._banner)
        content_box.append(self._body_stack)

        self._toast_overlay = Adw.ToastOverlay()
        self._toast_overlay.set_child(content_box)

        toolbar_view = Adw.ToolbarView()
        toolbar_view.add_top_bar(header)
        toolbar_view.add_bottom_bar(self._view_switcher_bar)
        toolbar_view.set_content(self._toast_overlay)
        self.set_content(toolbar_view)

        # -- lifecycle ----------------------------------------------------
        self.connect("map", self._on_map)
        self.connect("unmap", self._on_unmap)
        self.connect("close-request", self._on_close_request)

        self._unsubscribe = self._state.subscribe(self._render)
        self._render()

    # -- construction helpers --------------------------------------------

    def _install_apply_success_hook(self) -> None:
        """Wrap apply_config to observe success without AppState knowing about the banner."""
        original_apply_config = self._state.apply_config

        def wrapped(cfg: Config) -> bool:
            ok = original_apply_config(cfg)
            if ok:
                self._on_apply_success()
            return ok

        self._state.apply_config = wrapped  # type: ignore[method-assign]  # instance-level wrap

    def _install_actions(self) -> None:
        preferences_action = Gio.SimpleAction.new("preferences", None)
        preferences_action.connect("activate", self._on_preferences_activate)
        self.add_action(preferences_action)

        about_action = Gio.SimpleAction.new("about", None)
        about_action.connect("activate", self._on_about_activate)
        self.add_action(about_action)

    # -- rendering --------------------------------------------------------

    def _render(self) -> None:
        if self._rendering:
            return
        self._rendering = True
        try:
            self._body_stack.set_visible_child_name(
                "content" if self._state.connected else "disconnected"
            )

            status = self._state.status
            if status is not None:
                self._pause_button.set_active(status.paused)
            self._update_temp_label(status)

            self._maybe_show_error()
        finally:
            self._rendering = False

    def _update_temp_label(self, status) -> None:
        if status is None or not status.displays:
            self._temp_label.set_label("")
            return
        parts = []
        for d in status.displays:
            parts.append(f"{d.connector} {d.live_kelvin} K")
        self._temp_label.set_label(" · ".join(parts))

    def _maybe_show_error(self) -> None:
        error = self._state.last_error
        if error is None:
            return
        self._toast_overlay.add_toast(Adw.Toast(title=error))
        # Acknowledge it so a later render does not show it again. clear_error() re-enters
        # _render() synchronously; the _rendering guard makes that a no-op.
        self._state.clear_error()

    def _on_apply_success(self) -> None:
        if self._formatting_banner_shown:
            return
        self._formatting_banner_shown = True
        self._banner.set_revealed(True)

    def _on_banner_dismiss(self, _banner: Adw.Banner) -> None:
        self._banner.set_revealed(False)

    # -- header bar handlers ------------------------------------------------

    def _on_pause_toggled(self, button: Gtk.ToggleButton) -> None:
        if self._rendering:
            return
        attempted = button.get_active()
        try:
            self._client.set_paused(attempted)
        except Exception as exc:
            log.exception("set_paused failed")
            self._state.set_error(str(exc))
            # The daemon did not apply `attempted`: revert the toggle to the store's value,
            # under the guard so it does not re-enter _on_pause_toggled.
            status = self._state.status
            reverted = status.paused if status is not None else not attempted
            self._rendering = True
            try:
                button.set_active(reverted)
            finally:
                self._rendering = False

    def _on_retry_clicked(self, _button: Gtk.Button) -> None:
        self._client.refresh()

    def _on_preferences_activate(
        self, _action: Gio.SimpleAction, _param: GObject.GParamSpec | None
    ) -> None:
        dialog = PreferencesDialog(self._state)
        dialog.present(self)

    def _on_about_activate(
        self, _action: Gio.SimpleAction, _param: GObject.GParamSpec | None
    ) -> None:
        dialog = Adw.AboutDialog(
            application_name=_APP_NAME,
            application_icon="org.duskcurve.gui",
            version=_APP_VERSION,
            developer_name="Duskcurve contributors",
            license_type=Gtk.License.GPL_3_0,
            comments="Circadian colour temperature for GNOME/Wayland.",
        )
        dialog.present(self)

    # -- polling / connection lifecycle --------------------------------------

    def _on_map(self, _widget: Gtk.Widget) -> None:
        cfg = self._state.config
        interval = cfg.tick_interval_seconds if cfg is not None else _DEFAULT_TICK_SECONDS
        self._client.start_polling(interval)

    def _on_unmap(self, _widget: Gtk.Widget) -> None:
        self._client.stop_polling()

    def _on_close_request(self, _widget: Gtk.Widget) -> bool:
        if self._closing:
            return False
        self._closing = True
        self._unsubscribe()
        self._client.stop()
        # False lets the default handler destroy the window.
        return False
