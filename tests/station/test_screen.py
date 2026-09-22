"""`station.devices.screen.Screen` — the one importer of `mss`.

No test here opens a real display: a factory is injected, which is the reason
the seam exists. `tests/conftest.py` already replaces `mss` with a
`MagicMock`, so the un-injected path would silently "work" and prove nothing.
"""
import sys
import threading

import pytest

from station.devices.screen import Screen


class FakeCapture:
    """What `mss.mss()` returns, reduced to the one method we use."""

    def __init__(self, error=None):
        self.regions = []
        self.closed = False
        self._error = error

    def grab(self, region):
        self.regions.append(dict(region))
        if self._error is not None:
            raise self._error
        return f"frame-{len(self.regions)}"

    def close(self):
        self.closed = True


def _screen(error=None):
    made = []

    def factory():
        capture = FakeCapture(error)
        made.append(capture)
        return capture

    return Screen(factory=factory), made


# --- lifecycle ------------------------------------------------------------

def test_a_closed_screen_grabs_nothing():
    screen, made = _screen()
    assert screen.grab({"top": 0, "left": 0, "width": 4, "height": 4}) is None
    assert made == [], "a closed Screen must not build a capture handle"


def test_open_reports_its_state_in_one_word():
    screen, _ = _screen()
    assert screen.status == "closed"
    screen.open()
    assert screen.is_open and screen.status == "capturing"
    screen.close()
    assert not screen.is_open and screen.status == "closed"


def test_open_is_idempotent():
    screen, made = _screen()
    screen.open()
    screen.open()
    screen.grab({"top": 0, "left": 0, "width": 2, "height": 2})
    assert len(made) == 1


def test_only_the_asked_for_region_is_grabbed():
    """Grabbing the screen and cropping is what made the old capture path too
    expensive to run flat out."""
    screen, made = _screen()
    screen.open()
    region = {"top": 10, "left": 20, "width": 30, "height": 40}
    assert screen.grab(region) == "frame-1"
    assert made[0].regions == [region]


def test_no_region_is_not_an_error():
    screen, _ = _screen()
    screen.open()
    assert screen.grab(None) is None


# --- threads --------------------------------------------------------------

def test_each_thread_gets_its_own_capture_handle_and_close_closes_them_all():
    """`mss` is not thread-safe: an instance belongs to the thread that made
    it. The run loop is a worker thread; the live readouts are polled from
    the UI thread."""
    screen, made = _screen()
    screen.open()
    region = {"top": 0, "left": 0, "width": 2, "height": 2}

    screen.grab(region)
    screen.grab(region)
    assert len(made) == 1, "one thread must reuse its own handle"

    worker = threading.Thread(target=lambda: screen.grab(region))
    worker.start()
    worker.join()
    assert len(made) == 2, "a second thread must get its own handle"

    screen.close()
    assert all(capture.closed for capture in made)


# --- failure --------------------------------------------------------------

def test_a_grab_failure_is_a_none_not_an_exception():
    """A transient failure — a display sleeping, a space switching — must not
    end a run that is otherwise fine."""
    screen, _ = _screen(error=RuntimeError("display went away"))
    screen.open()

    assert screen.grab({"top": 0, "left": 0, "width": 2, "height": 2}) is None
    assert screen.grab({"top": 0, "left": 0, "width": 2, "height": 2}) is None
    assert screen.failures == 2


def test_mss_failing_to_import_leaves_the_screen_unavailable_not_crashed(
        monkeypatch):
    """REDPERCENT-4's `mss = None` half: the old code bound `mss = None` at
    import and then did `with mss.mss()` in the thread anyway, raising
    `AttributeError` from inside the run the instant it started."""
    monkeypatch.setitem(sys.modules, "mss", None)
    screen = Screen()

    screen.open()

    assert not screen.is_open
    assert not screen.is_available
    assert "mss" in screen.error
    assert screen.grab({"top": 0, "left": 0, "width": 2, "height": 2}) is None


def test_close_on_a_never_opened_screen_is_harmless():
    Screen().close()
