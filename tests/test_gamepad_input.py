"""MOD-1: the gamepad has a contract (`devices.gamepad.NEUTRAL`) and a mixin
(`model.gamepad_input.GamepadInput`).

`JoystickStage` below is a gamepad-driven device that is not a probe, defined
here and not in src/. It mixes in `GamepadInput` and writes only the hooks
(`_pumps_gamepad`, `_on_gamepad`, `_on_gamepad_lost`) - no bind, no gate, no
pump, no dropdown - and gets all of them. `tests/test_model_contract.py` also
runs every contract check against it.
"""
import time

import pytest

import schema as sch
from devices.gamepad import Gamepad, NEUTRAL
from model.base import Model
from model.gamepad_input import GamepadInput
from result import Refused

pytestmark = pytest.mark.loops


class JoystickStage(GamepadInput, Model):
    """A stage driven from a pad: one mode ("driving"), the levels it saw."""
    NAME = "Joystick Stage"
    IDENTITY = None
    NEEDS_PORT = False
    NEEDS_GAMEPAD = True
    GAMEPAD_RATE_HZ = 200.0

    def __init__(self, port=None, gamepad=None, sim=False):
        super().__init__()
        self.driving = False
        self.frames = []
        self.lost = []
        self._attach_gamepad(gamepad)

    @property
    def devices(self):
        return [self.gamepad] if self.gamepad is not None else []

    @property
    def schema(self):
        choice, log = self._gamepad_elements()
        return sch.schema(
            sch.section("Drive", choice,
                        sch.toggle("Drive:", "driving", "set_drive",
                                   "Driving (press to stop)", "Drive",
                                   on_args=["on"], off_args=["off"],
                                   disabled_when=("latched",))),
            sch.section("Diagnostics", log, tier=3, disclosure="Diagnostics"),
            self._safety_section())

    @property
    def mode_name(self):
        return "driving" if self.driving else "idle"

    @property
    def is_active(self):
        return self.driving

    def _expects_heartbeat(self):
        return False

    def set_drive(self, which):
        if which == "on":
            self._guard("Drive")
            if not self._is_gamepad_bound:
                raise Refused("Choose a gamepad first.")
            self.driving = True
        else:
            self.driving = False
        return self.mode_name

    def _halt_hardware(self):
        self.driving = False
        return True

    # -- the hooks, and nothing else of the gamepad's -----------------------
    @property
    def _pumps_gamepad(self):
        return self.driving

    def _on_gamepad(self, levels, edges):
        self.frames.append(dict(levels))

    def _on_gamepad_lost(self, reason):
        self.lost.append(reason)
        self.driving = False


class FakePad:
    """The pinned Gamepad surface, in the contract's channels."""
    status = "connected"
    EDGE_KEYS = Gamepad.EDGE_KEYS

    def __init__(self, levels=None):
        self.levels = dict(NEUTRAL, **(levels or {}))
        for key in self.EDGE_KEYS:
            self.levels[key] = 0
        self.edges = {}
        self.options = ["None", "Pad0"]
        self.log = ["[gamepad] bound"]
        self.is_bound = False
        self.is_gate_open = True
        self.name = None
        self.bind_ok = True
        self.bound_to = []

    def open(self):
        pass

    def close(self):
        pass

    def bind(self, name):
        self.bound_to.append(name)
        if name is None:
            self.is_bound, self.name = False, None
            return True
        if not self.bind_ok:
            return False
        self.is_bound, self.name = True, name
        return True

    def set_gate(self, is_open):
        self.is_gate_open = bool(is_open)

    def press(self, key, value=1):
        self.edges[key] = value

    def drain_edges(self):
        edges, self.edges = self.edges, {}
        return edges


def _stage(levels=None, bound=True):
    pad = FakePad(levels)
    stage = JoystickStage(gamepad=pad)
    if bound:
        stage.set_gamepad("Pad0")
    return stage, pad


def test_the_contract_is_one_generic_table():
    assert set(NEUTRAL) == {"axis_x", "axis_y", "trigger_left", "trigger_right",
                            "hat_x", "hat_y", "bumper_left", "bumper_right"}
    assert NEUTRAL["trigger_left"] == NEUTRAL["trigger_right"] == -1.0
    assert set(Gamepad.EDGE_KEYS) == {"hat_x", "hat_y", "bumper_left", "bumper_right"}


def test_it_binds_the_chosen_pad_on_open():
    pad = FakePad()
    stage = JoystickStage(gamepad=pad)
    stage._gamepad_name = "Pad0"          # what a name from Setup records
    stage.open()
    try:
        assert pad.bound_to == ["Pad0"] and stage.gamepad_name == "Pad0"
    finally:
        stage.close()


def test_the_commands_and_the_dropdown_come_from_the_mixin():
    stage, pad = _stage()
    assert stage.gamepad_options() == ["None", "Pad0"]
    assert stage.gamepad_log() == ["[gamepad] bound"]
    assert stage.run("set_gamepad", None, ("Pad0",)).is_ok
    dropdowns = [e for e in sch.elements(stage.schema) if e["type"] == "dropdown"]
    assert dropdowns and dropdowns[0]["command"] == "set_gamepad"
    assert dropdowns[0]["text"] == "Gamepad:"


def test_it_pumps_levels_only_while_driving():
    stage, pad = _stage({"axis_x": 0.5})
    stage._gamepad_tick()
    assert stage.frames == [], "nothing may pump outside the mode"
    stage.set_drive("on")
    stage._gamepad_tick()
    assert stage.frames[-1]["axis_x"] == 0.5


def test_a_closed_gate_holds_it_and_sends_one_neutral_tick():
    stage, pad = _stage({"axis_x": 0.5})
    stage.set_drive("on")
    stage._gamepad_tick()
    pad.set_gate(False)
    stage.frames.clear()
    for _ in range(5):
        stage._gamepad_tick()
    assert stage.frames == [{}], "one neutral tick on exit, not a stream"
    assert stage.driving, "the gate is not a stop"


def test_leaving_the_mode_sends_one_neutral_tick():
    stage, pad = _stage({"axis_x": 0.5})
    stage.set_drive("on")
    stage._gamepad_tick()
    stage.set_drive("off")
    stage.frames.clear()
    stage._gamepad_tick()
    stage._gamepad_tick()
    assert stage.frames == [{}]


def test_edges_are_merged_once_and_drained_in_every_mode():
    stage, pad = _stage()
    pad.press("hat_x", 1)
    stage._gamepad_tick()                 # not driving: drained and dropped
    stage.set_drive("on")
    stage._gamepad_tick()
    assert stage.frames[-1]["hat_x"] == 0, "a press made outside the mode fired"
    pad.press("bumper_left", 1)
    stage._gamepad_tick()
    stage._gamepad_tick()
    assert [f["bumper_left"] for f in stage.frames[-2:]] == [1, 0]


def test_nothing_pumps_while_latched():
    stage, pad = _stage({"axis_x": 0.5})
    stage.set_drive("on")
    stage._gamepad_tick()
    stage.frames.clear()
    stage._estop.set()                    # the latch alone, not the halt
    stage._gamepad_tick()
    stage._gamepad_tick()
    assert all(frame == {} for frame in stage.frames)


def test_losing_the_pad_drops_it_out_of_its_mode():
    stage, pad = _stage({"axis_x": 0.5})
    stage.set_drive("on")
    pad.is_bound = False
    stage._gamepad_tick()
    assert not stage.driving
    assert stage.lost == [GamepadInput.GAMEPAD_LOST]


def test_a_failed_bind_reverts_the_choice_and_drops_the_mode():
    stage, pad = _stage()
    stage.set_drive("on")
    pad.bind_ok = False
    with pytest.raises(Refused):
        stage.set_gamepad("Pad9")
    assert stage._gamepad_name == "Pad0" and not stage.driving
    # rb-pump P5: the mode is left BEFORE the bind is tried (a swap while
    # driving), so the failed bind finds nothing left to drop.
    assert stage.lost == [GamepadInput.GAMEPAD_SWAPPED]


def test_a_raising_hook_faults_through_the_mixin_and_the_pump_keeps_running():
    """rb-pump P1: the fault latch is the safety response and stays; the
    pump itself does not die with it (it used to return False and the loop
    returned, so no jog frame was ever sent again until a restart)."""
    stage, pad = _stage({"axis_x": 0.5})
    faults = []
    stage._on_gamepad_fault = faults.append

    def _boom(levels, edges):
        raise OSError("link gone")
    stage._on_gamepad = _boom
    stage.set_drive("on")
    assert stage._gamepad_tick() is True, "a fault must not stop the pump"
    assert faults and "link gone" in faults[0]


def _wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.005)
    return predicate()


def test_the_pump_loop_survives_a_fault_and_resumes_when_driven_again():
    """rb-pump P1, at the loop: one raising tick faults the model; the
    operator re-enters the mode and frames flow again on the SAME thread."""
    stage, pad = _stage({"axis_x": 0.5})
    faults = []
    raise_next = [True]
    original = stage._on_gamepad

    def _fault(reason):
        faults.append(reason)
        stage.driving = False            # the model's fault leaves the mode

    def _flaky(levels, edges):
        if raise_next[0]:
            raise_next[0] = False
            raise OSError("link glitched")
        original(levels, edges)
    stage._on_gamepad_fault = _fault
    stage._on_gamepad = _flaky
    stage.set_drive("on")
    stage._start_threads()
    try:
        assert _wait_for(lambda: faults), "the fault never reached the model"
        thread = stage._thread("gamepad")
        time.sleep(0.05)
        assert thread is not None and thread.is_alive(), "the pump died"
        stage.frames.clear()
        stage.set_drive("on")
        assert _wait_for(lambda: any(f.get("axis_x") == 0.5
                                     for f in stage.frames)), \
            "no frame after re-entering the mode"
    finally:
        stage._stop_threads()


def test_a_pump_that_keeps_raising_is_logged_rate_limited(monkeypatch):
    """rb-pump P1: a hook that raises on every tick is one log line a
    second, not one per tick; the fault hook still runs each time."""
    from events import events
    written = []
    real = events._write_file

    def _spy(severity, source, text, exception):
        written.append(text)
        return real(severity, source, text, exception)
    monkeypatch.setattr(events, "_write_file", _spy)
    events._debug_seen.pop((JoystickStage.NAME, "Gamepad Pump Failed"), None)
    stage, pad = _stage({"axis_x": 0.5})
    faults = []
    stage._on_gamepad_fault = faults.append

    def _boom(levels, edges):
        raise OSError("link gone")
    stage._on_gamepad = _boom
    stage.set_drive("on")
    for _ in range(20):
        assert stage._gamepad_tick() is True
    lines = [t for t in written if t.startswith("Gamepad Pump Failed")]
    assert len(lines) == 1, lines
    assert len(faults) == 20


class _JogPort:
    """The minimal pinned SerialPort surface (copied from tests/test_probe.py's
    FakePort, which this file does not own): records writes, raises on a
    predicate."""
    status = "simulated"

    def __init__(self):
        self.writes = []
        self.fail_on = None
        self.is_open = True

    def open(self):
        pass

    def close(self):
        self.is_open = False

    def write(self, payload, *, priority=False, abort_if=None):
        if abort_if is not None and abort_if():
            return False
        if self.fail_on is not None and self.fail_on(payload):
            raise OSError("the write did not leave the host")
        self.writes.append(payload)
        return True

    def read_line(self, timeout=None):
        return None


def test_a_probe_jogs_again_after_a_failed_jog_write_and_manual_reentry():
    """rb-pump P1, end to end on the real probe: one failed jog write faults
    it (the latch stays the safety response); the link comes back, the
    operator re-enters Manual, and jog frames flow again without a restart.
    At BASE: zero jog frames after re-entry -- the pump had returned."""
    from model.probe import PACKET_FORMAT, ProbeMode, StepperProbe
    import struct
    jog_size = struct.calcsize(PACKET_FORMAT)
    port = _JogPort()
    pad = FakePad({"axis_x": 0.5})
    probe = StepperProbe(port=port, gamepad=pad)
    probe.set_gamepad("Pad0")
    port.fail_on = lambda payload: len(payload) == jog_size
    probe._start_threads()
    try:
        probe.set_mode("manual")
        assert _wait_for(lambda: probe.mode is ProbeMode.FAULT), probe.mode
        port.fail_on = None
        probe.set_mode("manual")
        before = len(port.writes)
        assert _wait_for(lambda: sum(1 for w in port.writes[before:]
                                     if len(w) == jog_size) >= 3), \
            "no jog frame after re-entering manual"
        assert probe._thread("gamepad").is_alive()
    finally:
        probe._stop_threads()
        probe.disable()


def test_swapping_the_pad_while_driving_leaves_the_mode_before_the_bind():
    """rb-pump P5 (stop path): a rebind while pumping used to set the name
    and stay in the mode, so the next tick drove from the new pad. The
    original lab app ran a full stop first. Now: `_on_gamepad_lost(SWAPPED)`
    first, then the bind; the operator re-enters the mode by hand."""
    stage, pad = _stage({"axis_x": 0.5})
    order = []
    real_bind, real_lost = pad.bind, stage._on_gamepad_lost

    def _bind(name):
        order.append(("bind", name))
        return real_bind(name)

    def _lost(reason):
        order.append(("lost", reason))
        return real_lost(reason)
    pad.bind = _bind
    stage._on_gamepad_lost = _lost
    stage.set_drive("on")
    stage._gamepad_tick()
    assert stage.set_gamepad("Pad1") == "Pad1"
    assert order == [("lost", GamepadInput.GAMEPAD_SWAPPED), ("bind", "Pad1")]
    assert not stage.driving, "the swap left the model in its mode"
    stage.frames.clear()
    stage._gamepad_tick()
    stage._gamepad_tick()
    assert all(frame == {} for frame in stage.frames), stage.frames


def test_choosing_a_pad_while_not_driving_does_not_call_the_lost_hook():
    stage, pad = _stage()
    assert stage.set_gamepad("Pad1") == "Pad1"
    assert stage.lost == []


def test_a_probe_swapping_its_pad_in_manual_stops_before_any_jog_from_the_new_pad():
    """rb-pump P5 end to end: bind A, enter Manual, choose B. The probe
    leaves Manual through its own transition (zero frame and 'd' on the
    wire) before B is bound, and no jog from B follows. At BASE: the mode
    stayed Manual and the swap wrote nothing."""
    from model.probe import PACKET_FORMAT, ProbeMode, StepperProbe
    import struct
    jog_size = struct.calcsize(PACKET_FORMAT)
    port = _JogPort()
    pad = FakePad({"axis_x": 0.5})
    probe = StepperProbe(port=port, gamepad=pad)
    probe.set_gamepad("Pad0")
    probe.set_mode("manual")
    probe._gamepad_tick()
    before = len(port.writes)
    bound_at = []
    real_bind = pad.bind

    def _bind(name):
        bound_at.append(len(port.writes))
        pad.levels["axis_x"] = -0.75      # pad B is deflected
        return real_bind(name)
    pad.bind = _bind
    try:
        probe.set_gamepad("Pad1")
        assert probe.mode is not ProbeMode.MANUAL, probe.mode
        during = port.writes[before:bound_at[0]]
        assert b"d" in during, f"no disable before the new pad bound: {during}"
        assert b"0,0,0,0,0,0,0,0,0,0,0,0\n" in during, "no zero frame first"
        assert during.index(b"0,0,0,0,0,0,0,0,0,0,0,0\n") < during.index(b"d")
        for _ in range(3):
            probe._gamepad_tick()
        jogs_from_b = [w for w in port.writes[bound_at[0]:]
                       if len(w) == jog_size
                       and struct.unpack(PACKET_FORMAT, w)[2] == -0.75]
        assert jogs_from_b == [], "the new pad jogged without re-entering Manual"
    finally:
        probe.disable()


def test_a_refusal_is_not_a_fault():
    stage, pad = _stage({"axis_x": 0.5})
    faults = []
    stage._on_gamepad_fault = faults.append

    def _refuse(levels, edges):
        raise Refused("stopped")
    stage._on_gamepad = _refuse
    stage.set_drive("on")
    assert stage._gamepad_tick() is True and not faults


def test_the_pump_is_spawned_on_open_and_joined_at_close():
    stage, pad = _stage({"axis_x": 0.5})
    stage.open()
    stage.set_drive("on")
    deadline = time.monotonic() + 2.0
    while not stage.frames and time.monotonic() < deadline:
        time.sleep(0.005)
    thread = stage._thread("gamepad")
    stage.close()
    assert stage.frames, "the pump never ran"
    assert thread is not None and not thread.is_alive()


def test_the_pump_runs_at_the_declared_rate():
    stage, pad = _stage({"axis_x": 0.5})
    stage.GAMEPAD_RATE_HZ = 20.0
    stage.set_drive("on")
    stage._start_threads()
    time.sleep(0.3)
    stage._stop_threads()
    assert 2 <= len(stage.frames) <= 9, len(stage.frames)
