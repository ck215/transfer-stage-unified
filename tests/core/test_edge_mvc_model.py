import time
import pytest
from unittest.mock import MagicMock
from model.probes import StepperProbe, DCProbe
from model.system_manager import SystemManager

def test_assign_corrupted_dict_to_ui_schema():
    """Test passing/assigning corrupted dictionaries to ui_schema."""
    probe = StepperProbe("SIM", None)
    corrupted_schema = {"broken": "schema"}
    
    # ui_schema is a property, so assigning should raise AttributeError
    with pytest.raises(AttributeError):
        probe.ui_schema = corrupted_schema

def test_modifying_missing_variables():
    """Test modifying missing variables on the model."""
    probe = StepperProbe("SIM", None)
    
    # Try setting a variable that doesn't exist in the class
    probe.non_existent_var = "Should not crash"
    assert probe.non_existent_var == "Should not crash"
    
    # Verify that this doesn't impact get_params
    params = probe.get_params()
    assert "non_existent_var" not in params

def test_mutating_state_out_of_order():
    """Test mutating state out of order checks exact internal variables instead of just avoiding crashes."""
    probe = StepperProbe("SIM", None)
    probe.serial_comm = MagicMock()
    probe.system_enabled = True # Force enable to succeed for flag flips
    
    probe.enter_auton()
    assert probe.auton_flag is True
    assert probe.manual_flag is False
    
    probe.enter_manual()
    assert probe.auton_flag is False
    assert probe.manual_flag is True
    
    probe.macro_start_auton()
    assert probe.auton_flag is True
    assert probe.manual_flag is False
    assert probe.is_stepping is True
    
    probe.full_stop()
    assert probe.auton_flag is False
    assert probe.manual_flag is False
    assert probe.is_stepping is False

def test_mutually_exclusive_probe_flags():
    """Test that auton_flag and manual_flag can never be active at the same time."""
    probe = StepperProbe("SIM", None)
    probe.serial_comm = MagicMock()
    probe.system_enabled = True
    
    probe.enter_auton()
    assert not (probe.auton_flag and probe.manual_flag)
    
    probe.enter_manual()
    assert not (probe.auton_flag and probe.manual_flag)
    
    # Simulate a malformed state assignment
    probe.auton_flag = True
    probe.manual_flag = True
    probe.full_stop()
    assert not probe.auton_flag and not probe.manual_flag

def test_auto_disable_interlock_fires_with_no_view_attached():
    """The 5-minute idle interlock must live in the model so it protects
    every frontend, including the web dashboard (which previously had no
    auto-disable at all). No view/poller callback involved here at all."""
    probe = StepperProbe("SIM", None)
    probe.serial_comm = MagicMock()
    probe._INTERLOCK_POLL_INTERVAL = 0.02
    probe._INTERLOCK_TIMEOUT = 0.05

    probe.enable()
    assert probe.system_enabled is True

    time.sleep(0.3)

    assert probe.system_enabled is False
    probe.serial_comm.disable.assert_called()

def test_auto_disable_interlock_deferred_while_stepping():
    """Per user decision: defer the idle disable while actively stepping/
    manual, rather than disabling unconditionally like main does."""
    probe = StepperProbe("SIM", None)
    probe.serial_comm = MagicMock()
    probe._INTERLOCK_POLL_INTERVAL = 0.02
    probe._INTERLOCK_TIMEOUT = 0.05

    probe.enable()
    probe.is_stepping = True

    time.sleep(0.3)

    assert probe.system_enabled is True  # not disabled while "stepping"
    probe.full_stop()
    assert probe.system_enabled is False

def test_stepper_probe_step_size_defaults():
    """StepperProbe must default to step size 1, matching main/src/stepper_frame.py,
    not BaseProbe's 16 (a 16x-further-than-intended move on identical UI input)."""
    probe = StepperProbe("SIM", None)
    assert probe.x_step == "1"
    assert probe.y_step == "1"
    assert probe.z_step == "1"

def test_system_manager_invalid_model():
    """Test registering an invalid model type."""
    manager = SystemManager()
    manager.register_model("Invalid", "Not a model")
    assert manager.get_model("Invalid") == "Not a model"

def test_shutdown_all_disables_probes_without_disconnect_or_stop():
    """BaseProbe (Stepper/Chuck/DC) defines disable()/power_down(), not
    disconnect()/stop() — shutdown_all must fall back to disable() so
    motors actually de-energize on app close/reboot, matching main's
    explicit self.serial.disable() on window close."""
    manager = SystemManager()
    probe = MagicMock(spec=['disable'])
    manager.register_model("Stepper", probe)
    manager.shutdown_all()
    probe.disable.assert_called_once()

def test_reboot_model_disables_old_probe_without_disconnect_or_stop():
    manager = SystemManager()
    old_probe = MagicMock(spec=['disable'])
    manager.active_models["Stepper"] = old_probe
    manager.reboot_model("Stepper", lambda: MagicMock())
    old_probe.disable.assert_called_once()

def test_dcprobe_mutating_state_out_of_order():
    """Test mutating state out of order on the DCProbe."""
    probe = DCProbe("SIM", None)
    probe.serial_comm = MagicMock()
    
    try:
        probe.disable()
        probe.enter_auton()
        probe.enable()
        probe.reconnect_serial()
        probe.disable()
        probe.enter_manual()
    except Exception as e:
        pytest.fail(f"DCProbe crashed when mutating state out of order: {e}")

def test_dcprobe_invalid_speed():
    """Test DCProbe with invalid speed (assignment)."""
    probe = DCProbe("SIM", None)
    
    # Just checking it doesn't crash the program unexpectedly.
    try:
        probe.full_speed = "1000"
        probe.full_speed = "invalid_speed"
    except Exception:
        pass  # It's okay if it raises an exception, we just don't want a hard crash

from model.rotator_system import RotatorSystem

def test_rotator_state_code_map():
    """Test that the RotatorSystem correctly maps SMC100 state codes to human strings."""
    rotator = RotatorSystem()
    assert rotator._map_state_code("0A") == "Not referenced - run Home"
    assert rotator._map_state_code("33") == "Ready"
    assert rotator._map_state_code("1E") == "Homing"
    assert rotator._map_state_code("28") == "Moving"
    assert rotator._map_state_code("3C") == "Disabled"
    assert rotator._map_state_code("UNKNOWN") == "UNKNOWN"
