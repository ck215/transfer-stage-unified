"""The station's ONE transport. Every byte that reaches hardware leaves here.

Was `legacy/src/controller/serial.py` (class `serial`, which shadowed pyserial).
This is the only module in `src/` allowed to import pyserial.

What it knows: how to open a port without blocking the caller, how to ask an
Arduino what it is, how to put a payload on the wire without interleaving it
with another, how to let a stop jump the queue, and how to tell the truth
when any of that fails. What it does not know: anything about probes,
heaters or rotators. Frames are built by the model that owns the port.
"""
import enum
import threading
import time

try:
    import serial as pyserial
except ImportError:  # the station still runs in SIM without pyserial
    pyserial = None
try:
    # Separately, on purpose: a pyserial without `tools` must cost us the
    # port *listing*, not every real port in the station.
    from serial.tools import list_ports as _list_ports
except ImportError:
    _list_ports = None

from devices.device import Device
from events import events


class ConnectionState(str, enum.Enum):
    """What the transport actually knows about the link.

    The distinction that matters most is VERIFIED vs UNVERIFIED. Opening a
    port proves nothing: a cable into a powered-off board opens exactly like
    a working one (SERIAL-7). `status` on the port is `state.value`.
    """

    SIMULATED = "simulated"    # no hardware by design, and that is fine
    CONNECTING = "connecting"  # open in progress
    VERIFIED = "verified"      # opened AND the device answered
    UNVERIFIED = "unverified"  # opened, but nothing answered: operating blind
    LOST = "lost"              # the open failed, or the link failed after it
    CLOSED = "closed"          # not opened yet, or deliberately closed

    @property
    def is_usable(self):
        """True in the states where a command has any prospect of arriving."""
        return self in (ConnectionState.SIMULATED, ConnectionState.VERIFIED,
                        ConnectionState.UNVERIFIED)


class TransportError(Exception):
    """A payload did not reach the hardware, or a read failed.

    Raised only from this module. A model must treat it as *unknown hardware
    state*, never as success: the point of it existing is that the code this
    replaces swallowed write exceptions, so "the coils are disabled" and "the
    disable never left the process" looked identical to the caller.
    """


class SimulatedPort:
    """Stand-in pyserial object for simulator mode. It ACKs everything.

    SIM is a different *device*, not a different code path: writes succeed,
    reads return nothing, the port reports itself open, and `SerialPort`
    takes exactly one route whether or not a board is plugged in. Every
    method `SerialPort` calls on a pyserial handle must exist here; a
    missing one turns SIM back into a separate path (SERIAL-9 was a missing
    early-exit, SERIAL-20 was a missing `flush`).

    `writes` is every payload, in order. Tests read it.
    """

    def __init__(self):
        self.is_open = True
        self.in_waiting = 0
        self.writes = []

    def open(self):
        self.is_open = True

    def write(self, payload):
        self.writes.append(bytes(payload))
        return len(payload)

    def read(self, _size=1):
        return b""

    def read_all(self):
        return b""

    def readline(self):
        return b""

    def reset_input_buffer(self):
        pass

    def reset_output_buffer(self):
        pass

    def flush(self):
        """Nothing is in flight, so a drain is instantly complete (SERIAL-20)."""

    def close(self):
        self.is_open = False


def list_ports():
    """[(device, hwid), ...] for every serial port the OS reports, unfiltered
    and unsorted; [] when pyserial is missing. Here because only this module
    may import pyserial. Which ports are worth offering is Setup's policy."""
    if _list_ports is None:
        return []
    return [(entry.device, entry.hwid or "") for entry in _list_ports.comports()]


def query(port, baud_rate, payload, *, wait=0.1, xonxoff=False, timeout=0.2):
    """One-shot ask-and-listen on a raw port. -> str (the reply, possibly "")

    For identifying a device that must NOT receive the Arduino handshake: the
    SMC100 at 57600 with xonxoff, asked `1ID?` + CR LF. Replaces the 57600-baud
    branch of the old `probe_device_at`, byte for byte: open (bounded read
    and write timeouts), reset both buffers, write `payload`, sleep `wait`,
    return everything that arrived, decoded as UTF-8 with errors ignored.
    Always closes. Raises TransportError on any I/O failure.

    Not a transport: nothing that commands hardware may use this. It has no
    priority lane and no abort check, and it opens the port itself, so it
    must never be pointed at a port a `SerialPort` already holds.
    """
    if pyserial is None:
        raise TransportError("pyserial is not installed")
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    handle = None
    try:
        handle = pyserial.Serial(port, baudrate=baud_rate, timeout=timeout,
                                 write_timeout=0.2, xonxoff=xonxoff)
        handle.reset_input_buffer()
        handle.reset_output_buffer()
        handle.write(payload)
        time.sleep(wait)
        return handle.read_all().decode("utf-8", errors="ignore")
    except Exception as exc:
        raise TransportError(f"query of {port} at {baud_rate} failed: {exc}") from exc
    finally:
        if handle is not None:
            try:
                handle.close()
            except Exception:
                pass


class SerialPort(Device):
    """One serial link with ONE write path, a priority lane and an in-lock
    abort check. Knows nothing about probes.

        SerialPort(port, baud_rate=115200, *, xonxoff=False, read_timeout=1.0,
                   write_timeout=1.0, line_terminator="\\n", handshake=True)

    `port` of None, "None" or "SIM" gives a `SimulatedPort`. Constructing
    does no I/O; `open()` does, on a worker thread.

    Keyword overrides (the defaults are the Arduino boards' settings):

    * `baud_rate`       line speed. The probes' firmware runs at 500000 and
                        the heater at 115200; the SMC100 at 57600.
    * `xonxoff`         software flow control. The SMC100 needs True.
    * `read_timeout`    seconds `read_line()` waits when called without a
                        timeout. The SMC100 driver used 0.05.
    * `write_timeout`   bound on one `handle.write()`. Must be a positive
                        number: pyserial's own default is None, "block
                        forever", and with flow control on the device can
                        withhold XON exactly when someone is pressing FULL
                        STOP (ROTATOR-16). The SMC100 driver used 0.2.
    * `line_terminator` what ends a line for `read_line()`: "\\n" (a trailing
                        "\\r" is stripped too) or "\\r\\n" for the SMC100.
                        It is never appended to a write: `write()` sends
                        exactly the bytes it is given.
    * `handshake`       True asks the board what it is (`s\\n` -> `DEV: x`)
                        after the bootloader wait, and ends VERIFIED or
                        UNVERIFIED. False skips both the wait and the pings,
                        for a device that does not speak that protocol; the
                        link then opens UNVERIFIED and the protocol driver
                        calls `mark_verified()` once the device has answered
                        in its own language.

    Locks, and the order they are taken in (always this order, never
    reversed, so there is no cycle):

    1. `_lock`, the **transaction lock** (re-entrant). An ordinary write
       holds it; so does each handshake ping and each slice of a read.
    2. `_write_io_lock`, the **write-in-flight lock**. Held only around the
       `handle.write()` call itself, always innermost.
    3. `_state_lock` guards `_state`/`_handle` swaps. Never held across I/O
       or a callback, so taking it can never delay a stop.

    The priority lane (`write(..., priority=True)`, for stops only) waits at
    most `PRIORITY_LOCK_TIMEOUT` for the transaction lock and then goes
    without it: a stop that cannot get the lock is worse than an
    unsynchronised one. It still waits for the write-in-flight lock, bounded
    by `WRITE_IO_LOCK_TIMEOUT`, because both binary firmwares `readBytes` a
    fixed-size packet after 0xAA and would swallow a 'd' landing inside one
    as payload - a *lost* stop (SERIAL-23) - and a split heater frame reaches
    `atof(NULL)` in its parser (TEMP-17).

    MUST SATISFY (all carried): SERIAL-8 (loss is a state, reported once),
    SERIAL-11/DC-18 (one locked write path), SERIAL-12 (no unbounded drain,
    nothing printed), SERIAL-16 (close never raises), SERIAL-20 (SIM has
    flush), SERIAL-23/TEMP-17 (no interleave; abort_if inside the lock),
    ROTATOR-16 (bounded write timeout), SERIAL-6 (open does not block),
    SERIAL-7/SERIAL-17 (honest, non-flooding handshake).
    """

    #: How long a priority write waits for the transaction lock before
    #: forcing itself through. Short enough that FULL STOP is not held up by
    #: a transaction in flight, long enough that the ordinary case still
    #: serialises.
    PRIORITY_LOCK_TIMEOUT = 0.05
    #: How long a priority write waits for a write already on the wire. A
    #: frame is a few ms, so this only runs out on a writer wedged inside
    #: `handle.write` - and then the stop goes anyway.
    WRITE_IO_LOCK_TIMEOUT = 0.25
    #: Default bound on one `handle.write()`. Never None.
    WRITE_TIMEOUT = 1.0
    #: Rate limit on the debug line for an ORDINARY write. A model may stream
    #: jog frames through `write()` at 50 Hz, and the log file is not the
    #: place to keep 50 lines a second; the suppressed count reports the rate
    #: instead (Addendum 1). Priority writes and failures are never limited.
    WRITE_DEBUG_INTERVAL = 1.0
    #: Default for `read_line(timeout=None)`.
    READ_TIMEOUT = 1.0
    #: Default budget for `flush()`, and for the drain `close()` does first.
    FLUSH_TIMEOUT = 1.0
    #: How long `close()` waits for the transaction lock before closing
    #: regardless. A teardown that can hang is its own bug.
    CLOSE_LOCK_TIMEOUT = 1.0

    #: How long to give the Arduino bootloader before the first ping.
    BOOTLOADER_WAIT = 1.5
    #: Seconds between identity pings. Every `s` is answered, so pinging on
    #: every poll left a verified link with a queue of stale `DEV:` replies.
    PING_INTERVAL = 0.25
    #: How long to keep asking before declaring the link UNVERIFIED.
    HANDSHAKE_TIMEOUT = 3.0
    #: The identity query. Part of the wire contract with the firmware.
    PING = b"s\n"

    _HANDSHAKE_POLL = 0.05
    _READ_POLL = 0.005
    _READ_BUFFER_LIMIT = 4096
    _SIMULATED_NAMES = (None, "None", "SIM")

    def __init__(self, port, baud_rate=115200, *, xonxoff=False,
                 read_timeout=None, write_timeout=None, line_terminator="\n",
                 handshake=True):
        """was serial.__init__ - minus all the I/O, which is now `open()`."""
        write_timeout = self.WRITE_TIMEOUT if write_timeout is None else write_timeout
        if not isinstance(write_timeout, (int, float)) or write_timeout <= 0:
            raise ValueError("write_timeout must be a positive number of "
                             "seconds; an unbounded write can hang a stop")
        read_timeout = self.READ_TIMEOUT if read_timeout is None else read_timeout
        if not isinstance(read_timeout, (int, float)) or read_timeout < 0:
            raise ValueError("read_timeout must be zero or more seconds")
        if isinstance(line_terminator, str):
            line_terminator = line_terminator.encode("ascii")
        if not line_terminator:
            raise ValueError("line_terminator must not be empty")

        self.port = port
        self.baud_rate = baud_rate
        self.xonxoff = bool(xonxoff)
        self.read_timeout = float(read_timeout)
        self.write_timeout = float(write_timeout)
        self.line_terminator = line_terminator
        self.has_handshake = bool(handshake)
        self.is_simulated = port in self._SIMULATED_NAMES

        self._lock = threading.RLock()
        self._write_io_lock = threading.Lock()
        self._state_lock = threading.Lock()

        self._state = ConnectionState.CLOSED
        self._identity = None
        self._read_buffer = b""
        self._connect_thread = None
        self._generation = 0  # bumped by open() and close(); a stale worker quits

        # The simulated handle exists from construction and survives close(),
        # so `writes` stays readable for a test asserting on a teardown.
        self._handle = SimulatedPort() if self.is_simulated else None
        if self.is_simulated:
            self._handle.close()

    # -- reporting ---------------------------------------------------------
    @property
    def state(self):
        return self._state

    @property
    def status(self):
        return self._state.value

    @property
    def identity(self):
        """What the device said it is (`DEV: s` -> "s"), or None."""
        return self._identity

    @property
    def is_open(self):
        """True when a payload has somewhere to go: a real port or the
        simulator. True while CONNECTING once the handle exists - a stop must
        not have to wait for the handshake."""
        return self._verify()

    @property
    def writes(self):
        """The simulated port's payloads, in order. Empty on real hardware."""
        return self._handle.writes if self.is_simulated else []

    @property
    def _source(self):
        return f"SerialPort {self.port if not self.is_simulated else 'SIM'}"

    def _verify(self):
        """was serial._verify_serial. Is there an open handle?"""
        handle = self._handle
        return handle is not None and bool(getattr(handle, "is_open", False))

    def _note_state(self, old, new, why):
        """Diagnostics for one connection-state transition (Addendum 1).

        Always called *after* the lock that made the transition has been
        released: `_state_lock` is documented as never held across I/O, and
        a log line is I/O. A transition that changes nothing is not logged.
        """
        if old is not new:
            events.debug("State Change", f"{old.value} -> {new.value} ({why})",
                         source=self._source)

    # -- open / close ------------------------------------------------------
    def open(self):
        """Start connecting and return at once (SERIAL-6).

        The port open, the bootloader wait and the identity handshake all
        run on a worker, so the GUI thread or an HTTP handler building a
        model is never frozen for the 1.5-4.5 s they take. Progress is
        `state`: CONNECTING, then VERIFIED / UNVERIFIED / LOST. Idempotent
        while open or connecting; reopens after `close()` or a loss.
        """
        with self._state_lock:
            if self._state == ConnectionState.CONNECTING or self._verify():
                return
            was = self._state
            self._generation += 1
            generation = self._generation
            self._identity = None
            self._read_buffer = b""
            if self.is_simulated:
                self._handle.open()
                self._state = ConnectionState.SIMULATED
                self._connect_thread = None
            else:
                self._handle = None
                self._state = ConnectionState.CONNECTING
                self._connect_thread = threading.Thread(
                    target=self._connect_loop, args=(generation,), daemon=True,
                    name=f"serial-connect-{self.port}")
                thread = self._connect_thread
        events.debug("Opening", f"port={self.port!r} baud={self.baud_rate} "
                     f"xonxoff={self.xonxoff} read_timeout={self.read_timeout} "
                     f"write_timeout={self.write_timeout} "
                     f"handshake={self.has_handshake} generation={generation}",
                     source=self._source)
        self._note_state(was, self._state, "open() requested")
        if self.is_simulated:
            events.info("Simulator Mode", "commands are acknowledged locally",
                        source=self._source)
        else:
            thread.start()

    def wait_open(self, timeout=None):
        """Block until the connect attempt has finished. -> bool

        True when it finished and the port is open (verified or not); False
        on timeout or when the open failed. For Setup's autodetect, scripts
        and tests. A view or a model command must never call it: that would
        put the stall `open()` exists to remove straight back.
        """
        thread = self._connect_thread
        if thread is not None:
            thread.join(timeout)
            if thread.is_alive():
                return False
        return self.is_open

    def mark_verified(self, identity=None):
        """For a protocol driver on a `handshake=False` port: the device has
        answered in its own language. UNVERIFIED -> VERIFIED, nothing else."""
        with self._state_lock:
            if self._state != ConnectionState.UNVERIFIED:
                return False
            self._state = ConnectionState.VERIFIED
            if identity is not None:
                self._identity = str(identity)
        self._note_state(ConnectionState.UNVERIFIED, ConnectionState.VERIFIED,
                         f"protocol driver vouched for it as {self._identity!r}")
        return True

    def close(self):
        """Drain, then release the handle. Never raises (SERIAL-16).

        The drain comes first because `close()` on POSIX may discard bytes
        the OS has accepted but not transmitted, and the last thing a model
        writes before closing is its off-frame. Bounded, like everything on
        a teardown path.
        """
        with self._state_lock:
            self._generation += 1  # a connect still in flight is now stale
            was = self._state
            was_usable = was.is_usable or was == ConnectionState.CONNECTING
        drained = None
        if was_usable and self._verify():
            drained = self.flush()

        close_error = None
        acquired = self._lock.acquire(timeout=self.CLOSE_LOCK_TIMEOUT)
        try:
            with self._state_lock:
                handle = self._handle
                if not self.is_simulated:
                    self._handle = None
                self._state = ConnectionState.CLOSED
                self._read_buffer = b""
            if handle is not None and getattr(handle, "is_open", False):
                try:
                    handle.close()
                except Exception as exc:
                    close_error = exc
        finally:
            if acquired:
                self._lock.release()
        # Reported outside every lock: a subscriber is arbitrary code.
        events.debug("Closing", f"drained={drained} "
                     f"had_transaction_lock={acquired} error={close_error}",
                     source=self._source, exception=close_error)
        self._note_state(was, ConnectionState.CLOSED, "close() requested")
        if close_error is not None:
            events.warn("Port Close Failed", str(close_error),
                        source=self._source, exception=close_error)

    def _open_handle(self):
        if pyserial is None:
            raise TransportError("pyserial is not installed")
        return pyserial.Serial(
            port=self.port, baudrate=self.baud_rate, bytesize=8, parity="N",
            stopbits=1, xonxoff=self.xonxoff, timeout=self.read_timeout,
            write_timeout=self.write_timeout)

    def _is_current(self, generation):
        return (self._generation == generation
                and self._state == ConnectionState.CONNECTING)

    def _connect_loop(self, generation):
        """was serial._connect_worker. Open, wait out the bootloader, ask the
        board what it is - all off the caller's thread.

        Never holds the transaction lock across a wait: only individual
        pings and reads take it, so a priority write forces through within
        `PRIORITY_LOCK_TIMEOUT` however far the handshake has got. Whoever
        moved `state` first wins: a stale result never overwrites CLOSED or
        LOST.
        """
        started = time.monotonic()
        try:
            handle = self._open_handle()
        except Exception as exc:
            with self._state_lock:
                is_current = self._is_current(generation)
                if is_current:
                    self._state = ConnectionState.LOST
            if is_current:
                self._note_state(ConnectionState.CONNECTING, ConnectionState.LOST,
                                 f"the port would not open: {exc}")
                events.warn("Port Open Failed", f"{self.port}: {exc}",
                            source=self._source, exception=exc)
            return
        events.debug("Handle Open", f"{self.port} opened in "
                     f"{time.monotonic() - started:.3f}s; "
                     f"{'starting handshake' if self.has_handshake else 'no handshake'}",
                     source=self._source)

        with self._state_lock:
            is_current = self._is_current(generation)
            if is_current:
                self._handle = handle
        if not is_current:  # close() got there while the port was opening
            events.debug("Open Abandoned", "close() or a reopen got there "
                         "first; discarding the handle", source=self._source)
            try:
                handle.close()
            except Exception:
                pass
            return

        verified = False
        if self.has_handshake:
            try:
                with self._lock:
                    handle.reset_input_buffer()
                    handle.reset_output_buffer()
            except Exception as exc:
                # the handshake is attempted on its own merits
                events.debug("Buffer Reset Failed", str(exc),
                             source=self._source, exception=exc)
            time.sleep(self.BOOTLOADER_WAIT)
            try:
                verified = self._handshake(handle, generation)
            except Exception as exc:
                verified = False
                events.debug("Handshake Raised", str(exc), source=self._source,
                             exception=exc)

        with self._state_lock:
            if not self._is_current(generation):
                events.debug("Handshake Discarded", f"verified={verified}; the "
                             "link moved on while the handshake ran",
                             source=self._source)
                return
            self._state = (ConnectionState.VERIFIED if verified
                           else ConnectionState.UNVERIFIED)
        events.debug("Handshake Result",
                     f"verified={verified} identity={self._identity!r} "
                     f"after {time.monotonic() - started:.3f}s",
                     source=self._source)
        self._note_state(ConnectionState.CONNECTING, self._state,
                         f"handshake {'answered' if verified else 'unanswered'}")
        if verified:
            events.info("Port Verified", f"{self.port} answered as "
                        f"'{self._identity}'", source=self._source)
        elif self.has_handshake:
            events.warn("Port Unverified", f"{self.port} opened, but nothing "
                        "answered the identity query. Operating blind.",
                        source=self._source)

    def _handshake(self, handle, generation):
        """Ask the board what it is. True if it answered; sets `identity`.

        All SERIAL-17: one ping every `PING_INTERVAL`, not one per poll, and
        none once the board has answered; a *whole* `DEV:` line or nothing;
        and the input is drained after the match, so replies to pings already
        in flight are not left for the owner's reader to wade through.
        """
        deadline = time.monotonic() + self.HANDSHAKE_TIMEOUT
        buffer, next_ping, pings = "", 0.0, 0
        while time.monotonic() < deadline:
            if not self._is_current(generation):
                return False
            now = time.monotonic()
            if now >= next_ping:
                try:
                    with self._lock, self._write_io_lock:
                        handle.write(self.PING)
                except Exception as exc:
                    self._mark_lost(exc)
                    return False
                pings += 1
                # In a loop, so rate-limited (Addendum 1): the count carries
                # the rate, one line per second carries the fact.
                events.debug("Identity Ping", f"{self.PING!r} x{pings}",
                             source=self._source, every=1.0)
                next_ping = now + self.PING_INTERVAL
            try:
                with self._lock:
                    waiting = handle.in_waiting
                    if waiting > 0:
                        buffer += handle.read(waiting).decode("utf-8", errors="ignore")
            except Exception as exc:
                self._mark_lost(exc)
                return False

            identity = self._identity_from(buffer)
            if identity is not None:
                self._identity = identity
                events.debug("Identity", f"{identity!r} after {pings} ping(s); "
                             f"draining {len(buffer)} buffered byte(s)",
                             source=self._source)
                try:
                    with self._lock:
                        handle.reset_input_buffer()
                        self._read_buffer = b""
                except Exception as exc:
                    events.debug("Post-Identity Drain Failed", str(exc),
                                 source=self._source, exception=exc)
                return True
            time.sleep(self._HANDSHAKE_POLL)
        events.debug("Handshake Timed Out",
                     f"{pings} ping(s) in {self.HANDSHAKE_TIMEOUT}s, no DEV: "
                     f"line; buffer={buffer[-120:]!r}", source=self._source)
        return False

    @staticmethod
    def _identity_from(buffer):
        """was serial._device_from. The id from a **complete** `DEV:` line,
        or None. Whatever follows the final newline is a partial line by
        definition and is not considered."""
        if "\n" not in buffer:
            return None
        for line in buffer.split("\n")[:-1]:
            if "DEV:" in line:
                identity = line.split("DEV:")[1].strip()
                if identity:
                    return identity
        return None

    # -- the one write path --------------------------------------------------
    def write(self, payload, *, priority=False, abort_if=None):
        """was serial.write_command. The one way anything reaches the wire.

        Returns True when `payload` (bytes; a str is UTF-8 encoded) was
        written, in ONE `handle.write()`, byte for byte as given. Returns
        False when `abort_if()` was true - it is called at the last moment,
        with every lock this write will take already held, immediately
        before the bytes go out, because a check made before calling is a
        check-then-act and the wait for the lock can be long (TEMP-17).
        Raises TransportError when the port is not open, or when the write
        fails - and a failed write also marks the port LOST. Nothing is
        swallowed and nothing is reported from here: the caller decides what
        a failed write means.

        `priority=True` is for stops only, never for motion. See the class
        docstring for what it waits for and what it does not.
        """
        if isinstance(payload, str):
            payload = payload.encode("utf-8")

        started = time.monotonic()
        outcome, is_wedged, failure = "not attempted", False, None
        acquired = self._lock.acquire(
            timeout=self.PRIORITY_LOCK_TIMEOUT if priority else -1)
        lock_wait = time.monotonic() - started
        io_acquired = False
        try:
            io_acquired = self._write_io_lock.acquire(
                timeout=self.WRITE_IO_LOCK_TIMEOUT if priority else -1)
            is_wedged = not io_acquired
            if abort_if is not None and abort_if():
                outcome = "aborted"
                return False
            handle = self._handle
            if handle is None or not getattr(handle, "is_open", False):
                outcome = "port not open"
                raise TransportError(
                    f"port {self.port} is not open; {payload!r} was not sent")
            try:
                handle.write(payload)
            except Exception as exc:
                failure, outcome = exc, f"failed: {exc}"
            else:
                outcome = "sent"
        finally:
            if io_acquired:
                self._write_io_lock.release()
            if acquired:
                self._lock.release()
            # Every exit reports, the abort and the raise included: a write
            # that did NOT happen is the thing worth having in the log.
            total = time.monotonic() - started
            detail = (f"{outcome}: {len(payload)}B {payload.hex()} "
                      f"lock_wait={lock_wait * 1000:.1f}ms "
                      f"total={total * 1000:.1f}ms")
            if priority:
                # Rare and safety-critical, so never rate-limited: the lock
                # wait is the number the bench wants when a stop feels slow.
                events.debug("Priority Write", f"{detail} forced="
                             f"{not acquired} wedged={is_wedged}",
                             source=self._source, exception=failure)
            elif outcome == "sent":
                # A model may stream through here at 50 Hz, so this call site
                # is rate-limited and the suppressed count reports the rate.
                events.debug("Write", detail, source=self._source,
                             every=self.WRITE_DEBUG_INTERVAL)
            else:
                # Aborts and failures are not the stream, and are never
                # rate-limited away.
                events.debug("Write Not Sent", detail, source=self._source,
                             exception=failure)

        if is_wedged:
            events.warn("Write Wedged", "a write was stuck on the wire; a "
                        "priority payload was forced past it",
                        source=self._source)
        if failure is not None:
            self._mark_lost(failure)
            raise TransportError(
                f"write of {payload!r} to {self.port} failed: {failure}") from failure
        return True

    def _mark_lost(self, why):
        """First transport failure wins: go LOST, release the handle, report
        once (SERIAL-8). The state then carries the fact; later failures
        raise TransportError without another report.

        Takes only `_state_lock`, which is never held across I/O, so this is
        safe on the priority path with the transaction lock in someone
        else's hands. A deliberate CLOSED is never turned into LOST.
        """
        with self._state_lock:
            if self._state in (ConnectionState.LOST, ConnectionState.CLOSED):
                events.debug("Loss Already Recorded",
                             f"state is {self._state.value}; not reporting "
                             f"again: {why}", source=self._source)
                return
            was = self._state
            self._state = ConnectionState.LOST
            handle = self._handle
            if not self.is_simulated:
                self._handle = None
        self._note_state(was, ConnectionState.LOST, f"transport failure: {why}")
        if handle is not None:
            try:
                handle.close()
            except Exception as exc:
                events.debug("Close After Loss Failed", str(exc),
                             source=self._source, exception=exc)
        # warn, not error: the owning model faults on the TransportError it
        # is about to receive, and that fault is the acknowledged popup.
        events.warn("Connection Lost", f"{self.port}: {why}",
                    source=self._source, exception=why if isinstance(why, Exception) else None)

    # -- reads ---------------------------------------------------------------
    def read_line(self, timeout=None):
        """was serial.read_line and SMC100._readline. -> str | None

        One complete line without its terminator, or None when none arrived
        within `timeout` seconds (None = the port's `read_timeout`; 0 = only
        what is already waiting). Undecodable bytes come back as U+FFFD so a
        corrupted line fails its parser instead of passing as a clean one.

        The transaction lock is taken per slice, not across the wait, so a
        reader loop never makes an ordinary write queue behind a blocking
        read. Inside a caller's own transaction the lock is re-entrant and
        simply stays held. Returns None while CONNECTING: the handshake owns
        the input until it is done.

        Raises TransportError when the port is not open, or when the read
        fails - which also marks the port LOST.
        """
        budget = self.read_timeout if timeout is None else max(0.0, float(timeout))
        deadline = time.monotonic() + budget
        while True:
            failure = None
            with self._lock:
                handle = self._handle
                if handle is None or not getattr(handle, "is_open", False):
                    raise TransportError(f"port {self.port} is not open; cannot read")
                if self._state != ConnectionState.CONNECTING:
                    line = self._take_line()
                    if line is not None:
                        return line
                    try:
                        waiting = handle.in_waiting
                        if waiting > 0:
                            self._read_buffer += handle.read(waiting)
                    except Exception as exc:
                        failure = exc
                    else:
                        line = self._take_line()
                        if line is not None:
                            return line
            if failure is not None:
                self._mark_lost(failure)
                raise TransportError(
                    f"read from {self.port} failed: {failure}") from failure
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            time.sleep(min(self._READ_POLL, remaining))

    def _take_line(self):
        """Pop one complete line off the buffer, or None. Bounds the buffer
        so a device that never sends a terminator cannot grow it forever."""
        head, found, tail = self._read_buffer.partition(self.line_terminator)
        if not found:
            if len(self._read_buffer) > self._READ_BUFFER_LIMIT:
                self._read_buffer = self._read_buffer[-self._READ_BUFFER_LIMIT // 2:]
            return None
        self._read_buffer = tail
        if self.line_terminator == b"\n" and head.endswith(b"\r"):
            head = head[:-1]
        return head.decode("utf-8", errors="replace")

    # -- drain -----------------------------------------------------------------
    def flush(self, timeout=None):
        """Wait until the bytes already written have reached the wire. -> bool

        True only if the drain completed. False means the bytes may still be
        in the buffer, and a caller about to close should treat the last
        frame as **not delivered**.

        Bounded, on a daemon worker: pyserial's `flush()` is `tcdrain`, which
        is unbounded, and a dead or flow-controlled port would otherwise hang
        whatever teardown called it (SERIAL-12). The handle is snapshotted
        rather than locked - `tcdrain` only waits on the port - and a failed
        drain is not a state transition: a write that matters has already
        faulted through `write()`.
        """
        budget = self.FLUSH_TIMEOUT if timeout is None else timeout
        handle = self._handle
        if handle is None or not getattr(handle, "is_open", False):
            return False

        drained, failure = threading.Event(), []

        def _drain():
            try:
                handle.flush()
            except Exception as exc:
                failure.append(exc)
            finally:
                drained.set()

        started = time.monotonic()
        threading.Thread(target=_drain, daemon=True,
                         name=f"serial-flush-{self.port}").start()
        ok = drained.wait(budget)
        events.debug("Drain", f"completed={ok} in "
                     f"{(time.monotonic() - started) * 1000:.1f}ms "
                     f"(budget {budget}s) error={failure[0] if failure else None}",
                     source=self._source)
        if not ok:
            events.warn("Port Did Not Drain", f"{self.port} did not drain in "
                        f"{budget}s; treat the last frame as undelivered",
                        source=self._source)
            return False
        if failure:
            events.warn("Port Drain Failed", f"{self.port}: {failure[0]}",
                        source=self._source, exception=failure[0])
            return False
        return True
