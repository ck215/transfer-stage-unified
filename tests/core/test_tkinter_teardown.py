import pytest
from unittest.mock import MagicMock
from model.system_manager import SystemManager
from views.tkinter.view import DashboardWindow

def test_dashboard_window_teardown_ordering(dual_shutdown_model):
    """
    Test that when DashboardWindow is closed, it uses SystemManager.shutdown_all()
    which correctly calls power_down() before disable(), and doesn't call disable()
    if power_down() is present.
    """
    manager = SystemManager()
    manager.register_model("TestModel", dual_shutdown_model)
    
    parent = MagicMock()
    
    dash = DashboardWindow(parent, manager)
    
    dash.on_close()
    
    assert dual_shutdown_model.power_down_calls == 1
    assert dual_shutdown_model.disable_calls == 0
