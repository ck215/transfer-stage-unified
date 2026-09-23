"""PYSIDE-16: layout-intent divergences from the Tk reference in
DashboardWindow.

All three checks need a real QMainWindow, so this file is qtbot-based and
qt-marked — not covered by the fast gate. The visual effects were never
run in this session either (the brief calls for conservative, reversible
changes); these tests check the concrete, checkable claims instead: widget
order, the absence of a space-claiming central widget, and the exact
bookkeeping bug in `_last_added_dock`.
"""
import pytest

from model.probes import StepperProbe, DCProbe
from model.system_manager import SystemManager


@pytest.fixture
def dashboard(qtbot):
    from views.pyside.view import DashboardWindow
    mgr = SystemManager()
    mgr.register("Stepper Probe", StepperProbe("COM1", "None"))
    mgr.register("DC Probe", DCProbe("COM2", "None"))
    dash = DashboardWindow(mgr)
    qtbot.addWidget(dash)
    yield dash


def test_full_stop_is_the_last_widget_in_the_sidebar(dashboard):
    """Tk bottom-docks FULL STOP specifically so it is not "sitting directly
    above" the control an operator is reaching for — here, the device
    checkboxes that hide/show and destroy models. Before the fix it was
    `sidebar_layout.addWidget`'s *first* call, directly above them.
    """
    sidebar_layout = dashboard.sidebar.widget().layout()
    widgets = [sidebar_layout.itemAt(i).widget()
               for i in range(sidebar_layout.count())]
    assert widgets[-1] is dashboard.stop_btn
    assert dashboard.device_list in widgets
    assert widgets.index(dashboard.device_list) < widgets.index(dashboard.stop_btn)


def test_full_stop_has_its_own_object_name_for_hover_and_pressed_styling(dashboard):
    assert dashboard.stop_btn.objectName() == "fullStopButton"


def test_no_central_widget_claims_a_stretch_share(dashboard):
    """`setCentralWidget(QWidget())` gave QMainWindow's layout a central
    pane with nothing in it, which still claims space and squeezes the
    docks around it. QMainWindow does not require a central widget at all.
    """
    assert dashboard.centralWidget() is None


def test_last_added_dock_falls_back_to_a_remaining_open_dock_on_close(dashboard):
    """Before the fix, closing whichever dock was `_last_added_dock` reset
    it to None outright, so the *next* dock opened took
    `addDockWidget(RightDockWidgetArea, ...)` in `open_device_view` instead
    of continuing the `splitDockWidget` horizontal chain against whatever
    dock was still open — a different stacking result depending on close
    order alone.
    """
    dashboard.open_device_view("Stepper Probe")
    dashboard.open_device_view("DC Probe")
    assert dashboard._last_added_dock is dashboard.active_docks["DC Probe"]

    dashboard.close_device_view("DC Probe")
    assert dashboard._last_added_dock is dashboard.active_docks["Stepper Probe"], (
        "closing the most-recently-added dock should fall back to the "
        "next most recent survivor, not None")


def test_last_added_dock_is_none_once_every_dock_is_closed(dashboard):
    """The fallback bottoms out at None -- but only when nothing is left.

    `DashboardWindow` auto-opens a dock for every device the manager already
    holds, so the fixture starts with *both* probes docked. Closing one only
    exercises the fallback; the None branch needs every dock closed.
    """
    assert set(dashboard.active_docks) == {"Stepper Probe", "DC Probe"}
    for name in list(dashboard.active_docks):
        dashboard.close_device_view(name)
    assert dashboard.active_docks == {}
    assert dashboard._last_added_dock is None
