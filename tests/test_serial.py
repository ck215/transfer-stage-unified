import pytest
import struct
from unittest.mock import patch, MagicMock
from controller.seiral import serial

def get_mock_serial():
    mock_instance = MagicMock()
    mock_instance.is_open = True
    mock_instance.in_waiting = 0
    return mock_instance

def test_serial_send_manual_mode_command():
    with patch("controller.seiral.pyserial.Serial") as mock_serial:
        mock_instance = get_mock_serial()
        mock_serial.return_value = mock_instance
        
        s = serial("COM1")
        
        params = {
            "x_axisStatus": 0.5,
            "y_axisStatus": -0.5,
            "z_axisStatusL": -1.0,
            "z_axisStatusR": -1.0,
            "x_stepSize": 10,
            "y_stepSize": 10,
            "z_stepSize": 10,
            "dpad_LR": 0,
            "dpad_UD": 0,
            "LBumper": 0,
            "RBumper": 0,
            "manual_jog_speed": 400,
            "packet_format": "<BBffffffffff"
        }
        
        s.send_manual_mode_command(params)
        
        mock_instance.write.assert_called()
        packet = mock_instance.write.call_args[0][0]
        
        assert len(packet) == 42
        unpacked = struct.unpack('<BBffffffffff', packet)
        assert unpacked[0] == 0xAA
        assert unpacked[1] == 1
        assert unpacked[2] == 0.5
        assert unpacked[3] == -0.5
        assert unpacked[4] == 0.0
        
def test_serial_read_position():
    with patch("controller.seiral.pyserial.Serial") as mock_serial:
        mock_instance = get_mock_serial()
        mock_serial.return_value = mock_instance
        
        s = serial("COM1")
        
        mock_instance.in_waiting = 20
        mock_instance.read.return_value = b"POS:10,20,30\n"
        
        pos = s.read_position()
        assert pos == (10, 20, 30)
