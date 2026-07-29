"""The curve editor drawing area -- the centrepiece of the flux-max GUI.

Renders the day/night colour-temperature curve and lets the user drag,
insert and delete keypoints. This module is gi-dependent (unlike the pure
`geometry`/`editing` modules it calls into) and is imported only by widget
code, never by `fluxmax.gui.__init__` or a pure module.
"""

from collections.abc import Callable, Sequence
from datetime import datetime

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")

from gi.repository import Gdk, GObject, Gtk  # noqa: E402

from fluxmax.color import kelvin_to_rgb  # noqa: E402
from fluxmax.daemon.config import KeypointConfig  # noqa: E402
from fluxmax.gui.editing import (  # noqa: E402
    EVENT_ANCHORS,
    config_to_keypoint,
    delete_keypoint,
    insert_keypoint,
    set_keypoint_anchor,
    set_keypoint_kelvin,
    shift_keypoint_time,
)
from fluxmax.gui.geometry import (  # noqa: E402
    Viewport,
    hit_test,
    insert_index,
    kelvin_to_y,
    keypoint_points,
    minutes_to_x,
    x_to_minutes,
    y_to_kelvin,
)
from fluxmax.schedule.curve import Curve, DayContext, walk_minutes  # noqa: E402

_HANDLE_RADIUS = 7.0
_HANDLE_HIT_RADIUS = 12.0
_DRAG_TIME_THRESHOLD_PX = 4.0
_CURVE_SAMPLE_STEP_PX = 2.0
_HOUR_LABEL_STEP = 3


class CurveEditor(Gtk.DrawingArea):
    """A draggable, editable rendering of a day's colour-temperature curve."""

    def __init__(self, on_keypoints_changed: Callable[[tuple[KeypointConfig, ...]], None]) -> None:
        super().__init__()
        self._on_keypoints_changed = on_keypoints_changed

        self._kps: tuple[KeypointConfig, ...] = ()
        self._ctx: DayContext | None = None
        self._min_kelvin = 2000.0
        self._max_kelvin = 6500.0
        self._now: datetime | None = None

        # Drag state.
        self._busy = False
        self._drag_index: int | None = None
        self._drag_start_kps: tuple[KeypointConfig, ...] = ()
        self._drag_start_x = 0.0
        self._drag_start_y = 0.0
        self._drag_live_kps: tuple[KeypointConfig, ...] = ()

        self.set_content_width(600)
        self.set_content_height(320)
        self.set_focusable(True)
        self.set_draw_func(self._on_draw)

        drag = Gtk.GestureDrag.new()
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.add_controller(drag)

        primary_click = Gtk.GestureClick.new()
        primary_click.set_button(Gdk.BUTTON_PRIMARY)
        primary_click.connect("pressed", self._on_primary_pressed)
        self.add_controller(primary_click)

        secondary_click = Gtk.GestureClick.new()
        secondary_click.set_button(Gdk.BUTTON_SECONDARY)
        secondary_click.connect("pressed", self._on_secondary_pressed)
        self.add_controller(secondary_click)

    # -- public API -------------------------------------------------------

    def set_curve(
        self,
        kps: Sequence[KeypointConfig],
        ctx: DayContext,
        min_kelvin: float,
        max_kelvin: float,
        now: datetime,
    ) -> None:
        self._kps = tuple(kps)
        self._ctx = ctx
        self._min_kelvin = min_kelvin
        self._max_kelvin = max_kelvin
        self._now = now
        self.queue_draw()

    # -- geometry helpers ---------------------------------------------------

    def _viewport(self) -> Viewport:
        return Viewport(
            width=float(self.get_width() or self.get_content_width()),
            height=float(self.get_height() or self.get_content_height()),
            min_kelvin=self._min_kelvin,
            max_kelvin=self._max_kelvin,
        )

    def _active_kps(self) -> tuple[KeypointConfig, ...]:
        """The keypoint tuple to draw/hit-test: the live drag copy, if any."""
        return self._drag_live_kps if self._busy else self._kps

    def _curve(self, kps: Sequence[KeypointConfig]) -> Curve:
        return Curve([config_to_keypoint(k) for k in kps])

    # -- drawing --------------------------------------------------------

    def _on_draw(self, _area: Gtk.DrawingArea, cr, width: int, height: int) -> None:
        if not self._kps or self._ctx is None or self._now is None:
            return
        vp = Viewport(
            width=float(width), height=float(height),
            min_kelvin=self._min_kelvin, max_kelvin=self._max_kelvin,
        )
        kps = self._active_kps()
        ctx = self._ctx
        curve = self._curve(kps)

        self._draw_gradient_background(cr, vp, curve, ctx)
        self._draw_gridlines(cr, vp)
        self._draw_marker_lines(cr, vp, ctx)
        self._draw_curve_line(cr, vp, curve, ctx)
        self._draw_now_dot(cr, vp, curve, ctx)
        self._draw_handles(cr, vp, kps, ctx)

    def _draw_gradient_background(self, cr, vp: Viewport, curve: Curve, ctx: DayContext) -> None:
        step = max(1, int(_CURVE_SAMPLE_STEP_PX))
        x = vp.left
        while x < vp.right:
            minutes = x_to_minutes(x, vp)
            now = _minutes_to_datetime(minutes, self._now)
            kelvin = curve.evaluate(now, ctx)
            r, g, b = kelvin_to_rgb(kelvin)
            cr.set_source_rgba(r, g, b, 0.18)
            cr.rectangle(x, vp.top, step, vp.plot_height)
            cr.fill()
            x += step

    def _draw_gridlines(self, cr, vp: Viewport) -> None:
        cr.set_source_rgba(0.5, 0.5, 0.5, 0.35)
        cr.set_line_width(1.0)
        for hour in range(0, 25, _HOUR_LABEL_STEP):
            x = minutes_to_x(min(hour * 60, 1440), vp)
            cr.move_to(x, vp.top)
            cr.line_to(x, vp.bottom)
            cr.stroke()
            cr.move_to(x + 2, vp.bottom + 14)
            cr.set_source_rgba(0.5, 0.5, 0.5, 0.8)
            cr.show_text(f"{hour:02d}:00")
            cr.set_source_rgba(0.5, 0.5, 0.5, 0.35)

    def _draw_marker_lines(self, cr, vp: Viewport, ctx: DayContext) -> None:
        markers = (
            ("sunrise", ctx.sunrise, (0.95, 0.65, 0.2)),
            ("sunset", ctx.sunset, (0.85, 0.35, 0.2)),
            ("wake", ctx.wake, (0.3, 0.6, 0.9)),
            ("bedtime", ctx.bedtime, (0.5, 0.3, 0.8)),
        )
        cr.set_line_width(1.0)
        for _name, dt, (r, g, b) in markers:
            minutes = dt.hour * 60 + dt.minute + dt.second / 60
            x = minutes_to_x(minutes, vp)
            cr.set_source_rgba(r, g, b, 0.6)
            cr.move_to(x, vp.top)
            cr.line_to(x, vp.bottom)
            cr.stroke()

    def _draw_curve_line(self, cr, vp: Viewport, curve: Curve, ctx: DayContext) -> None:
        cr.set_line_width(2.5)
        cr.set_source_rgba(0.9, 0.9, 0.95, 0.95)
        first = True
        x = vp.left
        while x <= vp.right:
            minutes = x_to_minutes(x, vp)
            now = _minutes_to_datetime(minutes, self._now)
            kelvin = curve.evaluate(now, ctx)
            y = kelvin_to_y(kelvin, vp)
            if first:
                cr.move_to(x, y)
                first = False
            else:
                cr.line_to(x, y)
            x += _CURVE_SAMPLE_STEP_PX
        cr.stroke()

    def _draw_now_dot(self, cr, vp: Viewport, curve: Curve, ctx: DayContext) -> None:
        if self._now is None:
            return
        minutes = self._now.hour * 60 + self._now.minute + self._now.second / 60
        kelvin = curve.evaluate(self._now, ctx)
        x = minutes_to_x(minutes, vp)
        y = kelvin_to_y(kelvin, vp)
        cr.set_source_rgba(1.0, 1.0, 1.0, 0.95)
        cr.arc(x, y, 5.0, 0, 2 * 3.141592653589793)
        cr.fill()
        cr.set_source_rgba(0.1, 0.1, 0.1, 0.9)
        cr.set_line_width(1.5)
        cr.arc(x, y, 5.0, 0, 2 * 3.141592653589793)
        cr.stroke()

    def _draw_handles(
        self, cr, vp: Viewport, kps: Sequence[KeypointConfig], ctx: DayContext
    ) -> None:
        points = keypoint_points(kps, ctx, vp)
        for i, (x, y) in enumerate(points):
            selected = self._busy and i == self._drag_index
            cr.set_source_rgba(0.2, 0.2, 0.2, 0.9)
            cr.arc(x, y, _HANDLE_RADIUS + 1.5, 0, 2 * 3.141592653589793)
            cr.fill()
            r, g, b = kelvin_to_rgb(kps[i].kelvin)
            cr.set_source_rgba(r, g, b, 1.0)
            cr.arc(x, y, _HANDLE_RADIUS, 0, 2 * 3.141592653589793)
            cr.fill()
            if selected:
                cr.set_source_rgba(1.0, 1.0, 1.0, 0.9)
                cr.set_line_width(2.0)
                cr.arc(x, y, _HANDLE_RADIUS + 3.0, 0, 2 * 3.141592653589793)
                cr.stroke()

    # -- drag gesture --------------------------------------------------------

    def _on_drag_begin(self, gesture: Gtk.GestureDrag, x: float, y: float) -> None:
        if self._ctx is None or not self._kps:
            return
        vp = self._viewport()
        points = keypoint_points(self._kps, self._ctx, vp)
        index = hit_test(points, x, y, radius=_HANDLE_HIT_RADIUS)
        if index is None:
            gesture.set_state(Gtk.EventSequenceState.DENIED)
            return
        self._drag_index = index
        self._drag_start_kps = self._kps
        self._drag_live_kps = self._kps
        self._drag_start_x = x
        self._drag_start_y = y
        self._busy = True
        self.queue_draw()

    def _on_drag_update(self, _gesture: Gtk.GestureDrag, offset_x: float, offset_y: float) -> None:
        if not self._busy or self._drag_index is None or self._ctx is None:
            return
        vp = self._viewport()
        index = self._drag_index

        y = self._drag_start_y + offset_y
        kelvin = y_to_kelvin(y, vp)
        kps = set_keypoint_kelvin(
            self._drag_start_kps,
            index,
            kelvin,
            min_kelvin=self._min_kelvin,
            max_kelvin=self._max_kelvin,
        )

        if abs(offset_x) > _DRAG_TIME_THRESHOLD_PX:
            start_minutes = x_to_minutes(self._drag_start_x, vp)
            end_minutes = x_to_minutes(self._drag_start_x + offset_x, vp)
            delta_min = int(round(end_minutes - start_minutes))
            if delta_min != 0:
                kps = shift_keypoint_time(kps, index, delta_min)

        self._drag_live_kps = kps
        self.queue_draw()

    def _on_drag_end(self, _gesture: Gtk.GestureDrag, _offset_x: float, _offset_y: float) -> None:
        if not self._busy:
            return
        final = self._drag_live_kps
        started_from = self._drag_start_kps
        self._busy = False
        self._drag_index = None
        self._drag_live_kps = ()
        self._kps = final
        self.queue_draw()
        # A gesture that ends with no net change (e.g. a stray click that GTK
        # still routes through GestureDrag's begin/update/end, such as one leg
        # of a double-click landing on a handle) must not notify: downstream,
        # a notification forks a built-in mode to a custom curve and rewrites
        # the config file, so a no-op drag must be indistinguishable from no
        # gesture at all. KeypointConfig is a frozen dataclass, so tuple `!=`
        # is a correct, sufficient comparison.
        if final != started_from:
            self._on_keypoints_changed(final)

    # -- click gestures --------------------------------------------------------

    def _on_primary_pressed(
        self, gesture: Gtk.GestureClick, n_press: int, x: float, y: float
    ) -> None:
        if n_press != 2 or self._ctx is None or not self._kps:
            return
        vp = self._viewport()
        points = keypoint_points(self._kps, self._ctx, vp)
        if hit_test(points, x, y, radius=_HANDLE_HIT_RADIUS) is not None:
            return  # double-click on an existing keypoint: not an insert
        minutes = x_to_minutes(x, vp)
        walk = walk_minutes([config_to_keypoint(k) for k in self._kps], self._ctx)
        index = insert_index(walk, minutes)
        now = _minutes_to_datetime(minutes, self._now)
        kelvin = self._curve(self._kps).evaluate(now, self._ctx)
        new_kps = insert_keypoint(self._kps, index, minutes, kelvin)
        changed = new_kps != self._kps
        self._kps = new_kps
        self.queue_draw()
        if changed:
            self._on_keypoints_changed(new_kps)

    def _on_secondary_pressed(
        self, gesture: Gtk.GestureClick, _n_press: int, x: float, y: float
    ) -> None:
        if self._ctx is None or not self._kps:
            return
        vp = self._viewport()
        points = keypoint_points(self._kps, self._ctx, vp)
        index = hit_test(points, x, y, radius=_HANDLE_HIT_RADIUS)
        if index is None:
            return
        self._show_keypoint_popover(index, x, y)

    # -- keypoint context popover --------------------------------------------------------

    def _show_keypoint_popover(self, index: int, x: float, y: float) -> None:
        kc = self._kps[index]

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        box.set_margin_start(8)
        box.set_margin_end(8)

        anchor_options = (*EVENT_ANCHORS, "fixed time")
        current_anchor = kc.anchor if kc.anchor in EVENT_ANCHORS else "fixed time"
        anchor_model = Gtk.StringList.new(anchor_options)
        anchor_row = Gtk.DropDown(model=anchor_model)
        anchor_row.set_selected(anchor_options.index(current_anchor))
        box.append(anchor_row)

        delete_button = Gtk.Button(label="Delete keypoint")
        delete_button.add_css_class("destructive-action")
        delete_button.set_sensitive(len(self._kps) > 1)
        box.append(delete_button)

        popover = Gtk.Popover()
        popover.set_child(box)
        popover.set_parent(self)
        # Gdk.Rectangle(x=..., y=..., width=..., height=...) kwarg construction
        # is deprecated in this PyGObject version and SILENTLY zeroes every
        # field instead of raising -- verified live. Must build then assign
        # attributes, not pass kwargs to the constructor.
        rect = Gdk.Rectangle()
        rect.x, rect.y, rect.width, rect.height = int(x), int(y), 1, 1
        popover.set_pointing_to(rect)

        def on_anchor_changed(dropdown: Gtk.DropDown, _pspec: GObject.ParamSpec) -> None:
            selected = anchor_options[dropdown.get_selected()]
            if selected == current_anchor:
                return
            if selected == "fixed time":
                anchor_str = _minutes_to_hhmm(kc_minutes_for(index))
                new_kps = set_keypoint_anchor(self._kps, index, anchor_str)
            else:
                new_kps = set_keypoint_anchor(self._kps, index, selected, 0)
            changed = new_kps != self._kps
            self._kps = new_kps
            self.queue_draw()
            if changed:
                self._on_keypoints_changed(new_kps)
            popover.popdown()

        def kc_minutes_for(idx: int) -> float:
            assert self._ctx is not None
            walk = walk_minutes([config_to_keypoint(k) for k in self._kps], self._ctx)
            return walk[idx] % 1440.0

        def on_delete_clicked(_button: Gtk.Button) -> None:
            new_kps = delete_keypoint(self._kps, index)
            changed = new_kps != self._kps
            self._kps = new_kps
            self.queue_draw()
            if changed:
                self._on_keypoints_changed(new_kps)
            popover.popdown()

        anchor_row.connect("notify::selected", on_anchor_changed)
        delete_button.connect("clicked", on_delete_clicked)
        popover.connect("closed", lambda _p: popover.unparent())
        popover.popup()


def _minutes_to_hhmm(minutes: float) -> str:
    total = round(minutes) % 1440
    return f"{total // 60:02d}:{total % 60:02d}"


def _minutes_to_datetime(minutes: float, reference: datetime | None) -> datetime:
    """A datetime on the reference's date at `minutes` past midnight.

    Curve.evaluate only reads hour/minute/second, so any date works; using
    the widget's `now` keeps things deterministic without needing a real
    clock during rendering.
    """
    base = reference if reference is not None else datetime.now()
    total_seconds = int(round(minutes * 60)) % 86400
    hour, remainder = divmod(total_seconds, 3600)
    minute, second = divmod(remainder, 60)
    return base.replace(hour=hour, minute=minute, second=second, microsecond=0)
