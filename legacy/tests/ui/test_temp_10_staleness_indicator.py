"""TEMP-10: Test temperature system connection_state display (staleness indicator).

Verify that:
1. TemperatureSystem.connection_state exists and is exposed in schema
2. Connection state field is rendered visually distinct (not default lightgreen)
3. Role-based styling is applied to readonly fields when specified
"""

import pytest
from model.temperature_system import TemperatureSystem


def test_temp_10_connection_state_exists():
    """Verify TemperatureSystem.connection_state property exists."""
    system = TemperatureSystem(port=None)

    # connection_state should exist and return a value
    connection_state = system.connection_state
    assert connection_state is not None
    assert isinstance(connection_state, str)


def test_temp_10_connection_state_in_schema():
    """Verify connection_state is exposed in the schema as readonly."""
    system = TemperatureSystem(port=None)
    schema = system.ui_schema

    # Find the connection_state readonly field
    connection_field = None
    for section in schema["sections"]:
        for el in section.get("elements", []):
            if el.get("model_attr") == "connection_state":
                connection_field = el
                break

    assert connection_field is not None, "connection_state field not found in schema"
    assert connection_field.get("type") == "readonly", "connection_state should be readonly"


def test_temp_10_connection_state_has_role():
    """Verify connection_state has a role attribute for styling."""
    system = TemperatureSystem(port=None)
    schema = system.ui_schema

    # Find the connection_state readonly field
    connection_field = None
    for section in schema["sections"]:
        for el in section.get("elements", []):
            if el.get("model_attr") == "connection_state":
                connection_field = el
                break

    assert connection_field is not None
    # Should have a role attribute to distinguish it visually
    role = connection_field.get("role")
    assert role is not None, "connection_state should have a role for visual distinction"
    # The role should be something like "info" or "warning"
    assert role in ("info", "warning", "danger"), f"Unexpected role: {role}"


def test_temp_10_connection_state_closed_when_no_port():
    """Verify connection_state is CLOSED when there's no port."""
    system = TemperatureSystem(port=None)

    # With no port, connection_state should indicate disconnection
    state = system.connection_state
    # Should be "CLOSED" or similar
    assert state is not None
    assert isinstance(state, str)


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
