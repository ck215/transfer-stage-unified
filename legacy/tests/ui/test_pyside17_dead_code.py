"""PYSIDE-17: dead code inventory in `views/pyside/view.py` and `style.qss`.

Two kinds of check live here. Most of PYSIDE-17's sub-items were already
dead code with nothing rendering them, so removing them is a structural
change with no runtime behaviour to exercise — those are asserted by
grepping the shipped source, the same evidence used to justify deleting
them in the first place (a source-of-truth test, not a placeholder).

The one sub-item with an actual runtime effect is the `"type": "internal"`
schema element: before the fix it fell through the `elif` chain in
`QtDynamicView._build_ui` and still reached `card_layout.addRow(row_layout)`
with a never-populated, empty `QHBoxLayout` — adding a blank spaced row to
every card that declares one. That one needs a real widget, so it is
skipped whenever Qt cannot be brought up in this process (see
`tests/conftest.py`'s macOS Qt probe); the harness marks it `qt` via the
`qtbot` fixture either way, so it never runs in the fast gate.
"""
import re

import pytest


VIEW_PY = "src/views/pyside/view.py"
STYLE_QSS = "src/views/pyside/style.qss"


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def test_unused_imports_are_gone_from_pyside_view():
    """`csv`, `Figure` and `QCheckBox` were imported and never referenced.

    `csv` module functions (`csv.reader`, etc.) are never called — CSV
    parsing was already delegated to `model.plot_data`. `Figure` (imported
    directly from `matplotlib.figure`) is never referenced; the module only
    uses `FigureCanvasQTAgg`/`NavigationToolbar2QT`. `QCheckBox` is never
    instantiated since the sync checkboxes it backed were removed.
    """
    src = _read(VIEW_PY)
    assert not re.search(r"^import csv\s*$", src, re.MULTILINE), (
        "`import csv` should be gone; nothing in view.py calls csv.*")
    assert "from matplotlib.figure import Figure" not in src
    assert "QCheckBox" not in src


def test_orphaned_qss_selectors_are_gone():
    """`filePicker` / `fileLabel` backed the dead `file_picker` branch

    (already removed under PYSIDE-11), and the `QCheckBox` rules backed
    checkboxes removed under finding #5. None of the three object names is
    ever set in view.py and `QCheckBox` is never instantiated there, so
    these selectors style nothing.
    """
    qss = _read(STYLE_QSS)
    assert "fileLabel" not in qss
    assert "filePicker" not in qss
    assert "QCheckBox" not in qss


def test_self_layout_no_longer_shadows_qwidget_layout():
    """`self.layout = QVBoxLayout(...)` shadowed `QWidget.layout()` in three
    classes (`ControllerLogWindow`, `QtDynamicView`, `PlotDialog`). Nothing
    called the real `.layout()` accessor (grep-verified: no `.layout()`
    call site existed anywhere in the file), so the rename to `self._layout`
    is behaviour-preserving and only removes the shadow.
    """
    src = _read(VIEW_PY)
    assert re.search(r"self\.layout\s*=", src) is None, (
        "self.layout assignment should be renamed to self._layout")
    assert ".layout()" not in src, (
        "no call site relied on the real QWidget.layout() accessor before "
        "or after the rename")
    assert src.count("self._layout") >= 6


@pytest.fixture
def qt_internal_view(qtbot):
    from model.base import SchemaCommands
    from views.pyside.view import QtDynamicView

    class InternalOnlyModel(SchemaCommands):
        @property
        def ui_schema(self):
            return {
                "version": 2,
                "sections": [{
                    "title": "Section",
                    "elements": [
                        {"type": "internal", "text": "", "command": "noop"},
                    ],
                }],
            }

        def noop(self):
            return True

    model = InternalOnlyModel()
    view = QtDynamicView(model)
    qtbot.addWidget(view)
    yield view
    view.cleanup()


def test_internal_schema_element_adds_no_blank_row(qt_internal_view):
    """Before the fix, an `internal` element fell through to the shared
    `card_layout.addRow(row_layout)` with an empty, never-populated
    `QHBoxLayout` — one blank spaced row per internal element. The card
    should have exactly its title row and nothing else.
    """
    view = qt_internal_view
    from PySide6.QtWidgets import QFrame, QFormLayout
    cards = view.findChildren(QFrame)
    assert cards, "expected the section to render one QFrame card"
    form = cards[0].layout()
    assert isinstance(form, QFormLayout)
    # Only the title row (`addRow(lbl_title)`) should be present; the
    # internal element must not have added a second, blank row.
    assert form.rowCount() == 1
