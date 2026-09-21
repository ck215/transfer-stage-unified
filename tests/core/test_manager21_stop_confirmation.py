"""MANAGER-21: FULL STOP may not report a stop it cannot confirm.

**Seam pin, written before the fix.**

`SystemManager.full_stop_all` builds a `{name: ok}` map and sets `ok = True`
the instant `model.emergency_stop()` returns without raising. But every
model's `emergency_stop` is built to *always* return inside
`ESTOP_RETURN_BUDGET` (0.08 s) whether or not the hardware write landed — on
expiry it prints "latched; hardware stop still in flight" and returns
normally. It never raises and, before this change, had no way to say
"unconfirmed".

So `full_stop_all`'s own docstring described something the code could not do:

    A model reported as False is a model whose stop did not confirm in
    time. It is not a model that was skipped: its `emergency_stop` has
    already latched, and its hardware write is still in flight.

`False` was reachable only if `emergency_stop` itself blocked past
`FULL_STOP_BUDGET` (1.0 s), which by construction it does not. The Web
frontend renders the result as "FULL STOP confirmed for all devices", so an
operator watching a stage that is still moving was told every device had
stopped.

**What must not change**, and is asserted here as well: the latch still sets
first and unconditionally, and `emergency_stop` still returns inside its
budget (invariant I-5.2). A truthfulness fix does not get to make FULL STOP
slower or weaker. `False` means "I could not confirm this" — the latch is set
and the write is still being forced through — never "I did not try".

The existing tests missed this because they mock `emergency_stop` to raise or
hand-return `False`. None of them exercises the real shape: a model that
returns successfully but unconfirmed.
"""
import threading
import time
from unittest.mock import MagicMock

import pytest

from model.probes import DCProbe
from model.system_manager import SystemManager


class _Transport:
    """Answers normally. Same shape as `test_transport_truth.RecordingTransport`
    — the probe drives all of these during a stop, so a partial double fails
    for reasons that have nothing to do with the finding."""

    def __init__(self):
        self.writes = []

    def write_command(self, payload, priority=False):
        self.writes.append(payload)

    def enable(self):
        self.write_command(b"e")

    def disable(self):
        self.write_command(b"d")

    def send_autonomous_command(self, params):
        self.writes.append(("auton", params))

    def send_manual_mode_command(self, params):
        self.writes.append(("manual", params))

    def read_position(self):
        return None

    def close(self):
        pass


class _StalledTransport(_Transport):
    """A live port with a wedged board: writes never return."""

    def __init__(self, release):
        super().__init__()
        self._release = release

    def write_command(self, payload, priority=False):
        self._release.wait(10)


class _FailingTransport(_Transport):
    """A port whose writes raise — the kill never reached the board."""

    def write_command(self, payload, priority=False):
        raise OSError("device not configured")


def _probe(transport):
    probe = DCProbe("SIM", None)
    probe.serial_comm = transport
    return probe


# -- the model half ---------------------------------------------------------

def test_manager21_a_healthy_stop_reports_confirmed():
    probe = _probe(_Transport())
    assert probe.emergency_stop() is True, (
        "a stop that completed against a healthy transport reported as "
        "unconfirmed; False has to mean something or it means nothing")


def test_manager21_a_stop_still_in_flight_reports_unconfirmed():
    """The shape no existing test covered."""
    release = threading.Event()
    probe = _probe(_StalledTransport(release))
    try:
        result = probe.emergency_stop()
        assert result is False, (
            "emergency_stop returned True while its hardware write was still "
            "blocked inside the transport. This is the value full_stop_all "
            "turns into 'FULL STOP confirmed for all devices'")
    finally:
        release.set()


def test_manager21_a_failed_stop_reports_unconfirmed():
    probe = _probe(_FailingTransport())
    assert probe.emergency_stop() is False, (
        "the kill write raised and the stop still reported confirmed")


# -- what must not regress --------------------------------------------------

def test_manager21_the_latch_is_set_even_when_unconfirmed():
    """False is 'not confirmed', never 'not attempted'. The latch is the part
    that actually protects the bench and it is not allowed to depend on the
    transport answering."""
    release = threading.Event()
    probe = _probe(_StalledTransport(release))
    try:
        probe.emergency_stop()
        assert probe.estop_latched
    finally:
        release.set()


def test_manager21_returning_a_value_did_not_make_the_stop_slower():
    """I-5.2. The caller is frequently the UI thread."""
    release = threading.Event()
    probe = _probe(_StalledTransport(release))
    try:
        started = time.time()
        probe.emergency_stop()
        elapsed = time.time() - started
        assert elapsed < probe.ESTOP_RETURN_BUDGET + 0.5, (
            f"emergency_stop took {elapsed:.2f}s against a stalled transport; "
            f"the budget is {probe.ESTOP_RETURN_BUDGET}s")
    finally:
        release.set()


# -- the manager half, which is the reported seam ---------------------------

def test_manager21_full_stop_all_reports_a_real_unconfirmed_device():
    """The finding itself, end to end, with a real model and a real stall.

    Deliberately not a mock that hand-returns False — that is exactly what the
    existing tests do, and it is why this survived every prior wave.
    """
    release = threading.Event()
    manager = SystemManager()
    healthy = _probe(_Transport())
    stalled = _probe(_StalledTransport(release))
    manager.register("healthy", healthy)
    manager.register("stalled", stalled)
    try:
        results = manager.full_stop_all()

        assert results["stalled"] is False, (
            "full_stop_all reported a device confirmed while its stop was "
            "still wedged in the transport")
        assert results["healthy"] is True, (
            "the healthy device must still report confirmed, or the signal "
            "is useless")
    finally:
        release.set()
        try:
            manager.shutdown_all()
        except Exception:
            pass


def test_manager21_full_stop_all_latches_every_device_regardless():
    release = threading.Event()
    manager = SystemManager()
    stalled = _probe(_StalledTransport(release))
    manager.register("stalled", stalled)
    try:
        manager.full_stop_all()
        assert stalled.estop_latched
    finally:
        release.set()
        try:
            manager.shutdown_all()
        except Exception:
            pass


# -- MANAGER-23: does the operator actually get told? -----------------------

def test_manager23_an_unconfirmed_device_is_reported_to_every_frontend():
    """MANAGER-23 claimed Tk and PySide have no path that could tell the
    operator a device did not confirm, because both wire the FULL STOP button
    straight to the bare `full_stop_all` callable and discard its return.

    They do discard it — but the premise is still wrong, and this test is why
    the row closes instead of being fixed. `full_stop_all` reports the
    unconfirmed devices *itself*, through `SystemManager._report` ->
    `ErrorRouter.report_error` -> the `EventBus`, which **both** desktop views
    subscribe to (tkinter/view.py:38, pyside/view.py:43) and render as a
    popup. The information reaches all three frontends; it simply does not
    travel by return value.

    The reason this looked broken is worth keeping: before MANAGER-21 the
    `unconfirmed` list could never be populated, because `ok` was set to True
    whenever `emergency_stop` did not raise. The reporting path was correct
    and **unreachable** — the same shape as ROTATOR-6, where `poll_status`
    was correct and had no caller. Fixing the confirmation contract is what
    turned this code back on.

    What the desktop views genuinely lack next to the Web client is the
    *positive* toast on success. That is a deliberate difference, not a
    defect: a modal on every successful FULL STOP is a modal operators learn
    to dismiss without reading.
    """
    from error_routing import bus as event_bus

    release = threading.Event()
    manager = SystemManager()
    manager.register("stalled", _probe(_StalledTransport(release)))

    received = []
    event_bus.subscribe(received.append)
    try:
        manager.full_stop_all()

        titles = [getattr(e, "title", "") for e in received]
        assert any("Not Confirmed" in t for t in titles), (
            f"a device failed to confirm and nothing was published to the "
            f"bus, so no frontend could tell the operator. Saw: {titles}")
    finally:
        event_bus.unsubscribe(received.append)
        release.set()
        try:
            manager.shutdown_all()
        except Exception:
            pass
