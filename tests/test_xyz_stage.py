"""The XYZ Stage: three axis boards on three serial links, one model.

Safety first (repo rule), in this order: the stop reaches all three axes on
the priority lane within the base's ESTOP_BUDGET, even past a wedged link;
one lost or faulted axis halts all three; an axis that stops reporting while
the stage is driven is a latched stop; powered modes enable all three or
none; a board on the wrong resource is refused. Then the features, at parity
with the Stepper Probe: modes, the autonomous vector Step, manual jog over
JOGV, D-pad and bumper steps, zeroing and homing, physical units.

Every test runs the real model over real `TeensyAxis` links. "SIM" links put
the protocol simulator behind the real transport; the link-loss tests put it
behind a port name, so the full connect, loss and reconnect path runs.
"""
import math
import threading
import time

import pytest

from devices.gamepad import Gamepad, NEUTRAL
from devices.teensy_axis import AxisSimulator, TeensyAxis
from events import events
from model.xyz_stage import StageMode, XyzStage
from result import Refused

AXES = ("X", "Y", "Z")


# -- doubles and helpers ---------------------------------------------------------

class FakePad:
    """The Gamepad surface the mixin and the stage touch, in the contract's
    channels (`devices.gamepad.NEUTRAL`)."""

    status = "bound"
    is_hardware = False
    EDGE_KEYS = Gamepad.EDGE_KEYS

    def __init__(self, **levels):
        self.levels = dict(NEUTRAL, **levels)
        self.edges = {}
        self.options = ["None", "Pad0"]
        self.log = []
        self.is_bound = True
        self.is_gate_open = True
        self.name = "Pad0"

    def open(self):
        pass

    def close(self):
        pass

    def bind(self, name):
        self.is_bound = name is not None
        return True

    def set_gate(self, is_open):
        self.is_gate_open = bool(is_open)

    def press(self, key, value=1):
        self.edges[key] = value

    def drain_edges(self):
        edges, self.edges = self.edges, {}
        return edges


class Collected:
    def __init__(self):
        self.seen = []

    def __enter__(self):
        events.clear()
        events.subscribe(self.seen.append)
        return self

    def __exit__(self, *exc):
        events.unsubscribe(self.seen.append)

    def of(self, severity):
        return [e for e in self.seen if e.severity == severity]

    def text(self, severity):
        return " | ".join(f"{e.title}: {e.message}" for e in self.of(severity))


def wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def sim(stage, axis):
    return stage.axes[axis].simulator


def received(stage, axis, prefix=""):
    """Every command line the axis board processed, oldest first."""
    return [line for _t, line in sim(stage, axis).received if line.startswith(prefix)]


def received_at(stage, axis, line):
    """When the board processed `line` (its clock is time.monotonic)."""
    return [t for t, seen in sim(stage, axis).received if seen == line]


def record_writes(stage):
    """{axis: [(payload, priority)]}: what each link was asked to write."""
    seen = {axis: [] for axis in AXES}
    for axis, link in stage.axes.items():
        real = link.write

        def write(payload, *, priority=False, abort_if=None, _real=real, _axis=axis):
            seen[_axis].append((bytes(payload), priority))
            return _real(payload, priority=priority, abort_if=abort_if)

        link.write = write
    return seen


def fast(stage):
    for link in stage.axes.values():
        if link.simulator is not None:
            link.simulator.accel_mm_s2 = 1000.0
    return stage


@pytest.fixture
def make():
    """XyzStage factory: SIM links, fast ramps, opened; always closed."""
    built = []

    def _make(gamepad=None, opened=True, **ports):
        ports = {name: ports.get(name, "SIM") for name in ("port_x", "port_y", "port_z")}
        stage = fast(XyzStage(sim=True, gamepad=gamepad, **ports))
        if opened:
            stage.open()
        built.append(stage)
        return stage

    yield _make
    for stage in built:
        try:
            stage.estop()
        finally:
            stage.close()


def armed(stage, mode="autonomous"):
    assert stage.run("set_mode", None, (mode,)).is_ok, stage.run("set_mode", None, (mode,))
    assert stage.mode_name == mode
    return stage


def stopped(stage):
    """The latched stop has finished (the latch closes first, the halt runs
    after it on its own worker)."""
    return (stage.is_estopped and stage.stop_confirmed is not None
            and stage.mode not in (StageMode.AUTO, StageMode.MANUAL))


# == SAFETY =========================================================================

# -- the class -------------------------------------------------------------------

def test_the_class_declares_three_tagged_ports_and_a_gamepad():
    assert XyzStage.NAME == "XYZ Stage"
    assert XyzStage.IDENTITY == "t"
    assert XyzStage.NEEDS_PORT is True and XyzStage.NEEDS_GAMEPAD is True
    assert XyzStage.RESOURCES == ("port_x", "port_y", "port_z", "gamepad")
    assert XyzStage.PORT_TAGS == {"port_x": "X", "port_y": "Y", "port_z": "Z"}
    for resource in ("port_x", "port_y", "port_z", "gamepad"):
        assert resource in XyzStage.__dict__["RESOURCES"]


def test_each_axis_is_its_own_device_in_state(make):
    stage = make()
    state = stage.state
    assert {"Axis X", "Axis Y", "Axis Z"} <= set(state["devices"])
    assert set(state["hardware_devices"]) == {"Axis X", "Axis Y", "Axis Z"}
    assert state["link"]["status"] == "simulated"


# -- the stop --------------------------------------------------------------------

def test_the_halt_sends_estop_to_all_three_axes_on_the_priority_lane(make):
    stage = armed(make())
    seen = record_writes(stage)
    assert stage._halt_hardware() is True
    for axis in AXES:
        assert (b"ESTOP\n", True) in seen[axis], seen[axis]
        assert not sim(stage, axis).enabled
    assert stage.mode is StageMode.DISABLED


def test_the_stop_reaches_every_healthy_axis_within_the_budget_past_a_wedged_link(make):
    """A write stuck on axis X (its transaction and write-in-flight locks
    held) must not hold the stop back from Y and Z: each axis gets its ESTOP
    on its own, and Y and Z land inside ESTOP_BUDGET."""
    stage = armed(make())
    link = stage.axes["X"]
    link._lock.acquire()
    link._write_io_lock.acquire()
    try:
        started = time.monotonic()
        confirmed = stage.estop()
        for axis in ("Y", "Z"):
            assert wait_for(lambda a=axis: received_at(stage, a, "ESTOP"), 1.0)
            landed = received_at(stage, axis, "ESTOP")[0] - started
            assert landed <= XyzStage.ESTOP_BUDGET, f"{axis} after {landed * 1000:.0f} ms"
        assert confirmed is False          # X's stop was still in flight
    finally:
        link._write_io_lock.release()
        link._lock.release()
    assert wait_for(lambda: received_at(stage, "X", "ESTOP"), 2.0)


def test_estop_confirms_in_sim_and_disables_all_three(make):
    stage = armed(make())
    assert stage.estop() is True
    assert stopped(stage)
    assert not any(sim(stage, axis).enabled for axis in AXES)


def test_a_stop_that_does_not_reach_one_axis_is_unconfirmed_and_faults(make):
    stage = armed(make())
    sim(stage, "Y").fail_writes = 1
    with Collected() as seen:
        assert stage._halt_hardware() is False
    assert stage.mode is StageMode.FAULT
    assert "axis Y" in stage.fault
    assert not sim(stage, "X").enabled and not sim(stage, "Z").enabled
    assert seen.of("error")


def test_close_leaves_all_three_axes_disabled(make):
    stage = armed(make())
    stage.close()
    for axis in AXES:
        assert not sim(stage, axis).enabled
        assert received(stage, axis, "ESTOP") or received(stage, axis, "DISABLE")


# -- one axis down halts all three ------------------------------------------------

@pytest.fixture
def plugged(monkeypatch):
    """A stage whose three links run the full connect path (open, identity
    query, loss, reconnect) with a simulator behind each port name."""
    monkeypatch.setattr(TeensyAxis, "BOOTLOADER_WAIT", 0.0)
    monkeypatch.setattr(TeensyAxis, "RECONNECT_BACKOFF", (0.05, 0.05))
    monkeypatch.setattr(TeensyAxis, "RECONNECT_EVERY", 0.05)
    built = []

    def _make(tags=None):
        tags = tags or {}
        links = {}
        for axis in AXES:
            board = AxisSimulator(tags.get(axis, axis))
            board.accel_mm_s2 = 1000.0
            links[axis] = TeensyAxis(f"/dev/cu.usbmodemTEST{axis}", axis, simulator=board)
        stage = XyzStage(port_x=links["X"], port_y=links["Y"], port_z=links["Z"],
                         gamepad=None)
        stage.open()
        built.append(stage)
        assert wait_for(lambda: all(l.status == "verified" for l in links.values()))
        return stage

    yield _make
    for stage in built:
        try:
            stage.estop()
        finally:
            stage.close()


def test_a_lost_axis_link_halts_all_three_and_leaves_the_mode_without_faulting(plugged):
    stage = armed(plugged())
    with Collected():
        sim(stage, "Y").unplug()
        assert wait_for(lambda: stage.mode is StageMode.DISABLED)
    for axis in ("X", "Z"):
        assert received(stage, axis, "ESTOP"), axis
        assert not sim(stage, axis).enabled
    assert not stage.is_faulted
    assert stage.state["link"]["status"] in ("lost", "reconnecting")
    refused = stage.run("set_mode", None, ("autonomous",))
    assert refused.is_refused
    sim(stage, "Y").replug()
    assert wait_for(lambda: stage.axes["Y"].status == "verified", 3.0)
    # Back, and reconfigured: a reconnected board may have rebooted.
    assert wait_for(lambda: received(stage, "Y").count("STREAM 20") >= 2, 2.0)
    armed(stage)


@pytest.mark.parametrize("axis", AXES)
def test_a_firmware_fault_on_one_axis_stops_all_three_and_latches(make, axis):
    stage = armed(make())
    with Collected() as seen:
        sim(stage, axis).inject_fault("short s2ga=1")
        # The stop goes first; the sentence is published once it has landed.
        assert wait_for(lambda: stopped(stage) and seen.of("error"))
    assert stage.mode is StageMode.DISABLED
    for other in AXES:
        assert not sim(stage, other).enabled
        assert received(stage, other, "ESTOP"), other
    errors = seen.text("error")
    assert f"Axis {axis}" in errors and "short" in errors


def test_a_host_timeout_reported_by_an_axis_stops_the_stage(make):
    stage = armed(make())
    sim(stage, "X").inject_fault("host-timeout")
    assert wait_for(lambda: stopped(stage))
    assert not any(sim(stage, axis).enabled for axis in AXES)


# -- board silence ---------------------------------------------------------------

def test_the_silence_threshold_is_one_second():
    """The Stepper Probe's board-silent rule: twenty P periods at 20 Hz."""
    assert XyzStage.BOARD_SILENT_AFTER == 1.0


def test_an_axis_that_stops_reporting_while_driven_latches_the_stage(make):
    stage = armed(make())
    stage.BOARD_SILENT_AFTER = 0.3
    with Collected() as seen:
        sim(stage, "Z").silent = True
        assert wait_for(lambda: stopped(stage) and seen.of("error"), 3.0)
    assert stage.stop_confirmed is False       # Z never read its ESTOP
    assert received(stage, "X", "ESTOP") and received(stage, "Y", "ESTOP")
    text = seen.text("error").lower()
    assert "not answering" in text and "axis z" in text
    stage.clear_estop(confirmed=True)
    refused = stage.run("set_mode", None, ("autonomous",))
    assert refused.is_refused and "not answering" in refused.reason
    assert not received(stage, "X", "ENABLE")[1:]   # never re-armed


def test_a_quiet_axis_while_disabled_is_not_a_stop(make):
    stage = make()
    stage.BOARD_SILENT_AFTER = 0.3
    assert wait_for(lambda: all(l.p is not None for l in stage.axes.values()))
    sim(stage, "X").silent = True
    time.sleep(0.6)
    assert not stage.is_estopped


# -- all three or none -----------------------------------------------------------

@pytest.mark.parametrize("refusing", AXES)
def test_one_refused_enable_disables_the_others_and_faults(make, refusing):
    stage = make()
    sim(stage, refusing).refuse_enable = "driver-uart-not-ok"
    result = stage.run("set_mode", None, ("autonomous",))
    assert result.is_refused
    assert f"Axis {refusing}" in result.reason and "driver-uart-not-ok" in result.reason
    assert stage.mode is StageMode.FAULT and stage.is_faulted
    assert not any(sim(stage, axis).enabled for axis in AXES)
    for axis in AXES:
        assert received(stage, axis, "DISABLE"), axis
    # The way out is the stop, as for the probes: a landed stop clears it.
    assert stage.estop() is True and not stage.is_faulted


def test_an_enable_that_gets_no_reply_is_a_refusal_too(make):
    stage = make()
    stage.axes["Y"].REPLY_TIMEOUT = 0.1
    sim(stage, "Y").refuse_enable = None
    real = sim(stage, "Y")._cmd_enable
    sim(stage, "Y")._cmd_enable = lambda command, args: None    # swallowed
    try:
        result = stage.run("set_mode", None, ("autonomous",))
    finally:
        sim(stage, "Y")._cmd_enable = real
    assert result.is_refused and "Axis Y" in result.reason
    assert not any(sim(stage, axis).enabled for axis in AXES)


# -- the right board on the right resource ---------------------------------------

def test_a_board_whose_tag_is_another_axis_is_refused_and_nothing_is_enabled(make):
    stage = make(opened=False)
    sim(stage, "X").tag = "Y"
    with Collected() as seen:
        stage.open()
        result = stage.run("set_mode", None, ("autonomous",))
    assert result.is_refused
    assert "port_x" in result.reason and "axis Y" in result.reason
    assert not any(received(stage, axis, "ENABLE") for axis in AXES)
    assert seen.of("error")


def test_an_untagged_board_is_refused(make):
    stage = make(opened=False)
    sim(stage, "Z").tag = None
    stage.open()
    result = stage.run("set_mode", None, ("autonomous",))
    assert result.is_refused and "no axis tag" in result.reason
    assert not any(received(stage, axis, "ENABLE") for axis in AXES)


def test_the_identity_answer_is_checked_on_a_real_port(plugged):
    stage = plugged(tags={"Y": "Z", "Z": "Y"})
    result = stage.run("set_mode", None, ("autonomous",))
    assert result.is_refused and "port_y" in result.reason
    assert not any(received(stage, axis, "ENABLE") for axis in AXES)


# -- the link is set up for the station --------------------------------------------

def test_open_sets_the_heartbeat_window_and_the_stream_on_every_axis(make):
    stage = make()
    for axis in AXES:
        assert wait_for(lambda a=axis: {"HOSTTIMEOUT 1000", "STREAM 20"}
                        <= set(received(stage, a)), 2.0), received(stage, axis)
    # And the heartbeat keeps every board inside it.
    assert wait_for(lambda: all(len(received(stage, a, "HB")) >= 2 for a in AXES), 2.0)


def test_a_board_without_the_v1_commands_is_refused(make):
    stage = make(opened=False)
    board = sim(stage, "Y")
    board._cmd_hosttimeout = lambda command, args: board._err(command, "unknown-command")
    stage.open()
    result = stage.run("set_mode", None, ("autonomous",))
    assert result.is_refused and "HOSTTIMEOUT" in result.reason
    assert not any(received(stage, axis, "ENABLE") for axis in AXES)


# -- leaving a mode ----------------------------------------------------------------

def test_leaving_a_mode_disables_all_three(make):
    stage = armed(make())
    assert stage.run("set_mode", None, ("disabled",)).is_ok
    assert stage.mode is StageMode.DISABLED
    for axis in AXES:
        assert received(stage, axis, "DISABLE")
        assert not sim(stage, axis).enabled


def test_a_disable_that_does_not_reach_an_axis_faults(make):
    stage = armed(make())
    sim(stage, "Z").fail_writes = 1
    stage.run("set_mode", None, ("disabled",))
    assert stage.mode is StageMode.FAULT and "axis Z" in stage.fault


def test_a_stop_landing_during_mode_entry_backs_the_entry_out(make):
    stage = make()
    real = stage.axes["Z"].request

    def enable_then_stop(command, *args, **kwargs):
        reply = real(command, *args, **kwargs)
        if command == "ENABLE":
            stage._halt_hardware()       # the Web watchdog, mid-entry
        return reply

    stage.axes["Z"].request = enable_then_stop
    result = stage.run("set_mode", None, ("autonomous",))
    assert result.is_refused
    assert stage.mode is StageMode.DISABLED
    assert not any(sim(stage, axis).enabled for axis in AXES)


# -- manual input is neutral when it must be ----------------------------------------

def test_the_jog_stream_goes_neutral_when_the_gate_closes(make):
    pad = FakePad(axis_x=0.5)
    stage = armed(make(gamepad=pad), "manual")
    assert wait_for(lambda: any(line != "JOGV 0" and line.startswith("JOGV")
                                for line in received(stage, "X", "JOGV")))
    pad.set_gate(False)
    for axis in AXES:
        assert wait_for(lambda a=axis: received(stage, a, "JOGV")[-1:] == ["JOGV 0"]), axis
    count = len(received(stage, "X", "JOGV"))
    time.sleep(0.2)
    assert len(received(stage, "X", "JOGV")) == count     # one neutral, not a stream


def test_a_silent_axis_holds_the_jog_at_neutral(make):
    """The probe's "Jog Held": while any axis is not answering, the sticks
    send nothing but neutral, whether or not the watchdog's stop has landed
    yet. The watchdog is switched off here to see the hold on its own (the
    latch has its own test above)."""
    pad = FakePad(axis_x=0.5)
    stage = armed(make(gamepad=pad), "manual")
    stage.BOARD_SILENT_AFTER = 0.3
    stage._watch_axes = lambda: None
    assert wait_for(lambda: received(stage, "X", "JOGV 0.2500"))
    silent_at = time.monotonic()
    sim(stage, "Y").silent = True
    assert wait_for(lambda: received(stage, "X", "JOGV")[-1] == "JOGV 0", 2.0)
    time.sleep(0.2)
    late = [t for t, line in sim(stage, "X").received
            if line.startswith("JOGV") and line != "JOGV 0" and t > silent_at + 0.4]
    assert not late
    assert received(stage, "X", "JOGV")[-1] == "JOGV 0"


def test_no_jog_reaches_an_axis_after_the_watchdogs_stop(make):
    pad = FakePad(axis_x=0.5)
    stage = armed(make(gamepad=pad), "manual")
    stage.BOARD_SILENT_AFTER = 0.3
    assert wait_for(lambda: received(stage, "X", "JOGV 0.2500"))
    sim(stage, "Y").silent = True
    assert wait_for(lambda: stopped(stage), 3.0)
    time.sleep(0.2)
    estop_at = received_at(stage, "X", "ESTOP")[0]
    after = [line for t, line in sim(stage, "X").received
             if t > estop_at and line.startswith("JOGV") and line != "JOGV 0"]
    assert not after and not sim(stage, "X").enabled


# == FEATURES ======================================================================

# -- modes, parity with the Stepper Probe ------------------------------------------

def test_modes_are_the_stepper_probes(make):
    stage = make()
    assert [m.value for m in StageMode] == ["disabled", "idle", "autonomous",
                                            "manual", "fault"]
    assert stage.mode is StageMode.DISABLED and not stage.is_energized
    assert stage.enable() == "idle" and stage.is_energized
    assert all(sim(stage, axis).enabled for axis in AXES)
    assert stage.disable() == "disabled"
    refused = stage.run("set_mode", None, ("manual",))
    assert refused.is_refused and "gamepad" in refused.reason.lower()
    assert not any(sim(stage, axis).enabled for axis in AXES)


def test_switching_powered_modes_does_not_re_enable(make):
    pad = FakePad()
    stage = armed(make(gamepad=pad), "autonomous")
    armed(stage, "manual")
    for axis in AXES:
        assert len(received(stage, axis, "ENABLE")) == 1


def test_the_idle_clock_powers_the_stage_down(make):
    stage = make()
    stage.INTERLOCK_TIMEOUT = 0.3
    stage.INTERLOCK_POLL_INTERVAL = 0.05
    armed(stage, "autonomous")
    assert wait_for(lambda: stage.mode is StageMode.DISABLED, 3.0)
    assert not any(sim(stage, axis).enabled for axis in AXES)


# -- the autonomous vector step -------------------------------------------------------

def test_a_step_gives_each_axis_its_share_of_the_vector_speed(make):
    stage = make()
    result = stage.run("step", {"x_dist": 300, "y_dist": -400, "z_dist": 0,
                                "full_speed": 1000})
    assert result.is_ok, result
    assert stage.mode is StageMode.AUTO
    # |d| = 500 um; X gets 300/500 of 1000 um/s, Y 400/500; Z does not move.
    assert received(stage, "X", "MOVE") == ["MOVE 0.3000 0.6000"]
    assert received(stage, "Y", "MOVE") == ["MOVE -0.4000 0.8000"]
    assert received(stage, "Z", "MOVE") == []
    assert stage.is_moving
    assert wait_for(lambda: not stage.is_moving, 3.0)
    assert stage.position[0] == pytest.approx(300.0, abs=0.7)
    assert stage.position[1] == pytest.approx(-400.0, abs=0.7)


def test_the_axes_of_a_step_arrive_together(make):
    stage = make()
    stage.run("step", {"x_dist": 200, "y_dist": 600, "z_dist": 300, "full_speed": 1400})
    finished = {}

    def note():
        for axis, link in stage.axes.items():
            p = link.p
            if axis not in finished and p and not p["mv"] and p["pos"] != 0:
                finished[axis] = time.monotonic()
        return len(finished) == 3

    assert wait_for(note, 3.0)
    assert max(finished.values()) - min(finished.values()) < 0.12


def test_a_step_is_refused_while_moving_and_repeats_once_arrived(make):
    stage = make()
    stage.run("step", {"x_dist": 1000, "full_speed": 500})
    again = stage.run("step")
    assert again.is_refused and "moving" in again.reason
    assert wait_for(lambda: not stage.is_moving, 4.0)
    assert stage.run("step").is_ok
    assert len(received(stage, "X", "MOVE")) == 2


def test_a_step_with_nothing_to_move_is_refused(make):
    stage = make()
    result = stage.run("step", {"x_dist": 0, "y_dist": 0, "z_dist": 0})
    assert result.is_refused
    assert stage.mode is StageMode.DISABLED


def test_a_step_one_axis_refuses_stops_the_others(make):
    stage = make()
    sim(stage, "Y").trip_limit(2)
    result = stage.run("step", {"x_dist": 500, "y_dist": 500, "full_speed": 500})
    assert result.is_refused and "Axis Y" in result.reason and "limit-ls2" in result.reason
    assert received(stage, "X", "STOP") and received(stage, "Z", "STOP")
    assert wait_for(lambda: not sim(stage, "X").moving, 1.0)


def test_the_distances_lock_while_autonomous(make):
    stage = armed(make(), "autonomous")
    result = stage.run("_commit", {"x_dist": 5})
    assert result.is_refused and "autonomous" in result.reason


# -- manual: JOGV from the sticks, steps from the D-pad and bumpers --------------------

def test_the_sticks_and_triggers_jog_by_velocity(make):
    pad = FakePad(axis_x=0.5, axis_y=-0.25, trigger_left=1.0)
    stage = make(gamepad=pad)
    stage.run("_commit", {"man_full_speed": 1000})
    armed(stage, "manual")
    assert wait_for(lambda: len(received(stage, "X", "JOGV 0.5000")) >= 5)
    assert received(stage, "Y", "JOGV -0.2500")
    assert received(stage, "Z", "JOGV 1.0000")      # left trigger is Z up
    pad.levels.update(axis_x=0.0, axis_y=0.0, trigger_left=-1.0, trigger_right=0.0)
    assert wait_for(lambda: received(stage, "Z", "JOGV -0.5000"))   # right trigger half: down
    assert wait_for(lambda: received(stage, "X", "JOGV")[-1] == "JOGV 0")


def test_the_jog_is_resent_inside_the_dead_man_window_while_held(make):
    pad = FakePad(axis_x=1.0)
    stage = armed(make(gamepad=pad), "manual")
    time.sleep(0.6)
    times = [t for t, line in sim(stage, "X").received if line.startswith("JOGV")]
    assert len(times) >= 10
    gaps = [b - a for a, b in zip(times, times[1:])]
    assert max(gaps) < 0.25 / 2


def test_dpad_and_bumpers_step_each_axis_by_its_step_size(make):
    pad = FakePad()
    stage = make(gamepad=pad)
    stage.run("_commit", {"x_step": 5, "y_step": 10, "z_step": 20,
                          "man_full_speed": 500})
    armed(stage, "manual")
    pad.press("hat_x", 1)
    assert wait_for(lambda: received(stage, "X", "MOVE") == ["MOVE 0.0050 0.5000"])
    pad.press("hat_y", -1)
    assert wait_for(lambda: received(stage, "Y", "MOVE") == ["MOVE -0.0100 0.5000"])
    pad.press("bumper_left", 1)
    assert wait_for(lambda: received(stage, "Z", "MOVE") == ["MOVE 0.0200 0.5000"])
    pad.press("bumper_right", 1)
    assert wait_for(lambda: received(stage, "Z", "MOVE")[-1:] == ["MOVE -0.0200 0.5000"])


# -- zeroing and homing --------------------------------------------------------------------

def test_zero_here_zeroes_one_axis(make):
    stage = make()
    stage.run("step", {"x_dist": 100, "y_dist": 100, "full_speed": 1000})
    assert wait_for(lambda: not stage.is_moving, 3.0)
    assert stage.run("zero_axis", None, ("X",)).is_ok
    assert wait_for(lambda: abs(stage.position[0]) < 0.01)
    assert stage.position[1] == pytest.approx(100.0, abs=0.7)
    assert received(stage, "X", "ZERO") == ["ZERO"] and not received(stage, "Y", "ZERO")


def test_home_runs_on_one_axis_and_reports_homed(make):
    stage = make()
    result = stage.run("home_axis", None, ("Y",))
    assert result.is_ok, result
    assert stage.mode is StageMode.AUTO
    assert received(stage, "Y", "HOME") == ["HOME"] and not received(stage, "X", "HOME")
    assert wait_for(lambda: stage.homed["Y"], 3.0)
    assert not stage.is_moving
    assert stage.position[1] == pytest.approx(0.0, abs=0.01)


def test_zeroing_a_homed_axis_asks_first(make):
    stage = make()
    stage.run("home_axis", None, ("X",))
    assert wait_for(lambda: stage.homed["X"], 3.0)
    ask = stage.run("zero_axis", None, ("X",))
    assert ask.needs_confirm and ask.command == "zero_axis"
    assert stage.run(ask.command, None, (*ask.args, True)).is_ok
    assert wait_for(lambda: not stage.homed["X"])


def test_home_all_homes_z_first_then_x_and_y_together(make):
    stage = make()
    for axis in AXES:
        sim(stage, axis).home_edge_mm = 0.6      # 0.6 s of approach each
    assert stage.run("home_all").is_ok
    assert wait_for(lambda: all(stage.homed.values()), 5.0)
    z_home = received_at(stage, "Z", "HOME")[0]
    x_home = received_at(stage, "X", "HOME")[0]
    y_home = received_at(stage, "Y", "HOME")[0]
    assert x_home - z_home > 0.5 and y_home - z_home > 0.5    # after Z finished
    assert abs(x_home - y_home) < 0.1                         # together
    assert wait_for(lambda: not stage.is_moving, 2.0)


def test_home_all_is_marked_provisional(make):
    stage = make()
    texts = [e.get("text", "") for s in stage.schema["sections"] for e in s["elements"]
             if e.get("command") == "home_all"]
    assert texts and "provisional" in texts[0].lower()


def test_a_stop_ends_home_all(make):
    stage = make()
    sim(stage, "Z").home_seek_mm_s = 0.5
    sim(stage, "Z").home_edge_mm = -5.0
    stage.run("home_all")
    assert wait_for(lambda: received(stage, "Z", "HOME"))
    stage.estop()
    time.sleep(0.3)
    assert not received(stage, "X", "HOME") and not received(stage, "Y", "HOME")
    assert not stage.is_moving


# -- physical units ------------------------------------------------------------------------

def test_positions_read_in_micrometres_with_a_microstep_line_beneath(make):
    stage = make()
    stage.run("step", {"x_dist": 125, "full_speed": 1000})
    assert wait_for(lambda: not stage.is_moving and stage.position[0] > 100, 3.0)
    assert stage.position_x == "125.00"
    assert stage.position_x_usteps == 200            # 125 um / 0.625 um
    elements = [e for s in stage.schema["sections"] for e in s["elements"]]
    by_attr = {e.get("model_attr"): (i, e) for i, e in enumerate(elements)}
    for axis in "xyz":
        index, readout = by_attr[f"position_{axis}"]
        assert readout["rail"] and readout["unit"] == "µm"
        assert readout["text"] == f"{axis.upper()}:"
        nxt = elements[index + 1]
        assert nxt["model_attr"] == f"position_{axis}_usteps" and nxt.get("secondary")


def test_speeds_are_micrometres_per_second_with_a_steps_line(make):
    stage = make()
    stage.run("_commit", {"full_speed": 1000, "man_full_speed": 250})
    elements = [e for s in stage.schema["sections"] for e in s["elements"]]
    for name, usteps in (("full_speed", 1600), ("man_full_speed", 400)):
        index = next(i for i, e in enumerate(elements) if e.get("model_attr") == name)
        assert elements[index]["unit"] == "µm/s"
        assert elements[index + 1].get("secondary")
        assert getattr(stage, elements[index + 1]["model_attr"]) == usteps


def test_position_source_duck_type_for_the_maps_and_rgb_analysis(make):
    stage = make()
    assert wait_for(lambda: stage.position_time is not None)
    x, y, z = stage.position
    assert all(isinstance(v, float) for v in (x, y, z))
    assert stage.position_age is not None and stage.position_age < 1.0
    assert len(stage.velocity) == 3
    assert stage.position_epoch >= 3
