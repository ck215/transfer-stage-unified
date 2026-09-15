import sys
import os
import pytest
from PySide6.QtWidgets import QApplication

# Add mvc-refactor/src to path

from view_pyside import PlotDialog, ControllerLogWindow, RedPercentDynamicView, QtDynamicView, DashboardWindow

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
    class MockModel:
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


def test_live_polling_loops(qtbot):
    class PollingModel:
        def __init__(self):
            self.pos_count = 0
            self.stat_count = 0
            
        @property
        def ui_schema(self):
            return {"sections": []}
            
        def read_position(self):
            self.pos_count += 1
            
        def poll_status(self):
            self.stat_count += 1

    model = PollingModel()
    view = QtDynamicView(model)
    qtbot.addWidget(view)
    
    assert hasattr(view, 'pos_timer') and view.pos_timer.isActive()
    assert hasattr(view, 'status_timer') and view.status_timer.isActive()
    
    # Trigger timers
    view._safe_read_position()
    view._safe_poll_status()
    
    assert model.pos_count >= 1
    assert model.stat_count >= 1
    
    view.cleanup()
    assert not view.pos_timer.isActive()
    assert not view.status_timer.isActive()


def test_controller_log_window_lifecycle(qtbot):
    class MockPoller:
        def __init__(self):
            self.log_updater = None
        def get_mapped_state(self):
            return {}
        def start_polling(self, *args, **kwargs): pass
        def stop_polling(self): pass
        def close(self): pass

    class ModelWithPoller:
        def __init__(self):
            self.poller = MockPoller()
        @property
        def ui_schema(self):
            return {"sections": []}

    model = ModelWithPoller()
    view = QtDynamicView(model)
    qtbot.addWidget(view)
    
    # Open log window
    view._execute_command("open_controller_log")
    assert view.log_window is not None
    view.log_window.append_log("Test Log Entry")
    assert "Test Log Entry" in view.log_window.text_edit.toPlainText()
    
    # Close log window
    view.log_window.close()
    
    # Reopen log window (must not raise RuntimeError from WA_DeleteOnClose)
    view._execute_command("open_controller_log")
    assert view.log_window is not None
    view.log_window.close()
    view.cleanup()


def test_redpercent_sync_dimensions(qtbot):
    from model.redpercent_system import RedPercentSystem
    model = RedPercentSystem()
    view = RedPercentDynamicView(model)
    qtbot.addWidget(view)
    
    assert hasattr(view, 'sync_cbs')
    assert 'X' in view.sync_cbs
    assert 'Y' in view.sync_cbs
    assert 'Z' in view.sync_cbs
    
    view.sync_cbs['X'].setChecked(True)
    view.sync_cbs['Y'].setChecked(True)
    assert model.sync_dimensions == ['X', 'Y']
    
    view.sync_cbs['X'].setChecked(False)
    assert model.sync_dimensions == ['Y']
    
    view.cleanup()


def test_dashboard_dock_lifecycle(qtbot):
    from model.system_manager import SystemManager
    from model.probes import StepperProbe
    from PySide6.QtCore import Qt

    mgr = SystemManager()
    probe = StepperProbe("COM1", "None")
    mgr.register_model("Stepper Probe", probe)
    
    dash = DashboardWindow(mgr)
    qtbot.addWidget(dash)
    dash.show()
    
    dock = dash.active_docks["Stepper Probe"]
    assert dock.isVisible()
    
    # User closes dock
    dock.close()
    
    # Wait for the dock visibility to update and the checkbox to sync
    qtbot.waitUntil(lambda: not dock.isVisible(), timeout=1000)
    item = dash.device_list.item(0)
    assert item.checkState() == Qt.Unchecked
    
    # User checks the checkbox to restore the dock
    item.setCheckState(Qt.Checked)
    
    qtbot.waitUntil(lambda: dock.isVisible(), timeout=1000)
    assert dock.isVisible()
    
    # User unchecks the checkbox to hide the dock
    item.setCheckState(Qt.Unchecked)
    qtbot.waitUntil(lambda: not dock.isVisible(), timeout=1000)
    assert not dock.isVisible()
    
    dash.close()

def test_dashboard_dock_focus_loss(qtbot):
    from model.system_manager import SystemManager
    from model.probes import StepperProbe
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QWindowStateChangeEvent

    mgr = SystemManager()
    probe = StepperProbe("COM1", "None")
    mgr.register_model("Stepper Probe", probe)
    
    dash = DashboardWindow(mgr)
    qtbot.addWidget(dash)
    dash.show()
    
    dock = dash.active_docks["Stepper Probe"]
    item = dash.device_list.item(0)
    
    assert dock.isVisible()
    assert item.checkState() == Qt.Checked
    
    # Simulate dock hiding due to alt-tab / visibility loss (not explicit closeEvent)
    dock.hide()
    
    # Checkbox should REMAIN checked
    assert item.checkState() == Qt.Checked
    
    dash.close()

def test_negative_numeric_entry_not_blocked_by_validator(qtbot):
    class MockModel:
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
    class MockModel:
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
    class BadModel:
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
    
    # Trigger missing command (should safely ignore without crashing)
    view._execute_command("does_not_exist")
    
    # Trigger throwing command (should catch and show message box)
    with patch('PySide6.QtWidgets.QMessageBox.critical') as mock_critical:
        view._execute_command("throws_error")
        mock_critical.assert_called_once()
        args = mock_critical.call_args[0]
        assert "Test error" in args[2]


def test_numeric_field_validation(qtbot):
    class NumericModel:
        def __init__(self):
            self.x_dist = "0"
            
        @property
        def ui_schema(self):
            return {
                "sections": [{
                    "title": "Numbers",
                    "elements": [
                        {"type": "entry", "text": "X:", "model_attr": "x_dist"}
                    ]
                }]
            }
            
    model = NumericModel()
    # Mock is_numeric checking in view to treat x_dist as numeric
    model.x_dist = "0.0" 
    
    view = QtDynamicView(model)
    qtbot.addWidget(view)
    
    entry = view.vars["x_dist"]
    # It should have a QDoubleValidator attached
    from PySide6.QtGui import QDoubleValidator
    assert isinstance(entry.validator(), QDoubleValidator)

def test_error_popup_manager_signal_routing(qtbot):
    from view_pyside import QtErrorPopupManager
    manager = QtErrorPopupManager.initialize()
    
    with patch('PySide6.QtWidgets.QMessageBox.critical') as mock_critical:
        # Trigger an error using the global router (simulating a background thread error)
        from error_routing import ErrorRouter
        ErrorRouter.report_error("Test Title", "Test Message")
        
        # Give Qt event loop time to process the signal
        import time; time.sleep(0.1)
        qtbot.wait(100)
        
        mock_critical.assert_called_once()
        args = mock_critical.call_args[0]
        assert "Test Title" in args[1]
        assert "Test Message" in args[2]

def test_selection_overlay_mouse_drag(qtbot):
    from view_pyside import SelectionOverlay
    from PySide6.QtGui import QMouseEvent, QScreen
    from PySide6.QtCore import Qt, QPoint, QPointF
    
    class MockModel:
        def __init__(self):
            self.focus_area = None
            
    model = MockModel()
    overlay = SelectionOverlay(model)
    qtbot.addWidget(overlay)
    
    # Simulate a drag
    # Mouse Press
    press_event = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        QPointF(100, 100), QPointF(100, 100),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier
    )
    overlay.mousePressEvent(press_event)
    assert overlay.start_pos_global.x() == 100
    
    # Mouse Move
    move_event = QMouseEvent(
        QMouseEvent.Type.MouseMove,
        QPointF(250, 300), QPointF(250, 300),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier
    )
    overlay.mouseMoveEvent(move_event)
    
    # Mouse Release
    release_event = QMouseEvent(
        QMouseEvent.Type.MouseButtonRelease,
        QPointF(250, 300), QPointF(250, 300),
        Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier
    )
    overlay.mouseReleaseEvent(release_event)
    
    assert model.focus_area is not None
    assert model.focus_area['left'] == 100
    assert model.focus_area['top'] == 100
    assert model.focus_area['width'] == 150
    assert model.focus_area['height'] == 200

def test_dynamic_view_inactivity_timer_expiration(qtbot):
    class MockPoller:
        def __init__(self):
            self.activity_callback = None
        def get_mapped_state(self):
            return {}
        def start_polling(self, adapter, log_updater, activity_callback):
            self.activity_callback = activity_callback
        def stop_polling(self): pass
        def close(self): pass
        
    class MockModelWithPoller:
        def __init__(self):
            self.poller = MockPoller()
            self.system_enabled = True
            self.disable_called = False
            self.is_stepping = False
            self.manual_flag = False
        @property
        def ui_schema(self): return {"sections": []}
        def disable(self):
            self.disable_called = True
            self.system_enabled = False
            
    model = MockModelWithPoller()
    view = QtDynamicView(model)
    qtbot.addWidget(view)
    
    # Fake gamepad activity to start the timer
    model.poller.activity_callback()
    
    assert hasattr(view, 'disable_timer')
    assert view.disable_timer is not None
    assert view.disable_timer.isActive()
    
    # Manually trigger timeout
    view.disable_timer.timeout.emit()
    
    # Assert model was disabled
    assert model.disable_called is True

