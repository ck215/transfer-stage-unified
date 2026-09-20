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
    probe.manual_flag = True
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
