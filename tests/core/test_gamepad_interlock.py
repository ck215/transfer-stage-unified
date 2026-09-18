import pytest
from unittest.mock import MagicMock
from model.probes import BaseProbe

def test_manual_mode_blocked_without_gamepad():
    probe = BaseProbe(port="SIM", controller_id="None")
    probe.serial_comm = MagicMock()
    probe.poller = MagicMock()
    probe.poller.gamepad = None
    
    probe.manual_flag = True
    
    probe.send_manual_mode_command({"x_axisStatus": 1.0})
    
    assert probe.manual_flag is False
    probe.serial_comm.send_manual_mode_command.assert_called_once()
    args, kwargs = probe.serial_comm.send_manual_mode_command.call_args
    assert args[0]["x_axisStatus"] == 0.0

def test_manual_mode_allowed_with_gamepad():
    probe = BaseProbe(port="SIM", controller_id="None")
    probe.serial_comm = MagicMock()
    probe.poller = MagicMock()
    probe.poller.gamepad = MagicMock() # Truthy gamepad
    
    probe.manual_flag = True
    
    probe.send_manual_mode_command({"x_axisStatus": 1.0})
    
    assert probe.manual_flag is True
    probe.serial_comm.send_manual_mode_command.assert_called_once()
    args, kwargs = probe.serial_comm.send_manual_mode_command.call_args
    assert args[0]["x_axisStatus"] == 1.0
