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
        # REDPERCENT-6 routed `save_log_ui` through the model instead of
        # reaching past it into `data_log`, so the double needs the method
        # the view now calls. The suffix behaviour PYSIDE-18 pins is
        # unchanged; only the collaborator it is observed on moved.
        self.save_log = MagicMock()


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
    model.save_log.assert_called_once_with("/tmp/mylog.csv")


def test_save_log_ui_leaves_an_explicit_suffix_alone(red_percent_view):
    view, model = red_percent_view
    with patch("PySide6.QtWidgets.QFileDialog.getSaveFileName",
               return_value=("/tmp/mylog.csv", "")):
        view.save_log_ui()
    model.save_log.assert_called_once_with("/tmp/mylog.csv")


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
         patch("error_routing.ErrorRouter.report_error") as crit, \
         patch.object(type(plot_dialog), "select_plot_type") as select_plot_type:
        plot_dialog.load_csv()

    select_plot_type.assert_not_called()
    # ERRORS-9 moved this surface off QMessageBox and onto the bus. The
    # rejection itself is what PYSIDE-18 pinned and it still holds — what
    # changed is only *where* the operator is told, which is the whole point
    # of the finding. Asserting on QMessageBox here would re-pin the direct
    # dialog ERRORS-9 exists to remove.
    crit.assert_called_once()
    assert "Invalid CSV File" in crit.call_args[0][0]


def test_load_csv_opens_the_file_with_newline_empty_string(plot_dialog):
    with patch("PySide6.QtWidgets.QFileDialog.getOpenFileName",
               return_value=("/tmp/data.csv", "")), \
         patch("builtins.open", mock_open(read_data=_ZERO_DIM_CSV_WITH_DATA)) as m, \
         patch.object(type(plot_dialog), "select_plot_type"):
        plot_dialog.load_csv()

    m.assert_called_once_with("/tmp/data.csv", "r", newline="")


class _FakeAx:
    def __init__(self, title):
        self._title = title

    def get_title(self):
        return self._title

    def set_title(self, title):
        self._title = title


class _FakeFig:
    """Stands in for the Figure `render_red_percent_figure` returns.

    `tests/conftest.py` mocks `matplotlib` itself for the whole suite, so a
    real Figure is not available here and a MagicMock is worse than useless
    for this particular check: `for ax in fig.axes` over a MagicMock
    iterates *empty*, so the title loop would silently not run and the test
    would pass against code that does nothing.
    """

    def __init__(self):
        self.axes = [_FakeAx("Red Percent over Time")]


def test_draw_plot_appends_probe_metadata_to_the_title(plot_dialog, tmp_path):
    """The CSV's metadata block (probe name, tilt) reaches the plot title.

    `parsed["metadata"]` was read and discarded; the title never showed it.
    Runs the real path -- load_csv parses a real file, and `dims_found` is
    empty so `select_plot_type` goes straight to `draw_plot("0D", ...)`
    without a modal dialog.
    """
    csv_path = tmp_path / "data.csv"
    csv_path.write_text(_ZERO_DIM_CSV_WITH_DATA)
    fig = _FakeFig()

    with patch("PySide6.QtWidgets.QFileDialog.getOpenFileName",
               return_value=(str(csv_path), "")), \
         patch("model.plot_data.render_red_percent_figure", return_value=fig), \
         patch.object(plot_dialog, "plot_frame", MagicMock()):
        plot_dialog.load_csv()

    title = fig.axes[0].get_title()
    assert "Red Percent over Time" in title, title
    assert "Rig 1" in title, title
    assert "45" in title, title
