import pytest
import threading
import time
from unittest.mock import patch, MagicMock, PropertyMock
from model.temperature_system import TemperatureSystem


class TestTemperatureSystemInit:
    """Tests for TemperatureSystem initialization."""

    def test_init_without_port(self):
        """Test initialization with no port provided."""
        with patch('model.temperature_system.serial') as mock_serial:
            ts = TemperatureSystem(port=None)
            assert ts.serial_conn is None
            assert mock_serial.call_count == 0
            assert ts.continue_reading is True
            assert ts.setpoint == "0"
            assert ts.ramp_rate == "10"
            assert ts.p_term == "2.0"
            assert ts.i_term == "0.5"
            assert ts.d_term == ".1"
            assert ts.offset == "0"
            assert ts.current_temp == "N/A"
            assert ts.tempC == []
            assert ts.time == []
            assert ts.sp == []
            assert ts.cnt == 0

    def test_init_with_none_string_port(self):
        """Test initialization with port='None'."""
        with patch('model.temperature_system.serial') as mock_serial:
            ts = TemperatureSystem(port="None")
            assert ts.serial_conn is None
            assert mock_serial.call_count == 0

    def test_init_with_valid_port(self):
        """Test initialization with a valid port string.

        The model talks to the transport, not to a pyserial handle
        (invariant I-2.3), so the double exposes is_open()/write_command().
        """
        mock_serial_instance = MagicMock()
        mock_serial_instance.is_open.return_value = True

        with patch('model.temperature_system.serial', return_value=mock_serial_instance) as mock_serial:
            ts = TemperatureSystem(port="/dev/ttyUSB0")
            
            # Verify serial was called
            mock_serial.assert_called_once_with("/dev/ttyUSB0", baud_rate=115200)
            assert ts.serial_conn is not None
            
            # Verify initial write command
            mock_serial_instance.write_command.assert_called_once_with(b"<0,6.0,0,0,0,0>")
            
            # Verify thread started
            assert hasattr(ts, 'serial_thread')
            assert ts.serial_thread.is_alive()

    def test_init_serial_write_error(self):
        """Test initialization when serial write fails."""
        mock_serial_instance = MagicMock()
        mock_serial_instance.is_open.return_value = True
        mock_serial_instance.write_command.side_effect = IOError("Write failed")

        with patch('model.temperature_system.serial', return_value=mock_serial_instance):
            with patch('error_routing.ErrorRouter.report_error') as mock_report:
                ts = TemperatureSystem(port="/dev/ttyUSB0")
                
                # Verify error was reported
                mock_report.assert_called_once()
                args, kwargs = mock_report.call_args
                assert "Serial Write Error" in args[0]
                assert "Write failed" in args[1]

    def test_init_serial_not_open(self):
        """Test initialization when serial connection is not open."""
        mock_serial_instance = MagicMock()
        mock_serial_instance.is_open.return_value = False

        with patch('model.temperature_system.serial', return_value=mock_serial_instance):
            ts = TemperatureSystem(port="/dev/ttyUSB0")
            
            # Should not write or start thread if not open
            mock_serial_instance.write_command.assert_not_called()
            assert not hasattr(ts, 'serial_thread')


class TestTemperatureSystemUI:
    """Tests for UI schema property."""

    def test_ui_schema_structure(self):
        """Test that ui_schema returns expected structure."""
        ts = TemperatureSystem(port=None)
        schema = ts.ui_schema
        
        assert "sections" in schema
        assert len(schema["sections"]) == 3
        
        # Check sections titles
        titles = [s["title"] for s in schema["sections"]]
        assert "Temperature Readings" in titles
        assert "Control Parameters" in titles
        assert "System Control" in titles
        
        # Check elements in Control Parameters
        control_params = next(s for s in schema["sections"] if s["title"] == "Control Parameters")
        element_types = [e["type"] for e in control_params["elements"]]
        assert "entry" in element_types
        
        # Check elements in System Control
        system_control = next(s for s in schema["sections"] if s["title"] == "System Control")
        button_commands = [e["command"] for e in system_control["elements"]]
        assert "send_settings" in button_commands
        assert "stop" in button_commands


