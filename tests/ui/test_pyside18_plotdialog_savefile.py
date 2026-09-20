"""PYSIDE-18: PlotDialog / save-file details that diverged from the Tk
reference.

All four sub-items need a real `QDialog`/`QWidget` (PlotDialog, or
RedPercentDynamicView for `save_log_ui`), so every test here uses the
`qtbot` fixture and is `qt`-marked by the harness — none of it runs in the
fast gate. Each test is written to fail against the pre-fix code for a
concrete, checked reason (not just "it looks different"), so the failure
mode is documented even though it cannot be observed from this session.

- `save_log_ui` used `QFileDialog.getSaveFileName` with a filter but no
  default suffix; Qt does not enforce one itself (unlike Tk's
  `defaultextension=".csv"`), so a bare filename on Linux saved with no
  extension at all.
- `PlotDialog.load_csv` rejected a file only when both `dims` and
  `red_percents` were empty, so a CSV with a `Stepper X Location` column
  and zero data rows passed the guard and reached `select_plot_type`,
  which runs a *modal* dialog loop when `dims_found` is non-empty — the
  second of the two dialogs `tests/ui/test_composites.py` documents as
  having hung this suite (PYSIDE-12). The fixed guard rejects on
  `red_percents` alone and never reaches that dialog for this input.
- `load_csv` opened without `newline=''`, unlike `save_to_csv` and the Tk
  reference.
- `parsed["metadata"]` (probe name, tilt) was read from the CSV and
  discarded; the plot title never reflected it.
"""
from types import SimpleNamespace
from unittest.mock import MagicMock, mock_open, patch

import pytest


class FakeRedPercentModel:
    """Just enough of RedPercentSystem for `save_log_ui` and `_build_ui`."""

    def __init__(self, has_data=True, probe_name="Rig 1"):
        self.ui_schema = {"sections": []}
        self.probe_name = probe_name
        self.has_unsaved_data = False
        self.data_log = SimpleNamespace(
            red_values=[1, 2, 3] if has_data else [],
            save_to_csv=MagicMock(),
        )


@pytest.fixture
def red_percent_view(qtbot):
    from views.pyside.view import RedPercentDynamicView
    model = FakeRedPercentModel()
    view = RedPercentDynamicView(model)
    qtbot.addWidget(view)
    yield view, model
    view.cleanup()


@pytest.fixture
def plot_dialog(qtbot):
    from views.pyside.view import PlotDialog
    dialog = PlotDialog(parent=None)
    qtbot.addWidget(dialog)
    yield dialog


# ---------------------------------------------------------------------------
# save_log_ui
# ---------------------------------------------------------------------------

def test_save_log_ui_appends_csv_when_the_chosen_name_has_no_suffix(red_percent_view):
    view, model = red_percent_view
    with patch("PySide6.QtWidgets.QFileDialog.getSaveFileName",
               return_value=("/tmp/mylog", "")):
        view.save_log_ui()
    model.data_log.save_to_csv.assert_called_once_with("/tmp/mylog.csv")


def test_save_log_ui_leaves_an_explicit_suffix_alone(red_percent_view):
    view, model = red_percent_view
    with patch("PySide6.QtWidgets.QFileDialog.getSaveFileName",
               return_value=("/tmp/mylog.csv", "")):
        view.save_log_ui()
    model.data_log.save_to_csv.assert_called_once_with("/tmp/mylog.csv")


# ---------------------------------------------------------------------------
# PlotDialog.load_csv
# ---------------------------------------------------------------------------

_HEADER_ONLY_WITH_DIMS_CSV = (
    "# Probe Name,Rig 1\n"
    "# Probe Tilt Angle,45\n"
    "\n"
    "Red Percent,Stepper X Location,Stepper X Velocity\n"
)

_ZERO_DIM_CSV_WITH_DATA = (
    "# Probe Name,Rig 1\n"
    "# Probe Tilt Angle,45\n"
    "\n"
    "Red Percent\n"
    "10.5\n"
    "20.0\n"
)


def test_load_csv_rejects_a_header_only_file_even_with_a_dims_column(plot_dialog):
    """`dims` is non-empty (a `Stepper X Location` column exists) but there
    is no data, so `red_percents` is empty. The old guard
    (`not dims and not red_percents`) passed this through to
    `select_plot_type`, which — because `dims_found` is non-empty — runs a
    modal dialog loop with nothing to answer it. The fixed guard
    (`not red_percents`) must reject before that dialog is ever created.
    """
    with patch("PySide6.QtWidgets.QFileDialog.getOpenFileName",
               return_value=("/tmp/empty.csv", "")), \
         patch("builtins.open", mock_open(read_data=_HEADER_ONLY_WITH_DIMS_CSV)), \
         patch("PySide6.QtWidgets.QMessageBox.critical") as crit, \
         patch.object(type(plot_dialog), "select_plot_type") as select_plot_type:
        plot_dialog.load_csv()

    select_plot_type.assert_not_called()
    crit.assert_called_once()
    assert "Invalid File" in crit.call_args[0][1]


def test_load_csv_opens_the_file_with_newline_empty_string(plot_dialog):
    with patch("PySide6.QtWidgets.QFileDialog.getOpenFileName",
               return_value=("/tmp/data.csv", "")), \
         patch("builtins.open", mock_open(read_data=_ZERO_DIM_CSV_WITH_DATA)) as m, \
         patch.object(type(plot_dialog), "select_plot_type"):
        plot_dialog.load_csv()

    m.assert_called_once_with("/tmp/data.csv", "r", newline="")


def test_draw_plot_appends_probe_metadata_to_the_title(plot_dialog):
    """No `Stepper * Location` column, so `dims_found` is empty and
    `select_plot_type` goes straight to `draw_plot("0D", ...)` without a
    modal dialog — safe to run end to end.
    """
    with patch("PySide6.QtWidgets.QFileDialog.getOpenFileName",
               return_value=("/tmp/data.csv", "")), \
         patch("builtins.open", mock_open(read_data=_ZERO_DIM_CSV_WITH_DATA)):
        plot_dialog.load_csv()

    assert plot_dialog.canvas is not None
    title = plot_dialog.canvas.figure.axes[0].get_title()
    assert "Rig 1" in title
    assert "45" in title
