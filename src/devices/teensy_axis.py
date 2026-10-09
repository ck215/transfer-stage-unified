"""One axis of the XYZ Stage: a Teensy 3.5 and a TMC2209 on their own serial
link, speaking the axis firmware protocol, version 1 (the lead's SPEC,
2026-10-09).

Two things live here.

`TeensyAxis` is the per-axis link. It IS a `SerialPort`, so the station's one
write path, its priority lane, the in-lock abort check, the loss handling and
the reconnect apply unchanged; a model that owns three of them gets three
links in `state["devices"]` (each names itself through `state_key`) and the
worst of them in `state["link"]`. What it adds is the protocol's framing:

* every command is one ASCII line; `send` writes it, `request` writes it and
  waits for the one reply it earns (`OK <CMD> k=v ...` or `ERR <CMD>
  reason`), matched by command word;
* `P ...` lines (the position stream) are parsed into `p` / `p_time`;
* `EVT ...` lines are queued for the owner (`take_events`);
* anything else is counted in `dropped`.

Reading is `poll()`: whoever calls it (the owner's reader loop, or a
`request` waiting for its reply) dispatches what has arrived, in wire order,
under one poll lock. Nothing here decides anything about motion.

`AxisSimulator` is a protocol-level stand-in for one board, shaped like the
pyserial handle `SerialPort` drives, so a `"SIM"` port runs the real
transport. It answers the identity query and every command the station
sends, integrates motion (ramps at ACCEL, the per-move speed, the JOGV
dead-man, the host-timeout window, the limit interlock, a HOME sequence), and
can be told to trip a limit, move the home edge, go silent (a hung loop: no
reply, no stream, while the step ISR keeps moving), fail an ENABLE, report a
fault, reboot, or be unplugged. It is the firmware's contract as the host
reads it, not the firmware: the HOME sequence in particular is the firmware
agent's to design, and only its events are modelled here.
"""
import collections
import math
import threading
import time

from devices.serial_port import SerialPort, TransportError
from events import events

#: The three axes, in the order a model lists them.
AXES = ("X", "Y", "Z")

#: SerialPort statuses under which the board can be talked to.
USABLE = ("verified", "unverified", "simulated")


class Reply:
    """One answer to one command, or the lack of one."""

    __slots__ = ("command", "ok", "fields", "words", "reason", "line", "seq",
                 "timed_out", "aborted")

    def __init__(self, command, ok, *, fields=None, words=(), reason="",
                 line="", seq=0, timed_out=False, aborted=False):
        self.command = command
        self.ok = bool(ok)
        self.fields = dict(fields or {})
        self.words = tuple(words)
        self.reason = reason
        self.line = line
        self.seq = seq
        self.timed_out = timed_out
        self.aborted = aborted

    @property
    def why(self):
        """The reason in a few words, for an operator sentence."""
        if self.ok:
            return "ok"
        if self.aborted:
            return "not sent: the stage is stopped"
        if self.timed_out:
            return "no reply"
        return self.reason or "refused"

    def __repr__(self):
        return f"<Reply {self.line or self.command} ok={self.ok}>"


class _Waiter:
    __slots__ = ("command", "event", "reply")

    def __init__(self, command):
        self.command = command
        self.event = threading.Event()
        self.reply = None


def _parse_fields(tokens):
    """`k=v` tokens -> ({k: v}, [bare words])."""
    found, words = {}, []
    for token in tokens:
        key, eq, value = token.partition("=")
        if eq and key:
            found[key] = value
        else:
            words.append(token)
    return found, words


#: P-line fields and their types, in the SPEC's order.
P_FIELDS = (("pos", float), ("tgt", float), ("v", float), ("en", int),
            ("mv", int), ("ls1", int), ("ls2", int), ("home", int),
            ("homed", int))


def parse_p_line(line):
    """`P pos=.. tgt=.. v=.. en=.. mv=.. ls1=.. ls2=.. home=.. homed=..` ->
    dict, or None when any field is missing or malformed."""
    found, _words = _parse_fields(line.split()[1:])
    parsed = {}
    try:
        for name, kind in P_FIELDS:
            value = kind(found[name])
            if kind is float and not math.isfinite(value):
                return None
            parsed[name] = value
    except (KeyError, ValueError):
        return None
    return parsed


class TeensyAxis(SerialPort):
    """One axis board on its own serial link (protocol v1).

        TeensyAxis(port, axis, *, simulator=None, owner=None)

    `port` is a port name, or "SIM"/None for the built-in simulator. With a
    real name and a `simulator`, the simulator stands in for the board behind
    the full connect path (open, identity query, loss, reconnect): tests use
    that; Setup never does.
    """

    #: Teensy USB serial ignores the baud rate (the scan tries 500000 and
    #: 115200; both work). 115200 is the validator's own `Serial.begin`.
    BAUD_RATE = 115200
    #: Seconds a `request` waits for its reply by default. The slowest reply
    #: the station asks for is ENABLE (a driver read-back over UART, a few
    #: ms); this is generous so a busy USB stack is not a refusal.
    REPLY_TIMEOUT = 0.5
    #: How many lines one poll takes before it yields: a 50 Hz JOGV stream
    #: earns 50 replies a second, the 20 Hz P stream 20 lines.
    MAX_POLL_LINES = 64
    #: EVT lines kept for the owner between two of its reads.
    EVENT_BACKLOG = 256

    def __init__(self, port, axis, *, simulator=None, owner=None):
        axis = str(axis).strip().upper()
        if axis not in AXES:
            raise ValueError(f"axis must be one of {AXES}, not {axis!r}")
        super().__init__(port, baud_rate=self.BAUD_RATE, owner=owner)
        self.axis = axis
        #: The name in `Model.state["devices"]` (one entry per axis).
        self.state_key = f"Axis {axis}"
        self._simulator = simulator
        if self.is_simulated:
            if self._simulator is None:
                self._simulator = AxisSimulator(axis)
            # SerialPort built a SimulatedPort; the protocol simulator takes
            # its place, closed until `open()` opens it.
            self._handle = self._simulator
            self._handle.close()
        self._poll_lock = threading.Lock()
        self._dispatch_lock = threading.Lock()
        self._waiters = {}           # COMMAND -> [_Waiter]
        self._replies = {}           # COMMAND -> latest Reply
        self._events = collections.deque(maxlen=self.EVENT_BACKLOG)
        self._seq = 0                # every dispatched line, in wire order
        self._up = False
        #: How many times the link has come up (`track_link`); what the last
        #: board said is forgotten each time.
        self.epoch = 0
        #: The tag the board gave to `AXIS` this epoch ("X", "?"), or None.
        self.tag = None
        #: The latest P line as a dict, its arrival (monotonic) and sequence.
        self.p = None
        self.p_time = None
        self.p_seq = 0
        self.p_count = 0
        #: Lines that were none of P, EVT, OK, ERR or DEV.
        self.dropped = 0

    @property
    def simulator(self):
        """The simulator behind this link, or None for a real board."""
        return self._simulator

    def _open_handle(self):
        if self._simulator is not None and not self.is_simulated:
            self._simulator.open()       # raises while "unplugged"
            return self._simulator
        return super()._open_handle()

    # -- what the board said ---------------------------------------------------
    @property
    def identity_tag(self):
        """The axis letter from the identity answer (`DEV: t X` -> "X"), or
        None when there was no handshake (SIM) or it was not a `t` board."""
        words = str(self.identity or "").split()
        if len(words) >= 2 and words[0].lower() == "t":
            return words[1].upper()
        return None

    @property
    def is_usable(self):
        return self.status in USABLE

    def last_reply(self, command):
        """The latest reply to `command` this epoch, or None."""
        with self._dispatch_lock:
            return self._replies.get(str(command).upper())

    def take_events(self):
        """Every EVT line since the last call: [(monotonic, seq, text)],
        `text` without the `EVT ` prefix. For the owning model's loop."""
        with self._dispatch_lock:
            taken = list(self._events)
            self._events.clear()
        return taken

    @property
    def last_seq(self):
        with self._dispatch_lock:
            return self._seq

    def track_link(self):
        """True exactly once each time the link comes up (open, reconnect).
        Everything the last board said is forgotten then: a reconnected
        board may have rebooted, and a tag or a stream setting heard from it
        before is not evidence about it now."""
        up = self.is_usable
        with self._dispatch_lock:
            came_up = up and not self._up
            self._up = up
            if came_up:
                self.epoch += 1
                self.tag = None
                self.p = None
                self.p_time = None
                self._replies.clear()
                self._events.clear()
        return came_up

    # -- writing -----------------------------------------------------------------
    @staticmethod
    def _line(command):
        text = str(command).strip()
        if not text or "\n" in text or "\r" in text:
            raise ValueError(f"one command per line, not {command!r}")
        return (text + "\n").encode("ascii")

    def send(self, command, *, priority=False, abort_if=None):
        """Write one command line. Returns what `SerialPort.write` returns
        (False when `abort_if` stopped it); raises TransportError."""
        return self.write(self._line(command), priority=priority, abort_if=abort_if)

    def expect(self, command):
        """Register for the next reply to `command` BEFORE sending it, so a
        fast board cannot answer before anyone is listening."""
        waiter = _Waiter(str(command).split()[0].upper())
        with self._dispatch_lock:
            self._waiters.setdefault(waiter.command, []).append(waiter)
        return waiter

    def _forget(self, waiter):
        with self._dispatch_lock:
            pending = self._waiters.get(waiter.command, [])
            if waiter in pending:
                pending.remove(waiter)

    def request(self, command, timeout=None, *, priority=False, abort_if=None):
        """Send `command` and return its Reply. Never raises for a refusal,
        a silent board or an abort (the Reply says which); raises
        TransportError when the write itself failed."""
        waiter = self.expect(command)
        try:
            sent = self.send(command, priority=priority, abort_if=abort_if)
        except Exception:
            self._forget(waiter)
            raise
        if not sent:
            self._forget(waiter)
            return Reply(waiter.command, False, aborted=True)
        return self.wait(waiter, timeout)

    def wait(self, waiter, timeout=None):
        """Wait for `waiter`'s reply, reading the link meanwhile. A Reply with
        `timed_out` when none came within `timeout` (default REPLY_TIMEOUT)."""
        budget = self.REPLY_TIMEOUT if timeout is None else max(0.0, float(timeout))
        deadline = time.monotonic() + budget
        while True:
            if waiter.event.is_set():
                return waiter.reply
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._forget(waiter)
                if waiter.event.is_set():          # answered at the wire
                    return waiter.reply
                return Reply(waiter.command, False, timed_out=True)
            try:
                self.poll(blocking=False)
            except TransportError:
                pass             # the link's own state says it; keep waiting out the budget
            waiter.event.wait(min(0.005, remaining))

    # -- reading -----------------------------------------------------------------
    def poll(self, max_lines=None, *, blocking=True):
        """Read and dispatch every whole line that has arrived (at most
        `max_lines`). -> how many. One reader at a time, so lines are
        dispatched in the order they came off the wire; a non-blocking caller
        that finds another reader at work returns 0 at once. Reads nothing
        while the link is not usable (connecting, lost, closed); raises
        TransportError when a read fails."""
        if not self._poll_lock.acquire(blocking=blocking, timeout=0.25 if blocking else -1):
            return 0
        try:
            if not self.is_usable:
                return 0
            handled = 0
            for _ in range(max_lines or self.MAX_POLL_LINES):
                line = self.read_line(timeout=0)
                if line is None:
                    break
                line = line.strip()
                if not line:
                    continue
                handled += 1
                self._dispatch(line)
            return handled
        finally:
            self._poll_lock.release()

    def _dispatch(self, line):
        """One line, in wire order. Reported (and waiters woken) after the
        dispatch lock is released."""
        now = time.monotonic()
        head = line.split(" ", 1)[0]
        reply, wake, dropped = None, [], None
        with self._dispatch_lock:
            self._seq += 1
            seq = self._seq
            if head == "P":
                parsed = parse_p_line(line)
                if parsed is None:
                    self.dropped += 1
                    dropped = self.dropped
                else:
                    self.p, self.p_time, self.p_seq = parsed, now, seq
                    self.p_count += 1
            elif head == "EVT":
                self._events.append((now, seq, line[4:].strip()))
            elif head in ("OK", "ERR"):
                words = line.split()
                command = words[1].upper() if len(words) > 1 else ""
                if head == "OK":
                    found, bare = _parse_fields(words[2:])
                    reply = Reply(command, True, fields=found, words=bare,
                                  line=line, seq=seq)
                    if command == "AXIS" and "axis" in found:
                        self.tag = found["axis"].upper()
                else:
                    reply = Reply(command, False, reason=" ".join(words[2:]),
                                  line=line, seq=seq)
                self._replies[command] = reply
                wake = self._waiters.pop(command, [])
            elif not line.startswith("DEV:"):   # DEV: an identity answer, late
                self.dropped += 1
                dropped = self.dropped
        if dropped is not None:
            events.debug("Line Dropped", f"#{dropped}: {line[:80]!r}",
                         source=self._source)
        for waiter in wake:
            waiter.reply = reply
            waiter.event.set()


class AxisSimulator:
    """One axis board, simulated at the protocol level.

    Shaped like a pyserial handle (`write`, `read`, `in_waiting`, ...), so
    `SerialPort` drives it exactly as it drives a board. Time comes from
    `clock` (monotonic seconds); the motion is integrated lazily, in small
    steps, whenever the host touches the handle or a test reads a property.

    Positions: `position_mm` is the board's counter (what P reports). The
    switches and the home edge are fixed in the stage's own frame, in which
    the board booted at 0: ZERO and HOME move the counter's origin, never
    the switches.
    """

    FULL_STEPS_PER_REV = 200
    LEAD_MM = 1.0
    MAX_MOVE_MM = 50.0
    MAX_SPEED_MM_S = 2.5
    DEFAULT_SPEED_MM_S = 0.5
    DEFAULT_ACCEL_MM_S2 = 2.5
    MAX_CURRENT_MA = 1000
    JOG_TIMEOUT_MS = 250
    DEFAULT_HOST_TIMEOUT_MS = 2500
    HOST_TIMEOUT_RANGE_MS = (250, 5000)
    MAX_STREAM_HZ = 50
    #: The simulated HOME (time-compressed: a simulated session homes in a
    #: moment). The real sequence and its speeds are the firmware's.
    HOME_SEEK_MM_S = 5.0
    HOME_APPROACH_MM_S = 1.0
    HOME_BACKOFF_MM = 0.2
    HOME_TIME_LIMIT_S = 120.0
    #: Integration step, and the most a single catch-up integrates.
    STEP_S = 0.002
    MAX_CATCH_UP_S = 30.0
    #: Lines kept in `writes` / `received` (a long SIM session streams).
    HISTORY = 20000

    def __init__(self, tag="X", *, clock=time.monotonic,
                 limits_mm=(-25.0, 25.0), home_edge_mm=0.4):
        self.tag = None if tag in (None, "?") else str(tag).upper()
        self.clock = clock
        self.limits_mm = tuple(limits_mm)
        self.home_edge_mm = home_edge_mm
        self.is_open = False
        #: Every payload the host wrote, in order (bytes).
        self.writes = collections.deque(maxlen=self.HISTORY)
        #: Every command line the board processed: (clock time, line).
        self.received = collections.deque(maxlen=self.HISTORY)
        #: True: the firmware loop is hung. No command is read, nothing is
        #: printed, and the loop's guards (dead-man, host timeout) do not run;
        #: the step ISR keeps moving the axis.
        self.silent = False
        #: True: the USB cable is out. Every call raises OSError.
        self.unplugged = False
        #: The next N writes raise OSError (a failing USB stack).
        self.fail_writes = 0
        #: A reason ENABLE answers with ERR (e.g. "driver-uart-not-ok").
        self.refuse_enable = None
        self.home_seek_mm_s = self.HOME_SEEK_MM_S
        self.home_approach_mm_s = self.HOME_APPROACH_MM_S
        self._forced = [False, False]
        self._lock = threading.RLock()
        self._out = bytearray()
        self._rx = bytearray()
        self._boot()

    # -- firmware state ----------------------------------------------------------
    def _boot(self):
        now = self.clock()
        self._t = now
        self._t0 = now
        self.enabled = False
        self.microsteps = 8
        self._phys = getattr(self, "_phys", 0.0)   # the carriage does not move on a reboot
        self._origin = self._phys                   # ...but the counter restarts at 0
        self._v = 0.0
        self._target = None          # counter mm
        self._cruise = None
        self._jog = None             # mm/s, signed; 0.0 = decelerating to rest
        self._last_jog = now
        self.speed_mm_s = self.DEFAULT_SPEED_MM_S
        self.accel_mm_s2 = self.DEFAULT_ACCEL_MM_S2
        self.current_ma = 600
        self.host_timeout_ms = self.DEFAULT_HOST_TIMEOUT_MS
        self._timed_out = False
        self._last_rx = now
        self.stream_hz = 0
        self._next_p = None
        self.homed = False
        self._homing = None          # {"phase", "dir", "speed", "started", "reversed"}
        self._home_was_low = False   # a search saw the beam clear (so a rise is an edge)
        self._ls_was = [self._tripped(0), self._tripped(1)]
        self._home_was = self._home_level()

    @property
    def step_mm(self):
        return self.LEAD_MM / (self.FULL_STEPS_PER_REV * self.microsteps)

    def _quantize(self, mm):
        step = self.step_mm
        return round(mm / step) * step

    def _counter(self):
        return self._phys - self._origin

    def _tripped(self, index):
        low, high = self.limits_mm
        if self._forced[index]:
            return True
        return self._phys <= low if index == 0 else self._phys >= high

    @staticmethod
    def _end(index):
        return -1 if index == 0 else 1

    def _home_level(self):
        edge = self.home_edge_mm
        return 1 if edge is not None and self._phys >= edge else 0

    def _busy(self):
        return (self._target is not None or self._homing is not None
                or (self._jog is not None) or abs(self._v) > 0.0)

    # -- what a test reads (each catches the motion up first) ---------------------
    @property
    def position_mm(self):
        with self._lock:
            self._advance()
            return self._quantize(self._counter()) + 0.0

    @property
    def velocity_mm_s(self):
        with self._lock:
            self._advance()
            return self._v

    @property
    def moving(self):
        with self._lock:
            self._advance()
            return self._busy()

    # -- injections ----------------------------------------------------------------
    def trip_limit(self, number, tripped=True):
        """Close (or with tripped=False, release) limit switch LS1 or LS2."""
        with self._lock:
            self._advance()
            self._forced[int(number) - 1] = bool(tripped)

    def release_limit(self, number):
        self.trip_limit(number, False)

    def inject_fault(self, reason):
        """A driver fault: the board ESTOPs itself and says why."""
        with self._lock:
            self._advance()
            self._halt(disable=True, why="fault")
            self._emit(f"EVT FAULT {reason}")

    def reboot(self):
        """The board restarts (a watchdog, a brown-out): defaults, counter 0."""
        with self._lock:
            self._boot()
            self._emit(f"EVT BOOT version=0x21 expected=0x21 uart=OK enabled=0 "
                       f"axis={self.tag or '?'}")

    def unplug(self):
        with self._lock:
            self.unplugged = True
            self.is_open = False

    def replug(self):
        """Back on USB: a USB-powered board has rebooted."""
        with self._lock:
            self.unplugged = False
            self._out.clear()
            self._rx.clear()
            self._boot()

    def emit_raw(self, text):
        """Put one raw line on the board's output (a garbled line, say)."""
        with self._lock:
            self._emit(text)

    def ask(self, command):
        """Test helper: send one command line, return every line the board
        has printed since (its reply, and anything else due)."""
        self.write((command + "\n").encode("ascii"))
        return self.drain_lines()

    def drain_lines(self):
        """Test helper: catch up, then take every whole line printed."""
        with self._lock:
            self._advance()
            data = bytes(self._out)
            cut = data.rfind(b"\n") + 1
            del self._out[:cut]
        return [line.decode("utf-8", "replace") for line in data[:cut].split(b"\n")[:-1]]

    # -- the pyserial surface ----------------------------------------------------
    def _check(self):
        if self.unplugged:
            raise OSError(6, "Device not configured")

    def open(self):
        with self._lock:
            self._check()
            self._advance()
            self.is_open = True

    def close(self):
        """Closing the port drops DTR: the firmware's DTR guard ESTOPs."""
        with self._lock:
            if self.is_open and not self.unplugged:
                self._advance()
                self._halt(disable=True, why="estop")
            self.is_open = False

    @property
    def in_waiting(self):
        with self._lock:
            self._check()
            self._advance()
            return len(self._out)

    def read(self, size=1):
        with self._lock:
            self._check()
            self._advance()
            taken = bytes(self._out[:size])
            del self._out[:size]
            return taken

    def read_all(self):
        return self.read(len(self._out) or 0)

    def readline(self):
        with self._lock:
            self._check()
            self._advance()
            cut = self._out.find(b"\n") + 1
            taken = bytes(self._out[:cut])
            del self._out[:cut]
            return taken

    def write(self, payload):
        with self._lock:
            self._check()
            if self.fail_writes:
                self.fail_writes -= 1
                raise OSError(5, "Input/output error")
            payload = bytes(payload)
            self.writes.append(payload)
            self._advance()
            self._rx += payload
            if not self.silent:
                self._read_commands()
            return len(payload)

    def reset_input_buffer(self):
        with self._lock:
            self._out.clear()

    def reset_output_buffer(self):
        pass

    def flush(self):
        """Nothing is in flight (SERIAL-20)."""

    # -- the firmware loop ---------------------------------------------------------
    def _emit(self, text):
        if not self.silent:
            self._out += (text + "\n").encode("utf-8")

    def _ok(self, command, detail=""):
        self._emit(f"OK {command}" + (f" {detail}" if detail else ""))

    def _err(self, command, reason):
        self._emit(f"ERR {command} {reason}")

    def _read_commands(self):
        while True:
            cut = self._rx.find(b"\n")
            if cut < 0:
                return
            raw = bytes(self._rx[:cut])
            del self._rx[:cut + 1]
            now = self.clock()
            self._last_rx = now           # any line is a heartbeat
            self._timed_out = False
            line = raw.decode("ascii", "replace").strip()
            if not line:
                continue
            self.received.append((now, line))
            self._handle(line)

    def _handle(self, line):
        words = line.split()
        command = words[0].upper()
        args = [w.upper() for w in words[1:]]
        if command == "S":
            self._emit(f"DEV: t {self.tag or '?'}")
            return
        handler = getattr(self, "_cmd_" + command.lower(), None)
        if handler is None or not command.isalpha():
            self._err(command, "unknown-command")
            return
        handler(command, args)

    @staticmethod
    def _float(args, index):
        try:
            value = float(args[index])
        except (IndexError, ValueError):
            return None
        return value if math.isfinite(value) else None

    @staticmethod
    def _int(args, index):
        try:
            return int(args[index])
        except (IndexError, ValueError):
            return None

    def _limit_block(self, direction):
        for index in (0, 1):
            if self._tripped(index) and self._end(index) == direction:
                return f"limit-ls{index + 1}"
        return None

    def _halt(self, *, disable, why):
        """Stop at once (the ISR halt); ESTOP and DISABLE also power down."""
        self._abort_home(why)
        self._v = 0.0
        self._target = self._cruise = self._jog = None
        if disable:
            self.enabled = False

    def _soft_stop(self, why):
        self._abort_home(why)
        self._target = self._cruise = None
        self._jog = 0.0 if abs(self._v) > 0 else None

    def _abort_home(self, why):
        if self._homing is not None:
            self._homing = None
            self._emit(f"EVT HOME FAIL reason={why}")

    # -- commands -----------------------------------------------------------------
    def _cmd_ping(self, command, args):
        self._ok(command, f"uptime_ms={int((self.clock() - self._t0) * 1000)}")

    def _cmd_info(self, command, args):
        self._ok(command, f"axis={self.tag or '?'} protocol=1 microsteps={self.microsteps} "
                          f"step_um={self.step_mm * 1000:.4f} lead_mm={self.LEAD_MM:.3f} "
                          f"max_move_mm={self.MAX_MOVE_MM:.3f} "
                          f"speed_mm_s={self.speed_mm_s:.4f} "
                          f"max_speed_mm_s={self.MAX_SPEED_MM_S:.4f} "
                          f"accel_mm_s2={self.accel_mm_s2:.4f} "
                          f"jog_timeout_ms={self.JOG_TIMEOUT_MS} "
                          f"host_timeout_ms={self.host_timeout_ms} "
                          f"stream_hz={self.stream_hz} limits=NO")

    def _cmd_status(self, command, args):
        target = self._target if self._target is not None else self._counter()
        self._ok(command, f"pos_mm={self._quantize(self._counter()):.5f} "
                          f"target_mm={self._quantize(target):.5f} "
                          f"speed_mm_s={self._v:.4f} enabled={int(self.enabled)} "
                          f"microsteps={self.microsteps} ls1={int(self._tripped(0))} "
                          f"ls2={int(self._tripped(1))} home={self._home_level()} "
                          f"homed={int(self.homed)} axis={self.tag or '?'} test=none")

    def _cmd_enable(self, command, args):
        if self._busy():
            self._err(command, "busy")
        elif self.refuse_enable:
            self._err(command, self.refuse_enable)
        else:
            self.enabled = True
            self._ok(command, f"enabled=1 microsteps={self.microsteps}")

    def _cmd_disable(self, command, args):
        self._halt(disable=True, why="disabled")
        self._ok(command, "enabled=0")

    def _cmd_stop(self, command, args):
        self._soft_stop("stop")
        self._ok(command, f"pos_mm={self._quantize(self._counter()):.5f}")

    def _cmd_estop(self, command, args):
        self._halt(disable=True, why="estop")
        self._ok(command, f"enabled=0 pos_mm={self._quantize(self._counter()):.5f}")

    def _cmd_speed(self, command, args):
        value = self._float(args, 0)
        if value is None or value <= 0:
            return self._err(command, "bad-arg")
        clamped = value > self.MAX_SPEED_MM_S
        self.speed_mm_s = min(value, self.MAX_SPEED_MM_S)
        self._ok(command, f"speed_mm_s={self.speed_mm_s:.4f} clamped={int(clamped)} applies=now")

    def _cmd_accel(self, command, args):
        value = self._float(args, 0)
        if value is None or value <= 0:
            return self._err(command, "bad-arg")
        self.accel_mm_s2 = min(max(value, 0.25), 25.0)
        self._ok(command, f"accel_mm_s2={self.accel_mm_s2:.4f} "
                          f"clamped={int(self.accel_mm_s2 != value)} applies=now")

    def _cmd_current(self, command, args):
        value = self._int(args, 0)
        if value is None or value < 0:
            return self._err(command, "bad-arg")
        clamped = value > self.MAX_CURRENT_MA
        self.current_ma = min(value, self.MAX_CURRENT_MA)
        self._ok(command, f"ma={self.current_ma} clamped={int(clamped)}")

    def _cmd_microsteps(self, command, args):
        value = self._int(args, 0)
        if value is None or not 1 <= value <= 256 or value & (value - 1):
            return self._err(command, "bad-arg-power-of-2-1..256")
        if self._busy():
            return self._err(command, "busy")
        self.microsteps = value          # the counter in mm is kept
        self._ok(command, f"microsteps={value} step_um={self.step_mm * 1000:.4f} "
                          f"pos_mm={self._quantize(self._counter()):.5f}")

    def _cmd_mode(self, command, args):
        if not args or args[0] not in ("STEALTH", "SPREAD"):
            return self._err(command, "bad-arg-STEALTH|SPREAD")
        self._ok(command, f"mode={args[0].lower()}")

    def _cmd_limits(self, command, args):
        if not args or args[0] not in ("NC", "NO"):
            return self._err(command, "bad-arg-NC|NO")
        if self._busy():
            return self._err(command, "busy")
        self._ok(command, f"limits={args[0]} ls1={int(self._tripped(0))} "
                          f"ls2={int(self._tripped(1))} ends=forgotten")

    def _cmd_move(self, command, args, absolute=False):
        if not self.enabled:
            return self._err(command, "not-enabled")
        if self._homing is not None:
            return self._err(command, "busy")
        value = self._float(args, 0)
        speed = self._float(args, 1) if len(args) > 1 else None
        if value is None or (len(args) > 1 and (speed is None or speed <= 0)):
            return self._err(command, "bad-arg")
        here = self._counter()
        distance = value - here if absolute else value
        clamped = abs(distance) > self.MAX_MOVE_MM
        distance = max(-self.MAX_MOVE_MM, min(self.MAX_MOVE_MM, distance))
        if self._busy():
            return self._err(command, "busy")
        target = self._quantize(here + distance)
        distance = target - self._quantize(here)
        if distance:
            why = self._limit_block(1 if distance > 0 else -1)
            if why:
                return self._err(command, why)
            self._target = target
            self._cruise = min(speed or self.speed_mm_s, self.MAX_SPEED_MM_S)
        self._ok(command, f"mm={distance:.5f} target_mm={target:.5f} clamped={int(clamped)}")

    def _cmd_moveto(self, command, args):
        self._cmd_move(command, args, absolute=True)

    def _jog_to(self, command, velocity, echo):
        if not self.enabled:
            return self._err(command, "not-enabled")
        if self._homing is not None or self._target is not None:
            if velocity == 0:
                return self._ok(command, echo(0.0, False))
            return self._err(command, "busy")
        if velocity == 0:
            self._jog = 0.0 if abs(self._v) > 0 else None
            return self._ok(command, echo(0.0, False))
        clamped = abs(velocity) > self.MAX_SPEED_MM_S
        velocity = math.copysign(min(abs(velocity), self.MAX_SPEED_MM_S), velocity)
        why = self._limit_block(1 if velocity > 0 else -1)
        if why:
            self._jog = 0.0 if abs(self._v) > 0 else None
            return self._err(command, why)
        self._jog = velocity
        self._last_jog = self.clock()
        self._ok(command, echo(velocity, clamped))

    def _cmd_jogv(self, command, args):
        value = self._float(args, 0)
        if value is None:
            return self._err(command, "bad-arg")
        self._jog_to(command, value,
                     lambda v, c: f"mm_s={v:.4f} clamped={int(c)}")

    def _cmd_jog(self, command, args):
        value = self._int(args, 0)
        if value not in (-1, 0, 1):
            return self._err(command, "bad-arg")
        self._jog_to(command, value * self.speed_mm_s,
                     lambda v, c: f"dir={value}")

    def _cmd_zero(self, command, args):
        if self._busy():
            return self._err(command, "busy")
        self._origin = self._phys
        self.homed = False
        self._ok(command, "pos_mm=0")

    def _cmd_home(self, command, args):
        if not self.enabled:
            return self._err(command, "not-enabled")
        if self._busy():
            return self._err(command, "busy")
        self._homing = {"phase": "seek", "dir": -1, "speed": self.home_seek_mm_s,
                        "started": self.clock(), "reversed": False}
        self._ok(command, "started")
        self._emit("EVT HOME phase=seek dir=-1")

    def _cmd_hosttimeout(self, command, args):
        value = self._int(args, 0)
        low, high = self.HOST_TIMEOUT_RANGE_MS
        if value is None or not low <= value <= high:
            return self._err(command, "bad-arg")
        self.host_timeout_ms = value
        self._ok(command, f"ms={value}")

    def _cmd_hb(self, command, args):
        self._ok(command)

    def _cmd_stream(self, command, args):
        value = self._int(args, 0)
        if value is None or not 0 <= value <= self.MAX_STREAM_HZ:
            return self._err(command, "bad-arg")
        self.stream_hz = value
        self._next_p = self.clock() if value else None
        self._ok(command, f"hz={value}")

    def _cmd_axis(self, command, args):
        if not args:
            return self._ok(command, f"axis={self.tag or '?'}")
        if args[0] not in AXES:
            return self._err(command, "bad-arg")
        if self.enabled or self._busy():
            return self._err(command, "busy")
        self.tag = args[0]
        self._ok(command, f"axis={self.tag} stored=1")

    def _cmd_test(self, command, args):
        self._err(command, "not-simulated")

    # -- time ---------------------------------------------------------------------------
    def _advance(self):
        now = self.clock()
        if now <= self._t:
            return
        span = min(now - self._t, self.MAX_CATCH_UP_S)
        t = self._t
        while span > 1e-12:
            dt = min(self.STEP_S, span)
            span -= dt
            t += dt
            self._tick(t, dt)
        self._t = now

    def _tick(self, t, dt):
        if not self.silent:
            self._loop_guards(t)
        self._isr(dt)
        if not self.silent:
            self._home_step(t)
            if self.stream_hz and self._next_p is not None and t >= self._next_p:
                period = 1.0 / self.stream_hz
                self._next_p += period
                if self._next_p < t:
                    self._next_p = t + period
                self._emit_p()

    def _loop_guards(self, t):
        if (self._jog is not None and self._jog != 0.0
                and (t - self._last_jog) * 1000 > self.JOG_TIMEOUT_MS):
            self._jog = 0.0                          # the dead-man
        if (self._busy() and not self._timed_out
                and (t - self._last_rx) * 1000 > self.host_timeout_ms):
            self._timed_out = True
            self._soft_stop("host-timeout")
            self._emit("EVT FAULT host-timeout")

    def _desired_velocity(self):
        if not self.enabled:
            return 0.0
        if self._homing is not None:
            return self._homing["dir"] * self._homing["speed"]
        if self._target is not None:
            remaining = self._target - self._counter()
            stopping = math.sqrt(2.0 * self.accel_mm_s2 * abs(remaining))
            speed = min(self._cruise, max(stopping, min(self._cruise, 0.005)))
            return math.copysign(speed, remaining)
        if self._jog is not None:
            return self._jog
        return 0.0

    def _isr(self, dt):
        if not self.enabled:
            self._v = 0.0
            self._target = self._cruise = self._jog = None
        wanted = self._desired_velocity()
        step = self.accel_mm_s2 * dt
        if self._homing is not None:
            self._v = wanted                         # homing speeds are slow
        elif self._v < wanted:
            self._v = min(wanted, self._v + step)
        else:
            self._v = max(wanted, self._v - step)
        before = self._counter()
        self._phys += self._v * dt
        if self._target is not None:
            after = self._counter()
            if (self._target - before) * (self._target - after) <= 0:
                self._phys = self._origin + self._target
                self._v = 0.0
                self._target = self._cruise = None
        if self._jog == 0.0 and self._v == 0.0:
            self._jog = None
        for index in (0, 1):
            tripped = self._tripped(index)
            if tripped and not self._ls_was[index]:
                self._emit(f"EVT LIMIT ls{index + 1} tripped "
                           f"pos_mm={self._quantize(self._counter()):.4f} "
                           f"end={self._end(index):+d}")
            self._ls_was[index] = tripped
            if tripped and self._v * self._end(index) > 0:
                # The ISR interlock: no step toward a tripped end.
                self._v = 0.0
                self._target = self._cruise = None
                if self._jog is not None:
                    self._jog = None
        level = self._home_level()
        if level != self._home_was:
            self._home_was = level
            self._emit(f"EVT HOME edge level={level} "
                       f"pos_mm={self._quantize(self._counter()):.4f}")

    def _home_step(self, t):
        homing = self._homing
        if homing is None:
            return
        if t - homing["started"] > self.HOME_TIME_LIMIT_S:
            return self._home_failed("timeout")
        edge = self.home_edge_mm
        backoff = self.HOME_BACKOFF_MM
        phase = homing["phase"]
        low_hit, high_hit = self._tripped(0), self._tripped(1)
        if phase == "seek":
            if edge is not None and self._phys <= edge - backoff and self._home_level() == 0:
                self._home_phase("approach", 1, self.home_approach_mm_s)
            elif low_hit:
                if homing["reversed"]:
                    return self._home_failed("no-edge")
                homing["reversed"] = True
                self._home_phase("search", 1, self.home_seek_mm_s)
        elif phase == "search":
            if self._home_level() == 1 and self._home_was_low:
                self._home_phase("seek", -1, self.home_seek_mm_s)
            elif high_hit:
                return self._home_failed("no-edge")
            self._home_was_low = self._home_was_low or self._home_level() == 0
        elif phase == "approach":
            if edge is not None and self._phys >= edge:
                before = self._counter() - (self._phys - edge)
                self._phys = edge                    # latched at the edge
                self._origin = edge
                self._v = 0.0
                self._homing = None
                self.homed = True
                self._emit(f"EVT HOMED edge_mm={self._quantize(before):.5f} pos=0")
            elif high_hit:
                return self._home_failed("no-edge")

    def _home_phase(self, phase, direction, speed):
        self._homing.update(phase=phase, dir=direction, speed=speed)
        if phase == "search":
            self._home_was_low = self._home_level() == 0
        self._emit(f"EVT HOME phase={phase} dir={direction:+d}")

    def _home_failed(self, reason):
        self._homing = None
        self._v = 0.0
        self._emit(f"EVT HOME FAIL reason={reason}")

    def _emit_p(self):
        counter = self._quantize(self._counter())
        target = self._quantize(self._target) if self._target is not None else counter
        moving = int(self._busy())
        self._emit(f"P pos={counter:.5f} tgt={target:.5f} v={self._v:.4f} "
                   f"en={int(self.enabled)} mv={moving} ls1={int(self._tripped(0))} "
                   f"ls2={int(self._tripped(1))} home={self._home_level()} "
                   f"homed={int(self.homed)}")
