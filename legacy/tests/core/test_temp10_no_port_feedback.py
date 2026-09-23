"""TEMP-10 (model-state bug half): No-port or SIM start backs off silently.

The audit's headline: "no-port branch backs off silently and never sets
Disconnected." When serial_conn is None (SIM or open failure), send_settings
and stop do nothing, no message, no state change. The operator presses
Enter Settings / Stop and gets no feedback that the model is disconnected.

The reader thread does set current_temp = "Disconnected" after persistent
failures — but only if the port was open and then closed. If it was never
open, the reader thread never starts.

The fix: emit ErrorRouter.report_warning when send_settings or stop are
called with no serial connection, just as they report on write errors
now (ERRORS-7).

The PySide staleness indicator half is Lane 1's (agent A, w5-views); this
test covers the model only.
"""
from unittest.mock import MagicMock, patch

import pytest

from model.temperature_system import TemperatureSystem


@pytest.fixture
def sim_temp_system():
    """A simulated temperature system (no port = no reader thread)."""
    with patch('model.temperature_system.serial') as serial_class, \
         patch('error_routing.ErrorRouter') as error_router:
        transport = MagicMock()
        transport.is_open.return_value = False
        serial_class.return_value = transport
        ts = TemperatureSystem(None)  # SIM mode
        yield ts, error_router
        ts.close()


@pytest.fixture
def no_port_system():
    """A temperature system constructed with no port at all."""
    with patch('error_routing.ErrorRouter') as error_router:
        ts = TemperatureSystem(None)
        yield ts, error_router
        ts.close()


def test_send_settings_with_no_port_reports_not_connected(sim_temp_system):
    """send_settings with no serial connection should report a warning."""
    ts, error_router = sim_temp_system
    ts.setpoint = "50"

    ts.send_settings()

    # Should have reported that the connection is not available
    error_router.report_warning.assert_called()
    call_args = error_router.report_warning.call_args
    assert "not connected" in str(call_args).lower() or \
           "disconnected" in str(call_args).lower(), (
        "Warning text should mention connection status")


def test_stop_with_no_port_reports_not_connected(sim_temp_system):
    """stop with no serial connection should report a warning."""
    ts, error_router = sim_temp_system

    ts.stop()

    # Should have reported that the connection is not available
    error_router.report_warning.assert_called()
    call_args = error_router.report_warning.call_args
    assert "not connected" in str(call_args).lower() or \
           "disconnected" in str(call_args).lower(), (
        "Warning text should mention connection status")


def test_send_settings_silently_does_nothing_when_invalid_fields(no_port_system):
    """send_settings should refuse invalid fields before checking connection."""
    ts, error_router = no_port_system
    ts.setpoint = "not_a_number"

    ts.send_settings()

    # The invalid field check comes first, so it reports that instead
    error_router.report_warning.assert_called()
    call_args = error_router.report_warning.call_args
    # The warning should be about invalid fields, not connection
    assert "not a number" in str(call_args).lower()


def test_send_settings_with_valid_fields_but_no_port_reports_connection(no_port_system):
    """send_settings with valid fields but no port should report disconnection."""
    ts, error_router = no_port_system
    ts.setpoint = "50"
    ts.ramp_rate = "10"
    ts.p_term = "2.0"
    ts.i_term = "0.5"
    ts.d_term = "0.1"
    ts.offset = "0"

    ts.send_settings()

    # Should report connection issue after field validation
    error_router.report_warning.assert_called()
    call_args = str(error_router.report_warning.call_args)
    assert "not connected" in call_args.lower() or \
           "disconnected" in call_args.lower() or \
           "no port" in call_args.lower(), (
        "Should report connection status when no serial is available")
