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

