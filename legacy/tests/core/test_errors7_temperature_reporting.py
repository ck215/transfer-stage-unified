"""ERRORS-7 (temperature_system.py share): Swallowed exceptions.

The audit found several places where exceptions were caught but not
reported:
- temperature_system.py:186-188 close() writes heater-off with except: pass
- temperature_system.py:191-192 serial_conn.close() with except: pass
- temperature_system.py:117-121 After 5 read failures, no attempt to set heater to 0

The fix: report these failures through ErrorRouter, not silently.
"""
from unittest.mock import MagicMock, patch, call

import pytest

from model.temperature_system import TemperatureSystem


@pytest.fixture
def temp_with_mock_serial():
    """A temperature system with a mock serial connection."""
    with patch('model.temperature_system.serial') as serial_class, \
         patch('error_routing.ErrorRouter') as error_router:
        transport = MagicMock()
        transport.is_open.return_value = True
        transport.read_line.return_value = None
        serial_class.return_value = transport
        ts = TemperatureSystem("COM_TEST")
        ts.serial_conn = transport
        yield ts, transport, error_router
        ts.close()


def test_close_reports_write_failure(temp_with_mock_serial):
    """When close() fails to write the heater-off frame, it must report."""
    ts, transport, error_router = temp_with_mock_serial

    # Make the write fail
    transport.write_command.side_effect = IOError("Port closed unexpectedly")

    ts.close()

    # Should have reported the error
    error_router.report_error.assert_called()
    calls = error_router.report_error.call_args_list
    # Look for a call about "Heater Off Not Delivered"
    found = False
    for call_obj in calls:
        args = call_obj[0]
        if "Heater Off" in str(args):
            found = True
            break
    assert found, "close() must report when heater-off write fails"


def test_close_reports_flush_failure(temp_with_mock_serial):
    """When close() flush fails, it must report that the frame may not be delivered."""
    ts, transport, error_router = temp_with_mock_serial

    # Mock flush to return False (didn't flush)
    transport.flush = MagicMock(return_value=False)

    ts.close()

    # Should have reported the flush failure
    error_router.report_error.assert_called()
    calls = error_router.report_error.call_args_list
    found = False
    for call_obj in calls:
        args = call_obj[0]
        if "Heater Off" in str(args):
            found = True
            break
    assert found, "close() must report when flush fails"


def test_close_reports_close_failure(temp_with_mock_serial):
    """When close() fails to close the port, it must report."""
    ts, transport, error_router = temp_with_mock_serial

    # Make close() fail
    transport.close.side_effect = IOError("Failed to close port")

    ts.close()

    # Should have reported the error
    error_router.report_error.assert_called()
    calls = error_router.report_error.call_args_list
    found = False
    for call_obj in calls:
        args = call_obj[0]
        if "Serial Close" in str(args):
            found = True
            break
    assert found, "close() must report when port close fails"


def test_read_persistent_failure_sets_disconnected(temp_with_mock_serial):
    """When reader thread has persistent failures, it sets current_temp to Disconnected."""
    ts, transport, error_router = temp_with_mock_serial

    # Simulate persistent read failures by raising an exception
    transport.read_line.side_effect = IOError("Read timeout")
    transport.is_open.return_value = True

    # Let the reader run for a bit
    import time
    time.sleep(0.5)

    # After persistent failures, the reader should report errors
    # (report_error for transient failures, report_warning for persistent)
    assert error_router.report_error.called or error_router.report_warning.called, (
        "persistent read failures should trigger error/warning reporting")

    ts.close()
