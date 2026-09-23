"""STEPPER-9 — the G-code script path is a motion path, and is validated like one.

Two defects, both on the wire side of `run_script`:

1. **Every word starting with 'G' was treated as a move.** `G21`, `G90`,
   `G91`, `G28` have no X/Y/Z, so each one became a zeroed autonomous packet
   sent to the board. A file that opens with the usual `G21`/`G90` preamble
   commanded the stage twice before its first real move.
2. **Numerics were unvalidated.** `cmd_params` was built from raw strings and
   sent, and only *then* did `float(self.x_step)` / `float(feedrate)` run. So
   a malformed value was dispatched to the hardware first and blew up
   afterwards, aborting the script through the generic `except` with the bad
   command already on the wire. `get_params` has sanitised through the
   parameter table since RC-6; this path never did.

The reference behaviour is `get_params`, and the rule these pin is the one
`temperature_system.send_settings` already follows: a value that cannot be
read is an error to refuse, not a number to invent.
"""

import sys
import time
from unittest.mock import MagicMock, patch

import pytest

from error_routing import ErrorRouter
from model.probes import StepperProbe


def _wait_until(condition, timeout=2.0, interval=0.01):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return True
        time.sleep(interval)
    return condition()


class Line:
    def __init__(self, command, params, gcode_str):
        self.command = command
        self.params = params
        self.gcode_str = gcode_str


def _parser_yielding(*lines):
    """A stand-in for gcodeparser that yields exactly `lines`.

    Patched onto `model.probes.gcodeparser` by name, never via
    `sys.modules` — the scripting suite already learned the hard way that
    `sys.modules['gcodeparser'] = mock` only takes effect if this file is
    what first imports `model.probes`.
    """
    mock = MagicMock()

    class _P:
        def __init__(self, _text):
            self.lines = list(lines)

    mock.GcodeParser = _P
    return mock


def _run(tmp_path, probe, *lines, wait_for=None):
    path = tmp_path / "script.gcode"
    path.write_text("; irrelevant, the parser is mocked\n")
    with patch.object(sys.modules["model.probes"],
                      "gcodeparser", _parser_yielding(*lines)):
        probe.run_script(str(path))
        thread = probe._script_thread
        if wait_for is not None:
            _wait_until(wait_for)
        thread.join(timeout=3.0)
        assert not thread.is_alive(), "the script thread never finished"


@pytest.fixture
def probe():
    p = StepperProbe(port="SIM", controller_id="None")
    p.serial_comm = MagicMock()
    p.serial_comm.is_open.return_value = True
    return p


def _moves(probe):
    """Only the real auton frames: the zeroed stop frames are not moves."""
    return [call[0][0]
            for call in probe.serial_comm.send_autonomous_command.call_args_list
            if call[0][0].get("command_code_auton")]


# --------------------------------------------------------------------------
# 1. Not every 'G' is a move.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("word,text", [
    (21, "G21"), (90, "G90"), (91, "G91"), (28, "G28"), (17, "G17"),
])
def test_non_motion_g_words_are_not_sent_as_moves(probe, tmp_path, word, text):
    _run(tmp_path, probe, Line(("G", word), {}, text))
    assert _moves(probe) == [], f"{text} was dispatched to the board as a move"


def test_an_ignored_g_word_is_reported_rather_than_silently_dropped(probe, tmp_path):
    seen = []
    ErrorRouter.subscribe(seen.append)
    try:
        _run(tmp_path, probe, Line(("G", 90), {}, "G90"))
    finally:
        ErrorRouter.unsubscribe(seen.append)
    assert any("G90" in e.message for e in seen), \
        "the operator was never told their G90 did nothing"


def test_a_motion_word_after_an_ignored_one_still_runs(probe, tmp_path):
    """Skipping a preamble word must not abandon the file."""
    _run(tmp_path, probe,
         Line(("G", 21), {}, "G21"),
         Line(("G", 90), {}, "G90"),
         Line(("G", 1), {"X": 5}, "G1 X5"),
         wait_for=lambda: _moves(probe))
    moves = _moves(probe)
    assert len(moves) == 1
    assert moves[0]["x_dist"] == "5"


@pytest.mark.parametrize("word", [0, 1])
def test_g0_and_g1_are_still_moves(probe, tmp_path, word):
    _run(tmp_path, probe, Line(("G", word), {"X": 2}, f"G{word} X2"),
         wait_for=lambda: _moves(probe))
    assert len(_moves(probe)) == 1


# --------------------------------------------------------------------------
# 2. An unvalidated numeric must never become an axis command.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("axis", ["X", "Y", "Z"])
def test_a_non_numeric_axis_value_is_never_dispatched(probe, tmp_path, axis):
    _run(tmp_path, probe, Line(("G", 1), {axis: "abc"}, f"G1 {axis}abc"))
    assert _moves(probe) == [], \
        f"a non-numeric {axis} reached the hardware as an axis command"


@pytest.mark.parametrize("bad", ["nope", float("nan"), float("inf"), None])
def test_a_non_numeric_feedrate_is_never_dispatched(probe, tmp_path, bad):
    _run(tmp_path, probe, Line(("G", 1), {"X": 1, "F": bad}, "G1 X1 F?"))
    assert _moves(probe) == []


def test_a_malformed_numeric_aborts_the_run_and_is_reported(probe, tmp_path):
    with patch.object(ErrorRouter, "report_error") as reported:
        _run(tmp_path, probe,
             Line(("G", 1), {"X": "abc"}, "G1 Xabc"),
             Line(("G", 1), {"X": 5}, "G1 X5"),
             wait_for=lambda: reported.called)
    assert reported.called, "a malformed numeric was swallowed"
    assert _moves(probe) == [], "the run continued past a refused line"


def test_an_unparseable_step_size_does_not_reach_the_board(probe, tmp_path):
    """The class's own default, via the parameter table — never the raw field.

    `x_step_size` was interpolated straight out of `self.x_step`, so an empty
    or edited-to-garbage step box was sent verbatim. `get_params` coerces the
    same field through `PARAMS`; this path now does too.
    """
    probe.x_step = ""
    _run(tmp_path, probe, Line(("G", 1), {"X": 1}, "G1 X1"),
         wait_for=lambda: _moves(probe))
    moves = _moves(probe)
    assert moves, "the move was refused for the wrong reason"
    assert moves[0]["x_step_size"] == StepperProbe.PARAMS["x_step"].default


# --------------------------------------------------------------------------
# Regression guards: a valid move is untouched.
# --------------------------------------------------------------------------


def test_a_valid_move_is_dispatched_exactly_as_before(probe, tmp_path):
    _run(tmp_path, probe,
         Line(("G", 0), {"X": 10.5, "Y": 20.0, "F": 100}, "G0 X10.5 Y20 F100"),
         wait_for=lambda: _moves(probe))
    moves = _moves(probe)
    assert len(moves) == 1
    assert moves[0]["x_dist"] == "10.5"
    assert moves[0]["y_dist"] == "20.0"
    assert moves[0]["z_dist"] == "0"
    assert moves[0]["full_speed"] == "100"


def test_non_gcode_lines_still_go_out_through_the_transport(probe, tmp_path):
    """The raw `ser.write` fallbacks are already gone (S3); keep them gone."""
    _run(tmp_path, probe,
         Line(("M", 104), {"S": 200}, "M104 S200"),
         wait_for=lambda: probe.serial_comm.write_command.called)
    probe.serial_comm.write_command.assert_called_once_with("M104 S200\n")
