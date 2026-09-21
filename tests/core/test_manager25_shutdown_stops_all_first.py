"""MANAGER-25: shutdown stops every device before tearing any of them down.

`shutdown_all()` used to run stop-then-teardown **per device, in sequence**.
Anything that held the calling thread inside one device's `teardown()` - a
slow drain, or PySide's synchronous modal from PYSIDE-21 - left every
later-registered device not yet stopped. Registration order puts the heater
before the rotator, so a flaky heater cable at window close left the rotator
live behind a dialog.

The contract pinned here: by the time any teardown runs, every device's
`emergency_stop()` has been called, and one device whose stop hangs cannot
keep the others from being stopped.
"""
import threading
import time

import pytest

from model.system_manager import SystemManager


class _Recorder:
    def __init__(self, name, log, *, stop_blocks=None, teardown_blocks=None):
        self.name = name
        self.log = log
        self.stop_blocks = stop_blocks
        self.teardown_blocks = teardown_blocks

    def emergency_stop(self):
        self.log.append(("stop", self.name))
        if self.stop_blocks is not None:
            self.stop_blocks.wait(5)
        return True

    def teardown(self):
        self.log.append(("teardown", self.name))
        if self.teardown_blocks is not None:
            self.teardown_blocks.wait(5)


def _manager(*models):
    mgr = SystemManager()
    for m in models:
        mgr.register(m.name, m)
    return mgr


def test_every_device_is_stopped_before_any_teardown_runs():
    log = []
    mgr = _manager(*(_Recorder(n, log) for n in ("heater", "rotator", "probe")))

    mgr.shutdown_all()

    first_teardown = next(i for i, (op, _) in enumerate(log) if op == "teardown")
    stopped_before = {n for op, n in log[:first_teardown] if op == "stop"}
    assert stopped_before == {"heater", "rotator", "probe"}, log
    assert sorted(n for op, n in log if op == "teardown") == ["heater", "probe", "rotator"]


def test_a_teardown_that_blocks_cannot_leave_a_later_device_unstopped():
    # The PYSIDE-21 shape: the first device's teardown sits in a modal.
    log = []
    release = threading.Event()
    heater = _Recorder("heater", log, teardown_blocks=release)
    rotator = _Recorder("rotator", log)
    mgr = _manager(heater, rotator)

    t = threading.Thread(target=mgr.shutdown_all, daemon=True)
    t.start()
    deadline = time.monotonic() + 2
    while ("teardown", "heater") not in log and time.monotonic() < deadline:
        time.sleep(0.005)
    try:
        assert ("teardown", "heater") in log
        assert ("stop", "rotator") in log, (
            "the rotator must already be stopped while the heater's teardown "
            f"is still blocked; log={log}")
    finally:
        release.set()
        t.join(5)


def test_a_hung_stop_does_not_keep_the_others_from_being_stopped():
    log = []
    wedged = threading.Event()
    heater = _Recorder("heater", log, stop_blocks=wedged)
    rotator = _Recorder("rotator", log)
    mgr = _manager(heater, rotator)
    try:
        t0 = time.monotonic()
        mgr.shutdown_all()
        elapsed = time.monotonic() - t0
        assert ("stop", "rotator") in log
        assert elapsed < SystemManager.FULL_STOP_BUDGET + 1.0, elapsed
        # Teardown still runs for both, including the one whose stop hung.
        assert {n for op, n in log if op == "teardown"} == {"heater", "rotator"}
    finally:
        wedged.set()


def test_shutdown_is_still_idempotent():
    log = []
    mgr = _manager(_Recorder("heater", log))
    mgr.shutdown_all()
    mgr.shutdown_all()
    assert [e for e in log if e[0] == "teardown"] == [("teardown", "heater")]
