import pytest
import sys
import os
from unittest.mock import MagicMock

class MockSerialModule(MagicMock):
    pass

mock_serial = MockSerialModule()
mock_serial.SerialTimeoutException = Exception

# Mock serial module before importing src modules
sys.modules['serial'] = mock_serial
sys.modules['serial.tools'] = MagicMock()
sys.modules['serial.tools.list_ports'] = MagicMock()
sys.modules['pygame'] = MagicMock()
sys.modules['gcodeparser'] = MagicMock()
sys.modules['color_test_new'] = MagicMock()

# Add src to sys.path to import modules
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

from serialDrive import SerialArduino
from stepper_frame import AppLogic

class MockSerial:
    def __init__(self):
        self.is_open = True
        self.in_waiting = 0
        self.written_data = b""

    def write(self, data):
        self.written_data = data

    def flush(self):
        pass

    def close(self):
        self.is_open = False


def test_send_manual_mode_command_invalid_types():
    serial_drive = SerialArduino(port='SIM')
    # Force self.ser to be a mock so it bypasses _verify_serial() returning False
    serial_drive.ser = MockSerial()
    
    # Missing required keys
    params_missing = {
        'x_axisStatus': 0.0,
        # missing y_axisStatus
    }
    # It should not raise an unhandled exception (it should be caught by try/except in the method)
    # But wait, try/except prints the error and continues. We can verify no unhandled exception.
    try:
        serial_drive.send_manual_mode_command(params_missing)
    except Exception as e:
        pytest.fail(f"Unhandled exception with missing keys: {e}")

    # Invalid types that cannot be converted to int/float
    params_invalid_types = {
        'x_axisStatus': 'invalid',  # float('invalid') raises ValueError
        'y_axisStatus': 0.0,
        'z_axisStatusR': -1.0,
        'z_axisStatusL': -1.0,
        'x_stepSize': 'not_an_int', # int('not_an_int') raises ValueError
        'y_stepSize': '1',
        'z_stepSize': '1',
        'dpad_LR': 0,
        'dpad_UD': 0,
        'LBumper': None, # int(None) raises TypeError
        'RBumper': 0,
        'manual_jog_speed': '100'
    }
    
    try:
        serial_drive.send_manual_mode_command(params_invalid_types)
    except Exception as e:
        pytest.fail(f"Unhandled exception with invalid types: {e}")


def test_send_autonomous_command_missing_keys():
    serial_drive = SerialArduino(port='SIM')
    serial_drive.ser = MockSerial()
    
    # Missing keys in autonomous mode command
    params_missing = {
        'x_step_size': '1',
        # missing y_step_size
    }
    
    try:
        serial_drive.send_autonomous_command(params_missing)
    except Exception as e:
        pytest.fail(f"Unhandled exception with missing keys in auton command: {e}")


def test_get_controller_params_missing_key_behavior():
    # If a value in GUI is empty string, does it cause an issue?
    # We can just test the expected dict structure
    pass
