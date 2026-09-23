"""The device registry: one authority for what devices exist (RC-7).

Before this there were **four** copies of the device list, and they disagreed:

* `app_bootstrap._build_each` dispatched on the names in an if/elif chain;
* `app_bootstrap.DEVICE_MAP` mapped firmware identity characters to names and
  knew only four of the six;
* `views/pyside/view.py` kept its own literal list for the sidebar;
* the S0 invariant harness had to hardcode a fourth copy, deliberately, so
  that it would fail loudly when the others drifted.

That last one is the tell. A list duplicated four ways, where one copy exists
purely to catch the others drifting, is the shape RC-7 describes.

Everything now reads `DEVICES`. Adding a device means adding one entry here:
the builder, the identity character its firmware answers with (where it has
one), and whether it needs a serial port at all.
"""


class Device:
    """One device kind. `build(port, controller_id, claims)` constructs it."""

    __slots__ = ("name", "_factory", "identity_char", "needs_port",
                 "needs_controller")

    def __init__(self, name, factory, *, identity_char=None, needs_port=True,
                 needs_controller=True):
        self.name = name
        self._factory = factory
        self.identity_char = identity_char
        self.needs_port = needs_port
        self.needs_controller = needs_controller

    def build(self, port, controller_id, claims):
        return self._factory(port, controller_id, claims)

    def __repr__(self):
        return f"Device({self.name!r})"


def _stepper(port, controller_id, claims):
    from model.probes import StepperProbe
    return StepperProbe(port, controller_id, claims)


def _dc(port, controller_id, claims):
    from model.probes import DCProbe
    return DCProbe(port, controller_id, claims)


def _chuck(port, controller_id, claims):
    from model.probes import ChuckPositioner
    return ChuckPositioner(port, controller_id, claims)


def _temperature(port, _controller_id, _claims):
    from model.temperature_system import TemperatureSystem
    return TemperatureSystem(port)


def _rotator(port, _controller_id, _claims):
    from model.rotator_system import RotatorSystem
    return RotatorSystem(port)


def _red_percent(_port, _controller_id, _claims):
    from model.redpercent_system import RedPercentSystem
    return RedPercentSystem()


#: Registration order is display order: the sidebar and the Tk tab strip both
#: iterate this, so the device list cannot differ between frontends.
DEVICES = {
    d.name: d for d in (
        Device("Stepper Probe", _stepper, identity_char="s"),
        Device("DC Probe", _dc, identity_char="d"),
        Device("Chuck Positioner", _chuck, identity_char="c"),
        Device("Temperature Controller", _temperature, identity_char="t",
               needs_controller=False),
        # The SMC100 answers an ASCII protocol at 57600 rather than the custom
        # firmware's DEV: reply, so it has no identity character.
        Device("SMC100 Rotator", _rotator, needs_controller=False),
        # Screen capture, not hardware: no port, no controller.
        Device("Red Percent Window", _red_percent, needs_port=False,
               needs_controller=False),
    )
}

#: Firmware identity character -> device name, derived rather than declared
#: again. `DEVICE_MAP` used to be a separate literal that knew four of the six.
IDENTITY_CHARS = {
    d.identity_char: d.name for d in DEVICES.values() if d.identity_char
}


def names():
    """Every device name, in display order."""
    return list(DEVICES)


def get(name):
    return DEVICES.get(name)


def build(name, port, controller_id, claims):
    """Construct `name`, or return None if it is not a known device."""
    device = DEVICES.get(name)
    if device is None:
        return None
    return device.build(port, controller_id, claims)
