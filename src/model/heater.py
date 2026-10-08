"""The temperature controller. Was `TemperatureSystem`.

The firmware is untouched, so the frames this model puts on the wire are the
old ones, byte for byte:

    settings    <setpoint,ramp:.2f,p,i,d,offset>     apply_settings
    heater off  <0,ramp:.1f,0,0,0,offset>            halt / estop
    reset       <0,6.0,0,0,0,0>                      first open, then close

The `.2f` on one and the `.1f` on the other is not a typo here; it is what
`send_settings` and `stop` did, and `spdelay` is read with `atof`, so the two
spellings are the same number to the board. `tests/test_heater_
frames.py` drives the OLD `TemperatureSystem` in simulator mode and asserts
these bytes against it rather than against a literal in this file.

`temp_controller.ino` splits a frame with `strtok`, which does not see an
empty field — it sees the next one. So a blank or unparseable box shifts
every later parameter left and the board is handed the ramp rate as its
setpoint. That is why `apply_settings` refuses the whole frame rather than
substituting anything (TEMP-3, TEMP-13). The same routine reads into
`receivedChars[32]` and clamps its index, so a payload past 31 characters
silently loses its tail: that is refused too.

The firmware has NO host watchdog. Whatever was last commanded persists for
as long as the board has power, which is why a heater-off that cannot be
confirmed is an `events.error`, not a shrug.
"""
import math
import threading
import time

import schema as sch
from devices.serial_port import SerialPort
from events import events
from model.base import Model
from param import Param
from result import Refused


class Heater(Model):
    """Was TemperatureSystem. Owns a SerialPort.

    Absorbs: TemperatureSystem
    """

    NAME = "Temperature Controller"
    IDENTITY = "t"                 # firmware answers 's' with "DEV: t"
    NEEDS_PORT = True
    NEEDS_GAMEPAD = False

    BAUD_RATE = 115200

    #: TEMP-13: "a typo (e.g. 2000 instead of 200) is sent unmodified", and
    #: the audit asks for "a configurable max_setpoint". PROVISIONAL —
    #: nothing in the firmware states a limit (`endpoint` is a bare float),
    #: so this is wide enough not to refuse real work and is the owner's to
    #: tighten at the bench. The MAX6675 tops out around 1024 C.
    MAX_SETPOINT = 300.0

    PARAMS = {
        "setpoint": Param("setpoint", "float", default=0, minimum=0,
                          maximum=MAX_SETPOINT, decimals=1, unit="°C",
                          label="Setpoint"),
        "ramp_rate": Param("ramp_rate", "float", default=10, minimum=0,
                           maximum=3600, decimals=2, unit="s/°C",
                           label="Ramp Rate (s/°C)"),
        "p_term": Param("p_term", "float", default=2.0, minimum=0,
                        maximum=1000, decimals=3,
                        label="Proportional Term (P)"),
        "i_term": Param("i_term", "float", default=0.5, minimum=0,
                        maximum=1000, decimals=3, label="Integral Term (I)"),
        "d_term": Param("d_term", "float", default=0.1, minimum=0,
                        maximum=1000, decimals=3, label="Derivative Term (D)"),
        "offset": Param("offset", "float", default=0, minimum=-100,
                        maximum=100, decimals=2, unit="°C", label="Offset"),
    }
    #: Frame order. Also the `inputs` of Enter Settings, so the whole frame
    #: travels with the command and is validated as a set (D-5, TEMP-4).
    FRAME_FIELDS = ("setpoint", "ramp_rate", "p_term", "i_term", "d_term",
                    "offset")

    #: The board's power-on state. Old `__init__` and old `close()` sent it.
    RESET_FRAME = b"<0,6.0,0,0,0,0>"
    #: `receivedChars[32]` in temp_controller.ino, one byte of it the NUL.
    MAX_PAYLOAD_LENGTH = 31

    HISTORY_LENGTH = 200         # ~2 min at the board's ~1.7 lines/s
    READ_TIMEOUT = 0.25          # s one read may hold the reader
    READ_FLOOR = 0.01            # s between reads: never above ~100 Hz
    MIN_BACKOFF, MAX_BACKOFF = 0.1, 2.0
    PERSISTENT_AFTER = 5         # read failures before the link is called lost
    #: s after `open()` in which a failed read is the board coming up, not
    #: news (bench, 2026-09-27: "always boots with a warning, then works").
    #: `SerialPort.open()` returns before the handle exists and then spends
    #: up to its bootloader wait plus its handshake budget CONNECTING, so the
    #: reader's first pass sees a port that is not open yet. The window is
    #: that whole connect sequence. A failure inside it goes to the log file
    #: only; a streak that outlasts it warns, once, with the usual text. The
    #: first reading ends the window early: after it, every rule is as before.
    BOOT_GRACE_SEC = SerialPort.BOOTLOADER_WAIT + SerialPort.HANDSHAKE_TIMEOUT
    #: s a stop waits for an Enter Settings. Short, so that this plus the
    #: port's own priority wait still fits inside `Model.ESTOP_BUDGET`.
    WRITE_LOCK_TIMEOUT = 0.02
    FLUSH_TIMEOUT = 1.0
    #: L12 (SF-2): the bytes of an off frame reaching the port prove little
    #: (a truncated earlier frame turns it into heat-to-the-old-setpoint on
    #: the board). The board reports its setpoint as the third telemetry
    #: field and drops it to 0 within ~0.6 s of a real off; a heating stop
    #: is confirmed only when a reading at or below OFF_SETPOINT_MAX arrives
    #: within OFF_CONFIRM_SECONDS. SIM has no telemetry and is not checked.
    OFF_CONFIRM_SECONDS = 1.5
    OFF_SETPOINT_MAX = 0.5

    def __init__(self, port=None, gamepad=None, sim=False, clock=time.monotonic):
        """was TemperatureSystem.__init__

        `port` is a port name, or an already-built SerialPort (Setup may have
        opened one to read its identity byte, and a test hands in a double).
        `gamepad` is accepted because every Model takes it; a heater has no
        use for one. `clock` times the boot grace; a test hands in its own.
        """
        super().__init__()
        self._clock = clock
        #: `clock()` value the boot grace ends at; None = no grace (a reader
        #: driven without `open()`, or a boot that is over).
        self._boot_grace_until = None
        if hasattr(port, "write") and not isinstance(port, str):
            self.port = port
        else:
            self.port = SerialPort("SIM" if sim or port in (None, "None")
                                   else port, baud_rate=self.BAUD_RATE)

        self._history_lock = threading.Lock()
        self._times, self._temperatures, self._setpoints = [], [], []
        self._latest = None

        # `apply_settings` and the stops each build a frame and then write it.
        # Under the Web view those run on concurrent ThreadingHTTPServer
        # request threads, so Enter Settings could read the setpoint before a
        # stop zeroed it and land its write after the stop's — re-arming the
        # heater the operator just stopped. Build-and-write is ONE critical
        # section, not two steps (TEMP-7).
        self._write_lock = threading.Lock()
        # Bumped by every stop, before its frame is built. A settings frame
        # remembers the generation it started in and is dropped AT THE WIRE if
        # a stop has happened since (TEMP-17). The latch alone cannot do this:
        # a plain Stop System does not latch.
        self._stop_generation = 0
        # What the board was last SENT, not what is in the box: an operator
        # typing a number has not started heating. None = nothing non-zero
        # since the last stop. This is `is_active` (D-8a), and the old
        # `_client_liveness_active` is gone with the rest of that machinery.
        self._commanded_setpoint = None
        self._has_sent_reset = False
        # Has a frame ever had somewhere to go? Decides whether a failed
        # heater-off on the way out is news or a port that never opened.
        self._was_reachable = False

        # The reader waits on the base's `_threads_stop` (MOD-2): an Event,
        # not a bool, so a reader parked in a 2 s backoff leaves the moment
        # close() asks, or it would outlive the join and the port would shut
        # underneath a thread about to read it (TEMP-2 seam).
        self._is_link_lost = False
        #: L12: the heating stop awaiting the board's word, or None.
        self._off_pending = None

    # -- devices and lifecycle ---------------------------------------------
    @property
    def devices(self):
        return [self.port]

    def _start_threads(self):
        """The reader, spawned through the base (MOD-2). `Model.close()` stops
        and joins it BEFORE the devices close, so it is never inside
        `read_line` on a descriptor closing under it (TEMP-11)."""
        self._arm_boot_grace()
        self._spawn("reader", self._read_loop)

    def _arm_boot_grace(self):
        """Start the boot window. `open()` does it; a test does it by hand."""
        self._boot_grace_until = self._clock() + self.BOOT_GRACE_SEC

    def _is_booting(self):
        """Still inside the boot window: armed, no reading yet, not expired."""
        until = self._boot_grace_until
        if until is None:
            return False
        if self._clock() < until:
            return True
        self._boot_grace_until = None
        return False

    # -- what the operator reads -------------------------------------------
    @property
    def temperature(self):
        """The reading as text, and the truth about where it comes from.

        TEMP-10: a link that drops mid-session must not go on showing its last
        good value as if it were live, and "N/A" — which reads as *no reading
        yet*, a transient state the operator waits out — must never be shown
        for a reading that cannot arrive. How OLD a live reading is comes from
        the inherited `state["age"]`, which is what greys the field out.
        """
        if self.port.status == "simulated":
            return "Simulated"
        if not self.port.is_open or self._is_link_lost:
            return "Disconnected"
        latest = self._latest
        return "N/A" if latest is None else f"{latest:.2f} °C"

    @property
    def connection(self):
        return self.port.status

    @property
    def history(self):
        """was TemperatureSystem.get_history

        `(times, temperatures, setpoints)`, a snapshot under the lock.
        """
        with self._history_lock:
            return (list(self._times), list(self._temperatures),
                    list(self._setpoints))

    @property
    def series(self):
        """was TemperatureSystem.temp_series
        same name as RedMonitor.series: one name for 'the plot data'

        `{"x": times, "y": temperatures}` — the whole of what a plot renderer
        needs, which is what lets the plot be schema-driven in all three views
        """
        with self._history_lock:
            return {"x": list(self._times), "y": list(self._temperatures)}

    @property
    def heating_to(self):
        """The setpoint the board is holding, as text with its unit; "" while
        the heater is off (status by exception)."""
        if not self._commanded_setpoint:
            return ""
        return f"{float(self._commanded_setpoint):.1f} °C"

    @property
    def is_active(self):
        """Heating: a non-zero setpoint has been SENT and not since stopped.

        A setpoint typed into the box is not heating. Replaces the old
        `_client_liveness_active`, which is what the Web heartbeat watchdog
        now consults through `Controller.is_active`.
        """
        return bool(self._commanded_setpoint)

    @property
    def schema(self):
        params = self.PARAMS
        # Tiers (E, 2026-09-25): the temperature and its setpoint are the
        # session's work; PID, ramp and offset are configuration; the plot and
        # the connection word are on demand.
        return sch.schema(
            sch.section(
                "Temperature",
                sch.readonly("Current Temperature:", "temperature", rail=True, unit="°C"),
                # Round 8 (PM8-2, IMP8-4): a typed setpoint and a SENT one looked
                # the same. This line says what the board is holding; empty
                # (quiet) while the heater is off.
                sch.readonly("Heating to:", "heating_to", role="info"),
                sch.entry(params["setpoint"].label + ":", "setpoint", params["setpoint"]),
                # Every field of the frame travels with the command and is
                # validated as a set before it runs (D-5). Without this the
                # model reads whatever it happens to hold, one edit behind
                # what was just typed (TEMP-4).
                sch.button("Enter Settings", "apply_settings",
                           inputs=self.FRAME_FIELDS, role="go",
                           disabled_when=("latched",)),
                sch.button("Stop heater", "halt", role="neutral", stop=True),
            ),
            sch.section(
                "Control Parameters",
                *[sch.entry(params[name].label + ":", name, params[name])
                  for name in self.FRAME_FIELDS if name != "setpoint"],
                sch.plot("Temperature over time", "series",
                         x_label="time (s)", y_label="temperature (°C)",
                         empty="No readings yet. The temperature plots here as it is reported."),
                tier=2, disclosure=f"Configure {self.NAME}",
            ),
            sch.section(
                "Diagnostics",
                sch.readonly("Connection:", "connection", role="info"),
                tier=3, disclosure="Diagnostics",
            ),
            self._safety_section(),
        )

    # -- commands -----------------------------------------------------------
    def apply_settings(self):
        """was TemperatureSystem.send_settings
        raises Refused instead of returning None

        Returns the setpoint that reached the board. Refuses — never returns
        quietly — when the latch is set, when any field will not parse, when
        there is no open port, or when a stop superseded the frame while it
        waited for the wire.
        """
        self._guard("Settings")
        # Taken before anything is read: a stop landing at ANY point after
        # this command began supersedes it.
        generation = self._stop_generation
        frame, setpoint = self._build_settings_frame()
        if not self.port.is_open:
            self._refuse(f"Settings not sent: the {self.NAME} is not "
                         f"connected ({self.port.status}). Check its port in "
                         "Setup.")

        def _is_superseded():
            return self._estop.is_set() or self._stop_generation != generation

        events.debug("Settings Frame", f"{frame.hex(' ')}  ({frame!r})",
                     source=self.NAME)
        started = time.monotonic()
        with self._write_lock:
            # The check that counts. `_guard` above is a check-then-act: this
            # write can then wait on the port's transaction lock for as long
            # as the reader's read holds it, while a stop forces its frame
            # past. `abort_if` is re-asked with every lock this write takes
            # already held, immediately before the bytes go out (TEMP-7,
            # TEMP-17). It subsumes `self._estop.is_set`.
            is_written = bool(self.port.write(frame, abort_if=_is_superseded))
            if is_written:
                self._commanded_setpoint = setpoint or None
                self._was_reachable = True
        events.debug("Settings Write",
                     f"written={is_written} in "
                     f"{(time.monotonic() - started) * 1000:.1f} ms", source=self.NAME)
        if not is_written:
            self._guard("Settings")   # name the latch when the latch is why
            self._refuse("Settings not sent: a stop arrived while they were "
                         "waiting to be sent. Send them again.")
        events.debug("Settings Sent", frame.decode("ascii"), source=self.NAME)
        events.info("Settings Sent", "The settings reached the controller.",
                    source=self.NAME)
        return setpoint

    def _halt_hardware(self):
        """The heater-off frame, on the PRIORITY lane. True when it landed.

        The one hook `Model.halt()` and `Model.estop()` drive. Both the Stop
        System button and FULL STOP arrive here, so there is one stop path
        and not two spellings of one.
        """
        started = time.monotonic()
        heating = self._commanded_setpoint
        is_off = self._send_heater_off(self._heater_off_frame)
        if is_off and heating:
            self._await_off_by_telemetry(heating)
        events.debug("Halt", f"heater-off {'landed' if is_off else 'DID NOT LAND'} "
                     f"in {(time.monotonic() - started) * 1000:.1f} ms",
                     source=self.NAME)
        return is_off

    def _await_off_by_telemetry(self, heating):
        """L12 (SF-2): confirm a heating stop from the board's own reported
        setpoint, on a worker, within OFF_CONFIRM_SECONDS. Only on a real
        port (SIM and a test double have no telemetry to wait for)."""
        port = self.port
        if not isinstance(port, SerialPort) or port.is_simulated:
            return
        pending = {"generation": self._stop_generation, "heating": heating,
                   "landed": threading.Event(),
                   "latched_at": self._latched_at}
        self._off_pending = pending
        threading.Thread(target=self._confirm_off, args=(pending,), daemon=True,
                         name=f"heater-off-confirm-{self.NAME}").start()

    def _confirm_off(self, pending):
        if pending["landed"].wait(self.OFF_CONFIRM_SECONDS):
            events.debug("Heater Off Confirmed", "the board reported its "
                         "setpoint at 0", source=self.NAME)
            return
        if (self._off_pending is not pending
                or self._stop_generation != pending["generation"]
                or self._closing):
            return
        self._off_pending = None
        if self._commanded_setpoint is None:
            # Still possibly heating: say so (is_active, the watchdog).
            self._commanded_setpoint = pending["heating"]
        if self._estop.is_set() and self._latched_at == pending["latched_at"]:
            self._stop_confirmed = False
        events.debug("Heater Off Unconfirmed", f"no reported setpoint <= "
                     f"{self.OFF_SETPOINT_MAX} within {self.OFF_CONFIRM_SECONDS} s "
                     f"of the off frame (was {pending['heating']})",
                     source=self.NAME)
        events.warn(events.HEATER_OFF_NOT_SENT, "The heater did not report its "
                    f"setpoint at 0 within {self.OFF_CONFIRM_SECONDS:g} s of the "
                    "stop, so it may still be heating. Switch it off at the "
                    "controller.", source=self.NAME, ack=True)

    def disable(self):
        """De-energize and drain. `Model.close()` runs this after `halt()`.

        Together those two are the old teardown byte for byte: the heater-off
        frame, then the board's power-on frame, then a bounded flush. The
        flush is not ceremony — `close()` on POSIX does not guarantee that
        bytes handed to the OS were transmitted, so the off-frame can be
        discarded by the very close that follows it (TEMP-11 item 4).

        The firmware has no watchdog, so a frame that does not go out leaves
        the heater at its last setpoint for as long as it has power. That is
        an unconfirmed stop, and it is the one thing on this path that earns
        an `events.error`.
        """
        if not self.port.is_open and not self._was_reachable:
            events.debug("Disable Skipped", "the port was never reachable; "
                         "`state` has said so all along", source=self.NAME)
            return False
        is_off = self._send_heater_off(lambda: self.RESET_FRAME)
        try:
            is_drained = self.port.flush(self.FLUSH_TIMEOUT) is not False
        except Exception as exc:
            is_drained = False
            events.debug("Flush Failed", repr(exc), source=self.NAME,
                         exception=exc)
            events.warn("Flush Failed", "The heater-off command may not have "
                        "left the computer. Check the heater is off at the "
                        "controller.", source=self.NAME, exception=exc)
        events.debug("Disable", f"off={is_off} drained={is_drained}",
                     source=self.NAME)
        if not (is_off and is_drained):
            # ack=False: this is the exit path and a modal here blocks the
            # shutdown it is reporting on. It is still an error.
            events.error("Heater Off Not Delivered",
                         "The heater-off frame could not be confirmed on the "
                         "wire, so the heater may still be at its last "
                         "setpoint.", source=self.NAME, ack=False)
        return is_off and is_drained

    def _send_heater_off(self, build_frame):
        """The one stop path. Supersede first, then force the frame out.

        Bumping the generation BEFORE the frame is built is what drops a
        settings frame that is already waiting for the port, rather than
        letting it be written after this one. `_write_lock` is taken with a
        short timeout and forced past on expiry: a stop that cannot get a
        lock is worse than an unsynchronised one, and the port's own
        write-in-flight lock still keeps the two frames from interleaving.
        """
        self._stop_generation += 1
        self.setpoint = 0
        started = time.monotonic()
        is_locked = self._write_lock.acquire(timeout=self.WRITE_LOCK_TIMEOUT)
        if not is_locked:
            events.debug("Stop Forced", "write lock busy after "
                         f"{self.WRITE_LOCK_TIMEOUT} s; forcing the stop frame "
                         "through", source=self.NAME)
        try:
            frame = build_frame()
            events.debug("Heater Off Frame", f"{frame.hex(' ')}  ({frame!r}); "
                         f"lock wait {(time.monotonic() - started) * 1000:.1f} ms",
                         source=self.NAME)
            is_written = bool(self.port.write(frame, priority=True))
        except Exception as exc:
            events.debug("Heater Off Not Sent", repr(exc), source=self.NAME,
                         exception=exc)
            events.warn(events.HEATER_OFF_NOT_SENT, "The heater-off command "
                        "could not be sent. The heater may still be on; switch "
                        "it off at the controller.", source=self.NAME,
                        exception=exc, ack=True)
            return False
        finally:
            if is_locked:
                self._write_lock.release()
        if is_written:
            self._commanded_setpoint = None
            self._was_reachable = True
        return is_written

    # -- frames --------------------------------------------------------------
    def _build_settings_frame(self):
        """`(frame_bytes, setpoint)`, or `Refused` naming the field.

        Validated here as well as in `Panel.run`, because a command can be run
        with no inputs — from a script, from the Web API, from a re-run after
        a confirmation — and must still never build a frame out of a value
        the firmware would mis-parse.
        """
        values = {}
        for name in self.FRAME_FIELDS:
            is_valid, value = self.PARAMS[name].parse(getattr(self, name, None))
            if not is_valid:
                self._refuse(f"Settings not sent: {value}")
            values[name] = value
        payload = ",".join((
            self._field(values["setpoint"]),
            self._ramp_text(2),
            self._field(values["p_term"]),
            self._field(values["i_term"]),
            self._field(values["d_term"]),
            self._field(values["offset"]),
        ))
        if len(payload) > self.MAX_PAYLOAD_LENGTH:
            self._refuse(
                f"Settings not sent: {len(payload)} characters is more than "
                f"the {self.MAX_PAYLOAD_LENGTH} the controller can read "
                "before it starts dropping the tail. Use fewer decimals.")
        return f"<{payload}>".encode("utf-8"), values["setpoint"]

    def _heater_off_frame(self):
        """`<0,ramp:.1f,0,0,0,offset>` — the four zeros are literal, exactly
        as in the old `stop()`, so gains go to zero and `newdelay` with them,
        which is what actually cuts the element."""
        return (f"<0,{self._ramp_text(1)},0,0,0,"
                f"{self._field(self.offset)}>").encode("utf-8")

    def _ramp_text(self, decimals):
        """Seconds per degree, in the firmware's own unit — it is shown on the
        board's LCD as `RR = {spdelay}s/C` unconverted, so what is entered
        here is what the physical display reads.

        Never raises: the stop frame is built from whatever is stored, and a
        stop must not be blocked by a bad ramp rate.
        """
        try:
            rate = float(self.ramp_rate)
        except (TypeError, ValueError):
            return "0"
        if math.isnan(rate) or math.isinf(rate) or rate < 0:
            return "0"
        try:
            return f"{rate:.{decimals}f}"
        except (OverflowError, ValueError):
            return "0"

    @staticmethod
    def _field(value):
        """One frame field, in its shortest exact decimal form.

        The old model stored these as the operator's raw *text* and
        interpolated it unchanged; typed parameters mean this model holds a
        float, so the text has to be regenerated. The shortest round-tripping
        form is used because the alternatives are worse: `Param.format`'s
        fixed decimals lengthen the frame toward the 31-character cliff, and
        plain `str(float)` renders zero as "0.0" where the stop frame's
        literal zeros are "0".

        Consequence, stated rather than hidden: an operator who types "2.0"
        sends `2`, and the old ".1" default sends `0.1`. `atof` reads both
        spellings identically and the firmware is untouched.
        """
        number = float(value)
        if number == 0:
            return "0"           # also folds -0.0
        text = repr(number)
        if "e" in text or "E" in text:
            text = f"{number:.6f}".rstrip("0").rstrip(".")
        if text.endswith(".0"):
            text = text[:-2]
        return text

    def _refuse(self, reason):
        """Raise `Refused`, and say why in the diagnostic log first.

        `Panel.run` publishes the reason as `events.info`; this records it
        with the thread and the traceback context the bench wants, and keeps
        every refusal in this model going through one place.
        """
        events.debug("Refused", reason, source=self.NAME)
        raise Refused(reason)

    # -- reader ---------------------------------------------------------------
    def _backoff_wait(self, seconds):
        """was TemperatureSystem._backoff_wait

        Wait out a backoff, but return the moment `close()` asks. True means
        stop reading. `Event.wait` already returns True when the flag is set,
        which is exactly the shutdown case, so the two conditions collapse
        into one call and no backoff can outlive a shutdown.
        """
        return self._threads_stop.wait(seconds)

    def _read_loop(self):
        """was TemperatureSystem.read_serial_data

        TEMP-2: a real exponential backoff (0.1 -> 0.2 -> ... -> 2.0 s) and
        indefinite retry, instead of five fixed 0.1 s retries and a `break`
        that killed the reader for the rest of the session while the UI went
        on showing a frozen temperature as though it were live.

        A port that merely reports closed counts as a failure too. It used to
        reset the counter and spin in silence, so a link that dropped was
        indistinguishable from one that was working.

        Nothing here publishes per iteration: transitions only, and every
        in-loop diagnostic carries `every=`.

        Boot grace: a failure inside `BOOT_GRACE_SEC` of `open()`, before the
        first reading, is logged at debug only, and a link that comes up
        inside the window says nothing at all. `is_warned` is what "Resumed"
        answers to, so a warning is always paired with its recovery and a
        non-event produces neither.
        """
        failures, is_warned = 0, False
        while not self._threads_stop.is_set():
            self._touch()   # the reader is alive; a frozen value shows in `age` of the reading
            line, why, error = None, None, None
            try:
                if not self.port.is_open:
                    why = f"the port is {self.port.status}"
                else:
                    self._send_reset_once()
                    line = self.port.read_line(timeout=self.READ_TIMEOUT)
            except Exception as exc:
                why, error = f"{type(exc).__name__}: {exc}", exc

            if why is None:
                if line:
                    self._parse_line(line)
                    if is_warned or self._is_link_lost:
                        events.info("Temperature Reading Resumed",
                                    f"after {failures} failed read(s)",
                                    source=self.NAME)
                    if failures:
                        events.debug("Reader Recovered",
                                     f"failure count reset from {failures}",
                                     source=self.NAME)
                    failures, is_warned, self._is_link_lost = 0, False, False
                    self._boot_grace_until = None    # the board is up
                # An idle read is not a failure: the board sends ~1.7 lines/s,
                # so most passes legitimately see nothing. The floor is what
                # keeps a non-blocking port from free-spinning at GB/s.
                if self._backoff_wait(self.READ_FLOOR):
                    break
                continue

            failures += 1
            if self._is_booting():
                events.debug("Reader Boot Grace",
                             f"failure {failures} inside the "
                             f"{self.BOOT_GRACE_SEC:g} s boot grace, not "
                             f"reported: {why}", source=self.NAME,
                             exception=error, every=1.0)
            elif not is_warned:
                is_warned = True
                events.debug("Temperature Read Error", why, source=self.NAME,
                             exception=error)
                events.warn("Temperature Read Error", "A temperature reading "
                            "failed. Retrying; check the controller's cable "
                            "if this repeats.", source=self.NAME,
                            exception=error)
            if (is_warned and failures > self.PERSISTENT_AFTER
                    and not self._is_link_lost):
                self._is_link_lost = True
                events.warn(events.TEMPERATURE_DISCONNECTED,
                            f"No temperature reading after {failures} "
                            "attempts. Still retrying; check the controller's "
                            "cable and power.", source=self.NAME, ack=True)
            backoff = min(self.MIN_BACKOFF * 2 ** (failures - 1),
                          self.MAX_BACKOFF)
            events.debug("Reader Backoff",
                         f"failure {failures}: {why}; waiting {backoff:.2f} s",
                         source=self.NAME, exception=error, every=1.0)
            if self._backoff_wait(backoff):
                break
        events.debug("Reader Loop Left",
                     f"after {failures} consecutive failure(s)", source=self.NAME)

    def _send_reset_once(self):
        """Put the board in its power-on state the first time the port is
        seen open, as the old constructor did.

        It happens here rather than in `open()` because `SerialPort.open()` is
        non-blocking: the handshake finishes on a worker, so at `open()` time
        there is usually nothing to write to yet. Skipped if the operator got
        a settings frame in first — a housekeeping frame must never undo a
        command.
        """
        if self._has_sent_reset:
            return
        self._was_reachable = True
        with self._write_lock:
            if self._commanded_setpoint is None:
                self.port.write(self.RESET_FRAME)
                events.debug("Reset Frame",
                             f"{self.RESET_FRAME.hex(' ')}  ({self.RESET_FRAME!r})",
                             source=self.NAME)
            self._has_sent_reset = True

    def _parse_line(self, line):
        """was TemperatureSystem.process_raw_data

        `timer , temperature , setpoint`. Anything else — the `DEV: t`
        handshake answer, a half line, a boot banner — is ignored.
        """
        if isinstance(line, bytes):
            line = line.decode("utf-8", errors="ignore")
        text = line.strip()
        if not text:
            return
        fields = text.split(",")
        if len(fields) < 3:
            events.debug("Parse Skipped", f"{len(fields)} field(s): {text!r}",
                         source=self.NAME, every=5.0)
            return
        try:
            seconds, temperature, setpoint = (float(f.strip())
                                              for f in fields[:3])
        except ValueError:
            events.debug("Parse Failed", f"not three numbers: {text!r}",
                         source=self.NAME, every=5.0)
            return
        with self._history_lock:
            for series, value in ((self._times, seconds),
                                  (self._temperatures, temperature),
                                  (self._setpoints, setpoint)):
                series.append(value)
                del series[:-self.HISTORY_LENGTH]
            self._latest = temperature
        pending = self._off_pending
        if pending is not None and setpoint <= self.OFF_SETPOINT_MAX:
            self._off_pending = None
            pending["landed"].set()
        self._touch()
        events.debug("Reading", f"t={seconds:g}s temp={temperature:.2f}C "
                     f"sp={setpoint:.2f}C", source=self.NAME, every=5.0)
