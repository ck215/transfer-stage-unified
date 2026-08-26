import sys
import os
import pytest
from PySide6.QtWidgets import QApplication

# Add mvc-refactor/src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'src')))

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
    assert not dock.isVisible()
    
    # User clicks sidebar item to restore dock
    item = dash.device_list.item(0)
    dash.on_device_clicked(item)
    assert dock.isVisible()
    
    dash.close()
