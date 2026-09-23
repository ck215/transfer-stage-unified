"""PYSIDE-12: SelectionOverlay needs instruction label, crosshair cursor, and focus methods.

The overlay is created as a frameless, always-on-top window that guides the user
to drag a rectangle. However, it's missing:
1. An instruction label ("Click and drag ... ESC to cancel")
2. A crosshair cursor
3. Focus and window activation calls in showEvent
"""

import pytest
from PySide6.QtGui import QMouseEvent, QCursor
from PySide6.QtCore import Qt, QPointF


def test_pyside_12_selection_overlay_has_instruction_label(qtbot):
    """Verify that SelectionOverlay displays an instruction label to the user."""
    from views.pyside.view import SelectionOverlay
    from PySide6.QtWidgets import QLabel

    captured = []
    overlay = SelectionOverlay(lambda x, y, w, h: captured.append((x, y, w, h)))
    qtbot.addWidget(overlay)

    # The overlay should have a label widget with instruction text
    # Look for a child QLabel widget with instruction text
    label_found = False
    for child in overlay.findChildren(QLabel):
        text = child.text()
        if text and ('click' in text.lower() or 'drag' in text.lower() or 'esc' in text.lower()):
            label_found = True
            break

    assert label_found, "Instruction label not found in SelectionOverlay"


def test_pyside_12_selection_overlay_has_crosshair_cursor(qtbot):
    """Verify that SelectionOverlay uses a crosshair cursor."""
    from views.pyside.view import SelectionOverlay

    overlay = SelectionOverlay(lambda x, y, w, h: None)
    qtbot.addWidget(overlay)

    overlay.show()

    # After showing, the cursor should be set to crosshair
    cursor = overlay.cursor()
    assert cursor.shape() == Qt.CursorShape.CrossCursor, \
        f"Expected CrossCursor, got {cursor.shape()}"


def test_pyside_12_selection_overlay_requests_focus_on_show(qtbot):
    """Verify that SelectionOverlay calls setFocus() and activateWindow() when shown."""
    from views.pyside.view import SelectionOverlay

    overlay = SelectionOverlay(lambda x, y, w, h: None)
    qtbot.addWidget(overlay)

    # Mock setFocus and activateWindow to verify they're called
    setfocus_called = False
    activatewindow_called = False

    original_setfocus = overlay.setFocus
    original_activatewindow = overlay.activateWindow

    def mock_setfocus():
        nonlocal setfocus_called
        setfocus_called = True
        original_setfocus()

    def mock_activatewindow():
        nonlocal activatewindow_called
        activatewindow_called = True
        original_activatewindow()

    overlay.setFocus = mock_setfocus
    overlay.activateWindow = mock_activatewindow

    overlay.show()

    # After showing, both methods should have been called
    assert setfocus_called, "setFocus() was not called in showEvent"
    assert activatewindow_called, "activateWindow() was not called in showEvent"


def test_pyside_12_selection_overlay_escape_closes_without_report(qtbot):
    """Verify that pressing Escape closes the overlay without reporting a region."""
    from views.pyside.view import SelectionOverlay

    captured = []
    overlay = SelectionOverlay(lambda x, y, w, h: captured.append((x, y, w, h)))
    qtbot.addWidget(overlay)

    overlay.show()

    # Simulate Escape key
    from PySide6.QtGui import QKeyEvent
    escape_event = QKeyEvent(
        QKeyEvent.Type.KeyPress,
        Qt.Key.Key_Escape,
        Qt.KeyboardModifier.NoModifier
    )
    overlay.keyPressEvent(escape_event)

    # Nothing should be reported
    assert captured == []
    # Window should be closed
    assert not overlay.isVisible()


def test_pyside_12_selection_overlay_small_drag_not_reported(qtbot):
    """Verify that small drags (below MIN_SIDE_PX) are not reported as regions."""
    from views.pyside.view import SelectionOverlay

    captured = []
    overlay = SelectionOverlay(lambda x, y, w, h: captured.append((x, y, w, h)))
    qtbot.addWidget(overlay)

    overlay.show()

    # Simulate a small drag (less than MIN_SIDE_PX on both axes)
    def drag(overlay, points):
        types = (QMouseEvent.Type.MouseButtonPress,
                 QMouseEvent.Type.MouseMove,
                 QMouseEvent.Type.MouseButtonRelease)
        handlers = (overlay.mousePressEvent, overlay.mouseMoveEvent,
                    overlay.mouseReleaseEvent)
        for kind, handler, (x, y) in zip(types, handlers, points):
            handler(QMouseEvent(
                kind, QPointF(x, y), QPointF(x, y),
                Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier))

    # Drag 5 pixels in each direction (less than MIN_SIDE_PX=10)
    drag(overlay, [(100, 100), (105, 105), (105, 105)])

    # Nothing should be reported for a small drag
    assert captured == []


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
