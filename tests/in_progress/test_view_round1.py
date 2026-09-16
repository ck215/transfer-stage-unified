import sys
import os
import pytest
from unittest.mock import MagicMock, patch

from view_pyside import (
    QtDynamicView, RedPercentDynamicView, QtErrorPopupManager, 
    ControllerLogWindow, PlotDialog, SelectionOverlay, DashboardWindow
)
from model.system_manager import SystemManager


class DummyPySideModel:
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
    assert "selected_port" in view.vars
    assert len(view.toggle_buttons) == 1

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
    """Test RedPercentDynamicView checkbox toggles and probe dropdown selection."""
    from model.redpercent_system import RedPercentSystem
    model = RedPercentSystem()
    model.available_probes = {"Stepper Probe": MagicMock()}
    
    view = RedPercentDynamicView(model)
    qtbot.addWidget(view)

    # Test sync checkboxes
    assert 'X' in view.sync_cbs
    view.sync_cbs['X'].setChecked(True)
    view.sync_cbs['Z'].setChecked(True)
    assert model.sync_dimensions == ['X', 'Z']

    # Test probe selection dropdown
    assert view.probe_combo.currentText() == "Stepper Probe"
    view.cleanup()


def test_pyside_dashboard_sidebar_dock_sync(qtbot):
    """Test PySide DashboardWindow dock creation and sidebar check state synchronization."""
    from model.probes import StepperProbe
    mgr = SystemManager()
    probe = StepperProbe("COM1", "None")
    mgr.register_model("Stepper Probe", probe)

    dash = DashboardWindow(mgr)
    qtbot.addWidget(dash)
    dash.show()

    assert "Stepper Probe" in dash.active_docks
    dock = dash.active_docks["Stepper Probe"]
    assert dock.isVisible()

    # Close dock and verify list item unchecked
    dock.close()
    qtbot.waitUntil(lambda: not dock.isVisible(), timeout=1000)
    item = dash.device_list.item(0)
    from PySide6.QtCore import Qt
    assert item.checkState() == Qt.Unchecked

    dash.close()


# ---------------------------------------------------------------------------
# Legacy Tkinter View Tests
# ---------------------------------------------------------------------------

def test_legacy_error_popup_manager_queue():
    """Test legacy ErrorPopupManager thread-safe queue buffering."""
    from view import ErrorPopupManager
    ErrorPopupManager._error_queue.queue.clear()
    
    ErrorPopupManager._root = None
    ErrorPopupManager.report_error("Legacy Title", "Legacy error message")
    
    assert not ErrorPopupManager._error_queue.empty()
    item = ErrorPopupManager._error_queue.get()
    assert item['type'] == 'error'
    assert item['title'] == "Legacy Title"
    assert item['message'] == "Legacy error message"


def test_legacy_dynamic_view_schema_parsing():
    """Test legacy Tkinter DynamicView schema parsing logic."""
    model = DummyPySideModel()
    
    with patch('tkinter.Frame.__init__', return_value=None), \
         patch('tkinter.Label'), patch('tkinter.Entry'), \
         patch('tkinter.Button'), patch('tkinter.StringVar'):
        from view import DynamicView
        
        view = DynamicView.__new__(DynamicView)
        view.model = model
        view.bg_main = 'black'
        view.fg_accent = 'white'
        view.vars = {}
        view.toggle_buttons = []
        
        view._build_ui()
        assert "step_size" in view.vars
        assert "status_msg" in view.vars
        assert "selected_port" in view.vars
        assert len(view.toggle_buttons) == 1
