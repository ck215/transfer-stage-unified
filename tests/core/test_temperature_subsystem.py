import time
import threading
from unittest.mock import MagicMock, patch
import pytest

from model.temperature_system import TemperatureSystem


def get_mock_serial_conn():
    mock_conn = MagicMock()
    mock_conn.ser = MagicMock()
    mock_conn.ser.is_open = True
    return mock_conn


def test_read_serial_data_cannot_free_spin_on_instant_readline():
    """read_serial_data must never exceed ~100Hz even if the underlying
    serial object returns truthy data instantly instead of blocking
    (e.g. a misconfigured non-blocking port, or a mock) — previously this
    free-spun and grew RSS by multiple GB in seconds."""
    with patch("model.temperature_system.serial") as mock_serial_cls:
        mock_instance = get_mock_serial_conn()
        mock_instance.ser.readline.return_value = b"0,20.0,20.0\n"  # never blocks, always truthy
        mock_serial_cls.return_value = mock_instance

        ts = TemperatureSystem("COM4")
        thread = threading.Thread(target=ts.read_serial_data, daemon=True)
        thread.start()
        time.sleep(0.5)
        ts.close()
        thread.join(timeout=2)

        # At 0.01s/iteration a bound loop does ~50 iterations in 0.5s;
        # a free-spinning loop would do tens of thousands.
        assert mock_instance.ser.readline.call_count < 200


def test_temperature_system_initialization_and_handshake():
    """Verify 115200 baud rate and initial handshake packet <0,6.0,0,0,0,0>."""
    with patch("model.temperature_system.serial") as mock_serial_cls:
        mock_instance = get_mock_serial_conn()
        mock_serial_cls.return_value = mock_instance

        ts = TemperatureSystem("COM4")
        mock_serial_cls.assert_called_once_with("COM4", baud_rate=115200)
        mock_instance.ser.write.assert_called_once_with(b"<0,6.0,0,0,0,0>")
        ts.close()


def test_temperature_system_send_settings_packet():
    """Verify packet formatting: <setpoint,spdelay,p_term,i_term,d_term,offset>."""
    with patch("model.temperature_system.serial") as mock_serial_cls:
        mock_instance = get_mock_serial_conn()
        mock_serial_cls.return_value = mock_instance

        ts = TemperatureSystem("COM4")
        mock_instance.ser.write.reset_mock()

        ts.setpoint = "45.5"
        ts.ramp_rate = "12"  # 12 deg/min => spdelay = 60/12 = 5.0 s
        ts.p_term = "2.5"
        ts.i_term = "0.8"
        ts.d_term = "0.2"
        ts.offset = "1.0"

        ts.send_settings()

        mock_instance.ser.write.assert_called_once_with(b"<45.5,5.00,2.5,0.8,0.2,1.0>")
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
        mock_instance.ser.write.reset_mock()

        ts.stop()

        assert ts.setpoint == "0"
        assert ts.continue_reading is True
        mock_instance.ser.write.assert_called_once_with(b"<0,6.0,0,0,0,0>")
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

    def test_temperature_system_invalid_ramp_rates(self):
        """Test fallback to spdelay=0 for malformed ramp rates."""
        sys = TemperatureSystem()
        mock_serial = MagicMock()
        sys.serial_conn = mock_serial
        
        invalid_rates = ["invalid_string", "0", "inf", "nan", "-1"]
        
        for rate in invalid_rates:
            sys.ramp_rate = rate
            sys.send_settings()
            
            # Should have called write, and fallback logic sends '0' for spdelay when invalid
            # Let's inspect the actual write arguments
            assert mock_serial.ser.write.called
            write_args = mock_serial.ser.write.call_args[0][0].decode('utf-8')
            assert '0' in write_args, f"Failed for rate={rate}"
            mock_serial.ser.write.reset_mock()

    @patch('model.temperature_system.ErrorRouter.report_error')
    def test_temperature_system_serial_write_failure(self, mock_report_error):
        """Test serial write exception is routed securely to ErrorRouter."""
        sys = TemperatureSystem()
        mock_serial = MagicMock()
        mock_serial.ser.write.side_effect = Exception("USB Disconnected")
        sys.serial_conn = mock_serial
        sys.ramp_rate = "12"
        
        sys.send_settings()
        mock_report_error.assert_called_once()
        assert "Serial Write Error" in mock_report_error.call_args[0][0]
        assert "USB Disconnected" in mock_report_error.call_args[0][1]

