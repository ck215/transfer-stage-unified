"""Test ROTATOR-9: Verify PySide formats position like Tk.

This test verifies that the PySide view formats rotator position the same way
as Tk does: "--.--" for None (no reading state) and 4 decimal places for numeric values.
"""

import pytest
from unittest.mock import MagicMock
from model.rotator_system import RotatorSystem
from views.pyside.view import QtDynamicView


def test_rotator_9_pyside_display_none_as_dashes():
    """PySide's _display method should render None position as '--.--' like Tk."""
    rotator = RotatorSystem(default_port=None)
    rotator.position = None

    # Create a mock PySide view to test the _display method
    view = MagicMock(spec=QtDynamicView)
    view._display = QtDynamicView._display.__get__(view)
    view.model = rotator

    # Test that None is displayed as "--.--"
    display_value = view._display("position")
    assert display_value == "--.--", f"Expected '--.--' for None position, got '{display_value}'"


def test_rotator_9_pyside_display_formats_to_4_decimals():
    """PySide's _display method should format position to 4 decimal places."""
    rotator = RotatorSystem(default_port=None)

    view = MagicMock(spec=QtDynamicView)
    view._display = QtDynamicView._display.__get__(view)
    view.model = rotator

    test_cases = [
        (0.0, "0.0000"),
        (12.3456789, "12.3457"),  # Should round to 4 decimals
        (12.0, "12.0000"),
        (0.1, "0.1000"),
        (-45.678, "-45.6780"),
    ]

    for value, expected_display in test_cases:
        rotator.position = value
        display_value = view._display("position")
        assert display_value == expected_display, \
            f"Expected '{expected_display}' for position {value}, got '{display_value}'"
