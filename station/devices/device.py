"""Base of everything a Model owns that touches the outside world."""


class Device:
    """`open()`, `close()`, `is_open`, `status`. Nothing else is shared, on
    purpose: this is how a Model reports its hardware upward in one shape."""

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
