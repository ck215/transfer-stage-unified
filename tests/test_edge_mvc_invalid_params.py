import pytest
import struct
import sys
import os
sys.path.insert(0, os.path.abspath('src'))

from controller.seiral import serial
from model.probes import BaseProbe

class MockSerial:
    is_open = True
    def write(self, *args, **kwargs):
        pass
    def flush(self):
        pass

def get_sim_serial():
    s = serial('SIM')
    s.ser = MockSerial()
    return s

def test_manual_mode_struct_packing_out_of_bounds():
    s = get_sim_serial()
    params = {
        'x_axisStatus': 0.0, 'y_axisStatus': 0.0,
        'z_axisStatusL': -1.0, 'z_axisStatusR': -1.0,
        'x_stepSize': 100000000, # Too big for 'h'
        'y_stepSize': 0, 'z_stepSize': 0,
        'dpad_LR': 0, 'dpad_UD': 0,
        'LBumper': 0, 'RBumper': 0,
        'manual_jog_speed': 0,
        'packet_format': '<BBffffffffff'
    }
    # It catches struct.error inside except Exception as e
    # So it doesn't crash the program directly. We'll verify it doesn't crash.
    s.send_manual_mode_command(params)

def test_probe_none_controller_params():
    probe = BaseProbe("SIM", "dummy")
    probe.serial_comm = get_sim_serial()
    
    # Passing None to controller_params crashes with AttributeError
    with pytest.raises(AttributeError, match="'NoneType' object has no attribute 'get'"):
        probe.send_manual_mode_command(None)

def test_auton_missing_keys():
    s = get_sim_serial()
    params = {}
    
    # It triggers KeyError, but is caught by except Exception inside seiral.py
    # So it doesn't crash. We'll verify it returns silently.
    s.send_autonomous_command(params)

def test_pyserial_none_fallback_error():
    # If pyserial is None (like when serial is not installed),
    # the exception handling tries to access pyserial.SerialTimeoutException,
    # causing an AttributeError.
    import controller.seiral
    original_pyserial = controller.seiral.pyserial
    controller.seiral.pyserial = None
    
    s = get_sim_serial()
    params = {} # will trigger KeyError
    
    # Should crash with AttributeError trying to evaluate None.SerialTimeoutException
    try:
        with pytest.raises(AttributeError, match="'NoneType' object has no attribute 'SerialTimeoutException'"):
            s.send_autonomous_command(params)
    finally:
        controller.seiral.pyserial = original_pyserial

def test_probe_invalid_numeric_inputs():
    probe = BaseProbe("SIM", "dummy")
    probe.serial_comm = get_sim_serial()
    
    # Test blank inputs
    probe.man_full_speed = ""
    probe.x_step = ""
    
    try:
        probe.send_manual_mode_command({})
    except Exception as e:
        pytest.fail(f"send_manual_mode_command raised Exception with blank input: {e}")
        
    params = probe.get_params()
    assert params["full_speed"] == 400
    assert params["x_step_size"] == 16
    
    # Test garbage inputs
    probe.man_full_speed = "abc"
    probe.x_step = "def"
    try:
        probe.send_manual_mode_command({})
    except Exception as e:
        pytest.fail(f"send_manual_mode_command raised Exception with garbage input: {e}")
        
    params = probe.get_params()
    assert params["full_speed"] == 400
    assert params["x_step_size"] == 16
    
    # Test fractional speeds
    probe.man_full_speed = "0.5"
    probe.full_speed = "200.5"
    probe.x_step = "16.5"
    try:
        probe.send_manual_mode_command({})
    except Exception as e:
        pytest.fail(f"send_manual_mode_command raised Exception with fractional input: {e}")
        
    params = probe.get_params()
    assert params["full_speed"] == 200.5
    assert params["x_step_size"] == 16  # integer clamp
