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
    """Test mutating state out of order on the StepperProbe."""
    probe = StepperProbe("SIM", None)
    probe.serial_comm = MagicMock()
    
    try:
        probe.disable()
        probe.full_stop()
        probe.enter_auton()
        probe.macro_start_auton()
        probe.enter_manual()
        probe.enable()
        probe.send_stop_command()
        probe.reconnect_serial()
        probe.disable()
        probe.enter_manual()
        probe.macro_start_auton()
    except Exception as e:
        pytest.fail(f"StepperProbe crashed when mutating state out of order: {e}")

def test_system_manager_invalid_model():
    """Test registering an invalid model type."""
    manager = SystemManager()
    manager.register_model("Invalid", "Not a model")
    assert manager.get_model("Invalid") == "Not a model"

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
