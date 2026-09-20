"""I-1.2 / I-1.3 — teardown is safety-first and exception-safe.

RC-1's teardown contract is an *order* plus an *isolation* guarantee:

    hardware stop  ->  background activity stop  ->  transport close

each step in its own try/finally, so that a failure in any one of them
cannot prevent the others. The order matters because the first step is the
one that de-energizes hardware; the isolation matters because the original
code stopped the gamepad poller first with no guard, which meant a raising
poller cleanup skipped `power_down()` entirely and left coils energized
while Python believed the system was disabled (SERIAL-2, DC-3).

These tests inject a fault at each sub-step in turn. That is what I-1.2
asks for, and it is the only way to prove the guarantee: an ordering test
alone passes happily on code that has no try/finally at all.
"""

from unittest.mock import MagicMock, call, patch

import pytest

from model.probes import DCProbe
from model.redpercent_system import RedPercentSystem
from model.rotator_system import RotatorSystem
from model.system_manager import SystemManager
from model.temperature_system import TemperatureSystem


def _probe_with_mocks():
    """A probe whose poller and transport are mocks, so teardown is observable."""
    probe = DCProbe("SIM", None)
    probe.serial_comm = MagicMock()
    probe.poller = MagicMock()
    return probe


# --------------------------------------------------------------------------
# BaseProbe
# --------------------------------------------------------------------------


def test_probe_teardown_order_is_stop_then_poller_then_transport():
    probe = _probe_with_mocks()
    order = []
    with patch.object(DCProbe, "power_down", side_effect=lambda: order.append("stop")):
        probe.poller.stop_polling.side_effect = lambda: order.append("poller_stop")
        probe.poller.close.side_effect = lambda: order.append("poller_close")
        probe.serial_comm.close.side_effect = lambda: order.append("transport_close")
        probe.teardown()
    assert order == ["stop", "poller_stop", "poller_close", "transport_close"], order


def test_probe_teardown_sends_hardware_stop_when_poller_stop_raises():
    probe = _probe_with_mocks()
    probe.poller.stop_polling.side_effect = RuntimeError("poller is wedged")
    with patch.object(DCProbe, "power_down") as power_down:
        probe.teardown()
    power_down.assert_called_once()
    probe.serial_comm.close.assert_called_once()


def test_probe_teardown_sends_hardware_stop_when_poller_close_raises():
    probe = _probe_with_mocks()
    probe.poller.close.side_effect = RuntimeError("pygame is unhappy")
    with patch.object(DCProbe, "power_down") as power_down:
        probe.teardown()
    power_down.assert_called_once()
    probe.serial_comm.close.assert_called_once()


def test_probe_teardown_closes_transport_when_hardware_stop_raises():
    """The stop is attempted first, but a failing stop must not strand the port."""
    probe = _probe_with_mocks()
    with patch.object(DCProbe, "power_down", side_effect=RuntimeError("write failed")):
        probe.teardown()
    probe.poller.stop_polling.assert_called_once()
    probe.serial_comm.close.assert_called_once()


def test_probe_teardown_survives_a_failing_transport_close():
    probe = _probe_with_mocks()
    probe.serial_comm.close.side_effect = RuntimeError("port already gone")
    with patch.object(DCProbe, "power_down") as power_down:
        probe.teardown()  # must not raise
    power_down.assert_called_once()


def test_probe_teardown_is_idempotent():
    probe = _probe_with_mocks()
    with patch.object(DCProbe, "power_down"):
        probe.teardown()
        probe.teardown()  # must not raise on the second pass


# --------------------------------------------------------------------------
# RotatorSystem — MANAGER-10 / ROTATOR-1: teardown never sent the stop.
# --------------------------------------------------------------------------


def test_rotator_teardown_sends_stop_before_disconnecting():
    rotator = RotatorSystem()
    rotator.smc = MagicMock()
    order = []
    rotator.smc.stop.side_effect = lambda: order.append("stop")
    rotator.smc.close.side_effect = lambda: order.append("close")
    rotator.teardown()
    assert order == ["stop", "close"], order


def test_rotator_teardown_disconnects_when_stop_raises():
    rotator = RotatorSystem()
    rotator.smc = MagicMock()
    rotator.smc.stop.side_effect = RuntimeError("SMC100 not answering")
    rotator.teardown()
    assert rotator.is_connected is False
    assert rotator.smc is None


# --------------------------------------------------------------------------
# TemperatureSystem
# --------------------------------------------------------------------------


def test_temperature_teardown_stops_before_closing():
    temp = TemperatureSystem()
    order = []
    with patch.object(TemperatureSystem, "stop", side_effect=lambda: order.append("stop")):
        with patch.object(TemperatureSystem, "close", side_effect=lambda: order.append("close")):
            temp.teardown()
    assert order == ["stop", "close"], order


def test_temperature_teardown_closes_when_stop_raises():
    temp = TemperatureSystem()
    with patch.object(TemperatureSystem, "stop", side_effect=RuntimeError("no serial")):
        with patch.object(TemperatureSystem, "close") as close:
            temp.teardown()
    close.assert_called_once()


# --------------------------------------------------------------------------
# RedPercentSystem — background activity only, but it must actually stop.
# --------------------------------------------------------------------------


def test_redpercent_teardown_stops_monitoring_and_joins_the_thread():
    red = RedPercentSystem()
    red.monitoring = True
    thread = MagicMock()
    thread.is_alive.return_value = True
    red._monitor_thread = thread
    red.teardown()
    assert red.monitoring is False
    thread.join.assert_called_once()


def test_redpercent_teardown_survives_a_thread_that_will_not_join():
    red = RedPercentSystem()
    red.monitoring = True
    thread = MagicMock()
    thread.is_alive.return_value = True
    thread.join.side_effect = RuntimeError("cannot join current thread")
    red._monitor_thread = thread
    red.teardown()  # must not raise
    assert red.monitoring is False


# --------------------------------------------------------------------------
# SystemManager.shutdown_all — RC-1 item 4.
# --------------------------------------------------------------------------


class ManagedStub:
    """A real class, not a Mock.

    Under Python 3.12+ a `runtime_checkable` Protocol's isinstance check is
    stricter than `hasattr`, and no Mock satisfies it however it is spec'd.
    That is the right outcome: a Mock passing a lifecycle contract check was
    never evidence of anything. Registration is a contract boundary, so the
    things registered in these tests are real objects.
    """

    def __init__(self, order, name, stop_error=None, teardown_error=None):
        self.order, self.name = order, name
        self.stop_error, self.teardown_error = stop_error, teardown_error
        self.stops = self.teardowns = 0

    def emergency_stop(self):
        self.stops += 1
        self.order.append(f"{self.name}:stop")
        if self.stop_error:
            raise self.stop_error

    def teardown(self):
        self.teardowns += 1
        self.order.append(f"{self.name}:teardown")
        if self.teardown_error:
            raise self.teardown_error


def _managed(order, name, **kw):
    return ManagedStub(order, name, **kw)


def test_shutdown_all_stops_every_model_before_tearing_any_down():
    """No model's teardown can skip the stop, because the stop already happened."""
    manager = SystemManager()
    order = []
    manager.register("a", _managed(order, "a"))
    manager.register("b", _managed(order, "b"))
    manager.shutdown_all()
    assert order.index("a:stop") < order.index("a:teardown")
    assert order.index("b:stop") < order.index("b:teardown")


def test_shutdown_all_tears_down_the_rest_when_one_model_raises():
    manager = SystemManager()
    order = []
    bad = _managed(order, "bad", teardown_error=RuntimeError("wedged"))
    good = _managed(order, "good")
    manager.register("bad", bad)
    manager.register("good", good)
    manager.shutdown_all()
    assert good.teardowns == 1


def test_shutdown_all_tears_down_even_when_emergency_stop_raises():
    manager = SystemManager()
    order = []
    model = _managed(order, "m", stop_error=RuntimeError("stop failed"))
    manager.register("m", model)
    manager.shutdown_all()
    assert model.teardowns == 1


def test_shutdown_all_is_idempotent():
    manager = SystemManager()
    order = []
    model = _managed(order, "m")
    manager.register("m", model)
    manager.shutdown_all()
    manager.shutdown_all()
    assert model.teardowns == 1


# --------------------------------------------------------------------------
# register() is a contract boundary — RC-1 item 1.
# --------------------------------------------------------------------------


def test_register_rejects_a_model_that_is_not_managed():
    """The hasattr ladders this replaces skipped such a model at shutdown."""
    manager = SystemManager()

    class NotManaged:
        pass

    with pytest.raises(TypeError):
        manager.register("nope", NotManaged())
    with pytest.raises(TypeError):
        manager.register("nope", "not a model at all")
    assert manager.get_model("nope") is None


def test_register_refuses_to_overwrite_a_live_model():
    """Overwriting would drop a live model without ever tearing it down."""
    manager = SystemManager()
    order = []
    first = _managed(order, "first")
    manager.register("m", first)
    with pytest.raises(ValueError):
        manager.register("m", _managed(order, "second"))
    assert manager.get_model("m") is first
    assert first.teardowns == 0


def test_release_removes_and_tears_down():
    """remove_model used to do only the first half, which leaked the port."""
    manager = SystemManager()
    order = []
    model = _managed(order, "m")
    manager.register("m", model)
    manager.release("m")
    assert manager.get_model("m") is None
    assert order == ["m:stop", "m:teardown"]


def test_release_is_a_no_op_for_an_unknown_name():
    assert SystemManager().release("never registered") is None


def test_reconfigure_tears_down_before_building():
    """Building first meant two live handles on one port (I-1.4)."""
    manager = SystemManager()
    order = []
    manager.register("old", _managed(order, "old"))

    def builder(mgr):
        order.append("build")
        mgr.register("new", _managed(order, "new"))

    manager.reconfigure(builder)
    assert order.index("old:teardown") < order.index("build")
    assert manager.get_model("old") is None
    assert manager.get_model("new") is not None
