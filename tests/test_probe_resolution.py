"""CR-2: the Chuck Positioner's step-resolution lever (the station's side of
the `#RES` wire contract; firmware is another agent's).

The firmware lists `res` in its identity (`DEV: c caps=res`) and answers one
extension command, `#RES` (query) and `#RES <n>` (set). The station:

* never sends a `#` byte to a board with no caps (its parser would read the
  digit as a stop frame), and to a `res`-only board sends `#RES` and nothing
  else, ever (no INFO, LOG, HOSTTIMEOUT, HB: those are ext1's);
* learns the resolution by one query per link epoch and again after a
  suspected reset, never assumes it;
* sets it only at standstill, and takes the reply's `pos` as the position at
  once.

Fast = 2 (today's), Fine = 8 (PROVISIONAL, a bench value for the owner).
"""
import json
import time

import pytest

import schema as sch
from devices import serial_port as serial_device
from devices.mega_standard_sim import MegaStandardPort, MegaStandardSim
from model.probe import ChuckPositioner, ProbeMode, caps_of

from test_mega_standard import _a_full_session, hash_payloads
from test_wire_golden import RecordingPort
from test_xyz_stage import FakePad, wait_for

HASH_LINE = b"#RES\n"


@pytest.fixture
def make():
    """A Chuck Positioner on a simulated board (SIM path), opened with a pad
    bound; always closed."""
    built = []

    def _make(board=None, **board_kwargs):
        if board is None:
            board_kwargs.setdefault("letter", "c")
            board_kwargs.setdefault("caps", ("res",))
            board = MegaStandardSim(**board_kwargs)
        model = ChuckPositioner(port=MegaStandardPort("SIM", simulator=board),
                                gamepad=FakePad())
        built.append(model)
        model.open()
        return model, board

    yield _make
    for model in built:
        try:
            model.estop()
        finally:
            model.close()


def hash_lines(board):
    """Every `#` line the board processed, oldest first."""
    return [line for _t, line in board.received if line.startswith("#")]


def known(model):
    """Wait until the board has answered the resolution query."""
    assert wait_for(lambda: model.resolution is not None, 2.0), model.state["values"]
    return model


def element_for(model, command):
    return next(e for e in sch.elements(model.schema) if e.get("command") == command)


# == the identity ======================================================================

def test_the_res_token_is_read_from_the_identity():
    assert caps_of("c caps=res") == frozenset({"res"})
    assert caps_of("c") == frozenset()


def test_a_res_board_is_not_an_ext1_board(make):
    model, board = make()
    assert model.caps == frozenset({"res"})
    assert not model._ext_on
    assert "board" not in model.state            # ext1's diagnostics stay off
    titles = [s["title"] for s in model.schema["sections"]]
    assert "Zero and home" not in titles


# == a board that lists no capabilities =================================================

def test_a_capless_board_shows_the_toggle_greyed_with_its_reason_and_gets_no_hash(make):
    model, board = make(caps=())
    assert model.caps == frozenset()
    toggle = element_for(model, "set_resolution")
    assert toggle["enabled_by"] == "resolution_ready"
    assert "no step-resolution switch" in toggle["enabled_by_reason"]
    assert "flash" in toggle["enabled_by_reason"].lower()
    state = model.state
    assert state["values"]["resolution_ready"] is False
    assert state["values"]["is_fine"] is False
    assert model.resolution is None
    assert state["values"]["resolution_text"] == "unknown until the board answers"
    assert not sch.is_enabled(toggle, state["mode"], state["values"])
    refused = model.run("set_resolution", None, ("fine",))
    assert refused.is_refused and "step-resolution switch" in refused.reason, refused
    _a_full_session(model, board)
    assert hash_payloads(board) == []
    assert not any(p.startswith(b"#") for p in board.writes)
    assert model.resolution is None


def test_a_capless_board_with_no_answer_yet_is_not_asked_either():
    port = RecordingPort(identity="c")
    model = ChuckPositioner(port=port, gamepad=None)
    model.open()
    try:
        time.sleep(0.4)
        assert model.resolution is None and not model.resolution_ready
        assert not any(b"#" in p for p in port.writes), port.writes
    finally:
        model._stop_threads()
        model.close()


# == a res board: the query ==============================================================

def test_a_res_board_is_asked_once_and_reads_fast(make):
    model, board = make()
    known(model)
    time.sleep(0.5)                              # more sampler passes: no second ask
    assert [p for p in board.writes if b"#" in p] == [HASH_LINE]
    assert hash_lines(board) == ["#RES"]
    assert model.resolution == 2 and model.is_fine is False
    assert model.resolution_ready is True
    assert model.resolution_text == "½ step per count · fastest"
    values = model.state["values"]
    assert values["is_fine"] is False and values["resolution_ready"] is True
    assert values["resolution_text"] == model.resolution_text
    toggle = element_for(model, "set_resolution")
    assert sch.is_enabled(toggle, model.state["mode"], values)


def test_a_res_board_never_receives_an_ext1_line_over_a_full_session(make):
    """Connect, enable, step, jog, stop, disable, reconnect: the only `#`
    bytes are `#RES` queries (and sets), never INFO, LOG, HOSTTIMEOUT, HB."""
    model, board = make()
    known(model)
    _a_full_session(model, board)
    assert model.run("set_resolution", None, ("fine",)).is_ok
    time.sleep(0.4)
    seen = hash_lines(board)
    assert seen and all(line.split()[0] == "#RES" for line in seen), seen
    for word in (b"INFO", b"LOG", b"HOSTTIMEOUT", b"HB", b"HOME", b"ZERO", b"STOP"):
        assert not any(word in p for p in board.writes if b"#" in p), word
    assert board.host_timeout_ms is None          # no host timeout was ever armed
    assert board.log_level == 1
    assert all(len(f.split(",")) == 12 for f in board.frames), list(board.frames)


def test_the_resolution_is_asked_again_for_each_link_epoch(monkeypatch):
    """The connect path, then a cable pull and a replug: one `#RES` per
    epoch, and the board's answer after the reboot is what is read."""
    monkeypatch.setattr(serial_device.SerialPort, "BOOTLOADER_WAIT", 0.0)
    board = MegaStandardSim(letter="c", caps=("res",))
    port = MegaStandardPort("/dev/cu.usbmodemCHUCK", simulator=board)
    model = ChuckPositioner(port=port, gamepad=None)
    model.open()
    try:
        assert wait_for(lambda: port.status == "verified", 3.0)
        assert port.identity == "c caps=res"
        known(model)
        assert model.run("set_resolution", None, ("fine",)).is_ok
        assert model.resolution == 8
        epoch = model.position_epoch
        board.unplug()
        assert wait_for(lambda: port.status != "verified", 3.0)
        board.replug()                            # a new boot: back at 2
        assert wait_for(lambda: port.status == "verified" and model.position_epoch > epoch,
                        8.0), (port.status, model.position_epoch)
        assert wait_for(lambda: model.resolution == 2, 3.0), model.resolution
        time.sleep(0.5)
        asked = [p for p in board.writes if p.startswith(b"#RES\n")]
        assert len(asked) == 2, board.writes
        assert all(p.startswith(b"#RES") for p in board.writes if b"#" in p)
    finally:
        model.close()


# == the set =============================================================================

def test_fine_writes_res_8_and_rescales_the_position_at_once(make):
    model, board = make()
    known(model)
    board.move_carriage("X", 100)
    board.move_carriage("Y", -50)
    assert wait_for(lambda: model.position[:2] == (100, -50), 2.0), model.position
    start = len(board.writes)
    result = model.run("set_resolution", None, ("fine",))
    assert result.is_ok, result
    assert [p for p in list(board.writes)[start:]] == [b"#RES 8\n"]
    assert model.resolution == 8 and model.is_fine is True
    assert model.position[:2] == (400, -200), model.position
    assert board.position[:2] == (400, -200)
    assert model.velocity_text is not None and model._velocity == (0, 0, 0)
    assert model.resolution_text.startswith("⅛ step per count")
    assert model.state["values"]["is_fine"] is True
    assert board.microsteps == 8
    # Back down: position scaled toward zero, as the C does.
    board.move_carriage("X", board.axes["X"].phys + 1)   # 401: see the truncation
    assert wait_for(lambda: model.position[0] == 401, 2.0)
    assert model.run("set_resolution", None, ("fast",)).is_ok
    assert model.resolution == 2 and model.is_fine is False
    assert model.position[0] == 100 and model.position[1] == -50   # 401/4 -> 100


def test_the_same_value_writes_nothing(make):
    model, board = make()
    known(model)
    before = list(board.writes)
    assert model.run("set_resolution", None, ("fast",)).is_ok
    assert list(board.writes) == before
    assert model.run("set_resolution", None, ("fine",)).is_ok
    mid = list(board.writes)
    assert model.run("set_resolution", None, ("fine",)).is_ok
    assert list(board.writes) == mid


def test_an_unknown_choice_is_refused_without_a_byte(make):
    model, board = make()
    known(model)
    before = list(board.writes)
    result = model.run("set_resolution", None, ("coarse",))
    assert result.is_refused, result
    assert list(board.writes) == before and model.resolution == 2


def test_a_set_is_refused_while_a_step_is_in_flight(make):
    model, board = make()
    known(model)
    model.x_dist = 400
    assert model.run("step", None).is_ok           # a Step enters Autonomous itself
    assert model.is_moving
    before = list(board.writes)
    result = model.run("set_resolution", None, ("fine",))
    assert result.is_refused and "moving" in result.reason.lower(), result
    assert not any(p.startswith(b"#RES 8") for p in list(board.writes)[len(before):])
    assert model.resolution == 2 and board.microsteps == 2


def test_a_set_is_refused_while_latched(make):
    model, board = make()
    known(model)
    model.estop()
    before = list(board.writes)
    result = model.run("set_resolution", None, ("fine",))
    assert result.is_refused, result
    assert not any(p.startswith(b"#RES 8") for p in list(board.writes)[len(before):])
    assert model.resolution == 2


def test_a_busy_board_refuses_and_the_resolution_stays(make):
    """The station believes it is idle, the board is not (a hand on the pad,
    a D-pad step): `#ERR RES busy` comes back in the operator's words."""
    model, board = make()
    known(model)
    board.write(b"e")
    time.sleep(0.1)
    board.write(b"1,1,1,0,400.0,0,0,0,200,0,0,1\n")      # a long autonomous move
    assert board.moving
    result = model.run("set_resolution", None, ("fine",))
    assert result.is_refused and "busy" in result.reason, result
    assert model.resolution == 2 and board.microsteps == 2
    assert model.position_epoch == 1


def test_a_silent_board_leaves_the_resolution_unchanged(make):
    model, board = make()
    known(model)
    board.silent = True
    result = model.run("set_resolution", None, ("fine",))
    assert result.is_refused and "no reply" in result.reason, result
    assert model.resolution == 2 and model.is_fine is False


def test_a_refusal_on_the_wire_is_worded_from_its_reason():
    """A fake that answers each `#RES` line as scripted (the three refusals
    the contract lists)."""
    for reason in ("bad-arg-power-of-2-1..256", "driver-readback-mismatch"):
        port = ScriptedPort("c caps=res", {"RES 8": f"#ERR RES {reason}"})
        model = ChuckPositioner(port=port, gamepad=None)
        model.open()
        try:
            known(model)
            result = model.run("set_resolution", None, ("fine",))
            assert result.is_refused and reason in result.reason, result
            assert model.resolution == 2 and model.is_fine is False
        finally:
            model._stop_threads()
            model.close()


class ScriptedPort(RecordingPort):
    """A recording port that answers `#RES` as a board would: the query with
    the resolution it holds, anything else as `answers` says."""

    def __init__(self, identity, answers, microsteps=2):
        super().__init__(identity=identity)
        self.answers = answers
        self.microsteps = microsteps

    def write(self, payload, *, priority=False, abort_if=None):
        written = super().write(payload, priority=priority, abort_if=abort_if)
        text = bytes(payload).decode("ascii", "replace").strip() if written else ""
        if text.startswith("#"):
            line = text[1:]
            if line == "RES":
                self.replies.append(f"#OK RES microsteps={self.microsteps} pos=0,0,0")
            elif line in self.answers:
                self.replies.append(self.answers[line])
        return written


# == a reset ==============================================================================

def test_after_a_board_reset_the_host_asks_again_and_reads_the_boot_value(make):
    model, board = make()
    known(model)
    assert model.run("set_mode", None, ("auto",)).is_ok
    assert model.run("set_resolution", None, ("fine",)).is_ok
    time.sleep(model.EXT_JUMP_GRACE + 0.1)           # a set's own jump grace is over
    board.move_carriage("X", board.axes["X"].phys + 1500)
    assert wait_for(lambda: model.position[0] > 1280, 2.0), model.position
    asked = len([p for p in board.writes if p == HASH_LINE])
    board.reboot()                                   # boots at 2, position 0
    assert wait_for(lambda: len([p for p in board.writes if p == HASH_LINE]) > asked, 3.0), \
        "the host never re-asked after the suspected reset"
    assert wait_for(lambda: model.resolution == 2, 2.0), model.resolution
    assert model.is_fine is False
    assert board.microsteps == 2


def test_the_resolution_is_unknown_until_the_requery_lands(make):
    """After a suspected reset the old answer is not carried on."""
    model, board = make()
    known(model)
    assert model.run("set_resolution", None, ("fine",)).is_ok
    board.silent = True                              # the re-query goes unanswered
    time.sleep(model.EXT_JUMP_GRACE + 0.1)
    model._check_reset((0, 0, 0), (2000, 0, 0), time.monotonic() - 0.05,
                       time.monotonic())            # mode is disabled: nothing
    assert model.resolution == 8
    model.run("set_mode", None, ("manual",))
    model._check_reset((0, 0, 0), (2000, 0, 0), time.monotonic() - 0.05,
                       time.monotonic())
    assert model.resolution is None
    assert model.state["values"]["resolution_text"] == "unknown until the board answers"
    assert model.is_fine is False
    board.silent = False
    assert wait_for(lambda: model.resolution == 8, 2.0)


# == the page =============================================================================

def test_the_schema_has_one_tier_1_group_right_after_position(make):
    model, _ = make()
    sections = model.schema["sections"]
    titles = [s["title"] for s in sections]
    assert titles.index("Step resolution") == titles.index("Position") + 1
    section = sections[titles.index("Step resolution")]
    assert section.get("layout") == "group" and section.get("tier", 1) == 1
    toggle, line = section["elements"]
    assert toggle["type"] == "toggle" and toggle["model_attr"] == "is_fine"
    assert toggle["text"] == "Step resolution:"
    assert toggle["command"] == "set_resolution"
    assert toggle["on_args"] == ["fine"] and toggle["off_args"] == ["fast"]
    assert toggle["true_text"] == "Fine · ⅛ step (press for Fast)"
    assert toggle["false_text"] == "Fast · ½ step (press for Fine)"
    assert toggle["on_role"] == toggle["off_role"] == "neutral"
    assert toggle["disabled_when"] == ["latched", "fault"]
    assert line["type"] == "readonly" and line["model_attr"] == "resolution_text"
    assert line["secondary"] is True and line["dashboard"] is True and line["lead"] == "Now:"
    # The Panel lets the command through (its allow-list is the schema).
    assert model.run("set_resolution", None, ("fast",)).is_ok


def test_no_other_probe_gains_the_lever():
    from model.probe import DCProbe, StepperProbe
    for cls in (StepperProbe, DCProbe):
        model = cls(port=RecordingPort(identity=cls.IDENTITY), gamepad=None)
        assert "Step resolution" not in [s["title"] for s in model.schema["sections"]]
        assert "set_resolution" not in json.dumps(model.schema)


def test_a_set_is_taken_at_rest_in_manual_mode(make):
    """How the lever is used: manual mode on, sticks centred, nothing moving."""
    model, board = make()
    known(model)
    assert model.run("set_mode", None, ("manual",)).is_ok
    time.sleep(0.4)
    assert model.mode is ProbeMode.MANUAL and not model.is_moving
    assert model.run("set_resolution", None, ("fine",)).is_ok
    assert model.resolution == 8 and board.microsteps == 8
