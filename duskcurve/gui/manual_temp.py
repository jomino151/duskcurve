"""The shared manual-temperature scale.

`schedule_view.py` (global) and `display_panel.py` (per connector) both need
the same widget: a Kelvin `Gtk.Scale` whose value is rewritten by every status
poll, but which must commit a `SetManualTemp` only when the *user* moved it.
This module owns that widget so the three guards it needs live in one place
rather than being reimplemented per consumer. It is gi-dependent and is
imported only by widget code, never by `duskcurve.gui.__init__` or a pure
module.

Three pieces of state, all here:

- **Gesture guard** (`grabbed`): while the pointer holds the slider, `render()`
  refuses to push a fresh value in -- otherwise the poll would fight the drag.
  The gesture controller runs in the CAPTURE phase so it sees the press before
  `Gtk.Scale`'s own handler consumes it.
- **Dirty flag** (`_dirty`): set only by a `value-changed` that did *not*
  originate from a programmatic push. Commit-on-release and commit-on-focus-
  leave both check it, so merely focusing (or tabbing through) the scale, or
  watching the poll write a value into it, can never send a temperature the
  user did not ask for. Since `duration_min=0` means "until explicitly
  cleared", such a write would pin the schedule indefinitely.
- **Push guard** (`_pushing`): True only while this module is writing a value
  into the widget, so the resulting `value-changed` is not mistaken for a user
  edit. The owning view's own `_rendering` flag is consulted as well, since a
  view may reshape bounds outside `render()`.
"""

import gi

gi.require_version("Gtk", "4.0")

from gi.repository import Gtk  # noqa: E402

SCALE_STEP = 50.0


class ManualTempScale(Gtk.Scale):
    """A Kelvin scale that commits only deliberate user changes.

    `on_commit(kelvin)` is called with the widget's value when the user
    finishes an interaction they actually made. `is_rendering()` must return
    the owning view's re-entrancy flag.
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
        """Push store values in, unless the user is holding the slider.

        Clears the dirty flag: whatever is in the widget afterwards came from
        the store, not from the user, so it must not be committed on the next
        focus-leave.
        """
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
        """Commit a value the user picked outside the scale (a preset chip).

        The value is pushed in first so the widget agrees with what was sent,
        and the push clears the dirty flag so a later focus-leave does not
        re-send it.
        """
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
