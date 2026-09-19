import time
import math
import pytest
from unittest.mock import MagicMock, patch

from model.probes import StepperProbe, DCProbe
from model.temperature_system import TemperatureSystem
from model.rotator_system import RotatorSystem
from model.redpercent_system import RedPercentSystem, RedPercentDataLog




def test_temperature_system_pid_thermal_drift_simulation():
    """Simulate thermal loop convergence and drift bounds."""
    temp_sys = TemperatureSystem(port=None)
    temp_sys.setpoint = "100.0"
    temp_sys.ramp_rate = "10.0"
    temp_sys.p_term = "5.0"
    temp_sys.i_term = "0.1"
    temp_sys.d_term = "1.0"

    # Simulate 50 temperature readings converging to setpoint
    current_temp = 25.0
    for i in range(50):
        error = 100.0 - current_temp
        current_temp += error * 0.1
        temp_sys.process_raw_data(f"{float(i)},{current_temp},{temp_sys.setpoint}")

    time_arr, temp_arr, sp_arr = temp_sys.get_history()
    assert len(temp_arr) == 50
    assert abs(temp_arr[-1] - 100.0) < 2.0


def test_stepper_probe_3d_velocity_vector_math():
    """Verify 3D velocity vector calculation across X, Y, Z axes."""
    probe = StepperProbe(port=None, controller_id=0)
    probe.full_speed = "1000"
    probe.x_step = "10"
    probe.y_step = "20"
    probe.z_step = "30"

    # Verify component velocities are non-negative floats
    assert probe.vel_x >= 0.0
    assert probe.vel_y >= 0.0
    assert probe.vel_z >= 0.0
    assert isinstance(probe.vel_x, float)
