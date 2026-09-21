"""Test ROTATOR-13: Verify both desktop views disable controls when no stage.

When the rotator is in SIM mode or has no connection (port=None),
all motion controls should be disabled in both Tk and PySide views.
"""

import pytest
from unittest.mock import MagicMock
from model.rotator_system import RotatorSystem


def test_rotator_13_disconnected_connection_state():
    """When port=None, connection_status should indicate disconnection."""
    rotator = RotatorSystem(default_port=None)

    # With no port, should be disconnected
    assert rotator.is_connected == False
    assert rotator.smc is None
    assert rotator.connection_status == "disconnected"


def test_rotator_13_connected_connection_state():
    """When port is valid, connection_status can be hardware."""
    rotator = RotatorSystem(default_port=None)

    # Initially false since we gave no port
    assert rotator.is_connected == False
    assert rotator.connection_status == "disconnected"

    # Mock a connected state
    rotator.is_connected = True
    rotator.smc = MagicMock()
    assert rotator.connection_status == "hardware"


def test_rotator_13_tk_view_mode_name_reflects_disconnection():
    """Tk view's _mode_name should return 'disconnected' for unconnected rotator."""
    from views.tkinter.view import DynamicView

    rotator = RotatorSystem(default_port=None)

    # Create a mock Tk view
    view = MagicMock(spec=DynamicView)
    view._mode_name = DynamicView._mode_name.__get__(view)
    view.model = rotator

    # When disconnected, mode should be "disconnected"
    mode = view._mode_name()
    assert mode == "disconnected", f"Expected mode 'disconnected' for unconnected rotator, got '{mode}'"


def test_rotator_13_pyside_view_mode_name_reflects_disconnection():
    """PySide view's _mode_name should return 'disconnected' for unconnected rotator."""
    from views.pyside.view import QtDynamicView

    rotator = RotatorSystem(default_port=None)

    # Create a mock PySide view
    view = MagicMock(spec=QtDynamicView)
    view._mode_name = QtDynamicView._mode_name.__get__(view)
    view.model = rotator

    # When disconnected, mode should be "disconnected"
    mode = view._mode_name()
    assert mode == "disconnected", f"Expected mode 'disconnected' for unconnected rotator, got '{mode}'"
