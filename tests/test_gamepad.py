import pytest
import sys
from unittest.mock import MagicMock
from controller.gamepad import XboxGamepad, T16000MGamepad

def test_xbox_gamepad_mapping():
    mock_joystick = MagicMock()
    mock_joystick.get_numaxes.return_value = 6
    mock_joystick.get_numbuttons.return_value = 10
    mock_joystick.get_numhats.return_value = 1
    
    gamepad = XboxGamepad(mock_joystick)
    gamepad.prev_axis_states[0] = 0.8
    gamepad.prev_axis_states[3] = -0.5
    gamepad.prev_button_states[4] = 1 # L Bumper
    
    state = gamepad.get_mapped_state()
    
    assert state["x_axisStatus"] == 0.8
    if not sys.platform.startswith("linux"):
        assert state["y_axisStatus"] == -0.5
    assert state["LBumper"] == 1

def test_t16000m_overrides():
    mock_joystick = MagicMock()
    mock_joystick.get_numaxes.return_value = 11
    mock_joystick.get_numbuttons.return_value = 16
    mock_joystick.get_numhats.return_value = 1
    
    mock_joystick.get_button.side_effect = lambda idx: 1 if idx == 2 else 0
    
    gamepad = T16000MGamepad(mock_joystick)
    gamepad.update_overrides()
    
    assert gamepad.prev_axis_states[9] == 1 # Button 2 maps to axis 9
    assert gamepad.prev_axis_states[10] == 0
    
    state = gamepad.get_mapped_state()
    if sys.platform.startswith("linux"):
        assert state["z_axisStatusL"] == 1.0 # (1.0 * 2) - 1.0
    else:
        assert state["z_axisStatusR"] == 1.0
