"""One axis of the XYZ Stage: the protocol-level simulator and the per-axis link.

The simulator (`AxisSimulator`) speaks the axis firmware protocol, version 1
(the lead's SPEC of 2026-10-09): one reply per command (`OK <CMD> k=v ...` or
`ERR <CMD> reason`), the `P` position stream, `EVT` lines, the identity
answer `DEV: x X`, the JOGV dead-man, the host-timeout heartbeat window, the
limit interlock and a HOME sequence. It is shaped like the pyserial handle
`SerialPort` drives, so a `"SIM"` port runs the real transport.

The link (`TeensyAxis`) is a `SerialPort` with a `state_key` per axis that
frames the protocol: replies matched to their request, `P` lines parsed, `EVT`
lines queued for the model.

The simulator tests run on a virtual clock; the link tests run the real
transport over the simulator.
"""
import re
import time

import pytest

from devices.serial_port import SerialPort
from devices import teensy_axis
from devices.teensy_axis import AxisSimulator, TeensyAxis

#: The SPEC's P line, field for field: pos and tgt %.5f mm, v %.4f mm/s,
#: then the seven 0/1 flags in this order.
P_LINE = re.compile(
    r"^P pos=-?\d+\.\d{5} tgt=-?\d+\.\d{5} v=-?\d+\.\d{4} en=[01] mv=[01] "
    r"ls1=[01] ls2=[01] home=[01] homed=[01]$")


class Clock:
    """A virtual monotonic clock for the simulator."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def advance(self, seconds):
        self.t += seconds


@pytest.fixture
def clock():
    return Clock()


def board(clock, **kwargs):
    sim = AxisSimulator(kwargs.pop("tag", "X"), clock=clock, **kwargs)
    sim.open()
    return sim


def p_lines(lines):
    return [line for line in lines if line.startswith("P ")]


def fields(line):
    return dict(token.split("=", 1) for token in line.split()[1:] if "=" in token)


# -- identity and the axis tag ------------------------------------------------

def test_identity_query_answers_dev_x_and_the_axis_tag(clock):
    sim = board(clock, tag="Y")
    assert sim.ask("S") == ["DEV: x Y"]
    # The station's scan sends lower-case `s`; commands are case-insensitive.
    assert sim.ask("s") == ["DEV: x Y"]


def test_an_untagged_board_answers_a_question_mark(clock):
    sim = board(clock, tag=None)
    assert sim.ask("S") == ["DEV: x ?"]
    assert sim.ask("AXIS") == ["OK AXIS axis=?"]


def test_the_axis_tag_is_queried_stored_and_refused_while_enabled(clock):
    sim = board(clock, tag="X")
    assert sim.ask("AXIS") == ["OK AXIS axis=X"]
    assert sim.ask("AXIS Z") == ["OK AXIS axis=Z stored=1"]
    assert sim.ask("S") == ["DEV: x Z"]
    sim.ask("ENABLE")
    assert sim.ask("AXIS Y") == ["ERR AXIS busy"]
    assert sim.ask("AXIS Q") == ["ERR AXIS bad-arg"]


# -- the commands the station relies on ----------------------------------------

def test_enable_disable_and_estop_answer_once_each(clock):
    sim = board(clock)
    assert sim.ask("ENABLE") == ["OK ENABLE enabled=1 microsteps=8"]
    assert sim.enabled
    assert sim.ask("DISABLE") == ["OK DISABLE enabled=0"]
    assert not sim.enabled
    sim.ask("ENABLE")
    reply = sim.ask("ESTOP")
    assert reply[0].startswith("OK ESTOP enabled=0 pos_mm=")
    assert not sim.enabled
    sim.refuse_enable = "driver-uart-not-ok"
    assert sim.ask("ENABLE") == ["ERR ENABLE driver-uart-not-ok"]
    assert not sim.enabled
    assert sim.ask("FROB") == ["ERR FROB unknown-command"]


def test_hosttimeout_hb_and_stream_are_bounded(clock):
    sim = board(clock)
    assert sim.ask("HOSTTIMEOUT 1000") == ["OK HOSTTIMEOUT ms=1000"]
    assert sim.ask("HOSTTIMEOUT 100") == ["ERR HOSTTIMEOUT bad-arg"]
    assert sim.ask("HB") == ["OK HB"]
    assert sim.ask("STREAM 20") == ["OK STREAM hz=20"]
    assert sim.ask("STREAM 51") == ["ERR STREAM bad-arg"]


def test_the_stream_sends_p_lines_in_the_contract_format_at_its_rate(clock):
    sim = board(clock)
    sim.ask("STREAM 20")
    clock.advance(1.0)
    lines = p_lines(sim.drain_lines())
    assert 19 <= len(lines) <= 21
    assert all(P_LINE.match(line) for line in lines), lines[:2]
    sim.ask("STREAM 0")
    clock.advance(1.0)
    assert p_lines(sim.drain_lines()) == []


def test_a_move_with_its_own_speed_reaches_its_target_and_reports_motion(clock):
    sim = board(clock)
    sim.accel_mm_s2 = 1000.0           # near-instant ramps, so the times are plain
    sim.ask("STREAM 50")
    sim.ask("ENABLE")
    reply = sim.ask("MOVE 0.5 0.25")
    assert reply == ["OK MOVE mm=0.50000 target_mm=0.50000 clamped=0"]
    clock.advance(1.0)
    moving = fields(p_lines(sim.drain_lines())[-1])
    assert moving["mv"] == "1" and moving["tgt"] == "0.50000"
    assert 0.2 < float(moving["pos"]) < 0.3       # 0.25 mm/s for 1 s
    clock.advance(1.5)
    done = fields(p_lines(sim.drain_lines())[-1])
    assert done["mv"] == "0" and done["pos"] == "0.50000"
    assert sim.ask("MOVETO -0.25")[0] == "OK MOVETO mm=-0.75000 target_mm=-0.25000 clamped=0"


def test_a_move_is_clamped_to_the_travel_and_refused_while_disabled(clock):
    sim = board(clock)
    assert sim.ask("MOVE 1") == ["ERR MOVE not-enabled"]
    sim.ask("ENABLE")
    assert sim.ask("MOVE 80")[0].endswith("clamped=1")
    assert sim.ask("MOVE 1") == ["ERR MOVE busy"]


def test_jogv_stops_by_itself_when_no_fresh_jogv_arrives(clock):
    """The dead-man: JOG_TIMEOUT_MS (250) after the last JOGV the axis
    decelerates to a stop."""
    sim = board(clock, home_edge_mm=None)
    sim.accel_mm_s2 = 1000.0
    sim.ask("ENABLE")
    assert sim.ask("JOGV 1.0") == ["OK JOGV mm_s=1.0000 clamped=0"]
    for _ in range(10):                # held: re-sent every 20 ms
        clock.advance(0.02)
        sim.ask("JOGV 1.0")
    assert sim.velocity_mm_s == pytest.approx(1.0)
    clock.advance(0.2)
    assert sim.velocity_mm_s == pytest.approx(1.0)  # inside the 250 ms
    clock.advance(0.1)
    assert sim.velocity_mm_s == 0.0
    assert sim.ask("JOGV 9")[0] == "OK JOGV mm_s=2.5000 clamped=1"


def test_the_host_timeout_stops_a_busy_axis_and_reports_a_fault(clock):
    sim = board(clock, home_edge_mm=None)
    sim.accel_mm_s2 = 1000.0
    sim.ask("HOSTTIMEOUT 1000")
    sim.ask("ENABLE")
    sim.ask("MOVE 10 0.5")
    clock.advance(0.9)
    assert sim.drain_lines() == [] and sim.moving
    clock.advance(0.2)
    assert sim.drain_lines() == ["EVT FAULT host-timeout"]
    clock.advance(0.1)
    assert not sim.moving
    # An idle axis never times out.
    clock.advance(5.0)
    assert sim.drain_lines() == []


def test_a_tripped_limit_stops_motion_toward_it_and_refuses_a_move_into_it(clock):
    sim = board(clock)
    sim.accel_mm_s2 = 1000.0
    sim.ask("HOSTTIMEOUT 5000")
    sim.ask("ENABLE")
    sim.ask("MOVE 5 2.5")
    clock.advance(0.2)
    sim.trip_limit(2)
    clock.advance(0.01)
    events = sim.drain_lines()
    assert any(line.startswith("EVT LIMIT ls2 tripped pos_mm=") and line.endswith("end=+1")
               for line in events), events
    assert not sim.moving
    assert sim.ask("MOVE 1") == ["ERR MOVE limit-ls2"]
    assert sim.ask("JOGV 0.5") == ["ERR JOGV limit-ls2"]
    assert sim.ask("MOVE -1")[0].startswith("OK MOVE")      # away from it is allowed


def test_home_zeroes_at_the_edge_approached_from_the_same_side(clock):
    sim = board(clock, home_edge_mm=1.2)
    sim.accel_mm_s2 = 1000.0
    sim.ask("HOSTTIMEOUT 5000")
    assert sim.ask("HOME") == ["ERR HOME not-enabled"]
    sim.ask("ENABLE")
    sim.ask("MOVE 3 2.5")              # start above the edge
    clock.advance(2.0)
    sim.drain_lines()
    lines = sim.ask("HOME")
    assert lines[0] == "OK HOME started"
    for _ in range(100):
        clock.advance(0.1)
        lines += sim.ask("HB")
    homed = [line for line in lines if line.startswith("EVT HOMED")]
    assert len(homed) == 1, lines
    assert homed[0].endswith(" pos=0")
    # The edge sits 1.2 mm above where the board booted (its old zero).
    assert float(fields(homed[0])["edge_mm"]) == pytest.approx(1.2, abs=0.001)
    assert any(line.startswith("EVT HOME phase=approach") for line in lines)
    assert sim.homed and sim.position_mm == 0.0
    # From below this time: the final approach is always upward, so the
    # second home finds the edge where the first one put zero.
    sim.ask("MOVE -2 2.5")
    clock.advance(2.0)
    lines = sim.ask("HOME")
    for _ in range(100):
        clock.advance(0.1)
        lines += sim.ask("HB")
    again = [line for line in lines if line.startswith("EVT HOMED")]
    assert float(fields(again[0])["edge_mm"]) == pytest.approx(0.0, abs=0.001)


def test_home_fails_cleanly_when_no_edge_lies_between_the_switches(clock):
    sim = board(clock, home_edge_mm=None, limits_mm=(-2.0, 2.0))
    sim.accel_mm_s2 = 1000.0
    sim.ask("HOSTTIMEOUT 5000")
    sim.ask("ENABLE")
    lines = sim.ask("HOME")
    for _ in range(60):
        clock.advance(0.1)
        lines += sim.ask("HB")
    assert any(line == "EVT HOME FAIL reason=no-edge" for line in lines), lines
    assert sim.enabled and not sim.moving and not sim.homed


def test_stop_aborts_a_home_and_zero_clears_homed(clock):
    sim = board(clock, home_edge_mm=0.5)
    sim.accel_mm_s2 = 1000.0
    sim.ask("ENABLE")
    sim.ask("HOME")
    clock.advance(0.05)
    assert "EVT HOME FAIL reason=stop" in sim.ask("STOP")
    clock.advance(0.1)                 # a STOP decelerates; HOME needs it idle
    sim.ask("HOME")
    for _ in range(30):
        clock.advance(0.1)
        sim.ask("HB")
    assert sim.homed
    assert sim.ask("ZERO") == ["OK ZERO pos_mm=0"]
    assert not sim.homed


def test_a_silent_board_answers_nothing_and_keeps_moving(clock):
    """The 2026-10-04 incident, simulated: the loop hangs, the step ISR does
    not. No reply, no P line, and neither the dead-man nor the host timeout
    (both in the loop) stops the axis."""
    sim = board(clock)
    sim.accel_mm_s2 = 1000.0
    sim.ask("STREAM 20")
    sim.ask("HOSTTIMEOUT 1000")
    sim.ask("ENABLE")
    sim.ask("JOGV 1.0")
    sim.silent = True
    clock.advance(2.0)
    assert sim.ask("ESTOP") == []
    assert sim.velocity_mm_s == pytest.approx(1.0)


def test_an_injected_fault_disables_the_axis_and_says_why(clock):
    sim = board(clock)
    sim.ask("ENABLE")
    sim.inject_fault("short s2ga=1")
    assert sim.drain_lines() == ["EVT FAULT short s2ga=1"]
    assert not sim.enabled


# -- the link ------------------------------------------------------------------

def test_the_link_is_a_serial_port_with_a_state_key_per_axis():
    link = TeensyAxis("SIM", "y")
    assert isinstance(link, SerialPort) and link.is_hardware
    assert link.axis == "Y" and link.state_key == "Axis Y"
    assert link.status == "closed"
    link.open()
    try:
        assert link.status == "simulated"
        assert link.simulator.tag == "Y"
    finally:
        link.close()
    with pytest.raises(ValueError):
        TeensyAxis("SIM", "W")


def test_a_request_returns_its_reply_with_fields_and_an_err_its_reason():
    link = TeensyAxis("SIM", "X")
    link.open()
    try:
        reply = link.request("AXIS")
        assert reply.ok and reply.command == "AXIS" and reply.fields == {"axis": "X"}
        assert link.tag == "X"
        refused = link.request("MOVE 1")
        assert not refused.ok and refused.reason == "not-enabled"
        assert link.last_reply("MOVE") is refused
        # Every line is on the wire exactly as the protocol writes it.
        assert list(link.writes)[-2:] == [b"AXIS\n", b"MOVE 1\n"]
    finally:
        link.close()


def test_a_request_to_a_silent_board_times_out_without_raising():
    link = TeensyAxis("SIM", "X")
    link.open()
    try:
        link.simulator.silent = True
        started = time.monotonic()
        reply = link.request("PING", timeout=0.1)
        assert reply.timed_out and not reply.ok
        assert time.monotonic() - started < 0.5
    finally:
        link.close()


def test_poll_parses_p_lines_queues_events_and_counts_garbage():
    link = TeensyAxis("SIM", "Z")
    link.open()
    try:
        link.simulator.accel_mm_s2 = 1000.0
        assert link.request("STREAM 50").ok
        assert link.request("ENABLE").ok
        link.simulator.inject_fault("overtemp ot=1 otpw=1")
        link.simulator.emit_raw("\x00garbage")
        deadline = time.monotonic() + 1.0
        while link.p is None and time.monotonic() < deadline:
            link.poll()
            time.sleep(0.01)
        link.poll()
        p = link.p
        assert p is not None and set(p) == {"pos", "tgt", "v", "en", "mv", "ls1",
                                             "ls2", "home", "homed"}
        assert isinstance(p["pos"], float) and isinstance(p["en"], int)
        assert link.p_time is not None and link.p_count >= 1
        events = [text for _at, _seq, text in link.take_events()]
        assert "FAULT overtemp ot=1 otpw=1" in events
        assert link.take_events() == []
        assert link.dropped == 1
    finally:
        link.close()


def test_a_real_port_handshake_reports_the_boards_tag(monkeypatch):
    """A port that is not "SIM" runs the full connect: open, the identity
    query `s`, `DEV: x X`. The simulator stands in for the board."""
    monkeypatch.setattr(TeensyAxis, "BOOTLOADER_WAIT", 0.0)
    sim = AxisSimulator("Y")
    link = TeensyAxis("/dev/cu.usbmodemTEST", "X", simulator=sim)
    link.open()
    try:
        assert link.wait_open(2.0)
        assert link.status == "verified"
        assert link.identity == "x Y"
        assert link.identity_tag == "Y"
        assert b"s\n" in list(sim.writes)
    finally:
        link.close()
    assert not sim.is_open


def test_a_new_link_epoch_forgets_what_the_last_board_said():
    link = TeensyAxis("SIM", "X")
    link.open()
    try:
        assert link.track_link() is True and link.epoch == 1
        assert link.track_link() is False
        link.request("AXIS")
        assert link.tag == "X"
        link.close()
        assert link.track_link() is False
        link.open()
        assert link.track_link() is True and link.epoch == 2
        assert link.tag is None and link.p is None and link.last_reply("AXIS") is None
    finally:
        link.close()


def test_the_simulated_link_is_a_serial_port_handle_with_every_method_it_calls():
    """SERIAL-9/SERIAL-20: a SIM handle missing a method SerialPort calls
    turns SIM into a different code path."""
    sim = AxisSimulator("X")
    for name in ("open", "close", "write", "read", "read_all", "readline",
                 "reset_input_buffer", "reset_output_buffer", "flush"):
        assert callable(getattr(sim, name)), name
    assert hasattr(sim, "in_waiting") and hasattr(sim, "is_open")
    assert not hasattr(sim, "fd")      # _close_serial's TIOCNXCL is for a real tty only


# -- the board's own words reach the station's log (lead, 2026-10-09) ------------

def test_the_boards_own_words_are_logged_and_only_garble_is_dropped(monkeypatch):
    logged = []
    monkeypatch.setattr(teensy_axis.events, "debug",
                        lambda title, message, **kw: logged.append((title, message)))
    link = TeensyAxis("SIM", "X")
    link.open()
    try:
        link.simulator.emit_raw("tmc2209 version=0x21 addr=0")
        link.simulator.emit_raw("\x00\xffgarble")
        deadline = time.monotonic() + 1.0
        while link.dropped == 0 and time.monotonic() < deadline:
            link.poll()
            time.sleep(0.01)
        assert ("Axis Board Says", "tmc2209 version=0x21 addr=0") in logged
        assert link.dropped == 1
        assert [t for t, _ in logged if t == "Line Dropped"] == ["Line Dropped"]
    finally:
        link.close()


def test_the_log_level_is_bounded_like_the_firmwares():
    sim = AxisSimulator("Y")
    assert sim.ask("LOG 2") == ["OK LOG level=2"] and sim.log_level == 2
    assert sim.ask("LOG 3") == ["ERR LOG bad-arg-0|1|2"] and sim.log_level == 2
