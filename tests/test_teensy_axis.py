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


# -- the axis firmware's learned limit ends, parked switch and host-silent disable ----------
# Ported from firmware/xyz_stage_axis (PROTOCOL.md "Safety behaviour"; dev/firmware_sim/sim.cpp).

def hold(sim, clock, command, seconds, every=0.1):
    """Send `command` every `every` s (a dead-man host) for `seconds`; return every line printed."""
    lines = []
    for _ in range(round(seconds / every)):
        lines += sim.ask(command)
        clock.advance(every)
    lines += sim.drain_lines()
    return lines


def enabled_board(clock, **kwargs):
    sim = board(clock, **kwargs)
    sim.ask("ENABLE")
    return sim


def test_a_trip_from_clear_while_moving_learns_that_direction_as_the_end(clock):
    sim = enabled_board(clock)
    sim.accel_mm_s2 = 1000.0
    sim.ask("HOSTTIMEOUT 5000")
    sim.ask("MOVE 5 2.5")
    clock.advance(0.2)
    sim.trip_limit(1)                    # LS1 closes while the carriage moves toward +
    clock.advance(0.01)
    events = sim.drain_lines()
    assert any(line.startswith("EVT LIMIT ls1 tripped pos_mm=") and line.endswith("end=+1")
               for line in events), events
    assert sim.limit_end(1) == 1
    assert sim.ask("MOVE 1") == ["ERR MOVE limit-ls1"]            # LS1 now guards +
    assert sim.ask("MOVE -1")[0].startswith("OK MOVE")


def test_a_release_while_moving_teaches_the_opposite_end(clock):
    sim = enabled_board(clock, parked_on=1)
    lines = hold(sim, clock, "JOGV 0.5", 1.5)     # off LS1 toward +, at the parked cap
    assert sim.limit_end(1) == -1
    assert not any(line.startswith("EVT LIMIT") for line in lines), lines
    sim.ask("JOGV 0")
    clock.advance(1.0)
    assert sim.ask("JOGV 0.5") == ["OK JOGV mm_s=0.5000 clamped=0"]     # the cap lifted with the release
    sim.ask("JOGV 0")
    clock.advance(2.0)
    sim.ask("MOVE -5 2.5")                                             # back toward LS1: halts on it
    clock.advance(4.0)
    events = sim.drain_lines()
    assert any(line.startswith("EVT LIMIT ls1 tripped") and line.endswith("end=-1")
               for line in events), events
    assert sim.ask("MOVE -1") == ["ERR MOVE limit-ls1"]


def test_a_retrip_after_reading_pressed_with_its_end_unknown_halts_and_teaches_nothing(clock):
    sim = enabled_board(clock)
    sim.accel_mm_s2 = 1000.0
    sim.ask("HOSTTIMEOUT 5000")
    sim.trip_limit(1)                    # pressed at rest: read pressed, end unknown
    clock.advance(0.01)
    sim.release_limit(1)                 # released at rest: no direction, nothing learned
    clock.advance(0.01)
    assert sim.limit_end(1) == 0
    sim.ask("MOVE 5 2.5")
    clock.advance(0.2)
    sim.trip_limit(1)                    # pressed again while moving: maybe chatter
    clock.advance(0.01)
    events = sim.drain_lines()
    assert any(line.startswith("EVT LIMIT ls1 tripped pos_mm=") and line.endswith("end=+0")
               for line in events), events
    assert sim.limit_end(1) == 0
    assert not sim.moving


def test_a_learned_end_halts_motion_toward_it_and_only_it(clock):
    sim = enabled_board(clock, limits_mm=(-2.0, 2.0))
    sim.ask("HOSTTIMEOUT 5000")
    sim.ask("MOVE 5 2.5")
    clock.advance(3.0)
    events = sim.drain_lines()
    assert any(line.startswith("EVT LIMIT ls2 tripped") and line.endswith("end=+1")
               for line in events), events
    assert sim.limit_end(2) == 1 and not sim.moving
    assert sim.ask("MOVE 1") == ["ERR MOVE limit-ls2"]


def test_a_parked_switch_refuses_moves_homes_and_tests_with_the_firmwares_reasons(clock):
    sim = enabled_board(clock, parked_on=1)
    off = "limit-ls1-end-unknown:jog-off-it"
    assert sim.ask("MOVE 1") == [f"ERR MOVE {off}"]
    assert sim.ask("MOVE -1") == [f"ERR MOVE {off}"]
    assert sim.ask("MOVETO 5") == [f"ERR MOVETO {off}"]
    assert sim.ask("HOME") == [f"ERR HOME {off}"]
    assert sim.ask("TEST COILS") == ["ERR TEST limit-tripped:jog-off-it"]
    assert sim.ask("STATUS")[0].count("ls1=1") == 1
    other = enabled_board(clock, tag="Y", parked_on=2)
    assert other.ask("MOVE -1") == ["ERR MOVE limit-ls2-end-unknown:jog-off-it"]


def test_a_parked_jog_is_capped_at_the_parked_speed(clock):
    sim = enabled_board(clock, parked_on=1)
    assert sim.ask("JOGV 0.5") == ["OK JOGV mm_s=0.1000 clamped=1"]
    clock.advance(0.1)
    assert sim.velocity_mm_s <= 0.1001
    sim.ask("JOGV 0")
    sim2 = enabled_board(clock, tag="Y", parked_on=1)
    assert sim2.ask("JOG 1") == ["OK JOG dir=1"]
    clock.advance(0.2)
    assert 0 < sim2.velocity_mm_s <= 0.1001


def test_the_parked_travel_halts_and_learns_the_end_it_ran_into(clock):
    sim = enabled_board(clock, parked_on=1)
    lines = hold(sim, clock, "JOGV -0.5", 7.0)
    hit = [line for line in lines if line.startswith("EVT LIMIT")]
    assert len(hit) == 1 and hit[0].endswith("end=-1 learned=travel travel_mm=0.500"), hit
    assert sim.position_mm == pytest.approx(-0.5, abs=0.002)
    assert sim.limit_end(1) == -1 and not sim.moving
    assert sim.ask("JOGV -0.5") == ["ERR JOGV limit-ls1"]
    assert sim.ask("MOVE 1") == ["ERR MOVE limit-ls1-end-unknown:jog-off-it"]   # still parked
    hold(sim, clock, "JOGV 0.5", 6.5)                                 # off it: 0.6 mm of pressed zone at 0.1 mm/s
    sim.ask("JOGV 0")
    clock.advance(0.5)
    assert sim.ask("MOVE 1")[0].startswith("OK MOVE")                  # released: confirmed, no longer parked


def test_a_stuck_switch_confines_the_axis_when_the_travel_runs_out_both_ways(clock):
    sim = enabled_board(clock)
    sim.trip_limit(1)                    # pressed at rest and never releasing
    clock.advance(0.01)
    hold(sim, clock, "JOGV -0.5", 7.0)
    lines = hold(sim, clock, "JOGV 0.5", 12.0)
    hit = [line for line in lines if line.startswith("EVT LIMIT")]
    assert len(hit) == 1 and hit[0].endswith("pressed_both_ways=1 travel_mm=0.500"), hit
    assert sim.ask("JOGV 0.5") == ["ERR JOGV limit-ls1-pressed-both-ways:check-switch"]
    assert sim.ask("JOG 1") == ["ERR JOG limit-ls1-pressed-both-ways:check-switch"]
    assert sim.ask("JOGV -0.5") == ["ERR JOGV limit-ls1"]
    assert sim.ask("LIMITS NO")[0].startswith("OK LIMITS limits=NO")  # forgets the ends, window restarts here
    assert sim.ask("JOGV 0.5") == ["OK JOGV mm_s=0.1000 clamped=1"]


def test_a_silent_host_after_a_host_timeout_switches_the_outputs_off(clock):
    sim = enabled_board(clock)
    sim.ask("HOSTTIMEOUT 250")
    sim.ask("MOVE 5 0.5")
    clock.advance(0.6)
    assert "EVT FAULT host-timeout" in sim.drain_lines()
    clock.advance(9.5)
    assert sim.drain_lines() == []                                    # holding, still energised
    clock.advance(0.6)
    lines = sim.drain_lines()
    assert len(lines) == 1 and re.fullmatch(
        r"EVT FAULT host-timeout-disabled silent_ms=10[0-9]{3}", lines[0]), lines
    assert sim.ask("MOVE 1") == ["ERR MOVE not-enabled"]
    assert sim.ask("ENABLE")[0].startswith("OK ENABLE")


def test_any_line_in_the_ten_seconds_cancels_the_disable(clock):
    sim = enabled_board(clock)
    sim.ask("HOSTTIMEOUT 250")
    sim.ask("MOVE 5 0.5")
    clock.advance(0.6)
    assert "EVT FAULT host-timeout" in sim.drain_lines()
    clock.advance(5.0)
    assert sim.ask("HB") == ["OK HB"]
    clock.advance(30.0)
    assert sim.drain_lines() == []
    assert "enabled=1" in sim.ask("STATUS")[0]


def test_info_carries_the_parked_and_host_silent_constants(clock):
    sim = board(clock)
    info = sim.ask("INFO")[0]
    assert info.endswith(" parked_jog_mm_s=0.1000 parked_travel_mm=0.500 host_silent_off_ms=10000"), info
    assert (AxisSimulator.PARKED_JOG_SPEED_MM, AxisSimulator.PARKED_TRAVEL_MM,
            AxisSimulator.HOST_SILENT_OFF_MS) == (0.1, 0.5, 10000)


def test_a_board_can_be_parked_by_a_hook_and_the_old_hooks_still_work(clock):
    sim = enabled_board(clock)
    assert "ls2=0" in sim.ask("STATUS")[0]
    sim.park_on(2)
    assert "ls2=1" in sim.ask("STATUS")[0]
    sim.ask("ENABLE")
    assert sim.ask("MOVE 1") == ["ERR MOVE limit-ls2-end-unknown:jog-off-it"]
    sim.reboot()
    assert sim.drain_lines()[0].startswith("EVT BOOT")
    assert sim.limit_end(2) == 0                                      # RAM: the ends are forgotten


def test_info_names_the_firmware_and_protocol_as_the_firmware_does(clock):
    info = board(clock, tag="Y").ask("INFO")[0]
    assert " fw=xyz_stage_axis proto=1 axis=Y " in info + " ", info
    assert "protocol=" not in info


# -- per-move acceleration: a vector Step's axes arrive together (2026-10-09) ---------

def _legs(clock, lines):
    """Each line on its own enabled board, all sent at the same instant (the
    station writes a Step's MOVEs back to back). Advances one tick at a time
    -> ([arrival seconds per board], [[(t, pos_mm)] per board]); arrival
    is the first tick at which the board no longer moves."""
    boards = [board(clock) for _ in lines]
    for sim in boards:
        sim.ask("ENABLE")
    started = clock.t
    for sim, line in zip(boards, lines):
        assert sim.ask(line)[0].startswith("OK MOVE"), line
    arrived, paths = [None] * len(boards), [[] for _ in boards]
    while None in arrived and clock.t - started < 10.0:
        clock.advance(AxisSimulator.STEP_S)
        for i, sim in enumerate(boards):
            paths[i].append((clock.t - started, sim.position_mm))
            if arrived[i] is None and not sim.moving:
                arrived[i] = clock.t - started
    return arrived, paths


def _off_the_line(paths, dx, dy):
    """The farthest the two axes' joint position strays from the straight
    line of (dx, dy), in mm, sampled every tick."""
    length = (dx * dx + dy * dy) ** 0.5
    return max(abs(x * dy - y * dx) / length
               for (_t, x), (_u, y) in zip(paths[0], paths[1]))


def test_a_move_with_its_own_acceleration_runs_the_same_trapezoid_scaled(clock):
    """The station's vector Step: a short diagonal (0.6, 0.2) mm at the full
    2.5 mm/s, the board's ACCEL 2.5 mm/s^2. With the speed share alone both
    axes ramp at the one ACCEL, so the shorter leg finishes far earlier and
    the path bows. With the acceleration share as well (|d_i|/|d| x ACCEL)
    each axis runs the same trapezoid scaled to its distance: they finish
    within one simulator tick and never leave the line by a microstep."""
    dx, dy, v, a = 0.6, 0.2, 2.5, AxisSimulator.DEFAULT_ACCEL_MM_S2
    length = (dx * dx + dy * dy) ** 0.5
    kx, ky = dx / length, dy / length
    microstep = 1.0 / 1600
    old, old_paths = _legs(clock, [f"MOVE {dx:.4f} {kx * v:.4f}", f"MOVE {dy:.4f} {ky * v:.4f}"])
    assert abs(old[0] - old[1]) > 0.2, old
    assert _off_the_line(old_paths, dx, dy) > 0.02
    new, new_paths = _legs(clock, [f"MOVE {dx:.4f} {kx * v:.4f} {kx * a:.4f}",
                                   f"MOVE {dy:.4f} {ky * v:.4f} {ky * a:.4f}"])
    assert abs(new[0] - new[1]) <= AxisSimulator.STEP_S + 1e-9, new
    assert _off_the_line(new_paths, dx, dy) <= microstep, _off_the_line(new_paths, dx, dy)
    assert new_paths[0][-1][1] == pytest.approx(dx) and new_paths[1][-1][1] == pytest.approx(dy)


def test_the_acceleration_argument_is_clamped_refused_and_for_that_move_only(clock):
    """As the firmware: clamped to ACCEL's bounds (0.25..25), refused with
    `bad-accel` when it is not a number above 0 (nothing moves), echoed in
    the reply, and the board's ACCEL is back once the move has stopped; a
    two-argument MOVE and its reply are unchanged."""
    sim = enabled_board(clock)
    assert sim.ask("MOVE 0.1 0.5 100") == [
        "OK MOVE mm=0.10000 target_mm=0.10000 clamped=0 accel_mm_s2=25.0000 accel_clamped=1"]
    clock.advance(1.0)
    assert sim.ask("MOVETO 0 0.5 0.01") == [
        "OK MOVETO mm=-0.10000 target_mm=0.00000 clamped=0 accel_mm_s2=0.2500 accel_clamped=1"]
    clock.advance(5.0)
    for bad in ("MOVE 1 0.5 0", "MOVE 1 0.5 -2", "MOVE 1 0.5 fast", "MOVETO 3 0.5 nan"):
        assert sim.ask(bad) == [f"ERR {bad.split()[0]} bad-accel"], bad
        assert not sim.moving
    assert " accel_mm_s2=2.5000 " in sim.ask("INFO")[0]
    # A STOP mid-move decelerates at the move's own rate, then the board's is back.
    sim.ask("MOVE 5 0.5 0.25")
    clock.advance(0.5)
    sim.ask("STOP")
    clock.advance(2.0)
    assert not sim.moving

    def timed(axis_board):
        reply = axis_board.ask("MOVE 0.3 2.5")[0]
        started = clock.t
        while axis_board.moving:
            clock.advance(AxisSimulator.STEP_S)
        return reply, clock.t - started

    fresh = enabled_board(clock)
    after, plain = timed(sim), timed(fresh)
    assert plain[0] == "OK MOVE mm=0.30000 target_mm=0.30000 clamped=0"
    assert after[0].startswith("OK MOVE mm=0.30000 ") and "accel" not in after[0]
    assert abs(after[1] - plain[1]) <= AxisSimulator.STEP_S + 1e-9, (after, plain)
