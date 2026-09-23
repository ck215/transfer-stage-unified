"""REDPERCENT-4 (the probes.py share) — a failing poller must not kill a reader.

The Red Percent monitor thread samples the stepper on every frame with
`getattr(self.stepper_model, 'vel_x', 0.0)`. The position reads beside it are
wrapped; these are not, and the `getattr` default does **not** shield them,
because `vel_x` is a property whose body runs `poller.get_mapped_state()`.
An exception raised in there propagates straight out through the `getattr`,
out of the monitor loop, and takes the thread with it — leaving
`monitoring=True` with nothing monitoring and a Start button that does
nothing on the next press.

Only the model half of REDPERCENT-4 is here. The loop's own `try`/`except`,
the `mss is None` path and the dependency check in `start_monitoring` all
live in `src/model/redpercent_system.py`, which is being rewritten for S13
and is not this worktree's to touch.

`get_mapped_state` is a real place for an exception to come from: it reads
the SDL device through the shared poller, and a controller unplugged
mid-session is the ordinary case.
"""

from unittest.mock import MagicMock

import pytest

import error_routing
from error_routing import EventBus
from model.probes import BaseProbe

AXES = ["vel_x", "vel_y", "vel_z"]


@pytest.fixture
def isolated_bus(monkeypatch):
    """A fresh `EventBus` in place of the process one, for this test only.

    `ErrorRouter`'s static methods resolve `bus` out of the module globals at
    call time, so replacing the name is enough — and it keeps these
    assertions about *this* test's events rather than about whatever the rest
    of the session has already published under the same key.
    """
    fresh = EventBus()
    monkeypatch.setattr(error_routing, "bus", fresh)
    return fresh


class ExplodingPoller:
    """A poller whose device has gone away underneath it."""

    def __init__(self, exc=None):
        self.exc = exc or RuntimeError("joystick 0 is no longer attached")
        self.calls = 0
        self.gamepad = object()

    def get_mapped_state(self):
        self.calls += 1
        raise self.exc


@pytest.fixture
def probe():
    p = BaseProbe("SIM", "TEST_CTRL")
    p.poller = ExplodingPoller()
    return p


# --------------------------------------------------------------------------
# The reader survives.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("axis", AXES)
def test_a_velocity_read_survives_a_poller_that_raises(probe, axis):
    assert getattr(probe, axis) == 0.0


@pytest.mark.parametrize("axis", AXES)
def test_getattr_with_a_default_does_not_shield_a_raising_property(probe, axis):
    """This is the exact call the Red Percent monitor thread makes.

    `getattr(obj, name, default)` returns the default only when the attribute
    is *missing*. A property that raises is not missing — the exception comes
    out, and the thread dies.
    """
    assert getattr(probe, axis, 0.0) == 0.0


@pytest.mark.parametrize("exc", [
    RuntimeError("device lost"),
    AttributeError("joystick"),
    OSError("SDL"),
    KeyError("x_axisStatus"),
])
def test_any_poller_failure_is_contained(probe, exc):
    probe.poller = ExplodingPoller(exc)
    assert (probe.vel_x, probe.vel_y, probe.vel_z) == (0.0, 0.0, 0.0)


# --------------------------------------------------------------------------
# ...but it is not silent, and it does not spam.
# --------------------------------------------------------------------------


def test_a_failing_poller_is_reported(probe, isolated_bus):
    probe.vel_x
    events = isolated_bus.since(0)
    assert events, "a dead poller read as 0.0 with nobody told"
    assert "joystick 0 is no longer attached" in events[0].message


def test_repeated_failures_fold_into_one_event(probe, isolated_bus):
    """The monitor samples at ~60 Hz; this must not become 60 popups a second.

    Folding is the bus's job — a repeat of the same
    `(severity, source, title)` inside the window increments `count` and
    never reaches a subscriber — so the reader is allowed to report on every
    failed read without a rate limit of its own.
    """
    for _ in range(60):
        probe.vel_x

    events = isolated_bus.since(0)
    assert len(events) == 1, f"{len(events)} events from 60 failing reads"
    assert events[0].count == 60


# --------------------------------------------------------------------------
# Regression guards: the working path is unchanged.
# --------------------------------------------------------------------------


def test_a_healthy_poller_still_reports_velocities():
    probe = BaseProbe("SIM", "TEST_CTRL")
    probe.poller = MagicMock()
    probe.poller.get_mapped_state.return_value = {
        "x_axisStatus": 1.5,
        "y_axisStatus": "2.5",
        "z_axisStatusR": 3.0,
        "z_axisStatusL": 1.0,
    }
    assert probe.vel_x == 1.5
    assert probe.vel_y == 2.5
    assert probe.vel_z == 1.0


def test_unparseable_axis_values_still_read_as_zero():
    probe = BaseProbe("SIM", "TEST_CTRL")
    probe.poller = MagicMock()
    probe.poller.get_mapped_state.return_value = {
        "x_axisStatus": None,
        "y_axisStatus": "invalid_string",
        "z_axisStatusR": None,
        "z_axisStatusL": "",
    }
    assert probe.vel_x == 0.0
    assert probe.vel_y == 0.0
    assert probe.vel_z == 0.0


def test_no_poller_at_all_reads_as_zero():
    probe = BaseProbe("SIM", "TEST_CTRL")
    probe.poller = None
    assert (probe.vel_x, probe.vel_y, probe.vel_z) == (0.0, 0.0, 0.0)
