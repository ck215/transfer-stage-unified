"""ROTATOR-13: neither desktop view gated its controls on a missing stage.

The model half and the web half landed in earlier waves. This is the two GUI
halves, and the row closes only with all four.

**What these assert, and why it is not what the first draft asserted.** The
original tests here proved that `_mode_name()` *returns* `"disconnected"` —
the input to the gating decision — and stopped there. That is the
reachable-half pattern this branch keeps having to downgrade rows for: the
finding is "neither GUI view **disables its rotator controls**", so the test
has to reach `_sync_gates` and show a widget actually being disabled.

Both views are driven here by binding the real unbound method onto a mock
`self`, so the production `_sync_gates` body runs — against mock widgets,
with no toolkit — and the call it makes on each widget is asserted. That
keeps it in the fast gate instead of the qt pass.

The blanket rule matches the web half that already landed: a device with no
live link is not "manual", "auton" or normally operating, it simply cannot
execute anything, so every button/entry/toggle goes disabled — **including
STOP**, whose `stop()` is a no-op with `smc` as None anyway.
"""
from unittest.mock import MagicMock

from model.rotator_system import RotatorSystem


def _gate(el_type, **el):
    el.setdefault("type", el_type)
    return {"element": el, "widget": MagicMock()}


# --- the model's account of the link --------------------------------------

def test_rotator_13_no_port_reports_disconnected_not_simulated():
    """A port of None leaves `smc` None; nothing about the stage is simulated."""
    rotator = RotatorSystem(default_port=None)
    assert rotator.is_connected is False
    assert rotator.smc is None
    assert rotator.connection_status == "disconnected"


def test_rotator_13_a_live_link_reports_hardware():
    rotator = RotatorSystem(default_port=None)
    rotator.is_connected = True
    rotator.smc = MagicMock()
    assert rotator.connection_status == "hardware"


# --- PySide: the gate actually fires --------------------------------------

def test_rotator_13_pyside_disables_every_control_when_disconnected():
    from views.pyside.view import QtDynamicView

    view = MagicMock(spec=QtDynamicView)
    view.model = RotatorSystem(default_port=None)
    view._mode_name = QtDynamicView._mode_name.__get__(view)
    view._sync_gates = QtDynamicView._sync_gates.__get__(view)

    stop = _gate("button", command="stop", label="STOP")
    home = _gate("button", command="home")
    target = _gate("entry", model_attr="target_deg")
    view._gated = [stop, home, target]

    view._sync_gates()

    for name, gate in (("STOP", stop), ("Home", home), ("Target entry", target)):
        gate["widget"].setEnabled.assert_called_once_with(False), name
        assert gate["widget"].setEnabled.call_args[0][0] is False, (
            f"PySide left {name} enabled with no stage behind it; the "
            f"operator can issue a command that cannot reach hardware")


def test_rotator_13_pyside_leaves_controls_alone_when_connected():
    """The gate must be the missing stage, not a permanent disable."""
    from views.pyside.view import QtDynamicView

    rotator = RotatorSystem(default_port=None)
    rotator.is_connected = True
    rotator.smc = MagicMock()

    view = MagicMock(spec=QtDynamicView)
    view.model = rotator
    view._mode_name = QtDynamicView._mode_name.__get__(view)
    view._sync_gates = QtDynamicView._sync_gates.__get__(view)

    home = _gate("button", command="home")
    view._gated = [home]
    view._sync_gates()

    assert home["widget"].setEnabled.call_args[0][0] is True, (
        "PySide disabled a rotator control while the stage was connected — "
        "the ROTATOR-13 gate is firing on something other than the link")


# --- Tkinter: the gate actually fires -------------------------------------

def _tk_state(widget):
    """The `state=` Tk was configured with, across either call shape."""
    for call in widget.configure.call_args_list:
        if "state" in call.kwargs:
            return call.kwargs["state"]
    return None


def test_rotator_13_tk_disables_every_control_when_disconnected():
    from views.tkinter.view import DynamicView

    view = MagicMock(spec=DynamicView)
    view.model = RotatorSystem(default_port=None)
    view._mode_name = DynamicView._mode_name.__get__(view)
    view._sync_gates = DynamicView._sync_gates.__get__(view)

    stop = _gate("button", command="stop", label="STOP")
    target = _gate("entry", model_attr="target_deg")
    view._gated = [stop, target]

    view._sync_gates()

    for name, gate in (("STOP", stop), ("Target entry", target)):
        assert _tk_state(gate["widget"]) == "disabled", (
            f"Tk left {name} enabled with no stage behind it "
            f"(state={_tk_state(gate['widget'])!r})")


def test_rotator_13_tk_leaves_controls_alone_when_connected():
    from views.tkinter.view import DynamicView

    rotator = RotatorSystem(default_port=None)
    rotator.is_connected = True
    rotator.smc = MagicMock()

    view = MagicMock(spec=DynamicView)
    view.model = rotator
    view._mode_name = DynamicView._mode_name.__get__(view)
    view._sync_gates = DynamicView._sync_gates.__get__(view)

    home = _gate("button", command="home")
    view._gated = [home]
    view._sync_gates()

    assert _tk_state(home["widget"]) == "normal", (
        "Tk disabled a rotator control while the stage was connected — the "
        "ROTATOR-13 gate is firing on something other than the link")
