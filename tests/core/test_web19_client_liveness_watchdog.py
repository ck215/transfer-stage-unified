"""WEB-19 / D-8 (model half): the watchdog learns a client-liveness deadline
and drives the same stop path it already drives.

D-8 (owner, 2026-09-20): "warn at N s, FULL STOP at M s while motion is
active... Suggested starting values N=5, M=15, to be tuned at the bench."
Folded into the existing interlock watchdog rather than a second timer --
same thread, same generation, a second threshold. The two timers measure
different things (operator inactivity vs. web client absence) and the
liveness tier needs its own timestamp, so `touch_activity()` alone is not
enough -- see `touch_client_liveness()`.

**The seam** (for agent B's web half, joined by the lead at merge):
`BaseProbe.touch_client_liveness()` takes no arguments; the Web adapter
calls it once per live poll of a device (e.g. inside `/api/state`, on the
device the request names) and "now" is read inside the model, so a slow
request cannot backdate the deadline. Before the first call,
`last_client_seen_time` is `None` and the watchdog does not gate on it at
all -- a Tk/PySide session, which never calls this, is a desktop session as
far as D-8 is concerned, not an absent web client. Once a web client has
checked in at least once, the gate applies for the rest of this model's
lifetime, and only while a mode that can move an axis is engaged
(AUTONOMOUS/MANUAL) -- an idle-but-armed probe is explicitly left alone.
"""
import time
from unittest.mock import MagicMock, patch

import pytest

from model.probes import ProbeMode, StepperProbe


@pytest.fixture
def probe():
    with patch('model.probes.serial') as serial_class, \
         patch('model.probes.ErrorPopupManager') as error_router, \
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
        p._INTERLOCK_POLL_INTERVAL = 0.02
        p._INTERLOCK_TIMEOUT = 999  # keep the operator-idle tier out of the way
        p.WEB_CLIENT_WARN_TIMEOUT = 0.05
        p.WEB_CLIENT_STOP_TIMEOUT = 0.15
        p._error_router = error_router
        yield p
        p._loops_stop.set()
        p._stop_interlock_watchdog()
        try:
            p.teardown()
        except Exception:
            pass


def _wait_until(fn, timeout=3.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if fn():
            return True
        time.sleep(0.01)
    return fn()


def test_the_seam_takes_no_arguments_and_stamps_now(probe):
    before = time.time()
    assert probe.last_client_seen_time is None
    probe.touch_client_liveness()
    assert probe.last_client_seen_time >= before


def test_no_client_ever_checked_in_is_never_gated(probe):
    """The desktop (Tk/PySide) default: `touch_client_liveness` is never
    called, so a run can sit autonomous indefinitely with no web client
    watching, and D-8 must not fire."""
    assert probe.macro_start_auton() is True
    time.sleep(probe.WEB_CLIENT_STOP_TIMEOUT * 4)
    assert probe.mode is ProbeMode.AUTONOMOUS
    assert probe.system_enabled is True


def test_an_idle_armed_probe_is_left_alone(probe):
    """energized but not moving: D-8 does not apply even after a check-in."""
    assert probe.enable() is True
    probe.touch_client_liveness()
    time.sleep(probe.WEB_CLIENT_STOP_TIMEOUT * 4)
    assert probe.mode is ProbeMode.ENABLED_IDLE
    assert probe.system_enabled is True


def test_a_silent_client_gets_a_warning_before_the_stop_threshold(probe):
    assert probe.macro_start_auton() is True
    probe.touch_client_liveness()
    assert _wait_until(lambda: probe._error_router.report_warning.called,
                        timeout=probe.WEB_CLIENT_STOP_TIMEOUT)
    # Still armed: the warn tier alone must not stop anything.
    assert probe.mode is ProbeMode.AUTONOMOUS


def test_full_stop_after_the_stop_threshold_of_silence(probe):
    assert probe.macro_start_auton() is True
    probe.touch_client_liveness()
    assert _wait_until(lambda: not probe.system_enabled,
                        timeout=probe.WEB_CLIENT_STOP_TIMEOUT * 6)
    assert probe.estop_latched, "D-8's stop tier is the FULL STOP latch"
    assert probe.mode is ProbeMode.DISABLED


def test_a_continuously_polled_client_never_trips_the_stop(probe):
    assert probe.macro_start_auton() is True
    deadline = time.time() + probe.WEB_CLIENT_STOP_TIMEOUT * 4
    while time.time() < deadline:
        probe.touch_client_liveness()
        time.sleep(probe._INTERLOCK_POLL_INTERVAL)
    assert probe.mode is ProbeMode.AUTONOMOUS
    assert not probe.estop_latched


def test_checking_in_again_after_a_warning_clears_it_and_no_stop_follows(probe):
    assert probe.macro_start_auton() is True
    probe.touch_client_liveness()
    assert _wait_until(lambda: probe._error_router.report_warning.called,
                        timeout=probe.WEB_CLIENT_STOP_TIMEOUT)
    probe._error_router.report_warning.reset_mock()
    probe.touch_client_liveness()
    time.sleep(probe.WEB_CLIENT_WARN_TIMEOUT / 2)
    assert probe.mode is ProbeMode.AUTONOMOUS
    assert not probe.estop_latched


def test_manual_mode_is_gated_the_same_way(probe):
    probe.poller.gamepad = MagicMock()
    assert probe.enter_manual() is True
    probe.touch_client_liveness()
    assert _wait_until(lambda: not probe.system_enabled,
                        timeout=probe.WEB_CLIENT_STOP_TIMEOUT * 6)
    assert probe.estop_latched
