"""S6 / D-1: closing a device view hides it. It does not end the device.

The distinction these tests defend is lifetime versus visibility. Before S2
a tab close ran the view's own teardown ladder and `del`'d the model from the
manager's dict; S2 withdrew the affordance rather than fix it in place,
because a hidden device keeps running and its control loops still belonged to
the widget being destroyed. S5 moved those loops into the model, which is what
made hide safe to offer at all.

**D-2 (owner): hiding de-energizes.** Hiding removes the operator's ability to
watch the device, so the coils come down on the way out. The transport stays
open, so showing it again costs no handshake.
"""
from unittest.mock import MagicMock

import pytest

from model.base import ManagedModel
from model.system_manager import SystemManager


class FakeDevice(ManagedModel):
    def __init__(self):
        self.disables = 0
        self.teardowns = 0
        self.stops = 0
        self.transport_open = True
        self.disable_raises = None

    def disable(self):
        self.disables += 1
        if self.disable_raises:
            raise self.disable_raises

    def teardown(self):
        self.teardowns += 1
        self.transport_open = False

    def emergency_stop(self):
        self.stops += 1


@pytest.fixture
def manager():
    m = SystemManager()
    m.register("Stepper Probe", FakeDevice())
    return m


def test_hiding_does_not_release_the_model(manager):
    """The leak this replaces: a tab close used to del the model outright."""
    device = manager.get_model("Stepper Probe")
    assert manager.hide("Stepper Probe") is True

    assert manager.get_model("Stepper Probe") is device
    assert device.teardowns == 0
    assert device.transport_open is True, "hiding must not close the port"


def test_hiding_brings_the_hardware_down(manager):
    """D-2. An unwatched axis is not left energized."""
    device = manager.get_model("Stepper Probe")
    manager.hide("Stepper Probe")
    assert device.disables == 1


def test_showing_returns_the_same_model_it_hid(manager):
    """Reopen shows the existing model. It never constructs one."""
    device = manager.get_model("Stepper Probe")
    manager.hide("Stepper Probe")
    assert manager.show("Stepper Probe") is device
    assert manager.is_hidden("Stepper Probe") is False


def test_showing_does_not_re_arm_the_hardware(manager):
    """Re-energizing is an operator action taken while looking at the device."""
    device = manager.get_model("Stepper Probe")
    manager.hide("Stepper Probe")
    manager.show("Stepper Probe")
    assert device.disables == 1
    assert not hasattr(device, "enables"), "show must not enable anything"


def test_showing_an_unconfigured_device_returns_none_rather_than_inventing_one(manager):
    """PYSIDE-1 / MANAGER-8: the view used to fabricate a headless stand-in."""
    assert manager.show("SMC100 Rotator") is None
    assert manager.get_model("SMC100 Rotator") is None


def test_hiding_an_unconfigured_device_is_a_refusal_not_a_crash(manager):
    assert manager.hide("SMC100 Rotator") is False


def test_a_failed_disable_still_hides(manager):
    """A serial write that failed must not trap the operator in a window.

    The model faults and says so (RC-2); the view still closes.
    """
    device = manager.get_model("Stepper Probe")
    device.disable_raises = RuntimeError("port wedged")
    assert manager.hide("Stepper Probe") is True
    assert manager.is_hidden("Stepper Probe") is True


def test_hidden_devices_are_excluded_from_visible_models_but_not_from_active(manager):
    manager.register("DC Probe", FakeDevice())
    manager.hide("DC Probe")
    assert set(manager.visible_models()) == {"Stepper Probe"}
    assert set(manager.get_active_models_snapshot()) == {"Stepper Probe", "DC Probe"}


def test_hiding_is_idempotent(manager):
    manager.hide("Stepper Probe")
    manager.hide("Stepper Probe")
    assert manager.is_hidden("Stepper Probe") is True


# -- hidden state must not survive the model it describes -------------------

def test_release_clears_the_hidden_mark(manager):
    """Otherwise a rebuilt device of the same name comes back invisible."""
    manager.hide("Stepper Probe")
    manager.release("Stepper Probe")
    manager.register("Stepper Probe", FakeDevice())
    assert manager.is_hidden("Stepper Probe") is False


def test_shutdown_clears_the_hidden_marks(manager):
    manager.hide("Stepper Probe")
    manager.shutdown_all()
    assert manager.hidden == set()


def test_a_hidden_device_is_still_torn_down_at_shutdown(manager):
    """The leak that would be worst: hidden and therefore forgotten."""
    device = manager.get_model("Stepper Probe")
    manager.hide("Stepper Probe")
    manager.shutdown_all()
    assert device.teardowns == 1
    assert device.transport_open is False


def test_a_hidden_device_still_answers_full_stop(manager):
    """Out of sight is not out of the stop path."""
    device = manager.get_model("Stepper Probe")
    manager.hide("Stepper Probe")
    results = manager.full_stop_all()
    assert results["Stepper Probe"] is True
    assert device.stops == 1
