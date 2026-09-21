"""ROTATOR-6: the rotator needs the model-owned sampler RC-4 gave the probes.

**This is the seam pin, written by the lead before either half was briefed**,
per the WEB-19 failure of 2026-09-20 — where a model half and a caller half
were each built correctly, each tested in isolation, and never joined.

The seam here is small enough to state in one line: `poll_status` is the only
writer of live `position`/`state` on this model (rotator_system.py:513-519),
and at f71c955 **nothing in `src/` calls it.** The Tk and PySide timers that
used to, on the GUI thread, were removed by S5/RC-4 and nothing replaced them;
`web_adapter.get_state` deliberately does no I/O and documents that "the model
samples on its own thread" (web_adapter.py:451-456) — a thread that exists for
`BaseProbe` and not for `RotatorSystem`.

So the rotator card publishes whatever `connect()` last wrote and never moves
again, on all three frontends. At the bench that reads as: home the stage,
watch it turn, and the position field sits at its initial value. The operator
cannot tell a moving stage from a wedged one, which is the precise condition
under which someone reaches for FULL STOP.

What these tests deliberately do NOT pin: the thread's name, its interval, or
whether it is started from `connect()` or from an explicit `start_loops()`.
Any of those is a fair implementation. What is pinned is that *live values
reach the published properties with no caller doing the polling*, that the
sampler cannot delay FULL STOP, and that it does not outlive the connection.
"""
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from model.rotator_system import RotatorSystem


# How long a test will wait for a background sampler to publish one value.
# Generous on purpose: this pins that sampling happens at all, not how fast.
SAMPLE_DEADLINE = 3.0


class FakeSMC:
    """Stands in for `lib.smc100.SMC100`, counting polls.

    `get_position_deg` walks so a test can tell a live republish from the one
    value `connect()` happened to leave behind.
    """

    def __init__(self, *args, **kwargs):
        self.polls = 0
        self.closed = False
        self._lock = threading.Lock()
        self.block = threading.Event()   # set -> get_position_deg hangs
        self.released = threading.Event()
        self.stopped = threading.Event()

    def get_position_deg(self):
        with self._lock:
            self.polls += 1
            n = self.polls
        if self.block.is_set():
            # A wedged transaction, the thing that makes a synchronous poll
            # dangerous in the first place.
            self.released.wait(10)
        return 10.0 + n

    def get_status(self, silent=False):
        return (0, "32")     # "Ready" in the SMC100 state table

    def stop(self, priority=False):
        self.stopped.set()

    def close(self):
        self.closed = True


def _connected_rotator(fake):
    """Build a rotator connected to `fake`, through the real `connect()`."""
    rotator = RotatorSystem(default_port=None)
    fake_module = SimpleNamespace(SMC100=lambda **kw: fake)
    with patch("model.rotator_system.smc100", fake_module):
        rotator.connect("SIM-PORT", smc_id=1)
    assert rotator.is_connected, "test setup: connect() did not take"
    return rotator


def _teardown(rotator):
    try:
        rotator.disconnect()
    except Exception:
        pass


def test_rotator6_position_is_published_without_anyone_calling_poll_status():
    """The seam. Nothing here calls `poll_status`; the value must still move.

    This is the whole finding in one assertion. If it passes only because the
    test called `poll_status` itself, it is pinning the model's ability to
    parse a reply — which ROTATOR-9 already covers — and not the thing that
    is actually missing, which is a caller.
    """
    fake = FakeSMC()
    rotator = _connected_rotator(fake)
    try:
        deadline = time.time() + SAMPLE_DEADLINE
        while time.time() < deadline:
            if rotator.position is not None:
                break
            time.sleep(0.02)
        assert fake.polls > 0, (
            "no background sampler ever polled the SMC100. `poll_status` is "
            "the only writer of live position/state and has no caller in "
            "src/ — the rotator card is frozen at whatever connect() wrote")
        assert rotator.position is not None, (
            "the sampler ran but never published; `position` is still None")
    finally:
        _teardown(rotator)


def test_rotator6_the_published_position_keeps_tracking_the_hardware():
    """One sample is not sampling. The value has to keep following the stage."""
    fake = FakeSMC()
    rotator = _connected_rotator(fake)
    try:
        deadline = time.time() + SAMPLE_DEADLINE
        first = None
        while time.time() < deadline:
            pos = rotator.position
            if pos is not None:
                if first is None:
                    first = pos
                elif pos != first:
                    break
            time.sleep(0.02)
        assert first is not None, "nothing was ever published"
        assert rotator.position != first, (
            f"position published {first} once and then stopped changing, "
            f"though the hardware reported a new value every poll "
            f"({fake.polls} polls seen)")
    finally:
        _teardown(rotator)


def test_rotator6_a_wedged_poll_does_not_delay_full_stop():
    """The reason this belongs to the lead and not to a lane.

    A sampler that holds `_serial_lock` across a blocking read reintroduces
    exactly the defect ROTATOR-8 closed: FULL STOP queued behind a status
    transaction while the stage keeps turning. `emergency_stop` is required to
    return within `ESTOP_RETURN_BUDGET` (plus slack for scheduling) no matter
    what the sampler is doing.
    """
    fake = FakeSMC()
    fake.block.set()
    rotator = _connected_rotator(fake)
    try:
        # Let the sampler get itself wedged inside a poll.
        deadline = time.time() + SAMPLE_DEADLINE
        while time.time() < deadline and fake.polls == 0:
            time.sleep(0.02)
        assert fake.polls > 0, (
            "no sampler to wedge — see "
            "test_rotator6_position_is_published_without_anyone_calling_poll_status")

        started = time.time()
        rotator.emergency_stop()
        elapsed = time.time() - started

        budget = rotator.ESTOP_RETURN_BUDGET + 1.0
        assert elapsed < budget, (
            f"FULL STOP took {elapsed:.2f}s with a poll wedged; the budget is "
            f"{rotator.ESTOP_RETURN_BUDGET}s. The sampler is holding the "
            f"serial lock across a blocking read (ROTATOR-8 regression)")
        assert rotator.estop_latched, "the latch must be set regardless"
    finally:
        fake.released.set()
        _teardown(rotator)


def test_rotator6_disconnect_stops_the_sampler():
    """A sampler that outlives the connection polls a closed port forever."""
    fake = FakeSMC()
    rotator = _connected_rotator(fake)
    try:
        deadline = time.time() + SAMPLE_DEADLINE
        while time.time() < deadline and fake.polls == 0:
            time.sleep(0.02)
        assert fake.polls > 0, "no sampler started; nothing to stop"

        rotator.disconnect()
        time.sleep(0.4)
        settled = fake.polls
        time.sleep(0.6)
        assert fake.polls == settled, (
            f"the sampler kept polling after disconnect "
            f"({settled} -> {fake.polls}); it is running against a closed "
            f"handle for the rest of the session")
    finally:
        fake.released.set()
        _teardown(rotator)


def test_rotator6_an_unconnected_model_runs_no_sampler_thread():
    """Construction must stay cheap and thread-free.

    `BaseProbe.start_loops` is demand-driven for this reason: "an idle or
    test-constructed model should not be running two threads" (probes.py).
    The rotator's sampler has to honour the same rule, or every one of the
    hundreds of `RotatorSystem(default_port=None)` constructions in this
    suite leaks a thread.
    """
    before = threading.active_count()
    rotators = [RotatorSystem(default_port=None) for _ in range(5)]
    time.sleep(0.3)
    after = threading.active_count()
    assert after <= before + 1, (
        f"constructing 5 unconnected rotators added {after - before} threads; "
        f"an unconnected model has nothing to sample")
    assert all(r.position is None for r in rotators)
