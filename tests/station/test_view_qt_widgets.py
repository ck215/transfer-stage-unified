"""The Qt view's widget behaviour. **Every test here is `qt`-marked.**

A native Qt abort takes the whole pytest session down with it and discards
every already-passed result, so the agent that wrote these did not run them;
the lead runs the Qt pass. They use pytest-qt's `qapp` fixture, which
`tests/conftest.py` also keys its auto-marking and its skip-on-broken-Qt
probe off.

Nothing here opens a real modal: `QMessageBox.exec` is patched wherever one
could be raised, because there is nobody to click it.
"""
import functools
import os
import threading

import pytest

from PySide6.QtGui import QIntValidator
from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import (QFileDialog, QFrame, QLabel, QMessageBox,
                               QSizePolicy)

from station import schema as sch
from station.events import events
from station.panel import Panel
from station.param import Param
from station.result import Refused, Result
from station.views import qt, theme

pytestmark = pytest.mark.qt


# ---------------------------------------------------------------------------
# Fakes. A real `Panel` (so `run`, `_apply_inputs` and the allow-list are the
# real ones) behind a Controller stub that records what the view asked for.
# ---------------------------------------------------------------------------

class FakePanel(Panel):
    NAME = "Fake"
    PARAMS = {"speed": Param("speed", "float", default=1.0, minimum=0,
                             maximum=10, decimals=3, label="Speed")}

    def __init__(self):
        super().__init__()
        self.reading = "1.234"
        self.is_on = False
        self.is_faulted = False
        self.region = None
        self.choice = None
        self.refuse = False
        self.saved_path = None
        self.loaded_path = None
        self.commands = []

    @property
    def mode_name(self):
        return "running" if self.is_on else "idle"

    @property
    def schema(self):
        return sch.schema(sch.section(
            "Everything",
            sch.readonly("Reading:", "reading"),
            sch.entry("Speed:", "speed", self.PARAMS["speed"]),
            sch.button("Go", "go", inputs=("speed",), role="go"),
            sch.button("Idle only", "idle_only", enabled_when=("idle",)),
            sch.toggle("Power", "is_on", "set_power", "ON", "OFF",
                       on_args=(True,), off_args=(False,)),
            sch.dropdown("Pick:", "choice", "set_choice", "choices"),
            sch.region_select("Select region", "set_region",
                              model_attr="region"),
            sch.file_save("Save", "save_run"),
            sch.file_open("Load", "load_run"),
            sch.plot("Series", "series"),
            sch.image("Picture", "picture"),
            sch.indicator("Fault", "is_faulted"),
            sch.log_stream("Log", "log_lines"),
            sch.button("Invisible", "quiet"),
        ))

    def go(self):
        self.commands.append(("go", self.speed))
        if self.refuse:
            raise Refused("the stage is not homed")
        return "went"

    def idle_only(self):
        return None

    def set_power(self, is_on):
        self.is_on = bool(is_on)

    def set_choice(self, text):
        self.choice = text

    def choices(self):
        return ["a", "b"]

    def set_region(self, x, y, width, height):
        self.region = {"left": x, "top": y, "width": width, "height": height}

    def save_run(self):
        return self.saved_path

    def load_run(self, path):
        self.loaded_path = path

    def series(self):
        return {"y": [1, 5, 2]}

    def picture(self):
        return b""

    def log_lines(self):
        return ["first", "second"]

    def quiet(self):
        return None


class TablePanel(Panel):
    """A panel shaped like Setup after Addendum 2.

    A column section, then one `layout="row"` section per model type, then a
    column section. The rows deliberately declare *different* controls — the
    rotator takes no gamepad, the screen monitor takes no port at all — since
    a ragged set of rows is exactly what a table has to survive if "Port" is
    to stay under "Port". It also carries the one `int` parameter.
    """

    NAME = "Table"
    PARAMS = {"dwell": Param("dwell", "int", default=5, minimum=0, maximum=60,
                             label="Dwell")}

    #: (display name, attribute prefix, needs a port, needs a gamepad)
    ROWS = (("Stepper Probe", "stepper", True, True),
            ("Rotator", "rotator", True, False),
            ("Red Percent", "red", False, False))

    def __init__(self):
        super().__init__()
        self.scan_status = "Ready."
        self.is_stopped = False
        self.selected = []
        for _, key, _, _ in self.ROWS:
            setattr(self, f"{key}_port", "Off")
            setattr(self, f"{key}_gamepad", "None")
            setattr(self, f"{key}_found", "")
            for field in ("port", "gamepad"):
                setattr(self, f"set_{key}_{field}",
                        functools.partial(self._select, key, field))

    @property
    def schema(self):
        sections = [sch.section(
            "Hardware",
            sch.readonly("Status:", "scan_status"),
            sch.entry("Dwell (s):", "dwell", self.PARAMS["dwell"]),
            sch.button("Refresh", "refresh", role="info"),
        )]
        for name, key, needs_port, needs_gamepad in self.ROWS:
            elements = []
            if needs_port:
                elements.append(sch.dropdown("Port", f"{key}_port",
                                             f"set_{key}_port", "port_options"))
            if needs_gamepad:
                elements.append(sch.dropdown("Gamepad", f"{key}_gamepad",
                                             f"set_{key}_gamepad",
                                             "gamepad_options"))
            elements.append(sch.readonly("Detected:", f"{key}_found"))
            sections.append(sch.section(name, *elements, layout="row"))
        sections.append(sch.section(
            "Launch", sch.button("Launch", "launch", role="go")))
        # The shape `Model._safety_section` builds: a stop toggle that is a
        # danger role in BOTH states.
        sections.append(sch.section(
            "Safety",
            sch.toggle("FULL STOP", "is_stopped", "toggle_stop",
                       "LATCHED - click to clear", "FULL STOP",
                       on_role="danger", off_role="danger")))
        return sch.schema(*sections)

    def toggle_stop(self):
        self.is_stopped = not self.is_stopped
        return self.is_stopped

    def _select(self, key, field, choice):
        self.selected.append((key, field, choice))
        setattr(self, f"{key}_{field}", choice)
        return choice

    def port_options(self):
        return ["Off", "SIM", "COM3"]

    def gamepad_options(self):
        return ["None", "Pad 1"]

    def refresh(self):
        return True

    def launch(self):
        return True


class FakeController:
    def __init__(self, panel):
        self.panel = panel
        self.open_names = ["Fake"]
        self.closed = ["Gone"]
        self.removed, self.reopened, self.focus_calls = [], [], []
        self.is_estopped = False
        self.estop_calls = 0
        self.is_closed = False
        self._subscribers = []

    @property
    def model_names(self):
        return list(self.open_names)

    @property
    def closed_names(self):
        return list(self.closed)

    def schema(self, name):
        return self.panel.schema

    def state(self, name=None):
        snapshot = dict(self.panel.state)
        snapshot["age"] = 0.0
        return snapshot

    def run(self, name, command, inputs=None, args=()):
        return self.panel.run(command, inputs, args)

    def options(self, name, command):
        return self.panel.options(command)

    def subscribe(self, fn):
        self._subscribers.append(fn)

    def unsubscribe(self, fn):
        if fn in self._subscribers:
            self._subscribers.remove(fn)

    def set_input_focus(self, is_focused):
        self.focus_calls.append(is_focused)

    def estop_all(self):
        self.estop_calls += 1
        self.is_estopped = True
        return {"Fake": True}

    def clear_estop_all(self, confirmed=False):
        self.is_estopped = False
        return Result(Result.OK)

    def reopen(self, name):
        self.reopened.append(name)
        self.open_names.append(name)
        return None

    def remove(self, name):
        self.removed.append(name)
        if name in self.open_names:
            self.open_names.remove(name)
        return True

    def close(self):
        self.is_closed = True

    def notify(self, change, name):
        for fn in list(self._subscribers):
            fn(change, name)


@pytest.fixture
def panel():
    return FakePanel()


@pytest.fixture
def controller(panel):
    return FakeController(panel)


@pytest.fixture
def view(qapp, controller):
    built = qt.QtPanelView(controller, "Fake")
    yield built
    built.close()


@pytest.fixture
def dashboard(qapp, controller, panel):
    window = qt.QtDashboard(controller, panel)
    yield window
    if not window._closing:
        window.close()
    events.unsubscribe(window._on_event)


@pytest.fixture
def table_panel():
    return TablePanel()


@pytest.fixture
def table_view(qapp, table_panel):
    built = qt.QtPanelView(FakeController(table_panel), "Table")
    yield built
    built.close()


def element_of(view, kind):
    return next(e for e in view._elements if e["type"] == kind)


def element_named(view, attr):
    return next(e for e in view._elements if e.get("model_attr") == attr)


def cell_of(grid, widget):
    """(row, column) of the grid cell `widget` sits in, or None.

    A dropdown's widget is the combo, and what the grid holds is the cell that
    carries the combo *and* its rescan button - so the lookup climbs.
    """
    while widget is not None:
        index = grid.indexOf(widget)
        if index >= 0:
            row, column, _, _ = grid.getItemPosition(index)
            return (row, column)
        widget = widget.parentWidget()
    return None


# ---------------------------------------------------------------------------
# QtPanelView: the build contract
# ---------------------------------------------------------------------------

def test_a_panel_renders_every_element_type_in_its_schema(view):
    kinds = {e["type"] for e in view._elements}
    assert kinds == sch.ELEMENT_TYPES - {"internal"} | {"button"}
    for element in view._elements:
        if element["type"] != "internal":
            assert view._widget_for(element) is not None


def test_a_renderer_missing_one_element_type_refuses_to_construct(controller):
    """Feature parity, enforced at construction rather than hoped for."""
    class Crippled(qt.QtPanelView):
        _make_plot = None

    with pytest.raises(TypeError, match="cannot render"):
        Crippled(controller, "Fake")


def test_an_internal_element_renders_no_widget_and_no_blank_row(qapp, controller,
                                                                panel, monkeypatch):
    """PYSIDE-17: `internal` used to fall through to an empty `addRow`, which
    still left a gap for a control that does not exist."""
    real_schema = panel.schema
    real_schema["sections"][0]["elements"].append(
        {"type": "internal", "text": "", "command": "quiet", "writable": False,
         "role": "neutral"})
    monkeypatch.setattr(type(panel), "schema",
                        property(lambda self: real_schema))
    built = qt.QtPanelView(controller, "Fake")
    try:
        internal = element_of(built, "internal")
        assert built._widget_for(internal) is None
    finally:
        built.close()


# ---------------------------------------------------------------------------
# QtPanelView: commands and inputs (PYSIDE-5, PYSIDE-6)
# ---------------------------------------------------------------------------

def test_the_text_in_the_box_travels_with_the_command(view, panel):
    """PYSIDE-5/6: the click used to read the model, which still held the
    *previous* value whenever the button did not take focus first (macOS).
    The widget's text goes with the command, so there is nothing to flush."""
    entry = view._widget_for(element_of(view, "entry"))
    entry.setText("7.5")
    view._run(element_of(view, "button"))
    assert panel.commands[-1] == ("go", 7.5)


def test_a_refused_command_shows_a_status_line_and_opens_no_dialog(view, panel,
                                                                   monkeypatch):
    raised = []
    monkeypatch.setattr(QMessageBox, "exec",
                        lambda self: raised.append(self.text()))
    panel.refuse = True
    view._run(element_of(view, "button"))
    assert "not homed" in view.status_label.text()
    assert raised == []


def test_the_status_line_clears_on_the_next_command_that_succeeds(view, panel):
    panel.refuse = True
    view._run(element_of(view, "button"))
    assert view.status_label.text()
    panel.refuse = False
    view._run(element_of(view, "button"))
    assert view.status_label.text() == ""


def test_an_entry_the_operator_is_typing_in_is_not_overwritten(view):
    """`_is_focused` is the seam: offscreen tests cannot stage real focus."""
    element = element_of(view, "entry")
    entry = view._widget_for(element)
    view._set_text(element, "1.000")
    entry.setText("9.")                      # mid-edit, not yet a number
    view.__class__._is_focused = staticmethod(lambda widget: True)
    try:
        assert view._entry_is_dirty(element) is True
        view._refresh()
        assert entry.text() == "9."
    finally:
        view.__class__._is_focused = staticmethod(lambda w: w.hasFocus())


def test_an_unfocused_entry_follows_the_model(view, panel):
    element = element_of(view, "entry")
    entry = view._widget_for(element)
    panel.speed = 3.5
    view._refresh()
    assert entry.text() == "3.500"
    assert view._entry_is_dirty(element) is False


def test_a_numeric_entry_accepts_more_decimals_than_it_displays(view):
    """PYSIDE-19: the fixed 3-decimal validator made 0.0005 untypable."""
    entry = view._widget_for(element_of(view, "entry"))
    validator = entry.validator()
    assert validator is not None
    state, _, _ = validator.validate("0.0005", 0)
    assert state != type(state).Invalid


def test_the_entry_validator_uses_the_c_locale(view):
    """A comma-decimal system locale rejected "." outright, so the operator
    could not type a number at all."""
    entry = view._widget_for(element_of(view, "entry"))
    assert entry.validator().locale().name().startswith("C")


# ---------------------------------------------------------------------------
# QtPanelView: state rendering
# ---------------------------------------------------------------------------

def test_a_toggle_takes_its_text_and_colour_from_the_state(view, panel):
    element = element_of(view, "toggle")
    button = view._widget_for(element)
    view._refresh()
    assert button.text() == "OFF"
    panel.is_on = True
    view._refresh()
    assert button.text() == "ON"
    on_colours = theme.toggle_colors(element, True)
    assert on_colours["background"] in button.styleSheet()


def test_a_toggle_follows_the_model_after_a_full_stop(view, panel):
    """REDPERCENT-19/PYSIDE-16: button state used to follow the last click."""
    button = view._widget_for(element_of(view, "toggle"))
    panel.is_on = True
    view._refresh()
    assert button.text() == "ON"
    panel.is_on = False          # e.g. the estop latched behind the view's back
    view._refresh()
    assert button.text() == "OFF"


def test_a_gated_control_is_disabled_in_the_mode_that_gates_it(view, panel):
    gated = next(e for e in view._elements if e.get("command") == "idle_only")
    view._refresh()
    assert view._widget_for(gated).isEnabled() is True
    panel.is_on = True           # mode_name -> "running"
    view._refresh()
    assert view._widget_for(gated).isEnabled() is False


def test_a_stale_state_dims_the_readouts(view, controller):
    view._set_stale(True)
    assert view.property("stale") == "true"
    assert view.stale_label.isVisibleTo(view) is True
    view._set_stale(False)
    assert view.property("stale") == "false"


def test_a_region_is_shown_as_text_rather_than_announced_by_a_dialog(view, panel):
    """PYSIDE-12: the capture used to be confirmed by a modal raised over the
    overlay. The schema already carries the value; the renderer draws it."""
    element = element_of(view, "region_select")
    panel.region = {"left": 1, "top": 2, "width": 30, "height": 40}
    view._refresh()
    assert view._widget_for(element).text() == sch.format_region(panel.region)


def test_the_live_series_is_drawn_inline_from_the_data_command(view):
    plot = view._widget_for(element_of(view, "plot"))
    view._refresh()
    assert plot.points == [(0.0, 1.0), (1.0, 5.0), (2.0, 2.0)]


def test_the_log_stream_shows_what_its_source_command_returned(view):
    stream = view._widget_for(element_of(view, "log_stream"))
    view._refresh()
    assert stream.toPlainText() == "first\nsecond"


def test_an_exception_on_the_render_tick_neither_stops_it_nor_opens_a_dialog(
        view, monkeypatch):
    """PYSIDE-10: an unguarded timer slot reached the excepthook, which opened
    a modal, while the timer kept firing - dozens of dialogs a second."""
    raised = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: raised.append(1))
    monkeypatch.setattr(type(view), "_refresh",
                        lambda self: (_ for _ in ()).throw(RuntimeError("boom")))
    view._on_timer_tick()
    view._on_timer_tick()
    assert raised == []
    assert view._timer.isActive()


# ---------------------------------------------------------------------------
# QtPanelView: the composites
# ---------------------------------------------------------------------------

def test_file_save_copies_what_the_model_wrote_to_the_chosen_path(view, panel,
                                                                  tmp_path,
                                                                  monkeypatch):
    written = tmp_path / "model-wrote-this.csv"
    written.write_text("a,b\n1,2\n")
    panel.saved_path = str(written)
    destination = tmp_path / "operator-chose-this"
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(destination), "")))
    view._save_to_file(element_of(view, "file_save"))
    # PYSIDE-18: Qt's filter is a display hint, so a bare name saved with no
    # extension at all where Tk's `defaultextension` always supplied one.
    assert (tmp_path / "operator-chose-this.csv").read_text() == "a,b\n1,2\n"


def test_a_cancelled_save_dialog_runs_no_command(view, panel, monkeypatch):
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: ("", "")))
    panel.saved_path = None
    view._save_to_file(element_of(view, "file_save"))
    assert panel.saved_path is None


def test_file_open_passes_the_chosen_path_to_the_command(view, panel,
                                                         tmp_path, monkeypatch):
    chosen = tmp_path / "run.csv"
    chosen.write_text("x\n")
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (str(chosen), "")))
    view._open_from_file(element_of(view, "file_open"))
    assert panel.loaded_path == str(chosen)


def test_a_dropdown_selection_runs_the_command(view, panel):
    view._on_dropdown_changed(element_of(view, "dropdown"), "b")
    assert panel.choice == "b"


def test_a_programmatic_dropdown_update_does_not_run_the_command(view, panel):
    """PYSIDE-7: the refresh loop wrote the combo, the combo fired its
    handler, and the handler ran a command nobody asked for."""
    element = element_of(view, "dropdown")
    panel.choice = "a"
    view._refresh()
    panel.choice = None
    view._set_text(element, "b")
    assert panel.choice is None
    assert view._widget_for(element).currentText() == "b"


def test_refreshing_the_options_keeps_the_selection_and_runs_nothing(view, panel):
    element = element_of(view, "dropdown")
    combo = view._widget_for(element)
    view._set_text(element, "a")
    panel.choice = None
    view._reload_options(element, combo)
    assert combo.currentText() == "a"
    assert panel.choice is None


def test_a_region_pick_calls_the_command_with_the_rectangle(view, panel):
    element = element_of(view, "region_select")
    view._pick_region(element)
    view._overlay.on_region(10, 20, 300, 400)
    assert panel.region == {"left": 10, "top": 20, "width": 300, "height": 400}


# ---------------------------------------------------------------------------
# RegionOverlay (PYSIDE-12, REDPERCENT-18)
# ---------------------------------------------------------------------------

def test_the_overlay_spans_the_whole_virtual_desktop(qapp):
    from PySide6.QtWidgets import QApplication
    overlay = qt.RegionOverlay(lambda *a: None)
    try:
        union = overlay.geometry()
        for screen in QApplication.screens():
            assert union.contains(screen.geometry())
    finally:
        overlay.close()


def test_the_overlay_is_frameless_translucent_and_on_top(qapp):
    overlay = qt.RegionOverlay(lambda *a: None)
    try:
        flags = overlay.windowFlags()
        assert flags & Qt.WindowType.FramelessWindowHint
        assert flags & Qt.WindowType.WindowStaysOnTopHint
        assert overlay.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        assert "drag" in overlay.instruction_label.text().lower()
        assert "ESC" in overlay.instruction_label.text()
    finally:
        overlay.close()


def test_a_drag_shorter_than_ten_pixels_reports_a_message_never_a_dialog(
        qapp, monkeypatch):
    """PYSIDE-12's deadlock: a QMessageBox raised over an always-on-top
    frameless overlay can land *beneath* it with modal input already blocked.
    Nothing this widget does may open a dialog."""
    raised, regions = [], []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: raised.append(1))
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: raised.append(1)))
    overlay = qt.RegionOverlay(lambda *a: regions.append(a))
    try:
        overlay.start_point = QPoint(10, 10)
        overlay.end_point = QPoint(15, 18)
        overlay.mouseReleaseEvent(None)
        assert regions == []
        assert raised == []
        assert "too small" in overlay.instruction_label.text()
        assert overlay.isVisible() is False
    finally:
        overlay.close()


def test_a_real_drag_reports_the_rectangle_in_physical_pixels(qapp):
    from PySide6.QtWidgets import QApplication
    regions = []
    overlay = qt.RegionOverlay(lambda *a: regions.append(a))
    overlay.start_point = QPoint(10, 20)
    overlay.end_point = QPoint(110, 140)
    overlay.mouseReleaseEvent(None)
    ratio = (QApplication.screenAt(QPoint(10, 20))
             or QApplication.primaryScreen()).devicePixelRatio()
    assert regions == [qt.to_physical_pixels(10, 20, 100, 120, ratio)]


def test_escape_cancels_without_reporting_anything(qapp):
    from PySide6.QtGui import QKeyEvent
    from PySide6.QtCore import QEvent
    regions = []
    overlay = qt.RegionOverlay(lambda *a: regions.append(a))
    overlay.show()
    overlay.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape,
                                    Qt.KeyboardModifier.NoModifier))
    assert regions == []
    assert overlay.isVisible() is False


# ---------------------------------------------------------------------------
# DeviceDock and the dashboard's panels
# ---------------------------------------------------------------------------

def test_closing_a_dock_closes_the_model(dashboard, controller):
    dashboard._add_panel("Fake")
    dock = dashboard._docks["Fake"]
    dock.close()
    assert controller.removed == ["Fake"]


def test_a_dock_closed_by_the_controller_does_not_close_the_model_twice(
        dashboard, controller):
    dashboard._add_panel("Fake")
    dashboard._remove_panel("Fake")
    assert controller.removed == []
    assert "Fake" not in dashboard._docks


def test_the_setup_panel_is_shown_first(dashboard, qapp):
    dashboard.open()
    assert dashboard._setup_dock is not None
    assert dashboard._setup_dock.windowTitle() == "Setup"
    assert isinstance(dashboard._setup_dock.widget(), qt.QtPanelView)


def test_the_sidebar_lists_open_and_closed_models(dashboard, controller):
    dashboard._build_sidebar()
    rows = {dashboard.model_list.item(i).text():
            dashboard.model_list.item(i).checkState()
            for i in range(dashboard.model_list.count())}
    assert rows == {"Fake": Qt.CheckState.Checked,
                    "Gone": Qt.CheckState.Unchecked}


def test_checking_a_closed_model_reopens_it(dashboard, controller):
    dashboard._build_sidebar()
    row = next(dashboard.model_list.item(i)
               for i in range(dashboard.model_list.count())
               if dashboard.model_list.item(i).text() == "Gone")
    row.setCheckState(Qt.CheckState.Checked)
    assert controller.reopened == ["Gone"]


def test_a_reopen_that_fails_reverts_the_checkbox_instead_of_raising(
        dashboard, controller, monkeypatch):
    def refuse(name):
        raise ValueError(f"{name} was never configured")
    monkeypatch.setattr(controller, "reopen", refuse)
    dashboard._build_sidebar()
    row = next(dashboard.model_list.item(i)
               for i in range(dashboard.model_list.count())
               if dashboard.model_list.item(i).text() == "Gone")
    row.setCheckState(Qt.CheckState.Checked)
    assert row.checkState() == Qt.CheckState.Unchecked


# ---------------------------------------------------------------------------
# The global stop
# ---------------------------------------------------------------------------

def test_the_full_stop_button_latches_and_relabels(dashboard, controller):
    dashboard._on_stop_clicked()
    assert controller.estop_calls == 1
    assert dashboard.stop_button.text() == "CLEAR FULL STOP"


def test_clearing_the_latch_asks_first(dashboard, controller, monkeypatch):
    controller.is_estopped = True
    asked = []

    def confirm(prompt):
        asked.append(prompt)
        return True

    monkeypatch.setattr(dashboard, "_confirm", confirm)
    monkeypatch.setattr(controller, "clear_estop_all",
                        lambda confirmed=False: (
                            Result(Result.CONFIRM, reason="Release?",
                                   command="clear_estop_all")
                            if not confirmed else Result(Result.OK)))
    dashboard.toggle_estop_all()
    assert asked == ["Release?"]


def test_the_stop_button_colour_comes_from_the_theme(dashboard, controller):
    controller.is_estopped = True
    dashboard._sync_stop_button()
    latched = theme.toggle_colors({"on_role": "danger", "off_role": "danger"},
                                  True)
    assert latched["background"] in dashboard.stop_button.styleSheet()


# ---------------------------------------------------------------------------
# Events: the queued marshal (PYSIDE-21)
# ---------------------------------------------------------------------------

def test_an_event_published_on_the_gui_thread_is_queued_never_direct(
        dashboard, qapp, monkeypatch):
    """PYSIDE-21, pinned directly. `AutoConnection` resolves to a *direct*
    call when the publisher is already on the GUI thread, so an error raised
    inside `closeEvent -> Controller.close()` opened a nested event loop
    mid-teardown. Restoring the plain `connect` fails this test."""
    shown = []
    monkeypatch.setattr(type(dashboard), "_show_event",
                        lambda self, event: shown.append(event.text))
    dashboard.open()
    events.error("Heater Off Not Delivered", "boom", source="Test", ack=False)
    assert shown == [], "the slot ran inside the publisher's own call stack"
    qapp.processEvents()
    assert len(shown) == 1


def test_an_event_published_from_a_worker_thread_still_arrives(
        dashboard, qapp, monkeypatch):
    shown = []
    monkeypatch.setattr(type(dashboard), "_show_event",
                        lambda self, event: shown.append(event.text))
    dashboard.open()
    worker = threading.Thread(
        target=lambda: events.warn("Worker", "from a thread", source="Test"))
    worker.start()
    worker.join(timeout=5.0)
    assert not worker.is_alive()
    assert shown == []
    qapp.processEvents()
    assert len(shown) == 1


def test_an_acknowledged_event_opens_exactly_one_modal(dashboard, qapp,
                                                       monkeypatch):
    raised = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: raised.append(self.text()))
    dashboard.open()
    events.error("Fault", "stop not confirmed", source="Test")
    qapp.processEvents()
    assert len(raised) == 1


def test_no_modal_opens_while_the_dashboard_is_closing(dashboard, qapp,
                                                       monkeypatch):
    """The popup that hung the Qt suite for three sessions: a modal raised
    from inside a close path blocks the exit with nobody to click it."""
    raised = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: raised.append(1))
    dashboard.open()
    dashboard.close()
    events.error("Fault", "raised during teardown", source="Test")
    qapp.processEvents()
    assert raised == []


def test_the_event_log_escapes_what_a_device_said(dashboard, qapp):
    class Spoof:
        severity, source, title, message, count = "info", "T", "t", "m", 1
        needs_ack = False
        text = "<b>not bold</b>"

    dashboard._show_event(Spoof())
    assert "not bold" in dashboard.event_view.toPlainText()
    assert "<b>not bold</b>" in dashboard.event_view.toPlainText()


# ---------------------------------------------------------------------------
# Lifecycle and focus
# ---------------------------------------------------------------------------

def test_closing_the_window_unsubscribes_before_closing_the_controller(
        dashboard, qapp, monkeypatch):
    from PySide6.QtGui import QCloseEvent
    dashboard.open()
    dashboard.closeEvent(QCloseEvent())
    assert dashboard._closing is True
    assert dashboard._on_event not in getattr(events, "_subscribers", [])
    assert dashboard.controller.is_closed is True


def test_a_second_close_does_not_run_the_teardown_twice(dashboard, qapp):
    dashboard.open()
    dashboard.close()
    dashboard.controller.is_closed = False
    dashboard.close()
    assert dashboard.controller.is_closed is False


def test_losing_focus_gates_input_and_never_stops_anything(dashboard,
                                                           controller,
                                                           monkeypatch):
    dashboard._is_focused = True
    monkeypatch.setattr(type(dashboard), "_app_has_focus", lambda self: False)
    dashboard._sync_focus_gate()
    assert controller.focus_calls == [False]


def test_a_child_dialog_taking_focus_is_not_focus_loss(dashboard, controller,
                                                       monkeypatch):
    """D-4/PYSIDE-14: a file picker or a confirmation deactivates the main
    window while the *application* is still focused. Gating input then would
    kill the jog the operator is in the middle of."""
    dashboard._is_focused = True
    monkeypatch.setattr(type(dashboard), "_app_has_focus", lambda self: True)
    dashboard._sync_focus_gate()
    assert controller.focus_calls == []


def test_the_focus_gate_is_only_forwarded_when_it_changes(dashboard,
                                                          controller,
                                                          monkeypatch):
    monkeypatch.setattr(type(dashboard), "_app_has_focus", lambda self: False)
    dashboard._sync_focus_gate()
    dashboard._sync_focus_gate()
    assert controller.focus_calls == [False]


def test_the_stylesheet_is_applied_to_the_application_not_the_window(view, qapp):
    """PYSIDE-15: on the window, every QMessageBox and file dialog stayed in
    the native light palette, unparented over a dark dashboard."""
    assert qapp.styleSheet() == qt.stylesheet()


# ---------------------------------------------------------------------------
# The row layout: Setup as a table (Addendum 2)
# ---------------------------------------------------------------------------

def test_a_row_section_puts_all_of_its_elements_on_one_line(table_view):
    """`layout="row"` is a hint the renderer has to honour. Before this, the
    Setup panel was one long vertical column of six stacked forms - the owner's
    "overly convoluted, everything in one vertical tab is poor UI/UX"."""
    grid = table_view._table.grid
    rows = {attr: cell_of(grid, table_view._widget_for(element_named(table_view, attr)))[0]
            for attr in ("stepper_port", "stepper_gamepad", "stepper_found")}
    assert len(set(rows.values())) == 1, rows


def test_every_model_gets_its_own_line_in_schema_order(table_view):
    grid = table_view._table.grid
    lines = [cell_of(grid, table_view._widget_for(element_named(table_view, attr)))[0]
             for attr in ("stepper_found", "rotator_found", "red_found")]
    assert lines == sorted(lines) and len(set(lines)) == 3


def test_a_column_named_port_holds_only_ports_however_ragged_the_rows(table_view):
    """The alignment claim, pinned. The rotator declares no gamepad and Red
    Percent declares no port, so allocating columns by *position* would put
    the rotator's "Detected:" under the stepper's Gamepad dropdown. Columns
    are allocated by label instead."""
    grid = table_view._table.grid
    column = {attr: cell_of(grid, table_view._widget_for(element_named(table_view, attr)))[1]
              for attr in ("stepper_port", "rotator_port",
                           "stepper_found", "rotator_found", "red_found")}
    assert column["stepper_port"] == column["rotator_port"]
    assert column["stepper_found"] == column["rotator_found"] == column["red_found"]
    assert column["stepper_port"] != column["stepper_found"]


def test_the_table_names_each_row_and_captions_each_column_once(table_view):
    grid = table_view._table.grid
    headers = [grid.itemAtPosition(qt.PanelTable.HEADER_ROW, c).widget().text()
               for c in range(1, grid.columnCount())
               if grid.itemAtPosition(qt.PanelTable.HEADER_ROW, c) is not None]
    titles = [grid.itemAtPosition(r, 0).widget().text()
              for r in range(1, grid.rowCount())
              if grid.itemAtPosition(r, 0) is not None]
    assert headers == ["Port", "Gamepad", "Detected"]
    assert titles == ["Stepper Probe", "Rotator", "Red Percent"]


def test_an_untitled_row_section_claims_no_name_column(qapp, monkeypatch):
    """A schema that already carries the row's name as an element - Setup's
    `Device:` readout - can title the section "" and get a table with no
    duplicated name and no dead column, without a renderer change."""
    panel = TablePanel()
    schema = panel.schema
    for section in schema["sections"]:
        if section.get("layout") == "row":
            section["title"] = ""
    monkeypatch.setattr(type(panel), "schema", property(lambda self: schema))
    built = qt.QtPanelView(FakeController(panel), "Table")
    try:
        grid = built._table.grid
        assert grid.columnMinimumWidth(0) == 0
        assert all(grid.itemAtPosition(r, 0) is None
                   for r in range(grid.rowCount()))
    finally:
        built.close()


def test_a_column_section_keeps_its_own_card_and_stays_out_of_the_table(table_view):
    """Only `layout="row"` sections join the table: the scan status and the
    Launch button are still a form apiece."""
    grid = table_view._table.grid
    status = table_view._widget_for(element_named(table_view, "scan_status"))
    assert cell_of(grid, status) is None


def test_every_dropdown_is_one_width_rather_than_as_wide_as_its_longest_option(
        table_view):
    combos = [table_view._widget_for(e) for e in table_view._elements
              if e["type"] == "dropdown"]
    assert len(combos) == 3
    assert {c.minimumContentsLength() for c in combos} == {qt.DROPDOWN_CHARS}
    grid = table_view._table.grid
    used = {cell_of(grid, c)[1] for c in combos}
    assert all(grid.columnMinimumWidth(c) == qt.TABLE_CONTROL_MIN_PX
               for c in used)


def test_a_dropdown_in_a_row_section_still_runs_its_command(table_view,
                                                            table_panel):
    element = element_named(table_view, "stepper_port")
    table_view._on_dropdown_changed(element, "COM3")
    assert ("stepper", "port", "COM3") in table_panel.selected


def test_a_panel_with_no_row_section_builds_no_table(view):
    assert view._table is None


# ---------------------------------------------------------------------------
# Integers are integers (Addendum 2)
# ---------------------------------------------------------------------------

def test_an_int_entry_refuses_a_decimal_point_outright(table_view):
    """A QDoubleValidator with decimals=0 keeps *accepting* the point and
    calls the result intermediate, so the box looked as though it took the
    value. QIntValidator refuses it."""
    entry = table_view._widget_for(element_named(table_view, "dwell"))
    validator = entry.validator()
    assert isinstance(validator, QIntValidator)
    state, _, _ = validator.validate("5.5", 0)
    assert state == type(state).Invalid
    state, _, _ = validator.validate("42", 0)
    assert state == type(state).Acceptable


def test_a_refresh_never_writes_a_decimal_into_an_int_entry(table_view):
    """The box would then reject the text the refresh just put in it, and the
    operator would be editing a field that refuses its own contents."""
    element = element_named(table_view, "dwell")
    entry = table_view._widget_for(element)
    table_view._set_text(element, "5.000")
    assert entry.text() == "5"
    assert entry.hasAcceptableInput() is True


def test_a_float_entry_still_gets_the_wide_decimal_validator(view):
    """PYSIDE-19 is not undone by the integer path."""
    entry = view._widget_for(element_of(view, "entry"))
    assert not isinstance(entry.validator(), QIntValidator)
    state, _, _ = entry.validator().validate("0.0005", 0)
    assert state != type(state).Invalid


# ---------------------------------------------------------------------------
# Polish: the lamp, the log box, FULL STOP, the event dock
# ---------------------------------------------------------------------------

def test_an_indicator_is_a_lamp_and_never_repeats_its_own_caption(view, panel):
    """It rendered as "Fault    Fault" on the bench: the caption, then a
    full-width box carrying the element's text all over again."""
    element = element_of(view, "indicator")
    lamp = view._widget_for(element)
    view._refresh()
    assert lamp.text() == ""
    assert lamp.width() == qt.LAMP_PX and lamp.height() == qt.LAMP_PX


def test_a_lamp_takes_its_colour_from_the_state_not_from_a_literal(view, panel):
    element = element_of(view, "indicator")
    lamp = view._widget_for(element)
    view._refresh()
    assert theme.toggle_colors(element, False)["border"] in lamp.styleSheet()
    panel.is_faulted = True
    view._refresh()
    assert theme.toggle_colors(element, True)["background"] in lamp.styleSheet()
    assert lamp.text() == ""


def test_a_log_stream_stays_a_few_scrollable_lines(view):
    """Left to expand, the stepper's Gamepad Log took a third of the panel and
    pushed the Safety section off the bottom of the dock."""
    stream = view._widget_for(element_of(view, "log_stream"))
    assert stream.height() == qt.LOG_STREAM_PX
    assert stream.maximumHeight() == qt.LOG_STREAM_PX
    view._refresh()
    assert stream.toPlainText() == "first\nsecond"


def test_a_models_own_stop_takes_the_whole_section_and_the_tall_metric(
        table_view):
    """It rendered as a caption and a small button beside it - "FULL STOP
    FULL STOP" - which is neither prominent nor readable as one control."""
    element = next(e for e in table_view._elements
                   if e["type"] == "toggle" and e.get("on_role") == "danger")
    button = table_view._widget_for(element)
    assert button.minimumHeight() == qt.STOP_BUTTON_PX
    assert button.sizePolicy().horizontalPolicy() == QSizePolicy.Policy.Expanding


def test_an_ordinary_toggle_keeps_its_caption_and_its_natural_size(view):
    """Only a danger toggle is a stop; everything else stays a labelled row."""
    button = view._widget_for(element_of(view, "toggle"))
    assert button.minimumHeight() != qt.STOP_BUTTON_PX


def test_a_section_card_keeps_its_natural_height(table_view):
    """Left to expand, the stepper's System Control card grew to a third of
    the dock with its title floating in the middle of the empty space."""
    cards = [c for c in table_view.findChildren(QFrame)
             if c.objectName() == "card"]
    assert len(cards) >= 3
    assert all(c.sizePolicy().verticalPolicy() == QSizePolicy.Policy.Maximum
               for c in cards)


def test_the_full_stop_button_is_tall_and_set_in_the_theme_size(dashboard):
    assert dashboard.stop_button.minimumHeight() == qt.STOP_BUTTON_PX
    dashboard._sync_stop_button()
    _, size, _ = theme.font(qt.STOP_FONT_SCALE, bold=True)
    assert f"font-size: {size}pt" in dashboard.stop_button.styleSheet()


def test_the_event_log_keeps_only_its_tail(dashboard):
    """A window that runs a whole bench session cannot hold every line."""
    class Spoof:
        severity, source, title, message, count = "info", "T", "t", "m", 1
        needs_ack = False
        text = "a line"

    assert dashboard.event_view.document().maximumBlockCount() == qt.EVENT_LOG_LINES
    for _ in range(qt.EVENT_LOG_LINES + 25):
        dashboard._show_event(Spoof())
    assert dashboard.event_view.document().blockCount() <= qt.EVENT_LOG_LINES


# ---------------------------------------------------------------------------
# Collapsing Setup, and getting it back (Addendum 2)
# ---------------------------------------------------------------------------

@pytest.fixture
def fresh_dashboard(dashboard, controller):
    """A dashboard opened the way the app opens one: nothing built yet, so
    Setup is the only thing on screen until the operator launches something."""
    controller.open_names.clear()
    return dashboard


def test_the_setup_dock_collapses_when_the_first_model_launches(fresh_dashboard,
                                                                qapp,
                                                                controller):
    dashboard = fresh_dashboard
    dashboard.open()
    assert dashboard._setup_dock.isHidden() is False
    controller.notify("added", "Fake")
    qapp.processEvents()
    assert dashboard._setup_dock.isHidden() is True


def test_the_collapsed_setup_dock_comes_back_from_the_toolbar(fresh_dashboard, qapp,
                                                              controller):
    """It has to stay reopenable: Refresh and Relaunch are mid-session jobs."""
    dashboard = fresh_dashboard
    dashboard.open()
    controller.notify("added", "Fake")
    qapp.processEvents()
    action = dashboard.setup_action
    assert action.isCheckable() is True
    # Qt *disables* a dock's toggleViewAction unless the dock is closable, so
    # this is the assertion that catches a dead toolbar entry.
    assert action.isEnabled() is True
    assert action.isChecked() is False
    action.trigger()
    assert dashboard._setup_dock.isHidden() is False
    assert action.isChecked() is True


def test_the_setup_panel_is_put_away_not_torn_down(fresh_dashboard, qapp, controller):
    """Its scan results and its selections are still there when it comes back,
    which they would not be if the panel behind it had been destroyed. A
    model's `DeviceDock` deletes itself on close; Setup's must not."""
    dashboard = fresh_dashboard
    dashboard.open()
    panel_view = dashboard._setup_dock.widget()
    assert not dashboard._setup_dock.testAttribute(
        Qt.WidgetAttribute.WA_DeleteOnClose)
    controller.notify("added", "Fake")
    qapp.processEvents()
    dashboard.show_setup()
    assert dashboard._setup_dock.widget() is panel_view
    assert panel_view._timer.isActive() is True


def test_a_dashboard_that_opens_onto_running_models_starts_collapsed(dashboard,
                                                                     qapp):
    """`base.Dashboard` only sees the first add *after* open(), so a config
    built before the window existed would otherwise leave the wizard up over
    a system that has already launched."""
    dashboard.open()
    assert dashboard.controller.model_names == ["Fake"]
    assert dashboard._setup_dock.isHidden() is True
    assert dashboard.setup_action.isEnabled() is True


def test_closing_the_setup_dock_puts_it_away_and_closes_no_model(fresh_dashboard,
                                                                 qapp,
                                                                 controller):
    """Its X is not a model's X: it hides Setup and removes nothing."""
    dashboard = fresh_dashboard
    dashboard.open()
    dashboard._setup_dock.close()
    assert controller.removed == []
    assert dashboard.setup_action.isChecked() is False
    dashboard.show_setup()
    assert dashboard._setup_dock.isHidden() is False


def test_setup_collapses_once_and_a_reopened_panel_is_left_alone(fresh_dashboard, qapp,
                                                                 controller):
    dashboard = fresh_dashboard
    dashboard.open()
    controller.notify("added", "Fake")
    qapp.processEvents()
    dashboard.show_setup()
    controller.notify("added", "Gone")
    qapp.processEvents()
    assert dashboard._setup_dock.isHidden() is False


def test_collapsing_without_a_setup_dock_is_not_an_error(dashboard):
    """`_collapse_setup` can arrive before `open()` has built the dock."""
    dashboard._collapse_setup()
    dashboard.show_setup()
    assert dashboard._setup_dock is None


def test_the_toolbar_offers_an_entry_for_every_dock_it_can_hide(dashboard, qapp):
    dashboard.open()
    assert [a.text() for a in dashboard.toolbar.actions()] == [
        "Setup", "Models", "Event Log"]
