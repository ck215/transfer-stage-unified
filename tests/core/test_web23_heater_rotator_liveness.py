"""WEB-23 (D-8a): the web client-liveness gate covers the heater and rotator.

The owner ruled on 2026-09-21 that D-8's gate is not probes-only. Before
this, `WebModelAdapter.record_client_heartbeat` looked up
`touch_client_liveness` with a duck-typed `getattr` and silently found
nothing on `TemperatureSystem` or `RotatorSystem`, so a heater brought to
setpoint from a browser kept heating after the tab closed - with no
firmware watchdog behind it.

The same three rules as BaseProbe's gate are pinned for both:
  * a desktop session (no web check-in, ever) is never gated;
  * an idle device is never stopped, however long the silence;
  * an active device is warned once, then FULL STOPped (latched).
"""
import time
from unittest.mock import MagicMock

import pytest

from model.rotator_system import RotatorSystem
from model.temperature_system import TemperatureSystem
from views.web.web_adapter import WebModelAdapter


# -- doubles ---------------------------------------------------------------

def _heater():
    h = TemperatureSystem(port=None)
    conn = MagicMock()
    conn.is_open.return_value = True
    h.serial_conn = conn
    return h


def _heating(h):
    h.setpoint = "80"
    h.send_settings()
    return h


def _rotator(state="Ready"):
    r = RotatorSystem(default_port=None)
    r.smc = MagicMock()
    r.is_connected = True
    r.state = state
    return r


def _silent_for(model, seconds):
    model.last_client_seen_time = time.time() - seconds


@pytest.fixture
def events(monkeypatch):
    seen = []
    from error_routing import ErrorRouter
    monkeypatch.setattr(ErrorRouter, "report_error",
                        staticmethod(lambda t, m, *a, **k: seen.append(("error", t))))
    monkeypatch.setattr(ErrorRouter, "report_warning",
                        staticmethod(lambda t, m, *a, **k: seen.append(("warning", t))))
    return seen


def _cleanup(*models):
    for m in models:
        m.stop_client_liveness_watchdog()


# -- the seam ----------------------------------------------------------------

@pytest.mark.parametrize("cls", [TemperatureSystem, RotatorSystem])
def test_web23_both_models_answer_the_adapters_heartbeat_hook(cls):
    assert callable(getattr(cls, WebModelAdapter.CLIENT_HEARTBEAT_HOOK, None)), (
        f"{cls.__name__} must define {WebModelAdapter.CLIENT_HEARTBEAT_HOOK}; "
        "without it the adapter's getattr silently skips it (WEB-23)")


@pytest.mark.parametrize("cls", [TemperatureSystem, RotatorSystem])
def test_web23_timeouts_are_their_own_and_marked_provisional(cls):
    from model.probes import BaseProbe
    warn, stop = cls.WEB_CLIENT_WARN_TIMEOUT, cls.WEB_CLIENT_STOP_TIMEOUT
    assert 0 < warn < stop
    import inspect, model.client_liveness  # noqa: F401
    src = inspect.getsource(inspect.getmodule(cls))
    assert "PROVISIONAL" in src
    if cls is TemperatureSystem:
        # The heater's window is a thermal question, not a motion one.
        assert (warn, stop) != (BaseProbe.WEB_CLIENT_WARN_TIMEOUT,
                                BaseProbe.WEB_CLIENT_STOP_TIMEOUT)


# -- heater ------------------------------------------------------------------

def test_web23_heater_is_stopped_after_web_silence_while_heating(events):
    h = _heating(_heater())
    try:
        h.touch_client_liveness()
        _silent_for(h, h.WEB_CLIENT_STOP_TIMEOUT + 1)
        h._check_client_liveness()
        assert h.estop_latched, "silence past the stop timeout must FULL STOP"
        written = [str(c.args[0]) for c in h.serial_conn.write_command.call_args_list]
        assert written[-1].startswith("<0,"), written
        assert ("error", "Client Liveness FULL STOP") in events
    finally:
        _cleanup(h)


def test_web23_heater_desktop_session_is_never_gated(events):
    h = _heating(_heater())
    try:
        # No touch_client_liveness(): a Tk/PySide session.
        h._check_client_liveness()
        assert not h.estop_latched
        assert h._client_liveness_thread is None, (
            "no web client ever checked in; no watchdog thread may exist")
    finally:
        _cleanup(h)


def test_web23_idle_heater_is_left_alone(events):
    h = _heater()  # never sent a nonzero setpoint
    try:
        h.touch_client_liveness()
        _silent_for(h, h.WEB_CLIENT_STOP_TIMEOUT + 60)
        h._check_client_liveness()
        assert not h.estop_latched
        assert events == []
    finally:
        _cleanup(h)


def test_web23_heater_stopped_by_operator_is_idle():
    h = _heating(_heater())
    try:
        assert h._client_liveness_active()
        h.stop()
        assert not h._client_liveness_active()
    finally:
        _cleanup(h)


def test_web23_heater_warns_once_then_a_checkin_clears_it(events):
    h = _heating(_heater())
    try:
        h.touch_client_liveness()
        _silent_for(h, h.WEB_CLIENT_WARN_TIMEOUT + 0.5)
        h._check_client_liveness()
        h._check_client_liveness()
        assert events.count(("warning", "Client Liveness Warning")) == 1
        assert not h.estop_latched
        h.touch_client_liveness()
        _silent_for(h, h.WEB_CLIENT_WARN_TIMEOUT + 0.5)
        h._check_client_liveness()
        assert events.count(("warning", "Client Liveness Warning")) == 2
    finally:
        _cleanup(h)


# -- rotator -----------------------------------------------------------------

@pytest.mark.parametrize("state", ["Moving", "Homing"])
def test_web23_rotator_in_motion_is_stopped_after_web_silence(state, events):
    r = _rotator(state)
    try:
        r.touch_client_liveness()
        _silent_for(r, r.WEB_CLIENT_STOP_TIMEOUT + 1)
        r._check_client_liveness()
        assert r.estop_latched
        r.smc.stop.assert_called()
    finally:
        _cleanup(r)


def test_web23_rotator_at_rest_is_left_alone(events):
    r = _rotator("Ready")
    try:
        r.touch_client_liveness()
        _silent_for(r, r.WEB_CLIENT_STOP_TIMEOUT + 60)
        r._check_client_liveness()
        assert not r.estop_latched
    finally:
        _cleanup(r)


def test_web23_rotator_with_a_move_in_flight_counts_as_active():
    r = _rotator("Ready")
    try:
        with r._motion_lock:
            assert r._client_liveness_active()
    finally:
        _cleanup(r)


# -- watchdog lifecycle -------------------------------------------------------

def test_web23_the_watchdog_runs_the_check_by_itself(events, monkeypatch):
    h = _heating(_heater())
    monkeypatch.setattr(TemperatureSystem, "CLIENT_LIVENESS_INTERVAL", 0.02)
    try:
        h.touch_client_liveness()
        _silent_for(h, h.WEB_CLIENT_STOP_TIMEOUT + 1)
        deadline = time.time() + 2
        while not h.estop_latched and time.time() < deadline:
            time.sleep(0.01)
        assert h.estop_latched, "the watchdog thread never ran the check"
    finally:
        _cleanup(h)


def test_web23_teardown_ends_the_watchdog_and_a_late_checkin_cannot_restart_it():
    r = _rotator("Ready")
    r.touch_client_liveness()
    t = r._client_liveness_thread
    assert t is not None and t.is_alive()
    r.teardown()
    t.join(2)
    assert not t.is_alive()
    r.touch_client_liveness()
    assert r._client_liveness_thread is t


def test_web23_fires_once_not_every_tick(events):
    h = _heating(_heater())
    try:
        h.serial_conn.write_command.side_effect = OSError("unplugged")
        h.touch_client_liveness()
        _silent_for(h, h.WEB_CLIENT_STOP_TIMEOUT + 1)
        for _ in range(5):
            h._check_client_liveness()
        assert events.count(("error", "Client Liveness FULL STOP")) == 1
    finally:
        _cleanup(h)


def test_web23_the_real_adapter_heartbeat_reaches_both_models():
    """End to end through the adapter, not the hook name alone: WEB-19's
    seam once failed silently because each half mocked the other."""
    import threading
    h, r = _heater(), _rotator("Ready")
    adapter = WebModelAdapter.__new__(WebModelAdapter)
    adapter._state_lock = threading.RLock()
    adapter.system_manager = MagicMock()
    adapter.system_manager.get_active_models_snapshot.return_value = {
        "Temperature Controller": h, "SMC100 Rotator": r}
    try:
        adapter.record_client_heartbeat()
        assert h.last_client_seen_time is not None
        assert r.last_client_seen_time is not None
    finally:
        _cleanup(h, r)
