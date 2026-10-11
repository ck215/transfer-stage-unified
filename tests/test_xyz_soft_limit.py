"""The soft travel limit on the station side (X-14): the XYZ Stage (one
Teensy per axis, `SOFTLIMIT`) and the XYZ Stage (Mega) (cap `soft`,
`#SOFTLIMIT`, MEGA_STANDARD section 4).

The firmware is the guard (dev/firmware_sim proves it: nothing passes the
limit); the station reads each limit, shows it beside the µsteps and limit
readouts, sets it while the stage is disabled, refuses a Step that would
cross a referenced limit before anything is sent, and treats the board's
stop on the limit as a LIMIT: a Step in flight ends on every axis.

The station's protocol simulators model the feature as the firmware does
(`AxisSimulator`, `MegaStandardSim` with the `soft` cap; their own
behaviour is pinned at the end of this file), so these tests drive the
stage against the simulators as they are. `SoftBoard` and `SoftMega` only
set the board's EEPROM word and give it LS1's reference the short way.
"""
import pytest

from devices.mega_standard_sim import CAPS, MegaStandardPort, MegaStandardSim
from model.probe import ProbeMode
from model.xyz_stage import StageMode, XyzStage
from model.xyz_stage_mega import XyzStageMega

from test_xyz_stage import Collected, FakePad, fast, received, sim, wait_for

AXES = ("X", "Y", "Z")


# == the XYZ Stage (Teensy per axis) =========================================================

class SoftBoard:
    """A handle on one axis simulator's soft limit. LS1 is at the - end, so
    the limit lies `mm` above the reference."""

    def __init__(self, board, mm=0.0):
        self.board = board
        self.mm = mm

    @property
    def mm(self):
        return self.board.soft_mm

    @mm.setter
    def mm(self, value):
        self.board._soft_um = int(round(value * 1000))

    def reference(self, ls1_mm):
        """LS1 has given the reference at counter `ls1_mm`."""
        self.board.soft_reference(ls1_mm)

    @property
    def damaged(self):
        return self.board.soft_damaged

    @damaged.setter
    def damaged(self, value):
        if value:
            self.board.damage_soft()

    @property
    def refuse(self):
        return self.board.refuse_softlimit

    @refuse.setter
    def refuse(self, reason):
        self.board.refuse_softlimit = reason


@pytest.fixture
def teensy():
    """XyzStage on SIM links, fast ramps; without `soft` the boards answer
    SOFTLIMIT as an old firmware does (unknown-command). Always closed."""
    built = []

    def _make(soft=True, opened=True):
        stage = fast(XyzStage(sim=True, gamepad=None, port_x="SIM", port_y="SIM",
                              port_z="SIM"))
        if not soft:
            for a in AXES:                       # an old firmware: no such command
                sim(stage, a)._cmd_softlimit = None
        boards = {a: SoftBoard(sim(stage, a)) for a in AXES} if soft else None
        built.append(stage)
        if opened:
            stage.open()
            assert wait_for(lambda: all(stage._soft(a) is not None for a in AXES), 2.0)
        return stage, boards

    yield _make
    for stage in built:
        try:
            stage.estop()
        finally:
            stage.close()


def test_link_up_asks_each_board_and_seeds_the_entry_with_its_value():
    stage = fast(XyzStage(sim=True, gamepad=None, port_x="SIM", port_y="SIM", port_z="SIM"))
    boards = {a: SoftBoard(sim(stage, a)) for a in AXES}
    boards["X"].mm = 28.0
    stage.open()
    try:
        assert wait_for(lambda: all(stage._soft(a) is not None for a in AXES), 2.0)
        for axis in AXES:
            assert "SOFTLIMIT" in received(stage, axis), received(stage, axis)
        assert (stage.x_soft, stage.y_soft, stage.z_soft) == (28000, 0, 0)
        assert stage.x_soft_usteps == 44800            # 28000 um at 8 microsteps
        assert stage.soft_text == ("X 28000 µm from LS1, not referenced (touch LS1); "
                                   "Y none; Z none")
        assert stage.state["axes"]["X"]["soft"]["um"] == 28000
    finally:
        stage.estop()
        stage.close()


def test_a_board_whose_firmware_has_no_soft_limit_says_so_and_takes_none(teensy):
    stage, _ = teensy(soft=False)
    assert wait_for(lambda: stage._soft("X") == {"supported": False}, 2.0)
    assert "X not in its firmware" in stage.soft_text
    assert stage.run("set_soft_limits", {"x_soft": "0"}).value == "unchanged"
    result = stage.run("set_soft_limits", {"x_soft": "5000"})
    assert result.is_refused and "firmware has no soft limit" in result.reason
    assert not received(stage, "X", "SOFTLIMIT ")


def test_set_soft_limits_stores_what_changed_and_only_while_disabled(teensy):
    stage, boards = teensy()
    assert stage.run("set_mode", None, ("autonomous",)).is_ok
    refused = stage.run("set_soft_limits", None)
    assert refused.is_refused
    assert not any(received(stage, a, "SOFTLIMIT ") for a in AXES)
    assert stage.run("set_mode", None, ("disabled",)).is_ok
    with Collected() as seen:
        result = stage.run("set_soft_limits", {"x_soft": "28000"})
    assert result.is_ok and result.value == "set X", result
    assert received(stage, "X", "SOFTLIMIT ") == ["SOFTLIMIT 28.000"]
    assert not received(stage, "Y", "SOFTLIMIT ") and not received(stage, "Z", "SOFTLIMIT ")
    assert boards["X"].mm == 28.0
    assert "Soft Limit Set" in [e.title for e in seen.of("info")]
    assert stage.run("set_soft_limits", None).value == "unchanged"
    assert received(stage, "X", "SOFTLIMIT ") == ["SOFTLIMIT 28.000"]


def test_a_refused_set_names_the_axis(teensy):
    stage, boards = teensy()
    boards["Y"].refuse = "eeprom-verify-failed"
    result = stage.run("set_soft_limits", {"x_soft": "1000", "y_soft": "2000"})
    assert result.is_refused
    assert "Axis Y did not store its soft limit (eeprom-verify-failed)" in result.reason
    assert "Axis X was set" in result.reason


def test_a_step_that_would_cross_a_referenced_limit_is_refused_before_anything_is_sent(teensy):
    stage, boards = teensy(opened=False)
    boards["X"].mm = 3.0
    boards["X"].reference(0.0)                          # LS1 at counter 0: the limit at 3 mm
    stage.open()
    assert wait_for(lambda: (stage._soft("X") or {}).get("ref"), 2.0)
    assert wait_for(lambda: all(stage.axes[a].p is not None for a in AXES), 2.0)
    stage.x_dist, stage.y_dist = 5000, 1000
    result = stage.run("step", None)
    assert result.is_refused, result
    assert "Axis X would pass its soft limit" in result.reason and "3000 µm" in result.reason
    assert not any(received(stage, a, "MOVE") for a in AXES)
    assert stage.mode is StageMode.DISABLED
    stage.x_dist = 3000                                  # onto the limit, not past it
    assert stage.run("step", None).is_ok


def test_the_boards_stop_on_its_soft_limit_ends_a_step_on_every_axis(teensy):
    stage, _ = teensy()
    stage.x_dist, stage.y_dist, stage.full_speed = 2000, 2000, 100
    assert stage.run("step", None).is_ok
    assert wait_for(lambda: stage.is_moving, 1.0)
    before = {a: len(received(stage, a, "STOP")) for a in AXES}
    with Collected() as seen:
        sim(stage, "X").emit_raw("EVT SOFTLIMIT stopped pos_mm=0.50000 limit_mm=0.50000")
        assert wait_for(lambda: "Soft Limit Reached" in seen.text("warning"), 2.0)
    assert "stopped at its soft limit (0.50000 mm)" in seen.text("warning")
    assert "Step ends here" in seen.text("warning")
    assert "Limit Reached" not in [e.title for e in seen.of("warning")]   # not a switch
    for axis in AXES:
        assert wait_for(lambda a=axis: len(received(stage, a, "STOP")) > before[a], 1.0), axis


def test_reference_events_are_said_and_the_board_is_asked_again(teensy):
    stage, _ = teensy()
    asked = len(received(stage, "Z", "SOFTLIMIT"))
    with Collected() as seen:
        sim(stage, "Z").emit_raw("EVT SOFTLIMIT referenced ls1_mm=-25.00063 limit_mm=2.99937 "
                                 "travel_mm=28.000")
        sim(stage, "Z").emit_raw("EVT SOFTLIMIT lost reason=estop")
        assert wait_for(lambda: "Soft Limit Reference Lost" in seen.text("warning"), 2.0)
    assert "touched LS1 at -25.00063 mm: its soft limit is at 2.99937 mm" in seen.text("info")
    assert "lost its LS1 reference (estop)" in seen.text("warning")
    assert wait_for(lambda: len(received(stage, "Z", "SOFTLIMIT")) >= asked + 2, 1.0)


def test_a_soft_limit_refusal_is_said_in_the_operators_words(teensy):
    stage, _ = teensy()
    board = sim(stage, "X")
    board._cmd_move = lambda command, args: board._err(command, "soft-limit-unreferenced:touch-ls1")
    stage.x_dist = 100
    result = stage.run("step", None)
    assert result.is_refused and "has not touched LS1" in result.reason, result


def test_the_page_shows_each_soft_limit_beside_the_usteps_and_limit_readouts(teensy):
    stage, boards = teensy(opened=False)
    boards["Y"].mm = 12.5
    boards["Y"].reference(-1.0)
    stage.open()
    assert wait_for(lambda: (stage._soft("Y") or {}).get("ref"), 2.0)
    sections = {s["title"]: s for s in stage.schema["sections"]}
    config = sections["Configuration"]["elements"]
    attrs = [e.get("model_attr") or e.get("command") for e in config]
    for axis in "xyz":
        i = attrs.index(f"{axis}_soft")
        assert config[i]["type"] == "entry" and config[i]["unit"] == "µm"
        assert config[i + 1]["model_attr"] == f"{axis}_soft_usteps" and config[i + 1]["secondary"]
    button = config[attrs.index("set_soft_limits")]
    assert button["inputs"] == ["x_soft", "y_soft", "z_soft"]
    assert {"idle", "autonomous", "manual"} <= set(button["disabled_when"])
    assert "soft_text" in attrs
    assert "Y 12500 µm from LS1, at 11500.00 µm" in stage.soft_text
    assert wait_for(lambda: stage.axes["Y"].p is not None, 2.0)
    assert "soft limit at 11500.00 µm" in stage.switches_text


# == the XYZ Stage (Mega) =====================================================================

class SoftMega:
    """A handle on the standard board simulator's per-axis soft limit
    (`#SOFTLIMIT`, `#INFO` x_soft words): the EEPROM word in counts, and
    LS1's reference the short way."""

    def __init__(self, board):
        self.board = board
        self.counts = _Counts(board)

    def reference(self, axis, limit):
        """LS1 gave axis `axis` the reference that puts the limit at `limit`
        counts (LS1 at the - end: the limit lies `counts` above it)."""
        self.board.soft_reference(axis, limit - self.board.axes[axis].soft_counts)


class _Counts:
    def __init__(self, board):
        self.board = board

    def __getitem__(self, axis):
        return self.board.axes[axis].soft_counts

    def __setitem__(self, axis, value):
        self.board.axes[axis].soft_counts = int(value)


@pytest.fixture
def mega():
    """XyzStageMega on a standard board's simulator; `soft` adds the cap.
    Always closed."""
    built = []

    def _make(soft=True):
        board = MegaStandardSim(caps=CAPS + (("soft",) if soft else ()))
        grafted = SoftMega(board)
        stage = XyzStageMega(port=MegaStandardPort("SIM", simulator=board), gamepad=FakePad())
        built.append(stage)
        stage.open()
        assert wait_for(lambda: stage._ext_info, 2.0)
        return stage, board, grafted

    yield _make
    for stage in built:
        try:
            stage.estop()
        finally:
            stage.close()


def ext_lines(board, prefix="#"):
    return [line for _t, line in board.received if line.startswith(prefix)]


def test_a_mega_without_the_soft_cap_gets_no_soft_controls_and_no_softlimit_byte(mega):
    stage, board, _ = mega(soft=False)
    commands = {e.get("command") for s in stage.schema["sections"] for e in s["elements"]}
    assert "set_soft_limits" not in commands
    assert stage.run("set_soft_limits", None).is_refused
    assert stage.soft_text == "X not in this firmware; Y not in this firmware; Z not in this firmware"
    assert not ext_lines(board, "#SOFTLIMIT")


def test_the_mega_shows_its_soft_limits_and_sets_them_only_while_disabled(mega):
    stage, board, soft = mega()
    soft.counts["Z"] = 16000
    stage._ext_request("INFO")
    assert wait_for(lambda: stage.z_soft_um == 10000, 2.0)
    assert stage.z_soft_counts == 16000
    assert "Z 10000 µm from LS1, not referenced (touch LS1)" in stage.soft_text
    config = next(s for s in stage.schema["sections"] if s["title"] == "Configuration")["elements"]
    attrs = [e.get("model_attr") or e.get("command") for e in config]
    for axis in "xyz":
        i = attrs.index(f"{axis}_soft_um")
        assert config[i + 1]["model_attr"] == f"{axis}_soft_counts" and config[i + 1]["secondary"]
    assert "set_soft_limits" in attrs and "soft_text" in attrs
    assert stage.run("set_mode", None, ("autonomous",)).is_ok
    assert stage.run("set_soft_limits", {"x_soft_um": "28000"}).is_refused
    assert not ext_lines(board, "#SOFTLIMIT")
    assert stage.run("set_mode", None, ("disabled",)).is_ok
    with Collected() as seen:
        result = stage.run("set_soft_limits", {"x_soft_um": "28000"})
    assert result.is_ok and result.value == "set X", result
    assert ext_lines(board, "#SOFTLIMIT") == ["#SOFTLIMIT X 44800"]
    assert soft.counts["X"] == 44800
    assert "Soft Limit Set" in [e.title for e in seen.of("info")]
    assert stage.run("set_soft_limits", None).value == "unchanged"


def test_a_mega_step_that_would_cross_a_referenced_limit_is_refused_before_its_frame(mega):
    stage, board, soft = mega()
    board.axes["X"].ls_end[0] = -1                     # LS1 guards the - end: the limit lies +
    soft.counts["X"] = 44800
    soft.reference("X", 1600)                          # the limit 1 mm above where the axis stands (0)
    stage._ext_request("INFO")
    assert wait_for(lambda: (stage._soft("X") or {}).get("lim") == 1600, 2.0)
    frames = len(board.frames)
    stage.x_step_um = 0.625
    stage.x_dist_um = -2000                              # X is negated on the wire: +3200 counts
    result = stage.run("step", None)
    assert result.is_refused and "Axis X would pass its soft limit" in result.reason, result
    assert "1000 µm (1600 counts)" in result.reason
    assert len(board.frames) == frames
    stage.x_dist_um = -1000                              # onto it, not past it
    assert stage.run("step", None).is_ok


def test_the_megas_stop_on_a_soft_limit_ends_the_step(mega):
    stage, board, _ = mega()
    stage.y_dist_um, stage.full_speed_um_s = 5000, 100
    assert stage.run("step", None).is_ok
    with Collected() as seen:
        board.emit_raw("#EVT SOFTLIMIT Y stopped pos=800 lim=800")
        assert wait_for(lambda: "Soft Limit Reached" in seen.text("warning"), 2.0)
    assert "Step was stopped there, on every axis" in seen.text("warning")
    assert stage._zero_frame() in list(board.writes)
    with Collected() as seen:
        board.emit_raw("#EVT REFUSED X reason=soft-limit-unreferenced:touch-ls1")
        assert wait_for(lambda: "Move Refused" in seen.text("warning"), 2.0)
    assert "has not touched LS1" in seen.text("warning")
    assert stage.mode in (ProbeMode.AUTO, ProbeMode.DISABLED)


# == the simulators model the firmware (X-14, read from the sketches) ==========================
# The stage tests above run against these simulators, so they are held to the
# firmware's rules here: xyz_stage_axis PROTOCOL.md "Soft travel limit" for the
# axis board, MEGA_STANDARD.md section 4 for the Mega. A virtual clock, no stage.

import re
import struct

from devices.mega_standard_sim import JOG_FORMAT
from devices.teensy_axis import AxisSimulator


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def run(board, clock, seconds, beat=None, step=0.05):
    """Advance `seconds`, sending `beat` every `step` (a jog's dead-man);
    every line the board printed meanwhile."""
    lines, end = [], clock.t + seconds
    ask = board.ask
    while clock.t < end:
        clock.t = min(end, clock.t + step)
        lines += ask(beat) if beat else board.drain_lines()
    return lines + board.drain_lines()


def num(line, key):
    return float(re.search(rf"\b{key}=(-?[0-9.]+)", line).group(1))


# -- the axis board -------------------------------------------------------------------------

def axis_board(clock, mm=3.0):
    """An axis whose LS1 is 2 mm below the boot position, with `mm` of travel
    stored; open, disabled."""
    board = AxisSimulator("X", clock=clock, limits_mm=(-2.0, 25.0))
    board.open()
    if mm:
        assert board.ask(f"SOFTLIMIT {mm}")[0].endswith("stored=1")
    return board


def referenced_axis(clock, mm=3.0):
    """The same, enabled, after a real approach: jogged into LS1, which gives
    the reference, then jogged off it."""
    board = axis_board(clock, mm)
    board.ask("ENABLE")
    lines = run(board, clock, 3.0, beat="JOGV -2.5")
    assert any(line.startswith("EVT SOFTLIMIT referenced") for line in lines), lines
    run(board, clock, 0.6, beat="JOGV 2.5")
    assert board.ask("JOGV 0") and run(board, clock, 1.0) is not None
    return board


def test_axis_softlimit_is_set_queried_bounded_and_kept_across_a_reboot():
    clock = Clock()
    board = AxisSimulator("X", clock=clock)
    board.open()
    assert board.ask("SOFTLIMIT") == ["OK SOFTLIMIT mm=0.000 ref=0"]
    assert board.ask("SOFTLIMIT 28") == ["OK SOFTLIMIT mm=28.000 ref=0 stored=1"]
    for bad in ("-1", "100.5", "far"):
        assert board.ask(f"SOFTLIMIT {bad}") == ["ERR SOFTLIMIT bad-arg"], bad
    board.refuse_softlimit = "eeprom-verify-failed"
    assert board.ask("SOFTLIMIT 5") == ["ERR SOFTLIMIT eeprom-verify-failed"]
    board.refuse_softlimit = None
    board.reboot()
    board.drain_lines()
    assert board.ask("SOFTLIMIT") == ["OK SOFTLIMIT mm=28.000 ref=0"]    # EEPROM
    board.ask("ENABLE")
    assert board.ask("SOFTLIMIT 5") == ["ERR SOFTLIMIT busy"]
    board.ask("DISABLE")
    assert board.ask("SOFTLIMIT 0") == ["OK SOFTLIMIT mm=0.000 ref=0 stored=1"]


def test_axis_with_a_limit_is_jog_only_until_ls1_gives_the_reference():
    clock = Clock()
    board = axis_board(clock)
    board.ask("ENABLE")
    assert board.ask("MOVE 1") == ["ERR MOVE soft-limit-unreferenced:touch-ls1"]
    assert board.ask("MOVETO 1") == ["ERR MOVETO soft-limit-unreferenced:touch-ls1"]
    assert board.ask("HOME") == ["ERR HOME soft-limit-unreferenced:touch-ls1"]
    assert board.ask("MOVE -1") == ["ERR MOVE soft-limit-unreferenced:touch-ls1"]   # LS1's end is not known yet
    assert board.ask("JOGV 0.5")[0].startswith("OK JOGV")                          # a jog is allowed
    lines = run(board, clock, 0.1, beat="JOGV -2.5")
    assert not [line for line in lines if line.startswith("ERR")]
    lines = run(board, clock, 3.0, beat="JOGV -2.5")
    referenced = next(line for line in lines if line.startswith("EVT SOFTLIMIT referenced"))
    assert any(line.startswith("EVT LIMIT ls1") for line in lines)
    ref, limit = num(referenced, "ls1_mm"), num(referenced, "limit_mm")
    assert -2.01 < ref < -1.99 and abs(limit - ref - 3.0) < 1e-6 and num(referenced, "travel_mm") == 3.0
    reply = board.ask("SOFTLIMIT")[0]
    assert reply.startswith("OK SOFTLIMIT mm=3.000 ref=1 ls1_mm=") and num(reply, "limit_mm") == limit


def test_axis_without_a_limit_takes_the_reference_silently_and_needs_none():
    clock = Clock()
    board = axis_board(clock, mm=0)
    board.ask("ENABLE")
    assert board.ask("MOVE 1")[0].startswith("OK MOVE")           # no limit, nothing is refused
    run(board, clock, 2.0)
    lines = run(board, clock, 3.0, beat="JOGV -2.5")
    assert not [line for line in lines if "SOFTLIMIT" in line]    # no limit set: nothing to report
    assert board.ask("SOFTLIMIT") == ["OK SOFTLIMIT mm=0.000 ref=1 ls1_mm=%s" % (
        re.search(r"ls1_mm=(\S+)", board.ask("SOFTLIMIT")[0]).group(1))]


def test_axis_move_past_the_limit_is_shortened_onto_it_and_at_it_nothing_goes_out():
    clock = Clock()
    board = referenced_axis(clock)
    limit = num(board.ask("SOFTLIMIT")[0], "limit_mm")
    here = board.position_mm
    asked = board.ask("MOVE 10 2.5")
    reply = asked[0]
    assert "clamped=1" in reply and abs(num(reply, "target_mm") - limit) < 1e-3, reply
    lines = asked[1:] + run(board, clock, 6.0)           # the event follows the OK at once
    event = next(line for line in lines if line.startswith("EVT SOFTLIMIT clamped"))
    assert "cmd=MOVE" in event and abs(num(event, "target_mm") - (here + 10)) < 1e-3
    assert abs(num(event, "limit_mm") - limit) < 1e-3
    assert abs(board.position_mm - limit) < 1e-3 and not board.moving
    assert not [line for line in lines if line.startswith("EVT SOFTLIMIT stopped")]   # an arrival
    assert board.ask("MOVE 0.5") == ["ERR MOVE soft-limit"]
    assert board.ask("JOGV 1") == ["ERR JOGV soft-limit"]
    assert board.ask("MOVE -0.5")[0].startswith("OK MOVE")                # toward LS1: nothing changes


def test_axis_jog_out_decelerates_onto_the_limit_and_says_reached():
    clock = Clock()
    board = referenced_axis(clock)
    limit = num(board.ask("SOFTLIMIT")[0], "limit_mm")
    lines = run(board, clock, 8.0, beat="JOGV 2.5")
    assert [line for line in lines if line.startswith("EVT SOFTLIMIT reached")], lines
    assert abs(board.position_mm - limit) < 1e-3 and not board.moving
    assert not [line for line in lines if line.startswith(("EVT LIMIT", "EVT SOFTLIMIT stopped"))]


def test_axis_step_isr_halts_a_motion_about_to_pass_the_limit():
    clock = Clock()
    board = referenced_axis(clock)
    here = board.position_mm
    room = num(board.ask("SOFTLIMIT")[0], "limit_mm") - here
    assert room > 1.0
    assert "clamped=0" in board.ask(f"MOVE {room - 0.2:.4f} 1")[0]
    run(board, clock, 0.4)
    # The reference moves up, so the limit now lies before where it is heading.
    board.soft_reference(board.position_mm + 0.3 - 3.0)
    lines = run(board, clock, 4.0)
    stopped = next(line for line in lines if line.startswith("EVT SOFTLIMIT stopped"))
    limit = num(board.ask("SOFTLIMIT")[0], "limit_mm")
    assert abs(num(stopped, "limit_mm") - limit) < 1e-3 and abs(board.position_mm - limit) < 1e-3
    assert not board.moving


def test_axis_reference_survives_a_stationary_stop_and_zero_but_not_a_stop_while_turning():
    clock = Clock()
    board = referenced_axis(clock)
    ref = num(board.ask("SOFTLIMIT")[0], "ls1_mm")
    here = board.position_mm
    assert board.ask("ZERO")[0].startswith("OK ZERO")
    reply = board.ask("SOFTLIMIT")[0]
    assert "ref=1" in reply and abs(num(reply, "ls1_mm") - (ref - here)) < 1e-3   # the origin moved, the limit did not
    board.ask("ESTOP")                                   # standing still: the count is intact
    assert "ref=1" in board.ask("SOFTLIMIT")[0]
    board.ask("ENABLE")
    board.ask("JOGV 0.5")
    run(board, clock, 0.2, beat="JOGV 0.5")
    lines = board.ask("ESTOP") + run(board, clock, 0.1)
    assert "EVT SOFTLIMIT lost reason=estop" in lines, lines
    assert "ref=0" in board.ask("SOFTLIMIT")[0]
    board.ask("ENABLE")
    assert board.ask("MOVE 0.5") == ["ERR MOVE soft-limit-unreferenced:touch-ls1"]
    assert board.ask("MOVE -0.5")[0].startswith("OK MOVE")      # toward LS1, whose end is still known


def test_axis_new_limits_meaning_loses_the_reference_and_a_reboot_does_too():
    clock = Clock()
    board = referenced_axis(clock)
    board.ask("DISABLE")
    assert "EVT SOFTLIMIT lost reason=limits-changed" in board.ask("LIMITS NO") + board.drain_lines()
    board = referenced_axis(Clock())
    board.reboot()
    board.drain_lines()
    assert "ref=0" in board.ask("SOFTLIMIT")[0]


def test_axis_damaged_block_is_jog_only_until_softlimit_writes_it_again():
    clock = Clock()
    board = axis_board(clock)
    board.damage_soft()
    assert board.ask("SOFTLIMIT") == ["OK SOFTLIMIT mm=0.000 ref=0 damaged=1"]
    board.ask("ENABLE")
    assert board.ask("MOVE 1") == ["ERR MOVE soft-limit-eeprom-damaged:set-SOFTLIMIT"]
    assert board.ask("HOME") == ["ERR HOME soft-limit-eeprom-damaged:set-SOFTLIMIT"]
    assert board.ask("JOGV 0.5")[0].startswith("OK JOGV")
    board.ask("JOGV 0")
    run(board, clock, 1.0)
    board.reboot()
    board.drain_lines()
    assert board.ask("SOFTLIMIT") == ["OK SOFTLIMIT mm=0.000 ref=0 damaged=1"]    # it is EEPROM
    assert board.ask("SOFTLIMIT 3") == ["OK SOFTLIMIT mm=3.000 ref=0 stored=1"]
    assert board.ask("SOFTLIMIT") == ["OK SOFTLIMIT mm=3.000 ref=0"]


# -- the standard Mega ----------------------------------------------------------------------

def _frame(y_counts, speed=3200.0):
    return f"1,1,1,0,{speed},0,0,0.0,{y_counts},0.0,0,1\n".encode()


def _jog(y=0.0, speed=3200.0):
    return struct.pack(JOG_FORMAT, 0xAA, 1, 0.0, y, 0.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0, speed)


def mega_board(clock, counts=2000, caps=CAPS + ("soft",)):
    board = MegaStandardSim(clock=clock, caps=caps, limits=(-4000, 20000))
    board.open()
    if counts:
        assert board.ask(f"#SOFTLIMIT Y {counts}") == [
            f"#OK SOFTLIMIT axis=Y counts={counts} ref=0 stored=1"]
    return board


def referenced_mega(clock, counts=2000):
    """Enabled, after a real approach: Y jogged into LS1 (the reference),
    then off it."""
    board = mega_board(clock, counts)
    board.write(b"e")
    run(board, clock, 0.1)
    lines = run(board, clock, 2.5, beat=_jog(y=-1.0))
    assert any(line.startswith("#EVT SOFTLIMIT Y referenced") for line in lines), lines
    run(board, clock, 0.5, beat=_jog(y=1.0))
    board.write(_jog(y=0.0, speed=0.0))
    run(board, clock, 0.3)
    return board


def test_mega_without_the_soft_cap_has_no_softlimit_and_no_soft_words():
    board = mega_board(Clock(), counts=0, caps=CAPS)
    assert board.ask("#SOFTLIMIT Y 100") == ["#ERR SOFTLIMIT unknown-command"]
    assert "_soft" not in board.ask("#INFO")[0]


def test_mega_softlimit_is_set_queried_bounded_and_kept_across_a_reboot():
    clock = Clock()
    board = mega_board(clock, counts=0)
    assert board.ask("#SOFTLIMIT Y") == ["#OK SOFTLIMIT axis=Y counts=0 ref=0"]
    assert board.ask("#SOFTLIMIT Y 44800") == ["#OK SOFTLIMIT axis=Y counts=44800 ref=0 stored=1"]
    for bad in ("#SOFTLIMIT Y -1", "#SOFTLIMIT Y 160001", "#SOFTLIMIT Q 5", "#SOFTLIMIT Y 1 2"):
        assert board.ask(bad) == ["#ERR SOFTLIMIT bad-arg"], bad
    info = board.ask("#INFO")[0]
    assert "y_soft=44800 y_soft_ref=0" in info and "x_soft=0 x_soft_ref=0" in info
    board.reboot()
    board.drain_lines()
    assert board.ask("#SOFTLIMIT Y") == ["#OK SOFTLIMIT axis=Y counts=44800 ref=0"]
    board.write(b"e")
    run(board, clock, 0.1)
    assert board.ask("#SOFTLIMIT Y 5") == ["#ERR SOFTLIMIT busy"]


def test_mega_axis_with_a_limit_is_jog_only_until_ls1_gives_the_reference():
    clock = Clock()
    board = mega_board(clock)
    board.write(b"e")
    run(board, clock, 0.1)
    board.write(_frame(500))
    assert "#EVT REFUSED Y reason=soft-limit-unreferenced:touch-ls1" in run(board, clock, 0.1)
    assert board.ask("#HOME Y") == ["#ERR HOME soft-limit-unreferenced:touch-ls1"]
    lines = run(board, clock, 2.5, beat=_jog(y=-1.0))             # a jog is allowed, into LS1
    referenced = next(line for line in lines if line.startswith("#EVT SOFTLIMIT Y referenced"))
    ref = int(num(referenced, "ls1"))
    assert int(num(referenced, "lim")) == ref + 2000
    assert board.limit_end("Y", 1) == -1
    info = board.ask("#INFO")[0]
    assert "y_soft_ref=1" in info and f"y_soft_lim={ref + 2000}" in info
    reply = board.ask("#SOFTLIMIT Y")[0]
    assert f"ls1={ref} lim={ref + 2000}" in reply


def test_mega_stops_a_motion_on_the_limit_and_refuses_one_starting_out_from_it():
    clock = Clock()
    board = referenced_mega(clock)
    ref = board.axes["Y"].soft_ref - board.axes["Y"].origin
    limit = int(ref + 2000)
    board.write(_frame(30000))                                     # far past it
    lines = run(board, clock, 6.0)
    assert f"#EVT SOFTLIMIT Y stopped pos={limit} lim={limit}" in lines, lines
    assert board.position[1] == limit and not board.moving
    assert not [line for line in lines if line.startswith("#EVT LIMIT")]    # never a LIMIT line
    board.write(_frame(100))
    assert "#EVT REFUSED Y reason=soft-limit" in run(board, clock, 0.1)
    board.write(_jog(y=1.0))                                       # a jog out is refused too
    run(board, clock, 0.3, beat=_jog(y=1.0))
    assert board.position[1] == limit
    board.write(_frame(-300))                                      # toward LS1: nothing changes
    run(board, clock, 1.0)
    assert board.position[1] == limit - 300


def test_mega_arrival_exactly_on_the_limit_is_not_a_stop():
    clock = Clock()
    board = referenced_mega(clock)
    here = board.position[1]
    ref = board.axes["Y"].soft_ref - board.axes["Y"].origin
    board.write(_frame(int(ref + 2000 - here)))
    lines = run(board, clock, 4.0)
    assert board.position[1] == ref + 2000
    assert not [line for line in lines if "SOFTLIMIT" in line], lines


def test_mega_reference_moves_with_zero_and_is_lost_by_a_disable_while_turning():
    clock = Clock()
    board = referenced_mega(clock)
    before = int(num(board.ask("#SOFTLIMIT Y")[0], "lim"))
    here = board.position[1]
    assert board.ask("#ZERO Y") == ["#OK ZERO axis=Y"]
    assert int(num(board.ask("#SOFTLIMIT Y")[0], "lim")) == before - here
    board.write(_frame(3000, speed=800.0))
    run(board, clock, 0.2)
    assert board.moving
    board.write(b"d")
    assert "#EVT SOFTLIMIT Y lost reason=disabled" in run(board, clock, 0.1)
    assert "y_soft_ref=0" in board.ask("#INFO")[0]


def test_mega_damaged_entry_is_jog_only_until_softlimit_writes_it_again():
    clock = Clock()
    board = mega_board(clock, counts=0)
    board.damage_soft("Y")
    assert board.ask("#SOFTLIMIT Y") == ["#OK SOFTLIMIT axis=Y counts=0 ref=0 damaged=1"]
    assert "y_soft_damaged=1" in board.ask("#INFO")[0]
    board.write(b"e")
    run(board, clock, 0.1)
    board.write(_frame(500))
    assert "#EVT REFUSED Y reason=soft-limit-eeprom-damaged:set-SOFTLIMIT" in run(board, clock, 0.1)
    assert board.ask("#HOME Y") == ["#ERR HOME soft-limit-eeprom-damaged:set-SOFTLIMIT"]
    board.write(_jog(y=1.0))
    run(board, clock, 0.3, beat=_jog(y=1.0))
    assert board.position[1] > 0                                  # a jog moves it
    board.write(_jog(y=0.0, speed=0.0))
    run(board, clock, 0.3)
    board.write(b"d")
    assert board.ask("#SOFTLIMIT Y 1600") == ["#OK SOFTLIMIT axis=Y counts=1600 ref=0 stored=1"]
    assert "y_soft_damaged" not in board.ask("#INFO")[0]
