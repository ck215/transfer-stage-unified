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

        mock_instance.ser.write.assert_called_once_with(b"<45.5,5.0,2.5,0.8,0.2,1.0>")
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
