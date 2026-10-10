"""The soft travel limit on the station side (X-14): the XYZ Stage (one
Teensy per axis, `SOFTLIMIT`) and the XYZ Stage (Mega) (cap `soft`,
`#SOFTLIMIT`, MEGA_STANDARD section 4).

The firmware is the guard (dev/firmware_sim proves it: nothing passes the
limit); the station reads each limit, shows it beside the µsteps and limit
readouts, sets it while the stage is disabled, refuses a Step that would
cross a referenced limit before anything is sent, and treats the board's
stop on the limit as a LIMIT: a Step in flight ends on every axis.

The station's protocol simulators predate the feature (their port is a
follow-up outside this round's write set), so each test grafts the
firmware's answer onto them: `SoftBoard` for an axis board, `SoftMega` for
the Mega.
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
    """`SOFTLIMIT [mm]` as xyz_stage_axis answers it (PROTOCOL.md, "Soft
    travel limit"), grafted on one axis's protocol simulator. LS1 is at the
    - end, so the limit lies `mm` above the reference."""

    def __init__(self, board, mm=0.0):
        self.board, self.mm = board, mm
        self.ref, self.ls1_mm, self.damaged, self.refuse = False, None, False, None
        board._cmd_softlimit = self.handle

    def reference(self, ls1_mm):
        self.ref, self.ls1_mm = True, ls1_mm

    def body(self):
        words = [f"mm={self.mm:.3f}", f"ref={int(self.ref)}"]
        if self.ref:
            words.append(f"ls1_mm={self.ls1_mm:.5f}")
        if self.ref and self.mm:
            words.append(f"limit_mm={self.ls1_mm + self.mm:.5f}")
        if self.damaged:
            words.append("damaged=1")
        return " ".join(words)

    def handle(self, command, args):
        if args:
            if self.refuse:
                return self.board._err(command, self.refuse)
            try:
                value = float(args[0])
            except ValueError:
                return self.board._err(command, "bad-arg")
            if self.board.enabled:
                return self.board._err(command, "busy")
            self.mm, self.damaged = value, False
            return self.board._ok(command, self.body() + " stored=1")
        return self.board._ok(command, self.body())


@pytest.fixture
def teensy():
    """XyzStage on SIM links, fast ramps; `soft` grafts SoftBoard on every
    axis before it opens. Always closed."""
    built = []

    def _make(soft=True, opened=True):
        stage = fast(XyzStage(sim=True, gamepad=None, port_x="SIM", port_y="SIM",
                              port_z="SIM"))
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
    """`#SOFTLIMIT A [counts]` and its `#INFO` keys as xyz_stage_mega answers
    them (MEGA_STANDARD section 3-4), grafted on the standard board's
    simulator built with the `soft` cap."""

    def __init__(self, board):
        self.board = board
        self.counts = {a: 0 for a in AXES}
        self.lim = {a: None for a in AXES}               # set: referenced, the limit in counts
        board._x_softlimit = self.handle
        real_ok = board._ok

        def ok(command, detail=""):
            if command == "INFO":
                detail += "".join(self.info_words(a) for a in AXES)
            real_ok(command, detail)

        board._ok = ok

    def info_words(self, axis):
        low, lim = axis.lower(), self.lim[axis]
        words = f" {low}_soft={self.counts[axis]} {low}_soft_ref={int(lim is not None)}"
        if lim is not None and self.counts[axis]:
            words += f" {low}_soft_lim={lim}"
        return words

    def handle(self, command, args):
        axis = args[0].upper() if args else ""
        if axis not in AXES or len(args) > 2:
            return self.board._err(command, "bad-arg")
        if len(args) == 2:
            if self.board.enabled:
                return self.board._err(command, "busy")
            self.counts[axis] = int(args[1])
        extra = " stored=1" if len(args) == 2 else ""
        self.board._ok(command, f"axis={axis} counts={self.counts[axis]} "
                                f"ref={int(self.lim[axis] is not None)}{extra}")


@pytest.fixture
def mega():
    """XyzStageMega on a standard board's simulator; `soft` adds the cap and
    grafts SoftMega. Always closed."""
    built = []

    def _make(soft=True):
        board = MegaStandardSim(caps=CAPS + (("soft",) if soft else ()))
        grafted = SoftMega(board) if soft else None
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
    soft.counts["X"], soft.lim["X"] = 44800, 1600      # 1 mm above where the axis stands (0)
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
