import sys
import os
import pytest
from PySide6.QtWidgets import QApplication

# Add mvc-refactor/src to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'mvc-refactor', 'src')))

from view_pyside import PlotDialog, ControllerLogWindow, RedPercentDynamicView, QtDynamicView

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
