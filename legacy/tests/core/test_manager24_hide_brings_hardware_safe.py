"""MANAGER-24: hiding a device must actually bring it to a safe state.

**Seam pin, written before the fix.**

`SystemManager.hide()` states a hard guarantee in its own docstring:

    **The hardware is brought to a safe state first, per D-2: motion
    stops and the coils are de-energized.** Hiding a device removes the
    operator's ability to see what it is doing, and leaving an unwatched
    axis energized is precisely the situation the owner ruled against.

The implementation is:

    disable = getattr(model, "disable", None)
    if callable(disable):
        disable()

`disable()` is defined **only on `BaseProbe`** (probes.py:1369).
`TemperatureSystem` and `RotatorSystem` do not have it, so for those two the
`if callable(...)` guard is silently False, nothing is sent to the hardware,
and `hide()` returns True anyway.

At the bench: an operator middle-clicks the Temperature Controller's tab, or
closes its dock — the same gesture that safely de-energizes a stepper, DC or
chuck probe. The tab disappears. **The heater keeps driving toward its last
setpoint, unobserved, with no error, no warning and nothing in the log.** The
same gesture on the rotator neither stops nor de-energizes the stage.

The duck-typed `getattr` is what let this hide: a model that cannot be brought
to a safe state is indistinguishable, at this call site, from one that just
was. The existing hide/show tests never caught it because their `FakeDevice`
defines `disable`, so the absent-method branch had no coverage in either
direction.

Found by a fresh-eyes audit on 2026-09-21 and verified before writing this.
"""
import threading
from unittest.mock import MagicMock

import pytest

from model.probes import DCProbe
from model.system_manager import SystemManager
from model.temperature_system import TemperatureSystem
from model.rotator_system import RotatorSystem


class _Recorder:
    """Records what reached the wire."""

    def __init__(self):
        self.writes = []
        self.open = True

    def is_open(self):
        return self.open

    def write_command(self, payload, priority=False):
        self.writes.append(payload)

    def read_position(self):
        return None

    def close(self):
        self.open = False


# -- 1. the contract, stated structurally ----------------------------------

@pytest.mark.parametrize("build", [
    pytest.param(lambda: DCProbe("SIM", None), id="probe"),
    pytest.param(lambda: TemperatureSystem(port=None), id="heater"),
    pytest.param(lambda: RotatorSystem(default_port=None), id="rotator"),
])
def test_manager24_every_hideable_model_can_be_brought_to_a_safe_state(build):
    """`hide()` promises this for every device, not for probes only."""
    model = build()
    disable = getattr(model, "disable", None)
    assert callable(disable), (
        f"{type(model).__name__} has no disable(), so SystemManager.hide() "
        f"silently sends nothing and still reports the device hidden — while "
        f"its docstring promises motion stops and coils de-energize (D-2)")


# -- 2. it actually reaches the hardware -----------------------------------

def test_manager24_hiding_the_heater_writes_a_zero_setpoint():
    heater = TemperatureSystem(port=None)
    wire = _Recorder()
    heater.serial_conn = wire

    manager = SystemManager()
    manager.register("Temperature Controller", heater)
    assert manager.hide("Temperature Controller") is True

    assert wire.writes, (
        "hiding the heater sent nothing to the board; it is still driving "
        "toward its last setpoint with nobody watching it")
    sent = str(wire.writes[-1])
    assert sent.startswith("<0,"), (
        f"hiding the heater sent {sent!r}, which is not a zero-setpoint frame")


def test_manager24_hiding_the_rotator_stops_the_stage():
    rotator = RotatorSystem(default_port=None)
    smc = MagicMock()
    rotator.smc = smc
    rotator.is_connected = True

    manager = SystemManager()
    manager.register("SMC100 Rotator", rotator)
    assert manager.hide("SMC100 Rotator") is True

    assert smc.stop.called, (
        "hiding the rotator did not stop the stage; a move in progress "
        "continues with the view gone")


# -- 3. what must not regress ----------------------------------------------

def test_manager24_a_failing_disable_still_hides_the_view():
    """Existing contract, explicitly preserved: refusing to close a window
    because a serial write failed leaves the operator staring at a device
    they cannot dismiss."""
    heater = TemperatureSystem(port=None)
    heater.disable = MagicMock(side_effect=OSError("port gone"))

    manager = SystemManager()
    manager.register("Temperature Controller", heater)

    assert manager.hide("Temperature Controller") is True
    assert "Temperature Controller" in manager.hidden


def test_manager24_a_model_with_no_disable_is_reported_not_skipped_silently():
    """The duck-typed `getattr` is the thing that let this hide for so long.

    A model that genuinely cannot be brought to a safe state is a real
    situation — but it has to be visible, not indistinguishable from one that
    just was.
    """
    class _NoDisable:
        def teardown(self):
            pass

        def emergency_stop(self):
            return True

    reports = []
    manager = SystemManager()
    manager._report = lambda msg, exc, title: reports.append((msg, title))
    manager.register("odd", _NoDisable())

    assert manager.hide("odd") is True
    assert reports, (
        "a model with no disable() was hidden silently; hide() reported "
        "success for a device it never brought to a safe state")
