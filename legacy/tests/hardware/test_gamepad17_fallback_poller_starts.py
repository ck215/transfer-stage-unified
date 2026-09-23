"""GAMEPAD-17 (the `probes.py` share): "fallback poller creation is only
reachable if `ControllerPoller(...)` raised at construction... a poller
created here never polls."

That was true under the old view-owned polling model, where a view decided
at build time whether to start a poller's clock. RC-4 moved polling into
the model (`BaseProbe.start_loops`), started once, from `_transition`, the
first time the probe arms. If a poller could not be built at `__init__`
(no gamepad hardware yet) and is only constructed later, inside
`set_controller`'s fallback branch, `start_loops()` already ran — against
`self.poller is None` — the last time this probe armed. Nothing has ever
told the newly-built poller to start polling, so it sits bound but inert:
the exact end state the audit named, reached by a different route now that
the loop is model-owned rather than view-owned.

The other two GAMEPAD-17 bullets (`change_controller`, `parse_controller_id`)
live in `controller/gamepad.py` and `app.py`, neither of which is in this
worktree's write set, and both have live callers in test files this
worktree also does not own (`tests/hardware/test_gamepad.py`,
`tests/core/test_integration.py`, `tests/scripting/test_edge_mvc_parser.py`)
— see the wave 4 handoff.
"""
from unittest.mock import MagicMock

import pytest

from model.probes import StepperProbe


@pytest.fixture
def probe():
    p = StepperProbe("SIM", None)
    # Constructed with no bound controller (poller creation may itself have
    # failed in a headless test env; force the exact precondition either
    # way: no poller yet).
    p.poller = None
    yield p
    p.teardown()


def _fake_poller_class(monkeypatch):
    """A `ControllerPoller` stand-in that reports success and records
    whether its polling clock was started."""
    started = {"count": 0}

    class FakePoller:
        def __init__(self, controller_id, active_claims, process_name):
            self.controllerID = controller_id
            self.gamepad = object()  # bound
            self.is_polling = False

        def start_polling(self, *args, **kwargs):
            started["count"] += 1
            self.is_polling = True

        def set_controller(self, controller_id):
            return True

        def stop_polling(self):
            self.is_polling = False

        def close(self):
            pass

    monkeypatch.setattr("controller.gamepad.ControllerPoller", FakePoller)
    return started


def test_a_fallback_poller_built_while_already_armed_is_started(probe, monkeypatch):
    """The probe is already energized (poller was None at the time), then
    hardware appears and `set_controller` builds a poller for the first
    time. It must not sit bound but never polled.
    """
    probe.serial_comm = MagicMock()
    assert probe.enable() is True
    assert probe.system_enabled is True
    assert probe.poller is None

    started = _fake_poller_class(monkeypatch)

    assert probe.set_controller("ID 0") is True
    assert probe.poller is not None
    assert started["count"] == 1, (
        "a poller built after the probe was already armed must have its "
        "polling loop started, not sit inert")


def test_a_fallback_poller_built_while_disabled_is_left_for_the_next_arm(probe, monkeypatch):
    """No regression: while the probe is not armed, nothing needs to start
    polling yet -- `_transition` starts it the normal way on the next
    `enable()`/`enter_manual()`/`enter_auton()`.
    """
    assert probe.system_enabled is False
    started = _fake_poller_class(monkeypatch)

    assert probe.set_controller("ID 0") is True
    assert probe.poller is not None
    assert started["count"] == 0
