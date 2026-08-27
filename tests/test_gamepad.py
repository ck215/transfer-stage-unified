import pytest
import sys
from unittest.mock import MagicMock, patch
from controller.gamepad import (
    BaseGamepad,
    XboxGamepad,
    BluetoothXboxGamepad,
    LogitechF310Gamepad,
    T16000MGamepad,
    ControllerPoller,
    get_gamepad_wrapper,
)

def test_xbox_gamepad_mapping():
    mock_joystick = MagicMock()
    mock_joystick.get_numaxes.return_value = 6
    mock_joystick.get_numbuttons.return_value = 10
    mock_joystick.get_numhats.return_value = 1
    
    gamepad = XboxGamepad(mock_joystick)
    gamepad.prev_axis_states[0] = 0.8
    gamepad.prev_axis_states[3] = -0.5
    gamepad.prev_axis_states[4] = -0.5
    gamepad.prev_button_states[4] = 1  # L Bumper
    gamepad.prev_hat_states[0] = (1, -1)  # Hat: Right, Down
    
    state = gamepad.get_mapped_state()
    
    assert state["x_axisStatus"] == 0.8
    assert state["LBumper"] == 1
    # D-pad must not be inverted
    assert state["dpad_LR"] == 1
    assert state["dpad_UD"] == -1

def test_bluetooth_xbox_gamepad_linux():
    mock_joystick = MagicMock()
    mock_joystick.get_numaxes.return_value = 6
    mock_joystick.get_numbuttons.return_value = 10
    mock_joystick.get_numhats.return_value = 1
    
    gamepad = BluetoothXboxGamepad(mock_joystick)
    gamepad.prev_axis_states[0] = 0.5
    gamepad.prev_axis_states[3] = -0.2
    gamepad.prev_axis_states[5] = 0.8  # LT on BT linux
    gamepad.prev_axis_states[4] = -1.0 # RT on BT linux
    gamepad.prev_button_states[6] = 1  # LB on BT linux
    gamepad.prev_hat_states[0] = (-1, 1)
    
    state = gamepad.get_mapped_state()
    if sys.platform.startswith("linux"):
        assert state["x_axisStatus"] == 0.5
        assert state["y_axisStatus"] == -0.2
        assert state["z_axisStatusL"] == 0.8
        assert state["z_axisStatusR"] == -1.0
        assert state["LBumper"] == 1
        assert state["dpad_LR"] == -1
        assert state["dpad_UD"] == 1

def test_logitech_f310_dinput_mode():
    mock_joystick = MagicMock()
    mock_joystick.get_name.return_value = "Logitech Dual Action"
    mock_joystick.get_numaxes.return_value = 4
    mock_joystick.get_numbuttons.return_value = 10
    mock_joystick.get_numhats.return_value = 1
    
    gamepad = LogitechF310Gamepad(mock_joystick)
    gamepad.prev_axis_states[0] = 0.4
    gamepad.prev_axis_states[1] = -0.6
    gamepad.prev_button_states[6] = 1  # LT digital button in DInput mode
    gamepad.prev_button_states[7] = 0  # RT digital button in DInput mode
    gamepad.prev_button_states[4] = 1  # LB
    
    state = gamepad.get_mapped_state()
    assert state["x_axisStatus"] == 0.4
    assert state["z_axisStatusL"] == 1.0
    assert state["z_axisStatusR"] == -1.0
    assert state["LBumper"] == 1

def test_logitech_f310_xinput_mode():
    mock_joystick = MagicMock()
    mock_joystick.get_name.return_value = "Logitech Gamepad F310"
    mock_joystick.get_numaxes.return_value = 6
    mock_joystick.get_numbuttons.return_value = 10
    mock_joystick.get_numhats.return_value = 1
    
    gamepad = LogitechF310Gamepad(mock_joystick)
    gamepad.prev_axis_states[0] = 0.7
    state = gamepad.get_mapped_state()
    assert state["x_axisStatus"] == 0.7

def test_t16000m_overrides():
    mock_joystick = MagicMock()
    mock_joystick.get_numaxes.return_value = 11
    mock_joystick.get_numbuttons.return_value = 16
    mock_joystick.get_numhats.return_value = 1
    
    # Button 2 pressed (maps to axis 9 -> Z Down / z_axisStatusR)
    mock_joystick.get_button.side_effect = lambda idx: 1 if idx == 2 else 0
    
    gamepad = T16000MGamepad(mock_joystick)
    gamepad.update_overrides()
    
    assert gamepad.prev_axis_states[9] == 1  # Button 2 maps to axis 9
    assert gamepad.prev_axis_states[10] == 0
    
    state = gamepad.get_mapped_state()
    # Button 2 pressed -> z_axisStatusR is 1.0, z_axisStatusL is -1.0 (idle) across all OSes
    assert state["z_axisStatusR"] == 1.0
    assert state["z_axisStatusL"] == -1.0

def test_t16000m_z_up():
    mock_joystick = MagicMock()
    mock_joystick.get_numaxes.return_value = 11
    mock_joystick.get_numbuttons.return_value = 16
    mock_joystick.get_numhats.return_value = 1
    
    # Button 3 pressed (maps to axis 10 -> Z Up / z_axisStatusL)
    mock_joystick.get_button.side_effect = lambda idx: 1 if idx == 3 else 0
    
    gamepad = T16000MGamepad(mock_joystick)
    gamepad.update_overrides()
    
    assert gamepad.prev_axis_states[10] == 1
    assert gamepad.prev_axis_states[9] == 0
    
    state = gamepad.get_mapped_state()
    assert state["z_axisStatusL"] == 1.0
    assert state["z_axisStatusR"] == -1.0

def test_get_gamepad_wrapper_factory():
    js_xbox = MagicMock()
    js_xbox.get_name.return_value = "Xbox Series X Controller"
    js_xbox.get_guid.return_value = "030000005e04"
    assert isinstance(get_gamepad_wrapper(js_xbox), XboxGamepad)

    js_f310 = MagicMock()
    js_f310.get_name.return_value = "Logitech Gamepad F310"
    assert isinstance(get_gamepad_wrapper(js_f310), LogitechF310Gamepad)

    js_t16000 = MagicMock()
    js_t16000.get_name.return_value = "Thrustmaster T.16000M"
    assert isinstance(get_gamepad_wrapper(js_t16000), T16000MGamepad)

def test_controller_claim_conflict():
    with patch("controller.gamepad.pygame"):
        claims = {"ProcessA": "ID 0: Xbox Controller"}
        # ProcessB attempts to claim ID 0 which is already claimed by ProcessA
        poller = ControllerPoller("ID 0: Xbox Controller", claims, "ProcessB")
        assert poller.gamepad is None
        assert claims["ProcessB"] == "None Detected"

def test_controller_claim_conflict_with_integer_ids():
    """
    parse_controller_id() returns raw ints (e.g. 0, 1), and active_claims is
    populated with those ints directly. The claim-collision check must not
    assume claimed_id is a string (regression: crashed with
    TypeError: argument of type 'int' is not iterable on real hardware
    when two probes were assigned integer controller IDs).
    """
    with patch("controller.gamepad.pygame"):
        claims = {"ProcessA": 0}
        poller = ControllerPoller(0, claims, "ProcessB")
        assert poller.gamepad is None
        assert claims["ProcessB"] == "None Detected"

def test_controller_multi_digit_id_parsing():
    with patch("controller.gamepad.pygame") as mock_pygame:
        mock_pygame.joystick.get_count.return_value = 15
        mock_js = MagicMock()
        mock_js.get_name.return_value = "Xbox Series X Controller"
        mock_pygame.joystick.Joystick.return_value = mock_js
        
        claims = {}
        with patch.object(ControllerPoller, "_is_os_connected", return_value=True):
            poller = ControllerPoller("ID 12: Xbox Controller", claims, "ProcessA")
            assert poller.controller_index == 12
            mock_pygame.joystick.Joystick.assert_called_with(12)

def test_edge_triggered_dpad_and_bumpers():
    poller = ControllerPoller.__new__(ControllerPoller)
    poller.gamepad = MagicMock()
    
    # Simulate first press of LBumper and Dpad Up
    poller.gamepad.get_mapped_state.return_value = {
        "x_axisStatus": 0.5,
        "y_axisStatus": 0.0,
        "z_axisStatusL": -1.0,
        "z_axisStatusR": -1.0,
        "dpad_LR": 0,
        "dpad_UD": 1,
        "LBumper": 1,
        "RBumper": 0,
    }
    
    # First call: rising edge detected
    state1 = poller.get_mapped_state()
    assert state1["dpad_UD"] == 1
    assert state1["LBumper"] == 1
    assert state1["x_axisStatus"] == 0.5  # Continuous axis passes through
    
    # Second call while button is still held down: edge trigger suppresses discrete commands
    state2 = poller.get_mapped_state()
    assert state2["dpad_UD"] == 0
    assert state2["LBumper"] == 0
    assert state2["x_axisStatus"] == 0.5  # Continuous axis still passes through
    
    # Third call after release: 0 returned
    poller.gamepad.get_mapped_state.return_value["dpad_UD"] = 0
    poller.gamepad.get_mapped_state.return_value["LBumper"] = 0
    state3 = poller.get_mapped_state()
    assert state3["dpad_UD"] == 0
    assert state3["LBumper"] == 0
    
    # Fourth call when pressed again: rising edge fires again
    poller.gamepad.get_mapped_state.return_value["LBumper"] = 1
    state4 = poller.get_mapped_state()
    assert state4["LBumper"] == 1

def test_controller_claim_conflict_mixed_types():
    """Verify claim collision detection works when mixing int and string formats."""
    with patch("controller.gamepad.pygame"):
        # ProcessA claimed int 0, ProcessB tries 'ID 0: Xbox Controller'
        claims = {"ProcessA": 0}
        poller = ControllerPoller("ID 0: Xbox Controller", claims, "ProcessB")
        assert poller.gamepad is None
        assert claims["ProcessB"] == "None Detected"

        # ProcessA claimed 'Joy 1: Stick', ProcessB tries int 1
        claims2 = {"ProcessA": "Joy 1: Stick"}
        poller2 = ControllerPoller(1, claims2, "ProcessB")
        assert poller2.gamepad is None
        assert claims2["ProcessB"] == "None Detected"

def test_set_controller_resumes_polling_when_active():
    """Verify set_controller automatically resumes polling loop if GUI was previously polling."""
    mock_gui = MagicMock()
    with patch("controller.gamepad.pygame") as mock_pygame:
        mock_pygame.joystick.get_count.return_value = 2
        mock_js = MagicMock()
        mock_js.get_name.return_value = "Controller"
        mock_js.get_numaxes.return_value = 4
        mock_js.get_numbuttons.return_value = 4
        mock_js.get_numhats.return_value = 1
        mock_js.get_axis.return_value = 0.0
        mock_js.get_button.return_value = 0
        mock_js.get_hat.return_value = (0, 0)
        mock_pygame.joystick.Joystick.return_value = mock_js

        claims = {}
        with patch.object(ControllerPoller, "_is_os_connected", return_value=True):
            poller = ControllerPoller(0, claims, "ProcessA")
            poller.start_polling(mock_gui, log_updater=MagicMock())
            assert poller.is_polling is True

            # Swap controller
            success = poller.set_controller(1)
            assert success is True
            assert poller.controller_index == 1
            assert poller.is_polling is True

