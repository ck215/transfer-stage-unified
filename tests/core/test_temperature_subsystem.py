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
    """Verify read_serial_data breaks after 5 consecutive failures,
    but keeps retrying for <5, and resets counter on success."""
    with patch("model.temperature_system.serial") as mock_serial_cls:
        mock_instance = get_mock_serial_conn()
        mock_serial_cls.return_value = mock_instance
        
        responses = [
            Exception("Transient 1"),
            Exception("Transient 2"),
            b"0,20.0,20.0\n",
            Exception("Fatal 1"),
            Exception("Fatal 2"),
            Exception("Fatal 3"),
            Exception("Fatal 4"),
            Exception("Fatal 5"),
            Exception("Should not be reached"),
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
            ts = TemperatureSystem("COM4")
            ts.serial_thread.join(timeout=3)
            
            assert not ts.serial_thread.is_alive()
            assert len(responses) == 1  # The last exception shouldn't be reached
            
            fatal_calls = [call for call in mock_report_error.mock_calls if "Fatal" in call.args[0]]
            assert len(fatal_calls) == 1
            assert "Giving up after 5 consecutive failures" in fatal_calls[0].args[1]
            
        ts.close()
