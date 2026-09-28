"""MOD-3: the idle interlock is a mixin, not probe code (CON-11).

A fake energizing model defined here (not in src/) mixes in `IdleInterlock`,
writes only the two hooks, and gets the countdown, the one warning per idle
period, the power-down, `extend_idle` and a loop the base stops at close.
"""
import time

import pytest

import schema as sch
from events import events
from model.base import Model
from model.idle import IdleInterlock
from result import Refused

from test_core_fakes import EventRecorder

pytestmark = pytest.mark.loops


class Lamp(IdleInterlock, Model):
    """On/off, nothing else: the least an energizing device can be."""
    NAME = "Lamp"
    INTERLOCK_POLL_INTERVAL = 0.01
    INTERLOCK_TIMEOUT = 0.4
    IDLE_WARN_SECONDS = 0.25

    def __init__(self):
        self.on = False
        self.expired_with = []
        super().__init__()

    @property
    def schema(self):
        return sch.schema(
            sch.section("Lamp", sch.indicator("On", "on"),
                        {"type": "internal", "command": "extend_idle",
                         "writable": False, "role": "neutral"}),
            self._safety_section())

    def _expects_heartbeat(self):
        return False

    def switch_on(self):
        self.on = True
        self._start_interlock()

    def switch_off(self):
        self.on = False
        self._stop_interlock()

    # the two hooks
    @property
    def _idle_is_armed(self):
        return self.on

    def _on_idle_expired(self, idle):
        self.expired_with.append(idle)
        self.switch_off()


def _wait_for(predicate, timeout=3.0):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.005)
    return predicate()


@pytest.fixture
def lamp():
    built = Lamp()
    yield built
    built.close()


def test_the_countdown_is_none_until_armed_and_published_in_state(lamp):
    assert lamp.idle_remaining is None
    assert lamp.state["idle_remaining"] is None
    assert lamp.state["idle_warn_seconds"] == Lamp.IDLE_WARN_SECONDS
    lamp.switch_on()
    assert 0.3 < lamp.idle_remaining <= 0.4
    assert lamp.state["idle_remaining"] == pytest.approx(lamp.idle_remaining, abs=0.05)


def test_it_warns_once_then_powers_down_through_the_hook(lamp):
    with EventRecorder() as log:
        lamp.switch_on()
        assert _wait_for(lambda: log.titled(events.IDLE_TIMEOUT_SOON))
        assert lamp.on, "the warning must come BEFORE the power-down"
        assert _wait_for(lambda: not lamp.on)
    assert len(log.titled(events.IDLE_TIMEOUT_SOON)) == 1
    assert log.titled(events.IDLE_TIMEOUT)
    assert len(lamp.expired_with) == 1 and lamp.expired_with[0] > Lamp.INTERLOCK_TIMEOUT
    assert lamp.idle_remaining is None


def test_activity_holds_it_off(lamp):
    lamp.switch_on()
    for _ in range(30):
        lamp._touch_activity()
        time.sleep(0.02)
    assert lamp.on and not lamp.expired_with


def test_extend_idle_restarts_the_clock_through_run(lamp):
    with EventRecorder() as log:
        lamp.switch_on()
        assert _wait_for(lambda: log.titled(events.IDLE_TIMEOUT_SOON))
        assert lamp.run("extend_idle").is_ok
        assert lamp.idle_remaining > 0.3
        assert _wait_for(lambda: len(log.titled(events.IDLE_TIMEOUT_SOON)) == 2)


def test_extend_idle_is_refused_when_not_armed(lamp):
    with pytest.raises(Refused):
        lamp.extend_idle()


def test_a_raising_power_down_is_reported(lamp):
    def _boom(idle):
        raise OSError("relay stuck")
    lamp._on_idle_expired = _boom
    with EventRecorder() as log:
        lamp.switch_on()
        assert _wait_for(lambda: log.titled("Idle Disable Failed"))


def test_each_arming_gets_a_fresh_event_and_generation(lamp):
    lamp.switch_on()
    first, generation = lamp._interlock_stop, lamp._interlock_generation
    lamp.switch_off()
    assert first.is_set()
    lamp.switch_on()
    assert lamp._interlock_stop is not first and not lamp._interlock_stop.is_set()
    assert lamp._interlock_generation > generation


def test_the_loop_is_spawned_through_the_base_and_stopped_at_close():
    lamp = Lamp()
    lamp.INTERLOCK_POLL_INTERVAL = 30.0   # parked: only the stop can wake it
    lamp.switch_on()
    thread = lamp._thread("interlock")
    assert thread is not None and thread.is_alive()
    started = time.monotonic()
    lamp.close()
    assert not thread.is_alive()
    assert time.monotonic() - started < 1.0, "close waited out the poll interval"


def test_the_timeout_asks_for_an_acknowledgement_and_the_countdown_does_not(lamp):
    """rb-ack A2: the clock ending manual mode is a popup the operator must
    answer; the countdown before it (with its Extend) stays a tray line."""
    with EventRecorder() as log:
        lamp.switch_on()
        assert _wait_for(lambda: log.titled(events.IDLE_TIMEOUT))
    soon = log.titled(events.IDLE_TIMEOUT_SOON)
    timeout = log.titled(events.IDLE_TIMEOUT)
    assert soon and all(e.needs_ack is False for e in soon)
    assert len(timeout) == 1 and timeout[0].needs_ack is True
    assert timeout[0].severity == "warning"
    assert "powered down" in timeout[0].message
