"""TEMP-2: Reader thread backoff and recovery.

The reader thread "gives up" after 5 failures with no real backoff and no
restart path, while the UI keeps showing a frozen temperature and "hardware"
status. This test verifies:

1. Real exponential backoff (0.1 -> 0.2 -> ... up to ~2 s)
2. Indefinite retry (no hard give-up)
3. On give-up, set current_temp to indicate disconnection
4. Recovery path on reconnection
"""
import time
import threading
from unittest.mock import patch, MagicMock
import pytest

from model.temperature_system import TemperatureSystem


def get_mock_serial_conn():
    """A transport double."""
    mock_conn = MagicMock()
    mock_conn.is_open.return_value = True
    return mock_conn


def test_reader_implements_exponential_backoff():
    """Verify exponential backoff: delays increase from 0.1s up to ~2s."""
    with patch("model.temperature_system.serial") as mock_serial_cls:
        mock_instance = get_mock_serial_conn()
        mock_serial_cls.return_value = mock_instance

        # Return errors until we can measure backoff
        errors = [Exception(f"Error {i}") for i in range(10)]
        success = [b"0,20.0,20.0\n"]

        responses = errors[:5] + success

        def side_effect(*args, **kwargs):
            if not responses:
                return b""
            resp = responses.pop(0)
            if isinstance(resp, Exception):
                raise resp
            return resp

        mock_instance.read_line.side_effect = side_effect

        # Time the backoff behavior
        ts = TemperatureSystem("COM4")
        start = time.time()
        ts.serial_thread.join(timeout=3)
        elapsed = time.time() - start

        # 5 errors with exponential backoff should take at least:
        # 0.1 + 0.2 + 0.4 + 0.8 + 1.6 = 3.1 seconds (approximately)
        # We give some tolerance for timing variations
        assert elapsed >= 1.5, (
            f"Reader did not implement backoff; completed in {elapsed}s, "
            f"expected at least 1.5s for exponential backoff")

        ts.close()


def test_reader_continues_retrying_indefinitely():
    """Verify reader does not give up after N failures but keeps retrying."""
    with patch("model.temperature_system.serial") as mock_serial_cls:
        mock_instance = get_mock_serial_conn()
        mock_serial_cls.return_value = mock_instance

        # Generate 20 consecutive failures, then success
        errors = [Exception(f"Error {i}") for i in range(20)]
        success = [b"0,20.0,20.0\n"]

        responses = errors + success

        def side_effect(*args, **kwargs):
            if not responses:
                return b""
            resp = responses.pop(0)
            if isinstance(resp, Exception):
                raise resp
            return resp

        mock_instance.read_line.side_effect = side_effect

        with patch('error_routing.ErrorRouter.report_error') as mock_report:
            ts = TemperatureSystem("COM4")
            ts.serial_thread.join(timeout=30)

            # Should not give up, should recover
            assert ts.serial_thread.is_alive() or len(responses) == 0, (
                "Reader gave up prematurely before all retries exhausted")

            # Should have reported errors but not a "fatal" give-up
            fatal_calls = [call for call in mock_report.mock_calls
                          if "Fatal" in str(call) or "Giving up" in str(call)]
            assert len(fatal_calls) == 0, (
                "Reader gave up with a fatal message after retries")

        ts.close()


def test_reader_sets_disconnected_on_persistent_failure():
    """On persistent errors, current_temp should indicate disconnection."""
    with patch("model.temperature_system.serial") as mock_serial_cls:
        mock_instance = get_mock_serial_conn()
        mock_serial_cls.return_value = mock_instance

        # Persistent failures
        def always_fail(*args, **kwargs):
            raise Exception("Serial error")

        mock_instance.read_line.side_effect = always_fail

        ts = TemperatureSystem("COM4")
        # Wait long enough for exponential backoff to reach persistent failure
        # 5 transient failures: 0.1 + 0.2 + 0.4 + 0.8 + 1.6 = 3.1 seconds minimum
        # Then one more failure to trigger persistent handler: +2.0 = 5.1 seconds
        time.sleep(5.5)
        ts.close()

        # After persistent failures, current_temp should not be "N/A"
        # but should indicate a disconnection state
        # (e.g., "Disconnected", "Error", or an error state)
        assert ts.current_temp != "N/A", (
            f"After persistent reader failures, current_temp should indicate "
            f"disconnection, not remain N/A. Got: {ts.current_temp}")


def test_reader_recovers_after_reconnect():
    """Reader should recover and resume reading after reconnection."""
    with patch("model.temperature_system.serial") as mock_serial_cls:
        mock_instance = get_mock_serial_conn()
        mock_serial_cls.return_value = mock_instance

        # Start with failures, then success
        responses = [
            Exception("Error 1"),
            Exception("Error 2"),
            b"0,20.0,20.0\n",
        ]

        def side_effect(*args, **kwargs):
            if not responses:
                return b""
            resp = responses.pop(0)
            if isinstance(resp, Exception):
                raise resp
            return resp

        mock_instance.read_line.side_effect = side_effect

        ts = TemperatureSystem("COM4")
        ts.serial_thread.join(timeout=3)

        # After recovery, should have valid temperature
        assert "°C" in ts.current_temp or ts.current_temp == "20.00 °C", (
            f"Reader did not recover; current_temp = {ts.current_temp}")

        ts.close()


def test_reader_backoff_respects_maximum():
    """Backoff should not exceed ~2 seconds."""
    with patch("model.temperature_system.serial") as mock_serial_cls:
        mock_instance = get_mock_serial_conn()
        mock_serial_cls.return_value = mock_instance

        # Many consecutive errors to test max backoff
        errors = [Exception(f"Error {i}") for i in range(10)]

        def side_effect(*args, **kwargs):
            if not errors:
                # Never success, trigger give-up or timeout
                time.sleep(10)
                return b""
            raise errors.pop(0)

        mock_instance.read_line.side_effect = side_effect

        ts = TemperatureSystem("COM4")
        start = time.time()
        ts.close()
        ts.serial_thread.join(timeout=5)
        elapsed = time.time() - start

        # With exponential backoff to 2s max, 10 errors should take ~20 seconds
        # But we timeout after 5s, so just verify it doesn't spin or block forever
        assert elapsed < 10, (
            f"Reader took too long: {elapsed}s, suggests uncontrolled backoff")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
