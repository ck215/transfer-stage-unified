"""The four schema composites, rendered (S10 item 2).

`region_select`, `file_save`, `plot` and `log_stream` are the element types
that replaced three view-side shims — `set_focus_area_ui`, `plot_data_ui` and
`save_log_web` — each of which stood in for something the schema could not
express and therefore existed in one frontend only. Their whole justification
is that one declaration now means the same thing in three renderers.

Nothing checked that. `tests/ui/test_schema_v2.py` proves a composite's
`command`, `data_command` and `source_command` *resolve* on the model; it
never builds a widget, so it cannot see a renderer that ignores the field it
resolved. Both defects this file was written to catch were of exactly that
kind: PySide's `region_select` wrote `model.focus_area` itself and never ran
the declared command, and its `plot` opened a CSV file dialog without ever
reading `data_command`.

Each composite is therefore asserted twice — once per desktop renderer — with
the *same* expectation, plus a serialisation check standing in for the Web
client, which receives the schema as JSON over the wire.
"""
import json
from unittest.mock import patch

import pytest

from model import schema as sch
from model.base import SchemaCommands


class CompositeModel(SchemaCommands):
    """One model declaring all four composites, so both renderers get the
    same declaration and any disagreement is theirs, not the schema's."""

    def __init__(self):
        self.focus_area = None
        self.saved_to = None
        self.lines = []
        self.values = []

    @property
    def ui_schema(self):
        return sch.schema(sch.section(
            "Composites",
            sch.region_select("Set Focus Area", "set_focus_area",
                              model_attr="focus_area"),
            sch.file_save("Save Log", "save_log"),
            sch.plot("Values", "plot_series", x_label="n", y_label="v"),
            sch.log_stream("Log:", "log_lines"),
        ))

    def set_focus_area(self, x, y, w, h):
        self.focus_area = {"left": x, "top": y, "width": w, "height": h}
        return True

    def save_log(self, file_path=None):
        self.saved_to = file_path
        return True

    def plot_series(self):
        return {"x": list(range(len(self.values))), "y": list(self.values)}

    def log_lines(self):
        return list(self.lines)


def _element(model, kind):
    return next(el for el in sch.elements(model.ui_schema)
                if el["type"] == kind)


# ---------------------------------------------------------------------------
# PySide
# ---------------------------------------------------------------------------

@pytest.fixture
def pyside_view(qtbot):
    from views.pyside.view import QtDynamicView
    model = CompositeModel()
    view = QtDynamicView(model)
    qtbot.addWidget(view)
    yield model, view
    view.cleanup()


def test_pyside_region_select_runs_the_declared_command(pyside_view):
    """The overlay reports a rectangle; the *model* records it.

    This arm used to hand the overlay the model and let it assign
    `model.focus_area` directly, so `set_focus_area` never ran in PySide. The
    distinction is not cosmetic: a model that validates, clamps or refuses a
    region had no say in this renderer.
    """
    model, view = pyside_view

    view._run_element(_element(model, "region_select"), args=(10, 20, 30, 40))
    assert model.focus_area == {"left": 10, "top": 20, "width": 30, "height": 40}


def test_pyside_region_select_shows_what_was_captured(pyside_view):
    """known-issues #9, without the modal that fixed it the first time.

    The drag gave no on-screen confirmation, and the 2026-09-18 fix for that
    was a `QMessageBox.information` — raised over a frameless always-on-top
    overlay, which is PYSIDE-12's deadlock and one of the two dialogs that
    hung this suite. The region is drawn instead, from the `model_attr` the
    composite has always declared.
    """
    model, view = pyside_view
    label = view._regions[0]["widget"]
    assert label.text() == "not set"

    view._run_element(_element(model, "region_select"), args=(10, 20, 30, 40))
    view._poll_model()
    assert label.text() == "30x40 at (10, 20)"


def test_tk_region_select_shows_the_same_wording(tk_view):
    """Same wording as PySide, from the same `schema.format_region`.

    Asserted through `StringVar.set`, because the Tk harness's `StringVar` is
    a mock and `get()` returns a mock rather than what was last set.
    """
    model, view = tk_view
    var = view._regions[0]["var"]
    # DynamicView polls once from __init__, so the empty state is already
    # rendered before the operator has dragged anything.
    assert var.set.call_args[0][0] == "not set"

    view._run_element(_element(model, "region_select"), args=(10, 20, 30, 40))
    view._poll_model()
    assert var.set.call_args[0][0] == "30x40 at (10, 20)"


def test_pyside_file_save_passes_the_chosen_path_and_respects_cancel(pyside_view):
    model, view = pyside_view
    element = _element(model, "file_save")

    with patch("PySide6.QtWidgets.QFileDialog.getSaveFileName",
               return_value=("/tmp/out.csv", "")):
        view._run_element(element, args=("/tmp/out.csv",))
    assert model.saved_to == "/tmp/out.csv"

    # A cancelled dialog returns "" and must run nothing at all — saving to
    # the empty path is how a "cancel" quietly writes a file named "".
    from PySide6.QtWidgets import QPushButton
    model.saved_to = None
    buttons = [w for w in view.findChildren(QPushButton)
               if w.text() == "Save Log"]
    assert len(buttons) == 1, (
        "the file_save composite should render exactly one labelled button; "
        f"found {len(buttons)}")
    button = buttons[0]
    with patch("PySide6.QtWidgets.QFileDialog.getSaveFileName",
               return_value=("", "")):
        button.click()
    assert model.saved_to is None


def test_pyside_plot_draws_the_model_series(pyside_view):
    """`data_command`, not a file dialog.

    This arm rendered a button that opened `PlotDialog` — a CSV loader — and
    never read `data_command`, while Tk and the Web client both drew the live
    series. The composite meant two different things.
    """
    from views.pyside.view import SeriesPlot
    model, view = pyside_view

    assert len(view._plots) == 1
    widget = view._plots[0]["widget"]
    assert isinstance(widget, SeriesPlot)

    model.values = [1.0, 4.0, 2.0]
    view._poll_model()
    assert widget._ys == [1.0, 4.0, 2.0]

    # A series too short to draw is not an error, and does not keep stale
    # points on screen.
    model.values = []
    view._poll_model()
    assert widget._ys == []


def test_pyside_log_stream_follows_the_model_buffer(pyside_view):
    model, view = pyside_view
    widget = view._log_streams[0]["widget"]
    assert widget.isReadOnly()

    model.lines.extend(["first", "second"])
    view._poll_model()
    assert "first" in widget.toPlainText()
    assert "second" in widget.toPlainText()


# ---------------------------------------------------------------------------
# Tkinter
# ---------------------------------------------------------------------------

@pytest.fixture
def tk_view():
    import tkinter as tk
    from views.tkinter.view import DynamicView
    model = CompositeModel()
    with patch.object(tk.Frame, "register", return_value="mock_vcmd",
                      create=True):
        view = DynamicView(tk.Frame(), model)
    return model, view


def test_tk_region_select_runs_the_declared_command(tk_view):
    model, view = tk_view
    view._run_element(_element(model, "region_select"), args=(10, 20, 30, 40))
    assert model.focus_area == {"left": 10, "top": 20, "width": 30, "height": 40}


def test_tk_file_save_passes_the_chosen_path(tk_view):
    model, view = tk_view
    view._run_element(_element(model, "file_save"), args=("/tmp/out.csv",))
    assert model.saved_to == "/tmp/out.csv"


def test_tk_plot_reads_the_same_data_command(tk_view):
    model, view = tk_view
    assert len(view._plots) == 1
    entry = view._plots[0]
    assert entry["element"]["data_command"] == "plot_series"

    model.values = [1.0, 4.0, 2.0]
    view._redraw_plot(entry)
    entry["widget"].create_line.assert_called()

    # Fewer than two points cannot be a line, and must not raise.
    entry["widget"].create_line.reset_mock()
    model.values = [1.0]
    view._redraw_plot(entry)
    entry["widget"].create_line.assert_not_called()


def test_tk_log_stream_reads_the_same_source_command(tk_view):
    model, view = tk_view
    assert len(view._log_streams) == 1
    entry = view._log_streams[0]
    assert entry["element"]["source_command"] == "log_lines"

    model.lines.append("first")
    view._refresh_log(entry)
    entry["widget"].insert.assert_called()


# ---------------------------------------------------------------------------
# The Web client gets the same declaration, over the wire
# ---------------------------------------------------------------------------

def test_every_composite_survives_json_untouched():
    """The Web renderer reads the schema as JSON, so a composite that cannot
    serialise is a composite that frontend does not have. Builders return
    plain dicts for exactly this reason; this is what pins it."""
    model = CompositeModel()
    round_tripped = json.loads(json.dumps(model.ui_schema))
    assert round_tripped == model.ui_schema

    by_type = {el["type"]: el for el in sch.elements(round_tripped)}
    assert set(by_type) == {"region_select", "file_save", "plot", "log_stream"}
    assert by_type["plot"]["data_command"] == "plot_series"
    assert by_type["log_stream"]["source_command"] == "log_lines"
    assert by_type["file_save"]["command"] == "save_log"
    assert by_type["region_select"]["command"] == "set_focus_area"


def test_the_real_red_percent_composites_serialise_too():
    """The synthetic model above proves the contract; this proves the device
    that actually uses all four still meets it."""
    from model.redpercent_system import RedPercentSystem
    model = RedPercentSystem()
    schema_dict = model.ui_schema
    assert json.loads(json.dumps(schema_dict)) == schema_dict

    composites = {el["type"] for el in sch.elements(schema_dict)
                  if el["type"] in ("region_select", "file_save", "plot",
                                    "log_stream")}
    assert composites == {"region_select", "file_save", "plot"}
