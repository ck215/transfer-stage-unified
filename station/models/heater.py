"""The temperature controller. Was `TemperatureSystem`.

The firmware is untouched, so the two frames this model sends are the old
ones, byte for byte:

    settings   <setpoint,ramp:.2f,p,i,d,offset>
    heater off <0,ramp:.1f,0,0,0,offset>          (halt, estop)
    reset      <0,6.0,0,0,0,0>                    (first open, disable, close)

The firmware reads a frame into a 32-byte buffer and splits it with `strtok`.
Two consequences are enforced here rather than hoped for: an empty field
shifts every later field left (so an invalid value refuses the whole frame),
and a payload longer than 31 characters loses its tail (so it refuses too).
"""
import math
import threading
import time

from station import schema as sch
from station.devices.serial_port import SerialPort
from station.events import events
from station.model import Model
from station.param import Param
from station.result import Refused


class Heater(Model):
    """Was TemperatureSystem. Owns a SerialPort.

    Absorbs: TemperatureSystem
    """

    NAME = "Temperature Controller"
    IDENTITY = "t"
    NEEDS_PORT = True
    NEEDS_GAMEPAD = False

    BAUD_RATE = 115200

    #: Bounds (TEMP-13: a typo of 2000 for 200 used to be sent unmodified).
    #: PROVISIONAL: nothing in the firmware or the old model states a limit,
    #: so these are wide enough not to refuse real work and are the owner's
    #: to tighten. The MAX6675 itself tops out at 1024 C.
    MAX_SETPOINT = 300.0

    PARAMS = {
        "setpoint": Param("setpoint", "float", default=0, minimum=0,
                          maximum=MAX_SETPOINT, decimals=1, unit="C",
                          label="Setpoint"),
        "ramp_rate": Param("ramp_rate", "float", default=10, minimum=0,
                           maximum=3600, decimals=2, unit="s/C",
                           label="Ramp Rate (s/°C)"),
        "p_term": Param("p_term", "float", default=2.0, minimum=0,
                        maximum=1000, decimals=3,
                        label="Proportional Term (P)"),
        "i_term": Param("i_term", "float", default=0.5, minimum=0,
                        maximum=1000, decimals=3, label="Integral Term (I)"),
        "d_term": Param("d_term", "float", default=0.1, minimum=0,
                        maximum=1000, decimals=3, label="Derivative Term (D)"),
        "offset": Param("offset", "float", default=0, minimum=-100,
                        maximum=100, decimals=2, unit="C", label="Offset"),
    }
    FRAME_FIELDS = ("setpoint", "ramp_rate", "p_term", "i_term", "d_term",
                    "offset")

    #: The board's power-on state. Old `__init__` and old `close()` sent it.
    RESET_FRAME = b"<0,6.0,0,0,0,0>"
    #: `receivedChars[32]` in temp_controller.ino, one byte of it the NUL.
    MAX_PAYLOAD_LENGTH = 31

    HISTORY_LENGTH = 200
    READ_TIMEOUT = 0.25          # s one read may hold the reader
    READ_FLOOR = 0.01            # s between reads: never above ~100 Hz
    MIN_BACKOFF, MAX_BACKOFF = 0.1, 2.0
    PERSISTENT_AFTER = 5         # read failures before the link is called lost
    READER_JOIN_TIMEOUT = 1.5
    #: s a stop waits for an Enter Settings. Short, so that this plus the
    #: port's own priority wait still fits inside ESTOP_BUDGET.
    WRITE_LOCK_TIMEOUT = 0.02
    FLUSH_TIMEOUT = 1.0

    def __init__(self, port=None, gamepad=None, sim=False):
        """was TemperatureSystem.__init__

        `port` is a port name, or an already-built SerialPort (Setup may
        have opened one to read its identity byte). `gamepad` is accepted
        because every Model takes it; a heater has no use for one.
        """
        super().__init__()
        if hasattr(port, "write"):
            self.port = port
        else:
            self.port = SerialPort("SIM" if sim or port in (None, "None")
                                   else port, baud_rate=self.BAUD_RATE)
        self._history_lock = threading.Lock()
        self._times, self._temperatures, self._setpoints = [], [], []
        self._latest = None

        # `apply_settings` and the stops each build a frame and write it.
        # Under the Web view they run on concurrent request threads, so the
        # build-and-write is one critical section (TEMP-7).
        self._write_lock = threading.Lock()
        # Bumped by every stop before its frame is built. A settings frame
        # remembers the value it started with and is dropped at the wire if
        # a stop has happened since (TEMP-17). The latch alone cannot do
        # this: a plain Stop does not latch.
        self._stop_generation = 0
        # What the board was last SENT, not what is in the box: typing a
        # number has not started heating. None = nothing non-zero since the
        # last stop.
        self._commanded_setpoint = None
        self._has_sent_reset = False
        # Has a frame ever had somewhere to go? Decides whether a failed
        # heater-off on the way out is news or just a port that never opened.
        self._was_reachable = False

        self._reader = None
        # An Event, not a bool: a reader parked in a 2 s backoff must leave
        # the moment close() asks, or it outlives the join (TEMP-2).
        self._reader_wake = threading.Event()
        self._is_link_lost = False

    # -- devices and lifecycle --------------------------------------------
    @property
    def devices(self):
        return [self.port]

    def _start_threads(self):
        if self._reader is not None and self._reader.is_alive():
            return
        self._reader_wake.clear()
        self._reader = threading.Thread(target=self._read_loop, daemon=True,
                                        name="heater-reader")
        self._reader.start()

    def _stop_threads(self):
        """Ask the reader to leave and wait for it, with a bound. It runs
        before the port closes, so the reader is never inside `read_line` on
        a descriptor closing under it (TEMP-11)."""
        self._reader_wake.set()
        reader = self._reader
        if (reader is None or reader is threading.current_thread()
                or not reader.is_alive()):
            return
        reader.join(self.READER_JOIN_TIMEOUT)
        if reader.is_alive():
            events.warn("Reader Still Running", "the temperature reader did "
                        f"not leave the port within {self.READER_JOIN_TIMEOUT}"
                        " s; closing anyway", source=self.NAME)

    # -- what the operator reads ------------------------------------------
    @property
    def temperature(self):
        """The reading as text, and the truth about where it comes from. A
        dropped link must never go on showing its last good value as live,
        and "N/A" must never be shown for a reading that cannot arrive
        (TEMP-10). How OLD a live reading is comes from the inherited
        `state["age"]`."""
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

        `(times, temperatures, setpoints)`, a snapshot.
        """
        with self._history_lock:
            return (list(self._times), list(self._temperatures),
                    list(self._setpoints))

    @property
    def series(self):
        """was TemperatureSystem.temp_series
        same name as RedMonitor.series: one name for 'the plot data'
        """
        with self._history_lock:
            return {"x": list(self._times), "y": list(self._temperatures)}

    @property
    def is_active(self):
        """Heating: a non-zero setpoint has been sent and not since stopped."""
        return bool(self._commanded_setpoint)

    @property
    def schema(self):
        params = self.PARAMS
        return sch.schema(
            sch.section(
                "Temperature Readings",
                sch.readonly("Current Temperature:", "temperature"),
                sch.readonly("Connection:", "connection", role="info"),
                sch.plot("Temperature over time", "series",
                         x_label="time (s)", y_label="temperature (°C)"),
            ),
            sch.section(
                "Control Parameters",
                *[sch.entry(params[name].label + ":", name, params[name])
                  for name in self.FRAME_FIELDS],
            ),
            sch.section(
                "System Control",
                # Every field of the frame travels with the command and is
                # validated as a set: `strtok` does not see an empty field,
                # it sees the next one.
                sch.button("Enter Settings", "apply_settings",
                           inputs=self.FRAME_FIELDS, role="go"),
                sch.button("Stop System", "halt", role="danger"),
            ),
            self._safety_section(),
        )

    # -- commands ----------------------------------------------------------
    def apply_settings(self):
        """was TemperatureSystem.send_settings
        raises Refused instead of returning None

        Returns the setpoint that was sent.
        """
        self._guard("Settings")
        # Taken before anything is read: a stop that lands at ANY point after
        # this command began supersedes it.
        generation = self._stop_generation
        frame, setpoint = self._build_settings_frame()
        if not self.port.is_open:
            raise Refused(f"Settings not sent: {self.NAME} is not connected "
                          f"({self.port.status})")

        def _is_superseded():
            return self._estop.is_set() or self._stop_generation != generation

        with self._write_lock:
            # The check that counts. The guard above is a check-then-act, and
            # this write can wait on the port for as long as the reader's
            # read holds it while a stop forces its frame past. The port asks
            # again with every lock it takes already held (TEMP-7, TEMP-17).
            is_written = self.port.write(frame, abort_if=_is_superseded)
            if is_written:
                self._commanded_setpoint = setpoint or None
                self._was_reachable = True
        if not is_written:
            self._guard("Settings")
            raise Refused("Settings not sent: a stop arrived while the frame "
                          "was waiting for the port")
        events.info("Settings Sent", frame.decode("ascii"), source=self.NAME)
        return setpoint

    def _halt_hardware(self):
        """Heater off, on the priority lane. True when the frame landed."""
        return self._send_heater_off(self._heater_off_frame)

    def disable(self):
        """The board's power-on state, drained to the wire. `Model.close()`
        runs this after `halt()`, which is the old teardown byte for byte:
        the stop frame, then the reset frame, then a bounded flush, because
        closing a POSIX port may discard what was written and not yet sent
        (TEMP-11). The firmware has no watchdog: a frame that does not go
        out leaves the heater at its last setpoint for as long as it has
        power, so failing here is reported as an unconfirmed stop."""
        if not self.port.is_open and not self._was_reachable:
            return False     # never connected: `state` has said so all along
        is_off = self._send_heater_off(lambda: self.RESET_FRAME)
        try:
            is_drained = self.port.flush(self.FLUSH_TIMEOUT) is not False
        except Exception as exc:
            is_drained = False
            events.warn("Flush Failed", str(exc), source=self.NAME,
                        exception=exc)
        if not (is_off and is_drained):
            # ack=False: this is on the close path, and a popup there blocks
            # the exit.
            events.error("Heater Off Not Delivered", "The heater-off frame "
                         "could not be confirmed on the wire. The heater may "
                         "still be at its last setpoint.", source=self.NAME,
                         ack=False)
        return is_off and is_drained

    def _send_heater_off(self, build_frame):
        """The one stop path. Supersede first, so a settings frame already
        waiting for the port is dropped at the wire rather than written
        after this one. Waits only briefly for `_write_lock`: a stop that
        cannot get a lock is worse than an unsynchronised one, and the port
        still keeps the two frames from interleaving."""
        self._stop_generation += 1
        self.setpoint = 0
        is_locked = self._write_lock.acquire(timeout=self.WRITE_LOCK_TIMEOUT)
        try:
            is_written = bool(self.port.write(build_frame(), priority=True))
        except Exception as exc:
            events.warn("Heater Off Not Sent", str(exc), source=self.NAME,
                        exception=exc)
            return False
        finally:
            if is_locked:
                self._write_lock.release()
        if is_written:
            self._commanded_setpoint = None
            self._was_reachable = True
        return is_written

    # -- frames ------------------------------------------------------------
    def _build_settings_frame(self):
        """`(frame, setpoint)`, or Refused naming the field. Validated here
        as well as in `Panel.run`, because a command can be run with no
        inputs and must still never build a frame from a bad value."""
        values = {}
        for name in self.FRAME_FIELDS:
            is_valid, value = self.PARAMS[name].parse(getattr(self, name, None))
            if not is_valid:
                raise Refused(f"Settings not sent: {value}")
            values[name] = value
        payload = (f"{self.setpoint},{self._ramp_text(2)},{self.p_term},"
                   f"{self.i_term},{self.d_term},{self.offset}")
        if len(payload) > self.MAX_PAYLOAD_LENGTH:
            raise Refused(
                f"Settings not sent: {len(payload)} characters is more than "
                f"the {self.MAX_PAYLOAD_LENGTH} the controller can read. Use "
                "fewer decimal places.")
        return f"<{payload}>".encode("utf-8"), values["setpoint"]

    def _heater_off_frame(self):
        return f"<0,{self._ramp_text(1)},0,0,0,{self.offset}>".encode("utf-8")

    def _ramp_text(self, decimals):
        """Seconds per degree, the firmware's own unit. Never raises: the
        stop frame is built from whatever is stored."""
        try:
            rate = float(self.ramp_rate)
        except (TypeError, ValueError):
            rate = 0.0
        if math.isnan(rate) or math.isinf(rate):
            rate = 0.0
        return f"{rate:.{decimals}f}" if rate >= 0 else "0"

    # -- reader ------------------------------------------------------------
    def _backoff_wait(self, seconds):
        """was TemperatureSystem._backoff_wait

        Wait, but return the moment close() asks. True = stop reading.
        """
        return self._reader_wake.wait(seconds)

    def _read_loop(self):
        """was TemperatureSystem.read_serial_data

        Backs off 0.1 -> 0.2 -> ... -> 2 s and never gives up (TEMP-2). A
        port that is simply not open counts as a failure too; it used to
        reset the counter and spin in silence. Publishes on transitions
        only, never per iteration.
        """
        failures = 0
        while not self._reader_wake.is_set():
            try:
                if not self.port.is_open:
                    failures += 1
                else:
                    self._send_reset_once()
                    line = self.port.read_line(timeout=self.READ_TIMEOUT)
                    if line:
                        if isinstance(line, bytes):
                            line = line.decode("utf-8", errors="ignore")
                        self._parse_line(line)
                        if self._is_link_lost:
                            events.info("Temperature Reading Resumed",
                                        f"after {failures} failed reads",
                                        source=self.NAME)
                        failures, self._is_link_lost = 0, False
                    if self._backoff_wait(self.READ_FLOOR):
                        break
                    continue
            except Exception as exc:
                failures += 1
                if failures == 1:
                    events.warn("Temperature Read Error", str(exc),
                                source=self.NAME, exception=exc)
            if failures > self.PERSISTENT_AFTER and not self._is_link_lost:
                self._is_link_lost = True
                events.warn("Temperature Disconnected", "no reading after "
                            f"{failures} attempts; still retrying",
                            source=self.NAME)
            backoff = min(self.MIN_BACKOFF * 2 ** (failures - 1),
                          self.MAX_BACKOFF)
            if self._backoff_wait(backoff):
                break

    def _send_reset_once(self):
        """Put the board in its power-on state the first time the port is
        seen open, as the old constructor did. `open()` does not block, so
        this happens here rather than there. Skipped if the operator got a
        settings frame in first: it must not undo a command."""
        if self._has_sent_reset:
            return
        self._was_reachable = True
        with self._write_lock:
            if self._commanded_setpoint is None:
                self.port.write(self.RESET_FRAME)
            self._has_sent_reset = True

    def _parse_line(self, line):
        """was TemperatureSystem.process_raw_data

        `timer , temperature , setpoint`. Anything else is ignored.
        """
        fields = line.strip().split(",")
        if len(fields) < 3:
            return
        try:
            seconds, temperature, setpoint = (float(f.strip()) for f in fields[:3])
        except ValueError:
            return
        with self._history_lock:
            for series, value in ((self._times, seconds),
                                  (self._temperatures, temperature),
                                  (self._setpoints, setpoint)):
                series.append(value)
                del series[:-self.HISTORY_LENGTH]
            self._latest = temperature
        self._touch()
