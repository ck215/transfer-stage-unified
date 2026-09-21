import contextlib
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

@contextlib.contextmanager
def patched_sdl(count=2, name="Controller"):
    """Patch SDL where it now lives: controller/input_service.py (RC-13).

    Tests used to patch `controller.gamepad.pygame`, because every poller
    talked to SDL directly. SDL has one owner now, so that is the module to
    patch, and `input_service` is reset between tests so one test's acquired
    handles cannot leak into the next.
    """
    from controller import input_service as svc

    svc.input_service._handles.clear()
    svc.input_service._initialised = False
    with patch("controller.input_service.pygame") as mock_pygame:
        mock_pygame.joystick.get_count.return_value = count
        mock_pygame.joystick.get_init.return_value = True
        js = MagicMock()
        js.get_name.return_value = name
        mock_pygame.joystick.Joystick.return_value = js
        try:
            yield mock_pygame
        finally:
            svc.input_service._handles.clear()
            svc.input_service._initialised = False



def _bare_poller():
    """A poller assembled without touching hardware.

    __init__ acquires a real device, so these tests build the object directly.
    _init_input_state() sets up the latched-input fields (RC-13 item 2) that
    __init__ would otherwise install.
    """
    poller = ControllerPoller.__new__(ControllerPoller)
    poller._init_input_state()
    poller._thread = None
    poller._closed = False
    return poller


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

def test_get_gamepad_wrapper_rejects_unrecognized_device():
    js_unknown = MagicMock()
    js_unknown.get_name.return_value = "Generic HID Device"
    with pytest.raises(ValueError, match="Unsupported joystick detected"):
        get_gamepad_wrapper(js_unknown)

def test_controller_claim_conflict():
    with patched_sdl():
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
    with patched_sdl():
        claims = {"ProcessA": 0}
        poller = ControllerPoller(0, claims, "ProcessB")
        assert poller.gamepad is None
        assert claims["ProcessB"] == "None Detected"

def test_controller_multi_digit_id_parsing():
    with patched_sdl() as mock_pygame:
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
    poller = _bare_poller()
    poller.gamepad = MagicMock()
    poller.is_polling = True
    
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
    with patched_sdl():
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
    with patched_sdl() as mock_pygame:
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

def test_stale_cache_guard():
    """Verify get_mapped_state() returns {} when polling is stopped."""
    poller = _bare_poller()
    poller.gamepad = MagicMock()
    poller.is_polling = False
    
    state = poller.get_mapped_state()
    assert state == {}


def test_controller_claim_success_then_conflict_with_string_ids():
    """Verify claim collision behaves correctly when using string IDs from combobox."""
    with patched_sdl() as mock_pygame:
        mock_pygame.joystick.get_count.return_value = 1
        mock_js = MagicMock()
        mock_js.get_name.return_value = "Xbox"
        mock_js.get_numaxes.return_value = 4
        mock_js.get_numbuttons.return_value = 4
        mock_js.get_numhats.return_value = 1
        mock_pygame.joystick.Joystick.return_value = mock_js
        
        claims = {}
        with patch.object(ControllerPoller, "_is_os_connected", return_value=True):
            with patch("controller.gamepad.ErrorPopupManager.report_warning") as mock_warn:
                poller1 = ControllerPoller("ID 0: Xbox", claims, "StepperProbe")
                assert poller1.gamepad is not None
                assert poller1.controller_index == 0
                mock_warn.assert_not_called()
                
                poller2 = ControllerPoller("ID 0: Xbox", claims, "DCProbe")
                assert poller2.gamepad is None

def test_flush_neutral():
    poller = _bare_poller()
    poller.gamepad = MagicMock()
    poller.gamepad.prev_axis_states = {0: 0.5, 1: -0.5, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.0}
    
    poller.flush_neutral()
    
    assert poller.gamepad.prev_axis_states == {0: 0.0, 1: 0.0, 2: -1.0, 3: 0.0, 4: -1.0, 5: -1.0}
    if hasattr(poller, '_latch_state'):
        assert len(poller._latch_state) == 0

def test_controller_deadzone():
    poller = _bare_poller()
    poller.is_polling = True
    poller.gamepad = MagicMock()
    
    # Simulate a raw mapped state just under and just over deadzones
    poller.gamepad.get_mapped_state.return_value = {
        "x_axisStatus": 0.11,
        "y_axisStatus": -0.11,
        "z_axisStatusL": -0.95,
        "z_axisStatusR": -0.85,
        "dpad_LR": 0,
        "dpad_UD": 0,
        "LBumper": 0,
        "RBumper": 0,
    }
    
    state = poller.get_mapped_state()
    
    assert state["x_axisStatus"] == 0.0
    assert state["y_axisStatus"] == 0.0
    assert state["z_axisStatusL"] == -1.0
    assert state["z_axisStatusR"] == -0.85

    # Test over deadzone
    poller.gamepad.get_mapped_state.return_value = {
        "x_axisStatus": 0.13,
        "y_axisStatus": -0.13,
        "z_axisStatusL": -1.0,
        "z_axisStatusR": 0.5,
        "dpad_LR": 0,
        "dpad_UD": 0,
        "LBumper": 0,
        "RBumper": 0,
    }
    
    state = poller.get_mapped_state()
    assert state["x_axisStatus"] == 0.13
    assert state["y_axisStatus"] == -0.13
    assert state["z_axisStatusL"] == -1.0
    assert state["z_axisStatusR"] == 0.5

def test_gamepad_disconnect_mid_session():
    """Verify poller handles pygame.error gracefully when reading disconnected hardware."""
    from unittest.mock import patch
    import pygame
    
    with patched_sdl() as mock_pygame:
        mock_pygame.error = type("error", (Exception,), {})
        poller = _bare_poller()
        poller.is_polling = True
        poller.gamepad = MagicMock()
        
        # Simulate pygame throwing an error on hardware read
        poller.gamepad.get_mapped_state.side_effect = mock_pygame.error("Joystick disconnected")
        
        # Should catch pygame.error and return empty dict instead of crashing thread
        with patch("controller.gamepad.ErrorPopupManager") as mock_error:
            state = poller.get_mapped_state()
            assert state == {}

            # Since the device is lost, the polling flag or object might be cleared/warned
            # (Depends on implementation, but testing it doesn't crash is primary requirement)


def test_read_raw_pygame_error_guard_does_not_attributeerror_when_pygame_is_none():
    """GAMEPAD-17: `except pygame.error` evaluates the attribute `pygame.error`
    only when something in the try block actually raises. If pygame failed
    to import (`pygame = None` at module scope), that attribute access
    itself raised AttributeError, replacing whatever exception the wrapper
    had raised with a confusing, unrelated one. `_read_raw` is unreachable
    in practice — every wrapper's get_mapped_state() only does dict lookups
    against its own prev_*_states caches, never touches pygame — but the
    guard against a missing pygame module needs to hold regardless.
    """
    poller = _bare_poller()
    poller.gamepad = MagicMock()
    poller.gamepad.get_mapped_state.side_effect = RuntimeError("wrapper boom")

    with patch("controller.gamepad.pygame", None):
        with pytest.raises(RuntimeError, match="wrapper boom"):
            poller._read_raw()

def test_closing_a_poller_never_tears_sdl_down():
    """Closing a device releases that device, and nothing else (RC-13).

    Re-authored in S5. This used to assert the *refcount*: close() decremented
    a module-level poller count and called pygame.quit() when it reached zero.
    That was the bug, not the contract — the count was a proxy for ownership
    that could not tell "nobody is using SDL" from "nobody happens to hold a
    poller object right now", so closing one device tore SDL down under
    another that was still running, and the next reconnect had to resurrect it
    via _ensure_pygame_video(). Process-wide teardown now happens once, in
    lifecycle.shutdown(), at process exit.
    """
    from controller.input_service import input_service

    with patched_sdl() as mock_pygame:
        claims = {}
        with patch.object(ControllerPoller, "_is_os_connected", return_value=True):
            poller1 = ControllerPoller(0, claims, "ProcessA")
            poller2 = ControllerPoller(1, claims, "ProcessB")

            assert poller1.gamepad is not None
            assert poller2.gamepad is not None

            poller1.close()
            mock_pygame.quit.assert_not_called()
            mock_pygame.joystick.quit.assert_not_called()
            assert input_service.index_for("ProcessA") is None, "A's handle should be released"
            assert input_service.index_for("ProcessB") == 1, "B still holds its device"

            # Even the last poller closing must not take SDL down.
            poller2.close()
            mock_pygame.quit.assert_not_called()
            mock_pygame.joystick.quit.assert_not_called()

            # Closing twice is a no-op, not a second release.
            poller1.close()
            mock_pygame.quit.assert_not_called()


def test_two_pollers_cannot_claim_the_same_controller():
    """The claim registry is derived from real acquisitions, so it cannot
    disagree with which handles actually exist."""
    from controller.input_service import input_service

    with patched_sdl():
        claims = {}
        with patch.object(ControllerPoller, "_is_os_connected", return_value=True):
            first = ControllerPoller(0, claims, "ProcessA")
            assert first.gamepad is not None

            second = ControllerPoller(0, claims, "ProcessB")
            assert second.gamepad is None, "ProcessB took a controller ProcessA holds"
            assert input_service.index_for("ProcessA") == 0


def test_sdl_comes_down_only_at_process_exit():
    import lifecycle
    from controller.input_service import input_service

    with patched_sdl() as mock_pygame:
        input_service.ensure_init()
        lifecycle._reset_for_tests()
        lifecycle.shutdown("test")
        mock_pygame.quit.assert_called_once()
    lifecycle._reset_for_tests()


# ==========================================
# RC-13 item 2 — edges latch at poll time
# ==========================================

def test_a_tap_shorter_than_a_read_interval_is_not_lost():
    """Edges used to be detected inside get_mapped_state(), i.e. by the
    reader. A press that started and ended between two reads was therefore
    never seen: both reads observed 0 and no edge existed. The poll loop now
    latches it when it happens, and it waits until someone drains it."""
    poller = _bare_poller()
    poller.is_polling = True
    poller.gamepad = MagicMock()

    neutral = {"x_axisStatus": 0.0, "y_axisStatus": 0.0, "dpad_LR": 0,
               "dpad_UD": 0, "LBumper": 0, "RBumper": 0}

    poller.gamepad.get_mapped_state.return_value = dict(neutral)
    poller._capture_state()

    # The tap: down and back up, entirely between reads.
    poller.gamepad.get_mapped_state.return_value = dict(neutral, LBumper=1)
    poller._capture_state()
    poller.gamepad.get_mapped_state.return_value = dict(neutral)
    poller._capture_state()

    assert poller.drain_edges().get("LBumper") == 1, "the tap was dropped"


def test_reading_levels_does_not_consume_edges():
    """Whichever caller read first used to swallow the edge for everyone
    else, because the read updated the latch."""
    poller = _bare_poller()
    poller.is_polling = True
    poller.gamepad = MagicMock()
    poller.gamepad.get_mapped_state.return_value = {
        "x_axisStatus": 0.8, "y_axisStatus": 0.0, "dpad_LR": 1,
        "dpad_UD": 0, "LBumper": 0, "RBumper": 0}
    poller._capture_state()

    assert poller.read_levels()["x_axisStatus"] == 0.8
    assert poller.read_levels()["x_axisStatus"] == 0.8  # still there
    assert poller.drain_edges().get("dpad_LR") == 1, "a level read ate the edge"


def test_edges_drain_exactly_once():
    """Single consumer: two actors must not both act on one press."""
    poller = _bare_poller()
    poller.is_polling = True
    poller.gamepad = MagicMock()
    poller.gamepad.get_mapped_state.return_value = {
        "x_axisStatus": 0.0, "y_axisStatus": 0.0, "dpad_LR": 0,
        "dpad_UD": 0, "LBumper": 0, "RBumper": 1}
    poller._capture_state()

    assert poller.drain_edges().get("RBumper") == 1
    assert poller.drain_edges() == {}


def test_holding_a_button_produces_one_edge_not_a_stream():
    poller = _bare_poller()
    poller.is_polling = True
    poller.gamepad = MagicMock()
    held = {"x_axisStatus": 0.0, "y_axisStatus": 0.0, "dpad_LR": 0,
            "dpad_UD": 0, "LBumper": 1, "RBumper": 0}
    poller.gamepad.get_mapped_state.return_value = held

    for _ in range(5):
        poller._capture_state()

    assert poller.drain_edges().get("LBumper") == 1
    for _ in range(5):
        poller._capture_state()
    assert poller.drain_edges() == {}, "a held button kept re-firing"


def test_levels_never_carry_edge_keys():
    poller = _bare_poller()
    poller.is_polling = True
    poller.gamepad = MagicMock()
    poller.gamepad.get_mapped_state.return_value = {
        "x_axisStatus": 0.0, "y_axisStatus": 0.0, "dpad_LR": 1,
        "dpad_UD": 0, "LBumper": 0, "RBumper": 0}
    poller._capture_state()
    assert poller.read_levels()["dpad_LR"] == 0


# ==========================================
# RC-13 item 2 — the poller owns its clock
# ==========================================

def test_polling_continues_without_a_tk_event_loop():
    """This is why the Web frontend has no manual mode.

    _poll_loop rescheduled itself with `self.gui_root.after(...)` and called
    stop_polling() when there was no such root — so with no Tk widget the
    loop ran exactly once and stopped. Entering manual mode on the web
    dashboard energized the coils and then did nothing else.
    """
    import time as _time

    with patched_sdl():
        claims = {}
        with patch.object(ControllerPoller, "_is_os_connected", return_value=True):
            poller = ControllerPoller(0, claims, "Headless")
        poller.gamepad = MagicMock()
        poller.gamepad.get_mapped_state.return_value = {
            "x_axisStatus": 0.0, "y_axisStatus": 0.0, "dpad_LR": 0,
            "dpad_UD": 0, "LBumper": 0, "RBumper": 0}
        poller.gamepad.joystick.get_numaxes.return_value = 0
        poller.gamepad.joystick.get_numbuttons.return_value = 0
        poller.gamepad.joystick.get_numhats.return_value = 0

        try:
            poller.start_polling(gui=None)          # no Tk root at all
            _time.sleep(0.15)
            assert poller.is_polling, "polling stopped with no Tk event loop"
            assert poller._thread is not None and poller._thread.is_alive()
        finally:
            poller.close()


def test_poll_loop_rearm_failure_is_treated_as_a_disconnect():
    """GAMEPAD-17: the Tk re-arm call used to sit outside _poll_loop's
    try/except.

    A destroyed `gui_root` (dashboard/tab torn down mid-poll) makes Tk's
    `after()` raise TclError. That used to propagate straight out of the
    scheduled callback instead of being handled like any other lost-device
    signal. This stands a plain exception in for TclError (this test file
    never imports real tkinter) — the re-arm call itself doesn't care what
    exception `after()` raises, only that one was raised.
    """
    claims = {"TestProcess": 0}
    poller = _bare_poller()
    poller.process_name = "TestProcess"
    poller.active_claims = claims
    poller.gamepad = MagicMock()
    poller.gamepad.joystick.get_numaxes.return_value = 0
    poller.gamepad.joystick.get_numbuttons.return_value = 0
    poller.gamepad.joystick.get_numhats.return_value = 0
    poller.is_polling = True
    poller.log_updater = None
    poller.activity_callback = None

    class DestroyedRoot:
        def after(self, *args, **kwargs):
            raise RuntimeError("invalid command name (destroyed widget)")

    poller.gui_root = DestroyedRoot()

    with patch.object(ControllerPoller, "_is_os_connected", return_value=True):
        poller._poll_loop()  # must not raise

    assert poller.gamepad is None
    assert poller.is_polling is False
    assert claims["TestProcess"] == "None Detected"


# ==========================================
# GAMEPAD-7 — one poll chain per poller, always
# ==========================================

class _FakeTkRoot:
    """A Tk root that only *queues* `after` callbacks; the test runs them.

    The real defect is invisible with a live event loop, because every chain
    does the same work and shares the same `prev_*` state. Holding the queue
    makes the number of live chains directly observable.
    """

    def __init__(self):
        self.queue = []

    def after(self, _ms, callback):
        self.queue.append(callback)
        return len(self.queue)

    def drain(self):
        """Fire everything currently scheduled, once."""
        due, self.queue = self.queue, []
        for callback in due:
            callback()
        return len(due)


def _sdl_handles_per_index(mock_pygame, count=2):
    """Give each SDL index its own joystick mock, memoised.

    `patched_sdl` hands the same object back for every index, which is fine
    when a test only ever binds one controller but makes a swap untestable:
    the "old" and "new" devices would be the same mock.
    """
    handles = {}

    def make(index):
        handle = handles.get(index)
        if handle is None:
            handle = MagicMock()
            handle.get_name.return_value = f"Xbox Controller {index}"
            handle.get_guid.return_value = "030000005e04"
            handle.get_numaxes.return_value = 0
            handle.get_numbuttons.return_value = 0
            handle.get_numhats.return_value = 0
            handles[index] = handle
        return handle

    mock_pygame.joystick.get_count.return_value = count
    mock_pygame.joystick.Joystick.side_effect = make
    return handles


def test_a_stale_poll_chain_stops_when_polling_is_restarted():
    """The stop half of GAMEPAD-7, and the reason it comes first.

    `stop_polling()` has to actually stop the chain it was asked to stop.
    It did not: the only thing an already-scheduled callback checked was the
    `is_polling` flag, so any restart that happened before the callback fired
    (which is exactly what `set_controller` does — `stop_polling()` inside
    `_initialize_pygame_joystick`, then `start_polling()` again) revived the
    old chain instead of ending it.
    """
    root = _FakeTkRoot()
    poller = _bare_poller()
    poller.is_polling = False
    poller.gui_root = root
    poller.log_updater = None
    poller.activity_callback = None
    poller.process_name = "ProcessA"
    poller.active_claims = {}
    poller.controller_index = 0
    poller.gamepad = MagicMock()
    poller.gamepad.joystick.get_numaxes.return_value = 0
    poller.gamepad.joystick.get_numbuttons.return_value = 0
    poller.gamepad.joystick.get_numhats.return_value = 0
    poller.gamepad.get_mapped_state.return_value = {
        "x_axisStatus": 0.0, "y_axisStatus": 0.0, "dpad_LR": 0,
        "dpad_UD": 0, "LBumper": 0, "RBumper": 0}

    with patch.object(ControllerPoller, "_is_os_connected", return_value=True):
        poller.start_polling(root)
        assert len(root.queue) == 1

        # Stop, then restart before the scheduled callback has run.
        poller.stop_polling()
        poller.start_polling(root)

        # Two callbacks are pending now — the stale one cannot be
        # unscheduled — but only the live chain may re-arm itself.
        root.drain()
        assert len(root.queue) == 1, (
            f"{len(root.queue)} poll chains are live; stop_polling() did not "
            "end the chain it stopped")


def test_a_controller_swap_does_not_start_a_second_poll_chain():
    """GAMEPAD-7: every successful swap used to leave the previous chain
    running, so N swaps gave N+1 concurrent `_poll_loop` chains sharing one
    set of `prev_*` caches — N+1x the pump work, and whichever chain saw a
    change first fired the log/activity callbacks.
    """
    root = _FakeTkRoot()
    with patched_sdl() as mock_pygame:
        _sdl_handles_per_index(mock_pygame, count=2)
        with patch.object(ControllerPoller, "_is_os_connected", return_value=True):
            poller = ControllerPoller(0, {}, "ProcessA")
            try:
                poller.start_polling(root)
                assert len(root.queue) == 1

                for target in (1, 0, 1):
                    assert poller.set_controller(target) is True
                    root.drain()
                    assert len(root.queue) == 1, (
                        f"after swapping to {target}, {len(root.queue)} poll "
                        "chains are live instead of 1")
            finally:
                poller.close()


def test_a_restart_does_not_leave_a_second_poll_thread_running():
    """The same race on the threaded (non-Tk) path, where it is not merely
    wasted work: two OS threads then drive `_poll_loop` against one
    unsynchronised set of `prev_*` caches.

    Driven through `stop_polling()`/`start_polling()` rather than through
    `set_controller`, because `set_controller` only resumes polling when
    `gui_root` is set and so never restarts the threaded clock at all (a
    separate defect, reported in the handoff, not fixed here).

    POLL_INTERVAL is stretched so the first thread is certainly asleep across
    the stop/start window — the flag it used to depend on is True again by
    the time it wakes.
    """
    with patched_sdl() as mock_pygame:
        _sdl_handles_per_index(mock_pygame, count=2)
        with patch.object(ControllerPoller, "_is_os_connected", return_value=True), \
                patch.object(ControllerPoller, "POLL_INTERVAL", 300):
            poller = ControllerPoller(0, {}, "Headless")
            try:
                poller.start_polling(gui=None)
                first = poller._thread
                assert first is not None and first.is_alive()

                poller.stop_polling()
                poller.start_polling(gui=None)
                second = poller._thread
                assert second is not first, "no new poll thread after the restart"

                first.join(timeout=3.0)
                assert not first.is_alive(), (
                    "the stopped poll thread is still polling alongside its "
                    "replacement")
                assert second.is_alive(), "the live poll thread died"
            finally:
                poller.close()
                if poller._thread is not None:
                    poller._thread.join(timeout=3.0)


# ==========================================
# GAMEPAD-19 — macOS presence check asks about the right device
# ==========================================

@contextlib.contextmanager
def _darwin():
    """Run the body as if on macOS, whatever the host actually is."""
    with patch("controller.gamepad.sys.platform", "darwin"):
        yield


def _poller_for_presence_check(index, owner="ProcessA"):
    poller = _bare_poller()
    poller.is_polling = False
    poller.process_name = owner
    poller.active_claims = {}
    poller.controller_index = index
    return poller


def test_macos_presence_check_does_not_consult_the_previous_controller():
    """GAMEPAD-19: the darwin branch asked `self.gamepad.joystick.get_name()`.

    During a swap `controller_index` already names the device being bound
    while `self.gamepad` is still the *previous* device's wrapper, so the
    presence check answered about the wrong controller. If the old pad was
    the one that was unplugged, its handle raises, and a perfectly present
    new controller was rejected as "not physically present at OS level".
    """
    from controller.input_service import InputService, input_service

    poller = _poller_for_presence_check(index=1)
    dead_previous = MagicMock()
    dead_previous.joystick.get_name.side_effect = Exception("device removed")
    poller.gamepad = dead_previous

    with _darwin(), \
            patch.object(InputService, "initialised", True), \
            patch.object(input_service, "is_index_connected", return_value=True), \
            patch.object(input_service, "index_for", return_value=0):
        assert poller._is_os_connected() is True, (
            "the presence check for controller 1 was answered by controller 0")


def test_macos_presence_check_still_reports_a_dead_handle_for_the_bound_device():
    """Regression guard — passes before and after the fix.

    When the handle we hold *is* the one for `controller_index`, a raising
    `get_name()` is still the macOS disconnect signal. The fix must narrow
    which object gets asked, not stop asking.
    """
    from controller.input_service import InputService, input_service

    poller = _poller_for_presence_check(index=0)
    dead = MagicMock()
    dead.joystick.get_name.side_effect = Exception("device removed")
    poller.gamepad = dead

    with _darwin(), \
            patch.object(InputService, "initialised", True), \
            patch.object(input_service, "is_index_connected", return_value=True), \
            patch.object(input_service, "index_for", return_value=0):
        assert poller._is_os_connected() is False


def test_macos_swap_succeeds_when_the_previous_controller_is_gone():
    """The user-visible half of GAMEPAD-19.

    Controller 0 is bound and then unplugged; the operator picks controller
    1 from the dropdown. The bind has to go through — nothing about the
    departed controller 0 says anything about whether controller 1 is there.
    """
    with _darwin(), patched_sdl() as mock_pygame:
        handles = _sdl_handles_per_index(mock_pygame, count=2)
        poller = ControllerPoller(0, {}, "ProcessA")
        try:
            assert poller.gamepad is not None
            assert poller.controller_index == 0

            # Controller 0 is yanked: its handle now raises.
            handles[0].get_name.side_effect = Exception("device removed")

            assert poller.set_controller(1) is True, (
                "swapping to a present controller failed because the "
                "*previous* controller had been unplugged")
            assert poller.controller_index == 1
            assert poller.gamepad is not None
            assert poller.gamepad.joystick is handles[1]
        finally:
            poller.close()
