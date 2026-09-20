"""S7 / RC-3: probe mode is a state machine, not four booleans.

Invariants under test:

* **I-3.1** `mode ∈ {MANUAL, AUTONOMOUS}` ⇒ an enable was confirmed and a
  watchdog generation is alive.
* **I-3.2** `mode == MANUAL` ⇒ a gamepad is bound. Otherwise refused.
* **I-3.3** An energized probe idle for `_INTERLOCK_TIMEOUT` is disabled, in
  *every* energized mode (D-3: manual included).
* **I-3.4** No schema or API write can change the mode.

The tests that matter most here are the ones about what is *not* possible any
more: you cannot assign a mode flag, and you cannot reach MANUAL without a
pad. Both were reachable before, and both energize coils.
"""
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from model.probes import BaseProbe, ProbeMode, StepperProbe


@pytest.fixture
def probe():
    """A probe with a mocked transport and no real gamepad."""
    with patch('model.probes.serial') as serial_class, \
         patch('model.probes.ErrorPopupManager'), \
         patch('controller.gamepad.ControllerPoller') as poller_class:
        transport = MagicMock()
        serial_class.return_value = transport
        poller = MagicMock()
        poller.gamepad = None
        poller.get_mapped_state.return_value = {}
        poller_class.return_value = poller
        p = StepperProbe("COM_TEST", "None")
        p.serial_comm = transport
        p.poller = poller
        yield p
        p._loops_stop.set()
        p._stop_interlock_watchdog()


def _bind_pad(probe):
    probe.poller.gamepad = MagicMock()
    probe.poller.controllerID = "Pad0"


# -- I-3.4: the flags are derived, not stored -------------------------------

@pytest.mark.parametrize("attr", ["system_enabled", "auton_flag",
                                  "manual_flag", "is_stepping", "mode"])
def test_i_3_4_mode_flags_cannot_be_assigned(probe, attr):
    """The Web set_attr route used to write manual_flag straight onto the model.

    That armed a mode with no gamepad check and no hardware enable. The names
    still render as schema toggles, but they are read-only views onto `mode`.
    """
    with pytest.raises(AttributeError):
        setattr(probe, attr, True)


def test_i_3_4_the_flags_still_read_as_booleans_for_the_schema(probe):
    """Read-only must not mean absent: three views render these."""
    _bind_pad(probe)
    probe.enter_manual()
    assert probe.manual_flag is True
    assert probe.auton_flag is False
    assert probe.system_enabled is True
    assert isinstance(probe.manual_flag, bool)


def test_mode_is_exactly_one_value(probe):
    _bind_pad(probe)
    probe.enter_auton()
    assert probe.mode is ProbeMode.AUTONOMOUS
    assert not (probe.auton_flag and probe.manual_flag)
    probe.enter_manual()
    assert probe.mode is ProbeMode.MANUAL
    assert not (probe.auton_flag and probe.manual_flag)
    probe.full_stop()
    assert probe.mode is ProbeMode.DISABLED
    assert not probe.auton_flag and not probe.manual_flag


# -- I-3.2: manual requires a bound pad, checked before the enable ----------

def test_i_3_2_manual_is_refused_without_a_gamepad(probe):
    assert probe.enter_manual() is False
    assert probe.mode is ProbeMode.DISABLED


def test_i_3_2_the_refusal_happens_before_the_hardware_enable(probe):
    """The ordering is the whole point: 'e' energizes coils.

    Checking after the enable can revert the Python flag, but the firmware has
    already been told to energize and nothing walks that back.
    """
    probe.enter_manual()
    probe.serial_comm.enable.assert_not_called()


def test_losing_the_controller_leaves_manual_mode_entirely(probe):
    """Not just the flag. Clearing manual_flag alone left the coils energized."""
    _bind_pad(probe)
    assert probe.enter_manual() is True
    probe.poller.gamepad = None
    probe.send_manual_mode_command({})
    assert probe.mode is ProbeMode.DISABLED
    assert probe.system_enabled is False
    probe.serial_comm.disable.assert_called()


def test_a_failed_controller_swap_does_not_claim_the_controller(probe):
    """controller_var used to be assigned before the bind was attempted."""
    _bind_pad(probe)
    probe.enter_manual()
    probe.poller.set_controller.side_effect = lambda _id: setattr(
        probe.poller, "gamepad", None)
    assert probe.set_controller("Pad9") is False
    assert probe.controller_var == "None"
    assert probe.mode is ProbeMode.DISABLED


# -- I-3.1: armed modes require a confirmed enable --------------------------

def test_i_3_1_a_failed_enable_does_not_reach_an_armed_mode(probe):
    probe.serial_comm.enable.side_effect = Exception("port gone")
    assert probe.enter_auton() is False
    assert probe.mode is ProbeMode.DISABLED
    assert probe.system_enabled is False


def test_i_3_1_an_armed_mode_has_a_live_watchdog_generation(probe):
    assert probe.enable() is True
    assert probe._interlock_thread is not None
    assert probe._interlock_thread.is_alive()
    assert probe._interlock_generation >= 1


def test_the_watchdog_gets_a_fresh_event_each_arming(probe):
    """STEPPER-7. One reused Event meant a disable silenced every later arming.

    The old code cleared a single `_interlock_stop` at start and set it at
    stop, so after one disable the next enable's thread returned on its first
    tick and the probe ran energized with no interlock at all.
    """
    probe.enable()
    first = probe._interlock_stop
    first_generation = probe._interlock_generation
    probe.disable()
    assert first.is_set()

    probe.enable()
    assert probe._interlock_stop is not first
    assert not probe._interlock_stop.is_set()
    assert probe._interlock_generation > first_generation
    assert probe._interlock_thread.is_alive()


# -- I-3.3: the interlock fires in every energized mode ---------------------

def test_i_3_3_the_interlock_no_longer_defers_on_manual_mode(probe):
    """D-3: manual mode idle-times-out on real input inactivity.

    The watchdog used to `continue` while `manual_flag`, so the timeout never
    fired in a mode that energizes coils.
    """
    probe._INTERLOCK_POLL_INTERVAL = 0.01
    probe._INTERLOCK_TIMEOUT = 0.05
    _bind_pad(probe)
    assert probe.enter_manual() is True
    deadline = time.time() + 3.0
    while probe.system_enabled and time.time() < deadline:
        time.sleep(0.01)
    assert probe.mode is ProbeMode.DISABLED, "manual mode never idled out"


def test_i_3_3_the_interlock_no_longer_defers_on_stepping(probe):
    """STEPPER-6 / DC-1: is_stepping was set once and never cleared.

    One autonomous move suppressed the interlock for the rest of the session.
    """
    probe._INTERLOCK_POLL_INTERVAL = 0.01
    probe._INTERLOCK_TIMEOUT = 0.05
    assert probe.macro_start_auton() is True
    deadline = time.time() + 3.0
    while probe.system_enabled and time.time() < deadline:
        time.sleep(0.01)
    assert probe.mode is ProbeMode.DISABLED, "a stepping probe never idled out"


# -- stepping is a timed sub-state ------------------------------------------

def test_stepping_expires_on_arrival_rather_than_lasting_forever(probe):
    probe._STEP_SETTLE = 0.05
    probe.macro_start_auton()
    # Silence the sample loop: against a MagicMock transport it reports a new
    # position on its first read, which legitimately extends the deadline.
    # Arrival is what this test is about, so hold the position still.
    probe.stop_loops()
    assert probe.is_stepping is True
    time.sleep(0.12)
    assert probe.is_stepping is False
    # Ending the move does not leave autonomous mode.
    assert probe.mode is ProbeMode.AUTONOMOUS


def test_motion_extends_stepping_and_counts_as_activity(probe):
    probe._STEP_SETTLE = 0.2
    probe.macro_start_auton()
    probe.last_activity_time = 0.0
    probe.pos_x = "42"
    probe._note_position()
    assert probe.is_stepping is True
    assert probe.last_activity_time > 0.0, "real motion must count as activity"


def test_a_plain_mode_entry_is_not_stepping(probe):
    _bind_pad(probe)
    probe.enter_auton()
    assert probe.is_stepping is False
    probe.enter_manual()
    assert probe.is_stepping is False


# -- D-2: leaving a mode de-energizes ---------------------------------------

def test_d_2_leaving_a_mode_disables_the_coils(probe):
    """Owner ruling 2026-09-20. Recorded as a test so a later 'fix' trips it."""
    _bind_pad(probe)
    probe.enter_manual()
    probe.serial_comm.reset_mock()
    probe.full_stop()
    probe.serial_comm.disable.assert_called_once()
    assert probe.system_enabled is False


def test_every_mode_entry_starts_from_rest(probe):
    _bind_pad(probe)
    probe.serial_comm.reset_mock()
    probe.enter_manual()
    probe.serial_comm.send_autonomous_command.assert_called()


# -- FAULT is a mode --------------------------------------------------------

def test_an_unconfirmed_disable_is_a_fault_not_a_disabled_claim(probe):
    probe.enable()
    probe.serial_comm.disable.side_effect = Exception("no answer")
    assert probe.disable() is False
    assert probe.mode is ProbeMode.FAULT
    assert probe.in_fault is True
    # Unknown reads as possibly-live, never as safe.
    assert probe.system_enabled is True


def test_a_fault_in_the_input_pump_leaves_manual_mode(probe):
    """A dead pump used to leave manual_flag True with nothing pumping it."""
    _bind_pad(probe)
    probe.enter_manual()
    probe._enter_fault("manual input pump failed: boom")
    assert probe.manual_flag is False
    assert probe.mode is ProbeMode.FAULT


def test_rearming_out_of_fault_resends_the_hardware_enable(probe):
    probe.enable()
    probe._enter_fault("unknown")
    probe.serial_comm.reset_mock()
    assert probe.enable() is True
    probe.serial_comm.enable.assert_called_once()


# -- the estop latch still wins ---------------------------------------------

def test_the_estop_latch_refuses_every_armed_transition(probe):
    probe._estop.set()
    assert probe.enable() is False
    assert probe.enter_auton() is False
    _bind_pad(probe)
    assert probe.enter_manual() is False
    assert probe.mode is ProbeMode.DISABLED
