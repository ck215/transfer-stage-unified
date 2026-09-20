import sys
import os
import pytest
from unittest.mock import MagicMock, patch

from views.pyside.view import (
    QtDynamicView, RedPercentDynamicView, QtErrorPopupManager, 
    ControllerLogWindow, PlotDialog, SelectionOverlay, DashboardWindow
)
from model.system_manager import SystemManager
from model.base import SchemaCommands


class DummyPySideModel(SchemaCommands):
    """Mock model providing a complete ui_schema for testing PySide component loading."""
    def __init__(self):
        self.step_size = "10"
        self.status_msg = "Ready"
        self.enabled = False
        self.selected_port = "COM1"
        self.script_path = ""
        self.cmd_executed = False

    @property
    def ui_schema(self):
        return {
            "sections": [
                {
                    "title": "Control Section",
                    "elements": [
                        {"type": "entry", "text": "Step Size:", "model_attr": "step_size"},
                        {"type": "readonly", "text": "Status:", "model_attr": "status_msg"},
                        {"type": "button", "text": "Run Command", "command": "run_cmd"},
                        {"type": "toggle", "text": "Enable", "model_attr": "enabled", "true_text": "ON", "false_text": "OFF", "command": "toggle_enabled"},
                        {"type": "dropdown", "text": "Port:", "model_attr": "selected_port", "options_command": "get_ports", "command": "port_changed"},
                        {"type": "file_picker", "text": "Select Script", "command": "load_script"}
                    ]
                }
            ]
        }

    def run_cmd(self):
        self.cmd_executed = True

    def toggle_enabled(self):
        self.enabled = not self.enabled

    def get_ports(self):
        return ["COM1", "COM2", "COM3"]

    def port_changed(self, text):
        self.selected_port = text

    def load_script(self, path):
        self.script_path = path


def test_pyside_ui_schema_component_loading(qtbot):
    """Test full schema rendering and widget instantiation in PySide QtDynamicView."""
    model = DummyPySideModel()
    view = QtDynamicView(model)
    qtbot.addWidget(view)

    # Verify dictionary of bound variables
    assert "step_size" in view.vars
    assert "status_msg" in view.vars

    # Verify entry widget value
    entry = view.vars["step_size"]
    assert entry.text() == "10"

    # Verify readonly widget value
    lbl = view.vars["status_msg"]
    assert lbl.text() == "Ready"

    view.cleanup()


def test_pyside_event_callbacks_and_two_way_binding(qtbot):
    """Test user interactions updating model and model updates reflecting in PySide UI."""
    model = DummyPySideModel()
    view = QtDynamicView(model)
    qtbot.addWidget(view)

    # 1. UI -> Model edit
    entry = view.vars["step_size"]
    entry.setText("25")
    entry.editingFinished.emit()
    assert model.step_size == "25"

    # 2. Model -> UI polling update
    model.status_msg = "Processing"
    view._poll_model()
    assert view.vars["status_msg"].text() == "Processing"

    # 3. Button execution
    view._execute_command("run_cmd")
    assert model.cmd_executed is True

    # 4. Toggle button state change
    assert model.enabled is False
    view._execute_command("toggle_enabled")
    assert model.enabled is True
    view._poll_model()
    assert view.toggle_buttons[0]["widget"].text() == "ON"

    view.cleanup()


def test_pyside_error_popup_manager_signal(qtbot):
    """Test PySide thread-safe error reporting via Qt signals."""
    manager = QtErrorPopupManager.initialize()

    with patch('PySide6.QtWidgets.QMessageBox.critical') as mock_critical:
        from error_routing import ErrorRouter
        ErrorRouter.report_error("Thread Error", "Fatal exception occurred")
        
        qtbot.wait(100)
        mock_critical.assert_called_once()
        args = mock_critical.call_args[0]
        assert "Thread Error" in args[1]
        assert "Fatal exception occurred" in args[2]


def test_pyside_redpercent_sync_and_probe_controls(qtbot):
    """Sync toggles and the position-source dropdown, driven from the schema.

    Re-authored in S10. It used to assert `view.sync_cbs` and
    `view.probe_combo` — a hand-built QCheckBox row and QComboBox that
    duplicated what the schema already declares, existed in this frontend
    only, and were deleted with D-6. The behavior they guarded is unchanged;
    it is reached through the rendered widgets now, so what this asserts is
    the contract all three renderers share rather than one view's furniture.
    """
    from model.redpercent_system import RedPercentSystem
    model = RedPercentSystem()
    mock_probe = MagicMock()
    mock_probe._disabled_in_setup = False  # a bare MagicMock() would otherwise
    # fabricate this attribute as a truthy child mock instead of returning the
    # getattr(..., False) default, incorrectly filtering this probe out of
    # get_available_probe_names() as "disabled".
    model.available_probes = {"Stepper Probe": mock_probe}

    view = RedPercentDynamicView(model)
    qtbot.addWidget(view)

    # One schema toggle per dimension, rendered as a command button.
    toggles = {tb["attr"]: tb["widget"] for tb in view.toggle_buttons}
    assert set(toggles) == {"sync_x", "sync_y", "sync_z"}

    toggles["sync_x"].click()
    toggles["sync_z"].click()
    assert model.sync_dimensions == ["X", "Z"]

    # Clicking again removes the dimension. The toggle issues a command and
    # never writes the attribute, so the model stays the list's only owner.
    toggles["sync_x"].click()
    assert model.sync_dimensions == ["Z"]

    # The caption follows the model only on a poll, as it does in the app.
    view._poll_model()
    assert toggles["sync_z"].text() == "Sync Z: ON"
    assert toggles["sync_x"].text() == "Sync X: OFF"

    # Position source: the schema's dropdown, filled by its options_command.
    combo = view.vars["selected_probe_name"]
    assert combo.currentText() == "Stepper Probe"
    view.cleanup()


def test_pyside_dashboard_sidebar_dock_sync(qtbot):
    """Test PySide DashboardWindow dock creation and sidebar check state synchronization."""
    from model.probes import StepperProbe
    mgr = SystemManager()
    probe = StepperProbe("COM1", "None")
    mgr.register("Stepper Probe", probe)

    dash = DashboardWindow(mgr)
    qtbot.addWidget(dash)
    dash.show()

    assert "Stepper Probe" in dash.active_docks
    dock = dash.active_docks["Stepper Probe"]
    assert dock.isVisible()

    # Close dock and verify list item unchecked
    dock.close()
    qtbot.waitUntil(lambda: not dash.active_docks.get("Stepper Probe", dock).isVisible(), timeout=1000)
    item = dash.device_list.item(0)
    from PySide6.QtCore import Qt
    assert item.checkState() == Qt.Unchecked

    dash.close()


def test_closing_a_view_does_not_close_the_models_poller(qtbot):
    """D-1: a hide must leave the device able to work when it comes back.

    `QtDynamicView.cleanup()` used to call `poller.stop_polling()` and
    `poller.close()`. Closing a dock is a *hide*, and `ControllerPoller.close`
    is terminal — `_closed` is never cleared — so hiding a device and showing
    it again produced a model whose manual mode could never arm, silently,
    for the rest of the session. Ending the device belongs to `teardown()`,
    reached through the manager.
    """
    class FakePoller:
        def __init__(self):
            self.stopped = False
            self.closed = False

        def get_mapped_state(self):
            return {}

        def stop_polling(self):
            self.stopped = True

        def close(self):
            self.closed = True

    class ModelWithPoller(SchemaCommands):
        def __init__(self):
            self.poller = FakePoller()

        @property
        def ui_schema(self):
            return {"sections": []}

    model = ModelWithPoller()
    view = QtDynamicView(model)
    qtbot.addWidget(view)

    view.cleanup()

    assert not view.timer.isActive()
    assert model.poller.stopped is False
    assert model.poller.closed is False


# ---------------------------------------------------------------------------
# Legacy Tkinter View Tests
# ---------------------------------------------------------------------------

def test_legacy_error_popup_manager_queue():
    """Test legacy ErrorPopupManager thread-safe queue buffering."""
    from views.tkinter.view import ErrorPopupManager
    ErrorPopupManager._error_queue.queue.clear()
    
    ErrorPopupManager._root = None
    ErrorPopupManager.report_error("Legacy Title", "Legacy error message")
    
    assert not ErrorPopupManager._error_queue.empty()
    item = ErrorPopupManager._error_queue.get()
    assert item['type'] == 'error'
    assert item['title'] == "Legacy Title"
    assert item['message'] == "Legacy error message"


def test_legacy_dynamic_view_schema_parsing():
    """Test legacy Tkinter DynamicView schema parsing logic using conftest mocks."""
    import tkinter as tk
    from views.tkinter.view import DynamicView
    from unittest.mock import patch
        
    model = DummyPySideModel()
        
    # Let conftest handle the tk patches, we just instantiate the view
    # Tkinter views are typically placed inside a Frame/Toplevel
    parent = tk.Frame()
    
    with patch.object(tk.Frame, 'register', return_value='mock_vcmd', create=True):
        view = DynamicView(parent, model)
    
    # We do not need to call _build_ui manually because __init__ handles it
    assert "step_size" in view.vars
    assert "status_msg" in view.vars

