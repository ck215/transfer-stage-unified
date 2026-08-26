import pytest
import math
import sys
import os
from unittest import mock

class MockSerialModule:
    class SerialTimeoutException(Exception):
        pass
    class SerialException(Exception):
        pass

sys.modules['serial'] = mock.MagicMock()
sys.modules['serial'].SerialTimeoutException = MockSerialModule.SerialTimeoutException
sys.modules['serial'].SerialException = MockSerialModule.SerialException

# Ensure src is in the python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))
from serialDrive import SerialArduino

@pytest.fixture
def serial_sim():
    return SerialArduino(port='SIM')

def test_send_autonomous_extreme_values(serial_sim):
    params = {
        'command_code_auton': 'CODE',
        'command_code_manual': float('inf'),
        'x_step_size': float('nan'),
        'y_step_size': -float('inf'),
        'z_step_size': 10**100,
        'full_speed': -10**100,
        'slow_speed': 'A' * 10000,
        'brake_distance': None,
        'x_dist': [],
        'y_dist': {},
        'z_dist': set()
    }
    # Mock _verify_serial to return True so it attempts to format and write
    serial_sim._verify_serial = mock.MagicMock(return_value=True)
    serial_sim.ser = mock.MagicMock()
    
    serial_sim.send_autonomous_command(params)

def test_send_manual_mode_struct_pack_inf_nan(serial_sim):
    params = {
        'x_axisStatus': float('nan'),
        'y_axisStatus': float('inf'),
        'z_axisStatusL': 1e308,
        'z_axisStatusR': -1e308,
        'x_stepSize': 40000, # > 32767, overflow for 'h'
        'y_stepSize': -40000, # < -32768, overflow for 'h'
        'z_stepSize': float('nan'), # int(nan)
        'dpad_LR': float('inf'), # int(inf)
        'dpad_UD': 0,
        'LBumper': 1,
        'RBumper': 2,
        'manual_jog_speed': 0
    }
    serial_sim._verify_serial = mock.MagicMock(return_value=True)
    serial_sim.ser = mock.MagicMock()
    
    serial_sim.send_manual_mode_command(params)

def test_send_manual_mode_large_int(serial_sim):
    params = {
        'x_axisStatus': 0,
        'y_axisStatus': 0,
        'z_axisStatusL': 0,
        'z_axisStatusR': 0,
        'x_stepSize': 0,
        'y_stepSize': 0,
        'z_stepSize': 0,
        'dpad_LR': 0,
        'dpad_UD': 0,
        'LBumper': 0,
        'RBumper': 0,
        'manual_jog_speed': 32768 # > 32767
    }
    serial_sim._verify_serial = mock.MagicMock(return_value=True)
    serial_sim.ser = mock.MagicMock()
    
    serial_sim.send_manual_mode_command(params)
