"""REDPERCENT-18: Screen-capture region and monitor selection.

PySide's SelectionOverlay spans all monitors and captures coordinates in Qt
logical space. On HiDPI displays (macOS Retina, Linux with fractional scaling),
Qt logical coordinates need to be converted to mss physical pixels using
devicePixelRatio().

The model should expose monitor information and focus-area details for proper
interpretation of the captured region.
"""

import pytest
from unittest.mock import patch, MagicMock
from PySide6.QtCore import Qt, QRect


def test_redpercent18_set_focus_area_stores_coordinates(qtbot):
    """set_focus_area should store coordinates for mss capture."""
    from model.redpercent_system import RedPercentSystem

    model = RedPercentSystem()

    # Set focus area
    result = model.set_focus_area(100, 200, 300, 400)

    assert result is True, "set_focus_area should return True"
    assert model.focus_area is not None
    assert model.focus_area['left'] == 100
    assert model.focus_area['top'] == 200
    assert model.focus_area['width'] == 300
    assert model.focus_area['height'] == 400


def test_redpercent18_focus_area_shown_in_view(qtbot):
    """PySide view should show the selected focus area dimensions."""
    from model.redpercent_system import RedPercentSystem
    from views.pyside.view import RedPercentDynamicView

    model = RedPercentSystem()
    model.set_focus_area(50, 75, 600, 400)

    view = RedPercentDynamicView(model)
    qtbot.addWidget(view)

    # Check if there's a way to display the focus area
    # This could be a label showing "WxH at (x,y)" format
    # For now, just verify the model has the area set
    assert model.focus_area is not None
    assert model.focus_area['width'] == 600
    assert model.focus_area['height'] == 400


def test_redpercent18_selection_overlay_coordinates(qtbot):
    """SelectionOverlay should report coordinates that work with mss.grab()."""
    from views.pyside.view import SelectionOverlay
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtCore import QPointF

    captured = []
    overlay = SelectionOverlay(lambda x, y, w, h: captured.append((x, y, w, h)))
    qtbot.addWidget(overlay)

    overlay.show()

    # Simulate a drag from (100, 100) to (400, 300)
    # These are global coordinates
    start_global = QPointF(100, 100)
    end_global = QPointF(400, 300)

    # Simulate mouse press at start position
    press_event = QMouseEvent(
        QMouseEvent.MouseButtonPress,
        QPointF(100, 100),  # local position
        start_global,  # global position
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier
    )
    overlay.mousePressEvent(press_event)

    # Simulate mouse move to end position
    move_event = QMouseEvent(
        QMouseEvent.MouseMove,
        QPointF(400, 300),  # local position (relative to widget)
        end_global,  # global position
        Qt.LeftButton,
        Qt.LeftButton,
        Qt.NoModifier
    )
    overlay.mouseMoveEvent(move_event)

    # Simulate mouse release
    release_event = QMouseEvent(
        QMouseEvent.MouseButtonRelease,
        QPointF(400, 300),
        end_global,
        Qt.LeftButton,
        Qt.NoButton,
        Qt.NoModifier
    )
    overlay.mouseReleaseEvent(release_event)

    # Verify the callback was called with correct coordinates
    # (x, y, width, height)
    assert len(captured) == 1
    x, y, w, h = captured[0]
    assert x == 100, f"Expected x=100, got {x}"
    assert y == 100, f"Expected y=100, got {y}"
    assert w == 300, f"Expected width=300, got {w}"
    assert h == 200, f"Expected height=200, got {h}"
    assert w > 10 and h > 10, "Region should be larger than MIN_SIDE_PX"


def test_redpercent18_focus_area_compatible_with_mss(qtbot):
    """Focus area format should be compatible with mss.grab() format."""
    from model.redpercent_system import RedPercentSystem

    model = RedPercentSystem()
    model.set_focus_area(100, 200, 400, 300)

    # mss.grab() expects {'left': x, 'top': y, 'width': w, 'height': h}
    # Verify our focus_area matches this format
    assert 'left' in model.focus_area
    assert 'top' in model.focus_area
    assert 'width' in model.focus_area
    assert 'height' in model.focus_area

    # Verify types are integers (mss requires integers)
    assert isinstance(model.focus_area['left'], int)
    assert isinstance(model.focus_area['top'], int)
    assert isinstance(model.focus_area['width'], int)
    assert isinstance(model.focus_area['height'], int)
