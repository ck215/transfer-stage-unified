"""The Qt view's widget behaviour. **Every test here is `qt`-marked.**

A native Qt abort takes the whole pytest session down with it and discards
every already-passed result, so the agent that wrote these did not run them;
the lead runs the Qt pass. They use pytest-qt's `qapp` fixture, which
`tests/conftest.py` also keys its auto-marking and its skip-on-broken-Qt
probe off.

Nothing here opens a real modal: `QMessageBox.exec` is patched wherever one
could be raised, because there is nobody to click it.
"""
import os
import threading

import pytest

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QFileDialog, QLabel, QMessageBox

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


def element_of(view, kind):
    return next(e for e in view._elements if e["type"] == kind)


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
