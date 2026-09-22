"""The rebuilt Probe: mode machine, stop path, interlock, position, jog.

The bytes are pinned by `test_probe_frames.py`. This file is about the
behaviour the old audit paid for, ported from `tests/core/test_probe_mode.py`,
`tests/core/test_gamepad_interlock.py` and
`tests/hardware/test_serial10_power_down_truth.py`:

* **I-3.1** an armed mode implies a confirmed enable and a live interlock;
* **I-3.2** MANUAL implies a bound gamepad, checked *before* the enable;
* **I-3.3** an energized probe idle for the timeout is disabled, in every
  energized mode (D-3: manual included);
* **I-3.4** nothing can write a mode flag;
* **DC-6 / finding 5** Step stays available while AUTO; only `is_moving`
  refuses it;
* **SERIAL-10 / D-7** a board that cannot kill its coils says so once, and
  that fact is not folded into the stop result.
"""
import collections
import threading
import time

import pytest

from station.events import events
from station.models.probe import (ChuckPositioner, DCProbe, Probe, ProbeMode,
                                  StepperProbe)
from station.result import Refused

Write = collections.namedtuple("Write", "payload priority abort_if")


class FakePort:
    """A recording stand-in on the pinned SerialPort interface."""

    status = "simulated"

    def __init__(self, lines=()):
        self.writes = []
        self.calls = []
        self.lines = list(lines)
        self.fail_on = None          # predicate(payload) -> raise
        self.is_open = True
        self.opened = self.closed = 0

    def open(self):
        self.opened += 1

    def close(self):
        self.closed += 1
        self.is_open = False

    def write(self, payload, *, priority=False, abort_if=None):
        self.calls.append(Write(payload, priority, abort_if))
        if abort_if is not None and abort_if():
            return False
        if self.fail_on is not None and self.fail_on(payload):
            raise OSError("the write did not leave the host")
        self.writes.append(payload)
        return True

    def read_line(self, timeout=None):
        return self.lines.pop(0) if self.lines else None

    def calls_for(self, payload):
        return [c for c in self.calls if c.payload == payload]


class FakeGamepad:
    status = "connected"

    def __init__(self, levels=None, bound=True):
        self.levels = dict(levels or {})
        self.options = ["None", "Pad0", "Pad1"]
        self.log = ["[gamepad] bound"]
        self.is_bound = bound
        self.is_gate_open = True
        self.name = "Pad0" if bound else None
        self.bind_ok = True
        self.bound_to = None
        self.opened = self.closed = 0

    def open(self):
        self.opened += 1

    def close(self):
        self.closed += 1

    def bind(self, name):
        self.bound_to = name
        if name is None:
            self.is_bound, self.name = False, None
            return True
        if not self.bind_ok:
            return False
        self.is_bound, self.name = True, name
        return True

    def set_gate(self, is_open):
        self.is_gate_open = bool(is_open)


def make_probe(cls=StepperProbe, lines=(), levels=None, bound=True):
    port = FakePort(lines)
    gamepad = FakeGamepad(levels, bound=bound)
    probe = cls(port=port, gamepad=gamepad)
    return probe, port, gamepad


@pytest.fixture
def probe():
    probe, port, gamepad = make_probe()
    probe.port, probe.gamepad = port, gamepad
    yield probe
    probe._stop_threads()


ZERO = b"0,0,0,0,0,0,0,0,0,0,0,0\n"


class Collected:
    """Events published while the block runs."""

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


# -- I-3.4: the flags are derived, never stored -----------------------------

@pytest.mark.mode
@pytest.mark.parametrize("attr", ["mode", "is_enabled", "is_auto", "is_manual",
                                  "is_moving", "can_kill_coils", "position",
                                  "velocity", "position_age"])
def test_mode_flags_and_readouts_cannot_be_assigned(probe, attr):
    """The Web set_attr route used to write `manual_flag` straight onto the
    model, arming a mode with no gamepad check and no hardware enable."""
    with pytest.raises(AttributeError):
        setattr(probe, attr, True)


@pytest.mark.mode
def test_the_flags_still_read_as_booleans_for_the_schema(probe):
    probe.set_mode("manual")
    assert probe.is_manual is True
    assert probe.is_auto is False
    assert probe.is_enabled is True
    assert isinstance(probe.is_manual, bool)


@pytest.mark.mode
def test_mode_is_exactly_one_value(probe):
    probe.set_mode(ProbeMode.AUTO)
    assert probe.mode is ProbeMode.AUTO and not probe.is_manual
    probe.set_mode("manual")
    assert probe.mode is ProbeMode.MANUAL and not probe.is_auto
    probe.set_mode("disabled")
    assert probe.mode is ProbeMode.DISABLED
    assert not probe.is_auto and not probe.is_manual
    assert probe.mode_name == "disabled"


@pytest.mark.mode
def test_an_unknown_mode_is_refused_not_guessed(probe):
    with pytest.raises(Refused):
        probe.set_mode("autonomos")
    with pytest.raises(Refused):
        probe.set_mode("fault")


# -- I-3.2: MANUAL needs a pad, checked before the enable -------------------

@pytest.mark.mode
def test_manual_is_refused_without_a_gamepad():
    probe, port, _ = make_probe(bound=False)
    with pytest.raises(Refused):
        probe.set_mode("manual")
    assert probe.mode is ProbeMode.DISABLED
    assert b"e" not in port.writes, "the refusal must precede the enable byte"


@pytest.mark.mode
def test_losing_the_gamepad_while_manual_halts_and_disables(probe):
    """STEPPER-5. Clearing the flag alone left the coils energized."""
    probe.set_mode("manual")
    probe.gamepad.is_bound = False
    probe.port.writes.clear()
    probe._send_jog({})
    assert probe.mode is ProbeMode.DISABLED
    assert probe.is_enabled is False
    assert ZERO in probe.port.writes and b"d" in probe.port.writes


@pytest.mark.mode
def test_a_failed_bind_reverts_the_selection_and_stops(probe):
    probe.set_gamepad("Pad0")
    probe.set_mode("manual")
    probe.gamepad.bind_ok = False
    with pytest.raises(Refused):
        probe.set_gamepad("Pad9")
    assert probe._gamepad_name == "Pad0"
    assert probe.mode is ProbeMode.DISABLED


@pytest.mark.mode
def test_unbinding_the_gamepad_leaves_manual_mode(probe):
    probe.set_mode("manual")
    assert probe.set_gamepad("None") == "None"
    assert probe.mode is ProbeMode.DISABLED


# -- I-3.1: armed modes require a confirmed enable --------------------------

@pytest.mark.mode
def test_a_failed_enable_does_not_reach_an_armed_mode(probe):
    probe.port.fail_on = lambda payload: payload == b"e"
    with pytest.raises(Refused):
        probe.set_mode("autonomous")
    assert probe.mode is ProbeMode.DISABLED
    assert probe.is_enabled is False


@pytest.mark.mode
def test_an_armed_mode_has_a_live_interlock_generation(probe):
    probe.enable()
    assert probe.mode is ProbeMode.IDLE
    assert probe._interlock_thread is not None
    assert probe._interlock_thread.is_alive()
    assert probe._interlock_generation >= 1


@pytest.mark.mode
def test_every_mode_entry_starts_from_rest(probe):
    probe.set_mode("autonomous")
    assert probe.port.writes == [b"e", ZERO]


@pytest.mark.mode
def test_leaving_a_mode_disables_the_coils(probe):
    """D-2, owner ruling. Recorded as a test so a later 'fix' trips it."""
    probe.set_mode("manual")
    probe.port.writes.clear()
    probe.set_mode("disabled")
    assert probe.port.writes == [ZERO, b"d"]
    assert probe.is_enabled is False


@pytest.mark.mode
def test_an_unconfirmed_disable_is_a_fault_not_a_disabled_claim(probe):
    probe.enable()
    probe.port.fail_on = lambda payload: payload == b"d"
    probe.set_mode("disabled")
    assert probe.mode is ProbeMode.FAULT
    assert probe.is_faulted is True
    # Unknown reads as possibly-live, never as safe.
    assert probe.is_enabled is True


@pytest.mark.mode
def test_rearming_out_of_fault_resends_the_hardware_enable(probe):
    probe.enable()
    probe._enter_fault("unknown")
    probe.port.writes.clear()
    probe.enable()
    assert probe.port.writes[0] == b"e"
    assert probe.is_faulted is False


@pytest.mark.mode
def test_the_toggles_reach_set_mode_through_run(probe):
    """The schema's on_args/off_args are the whole mode API for a view."""
    assert probe.run("set_mode", args=["autonomous"]).is_ok
    assert probe.mode is ProbeMode.AUTO
    assert probe.run("set_mode", args=["disabled"]).is_ok
    assert probe.mode is ProbeMode.DISABLED


# -- the stop path ----------------------------------------------------------

@pytest.mark.estop
def test_halt_sends_the_zero_frame_d_and_k_all_on_the_priority_lane(probe):
    probe.enable()
    probe.port.calls.clear()
    assert probe.halt() is True
    assert [c.payload for c in probe.port.calls] == [ZERO, b"d", b"k\n"]
    assert all(c.priority for c in probe.port.calls), "the stop path must not queue"


@pytest.mark.estop
def test_the_stop_path_never_aborts_on_its_own_latch(probe):
    """`abort_if` supersedes *motion*; a stop is what the latch wants."""
    probe.enable()
    probe.port.calls.clear()
    probe.estop()
    assert [c.payload for c in probe.port.calls if c.abort_if is not None] == []
    assert b"d" in probe.port.writes


@pytest.mark.estop
def test_every_motion_write_carries_the_latch_check(probe):
    probe.set_mode("autonomous")
    probe.port.calls.clear()
    probe._send_move()
    probe._send_jog({})
    assert probe.port.calls, "no motion frames were sent"
    for call in probe.port.calls:
        assert call.abort_if == probe._estop.is_set


@pytest.mark.estop
def test_a_latched_probe_refuses_every_armed_transition_and_every_move(probe):
    probe.estop()
    for target in ("idle", "autonomous", "manual"):
        with pytest.raises(Refused):
            probe.set_mode(target)
    with pytest.raises(Refused):
        probe.step()
    assert probe.mode is ProbeMode.DISABLED


@pytest.mark.estop
def test_a_motion_write_that_loses_the_race_is_refused_not_reported_sent(probe):
    """The check that counts happens inside the transport's lock."""
    probe.set_mode("autonomous")
    probe._estop.set()                      # latched after the guard would pass
    with pytest.raises(Refused):
        probe._send_move()


@pytest.mark.estop
def test_the_dc_probe_reports_it_cannot_kill_coils_once(probe):
    dc, port, _ = make_probe(DCProbe)
    assert dc.can_kill_coils is False
    with Collected() as log:
        for _ in range(3):
            dc.halt()
    warnings = [e for e in log.of("warning") if "Power Down" in e.title]
    assert len(warnings) == 1 and warnings[0].count == 1, (
        "a standing property of the board is not an event to repeat")


@pytest.mark.estop
def test_the_coil_kill_limit_is_not_folded_into_the_stop_result():
    """SERIAL-10 / D-7: 'did the stop land' and 'can this board de-energize'
    are different questions. Folding them together marks every DC probe
    permanently unconfirmed and trains the operator to ignore the one signal
    that means something."""
    dc, port, _ = make_probe(DCProbe)
    assert dc.estop() is True
    assert dc.is_estopped is True
    assert dc.can_kill_coils is False


@pytest.mark.estop
def test_a_stop_whose_disable_does_not_land_is_a_fault(probe):
    probe.enable()
    probe.port.fail_on = lambda payload: payload == b"d"
    assert probe.halt() is False
    assert probe.is_faulted is True


@pytest.mark.estop
@pytest.mark.parametrize("cls", [StepperProbe, DCProbe, ChuckPositioner])
def test_estop_returns_inside_the_budget(cls):
    probe, port, _ = make_probe(cls)
    started = time.monotonic()
    probe.estop()
    assert time.monotonic() - started < 0.5
    assert probe.is_estopped is True


# -- stepping ---------------------------------------------------------------

@pytest.mark.mode
def test_step_works_repeatedly_while_auto(probe):
    """DC-6 / review finding 5: closed for the Web, then re-broken in the
    schema by gating the button on `disabled_when=("autonomous", ...)`."""
    probe.step()
    probe._moving_deadline = None            # the move arrived
    probe.port.writes.clear()
    probe.step()
    assert probe.mode is ProbeMode.AUTO
    assert len(probe.port.writes) == 1, "the second step did not reach the wire"
    assert probe.port.writes[0].endswith(b",0,1\n")


@pytest.mark.schema
def test_the_step_button_is_not_gated_off_by_autonomous(probe):
    from station import schema as sch
    element = next(e for e in sch.elements(probe.schema)
                   if e.get("command") == "step")
    assert sch.is_enabled(element, "autonomous") is True
    assert sch.is_enabled(element, "manual") is False


@pytest.mark.mode
def test_step_is_refused_only_while_moving(probe):
    probe.step()
    assert probe.is_moving is True
    with pytest.raises(Refused):
        probe.step()


@pytest.mark.mode
def test_is_moving_expires_by_deadline(probe):
    probe.STEP_SETTLE = 0.05
    probe.step()
    assert probe.is_moving is True
    time.sleep(0.12)
    assert probe.is_moving is False
    # Ending the move does not leave autonomous mode.
    assert probe.mode is ProbeMode.AUTO


@pytest.mark.mode
def test_motion_extends_the_deadline_and_counts_as_activity(probe):
    probe.STEP_SETTLE = 0.2
    probe.step()
    probe._activity_time = 0.0
    probe._note_position((1, 2, 3))
    assert probe.is_moving is True
    assert probe._activity_time > 0.0, "real motion must count as activity"


@pytest.mark.mode
def test_a_plain_mode_entry_is_not_moving(probe):
    probe.set_mode("autonomous")
    assert probe.is_moving is False
    probe.set_mode("manual")
    assert probe.is_moving is False


# -- the idle interlock -----------------------------------------------------

@pytest.mark.loops
def test_the_interlock_gets_a_fresh_event_each_arming(probe):
    """STEPPER-7. One reused Event meant a disable silenced every later
    arming: the next enable's thread returned on its first tick and the probe
    ran energized with no interlock at all."""
    probe.enable()
    first, first_generation = probe._interlock_stop, probe._interlock_generation
    probe.disable()
    assert first.is_set()

    probe.enable()
    assert probe._interlock_stop is not first
    assert not probe._interlock_stop.is_set()
    assert probe._interlock_generation > first_generation
    assert probe._interlock_thread.is_alive()


@pytest.mark.loops
@pytest.mark.parametrize("target", ["manual", "autonomous"])
def test_the_interlock_fires_on_real_inactivity_in_every_energized_mode(probe, target):
    """D-3 (manual included) and STEPPER-6 / DC-1 (stepping no longer defers)."""
    probe.INTERLOCK_POLL_INTERVAL = 0.01
    probe.INTERLOCK_TIMEOUT = 0.05
    probe.set_mode(target)
    deadline = time.monotonic() + 3.0
    while probe.is_enabled and time.monotonic() < deadline:
        time.sleep(0.01)
    assert probe.mode is ProbeMode.DISABLED, f"{target} never idled out"


@pytest.mark.loops
def test_activity_holds_the_interlock_off(probe):
    probe.INTERLOCK_POLL_INTERVAL = 0.01
    probe.INTERLOCK_TIMEOUT = 0.2
    probe.set_mode("autonomous")
    for _ in range(10):
        probe._touch_activity()
        time.sleep(0.02)
    assert probe.is_enabled is True


@pytest.mark.loops
def test_a_restart_inside_the_stop_window_does_not_reuse_a_dying_thread(probe):
    """The restart race: `is_alive()` alone is not enough, because a thread
    told to stop stays alive until its next tick."""
    probe.INTERLOCK_POLL_INTERVAL = 5.0
    probe.enable()
    dying = probe._interlock_thread
    probe._stop_interlock()                  # set, but the thread is still alive
    assert dying.is_alive()
    probe.enable()
    assert probe._interlock_thread is not dying
    assert not probe._interlock_stop.is_set()


# -- position ---------------------------------------------------------------

@pytest.mark.transport
def test_position_is_unknown_until_the_first_line(probe):
    assert probe.position == (0, 0, 0)
    assert probe.position_time is None
    assert probe.position_age is None
    assert probe.velocity == (0.0, 0.0, 0.0)


@pytest.mark.transport
def test_the_drain_keeps_the_latest_complete_pos_line(probe):
    probe.port.lines = ["POS:1,2,3", "junk", "POS:4,5,6", "POS:bad,,",
                        "DEV: s", "POS:7,8,9"]
    assert probe._read_position() == (7, 8, 9)
    assert probe._read_position() is None


@pytest.mark.transport
def test_the_drain_is_bounded(probe):
    probe.port.lines = ["POS:1,1,1"] * (probe.MAX_DRAIN_LINES + 10)
    probe._read_position()
    assert probe.port.lines, "an unbounded drain can hold up a jog frame"


@pytest.mark.transport
def test_velocity_is_computed_between_two_distinct_samples(probe):
    probe._note_position((0, 0, 0))
    assert probe.velocity == (0.0, 0.0, 0.0), "one sample is not a velocity"
    first_time = probe.position_time
    assert first_time is not None
    time.sleep(0.05)
    probe._note_position((10, 0, -5))
    span = probe.position_time - first_time
    assert probe.position == (10, 0, -5)
    assert probe.velocity[0] == pytest.approx(10 / span, rel=0.01)
    assert probe.velocity[2] == pytest.approx(-5 / span, rel=0.01)
    assert probe.position_age < 1.0


@pytest.mark.transport
def test_a_standing_still_probe_reports_zero_velocity(probe):
    probe._note_position((3, 3, 3))
    time.sleep(0.02)
    probe._note_position((3, 3, 3))
    assert probe.velocity == (0.0, 0.0, 0.0)


@pytest.mark.transport
def test_the_sample_loop_fills_the_cache_without_being_polled(probe):
    probe.port.lines = ["POS:5,6,7"]
    probe._start_threads()
    try:
        deadline = time.monotonic() + 2.0
        while probe.position != (5, 6, 7) and time.monotonic() < deadline:
            time.sleep(0.01)
    finally:
        probe._stop_threads()
    assert probe.position == (5, 6, 7)
    assert probe.position_time is not None


# -- the jog loop -----------------------------------------------------------

LEVELS = {"x_axisStatus": 0.5, "y_axisStatus": 0.0, "z_axisStatusL": -1.0,
          "z_axisStatusR": -1.0, "dpad_LR": 0, "dpad_UD": 0,
          "LBumper": 0, "RBumper": 0}


@pytest.mark.loops
def test_the_jog_loop_streams_only_while_manual_and_the_gate_is_open():
    probe, port, gamepad = make_probe(levels=LEVELS)
    probe.JOG_INTERVAL = 0.005
    probe._start_threads()
    try:
        time.sleep(0.05)
        assert not port.writes, "nothing may stream while disabled"
        probe.set_mode("manual")
        port.writes.clear()
        time.sleep(0.08)
        assert len(port.writes) >= 3
        assert all(len(w) == 42 for w in port.writes)

        gamepad.set_gate(False)
        time.sleep(0.05)
        port.writes.clear()
        time.sleep(0.05)
        assert not port.writes, "a closed gate must hold the axis"
    finally:
        probe._stop_threads()


@pytest.mark.loops
def test_closing_the_gate_sends_one_neutral_frame_not_a_stream():
    """I-4.2: leaving the pumping state zeroes the axis once. The gate is not
    a stop, so it neither leaves the mode nor de-energizes."""
    probe, port, gamepad = make_probe(levels=LEVELS)
    probe.JOG_INTERVAL = 0.005
    probe.set_mode("manual")
    probe._start_threads()
    try:
        time.sleep(0.03)
        gamepad.set_gate(False)
        time.sleep(0.05)
        neutral = port.writes[-1]
    finally:
        probe._stop_threads()
    assert probe.mode is ProbeMode.MANUAL
    assert neutral == probe._jog_bytes({})


@pytest.mark.loops
def test_off_neutral_input_counts_as_activity(probe):
    probe.set_mode("manual")
    probe._activity_time = 0.0
    probe._send_jog({"x_axisStatus": 0.9})
    assert probe._activity_time > 0.0
    probe._activity_time = 0.0
    probe._send_jog({})
    assert probe._activity_time == 0.0, "a neutral stick is not activity"


@pytest.mark.loops
def test_a_gamepad_read_that_raises_is_reported_not_swallowed(probe):
    class Exploding(FakeGamepad):
        @property
        def levels(self):
            raise RuntimeError("pad unplugged")

        @levels.setter
        def levels(self, value):
            pass

    probe.gamepad = Exploding()
    with Collected() as log:
        assert probe._axis_state() == {}
    assert log.of("warning"), "a failed pad read must reach the operator"


# -- schema and state -------------------------------------------------------

@pytest.mark.schema
def test_the_schema_declares_the_gamepad_dropdown_and_its_options(probe):
    from station import schema as sch
    dropdown = next(e for e in sch.elements(probe.schema)
                    if e["type"] == "dropdown")
    assert dropdown["command"] == "set_gamepad"
    assert dropdown["options_command"] == "gamepad_options"
    assert probe.options("gamepad_options") == ["None", "Pad0", "Pad1"]


@pytest.mark.schema
def test_the_mode_toggles_carry_their_target_as_arguments(probe):
    from station import schema as sch
    toggles = {e["model_attr"]: e for e in sch.elements(probe.schema)
               if e["type"] == "toggle"}
    assert toggles["is_auto"]["command"] == "set_mode"
    assert toggles["is_auto"]["on_args"] == ["autonomous"]
    assert toggles["is_auto"]["off_args"] == ["disabled"]
    assert toggles["is_manual"]["on_args"] == ["manual"]
    assert "is_estopped" in toggles, "the safety section must be present"


@pytest.mark.schema
def test_the_schema_ends_with_the_safety_section(probe):
    assert probe.schema["sections"][-1]["title"] == "Safety"


@pytest.mark.schema
def test_every_element_is_a_type_the_renderers_implement(probe):
    from station import schema as sch
    for element in sch.elements(probe.schema):
        assert element["type"] in sch.ELEMENT_TYPES
        assert element["role"] in sch.ROLES


@pytest.mark.params
def test_motion_entries_are_gated_while_a_run_is_engaged(probe):
    from station import schema as sch
    entries = [e for e in sch.elements(probe.schema) if e["type"] == "entry"]
    assert entries
    for element in entries:
        assert element["disabled_when"] == ["autonomous", "manual"]
        assert element["writable"] is True
    probe.set_mode("autonomous")
    assert probe.run("_commit", inputs={"x_dist": "9"}).is_refused
    with pytest.raises(Refused):
        probe.x_dist = "9"


@pytest.mark.params
def test_the_dc_probe_publishes_its_brake_entries(probe):
    dc, _, _ = make_probe(DCProbe)
    from station import schema as sch
    names = [e.get("model_attr") for e in sch.elements(dc.schema)]
    assert "slow_speed" in names and "brake_distance" in names
    assert "slow_speed" not in [e.get("model_attr")
                                for e in sch.elements(probe.schema)]


@pytest.mark.params
def test_an_unparseable_value_falls_back_to_this_class_s_own_default():
    """RC-6: `_num(self.full_speed, 400)` hardcoded BaseProbe's number at every
    call site, so a DC probe with an empty field was handed 400 — more than
    three times the speed it is configured for."""
    dc, port, _ = make_probe(DCProbe)
    dc.full_speed = ""
    assert dc._number("full_speed") == 120.0


@pytest.mark.schema
def test_state_carries_the_position_names_the_monitor_reads(probe):
    probe._note_position((1, 2, 3))
    state = probe.state
    assert state["position"] == [1, 2, 3]
    assert state["mode"] == "disabled"
    assert state["position_time"] is not None
    assert "velocity" in state and "is_moving" in state
    assert set(state["devices"]) == {"FakePort", "FakeGamepad"}


@pytest.mark.schema
def test_a_command_the_schema_does_not_declare_is_refused(probe):
    assert probe.run("_halt_hardware").is_refused
    assert probe.run("_energize").is_refused


# -- lifecycle --------------------------------------------------------------

@pytest.mark.lifecycle
def test_open_opens_the_devices_and_starts_both_loops(probe):
    probe.open()
    try:
        assert probe.port.opened == 1 and probe.gamepad.opened == 1
        assert probe._sample_thread.is_alive() and probe._jog_thread.is_alive()
    finally:
        probe.close()
    assert not probe._sample_thread.is_alive()
    assert not probe._jog_thread.is_alive()
    assert probe.port.closed == 1 and probe.gamepad.closed == 1


@pytest.mark.lifecycle
def test_close_stops_the_hardware_before_it_closes_the_port(probe):
    probe.open()
    probe.port.writes.clear()
    probe.close()
    assert b"d" in probe.port.writes
    assert b"k\n" in probe.port.writes
    assert probe.port.closed == 1


@pytest.mark.lifecycle
def test_a_device_handed_in_is_used_as_is_and_never_bound_behind_your_back():
    port, gamepad = FakePort(), FakeGamepad(bound=False)
    probe = StepperProbe(port=port, gamepad=gamepad)
    probe.open()
    try:
        assert probe.port is port and probe.gamepad is gamepad
        assert gamepad.bound_to is None
    finally:
        probe.close()


@pytest.mark.lifecycle
def test_a_gamepad_named_at_construction_is_bound_at_open(monkeypatch):
    """Setup hands the model a *name*; the model owns the Device."""
    from station.models import probe as probe_module

    made = FakeGamepad(bound=False)
    monkeypatch.setattr(probe_module.gamepad_device, "Gamepad",
                        lambda *args, **kwargs: made)
    probe = StepperProbe(port=FakePort(), gamepad="Pad1")
    probe.open()
    try:
        assert probe.gamepad is made
        assert made.bound_to == "Pad1"
        assert probe.gamepad_name == "Pad1"
    finally:
        probe.close()


@pytest.mark.lifecycle
@pytest.mark.parametrize("cls,identity,name", [
    (StepperProbe, "s", "Stepper Probe"),
    (DCProbe, "d", "DC Probe"),
    (ChuckPositioner, "c", "Chuck Positioner"),
])
def test_each_probe_declares_what_setup_needs_to_place_it(cls, identity, name):
    assert cls.IDENTITY == identity
    assert cls.NAME == name
    assert cls.NEEDS_PORT is True and cls.NEEDS_GAMEPAD is True
    assert issubclass(cls, Probe)


@pytest.mark.lifecycle
def test_no_thread_outlives_the_model(probe):
    before = threading.active_count()
    probe.open()
    probe.close()
    time.sleep(0.05)
    assert threading.active_count() <= before + 1
