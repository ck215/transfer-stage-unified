"""The Mega standard (docs/rebuild/MEGA_STANDARD.md, 2026-10-09): the Probe
base's capability handshake and `#` extension channel, and the protocol
simulator of a standard Mega board it is built and tested against.

Safety first (repo rule), in this order:

1. A board that lists no capabilities gets exactly today's bytes and
   today's schema: the golden scenarios replayed with `DEV: s` as the
   identity, and a full session on a simulated OLD board (whose parser
   would read a digit inside a `#` line as a stop frame) that never sees a
   `#` byte, on the SIM path and on the full connect path.
2. On an `ext1` board: the host timeout is armed before anything is
   enabled, and kept alive by the heartbeat; a FAULT latches the stop; a
   LIMIT stops a Step in flight; a refused frame is said.
3. Then the features: per-axis Zero and Home, homed and limit state, each
   shown only when the caps list it and disabled with a reason when INFO
   says the axis's hardware is absent.

The model under test is a Stepper Probe whose board answers
`DEV: s caps=...` (a retrofitted board, MEGA_STANDARD section 6 phase 2):
the layer lives in the Probe base, so any Probe gets it from its caps.
`tests/test_xyz_stage_mega.py` covers the XYZ Stage (Mega) itself.
"""
import json
import struct
import time

import pytest

from devices import serial_port as serial_device
from devices.mega_standard_sim import (CAPS, JOG_FORMAT, MegaStandardPort,
                                       MegaStandardSim, _int16)
from model.probe import (ChuckPositioner, DCProbe, ProbeMode, StepperProbe,
                         caps_of)
from result import NeedsConfirm

from test_wire_golden import (PROBE_SCENARIOS, RecordingPort, _build_probe,
                              _drive_probe, _expected, _ids)
from test_xyz_stage import Collected, FakePad, wait_for

AXES = ("X", "Y", "Z")
LETTERS = {"StepperProbe": "s", "DCProbe": "d", "ChuckPositioner": "c"}


class Clock:
    """A virtual monotonic clock for the simulator's own tests."""

    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t

    def run(self, seconds, board, step=0.02, beat=None):
        """Advance `seconds`, touching the board every `step` (and sending
        `beat` each time, when given)."""
        end = self.t + seconds
        while self.t < end:
            self.t = min(end, self.t + step)
            if beat is not None:
                board.write(beat)
            else:
                board.in_waiting


def lines_from(board):
    return board.drain_lines()


def ext_lines(board):
    """Every `#` line the board processed, oldest first."""
    return [line for _t, line in board.received if line.startswith("#")]


def hash_payloads(board):
    """Every payload the host wrote that carries a `#` outside a jog packet
    (a jog packet's floats may hold the byte 0x23 as data)."""
    return [p for p in board.writes
            if p[:1] != b"\xaa" and b"#" in p]


@pytest.fixture
def make():
    """A Stepper Probe on a simulated board (SIM path), opened with a pad
    bound; always closed."""
    built = []

    def _make(board=None, opened=True, pad=True, **board_kwargs):
        if board is None:
            board_kwargs.setdefault("letter", "s")
            board = MegaStandardSim(**board_kwargs)
        port = MegaStandardPort("SIM", simulator=board)
        model = StepperProbe(port=port, gamepad=FakePad() if pad else None)
        built.append(model)
        if opened:
            model.open()
        return model, board

    yield _make
    for model in built:
        try:
            model.estop()
        finally:
            model.close()


def configured(model, board):
    """Wait until the link-up configuration has been answered."""
    assert wait_for(lambda: model._ext_reply("HOSTTIMEOUT") is not None
                    and model._ext_reply("INFO") is not None, 2.0), ext_lines(board)
    return model, board


# == 1. OLD BOARDS DO NOT CHANGE BY ONE BYTE ==========================================

@pytest.mark.parametrize("identity, expected", [
    (None, None), ("", None), ("s", frozenset()), ("x X", frozenset()),
    ("m caps=ext1,log,hostto", frozenset({"ext1", "log", "hostto"})),
    ("m caps=", frozenset()), ("s CAPS=Ext1", frozenset({"ext1"})),
])
def test_caps_are_read_from_the_identity_and_absent_caps_are_none(identity, expected):
    assert caps_of(identity) == expected


@pytest.mark.transport
@pytest.mark.parametrize("scenario", PROBE_SCENARIOS, ids=_ids(PROBE_SCENARIOS))
def test_a_board_that_answers_dev_s_gets_todays_golden_bytes(scenario):
    """The golden scenarios, replayed with the identity a real capless board
    gives (`DEV: s` -> "s"): byte for byte the captured frames."""
    port = RecordingPort(identity=LETTERS[scenario["device"]])
    probe, module = _build_probe(scenario["device"], port)
    assert probe.caps == frozenset()
    _drive_probe(scenario, probe, module, port)
    assert port.writes == _expected(scenario)


def _a_full_session(model, board):
    """Everything an operator does: sample, IDLE, a Step, manual jog with a
    D-pad press, leave, FULL STOP, clear, again. Long enough for several
    heartbeat intervals."""
    time.sleep(0.6)
    assert model.run("set_mode", None, ("idle",)).is_ok
    model.x_dist = 40
    assert model.run("step", None).is_ok
    time.sleep(0.4)
    assert model.run("set_mode", None, ("manual",)).is_ok
    model.gamepad.levels.update(axis_x=0.5)
    time.sleep(0.2)
    model.gamepad.press("hat_x", 1)
    time.sleep(0.2)
    model.gamepad.levels.update(axis_x=0.0)
    time.sleep(0.2)
    assert model.run("set_mode", None, ("disabled",)).is_ok
    model.estop()
    model.clear_estop(confirmed=True)
    time.sleep(0.3)


def test_no_hash_byte_ever_reaches_a_board_that_lists_no_capabilities(make):
    """An old board's parser drops `#` and the letters after it but reads a
    digit as a frame: `#LOG 2` would reach it as a stop. The station never
    sends one to a board without ext1, over a whole session."""
    model, board = make(letter="s", caps=())
    assert model.caps == frozenset()
    _a_full_session(model, board)
    assert hash_payloads(board) == []
    assert not any(p.startswith(b"#") for p in board.writes)
    # Every frame the board parsed is one of the station's 12-field frames.
    assert board.frames and all(len(f.split(",")) == 12 for f in board.frames), list(board.frames)
    assert "board" not in model.state
    commands = {e.get("command") for s in model.schema["sections"] for e in s["elements"]}
    assert not commands & {"zero_axis", "home_axis"}


def test_the_old_boards_parser_would_misread_a_hash_line():
    """The hazard, shown on the simulated old board: why the rule exists."""
    board = MegaStandardSim(letter="s", caps=())
    board.open()
    assert board.ask("s") == ["DEV: s"]
    assert board.ask("#LOG 2") == []
    assert list(board.frames) == ["2"], "a digit inside a # line was read as a frame"


def test_a_capless_board_on_the_full_connect_path_gets_only_the_identity_query(monkeypatch):
    """A real port name, the real handshake (`DEV: s` read off the wire),
    the sampler running: the only bytes are the identity pings."""
    monkeypatch.setattr(serial_device.SerialPort, "BOOTLOADER_WAIT", 0.0)
    board = MegaStandardSim(letter="s", caps=())
    port = MegaStandardPort("/dev/cu.usbmodemOLD", simulator=board)
    model = StepperProbe(port=port, gamepad=None)
    model.open()
    try:
        assert wait_for(lambda: port.status == "verified", 3.0)
        assert port.identity == "s" and model.caps == frozenset()
        time.sleep(0.7)
        assert set(board.writes) == {b"s\n"}
    finally:
        model.close()
    assert hash_payloads(board) == []


@pytest.mark.parametrize("cls", (StepperProbe, DCProbe, ChuckPositioner),
                         ids=lambda c: c.NAME)
def test_a_capless_probes_schema_and_state_are_todays(cls):
    """`DEV: s` (no caps) and no answer at all draw the same page."""
    letter = LETTERS[cls.__name__]
    unknown = cls(port=RecordingPort(identity=None), gamepad=None)
    capless = cls(port=RecordingPort(identity=letter), gamepad=None)
    assert json.dumps(capless.schema) == json.dumps(unknown.schema)
    assert set(capless.state) == set(unknown.state)
    assert set(capless.state["values"]) == set(unknown.state["values"])
    titles = [s["title"] for s in capless.schema["sections"]]
    assert "Zero and home" not in titles


# == 2. EXT1: THE STOP PATHS ============================================================

def test_link_up_sends_info_log_and_hosttimeout_then_a_heartbeat_every_250_ms(make):
    model, board = make()
    configured(model, board)
    time.sleep(0.8)
    lines = ext_lines(board)
    assert lines[:3] == ["#INFO", "#LOG 2", "#HOSTTIMEOUT 1000"], lines
    beats = [t for t, line in board.received if line == "#HB"]
    assert len(beats) >= 3, lines
    gaps = [b - a for a, b in zip(beats, beats[1:])]
    assert all(0.2 <= g <= 0.4 for g in gaps), gaps
    assert board.host_timeout_ms == 1000 and board.log_level == 2


def test_nothing_is_enabled_until_the_board_has_armed_its_host_timeout(make, monkeypatch):
    """A board that refuses HOSTTIMEOUT would not stop itself if the station
    went quiet: it is never armed."""
    board = MegaStandardSim(letter="s")
    monkeypatch.setattr(board, "_x_hosttimeout",
                        lambda command, args: board._err(command, "bad-arg"))
    model, board = make(board=board)
    assert wait_for(lambda: model._ext_reply("HOSTTIMEOUT") is not None, 2.0)
    result = model.run("set_mode", None, ("autonomous",))
    assert result.is_refused and "HOSTTIMEOUT" in result.reason
    assert model.mode is ProbeMode.DISABLED
    assert "e" not in [line for _t, line in board.received]


def test_the_heartbeat_keeps_a_long_move_alive_and_silence_stops_it(make):
    """The board's own host timeout is armed (1000 ms): with the station
    talking, a two-second Step runs to its end; with the station gone
    quiet mid-move, the board stops itself and says so."""
    model, board = configured(*make(limits=(-50000, 50000)))
    model.y_dist = 20000                        # 6.25 s at 3200 steps/s
    model.full_speed = 3200
    assert model.run("step", None).is_ok
    time.sleep(1.4)
    assert board.moving
    assert not any(l.startswith("#EVT FAULT") for l in board.drain_lines())
    model._stop_threads()                       # the station goes quiet
    assert wait_for(lambda: not board.moving, 2.5)
    assert board.position[1] < 20000, "the board ran on without the station"
    assert "#EVT FAULT host-timeout" in lines_from(board)


def test_a_board_fault_latches_the_stop(make):
    model, board = configured(*make())
    model.y_dist = 20000
    assert model.run("step", None).is_ok
    with Collected() as seen:
        board.inject_fault("Y", "short")
        assert wait_for(lambda: model.is_estopped, 2.0)
        assert wait_for(lambda: model.mode is ProbeMode.DISABLED, 2.0)
    assert "short" in seen.text("error") and "axis Y" in seen.text("error")
    assert not board.enabled


def test_a_fault_reported_while_disabled_warns_and_does_not_latch(make):
    model, board = configured(*make())
    with Collected() as seen:
        board.inject_fault("Z", "over-temperature")
        assert wait_for(lambda: "over-temperature" in seen.text("warning"), 2.0)
    assert not model.is_estopped


def test_a_limit_warns_and_stops_a_step_in_flight_on_every_axis(make):
    """Y reaches its switch; the board halts Y. The station stops the Step,
    so X does not go on along its own share of the move."""
    model, board = configured(*make(limits=(-1500, 1500)))
    model.x_dist, model.y_dist, model.full_speed = 200, 3000, 3200
    with Collected() as seen:
        assert model.run("step", None).is_ok
        assert wait_for(lambda: "Limit Reached" in seen.text("warning"), 3.0)
        assert wait_for(lambda: not board.moving, 1.0)
    assert "Step was stopped" in seen.text("warning")
    x, y, _z = board.position
    assert y >= 1500 and -200 < x < 0, board.position
    assert not model.is_moving
    assert "LS2" in model.limits_text


def test_a_limit_with_no_step_in_flight_only_warns(make):
    model, board = configured(*make())
    assert model.run("set_mode", None, ("manual",)).is_ok
    model.gamepad.levels.update(axis_x=-1.0)       # X counts up
    assert wait_for(lambda: board.moving, 1.0)
    with Collected() as seen:
        board.trip_limit("X", 2)                   # reached from clear, moving
        assert wait_for(lambda: "Limit Reached" in seen.text("warning"), 2.0)
    assert "Step was stopped" not in seen.text("warning")
    assert model.mode is ProbeMode.MANUAL


def test_a_refused_frame_is_said_and_nothing_counts_as_moving(make):
    """X powered up on LS1 with its end unknown: a frame that would move X
    is refused by the board (only a jog may move it), and the station says
    so instead of waiting for a move that will not come."""
    board = MegaStandardSim(letter="s")
    board.park_on("X", 1)
    model, board = make(board=board)
    configured(model, board)
    model.x_dist = 100
    with Collected() as seen:
        assert model.run("step", None).is_ok
        assert wait_for(lambda: "Move Refused" in seen.text("warning"), 2.0)
    assert "jog-off-it" in seen.text("warning")
    assert not model.is_moving


def test_a_stop_ends_a_home_and_the_late_fail_is_not_announced(make):
    model, board = configured(*make(home_edge=40000, limits=(-50000, 50000)))
    assert model.run("home_axis", None, ("X",)).is_ok
    assert model.is_moving
    with Collected() as seen:
        model.estop()
        assert wait_for(lambda: not board.moving, 1.0)
        time.sleep(0.2)
    assert "Home Failed" not in seen.text("warning")
    assert model._homing["X"] is None and not model.is_moving


# == 3. EXT1: THE FEATURES ===============================================================

def _elements(model):
    return [e for s in model.schema["sections"] for e in s["elements"]]


def test_zero_and_home_are_shown_only_when_the_caps_list_them(make):
    full, _ = make()
    commands = {e.get("command") for e in _elements(full)}
    assert {"zero_axis", "home_axis"} <= commands
    texts = {e.get("text") for e in _elements(full)}
    assert {"Zero X here", "Home Z", "Homed:", "Limit switches:", "Board:"} <= texts

    bare, _ = make(caps=("ext1", "log", "hostto"))
    commands = {e.get("command") for e in _elements(bare)}
    texts = {e.get("text") for e in _elements(bare)}
    assert not commands & {"zero_axis", "home_axis"}
    assert "Limit switches:" not in texts and "Board:" in texts
    refused = bare.run("home_axis", None, ("X",))
    assert refused.is_refused

    limits_only, _ = make(caps=("ext1", "hostto", "limits"))
    texts = {e.get("text") for e in _elements(limits_only)}
    assert "Limit switches:" in texts and "Zero X here" not in texts


def test_home_is_disabled_with_a_reason_when_info_says_there_is_no_sensor(make):
    model, board = configured(*make(hardware={"Y": {"home": False}}))
    home_y = next(e for e in _elements(model) if e.get("text") == "Home Y")
    assert home_y["enabled_by"] == "y_home_ready"
    assert home_y["enabled_by_reason"] == "No home sensor on Y"
    assert model.y_home_ready is False and model.x_home_ready is True
    assert model.state["values"]["y_home_ready"] is False
    result = model.run("home_axis", None, ("Y",))
    assert result.is_refused and "No home sensor on Y" in result.reason
    assert "#HOME Y" not in ext_lines(board)
    assert "no home sensor" in model.board_text


def test_home_runs_the_boards_sequence_and_homed_is_said(make):
    model, board = configured(*make())
    with Collected() as seen:
        assert model.run("home_axis", None, ("X",)).is_ok
        assert model.mode is ProbeMode.AUTO and model.is_moving
        assert wait_for(lambda: model.homed_text.startswith("X yes"), 5.0)
    assert "Axis Homed" in [e.title for e in seen.of("info")]
    assert wait_for(lambda: not model.is_moving, 2.0)
    assert board.position[0] == 0
    assert wait_for(lambda: model.position[0] == 0, 1.0)
    assert "X homed" in model.home_text
    assert "Board Reset Suspected" not in seen.text("warning")


def test_a_home_that_finds_no_edge_fails_and_says_so(make):
    model, board = configured(*make(limits=(-800, 800)))
    board.set_home_edge("X", None)
    with Collected() as seen:
        assert model.run("home_axis", None, ("X",)).is_ok
        assert wait_for(lambda: "Home Failed" in seen.text("warning"), 5.0)
    assert "no-edge" in seen.text("warning")
    assert model._homing["X"] is None and "failed" in model.home_text


def test_zero_here_zeros_one_axis_and_asks_first_when_it_is_homed(make):
    model, board = configured(*make())
    model.y_dist = 300
    assert model.run("step", None).is_ok
    assert wait_for(lambda: model.position[1] == 300 and not model.is_moving, 3.0)
    with Collected() as seen:
        assert model.run("zero_axis", None, ("Y",)).is_ok
        assert wait_for(lambda: model.position[1] == 0, 1.0)
    assert "Board Reset Suspected" not in seen.text("warning")
    assert board.position[1] == 0
    assert model.run("home_axis", None, ("Y",)).is_ok
    assert wait_for(lambda: model.homed_text == "X no, Y yes, Z no", 5.0)
    assert wait_for(lambda: not model.is_moving, 2.0)
    with pytest.raises(NeedsConfirm):
        model.zero_axis("Y")
    assert model.zero_axis("Y", True) == 0
    assert wait_for(lambda: model.homed_text == "X no, Y no, Z no", 1.0)


def test_ok_and_err_answer_the_pending_request(make):
    model, board = configured(*make())
    assert model._ext_request("HB").ok
    refused = model._ext_request("LOG 9")
    assert not refused.ok and refused.reason == "bad-arg"
    unknown = model._ext_request("FROB")
    assert not unknown.ok and unknown.reason == "unknown-command"


def test_a_request_with_the_sampler_stopped_reads_its_own_reply(make):
    model, board = make()
    configured(model, board)
    model._stop_threads()
    reply = model._ext_request("HB")
    assert reply.ok


def test_a_switch_seen_once_turns_the_interlock_on_for_the_session(make):
    model, board = configured(*make(hardware={"X": {"limits": False}},
                                    limits=(-1500, 1500)))
    assert "X none (no interlock)" in model.limits_text
    model.x_dist, model.full_speed = -3000, 3200  # X counts up: into LS2
    with Collected() as seen:
        assert model.run("step", None).is_ok
        assert wait_for(lambda: "Limit Switch Seen" in [e.title for e in seen.of("info")], 3.0)
        assert wait_for(lambda: "Limit Reached" in seen.text("warning"), 2.0)
    assert board.position[0] < 1600
    assert wait_for(lambda: "X none" not in model.limits_text, 2.0), model.limits_text


def test_state_carries_the_board_for_an_ext1_board(make):
    model, board = configured(*make())
    state = model.state
    json.dumps(state)
    assert state["board"]["caps"] == sorted(CAPS)
    assert state["board"]["homed"] == {"X": False, "Y": False, "Z": False}
    assert set(state["values"]) >= {"x_home_ready", "y_home_ready", "z_home_ready"}


# == 4. THE SIMULATOR'S OWN CONTRACT ======================================================

def _jog(x=0.0, y=0.0, z=0.0, speed=1600.0, mode=1, lr=0.0, ud=0.0, bumpers=0.0):
    return struct.pack(JOG_FORMAT, 0xAA, mode, x, y, z, 1.0, 1.0, 1.0, lr, ud, bumpers, speed)


def test_the_sim_answers_its_identity_with_its_caps():
    board = MegaStandardSim()
    board.open()
    assert board.ask("s") == ["DEV: m caps=ext1,log,hostto,home,limits,tmc"]
    info = board.ask("#INFO")[0]
    assert info.startswith("#OK INFO fw=xyz_stage_mega proto=1 caps=ext1,") and "axes=XYZ" in info
    for word in ("x_tmc=1", "y_limits=1", "z_home=1", "x_ls1_end=0", "z_homed=0",
                 "log=1", "hostto_ms=0"):
        assert word in info.split(), word


def test_the_sim_wraps_a_move_past_16_bits_as_the_board_does():
    """`int x_steps = XAXIS_SIZE * XAXIS_DIST` on an AVR: past 32767 it
    wraps and the axis moves the other way (review R-2)."""
    assert _int16(32767) == 32767 and _int16(32768) == -32768 and _int16(40000) == -25536
    clock = Clock()
    board = MegaStandardSim(clock=clock, limits=(-10 ** 6, 10 ** 6))
    board.open()
    board.write(b"e")
    clock.run(0.05, board)
    board.write(b"1,1,1,0,6400.0,0,0,0.0,40000.0,0.0,0,1\n")
    clock.run(0.5, board)
    assert board.position[1] < 0


def test_the_sim_reads_the_frame_with_x_and_z_negated():
    clock = Clock()
    board = MegaStandardSim(clock=clock)
    board.open()
    board.write(b"e")
    clock.run(0.05, board)
    board.write(b"2,1,3,0,3200.0,0,0,10.0,20.0,30.0,0,1\n")
    clock.run(1.0, board)
    assert board.position == (-20, 20, -90)


def test_the_sim_jog_dead_man_and_the_stop_packet():
    clock = Clock()
    board = MegaStandardSim(clock=clock)
    board.open()
    board.write(b"e")
    clock.run(0.05, board)
    board.write(_jog(y=1.0))
    clock.run(0.2, board)
    assert board.moving
    clock.run(0.2, board)                        # no packet for > 250 ms
    assert not board.moving and board.manual_on
    board.write(_jog(y=1.0))
    clock.run(0.05, board)
    board.write(_jog(mode=0))
    clock.run(0.01, board)
    assert not board.moving and not board.manual_on


def test_the_sim_host_timeout_stops_then_disables_after_ten_seconds():
    clock = Clock()
    board = MegaStandardSim(clock=clock, limits=(-10 ** 7, 10 ** 7))
    board.open()
    assert board.ask("#HOSTTIMEOUT 500") == ["#OK HOSTTIMEOUT ms=500"]
    board.write(b"e")
    clock.run(0.05, board)
    board.write(b"1,1,1,0,1000.0,0,0,0.0,100000.0,0.0,0,1\n")
    clock.run(0.4, board)
    assert board.moving
    clock.run(0.3, board)                        # 700 ms of silence while moving
    lines = board.drain_lines()
    assert "#EVT FAULT host-timeout" in lines and not board.moving and board.enabled
    clock.run(10.1, board)
    assert any(l.startswith("#EVT FAULT host-timeout-disabled") for l in board.drain_lines())
    assert not board.enabled


def test_the_sim_without_hosttimeout_has_no_host_timeout():
    clock = Clock()
    board = MegaStandardSim(clock=clock, limits=(-10 ** 7, 10 ** 7))
    board.open()
    board.write(b"e")
    clock.run(0.05, board)
    board.write(b"1,1,1,0,1000.0,0,0,0.0,100000.0,0.0,0,1\n")
    clock.run(3.0, board)
    assert board.moving and not any("FAULT" in l for l in board.drain_lines())


def test_the_sim_parked_switch_jogs_slowly_and_learns_its_end_from_the_travel():
    clock = Clock()
    board = MegaStandardSim(clock=clock)
    board.park_on("Y", 2)
    board.open()
    board.write(b"e")
    clock.run(0.05, board)
    board.write(b"1,1,1,0,1000.0,0,0,0.0,100.0,0.0,0,1\n")    # a frame into it: refused
    assert any("REFUSED Y reason=limit-ls2-end-unknown:jog-off-it" in l
               for l in board.drain_lines())
    start = board.position[1]
    board.write(_jog(y=1.0, speed=3200.0))
    clock.run(1.0, board, beat=_jog(y=1.0, speed=3200.0))
    moved = board.position[1] - start
    assert 140 <= moved <= 180, moved             # capped at 160 counts/s
    clock.run(5.0, board, beat=_jog(y=1.0, speed=3200.0))
    lines = board.drain_lines()
    assert any(l.startswith("#EVT LIMIT Y ls2") and "learned=travel" in l for l in lines), lines
    assert board.limit_end("Y", 2) == 1
    assert board.position[1] - start <= 800


def test_the_sim_absent_hardware_is_inert():
    clock = Clock()
    board = MegaStandardSim(clock=clock, hardware={"Z": {"tmc": False, "home": False}})
    board.open()
    assert "#EVT FAULT tmc-missing Z" in board.drain_lines()
    board.write(b"e")
    clock.run(0.05, board)
    assert board.ask("#HOME Z") == ["#ERR HOME not-enabled"]
    board.set_hardware("Z", tmc=True)
    board.reboot()
    board.write(b"e")
    clock.run(0.05, board)
    assert board.ask("#HOME Z") == ["#ERR HOME no-home-sensor"]
    assert board.ask("#AXISCFG Z home=1") == ["#ERR AXISCFG busy"]   # enabled
    board.write(b"d")
    assert board.ask("#AXISCFG Z home=1") == ["#OK AXISCFG axis=Z limits=1 home=1 stored=1"]


def test_the_sim_goes_silent_but_keeps_moving():
    clock = Clock()
    board = MegaStandardSim(clock=clock, limits=(-10 ** 7, 10 ** 7))
    board.open()
    board.write(b"e")
    clock.run(0.05, board)
    board.write(b"1,1,1,0,1000.0,0,0,0.0,100000.0,0.0,0,1\n")
    clock.run(0.2, board)
    board.drain_lines()
    board.silent = True
    before = board.position[1]
    clock.run(0.5, board)
    assert board.ask("#HB") == [] and board.position[1] != before


def test_a_limit_on_one_axis_while_another_homes_is_not_a_step(make):
    """Only a Step in flight is stopped by a LIMIT: a stale Step clock plus a
    HOME on another axis is not one."""
    model, board = configured(*make(home_edge=40000, limits=(-50000, 50000)))
    model.z_dist = 10
    assert model.run("step", None).is_ok
    assert wait_for(lambda: not model.is_moving, 3.0)
    assert model.run("home_axis", None, ("Y",)).is_ok
    with Collected() as seen:
        model.gamepad.levels.update(axis_x=0.0)
        board.emit_raw("#EVT LIMIT X ls1 pos=-5 end=-1")
        assert wait_for(lambda: "Limit Reached" in seen.text("warning"), 2.0)
    assert "Step was stopped" not in seen.text("warning")
    assert model._homing["Y"] is not None, "the HOME on Y was stopped"
