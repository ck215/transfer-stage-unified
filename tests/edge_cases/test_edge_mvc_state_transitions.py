import pytest
from unittest.mock import MagicMock, patch
from model.probes import StepperProbe, DCProbe
import time

def get_mock_serial():
    mock_instance = MagicMock()
    mock_instance.is_open = True
    mock_instance.in_waiting = 0
    return mock_instance

def test_rapid_manual_auton_toggle():
    with patch("controller.seiral.pyserial.Serial") as mock_serial:
        mock_serial.return_value = get_mock_serial()
        probe = StepperProbe("COM1", "Virtual Controller A")
        
        # Rapidly toggle between manual and auton
        for _ in range(100):
            probe.enter_manual()
            probe.enter_auton()
        
        # Check final state
        assert probe.auton_flag is True
        assert probe.manual_flag is False

def test_rapid_enable_disable():
    with patch("controller.seiral.pyserial.Serial") as mock_serial:
        mock_serial.return_value = get_mock_serial()
        probe = StepperProbe("COM1", "Virtual Controller A")
        
        # Rapidly enable and disable
        for _ in range(100):
            probe.enable()
            probe.disable()
            
        # Check final state
        assert probe.system_enabled is False
        assert probe.auton_flag is False
        assert probe.manual_flag is False

def test_enable_during_manual_auton_transition():
    with patch("controller.seiral.pyserial.Serial") as mock_serial:
        mock_serial.return_value = get_mock_serial()
        probe = StepperProbe("COM1", "Virtual Controller A")
        
        for _ in range(50):
            probe.enter_manual()
            probe.enable()
            probe.enter_auton()
            probe.disable()
            
        assert probe.system_enabled is False
        
def test_toggle_enable_rapidly():
    with patch("controller.seiral.pyserial.Serial") as mock_serial:
        mock_serial.return_value = get_mock_serial()
        probe = StepperProbe("COM1", "Virtual Controller A")

        for _ in range(100):
            probe.toggle_enable()

def test_ui_schema_no_longer_has_system_enabled_toggle():
    with patch("controller.seiral.pyserial.Serial") as mock_serial:
        mock_serial.return_value = get_mock_serial()
        probe = StepperProbe("COM1", "Virtual Controller A")

    schema = probe.ui_schema
    system_control = next(s for s in schema["sections"] if s["title"] == "System Control")
    model_attrs = {el.get("model_attr") for el in system_control["elements"] if "model_attr" in el}

    assert "system_enabled" not in model_attrs
    assert model_attrs == {"auton_flag", "manual_flag"}

    toggle_elements = {el["model_attr"]: el for el in system_control["elements"] if el["type"] == "toggle"}
    assert toggle_elements["auton_flag"]["command"] == "toggle_auton"
    assert toggle_elements["manual_flag"]["command"] == "toggle_manual"

def test_auton_manual_toggle_mutual_exclusivity():
    with patch("controller.seiral.pyserial.Serial") as mock_serial:
        mock_serial.return_value = get_mock_serial()
        probe = StepperProbe("COM1", "Virtual Controller A")

    probe.toggle_auton()
    assert probe.auton_flag is True
    assert probe.manual_flag is False
    assert probe.system_enabled is True

    probe.toggle_manual()
    assert probe.auton_flag is False
    assert probe.manual_flag is True
    assert probe.system_enabled is True

    probe.toggle_manual()
    assert probe.auton_flag is False
    assert probe.manual_flag is False
    assert probe.system_enabled is False

