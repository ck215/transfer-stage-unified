"""ROTATOR-6 extras: coverage the seam pin deliberately leaves out.

`tests/core/test_rotator6_model_owned_sampler.py` is the lead's seam pin --
it is not touched here. It pins that a sampler exists, that it keeps
tracking the hardware, that it cannot delay FULL STOP, and that it does not
outlive the connection. It explicitly does not cover:

  * a poll failure mid-sampling interacting with ROTATOR-9's
    "Communication lost" state, or recovery after one, and
  * what the sampler does across a disconnect/reconnect cycle -- does it
    come back, and does reconnecting leak a second thread.

These tests fill that in, against the implementation in
`src/model/rotator_system.py`.
"""
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from model.rotator_system import RotatorSystem


SAMPLE_DEADLINE = 3.0


class FlakySMC:
    """A fake SMC100 whose `get_position_deg` can be told to start failing.

    Distinct from the pin's `FakeSMC`: this one is not about wedging a
    transaction, it is about a poll that starts returning errors (a cable
    bump, a device reboot) and then recovers, which is the ROTATOR-9
    interaction the pin does not cover.
    """

    def __init__(self):
        self.polls = 0
        self.failing = False
        self.closed = False

    def get_position_deg(self):
        self.polls += 1
        if self.failing:
            raise TimeoutError("simulated read timeout")
        return 10.0 + self.polls

    def get_status(self, silent=False):
        return (0, "32")   # "Ready"

    def stop(self, priority=False):
        pass

    def close(self):
        self.closed = True


def _connect(rotator, fake, port="SIM-PORT"):
    fake_module = SimpleNamespace(SMC100=lambda **kw: fake)
    with patch("model.rotator_system.smc100", fake_module):
        rotator.connect(port, smc_id=1)
    assert rotator.is_connected, "test setup: connect() did not take"


def _teardown(rotator):
    try:
        rotator.disconnect()
    except Exception:
        pass


def _wait_until(predicate, deadline=SAMPLE_DEADLINE):
    end = time.time() + deadline
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_sampler_reports_communication_lost_then_recovers():
    """A poll failure mid-stream must surface as ROTATOR-9's state, then
    clear itself once the hardware starts answering again -- with no
    caller involved on either side, since the sampler is the only writer.
    """
    fake = FlakySMC()
    rotator = RotatorSystem(default_port=None)
    try:
        _connect(rotator, fake)

        assert _wait_until(lambda: rotator.position is not None), (
            "sampler never published an initial value")

        fake.failing = True
        assert _wait_until(lambda: rotator.state == "Communication lost"), (
            f"sampler kept reporting {rotator.state!r} after the device "
            f"started failing; ROTATOR-9 requires a visible state change, "
            f"not stale values")
        assert rotator.position is None, (
            "position must be cleared, not left at its last known value, "
            "while the device is unreachable")

        fake.failing = False
        assert _wait_until(lambda: rotator.state == "Ready"), (
            "sampler did not recover once the device started answering "
            "again -- it must not latch 'Communication lost' forever")
        assert rotator.position is not None
    finally:
        _teardown(rotator)


def test_sampler_stops_on_disconnect_and_resumes_on_reconnect():
    """A disconnect/reconnect cycle must both stop the old sampler and
    start a fresh one -- not leave the model permanently unsampled after
    the first disconnect.
    """
    fake1 = FlakySMC()
    rotator = RotatorSystem(default_port=None)
    try:
        _connect(rotator, fake1)
        assert _wait_until(lambda: fake1.polls > 0), "sampler never started"

        rotator.disconnect()
        time.sleep(0.4)
        settled = fake1.polls
        time.sleep(0.4)
        assert fake1.polls == settled, (
            "the old sampler kept polling a disconnected handle")
        assert rotator.position is None, "disconnect must clear position"

        fake2 = FlakySMC()
        _connect(rotator, fake2)
        assert _wait_until(lambda: fake2.polls > 0), (
            "reconnecting did not start a new sampler -- the card stays "
            "frozen forever after the first disconnect")
        assert _wait_until(lambda: rotator.position is not None), (
            "reconnected sampler never published a value")
    finally:
        _teardown(rotator)


def test_reconnect_cycle_does_not_leak_sampler_threads():
    """Two connect/disconnect cycles must leave at most one sampler thread
    alive at a time -- not stack a new one on top of an old one that never
    fully exited.
    """
    rotator = RotatorSystem(default_port=None)
    try:
        before = threading.active_count()
        for _ in range(3):
            fake = FlakySMC()
            _connect(rotator, fake)
            _wait_until(lambda: fake.polls > 0, deadline=1.0)
            rotator.disconnect()
            time.sleep(0.35)   # let the stopped thread actually exit
        after = threading.active_count()
        assert after <= before + 1, (
            f"three connect/disconnect cycles left {after - before} extra "
            f"threads running; the sampler is stacking rather than "
            f"replacing itself")
    finally:
        _teardown(rotator)


def test_connect_called_twice_does_not_start_a_second_sampler_thread():
    """`connect()` while already connected is a no-op (existing contract);
    it must not spawn a second sampler thread racing the first one.
    """
    fake = FlakySMC()
    rotator = RotatorSystem(default_port=None)
    try:
        _connect(rotator, fake)
        assert _wait_until(lambda: fake.polls > 0)
        before = threading.active_count()
        # Second call: is_connected is already True, so this returns early
        # inside RotatorSystem.connect() without touching the sampler.
        rotator.connect("SIM-PORT-2", smc_id=1)
        time.sleep(0.3)
        after = threading.active_count()
        assert after == before, (
            f"a redundant connect() call changed the thread count by "
            f"{after - before}")
    finally:
        _teardown(rotator)
