"""MANAGER-20 (the composition-root half) — a port scan can be told to give up.

`probe_device_at` blocks for roughly 1.5 s + 3 s per baud per port, so a
four-port scan can run for half a minute with no way to interrupt it. The Qt
setup window's `ScannerThread` is a `QThread` with nothing watching it: the
operator can close the window mid-scan and Qt may print "QThread: Destroyed
while thread is still running" and abort, or the process lingers until the
scan ends on its own.

Interrupting the `QThread` only helps if the thing it is blocked inside
notices. That is what this covers, and it needs no Qt at all. The
`closeEvent` half is in `tests/ui/test_manager20_setup_window_close.py`,
which is qt-marked.
"""

from unittest.mock import MagicMock, patch

import pytest

import app_bootstrap

PORT = "/dev/ttyFAKE0"


class FakeClock:
    """Virtual time, so the 1.5 s bootloader waits cost nothing."""

    def __init__(self):
        self.now = 500.0
        self.slept = []

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += max(0.0, float(seconds))


def _silent_serial(opened):
    """A pyserial stand-in that opens fine and then says nothing, ever."""

    def factory(port, baudrate, **kwargs):
        opened.append(baudrate)
        handle = MagicMock()
        handle.__enter__ = MagicMock(return_value=handle)
        handle.__exit__ = MagicMock(return_value=None)
        handle.in_waiting = 0
        handle.read_all.return_value = b""
        handle.read.return_value = b""
        return handle

    return factory


@pytest.fixture
def fast_clock(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(app_bootstrap, "time", clock)
    return clock


def test_an_abort_before_the_first_baud_opens_no_port_at_all(fast_clock):
    opened = []
    with patch("serial.Serial", side_effect=_silent_serial(opened)):
        result = app_bootstrap.probe_device_at(PORT, should_abort=lambda: True)
    assert result is None
    assert opened == [], f"a port was opened after the scan was aborted: {opened}"


def test_an_abort_stops_the_scan_between_baud_attempts(fast_clock):
    """The three baud attempts are tried in order; an abort ends the sequence."""
    opened = []
    calls = {"n": 0}

    def should_abort():
        # Let the first (57600) attempt happen, then give up.
        calls["n"] += 1
        return calls["n"] > 1

    with patch("serial.Serial", side_effect=_silent_serial(opened)):
        result = app_bootstrap.probe_device_at(PORT, should_abort=should_abort)

    assert result is None
    assert opened == [57600], f"kept scanning after the abort: {opened}"


def test_an_abort_breaks_out_of_the_polling_loop(fast_clock):
    """The 3 s poll is where the scan actually spends its time.

    Aborting has to be noticed *inside* it, not only between ports —
    otherwise `QThread.wait()` in `closeEvent` still blocks for seconds.
    """
    opened = []
    aborted = {"yes": False}

    def should_abort():
        return aborted["yes"]

    def factory(port, baudrate, **kwargs):
        opened.append(baudrate)
        handle = MagicMock()
        handle.__enter__ = MagicMock(return_value=handle)
        handle.__exit__ = MagicMock(return_value=None)
        handle.read_all.return_value = b""
        handle.read.return_value = b""
        if baudrate == 500000:
            # Once the slow loop is reached, the window closes.
            aborted["yes"] = True
        handle.in_waiting = 0
        return handle

    with patch("serial.Serial", side_effect=factory):
        result = app_bootstrap.probe_device_at(PORT, should_abort=should_abort)

    assert result is None
    assert 115200 not in opened, (
        "the 500k poll ran to its full 3 s and then opened the next baud "
        "anyway, so an interruption request would not have been noticed")
    # The 3 s window would be ~60 polling sleeps; an abort ends it far sooner.
    assert len(fast_clock.slept) < 20, fast_clock.slept


def test_an_abort_callback_that_raises_does_not_break_the_scan(fast_clock):
    opened = []
    with patch("serial.Serial", side_effect=_silent_serial(opened)):
        result = app_bootstrap.probe_device_at(
            PORT, should_abort=lambda: 1 / 0)
    assert result is None
    assert opened, "a broken abort callback stopped the scan working at all"


# --------------------------------------------------------------------------
# Regression guards: the ordinary scan is unchanged.
# --------------------------------------------------------------------------


def test_no_abort_callback_means_the_full_scan_still_runs(fast_clock):
    opened = []
    with patch("serial.Serial", side_effect=_silent_serial(opened)):
        result = app_bootstrap.probe_device_at(PORT)
    assert result is None
    assert opened == [57600, 500000, 115200]


def test_a_device_is_still_identified_with_an_abort_callback_present(fast_clock):
    def factory(port, baudrate, **kwargs):
        handle = MagicMock()
        handle.__enter__ = MagicMock(return_value=handle)
        handle.__exit__ = MagicMock(return_value=None)
        handle.read_all.return_value = b""
        if baudrate == 500000:
            handle.in_waiting = 8
            handle.read.return_value = b"DEV: s\r\n"
        else:
            handle.in_waiting = 0
            handle.read.return_value = b""
        return handle

    with patch("serial.Serial", side_effect=factory):
        result = app_bootstrap.probe_device_at(
            PORT, should_abort=lambda: False)
    assert result == "Stepper Probe"
