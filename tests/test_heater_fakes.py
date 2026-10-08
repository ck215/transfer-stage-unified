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


class FakeBoard:
    """A pyserial handle behind a REAL `SerialPort`, standing in for the
    Teensy running `firmware/temp_controller/temp_controller.ino`.

    What it copies from the sketch, and nothing more:
      * `s` is answered with `DEV: t` (the identity handshake);
      * a `<endpoint,spdelay,kp,ki,kd,offset>` frame sets `endpoint` from its
        first field (`parseData`'s `atof`);
      * it streams `timer , temp , setpoint` lines, and the setpoint it
        prints is `endpoint` (with endpoint 0 the sketch snaps its ramped
        `setpoint` to 0 on the first tick after the frame);
      * it keeps streaming whether or not anyone is listening, and nothing
        it was sent is forgotten when the host goes away (no watchdog).

    `deaf=True` is a board whose frames do not take (a corrupted or lost
    frame, a wedged sketch): it goes on reporting the old endpoint.

    `log` is the order of what happened on the wire, as the board saw it:
    ("frame", endpoint_sent), ("served", reported_setpoint) for each line the
    host actually read out of it, and ("close",). `log_path`, when given,
    mirrors it line by line to a file (a subprocess test reads it after the
    process is gone).
    """

    def __init__(self, endpoint=0.0, deaf=False, period=0.02, temp=25.0,
                 log_path=None):
        import time as _time
        self._time = _time
        self.is_open = True
        self.endpoint = float(endpoint)
        self.deaf = deaf
        self.period = period
        self.temp = temp
        self.wire = b""
        self.frames = []
        self.log = []
        self._log_path = log_path
        self._rx = b""
        self._pending = b""
        self._answered = False
        self._streaming = False
        self._last_line = 0.0
        self._started = _time.monotonic()
        self._lock = threading.Lock()

    def _note(self, *entry):
        self.log.append(entry)
        if self._log_path:
            with open(self._log_path, "a", encoding="utf-8") as f:
                f.write(" ".join(str(x) for x in entry) + "\n")

    @property
    def in_waiting(self):
        with self._lock:
            now = self._time.monotonic()
            if (self._streaming and now - self._last_line > self.period
                    and len(self._pending) < 4096):
                t = now - self._started
                self._pending += (f"{t:.2f} , {self.temp:.2f} , "
                                  f"{self.endpoint:.2f}\r\n").encode()
                self._last_line = now
            return len(self._pending)

    def write(self, data):
        data = bytes(data)
        with self._lock:
            self.wire += data
            if data == b"s\n":
                self._pending += b"DEV: t\r\n"
                self._answered = True
                return len(data)
            self._rx += data
            while b"<" in self._rx and b">" in self._rx.split(b"<", 1)[1]:
                body = self._rx.split(b"<", 1)[1]
                frame, self._rx = body.split(b">", 1)
                self.frames.append(b"<" + frame + b">")
                endpoint = float(frame.split(b",")[0] or 0)
                if not self.deaf:
                    self.endpoint = endpoint
                self._note("frame", endpoint)
        return len(data)

    def read(self, size=1):
        with self._lock:
            out, self._pending = self._pending[:size], self._pending[size:]
        for raw in out.split(b"\n"):
            fields = raw.decode(errors="ignore").split(",")
            if len(fields) == 3:
                self._note("served", float(fields[2]))
        return out

    def reset_input_buffer(self):
        with self._lock:
            self._pending = b""
            if self._answered:
                self._streaming = True

    def reset_output_buffer(self):
        pass

    def flush(self):
        pass

    def close(self):
        self.is_open = False
        self._note("close")


def off_confirmed_before_close(log):
    """True when, after the last frame the board received, a line reporting
    setpoint 0 was read out of it BEFORE its port closed: the host saw the
    board's own word that the heater is off, then let go."""
    if ("close",) not in log:
        return False
    closed_at = log.index(("close",))
    frames = [i for i, e in enumerate(log[:closed_at]) if e[0] == "frame"]
    if not frames:
        return False
    return any(e[0] == "served" and e[1] <= 0.5
               for e in log[frames[-1] + 1:closed_at])


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
