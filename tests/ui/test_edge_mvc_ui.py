import sys
import os
import pytest
from PySide6.QtWidgets import QApplication

# Add mvc-refactor/src to path

from views.pyside.view import PlotDialog, ControllerLogWindow, RedPercentDynamicView, QtDynamicView, DashboardWindow
from model.base import SchemaCommands

def test_plot_dialog_invalid_args(qtbot):
    # Pass invalid parent
    with pytest.raises(TypeError):
        dlg = PlotDialog(parent="invalid_parent")

def test_controller_log_window_invalid_args(qtbot):
    with pytest.raises(TypeError):
        win = ControllerLogWindow(poller="not_a_poller", parent="invalid_parent")

def test_rapid_spawn_close_plot_dialog(qtbot):
    # Spawn and close rapidly
    dialogs = []
    for _ in range(50):
        dlg = PlotDialog()
        dlg.show()
        dialogs.append(dlg)
    
    for dlg in dialogs:
        dlg.close()
        dlg.deleteLater()

def test_rapid_spawn_close_controller_log_window(qtbot):
    class MockPoller:
        pass
    
    windows = []
    for _ in range(50):
        win = ControllerLogWindow(poller=MockPoller())
        win.show()
        windows.append(win)
        
    for win in windows:
        win.close()
        win.deleteLater()

def test_multiple_spawn_same_parent(qtbot):
    class DummyModel:
        pass
        
    # The parent in the real code is the DynamicView
    parent_view = QtDynamicView(DummyModel())
    
    dialogs = []
    for _ in range(10):
        dlg = PlotDialog(parent=parent_view)
        dlg.show()
        dialogs.append(dlg)
        
    for dlg in dialogs:
        dlg.close()
        
    parent_view.close()


def test_two_way_data_binding_pyside(qtbot):
    class MockModel(SchemaCommands):
        def __init__(self):
            self.step_size = "16"
            self.pos_x = "0"
            self.system_enabled = False
            
        @property
        def ui_schema(self):
            return {
                "sections": [
                    {
                        "title": "Config",
                        "elements": [
                            {"type": "entry", "text": "Step Size:", "model_attr": "step_size"},
                            {"type": "readonly", "text": "X Pos:", "model_attr": "pos_x"},
                            {"type": "toggle", "model_attr": "system_enabled", "true_text": "ON", "false_text": "OFF", "command": "toggle_enable"},
                            {"type": "file_picker", "text": "Run Script", "command": "run_script"}
                        ]
                    }
                ]
            }
            
        def toggle_enable(self):
            self.system_enabled = not self.system_enabled

    model = MockModel()
    view = QtDynamicView(model)
    qtbot.addWidget(view)
    
    # UI -> Model binding
    step_input = view.vars["step_size"]
    step_input.setText("32")
    step_input.editingFinished.emit()
    assert model.step_size == "32"
    
    # Model -> UI polling
    model.pos_x = "123.45"
    view._poll_model()
    assert view.vars["pos_x"].text() == "123.45"
    
    # Toggle button
    assert len(view.toggle_buttons) == 1
    view._execute_command("toggle_enable")
    assert model.system_enabled is True
    view._poll_model()
    assert view.toggle_buttons[0]["widget"].text() == "ON"
    
    view.cleanup()


def test_view_keeps_one_render_tick_and_no_device_loops(qtbot):
    """Re-authored in S10. It used to assert `view.pos_timer` and
    `view.status_timer`, two of the four device loops the *view* owned and
    drove with `_safe_read_position` / `_safe_poll_status`. S5 moved every
    such loop into the model (RC-4), which is what I-4.1 pins; the view keeps
    a single render tick that copies model state onto widgets.

    Asserting the absence matters as much as the presence: a renderer that
    quietly re-grows its own device loop is the defect I-4.1 exists to catch,
    and this says so at the widget level where the grep invariant cannot.
    """
    class PollingModel(SchemaCommands):
        def __init__(self):
            self.pos_count = 0
            self.readout = "idle"

        @property
        def ui_schema(self):
            return {"sections": [{
                "title": "Readout",
                "elements": [
                    {"type": "readonly", "text": "State:",
                     "model_attr": "readout"},
                ],
            }]}

        def read_position(self):
            self.pos_count += 1

    model = PollingModel()
    view = QtDynamicView(model)
    qtbot.addWidget(view)

    assert view.timer.isActive()
    for owned_by_the_model in ("pos_timer", "status_timer", "input_timer",
                               "disable_timer"):
        assert not hasattr(view, owned_by_the_model), (
            f"{owned_by_the_model} is a device loop; it belongs to the model "
            "(I-4.1)")

    # The render tick reads the model and never drives it.
    model.readout = "running"
    view._poll_model()
    assert view.vars["readout"].text() == "running"
    assert model.pos_count == 0

    view.cleanup()
    assert not view.timer.isActive()


def test_controller_log_renders_as_a_log_stream(qtbot):
    """Re-authored in S10. The controller log used to be a
    `cmd_name == "open_controller_log"` branch opening a Toplevel/QDialog —
    an element type the schema could not express, so only the two desktop
    frontends had it and the Web client had none. It is a `log_stream`
    composite now: the model buffers the lines, every renderer shows them.

    The original test guarded reopening the detached window after
    WA_DeleteOnClose. There is no window in this path to reopen; the
    equivalent hazard is the inline view going stale, so this asserts the
    render tick keeps following the model's buffer.
    """
    class LoggingModel(SchemaCommands):
        def __init__(self):
            self.lines = []

        @property
        def ui_schema(self):
            return {"sections": [{
                "title": "Controller",
                "elements": [
                    {"type": "log_stream", "text": "Controller Log:",
                     "source_command": "controller_log", "role": "neutral"},
                ],
            }]}

        def controller_log(self):
            return list(self.lines)

    model = LoggingModel()
    view = QtDynamicView(model)
    qtbot.addWidget(view)

    assert len(view._log_streams) == 1
    widget = view._log_streams[0]["widget"]
    assert widget.isReadOnly()

    model.lines.append("Test Log Entry")
    view._poll_model()
    assert "Test Log Entry" in widget.toPlainText()

    model.lines.append("Second Entry")
    view._poll_model()
    assert "Second Entry" in widget.toPlainText()

    # Only the last 40 lines are shown, and the oldest fall off rather than
    # growing the widget without bound.
    model.lines.extend(f"line {i}" for i in range(60))
    view._poll_model()
    assert "Test Log Entry" not in widget.toPlainText()
    assert "line 59" in widget.toPlainText()

    view.cleanup()


def test_redpercent_sync_dimensions(qtbot):
    """Re-authored in S10: `view.sync_cbs` was a hand-built checkbox row that
    duplicated the schema's own toggles and existed in this frontend only.
    D-6 deleted it; the dimensions are driven through the rendered toggles.
    """
    from model.redpercent_system import RedPercentSystem
    model = RedPercentSystem()
    view = RedPercentDynamicView(model)
    qtbot.addWidget(view)

    toggles = {tb["attr"]: tb["widget"] for tb in view.toggle_buttons}
    assert set(toggles) == {"sync_x", "sync_y", "sync_z"}

    toggles["sync_x"].click()
    toggles["sync_y"].click()
    assert model.sync_dimensions == ["X", "Y"]

    toggles["sync_x"].click()
    assert model.sync_dimensions == ["Y"]

    view.cleanup()


def test_dashboard_dock_lifecycle(qtbot):
    from model.system_manager import SystemManager
    from PySide6 import QtCore
    from model.probes import StepperProbe
    from PySide6.QtCore import Qt

    mgr = SystemManager()
    probe = StepperProbe("COM1", "None")
    mgr.register("Stepper Probe", probe)
    
    dash = DashboardWindow(mgr)
    qtbot.addWidget(dash)
    dash.show()
    
    dock = dash.active_docks["Stepper Probe"]
    assert dash.active_docks["Stepper Probe"].isVisible()
    
    # User closes dock
    dock.close()
    
    # Wait for the dock visibility to update and the checkbox to sync
    qtbot.waitUntil(lambda: not dock.isVisible(), timeout=1000)
    item = dash.device_list.item(0)
    assert item.checkState() == Qt.Unchecked
    
    # User checks the checkbox to restore the dock
    item.setCheckState(Qt.Checked)
    
    qtbot.waitUntil(lambda: "Stepper Probe" in dash.active_docks and dash.active_docks["Stepper Probe"].isVisible(), timeout=1000)
    new_dock = dash.active_docks["Stepper Probe"]
    assert new_dock.isVisible()
    
    # User unchecks the checkbox to hide the dock
    item.setCheckState(Qt.Unchecked)
    qtbot.waitUntil(lambda: not new_dock.isVisible(), timeout=1000)
    assert not new_dock.isVisible()
    
    dash.close()

def test_red_percent_dock_close_stops_timers(qtbot):
    """Closing a device dock stops that view's render tick — and, since S6,
    leaves the device itself alive.

    The original hazard stands: only whole-app shutdown used to call
    `widget.cleanup()`, so closing one dock left its 50 ms `_poll_model`
    timer running against a widget Qt had scheduled for deletion, and the
    next tick raised "Internal C++ object already deleted". Red Percent
    surfaces it fastest because its readonly fields are written by a live
    background thread.

    Two things changed underneath it and both are asserted here. The view no
    longer fabricates a model when the manager has none (RC-1 item 5), so the
    device has to be registered rather than conjured by ticking a box; and
    D-1 made closing a *hide*, so the model survives the dock.
    """
    from model.system_manager import SystemManager
    from model.redpercent_system import RedPercentSystem
    from PySide6.QtCore import Qt

    mgr = SystemManager()
    model = RedPercentSystem()
    mgr.register("Red Percent Window", model)

    dash = DashboardWindow(mgr)
    qtbot.addWidget(dash)
    dash.show()

    item = next(
        dash.device_list.item(i)
        for i in range(dash.device_list.count())
        if dash.device_list.item(i).text() == "Red Percent Window"
    )

    item.setCheckState(Qt.Checked)
    qtbot.waitUntil(lambda: "Red Percent Window" in dash.active_docks, timeout=1000)

    widget = dash.active_docks["Red Percent Window"].widget()
    assert widget.timer.isActive()

    item.setCheckState(Qt.Unchecked)
    qtbot.waitUntil(lambda: "Red Percent Window" not in dash.active_docks, timeout=1000)

    assert not widget.timer.isActive()

    # D-1: the dock is gone, the device is not. Its model is still the
    # manager's, marked hidden, so re-ticking the box shows the same one.
    assert mgr.get_model("Red Percent Window") is model
    assert mgr.is_hidden("Red Percent Window")

    dash.close()


def test_dashboard_dock_focus_loss(qtbot):
    from model.system_manager import SystemManager
    from PySide6 import QtCore
    from model.probes import StepperProbe
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QWindowStateChangeEvent

    mgr = SystemManager()
    probe = StepperProbe("COM1", "None")
    mgr.register("Stepper Probe", probe)
    
    dash = DashboardWindow(mgr)
    qtbot.addWidget(dash)
    dash.show()
    
    dock = dash.active_docks["Stepper Probe"]
    item = dash.device_list.item(0)
    
    assert dash.active_docks["Stepper Probe"].isVisible()
    assert item.checkState() == Qt.Checked
    
    # Simulate dock hiding due to alt-tab / visibility loss (not explicit closeEvent)
    dock.hide()
    
    # Checkbox should REMAIN checked
    assert item.checkState() == Qt.Checked
    
    dash.close()

def test_negative_numeric_entry_not_blocked_by_validator(qtbot):
    class MockModel(SchemaCommands):
        def __init__(self):
            self.target_deg = "0"
            
        @property
        def ui_schema(self):
            return {
                "sections": [
                    {
                        "title": "Config",
                        "elements": [
                            {"type": "entry", "text": "Target (deg):", "model_attr": "target_deg"}
                        ]
                    }
                ]
            }

    model = MockModel()
    view = QtDynamicView(model)
    qtbot.addWidget(view)
    
    target_input = view.vars["target_deg"]
    target_input.setText("-45")
    target_input.editingFinished.emit()
    assert model.target_deg == "-45"
    
    view.cleanup()

def test_dropdown_binding_and_refresh(qtbot):
    class MockModel(SchemaCommands):
        def __init__(self):
            self.selected_port = "COM1"
            self.ports = ["COM1", "COM2"]
            self.cmd_called = False

        @property
        def ui_schema(self):
            return {
                "sections": [{
                    "title": "Ports",
                    "elements": [{
                        "type": "dropdown",
                        "text": "Port:",
                        "model_attr": "selected_port",
                        "options_command": "get_ports",
                        "command": "port_changed"
                    }]
                }]
            }

        def get_ports(self):
            return self.ports
            
        def port_changed(self, text):
            self.cmd_called = True
            self.selected_port = text

    model = MockModel()
    view = QtDynamicView(model)
    qtbot.addWidget(view)
    
    dropdown = view.vars["selected_port"]
    assert dropdown.currentText() == "COM1"
    
    # Change selection
    dropdown.setCurrentText("COM2")
    assert model.selected_port == "COM2"
    assert model.cmd_called is True



from unittest.mock import patch

def test_command_execution_failures(qtbot):
    """A command that cannot run is reported, never silently dropped.

    Re-authored in S10. The old version asserted that a command name with no
    method behind it "should safely ignore without crashing" — the view
    looked up `getattr(model, name, None)` and returned when it found
    nothing, so a schema typo produced a button that did nothing at all, with
    no message anywhere. `execute_command` raises for an unresolvable name
    now and the renderer shows it, which is what makes
    `tests/ui/test_schema_v2.py`'s conformance checks enforceable rather than
    advisory.
    """
    class BadModel(SchemaCommands):
        @property
        def ui_schema(self):
            return {
                "sections": [{
                    "title": "Failures",
                    "elements": [
                        {"type": "button", "text": "Missing", "command": "does_not_exist"},
                        {"type": "button", "text": "Throwing", "command": "throws_error"}
                    ]
                }]
            }

        def throws_error(self):
            raise ValueError("Test error")

    model = BadModel()
    view = QtDynamicView(model)
    qtbot.addWidget(view)

    # A name nothing resolves to still *raises*, and deliberately: S11 made
    # a failing command return `Failed`, but an unresolvable command name is
    # a schema bug in this repository rather than anything the operator can
    # act on, so it stays loud and I-7.2 catches it before a build ships.
    with pytest.raises(AttributeError, match="does_not_exist"):
        view._execute_command("does_not_exist")

    # A command that raises: no longer a modal opened by the view. S11 has
    # `execute_command` catch it, publish to the bus and return `Failed`, so
    # the report reaches the operator through the event log and one
    # acknowledged dialog raised by the subscriber. The modal this assertion
    # used to check is one of the two that hung this suite.
    from error_routing import ErrorRouter

    seen = []
    ErrorRouter.subscribe(seen.append)
    try:
        result = view._execute_command("throws_error")
    finally:
        ErrorRouter.unsubscribe(seen.append)

    assert result.failed
    assert "Test error" in result.reason
    assert isinstance(result.exception, ValueError)
    assert len(seen) == 1
    assert seen[0].severity == "error"
    assert seen[0].requires_ack, "a crash must be acknowledged, not just logged"
    assert "Test error" in seen[0].message


def test_numeric_field_validation(qtbot):
    """The validator follows the schema's declared `value_type`.

    Re-authored in S10. The old version set `x_dist = "0.0"` and commented
    that it was "mock[ing] is_numeric checking in view" — it was exercising
    the inference RC-6 item 2 deleted, where the renderer called `float()` on
    a field's *current contents* and treated the exception as "this is text".
    A cleared box was reclassified and silently lost its validator for the
    rest of the session. The type is declared now, so an empty field is still
    a numeric field.
    """
    from model.params import Param
    from model import schema as sch
    from PySide6.QtGui import QDoubleValidator

    class NumericModel(SchemaCommands):
        PARAMS = {
            "x_dist": Param("x_dist", "float", 0.0, minimum=-50, maximum=50),
            "label": Param("label", "text", ""),
        }

        def __init__(self):
            self.x_dist = 0.0
            self.label = ""

        @property
        def ui_schema(self):
            return sch.schema(sch.section(
                "Numbers",
                sch.entry("X:", "x_dist", self.PARAMS["x_dist"]),
                sch.entry("Label:", "label", self.PARAMS["label"]),
            ))

    model = NumericModel()
    view = QtDynamicView(model)
    qtbot.addWidget(view)

    validator = view.vars["x_dist"].validator()
    assert isinstance(validator, QDoubleValidator)
    assert validator.bottom() == -50
    assert validator.top() == 50

    # A text field declares itself text and gets no numeric validator.
    assert view.vars["label"].validator() is None

    # Clearing the numeric field does not reclassify it: this is the exact
    # state the old float()-the-contents inference got wrong.
    view.vars["x_dist"].setText("")
    view._poll_model()
    assert isinstance(view.vars["x_dist"].validator(), QDoubleValidator)

    view.cleanup()


def test_error_popup_manager_signal_routing(qtbot):
    """An event published off the GUI thread is marshalled onto it.

    Re-authored in S11 for the same reason as its twin in
    `tests/core/test_view_round1.py`: a modal is now reserved for an error
    that sets `requires_ack`. Published from a **real** background thread
    here, which is the case the Qt signal exists for and which the original
    only simulated.
    """
    import threading
    from error_routing import ErrorRouter
    from views.pyside.view import QtErrorPopupManager

    manager = QtErrorPopupManager.initialize()

    with patch('PySide6.QtWidgets.QMessageBox.critical') as mock_critical:
        t = threading.Thread(target=ErrorRouter.report_error,
                             args=("Test Title", "Test Message"),
                             kwargs={"source": "poller", "requires_ack": True})
        t.start()
        t.join(timeout=2.0)
        qtbot.wait(100)

        mock_critical.assert_called_once()
        args = mock_critical.call_args[0]
        assert "Test Title" in args[1]
        assert "Test Message" in args[2]

def test_selection_overlay_mouse_drag(qtbot):
    """A drag reports its rectangle to the composite; it never writes it.

    Re-authored in S10. The overlay used to be handed the model, assign
    `model.focus_area` itself and pop a modal confirming the drag — so the
    declared `set_focus_area` command never ran in this renderer, and the
    modal blocked the suite. It reports to a callback now, the way the
    `region_select` composite expects.
    """
    from views.pyside.view import SelectionOverlay
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import Qt, QPointF

    captured = []
    overlay = SelectionOverlay(lambda x, y, w, h: captured.append((x, y, w, h)))
    qtbot.addWidget(overlay)

    def drag(overlay, points):
        types = (QMouseEvent.Type.MouseButtonPress,
                 QMouseEvent.Type.MouseMove,
                 QMouseEvent.Type.MouseButtonRelease)
        handlers = (overlay.mousePressEvent, overlay.mouseMoveEvent,
                    overlay.mouseReleaseEvent)
        for kind, handler, (x, y) in zip(types, handlers, points):
            handler(QMouseEvent(
                kind, QPointF(x, y), QPointF(x, y),
                Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier))

    drag(overlay, [(100, 100), (250, 300), (250, 300)])

    assert overlay.start_pos_global.x() == 100
    assert captured == [(100, 100, 150, 200)]

    # A stray click is not a region, and nothing is reported for one.
    captured.clear()
    overlay2 = SelectionOverlay(lambda x, y, w, h: captured.append((x, y, w, h)))
    qtbot.addWidget(overlay2)
    drag(overlay2, [(10, 10), (14, 14), (14, 14)])
    assert captured == []


def test_the_view_does_not_wire_itself_into_the_input_poller(qtbot):
    """Re-authored in S10. It used to call
    `model.poller.activity_callback()` — a callback the *view* installed by
    calling `poller.start_polling(adapter, log_updater, activity_callback)`
    from its own constructor. S5 moved that wiring into the model's input
    service (RC-4): the view never starts, stops or subscribes to the poller,
    so a hidden device keeps its input alive and two open views cannot fight
    over one gamepad.

    The idle-timeout behavior this used to reach is owned by the model and
    tested there; what is view-side is the absence of the wiring.
    """
    class MockPoller:
        def __init__(self):
            self.started = False
            self.activity_callback = None

        def get_mapped_state(self):
            return {}

        def start_polling(self, *args, **kwargs):
            self.started = True

        def stop_polling(self):
            pass

        def close(self):
            pass

    class MockModelWithPoller(SchemaCommands):
        def __init__(self):
            self.poller = MockPoller()
            self.touch_called = False

        @property
        def ui_schema(self):
            return {"sections": []}

        def touch_activity(self):
            self.touch_called = True

    model = MockModelWithPoller()
    view = QtDynamicView(model)
    qtbot.addWidget(view)

    assert model.poller.started is False
    assert model.poller.activity_callback is None

    view._poll_model()
    assert model.touch_called is False, (
        "a render tick is not operator activity; treating it as such is what "
        "kept the idle interlock from ever firing")

    view.cleanup()


def test_pyside_dashboard_full_stop_wiring(qtbot):
    """Verify that clicking the PySide FULL STOP button calls system_manager.full_stop_all()."""
    from unittest.mock import MagicMock
    from views.pyside.view import DashboardWindow
    from model.system_manager import SystemManager
    from PySide6 import QtCore

    mgr = SystemManager()
    mgr.full_stop_all = MagicMock()
    
    dash = DashboardWindow(mgr)
    qtbot.addWidget(dash)
    
    # Simulate button click using qtbot
    qtbot.mouseClick(dash.stop_btn, QtCore.Qt.LeftButton)
    
    # Verify the manager method was called
    mgr.full_stop_all.assert_called_once()
