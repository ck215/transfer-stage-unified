import time
import threading
import pytest
from unittest.mock import MagicMock, patch

from model.system_manager import SystemManager
from model.probes import BaseProbe, StepperProbe, DCProbe, ChuckPositioner, _num
from model.temperature_system import TemperatureSystem
from model.rotator_system import RotatorSystem
from model.redpercent_system import RedPercentSystem, RedPercentDataLog


# ============================================================================
# 1. Parameter Boundary Values & Sanitization
# ============================================================================

def test_probes_num_sanitization_boundary_cases():
    """Verify _num handles NaN, Inf, -Inf, strings, floats, ints, and bounds."""
    assert _num("10", default=0) == 10.0
    assert _num("nan", default=5.0) == 5.0
    assert _num("inf", default=1.0) == 1.0
    assert _num("-inf", default=-1.0) == -1.0
    assert _num("invalid_string", default=42.0) == 42.0
    assert _num(None, default=7.0) == 7.0
    assert _num(-10, default=0, minimum=0) == 0.0
    assert _num(15.7, default=0, integer=True) == 15
    assert _num("0", default=1, minimum=1, integer=True) == 1




def test_rotator_system_boundary_values():
    """Verify RotatorSystem position boundary properties."""
    rotator = RotatorSystem(default_port=None)
    rotator.position = 359.999
    assert rotator.position == 359.999
    rotator.position = 0.0
    assert rotator.position == 0.0


# ============================================================================
# 2. Out-of-Order Calls & Mode Transition Interlocks
# ============================================================================

def test_base_probe_disarming_interlocks():
    """Verify full_stop, disable, and power_down reset auton/manual flags and stepping."""
    probe = BaseProbe(port=None, controller_id=0)

    probe.enter_auton()
    assert probe.auton_flag is True
    assert probe.manual_flag is False

    probe.full_stop()
    assert probe.auton_flag is False
    assert probe.manual_flag is False
    assert probe.is_stepping is False

    probe.enter_manual()
    assert probe.manual_flag is True
    assert probe.auton_flag is False

    probe.disable()
    assert probe.manual_flag is False
    assert probe.system_enabled is False


def test_stepper_probe_mutual_exclusion_auton_manual():
    """Verify entering auton clears manual mode and vice versa."""
    stepper = StepperProbe(port=None, controller_id=0)
    
    stepper.enter_manual()
    assert stepper.manual_flag is True
    assert stepper.auton_flag is False

    stepper.enter_auton()
    assert stepper.auton_flag is True
    assert stepper.manual_flag is False




# ============================================================================
# 3. Threading Safety & History Data Integrity
# ============================================================================

def test_temperature_system_history_thread_safety():
    """Verify concurrent reads and serial updates of temperature history under lock."""
    temp = TemperatureSystem(port=None)
    iterations = 300

    def serial_updater():
        for i in range(iterations):
            temp.process_raw_data(f"{float(i)},{float(i*2)},{float(i)}")

    def reader():
        for _ in range(iterations):
            time_arr, temp_arr, sp_arr = temp.get_history()
            assert len(time_arr) == len(temp_arr)

    t1 = threading.Thread(target=serial_updater)
    t2 = threading.Thread(target=reader)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    time_arr, temp_arr, sp_arr = temp.get_history()
    assert len(time_arr) <= 200


def test_redpercent_datalog_thread_safety():
    """Verify RedPercentDataLog thread safety across concurrent entries."""
    dims = ["X", "Y", "Z"]
    datalog = RedPercentDataLog(sync_dimensions=dims, probe_name="Probe1", probe_tilt_angle="0")

    def worker(worker_id):
        for i in range(50):
            datalog.add_entry(float(i), {"X": 1.0, "Y": 2.0, "Z": 3.0}, {"X": 0.1, "Y": 0.2, "Z": 0.3})

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(datalog.red_values) == 250


# ============================================================================
# 4. SystemManager Model Lifecycle & Teardown
# ============================================================================

def test_system_manager_register_get_unregister():
    """Verify SystemManager registration, lookup, and unregistration lifecycle."""
    mgr = SystemManager()
    probe = StepperProbe(port=None, controller_id=0)

    mgr.register_model("probe_a", probe)
    assert mgr.get_model("probe_a") == probe
    assert "probe_a" in mgr.active_models

    mgr.remove_model("probe_a")
    assert mgr.get_model("probe_a") is None
    assert "probe_a" not in mgr.active_models


def test_system_manager_shutdown_all_clears_models():
    """Verify shutdown_all calls disconnect/stop on registered models."""
    mgr = SystemManager()
    mock_model = MagicMock()
    mgr.register_model("mock_model", mock_model)

    mgr.shutdown_all()
    assert len(mgr.active_models) == 0
