import time
import pytest
from unittest.mock import MagicMock
from model.probes import StepperProbe, DCProbe
from model.system_manager import SystemManager
from conftest import ManagedStub

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

def test_system_manager_rejects_an_invalid_model():
    """Registration is a contract boundary (RC-1).

    This used to accept the string "Not a model" and store it, because
    register_model was a bare dict write. A non-ManagedModel in the registry
    is a model that will be silently skipped at shutdown.
    """
    manager = SystemManager()
    with pytest.raises(TypeError):
        manager.register("Invalid", "Not a model")
    assert manager.get_model("Invalid") is None

def test_shutdown_all_tears_down_registered_models():
    manager = SystemManager()
    probe = ManagedStub("Stepper")
    manager.register("Stepper", probe)
    manager.shutdown_all()
    assert probe.teardowns == 1

def test_shutdown_all_stops_before_tearing_down():
    """RC-1 item 4: no model's teardown can skip the stop."""
    manager = SystemManager()
    probe = ManagedStub("Stepper", teardown_error=RuntimeError("wedged"))
    manager.register("Stepper", probe)
    manager.shutdown_all()
    assert probe.stops == 1
    assert probe.teardowns == 1

def test_release_tears_down_the_model_it_removes():
    """remove_model only removed; the documentation claimed it tore down too."""
    manager = SystemManager()
    probe = ManagedStub("Stepper")
    manager.register("Stepper", probe)
    manager.release("Stepper")
    assert manager.get_model("Stepper") is None
    assert probe.teardowns == 1

def test_dcprobe_mutating_state_out_of_order():
    """Test mutating state out of order on the DCProbe."""
    probe = DCProbe("SIM", None)
    probe.serial_comm = MagicMock()
    
    try:
        probe.disable()
        probe.enter_auton()
        probe.enable()
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

def test_rotator_confirm_rotation_default():
    rotator = RotatorSystem()
    assert rotator._confirm_rotation(30.0) is True
    assert rotator._confirm_rotation(-30.0) is True
    # Default without callback denies > 30
    assert rotator._confirm_rotation(30.1) is False
    assert rotator._confirm_rotation(-30.1) is False

def test_rotator_confirm_rotation_with_callback():
    rotator = RotatorSystem()
    mock_cb = MagicMock(return_value=True)
    rotator.confirm_rotation_callback = mock_cb
    
    assert rotator._confirm_rotation(40.0) is True
    mock_cb.assert_called_once_with(40.0)
    
    mock_cb.return_value = False
    assert rotator._confirm_rotation(-40.0) is False
    mock_cb.assert_called_with(-40.0)
