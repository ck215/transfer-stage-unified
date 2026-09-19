import pytest
import sys
import time
from unittest.mock import MagicMock, patch
from error_routing import ErrorRouter
from controller.serial import serial, PACKET_FORMAT
from controller.gamepad import ControllerPoller, BaseGamepad, XboxGamepad, get_gamepad_wrapper


# ==========================================
# 1. Disconnected Joystick & Reconnect Tests
# ==========================================

def test_poller_initialization_with_none_or_invalid_id():
    """Verify initializing poller with None, 'None', or 'Virtual' sets claim to 'None Detected'."""
    claims = {}
    poller = ControllerPoller(None, claims, "TestProcess")
    assert poller.gamepad is None
    assert claims["TestProcess"] == "None Detected"

    poller2 = ControllerPoller("Virtual-Controller", claims, "TestProcess2")
    assert poller2.gamepad is None
    assert claims["TestProcess2"] == "None Detected"


def test_poller_os_disconnect_during_poll_loop():
    """Verify that OS disconnection during polling triggers _handle_disconnect and clears gamepad."""
    claims = {"TestProcess": 0}
    poller = ControllerPoller.__new__(ControllerPoller)
    poller.process_name = "TestProcess"
    poller.active_claims = claims
    poller.controllerID = 0
    poller.controller_index = 0
    poller.gamepad = MagicMock()
    poller.is_polling = True
    poller.gui_root = None
    poller.log_updater = None
    poller.activity_callback = None

    with patch.object(ControllerPoller, "_is_os_connected", return_value=False):
        with patch.object(ErrorRouter, "report_warning") as mock_warn:
            poller._poll_loop()
            assert poller.gamepad is None
            assert poller.is_polling is False
            assert claims["TestProcess"] == "None Detected"
            mock_warn.assert_called_once()


def test_poller_pygame_error_during_get_mapped_state():
    """Verify pygame.error during hardware read resets gamepad gracefully."""
    import pygame
    poller = ControllerPoller.__new__(ControllerPoller)
    poller.is_polling = True
    poller.gamepad = MagicMock()
    poller.gamepad.get_mapped_state.side_effect = pygame.error("Joystick hardware removed")

    with patch.object(ErrorRouter, "report_error") as mock_err:
        state = poller.get_mapped_state()
        assert state == {}
        assert poller.gamepad is None
        assert poller.is_polling is False
        mock_err.assert_called_once()


def test_poller_reconnect_flow():
    """Verify connect_controller attempts re-initialization."""
    claims = {}
    with patch("controller.gamepad.pygame") as mock_pygame:
        mock_pygame.joystick.get_count.return_value = 1
        mock_js = MagicMock()
        mock_js.get_name.return_value = "Controller (Xbox One For Windows)"
        mock_pygame.joystick.Joystick.return_value = mock_js

        poller = ControllerPoller.__new__(ControllerPoller)
        poller.process_name = "ProbeA"
        poller.active_claims = claims
        poller.controllerID = 0
        poller.gamepad = None
        poller.is_polling = False
        poller.gui_root = None
        poller.log_updater = None
        poller.activity_callback = None

        with patch.object(ControllerPoller, "_is_os_connected", return_value=True):
            success = poller.connect_controller()
            assert success is True
            assert poller.gamepad is not None
            assert claims["ProbeA"] == 0


def test_get_mapped_state_when_disconnected():
    """Verify get_mapped_state returns empty dict when gamepad is None or unpolled."""
    poller = ControllerPoller.__new__(ControllerPoller)
    poller.gamepad = None
    poller.is_polling = False
    assert poller.get_mapped_state() == {}


# ==========================================
# 2. Serial Command Dispatching & Routes
# ==========================================

def test_serial_auton_command_dispatch():
    """Verify autonomous 12-field command string dispatch."""
    with patch("controller.serial.pyserial.Serial") as mock_serial_cls:
        mock_inst = MagicMock()
        mock_inst.is_open = True
        mock_serial_cls.return_value = mock_inst

        s = serial("COM1")
        params = {
            'x_step_size': 100,
            'y_step_size': 200,
            'z_step_size': 300,
            'full_speed': 1000,
            'slow_speed': 100,
            'brake_distance': 50,
            'x_dist': 5.0,
            'y_dist': 10.0,
            'z_dist': 2.0,
            'command_code_manual': 0,
            'command_code_auton': 1,
        }
        s.send_autonomous_command(params)
        assert mock_inst.write.call_count == 2
        sent_bytes = mock_inst.write.call_args_list[1][0][0]
        expected_str = "100,200,300,0,1000,100,50,5.0,10.0,2.0,0,1\n"
        assert sent_bytes == expected_str.encode('utf-8')






def test_serial_enable_disable_disconnected():
    """Verify enable() and disable() raise ValueError when port is disconnected."""
    s = serial("SIM")
    with pytest.raises(ValueError, match="Arduino not detected"):
        s.enable()
    with pytest.raises(ValueError, match="Arduino not detected"):
        s.disable()


# ==========================================
# 3. Corrupt Serial Data & Buffer Handling
# ==========================================

def test_serial_read_position_corrupt_text_and_floats():
    """Verify corrupt/malformed POS strings are handled without crashing."""
    with patch("controller.serial.pyserial.Serial") as mock_serial_cls:
        mock_inst = MagicMock()
        mock_inst.is_open = True
        mock_serial_cls.return_value = mock_inst

        s = serial("COM1")

        # Non-numeric floats
        mock_inst.in_waiting = 20
        mock_inst.read.return_value = b"POS:abc,def,ghi\n"
        assert s.read_position() is None

        # Truncated POS string
        mock_inst.in_waiting = 15
        mock_inst.read.return_value = b"POS:10,20\n"
        assert s.read_position() is None

        # Too many fields
        mock_inst.in_waiting = 25
        mock_inst.read.return_value = b"POS:10,20,30,40\n"
        assert s.read_position() is None


def test_serial_read_position_buffer_overflow_prevention():
    """Verify read_position caps buffer size under heavy corrupt stream."""
    with patch("controller.serial.pyserial.Serial") as mock_serial_cls:
        mock_inst = MagicMock()
        mock_inst.is_open = True
        mock_serial_cls.return_value = mock_inst

        s = serial("COM1")
        s._read_buffer = "X" * 1500
        mock_inst.in_waiting = 0

        s.read_position()
        assert len(s._read_buffer) <= 512


def test_serial_read_position_multiline_stream():
    """Verify reading multiple POS lines returns the latest valid reading."""
    with patch("controller.serial.pyserial.Serial") as mock_serial_cls:
        mock_inst = MagicMock()
        mock_inst.is_open = True
        mock_serial_cls.return_value = mock_inst

        s = serial("COM1")
        mock_inst.in_waiting = 50
        mock_inst.read.return_value = b"POS:100,200,300\nGARBAGE\nPOS:400,500,600\n"

        pos = s.read_position()
        assert pos == (400, 500, 600)


# ==========================================
# 4. Exception Interceptors & ErrorRouter
# ==========================================

def test_error_router_custom_callbacks():
    """Verify custom error, warning, and info callbacks intercept reports."""
    mock_err = MagicMock()
    mock_warn = MagicMock()
    mock_info = MagicMock()

    ErrorRouter._last_messages.clear()
    ErrorRouter.set_callbacks(mock_err, mock_warn, mock_info)

    test_exc = RuntimeError("Test Exception")
    ErrorRouter.report_error("ErrTitle", "ErrMessage 123", test_exc)
    mock_err.assert_called_once_with("ErrTitle", "ErrMessage 123", test_exc)

    ErrorRouter.report_warning("WarnTitle", "WarnMessage 123")
    mock_warn.assert_called_once_with("WarnTitle", "WarnMessage 123", None)

    ErrorRouter.report_info("InfoTitle", "InfoMessage 123")
    mock_info.assert_called_once_with("InfoTitle", "InfoMessage 123")

    ErrorRouter.set_callbacks(None, None, None)


def test_error_router_spam_suppression():
    """Verify duplicate messages within 5 seconds are suppressed by _is_spam."""
    mock_err = MagicMock()
    ErrorRouter._last_messages.clear()
    ErrorRouter.set_callbacks(mock_err, None, None)

    ErrorRouter.report_error("Title", "Repeated Message")
    assert mock_err.call_count == 1

    # Second call within 5s should be suppressed
    ErrorRouter.report_error("Title", "Repeated Message")
    assert mock_err.call_count == 1

    # Different message should be delivered
    ErrorRouter.report_error("Title", "Different Message")
    assert mock_err.call_count == 2

    ErrorRouter.set_callbacks(None, None, None)


def test_error_router_default_fallback_printing(capsys):
    """Verify fallback to stdout/stderr print when no callbacks are set."""
    ErrorRouter._last_messages.clear()
    ErrorRouter.set_callbacks(None, None, None)

    ErrorRouter.report_error("FallbackErr", "Unique Err Text 999")
    captured = capsys.readouterr()
    assert "[ERROR] FallbackErr: Unique Err Text 999" in captured.out

    ErrorRouter.report_warning("FallbackWarn", "Unique Warn Text 999")
    captured = capsys.readouterr()
    assert "[WARNING] FallbackWarn: Unique Warn Text 999" in captured.out

    ErrorRouter.report_info("FallbackInfo", "Unique Info Text 999")
    captured = capsys.readouterr()
    assert "[INFO] FallbackInfo: Unique Info Text 999" in captured.out
