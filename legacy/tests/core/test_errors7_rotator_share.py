"""ERRORS-7, the `rotator_system.py` share: a refusal reported as success.

ERRORS-7 is the long-running "failures go to a console nobody is watching
instead of to the operator" finding. Its `probes.py`, `serial.py`,
`temperature_system.py` and `redpercent_system.py` shares are closed. This is
the last one.

The live defect: `_run_async` refuses to dispatch while the FULL STOP latch is
set — correctly, that is the point — but it does so with a bare `print` and an
implicit `return None`. `home()` and `reset_and_configure()` return whatever
it returns, and `SchemaCommands.execute_command` runs the result through
`as_result`, where **`None` becomes `Ok`**. So clicking "Home Stage" on a
latched rotator refuses the motion and tells the operator it worked.

Safety is not the problem here — the stage genuinely does not move, verified
below. Truthfulness is: the operator is looking at a success for an action
that was refused, on a device they have just emergency-stopped, and the one
record of the refusal is a line on stdout.

Contrast `_guarded_move`, which handles the same condition and returns
`False` — `as_result(False)` is a `Refused`, which every frontend renders.
Its comment even says "Refused where the operator can see it... Both happen;
only this one is visible." That was accurate, and this file closes the gap it
names.
"""
from unittest.mock import MagicMock

import pytest

from model.rotator_system import RotatorSystem
from results import Ok, Refused


def _latched_rotator():
    rotator = RotatorSystem(default_port=None)
    rotator.smc = MagicMock()
    rotator.is_connected = True
    rotator.emergency_stop()
    assert rotator.estop_latched, "test premise"
    return rotator


def test_errors7_home_while_latched_is_refused_not_reported_as_success():
    rotator = _latched_rotator()

    result = rotator.execute_command("home")

    assert isinstance(result, Refused), (
        f"Home on a latched rotator came back as {type(result).__name__}; the "
        f"operator is shown a success for an action that was refused")
    assert not bool(result)


def test_errors7_the_refusal_says_why():
    rotator = _latched_rotator()
    result = rotator.execute_command("home")
    assert "stop" in str(result.reason).lower(), (
        f"the refusal does not mention the FULL STOP latch: {result.reason!r}")


def test_errors7_reset_and_configure_while_latched_is_also_refused():
    rotator = _latched_rotator()
    result = rotator.execute_command("reset_and_configure")
    assert isinstance(result, Refused)


def test_errors7_the_latch_still_actually_blocks_the_hardware():
    """The safety half, which was never broken and must not become broken
    while fixing the reporting half."""
    rotator = _latched_rotator()

    rotator.execute_command("home")
    rotator.execute_command("reset_and_configure")

    assert not rotator.smc.home.called, "a latched rotator dispatched a home"
    assert not rotator.smc.reset_and_configure.called


def test_errors7_an_unlatched_rotator_still_homes():
    """The negative case: refusing everything would also pass the tests
    above."""
    rotator = RotatorSystem(default_port=None)
    rotator.smc = MagicMock()
    rotator.is_connected = True

    result = rotator.execute_command("home")

    assert not isinstance(result, Refused), (
        f"an unlatched rotator refused a home: {getattr(result, 'reason', '')}")
