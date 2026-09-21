import time
import threading
from unittest.mock import MagicMock, patch
import pytest

from model.temperature_system import TemperatureSystem


def get_mock_serial_conn():
    """A transport double, not a raw pyserial handle.

    The model no longer touches `.ser` (invariant I-2.3): every frame goes
    through write_command()/read_line(), which raise TransportError rather
    than swallowing a failed write. The double follows that API.
    """
    mock_conn = MagicMock()
    mock_conn.is_open.return_value = True
    return mock_conn


def test_read_serial_data_cannot_free_spin_on_instant_readline():
    """read_serial_data must never exceed ~100Hz even if the underlying
    serial object returns truthy data instantly instead of blocking
    (e.g. a misconfigured non-blocking port, or a mock) — previously this
    free-spun and grew RSS by multiple GB in seconds."""
    with patch("model.temperature_system.serial") as mock_serial_cls:
        mock_instance = get_mock_serial_conn()
        mock_instance.read_line.return_value = b"0,20.0,20.0\n"  # never blocks, always truthy
        mock_serial_cls.return_value = mock_instance

        ts = TemperatureSystem("COM4")
        thread = threading.Thread(target=ts.read_serial_data, daemon=True)
        thread.start()
        time.sleep(0.5)
        ts.close()
        thread.join(timeout=2)

        # At 0.01s/iteration a bound loop does ~50 iterations in 0.5s;
        # a free-spinning loop would do tens of thousands.
        assert mock_instance.read_line.call_count < 200


def test_temperature_system_initialization_and_handshake():
    """Verify 115200 baud rate and initial handshake packet <0,6.0,0,0,0,0>."""
    with patch("model.temperature_system.serial") as mock_serial_cls:
        mock_instance = get_mock_serial_conn()
        mock_serial_cls.return_value = mock_instance

        ts = TemperatureSystem("COM4")
        mock_serial_cls.assert_called_once_with("COM4", baud_rate=115200)
        mock_instance.write_command.assert_called_once_with(b"<0,6.0,0,0,0,0>")
        ts.close()


def test_temperature_system_send_settings_packet():
    """Verify packet formatting: <setpoint,spdelay,p_term,i_term,d_term,offset>."""
    with patch("model.temperature_system.serial") as mock_serial_cls:
        mock_instance = get_mock_serial_conn()
        mock_serial_cls.return_value = mock_instance

        ts = TemperatureSystem("COM4")
        mock_instance.write_command.reset_mock()

        ts.setpoint = "45.5"
        ts.ramp_rate = "12"  # ramp_rate is spdelay (s/°C) directly, sent unconverted
        ts.p_term = "2.5"
        ts.i_term = "0.8"
        ts.d_term = "0.2"
        ts.offset = "1.0"

        ts.send_settings()

        mock_instance.write_command.assert_called_once_with("<45.5,12.00,2.5,0.8,0.2,1.0>")
        ts.close()


def test_temperature_system_stop_logic():
    """Verify stop() sets setpoint to 0, sends stop packet, keeps serial reading alive."""
    with patch("model.temperature_system.serial") as mock_serial_cls:
        mock_instance = get_mock_serial_conn()
        mock_serial_cls.return_value = mock_instance

        ts = TemperatureSystem("COM4")
        ts.setpoint = "50"
        ts.ramp_rate = "10"
        ts.offset = "0"
        mock_instance.write_command.reset_mock()

        ts.stop()

        assert ts.setpoint == "0"
        assert ts.continue_reading is True
        # `priority` is explicit since TEMP-7: the ordinary stop still waits
        # for the write lock; only emergency_stop forces past it.
        mock_instance.write_command.assert_called_once_with(
            "<0,10.0,0,0,0,0>", priority=False)
        mock_instance.close.assert_not_called()  # Serial port should NOT be closed on Stop

        ts.close()
        assert ts.continue_reading is False
        mock_instance.close.assert_called()


def test_temperature_system_data_parsing_and_history():
    """Verify process_raw_data accurately parses 'timer, temp, setpoint' and updates history."""
    with patch("model.temperature_system.serial") as mock_serial_cls:
        mock_instance = get_mock_serial_conn()
        mock_serial_cls.return_value = mock_instance

        ts = TemperatureSystem(None)

        # Feed 250 readings to test 200-item history cap
        for i in range(250):
            ts.process_raw_data(f"{i * 0.5}, {20.0 + i * 0.1:.2f}, 50.0\n")

        assert ts.current_temp == f"{20.0 + 249 * 0.1:.2f} °C"
        time_arr, temp_arr, sp_arr = ts.get_history()
        assert len(time_arr) == 200
        assert len(temp_arr) == 200
        assert len(sp_arr) == 200
        assert time_arr[-1] == 249 * 0.5
        assert sp_arr[-1] == 50.0

        # Feed malformed data
        ts.process_raw_data("DEV: t\n")
        ts.process_raw_data("invalid,data\n")
        ts.process_raw_data("a,b,c\n")
        # Current temp should remain unchanged
        assert ts.current_temp == f"{20.0 + 249 * 0.1:.2f} °C"
        ts.close()


def test_temperature_system_clean_shutdown():
    """Verify close() and disconnect() shut down gracefully without reporting errors."""
    with patch("model.temperature_system.serial") as mock_serial_cls:
        mock_instance = get_mock_serial_conn()
        mock_serial_cls.return_value = mock_instance

        ts = TemperatureSystem("COM4")
        with patch("error_routing.ErrorRouter.report_error") as mock_report:
            ts.close()
            mock_report.assert_not_called()

        ts2 = TemperatureSystem("COM4")
        with patch("error_routing.ErrorRouter.report_error") as mock_report:
            ts2.disconnect()
            mock_report.assert_not_called()


def test_temperature_system_invalid_ramp_rates():
    """Test fallback to spdelay=0 for malformed ramp rates."""
    ts = TemperatureSystem()
    mock_serial = MagicMock()
    ts.serial_conn = mock_serial

    invalid_rates = ["invalid_string", "0", "inf", "nan", "-1"]

    for rate in invalid_rates:
        ts.ramp_rate = rate
        ts.send_settings()

        # Should have called write, and fallback logic sends '0' for spdelay when invalid
        # Let's inspect the actual write arguments
        assert mock_serial.write_command.called
        _payload = mock_serial.write_command.call_args[0][0]
        write_args = _payload.decode('utf-8') if isinstance(_payload, bytes) else _payload
        assert '0' in write_args, f"Failed for rate={rate}"
        mock_serial.write_command.reset_mock()


@patch('error_routing.ErrorRouter.report_error')
def test_temperature_system_serial_write_failure(mock_report_error):
    """Test serial write exception is routed securely to ErrorRouter."""
    ts = TemperatureSystem()
    mock_serial = MagicMock()
    mock_serial.write_command.side_effect = Exception("USB Disconnected")
    ts.serial_conn = mock_serial
    ts.ramp_rate = "12"

    ts.send_settings()
    mock_report_error.assert_called_once()
    assert "Serial Write Error" in mock_report_error.call_args[0][0]
    assert "USB Disconnected" in mock_report_error.call_args[0][1]


def test_read_serial_data_retry_limit():
    """TEMP-2: Verify reader retries indefinitely with exponential backoff.

    Resets counter on success, reports transient errors for first 5, then
    persistent connection loss on subsequent failures. No longer gives up."""
    with patch("model.temperature_system.serial") as mock_serial_cls:
        mock_instance = get_mock_serial_conn()
        mock_serial_cls.return_value = mock_instance

        responses = [
            Exception("Transient 1"),
            Exception("Transient 2"),
            b"0,20.0,20.0\n",
            Exception("Failure 1"),
            Exception("Failure 2"),
            Exception("Failure 3"),
            Exception("Failure 4"),
            Exception("Failure 5"),
            b"0,21.0,21.0\n",  # Recovery
        ]

        def side_effect(*args, **kwargs):
            if not responses:
                return b""
            resp = responses.pop(0)
            if isinstance(resp, Exception):
                raise resp
            return resp

        mock_instance.read_line.side_effect = side_effect

        with patch('error_routing.ErrorRouter.report_error') as mock_report_error:
            with patch('error_routing.ErrorRouter.report_warning') as mock_report_warning:
                ts = TemperatureSystem("COM4")
                ts = TemperatureSystem("COM4")

                # Wait for reader to process responses
                time.sleep(6)  # Enough for backoff: ~3 seconds for initial failures
                
                # Check state before closing
                assert len(responses) == 0, (
                    "Should have consumed all responses including recovery")
                assert ts.current_temp == "21.00 °C", (
                    "Should have recovered with latest temperature")
                
                # Should have reported transient errors
                transient_calls = [call for call in mock_report_error.mock_calls
                                 if "transient" in str(call).lower()]
                assert len(transient_calls) >= 2, (
                    "Should report transient errors for early failures")
                
                # Should NOT have "Giving up" or "Fatal" messages
                fatal_calls = [call for call in mock_report_error.mock_calls
                             if "giving up" in str(call).lower()]
                assert len(fatal_calls) == 0, (
                    "Should NOT give up after retries (TEMP-2)")
                


# --- TEMP-11: the heater-off frame on the shutdown path ------------------
#
# close() set continue_reading=False, wrote <0,6.0,0,0,0,0> inside a bare
# `except Exception: pass`, and closed the port immediately — no join, and
# no word to the operator when the off-frame did not go out. Quitting with
# a half-dead cable left the heater at its last setpoint (the firmware has
# no watchdog) and said nothing.

def test_close_reports_a_heater_off_frame_that_was_not_delivered():
    """TEMP-11: a failed heater-off write must be reported, not swallowed.

    The operator has to learn that the heater is still at setpoint; the
    window closing quietly is the failure mode the audit describes."""
    with patch("model.temperature_system.serial") as mock_serial_cls:
        mock_instance = get_mock_serial_conn()
        mock_serial_cls.return_value = mock_instance

        ts = TemperatureSystem("COM4")
        mock_instance.write_command.side_effect = OSError("port is gone")

        with patch("error_routing.ErrorRouter.report_error") as mock_report:
            ts.close()

        assert mock_report.call_count == 1, (
            "the heater-off frame failed and nothing was reported")
        title, message = mock_report.call_args[0][0], mock_report.call_args[0][1]
        assert "heater" in (title + message).lower()
        assert "port is gone" in message
        # The port is still released even though the write failed.
        mock_instance.close.assert_called_once()


def test_close_joins_the_reader_before_releasing_the_port():
    """TEMP-11: close() must wait for the reader to leave the port before
    closing the fd under it, which is also the only window the off-frame
    gets to drain."""
    with patch("model.temperature_system.serial") as mock_serial_cls:
        mock_instance = get_mock_serial_conn()

        def _slow_read():
            time.sleep(0.05)
            return b"0,20.0,20.0\n"

        mock_instance.read_line.side_effect = _slow_read
        mock_serial_cls.return_value = mock_instance

        observed = {}

        ts = TemperatureSystem("COM4")

        def _record_close():
            observed["reader_alive"] = ts.serial_thread.is_alive()

        mock_instance.close.side_effect = _record_close

        # Let the reader get into a read before the shutdown lands.
        time.sleep(0.15)
        ts.close()

        assert observed.get("reader_alive") is False, (
            "the port was closed while the reader thread was still in it")


def test_close_sends_the_heater_off_frame_on_the_priority_path():
    """TEMP-11: the zero-setpoint frame is idempotent, so on shutdown it
    takes the priority write path rather than waiting out whatever holds
    the transport lock. (safety-pattern.md item 3: a stop that cannot get
    the lock is worse than an unsynchronised one.)"""
    with patch("model.temperature_system.serial") as mock_serial_cls:
        mock_instance = get_mock_serial_conn()
        mock_serial_cls.return_value = mock_instance

        ts = TemperatureSystem("COM4")
        mock_instance.write_command.reset_mock()
        ts.close()

        assert mock_instance.write_command.call_count == 1
        args, kwargs = mock_instance.write_command.call_args
        assert args[0] == b"<0,6.0,0,0,0,0>"
        assert kwargs.get("priority") is True, (
            "the shutdown heater-off frame can be held by the transport lock")


def test_close_still_reports_nothing_on_a_clean_shutdown():
    """Guard for the TEMP-11 fix: reporting a failed off-frame must not
    turn an ordinary quit into a popup."""
    with patch("model.temperature_system.serial") as mock_serial_cls:
        mock_instance = get_mock_serial_conn()
        mock_serial_cls.return_value = mock_instance

        ts = TemperatureSystem("COM4")
        with patch("error_routing.ErrorRouter.report_error") as mock_report:
            ts.close()
            mock_report.assert_not_called()
        assert ts.continue_reading is False


# --- TEMP-11, the seam: the model half and the transport half wired up -----
# The transport gained a bounded flush() in the fix-transport worktree; the
# model half was written in fix-web against a transport that had none. These
# three pin the join, which neither worktree could test on its own.

def test_close_drains_the_heater_off_frame_before_releasing_the_port():
    """TEMP-11: close() on POSIX does not guarantee written bytes reached the
    wire, so the off-frame must be drained before the fd goes away. The
    ordering is the whole point: write, flush, then close."""
    with patch("model.temperature_system.serial") as mock_serial_cls:
        mock_instance = get_mock_serial_conn()
        mock_instance.flush.return_value = True
        mock_serial_cls.return_value = mock_instance

        ts = TemperatureSystem("COM4")
        calls = []
        mock_instance.write_command.side_effect = lambda *a, **k: calls.append("write")
        mock_instance.flush.side_effect = lambda *a, **k: (calls.append("flush"), True)[1]
        mock_instance.close.side_effect = lambda *a, **k: calls.append("close")

        ts.close()

        assert "flush" in calls, (
            "close() released the port without draining the heater-off frame")
        assert calls.index("write") < calls.index("flush") < calls.index("close"), (
            f"wrong order: {calls} — the drain must sit between the frame and "
            "the close, or it protects nothing")


def test_close_reports_an_undrained_heater_off_frame():
    """A flush that times out means the frame may still be buffered. The
    operator has to be told the heater may still be at setpoint — the same
    obligation as a failed write, reached a different way."""
    with patch("model.temperature_system.serial") as mock_serial_cls:
        mock_instance = get_mock_serial_conn()
        mock_serial_cls.return_value = mock_instance
        ts = TemperatureSystem("COM4")
        mock_instance.flush.return_value = False      # drain did not complete

        with patch("error_routing.ErrorRouter.report_error") as mock_report:
            ts.close()

        assert mock_report.call_count == 1, (
            "an undrained shutdown frame was not reported")
        title, message = mock_report.call_args[0][0], mock_report.call_args[0][1]
        assert "heater" in (title + message).lower()
        # Still released: a stuck drain must not also leak the port.
        mock_instance.close.assert_called_once()


def test_a_transport_without_flush_still_closes():
    """The model must not require a flush() the transport may not have — a
    simulated port, or an older double. Absence is not a failure to report."""
    with patch("model.temperature_system.serial") as mock_serial_cls:
        mock_instance = get_mock_serial_conn()
        del mock_instance.flush                       # no such attribute
        mock_serial_cls.return_value = mock_instance
        ts = TemperatureSystem("COM4")

        with patch("error_routing.ErrorRouter.report_error") as mock_report:
            ts.close()

        mock_instance.close.assert_called_once()
        assert mock_report.call_count == 0, (
            "a transport with no flush() is not an undelivered frame")
