"""The XYZ Stage (Mega): the XYZ stage on one Mega 2560, all three axes, in
the Probe family (owner rulings 2026-10-09, docs/rebuild/MEGA_STANDARD.md).

It is the Stepper Probe's frame code and units (um views over counts at
0.625 um/count, step size x distance as the board moves it, the 32767-step
guard) on a standard board: its caps open the `#` channel, so the stop
paths of tests/test_mega_standard.py apply, and Zero and Home are on its
page. Safety first: the wire is never sent a `#` byte on the class's
assumption alone; then the units, the features and Setup's row.

Every test runs the real model over a real MegaStandardPort with the
protocol simulator behind it ("SIM").
"""
import json
import time

import pytest

from controller import setup as station_setup
from devices import serial_port as serial_device
from devices.mega_standard_sim import CAPS, LETTER, MegaStandardPort, MegaStandardSim
from model.probe import ProbeMode, StepperProbe, STEPPER_MAX_MOVE_STEPS
from model.xyz_stage import XyzStage
from model.xyz_stage_mega import XyzStageMega
from result import NeedsConfirm

from test_xyz_stage import Collected, FakePad, wait_for


@pytest.fixture
def make():
    """XyzStageMega on SIM (its own simulator), opened; always closed."""
    built = []

    def _make(opened=True, gamepad="pad", **board_kwargs):
        pad = FakePad() if gamepad == "pad" else gamepad
        if board_kwargs:
            port = MegaStandardPort("SIM", simulator=MegaStandardSim(**board_kwargs))
            stage = XyzStageMega(port=port, gamepad=pad)
        else:
            stage = XyzStageMega(sim=True, port="SIM", gamepad=pad)
        built.append(stage)
        if opened:
            stage.open()
            assert wait_for(lambda: stage._ext_reply("HOSTTIMEOUT") is not None, 2.0)
        return stage

    yield _make
    for stage in built:
        try:
            stage.estop()
        finally:
            stage.close()


def board(stage):
    return stage.port.simulator


def ext_lines(stage):
    return [line for _t, line in board(stage).received if line.startswith("#")]


# == the class and its row ===============================================================

def test_the_class_is_a_probe_named_xyz_stage_mega_answering_m():
    assert XyzStageMega.NAME == "XYZ Stage (Mega)"
    assert XyzStageMega.IDENTITY == "m" == LETTER
    assert issubclass(XyzStageMega, StepperProbe)
    assert XyzStageMega.NEEDS_PORT is True and XyzStageMega.NEEDS_GAMEPAD is True
    assert station_setup.resources_of(XyzStageMega) == ("port", "gamepad")
    assert XyzStageMega.ASSUMED_CAPS == frozenset(CAPS)


def test_it_is_registered_right_after_the_teensy_xyz_stage():
    names = list(station_setup.MODEL_TYPES)
    assert names[names.index("XYZ Stage") + 1] == "XYZ Stage (Mega)"
    assert station_setup.MODEL_TYPES["XYZ Stage (Mega)"] is XyzStageMega
    assert station_setup.MODEL_TYPES["XYZ Stage"] is XyzStage
    assert station_setup._key_for("XYZ Stage (Mega)") == "xyz_stage_mega"


@pytest.mark.parametrize("identity", ["m caps=ext1,log,hostto,home,limits,tmc", "m"])
def test_setups_scan_names_it_from_its_identity_letter(identity):
    probe = station_setup.PortProbe()
    assert probe._name_for_identity(identity) == "XYZ Stage (Mega)"
    assert probe._tag_for_identity(identity) is None


def test_sim_builds_a_standard_board_and_reads_its_caps(make):
    stage = make()
    assert isinstance(stage.port, MegaStandardPort)
    assert isinstance(board(stage), MegaStandardSim)
    assert stage.caps == frozenset(CAPS)
    state = stage.state
    assert state["link"]["status"] == "simulated"
    assert state["hardware_devices"] == ["MegaStandardPort"]
    json.dumps(state)
    json.dumps(stage.schema)


# == safety ===============================================================================

def test_the_schema_assumes_the_standard_but_the_wire_never_does(monkeypatch):
    """Its page is built before the handshake ends, so the schema assumes the
    standard's features; a board that has not said ext1 (UNVERIFIED: the
    identity query went unanswered) gets no `#` byte."""
    monkeypatch.setattr(serial_device.SerialPort, "BOOTLOADER_WAIT", 0.0)
    monkeypatch.setattr(serial_device.SerialPort, "HANDSHAKE_TIMEOUT", 0.3)
    sim = MegaStandardSim()
    sim.silent = True                         # no identity answer
    port = MegaStandardPort("/dev/cu.usbmodemMEGA", simulator=sim)
    stage = XyzStageMega(port=port, gamepad=None)
    commands = {e.get("command") for s in stage.schema["sections"] for e in s["elements"]}
    assert {"zero_axis", "home_axis"} <= commands
    stage.open()
    try:
        assert wait_for(lambda: port.status == "unverified", 3.0)
        assert stage.caps is None
        sim.silent = False
        time.sleep(0.6)
        assert not [p for p in sim.writes if p[:1] != b"\xaa" and b"#" in p]
        refused = stage.run("home_axis", None, ("X",))
        assert refused.is_refused and "not said what it can do" in refused.reason
    finally:
        stage.close()


def test_a_fault_latches_and_a_limit_stops_the_step(make):
    stage = make(limits=(-1500, 1500))
    stage.x_dist, stage.y_dist, stage.full_speed = 200, 3000, 3200
    with Collected() as seen:
        assert stage.run("step", None).is_ok
        assert wait_for(lambda: "Limit Reached" in seen.text("warning"), 3.0)
    assert "Step was stopped" in seen.text("warning")
    assert stage.run("set_mode", None, ("disabled",)).is_ok
    stage.y_dist = -1000                      # back off the switch
    assert stage.run("step", None).is_ok
    with Collected() as seen:
        board(stage).inject_fault("X", "short")
        assert wait_for(lambda: stage.is_estopped, 2.0)
    assert "short on axis X" in seen.text("error")
    assert wait_for(lambda: stage.mode is ProbeMode.DISABLED, 1.0)


def test_the_stop_sends_the_frame_protocols_stop_bytes(make):
    stage = make()
    assert stage.run("set_mode", None, ("autonomous",)).is_ok
    before = len(board(stage).writes)
    assert stage._halt_hardware() is True
    sent = list(board(stage).writes)[before:]
    assert sent == [stage._zero_frame(), b"d", b"k\n"]
    assert not board(stage).enabled


# == the Stepper Probe's frame code and units ==============================================

def test_the_frames_are_the_stepper_probes(make):
    stage = make()
    stepper = StepperProbe(port=None, gamepad=None, sim=True)
    for model in (stage, stepper):
        model.x_step_um, model.x_dist_um, model.full_speed_um_s = 6.25, 62.5, 500
    assert stage._frame_bytes(stage._frame()) == stepper._frame_bytes(stepper._frame())
    assert stage._jog_bytes({"axis_x": 0.5}) == stepper._jog_bytes({"axis_x": 0.5})
    assert stage.scale_note.startswith("0.625 µm per count")


def test_a_step_moves_the_board_step_size_times_distance(make):
    stage = make()
    stage.y_step_um = 6.25                    # 10 counts per unit
    stage.y_dist_um = 62.5                    # 10 units: 100 counts
    stage.full_speed_um_s = 2000
    assert stage.y_move_steps == 100
    assert stage.run("step", None).is_ok
    assert wait_for(lambda: board(stage).position[1] == 100, 2.0)
    assert wait_for(lambda: stage.position_y_um == 62.5, 1.0)


def test_the_16_bit_guard_applies(make):
    stage = make()
    too_far = (STEPPER_MAX_MOVE_STEPS + 1) * 0.625
    result = stage.run("_commit", {"z_dist_um": too_far})
    assert result.is_refused and "16 bits" in result.reason


# == the standard's features on its page ===================================================

def test_zero_and_home_are_on_its_page_with_the_board_readouts(make):
    stage = make()
    texts = {e.get("text") for s in stage.schema["sections"] for e in s["elements"]}
    for text in ("Zero X here", "Zero Y here", "Zero Z here", "Home X", "Home Y",
                 "Home Z", "Homed:", "Board:", "Limit switches:", "Homing:"):
        assert text in texts, text
    titles = [s["title"] for s in stage.schema["sections"]]
    assert titles.index("Zero and home") < titles.index("Configuration")
    assert wait_for(lambda: "xyz_stage_mega proto 1" in stage.board_text, 2.0)


def test_home_then_zero_on_the_stage(make):
    stage = make()
    with Collected() as seen:
        assert stage.run("home_axis", None, ("Z",)).is_ok
        assert wait_for(lambda: stage.homed_text == "X no, Y no, Z yes", 5.0)
    assert "Axis Homed" in [e.title for e in seen.of("info")]
    assert wait_for(lambda: not stage.is_moving, 2.0)
    with pytest.raises(NeedsConfirm):
        stage.zero_axis("Z")
    assert stage.run("zero_axis", None, ("Z", True)).is_ok
    assert stage.homed_text == "X no, Y no, Z no"


def test_an_absent_home_sensor_is_disabled_with_its_reason(make):
    stage = make(hardware={"X": {"home": False}, "Z": {"tmc": False}})
    assert wait_for(lambda: stage._ext_info, 2.0)
    assert stage.x_home_ready is False and stage.y_home_ready is True
    result = stage.run("home_axis", None, ("X",))
    assert result.is_refused and "No home sensor on X" in result.reason
    assert "Z: driver missing" in stage.board_text
