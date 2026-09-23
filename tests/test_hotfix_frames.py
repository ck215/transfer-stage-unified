# Regression tests for the 2026-09-22 manual-mode hotfix.
#
# The firmware has NO host-liveness timeout: it keeps executing the last
# velocity packet it received until the next one arrives. Anything that
# silently kills the 5 ms manual loop therefore leaves the stage drifting.
# These tests pin the three paths that could do that.

import os
import sys

import pytest
from unittest.mock import MagicMock, patch

# Add src to sys.path (same pattern as test_edge_main_state_transitions.py)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../src')))

import chuck_frame  # noqa: E402
import controllerDrive  # noqa: E402
import DC_frame  # noqa: E402
import stepper_frame  # noqa: E402

FRAME_MODULES = [stepper_frame, DC_frame, chuck_frame]
FRAME_IDS = ["stepper", "dc", "chuck"]


def _gui_params(manual_flag, auton_flag):
    """Mirror of <frame>.get_gui_params()'s command-code contract."""
    return {
        "command_code_manual": int(manual_flag),
        "command_code_auton": int(auton_flag),
    }


def _build_logic(module):
    root = MagicMock()
    gui = MagicMock()
    controller = MagicMock()
    serial = MagicMock()
    active_claims = {}

    # Controller mock: a joystick is present so the manual loop gets past its guard
    controller.joystick = MagicMock()
    controller.get_hat_edge.return_value = (0, 0)
    controller.get_button_edge.return_value = 0
    controller.prev_axis_states = {}
    controller.controller_binds = [0, 1, 2, 3, 4, 5]

    # GUI mock: entries the three frames read while building manual params
    gui.entry_x_step.get.return_value = "1"
    gui.entry_y_step.get.return_value = "1"
    gui.entry_z_step.get.return_value = "1"
    gui.entry_man_full_speed.get.return_value = "100"
    gui.entry_rot_step.get.return_value = "1"        # chuck frame
    gui.entry_duration.get.return_value = "1"        # DC frame
    gui.entry_voltage.get.return_value = "1"         # DC frame
    gui.get_gui_params.side_effect = _gui_params

    logic = module.AppLogic(root, gui, controller, serial, active_claims, "Test")
    logic.system_enabled = True
    return logic, root, gui, controller, serial


def _stop_commands(serial):
    """Every autonomous packet that is a full stop (both mode codes zero)."""
    return [
        call.args[0]
        for call in serial.send_autonomous_command.call_args_list
        if isinstance(call.args[0], dict)
        and call.args[0].get("command_code_manual") == 0
        and call.args[0].get("command_code_auton") == 0
    ]


# ---------------------------------------------------------------------------
# Defect 1 -- an exception inside the manual loop must stop the hardware
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("module", FRAME_MODULES, ids=FRAME_IDS)
def test_manual_loop_error_sends_full_stop(module):
    logic, root, gui, controller, serial = _build_logic(module)

    logic.manualFlag = True
    serial.reset_mock()
    root.after.reset_mock()

    with patch.object(logic, 'get_controller_params',
                      side_effect=RuntimeError("controller read blew up")):
        logic._manual_mode_loop()

    assert _stop_commands(serial), (
        "manual loop died on an exception without commanding a FULL STOP; "
        "firmware keeps the last velocity -> stage drifts"
    )
    assert logic.manualFlag is False, "manualFlag left set after a fatal loop error"

    # The dead loop must not have rescheduled itself either.
    rescheduled = [c for c in root.after.call_args_list
                   if len(c.args) >= 2 and c.args[1] == logic._manual_mode_loop]
    assert not rescheduled, "loop rescheduled itself after a fatal error"


@pytest.mark.parametrize("module", FRAME_MODULES, ids=FRAME_IDS)
def test_manual_loop_normal_path_still_reschedules(module):
    logic, root, gui, controller, serial = _build_logic(module)

    logic.manualFlag = True
    serial.reset_mock()
    root.after.reset_mock()

    logic._manual_mode_loop()

    assert serial.send_manual_mode_command.call_count == 1
    rescheduled = [c for c in root.after.call_args_list
                   if len(c.args) >= 2 and c.args[1] == logic._manual_mode_loop]
    assert rescheduled, "normal manual loop pass did not reschedule itself"
    assert rescheduled[0].args[0] == 5, "manual loop cadence is no longer 5 ms"
    assert logic.manualFlag is True


# ---------------------------------------------------------------------------
# Defect 2 -- a controller swap during manual mode must FULL STOP first
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("module", FRAME_MODULES, ids=FRAME_IDS)
def test_controller_swap_in_manual_mode_stops_first(module):
    logic, root, gui, controller, serial = _build_logic(module)

    logic.manualFlag = True
    serial.reset_mock()
    controller.reset_mock()

    order = []
    gui.controller_var.get.return_value = "ID 1: Logitech Gamepad F310"
    controller.change_controller.side_effect = lambda sel: order.append("swap") or True
    serial.send_autonomous_command.side_effect = lambda p: order.append("stop")

    logic.on_controller_dropdown_selected(None)

    assert logic.manualFlag is False, (
        "manual mode survived a controller swap; the loop keeps sending stale "
        "axis values because change_controller() left polling stopped"
    )
    assert "stop" in order, "no FULL STOP command sent on controller swap"
    assert order.index("stop") < order.index("swap"), (
        "FULL STOP must be sent BEFORE the controller is swapped out"
    )


# ---------------------------------------------------------------------------
# Defect 3 -- the Thrustmaster deadzone override must reach the poll loop
# ---------------------------------------------------------------------------

class _FakeJoystick:
    def __init__(self, name):
        self._name = name

    def init(self):
        pass

    def get_name(self):
        return self._name

    def get_guid(self):
        return "03000000000000000000000000000000"

    def get_numaxes(self):
        return 6

    def get_numbuttons(self):
        return 12

    def get_numhats(self):
        return 1


def _make_poller(controller_name):
    active_claims = {}
    with patch('controllerDrive.pygame.init'), \
         patch('controllerDrive.pygame.quit'), \
         patch('controllerDrive.pygame.joystick.init'), \
         patch('controllerDrive.pygame.joystick.Joystick',
               return_value=_FakeJoystick(controller_name)), \
         patch('controllerDrive.sys.platform', 'linux'), \
         patch.object(controllerDrive.ControllerPoller, '_is_os_connected',
                      return_value=True):
        poller = controllerDrive.ControllerPoller(
            f"ID 0: {controller_name}", active_claims, "Test")
    assert poller.joystick is not None, "fixture failed to initialize the joystick"
    return poller


@pytest.mark.parametrize("name", ["T.16000M", "Thrustmaster T.16000M"])
def test_thrustmaster_deadzone_is_applied(name):
    poller = _make_poller(name)
    assert poller.deadzone == 0.03, (
        "Thrustmaster deadzone override never reaches the poll loop "
        "(commit b181506 assigned a local, not the instance/module value)"
    )


def test_standard_controller_keeps_default_deadzone():
    poller = _make_poller("Logitech Gamepad F310")
    assert poller.deadzone == controllerDrive.DEADZONE == 0.1


def test_deadzone_resets_when_swapping_away_from_thrustmaster():
    poller = _make_poller("Thrustmaster T.16000M")
    assert poller.deadzone == 0.03

    with patch('controllerDrive.pygame.init'), \
         patch('controllerDrive.pygame.quit'), \
         patch('controllerDrive.pygame.joystick.init'), \
         patch('controllerDrive.pygame.joystick.Joystick',
               return_value=_FakeJoystick("Logitech Gamepad F310")), \
         patch('controllerDrive.sys.platform', 'linux'), \
         patch.object(controllerDrive.ControllerPoller, '_is_os_connected',
                      return_value=True):
        poller._initialize_pygame_joystick("ID 0: Logitech Gamepad F310")

    assert poller.deadzone == 0.1, "deadzone stayed at the Thrustmaster value after a swap"
