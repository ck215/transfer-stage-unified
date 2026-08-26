import pytest
import math
import struct
from unittest.mock import patch, MagicMock

from controller.seiral import serial
from model.temperature_system import TemperatureSystem
from model.rotator_system import RotatorSystem

def get_mock_serial():
    mock_instance = MagicMock()
    mock_instance.is_open = True
    mock_instance.in_waiting = 0
    return mock_instance

def create_base_params():
    return {
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
        "packet_format": "<BBffffffffff" # default format
    }

def test_serial_send_manual_inf_nan():
    """Test passing inf and nan to struct pack float fields."""
    with patch("controller.seiral.pyserial.Serial") as mock_serial:
        mock_instance = get_mock_serial()
        mock_serial.return_value = mock_instance
        s = serial("COM1")
        
        # Test INF for float fields
        params_inf = create_base_params()
        params_inf["x_axisStatus"] = math.inf
        try:
            s.send_manual_mode_command(params_inf)
        except Exception as e:
            pytest.fail(f"send_manual_mode_command failed with INF: {e}")

        # Test NaN for float fields
        params_nan = create_base_params()
        params_nan["x_axisStatus"] = math.nan
        try:
            s.send_manual_mode_command(params_nan)
        except Exception as e:
            pytest.fail(f"send_manual_mode_command failed with NaN: {e}")

def test_temperature_system_extreme_ramp_rate():
    """Test passing an extremely small float that creates an infinity or large number during division."""
    temp_sys = TemperatureSystem("None") # simulator
    temp_sys.serial_conn = MagicMock()
    temp_sys.ramp_rate = "1e-300"

    # We will test to see if it causes an exception or passes "inf"
    try:
        temp_sys.send_settings()
        # Should have called write with inf
        args = temp_sys.serial_conn.ser.write.call_args
        if args:
            written = args[0][0].decode()
            assert "inf" not in written.lower()
    except Exception as e:
        pytest.fail(f"TemperatureSystem send_settings crashed on extreme ramp rate: {e}")

def test_temperature_system_nan_ramp_rate():
    temp_sys = TemperatureSystem("None")
    temp_sys.serial_conn = MagicMock()
    temp_sys.ramp_rate = "nan" 
    
    try:
        temp_sys.send_settings()
    except Exception as e:
        pytest.fail(f"TemperatureSystem send_settings crashed on nan ramp rate: {e}")

def test_rotator_system_nan_inf():
    rot = RotatorSystem()
    rot.smc = MagicMock()
    # Override async wrapper to run synchronously for the test
    rot._run_async = lambda func, *args: func(*args)
    
    rot.target_deg = "inf"
    try:
        rot._move_abs_ui()
        rot.smc.move_absolute_deg.assert_not_called()
    except Exception as e:
        pytest.fail(f"RotatorSystem crashed on inf target: {e}")
        
    rot.step_deg = "nan"
    try:
        rot._move_rel_pos_ui()
        rot.smc.move_relative_deg.assert_not_called()
    except Exception as e:
        pytest.fail(f"RotatorSystem crashed on nan step: {e}")
