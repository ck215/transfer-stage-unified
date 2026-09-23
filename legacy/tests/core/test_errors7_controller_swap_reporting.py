"""ERRORS-7 (the `probes.py` share): swallowed exceptions that hide hardware
faults. The audit's two probes.py line references map, in the current
tree, to `set_controller`'s two failure branches:

* the fallback poller construction (gamepad hardware still unavailable, or
  now raising for some other reason), and
* an existing poller's swap (`poller.set_controller` raising).

Both were print-only: the exception reached a terminal only the process
owner sees, and the operator watching the dashboard got no signal that
"Swapping controller to: X" had actually failed. The audit's third
reference, `reconnect_serial`'s print-only failure, no longer exists —
D-11 purged runtime serial reconnect from this file entirely, so that
clause is closed by removal, not by this change.

The other ERRORS-7 sites (`temperature_system.py`, `rotator_system.py`,
`redpercent_system.py`, `serial.py`, `gamepad.py`) are frozen or owned by
other agents this wave; see the wave 4 handoff for what the same fix would
look like there.
"""
from unittest.mock import MagicMock, patch

import pytest

from model.probes import StepperProbe


@pytest.fixture
def probe():
    with patch('model.probes.serial') as serial_class, \
         patch('model.probes.ErrorPopupManager') as error_router, \
         patch('controller.gamepad.ControllerPoller') as poller_class:
        transport = MagicMock()
        serial_class.return_value = transport
        p = StepperProbe("COM_TEST", "None")
        p.serial_comm = transport
        p._error_router = error_router
        yield p
        p.teardown()


def test_a_failed_fallback_poller_build_is_reported_not_just_printed(probe, monkeypatch):
    probe.poller = None

    def _raise(*a, **kw):
        raise RuntimeError("no SDL joystick backend")

    monkeypatch.setattr("controller.gamepad.ControllerPoller", _raise)

    assert probe.set_controller("ID 0") is False
    assert probe._error_router.report_warning.called, (
        "a controller-swap failure must reach the operator, not just stdout")


def test_a_failed_swap_on_an_existing_poller_is_reported_not_just_printed(probe):
    probe.poller = MagicMock()
    probe.poller.set_controller.side_effect = RuntimeError("index out of range")
    probe.poller.gamepad = None

    assert probe.set_controller("ID 9") is False
    assert probe._error_router.report_warning.called
