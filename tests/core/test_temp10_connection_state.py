"""TEMP-10: connection_state property exposure.

Stale/blank readout states: SIM or no-port shows "N/A" forever. The
connection_state property already exists in the model; this test credits it
and verifies it correctly reflects the transport's link state.
"""
import pytest
from unittest.mock import patch, MagicMock

from model.temperature_system import TemperatureSystem


class TestConnectionState:
    """Tests for the connection_state property (TEMP-10, already implemented)."""

    def test_connection_state_closed_when_no_port(self):
        """connection_state returns CLOSED when no port is configured."""
        ts = TemperatureSystem(port=None)
        assert ts.connection_state == "CLOSED", (
            "connection_state should return CLOSED when serial_conn is None")

    def test_connection_state_closed_with_none_string_port(self):
        """connection_state returns CLOSED for port='None'."""
        ts = TemperatureSystem(port="None")
        assert ts.connection_state == "CLOSED", (
            "connection_state should return CLOSED for port='None'")

    def test_connection_state_reflects_transport_state(self):
        """connection_state returns the transport's connection_state.name."""
        with patch("model.temperature_system.serial") as mock_serial_cls:
            mock_conn = MagicMock()
            mock_conn.is_open.return_value = True

            # Mock the connection_state attribute with an enum-like object
            mock_state = MagicMock()
            mock_state.name = "OPEN"
            mock_conn.connection_state = mock_state
            mock_serial_cls.return_value = mock_conn

            ts = TemperatureSystem(port="COM4")
            assert ts.connection_state == "OPEN", (
                "connection_state should reflect transport's connection_state.name")
            ts.close()

    def test_connection_state_in_schema(self):
        """connection_state is exposed in the UI schema."""
        ts = TemperatureSystem(port=None)
        schema = ts.ui_schema

        # Check that the "Temperature Readings" section includes connection state
        readings_section = next(
            (s for s in schema["sections"] if s["title"] == "Temperature Readings"),
            None)
        assert readings_section is not None, (
            "Should have Temperature Readings section")

        # Check for connection status element by model_attr
        connection_fields = [
            e for e in readings_section["elements"]
            if e.get("model_attr") == "connection_state"]
        assert len(connection_fields) > 0, (
            "Temperature Readings section should include connection_state")

    def test_connection_state_is_readonly(self):
        """connection_state is readonly in the schema."""
        ts = TemperatureSystem(port=None)
        schema = ts.ui_schema

        readings_section = next(
            (s for s in schema["sections"] if s["title"] == "Temperature Readings"),
            None)

        connection_fields = [
            e for e in readings_section["elements"]
            if e.get("model_attr") == "connection_state"]

        if connection_fields:
            assert connection_fields[0]["type"] == "readonly", (
                "connection_state should be readonly")
            assert connection_fields[0]["writable"] is False, (
                "connection_state should be non-writable")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
