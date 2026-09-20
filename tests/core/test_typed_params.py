"""RC-6 — a parameter's fallback belongs to its class, not to the call site.

The defect is a speed hazard, not a formatting one. `get_params` coerced
every value with hardcoded fallbacks — `_num(self.x_step, 16)`,
`_num(self.full_speed, 400)` — which are *BaseProbe's* numbers. A DCProbe
declares `full_speed = "120"` and `man_full_speed = "120"`, but an empty or
unparseable field was enough to make the fallback hand the firmware **400**:
more than three times the speed that probe is configured for, with no
indication anything had been substituted.
"""

import pytest

from model.probes import BaseProbe, ChuckPositioner, DCProbe, StepperProbe


@pytest.mark.parametrize("bad", ["", "  ", "abc", None, "nan", "inf"])
def test_a_dc_probe_never_falls_back_to_the_steppers_speed(bad):
    probe = DCProbe("SIM", None)
    probe.full_speed = bad
    params = probe.get_params()
    assert params["full_speed"] == 120, (
        f"an unparseable speed ({bad!r}) fell back to {params['full_speed']}, "
        "not this probe's own 120")


@pytest.mark.parametrize("bad", ["", "abc", None])
def test_a_dc_probes_manual_jog_speed_falls_back_to_its_own(bad):
    probe = DCProbe("SIM", None)
    probe.man_full_speed = bad
    # `manual_flag` is read-only (I-3.4). This test is about the jog speed the
    # frame carries, not about the mode, and send_manual_mode_command only
    # consults the mode to notice a lost pad — so no mode is needed at all.
    sent = {}

    class Recorder:
        def send_manual_mode_command(self, params):
            sent.update(params)

        def write_command(self, payload, priority=False):
            pass

    probe.serial_comm = Recorder()
    probe.poller = type("P", (), {"gamepad": object()})()
    probe.send_manual_mode_command({"x_axisStatus": 0.0})
    assert sent["manual_jog_speed"] == 120


@pytest.mark.parametrize("cls,expected_step", [
    (StepperProbe, 1),
    (DCProbe, 1),
    (ChuckPositioner, 2),
    (BaseProbe, 16),
])
def test_each_class_falls_back_to_its_own_step_size(cls, expected_step):
    probe = cls("SIM", None)
    probe.x_step = "not a number"
    assert probe.get_params()["x_step_size"] == expected_step


def test_a_valid_value_is_still_used_rather_than_the_default():
    probe = DCProbe("SIM", None)
    probe.full_speed = "75"
    assert probe.get_params()["full_speed"] == 75


def test_the_defaults_table_covers_every_parameter_it_is_asked_for():
    """A missing key would raise KeyError at the worst moment — mid-command."""
    for cls in (BaseProbe, StepperProbe, DCProbe, ChuckPositioner):
        probe = cls("SIM", None)
        probe.get_params()  # must not raise


# --------------------------------------------------------------------------
# RC-6 item 4 — a blank field must not command the wrong temperature.
# --------------------------------------------------------------------------


class _Recorder:
    def __init__(self):
        self.writes = []

    def is_open(self):
        return True

    def write_command(self, payload, priority=False):
        self.writes.append(payload)


def _temp():
    from model.temperature_system import TemperatureSystem

    ts = TemperatureSystem()
    ts.serial_conn = _Recorder()
    return ts


@pytest.mark.parametrize("field", ["setpoint", "p_term", "i_term", "d_term", "offset"])
@pytest.mark.parametrize("bad", ["", "   ", "abc"])
def test_a_non_numeric_field_refuses_the_whole_frame(field, bad):
    """The values were interpolated raw, so an empty field produced
    "<,6.0,2.0,0.5,.1,0>". The firmware parses that with strtok, which does
    not see an empty field — it sees the *next* one. Every parameter after
    the blank shifts left, so the board takes the ramp rate as its setpoint
    and the gains as everything else. A blank box silently commanded the
    wrong temperature with the wrong gains.
    """
    ts = _temp()
    setattr(ts, field, bad)
    ts.send_settings()
    assert ts.serial_conn.writes == [], (
        f"a frame was sent with {field}={bad!r}")


def test_a_valid_frame_still_goes_out_with_every_field_present():
    ts = _temp()
    ts.setpoint, ts.ramp_rate = "45.5", "12"
    ts.p_term, ts.i_term, ts.d_term, ts.offset = "2.5", "0.8", "0.2", "1.0"
    ts.send_settings()
    assert len(ts.serial_conn.writes) == 1
    frame = ts.serial_conn.writes[0]
    assert frame.startswith("<") and frame.endswith(">")
    assert len(frame.strip("<>").split(",")) == 6, "the frame lost a field"
    assert "" not in frame.strip("<>").split(","), "the frame carries a blank field"


def test_the_refusal_is_reported_rather_than_silent():
    from unittest.mock import patch

    ts = _temp()
    ts.setpoint = ""
    with patch("error_routing.ErrorRouter.report_warning") as warn:
        ts.send_settings()
    warn.assert_called_once()
    assert "Setpoint" in warn.call_args[0][1]


# -- S9 item 2: the type is declared, not inferred -------------------------

def test_a_param_declares_its_own_type_and_bounds():
    from model.probes import StepperProbe, DCProbe

    assert StepperProbe.PARAMS["x_step"].type == "int"
    assert StepperProbe.PARAMS["x_step"].minimum == 1
    # A DC probe runs at 120, not the stepper's 400 — the defect that made
    # the fallback a per-class fact rather than a call-site constant.
    assert DCProbe.PARAMS["full_speed"].default == 120
    assert StepperProbe.PARAMS["full_speed"].default == 400


def test_the_schema_publishes_the_type_so_views_stop_guessing():
    """Both desktop views called `float(current_value)` to classify a field.

    An empty box — what an operator leaves after clearing one — was therefore
    classified as text and silently lost its validator for the session.
    """
    from model import schema as sch
    from model.probes import StepperProbe

    probe = StepperProbe("SIM", None)
    try:
        entries = [e for e in sch.elements(probe.ui_schema)
                   if e["type"] == "entry"]
        assert entries
        for element in entries:
            assert element["value_type"] in ("int", "float", "text")
    finally:
        probe.teardown()


def test_parse_refuses_rather_than_substituting():
    """The RC-6 thesis in one method: an unreadable value is an error."""
    from model.probes import StepperProbe

    param = StepperProbe.PARAMS["full_speed"]
    ok, reason = param.parse("")
    assert ok is False and "empty" in reason
    ok, reason = param.parse("abc")
    assert ok is False and "not a number" in reason
    ok, reason = param.parse("0")
    assert ok is False and "at least" in reason
    assert param.parse("250") == (True, 250.0)


def test_coerce_falls_back_to_this_classs_own_default():
    from model.probes import DCProbe, StepperProbe

    assert DCProbe.PARAMS["full_speed"].coerce("") == 120
    assert StepperProbe.PARAMS["full_speed"].coerce("") == 400


# -- S9 item 3 / D-5: commands carry their inputs --------------------------

def test_d5_inputs_are_committed_before_the_command_runs():
    from model.probes import StepperProbe

    probe = StepperProbe("SIM", None)
    try:
        ok, error = probe.apply_inputs({"x_dist": "12", "full_speed": "300"})
        assert (ok, error) == (True, None)
        assert probe.x_dist == 12.0
        assert probe.full_speed == 300.0
    finally:
        probe.teardown()


def test_d5_a_bad_field_refuses_the_whole_set():
    """Atomic. Committing field by field is the stale-value class itself."""
    from model.probes import StepperProbe

    probe = StepperProbe("SIM", None)
    try:
        probe.x_dist = "1"
        ok, error = probe.apply_inputs({"x_dist": "5", "full_speed": ""})
        assert ok is False
        assert "Autonomous Speed" in error, "the refusal must name the field"
        assert probe.x_dist == "1", "no field may be committed when one fails"
    finally:
        probe.teardown()


def test_d5_a_refused_command_does_not_run():
    from model.probes import StepperProbe

    probe = StepperProbe("SIM", None)
    try:
        ran = []
        probe.reset_baseline = lambda: ran.append(True)
        result = probe.execute_command(
            "reset_baseline", inputs={"full_speed": "not-a-number"})
        # `.refused`, not `is False`: S11 gave commands a result type, and
        # the whole point is that a refusal is distinguishable from a crash
        # and from a success (I-8.1). `assert not result` would pass for a
        # `Failed` too.
        assert result.refused
        # The reason names the field the way the *operator* sees it
        # ("Autonomous Speed"), not the attribute, which is what makes it
        # printable straight into the event log.
        assert "Autonomous Speed" in result.reason
        assert ran == [], "the command ran despite an invalid input"
    finally:
        probe.teardown()


def test_d5_an_undeclared_input_is_refused():
    from model.probes import StepperProbe

    probe = StepperProbe("SIM", None)
    try:
        ok, error = probe.apply_inputs({"nonsense": "1"})
        assert ok is False and "not a parameter" in error
    finally:
        probe.teardown()
