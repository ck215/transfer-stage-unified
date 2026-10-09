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

from events import events
from model.probe import (ChuckPositioner, DCProbe, Probe, ProbeMode,
                                  StepperProbe)
from result import Refused

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
    """The pinned Gamepad surface, reporting input the way the device does.

    Continuous input (sticks, triggers) is in `levels`. Discrete presses
    (D-pad, bumpers) are *not*: the device zeroes those keys in `levels` and
    parks one edge per press for `drain_edges()`, the single consumer. This
    fake used to carry D-pad values in `levels`, which the real device never
    produces, and that is how D3 (steps never reaching the wire) went unseen.
    """

    status = "connected"
    EDGE_KEYS = ("hat_x", "hat_y", "bumper_left", "bumper_right")

    def __init__(self, levels=None, bound=True):
        self.levels = {key: (0 if key in self.EDGE_KEYS else value)
                       for key, value in dict(levels or {}).items()}
        self.edges = {}
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

    def press(self, key, value=1):
        """One discrete press, parked until drained (as `_capture_state`)."""
        assert key in self.EDGE_KEYS
        self.edges[key] = value

    def drain_edges(self):
        edges, self.edges = self.edges, {}
        return edges


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
    probe._gamepad_tick()
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
    assert probe._thread("interlock") is not None
    assert probe._thread("interlock").is_alive()
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
def test_rearming_out_of_fault_needs_a_confirmed_disable_first(probe):
    """L8 (SF-1): FAULT means the disable was never confirmed. No mode may
    be entered out of it; only a confirmed `'d'` leaves it, and the next
    arming then resends the hardware enable."""
    probe.enable()
    probe._enter_fault("unknown")
    probe.port.writes.clear()
    with pytest.raises(Refused):
        probe.enable()
    assert probe.port.writes == [], "an enable reached a FAULTed board"
    assert probe.is_faulted is True
    probe.set_mode("disabled")          # the 'd' lands: the fault clears
    assert probe.is_faulted is False and probe.mode is ProbeMode.DISABLED
    probe.port.writes.clear()
    probe.enable()
    assert probe.port.writes[0] == b"e"


def _fault_by_failed_disable(probe):
    probe.enable()
    probe.port.fail_on = lambda payload: payload == b"d"
    probe.set_mode("disabled")
    probe.port.fail_on = None
    assert probe.mode is ProbeMode.FAULT and probe.is_faulted
    probe.port.writes.clear()


@pytest.mark.mode
def test_step_is_refused_on_a_faulted_probe_and_sends_nothing(probe):
    """L8 (SF-1, blocker): Step on a FAULTed probe sent `'e'` and a move
    frame, the stage moved, and the fault vanished although the disable
    was never confirmed."""
    _fault_by_failed_disable(probe)
    assert probe.run("step").is_refused
    with pytest.raises(Refused):
        probe.step()
    assert probe.port.writes == [], probe.port.writes
    assert probe.mode is ProbeMode.FAULT and probe.is_faulted


@pytest.mark.mode
@pytest.mark.parametrize("target", ["idle", "autonomous", "manual"])
def test_no_mode_is_entered_out_of_fault(probe, target):
    _fault_by_failed_disable(probe)
    with pytest.raises(Refused):
        probe.set_mode(target)
    assert probe.port.writes == []
    assert probe.mode is ProbeMode.FAULT


def _stop_inside_the_entry_window(probe, stop):
    """Run `set_mode("manual")` with the window between its enable and its
    mode write widened by 20 ms (the auditor's reproduction of SF-5), and
    `stop()` from another thread inside that window."""
    entered, real = threading.Event(), probe.gamepad.drain_edges

    def _slow_drain():
        entered.set()
        time.sleep(0.02)
        return real()

    probe.gamepad.drain_edges = _slow_drain
    outcome = []

    def _enter():
        try:
            outcome.append(probe.set_mode("manual"))
        except Refused as refused:
            outcome.append(refused)

    worker = threading.Thread(target=_enter)
    worker.start()
    assert entered.wait(2.0)
    stop()
    worker.join(2.0)
    return outcome


@pytest.mark.estop
def test_a_stop_during_a_mode_entry_leaves_the_probe_disabled_not_manual(probe):
    """L9 (SF-5): a stop landing between `'e'` and the mode write was
    overwritten by it, leaving the probe latched in MANUAL; Clear then
    resumed the jog stream with no mode press."""
    outcome = _stop_inside_the_entry_window(probe, probe.estop)
    assert probe.is_estopped
    assert probe.mode is ProbeMode.DISABLED, (probe.mode, outcome)
    assert isinstance(outcome[0], Refused), outcome
    # The back-out ends on the wire with a stop: zero frame, then 'd'.
    assert probe.port.writes[-2:] in ([ZERO, b"d"], [b"d", b"k\n"]), \
        probe.port.writes[-4:]
    probe.clear_estop(True)
    assert probe.mode is ProbeMode.DISABLED
    assert probe.is_manual is False, "Clear would resume the jog stream"


@pytest.mark.estop
def test_an_unlatched_halt_during_a_mode_entry_is_not_overwritten(probe):
    """The same window with a plain halt (no latch): the entry backs out."""
    outcome = _stop_inside_the_entry_window(probe, probe.halt)
    assert probe.mode is ProbeMode.DISABLED, (probe.mode, outcome)


@pytest.mark.schema
def test_step_is_greyed_on_a_faulted_probe(probe):
    import schema as sch
    step = _by_command(probe, "step")
    assert "fault" in step["disabled_when"]
    assert not sch.is_enabled(step, "fault")


@pytest.mark.mode
def test_a_hundred_mode_round_trips_leak_nothing(probe):
    """L13 (legacy row 20): loop the manual/autonomous transition 100
    times with a bound pad and the loops running. The last mode wins, the
    wire ends at rest (the zero frame of the final entry, then at most the
    pump's one neutral packet for leaving manual, I-4.2), and exactly one
    interlock and one pump thread are alive."""
    import struct
    probe._start_threads()
    try:
        for _ in range(100):
            probe.set_mode("manual")
            probe.set_mode("autonomous")
        time.sleep(0.1)          # the pump's exit tick, if any
        assert probe.mode is ProbeMode.AUTO
        tail = probe.port.writes[-2:]
        neutral = lambda p: (len(p) == 42 and p[0] == 0xAA and all(
            v == 0 for v in struct.unpack("<ffffffffff", p[2:])[:3]))
        assert tail[-1] == ZERO or (neutral(tail[-1]) and tail[-2] == ZERO), tail
        alive = [t.name for t in threading.enumerate()
                 if t.name.endswith(f"-{probe.NAME}") and t.is_alive()]
        assert alive.count(f"interlock-{probe.NAME}") == 1, alive
        assert alive.count(f"gamepad-{probe.NAME}") == 1, alive
        assert alive.count(f"sample-{probe.NAME}") == 1, alive
    finally:
        probe._stop_threads()


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


class LockWitness:
    """Every event published while the block runs, with whether the
    publishing thread held the probe's `_mode_lock` at that moment."""

    def __init__(self, probe):
        self.probe, self.seen = probe, []

    def _record(self, event):
        self.seen.append((event.title, self.probe._mode_lock._is_owned()))

    def __enter__(self):
        events.clear()     # a repeat inside the dedupe window re-notifies nobody
        events.subscribe(self._record)
        return self

    def __exit__(self, *exc):
        events.unsubscribe(self._record)


@pytest.mark.estop
def test_a_fault_is_published_after_the_mode_lock_is_released(probe):
    """L10 (SF-4): `_enter_fault` published while holding `_mode_lock`. On
    Tk a subscriber can block that thread on the UI thread, and a mode
    toggle pressed then waits on `_mode_lock`: a deadlock that takes the
    stop with it. Nothing is published while the lock is held."""
    probe.enable()
    probe.port.fail_on = lambda payload: payload == b"d"
    with LockWitness(probe) as witness:
        probe.set_mode("disabled")
    assert probe.mode is ProbeMode.FAULT
    assert ("Fault", False) in witness.seen, witness.seen
    assert not [t for t, held in witness.seen if held], witness.seen


@pytest.mark.estop
def test_a_failed_enable_is_published_after_the_mode_lock_is_released(probe):
    probe.port.fail_on = lambda payload: payload == b"e"
    with LockWitness(probe) as witness:
        with pytest.raises(Refused):
            probe.set_mode("autonomous")
    assert ("Enable Failed", False) in witness.seen, witness.seen
    assert not [t for t, held in witness.seen if held], witness.seen


@pytest.mark.estop
def test_the_no_coil_kill_notice_is_published_with_no_lock_held():
    probe, port, _ = make_probe(DCProbe)
    try:
        probe.enable()
        with LockWitness(probe) as witness:
            probe.estop()
            time.sleep(0.2)
        assert ("Power Down Not Supported", False) in witness.seen, witness.seen
        assert not [t for t, held in witness.seen if held], witness.seen
    finally:
        probe._stop_threads()


def _slow_halt(probe, delay, result=True):
    real = probe._halt_hardware

    def _halt():
        time.sleep(delay)
        real()
        return result

    probe._halt_hardware = _halt


@pytest.mark.estop
def test_a_stop_that_lands_after_the_budget_is_reported_and_revised(probe, monkeypatch):
    """L11 (SF-6): a stop whose bytes land after ESTOP_BUDGET stayed "not
    confirmed" forever. It is still unconfirmed at the budget (the owner's
    number is not widened), but its landing is logged with its real
    latency, the state is revised, and the operator is told."""
    lines = _debug_lines(monkeypatch)
    _slow_halt(probe, 0.2)
    with Collected() as seen:
        assert probe.estop() is False
        assert probe.stop_confirmed is False
        deadline = time.monotonic() + 2.0
        while probe.stop_confirmed is not True and time.monotonic() < deadline:
            time.sleep(0.01)
    assert probe.stop_confirmed is True
    late = [e for e in seen.of("info") if e.title == "Stop Landed Late"]
    assert len(late) == 1, [e.text for e in seen.seen]
    assert "ms after the press" in late[0].message
    assert [m for t, m in lines if t == "Estop Late"], lines


@pytest.mark.estop
def test_a_stop_that_fails_late_is_not_revised(probe):
    _slow_halt(probe, 0.2, result=False)
    with Collected() as seen:
        assert probe.estop() is False
        time.sleep(0.4)
    assert probe.stop_confirmed is False
    assert not [e for e in seen.seen if e.title == "Stop Landed Late"]


@pytest.mark.estop
def test_the_unconfirmed_stop_names_the_real_budget(probe):
    _slow_halt(probe, 0.2)
    with Collected() as seen:
        probe.toggle_estop()
    error = [e for e in seen.of("error") if e.title == "Stop Not Confirmed"][0]
    budget = f"{probe.ESTOP_BUDGET * 1000:.0f} ms"
    assert budget in error.message, error.message
    assert "1 s" not in error.message


@pytest.mark.estop
def test_a_confirmed_stop_clears_the_fault(probe):
    """L5: `_halt_hardware` set DISABLED when `'d'` landed but kept the
    fault, so after Stop + Clear both mode toggles stayed refused."""
    probe.enable()
    probe.port.fail_on = lambda payload: payload == b"d"
    probe.set_mode("disabled")
    assert probe.is_faulted
    probe.port.fail_on = None
    probe.toggle_estop()
    probe.clear_estop(True)
    assert probe.is_faulted is False, probe.fault
    assert probe.gate_mode == "disabled"
    assert probe.run("set_mode", args=["autonomous"]).is_ok


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
    import schema as sch
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
    assert probe._thread("interlock").is_alive()


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
    dying = probe._thread("interlock")
    probe._stop_interlock()                  # set, but the thread is still alive
    assert dying.is_alive()
    probe.enable()
    assert probe._thread("interlock") is not dying
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
def test_malformed_and_garbled_lines_are_counted_as_dropped(probe):
    """L3: a malformed POS line or a garbled (non-printable) line is a dropped
    packet. The handshake's own `DEV:` answer is not one, and neither is
    printable board text (see test_probe_board_text.py)."""
    probe.port.lines = ["POS:1,2,3", "ju\x00nk", "POS:4,5", "POS:bad,,",
                        "DEV: s", "POS:7,8,9"]
    assert probe._read_position() == (7, 8, 9)
    assert probe.dropped == 3
    probe.port.lines = ["printable board text"]
    probe._read_position()
    assert probe.dropped == 3   # printable non-POS text is not counted


@pytest.mark.transport
def test_dropped_packets_warn_when_the_count_rises_and_not_more_often(probe):
    with Collected() as seen:
        probe.port.lines = ["ju\x00nk"]
        probe._read_position()
        probe.port.lines = ["more ju\x00nk"]
        probe._read_position()
    warned = [e for e in seen.of("warning") if e.title == "Packets Dropped"]
    assert len(warned) == 1, [e.text for e in seen.seen]
    assert "Stepper Probe" in warned[0].message
    probe._dropped_warned_at -= probe.DROPPED_WARN_INTERVAL
    with Collected() as seen:
        probe.port.lines = ["ju\x00nk again"]
        probe._read_position()
    assert [e.title for e in seen.of("warning")] == ["Packets Dropped"]


@pytest.mark.transport
def test_the_heartbeat_is_not_touched_by_a_read_that_raised(probe):
    """L3: `_sample_loop` touched the heartbeat BEFORE the read, so `age`
    never grew while every read failed. It is touched only after a read
    pass that did not raise."""

    def _broken(timeout=None):
        raise OSError("the read failed")

    probe.port.read_line = _broken
    probe._start_threads()
    try:
        time.sleep(0.4)
        age = probe.state["age"]
    finally:
        probe._stop_threads()
    assert age >= 0.3, f"age {age} s: the loop claimed to be alive"


@pytest.mark.transport
def test_a_snap_to_zero_while_enabled_warns_of_a_board_reset(probe):
    """L6: the stepper firmware's setup() zeroes its counts, so a board
    that reset mid-session reports exactly (0,0,0). Warn (ATTENTION); the
    mode is NOT changed (a false positive would be an unasked-for stop)."""
    import events as events_module
    probe.enable()
    probe._note_position((5000, -20, 7))
    with Collected() as seen:
        probe._note_position((0, 0, 0))
    warned = [e for e in seen.seen if e.title == events_module.BOARD_RESET_SUSPECTED]
    assert len(warned) == 1 and warned[0].needs_ack is True
    assert warned[0].message == (
        "Stepper Probe's position snapped to zero while enabled; the board "
        "may have reset and its drivers are off. Leave the mode and enter it "
        "again.")
    assert probe.mode is ProbeMode.IDLE, "the heuristic must never stop the probe"


@pytest.mark.transport
@pytest.mark.parametrize("case", ["disabled", "small", "slow", "not_zero"])
def test_what_is_not_a_board_reset(probe, case):
    if case != "disabled":
        probe.enable()
    start = (probe.RESET_JUMP_COUNTS // 2 if case == "small" else 5000, 0, 0)
    probe._note_position(start)
    if case == "slow":
        probe._position_time -= probe.RESET_WINDOW + 0.1
    with Collected() as seen:
        probe._note_position((0, 0, 1) if case == "not_zero" else (0, 0, 0))
    assert not [e for e in seen.seen if e.title == "Board Reset Suspected"]


def _debug_lines(monkeypatch):
    lines = []
    real = events.debug

    def _record(title, message, **kw):
        lines.append((title, message))
        return real(title, message, **kw)

    monkeypatch.setattr(events, "debug", _record)
    return lines


@pytest.mark.mode
def test_every_mode_line_carries_the_position(probe, monkeypatch):
    """L7: the position at every `Mode:` transition line."""
    lines = _debug_lines(monkeypatch)
    probe._note_position((12, -3, 4))
    probe.enable()
    probe.set_mode("autonomous")
    probe.set_mode("disabled")
    probe.enable()
    probe.halt()
    modes = [m for t, m in lines if t == "Mode"]
    assert len(modes) >= 4, modes
    assert all("at (12, -3, 4)" in m for m in modes), modes


@pytest.mark.loops
def test_the_sampler_logs_a_health_line_on_its_interval(probe, monkeypatch):
    """L7: one `Health` debug line per HEALTH_INTERVAL per probe, with
    everything the next bench occurrence needs."""
    monkeypatch.setattr(type(probe), "HEALTH_INTERVAL", 0.05)
    lines = _debug_lines(monkeypatch)
    probe._start_threads()
    try:
        time.sleep(0.3)
    finally:
        probe._stop_threads()
    health = [m for t, m in lines if t == "Health"]
    assert health, "no Health line"
    for field in ("mode=", "link=", "position=", "position_age=",
                  "idle_remaining=", "gate_open=", "pad_bound=",
                  "sampler_alive=True", "pump_alive=", "latched=", "fault="):
        assert field in health[-1], (field, health[-1])


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

LEVELS = {"axis_x": 0.5, "axis_y": 0.0, "trigger_left": -1.0,
          "trigger_right": -1.0, "hat_x": 0, "hat_y": 0,
          "bumper_left": 0, "bumper_right": 0}


@pytest.mark.loops
def test_the_jog_loop_streams_only_while_manual_and_the_gate_is_open():
    probe, port, gamepad = make_probe(levels=LEVELS)
    probe.GAMEPAD_RATE_HZ = 200
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
    probe.GAMEPAD_RATE_HZ = 200
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
    probe._send_jog({"axis_x": 0.9})
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


# -- D-pad and bumper steps (D3) --------------------------------------------
#
# A step travels in the manual jog packet (fields 8-10 of `<BBffffffffff`),
# built from one drained edge per press. The stop path comes first: a pending
# edge must never become motion while latched, gated, or outside manual.

STEP_FIELDS = slice(8, 11)         # hat_x, hat_y, bumpers


@pytest.fixture
def neutral_probe():
    """A probe whose pad reads neutral levels, as a bound real pad does, so a
    merged edge *would* reach the packet if the stop path let it."""
    probe, _port, _gamepad = make_probe(levels=LEVELS)
    yield probe
    probe._stop_threads()


def _jogs(port):
    import struct
    from model.probe import PACKET_FORMAT
    return [struct.unpack(PACKET_FORMAT, w) for w in port.writes if len(w) == 42]


def _steps(port):
    return [j for j in _jogs(port) if any(j[STEP_FIELDS])]


@pytest.mark.loops
def test_a_latched_probe_never_steps_from_a_pending_edge(neutral_probe):
    """Stop path: a press parked before or during a latch is discarded, and
    clearing the latch and re-entering manual does not release it."""
    probe = neutral_probe
    gamepad, port = probe.gamepad, probe.port
    probe.set_mode("manual")
    gamepad.press("hat_x", 1)
    probe.estop()
    gamepad.press("bumper_left", 1)
    for _ in range(3):
        probe._gamepad_tick()
    assert gamepad.edges == {}, "the latched tick must still consume the edges"
    probe.clear_estop(confirmed=True)
    probe.set_mode("manual")
    for _ in range(3):
        probe._gamepad_tick()
    assert _steps(port) == [], "a pending edge stepped across a latch"


@pytest.mark.loops
def test_a_closed_gate_never_steps_from_a_pending_edge(neutral_probe):
    probe = neutral_probe
    gamepad, port = probe.gamepad, probe.port
    probe.set_mode("manual")
    gamepad.set_gate(False)
    gamepad.press("hat_y", -1)
    gamepad.press("bumper_right", 1)
    probe._gamepad_tick()
    assert gamepad.edges == {}, "the gated tick must consume and discard"
    gamepad.set_gate(True)
    for _ in range(3):
        probe._gamepad_tick()
    assert _steps(port) == [], "a press made behind a closed gate stepped later"


@pytest.mark.loops
def test_a_press_made_while_idle_is_consumed_there_not_deferred(neutral_probe):
    """Draining is unconditional: an idle tick eats the edge, so it cannot
    fire on the first packet after entering manual (audit out_stale.txt)."""
    probe = neutral_probe
    gamepad, port = probe.gamepad, probe.port
    probe.set_mode("idle")
    gamepad.press("hat_x", 1)
    probe._gamepad_tick()
    assert gamepad.edges == {}
    probe.set_mode("manual")
    for _ in range(3):
        probe._gamepad_tick()
    assert _steps(port) == []


@pytest.mark.loops
def test_entering_manual_discards_a_press_no_tick_has_seen(neutral_probe):
    probe = neutral_probe
    gamepad, port = probe.gamepad, probe.port
    probe.set_mode("idle")
    gamepad.press("hat_x", 1)          # no tick between the press and entry
    probe.set_mode("manual")
    for _ in range(3):
        probe._gamepad_tick()
    assert _steps(port) == []


@pytest.mark.loops
def test_a_pending_edge_steps_once_in_manual_then_zeroes():
    probe, port, gamepad = make_probe(levels=LEVELS)
    probe.set_mode("manual")
    gamepad.press("hat_x", -1)
    gamepad.press("bumper_right", 1)
    for _ in range(4):
        probe._gamepad_tick()
    jogs = _jogs(port)
    assert len(jogs) == 4
    assert jogs[0][STEP_FIELDS] == (-1.0, 0.0, -1.0)
    assert [j[STEP_FIELDS] for j in jogs[1:]] == [(0.0, 0.0, 0.0)] * 3
    assert jogs[0][5:8] == (float(probe._number("x_step")),
                            float(probe._number("y_step")),
                            float(probe._number("z_step")))
    probe._stop_threads()


@pytest.mark.loops
def test_a_pad_whose_levels_cannot_be_read_does_not_step():
    """Held at neutral means no step either: an empty read sends no edge."""
    probe, port, gamepad = make_probe(levels={})
    probe.set_mode("manual")
    gamepad.press("hat_x", 1)
    probe._gamepad_tick()
    probe._stop_threads()
    assert _steps(port) == [] and gamepad.edges == {}


# -- schema and state -------------------------------------------------------

@pytest.mark.schema
def test_the_schema_declares_the_gamepad_dropdown_and_its_options(probe):
    import schema as sch
    dropdown = next(e for e in sch.elements(probe.schema)
                    if e["type"] == "dropdown")
    assert dropdown["command"] == "set_gamepad"
    assert dropdown["options_command"] == "gamepad_options"
    assert probe.options("gamepad_options") == ["None", "Pad0", "Pad1"]


@pytest.mark.schema
def test_the_mode_toggles_carry_their_target_as_arguments(probe):
    import schema as sch
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
    import schema as sch
    for element in sch.elements(probe.schema):
        assert element["type"] in sch.ELEMENT_TYPES
        assert element["role"] in sch.ROLES


@pytest.mark.params
def test_motion_entries_are_gated_while_a_run_is_engaged(probe):
    import schema as sch
    entries = [e for e in sch.elements(probe.schema) if e["type"] == "entry"]
    assert entries
    # Updated (owner ruling 2026-09-26): Manual Speed is live while jogging,
    # so its entry is locked in autonomous only; every other entry keeps both
    # motion modes (tests/test_manual_speed_live.py).
    for element in entries:
        # The Stepper speaks um and um/s (owner 2026-10-09): its Manual Speed
        # entry is man_full_speed_um_s; the DC and Chuck dial is the percent.
        expected = (["autonomous"] if element["model_attr"]
                    in ("man_full_speed_pct", "man_full_speed_um_s")
                    else ["autonomous", "manual"])
        assert element["disabled_when"] == expected
        assert element["writable"] is True
    probe.set_mode("autonomous")
    assert probe.run("_commit", inputs={"x_dist": "9"}).is_refused
    with pytest.raises(Refused):
        probe.x_dist = "9"
    # The um entry carries the same gate (9 um = 14 whole counts).
    assert probe.run("_commit", inputs={"x_dist_um": "9"}).is_refused
    with pytest.raises(Refused):
        probe.x_dist_um = "9"


@pytest.mark.params
def test_the_dc_probe_publishes_its_brake_entries(probe):
    dc, _, _ = make_probe(DCProbe)
    import schema as sch
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
def test_position_epoch_counts_each_time_the_link_comes_up(probe):
    """flake-coords section 6: the firmware's counter restarts at every port
    open, so a stage position means something only within one epoch. The
    epoch is the number of times the port has come up (simulated, verified
    or unverified) since construction, published in state."""
    probe.port.status = "closed"
    probe._track_epoch()
    assert probe.position_epoch == 0
    probe.port.status = "verified"
    probe._track_epoch()
    assert probe.position_epoch == 1
    probe._track_epoch()                       # still up: the same epoch
    assert probe.position_epoch == 1
    for down in ("lost", "connecting"):
        probe.port.status = down
        probe._track_epoch()
        assert probe.position_epoch == 1
    probe.port.status = "unverified"           # back up: a new counter
    probe._track_epoch()
    assert probe.position_epoch == 2
    assert probe.state["position_epoch"] == 2


@pytest.mark.schema
def test_every_position_sample_checks_the_epoch(probe):
    probe.port.status = "closed"
    probe._track_epoch()
    probe.port.status = "simulated"
    probe.port.lines = ["POS:5,6,7"]
    position = probe._read_position()
    probe._note_position(position)
    assert probe.position_epoch == 1


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
        assert probe._thread("sample").is_alive() and probe._thread("gamepad").is_alive()
    finally:
        probe.close()
    assert not probe._thread("sample").is_alive()
    assert not probe._thread("gamepad").is_alive()
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
    from model import probe as probe_module

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


# -- Tier F item 1: an unchanged value while locked is not a change ----------

@pytest.mark.params
@pytest.mark.parametrize("cls", [StepperProbe, DCProbe, ChuckPositioner])
def test_every_motion_parameter_holds_its_declared_default_from_construction(cls):
    """The gated params are properties, and `Panel._defaults` skipped every
    property, so the store started empty: the model read `''` while the view
    showed the default. The first write of that default in autonomous mode
    then looked like an edit and was refused."""
    probe, _, _ = make_probe(cls)
    for name, param in cls.PARAMS.items():
        if name.endswith("_pct"):
            # The dial is the stored steps/s read as a percent of the ceiling,
            # never a second stored value (test_the_defaults_read_13_...).
            steps = cls.PARAMS[name[:-4]].default
            assert getattr(probe, name) == cls.steps_to_pct(steps), name
            continue
        assert getattr(probe, name) == param.default, name


@pytest.mark.mode
def test_step_in_autonomous_mode_with_unchanged_distances_is_not_refused(probe):
    """audit-ui-harden-clarify, could-not-explain: every Step input travels
    with every command, so pressing Step while autonomous re-sent the held
    distances and was refused 'X ... cannot be changed while autonomous'."""
    # The operator enters autonomous mode with the toggle, then presses Step
    # with the fields showing what the schema declared (nothing was typed).
    probe.set_mode("autonomous")
    probe.port.writes.clear()
    # The Stepper's Step inputs are um and um/s: 400 counts/s = 250 um/s.
    inputs = {"x_dist_um": "0", "y_dist_um": "0", "z_dist_um": "0",
              "full_speed_um_s": "250"}
    result = probe.run("step", inputs=inputs)
    assert result.is_ok, result.reason
    probe._moving_deadline = None            # the move arrived
    result = probe.run("step", inputs=inputs)
    assert result.is_ok, result.reason
    assert probe.mode is ProbeMode.AUTO
    assert len(probe.port.writes) == 2
    # A committed field (a desktop view's focus-out) is the same case.
    assert probe.run("_commit", inputs={"x_step_um": "0.625"}).is_ok


@pytest.mark.mode
def test_step_in_autonomous_mode_is_refused_when_a_distance_changed(probe):
    probe.x_dist_um = 5                       # 8 whole counts
    assert probe.run("step", inputs={"x_dist_um": "5"}).is_ok
    probe._moving_deadline = None
    result = probe.run("step", inputs={"x_dist_um": "6"})   # 10 counts: an edit
    assert result.is_refused
    assert probe.x_dist_um == 5
    assert probe.x_dist == 8


@pytest.mark.params
def test_writing_the_held_value_while_locked_is_not_a_change(probe):
    """The property setter compares the normalised value: 5, "5" and 5.0 are
    the same write; 6 is an edit and is refused."""
    probe.x_step = 5
    probe.set_mode("autonomous")
    for same in (5, "5", 5.0, " 5 "):
        probe.x_step = same
    assert probe.x_step == 5
    with pytest.raises(Refused):
        probe.x_step = 6


# -- A1: a stop that wrote nothing is unconfirmed ---------------------------

@pytest.mark.estop
def test_a_stop_with_no_port_is_not_confirmed():
    """A1: `_write_stop` reported True when there was no port, so a probe
    with nothing behind it confirmed a stop it never sent. The rotator already
    answers False here (MANAGER-21); the probe now agrees."""
    probe, _, _ = make_probe()
    probe.port = None
    try:
        assert probe._halt_hardware() is False
        assert probe.estop() is False
        assert probe.is_estopped is True, "the latch holds either way"
    finally:
        probe._stop_threads()


# -- F11: the latch pre-disables what it will refuse -------------------------

def _by_command(model, command, args=None):
    import schema as sch
    for element in sch.elements(model.schema):
        if element.get("command") == command and (
                args is None or list(args) in (element.get("on_args"),
                                               element.get("off_args"))):
            return element
    raise AssertionError(f"no element for {command} {args}")


@pytest.mark.schema
def test_the_latch_greys_out_step_and_both_mode_toggles(probe):
    import schema as sch
    probe.estop()
    mode = probe.state["mode"]
    assert mode == "latched"
    assert probe.state["model_mode"] == probe.mode_name
    for element in (_by_command(probe, "step"),
                    _by_command(probe, "set_mode", ["autonomous"]),
                    _by_command(probe, "set_mode", ["manual"])):
        assert sch.is_enabled(element, mode) is False, element["text"]
    stop = _by_command(probe, "toggle_estop")
    assert sch.is_enabled(stop, mode) is True, "Clear must stay reachable"
    refused = probe.run("step")
    assert refused.is_refused and "stop" in refused.reason.lower()
    probe.clear_estop(confirmed=True)
    assert probe.state["mode"] == probe.mode_name
    assert sch.is_enabled(_by_command(probe, "step"), probe.state["mode"])


# -- F19: operator sentences ------------------------------------------------

@pytest.mark.estop
def test_the_coil_kill_warning_is_a_sentence_without_ids_or_bytes():
    """audit-ui-harden-clarify: 'SERIAL-10 ... D-7' and the byte list reached
    the operator. The finding IDs go to the file log only."""
    dc, _, _ = make_probe(DCProbe)
    with Collected() as log:
        dc.halt()
    warning = next(e for e in log.of("warning") if "Power Down" in e.title)
    for jargon in ("SERIAL-10", "D-7", "['s']", "coil-kill"):
        assert jargon not in warning.message, jargon
    assert "treat it as live" in warning.message


def test_a_failed_move_write_reaches_the_view_as_a_sentence(probe):
    """'step failed: ... b'1,1,1,...' was not sent' reached the operator."""
    probe.port.fail_on = lambda payload: payload.startswith(b"1,")
    original = probe.port.write

    def write(payload, **kwargs):
        if payload.startswith(b"1,"):
            raise OSError(f"port is not open; {payload!r} was not sent")
        return original(payload, **kwargs)

    probe.port.write = write
    with Collected() as log:
        result = probe.run("step")
    assert result.is_failed
    for jargon in ("b'", "step failed", "was not sent"):
        assert jargon not in result.reason, jargon
    assert result.reason.startswith("Step did not complete.")
    shown = [e for e in log.seen if e.severity != "debug"]
    assert all("b'" not in e.message for e in shown)


@pytest.mark.schema
def test_the_gamepad_choice_is_tier_one_beside_the_mode_toggles(probe):
    """Owner ruling 2026-09-26 (Tier K): the gamepad is chosen every session,
    so it does not sit behind Configure. It is in the same tier-1 section
    as the Manual toggle, ahead of it."""
    import schema as sch
    section = next(s for s in probe.schema["sections"]
                   if any(e["type"] == "dropdown" for e in s["elements"]))
    assert section.get("tier", 1) == 1, section["title"]
    kinds = [(e["type"], e.get("model_attr")) for e in section["elements"]]
    assert kinds.index(("dropdown", "gamepad_name")) < kinds.index(("toggle", "is_manual"))
    assert not any(e["type"] == "dropdown" for s in probe.schema["sections"]
                   if s.get("tier", 1) != 1 for e in s["elements"])


@pytest.mark.schema
def test_the_tier_two_disclosure_names_the_device(probe):
    """Tier K: "Configure" alone did not say what it configured once the
    press moved down to its well; the disclosure carries the model's name."""
    tier_two = [s for s in probe.schema["sections"] if s.get("tier") == 2]
    assert tier_two and tier_two[0]["disclosure"] == f"Configure {probe.NAME}"


# -- the idle countdown (Tier N, owner 2026-09-26: warn before the timeout
#    and offer to extend) ----------------------------------------------------

def _wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.005)
    return predicate()


@pytest.mark.loops
def test_the_idle_countdown_is_published_and_warns_once_before_the_interlock(probe):
    """A view can only offer to extend what it can see coming: `idle_remaining`
    counts down in state, an "Idle Timeout Soon" warning fires once inside
    the last IDLE_WARN_SECONDS, and the interlock still fires on time."""
    from test_core_fakes import EventRecorder
    probe.INTERLOCK_POLL_INTERVAL = 0.01
    probe.INTERLOCK_TIMEOUT = 0.4
    probe.IDLE_WARN_SECONDS = 0.25
    assert probe.idle_remaining is None, "no mode, no clock"
    with EventRecorder() as log:
        probe.set_mode("autonomous")
        assert 0.3 < probe.idle_remaining <= 0.4
        assert probe.state["idle_remaining"] == pytest.approx(probe.idle_remaining, abs=0.05)
        assert _wait_for(lambda: log.titled("Idle Timeout Soon"))
        assert probe.is_enabled, "the warning must come BEFORE the power-down"
        assert _wait_for(lambda: not probe.is_enabled)
    soon = log.titled("Idle Timeout Soon")
    assert len(soon) == 1 and probe.NAME in soon[0].message
    assert log.titled("Idle Timeout"), "the interlock itself still fired"
    assert probe.idle_remaining is None


@pytest.mark.loops
def test_extend_idle_restarts_the_clock_and_rearms_the_warning(probe):
    from test_core_fakes import EventRecorder
    probe.INTERLOCK_POLL_INTERVAL = 0.01
    probe.INTERLOCK_TIMEOUT = 0.4
    probe.IDLE_WARN_SECONDS = 0.25
    with EventRecorder() as log:
        probe.set_mode("autonomous")
        assert _wait_for(lambda: log.titled("Idle Timeout Soon"))
        assert probe.run("extend_idle").is_ok, "declared in the schema, so run() lets it through"
        assert probe.idle_remaining > 0.3
        time.sleep(0.2)
        assert probe.is_enabled, "the extension held the interlock off past the old deadline"
        assert _wait_for(lambda: len(log.titled("Idle Timeout Soon")) == 2), "warned again for the new period"
        assert _wait_for(lambda: not probe.is_enabled)


def test_extend_idle_is_refused_when_nothing_is_energized(probe):
    with pytest.raises(Refused):
        probe.extend_idle()
    assert probe.idle_remaining is None


def test_a_probe_in_a_mode_is_energized_even_when_it_is_not_moving(probe):
    assert probe.is_energized is False
    probe.set_mode("autonomous")
    assert probe.is_energized is True and probe.is_active is False
    probe.set_mode("disabled")
    assert probe.is_energized is False


def test_the_probes_own_stop_is_never_refused_by_a_bad_step_size(probe):
    """Round 8 (A11Y-1): the per-model stop switch with X step size 0 in the
    box was refused and nothing latched. Stops ignore the boxes; leaving a
    mode does too."""
    probe.set_mode("autonomous")
    bad = {"x_step": "0"}
    assert probe.run("step", inputs=bad).is_refused
    assert probe.run("set_mode", inputs=bad, args=("disabled",)).is_ok
    probe.set_mode("autonomous")
    result = probe.run("toggle_estop", inputs=bad)
    assert result.is_ok and probe.is_estopped
    assert probe.run("extend_idle", inputs=bad).is_refused, "refused for being latched, not for the box"


@pytest.mark.schema
def test_a_faulted_probes_mode_toggles_are_greyed_from_the_schema(probe):
    """Round 8 (IMP8-2): a probe whose disable failed looked like a safely
    disabled one. Every view greys the toggles from this list."""
    import schema as sch
    toggles = [e for e in sch.elements(probe.schema) if e.get("command") == "set_mode"]
    assert toggles and all("fault" in e["disabled_when"] for e in toggles)
    assert not sch.is_enabled(toggles[0], "fault")


@pytest.mark.parametrize("cls", [StepperProbe, DCProbe, ChuckPositioner])
def test_a_probe_opens_its_port_at_the_firmwares_500000_baud(cls, monkeypatch):
    """All three probe sketches run `Serial.begin(500000)`. The rebuild opened
    them at SerialPort's 115200 default: the scan (which tries 500000 first)
    found the boards, then the session port read garbage, went unverified,
    and every enable and jog frame arrived unreadable (bench, 2026-09-26)."""
    built = []

    class _Port:
        def __init__(self, port, baud_rate=None, **kwargs):
            built.append((port, baud_rate))

    monkeypatch.setattr("model.probe.serial_device.SerialPort", _Port)
    cls(port="/dev/ttyACM0", gamepad=object())
    cls(port=None, gamepad=object(), sim=True)
    assert built == [("/dev/ttyACM0", 500000), ("SIM", 500000)]


CEILINGS = [(StepperProbe, 3200), (DCProbe, 3200), (ChuckPositioner, 600)]


@pytest.mark.parametrize("cls,ceiling", [c for c in CEILINGS if c[0] is not StepperProbe])
@pytest.mark.parametrize("name", ["full_speed", "man_full_speed"])
def test_every_probe_speed_tops_out_at_its_own_ceiling(cls, ceiling, name):
    """Owner 2026-10-07: the stepper family keeps 3200 steps/s (the DC probe
    too: no separate bench value was given), the chuck positioner's is 600.
    The stored steps/s Param validates against the class's ceiling; the dial
    travels the whole 0-100 %."""
    param = cls.PARAMS[name]
    assert cls.MAX_SPEED == ceiling
    assert param.maximum == ceiling
    assert param.parse(ceiling) == (True, ceiling)
    assert param.parse(ceiling + 1)[0] is False
    assert cls.SPEED_SLIDER == (0, 100)


@pytest.mark.parametrize("name", ["full_speed_um_s", "man_full_speed_um_s"])
def test_the_stepper_speed_tops_out_at_2000_um_s_which_is_3200_counts_s(name):
    """The Stepper's ceiling, in the units the operator types: 2000 um/s is
    3200 counts/s at 0.625 um/count. The stored counts/s Param keeps 3200."""
    param = StepperProbe.PARAMS[name]
    assert StepperProbe.MAX_SPEED == 3200
    assert StepperProbe.PARAMS[name[:-len("_um_s")]].maximum == 3200
    assert param.maximum == 2000
    assert param.parse(2000) == (True, 2000)
    assert param.parse(2001)[0] is False
    assert StepperProbe.SPEED_SLIDER == (0.625, 2000)
    p, _, _ = make_probe(StepperProbe)
    assert p.run("_commit", inputs={name: "2000"}).is_ok
    assert getattr(p, name[:-len("_um_s")]) == 3200
    assert p.run("_commit", inputs={name: "2001"}).is_refused
    assert getattr(p, name[:-len("_um_s")]) == 3200


@pytest.mark.parametrize("cls,ceiling", CEILINGS)
def test_percent_to_steps_on_each_ceiling(cls, ceiling):
    p, port, _ = make_probe(cls)
    for pct, steps in ((100, ceiling), (50, ceiling // 2), (1, round(ceiling / 100))):
        p.full_speed_pct = pct
        p.man_full_speed_pct = pct
        assert p.full_speed == p.man_full_speed == steps
        assert p.full_speed_pct == p.man_full_speed_pct == pct
    # Zero percent is the slowest the firmware may be told, never 0.
    p.full_speed_pct = 0
    assert p.full_speed == 1
    assert p.full_speed_pct == 0
    assert cls.pct_to_steps(0) == 1


def test_the_defaults_read_13_on_the_stepper_and_67_on_the_chuck():
    stepper, _, _ = make_probe(StepperProbe)
    chuck, _, _ = make_probe(ChuckPositioner)
    dc, _, _ = make_probe(DCProbe)
    assert stepper.full_speed == chuck.full_speed == 400   # the stored default
    assert (stepper.full_speed_pct, stepper.man_full_speed_pct) == (13, 13)
    assert (chuck.full_speed_pct, chuck.man_full_speed_pct) == (67, 67)
    assert dc.full_speed == 120 and dc.full_speed_pct == 4


def test_the_two_representations_are_one_value_both_ways():
    p, _, _ = make_probe(StepperProbe)
    p.full_speed = 1600
    assert p.full_speed_pct == 50
    p.full_speed_pct = 25
    assert p.full_speed == 800
    p.man_full_speed = 1000            # 31.25 % -> nearest percent
    assert p.man_full_speed_pct == 31
    assert p.man_full_speed == 1000    # reading the dial never rewrites steps/s
    # Re-sending the percent the dial already shows (a Step does) is no edit.
    p.full_speed = 400
    p.full_speed_pct = 13
    assert p.full_speed == 400


@pytest.mark.parametrize("cls,steps", [(DCProbe, 1280), (ChuckPositioner, 240)])
def test_a_percent_out_of_range_is_refused_by_the_dial(cls, steps):
    p, _, _ = make_probe(cls)
    assert p.run("_commit", inputs={"full_speed_pct": "101"}).is_refused
    assert p.run("_commit", inputs={"full_speed_pct": "-1"}).is_refused
    assert p.run("_commit", inputs={"full_speed_pct": "40"}).is_ok
    assert p.full_speed == steps


def test_a_stepper_speed_out_of_range_is_refused_by_the_um_entry():
    """The Stepper has no dial; its speed entry is bounded 0.625..2000 um/s."""
    p, _, _ = make_probe(StepperProbe)
    assert p.run("_commit", inputs={"full_speed_um_s": "2001"}).is_refused
    assert p.run("_commit", inputs={"full_speed_um_s": "-1"}).is_refused
    assert p.run("_commit", inputs={"full_speed_um_s": "0"}).is_refused
    assert p.run("_commit", inputs={"full_speed_um_s": "800"}).is_ok
    assert p.full_speed == 1280
    assert p.full_speed_um_s == 800


def test_steps_per_second_still_apply_by_name_to_the_stored_value():
    """Profiles and older callers write steps/s; it applies, is bounded by the
    class ceiling, and the dial follows."""
    chuck, _, _ = make_probe(ChuckPositioner)
    assert chuck.apply_defaults({"full_speed": 300, "man_full_speed": 150}) == {}
    assert (chuck.full_speed, chuck.man_full_speed) == (300, 150)
    assert (chuck.full_speed_pct, chuck.man_full_speed_pct) == (50, 25)
    refused = chuck.apply_defaults({"full_speed": 3200})
    assert "full_speed" in refused and chuck.full_speed == 300
    assert chuck.run("_commit", inputs={"full_speed": "450"}).is_ok
    assert chuck.full_speed_pct == 75
    chuck.set_mode("autonomous")
    assert chuck.run("_commit", inputs={"full_speed": "100"}).is_refused
    assert chuck.run("_commit", inputs={"full_speed_pct": "10"}).is_refused


def test_the_wire_carries_the_steps_per_second_the_dial_set():
    p, port, _ = make_probe(StepperProbe)
    p.full_speed_pct = 50
    p.x_dist = 3
    p.step()
    sent = [w.payload for w in port.calls if b"1600.0" in w.payload]
    assert sent, [w.payload for w in port.calls]


@pytest.mark.schema
@pytest.mark.parametrize("cls", [DCProbe, ChuckPositioner])
def test_each_speed_is_a_percent_dial_with_a_steps_readout_in_its_group(cls):
    """The two speeds (2026-10-07): each is a percent dial with its steps/s
    readout under it, now in its own control system's group."""
    p, _, _ = make_probe(cls)
    groups = {s["title"]: s for s in p.schema["sections"]}
    for title, attr, label in (("Autonomous", "full_speed", "Autonomous Speed:"),
                               ("Manual", "man_full_speed", "Manual Speed:")):
        elements = groups[title]["elements"]
        kinds = [(e["type"], e.get("model_attr"), e.get("secondary", False))
                 for e in elements]
        at = kinds.index(("entry", attr + "_pct", False))
        assert kinds[at + 1] == ("readonly", attr, True)
        dial, readout = elements[at], elements[at + 1]
        assert dial["text"] == label
        assert dial["unit"] == "%" and (dial["min"], dial["max"]) == (0, 100)
        assert dial["slider"] == [0, 100]
        assert readout["unit"] == "steps/s" and readout["text"] == "steps/s"
        # Not a rail reading, not a second Position-style key number.
        assert not any(e.get("rail") for e in elements)
    state = p.state["values"]
    assert state["full_speed_pct"] == str(p.full_speed_pct)
    assert state["full_speed"] == str(p.full_speed)


@pytest.mark.schema
def test_each_stepper_speed_is_a_um_s_entry_with_a_steps_readout_in_its_group():
    """Owner 2026-10-09: the Stepper's speeds are um/s entries (no percent
    dial), each with its steps/s line directly beneath, in its own group."""
    p, _, _ = make_probe(StepperProbe)
    groups = {s["title"]: s for s in p.schema["sections"]}
    for title, attr, label in (("Autonomous", "full_speed", "Autonomous Speed:"),
                               ("Manual", "man_full_speed", "Manual Speed:")):
        elements = groups[title]["elements"]
        kinds = [(e["type"], e.get("model_attr"), e.get("secondary", False))
                 for e in elements]
        at = kinds.index(("entry", attr + "_um_s", False))
        assert kinds[at + 1] == ("readonly", attr, True)
        entry, readout = elements[at], elements[at + 1]
        assert entry["text"] == label
        assert entry["unit"] == "µm/s"
        assert list(entry["slider"]) == [0.625, 2000]
        assert readout["unit"] == "steps/s" and readout["text"] == "steps/s"
        assert not any(e.get("rail") for e in elements)
        assert not any(e.get("model_attr") == attr + "_pct" for e in elements)
    state = p.state["values"]
    assert float(state["full_speed_um_s"]) == p.full_speed_um_s == 250
    assert state["full_speed"] == str(p.full_speed)


@pytest.mark.schema
def test_the_stepper_summary_divides_autonomous_from_manual_in_um():
    """The same division as the other probes, through the Stepper's um and
    um/s entries (owner 2026-10-09); every gate is the one it was."""
    p, _, _ = make_probe(StepperProbe)
    tier1 = [s for s in p.schema["sections"] if s.get("tier", 1) == 1
             and s["title"] != "Safety"]
    assert [s["title"] for s in tier1] == ["Position", "Autonomous", "Manual"]
    auto, manual = tier1[1], tier1[2]
    assert auto["layout"] == manual["layout"] == "group"

    def drawn(section):
        return [e.get("model_attr") or e.get("command") for e in section["elements"]
                if e["type"] not in ("internal",) and not e.get("secondary")]

    assert drawn(auto) == ["x_dist_um", "y_dist_um", "z_dist_um",
                           "full_speed_um_s", "is_auto", "step"]
    assert drawn(manual) == ["gamepad_name", "man_full_speed_um_s", "is_manual"]
    gates = {e.get("model_attr") or e.get("command"): tuple(e.get("disabled_when", ()))
             for s in (auto, manual) for e in s["elements"]}
    assert gates["x_dist_um"] == gates["full_speed_um_s"] == ("autonomous", "manual")
    assert gates["man_full_speed_um_s"] == ("autonomous",)
    assert gates["step"] == ("manual", "latched", "fault")
    assert gates["is_auto"] == gates["is_manual"] == ("latched", "fault")
    config = next(s for s in p.schema["sections"] if s["title"] == "Configuration")
    in_config = [e["model_attr"] for e in config["elements"] if e["type"] == "entry"]
    assert in_config[:3] == ["x_step_um", "y_step_um", "z_step_um"]
    assert not {"x_dist_um", "y_dist_um", "z_dist_um"} & set(in_config)
    entries = [e["model_attr"] for s in p.schema["sections"] for e in s["elements"]
               if e["type"] == "entry"]
    assert sorted(entries) == sorted(p.ENTRY_PARAMS)


@pytest.mark.schema
@pytest.mark.parametrize("cls", [DCProbe, ChuckPositioner])
def test_the_summary_divides_autonomous_from_manual(cls):
    """Owner, 2026-10-07: "a division for autonomous and manual controls".
    Tier 1 is Position, then an Autonomous group (the targets, the
    autonomous speed, its mode, Step) and a Manual group (the gamepad, the
    manual speed, its mode); each is drawn as a titled group. Only the
    grouping moved: the step sizes (both systems use them) stay in
    Configure, and every gate is the one it was."""
    p, _, _ = make_probe(cls)
    tier1 = [s for s in p.schema["sections"] if s.get("tier", 1) == 1
             and s["title"] != "Safety"]
    assert [s["title"] for s in tier1] == ["Position", "Autonomous", "Manual"]
    auto, manual = tier1[1], tier1[2]
    assert auto["layout"] == manual["layout"] == "group"

    def drawn(section):
        return [e.get("model_attr") or e.get("command") for e in section["elements"]
                if e["type"] not in ("internal",) and not e.get("secondary")]

    assert drawn(auto) == ["x_dist", "y_dist", "z_dist", "full_speed_pct", "is_auto", "step"]
    assert drawn(manual) == ["gamepad_name", "man_full_speed_pct", "is_manual"]
    gates = {e.get("model_attr") or e.get("command"): tuple(e.get("disabled_when", ()))
             for s in (auto, manual) for e in s["elements"]}
    assert gates["x_dist"] == gates["full_speed_pct"] == ("autonomous", "manual")
    assert gates["man_full_speed_pct"] == ("autonomous",)
    assert gates["step"] == ("manual", "latched", "fault")
    assert gates["is_auto"] == gates["is_manual"] == ("latched", "fault")
    config = next(s for s in p.schema["sections"] if s["title"] == "Configuration")
    in_config = [e["model_attr"] for e in config["elements"]]
    assert in_config[:3] == ["x_step", "y_step", "z_step"]
    assert not {"x_dist", "y_dist", "z_dist"} & set(in_config)
    # every entry is drawn exactly once
    entries = [e["model_attr"] for s in p.schema["sections"] for e in s["elements"]
               if e["type"] == "entry"]
    assert sorted(entries) == sorted(p.ENTRY_PARAMS)
