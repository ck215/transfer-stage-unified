"""`station.models.rotator.Rotator`.

Ported from `tests/core/test_transport_truth.py` (ROTATOR-4, ROTATOR-8),
`test_edge_mvc_model.py` (ROTATOR-11, ROTATOR-13), `test_rotator9_poll_
failures.py`, `test_rotator6_model_owned_sampler.py` + `_extras.py`
(ROTATOR-6) and `test_errors7_rotator_share.py` (ERRORS-7), adapted to the
station's contracts: a command returns a value or raises `Refused` /
`NeedsConfirm`, the latch lives in `Model`, and the sampler starts in
`Model.open`.

The one test here that no old file contains is
`test_home_refused_while_latched_does_not_move_the_reference` -- review
finding 2, the defect the redesign of `home()` exists to remove.
"""
import threading
import time

import pytest

from station import schema as sch
from station.events import events
from station.models import rotator as rotator_module
from station.models.rotator import Rotator
from station.result import NeedsConfirm, Refused


class FakeSMC:
    """A stage that answers instantly unless a test tells it not to."""

    def __init__(self, position=0.0, state="32", errors=0):
        self.position = position
        self.state = state
        self.errors = errors
        self.is_open = True
        self.opened = 0
        self.closed = 0
        self.polls = 0
        self.stops = []
        self.calls = []
        self.status = "verified"
        self.failing = False
        self.block = threading.Event()      # set -> get_position_deg hangs
        self.released = threading.Event()
        self.moving = threading.Event()     # set -> a move blocks
        self.raises = None                  # exception for the next move

    # Device
    def open(self):
        self.opened += 1

    def close(self):
        self.closed += 1
        self.is_open = False

    # protocol
    def get_position_deg(self):
        self.polls += 1
        if self.block.is_set():
            self.released.wait(10)
        if self.failing:
            raise TimeoutError("simulated read timeout")
        return self.position

    def get_status(self):
        if self.failing:
            raise TimeoutError("simulated read timeout")
        return self.errors, self.state

    def stop(self, priority=False):
        self.stops.append(priority)
        return True

    def home(self):
        self._move("home")

    def move_absolute_deg(self, target, wait_stop=True):
        self._move(("move_absolute_deg", target))

    def move_relative_deg(self, step, wait_stop=True):
        self._move(("move_relative_deg", step))

    def reset_and_configure(self):
        self._move("reset_and_configure")

    def _move(self, call):
        self.calls.append(call)
        if self.moving.is_set():
            self.released.wait(10)
        if self.raises is not None:
            raise self.raises


def _rotator(smc=None, position=None, monkeypatch=None):
    """Build a Rotator through its real constructor, on a fake stage."""
    smc = smc if smc is not None else FakeSMC()
    built = []

    def factory(smc_id, port, **kwargs):
        built.append(kwargs)
        return smc

    original = rotator_module.SMC100
    rotator_module.SMC100 = factory
    try:
        model = Rotator(port="/dev/ttyS5")
    finally:
        rotator_module.SMC100 = original
    model._built_with = built[0] if built else {}
    if position is not None:
        model._publish(position, "Ready")
    return model


def _settle(model):
    try:
        model.close()
    except Exception:
        pass


def _wait_until(predicate, deadline=3.0):
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


# -- the stage carries the model's latch into the port lock ----------------

def test_the_stage_is_built_with_the_models_latch_as_abort_if():
    """The check that counts is the one inside the port lock: the latch
    reaches `SerialPort.write` through the driver, not through a re-check on
    the worker thread where its refusal could only ever be a print."""
    model = _rotator()
    assert model._built_with.get("abort_if") is not None
    assert model._built_with["abort_if"]() is False
    model.estop()
    assert model._built_with["abort_if"]() is True


# -- ROTATOR-4: the tubing guard -------------------------------------------

def test_an_absolute_move_past_the_limit_asks_first():
    model = _rotator(position=0.0)
    model.target_deg = 40
    with pytest.raises(NeedsConfirm) as ask:
        model.move_to()
    assert ask.value.command == "move_to"
    assert "40.00" in ask.value.prompt
    assert "tubing" in ask.value.prompt
    assert not model.smc.calls, "the stage moved before the operator answered"


def test_a_confirmed_move_past_the_limit_proceeds():
    model = _rotator(position=0.0)
    model.target_deg = 40
    assert model.move_to(True) is True
    assert _wait_until(lambda: model.smc.calls)
    assert model.smc.calls[0] == ("move_absolute_deg", 40.0)


def test_a_relative_move_is_judged_on_where_it_lands():
    """A small step from a large angle still leaves the safe range."""
    model = _rotator(position=29.0)
    model.step_deg = 5
    with pytest.raises(NeedsConfirm):
        model.move_by(1)
    model.step_deg = 1
    assert model.move_by(1) is True


def test_an_unknown_position_is_not_the_origin():
    """Failure scenario A: the stage is really at 25, the model has never
    polled, and a +10 step computes to 10 against a default of 0."""
    model = _rotator()
    assert model.position is None, "test premise: nothing has been polled"
    model.step_deg = 10
    with pytest.raises(NeedsConfirm) as ask:
        model.move_by(1)
    assert "unknown" in ask.value.prompt.lower()


def test_stacked_clicks_accumulate_toward_the_guard():
    """Failure scenario B: at 20 with step 4, five clicks reach 40 because
    each one recomputes from a position that has not moved yet."""
    model = _rotator(position=20.0)
    model.step_deg = 4
    accepted, asked = 0, 0
    for _ in range(5):
        try:
            model.move_by(1)
            accepted += 1
        except NeedsConfirm:
            asked += 1
        except Refused:
            asked += 1
        _wait_until(lambda: not model._motion_lock.locked(), 1.0)
    assert asked, f"five +4 clicks from 20 reach 40 and none asked ({accepted} accepted)"


def test_the_commanded_target_tracks_accepted_moves():
    model = _rotator(position=0.0)
    model.step_deg = 10
    for expected in (10.0, 20.0, 30.0):
        assert model.move_by(1) is True
        assert model._commanded_target == expected
        assert _wait_until(lambda: not model._motion_lock.locked())
    with pytest.raises(NeedsConfirm):
        model.move_by(1)          # -> 40, past the limit


def test_a_negative_step_is_the_same_command_with_a_sign():
    model = _rotator(position=0.0)
    model.step_deg = 10
    assert model.move_by(-1) is True
    assert _wait_until(lambda: model.smc.calls)
    assert model.smc.calls[0] == ("move_relative_deg", -10.0)
    assert model._commanded_target == -10.0


def test_the_confirmation_of_a_relative_move_carries_its_sign():
    """`NeedsConfirm.rerun_args` is re-run as `command(*rerun_args, True)`; without the
    sign, answering "yes" to a Move - would run a Move +."""
    model = _rotator(position=0.0)
    model.step_deg = 40
    with pytest.raises(NeedsConfirm) as ask:
        model.move_by(-1)
    assert ask.value.rerun_args == (-1,)
    assert model.move_by(*ask.value.rerun_args, True) is True
    assert _wait_until(lambda: model.smc.calls)
    assert model.smc.calls[0] == ("move_relative_deg", -40.0)


# -- review finding 2: home commits only after the guard -------------------

def test_home_refused_while_latched_does_not_move_the_reference():
    """The regression this redesign exists for.

    `home()` used to commit a target of 0 and *then* dispatch, and the
    dispatch refused because the FULL STOP latch was set. The refused Home
    left the guard believing the stage was at the origin while it sat at 40,
    so the next +10 step computed to 10 and sailed through a check that
    exists to stop exactly that.
    """
    model = _rotator(position=0.0)
    model.target_deg = 40
    assert model.move_to(True) is True
    assert model._commanded_target == 40.0, "test premise"
    assert _wait_until(lambda: not model._motion_lock.locked())

    model.estop()
    model._commit_target(40.0)      # the stage really is at 40

    with pytest.raises(Refused):
        model.home()

    assert model._commanded_target == 40.0, (
        "a Home refused by the FULL STOP latch reset the reference to the "
        "origin; the tubing guard now measures the next move from a place "
        "the stage is nowhere near")
    assert not model.smc.calls[1:], "a latched rotator dispatched a home"


def test_home_commits_the_origin_once_it_is_allowed_to_run():
    """The negative case: refusing everything would also pass the test above."""
    model = _rotator(position=40.0)
    model._commit_target(40.0)
    assert model.home() is True
    assert model._commanded_target == 0.0
    assert _wait_until(lambda: model.smc.calls == ["home"])


# -- a stop forgets the commanded target -----------------------------------

def test_a_full_stop_makes_the_position_unknown_again():
    """The stage halts wherever it was, not at the commanded target."""
    model = _rotator(position=0.0)
    model.step_deg = 5
    assert model.move_by(1) is True
    assert model._commanded_target == 5.0
    model.estop()
    assert model._commanded_target is None


def test_a_halt_also_forgets_the_target_so_the_next_relative_move_has_no_reference():
    """`halt` sets no latch, so nothing else stands between the operator and
    the next click: the forgotten target is the whole of the guard here. With
    no poll to fall back on either, the move has to ask."""
    model = _rotator()
    model.target_deg = 10
    assert model.move_to() is True
    assert model._commanded_target == 10.0
    assert _wait_until(lambda: not model._motion_lock.locked())

    model.halt()
    assert model._commanded_target is None

    model.step_deg = 5
    with pytest.raises(NeedsConfirm) as ask:
        model.move_by(1)
    assert "unknown" in ask.value.prompt.lower()


def test_a_move_that_did_not_finish_forgets_where_it_was_going():
    """ROTATOR-11: a wait that ended does not mean a stage that stopped. The
    target must not survive as if the move had landed."""
    model = _rotator()          # nothing polled: the target is the only reference
    model.smc.raises = RuntimeError("Wait timed out; the stage has not been stopped")
    model.target_deg = 10
    assert model.move_to() is True
    assert _wait_until(lambda: model._commanded_target is None), (
        "a move that failed left its target behind as if it had arrived")
    assert _wait_until(lambda: not model._motion_lock.locked())

    # ...so the next relative move is measured from the last polled position,
    # and with nothing polled it asks instead of assuming the origin.
    model.step_deg = 1
    with pytest.raises(NeedsConfirm):
        model.move_by(1)


def test_a_successful_move_keeps_its_target():
    """Guard: only a *failed* move forgets. Dropping the target after every
    move would put the guard back on the polled position."""
    model = _rotator(position=0.0)
    model.target_deg = 10
    assert model.move_to() is True
    assert _wait_until(lambda: model.smc.calls)
    time.sleep(0.05)
    assert model._commanded_target == 10.0


# -- the busy guard --------------------------------------------------------

def test_a_second_move_while_one_is_in_flight_is_refused():
    """Each click used to spawn its own thread straight into the driver, so
    the moves raced and their effects accumulated with no ordering."""
    smc = FakeSMC()
    smc.moving.set()
    model = _rotator(smc, position=0.0)
    model.target_deg = 10
    assert model.move_to() is True
    assert _wait_until(lambda: smc.calls)
    try:
        with pytest.raises(Refused) as refusal:
            model.move_to()
        assert "in flight" in refusal.value.reason
        assert len(smc.calls) == 1
    finally:
        smc.released.set()


def test_the_lane_is_released_when_a_move_fails():
    smc = FakeSMC()
    smc.raises = RuntimeError("boom")
    model = _rotator(smc, position=0.0)
    model.target_deg = 10
    assert model.move_to() is True
    assert _wait_until(lambda: not model._motion_lock.locked()), (
        "a failed move left the motion lane held; every later move is refused")


# -- ROTATOR-13: a disconnected rotator refuses, with a reason --------------

@pytest.mark.parametrize("port", [None, "None", "SIM"])
def test_a_rotator_with_no_stage_builds_no_device(port):
    model = Rotator(port=port)
    assert model.smc is None
    assert model.devices == []
    assert model.is_connected is False
    assert model.mode_name == "disconnected"


def test_every_command_refuses_when_there_is_no_stage():
    """Not silently skipped: with no port every control used to do nothing
    and say nothing. There is no rotator simulator to fall back on."""
    model = Rotator(port="SIM")
    for command in ("home", "move_to", "move_by", "configure"):
        with pytest.raises(Refused) as refusal:
            getattr(model, command)()
        assert "not connected" in refusal.value.reason.lower(), command


def test_the_schema_gates_every_control_on_a_live_link():
    """One declaration, three views. `Panel.run` refuses from the same gate,
    so the Web API cannot call what the schema shows greyed out."""
    model = Rotator(port="SIM")
    for element in sch.elements(model.schema):
        if element.get("command") in ("move_to", "move_by", "home", "halt",
                                      "configure"):
            assert sch.is_enabled(element, "disconnected") is False, element
    result = model.run("home")
    assert result.is_refused
    assert not result.is_ok


def test_a_live_stage_is_reported_as_hardware_not_simulation():
    model = _rotator()
    assert model.is_connected is True
    assert model.mode_name == "ready"
    model.smc.is_open = False
    assert model.is_connected is False


# -- ERRORS-7: a refusal is never reported as success ----------------------

def test_a_latched_home_comes_back_refused_through_run():
    model = _rotator(position=0.0)
    model.estop()
    result = model.run("home")
    assert result.is_refused, (
        f"Home on a latched rotator came back {result.status}; the operator "
        f"is shown a success for an action that was refused")
    assert "stop" in result.reason.lower()
    assert not model.smc.calls


def test_a_confirmation_comes_back_as_needs_confirm_through_run():
    model = _rotator(position=0.0)
    result = model.run("move_to", inputs={"target_deg": "40"})
    assert result.needs_confirm
    assert result.command == "move_to"
    assert not model.smc.calls
    assert model.run("move_to", args=(True,)).is_ok


def test_an_unparseable_entry_is_refused_by_name():
    model = _rotator(position=0.0)
    result = model.run("move_to", inputs={"target_deg": "nan"})
    assert result.is_refused
    assert "Target" in result.reason


# -- ROTATOR-8: a stop is never delayed by a poll --------------------------

def test_a_wedged_poll_does_not_delay_the_stop():
    """A sampler that holds the transport across a blocking read reintroduces
    exactly the defect ROTATOR-8 closed: FULL STOP queued behind a status
    transaction while the stage keeps turning."""
    smc = FakeSMC()
    smc.block.set()
    model = _rotator(smc)
    model.open()
    try:
        assert _wait_until(lambda: smc.polls > 0), "no sampler to wedge"
        started = time.monotonic()
        model.estop()
        elapsed = time.monotonic() - started
        assert elapsed < model.ESTOP_BUDGET + 1.0, (
            f"FULL STOP took {elapsed:.2f}s with a poll wedged")
        assert model.is_estopped
        assert _wait_until(lambda: smc.stops == [True]), (
            f"the stop did not take the priority lane: {smc.stops}")
    finally:
        smc.released.set()
        _settle(model)


def test_the_stop_is_confirmed_only_when_it_was_written():
    class Deaf(FakeSMC):
        def stop(self, priority=False):
            self.stops.append(priority)
            return False

    model = _rotator(Deaf())
    assert model.estop() is False, (
        "a stop the port never wrote was reported as confirmed")
    assert model.halt() is False


def test_the_latch_clears_only_by_an_operator_action():
    model = _rotator(position=0.0)
    model.estop()
    with pytest.raises(NeedsConfirm):
        model.clear_estop()
    assert model.is_estopped
    model.clear_estop(True)
    assert not model.is_estopped
    model._publish(0.0, "Ready")
    model.step_deg = 5
    assert model.move_by(1) is True


# -- ROTATOR-6 / ROTATOR-9: the model owns its sampler ---------------------

def test_position_is_published_with_nobody_calling_poll():
    """The seam: `_poll` is the only writer of live position and state, and
    the model is the only thing that calls it."""
    smc = FakeSMC(position=11.0)
    model = _rotator(smc)
    model.open()
    try:
        assert _wait_until(lambda: model.position is not None), (
            "no background sampler ever polled the stage")
        assert model.position == 11.0
        assert model.motion_state == "Ready"
    finally:
        _settle(model)


def test_the_published_position_keeps_tracking_the_stage():
    class Walking(FakeSMC):
        def get_position_deg(self):
            self.polls += 1
            return 10.0 + self.polls

    model = _rotator(Walking())
    model.open()
    try:
        assert _wait_until(lambda: model.position is not None)
        first = model.position
        assert _wait_until(lambda: model.position != first), (
            f"position published {first} once and then stopped changing")
    finally:
        _settle(model)


def test_a_poll_failure_is_visible_and_clears_the_position():
    """ROTATOR-9: the display must not show a confident "Ready" for a stage
    that is not answering."""
    smc = FakeSMC(position=20.0)
    model = _rotator(smc)
    model.open()
    try:
        assert _wait_until(lambda: model.position is not None)
        smc.failing = True
        assert _wait_until(lambda: model.motion_state == "Communication lost"), (
            f"the sampler kept reporting {model.motion_state!r}")
        assert model.position is None, "a stale position outlived the link"
        smc.failing = False
        assert _wait_until(lambda: model.motion_state == "Ready"), (
            "'Communication lost' latched; the sampler never recovered")
        assert model.position == 20.0
    finally:
        _settle(model)


def test_a_poll_failure_publishes_once_per_transition_not_once_per_tick():
    """Nothing in a loop publishes per iteration. The transition is worth a
    line in the log panel; the 4 Hz repeat is not."""
    smc = FakeSMC()
    model = _rotator(smc)
    seen = []
    events.clear()          # the dedupe window is shared with the test above
    events.subscribe(lambda event: seen.append(event))
    model.open()
    try:
        assert _wait_until(lambda: model.motion_state == "Ready")
        smc.failing = True
        assert _wait_until(lambda: model.motion_state == "Communication lost")
        time.sleep(4 * model.SAMPLE_INTERVAL)
        warnings = [e for e in seen if e.severity == "warning"
                    and e.source == model.NAME]
        assert len(warnings) == 1, (
            f"{len(warnings)} separate warnings for one communication loss")
    finally:
        events._subscribers.clear()
        _settle(model)


def test_an_unconnected_model_runs_no_sampler_thread():
    before = threading.active_count()
    models = [Rotator(port=None) for _ in range(5)]
    for model in models:
        model.open()
    time.sleep(0.3)
    assert threading.active_count() <= before + 1, (
        "an unconnected model has nothing to sample")
    assert all(model.position is None for model in models)


def test_closing_stops_the_sampler():
    smc = FakeSMC()
    model = _rotator(smc)
    model.open()
    assert _wait_until(lambda: smc.polls > 0)
    model.close()
    settled = smc.polls
    time.sleep(3 * model.SAMPLE_INTERVAL)
    assert smc.polls == settled, (
        f"the sampler kept polling after close ({settled} -> {smc.polls})")
    assert smc.closed == 1


def test_closing_stops_the_stage_before_it_closes_the_port():
    """ROTATOR-1: tearing the rotator down used to release the port without
    sending ST, so a stage mid-move kept moving with nothing able to stop it."""
    smc = FakeSMC()
    model = _rotator(smc)
    model.open()
    model.close()
    assert smc.stops, "close() released the port without stopping the stage"


def test_the_controller_error_word_becomes_the_models_fault():
    smc = FakeSMC(errors=0x001C)
    model = _rotator(smc)
    model.open()
    try:
        assert _wait_until(lambda: model.is_faulted)
        assert "001C" in model.fault
        smc.errors = 0
        assert _wait_until(lambda: not model.is_faulted)
    finally:
        _settle(model)


# -- the published surface -------------------------------------------------

def test_position_and_motion_state_are_read_only():
    """The old setters let a view or a test write a position the hardware
    never reported -- and the tubing guard was checked against it."""
    model = _rotator()
    for name in ("position", "motion_state"):
        with pytest.raises(AttributeError):
            setattr(model, name, 5.0)


def test_state_carries_an_age_so_a_view_can_grey_out_a_stale_value():
    model = _rotator(position=1.0)
    snapshot = model.state
    assert snapshot["name"] == "Rotator"
    assert "age" in snapshot
    assert snapshot["values"]["position"] == "1.0"
    assert snapshot["devices"] == {"FakeSMC": "verified"}


def test_an_unread_position_renders_as_absent_not_as_the_word_none():
    model = _rotator()
    assert model.state["values"]["position"] == ""


def test_the_schema_ends_with_the_safety_section():
    model = _rotator()
    sections = model.schema["sections"]
    assert sections[-1]["title"] == "Safety"
    commands = {e.get("command") for e in sch.elements(model.schema)}
    assert {"home", "move_to", "move_by", "halt", "configure",
            "toggle_estop"} <= commands
    attributes = {e.get("model_attr") for e in sch.elements(model.schema)}
    assert {"position", "motion_state", "target_deg", "step_deg"} <= attributes


def test_only_the_two_entries_are_writable():
    """STEPPER-11/DC-11's rule on this model: `writable` has to be asked for,
    and a readout never asks."""
    schema = _rotator().schema
    writable = {e["model_attr"] for e in sch.elements(schema) if e.get("writable")}
    assert writable == {"target_deg", "step_deg"}, schema


def test_the_step_buttons_carry_their_sign():
    model = _rotator()
    signs = [e.get("args") for e in sch.elements(model.schema)
             if e.get("command") == "move_by"]
    assert signs == [[1], [-1]], (
        "a view cannot tell Move + from Move - without the sign in the schema")


def test_client_liveness_is_gone():
    """The browser heartbeat belongs to the Web view, which calls
    `Controller.estop_all`. A model does not watch a client."""
    for gone in ("WEB_CLIENT_WARN_TIMEOUT", "WEB_CLIENT_STOP_TIMEOUT",
                 "_client_liveness_active", "_init_client_liveness",
                 "stop_client_liveness_watchdog", "connection_status"):
        assert not hasattr(Rotator, gone), f"{gone} came back"


# -- the two halves, joined ------------------------------------------------
#
# Everything above drives a fake stage. This drives the real `SMC100` over
# the real `SerialPort`, with only pyserial replaced -- the seam where a
# model half and a transport half that were each correct in isolation have
# been joined wrongly before (WEB-19, 2026-09-20).

class _Handle:
    """A pyserial-shaped SMC100 that answers TS? and TP?."""

    def __init__(self):
        self.is_open = True
        self.wire = b""
        self._partial = b""
        self._pending = b""

    @property
    def in_waiting(self):
        return len(self._pending)

    def write(self, data):
        self.wire += bytes(data)
        self._partial += bytes(data)
        while b"\r\n" in self._partial:
            frame, self._partial = self._partial.split(b"\r\n", 1)
            body = frame.decode("ascii")[1:]
            if body == "TS?":
                self._pending += b"1TS000032\r\n"
            elif body == "TP?":
                self._pending += b"1TP12.5000\r\n"
        return len(data)

    def read(self, size=1):
        out, self._pending = self._pending[:size], self._pending[size:]
        return out

    def read_all(self):
        return self.read(self.in_waiting)

    def readline(self):
        return self.read_all()

    def reset_input_buffer(self):
        pass

    def reset_output_buffer(self):
        pass

    def flush(self):
        pass

    def close(self):
        self.is_open = False


def test_a_rotator_over_the_real_transport_polls_and_stops():
    from types import SimpleNamespace
    from unittest.mock import patch

    from station.devices import serial_port

    handle = _Handle()
    with patch.object(serial_port, "pyserial",
                      SimpleNamespace(Serial=lambda **kwargs: handle)):
        model = Rotator(port="/dev/fake-smc100")
        model.open()
        try:
            assert _wait_until(lambda: model.position == 12.5), (
                f"the stage never published through the real transport "
                f"({model.motion_state!r}, wire={handle.wire!r})")
            assert model.motion_state == "Ready"
            assert model.is_connected is True
            assert model.state["devices"]["SMC100"] == "verified", (
                "a handshake=False port opens UNVERIFIED; the driver must "
                "vouch for it once the controller answers")

            assert model.estop() is True
            assert handle.wire.endswith(b"1ST\r\n"), (
                f"FULL STOP did not reach the wire: {handle.wire!r}")
        finally:
            model.close()
    assert handle.is_open is False
