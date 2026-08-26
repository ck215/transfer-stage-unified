import pytest
import struct
from unittest.mock import patch, MagicMock
import serial as pyserial
import pygame

from controller.seiral import serial
from controller.gamepad import ControllerPoller

def get_mock_serial():
    mock_instance = MagicMock()
    mock_instance.is_open = True
    mock_instance.in_waiting = 0
    return mock_instance

def test_serial_read_disconnect_exception():
    with patch("controller.seiral.pyserial.Serial") as mock_serial_class:
        mock_instance = get_mock_serial()
        mock_serial_class.return_value = mock_instance
        s = serial("COM1")
        
        # Simulate connection dropping while reading
        mock_instance.read.side_effect = Exception("Hardware disconnect")
        mock_instance.in_waiting = 1
        
        with patch("controller.seiral.ErrorPopupManager.report_error") as mock_err:
            pos = s.read_position()
            assert pos is None
            mock_err.assert_called_once()
            assert "Error reading position" in mock_err.call_args[0][1]

def test_serial_write_timeout_manual():
    with patch("controller.seiral.pyserial.Serial") as mock_serial_class:
        mock_instance = get_mock_serial()
        mock_serial_class.return_value = mock_instance
        s = serial("COM1")

        # Simulate timeout on write
        mock_instance.write.side_effect = pyserial.SerialTimeoutException("Write Timeout")
        
        params = {
            "x_axisStatus": 0, "y_axisStatus": 0, "z_axisStatusL": -1.0, "z_axisStatusR": -1.0,
            "x_stepSize": 10, "y_stepSize": 10, "z_stepSize": 10,
            "dpad_LR": 0, "dpad_UD": 0, "LBumper": 0, "RBumper": 0, "manual_jog_speed": 400
        }

        with patch("controller.seiral.ErrorPopupManager.report_error") as mock_err:
            s.send_manual_mode_command(params)
            mock_err.assert_called_once()
            assert "WRITE TIMEOUT ERROR" in mock_err.call_args[0][1]

def test_serial_write_timeout_autonomous():
    with patch("controller.seiral.pyserial.Serial") as mock_serial_class:
        mock_instance = get_mock_serial()
        mock_serial_class.return_value = mock_instance
        s = serial("COM1")

        mock_instance.write.side_effect = pyserial.SerialTimeoutException("Write Timeout")
        
        params = {
            "command_code_auton": 1, "command_code_manual": 0,
            "x_step_size": 1, "y_step_size": 1, "z_step_size": 1,
            "full_speed": 100, "slow_speed": 50, "brake_distance": 10,
            "x_dist": 10, "y_dist": 10, "z_dist": 10
        }

        with patch("controller.seiral.ErrorPopupManager.report_error") as mock_err:
            s.send_autonomous_command(params)
            mock_err.assert_called_once()
            assert "WRITE TIMEOUT ERROR" in mock_err.call_args[0][1]

def test_serial_not_open_warning():
    with patch("controller.seiral.pyserial.Serial") as mock_serial_class:
        mock_instance = get_mock_serial()
        mock_serial_class.return_value = mock_instance
        s = serial("COM1")
        
        # Force closed
        mock_instance.is_open = False
        
        with patch("controller.seiral.ErrorPopupManager.report_warning") as mock_warn:
            s.send_manual_mode_command({})
            mock_warn.assert_called_once()
            assert "connection not established" in mock_warn.call_args[0][1]

def test_gamepad_os_disconnect_mid_poll():
    with patch("controller.gamepad.pygame") as mock_pygame:
        # Avoid init errors
        mock_pygame.joystick.get_init.return_value = True
        
        claims = {"ProcessA": "ID 0: Mock"}
        poller = ControllerPoller("ID 0: Mock", claims, "ProcessA")
        poller.is_polling = True
        
        poller.gamepad = MagicMock()
        poller.gamepad.joystick = MagicMock()
        
        with patch.object(poller, "_is_os_connected", return_value=False):
            with patch.object(poller, "_handle_disconnect") as mock_handle:
                poller._poll_loop()
                mock_handle.assert_called_once()

def test_gamepad_pygame_error_mid_poll():
    with patch("controller.gamepad.pygame") as mock_pygame:
        claims = {"ProcessA": "ID 0: Mock"}
        poller = ControllerPoller("ID 0: Mock", claims, "ProcessA")
        poller.is_polling = True
        
        # Override OS connection check so it proceeds to polling
        poller._is_os_connected = MagicMock(return_value=True)
        poller.gamepad = MagicMock()
        
        # Trigger Pygame error during event pumping
        mock_pygame.error = Exception  # Define an error class
        mock_pygame.event.get.side_effect = mock_pygame.error("Controller physically yanked")
        
        with patch("controller.gamepad.ErrorPopupManager.report_error") as mock_err:
            with patch.object(poller, "_handle_disconnect") as mock_handle:
                poller._poll_loop()
                mock_err.assert_called_once()
                assert "Pygame error during polling" in mock_err.call_args[0][1]
                mock_handle.assert_called_once()
