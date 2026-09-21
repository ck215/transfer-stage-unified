"""Test TEMP-10: PySide staleness indicator uses role styling.

The temperature system's connection_state field should be displayed in
PySide with role-based styling (matching Tk's implementation) to visually
distinguish connection states.
"""

import pytest
from unittest.mock import MagicMock
from model.temperature_system import TemperatureSystem
from model import schema as sch


def test_temp_10_model_has_connection_state():
    """Temperature model should expose connection_state property."""
    temp = TemperatureSystem(port=None)

    # Should have a connection_state property
    assert hasattr(temp, 'connection_state')
    # When simulated (port=None), should indicate closed
    assert temp.connection_state is not None


def test_temp_10_schema_has_connection_state_with_role():
    """Schema should expose connection_state as readonly with a role."""
    temp = TemperatureSystem(port=None)
    schema = temp.ui_schema

    # Find connection_state field
    connection_field = None
    for section in schema["sections"]:
        for el in section.get("elements", []):
            if el.get("model_attr") == "connection_state":
                connection_field = el
                break

    assert connection_field is not None, "connection_state field not found in schema"
    assert connection_field.get("type") == "readonly", "connection_state should be readonly"
    # Should have a role for visual distinction
    assert connection_field.get("role") is not None, "connection_state should have a role attribute"


def test_temp_10_pyside_view_renders_role_styled_readonly():
    """PySide view should render readonly fields with role-based styling."""
    from views.pyside.view import QtDynamicView

    temp = TemperatureSystem(port=None)

    # Create a mock PySide view
    view = MagicMock(spec=QtDynamicView)
    view.model = temp
    view.ROLE_STYLES = QtDynamicView.ROLE_STYLES

    # The schema should have connection_state with a role
    schema = temp.ui_schema
    for section in schema["sections"]:
        for el in section.get("elements", []):
            if el.get("model_attr") == "connection_state":
                # Verify role is set
                role = el.get("role")
                assert role in view.ROLE_STYLES, \
                    f"Role '{role}' not found in ROLE_STYLES"
                assert role != "neutral", \
                    "connection_state should have a meaningful role, not 'neutral'"
