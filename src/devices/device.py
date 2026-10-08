"""Base of everything a Model owns that touches the outside world."""


class Device:
    """`open()`, `close()`, `is_open`, `status`, and two declared defaults
    (MOD-5): `is_hardware` and `set_gate` / `is_gate_open`. Nothing else is
    shared, on purpose: this is how a Model reports its hardware upward in
    one shape, and a caller reads these instead of matching a class name or
    probing with `getattr`."""

    #: A hardware link: a device whose loss means the instrument itself is
    #: unreachable (a serial board, a motion controller). The views count
    #: these in the rail's "Simulation, no hardware attached" line, through
    #: the `hardware_devices` list in `Model.state`. A gamepad or a screen
    #: grab is an input or a sensor, not a link, and keeps False.
    is_hardware = False

    #: What the last `set_gate` said; open until told otherwise.
    _gate_is_open = True

    def open(self):
        raise NotImplementedError

    def close(self):
        raise NotImplementedError

    @property
    def is_open(self):
        raise NotImplementedError

    @property
    def status(self):
        """A short word for `Model.state`: 'verified', 'simulated', 'lost',
        'closed', 'bound', 'unbound'..."""
        return "open" if self.is_open else "closed"

    def set_gate(self, is_open):
        """The window-focus gate every manual-input device honours (D-4):
        `Controller.set_input_focus` tells every device of every model, so
        an unfocused window holds manual input. A device that reads no
        manual input has nothing to hold: the base only remembers what it
        was told (`is_gate_open`) and does nothing else. An input device
        (Gamepad) overrides both."""
        self._gate_is_open = bool(is_open)

    @property
    def is_gate_open(self):
        return self._gate_is_open
