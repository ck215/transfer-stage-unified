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


def test_the_surface_is_only_open_close_is_open_status():
    """"Nothing else is shared, on purpose." A Model that reaches past this
    is a Model whose hardware cannot be swapped for a simulator."""
    public = {n for n in vars(Device) if not n.startswith("_")}
    assert public == {"open", "close", "is_open", "status"}


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
