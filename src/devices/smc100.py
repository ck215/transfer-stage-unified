"""Newport SMC100 controller, as a station Device.

The protocol methods keep their vendor-derived names (`get_position_deg`,
`move_relative_mdeg`, `sendcmd`, ...) so a diff against Newport's own driver
stays readable; the naming scheme exempts them, and so does the architecture
lint's `get_` rule.

**What changed, and why.** The old `lib/smc100.py` opened and owned a
`serial.Serial` of its own, with its own `_serial_lock`, its own priority-lane
timeout and its own hand-rolled `_readline`. That made two transport stacks in
one process with two different ideas about what a priority write is. All of
that raw I/O now goes through `devices.serial_port.SerialPort`, built
with **this device's** serial settings and `handshake=False` (there is no
identity byte to ask an SMC100 for), so the station has one write path, one
lock discipline and one priority lane.

What did **not** change is the bytes. Every frame this class puts on the wire
is byte-for-byte what `lib/smc100.py` sent -- `tests/
test_smc100_frames.py` records the old class and the new one against the same
script and compares the streams. Firmware is not being touched.

Carried fixes:

* **ROTATOR-16** -- the write is bounded. The port is opened with
  `write_timeout=WRITE_TIMEOUT_SEC`; `xonxoff=True` lets a busy or faulted
  controller withhold XON exactly when someone is pressing FULL STOP, and
  pyserial's default `write_timeout=None` means "block forever" at the wire
  even though `stop(priority=True)` bounds the *lock*.
* **ROTATOR-11** -- `wait_states` has no 12 s ceiling on a long move. The
  12 s bounds time spent *not making progress*; while the controller reports
  motion the idle clock is reset, under an absolute 300 s backstop so
  "wait while it says it is moving" cannot become "wait forever".
* **ROTATOR-8** -- `stop(priority=True)` takes the priority lane and never
  queues behind an in-flight transaction.

The five exception classes collapse into `SMC100Error` plus five one-line
subclasses, so a caller can catch the family without naming five imports.
`test_configure` / `test_general` (bench scripts living inside a library
module, which ran real motion on import-time-visible names) and `__del__`
(`Rotator.close` closes this device) are gone.
"""
import time
from math import floor

from devices.device import Device
from devices.serial_port import SerialPort
from events import events


# -- states, from page 65 of the manual ------------------------------------
STATE_NOT_REFERENCED_FROM_RESET = "0A"
STATE_NOT_REFERENCED_FROM_HOMING = "0B"
STATE_NOT_REFERENCED_FROM_CONFIGURATION = "0C"
STATE_CONFIGURATION = "14"
STATE_HOMING_FROM_RS232 = "1E"
STATE_HOMING_FROM_SMC_RC = "1F"
STATE_MOVING = "28"
STATE_READY_FROM_HOMING = "32"
STATE_READY_FROM_MOVING = "33"
STATE_READY_FROM_DISABLE = "34"
STATE_DISABLE_FROM_READY = "3C"
STATE_DISABLE_FROM_MOVING = "3D"
STATE_DISABLE_FROM_JOGGING = "3E"

STATE_NOT_REFERENCED = STATE_NOT_REFERENCED_FROM_RESET   # convenience alias

#: The controller is working: a wait must not expire under it (ROTATOR-11).
IN_MOTION_STATES = (STATE_MOVING, STATE_HOMING_FROM_RS232, STATE_HOMING_FROM_SMC_RC)

DISABLED_STATES = (STATE_DISABLE_FROM_READY, STATE_DISABLE_FROM_MOVING,
                   STATE_DISABLE_FROM_JOGGING)

#: For the log file only. The old driver `print`ed one of these on every
#: single `TS?` reply -- a 4 Hz poll writing to stdout forever.
STATE_NAMES = {
    "0A": "NOT REFERENCED from reset", "0B": "NOT REFERENCED from HOMING",
    "0C": "NOT REFERENCED from CONFIGURATION", "0D": "NOT REFERENCED from DISABLE",
    "0E": "NOT REFERENCED from READY", "0F": "NOT REFERENCED from MOVING",
    "10": "NOT REFERENCED ESP stage error", "11": "NOT REFERENCED from JOGGING",
    "14": "CONFIGURATION", "1E": "HOMING commanded from RS-232-C",
    "1F": "HOMING commanded by SMC-RC", "28": "MOVING",
    "32": "READY from HOMING", "33": "READY from MOVING",
    "34": "READY from DISABLE", "35": "READY from JOGGING",
    "3C": "DISABLE from READY", "3D": "DISABLE from MOVING",
    "3E": "DISABLE from JOGGING", "46": "JOGGING from READY",
    "47": "JOGGING from DISABLE",
}


class SMC100Error(Exception):
    """Base of every SMC100 driver failure: one family, five reasons."""

    MESSAGE = "SMC100 error"

    def __init__(self, detail=""):
        self.detail = "" if detail is None else str(detail)
        super().__init__(f"{self.MESSAGE}: {self.detail}" if self.detail else self.MESSAGE)


class SMC100ReadTimeout(SMC100Error):
    MESSAGE = "Read timed out"


class SMC100WaitTimeout(SMC100Error):
    MESSAGE = "Wait timed out; the stage has not been stopped"


class SMC100DisabledState(SMC100Error):
    MESSAGE = "Disabled state encountered"


class SMC100Corruption(SMC100Error):
    MESSAGE = "RS232 corruption detected"


class SMC100InvalidResponse(SMC100Error):
    MESSAGE = "Invalid response"


class SMC100(Device):
    """One rotation stage on one serial link.

    `abort_if` is the model's FULL STOP latch. It is handed to
    `SerialPort.write`, so the check happens **inside the port lock**, after
    this command has won the lock and immediately before its bytes go out --
    which is the only place a check can catch a stop that landed while the
    command was queued. `stop(priority=True)` deliberately passes no
    `abort_if`: a latched stop is the one write that must still go.
    """

    #: `lib/smc100.py` opened exactly these. Copied, not re-derived.
    #: 8 data bits, no parity and 1 stop bit are fixed in `SerialPort.
    #: _open_handle` and already match what this device needs.
    BAUD_RATE = 57600
    XONXOFF = True
    LINE_TERMINATOR = "\r\n"
    TERMINATOR = b"\r\n"
    READ_TIMEOUT_SEC = 0.050

    #: ROTATOR-16. Bound on a single write. Matches the value already used to
    #: identity-probe this device at this baud rate.
    WRITE_TIMEOUT_SEC = 0.2

    #: A stop that cannot get the lock is worse than an unsynchronised one.
    PRIORITY_LOCK_TIMEOUT = 0.05

    #: Bounded, so a flush cannot become the new unbounded wait.
    FLUSH_TIMEOUT_SEC = 0.2

    #: How long to wait for the port to come up before giving up on `open()`.
    OPEN_TIMEOUT_SEC = 5.0

    #: Time to leave between commands that expect no reply. Trial and error,
    #: inherited from the vendor driver.
    COMMAND_WAIT_TIME_SEC = 0.06

    #: ROTATOR-11: the ceiling on time spent *not* making progress...
    MAX_IDLE_WAIT_SEC = 12
    #: ...and the absolute backstop, even while it keeps reporting motion.
    MAX_MOVING_WAIT_SEC = 300

    #: Repeating these would repeat MOTION. Never retried, whatever is asked.
    NO_RETRY_COMMANDS = ("PR", "OR")

    #: retry=True means "until it answers". Bounded anyway: the vendor driver's
    #: `retry <= 0` test is False for `True`, so its loop had no exit at all.
    MAX_RETRIES = 1000

    def __init__(self, smc_id=1, port=None, *, transport=None, abort_if=None,
                 sleep=time.sleep):
        self._smc_id = str(smc_id)
        self._port_name = port
        self._abort_if = abort_if
        self._sleep = sleep
        self._last_command_at = 0.0
        self._last_state = None
        self._port = transport if transport is not None else self._build_transport(port)

    def _build_transport(self, port):
        """The SMC100's own serial settings, on the station's one transport.

        Exactly what `legacy/src/lib/smc100.py` passed `serial.Serial(...)`:
        57600 8N1, software flow control, CRLF lines, a 50 ms read and a
        200 ms bounded write (ROTATOR-16).

        `handshake=False`: there is no `s\\n` -> `DEV: x` identity protocol
        on a Newport controller, and a handshake write into a stage
        controller is not a harmless probe. The link therefore opens
        UNVERIFIED, and `open()` calls `mark_verified()` once the controller
        has answered `TS?` in its own language.
        """
        try:
            return SerialPort(
                port, self.BAUD_RATE,
                xonxoff=self.XONXOFF,
                read_timeout=self.READ_TIMEOUT_SEC,
                write_timeout=self.WRITE_TIMEOUT_SEC,
                line_terminator=self.LINE_TERMINATOR,
                handshake=False,
            )
        except (TypeError, ValueError) as exc:
            # Loud, not papered over: an unbounded write timeout is the whole
            # of ROTATOR-16, so silently falling back to a narrower SerialPort
            # signature would re-open a safety defect to keep a constructor
            # working.
            events.error("Rotator Transport", "SerialPort would not take the "
                         "SMC100's serial settings; the stage cannot be opened "
                         "with a bounded write timeout", source="SMC100",
                         exception=exc)
            raise

    # -- Device ------------------------------------------------------------
    def open(self):
        """Open the port, then prove the controller is actually answering.

        A `handshake=False` port opens UNVERIFIED, which is honest: opening a
        port proves nothing, and a cable into a powered-off controller opens
        exactly like a working one. `TS?` is this device's own identity
        question, so a reply to it is what earns `mark_verified` -- and what
        makes the rotator's status word read `verified` rather than leaving
        it permanently unverified.
        """
        started = time.monotonic()
        self._port.open()
        if not self._port.wait_open(self.OPEN_TIMEOUT_SEC):
            events.debug("Open Failed", f"{self._port_name} did not open within "
                         f"{self.OPEN_TIMEOUT_SEC}s", source="SMC100")
            raise SMC100Error(f"the port {self._port_name} did not open")
        errors, state = self.get_status()
        mark_verified = getattr(self._port, "mark_verified", None)
        if mark_verified is not None:
            mark_verified(f"SMC100 #{self._smc_id}")
        events.debug("Open", f"{self._port_name} open in "
                     f"{(time.monotonic() - started) * 1000:.0f} ms; "
                     f"state {state} ({STATE_NAMES.get(state, 'unknown')}), "
                     f"errors 0x{errors:04X}", source="SMC100")
        return True

    def close(self):
        port, self._port = self._port, None
        if port is None:
            return
        events.debug("Close", f"closing {self._port_name}", source="SMC100")
        port.close()

    @property
    def is_open(self):
        port = self._port
        return bool(port is not None and port.is_open)

    @property
    def status(self):
        port = self._port
        if port is None:
            return "closed"
        return getattr(port, "status", None) or ("open" if port.is_open else "closed")

    # -- protocol ----------------------------------------------------------
    def reset_and_configure(self):
        """Reset, load the stage's own parameters over ESP, home.

        Byte-for-byte the vendor sequence: RS, RS, wait, ID?, PW1, wait, ZX1,
        ZX2, PW0, wait.
        """
        events.debug("Configure", "resetting and reloading stage parameters",
                     source="SMC100")
        self.sendcmd("RS")
        self.sendcmd("RS")

        self._sleep(3)

        self.wait_states(STATE_NOT_REFERENCED_FROM_RESET, ignore_disabled_states=True)

        stage = self.sendcmd("ID", "?", True)
        events.info("Stage Found", f"{stage}", source="SMC100")

        self.sendcmd("PW", 1)                       # enter config mode
        self.wait_states(STATE_CONFIGURATION)
        self.sendcmd("ZX", 1)                       # load stage parameters
        self.sendcmd("ZX", 2)                       # enable stage ID check
        self.sendcmd("PW", 0)                       # exit config mode
        self.wait_states(STATE_NOT_REFERENCED_FROM_CONFIGURATION)
        return stage

    def home(self, wait_stop=True):
        """OR, then settle at 0.

        Homing a stage that is already homed has no effect, so an absolute
        move to 0 follows: callers expect this method to leave the stage at
        the origin, not merely referenced.
        """
        self.sendcmd("OR")
        if wait_stop:
            state = self.wait_states((STATE_READY_FROM_HOMING, STATE_READY_FROM_MOVING))
            if state == STATE_READY_FROM_MOVING:
                self.move_absolute_mdeg(0, wait_stop=True)
        else:
            self.move_absolute_mdeg(0, wait_stop=False)

    def stop(self, priority=False):
        """Send ST. Returns whether the bytes went out.

        With `priority`, this never queues behind an in-flight transaction
        (ROTATOR-8) and carries no `abort_if`: the latch is the reason this
        write exists, so it must not be the reason it is dropped. It is also
        the one command that skips the post-write flush -- a flush is another
        bounded wait, and the stop path has already spent its budget.
        """
        if not priority:
            self.sendcmd("ST")
            return True

        port = self._port
        if port is None:
            # MANAGER-21: nothing to stop, and nothing to claim landed.
            events.debug("Stop", "no port: nothing was sent", source="SMC100")
            return False
        frame = self._frame("ST")
        started = time.monotonic()
        landed = bool(port.write(frame, priority=True))
        events.debug("Stop", f"priority ST {frame.hex(' ')} "
                     f"{'landed' if landed else 'NOT written'} in "
                     f"{(time.monotonic() - started) * 1000:.1f} ms",
                     source="SMC100")
        return landed

    def get_status(self):
        """TS?: `(error_word, state_code)`, per pages 64-65 of the manual."""
        response = self.sendcmd("TS", "?", expect_response=True, retry=10)
        errors = int(response[0:4], 16)
        state = response[4:]
        if len(state) != 2:
            raise SMC100InvalidResponse(f"TS -> {response!r}")
        if state != self._last_state:
            events.debug("State", f"{self._last_state} -> {state} "
                         f"({STATE_NAMES.get(state, 'unknown')}); "
                         f"errors 0x{errors:04X}", source="SMC100")
            self._last_state = state
        return errors, state

    def get_position_deg(self):
        return float(self.sendcmd("TP", "?", expect_response=True, retry=10))

    def get_position_mdeg(self):
        return int(self.get_position_deg() * 1000)

    def move_relative_deg(self, dist_deg, wait_stop=True):
        self.sendcmd("PR", dist_deg)
        if wait_stop:
            # READY_FROM_HOMING is included because PR0 from a freshly homed
            # stage never leaves it, and the wait would otherwise never end.
            self.wait_states((STATE_READY_FROM_MOVING, STATE_READY_FROM_HOMING))

    def move_relative_mdeg(self, dist_mdeg, **kwargs):
        self.move_relative_deg(int(dist_mdeg) / 1000, **kwargs)

    def move_absolute_deg(self, position_deg, wait_stop=True):
        self.sendcmd("PA", position_deg)
        if wait_stop:
            self.wait_states((STATE_READY_FROM_MOVING, STATE_READY_FROM_HOMING))

    def move_absolute_mdeg(self, position_mdeg, **kwargs):
        return self.move_absolute_deg(floor(position_mdeg) / 1000, **kwargs)

    def wait_states(self, targetstates, ignore_disabled_states=False):
        """Wait for one of `targetstates`, and return the one that arrived.

        **No 12 s cap on a long move (ROTATOR-11).** `MAX_IDLE_WAIT_SEC`
        bounds time spent *not making progress*: every reply that reports
        motion resets it, so a 60 deg move at a low velocity is not a
        timeout. `MAX_MOVING_WAIT_SEC` is the absolute backstop for a
        controller wedged in state 28.

        Read timeouts are ignored and retried: after PW0 the controller can
        be unresponsive for ten seconds and still be perfectly healthy.

        A disabled state raises unless it is what we are waiting for -- a
        stage that sticks transitions into DISABLE_FROM_MOVING and stays
        there forever.
        """
        if isinstance(targetstates, str):
            targetstates = (targetstates,)
        started = time.monotonic()
        idle_since = started
        last_state = None
        events.debug("Wait", f"waiting for {targetstates}", source="SMC100")
        while True:
            now = time.monotonic()
            if self._abort_if is not None and self._abort_if():
                raise SMC100Error("wait abandoned: FULL STOP is latched")
            if now - started > self.MAX_MOVING_WAIT_SEC:
                raise SMC100WaitTimeout(f"last reported state {last_state} "
                                        f"after {now - started:.1f}s")
            if now - idle_since > self.MAX_IDLE_WAIT_SEC:
                raise SMC100WaitTimeout(f"last reported state {last_state} "
                                        f"after {now - started:.1f}s with no "
                                        f"progress")
            try:
                state = self.get_status()[1]
            except SMC100ReadTimeout:
                events.debug("Wait", "read timed out; retrying in 1 s",
                             source="SMC100", every=5.0)
                self._sleep(1)
                continue
            last_state = state
            if state in targetstates:
                events.debug("Wait", f"reached {state} after "
                             f"{time.monotonic() - started:.1f}s", source="SMC100")
                return state
            if state in IN_MOTION_STATES:
                idle_since = time.monotonic()
            elif not ignore_disabled_states and state in DISABLED_STATES:
                raise SMC100DisabledState(state)

    def sendcmd(self, command, argument=None, expect_response=False, retry=False):
        """One command out, optionally one reply back.

        For a GET, the `?` is the ARGUMENT, not part of the command -- `1ID?`
        is `sendcmd('ID', '?')`.

        `retry` re-sends on a reply that fails verification. Read-only
        commands only: PR and OR are refused a retry here regardless of what
        the caller asked, because repeating them repeats MOTION.
        """
        assert command[-1] != "?"
        port = self._port
        if port is None:
            return None

        frame = self._frame(command, argument)
        prefix = self._smc_id + command
        retries_left = self._retry_budget(retry if command not in self.NO_RETRY_COMMANDS
                                          else False)

        while True:
            if not port.write(frame, abort_if=self._abort_if):
                # `abort_if` fired inside the lock: nothing was written, and
                # nothing must pretend otherwise.
                events.debug("Aborted", f"{prefix} not written: abort_if fired "
                             f"inside the port lock", source="SMC100")
                raise SMC100Error(f"{prefix} was not written: FULL STOP is latched")
            events.debug("Wire", f"{prefix} <- {frame.hex(' ')}", source="SMC100",
                         every=1.0 if command in ("TS", "TP") else 0.0)
            if not expect_response:
                # A command with no reply has nothing else to prove it left,
                # so it is drained -- bounded, unlike the old driver's
                # unbounded `tcdrain`. A command that expects a reply is
                # proven delivered by the reply, and draining it as well
                # would put a thread and a log line on the 4 Hz poll path
                # for no information.
                port.flush(self.FLUSH_TIMEOUT_SEC)
                self._pace()
                return None
            try:
                return self._read_reply(prefix, command)
            except SMC100Error:
                if retries_left <= 0:
                    raise
                retries_left -= 1

    # -- helpers -----------------------------------------------------------
    def _frame(self, command, argument=None):
        """`<id><command><argument><CR><LF>`, exactly as the old driver built
        it: `str(argument)`, ASCII, terminator appended."""
        argument = "" if argument is None else argument
        return (self._smc_id + command + str(argument)).encode("ascii") + self.TERMINATOR

    def _retry_budget(self, retry):
        if retry is False or retry is None:
            return 0
        if retry is True:
            return self.MAX_RETRIES
        return max(0, int(retry))

    def _read_reply(self, prefix, command):
        line = self._port.read_line(self.READ_TIMEOUT_SEC)
        if line is None:
            raise SMC100ReadTimeout(f"no reply to {prefix}")
        for character in line:
            if not 32 <= ord(character) < 127:
                raise SMC100Corruption(hex(ord(character)))
        if not line.startswith(prefix):
            raise SMC100InvalidResponse(f"{command} -> {line!r}")
        return line[len(prefix):]

    def _pace(self):
        """The vendor driver's inter-command delay, preserved."""
        now = time.monotonic()
        remaining = self.COMMAND_WAIT_TIME_SEC - (now - self._last_command_at)
        if remaining > 0:
            self._sleep(remaining)
        self._last_command_at = now
