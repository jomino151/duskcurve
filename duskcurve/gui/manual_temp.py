"""The Kelvin scale shared by the global and per-display manual-override controls.

Status polls rewrite the value, but a `SetManualTemp` is committed only when the user moved it.
"""

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk  # noqa: E402

SCALE_STEP = 50.0


class ManualTempScale(Gtk.Scale):
    """A Kelvin scale that commits only deliberate user changes.

    `on_commit(kelvin)` is called when the user finishes an interaction they made.
    `is_rendering()` returns the owning view's re-entrancy flag.

    State: `grabbed` (pointer holds the slider, so `render()` does not fight the drag),
    `_dirty` (a user edit is pending; a duration of 0 pins the override, so a value the user
    did not choose must never be sent) and `_pushing` (we are writing the value ourselves).
    """

    def __init__(self, on_commit, is_rendering) -> None:
        super().__init__(
            orientation=Gtk.Orientation.HORIZONTAL,
            adjustment=Gtk.Adjustment(
                lower=1000, upper=10000, step_increment=SCALE_STEP, page_increment=SCALE_STEP
            ),
        )
        self._on_commit = on_commit
        self._is_rendering = is_rendering

        self.grabbed = False
        self._dirty = False
        self._pushing = False

        self.set_hexpand(True)
        self.set_digits(0)
        self.set_value_pos(Gtk.PositionType.RIGHT)

        self.connect("value-changed", self._on_value_changed)

        # CAPTURE phase: sees the press before Gtk.Scale's own handler consumes it.
        grab_ctrl = Gtk.GestureClick.new()
        grab_ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        grab_ctrl.connect("pressed", self._on_pressed)
        grab_ctrl.connect("released", self._on_released)
        self.add_controller(grab_ctrl)

        focus_ctrl = Gtk.EventControllerFocus()
        focus_ctrl.connect("leave", self._on_focus_leave)
        self.add_controller(focus_ctrl)

    # -- rendering ---------------------------------------------------------

    def render(self, lower: float, upper: float, value: float) -> None:
        """Push store values in, unless the user is holding the slider. Clears the dirty flag."""
        if self.grabbed:
            return
        self._push(lower, upper, value)

    def _push(self, lower: float, upper: float, value: float) -> None:
        self._pushing = True
        try:
            adjustment = self.get_adjustment()
            adjustment.set_lower(lower)
            adjustment.set_upper(upper)
            self.set_value(value)
        finally:
            self._pushing = False
        self._dirty = False

    # -- commit ------------------------------------------------------------

    def commit_explicit(self, kelvin: float) -> None:
        """Commit a value picked outside the scale (a preset chip), pushing it in first."""
        adjustment = self.get_adjustment()
        self._push(adjustment.get_lower(), adjustment.get_upper(), kelvin)
        self._on_commit(float(kelvin))

    def _commit_if_dirty(self) -> None:
        if self._is_rendering() or not self._dirty:
            return
        self._dirty = False
        self._on_commit(self.get_value())

    # -- handlers ----------------------------------------------------------

    def _on_value_changed(self, _scale: Gtk.Scale) -> None:
        if self._pushing or self._is_rendering():
            return
        self._dirty = True

    def _on_pressed(self, _gesture: Gtk.GestureClick, _n: int, _x: float, _y: float) -> None:
        self.grabbed = True

    def _on_released(self, _gesture: Gtk.GestureClick, _n: int, _x: float, _y: float) -> None:
        self.grabbed = False
        self._commit_if_dirty()

    def _on_focus_leave(self, _ctrl: Gtk.EventControllerFocus) -> None:
        self.grabbed = False
        self._commit_if_dirty()
