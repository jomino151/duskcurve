"""The main application window -- assembles the four views around the store.

This module (and `app.py`) is the only place in `fluxmax.gui` allowed to
import `gi` at module scope: everything downstream of here is UI wiring, not
logic, and both are excluded from the gi-free contract enforced by
`tests/gui/test_no_gi_import.py` (they are never imported from
`fluxmax.gui.__init__` or a pure module).

Two re-entrancy hazards are specific to this file, on top of the guard
pattern the four views already establish:

- **Render re-entrancy**: `_render()` is the `AppState` subscriber. Showing
  an error toast ends with `state.clear_error()`, which itself calls
  `AppState._notify()` -- i.e. it calls straight back into `_render()`
  before the outer call has returned. `_render()` guards against this with
  `self._rendering`, exactly like every view's own outbound-write guard,
  except here it guards *re-entrant renders* rather than outbound daemon
  calls.
- **One-time banner**: "the first successful `apply_config` of the
  session" is not a store event `AppState` exposes -- `apply_config()`
  returns a bool but nothing subscribes to *that*. The window wraps
  `state.apply_config` with a closure that observes the return value and
  fires the banner on the first `True`, then restores nothing (the wrapped
  version lives for the rest of the session). Every view calls
  `self._state.apply_config(...)`, which is an attribute lookup on the
  live instance, so they all go through the wrapped version transparently.
"""

import logging

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gio", "2.0")

from gi.repository import Adw, Gio, GObject, Gtk  # noqa: E402

from fluxmax.gui.client import DaemonClient  # noqa: E402
from fluxmax.gui.display_panel import DisplayView  # noqa: E402
from fluxmax.gui.prefs import PreferencesDialog  # noqa: E402
from fluxmax.gui.rules_panel import RulesView  # noqa: E402
from fluxmax.gui.schedule_view import ScheduleView  # noqa: E402
from fluxmax.gui.state import AppState  # noqa: E402

log = logging.getLogger("fluxmax.gui.window")

_APP_NAME = "flux-max"
_APP_VERSION = "0.1.0"
_DEFAULT_TICK_SECONDS = 2.0
_NARROW_WIDTH = "550sp"

# Adwaita has no built-in minimum size for ApplicationWindow -- without one,
# GTK lets the window shrink until content (the curve editor's 320px content
# height plus the surrounding rows) no longer fits, which is what produced
# the "AdwToolbarView exceeds ... height" warnings on real hardware. These
# are genuinely usable floors, not just warning-silencers: 360 is the
# conventional minimum width for a single-column Adwaita layout (and sits
# comfortably under the view-switcher breakpoint above), and 480 leaves
# useful headroom above a phone-sized "unusably squashed" window while still
# relying on the per-view Gtk.ScrolledWindow for anything taller.
_MIN_WIDTH = 360
_MIN_HEIGHT = 480


_FORMATTING_BANNER_TEXT = (
    "flux-max now owns this config file's formatting -- hand-written comments "
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


class FluxMaxWindow(Adw.ApplicationWindow):
    def __init__(
        self, application: Adw.Application, state: AppState, client: DaemonClient
    ) -> None:
        super().__init__(application=application, title=_APP_NAME)
        self.set_default_size(900, 700)
        # See _MIN_WIDTH/_MIN_HEIGHT: without an explicit minimum, Adwaita
        # warns ("FluxMaxWindow does not have a minimum size...") and lets
        # the window shrink past what its content can render.
        self.set_size_request(_MIN_WIDTH, _MIN_HEIGHT)

        self._state = state
        self._client = client

        # Guards a render triggered re-entrantly by state.clear_error()
        # (called from within this window's own render pass) from recursing
        # back into _render(). See module docstring.
        self._rendering = False
        self._formatting_banner_shown = False
        # Guards _on_close_request against a second close-request re-running
        # teardown (unsubscribe + stop_polling + client.stop()). Several of
        # those happen to be independently idempotent today, but the guard
        # makes that an explicit contract instead of an accident.
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
        self._view_stack.add_titled_with_icon(
            _scrolled(self._schedule_view), "schedule", "Schedule", "alarm-symbolic"
        )
        self._view_stack.add_titled_with_icon(
            _scrolled(self._display_view), "displays", "Displays", "video-display-symbolic"
        )
        self._view_stack.add_titled_with_icon(
            _scrolled(self._rules_view), "rules", "Rules", "preferences-system-symbolic"
        )

        self._view_switcher = Adw.ViewSwitcher(
            stack=self._view_stack, policy=Adw.ViewSwitcherPolicy.WIDE
        )
        header.set_title_widget(self._view_switcher)

        self._narrow_title = Adw.WindowTitle(title=_APP_NAME)

        menu = Gio.Menu()
        menu.append("Preferences", "win.preferences")
        menu.append("About flux-max", "win.about")
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
            title="flux-max daemon not running",
            description=(
                "The flux-max session daemon is not reachable over D-Bus. Check its "
                "status with `systemctl --user status fluxmax-daemon`, or start it with "
                "`systemctl --user start fluxmax-daemon`. This page updates automatically "
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
        """See module docstring: wrap apply_config to observe success without
        AppState needing to know about the one-time banner."""
        original_apply_config = self._state.apply_config

        def wrapped(cfg):
            ok = original_apply_config(cfg)
            if ok:
                self._on_apply_success()
            return ok

        self._state.apply_config = wrapped

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
        # Acknowledge it so a later, unrelated render (a routine status poll)
        # cannot show the same error again. clear_error() notifies
        # subscribers, re-entering _render() synchronously -- the
        # self._rendering guard above makes that re-entrant call a no-op.
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
            # The daemon never applied `attempted`, so the toggle must not
            # keep showing it -- otherwise the app's most prominent state
            # indicator lies until the next status poll corrects it (up to
            # tick_interval_seconds later). Revert to the store's last known
            # value under the re-entrancy guard so this doesn't loop back
            # into _on_pause_toggled itself.
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
            application_icon="org.fluxmax.gui",
            version=_APP_VERSION,
            developer_name="flux-max contributors",
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
        # False = propagate: let the default close handler run and destroy
        # the window instead of vetoing the close.
        return False
