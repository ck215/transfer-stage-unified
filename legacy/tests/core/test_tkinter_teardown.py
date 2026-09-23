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


# -- D-4: focus gating, and the child-dialog distinction --------------------

def _gated_dashboard(model):
    manager = SystemManager()
    manager.register("TestModel", model)
    return manager, DashboardWindow(MagicMock(), manager)


def test_d4_losing_focus_to_another_application_closes_the_gate(gate_model):
    manager, dash = _gated_dashboard(gate_model)
    dash.focus_get = lambda: None          # focus left this application
    dash._sync_input_gate()
    assert gate_model.gate_open is False


def test_d4_a_child_dialog_does_not_close_the_gate(gate_model):
    """The crux of D-4.

    Tk fires <FocusOut> on this window both when the operator switches to
    another application and when this application opens a dialog — a file
    picker, the rotation confirmation, an error box. `focus_get()` answers
    within this application, so a non-None result means focus is still ours.
    Treating the dialog case as focus loss is VIEW-TKINTER-9.
    """
    manager, dash = _gated_dashboard(gate_model)
    dash.focus_get = lambda: MagicMock()   # a dialog of ours holds focus
    dash._sync_input_gate()
    assert gate_model.gate_open is True


def test_d4_focus_loss_never_stops_the_hardware(gate_model):
    """Gate, do not stop. A move in flight continues."""
    manager, dash = _gated_dashboard(gate_model)
    dash.focus_get = lambda: None
    dash._sync_input_gate()
    assert gate_model.power_down_calls == 0
    assert gate_model.disable_calls == 0


def test_d4_an_event_from_a_child_widget_is_ignored(gate_model):
    """<FocusOut> bubbles from entry fields; only the window's own counts."""
    manager, dash = _gated_dashboard(gate_model)
    dash.focus_get = lambda: None
    event = MagicMock()
    event.widget = MagicMock()             # some entry, not the window
    dash._sync_input_gate(event)
    assert gate_model.gate_open is True, "a child widget's event must not gate"
