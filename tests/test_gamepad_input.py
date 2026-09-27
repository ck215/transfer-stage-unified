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
    assert stage.lost == [GamepadInput.GAMEPAD_BIND_FAILED]


def test_a_raising_hook_faults_through_the_mixin_and_stops_the_pump():
    stage, pad = _stage({"axis_x": 0.5})
    faults = []
    stage._on_gamepad_fault = faults.append

    def _boom(levels, edges):
        raise OSError("link gone")
    stage._on_gamepad = _boom
    stage.set_drive("on")
    assert stage._gamepad_tick() is False
    assert faults and "link gone" in faults[0]


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
