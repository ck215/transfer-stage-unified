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
    manager.register("TestModel", dual_shutdown_model)
    
    parent = MagicMock()
    
    dash = DashboardWindow(parent, manager)
    
    dash.on_close()
    
    # shutdown_all() stops before it tears down (RC-1 item 4), so power_down
    # runs twice here: once as emergency_stop, once inside teardown. The point
    # the test guards is unchanged — disable() is never used as a stop.
    assert dual_shutdown_model.power_down_calls == 2
    assert dual_shutdown_model.disable_calls == 0
