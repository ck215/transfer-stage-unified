"""DC-6 (probes half): the interlock that greys out "Start Stepping" and the
entry fields in Autonomous/Manual mode has to be enforced by the model, not
only rendered that way.

The schema has always declared `disabled_when=("autonomous", "manual")` on
every entry (`probes.py: ui_schema`), but until this fix that declaration
was read only by each view's own greying-out logic (and, per the Web JS,
inconsistently even there). A request that goes straight at the model —
`setattr`, the Web `/api/set_attr` route, or `execute_command` called
directly — reached "Start Stepping" or an entry regardless of mode. Wave 3
found the schema's `disabled_when` "never protected the web setattr path";
this closes that for probes.py's own contribution.
"""
import pytest
from unittest.mock import MagicMock

from model.probes import BaseProbe, DCProbe, ProbeMode, StepperProbe


@pytest.fixture
def probe():
    p = StepperProbe("SIM", None)
    yield p
    p.teardown()


def _force_mode(p, mode):
    """Put the probe in `mode` without the hardware ceremony — these tests
    are about the gate, not about arming."""
    p._mode = mode


def test_an_entry_can_be_set_while_idle(probe):
    probe.x_dist = "5"
    assert probe.x_dist == "5"


def test_an_entry_is_refused_while_autonomous(probe):
    _force_mode(probe, ProbeMode.AUTONOMOUS)
    before = probe.x_dist
    with pytest.raises(ValueError):
        probe.x_dist = "50"
    assert probe.x_dist == before, "a refused write must not land"


def test_an_entry_is_refused_while_manual(probe):
    _force_mode(probe, ProbeMode.MANUAL)
    with pytest.raises(ValueError):
        probe.full_speed = "300"


def test_the_refusal_names_the_mode(probe):
    _force_mode(probe, ProbeMode.AUTONOMOUS)
    with pytest.raises(ValueError, match="autonomous"):
        probe.x_step = "9"


def test_a_dc_probes_extra_entries_are_gated_too():
    """DCProbe adds slow_speed/brake_distance to the same section with the
    same disabled_when; the gate is generic, not hand-wired per field."""
    probe = DCProbe("SIM", None)
    try:
        _force_mode(probe, ProbeMode.AUTONOMOUS)
        with pytest.raises(ValueError):
            probe.slow_speed = "10"
    finally:
        probe.teardown()


def test_the_gate_does_not_touch_value_validation():
    """RC-6's lenient value handling is untouched by the mode gate: while
    idle, an unparseable value is still accepted and only substituted for
    later inside get_params (test_typed_params.py owns that contract)."""
    probe = StepperProbe("SIM", None)
    try:
        probe.full_speed = "not-a-number"
        assert probe.get_params()["full_speed"] == 400
    finally:
        probe.teardown()


# -- "Start Stepping" itself, reached through execute_command --------------

def test_start_stepping_is_refused_while_already_autonomous(probe):
    _force_mode(probe, ProbeMode.AUTONOMOUS)
    sent = []
    probe.serial_comm = MagicMock()
    probe.serial_comm.send_autonomous_command.side_effect = (
        lambda params: sent.append(params))

    result = probe.execute_command(
        "macro_start_auton",
        inputs={"x_dist": "1", "y_dist": "0", "z_dist": "0", "full_speed": "50"})

    assert result.refused, "Start Stepping must be refused while autonomous"
    assert sent == [], "no autonomous command frame may reach the transport"


def test_start_stepping_is_refused_while_manual(probe):
    _force_mode(probe, ProbeMode.MANUAL)
    result = probe.execute_command(
        "macro_start_auton",
        inputs={"x_dist": "1", "y_dist": "0", "z_dist": "0", "full_speed": "50"})
    assert result.refused


def test_start_stepping_still_runs_from_idle(probe):
    """Regression: the gate must not block the normal path — direct method
    calls (what every existing probe-mode test drives) and execute_command
    from ENABLED_IDLE/DISABLED both still work."""
    probe.serial_comm = MagicMock()
    result = probe.execute_command(
        "macro_start_auton",
        inputs={"x_dist": "1", "y_dist": "0", "z_dist": "0", "full_speed": "50"})
    assert result.ok
    assert probe.mode is ProbeMode.AUTONOMOUS


def test_stop_still_works_while_autonomous(probe):
    """The toggle that turns autonomous OFF must not itself be gated by
    disabled_when=autonomous, or the operator could never stop."""
    probe.serial_comm = MagicMock()
    probe.execute_command(
        "macro_start_auton",
        inputs={"x_dist": "1", "y_dist": "0", "z_dist": "0", "full_speed": "50"})
    assert probe.mode is ProbeMode.AUTONOMOUS
    result = probe.execute_command("toggle_auton")
    assert result.ok
    assert probe.mode is ProbeMode.DISABLED
