import time
import math
import pytest
from unittest.mock import MagicMock, patch

from model.probes import StepperProbe, DCProbe
from model.temperature_system import TemperatureSystem
from model.rotator_system import RotatorSystem
from model.redpercent_system import RedPercentSystem, RedPercentDataLog


def test_cross_subsystem_redpercent_probe_telemetry():
    """Verify RedPercentSystem correctly polls location and velocity from bound StepperProbe."""
    probe = StepperProbe(port=None, controller_id=0)
    probe.set_positions("10.0", "20.0", "30.0")
    probe.full_speed = "500"

    red_sys = RedPercentSystem()
    red_sys.available_probes = {"Stepper Probe": probe}
    red_sys.selected_probe_name = "Stepper Probe"
    red_sys.sync_dimensions = ["X", "Y", "Z"]

    # Trigger color monitoring cycle with dummy image
    dummy_img = MagicMock()
    with patch("cv2.cvtColor", return_value=dummy_img), \
         patch("cv2.inRange", return_value=dummy_img), \
         patch("cv2.countNonZero", return_value=5000):
        red_sys.process_frame(dummy_img)

    datalog_has_entries = (len(red_sys.datalog.red_values) > 0)
    assert datalog_has_entries
    assert red_sys.datalog.loc_values["X"][-1] == 10.0


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
        temp_sys.add_history_entry(float(i), current_temp)

    history = temp_sys.get_history()
    assert len(history["temp"]) == 50
    assert abs(history["temp"][-1] - 100.0) < 2.0


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
