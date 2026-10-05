"""A probe board that stops talking while it is being driven (bench incident
2026-10-04 17:40, `~/transfer-stage-runs/incidents/2026-10-04_fullstop_probe.log`).

The Stepper Probe's firmware loop hung mid-jog: its POS lines (10 per second,
printed unasked in every mode) stopped at about 17:40:14. The host noticed
nothing. It kept streaming 42-byte jog frames at 50 Hz to the silent board
(a -1.0 axis at 17:40:15.7), and the FULL STOP at 17:40:18 wrote zero / `d` /
`k` and reported "latched and confirmed" -- confirmed meaning the bytes left
the host, not that the board did anything with them. The tip broke; the board
had to be reset by hand. After clearing the stop the operator re-entered
manual mode on the still-silent board (17:41:30) and the host jogged it again.

The host cannot unhang a board. What it can do, and what these tests hold:

* **(a)** POS silence while the probe is in a motion mode (MANUAL, AUTO) is a
  stop: the probe latches (an explicit, confirmed operator clear), the jog
  stream ends, and the operator gets an error that says the board is not
  answering and to cut its power or reset it. A silent board cannot be
  re-armed until it reports again.
* **(b)** A stop sent to a board that has gone silent is reported as *not*
  confirmed, with the same words, never as plain "confirmed".
* A healthy board never trips: POS flows in every mode, DISABLED and IDLE
  included, and a board that has never reported (no POS yet; firmware
  without the stream; the recording fakes) is not called silent.

Every probe shares this path (`Probe`), so the Chuck Positioner and the DC
Probe are covered by the same parametrised tests.
"""
import threading
import time

import pytest

from controller.controller import Controller
from events import events
from model.probe import ChuckPositioner, DCProbe, ProbeMode, StepperProbe
from result import Refused

#: Seconds of POS silence the tests use, so a trip is quick to see. The
#: class's own value is pinned separately below.
SILENT_AFTER = 0.3
POS_PERIOD = 0.02

LEVELS = {"axis_x": 0.5, "axis_y": 0.0, "trigger_left": -1.0,
          "trigger_right": -1.0, "hat_x": 0, "hat_y": 0,
          "bumper_left": 0, "bumper_right": 0}

ALL_PROBES = [StepperProbe, ChuckPositioner, DCProbe]


class LiveBoard:
    """A port whose board prints `POS:x,y,z` every POS_PERIOD while `alive`,
    as the firmware does in every mode, and records when each write left."""

    status = "simulated"
    is_open = True

    def __init__(self, alive=True):
        self.alive = alive
        self.timed_writes = []           # (monotonic, payload)
        self._lock = threading.Lock()
        self._last_pos = 0.0
        self.x = 0

    def open(self):
        pass

    def close(self):
        pass

    @property
    def writes(self):
        with self._lock:
            return [p for _t, p in self.timed_writes]

    def write(self, payload, *, priority=False, abort_if=None):
        if abort_if is not None and abort_if():
            return False
        with self._lock:
            self.timed_writes.append((time.monotonic(), payload))
        return True

    def read_line(self, timeout=None):
        now = time.monotonic()
        if self.alive and now - self._last_pos >= POS_PERIOD:
            self._last_pos = now
            self.x += 1
            return f"POS:{self.x},0,0"
        return None


class FakeGamepad:
    EDGE_KEYS = ("hat_x", "hat_y", "bumper_left", "bumper_right")
    status = "connected"

    def __init__(self, levels):
        self.levels = dict(levels)
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
        return True

    def drain_edges(self):
        return {}


class Collected:
    def __init__(self):
        self.seen = []

    def __enter__(self):
        events.clear()
        events.subscribe(self.seen.append)
        return self

    def __exit__(self, *exc):
        events.unsubscribe(self.seen.append)

    def errors(self):
        return [e for e in self.seen if e.severity == "error"]


def make(cls=StepperProbe, alive=True, levels=LEVELS):
    board = LiveBoard(alive=alive)
    probe = cls(port=board, gamepad=FakeGamepad(levels))
    probe.BOARD_SILENT_AFTER = SILENT_AFTER
    probe.GAMEPAD_RATE_HZ = 200
    return probe, board


def wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def stopped(probe):
    """The watchdog's stop has finished, not merely latched: the latch
    closes first and the halt runs after it on its own worker, so
    `is_estopped` alone is true before the mode has left MANUAL/AUTO."""
    return (probe.is_estopped and probe.stop_confirmed is not None
            and probe.mode not in (ProbeMode.MANUAL, ProbeMode.AUTO))


def is_nonzero_jog(probe, payload):
    return len(payload) == 42 and payload != probe._jog_bytes({})


def not_answering(text):
    text = text.lower()
    return ("not answering" in text and "power" in text and "reset" in text)


@pytest.fixture
def running():
    """A started probe; its threads are always stopped."""
    made = []

    def start(cls=StepperProbe, alive=True, levels=LEVELS):
        probe, board = make(cls, alive, levels)
        made.append(probe)
        probe._start_threads()
        return probe, board

    yield start
    for probe in made:
        probe._stop_threads()


# -- the threshold ------------------------------------------------------------

@pytest.mark.parametrize("cls", ALL_PROBES)
def test_the_silence_threshold_is_ten_stepper_pos_periods(cls):
    """1.0 s: ten of the stepper/chuck firmware's 100 ms PRINT_INTERVAL
    (twenty of the DC board's 50 ms). On 2026-10-04 every steady 5-s window
    read 9.8-10.2 lines/s; the only dips (6.8/s) were the ~1.5 s handshake
    at open, when the probe is DISABLED and nothing watches."""
    assert cls.BOARD_SILENT_AFTER == 1.0


# -- (a) silence while driven is a latched stop --------------------------------

@pytest.mark.parametrize("cls", ALL_PROBES)
def test_a_board_that_goes_silent_while_jogging_is_latched_and_the_jog_stream_ends(
        running, cls):
    probe, board = running(cls)
    assert wait_for(lambda: probe.position_time is not None)
    with Collected() as seen:
        probe.set_mode("manual")
        assert wait_for(lambda: any(is_nonzero_jog(probe, p) for p in board.writes)), \
            "the jog stream must be running before the board goes quiet"
        board.alive = False
        went_silent = time.monotonic()

        assert wait_for(lambda: stopped(probe), timeout=SILENT_AFTER + 2.0), \
            "a board silent while jogging must latch the probe"
        time.sleep(0.15)      # the pump runs at 200 Hz: ample time to leak frames

    late = [t for t, p in board.timed_writes
            if is_nonzero_jog(probe, p) and t > went_silent + SILENT_AFTER + 0.1]
    assert late == [], f"{len(late)} non-zero jog frames went to a silent board"
    assert probe.mode is not ProbeMode.MANUAL
    assert probe.stop_confirmed is False
    errors = [e for e in seen.errors() if not_answering(e.message)]
    assert errors, [e.message for e in seen.errors()]
    assert errors[0].source == probe.NAME
    assert probe.NAME in errors[0].message


def test_a_board_that_goes_silent_during_an_autonomous_move_is_latched(running):
    probe, board = running()
    assert wait_for(lambda: probe.position_time is not None)
    probe.step()
    board.alive = False
    assert wait_for(lambda: stopped(probe), timeout=SILENT_AFTER + 2.0)
    assert probe.mode is not ProbeMode.AUTO
    assert probe.stop_confirmed is False


def test_the_silent_stop_needs_an_explicit_clear_and_a_silent_board_cannot_be_rearmed(
        running):
    probe, board = running()
    assert wait_for(lambda: probe.position_time is not None)
    probe.set_mode("manual")
    board.alive = False
    assert wait_for(lambda: stopped(probe), timeout=SILENT_AFTER + 2.0)

    with pytest.raises(Refused):
        probe.set_mode("manual")             # latched
    probe.clear_estop(confirmed=True)        # the operator's explicit clear
    for target in ("manual", "autonomous", "idle"):
        with pytest.raises(Refused) as refused:
            probe.set_mode(target)
        assert "reset" in refused.value.reason.lower()
    with pytest.raises(Refused):
        probe.step()
    assert probe.mode is ProbeMode.DISABLED
    assert not any(p == b"e" for p in board.writes[-5:]), \
        "a silent board must not be sent an enable"

    board.alive = True                       # the board was reset; POS is back
    assert wait_for(lambda: probe.position_age is not None
                    and probe.position_age < SILENT_AFTER)
    assert probe.set_mode("manual") == "manual"


def test_the_jog_pump_sends_neutral_to_a_silent_board_even_without_the_sampler():
    """Defence in depth: the sampler is what trips the latch, but the pump
    on its own never hands a silent board a non-zero frame."""
    probe, board = make()
    probe._note_position((1, 2, 3))
    probe.set_mode("manual")
    probe._position_time = time.monotonic() - (SILENT_AFTER + 1.0)
    board.timed_writes.clear()
    probe._gamepad_tick()
    jogs = [p for p in board.writes if len(p) == 42]
    assert jogs, "the pump still writes (a neutral frame stops a live board)"
    assert not any(is_nonzero_jog(probe, p) for p in jogs)


# -- (b) a stop to a silent board is not "confirmed" --------------------------

@pytest.mark.parametrize("cls", ALL_PROBES)
def test_a_full_stop_on_a_silent_board_is_not_reported_confirmed(cls):
    probe, board = make(cls, alive=False)
    controller = Controller()
    probe._note_position((4, 5, 6))
    # IDLE, not a motion mode: the watchdog does not trip there, so this is
    # the FULL STOP's own report, not the watchdog's.
    probe.set_mode("idle")
    with Collected() as seen:
        controller.add(probe.NAME, probe)
        try:
            probe._position_time = time.monotonic() - 4.0   # 17:40:14 -> :18
            results = controller.estop_all()
        finally:
            controller.remove(probe.NAME)
    assert results[probe.NAME] is False
    assert probe.stop_confirmed is False
    infos = [e.message for e in seen.seen
             if e.severity == "info" and e.title == "FULL STOP"]
    assert not any(probe.NAME in m for m in infos), infos
    assert any(not_answering(e.message) and e.source == probe.NAME
               for e in seen.errors()), [e.message for e in seen.errors()]


def test_a_stop_on_a_silent_board_still_sends_every_stop_byte():
    """Silence changes what is *reported*, never what is *sent*: the zero
    frame, `d` and `k` all still go out on the priority lane."""
    probe, board = make(alive=False)
    probe._note_position((1, 1, 1))
    probe.enable()
    probe._position_time = time.monotonic() - 4.0
    board.timed_writes.clear()
    assert probe.estop() is False
    assert board.writes == [probe._zero_frame(), b"d", b"k\n"]


# -- a healthy board never trips -----------------------------------------------

@pytest.mark.parametrize("cls", ALL_PROBES)
def test_a_healthy_board_never_trips_in_any_mode(running, cls):
    probe, board = running(cls)
    assert wait_for(lambda: probe.position_time is not None)
    with Collected() as seen:
        for target in ("idle", "manual", "autonomous", "disabled"):
            probe.set_mode(target)
            time.sleep(SILENT_AFTER * 2)
            assert not probe.is_estopped, target
    assert not seen.errors()
    assert probe.estop() is True, "a stop to a talking board is confirmed"


def test_a_silent_board_that_is_not_being_driven_is_not_stopped(running):
    """DISABLED and IDLE command no motion, so silence there latches
    nothing; arming it is what is refused."""
    probe, board = running()
    assert wait_for(lambda: probe.position_time is not None)
    probe.set_mode("idle")
    board.alive = False
    time.sleep(SILENT_AFTER * 3)
    assert not probe.is_estopped
    assert probe.mode is ProbeMode.IDLE


def test_a_board_that_has_never_reported_is_not_called_silent(running):
    """No POS yet (just opened; a board without the stream; the recording
    fakes): nothing to compare against, so nothing trips."""
    probe, board = running(alive=False)
    probe.set_mode("manual")
    time.sleep(SILENT_AFTER * 3)
    assert not probe.is_estopped
    assert probe.mode is ProbeMode.MANUAL
    assert any(is_nonzero_jog(probe, p) for p in board.writes)
    assert probe.estop() is True
