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
import sys
import threading

import pytest

from PySide6.QtGui import QColor, QFontMetrics, QIntValidator, QKeySequence
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QFrame,
                               QLabel, QMessageBox)

import schema as sch
from events import events
from panel import Panel
from param import Param
from result import Refused, Result
from views import qt, theme

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
        self.is_armed = False
        self.gated = "a"
        self.armed_calls = []

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
            # G3: a tick box, and a dropdown live only while it is ticked.
            sch.checkbox("Armed", "is_armed", "set_armed",
                         tooltip="Arm the fake"),
            sch.dropdown("Gated:", "gated", "set_gated", "choices",
                         enabled_by="is_armed"),
            sch.region_select("Select region", "set_region",
                              model_attr="region"),
            sch.file_save("Save", "save_run"),
            sch.file_open("Load", "load_run"),
            sch.plot("Series", "series"),
            sch.image("Picture", "picture"),
            sch.indicator("Fault", "is_faulted"),
            sch.log_stream("Log", "log_lines"),
            # G4: the probe's shape - behind a button, in its own window.
            sch.log_stream("Gamepad Log:", "gamepad_lines", detached=True),
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

    def set_armed(self, flag):
        self.armed_calls.append(flag)
        self.is_armed = bool(flag)
        return self.is_armed

    def set_gated(self, text):
        self.gated = text

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

    def gamepad_lines(self):
        self.gamepad_reads = getattr(self, "gamepad_reads", 0) + 1
        return ["pad up", "pad down"]

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


class TallPanel(Panel):
    """Red Percent's shape: ten sections, the last of them Safety.

    Stacked in one column its dock was twice the height of the screen and
    Safety - the section that must stay reachable - sat below the fold.
    """

    NAME = "Tall"
    SECTIONS = ("Run", "Operator Annotation", "Probe Metadata", "Synced Axes",
                "Red Detection", "Sampling", "Live", "Control", "Analysis",
                "Safety")

    def __init__(self):
        super().__init__()
        for name in self.SECTIONS:
            setattr(self, self.attr(name), name)

    @staticmethod
    def attr(name):
        return name.lower().replace(" ", "_")

    @property
    def schema(self):
        return sch.schema(*[
            sch.section(name, sch.readonly("Value:", self.attr(name)))
            for name in self.SECTIONS])


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
        # L1: `stop_state` as the Controller serves it. `latched` None means
        # "every open model while `is_estopped`" (what the older tests set);
        # a set is a partial stop. `unconfirmed` are latched models whose
        # stop did not confirm (their state's `stop_confirmed` is False).
        self.latched = None
        self.unconfirmed = set()

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
        latched = self.stop_state["latched"]
        snapshot["stop_confirmed"] = (None if name not in latched
                                      else name not in self.unconfirmed)
        return snapshot

    @property
    def stop_state(self):
        names = list(self.open_names)
        if self.latched is not None:
            latched = [n for n in names if n in self.latched]
        else:
            latched = names if self.is_estopped else []
        return {"latched": latched,
                "unconfirmed": [n for n in latched if n in self.unconfirmed],
                "every": bool(latched) and len(latched) == len(names)}

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
        self.latched = None
        return {n: n not in self.unconfirmed for n in self.open_names}

    def clear_estop_all(self, confirmed=False):
        self.is_estopped = False
        self.latched = None
        self.unconfirmed = set()
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


@pytest.fixture
def tall_view(qapp):
    built = qt.QtPanelView(FakeController(TallPanel()), "Tall")
    yield built
    built.close()


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
# SheetEntry and the dashboard's panels (E: entries on the sheet, not docks)
# ---------------------------------------------------------------------------

def test_closing_an_entry_closes_the_model(dashboard, controller):
    """Updated (E): a model is an entry on the sheet, not a dock; its close
    still closes the model."""
    dashboard._add_panel("Fake")
    entry = dashboard._entries["Fake"]
    entry.close_button.click()
    assert controller.removed == ["Fake"]


def test_an_entry_removed_by_the_controller_does_not_close_the_model_twice(
        dashboard, controller):
    dashboard._add_panel("Fake")
    dashboard._remove_panel("Fake")
    assert controller.removed == []
    assert "Fake" not in dashboard._entries


def test_the_setup_panel_is_shown_first(dashboard, qapp):
    """Updated (E): Setup's panel sits in a capped scroll area inside its
    dock (H3), so the dock's widget is the scroll and the panel is inside."""
    dashboard.open()
    assert dashboard._setup_dock is not None
    assert dashboard._setup_dock.windowTitle() == "Setup"
    assert isinstance(dashboard.setup_view, qt.QtPanelView)
    assert dashboard._setup_dock.widget().widget() is dashboard.setup_view


def test_the_rail_offers_a_way_back_to_every_closed_model(dashboard, controller):
    """Replaces the sidebar's checkbox list, which repeated every open dock's
    title a second time: a closed model is reopened from the rail, as in the
    Web view, and an open one is already on screen under its own title."""
    dashboard._sync_rail()
    assert list(dashboard.reopen_buttons) == ["Gone"]
    assert dashboard._reopen_holder.isHidden() is False


def test_the_rail_says_nothing_about_reopening_when_nothing_is_closed(
        dashboard, controller):
    controller.closed = []
    dashboard._sync_rail()
    assert dashboard.reopen_buttons == {}
    assert dashboard._reopen_holder.isHidden() is True


def test_reopening_from_the_rail_reopens_the_model(dashboard, controller):
    dashboard._sync_rail()
    dashboard.reopen_buttons["Gone"].click()
    assert controller.reopened == ["Gone"]


def test_a_reopen_that_fails_is_logged_instead_of_raising(
        dashboard, controller, monkeypatch):
    def refuse(name):
        raise ValueError(f"{name} was never configured")
    monkeypatch.setattr(controller, "reopen", refuse)
    dashboard._sync_rail()
    dashboard.reopen_buttons["Gone"].click()       # must not raise
    assert "Gone" in dashboard.reopen_buttons


def test_the_rail_lists_each_open_model_once_and_the_opened_one_is_marked(
        dashboard, controller):
    """Updated (E): the rail carries names only - no value appears twice
    (`design-Sheet.md`); the numbers are on the sheet, once. Updated (K4):
    at launch the overview is the shown page, so "Overview" is the checked
    item; a model is checked once it is shown alone."""
    dashboard.open()
    assert list(dashboard._rail_items) == ["Fake"]
    item = dashboard._rail_items["Fake"]
    assert not item.isChecked() and item.accessibleName() == "Fake"
    assert dashboard.overview_item.isChecked() and dashboard.page == "Overview"
    item.click()
    assert item.isChecked() and not dashboard.overview_item.isChecked()


def test_a_closed_model_leaves_the_rail(dashboard, controller):
    dashboard._sync_rail()
    controller.open_names = []
    dashboard._sync_rail()
    assert "Fake" not in dashboard._rail_items


# ---------------------------------------------------------------------------
# The global stop
# ---------------------------------------------------------------------------

def test_the_full_stop_button_latches_and_relabels(dashboard, controller):
    """The Web mushroom's face: `Stop`, then `Clear` once latched. Updated:
    it read "CLEAR FULL STOP" in capitals."""
    assert dashboard.stop_button.text() == "Stop"
    dashboard._on_stop_clicked()
    assert controller.estop_calls == 1
    assert dashboard.stop_button.text() == "Clear"
    assert dashboard.stop_button.is_latched is True


def test_clearing_the_latch_asks_first(dashboard, controller, monkeypatch):
    """Updated (L14): the question goes through `ask` with a title and verb
    buttons ("Clear the stop" / "Keep it stopped"), not Yes / No; the
    dashboard's own clear path, since the disc's press is decided by
    `stop_words` (L1), not by `Controller.is_estopped`."""
    controller.is_estopped = True
    asked = []

    def ask(parent, prompt, title="", yes="", no=""):
        asked.append((prompt, title, yes, no))
        return True

    monkeypatch.setattr(qt, "ask", ask)
    monkeypatch.setattr(controller, "clear_estop_all",
                        lambda confirmed=False: (
                            Result(Result.CONFIRM, reason="Release?",
                                   command="clear_estop_all")
                            if not confirmed else Result(Result.OK)))
    dashboard.toggle_estop_all()
    assert asked == [("Release?", "Clear the stop?", "Clear the stop",
                      "Keep it stopped")]


def _pixel(widget, x, y):
    return QColor(widget.grab().toImage().pixel(x, y))


def _near(colour, token, tolerance=40):
    target = QColor(token)
    return (abs(colour.red() - target.red()) + abs(colour.green() - target.green())
            + abs(colour.blue() - target.blue())) <= tolerance


def test_the_stop_disc_is_always_red_and_its_ring_thickens_when_latched(
        dashboard, controller):
    """Updated (E): A's disc, painted - a red face, a sheet-coloured gap, a
    red ring - red in every state (it does not go quiet); latched it reads
    Clear and the ring goes from `theme.STOP["ring"]` to `["ring_latched"]`.
    It was a stylesheet whose latched ring was the trace."""
    button = dashboard.stop_button
    centre = button.width() // 2
    assert _near(_pixel(button, centre, centre // 2), theme.SIGNAL)
    assert button.ring_px() == theme.STOP["ring"]
    controller.is_estopped = True
    dashboard._sync_stop_button()
    button._pulse.stop()
    button._on_pulse_done()
    assert button.text() == "Clear"
    assert button.ring_px() == theme.STOP["ring_latched"]
    assert _near(_pixel(button, centre, centre // 2), theme.SIGNAL)
    edge = button.FOCUS_GAP + button.FOCUS_PX + theme.STOP["ring_latched"] // 2
    assert _near(_pixel(button, edge, centre), theme.SIGNAL)
    assert theme.TRACE not in (button.styleSheet() or "")


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


def test_an_acknowledged_event_joins_the_alert_band_and_opens_no_modal(
        dashboard, qapp, monkeypatch):
    """Updated (F1): it asserted exactly one `QMessageBox.exec` per event -
    the blocking modal that stacked over the stop. It is now one line in the
    rail's alert band, and nothing modal."""
    raised = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: raised.append(self.text()))
    dashboard.open()
    events.error("Fault", "stop not confirmed", source="Test")
    qapp.processEvents()
    assert raised == []
    assert dashboard.alert_band.isHidden() is False
    assert "stop not confirmed" in dashboard.alert_text.full_text()


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
    # Updated (E): the tray reports warnings and errors only, so the spoof
    # is a warning (an info line no longer reaches the tray at all).
    # Updated (L11): a line is the event's title and message, so the
    # device's words ride in the message.
    class Spoof:
        severity, source, title, message, count = ("warning", "T", "Reply",
                                                   "<b>not bold</b>", 1)
        needs_ack = False
        text = "[T] Reply: <b>not bold</b>"

    dashboard._show_event(Spoof())
    assert "not bold" in dashboard.event_view.toPlainText()
    assert "<b>not bold</b>" in dashboard.event_view.toPlainText()


# ---------------------------------------------------------------------------
# Lifecycle and focus
# ---------------------------------------------------------------------------

def test_closing_the_window_unsubscribes_before_closing_the_controller(
        dashboard, qapp, monkeypatch):
    from PySide6.QtGui import QCloseEvent
    # Updated (L9): the window's close asks first; answered Quit here.
    monkeypatch.setattr(qt, "ask", lambda *args, **kwargs: True)
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
    # Updated (L8): a table row's dropdowns are a little narrower (they
    # elide the middle), so Setup's four columns fit a 900 px window.
    assert {c.minimumContentsLength() for c in combos} == {qt.DROPDOWN_ROW_CHARS}
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
# A tall panel stacks (was: two columns when one would run off the screen)
# ---------------------------------------------------------------------------

def test_a_tall_panel_stacks_its_sections_in_schema_order_with_safety_last(tall_view):
    """Updated (E; was "a tall panel splits into two columns"): the sheet
    scrolls as one page and tier 2 keeps a tall model's detail out of sight
    until asked for, so a panel no longer splits itself into columns; its
    sections stack in schema order, Safety the last."""
    frames = [f for f in tall_view.findChildren(QFrame) if f.objectName() == "section"]
    layout = tall_view._tier_layouts[1]
    order = []
    for element in tall_view._elements:
        widget = tall_view._widget_for(element)
        frame = next(f for f in frames if f.isAncestorOf(widget))
        order.append(layout.indexOf(frame))
    assert order == sorted(order) and len(set(order)) == len(TallPanel.SECTIONS)
    assert tall_view._elements[-1]["model_attr"] == "safety"


def test_row_sections_are_one_table_however_many_there_are(qapp, monkeypatch):
    """Ten row sections share a single table (Setup's shape). Updated (E): a
    panel has no columns to split any more; the claim left is one table."""
    panel = TablePanel()
    schema = sch.schema(*[
        sch.section(f"Row {index}", sch.readonly("Port", "scan_status"),
                    layout="row")
        for index in range(10)])
    monkeypatch.setattr(type(panel), "schema", property(lambda self: schema))
    built = qt.QtPanelView(FakeController(panel), "Table")
    try:
        assert built._table is not None
        tables = [f for f in built.findChildren(QFrame) if f.objectName() == "table"]
        assert len(tables) == 1
    finally:
        built.close()


# ---------------------------------------------------------------------------
# The window's shape (E): the rail on the left, the sheet beside it
#
# Was "no column is spent on chrome": the Bench sheet brings back a left rail
# on purpose - the stop disc and the model list - and the entries share the
# sheet's width equally.
# ---------------------------------------------------------------------------

def test_the_only_docks_are_the_rail_and_setup(dashboard, qapp):
    """Updated (E): the rail is a fixed left dock carrying the model list the
    brief asks for; the models themselves are entries on the sheet, so no
    dock is spent on a model and there is no second list of them."""
    from PySide6.QtWidgets import QDockWidget
    dashboard.open()
    docks = {d.objectName() for d in dashboard.findChildren(QDockWidget)}
    assert docks == {"railDock", "setupDock"}
    assert (dashboard.dockWidgetArea(dashboard.rail_dock)
            == Qt.DockWidgetArea.LeftDockWidgetArea)
    assert (dashboard.rail_dock.features()
            == QDockWidget.DockWidgetFeature.NoDockWidgetFeatures)


def test_entries_in_a_row_share_the_width_equally(qapp):
    """Updated (E; was "side by side docks are sized by what they hold"): a
    row of entries is equal columns, none under its own minimum."""
    controller = FakeController(FakePanel())
    controller.open_names = ["One", "Two", "Three"]
    controller.closed = []
    window = qt.QtDashboard(controller, controller.panel)
    try:
        window.open()
        window.resize(1400, 900)
        for _ in range(5):
            qapp.processEvents()
        window._arrange_entries()
        for _ in range(5):
            qapp.processEvents()
        rows = window._arrangement
        # Updated (K4): the overview is the grid alone, no leading row.
        assert rows[0] == ("One", "Two", "Three")
        side = [window._entries[n] for n in rows[0]]
        widths = [e.width() for e in side]
        assert max(widths) - min(widths) <= 2
        for entry in window._entries.values():
            assert entry.width() >= entry.minimumSizeHint().width()
    finally:
        window.close()
        events.unsubscribe(window._on_event)


def test_the_sheet_scrolls_rather_than_pushing_the_window_off_screen(dashboard):
    """Updated (E): one scroll for the whole sheet, not one per dock."""
    entry = dashboard._add_panel("Fake")
    assert dashboard.sheet_scroll.isAncestorOf(entry)
    assert entry.panel is dashboard._panels["Fake"]
    assert dashboard.minimumSizeHint().width() < 800


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
    """Updated (F8): it asserted the toggle's role fills, which made a lit
    lamp 1.50:1 and an unlit one 1.23:1 on the card. Lamps now use Tk's rule
    (`qt.lamp_colours`): a Fault lamp is a muted ring off, signal when on."""
    element = element_of(view, "indicator")
    lamp = view._widget_for(element)
    view._refresh()
    assert f"border: 2px solid {theme.MUTED}" in lamp.styleSheet()
    panel.is_faulted = True
    view._refresh()
    assert f"background-color: {theme.SIGNAL}" in lamp.styleSheet()
    assert lamp.text() == ""


def test_a_log_stream_stays_a_few_scrollable_lines(view):
    """Left to expand, the stepper's Gamepad Log took a third of the panel and
    pushed the Safety section off the bottom of the dock."""
    stream = view._widget_for(element_of(view, "log_stream"))
    assert stream.height() == qt.LOG_STREAM_PX
    assert stream.maximumHeight() == qt.LOG_STREAM_PX
    view._refresh()
    assert stream.toPlainText() == "first\nsecond"


def test_a_models_own_stop_is_a_small_switch_not_a_second_disc(table_view):
    """Updated (E): a model's own stop was the stop disc one size down; the
    Bench sheet makes it a small switch (tier 3) - the rail's disc is the
    stop an operator reaches for, and two red discs would be two stops. Its
    face is the schema's words; its track is red only when latched."""
    element = next(e for e in table_view._elements
                   if e["type"] == "toggle" and e.get("on_role") == "danger")
    switch = table_view._widget_for(element)
    assert isinstance(switch, qt.SwitchButton)
    assert not isinstance(switch, qt.StopButton)
    table_view._refresh()
    assert switch.text() == "Full stop" and switch.is_on is False
    table_view._set_on(element, True)
    assert switch.is_on is True
    assert switch.text() == "Latched - click to clear"
    switch.resize(switch.sizeHint())
    track = switch._track()
    colour = _pixel(switch, int(track.left()) + 3, int(track.center().y()))
    assert _near(colour, theme.SWITCH["on_fill"], 60)


def test_an_ordinary_toggle_is_a_button_with_its_state_dot(view):
    """Only a danger toggle is a stop; everything else is a button whose face
    says what a press does, with a filled or hollow dot for its state."""
    button = view._widget_for(element_of(view, "toggle"))
    assert not isinstance(button, (qt.StopButton, qt.SwitchButton))
    view._refresh()
    assert not button.icon().isNull()


def test_a_section_is_not_a_card(table_view):
    """Updated (E): entries, not cards - a section is a transparent frame with
    its refusal line; alignment and whitespace do the grouping."""
    sections = [c for c in table_view.findChildren(QFrame)
                if c.objectName() == "section"]
    assert len(sections) >= 2
    assert not [c for c in table_view.findChildren(QFrame)
                if c.objectName() == "card"]


def test_the_stop_disc_is_the_themes_diameter_and_narrows_under_1000_px(
        dashboard, qapp):
    """Updated (E): A's disc at `theme.STOP["diameter"]` in the 248 px rail,
    `diameter_narrow` in the 200 px rail under 1000 px wide (was: a disc sized
    in lines of the base font, capped at 96 px). The window is shown first:
    Qt holds a hidden widget's resize until it shows."""
    button = dashboard.stop_button
    dashboard.open()
    dashboard.resize(1400, 900)
    qapp.processEvents()
    assert button.width() == button.height()
    assert button.diameter == theme.STOP["diameter"]
    assert dashboard.rail.width() == qt.RAIL_PX
    dashboard.resize(900, 900)
    qapp.processEvents()
    assert button.diameter == theme.STOP["diameter_narrow"]
    assert dashboard.rail.width() == qt.RAIL_NARROW_PX


def test_the_event_log_keeps_only_its_tail(dashboard):
    """A window that runs a whole bench session cannot hold every line.
    Updated (E): only warnings and errors reach the tray, and one event is
    shown once, so each spoof is a warning with its own line."""
    class Spoof:
        severity, source, title, message, count = "warning", "T", "t", "m", 1
        needs_ack = False
        text = "a line"

    assert dashboard.event_view.document().maximumBlockCount() == qt.EVENT_LOG_LINES
    for number in range(qt.EVENT_LOG_LINES + 25):
        spoof = Spoof()
        spoof.text = f"a line {number}"
        dashboard._show_event(spoof)
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


def test_the_collapsed_setup_dock_comes_back_from_the_rail(fresh_dashboard, qapp,
                                                              controller):
    """It has to stay reopenable: Refresh and Relaunch are mid-session jobs."""
    dashboard = fresh_dashboard
    dashboard.open()
    controller.notify("added", "Fake")
    qapp.processEvents()
    action = dashboard.setup_action
    assert action.isCheckable() is True
    # Qt *disables* a dock's toggleViewAction unless the dock is closable, so
    # this is the assertion that catches a dead Setup button on the rail.
    assert action.isEnabled() is True
    assert action.isChecked() is False
    action.trigger()
    assert dashboard._setup_dock.isHidden() is False
    assert action.isChecked() is True


def test_the_setup_panel_is_put_away_not_torn_down(fresh_dashboard, qapp, controller):
    """Its scan results and its selections are still there when it comes back,
    which they would not be if the panel behind it had been destroyed.
    Updated (E): the panel is `setup_view`, inside the dock's scroll area."""
    dashboard = fresh_dashboard
    dashboard.open()
    panel_view = dashboard.setup_view
    assert not dashboard._setup_dock.testAttribute(
        Qt.WidgetAttribute.WA_DeleteOnClose)
    controller.notify("added", "Fake")
    qapp.processEvents()
    dashboard.show_setup()
    assert dashboard.setup_view is panel_view
    assert dashboard._setup_dock.widget().widget() is panel_view
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


def test_there_is_one_navigation_not_two(dashboard, qapp):
    """Replaces the toolbar test. The one way back to Setup is the rail's
    Setup, bound to the dock's own action. Updated (E): the rail is the left
    dock (was: the menu widget across the top), its model list the one
    navigation - no toolbar, no tabs."""
    from PySide6.QtWidgets import QTabBar, QToolBar
    dashboard.open()
    assert dashboard.findChildren(QToolBar) == []
    assert [t for t in dashboard.findChildren(QTabBar) if t.isVisible()] == []
    assert dashboard.setup_button.defaultAction() is dashboard.setup_action
    assert dashboard.setup_button.isHidden() is False
    assert dashboard.rail_dock.widget() is dashboard.rail
    assert dashboard.rail.isAncestorOf(dashboard.quit_button)


def test_the_stop_object_is_on_the_rail_and_the_rail_above_every_dock(
        dashboard, qapp):
    dashboard.open()
    assert dashboard.stop_button.parentWidget() is dashboard.rail
    assert not dashboard.findChildren(qt.QDockWidget, "sidebar")


def test_the_stop_object_is_never_dimmed(dashboard):
    dashboard.stop_button.setEnabled(False)
    assert dashboard.stop_button.isEnabled() is True


def test_the_stop_pulses_once_on_the_edge_and_not_while_latched(dashboard,
                                                                controller):
    button = dashboard.stop_button
    started = []
    button._pulse.stateChanged.connect(
        lambda new, old: started.append(new) if new == button._pulse.State.Running else None)
    controller.is_estopped = True
    dashboard._sync_stop_button()
    dashboard._sync_stop_button()      # still latched: no second pulse
    assert len(started) == 1
    controller.is_estopped = False
    dashboard._sync_stop_button()
    assert button.text() == "Stop"


# ---------------------------------------------------------------------------
# The empty state and the event tray
# ---------------------------------------------------------------------------

def test_an_empty_window_says_what_to_do_next(fresh_dashboard, qapp,
                                              controller):
    dashboard = fresh_dashboard
    dashboard.open()
    assert dashboard.empty_state.isHidden() is False
    texts = [w.text() for w in dashboard.empty_state.findChildren(QLabel)]
    assert qt.EMPTY_TITLE in texts and qt.EMPTY_HINT in texts
    controller.notify("added", "Fake")
    qapp.processEvents()
    assert dashboard.empty_state.isHidden() is True


def test_the_empty_state_offers_setup_when_setup_is_away(fresh_dashboard, qapp):
    dashboard = fresh_dashboard
    dashboard.open()
    assert dashboard.empty_setup_button.isHidden() is True
    dashboard._setup_dock.close()
    assert dashboard.empty_setup_button.isHidden() is False
    dashboard.empty_setup_button.click()
    assert dashboard._setup_dock.isHidden() is False


def test_the_event_tray_opens_as_one_line(dashboard, qapp):
    # Updated (L11): title and message in sentence case, no "[Setup]".
    class Spoof:
        severity, source, title, message, count = (
            "warning", "Setup", "Port Silent", "a port answered nothing", 1)
        needs_ack = False
        text = "[Setup] Port Silent: a port answered nothing"

    dashboard.open()
    assert dashboard.event_view.isHidden() is True
    dashboard._show_event(Spoof())
    assert dashboard.event_latest.full_text() == (
        "Warning  Port silent: a port answered nothing")
    assert dashboard.event_latest.toolTip() == dashboard.event_latest.full_text()
    dashboard.tray_toggle.click()
    assert dashboard.event_view.isHidden() is False
    assert dashboard.tray_toggle.text() == "Hide events"
    dashboard.tray_toggle.click()
    assert dashboard.event_view.isHidden() is True


def test_an_info_event_is_readable_not_panel_grey(dashboard):
    """`info` was drawn in the info role's fill, a panel grey. Updated (E): a
    warning is ink now - the trace is for changing numbers only."""
    assert dashboard._severity_colour("info") == theme.MUTED
    assert dashboard._severity_colour("warning") == theme.SEVERITY_INK["warning"]
    assert theme.SEVERITY_INK["warning"] == theme.TEXT


# ---------------------------------------------------------------------------
# Labels and readouts
# ---------------------------------------------------------------------------

def test_a_label_is_rendered_in_sentence_case_without_its_colon(view):
    captions = [w.text() for w in view.findChildren(QLabel)
                if w.objectName() == "caption"]
    assert "Speed" in captions and "Speed:" not in captions


def test_a_readout_at_rest_is_quiet_and_a_live_one_is_not(view, panel):
    element = element_named(view, "reading")
    label = view._widget_for(element)
    view._refresh()
    assert label.property("quiet") == "false"
    panel.reading = "off"
    view._refresh()
    assert label.property("quiet") == "true"
    panel.reading = ""
    view._refresh()
    assert label.text() == qt.EMPTY_READOUT


def test_a_rescan_is_a_quiet_icon_with_a_tooltip(view):
    from PySide6.QtWidgets import QPushButton
    icons = [b for b in view.findChildren(QPushButton)
             if b.objectName() == "iconButton"]
    assert icons and all(b.text() == "" and b.toolTip() for b in icons)
    assert not icons[0].icon().isNull()


# ---------------------------------------------------------------------------
# Action lines in the table (Setup's Devices and Launch rows)
# ---------------------------------------------------------------------------

class ActionTablePanel(TablePanel):
    """Setup's real shape: an action line, model rows, an action line."""

    NAME = "ActionTable"

    def __init__(self):
        super().__init__()
        self.summary = "nothing selected"

    @property
    def schema(self):
        sections = [sch.section(
            "Devices", sch.button("Refresh", "refresh", role="info"),
            sch.readonly("Scan:", "scan_status"), layout="row")]
        for name, key, needs_port, needs_gamepad in self.ROWS:
            elements = []
            if needs_port:
                elements.append(sch.dropdown("Port", f"{key}_port",
                                             f"set_{key}_port", "port_options"))
            if needs_gamepad:
                elements.append(sch.dropdown("Gamepad", f"{key}_gamepad",
                                             f"set_{key}_gamepad",
                                             "gamepad_options"))
            elements.append(sch.readonly("Status:", f"{key}_found"))
            sections.append(sch.section(name, *elements, layout="row"))
        sections.append(sch.section(
            "Launch", sch.readonly("Selected:", "summary"),
            sch.button("Launch", "launch", role="go"), layout="row"))
        return sch.schema(*sections)


@pytest.fixture
def action_view(qapp):
    built = qt.QtPanelView(FakeController(ActionTablePanel()), "ActionTable")
    yield built
    built.close()


def test_an_action_line_invents_no_column(action_view):
    """The scan message sat in a "Scan" column and the summary in a
    "Selected" column that every model row left empty."""
    table = action_view._table
    grid = table.grid
    headers = [grid.itemAtPosition(table.header_row, c).widget().text()
               for c in range(1, grid.columnCount())
               if grid.itemAtPosition(table.header_row, c) is not None]
    assert headers == ["Port", "Gamepad", "Status"]


def test_the_header_sits_under_the_devices_line_and_over_the_first_model(
        action_view):
    table = action_view._table
    grid = table.grid
    scan = action_view._widget_for(element_named(action_view, "scan_status"))
    first = action_view._widget_for(element_named(action_view, "stepper_port"))
    last = action_view._widget_for(element_named(action_view, "red_found"))
    summary = action_view._widget_for(element_named(action_view, "summary"))
    scan_row = cell_of(grid, scan)[0]
    assert table.header_row == scan_row + 1
    assert cell_of(grid, first)[0] == table.header_row + 1
    assert cell_of(grid, summary)[0] > cell_of(grid, last)[0]


def test_an_action_line_spans_the_table_and_wraps_its_message(action_view):
    grid = action_view._table.grid
    scan = action_view._widget_for(element_named(action_view, "scan_status"))
    index = -1
    widget = scan
    while index < 0 and widget is not None:
        index = grid.indexOf(widget)
        widget = widget.parentWidget()
    _, column, _, span = grid.getItemPosition(index)
    assert column == 0 and span == grid.columnCount()
    assert scan.wordWrap() is True



# ---------------------------------------------------------------------------
# Tier F (UI audit round, 2026-09-24). Each of these fails on 77ac52d.
# ---------------------------------------------------------------------------

class RailPanel(Panel):
    """Three position readouts on the rail, like a probe."""

    NAME = "Rail"

    def __init__(self):
        super().__init__()
        self.x_position = "12345.678"
        self.y_position = "0.00"
        self.z_position = "-9876.5"
        self.is_on = False
        self.refuse = False

    @property
    def schema(self):
        return sch.schema(
            sch.section("Coordinate frame",
                        sch.readonly("X Position:", "x_position", rail=True),
                        sch.readonly("Y Position:", "y_position", rail=True),
                        sch.readonly("Z Position:", "z_position", rail=True)),
            *[sch.section(f"Section {n}", sch.readonly("Value:", "x_position"))
              for n in range(8)],
            sch.section("Last", sch.button("Go", "go")))

    def go(self):
        if self.refuse:
            raise Refused("the stage is not homed")
        return None


@pytest.fixture
def restore_font(qapp):
    original = theme.FONT_SIZE
    yield
    theme.set_font_size(original)
    qapp.setStyleSheet(qt.stylesheet())


def rail_dashboard(qapp, names, width, height):
    controller = FakeController(RailPanel())
    controller.open_names = list(names)
    controller.closed = []
    window = qt.QtDashboard(controller, controller.panel)
    window.open()
    window.resize(width, height)
    for _ in range(5):
        qapp.processEvents()
    window._on_rail_tick()
    for _ in range(5):
        qapp.processEvents()
    return window


def clipped_numbers(window):
    """Every visible number on the sheet drawn narrower than its own text -
    the desktop auditor's measure (AUD-1). Updated (E): the numbers live in
    the sheet's entries (`reading`), not on the rail; elided readouts carry
    their whole value in a tooltip and are not numbers that can be cut."""
    clipped = []
    for label in window.findChildren(QLabel):
        if label.objectName() != "reading" or not label.isVisible():
            continue
        need = label.fontMetrics().horizontalAdvance(label.text())
        have = label.contentsRect().width()
        if need > have + 1:
            clipped.append((label.text(), have, need))
    return clipped


@pytest.mark.parametrize("font, names, width", [
    (12, ["Stepper Probe", "DC Probe", "Temperature Controller", "Red Percent"], 1000),
    (28, ["Stepper Probe", "Red Percent"], 1000),
])
def test_f5_the_sheet_never_clips_a_number(qapp, restore_font, font, names,
                                           width):
    """Updated (E; was "the rail never clips a number"): a number is never
    drawn narrower than itself - an entry flows it to the next line, the
    sheet takes fewer columns - and the sheet never scrolls sideways; the
    stop stays inside the window."""
    theme.set_font_size(font)
    window = rail_dashboard(qapp, names, width, 700)
    try:
        assert clipped_numbers(window) == []
        assert window.sheet_scroll.horizontalScrollBar().maximum() == 0
        for name in names:
            panel = window._panels[name]
            shown = [panel._widget_for(e) for e in panel._elements
                     if panel._kinds.get(id(e))]
            assert shown and all(v.text() in ("12345.678", "0.00", "-9876.5")
                                 for v in shown)
        stop = window.stop_button
        top_left = stop.mapTo(window, QPoint(0, 0))
        assert top_left.x() + stop.width() <= window.width()
        assert top_left.y() + stop.height() <= window.height()
    finally:
        window._closing = False
        window.close()
        events.unsubscribe(window._on_event)


def test_f5_a_narrow_entry_wraps_its_numbers_rather_than_cutting_one(
        qapp, restore_font):
    """Updated (E; was "past two lines a model shows fewer rail readouts"):
    every reading is always drawn, whole; at 28 pt in a 1000 px window the
    entries stack one to a row and a line of numbers wraps."""
    theme.set_font_size(28)
    window = rail_dashboard(qapp, ["A", "B"], 1000, 700)
    try:
        assert window._arrangement == (("A",), ("B",))
        panel = window._panels["A"]
        tops = {panel._widget_for(e).mapTo(panel, QPoint(0, 0)).y()
                for e in panel._elements if panel._kinds.get(id(e))}
        assert len(tops) >= 1
        assert clipped_numbers(window) == []
    finally:
        window._closing = False
        window.close()
        events.unsubscribe(window._on_event)


def test_f25_the_disc_never_outgrows_the_rail_at_28_pt_and_its_face_fits(
        qapp, restore_font):
    """Updated (E; was "at most 96 px"): the disc is `theme.STOP`'s diameter
    at every launch font - it does not grow with the text, so at 28 pt the
    rail keeps room for the model list - and "Clear" fits inside the face."""
    theme.set_font_size(28)
    button = qt.StopButton()
    assert button.diameter == theme.STOP["diameter"]
    assert button.width() == button.height()
    button.set_latched(True)
    room = button.face_rect().width()
    assert QFontMetrics(qt.numeral_font(button.face_size(), 700)).horizontalAdvance(
        "Clear") <= room


# -- F1: errors never block the stop -----------------------------------------

def test_f1_after_many_failures_the_stop_is_clickable_and_nothing_is_modal(
        dashboard, qapp, controller, monkeypatch):
    raised = []
    monkeypatch.setattr(QMessageBox, "exec", lambda self: raised.append(1))
    monkeypatch.setattr(QDialog, "exec", lambda self: raised.append(1))
    dashboard.open()
    for n in range(20):
        events.error("Command Failed", f"failure {n}", source="Test")
    qapp.processEvents()
    assert raised == []
    assert QApplication.activeModalWidget() is None
    assert dashboard.stop_button.isEnabled() is True
    dashboard.stop_button.click()
    assert controller.estop_calls == 1
    # The errors queue, oldest first; none overwrote another.
    assert len(dashboard.alerts) == 20
    assert "failure 0" in dashboard.alert_text.full_text()
    assert dashboard.alert_count.text() == "1 of 20"
    dashboard.acknowledge()
    assert "failure 1" in dashboard.alert_text.full_text()
    dashboard.acknowledge_all()
    assert dashboard.alert_band.isHidden() is True


def test_f1_f17_a_question_leaves_the_stop_clickable_and_defaults_to_no(
        dashboard, qapp, controller, monkeypatch):
    """`QMessageBox.question` was application-modal: while it was up the
    rail's stop could not be pressed. The stop now answers the open question
    No and then stops."""
    def modal(*_args, **_kwargs):
        raise AssertionError("a modal question blocks the stop")
    monkeypatch.setattr(QMessageBox, "question", modal)
    monkeypatch.setattr(QMessageBox, "exec", modal)
    dashboard.open()
    seen = {}

    def while_asking():
        pending = getattr(qt, "_PENDING_CONFIRMS", [])
        box = pending[0] if pending else None
        seen["modal"] = QApplication.activeModalWidget()
        if box is not None:
            no = box.button(QMessageBox.StandardButton.No)
            seen["default_is_no"] = box.defaultButton() is no
            seen["escape_is_no"] = box.escapeButton() is no
        dashboard.stop_button.click()
    QTimer.singleShot(0, while_asking)
    answer = dashboard._confirm("Clear the stop?")
    assert seen["modal"] is None
    assert seen["default_is_no"] and seen["escape_is_no"]
    assert controller.estop_calls == 1
    assert answer is False
    assert qt._PENDING_CONFIRMS == []


# -- F8: lamps -------------------------------------------------------------

def test_f8_lamps_are_visible_and_named(view, panel):
    element = element_of(view, "indicator")
    lamp = view._widget_for(element)
    view._refresh()
    assert lamp.accessibleName() == "Fault: off"
    panel.is_faulted = True
    view._refresh()
    assert lamp.accessibleName() == "Fault: on"


def test_f8_a_disconnected_stage_is_an_ink_ring_not_a_second_red():
    """Updated (E): a lit lamp is ink, not the trace (the trace is for
    changing numbers only)."""
    connected = sch.indicator("Stage connected", "is_connected",
                              on_role="go", off_role="danger")
    assert qt.lamp_colours(connected, True) == (theme.TEXT, theme.TEXT)
    fill, ring = qt.lamp_colours(connected, False)
    assert ring == theme.TEXT and theme.SIGNAL not in (fill, ring)
    fault = sch.indicator("Fault", "is_faulted")
    assert qt.lamp_colours(fault, True) == (theme.SIGNAL, theme.SIGNAL)
    assert qt.lamp_colours(fault, False)[1] == theme.MUTED


# -- F9: the keyboard path to the stop ----------------------------------------

@pytest.mark.parametrize("key", [Qt.Key.Key_Return, Qt.Key.Key_Enter,
                                 Qt.Key.Key_Space])
def test_f9_return_enter_and_space_all_press_the_stop(qapp, key):
    from PySide6.QtTest import QTest
    button = qt.StopButton()
    pressed = []
    button.clicked.connect(lambda: pressed.append(1))
    QTest.keyClick(button, key)
    assert pressed == [1]


def test_f9_the_latched_stop_shows_focus_in_ink(qapp, monkeypatch):
    button = qt.StopButton()
    button.set_latched(True)
    button._pulse.stop()
    button._dress()
    unfocused = button.grab().toImage()
    monkeypatch.setattr(button, "hasFocus", lambda: True)
    focused = button.grab().toImage()
    assert focused != unfocused
    edge = QColor(focused.pixel(1, button.height() // 2))
    ink = QColor(theme.STOP_FOCUS)
    assert abs(edge.red() - ink.red()) + abs(edge.green() - ink.green()) < 90


def test_f9_g5_one_global_shortcut_control_period_stops_and_never_clears(
        dashboard, controller):
    """G5 (owner ruling 2026-09-25): Ctrl+. is the one chord, the physical
    Control key on every OS; the Cmd+. chord F9 added on macOS is gone. Qt
    spells the macOS Control key "Meta", so the binding is Qt's Meta there -
    and Qt's Ctrl (the Command key on macOS) is bound nowhere. Named on the
    face's tooltip and the rail's hint as "Ctrl+.", never as a glyph."""
    from PySide6.QtCore import QKeyCombination
    from PySide6.QtGui import QShortcut
    shortcuts = dashboard.findChildren(QShortcut)
    stops = [s for s in shortcuts if not s.key().isEmpty()
             and s.key()[0].key() == Qt.Key.Key_Period]
    assert stops == [dashboard.stop_shortcut]
    combo = dashboard.stop_shortcut.key()[0]
    control = (Qt.KeyboardModifier.MetaModifier if sys.platform == "darwin"
               else Qt.KeyboardModifier.ControlModifier)
    assert combo.keyboardModifiers() == control
    assert dashboard.stop_shortcut.context() == Qt.ShortcutContext.ApplicationShortcut
    dashboard._sync_stop_button()
    hint = "Stop every model (Ctrl+.)"
    assert dashboard.stop_button.toolTip().startswith(hint)
    # Updated (E): the line under the disc is the artboard's "Stop: Ctrl+.".
    assert dashboard.stop_hint.full_text() == "Stop: Ctrl+."
    for text in (dashboard.stop_button.toolTip(), dashboard.stop_hint.full_text()):
        assert not any(mark in text for mark in ("\u2318", "\u2303", "Cmd", "Meta"))
    # Matched as a key sequence, not by a synthesised key press: every
    # dashboard an earlier test left alive holds an application-wide stop
    # shortcut too, and two of them make a real press ambiguous (neither
    # fires) - a harness artefact, since the app has one window.
    key = dashboard.stop_shortcut.key()
    pressed = QKeySequence(QKeyCombination(control, Qt.Key.Key_Period))
    assert key.matches(pressed) == QKeySequence.SequenceMatch.ExactMatch
    # The other modifier (Command on macOS, the Windows/Super key elsewhere)
    # is not the stop.
    other = (Qt.KeyboardModifier.ControlModifier if sys.platform == "darwin"
             else Qt.KeyboardModifier.MetaModifier)
    wrong = QKeySequence(QKeyCombination(other, Qt.Key.Key_Period))
    assert key.matches(wrong) == QKeySequence.SequenceMatch.NoMatch
    dashboard.stop_shortcut.activated.emit()
    assert controller.estop_calls == 1 and controller.is_estopped
    dashboard.stop_shortcut.activated.emit()      # latched: it does not clear
    assert controller.is_estopped is True and controller.estop_calls == 1
    dashboard._sync_stop_button()
    assert dashboard.stop_button.accessibleName() == "Clear the stop on every model"


# -- F3: a lost device ---------------------------------------------------------

def test_f3_a_lost_port_is_said_on_the_panel_the_entry_and_the_rail(
        dashboard, qapp, controller, monkeypatch):
    """Updated (E): the dock's bar became the entry's head, and the rail's
    readout group (gone) its status line."""
    live = controller.state

    def lost(name=None):
        snapshot = live(name)
        snapshot["devices"] = {"SerialPort": "lost"}
        return snapshot
    monkeypatch.setattr(controller, "state", lost)
    dashboard.open()
    panel_view = dashboard._panels["Fake"]
    panel_view._refresh()
    dashboard._on_rail_tick()
    assert panel_view.lost_devices == ["serial port"]
    assert panel_view.notice.isVisibleTo(panel_view)
    assert "Fake lost its serial port" in panel_view.notice.text()
    assert panel_view.property("stale") == "true"
    entry = dashboard._entries["Fake"]
    assert entry.lost_label.isVisibleTo(entry)
    assert entry.lost_label.text() == "Serial port lost"
    assert "Fake lost its serial port" in dashboard.rail_status.full_text()


def test_f3_stale_readouts_really_are_repolished(view, panel):
    """The stale rule is a descendant selector; polishing the panel alone
    never re-read it for the labels. Updated (E): a readout is ink at rest
    and the trace only while it changes, so the value changes first."""
    label = view._widget_for(element_named(view, "reading"))
    view._refresh()
    panel.reading = "2.468"
    view._refresh()
    live = label.palette().color(label.foregroundRole()).name()
    view._set_stale(True)
    dim = label.palette().color(label.foregroundRole()).name()
    assert live.lower() == theme.TRACE.lower()
    assert dim.lower() == theme.MUTED.lower()


# -- F10: a refusal at the control -----------------------------------------------

def test_f10_a_refusal_lands_in_the_card_of_the_control_and_scrolls_into_view(
        qapp):
    panel = RailPanel()
    panel.refuse = True
    view = qt.QtPanelView(FakeController(panel), "Rail")
    scroll = qt.QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setWidget(view)
    scroll.resize(500, 240)
    scroll.show()
    qapp.processEvents()
    try:
        button = view._widget_for(next(e for e in view._elements
                                       if e.get("command") == "go"))
        view._run(next(e for e in view._elements if e.get("command") == "go"))
        for _ in range(3):
            qapp.processEvents()
        line = view.status_label
        assert "not homed" in line.text()
        section = line.parentWidget()       # updated (E): a section, not a card
        assert section.objectName() == "section" and section.isAncestorOf(button)
        assert scroll.verticalScrollBar().value() > 0
        top = line.mapTo(scroll.viewport(), QPoint(0, 0)).y()
        assert 0 <= top <= scroll.viewport().height()
        panel.refuse = False
        view._run(next(e for e in view._elements if e.get("command") == "go"))
        assert line.isHidden() and view.status_label.text() == ""
    finally:
        view.close()
        scroll.close()


# -- F14: severity is a mark and a word, the text stays legible ---------------------

def test_f14_an_error_line_is_ink_with_a_signal_mark_beside_the_word(dashboard):
    class Spoof:
        severity, source, title, message, count = "error", "T", "t", "m", 1
        needs_ack = False
        text = "[T] t: the heater did not answer"

    dashboard._show_event(Spoof())
    page = dashboard.event_view.toHtml().lower()
    assert f"background-color:{theme.SEVERITY_MARK['error']}".lower() in page
    assert f"color:{theme.SEVERITY_INK['error']}".lower() in page
    assert f"color:{theme.SIGNAL}".lower() not in page.replace(
        "background-color", "")
    assert "Error" in dashboard.event_view.toPlainText()
    assert dashboard.event_latest.property("severity") == "error"


# -- F15: long names elide from the middle ----------------------------------

def test_f15_a_long_port_name_keeps_the_end_that_tells_ports_apart(view, panel):
    combo = view._widget_for(element_of(view, "dropdown"))
    long_name = "/dev/cu.usbmodem1234567890ABCDEF4401"
    view._set_combo_text(combo, long_name)
    combo.resize(160, combo.height())
    shown = combo.shown_text()
    assert "…" in shown and shown.endswith("4401")
    assert shown.startswith("/dev")
    assert combo.toolTip() == long_name
    index = combo.findText(long_name)
    assert combo.itemData(index, Qt.ItemDataRole.ToolTipRole) == long_name


def test_f15_a_long_run_id_elides_rather_than_pushing_the_column(view, panel):
    label = view._widget_for(element_named(view, "reading"))
    run_id = "run_20260924_104848_" + "x" * 40
    panel.reading = run_id
    view._refresh()
    cap = label.fontMetrics().averageCharWidth() * qt.READOUT_MAX_CHARS
    assert label.sizeHint().width() <= cap
    assert label.text() == run_id               # the value itself is whole
    label.resize(200, label.height())
    assert "…" in label.shown_text()
    assert label.toolTip() == run_id


# -- F21: no restyle when nothing changed; a hidden panel does not tick -------

def test_f21_an_idle_tick_restyles_nothing(dashboard, qapp, monkeypatch):
    from PySide6.QtWidgets import QWidget
    dashboard.open()
    panel_view = dashboard._panels["Fake"]
    panel_view._refresh()
    dashboard._on_rail_tick()
    calls = []
    real = QWidget.setStyleSheet
    monkeypatch.setattr(QWidget, "setStyleSheet",
                        lambda self, sheet: (calls.append(type(self).__name__),
                                             real(self, sheet)))
    for _ in range(5):
        panel_view._refresh()
        dashboard._on_rail_tick()
    assert calls == []


def test_f21_the_hidden_setup_panel_stops_ticking(fresh_dashboard, qapp,
                                                  controller):
    dashboard = fresh_dashboard
    dashboard.open()
    setup_view = dashboard.setup_view           # updated (E): inside a scroll
    assert setup_view._timer.isActive() is True
    controller.notify("added", "Fake")
    qapp.processEvents()
    assert dashboard._setup_dock.isHidden() is True
    assert setup_view._timer.isActive() is False
    dashboard.show_setup()
    assert setup_view._timer.isActive() is True


# -- F22: many models become tabs, not slivers --------------------------------------

def test_f22_six_models_are_one_full_row_then_rows_of_three_and_two(
        dashboard, controller, qapp):
    """Updated (E; was "the fourth model onward are tabs in the last
    column"): no tabs - the opened model full width, then rows of at most
    three, the earlier rows the fuller, as many columns as fit."""
    controller.open_names = ["One", "Two", "Three", "Four", "Five", "Six"]
    dashboard.open()
    dashboard.resize(4000, 900)
    for _ in range(5):
        qapp.processEvents()
    dashboard._arrange_entries()
    rows = dashboard._arrangement
    # Updated (K4): the overview has no leading row; a press shows one
    # model alone (its device page) rather than lifting it to the top.
    assert rows == (("One", "Two", "Three"), ("Four", "Five", "Six"))
    assert all(len(row) <= qt.MAX_SHEET_COLUMNS for row in rows)
    assert [n for row in rows for n in row] == controller.open_names
    dashboard.open_entry("Four")
    assert dashboard._arrangement == (("Four",),)
    assert dashboard._rail_items["Four"].isChecked()


# -- F25: targets, focus, reopen, motion --------------------------------------------

def test_f25_the_rescan_and_the_entry_close_are_at_least_24_px_and_grow(
        qapp, controller, restore_font):
    sizes = {}
    for font in (12, 28):
        theme.set_font_size(font)
        view = qt.QtPanelView(controller, "Fake")
        rescan = next(b for b in view.findChildren(qt.QPushButton)
                      if b.objectName() == "iconButton")
        entry = qt.SheetEntry("Fake")      # updated (E): the dock's bar
        close = entry.close_button         # became the entry's head
        sizes[font] = (rescan.width(), rescan.height(), close.width(),
                       close.height())
        assert min(sizes[font]) >= 24
        assert close.accessibleName() == "Close Fake"
        view.close()
        entry.deleteLater()
    assert min(sizes[28]) > min(sizes[12])


def test_f25_a_reopened_model_is_brought_forward(dashboard, qapp, controller,
                                                 monkeypatch):
    raised = []
    # Updated (E): an entry, named by `name` (a dock by its window title).
    monkeypatch.setattr(type(dashboard), "_bring_forward",
                        lambda self, entry: raised.append(entry.name))
    dashboard.open()
    dashboard._sync_rail()
    dashboard.reopen_buttons["Gone"].click()
    controller.notify("added", "Gone")
    qapp.processEvents()
    assert raised == ["Gone"]


def test_f25_no_motion_turns_the_pulse_off(qapp, monkeypatch):
    monkeypatch.setenv("STATION_NO_MOTION", "1")
    button = qt.StopButton()
    button.set_latched(True)
    assert button._pulse.state() != button._pulse.State.Running
    assert button.text() == "Clear"


def test_f25_the_sheet_scroll_area_shows_focus(dashboard):
    """Updated (E): one scroll area for the sheet (was one per dock)."""
    dashboard._add_panel("Fake")
    assert dashboard.sheet_scroll.objectName() == "sheetScroll"
    sheet = qt.stylesheet()
    rule = sheet.split("QScrollArea#sheetScroll:focus")[1].split("}")[0]
    assert qt.FOCUS_RING in rule


# ---------------------------------------------------------------------------
# G3: the Launch tick box
# ---------------------------------------------------------------------------

def test_g3_a_checkbox_renders_a_qcheckbox_named_by_its_tooltip(view):
    from PySide6.QtWidgets import QCheckBox
    box = view._widget_for(element_named(view, "is_armed"))
    assert isinstance(box, QCheckBox)
    assert box.text() == "Armed"            # a column section: its own words
    assert box.accessibleName() == "Arm the fake"
    assert box.toolTip() == "Arm the fake"


def test_g3_a_click_sends_true_when_off_and_false_when_on(view, panel):
    box = view._widget_for(element_named(view, "is_armed"))
    box.click()
    assert panel.armed_calls == [True] and panel.is_armed is True
    assert box.isChecked()
    box.click()
    assert panel.armed_calls == [True, False] and panel.is_armed is False
    assert not box.isChecked()


def test_g3_a_refresh_sets_the_tick_from_state_without_sending(view, panel):
    box = view._widget_for(element_named(view, "is_armed"))
    toggled = []
    box.toggled.connect(toggled.append)
    panel.is_armed = True
    view._refresh()
    assert box.isChecked()
    panel.is_armed = False
    view._refresh()
    assert not box.isChecked()
    assert toggled == [] and panel.armed_calls == []


def test_g3_a_refused_tick_is_undone_by_the_refresh(view, panel, monkeypatch):
    def refuse(flag):
        raise Refused("not now")
    monkeypatch.setattr(panel, "set_armed", refuse)
    box = view._widget_for(element_named(view, "is_armed"))
    box.click()
    assert panel.is_armed is False and not box.isChecked()


def test_g3_a_gated_dropdown_and_its_rescan_are_greyed_until_the_box_is_ticked(
        view, panel):
    element = element_named(view, "gated")
    combo = view._widget_for(element)
    rescan = view._companions[id(element)]
    view._refresh()
    assert not combo.isEnabled() and not rescan.isEnabled()
    assert view._widget_for(element_named(view, "choice")).isEnabled()
    view._widget_for(element_named(view, "is_armed")).click()
    assert combo.isEnabled() and rescan.isEnabled()


def _setup_view(qapp):
    from controller.controller import Controller
    from controller.setup import Setup
    setup = Setup(Controller())
    return setup, qt.QtPanelView(setup.controller, "Setup", panel=setup)


def test_g3_setup_rows_read_launch_port_gamepad_status(qapp):
    """[Launch] [Port] [Gamepad] [Status]: the box first, as on `main`, in a
    narrow column of its own that takes no control floor and never stretches."""
    from PySide6.QtWidgets import QCheckBox
    setup, built = _setup_view(qapp)
    try:
        table = built._table
        grid = table.grid
        headers = [grid.itemAtPosition(table.header_row, c).widget().text()
                   for c in range(1, grid.columnCount())
                   if grid.itemAtPosition(table.header_row, c) is not None]
        assert headers == ["Launch", "Port", "Gamepad", "Status"]
        launch = table.columns["Launch"]
        assert grid.columnMinimumWidth(launch) == 0
        assert grid.columnStretch(launch) == 0
        assert grid.columnMinimumWidth(table.columns["Port"]) == qt.TABLE_CONTROL_MIN_PX
        boxes = [e for e in built._elements if e["type"] == "checkbox"]
        assert boxes
        for element in boxes:
            box = built._widget_for(element)
            assert isinstance(box, QCheckBox) and box.text() == ""
            assert box.accessibleName().startswith("Launch ")
            assert cell_of(grid, box)[1] == launch
    finally:
        built.close()


def test_g3_ticking_a_setup_row_ungreys_its_port(qapp):
    setup, built = _setup_view(qapp)
    try:
        box_element = next(e for e in built._elements if e["type"] == "checkbox")
        key = box_element["model_attr"][:-len("_enabled")]
        port = built._widget_for(element_named(built, f"{key}_port"))
        built._refresh()
        assert not port.isEnabled()
        built._widget_for(box_element).click()
        assert getattr(setup, f"{key}_enabled") is True
        assert port.isEnabled()
    finally:
        built.close()


# ---------------------------------------------------------------------------
# G4: the Gamepad Log behind a button, in its own window
# ---------------------------------------------------------------------------

def detached_of(view):
    return next(e for e in view._elements
                if e["type"] == "log_stream" and e.get("detached"))


def test_g4_a_detached_stream_is_a_button_not_a_feed(view, panel):
    from PySide6.QtWidgets import QPushButton, QTextEdit
    element = detached_of(view)
    button = view._widget_for(element)
    assert isinstance(button, QPushButton)
    assert button.text() == "Gamepad log\u2026"
    assert view.detached_window(element) is None
    feeds = [w for w in view.findChildren(QTextEdit)]
    assert len(feeds) == 1              # the attached "Log" only
    view._refresh()
    assert getattr(panel, "gamepad_reads", 0) == 0


def test_g4_pressing_it_opens_one_non_modal_window_holding_the_lines(view, qapp):
    element = detached_of(view)
    view._widget_for(element).click()
    dialog = view.detached_window(element)
    assert isinstance(dialog, QDialog) and dialog.isVisible()
    assert dialog.isModal() is False
    assert dialog.windowModality() == Qt.WindowModality.NonModal
    assert not dialog.windowFlags() & Qt.WindowType.WindowStaysOnTopHint
    assert qapp.activeModalWidget() is None
    # Updated (L12): "<model> gamepad log", no dash.
    assert dialog.windowTitle() == "Fake gamepad log"
    feed = view._detached[id(element)][1]
    assert feed.toPlainText() == "pad up\npad down"
    assert feed.maximumHeight() != qt.LOG_STREAM_PX     # not the card's feed


def test_g4_a_second_press_raises_the_same_window(view):
    element = detached_of(view)
    button = view._widget_for(element)
    button.click()
    first = view.detached_window(element)
    first.hide()
    button.click()
    assert view.detached_window(element) is first and first.isVisible()
    button.click()                      # already open: still the one window
    assert view.detached_window(element) is first


def test_g4_escape_hides_it_and_focus_returns_to_the_button(view, qapp):
    from PySide6.QtTest import QTest
    element = detached_of(view)
    view._widget_for(element).click()
    dialog = view.detached_window(element)
    returned = []
    # Which widget holds focus cannot be staged offscreen; the hand-back can.
    view._return_focus = returned.append
    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    assert not dialog.isVisible()
    assert view.detached_window(element) is dialog      # hidden, not destroyed
    assert returned == [element]


def test_g4_the_close_button_hides_it_too(view):
    element = detached_of(view)
    view._widget_for(element).click()
    dialog = view.detached_window(element)
    dialog.close()
    assert not dialog.isVisible() and view.detached_window(element) is dialog


def test_g4_the_stream_is_polled_only_while_its_window_is_open(view, panel):
    element = detached_of(view)
    assert view._wants_data(element) is False
    assert view._wants_data(element_of(view, "log_stream")) is True
    view._refresh()
    assert getattr(panel, "gamepad_reads", 0) == 0
    view._widget_for(element).click()
    assert view._wants_data(element) is True
    reads = panel.gamepad_reads
    view._refresh()
    assert panel.gamepad_reads == reads + 1
    view.detached_window(element).hide()
    assert view._wants_data(element) is False
    view._refresh()
    assert panel.gamepad_reads == reads + 1


def test_g4_closing_the_panel_closes_its_window(qapp, controller):
    built = qt.QtPanelView(controller, "Fake")
    element = detached_of(built)
    built._widget_for(element).click()
    dialog = built.detached_window(element)
    assert dialog.isVisible()
    built.close()
    assert not dialog.isVisible()
    assert built._detached == {}


def test_g4_the_window_belongs_to_the_main_window_and_the_stop_stays_live(
        dashboard, controller):
    dashboard._add_panel("Fake")
    panel_view = dashboard._panels["Fake"]
    element = detached_of(panel_view)
    panel_view._widget_for(element).click()
    dialog = panel_view.detached_window(element)
    assert dialog.parent() is dashboard
    assert QApplication.activeModalWidget() is None
    assert dashboard.stop_button.isEnabled()
    dashboard.stop_shortcut.activated.emit()
    assert controller.is_estopped is True


# ---------------------------------------------------------------------------
# E (2026-09-25): the Bench sheet, tiered. Written by rb-e-qt, run by the
# lead (agents do not run the Qt pass).
# ---------------------------------------------------------------------------

class TieredPanel(Panel):
    """A probe's shape on the Bench sheet: X/Y/Z and a speed with a slider in
    tier 1, a step size and a plot behind "Configure", a fault word and the
    per-model stop behind "Diagnostics"."""

    NAME = "Tiered"
    PARAMS = {"speed": Param("speed", "int", default=400, minimum=1,
                             maximum=5000, label="Manual Speed"),
              "step": Param("step", "int", default=16, minimum=1,
                            label="X Step Size")}

    def __init__(self):
        super().__init__()
        self.position_x, self.position_y, self.position_z = "1184", "-352", "20"
        self.connection = "Connected"
        self.motion = "Moving"
        self.fault = "No"
        self.is_estopped = False
        self.commands = []

    @property
    def schema(self):
        P = self.PARAMS
        return sch.schema(
            sch.section("Position", *[sch.readonly(f"{a}:", f"position_{a.lower()}",
                                                   rail=True) for a in "XYZ"]),
            sch.section("Speeds",
                        sch.entry("Manual Speed:", "speed", P["speed"], slider=(1, 1000)),
                        sch.button("Step", "step_once", inputs=("speed",), role="go"),
                        sch.readonly("Connection:", "connection"),
                        sch.readonly("Motion:", "motion")),
            sch.section("Configuration", sch.entry("X Step Size:", "step", P["step"]),
                        sch.plot("Series", "series"), tier=2, disclosure="Configure"),
            sch.section("Diagnostics", sch.readonly("Fault:", "fault"),
                        tier=3, disclosure="Diagnostics"),
            sch.section("Safety", sch.toggle("Stop", "is_estopped", "toggle_estop",
                                             "Stopped", "Stop", on_role="danger",
                                             off_role="danger", tooltip="Stop Tiered"),
                        tier=3, disclosure="Diagnostics"))

    def step_once(self):
        self.commands.append(("step", self.speed))

    def toggle_estop(self):
        self.is_estopped = not self.is_estopped

    def series(self):
        return {"y": [1, 3, 2]}


@pytest.fixture
def tiered(qapp):
    qt.QtPanelView.open_tiers.clear()
    panel = TieredPanel()
    built = qt.QtPanelView(FakeController(panel), "Tiered")
    yield built, panel
    built.close()
    qt.QtPanelView.open_tiers.clear()


def test_e_tier_two_is_hidden_until_its_disclosure_is_pressed(tiered):
    view, _ = tiered
    step = view._widget_for(element_named(view, "step"))
    assert view.tier_button.text() == "Configure"
    assert view.tier_button.isChecked() is False
    assert step.isVisibleTo(view) is False
    view.tier_button.click()
    assert view.well.isVisibleTo(view) and step.isVisibleTo(view)
    assert view.tier_button.property("open") is True
    view.tier_button.click()
    assert step.isVisibleTo(view) is False


def test_e_tier_three_sits_inside_tier_two_behind_diagnostics(tiered):
    view, _ = tiered
    fault = view._widget_for(element_named(view, "fault"))
    switch = view._widget_for(element_named(view, "is_estopped"))
    assert view.well.isAncestorOf(view.diag_button)
    assert view.diagnostics.isAncestorOf(fault)
    assert view.diagnostics.isAncestorOf(switch)
    assert view.diag_button.text() == "Diagnostics"
    view.tier_button.click()
    assert fault.isVisibleTo(view) is False
    view.diag_button.click()
    assert fault.isVisibleTo(view) and switch.isVisibleTo(view)
    assert f"border-left: 2px solid {theme.MUTED}" in qt.stylesheet()


def test_e_open_tiers_are_remembered_per_model_for_the_session(qapp):
    qt.QtPanelView.open_tiers.clear()
    first = qt.QtPanelView(FakeController(TieredPanel()), "Tiered")
    first.tier_button.click()
    first.diag_button.click()
    first.close()
    again = qt.QtPanelView(FakeController(TieredPanel()), "Tiered")
    other = qt.QtPanelView(FakeController(TieredPanel()), "Other")
    try:
        assert again.tier_is_open(2) and again.tier_is_open(3)
        assert again.tier_button.isChecked()
        assert not other.tier_is_open(2)
    finally:
        again.close()
        other.close()
        qt.QtPanelView.open_tiers.clear()


def test_k3_the_disclosure_is_the_foot_of_the_body_directly_above_its_well(tiered):
    """Replaces "an entry lifts the disclosure into its head" (E): K3 puts the
    disclosure at the foot of tier 1, left-aligned, the well right under it
    with no gap; the head holds the name and the close only."""
    view, _ = tiered
    entry = qt.SheetEntry("Tiered", view)
    entry.set_page(False)
    entry.resize(900, 600)
    entry.show()
    view.tier_button.click()
    QApplication.processEvents()
    assert not entry.head.isAncestorOf(view.tier_button)
    assert not hasattr(view, "take_disclosure")
    block = view.tier_block.layout()
    assert block.itemAt(0).widget() is view.tier_button
    # Updated (L5): the well sits in its own scroll area (only the well
    # scrolls on the device page), directly under the disclosure.
    assert block.itemAt(1).widget() is view.well_scroll
    assert view.well_scroll.widget() is view.well
    assert block.spacing() == 0
    assert view.tier_button.geometry().bottom() + 1 == view.well_scroll.geometry().top()
    assert view.tier_button.x() == 0                        # left-aligned
    # Below every tier-1 widget, and reached after them by Tab.
    step = view._widget_for(next(e for e in view._elements
                                 if e.get("command") == "step_once"))
    assert view.tier_button.mapTo(view, QPoint(0, 0)).y() > step.mapTo(view, QPoint(0, 0)).y()
    chain, widget = [], step
    for _ in range(500):
        widget = widget.nextInFocusChain()
        chain.append(widget)
        if widget is view.tier_button:
            break
    assert view.tier_button in chain
    # Nothing of tier 1 comes after the disclosure; the well's inputs do.
    after, widget = [], view.tier_button
    for _ in range(500):
        widget = widget.nextInFocusChain()
        if widget is view.tier_button or not view.isAncestorOf(widget):
            break
        after.append(widget)
    assert step not in after
    assert view._widget_for(element_named(view, "step")) in after
    entry.hide()
    # The entry owns the panel now; deleting it takes the panel and its timer
    # with it, and the fixture's later close() must survive that.
    import shiboken6
    shiboken6.delete(entry)
    assert not qt.qt_alive(view._timer)
    assert view.close() is True


def test_e_the_slider_and_the_entry_follow_each_other_and_the_command_gets_the_value(
        tiered):
    view, panel = tiered
    element = element_named(view, "speed")
    entry = view._widget_for(element)
    slider = view._sliders[id(element)]
    assert isinstance(slider, qt.QSlider)
    assert (slider.minimum(), slider.maximum()) == (1, 1000)
    assert entry.parentWidget() is slider.parentWidget()     # beside, not instead
    view._refresh()
    assert slider.value() == 400
    slider.setValue(250)                  # a key press: not a drag
    assert entry.text() == "250"
    assert panel.speed == 250             # committed through `_commit`
    view._refresh()
    assert entry.text() == "250" and slider.value() == 250
    entry.setText("730")
    entry.editingFinished.emit()
    assert slider.value() == 730
    view._run(next(e for e in view._elements if e.get("command") == "step_once"))
    assert panel.commands[-1] == ("step", 730)
    entry.setText("4000")                 # past the slider's travel
    entry.editingFinished.emit()
    assert slider.value() == 1000 and entry.text() == "4000"


def test_e_a_drag_is_not_snapped_back_by_the_refresh(tiered, monkeypatch):
    view, panel = tiered
    element = element_named(view, "speed")
    slider = view._sliders[id(element)]
    monkeypatch.setattr(slider, "isSliderDown", lambda: True)
    slider.setValue(600)
    view._refresh()
    assert view._widget_for(element).text() == "600"
    assert panel.speed == 400             # not yet: it commits on release
    monkeypatch.setattr(slider, "isSliderDown", lambda: False)
    slider.sliderReleased.emit()
    assert panel.speed == 600


def test_e_a_normal_value_is_not_drawn_in_tier_one(tiered):
    view, panel = tiered
    view._refresh()
    connection = element_named(view, "connection")
    motion = element_named(view, "motion")
    assert view._holders[id(connection)].isHidden() is True     # "Connected"
    assert view._holders[id(motion)].isHidden() is False        # "Moving"
    panel.connection = "Lost"
    view._refresh()
    assert view._holders[id(connection)].isHidden() is False
    # Tier 3 still draws its normal values ("No" fault) for the diagnostics.
    view.tier_button.click()
    view.diag_button.click()
    assert view._widget_for(element_named(view, "fault")).isVisibleTo(view)


def test_e_axis_readings_are_focal_on_the_opened_model_and_compact_otherwise(tiered):
    view, _ = tiered
    x = view._widget_for(element_named(view, "position_x"))
    assert x.objectName() == "reading" and x.property("scale") == "compact"
    view.set_opened(True)
    assert x.property("scale") == "focal"
    assert x.font().pointSize() == qt.reading_pt("focal")   # 52 px at any dpi
    captions = [w.text() for w in view.findChildren(QLabel) if w.objectName() == "caption"]
    assert captions.count("Position") == 1                     # once, then X Y Z
    letters = [w.text() for w in view.findChildren(QLabel)
               if w.objectName() == "axisLetter"]
    assert letters == ["X", "Y", "Z"]


def test_e_a_number_is_the_trace_only_while_it_changes(tiered, monkeypatch):
    view, panel = tiered
    x = view._widget_for(element_named(view, "position_x"))
    view._refresh()
    assert x.property("live") == "false"
    panel.position_x = "1190"
    view._refresh()
    assert x.property("live") == "true"
    later = qt.time.monotonic() + qt.LIVE_S + 0.5
    monkeypatch.setattr(qt.time, "monotonic", lambda: later)
    view._refresh()
    assert x.property("live") == "false"


def test_e_a_latched_model_freezes_its_numbers(tiered):
    view, panel = tiered
    view._refresh()
    assert view.property("frozen") in (None, "false")
    panel.is_estopped = True
    view._refresh()
    assert view.property("frozen") == "true"
    assert view.property("stale") == "true"          # the one dimming rule
    x = view._widget_for(element_named(view, "position_x"))
    assert x.palette().color(x.foregroundRole()).name().lower() == theme.MUTED.lower()


def test_e_the_disc_reads_stop_then_clear_and_is_red_both_ways(qapp):
    button = qt.StopButton()
    assert button.text() == "Stop" and not button.is_latched
    assert button.diameter == theme.STOP["diameter"]
    button.set_latched(True)
    assert button.text() == "Clear" and button.ring_px() == theme.STOP["ring_latched"]
    button.setEnabled(False)
    assert button.isEnabled()
    centre = button.width() // 2
    colour = QColor(button.grab().toImage().pixel(centre, centre - button.diameter // 3))
    target = QColor(theme.SIGNAL)
    assert abs(colour.red() - target.red()) + abs(colour.green() - target.green()) < 60


def test_e_a_stop_that_did_not_confirm_is_marked_at_its_entry(dashboard, controller):
    """Updated (L1): the mark follows the model's own `stop_confirmed` in its
    state, not the view's memory of the last `estop_all` answer."""
    dashboard.open()
    controller.unconfirmed = {"Fake"}
    dashboard._on_stop_clicked()
    entry = dashboard._entries["Fake"]
    assert entry.is_unconfirmed is True
    assert entry.unconfirmed_label.text() == "Stop not confirmed. Treat as live."
    assert entry.unconfirmed_label.isVisibleTo(entry)
    assert theme.SIGNAL in entry.rule.styleSheet()
    controller.is_estopped = False
    controller.unconfirmed = set()
    dashboard._on_rail_tick()
    assert entry.is_unconfirmed is False and entry.rule.styleSheet() == ""


def test_e_the_tray_reports_warnings_and_errors_only_and_each_once(dashboard):
    class Spoof:
        source, title, message, count = "T", "t", "m", 1
        needs_ack = False

    info, warning = Spoof(), Spoof()
    # Updated (L11): the line is built from the title and the message.
    info.severity, info.message = "info", "launched"
    warning.severity, warning.message = "warning", "a port answered nothing"
    dashboard._show_event(info)
    assert dashboard.event_latest.full_text() == ""
    dashboard._show_event(warning)
    dashboard._show_event(warning)
    assert dashboard.event_view.toPlainText().count("a port answered nothing") == 1
    # The warning's mark is a hollow square in the warning ink: an image in
    # the log (rich text drops a span's border), ink at its edge, clear inside.
    # (Was an HTML `border:1px solid` span, which Qt never rendered.)
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QTextDocument
    page = dashboard.event_view.toHtml()
    assert 'src="mark:warning"' in page
    image = dashboard.event_view.document().resource(
        QTextDocument.ResourceType.ImageResource.value, QUrl("mark:warning"))
    side = image.width()
    edge, inside = QColor(image.pixel(1, side // 2)), image.pixelColor(side // 2, side // 2)
    assert _near(edge, theme.SEVERITY_MARK["warning"]) and inside.alpha() == 0
    error = Spoof()
    error.severity, error.message = "error", "the heater did not answer"
    dashboard._show_event(error)
    assert (f"background-color:{theme.SEVERITY_MARK['error']}".lower()
            in dashboard.event_view.toHtml().lower().replace(" ", ""))


def _six_tiered_window(qapp, width, height):
    controller = FakeController(TieredPanel())
    controller.open_names = ["Stepper Probe", "DC Probe", "Chuck Positioner",
                             "Temperature Controller", "Rotator", "Red Percent"]
    controller.closed = []
    window = qt.QtDashboard(controller, controller.panel)
    window.open()
    window.resize(width, height)
    for _ in range(5):
        qapp.processEvents()
    window._arrange_entries()
    for _ in range(5):
        qapp.processEvents()
    return window


def _cut_labels(window):
    cut = []
    for label in window.findChildren(QLabel):
        if (not label.isVisible() or not label.text() or label.wordWrap()
                or isinstance(label, (qt.ElidedLabel, qt.ReadoutLabel))):
            continue
        need = label.fontMetrics().horizontalAdvance(label.text())
        if need > label.contentsRect().width() + 1:
            cut.append((label.objectName(), label.text()))
    return cut


def test_e_nothing_clips_at_1000_by_700_with_six_models(qapp, restore_font):
    qt.QtPanelView.open_tiers.clear()
    theme.set_font_size(12)
    window = _six_tiered_window(qapp, 1000, 700)
    try:
        assert _cut_labels(window) == []
        assert window.sheet_scroll.horizontalScrollBar().maximum() == 0
        stop = window.stop_button
        corner = stop.mapTo(window, QPoint(stop.width(), stop.height()))
        assert corner.x() <= window.width() and corner.y() <= window.height()
    finally:
        window.close()
        events.unsubscribe(window._on_event)


def test_e_nothing_clips_at_28_pt_with_a_models_details_open(qapp, restore_font):
    qt.QtPanelView.open_tiers.clear()
    theme.set_font_size(28)
    window = _six_tiered_window(qapp, 1000, 700)
    try:
        window.open_entry("Red Percent")
        panel = window._panels["Red Percent"]
        panel.tier_button.click()
        panel.diag_button.click()
        for _ in range(5):
            qapp.processEvents()
        window._arrange_entries()
        for _ in range(5):
            qapp.processEvents()
        # Updated (K4): the device page is the model alone.
        assert window._arrangement == (("Red Percent",),)
        assert _cut_labels(window) == []
        assert window.sheet_scroll.horizontalScrollBar().maximum() == 0
        entry = window._entries["Red Percent"]
        assert entry.height() >= entry.heightForWidth(entry.width()) - 1
    finally:
        window.close()
        events.unsubscribe(window._on_event)
        qt.QtPanelView.open_tiers.clear()


# ---------------------------------------------------------------------------
# K (2026-09-26): the disclosure at the foot of the body (K3), the overview
# and the device page (K4). Written by rb-k-qt, run by the lead.
# ---------------------------------------------------------------------------

def _pump(qapp, times=5):
    for _ in range(times):
        qapp.processEvents()


@pytest.fixture
def six(qapp, restore_font):
    qt.QtPanelView.open_tiers.clear()
    theme.set_font_size(12)
    window = _six_tiered_window(qapp, 1400, 900)
    yield window
    window.close()
    events.unsubscribe(window._on_event)
    qt.QtPanelView.open_tiers.clear()


def _shown_entries(window):
    return [n for n, e in window._entries.items() if e.isVisible()]


def test_k3_the_disclosure_says_the_schemas_phrase(tiered):
    view, _ = tiered
    assert view.tier_button.text() == "Configure"            # the section's own
    assert view.diag_button.text() == "Diagnostics"


def test_k4_overview_is_the_rails_first_item_and_current_at_launch(six):
    window = six
    rail = window.overview_item
    assert rail.text() == "Overview" and rail.isChecked()
    assert window.page == "Overview"
    stack = rail.parentWidget().layout()
    assert stack.indexOf(rail) == 0
    tops = [item.mapTo(window.rail, QPoint(0, 0)).y()
            for item in window._rail_items.values()]
    assert all(rail.mapTo(window.rail, QPoint(0, 0)).y() < top for top in tops)
    assert not any(item.isChecked() for item in window._rail_items.values())


def test_k4_the_overview_shows_every_model_with_no_well_or_disclosure(six):
    window = six
    assert _shown_entries(window) == list(window.controller.model_names)
    assert window._arrangement == (("Stepper Probe", "DC Probe", "Chuck Positioner"),
                                   ("Temperature Controller", "Rotator", "Red Percent"))
    for name, entry in window._entries.items():
        panel = window._panels[name]
        assert not panel.tier_button.isVisible()
        assert not panel.well.isVisible()
        assert entry.head.is_pressable and entry.open_word.isVisible()
        assert entry.open_word.text() == "Open"
        assert entry.head.accessibleName() == f"Open {name}"
        assert entry.head.toolTip() == f"Open {name}"
        assert entry.head.focusPolicy() != Qt.FocusPolicy.NoFocus


def test_k4_a_plot_behind_a_tier_that_is_not_shown_is_not_polled(six):
    window = six
    panel = window._panels["Stepper Probe"]
    plot = next(e for e in panel._elements if e["type"] == "plot")
    qt.QtPanelView.open_tiers[("Stepper Probe", 2)] = True
    panel._set_tier_open(2, True)
    assert panel._wants_data(plot) is False                  # the overview
    window.open_entry("Stepper Probe")
    assert panel._wants_data(plot) is True                   # its page, well open
    panel.tier_button.click()
    assert panel._wants_data(plot) is False                  # well closed


def test_k4_pressing_a_model_in_the_rail_shows_it_alone_with_its_disclosures(six, qapp):
    window = six
    window._rail_items["Rotator"].click()
    _pump(qapp)
    assert window.page == "Rotator"
    assert window._arrangement == (("Rotator",),)
    assert _shown_entries(window) == ["Rotator"]
    assert window._rail_items["Rotator"].isChecked()
    assert not window.overview_item.isChecked()
    entry, panel = window._entries["Rotator"], window._panels["Rotator"]
    assert panel.tier_button.isVisible() and panel._opened is True
    assert not entry.head.is_pressable and not entry.open_word.isVisible()
    x = panel._widget_for(element_named(panel, "position_x"))
    assert x.property("scale") == "focal"


def test_k4_pressing_an_overview_head_opens_the_device_by_mouse_and_by_key(six, qapp):
    from PySide6.QtTest import QTest
    window = six
    head = window._entries["DC Probe"].head
    QTest.mouseClick(head, Qt.MouseButton.LeftButton, pos=QPoint(4, head.height() // 2))
    _pump(qapp)
    assert window.page == "DC Probe" and _shown_entries(window) == ["DC Probe"]
    window.show_overview()
    _pump(qapp)
    for key in (Qt.Key.Key_Return, Qt.Key.Key_Space):
        window.show_overview()
        _pump(qapp)
        head = window._entries["Rotator"].head
        head.setFocus()
        QTest.keyClick(head, key)
        _pump(qapp)
        assert window.page == "Rotator"


def test_k4_the_close_in_an_overview_head_does_not_open_the_device(six, qapp):
    window = six
    window._entries["Rotator"].close_button.click()
    _pump(qapp)
    assert window.page == "Overview"
    assert "Rotator" in window.controller.removed


def test_k4_pressing_overview_returns(six, qapp):
    window = six
    window.open_entry("Stepper Probe")
    window.overview_item.click()
    _pump(qapp)
    assert window.page == "Overview" and window.overview_item.isChecked()
    assert _shown_entries(window) == list(window.controller.model_names)
    panel = window._panels["Stepper Probe"]
    assert not panel.tier_button.isVisible() and panel._opened is False


def test_k4_closing_the_shown_device_returns_to_the_overview(six, qapp):
    window = six
    window.open_entry("Rotator")
    window._entries["Rotator"].close_button.click()
    window.controller.notify("removed", "Rotator")
    _pump(qapp)
    assert "Rotator" not in window._entries
    assert window.page == "Overview" and window.overview_item.isChecked()
    assert _shown_entries(window) == list(window.controller.model_names)


def test_k4_a_setup_launch_lands_on_the_overview(six, qapp):
    window = six
    window.open_entry("DC Probe")
    window.controller.open_names.append("Seventh")
    window.controller.notify("added", "Seventh")
    _pump(qapp)
    assert window.page == "Overview"


def test_k4_tier_state_survives_overview_device_overview_device(six, qapp):
    window = six
    panel = window._panels["Stepper Probe"]
    window.open_entry("Stepper Probe")
    panel.tier_button.click()
    panel.diag_button.click()
    window.show_overview()
    _pump(qapp)
    assert not panel.well.isVisible()
    assert qt.QtPanelView.open_tiers[("Stepper Probe", 2)] is True
    window.open_entry("Stepper Probe")
    _pump(qapp)
    assert panel.tier_button.isChecked() and panel.well.isVisible()
    assert panel.diagnostics.isVisible()
    assert not qt.QtPanelView.open_tiers.get(("DC Probe", 2), False)


# ---------------------------------------------------------------------------
# L (2026-09-26): audit round 7 in the Qt view. Written by rb-l-qt, run by
# the lead.
# ---------------------------------------------------------------------------

def _stop_window(six, qapp, latched=None, unconfirmed=(), every=False):
    """The six-model window with a stop staged on the fake Controller:
    `latched` names (a partial stop), or `every` model."""
    controller = six.controller
    controller.is_estopped = bool(latched) or every
    controller.latched = None if every else set(latched or ())
    controller.unconfirmed = set(unconfirmed)
    six._on_rail_tick()
    _pump(qapp)
    return controller


def test_l1_one_models_own_stop_leaves_the_disc_a_working_stop(six, qapp):
    """IMP7-1/2: Rotator's own switch latched Rotator only. The disc still
    reads Stop and a press stops the other five; nothing says "every model"."""
    controller = _stop_window(six, qapp, latched={"Rotator"})
    assert six.stop_button.text() == "Stop" and not six.stop_button.is_latched
    assert six.latched_label.text() == "Stopped: Rotator"
    assert six.latched_row.isVisibleTo(six.rail)
    assert not six.headline_row.isVisibleTo(six.sheet)
    assert six.stop_button.accessibleName() == "Stop every model"
    six.stop_button.click()
    assert controller.estop_calls == 1          # a stop, never a clear


def test_l1_a_stop_that_did_not_confirm_is_the_headline_and_the_rail(six, qapp):
    """QT7-1: every model latched, Rotator unconfirmed. The headline names it,
    the subline says what to do, the rail line says it too; the disc clears."""
    _stop_window(six, qapp, every=True, unconfirmed={"Rotator"})
    assert six.stop_button.text() == "Clear"
    assert six.headline.text() == "Stopped. Rotator did not confirm."
    assert six.subline.text() == "Treat it as live until you have checked it by hand."
    assert six.headline_row.isVisibleTo(six.sheet)
    assert six.subline.isVisibleTo(six.sheet)
    # The subline sits under the headline, not beside it.
    assert six.subline.mapTo(six.sheet, QPoint(0, 0)).y() >= (
        six.headline.mapTo(six.sheet, QPoint(0, 0)).y() + six.headline.height())
    assert six.latched_label.text() == "Stopped: Rotator did not confirm"
    assert six._entries["Rotator"].is_unconfirmed is True
    assert six._entries["DC Probe"].is_unconfirmed is False


def test_l1_every_model_confirmed_says_every_model_is_stopped(six, qapp):
    _stop_window(six, qapp, every=True)
    assert six.headline.text() == "Every model is stopped."
    assert not six.subline.isVisibleTo(six.sheet)            # no subline words
    assert six.latched_label.text() == "Stopped: every model latched"
    assert six.stop_button.accessibleName() == "Clear the stop on every model"


def test_l1_the_rail_marks_each_model_latched_or_unconfirmed(six, qapp):
    """A small ink square for a latched model, a signal one for a model that
    did not confirm, and the words in its tooltip and name: never colour
    alone."""
    _stop_window(six, qapp, latched={"Rotator", "DC Probe"},
                 unconfirmed={"Rotator"})
    rotator, probe = six._rail_items["Rotator"], six._rail_items["DC Probe"]
    free = six._rail_items["Stepper Probe"]
    assert rotator.stop_mark == "unconfirmed" and probe.stop_mark == "stopped"
    assert free.stop_mark is None and free.icon().isNull()
    assert not rotator.icon().isNull() and not probe.icon().isNull()
    assert rotator.accessibleName() == "Rotator, did not confirm"
    assert probe.accessibleName() == "DC Probe, stopped"
    assert "did not confirm" in rotator.toolTip() and "stopped" in probe.toolTip()
    size = rotator.iconSize().width()
    ink = rotator.icon().pixmap(size, size).toImage().pixelColor(size // 2, size // 2)
    assert _near(ink, theme.SIGNAL)
    ink = probe.icon().pixmap(size, size).toImage().pixelColor(size // 2, size // 2)
    assert _near(ink, theme.TEXT)
    controller = six.controller
    controller.is_estopped, controller.latched = False, set()
    controller.unconfirmed = set()
    six._on_rail_tick()
    assert rotator.stop_mark is None and rotator.accessibleName() == "Rotator"


def test_l1_the_chord_hint_is_shown_in_every_state(six, qapp):
    for staged in ({}, {"latched": {"Rotator"}}, {"every": True}):
        _stop_window(six, qapp, **staged)
        assert six.stop_hint.isVisibleTo(six.rail)
        assert six.stop_hint.full_text() == "Stop: Ctrl+."


def test_l1_the_chord_stops_a_partial_stop_and_never_clears(six, qapp):
    controller = _stop_window(six, qapp, latched={"Rotator"})
    six.stop_shortcut.activated.emit()
    assert controller.estop_calls == 1
    six._on_rail_tick()
    six.stop_shortcut.activated.emit()          # every model latched now
    assert controller.estop_calls == 1 and controller.is_estopped


def test_l2_the_stop_not_confirmed_line_leaves_with_the_latch(six, qapp):
    """QT7-12: after Clear the band still said "did not confirm". The
    acknowledgement is required while latched; once the latch opens the line
    goes, and an unrelated error stays."""
    import uuid
    token = uuid.uuid4().hex             # a repeat within 5 s is not re-sent
    _stop_window(six, qapp, every=True, unconfirmed={"Rotator"})
    events.error("Stop Not Confirmed", f"Rotator did not confirm the stop {token}.",
                 source="Controller")
    events.error("Command Failed", f"the heater did not answer {token}",
                 source="Heater")
    _pump(qapp)
    titles = [a.title for a in six.alerts]
    assert "Stop Not Confirmed" in titles
    six._on_rail_tick()                          # still latched: it stays
    assert "Stop Not Confirmed" in [a.title for a in six.alerts]
    _stop_window(six, qapp)                      # the latch opens
    assert [a.title for a in six.alerts] == ["Command Failed"]
    assert "confirm" not in six.alert_text.full_text()


def test_l9_quit_asks_first_in_the_stations_words(dashboard, controller, monkeypatch):
    """QT7-7: Quit closed the station on one press. It asks, as Tk and Web do,
    with a title and verb buttons (L14); "Stay" keeps everything running."""
    asked, answer = [], [False]

    def ask(parent, prompt, title="", yes="", no=""):
        asked.append((prompt, title, yes, no))
        return answer[0]

    monkeypatch.setattr(qt, "ask", ask)
    dashboard.open()
    dashboard.quit_button.click()
    assert asked == [("Quit the station? This stops every model, closes every "
                      "port and exits.", "Quit the station?", "Quit", "Stay")]
    assert controller.is_closed is False and not dashboard._closing
    answer[0] = True
    dashboard.quit_button.click()
    assert controller.is_closed is True


def test_l9_the_windows_close_asks_the_same_question(dashboard, controller,
                                                     monkeypatch, qapp):
    asked, answer = [], [False]

    def ask(parent, prompt, title="", yes="", no=""):
        asked.append(title)
        return answer[0]

    monkeypatch.setattr(qt, "ask", ask)
    dashboard.open()
    from PySide6.QtWidgets import QMainWindow
    QMainWindow.close(dashboard)                  # the title bar's close
    _pump(qapp)
    assert asked == ["Quit the station?"]
    assert dashboard.isVisible() and controller.is_closed is False
    answer[0] = True
    QMainWindow.close(dashboard)
    _pump(qapp)
    assert controller.is_closed is True


def test_l14_a_question_has_a_title_and_verb_buttons(qapp):
    """QT7-13: "Yes" / "No" under no title. No stays the default and Escape
    stays No."""
    seen = {}

    def look():
        box = qt._PENDING_CONFIRMS[0]
        yes = box.button(QMessageBox.StandardButton.Yes)
        no = box.button(QMessageBox.StandardButton.No)
        # The heading is the box's text: macOS ignores a message box's
        # window title, so the title is said where every platform shows it.
        seen.update(text=box.text(),
                    detail=box.informativeText(), yes=yes.text(), no=no.text(),
                    default=box.defaultButton() is no,
                    escape=box.escapeButton() is no)
        box.reject()
    QTimer.singleShot(0, look)
    answer = qt.ask(None, "Clear the stop on Rotator?", title="Clear the stop?",
                    yes="Clear the stop", no="Keep it stopped")
    assert answer is False
    assert seen == {"text": "Clear the stop?",
                    "detail": "Clear the stop on Rotator?",
                    "yes": "Clear the stop", "no": "Keep it stopped",
                    "default": True, "escape": True}


class GatePanel(Panel):
    """The gates the station's models declare: Red Percent's Start/Stop run,
    the probe's Step. `mode` is whatever the test sets."""

    NAME = "Gate"

    def __init__(self):
        super().__init__()
        self.mode = "idle"

    @property
    def gate_mode(self):
        return self.mode

    @property
    def mode_name(self):
        return self.mode

    @property
    def schema(self):
        return sch.schema(
            sch.section("Run",
                        sch.button("Start run", "start", role="go",
                                   disabled_when=("running", "latched", "no_region")),
                        sch.button("Stop run", "end", enabled_when=("running",))),
            sch.section("Step",
                        sch.button("Step", "step", role="go",
                                   disabled_when=("manual", "latched")),
                        sch.button("Home", "home")))

    def start(self):
        return None

    def end(self):
        return None

    def step(self):
        return None

    def home(self):
        return None


@pytest.fixture
def gated(qapp):
    panel = GatePanel()
    built = qt.QtPanelView(FakeController(panel), "Gate")
    built.resize(900, 400)
    built.show()
    yield built, panel
    built.close()


def _button(view, command):
    return next(view._widget_for(e) for e in view._elements if e.get("command") == command)


def test_l3_a_disabled_command_says_why_and_a_go_row_says_it_under_the_row(gated, qapp):
    """QT7-6: Start run was greyed from launch with no tooltip. The gate's
    reason rides in the tooltip; under a `go` command's row, a muted caption
    says it too. Enabled again, the words go."""
    view, panel = gated
    start, stop, step = _button(view, "start"), _button(view, "end"), _button(view, "step")
    for mode, start_why, stop_why, step_why in (
            ("no_region", "Set a capture region first", "No run in progress", ""),
            ("latched", "Stopped: clear the stop first",
             "Stopped: clear the stop first", "Stopped: clear the stop first"),
            ("manual", "", "No run in progress", "In manual mode"),
            ("running", "A run is in progress", "", "")):
        panel.mode = mode
        view._refresh()
        qapp.processEvents()
        assert start.isEnabled() is (not start_why), mode
        assert start.toolTip() == start_why, mode
        assert stop.toolTip() == stop_why, mode
        assert step.toolTip() == step_why, mode
        run_line, step_line = view.reason_line(start), view.reason_line(step)
        assert run_line.text() == start_why and run_line.isVisible() is bool(start_why)
        # Stop run is not a `go` command: its reason is its tooltip alone.
        assert step_line.text() == step_why and step_line.isVisible() is bool(step_why)
        assert run_line.objectName() == "caption"
    panel.mode = "idle"
    view._refresh()
    assert start.toolTip() == "" and not view.reason_line(start).isVisible()


def test_l6_the_slider_keys_step_one_percent_and_home_end_do_nothing(tiered):
    """TK7-5 in Qt: End committed the maximum speed in one key. An arrow is
    1 % of the travel, a page key 10 %; Home and End are inert. Keys, not
    bindings, and each commits as today."""
    from PySide6.QtTest import QTest
    view, panel = tiered
    element = element_named(view, "speed")
    slider = view._sliders[id(element)]
    view._refresh()
    assert slider.value() == 400
    QTest.keyClick(slider, Qt.Key.Key_Right)
    assert slider.value() == 410 and panel.speed == 410
    for key in (Qt.Key.Key_End, Qt.Key.Key_Home):
        QTest.keyClick(slider, key)
        assert slider.value() == 410 and panel.speed == 410
    QTest.keyClick(slider, Qt.Key.Key_PageUp)
    assert slider.value() == 510 and panel.speed == 510
    QTest.keyClick(slider, Qt.Key.Key_Left)
    assert slider.value() == 500 and panel.speed == 500


def test_l7_tab_from_the_sheet_reaches_the_disc_and_every_button(six, qapp):
    """QT7-4: with macOS's default (Tab reaches text controls only) no button
    was ever focused. The dashboard asks for every control, on every OS."""
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QPushButton
    hints = QGuiApplication.styleHints()
    hints.setTabFocusBehavior(Qt.TabFocusBehavior.TabFocusTextControls)
    window = qt.QtDashboard(six.controller, six.controller.panel)
    try:
        assert hints.tabFocusBehavior() == Qt.TabFocusBehavior.TabFocusAllControls
        window.open()
        window.show()
        window.activateWindow()
        _pump(qapp, 10)
        start = window._entries["Stepper Probe"].head
        start.setFocus(Qt.FocusReason.TabFocusReason)
        _pump(qapp)
        reached = []
        for _ in range(200):
            window.focusNextChild()
            _pump(qapp, 1)
            focused = QApplication.focusWidget()
            reached.append(focused)
            if focused is window.stop_button:
                break
        assert window.stop_button in reached
        assert window.quit_button in reached or any(
            isinstance(w, QPushButton) for w in reached)
    finally:
        window._closing = True
        window.close()
        events.unsubscribe(window._on_event)


def test_l12_the_gamepad_log_window_is_named_says_it_is_empty_and_closes(view, panel,
                                                                          qapp):
    element = next(e for e in view._elements if e.get("detached"))
    panel.gamepad_lines = lambda: []
    dialog = view.open_detached(element)
    try:
        assert dialog.windowTitle() == "Fake gamepad log"
        _, feed = view._detached[id(element)]
        assert feed.toPlainText() == ""
        assert feed.placeholderText() == "No gamepad input yet."
        close = dialog.findChild(qt.QPushButton, "logClose")
        assert close is not None and close.text() == "Close"
        close.click()
        qapp.processEvents()
        assert not dialog.isVisible()
    finally:
        dialog.close()


def test_l15_an_empty_plot_or_figure_is_one_caption_line_tall(qapp):
    """QT7-14: two empty panes stacked 600 px of "no data". Empty, a pane is
    its `empty` sentence, one caption line; with data it takes its height."""
    plot = qt.SeriesPlot(empty="No samples yet. Start a run.")
    line = QFontMetrics(plot.font()).height()
    assert plot.minimumSizeHint().height() <= line + 2 * theme.SPACE[1]
    assert plot.empty_text == "No samples yet. Start a run."
    plot.set_series({"y": [1, 3, 2]})
    assert plot.minimumSizeHint().height() >= 140
    plot.set_series({"y": []})
    assert plot.minimumSizeHint().height() <= line + 2 * theme.SPACE[1]
    figure = qt.FigureLabel(empty="No run loaded.")
    assert figure.sizeHint().height() <= line + 2 * theme.SPACE[1]
    assert figure.text() == "No run loaded."


def test_l15_the_plot_says_the_schemas_empty_sentence(view):
    element = element_of(view, "plot")
    assert view._widget_for(element).empty_text == element["empty"]


def test_l16_names_carry_the_visible_words_and_rescans_name_their_field(table_view,
                                                                       view):
    """QT7-11: "Enter autonomous mode" was named "Autonomous"; nine rescans
    were all "Rescan the choices"."""
    names = [w.accessibleName() for w in table_view.findChildren(qt.QPushButton)
             if w.objectName() == "iconButton"]
    assert len(names) == len(set(names))
    assert "Rescan Port choices, Stepper Probe" in names
    toggle = view._widget_for(element_of(view, "toggle"))
    assert toggle.text() in toggle.accessibleName()


def _small_targets(window):
    """(object name, class, width, height) of every visible pressable under
    24 px, and every visible command under 36 px tall (L4)."""
    from PySide6.QtWidgets import QAbstractButton, QSlider, QLineEdit, QComboBox
    small = []
    for widget in window.findChildren(QWidget):
        if not widget.isVisible() or not isinstance(
                widget, (QAbstractButton, QSlider, QLineEdit, QComboBox)):
            continue
        if widget.objectName() in ("entryOpen", "qt_toolbar_ext_button"):
            continue                    # a word inside the head, not a target
        width, height = widget.width(), widget.height()
        if width < 24 or height < 24:
            small.append((widget.objectName(), type(widget).__name__, width, height))
        command = (type(widget).__name__ == "QPushButton"
                   and widget.objectName() not in ("railModel", "iconButton"))
        if command and height < 36:
            small.append((widget.objectName(), widget.text(), width, height))
    return small


from PySide6.QtWidgets import QWidget  # noqa: E402 - for the L helpers


def test_l4_every_target_is_24_px_and_every_command_36(six, qapp):
    """QT7-5: commands were 22 px tall, under the view's own 24 px floor.
    Every pressable at least 24 px both ways; commands at least 36 tall
    (not 44: the owner's call is pending)."""
    six.open_entry("Stepper Probe")
    panel = six._panels["Stepper Probe"]
    panel.tier_button.click()
    panel.diag_button.click()
    six.tray_toggle.click()
    six.alert_band.setVisible(True)
    _pump(qapp, 10)
    assert _small_targets(six) == []
    assert _small_targets(six.rail) == []


class TickRowPanel(Panel):
    NAME = "Rows"

    def __init__(self):
        super().__init__()
        self.probe_on = False

    @property
    def schema(self):
        return sch.schema(sch.section(
            "Stepper Probe",
            sch.checkbox("Launch", "probe_on", "set_probe_on",
                         tooltip="Launch Stepper Probe"),
            sch.readonly("Status:", "probe_on"), layout="row"))

    def set_probe_on(self, flag):
        self.probe_on = bool(flag)


def test_l4_the_setup_tick_and_its_row_name_are_one_target(qapp):
    from PySide6.QtTest import QTest
    panel = TickRowPanel()
    view = qt.QtPanelView(FakeController(panel), "Rows")
    try:
        view.show()
        _pump(qapp)
        title = next(w for w in view.findChildren(QLabel)
                     if w.objectName() == "rowTitle" and w.text() == "Stepper Probe")
        QTest.mouseClick(title, Qt.MouseButton.LeftButton)
        _pump(qapp)
        assert panel.probe_on is True
        QTest.mouseClick(title, Qt.MouseButton.LeftButton)
        assert panel.probe_on is False
    finally:
        view.close()


def test_l8_setup_takes_the_height_it_needs_while_the_sheet_is_empty(qapp):
    """QT7-3: capped at 55 %, Setup hid Launch under its own fold over an
    empty window; at 900 px a sideways bar covered the Launch row."""
    class BigSetup(TablePanel):
        """Setup's size on the bench: eight rows, a port and a gamepad each."""
        ROWS = tuple((f"Temperature Controller {n}", f"model{n}", True, True)
                     for n in range(8))

    for width in (1400, 900):
        setup = BigSetup()
        controller = FakeController(setup)
        controller.open_names = []
        window = qt.QtDashboard(controller, setup)
        try:
            window.resize(width, 900)
            window.open()
            _pump(qapp, 10)
            scroll = window._setup_scroll
            assert scroll.verticalScrollBar().maximum() == 0, width
            assert scroll.horizontalScrollBar().maximum() == 0, width
        finally:
            window.close()
            events.unsubscribe(window._on_event)


def test_l19_an_identifier_is_never_drawn_in_the_trace(tiered):
    """QT7-8: the Run ID changes every second and was drawn in trace. Only a
    number goes live."""
    view, panel = tiered
    view._refresh()
    motion = view._widget_for(element_named(view, "motion"))
    panel.motion = "run_20260926_122437"
    view._refresh()
    assert motion.property("live") == "false"
    x = view._widget_for(element_named(view, "position_x"))
    panel.position_x = "1200"
    view._refresh()
    assert x.property("live") == "true"


def test_l19_hide_events_keeps_its_place(dashboard, qapp):
    """QT7-9: opening the tray turned the toggle into a full-width bar."""
    class Spoof:
        severity, source, title, message, count = "warning", "T", "Port", "quiet", 1
        needs_ack = False
        text = "[T] Port: quiet"

    dashboard.open()
    dashboard._show_event(Spoof())
    _pump(qapp)
    before = dashboard.tray_toggle.geometry()
    dashboard.tray_toggle.click()
    _pump(qapp)
    after = dashboard.tray_toggle.geometry()
    assert after.width() < before.width() * 1.5
    assert abs(after.right() - before.right()) <= 2


def test_l19_setup_has_a_real_close_and_the_rails_setup_shows_it_is_open(dashboard,
                                                                         qapp):
    dashboard.open()
    dashboard.show_setup()
    _pump(qapp)
    close = dashboard.setup_close
    assert close.isVisible() and close.width() >= 24 and close.height() >= 24
    assert dashboard.setup_button.isChecked()
    sheet = qt.stylesheet()
    checked = sheet.split("QToolButton#ghost:checked {")[1].split("}")[0]
    assert f"background: {theme.TEXT}" in checked
    close.click()
    _pump(qapp)
    assert dashboard._setup_dock.isHidden() and not dashboard.setup_button.isChecked()


def test_l22_no_unit_after_an_empty_dash(qapp):
    class UnitPanel(Panel):
        NAME = "Unit"
        PARAMS = {}

        def __init__(self):
            super().__init__()
            self.age = ""

        @property
        def schema(self):
            return sch.schema(sch.section("Info", sch.readonly("Position age (s):", "age"),
                                          tier=2, disclosure="Configure"))

    panel = UnitPanel()
    view = qt.QtPanelView(FakeController(panel), "Unit")
    try:
        view._set_tier_open(2, True)
        view._refresh()
        unit = next(w for w in view.findChildren(QLabel) if w.objectName() == "unit")
        assert unit.isHidden()
        panel.age = "1.5"
        view._refresh()
        assert not unit.isHidden()
    finally:
        view.close()


def test_l22_the_disclosure_tooltip_reads_as_a_sentence(tiered):
    view, _ = tiered
    assert view.tier_button.toolTip() == "Show or hide the Tiered settings"
    assert view.diag_button.toolTip() == "Show or hide the Tiered diagnostics"


def test_l22_a_wrapped_tray_line_hangs_clear_of_its_mark(dashboard):
    class Spoof:
        severity, source, title, message, count = ("warning", "T", "Board",
                                                   "cannot power down " * 12, 1)
        needs_ack = False
        text = ""

    dashboard._show_event(Spoof())
    block = dashboard.event_view.document().lastBlock()
    fmt = block.blockFormat()
    assert fmt.textIndent() < 0 and fmt.leftMargin() == -fmt.textIndent()


def test_l22_the_selected_rail_item_has_an_ink_rule_at_its_left():
    sheet = qt.stylesheet()
    checked = sheet.split("QPushButton#railModel:checked {")[1].split("}")[0]
    assert f"border-left: 2px solid {theme.TEXT}" in checked


def test_l5_on_the_device_page_only_the_well_scrolls(six, qapp):
    """QT7-15: a long well scrolled tier 1 away. On the device page the head
    and tier 1 stay; the well scrolls inside."""
    six.resize(1400, 560)
    _pump(qapp)
    six.open_entry("Stepper Probe")
    panel = six._panels["Stepper Probe"]
    panel.tier_button.click()
    panel.diag_button.click()
    for _ in range(3):
        _pump(qapp)
        six._arrange_entries()
    _pump(qapp, 10)
    assert six.sheet_scroll.verticalScrollBar().maximum() == 0
    assert panel.well_scroll.isVisible()
    assert panel.well_scroll.verticalScrollBar().maximum() > 0
    step = next(panel._widget_for(e) for e in panel._elements
                if e.get("command") == "step_once")
    top = step.mapTo(six.sheet_scroll.viewport(), QPoint(0, 0)).y()
    assert 0 <= top and top + step.height() <= six.sheet_scroll.viewport().height()
    six.show_overview()
    _pump(qapp)
    assert not panel.well_scroll.isVisible()


def test_l22_setups_status_words_start_with_a_capital(qapp):
    """QT7-17: "not scanned yet", "simulated", "off" in Setup's table."""
    setup = TablePanel()
    setup.scan_status = "not scanned yet"
    view = qt.QtPanelView(FakeController(setup), "Setup", panel=setup)
    try:
        view._refresh()
        status = view._widget_for(element_named(view, "scan_status"))
        assert status.full_text() if hasattr(status, "full_text") else status.text()
        assert status.text() == "Not scanned yet"
    finally:
        view.close()
