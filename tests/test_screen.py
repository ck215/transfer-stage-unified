"""`devices.screen.Screen` — the one importer of `mss`.

No test here opens a real display: a factory is injected, which is the reason
the seam exists. `tests/conftest.py` already replaces `mss` with a
`MagicMock`, so the un-injected path would silently "work" and prove nothing.
"""
import sys
import threading

import pytest

from devices.screen import Screen


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

def test_only_a_keeping_thread_reuses_a_handle_and_close_closes_it():
    """`mss` is not thread-safe: an instance belongs to the thread that made
    it. Amended 2026-09-28 (it asserted a kept handle for EVERY thread, the
    leak): only the thread that declared itself with `keep_handle()` (the
    run loop) reuses one; `close()` still closes it."""
    screen, made = _screen()
    screen.open()
    region = {"top": 0, "left": 0, "width": 2, "height": 2}

    def loop():
        screen.keep_handle()
        screen.grab(region)
        screen.grab(region)

    worker = threading.Thread(target=loop)
    worker.start()
    worker.join()
    assert len(made) == 1, "the keeping thread must reuse its own handle"
    assert not made[0].closed and screen.kept_handles == 1

    screen.close()
    assert all(capture.closed for capture in made)
    assert screen.kept_handles == 0


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


# --- the whole desktop (full pictures, 2026-09-28; additive) --------------

class _Shot:
    """What `mss` returns from a grab: `size` and a BGRA buffer."""

    def __init__(self, width, height):
        self.size = (width, height)
        self.bgra = bytes([0, 0, 200, 255]) * (width * height)   # pure red


class DesktopCapture(FakeCapture):
    """A capture whose virtual desktop is wider than the picker's 1600."""

    def __init__(self, width=2000, height=100):
        super().__init__()
        self.monitors = [{"left": 0, "top": 0, "width": width, "height": height}]

    def grab(self, region):
        self.regions.append(dict(region))
        return _Shot(region["width"], region["height"])


def _decoded(png):
    import io
    from PIL import Image
    return Image.open(io.BytesIO(png))


def test_screenshot_png_without_a_max_width_is_the_desktop_at_full_size():
    """`max_width=None` means no downscale (it used to raise at the
    comparison and come back as (None, None))."""
    screen = Screen(factory=DesktopCapture)
    screen.open()
    png, bounds = screen.screenshot_png(max_width=None)
    assert png is not None and png[:8] == b"\x89PNG\r\n\x1a\n"
    assert bounds == {"left": 0, "top": 0, "width": 2000, "height": 100}
    image = _decoded(png)
    assert image.size == (2000, 100)
    assert image.getpixel((0, 0))[:3] == (200, 0, 0)


def test_screenshot_png_still_downscales_for_the_picker():
    screen = Screen(factory=DesktopCapture)
    screen.open()
    png, bounds = screen.screenshot_png()
    assert _decoded(png).size == (1600, 80)
    assert bounds["width"] == 2000                   # full-size bounds


# --- one-call handles (2026-09-28): no connection left behind ------------
# A Web request is a new thread each time; Arm, Finish and the picker grab
# from such threads. A kept handle per thread stayed open until close(),
# one per request (on X11, one display connection each).

REGION = {"top": 0, "left": 0, "width": 2, "height": 2}


def _from_fresh_thread(fn):
    out = []
    worker = threading.Thread(target=lambda: out.append(fn()))
    worker.start()
    worker.join()
    return out[0]


def test_a_grab_from_a_fresh_thread_leaves_no_handle_behind():
    screen, made = _screen()
    screen.open()
    for _ in range(5):
        assert _from_fresh_thread(lambda: screen.grab(REGION)) == "frame-1"
    assert len(made) == 5 and all(c.closed for c in made)
    assert screen.kept_handles == 0
    assert screen.grab(REGION) == "frame-1"        # the calling thread too
    assert made[-1].closed and screen.kept_handles == 0


def test_a_screenshot_from_a_fresh_thread_leaves_no_handle_behind():
    made = []

    def factory():
        made.append(DesktopCapture())
        return made[-1]

    screen = Screen(factory=factory)
    screen.open()
    for _ in range(3):
        png, _bounds = _from_fresh_thread(lambda: screen.screenshot_png(max_width=None))
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(made) == 3 and all(c.closed for c in made)
    assert screen.kept_handles == 0


def test_a_failing_one_call_grab_still_closes_its_handle():
    screen, made = _screen(error=RuntimeError("display went away"))
    screen.open()
    assert _from_fresh_thread(lambda: screen.grab(REGION)) is None
    assert screen.failures == 1 and made[0].closed and screen.kept_handles == 0


def test_the_keeping_threads_handle_is_reused_for_every_frame():
    """The run loop's throughput: one open for the loop, never one per frame;
    another thread's grab meanwhile opens and closes its own."""
    screen, made = _screen()
    screen.open()
    kept, go_on, grabbed = [], threading.Event(), threading.Event()

    def loop():
        screen.keep_handle()
        screen.keep_handle()                       # idempotent
        for _ in range(100):
            kept.append(screen.grab(REGION))
        grabbed.set()
        go_on.wait(5.0)
        screen.drop_handle()

    worker = threading.Thread(target=loop)
    worker.start()
    assert grabbed.wait(5.0)
    assert len(made) == 1 and len(made[0].regions) == 100
    assert kept[-1] == "frame-100"
    assert not made[0].closed and screen.kept_handles == 1
    assert _from_fresh_thread(lambda: screen.grab(REGION)) == "frame-1"
    assert len(made) == 2 and made[1].closed and not made[0].closed
    go_on.set()
    worker.join()
    assert made[0].closed and screen.kept_handles == 0     # dropped on leaving


def test_keep_handle_that_cannot_open_falls_back_to_one_call_handles():
    calls = []

    def factory():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("no display yet")
        return FakeCapture()

    screen = Screen(factory=factory)
    screen.open()
    screen.keep_handle()                           # never raises
    assert screen.kept_handles == 0
    assert screen.grab(REGION) == "frame-1"
    screen.drop_handle()                           # nothing kept: harmless


def test_a_closed_screen_keeps_nothing():
    screen, made = _screen()
    screen.keep_handle()
    assert made == [] and screen.kept_handles == 0


def test_handle_creation_is_serialised_across_threads():
    """`mss` instances are created and closed under one lock: two threads
    never construct one at the same moment."""
    import time
    inside, overlap = [0], []

    class Slow(FakeCapture):
        def __init__(self):
            inside[0] += 1
            overlap.append(inside[0])
            time.sleep(0.01)
            inside[0] -= 1
            super().__init__()

    screen = Screen(factory=Slow)
    screen.open()
    workers = [threading.Thread(target=lambda: screen.grab(REGION))
               for _ in range(8)]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    assert len(overlap) == 8 and max(overlap) == 1
