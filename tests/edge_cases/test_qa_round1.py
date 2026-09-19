import time
import threading
import tempfile
import os
import math
import pytest
from unittest.mock import MagicMock, patch

from model.system_manager import SystemManager
from model.probes import BaseProbe, _num
from model.temperature_system import TemperatureSystem
from model.rotator_system import RotatorSystem
from model.redpercent_system import RedPercentSystem, RedPercentDataLog


# ============================================================================
# 1. Multi-System Manager Teardowns & Reboot Stress Tests
# ============================================================================

class MockHardwareModel:
    """Mock model with active poller and serial connection for teardown testing."""
    def __init__(self, fail_on_teardown=False):
        self.fail_on_teardown = fail_on_teardown
        self.poller = MagicMock()
        self.serial_conn = MagicMock()
        self.is_disconnected = False

    def disconnect(self):
        if self.fail_on_teardown:
            raise RuntimeError("Hardware disconnect failure simulated")
        self.is_disconnected = True

    def stop(self):
        if self.fail_on_teardown:
            raise AttributeError("Hardware stop failure simulated")

    def teardown(self):
        if self.fail_on_teardown:
            raise RuntimeError("Hardware teardown failure simulated")
        self.is_disconnected = True


def test_system_manager_concurrent_reboot_and_teardown():
    """
    Stress test SystemManager under rapid concurrent model registrations,
    reboots, lookups, and shutdowns from multiple threads.
    """
    manager = SystemManager()
    errors = []

    def worker(worker_id):
        try:
            model_name = f"probe_{worker_id % 3}"
            model = MockHardwareModel()
            manager.register_model(model_name, model)
            
            # Fetch model
            fetched = manager.get_model(model_name)
            assert fetched is not None or model_name not in manager.active_models

            # Simulate reboot call with mock constructor
            with patch("time.sleep", return_value=None):
                rebooted = manager.reboot_model(model_name, MockHardwareModel)
                assert rebooted is not None
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Perform final multi-system shutdown
    manager.shutdown_all()
    assert len(errors) == 0, f"Concurrent reboot/teardown raised errors: {errors}"
    assert len(manager.active_models) == 0, "Active models map should be cleared after shutdown_all"


def test_system_manager_teardown_exception_resilience():
    """
    Verify SystemManager handles failing model teardowns gracefully without crashing
    or leaving the internal lock acquired.
    """
    manager = SystemManager()
    faulty_model = MockHardwareModel(fail_on_teardown=True)
    manager.register_model("faulty_probe", faulty_model)

    with patch("time.sleep", return_value=None):
        new_model = manager.reboot_model("faulty_probe", MockHardwareModel)
        assert new_model is not None
        assert manager.get_model("faulty_probe") == new_model

    manager.shutdown_all()
    assert len(manager.active_models) == 0


def test_multi_manager_concurrent_shutdown():
    """
    Simulate multiple SystemManager instances being shut down concurrently under heavy load.
    """
    managers = [SystemManager() for _ in range(5)]
    for i, mgr in enumerate(managers):
        mgr.register_model("temp_sys", TemperatureSystem())
        mgr.register_model("rotator_sys", RotatorSystem())

    def shutdown_worker(mgr):
        mgr.shutdown_all()

    threads = [threading.Thread(target=shutdown_worker, args=(mgr,)) for mgr in managers]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    for mgr in managers:
        assert len(mgr.active_models) == 0


# ============================================================================
# 2. Rapid Parameter Updates & High-Frequency Concurrent Writes
# ============================================================================

def test_rapid_parameter_updates_probes():
    """
    Stress test concurrent rapid parameter updates (speeds, step sizes, flags)
    and velocity calculations on BaseProbe.
    """
    probe = BaseProbe(port=None, controller_id=0)
    iterations = 500

    def update_parameters():
        for i in range(iterations):
            probe.full_speed = str(100 + (i % 500))
            probe.x_step = str(1 + (i % 32))
            probe.y_step = str(1 + (i % 32))
            probe.z_step = str(1 + (i % 32))
            probe.system_enabled = (i % 2 == 0)

    def read_velocities():
        for _ in range(iterations):
            _ = probe.vel_x
            _ = probe.vel_y
            _ = probe.vel_z

    t1 = threading.Thread(target=update_parameters)
    t2 = threading.Thread(target=read_velocities)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert probe.full_speed is not None


def test_rapid_parameter_updates_temperature_system():
    """
    Test rapid updates to TemperatureSystem PID, setpoints, and ramp rates
    from multiple concurrent threads.
    """
    temp_sys = TemperatureSystem(port=None)
    iterations = 300

    def updater(thread_id):
        for i in range(iterations):
            temp_sys.setpoint = str(20.0 + (i * 0.1) + thread_id)
            temp_sys.ramp_rate = str(1.0 + (i % 10))
            temp_sys.p_term = str(2.0 + thread_id)
            temp_sys.i_term = str(0.5 + (i % 3))
            temp_sys.d_term = str(0.1 * i)
            temp_sys.send_settings()

    threads = [threading.Thread(target=updater, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert float(temp_sys.setpoint) > 0


def test_rapid_concurrent_data_logging_redpercent():
    """
    Stress test RedPercentDataLog with high-frequency concurrent add_entry calls
    across 10 threads, verifying thread-safe append operations.
    """
    dims = ["X", "Y", "Z"]
    data_log = RedPercentDataLog(sync_dimensions=dims, probe_name="TestProbe", probe_tilt_angle="45")
    num_threads = 10
    entries_per_thread = 200

    def log_worker(thread_id):
        for i in range(entries_per_thread):
            val = float(thread_id * 100 + i)
            locs = {"X": val, "Y": val + 0.1, "Z": val + 0.2}
            vels = {"X": 1.0, "Y": 2.0, "Z": 3.0}
            data_log.add_entry(val, locs, vels)

    threads = [threading.Thread(target=log_worker, args=(i,)) for i in range(num_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    expected_total = num_threads * entries_per_thread
    assert len(data_log.red_values) == expected_total
    assert len(data_log.loc_values["X"]) == expected_total

    with tempfile.NamedTemporaryFile(mode='w', delete=False, suffix='.csv') as tmp:
        tmp_path = tmp.name

    try:
        data_log.save_to_csv(tmp_path)
        assert os.path.exists(tmp_path)
        assert os.path.getsize(tmp_path) > 0
    finally:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)


# ============================================================================
# 3. Boundary Conditions & Extreme Input Tests
# ============================================================================

def test_temperature_system_boundary_conditions():
    """
    Test TemperatureSystem parameter calculations with edge case inputs:
    NaN, Inf, negative values, zero ramp rates, malformed strings.
    """
    temp_sys = TemperatureSystem(port=None)

    boundary_cases = [
        {"ramp": "0", "setpoint": "-273.15"},
        {"ramp": "nan", "setpoint": "inf"},
        {"ramp": "-inf", "setpoint": "-10"},
        {"ramp": "invalid_string", "setpoint": "999999999"},
        {"ramp": "0.000000001", "setpoint": "0"},
    ]

    for case in boundary_cases:
        temp_sys.ramp_rate = case["ramp"]
        temp_sys.setpoint = case["setpoint"]
        try:
            temp_sys.send_settings()
        except Exception as e:
            pytest.fail(f"send_settings crashed on boundary input {case}: {e}")


def test_probes_num_helper_boundary_cases():
    """
    Test the _num boundary helper in probes module against NaN, Inf, empty,
    out-of-bounds inputs, integer casting, and fallback defaults.
    """
    assert _num("10", default=0) == 10.0
    assert _num("nan", default=5.0) == 5.0
    assert _num("inf", default=1.0) == 1.0
    assert _num("-inf", default=-1.0) == -1.0
    assert _num("invalid", default=42.0) == 42.0
    assert _num(None, default=7.0) == 7.0
    assert _num(-10, default=0, minimum=0) == 0.0
    assert _num(15.7, default=0, integer=True) == 15


def test_rotator_system_boundary_conditions():
    """
    Test RotatorSystem thread safety and property setter edge cases.
    """
    rotator = RotatorSystem(default_port=None)

    rotator.position = 359.999
    assert rotator.position == 359.999

    rotator.state = "FAULT"
    assert rotator.state == "FAULT"

    rotator.error = "ERR_OVERTEMP"
    assert rotator.error == "ERR_OVERTEMP"

    error_logged = []
    rotator.error_callback = lambda e: error_logged.append(e)

    def faulty_action():
        raise ValueError("Simulated rotator hardware error")

    rotator._run_async(faulty_action)
    time.sleep(0.1)

    assert len(error_logged) == 1
    assert isinstance(error_logged[0], ValueError)
