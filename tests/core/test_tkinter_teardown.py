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


def test_tk_hide_is_reversible(dual_shutdown_model):
    """S6 item 3: Tk's close used to be a one-way removal.

    `ttk.Notebook.forget()` deregisters the tab and ttk keeps no way to bring
    it back, so the device became unreachable for the session while its model
    went on running — the "one-way hide that leaks" this stage had to fix.
    `hide()` keeps the tab registered, so `add()` on the same frame restores
    it, and the Devices menu is the gesture that does it.
    """
    manager = SystemManager()
    manager.register("TestModel", dual_shutdown_model)
    dash = DashboardWindow(MagicMock(), manager)
    dash.notebook = MagicMock()

    dash.hide_device("TestModel")
    assert manager.is_hidden("TestModel") is True
    dash.notebook.hide.assert_called_once()
    dash.notebook.forget.assert_not_called(), "forget() cannot be undone"

    dash.show_device("TestModel")
    assert manager.is_hidden("TestModel") is False
    dash.notebook.add.assert_called_once()


def test_tk_hiding_a_device_does_not_tear_it_down(dual_shutdown_model):
    """D-1. The model, the port and the controller binding all persist."""
    manager = SystemManager()
    manager.register("TestModel", dual_shutdown_model)
    dash = DashboardWindow(MagicMock(), manager)
    dash.notebook = MagicMock()

    dash.hide_device("TestModel")

    assert manager.get_model("TestModel") is dual_shutdown_model
    assert dual_shutdown_model.power_down_calls == 0
