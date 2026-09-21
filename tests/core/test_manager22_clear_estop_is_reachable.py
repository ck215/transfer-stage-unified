"""MANAGER-22: a latched FULL STOP has to be clearable from the UI.

**Seam pin, written before the fix**, per the WEB-19 rule.

`clear_estop()` is the only sanctioned way to release `_estop`, and RC-5's
docstring on it says "Explicit operator action. Nothing else may call this."
That sentence presupposes an operator-reachable control. At `feb77fe` there
was none: `grep -rn "clear_estop\|estop_latched" src/` returned only the three
definitions and their three properties. No view, no `ui_schema` entry, no Web
endpoint. Once latched, `_refuse_if_estopped` blocked every transition on that
model for the rest of the process's life.

This is not a corner case. D-8's client-liveness watchdog calls
`emergency_stop()` **by itself** after `WEB_CLIENT_STOP_TIMEOUT` of web-client
silence while a probe is in AUTONOMOUS or MANUAL, and that timeout is still
the unmeasured 15 s placeholder. A web operator who alt-tabs mid-jog latched
the probe, and the only recoveries were restarting the process — dropping
every other device's connection — or re-running the setup wizard.

**Why per-device rather than one dashboard-wide control.** The latch is
per-device state, so the clear is too: an operator who has inspected one stage
should not be silently re-arming a probe on another tab they have not looked
at. Note this is *not* a reintroduction of the per-device "Full Stop" button
that was deliberately removed from `BaseProbe.ui_schema` — that one was a
redundant second E-stop, since the dashboard's global FULL STOP already
reaches every model. A clear is a recovery control, and it has no global
equivalent to be redundant with.

What these tests deliberately do NOT pin: the button's label, its section, or
its `role`. What is pinned is that the command is declared in the schema (so
all three renderers get it from one declaration, per D-6), that it refuses to
fire unattended, that it actually releases the latch, and that releasing the
latch does not itself start anything moving.
"""
import threading
from unittest.mock import MagicMock

import pytest

from model.probes import StepperProbe, DCProbe, ChuckPositioner, ProbeMode
from model.temperature_system import TemperatureSystem
from model.rotator_system import RotatorSystem
from results import NeedsConfirmation, Ok


def _probe():
    """The canonical double: a SIM probe with a recording transport.

    Deliberately not `StepperProbe(MagicMock(), ...)`. A MagicMock transport
    makes `read_position()` return a MagicMock, which the model's sampler
    thread then compares against an int — raising on every tick and flooding
    the global `EventBus` with "Serial Read Error" for the rest of the
    session. That leaks into unrelated tests that assert on the error buffer
    (it broke `tests/web/test_web_server.py::test_api_logs_and_errors` under
    random ordering), which is why these tests stop their loops in `finally`.
    """
    probe = DCProbe("SIM", None)
    probe.serial_comm = _Transport()
    return probe


class _Transport:
    def __init__(self):
        self.writes = []

    def write_command(self, payload, priority=False):
        self.writes.append(payload)

    def enable(self):
        self.write_command(b"e")

    def disable(self):
        self.write_command(b"d")

    def send_autonomous_command(self, params):
        self.writes.append(("auton", params))

    def send_manual_mode_command(self, params):
        self.writes.append(("manual", params))

    def read_position(self):
        return None

    def close(self):
        pass


def _commands_in(schema):
    """Every command name the schema declares, from any element type."""
    names = set()
    for section in schema.get("sections", []):
        for el in section.get("elements", []):
            for key in ("command", "options_command"):
                if el.get(key):
                    names.add(el[key])
    return names


# -- 1. the control exists, in the schema, for every model that can latch ----

@pytest.mark.parametrize("build", [
    pytest.param(lambda: StepperProbe(MagicMock(), controller_id="0"), id="stepper"),
    pytest.param(lambda: DCProbe(MagicMock(), controller_id="0"), id="dc"),
    pytest.param(lambda: ChuckPositioner(MagicMock(), controller_id="0"), id="chuck"),
    pytest.param(lambda: TemperatureSystem(port=None), id="heater"),
    pytest.param(lambda: RotatorSystem(default_port=None), id="rotator"),
])
def test_manager22_every_latching_model_declares_the_clear_command(build):
    """One declaration, three renderers (D-6). A view-only button would not
    reach the Web client, which is the frontend the D-8 auto-latch actually
    strands."""
    model = build()
    assert hasattr(model, "clear_estop"), "test premise: this model can latch"
    assert "clear_estop" in _commands_in(model.ui_schema), (
        f"{type(model).__name__} can latch FULL STOP but declares no command "
        f"to clear it, so no frontend renders one and the latch is "
        f"unrecoverable without restarting the process")


# -- 2. it refuses to fire unattended ---------------------------------------

def test_manager22_clearing_unconfirmed_asks_first_and_does_not_clear():
    probe = _probe()
    probe.emergency_stop()
    assert probe.estop_latched

    result = probe.clear_estop()

    assert isinstance(result, NeedsConfirmation), (
        "releasing a FULL STOP latch must not happen on a single click; "
        f"got {type(result).__name__}")
    assert probe.estop_latched, (
        "the latch was released while only asking for confirmation")


def test_manager22_the_confirmation_names_its_own_command():
    """The views re-dispatch `result.command` with confirmed=True. If it names
    the wrong command the dialog is a dead end in all three frontends."""
    probe = _probe()
    probe.emergency_stop()
    assert probe.clear_estop().command == "clear_estop"


# -- 3. confirmed, it actually releases, and the device works again ---------

def test_manager22_confirmed_clear_releases_the_latch():
    probe = _probe()
    probe.emergency_stop()

    probe.clear_estop(True)

    assert not probe.estop_latched


def test_manager22_after_clearing_a_refused_transition_is_possible_again():
    """The point of the whole finding. Latching blocks every transition via
    `_refuse_if_estopped`; clearing has to actually restore the device, not
    merely flip a flag that nothing consults."""
    probe = _probe()
    probe.emergency_stop()
    assert probe._refuse_if_estopped("test") is True

    probe.clear_estop(True)

    assert probe._refuse_if_estopped("test") is False, (
        "the latch reads as clear but the refusal gate still fires")


def test_manager22_the_clear_is_reachable_through_execute_command():
    """The path all three views actually use. A method the schema names but
    `execute_command` cannot dispatch is still unreachable."""
    probe = _probe()
    probe.emergency_stop()

    asked = probe.execute_command("clear_estop")
    assert isinstance(asked, NeedsConfirmation)
    assert probe.estop_latched

    probe.execute_command("clear_estop", args=[True])
    assert not probe.estop_latched


# -- 4. clearing is not itself an action -----------------------------------

def test_manager22_clearing_does_not_re_arm_or_move_anything():
    """A clear returns the device to *refusable*, not to *running*. If it
    re-armed, an operator recovering from an auto-latch would energize a stage
    they are standing over."""
    probe = _probe()
    try:
        probe.enter_manual()
        probe.emergency_stop()
        assert probe.estop_latched

        probe.clear_estop(True)

        assert probe._mode is ProbeMode.DISABLED, (
            f"clearing the latch left the probe in {probe._mode}; it must "
            f"come back disabled and be re-armed deliberately")
        assert not probe.manual_flag
        assert not probe.auton_flag
    finally:
        # Arming starts the model's loops; leaving them running leaks a
        # sampler thread into every later test in the session.
        probe.stop_loops()


def test_manager22_clearing_an_unlatched_device_is_harmless():
    probe = _probe()
    assert not probe.estop_latched
    probe.clear_estop(True)
    assert not probe.estop_latched
