"""Heater-off is confirmed by the board's own word before a port closes, and
a board found heating at connect is reported and driven to 0 (owner,
2026-10-07: "closing that device/program should DISABLE the heater before
closing!" and "build the watchdog that confirms setpoint is 0 (disabled)
before close").

Every test here runs the Heater over a REAL `SerialPort` whose pyserial
handle is `FakeBoard`, a stand-in for `temp_controller.ino`: it parses the
frames, keeps its endpoint across hosts (the firmware has no watchdog) and
streams `timer , temp , setpoint` lines. No hardware is opened.
"""
import os
import signal
import subprocess
import sys
import textwrap
import threading
import time
from types import SimpleNamespace

import pytest

from controller.controller import Controller
from devices import serial_port
from devices.serial_port import SerialPort
from events import events
from model.heater import Heater

from test_heater_fakes import EventRecorder, FakeBoard, off_confirmed_before_close

TESTS = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(os.path.dirname(TESTS), "src")


@pytest.fixture(autouse=True)
def quiet_event_log():
    for reset in (events.clear, events._debug_seen.clear):
        reset()
    yield
    for reset in (events.clear, events._debug_seen.clear):
        reset()


@pytest.fixture
def board_factory(monkeypatch):
    """Patch pyserial so the next real SerialPort opens the given board."""
    monkeypatch.setattr(SerialPort, "BOOTLOADER_WAIT", 0.05)
    # Fast windows so a board that never confirms does not stall the suite.
    monkeypatch.setattr(Heater, "OFF_CONFIRM_SECONDS", 0.4)

    def make(**kwargs):
        board = FakeBoard(**kwargs)
        monkeypatch.setattr(serial_port, "pyserial",
                            SimpleNamespace(Serial=lambda **_kw: board))
        return board
    return make


def _wait(predicate, timeout=4.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _open(board):
    heater = Heater(port="/dev/fake-teensy")
    heater.open()
    assert _wait(lambda: heater.temperature.endswith("°C")), heater.temperature
    return heater


def _heat(heater, board, setpoint=80):
    heater.setpoint = setpoint
    assert heater.apply_settings() == setpoint
    assert _wait(lambda: any(e == ("served", float(setpoint)) for e in board.log))


# -- close: the read-back ----------------------------------------------------------

def test_close_reads_back_setpoint_zero_before_the_port_closes(board_factory):
    board = board_factory()
    heater = _open(board)
    _heat(heater, board)
    with EventRecorder(events) as log:
        heater.close()
    assert board.endpoint == 0
    assert off_confirmed_before_close(board.log), board.log[-12:]
    assert heater.off_confirmed is True
    assert log.of("error") == [], [e.text for e in log.of("error")]


def test_close_reads_back_even_when_the_heater_was_never_heated(board_factory):
    """Unconditional: the station cannot know what the board holds from an
    earlier session, so every close asks it."""
    board = board_factory()
    heater = _open(board)
    heater.close()
    assert off_confirmed_before_close(board.log), board.log[-12:]
    assert heater.off_confirmed is True


def test_a_board_that_never_reports_zero_is_retried_then_reported(board_factory):
    board = board_factory()
    heater = _open(board)
    _heat(heater, board)
    board.deaf = True                 # the off frames from here on do not take
    frames_before = len(board.frames)
    with EventRecorder(events) as log:
        heater.close()
    off_frames = [f for f in board.frames[frames_before:] if f.startswith(b"<0,")]
    # halt's off frame, disable's reset frame, then one off frame per retry
    assert len(off_frames) >= 2 + (Heater.OFF_CONFIRM_ATTEMPTS - 1), off_frames
    assert heater.off_confirmed is False
    errors = log.titled("Heater Off Not Confirmed")
    assert len(errors) == 1 and errors[0].severity == "error"
    assert errors[0].needs_ack is True, "it must stay in front of the operator"
    assert "80" in errors[0].message
    assert not board.is_open, "the port is still released at the end"


def test_close_confirms_while_latched_and_with_the_write_lock_held(board_factory):
    """A latched model and a stuck Enter Settings must not skip the off."""
    board = board_factory()
    heater = _open(board)
    _heat(heater, board)
    heater.estop()
    held = threading.Event()
    release = threading.Event()

    def _hog():
        with heater._write_lock:
            held.set()
            release.wait(10)

    threading.Thread(target=_hog, daemon=True).start()
    assert held.wait(1)
    try:
        heater.close()
    finally:
        release.set()
    assert board.endpoint == 0
    assert off_confirmed_before_close(board.log), board.log[-12:]
    assert heater.off_confirmed is True


def test_a_simulated_heater_closes_as_before_with_nothing_to_read_back():
    heater = Heater(sim=True)
    heater.open()
    with EventRecorder(events) as log:
        heater.close()
    assert heater.off_confirmed is None
    assert log.of("error") == []


# -- connect: a board found heating -------------------------------------------------

def test_a_board_found_heating_at_connect_is_reported_and_driven_to_zero(
        board_factory):
    """22:39 on 2026-10-07: the reset frame went out before anything was read,
    so the board's earlier setpoint was overwritten unseen. Now the first
    reading is looked at first."""
    board = board_factory(endpoint=67.0)
    with EventRecorder(events) as log:
        heater = _open(board)
        try:
            assert _wait(lambda: heater.off_confirmed is not None)
        finally:
            seen = list(log.seen)
            heater.close()
    found = [e for e in seen if e.title == "Heater Was On At Connect"]
    assert len(found) == 1, [e.text for e in seen]
    assert found[0].severity == "error" and "67" in found[0].message
    assert heater.off_confirmed is True
    # The first thing the board heard was the off (the reset frame).
    assert board.frames[0] == Heater.RESET_FRAME
    # ... and only after the host had read the setpoint it reported.
    first_frame = next(i for i, e in enumerate(board.log) if e[0] == "frame")
    assert ("served", 67.0) in board.log[:first_frame]
    assert not any(e.title == "Heater Off Not Confirmed" for e in seen)


def test_a_board_at_zero_at_connect_gets_its_reset_frame_and_no_warning(
        board_factory):
    board = board_factory(endpoint=0.0)
    with EventRecorder(events) as log:
        heater = _open(board)
        assert _wait(lambda: board.frames)
        seen = list(log.seen)
    heater.close()
    assert board.frames[0] == Heater.RESET_FRAME
    assert [e for e in seen if e.severity == "error"] == []
    assert not any(e.title == "Heater Was On At Connect" for e in seen)


def test_a_board_heating_at_connect_that_will_not_stop_stays_visible(board_factory):
    board = board_factory(endpoint=67.0, deaf=True)
    with EventRecorder(events) as log:
        heater = _open(board)
        try:
            assert _wait(lambda: heater.off_confirmed is False, timeout=6)
            assert heater.is_active, "the watchdog and the panel must see it heating"
            assert "67" in heater.heating_to
            seen = list(log.seen)
        finally:
            heater.close()
    assert any(e.title == "Heater Was On At Connect" for e in seen)
    assert any(e.title == "Heater Off Not Confirmed" and e.needs_ack for e in seen)


def test_a_close_during_the_connect_check_is_not_held_up_by_it(board_factory):
    """The reader's connect read-back leaves the moment a close starts; the
    close then runs its own read-back and reports."""
    board = board_factory(endpoint=67.0, deaf=True)
    with EventRecorder(events) as log:
        heater = _open(board)
        assert _wait(lambda: any(e.title == "Heater Was On At Connect"
                                 for e in log.seen))
        started = time.monotonic()
        heater.close()
        took = time.monotonic() - started
        seen = list(log.seen)
    assert not any(e.title == "Thread Still Running" for e in seen)
    assert heater.off_confirmed is False
    assert took < Heater.OFF_CONFIRM_ATTEMPTS * Heater.OFF_CONFIRM_SECONDS + 1.5
    assert not board.is_open


def test_a_setpoint_the_station_sent_itself_is_not_reported_at_connect(
        board_factory):
    """Enter Settings before the first reading: the board heating is ours."""
    board = board_factory(endpoint=0.0)
    heater = Heater(port="/dev/fake-teensy")
    heater.setpoint = 50
    with EventRecorder(events) as log:
        heater.open()
        assert _wait(lambda: heater.port.is_open)
        heater.apply_settings()
        assert _wait(lambda: heater.temperature.endswith("°C"))
        time.sleep(0.1)
        seen = list(log.seen)
    heater.close()
    assert not any(e.title == "Heater Was On At Connect" for e in seen)
    assert Heater.RESET_FRAME not in board.frames[:1]


# -- the Controller's close paths ---------------------------------------------------

def test_controller_remove_confirms_the_heater_off_before_the_port_closes(
        board_factory):
    """Setup's Hard reset and a closed tab both go through `remove`."""
    board = board_factory()
    controller = Controller()
    heater = controller.add("Temperature Controller", _open(board))
    _heat(heater, board)
    controller.remove("Temperature Controller")
    assert board.endpoint == 0
    assert off_confirmed_before_close(board.log), board.log[-12:]


def test_a_hard_reset_onto_a_new_port_confirms_the_heater_off_first(
        monkeypatch):
    """Owner ruling 2026-10-08: Settings' Hard reset applies a changed port
    without a station Restart. The old board's heater-off is read back
    before its port closes, and only then is the new port opened."""
    from controller import setup as station_setup
    monkeypatch.setattr(SerialPort, "BOOTLOADER_WAIT", 0.05)
    monkeypatch.setattr(Heater, "OFF_CONFIRM_SECONDS", 0.4)
    boards = {"/dev/fake-old": FakeBoard(), "/dev/fake-new": FakeBoard()}
    opened = []

    def serial(**kw):
        # (the port opened, whether the old board's port was closed by then)
        opened.append((kw["port"], ("close",) in boards["/dev/fake-old"].log))
        return boards[kw["port"]]
    monkeypatch.setattr(serial_port, "pyserial", SimpleNamespace(Serial=serial))
    setup = station_setup.Setup(Controller())
    key = station_setup._key_for(Heater.NAME)
    setup._ports[:] = list(boards)
    setup._found.update({port: Heater.NAME for port in boards})
    assert setup.run(f"set_{key}_port", args=("/dev/fake-old",)).is_ok
    assert setup.run("launch", args=(True,)).is_ok, "launched"
    heater = setup.controller._model(Heater.NAME)
    assert _wait(lambda: heater.temperature.endswith("°C")), heater.temperature
    assert setup.run(f"set_{key}_port", args=("/dev/fake-new",)).is_ok
    assert setup.run(f"hard_reset_{key}", args=(True,)).is_ok
    old_log = boards["/dev/fake-old"].log
    assert off_confirmed_before_close(old_log), old_log[-12:]
    assert opened == [("/dev/fake-old", False), ("/dev/fake-new", True)]
    new = setup.controller._model(Heater.NAME)
    assert new is not heater and setup.controller.config(Heater.NAME)["port"] == "/dev/fake-new"
    setup.controller.close()


def test_controller_close_confirms_the_heater_off_before_the_port_closes(
        board_factory):
    """Quit, Restart, Close every model (reset) and every signal end here."""
    board = board_factory()
    controller = Controller()
    heater = controller.add("Temperature Controller", _open(board))
    _heat(heater, board)
    controller.close()
    assert off_confirmed_before_close(board.log), board.log[-12:]


# -- the process: signals, atexit, an unhandled exception ---------------------------

_CHILD = textwrap.dedent("""
    import os, sys, time, threading
    sys.path[:0] = [{src!r}, {tests!r}]
    from types import SimpleNamespace
    from devices import serial_port
    from devices.serial_port import SerialPort
    from controller.controller import Controller
    from model.heater import Heater
    from test_heater_fakes import FakeBoard
    SerialPort.BOOTLOADER_WAIT = 0.05
    board = FakeBoard(log_path={log!r})
    serial_port.pyserial = SimpleNamespace(Serial=lambda **kw: board)
    controller = Controller()
    controller._hook_exit()
    heater = Heater(port="/dev/fake-teensy")
    controller.add("Temperature Controller", heater)
    deadline = time.monotonic() + 5
    while not heater.temperature.endswith("C") and time.monotonic() < deadline:
        time.sleep(0.01)
    heater.setpoint = 80
    heater.apply_settings()
    while board.endpoint != 80 and time.monotonic() < deadline:
        time.sleep(0.01)
    print("READY", flush=True)
    how = {how!r}
    if how == "exit":
        sys.exit(0)
    if how == "raise":
        raise RuntimeError("an unhandled exception on the main thread")
    while True:
        time.sleep(0.05)
""")


def _child_log(tmp_path, how, sig=None):
    log = tmp_path / f"board-{how}.log"
    script = _CHILD.format(src=SRC, tests=TESTS, log=str(log), how=how)
    env = dict(os.environ, TRANSFER_STAGE_DATA_ROOT=str(tmp_path))
    proc = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, env=env)
    try:
        line = proc.stdout.readline()
        assert line.strip() == "READY", (line, proc.stderr.read() if proc.poll() is not None else "")
        if sig is not None:
            proc.send_signal(sig)
        proc.wait(timeout=20)
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    entries = []
    for raw in log.read_text().splitlines():
        kind, *rest = raw.split()
        entries.append((kind, *(float(x) for x in rest)))
    return entries, proc


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGHUP, signal.SIGINT],
                         ids=["SIGTERM", "SIGHUP", "SIGINT"])
def test_a_signal_confirms_the_heater_off_before_the_process_ends(tmp_path, sig):
    if not hasattr(signal, sig.name):
        pytest.skip(f"{sig.name} does not exist here")
    entries, proc = _child_log(tmp_path, "wait", sig)
    assert off_confirmed_before_close(entries), entries[-12:]
    assert proc.returncode != 0, "the signal is re-raised, not swallowed"


@pytest.mark.parametrize("how", ["exit", "raise"])
def test_atexit_confirms_the_heater_off_on_a_plain_or_crashing_exit(tmp_path, how):
    entries, _proc = _child_log(tmp_path, how)
    assert off_confirmed_before_close(entries), entries[-12:]
