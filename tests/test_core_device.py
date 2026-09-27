"""`devices.device` — the one shape a Model reports its hardware in."""
import pytest

from devices.device import Device

from test_core_fakes import FakeDevice, FakeModel

pytestmark = pytest.mark.transport


def test_the_base_device_refuses_to_pretend_it_opened():
    """A Device that silently no-ops is a port a Model believes it owns."""
    device = Device()
    for call in (device.open, device.close):
        with pytest.raises(NotImplementedError):
            call()
    with pytest.raises(NotImplementedError):
        device.is_open


def test_status_is_derived_from_is_open_unless_a_subclass_says_otherwise():
    class Plain(Device):
        def __init__(self):
            self._open = False

        def open(self):
            self._open = True

        def close(self):
            self._open = False

        @property
        def is_open(self):
            return self._open

    device = Plain()
    assert device.status == "closed"
    device.open()
    assert device.status == "open"


def test_the_surface_is_open_close_is_open_status_and_the_two_declared_defaults():
    """"Nothing else is shared, on purpose." A Model that reaches past this
    is a Model whose hardware cannot be swapped for a simulator. MOD-5 adds
    the two things callers used to duck-type: `is_hardware` (the views'
    hardware-link count) and `set_gate` / `is_gate_open` (window focus)."""
    public = {n for n in vars(Device) if not n.startswith("_")}
    assert public == {"open", "close", "is_open", "status",
                      "is_hardware", "set_gate", "is_gate_open"}


# -- MOD-5 / CON-6: declared, not duck-typed --------------------------------

def test_a_device_is_not_a_hardware_link_unless_it_says_so():
    assert Device.is_hardware is False
    assert FakeDevice().is_hardware is False


def test_the_serial_port_and_the_smc100_are_hardware_links_and_the_screen_is_not():
    from devices.serial_port import SerialPort
    from devices.smc100 import SMC100
    from devices.screen import Screen
    assert SerialPort.is_hardware is True
    assert SMC100.is_hardware is True
    assert Screen.is_hardware is False


def test_every_device_answers_set_gate_and_only_remembers_it():
    """The base gate is inert: a device that reads no manual input has
    nothing to hold, but a caller (Controller.set_input_focus) may tell every
    device without asking first, and can read back what it said."""
    device = FakeDevice()
    assert device.is_gate_open is True
    assert device.set_gate(False) is None
    assert device.is_gate_open is False
    device.open()
    assert device.status == "verified"          # nothing else moved
    device.set_gate(True)
    assert device.is_gate_open is True


def test_a_subclass_may_report_a_richer_status_word():
    assert FakeDevice(status="simulated").status == "closed"
    device = FakeDevice(status="simulated")
    device.open()
    assert device.status == "simulated"


def test_a_models_state_reports_every_device_by_class_name():
    model = FakeModel(devices=[FakeDevice("a")])
    model.open()
    assert model.state["devices"] == {"FakeDevice": "verified"}


def test_two_devices_of_one_class_collapse_into_one_state_key():
    """Documented, not asserted as desirable: `Model.state` keys `devices` by
    `type(d).__name__`, so a model owning two SerialPorts reports one. Any
    model that does needs to override `state`."""
    model = FakeModel(devices=[FakeDevice("a"), FakeDevice("b")])
    model.open()
    assert list(model.state["devices"]) == ["FakeDevice"]
