"""ERRORS-9: PySide's CSV surfaces report through the bus, not a direct modal.

Written by the lead on merge. The `fix-gui` worktree made this change and
reported the row `closed` with "TEST: Routed through code inspection; no
standalone test created". Code inspection is not evidence for a code change,
and the project's rule is that a row closes on a test name or a verification
note — so the row stayed open until this file existed.

These are deliberately not qt-marked: they assert on which reporting function
the module calls, which does not need a QApplication. The agent could have
run them.
"""
import ast
from pathlib import Path

import pytest

_VIEW = Path(__file__).resolve().parents[2] / "src" / "views" / "pyside" / "view.py"


def _plotdialog_body():
    tree = ast.parse(_VIEW.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "PlotDialog":
            return node
    pytest.fail("PlotDialog is no longer in pyside/view.py")


def _calls_in(node):
    out = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
            owner = sub.func.value
            owner_name = getattr(owner, "id", None) or getattr(owner, "attr", None)
            out.append((owner_name, sub.func.attr))
    return out


def _method(cls, name):
    for node in cls.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    pytest.fail(f"PlotDialog.{name} is gone")


@pytest.mark.parametrize("method", ["load_csv", "save_plot_data"])
def test_the_csv_surfaces_do_not_raise_their_own_modal(method):
    """No QMessageBox.critical/information left on these paths."""
    cls = _plotdialog_body()
    try:
        node = _method(cls, method)
    except BaseException:
        pytest.skip(f"PlotDialog has no {method}")
    offenders = [(o, a) for (o, a) in _calls_in(node)
                 if o == "QMessageBox" and a in {"critical", "information", "warning"}]
    assert not offenders, (
        f"PlotDialog.{method} still raises its own dialog: {offenders}. "
        f"ERRORS-9 requires these go through the bus like every other surface")


def test_the_csv_load_path_reports_through_the_error_router():
    cls = _plotdialog_body()
    node = _method(cls, "load_csv")
    routed = [a for (o, a) in _calls_in(node) if o == "ErrorRouter"]
    assert routed, (
        "load_csv reports nothing through ErrorRouter, so a failed load is "
        "now silent — worse than the modal it replaced")
    assert "report_error" in routed
