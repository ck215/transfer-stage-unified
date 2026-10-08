"""Every port the station opens is held exclusively (bench 2026-10-07).

A second station's startup scan opened /dev/ttyACM0-2 and /dev/ttyUSB0
while the first station held them: the open toggled DTR (an Arduino Mega
resets), the handshake stole the first station's replies, and the running
station saw "Position Stream Stalled", 0xFF garble and Rotator timeouts.
The station now opens every port with pyserial's `exclusive=True` plus
TIOCEXCL, so another open() of the tty fails with EBUSY before it can touch
DTR, and the scan reports such a port as "in use by another program" - one
plain line, no failure warning, nothing sent.

Driven over a pseudo-terminal (Linux and macOS ptys honour TIOCEXCL); no
real serial port is opened.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from controller import setup as station_setup
from controller.setup import Setup
from devices import serial_port
from events import events

from tests.test_setup import RecordingController

SRC = Path(__file__).resolve().parents[1] / "src"

pytestmark = pytest.mark.skipif(not serial_port.EXCLUSIVE or serial_port.pyserial is None,
                                reason="TIOCEXCL and pyserial are POSIX-only here")


@pytest.fixture
def pty_port():
    master, slave = os.openpty()
    name = os.ttyname(slave)
    try:
        yield name
    finally:
        os.close(slave)
        os.close(master)


def _other_process_opens(name):
    """What a second process gets when it opens the port (the 23:18 launch)."""
    done = subprocess.run([sys.executable, "-c",
                           "import os, sys\n"
                           "try:\n"
                           "    os.close(os.open(sys.argv[1], os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK))\n"
                           "    print('opened')\n"
                           "except OSError as e:\n"
                           "    print('refused', e.errno)\n", name],
                          capture_output=True, text=True, timeout=30)
    return done.stdout.strip()


def test_a_held_port_refuses_another_process_and_is_free_after_close(pty_port):
    handle = serial_port._open_serial(port=pty_port, baudrate=115200, timeout=0.1,
                                      write_timeout=0.2)
    try:
        assert handle.exclusive is True
        assert _other_process_opens(pty_port) == "refused 16", (
            "another process could open (and reset) a port this station holds")
    finally:
        serial_port._close_serial(handle)
    assert _other_process_opens(pty_port) == "opened", "the hold outlived the close"


def test_a_serial_port_holds_its_port_while_open(pty_port):
    port = serial_port.SerialPort(pty_port, 115200, handshake=False)
    port.open()
    try:
        assert port.wait_open(5.0)
        assert _other_process_opens(pty_port) == "refused 16"
    finally:
        port.close()
    assert _other_process_opens(pty_port) == "opened"


def test_a_port_held_elsewhere_is_lost_as_busy_with_one_plain_warning(pty_port):
    holder = serial_port._open_serial(port=pty_port, baudrate=115200, timeout=0.1)
    seen = []
    events.clear()
    events.subscribe(seen.append)
    try:
        port = serial_port.SerialPort(pty_port, 115200, handshake=False)
        port.open()
        assert not port.wait_open(2.0)
        assert port.open_busy is True
        warned = [e for e in seen if e.severity == "warning"]
        assert [e.title for e in warned] == ["Port In Use"], warned
        assert "in use by another program" in warned[0].message
        assert "nothing was sent" in warned[0].message
        port.close()
    finally:
        events.unsubscribe(seen.append)
        serial_port._close_serial(holder)


def test_query_on_a_held_port_is_a_busy_error(pty_port):
    holder = serial_port._open_serial(port=pty_port, baudrate=57600, timeout=0.1)
    try:
        with pytest.raises(serial_port.TransportError) as raised:
            serial_port.query(pty_port, 57600, b"1ID?\r\n", xonxoff=True)
        assert serial_port.is_busy_error(raised.value)
    finally:
        serial_port._close_serial(holder)


def test_the_scan_names_a_busy_port_and_does_not_warn(pty_port, monkeypatch):
    """Setup's identify over the REAL SerialPort and query: a port another
    process holds is one info line naming it, the scan status says so, and
    no "Probe Failed" warning (no dialog) is raised."""
    monkeypatch.setattr(station_setup, "PROBE_SECONDS", 0.5)
    holder = subprocess.Popen(
        [sys.executable, "-c",
         f"import sys; sys.path.insert(0, {str(SRC)!r}); "
         "from devices import serial_port as s; "
         "h = s._open_serial(port=sys.argv[1], baudrate=115200, timeout=0.1); "
         "print('held', flush=True); sys.stdin.read(); s._close_serial(h)", pty_port],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    assert holder.stdout.readline().strip() == "held"
    seen = []
    events.clear()
    events.subscribe(seen.append)
    try:
        panel = Setup(RecordingController())
        assert panel.identify(pty_port) is None
        assert panel.busy_ports == [pty_port]
        said = [e for e in seen if e.title == "Port In Use"]
        assert len(said) == 1 and said[0].severity == "info", said
        assert pty_port in said[0].message and "in use by another program" in said[0].message
        assert not [e for e in seen if e.severity in ("warning", "error")], seen
    finally:
        events.unsubscribe(seen.append)
        holder.stdin.close()
        holder.wait(timeout=10)


def test_windows_is_unchanged():
    """Windows refuses a second open of a COM port by itself; the station
    passes nothing new there."""
    source = (SRC / "devices" / "serial_port.py").read_text()
    assert 'EXCLUSIVE = _fcntl is not None and hasattr(_termios, "TIOCEXCL")' in source
    assert 'if EXCLUSIVE:\n        settings["exclusive"] = True' in source
