import pytest
import struct
from unittest.mock import patch, MagicMock
from controller.serial import serial

def get_mock_serial():
    mock_instance = MagicMock()
    mock_instance.is_open = True
    mock_instance.in_waiting = 0
    return mock_instance

def test_serial_send_manual_mode_command():
    with patch("controller.serial.pyserial.Serial") as mock_serial:
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
        
def test_serial_enable_disable_send_explicit_bytes():
    """
    enable()/disable() must send distinct, idempotent commands rather than a
    single shared toggle byte — a blind toggle can desync from the firmware's
    actual armed state (e.g. across a reconnect that doesn't power-cycle the
    Arduino), silently disarming hardware when Python believes it just armed
    it. Also must not collide with the '0'-'9'/'-' autonomous text-command
    range or the 's' identity-query byte used elsewhere in the protocol.
    """
    with patch("controller.serial.pyserial.Serial") as mock_serial:
        mock_instance = get_mock_serial()
        mock_serial.return_value = mock_instance

        s = serial("COM1")

        s.enable()
        mock_instance.write.assert_called_with("e".encode('utf-8'))

        s.disable()
        mock_instance.write.assert_called_with("d".encode('utf-8'))

def test_serial_read_position():
    with patch("controller.serial.pyserial.Serial") as mock_serial:
        mock_instance = get_mock_serial()
        mock_serial.return_value = mock_instance
        
        s = serial("COM1")
        
        mock_instance.in_waiting = 20
        mock_instance.read.return_value = b"POS:10,20,30\n"
        
        pos = s.read_position()
        assert pos == (10, 20, 30)

def test_serial_read_position_corrupt_data():
    """Verify that read_position handles corrupt serial data and returns None."""
    with patch("controller.serial.pyserial.Serial") as mock_serial:
        mock_instance = get_mock_serial()
        mock_serial.return_value = mock_instance
        s = serial("COM1")
        
        # Incomplete data
        mock_instance.in_waiting = 10
        mock_instance.read.return_value = b"POS:10,20\n"
        pos = s.read_position()
        assert pos is None

        # Garbled text
        mock_instance.in_waiting = 15
        mock_instance.read.return_value = b"GarbageData\n"
        pos = s.read_position()
        assert pos is None

def test_serial_disconnect_mid_operation():
    """Verify sudden SerialException doesn't bubble up unhandled."""
    import serial as pyserial
    with patch("controller.serial.pyserial.Serial") as mock_serial:
        mock_instance = get_mock_serial()
        mock_serial.return_value = mock_instance
        s = serial("COM1")
        
        # Read operation failure
        mock_instance.read.side_effect = pyserial.SerialException("Device unplugged")
        mock_instance.in_waiting = 5
        pos = s.read_position()
        assert pos is None
        
        # Write operation failure
        mock_instance.write.side_effect = pyserial.SerialException("Device unplugged")
        # Ensure it doesn't crash
        s.enable() 
