"""REDPERCENT-17 (PySide half): `PlotDialog.select_plot_type` defaults and
cancel handling.

Two defects, per docs/architecture/audit/redpercent.md:

  * `dim1`/`dim2`/`dim3` combos all defaulted to index 0, so accepting a
    default 2D (or 3D) plot used the same dim on more than one axis.
  * Closing the dialog (Escape, the window's X button -- anything that does
    not go through the "Plot Data" button) aborted with no trace: `selected`
    stayed empty and the method just returned.

Needs a real `QDialog`/`QApplication` (the nested "Select Plot Type" dialog
`select_plot_type` builds), so this is `qt`-marked and not run by the agent
that wrote it -- see tests/ui/test_manager20_*.py for the same constraint.
The dialog's blocking call is patched per-test rather than actually run: a
real modal loop here would hang with nobody to click it, and pytest-qt's
`qtbot` cannot drive a dialog this method builds and discards internally
(it is a local variable, never returned).
"""
from unittest.mock import patch

import pytest
from PySide6.QtWidgets import QDialog, QPushButton

pytestmark = pytest.mark.qt


@pytest.fixture
def plot_dialog(qtbot):
    from views.pyside.view import PlotDialog
    dialog = PlotDialog()
    qtbot.addWidget(dialog)
    return dialog


def _accept_via_button(self):
    """Stand in for the dialog's blocking call: click "Plot Data" with
    whatever the combos default to, instead of running a real modal loop."""
    btn = self.findChild(QPushButton)
    assert btn is not None and btn.text() == "Plot Data"
    btn.click()
    return QDialog.DialogCode.Accepted


def _cancel(self):
    """Stand in for the operator closing the dialog without picking
    anything -- Escape, the window's close box, anything but the button."""
    return QDialog.DialogCode.Rejected


def test_default_2d_plot_uses_two_distinct_dims(plot_dialog):
    with patch.object(QDialog, "exec", _accept_via_button), \
         patch.object(type(plot_dialog), "draw_plot") as draw_plot:
        plot_dialog.select_plot_type(["Stepper X", "Stepper Y"], [1, 2, 3], {})

    draw_plot.assert_called_once()
    plot_type, dim1, dim2, dim3, _red_percents, _dim_data = draw_plot.call_args.args
    assert plot_type == "2D"
    assert dim1 != dim2, (
        "the default 2D plot used the same dim on both axes -- dim1 and "
        "dim2 combos both defaulted to index 0")


def test_default_3d_plot_uses_three_distinct_dims(plot_dialog):
    with patch.object(QDialog, "exec", _accept_via_button), \
         patch.object(type(plot_dialog), "draw_plot") as draw_plot:
        plot_dialog.select_plot_type(
            ["Stepper X", "Stepper Y", "Stepper Z"], [1, 2, 3], {})

    draw_plot.assert_called_once()
    plot_type, dim1, dim2, dim3, _red_percents, _dim_data = draw_plot.call_args.args
    assert plot_type == "3D"
    assert len({dim1, dim2, dim3}) == 3, (
        f"the default 3D plot did not use three distinct dims: "
        f"{dim1!r}, {dim2!r}, {dim3!r}")


def test_closing_the_dialog_reports_instead_of_aborting_silently(plot_dialog):
    with patch.object(QDialog, "exec", _cancel), \
         patch.object(type(plot_dialog), "draw_plot") as draw_plot, \
         patch("views.pyside.view.ErrorRouter") as router:
        plot_dialog.select_plot_type(["Stepper X", "Stepper Y"], [1, 2, 3], {})

    draw_plot.assert_not_called()
    assert router.report_info.called or router.report_warning.called, (
        "closing the dialog produced no report at all -- an operator who "
        "cancels has no way to tell that from a real failure elsewhere")
