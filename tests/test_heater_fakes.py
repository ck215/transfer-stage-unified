"""Doubles shared by the Heater tests. No tests of its own.

Named `test_heater_*` so it stays inside this agent's write set; pytest
collects it, finds nothing, and moves on.

`devices.serial_port.SerialPort` is still a skeleton in this worktree
(every method raises `NotImplementedError`), so `FakePort` implements the
interface the rebuild brief pins instead:

    .open() .close() .is_open .status
    .write(payload: bytes, *, priority=False, abort_if=None) -> bool
    .read_line(timeout=None) -> str | None
    .flush(timeout) -> bool

It records every call in order, which is what lets the close-path test assert
that the heater-off frame is written, drained and only then followed by the
port closing.
"""
import collections
import threading


class PortCall:
    __slots__ = ("kind", "payload", "priority", "is_aborted")

    def __init__(self, kind, payload=None, priority=False, is_aborted=False):
        self.kind = kind
        self.payload = payload
        self.priority = priority
        self.is_aborted = is_aborted

    def __repr__(self):
        if self.kind != "write":
            return f"<{self.kind}>"
        return (f"<write {self.payload!r} priority={self.priority} "
                f"aborted={self.is_aborted}>")


class FakePort:
    """A SerialPort stand-in that acknowledges everything and remembers it."""

    def __init__(self, status="verified", is_open=True):
        self.calls = []
        self.lines = collections.deque()
        self.read_error = None
        self.write_error = None
        self.flush_result = True
        self.open_count = 0
        self._is_open = is_open
        self._status = status
        self._lock = threading.Lock()

    # -- Device ------------------------------------------------------------
    def open(self):
        self.open_count += 1
        self._is_open = True

    def close(self):
        with self._lock:
            self.calls.append(PortCall("close"))
        self._is_open = False

    @property
    def is_open(self):
        return self._is_open

    @property
    def status(self):
        return self._status if self._is_open else "closed"

    # -- SerialPort --------------------------------------------------------
    def write(self, payload, *, priority=False, abort_if=None):
        if self.write_error is not None:
            raise self.write_error
        is_aborted = bool(abort_if is not None and abort_if())
        with self._lock:
            self.calls.append(PortCall("write", payload, priority, is_aborted))
        return not is_aborted

    def read_line(self, timeout=None):
        if self.read_error is not None:
            raise self.read_error
        with self._lock:
            return self.lines.popleft() if self.lines else None

    def flush(self, timeout=None):
        with self._lock:
            self.calls.append(PortCall("flush"))
        return self.flush_result

    # -- what the tests ask ------------------------------------------------
    @property
    def frames(self):
        """Payloads that actually reached the wire, in order."""
        with self._lock:
            return [c.payload for c in self.calls
                    if c.kind == "write" and not c.is_aborted]

    @property
    def writes(self):
        with self._lock:
            return [c for c in self.calls if c.kind == "write"]

    @property
    def kinds(self):
        with self._lock:
            return [c.kind for c in self.calls]

    def forget(self):
        with self._lock:
            self.calls.clear()


class EventRecorder:
    """Subscribes to the one event log so a test can assert on severity."""

    def __init__(self, events):
        self._events = events
        self.seen = []

    def __enter__(self):
        self._events.subscribe(self.seen.append)
        return self

    def __exit__(self, *_):
        self._events.unsubscribe(self.seen.append)
        return False

    def of(self, severity):
        return [e for e in self.seen if e.severity == severity]

    def titled(self, title):
        return [e for e in self.seen if e.title == title]
