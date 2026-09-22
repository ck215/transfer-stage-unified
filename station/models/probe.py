"""Probes: the mode machine, the move/jog frames, position sampling and the
motion interlock.

Was `BaseProbe` (1682 lines) plus the probe-shaped half of the old `serial`
transport. What is gone, by owner ruling: G-code scripts and run generations,
web client liveness, and the gamepad plumbing (which is the Gamepad device's
job now). What is inherited rather than re-implemented: the FULL STOP latch,
the bounded stop, the ordered close, the fault record — all from `Model`.

**Firmware is untouched.** Every byte this module puts on the wire is the byte
`src/` sends today:

    enable      b"e"
    disable     b"d"
    move/step   b"<x_step>,<y_step>,<z_step>,0,<full>,<slow>,<brake>,"
                b"<x_dist>,<y_dist>,<z_dist>,<manual>,<auto>\\n"
    zero frame  b"0,0,0,0,0,0,0,0,0,0,0,0\\n"
    jog         42-byte struct `<BBffffffffff`, START_MARKER first
    kill coils  b"k\\n"   (no firmware handles it; see can_kill_coils, D-7)

`tests/station/test_probe_frames.py` drives the *old* classes in simulator
mode and asserts these are byte-identical, per probe type, so a refactor here
cannot quietly change what the boards receive.

What did change is the *lane*: the stop path (zero frame, `'d'`, `'k\\n'`)
goes out on the priority lane and never waits on a blocking lock, and every
motion write carries `abort_if=self._estop.is_set` so the latch is checked
inside the transport's lock rather than before it.
"""
import enum
import struct
import threading
import time

from station import schema as sch
from station.devices import gamepad as gamepad_device
from station.devices import serial_port as serial_device
from station.events import events
from station.model import Model
from station.param import Param
from station.result import Refused


class ProbeMode(enum.Enum):
    """The modes a probe can be in. Exactly one at a time (RC-3).

    This replaced four independently-writable booleans (`system_enabled`,
    `auton_flag`, `manual_flag`, `is_stepping`) that were set by different
    methods, threads and views with no single owner of the hardware side
    effects. The names survive as read-only derived properties.

    `FAULT` is a mode, not a flag beside one: when a disable is not confirmed
    the hardware state is genuinely unknown, and the honest answer is neither
    "enabled" nor "disabled" but "treat this as live until resolved".
    """
    DISABLED = "disabled"
    IDLE = "idle"
    AUTO = "autonomous"
    MANUAL = "manual"
    FAULT = "fault"


#: Modes in which the coils may be energized. FAULT is included deliberately:
#: a failed disable leaves the board in an unknown state, and the safe reading
#: of unknown is "possibly live".
_ENERGIZED = frozenset({ProbeMode.IDLE, ProbeMode.AUTO, ProbeMode.MANUAL,
                        ProbeMode.FAULT})

#: Modes reached only through a *confirmed* enable (invariant I-3.1).
_ARMED = frozenset({ProbeMode.IDLE, ProbeMode.AUTO, ProbeMode.MANUAL})

#: The modes in which a motion parameter may not be edited. One tuple, read by
#: the schema entries and by the property setters, so what a view greys out and
#: what the model refuses cannot drift apart (DC-6).
_MOTION_GATE = ("autonomous", "manual")

#: 42-byte jog packet: start marker, packet type, then ten floats.
PACKET_FORMAT = "<BBffffffffff"
START_MARKER = 0xAA


class Probe(Model):
    """A three-axis probe: one SerialPort, one Gamepad, one mode."""

    NAME = "Probe"
    IDENTITY = None
    NEEDS_PORT = True
    NEEDS_GAMEPAD = True

    PARAMS = {p.name: p for p in (
        Param("x_step", "int", default=16, minimum=1, label="X Step Size"),
        Param("y_step", "int", default=16, minimum=1, label="Y Step Size"),
        Param("z_step", "int", default=16, minimum=1, label="Z Step Size"),
        Param("x_dist", "int", default=0, label="Target X Dist"),
        Param("y_dist", "int", default=0, label="Target Y Dist"),
        Param("z_dist", "int", default=0, label="Target Z Dist"),
        Param("full_speed", "int", default=400, minimum=1,
              label="Autonomous Speed"),
        Param("man_full_speed", "int", default=400, minimum=1,
              label="Manual Speed"),
        Param("slow_speed", "int", default=0, label="Brake Speed (Slow)"),
        Param("brake_distance", "int", default=0,
              label="Brake Distance (steps)"),
    )}

    #: Single-byte control commands this board's firmware is *observed* to
    #: handle, read out of `firmware/*/*.ino` (SERIAL-10). `'k'` has no branch
    #: in any .ino, on any board; whether the protocol should grow one is owner
    #: decision D-7. Nothing here changes a byte on the wire — the only thing
    #: it buys is that the model stops asserting an outcome the firmware never
    #: produced.
    FIRMWARE_CONTROL_BYTES = frozenset({b"d", b"e", b"s"})

    PACKET_FORMAT = PACKET_FORMAT

    #: The one documented rate for each model-owned loop (RC-4).
    JOG_INTERVAL = 0.02       # s -> 50 Hz, the manual jog stream
    SAMPLE_INTERVAL = 0.01    # s -> ~100 Hz drain of a 10 Hz POS stream
    #: How many lines one drain pass will take before yielding. The firmware
    #: prints one POS line per 100 ms (PRINT_INTERVAL); a cap keeps a flooded
    #: buffer from holding the transaction lock long enough to delay a jog
    #: frame or a priority write.
    MAX_DRAIN_LINES = 32

    #: How long the position must stay unchanged before an autonomous move is
    #: considered arrived.
    STEP_SETTLE = 1.0

    #: Idle interlock. Overridable by tests to avoid a five-minute wait.
    INTERLOCK_POLL_INTERVAL = 5.0
    INTERLOCK_TIMEOUT = 300.0

    def __init__(self, port=None, gamepad=None, sim=False):
        # Mode and the gated parameter store come first: `Panel.__init__`
        # assigns every declared default through the property setters below,
        # and those consult the mode. Construction always runs in DISABLED,
        # which no entry's `disabled_when` names, so nothing is refused here.
        self._mode = ProbeMode.DISABLED
        self._mode_lock = threading.RLock()
        self._param_store = {}
        self._gates = {}
        super().__init__()

        self.port = self._build_port(port, sim)
        self.gamepad = self._build_gamepad(gamepad)
        self._gamepad_name = gamepad if isinstance(gamepad, str) else None

        # Position, sampled from the stream the firmware sends unasked.
        self._position = (0, 0, 0)
        self._position_time = None
        self._velocity = (0.0, 0.0, 0.0)
        self._samples_seen = 0

        # Autonomous "stepping" is a *timed sub-state*, not a fifth boolean
        # (RC-3 item 2). `is_stepping` used to be set by the step command and
        # cleared only by a stop, so a move that finished normally left it True
        # forever -- and the interlock deferred on it, which is how an
        # energized probe sat idle indefinitely (STEPPER-6, DC-1).
        self._moving_deadline = None

        # Idle interlock. A fresh Event and generation per arming (STEPPER-7):
        # one reused Event meant a watchdog stopped by one disable stayed
        # stopped for the next enable.
        self._activity_time = time.monotonic()
        self._interlock_stop = threading.Event()
        self._interlock_stop.set()
        self._interlock_thread = None
        self._interlock_generation = 0

        self._threads_stop = threading.Event()
        self._threads_stop.set()
        self._jog_thread = None
        self._sample_thread = None
        self._coil_kill_reported = False

    # -- devices ----------------------------------------------------------
    def _build_port(self, port, sim):
        """A SerialPort, or whatever port-like object was handed in.

        Setup passes a port *name* (`"COM3"`, `"SIM"`, or None); tests pass a
        recording fake. Anything that is not a string is taken to be the
        device itself, which is what keeps this testable while
        `devices/serial_port.py` is still someone else's work in progress.
        """
        if port is None or isinstance(port, str):
            name = "SIM" if sim else port
            return serial_device.SerialPort(name)
        return port

    def _build_gamepad(self, gamepad):
        if gamepad is None or isinstance(gamepad, str):
            hub = getattr(gamepad_device, "hub", None)
            if hub is None:
                return gamepad_device.Gamepad(self.NAME)
            return gamepad_device.Gamepad(self.NAME, hub=hub)
        return gamepad

    @property
    def devices(self):
        return [d for d in (self.port, self.gamepad) if d is not None]

    def open(self):
        super().open()
        if self._gamepad_name and self._gamepad_name != "None":
            try:
                self.gamepad.bind(self._gamepad_name)
            except Exception as exc:
                events.warn("Gamepad Bind Failed",
                            f"{self._gamepad_name!r}: {exc}", source=self.NAME,
                            exception=exc)
        events.debug("Devices Open", f"port={getattr(self.port, 'status', '?')} "
                     f"gamepad={self.gamepad_name}", source=self.NAME)

    # -- threads ----------------------------------------------------------
    def _start_threads(self):
        self._threads_stop.clear()
        if self._sample_thread is None or not self._sample_thread.is_alive():
            self._sample_thread = threading.Thread(
                target=self._sample_loop, daemon=True,
                name=f"sample-{self.NAME}")
            self._sample_thread.start()
        if self._jog_thread is None or not self._jog_thread.is_alive():
            self._jog_thread = threading.Thread(
                target=self._jog_loop, daemon=True, name=f"jog-{self.NAME}")
            self._jog_thread.start()
        events.debug("Threads Started", "sample ~100 Hz, jog 50 Hz",
                     source=self.NAME)

    def _stop_threads(self):
        self._threads_stop.set()
        self._stop_interlock()
        for thread in (self._jog_thread, self._sample_thread):
            if thread is not None and thread.is_alive():
                thread.join(timeout=1.0)
        events.debug("Threads Stopped", "sample and jog loops joined",
                     source=self.NAME)

    # -- mode -------------------------------------------------------------
    @property
    def mode(self):
        return self._mode

    @property
    def mode_name(self):
        return self._mode.value

    @property
    def is_enabled(self):
        return self._mode in _ENERGIZED

    @property
    def is_auto(self):
        return self._mode is ProbeMode.AUTO

    @property
    def is_manual(self):
        return self._mode is ProbeMode.MANUAL

    @property
    def is_moving(self):
        """True while an autonomous move is believed to still be moving.

        Expires by deadline: `STEP_SETTLE` seconds after the position last
        changed. Arrival, observed -- not a duration computed from step counts
        and a speed whose units this layer does not get to assume.
        """
        if self._mode is not ProbeMode.AUTO:
            return False
        deadline = self._moving_deadline
        return deadline is not None and time.monotonic() < deadline

    @property
    def is_active(self):
        return self.is_moving or self._mode is ProbeMode.MANUAL

    def set_mode(self, target):
        """The one public way to change mode.

        Replaces `toggle_manual`, `toggle_auton`, `enter_auton`,
        `enter_manual`, `toggle_enable` and `full_stop`. The schema toggles
        pass the target as `on_args` / `off_args`, so one command serves them
        all and no view can invent a seventh way in.
        """
        return self._set_mode(self._to_mode(target), "operator command")

    def enable(self):
        return self._set_mode(ProbeMode.IDLE, "enable")

    def disable(self):
        return self._set_mode(ProbeMode.DISABLED, "disable")

    def _to_mode(self, target):
        if isinstance(target, ProbeMode):
            return target
        text = str(target).strip().lower()
        for mode in ProbeMode:
            if text in (mode.value, mode.name.lower()):
                return mode
        self._refuse(f"{target!r} is not a mode of {self.NAME}")

    def _set_mode(self, target, reason, quiesce=True):
        """Change mode and own the hardware side effects. The only writer.

        The ordering is the safety property, and it is why this is one
        function rather than six:

        * **Leaving any mode sends a zeroed motion frame first**, so the "last
          non-zero command stands and the axis keeps moving" class cannot
          recur (RC-3 item 3).
        * **Arming requires a confirmed enable** (I-3.1), and MANUAL
          additionally requires a bound gamepad *before* the enable, because
          the enable energizes coils and nothing walks that back (I-3.2).
        * **De-energizing never skips a step because an earlier one raised.**

        D-2 (owner): leaving a mode disables the coils. There is deliberately
        no "stop motion but hold torque" target.
        """
        with self._mode_lock:
            previous = self._mode
            if target is ProbeMode.DISABLED:
                return self._deenergize(reason)
            if target is ProbeMode.FAULT:
                self._refuse("fault is not a mode an operator may select")
            if target is ProbeMode.MANUAL and not self._is_gamepad_bound:
                # Before the enable, never after: checking afterwards can
                # revert the Python flag, but the firmware has already been
                # told to energize.
                self._refuse("Manual mode refused: no gamepad is bound")
            self._guard(f"Mode change to {target.value}")

            self._energize(reason)
            self._mode = target
            self._moving_deadline = None
            self._clear_fault()
            self._start_interlock()
            self._touch()
            events.debug("Mode", f"{previous.value} -> {target.value} ({reason})",
                         source=self.NAME)
            # Entering a mode starts from rest. `quiesce=False` is for the one
            # caller entering a mode *in order to move* -- `step` -- where a
            # zeroed frame immediately followed by the move is a wasted write
            # and a stop-then-go hiccup at the board. Leaving a mode always
            # quiesces: that half lives in `_deenergize`, where no caller can
            # opt out of it.
            if quiesce:
                self._send_zero_frame("entering " + target.value)
            return target.value

    def _energize(self, reason):
        """Send the hardware enable unless the probe is already armed.

        The mode moves into an armed state **only on a successful write**. It
        used to move even when the write raised, because the transport
        swallowed the exception -- so the UI showed the system armed when
        nothing had reached the board.
        """
        if self._mode in _ARMED:
            return True
        if self.port is None:
            return True
        try:
            written = self.port.write(b"e", abort_if=self._estop.is_set)
        except Exception as exc:
            events.warn("Enable Failed", f"the enable did not reach the board: "
                        f"{exc}", source=self.NAME, exception=exc)
            self._refuse(f"enable failed: {exc}")
        events.debug("Frame", f"enable {b'e'.hex()} written={bool(written)} "
                     f"({reason})", source=self.NAME)
        if not written:
            self._refuse("enable aborted: FULL STOP is latched")
        return True

    def _deenergize(self, reason):
        """Stop motion, then de-energize. Each step isolated from the last."""
        previous = self._mode
        self._moving_deadline = None
        self._stop_interlock()
        try:
            self._send_zero_frame("leaving " + previous.value)
        except Exception as exc:
            # A zero-motion frame that failed must not prevent the hardware
            # disable below.
            events.debug("Stop Frame Failed", f"continuing to disable: {exc}",
                         source=self.NAME, exception=exc)
        if self.port is not None:
            try:
                # No `abort_if` here, ever: a disable is the thing the latch
                # wants to happen, not a motion command it supersedes.
                written = self.port.write(b"d", priority=True)
            except Exception as exc:
                # The disable did not reach the board, so the coils may still
                # be energized. Recording DISABLED here would report the
                # system safe on the strength of a command that failed.
                self._enter_fault(f"disable not confirmed — coils may be "
                                  f"energized: {exc}")
                return self._mode.value
            events.debug("Frame", f"disable {b'd'.hex()} written={bool(written)} "
                         f"({reason})", source=self.NAME)
            if not written:
                self._enter_fault("disable not confirmed — the write did "
                                  "not land; coils may be energized")
                return self._mode.value
        self._mode = ProbeMode.DISABLED
        self._clear_fault()
        self._touch()
        events.debug("Mode", f"{previous.value} -> disabled ({reason})",
                     source=self.NAME)
        return ProbeMode.DISABLED.value

    def _enter_fault(self, reason):
        """Move to FAULT: the hardware state is unknown (RC-2, RC-3).

        A mode, not a flag beside one, so a fault leaving manual mode also
        leaves the jog pump's notion of manual mode.
        """
        previous = self._mode
        self._mode = ProbeMode.FAULT
        self._moving_deadline = None
        events.debug("Mode", f"{previous.value} -> fault ({reason})",
                     source=self.NAME)
        self._fault(reason)

    # -- stopping ---------------------------------------------------------
    @property
    def can_kill_coils(self):
        """True when this board's firmware has a handler that cuts coil current.

        `'d'` is that handler on the stepper and chuck boards: TOFF=0 on all
        three TMC2209 drivers. The DC board has no branch for it, so the same
        byte arrives at `parseSerialAuto()` as text and produces the stop
        fallthrough -- a halt, not a de-energize.
        """
        return b"d" in self.FIRMWARE_CONTROL_BYTES

    def _halt_hardware(self):
        """The strongest stop this probe has: zero frame, `'d'`, `'k\\n'`.

        All three on the **priority lane**, none of them behind a blocking
        lock, and the coil-kill limitation reported separately rather than
        folded into the result (SERIAL-10, D-7): `can_kill_coils` is a fixed
        property of the firmware, not an outcome of this stop, and folding it
        in would mark every DC probe permanently unconfirmed and train the
        operator to ignore the one signal meant to mean something.
        """
        started = time.monotonic()
        self._moving_deadline = None
        self._stop_interlock()
        landed = {}
        for label, payload in (("zero", self._zero_frame()), ("d", b"d"),
                               ("k", b"k\n")):
            landed[label] = self._write_stop(label, payload)
            events.debug("Frame", f"stop {label} {payload.hex()} "
                         f"written={landed[label]}", source=self.NAME)
        duration = (time.monotonic() - started) * 1000
        events.debug("Halt", f"zero={landed['zero']} d={landed['d']} "
                     f"k={landed['k']} in {duration:.1f} ms", source=self.NAME)
        if landed["d"]:
            previous = self._mode
            self._mode = ProbeMode.DISABLED
            if previous is not ProbeMode.DISABLED:
                events.debug("Mode", f"{previous.value} -> disabled (halt)",
                             source=self.NAME)
        elif self.port is not None:
            self._enter_fault("stop not confirmed — the disable byte did "
                              "not land; coils may be energized")
        if not self.can_kill_coils:
            self._report_no_coil_kill()
        return all(landed.values())

    def _write_stop(self, label, payload):
        if self.port is None:
            return True
        try:
            return bool(self.port.write(payload, priority=True))
        except Exception as exc:
            events.debug("Stop Write Failed", f"{label} ({payload!r}): {exc}",
                         source=self.NAME, exception=exc)
            return False

    def _report_no_coil_kill(self):
        """Tell the operator once that this board cannot de-energize.

        Once per model, not once per stop: `_halt_hardware` is on the close
        and FULL STOP paths, and a warning on every one of those trains the
        operator to dismiss the message that matters. It is a standing
        property of the board, not an event.
        """
        if self._coil_kill_reported:
            return
        self._coil_kill_reported = True
        events.warn("Power Down Not Supported",
                    f"{self.NAME}: this board's firmware has no coil-kill "
                    f"command — it handles "
                    f"{sorted(b.decode() for b in self.FIRMWARE_CONTROL_BYTES)} "
                    f"and nothing else. A stop was sent and the motion frame "
                    f"was zeroed, but the driver outputs were NOT "
                    f"de-energized. Treat the device as live. (SERIAL-10; "
                    f"firmware support is owner decision D-7.)",
                    source=self.NAME)

    # -- motion -----------------------------------------------------------
    def step(self):
        """Run one autonomous move to the declared distances.

        Stays available **while AUTO** so repeated stepping works (DC-6,
        review finding 5). The only thing that refuses it is a move already in
        flight -- and the FULL STOP latch, like every motion command.
        """
        self._guard("Step")
        if self.is_moving:
            self._refuse("Step refused: the stage is still moving")
        self._set_mode(ProbeMode.AUTO, "step", quiesce=False)
        self._mark_moving()
        self._send_move()
        return list(self._position)

    def _mark_moving(self):
        self._moving_deadline = time.monotonic() + self.STEP_SETTLE
        self._touch_activity()

    def _frame(self):
        """The twelve fields of the move frame, in the firmware's order."""
        # Speeds and distances are integers to the operator but floats on the
        # wire ("250.0"): the firmware parses them as floats and the golden
        # frames pin that rendering.
        return {
            "x_step_size": self._number("x_step"),
            "y_step_size": self._number("y_step"),
            "z_step_size": self._number("z_step"),
            "full_speed": float(self._number("full_speed")),
            "slow_speed": 0,
            "brake_distance": 0,
            "x_dist": float(self._number("x_dist")),
            "y_dist": float(self._number("y_dist")),
            "z_dist": float(self._number("z_dist")),
            "command_code_manual": int(self.is_manual),
            "command_code_auton": int(self.is_auto),
        }

    @staticmethod
    def _frame_bytes(fields):
        # The literal "0" is the firmware's unused target_steps placeholder.
        return (
            f"{fields['x_step_size']},{fields['y_step_size']},"
            f"{fields['z_step_size']},0,"
            f"{fields['full_speed']},{fields['slow_speed']},"
            f"{fields['brake_distance']},"
            f"{fields['x_dist']},{fields['y_dist']},{fields['z_dist']},"
            f"{fields['command_code_manual']},{fields['command_code_auton']}\n"
        ).encode("utf-8")

    def _zero_frame(self):
        """The all-zero motion frame. Literal zeros, never coerced params: this
        is the frame that has to be identical on every board."""
        return self._frame_bytes({
            "x_step_size": 0, "y_step_size": 0, "z_step_size": 0,
            "full_speed": 0, "slow_speed": 0, "brake_distance": 0,
            "x_dist": 0, "y_dist": 0, "z_dist": 0,
            "command_code_manual": 0, "command_code_auton": 0,
        })

    def _send_zero_frame(self, reason):
        """Quiesce the axes. A stop, so it takes the priority lane and is not
        aborted by the latch it is helping to enforce."""
        if self.port is None:
            return True
        payload = self._zero_frame()
        written = bool(self.port.write(payload, priority=True))
        events.debug("Frame", f"zero {payload.hex()} written={written} "
                     f"({reason})", source=self.NAME)
        return written

    def _send_move(self):
        """One autonomous move frame. Goes through `SerialPort.write` like
        every other byte, with the latch checked inside the transport's lock."""
        self._guard("Move")
        if self.port is None:
            return False
        self._touch_activity()
        payload = self._frame_bytes(self._frame())
        written = bool(self.port.write(payload, abort_if=self._estop.is_set))
        events.debug("Frame", f"move {payload.hex()} written={written}",
                     source=self.NAME)
        if not written:
            self._refuse("Move aborted: FULL STOP is latched")
        return written

    def _send_jog(self, levels):
        """One 42-byte jog packet built from gamepad levels.

        Losing the pad while MANUAL leaves the mode through the one transition,
        which sends the stop frame and de-energizes (STEPPER-5). The old code
        cleared `manual_flag` alone and left `system_enabled` True, so the
        coils stayed energized in a mode nothing was driving.
        """
        levels = dict(levels or {})
        if self.is_manual and not self._is_gamepad_bound:
            events.warn("Manual Mode Stopped",
                        "the gamepad is no longer bound; disabling",
                        source=self.NAME)
            self._set_mode(ProbeMode.DISABLED, "gamepad lost")
            levels = {}
        if self.port is None:
            return False

        if any(levels.get(key, default) != default for key, default in (
                ("x_axisStatus", 0.0), ("y_axisStatus", 0.0),
                ("z_axisStatusR", -1.0), ("z_axisStatusL", -1.0),
                ("dpad_LR", 0), ("dpad_UD", 0),
                ("LBumper", 0), ("RBumper", 0))):
            self._touch_activity()

        payload = self._jog_bytes(levels)
        written = bool(self.port.write(payload, abort_if=self._estop.is_set))
        events.debug("Jog", f"50 Hz stream; last frame written={written}",
                     source=self.NAME, every=1.0)
        return written

    def _jog_bytes(self, levels):
        # Triggers idle at -1; remap [-1, 1] -> [0, 1]. UP (L) is positive,
        # DOWN (R) negative.
        z_up = (self._level(levels, "z_axisStatusL", -1.0) + 1.0) / 2.0
        z_down = (self._level(levels, "z_axisStatusR", -1.0) + 1.0) / 2.0
        bumpers = (int(self._level(levels, "LBumper", 0))
                   - int(self._level(levels, "RBumper", 0)))
        fmt = self.PACKET_FORMAT
        cast = float if "f" in fmt[5:] else int
        return struct.pack(
            fmt,
            START_MARKER,
            1,
            float(self._level(levels, "x_axisStatus", 0.0)),
            float(self._level(levels, "y_axisStatus", 0.0)),
            float(z_up - z_down),
            cast(self._number("x_step")),
            cast(self._number("y_step")),
            cast(self._number("z_step")),
            cast(self._level(levels, "dpad_LR", 0)),
            cast(self._level(levels, "dpad_UD", 0)),
            cast(bumpers),
            cast(self._number("man_full_speed")),
        )

    @staticmethod
    def _level(levels, key, default):
        value = levels.get(key, default)
        return default if value is None else value

    # -- loops ------------------------------------------------------------
    def _jog_loop(self):
        """Pump gamepad input to the hardware at 50 Hz while MANUAL.

        The gate is checked here, in the one place that writes motion from
        gamepad input. `flush_neutral` used to be the answer and could not
        work: the poll loop simply read the physical stick again and refilled
        the cache before the next send. Gating the *send* is what holds the
        axis.
        """
        was_pumping = False
        while not self._threads_stop.wait(self.JOG_INTERVAL):
            try:
                pumping = self.is_manual and self._is_gate_open
                if pumping and not self._estop.is_set():
                    self._send_jog(self._axis_state())
                elif was_pumping:
                    # Neutral on exit (I-4.2): leaving manual mode, or having
                    # the gate close under it, sends one zeroed frame or the
                    # last non-zero command stands. One frame, not a stream --
                    # the gate is not a stop.
                    self._send_jog({})
                was_pumping = pumping
            except Refused as refusal:
                events.debug("Jog Refused", refusal.reason, source=self.NAME,
                             every=1.0)
                was_pumping = False
            except Exception as exc:
                self._enter_fault(f"manual jog pump failed: {exc}")
                return

    def _sample_loop(self):
        """Drain the position stream the firmware sends unasked.

        The firmware prints `POS:x,y,z` every 100 ms on its own
        (PRINT_INTERVAL), so there is nothing to poll: this reads what has
        already arrived, cheaply and without blocking, and parses the latest
        complete line. Views and the Web API read the cache and never touch
        the transport (I-4.1), so a stalled read cannot block a render tick or
        a FULL STOP (I-4.3).
        """
        counted_from = time.monotonic()
        seen = 0
        while not self._threads_stop.wait(self.SAMPLE_INTERVAL):
            self._touch()   # the loop is alive; data freshness is position_age
            try:
                position = self._read_position()
                if position is not None:
                    seen += 1
                    self._note_position(position)
                elapsed = time.monotonic() - counted_from
                if elapsed >= 5.0:
                    events.debug("Position Rate",
                                 f"{seen / elapsed:.1f} POS lines/s",
                                 source=self.NAME, every=5.0)
                    counted_from, seen = time.monotonic(), 0
            except Exception as exc:
                # Sampling is best-effort: a transport hiccup must not kill the
                # loop, or positions freeze silently for the rest of the run.
                events.debug("Sample Failed", str(exc), source=self.NAME,
                             exception=exc, every=5.0)

    def _read_position(self):
        """The latest complete `POS:x,y,z` line, or None. Never blocks."""
        if self.port is None:
            return None
        latest = None
        for _ in range(self.MAX_DRAIN_LINES):
            line = self.port.read_line(timeout=0)
            if not line:
                break
            if isinstance(line, bytes):
                line = line.decode("utf-8", errors="ignore")
            line = line.strip()
            if not line.startswith("POS:"):
                continue
            parts = line[4:].split(",")
            if len(parts) != 3:
                continue
            try:
                latest = tuple(int(part) for part in parts)
            except ValueError:
                continue   # malformed line, skip
        return latest

    def _note_position(self, position):
        """Record one sample, and the velocity between it and the last one.

        Velocity is computed **only between two distinct samples**, so it is
        never a division by a zero interval and never survives as a stale
        number: an unchanged position between two POS lines is a real zero.
        """
        now = time.monotonic()
        previous, previous_time = self._position, self._position_time
        moved = position != previous
        if previous_time is not None and now > previous_time:
            span = now - previous_time
            self._velocity = tuple(
                (new - old) / span for new, old in zip(position, previous))
        self._position = position
        self._position_time = now
        self._samples_seen += 1
        self._touch()
        if moved:
            # Motion *is* activity, so a long move does not age into the idle
            # interlock; arrival starts the idle clock.
            self._touch_activity()
            if self._mode is ProbeMode.AUTO and self._moving_deadline is not None:
                self._moving_deadline = now + self.STEP_SETTLE

    # -- position ---------------------------------------------------------
    @property
    def position(self):
        return self._position

    @property
    def position_time(self):
        """Monotonic seconds of the latest POS line. None before the first."""
        return self._position_time

    @property
    def position_age(self):
        if self._position_time is None:
            return None
        return round(time.monotonic() - self._position_time, 2)

    @property
    def velocity(self):
        return self._velocity

    @property
    def position_x(self):
        return self._position[0]

    @property
    def position_y(self):
        return self._position[1]

    @property
    def position_z(self):
        return self._position[2]

    @property
    def velocity_text(self):
        return ", ".join(f"{v:.1f}" for v in self._velocity)

    # -- gamepad ----------------------------------------------------------
    @property
    def _is_gamepad_bound(self):
        return bool(self.gamepad is not None and self.gamepad.is_bound)

    @property
    def _is_gate_open(self):
        return bool(self.gamepad is not None and self.gamepad.is_gate_open)

    @property
    def gamepad_name(self):
        """What the Gamepad says it is bound to, not a mirror this model keeps
        in step by hand."""
        if self.gamepad is None:
            return "None"
        name = getattr(self.gamepad, "name", None)
        if name is None:
            name = self._gamepad_name if self._is_gamepad_bound else None
        return name or "None"

    def gamepad_options(self):
        if self.gamepad is None:
            return ["None"]
        return list(self.gamepad.options)

    def gamepad_log(self):
        if self.gamepad is None:
            return []
        return list(self.gamepad.log)

    def set_gamepad(self, name):
        """Bind a gamepad. A failed bind reverts the selection and stops.

        The selection used to be assigned *before* the bind was attempted, so
        a failed swap left the UI naming a pad that was never bound and the
        mode flipping lazily on some later tick (GAMEPAD-3, GAMEPAD-4).
        """
        previous = self._gamepad_name
        wanted = None if name in (None, "", "None") else str(name)
        bound = False
        try:
            bound = bool(self.gamepad.bind(wanted))
        except Exception as exc:
            events.warn("Gamepad Bind Failed", f"{name!r}: {exc}",
                        source=self.NAME, exception=exc)
        if wanted is None:
            self._gamepad_name = None
            events.debug("Gamepad", "unbound by operator", source=self.NAME)
            if self.is_manual:
                self._set_mode(ProbeMode.DISABLED, "gamepad released")
            return "None"
        if not bound:
            self._gamepad_name = previous
            if self.is_manual:
                # Manual mode without a bound pad is exactly the state I-3.2
                # forbids.
                self._set_mode(ProbeMode.DISABLED, "gamepad bind failed")
            events.debug("Gamepad", f"bind to {name!r} failed; selection "
                         f"reverted to {previous!r}", source=self.NAME)
            self._refuse(f"could not bind gamepad {name!r}")
        self._gamepad_name = wanted
        events.debug("Gamepad", f"bound to {wanted!r}", source=self.NAME)
        return wanted

    def _axis_state(self):
        """The gamepad's mapped levels, or `{}` if they could not be read.

        The read itself is guarded, not just the float cast (REDPERCENT-4): a
        pad unplugged mid-session is the ordinary case. Reported rather than
        swallowed -- the event log folds repeats of one event into a single
        counted entry, so a failure at 50 Hz is one warning, not a storm.
        """
        if self.gamepad is None:
            return {}
        try:
            return dict(self.gamepad.levels or {})
        except Exception as exc:
            events.warn("Gamepad Read Failed",
                        f"could not read the gamepad: {exc}. Treating every "
                        f"axis as neutral until it recovers.",
                        source=self.NAME, exception=exc)
            return {}

    # -- idle interlock ---------------------------------------------------
    def _touch_activity(self):
        self._activity_time = time.monotonic()

    def _stop_interlock(self):
        self._interlock_stop.set()

    def _start_interlock(self):
        """Arm the idle interlock for this arming (STEPPER-7, RC-3 item 5).

        **Each arming gets its own Event and generation.** The old code reused
        one Event for the model's lifetime, so a disable that set it left it
        set: the next enable started a thread that returned on its first tick,
        and the probe ran energized with no interlock at all. The generation
        lets a stale thread from a previous arming retire itself rather than
        fight the current one -- `is_alive()` alone is not enough, because a
        thread told to stop stays alive until its next tick.
        """
        if (self._interlock_thread is not None
                and self._interlock_thread.is_alive()
                and not self._interlock_stop.is_set()):
            return
        self._interlock_stop = threading.Event()
        self._interlock_generation += 1
        generation = self._interlock_generation
        stop_event = self._interlock_stop
        self._touch_activity()

        def _watch():
            events.debug("Interlock", f"armed, generation {generation}, "
                         f"{self.INTERLOCK_TIMEOUT:.0f} s", source=self.NAME)
            while not stop_event.wait(self.INTERLOCK_POLL_INTERVAL):
                if generation != self._interlock_generation:
                    return
                if not self.is_enabled:
                    return
                # **No flag deferral.** This used to `continue` while stepping
                # or manual, which suppressed the interlock in exactly the two
                # modes that energize coils. The clock is real inactivity
                # instead: motion extends it through `_note_position`, and
                # off-neutral gamepad input through `_send_jog`. D-3 (owner):
                # manual mode does idle-time-out.
                idle = time.monotonic() - self._activity_time
                if idle > self.INTERLOCK_TIMEOUT:
                    events.debug("Interlock", f"fired after {idle:.1f} s idle "
                                 f"in {self._mode.value}", source=self.NAME)
                    events.warn("Idle Timeout", f"{idle:.0f} s of inactivity; "
                                f"disabling {self.NAME}", source=self.NAME)
                    try:
                        self._set_mode(ProbeMode.DISABLED, "idle interlock")
                    except Exception as exc:
                        events.warn("Idle Disable Failed", str(exc),
                                    source=self.NAME, exception=exc)
                    return

        self._interlock_thread = threading.Thread(
            target=_watch, daemon=True,
            name=f"interlock-{self.NAME}-{generation}")
        self._interlock_thread.start()

    # -- params -----------------------------------------------------------
    def _number(self, name):
        """A motion parameter coerced against *this class's* declaration.

        The fallback belongs to the class, not the call site: a DCProbe
        declares 120, and `_num(self.full_speed, 400)` handed the firmware
        more than three times that when the field would not parse (RC-6).
        """
        return self.PARAMS[name].coerce(self._param_store.get(name))

    def _gate_for(self, name):
        """The schema element declaring `name`, cached. One place reads the
        schema's own gate, so a control's rendered state and its actual
        enforcement cannot drift apart (DC-6)."""
        if name not in self._gates:
            self._gates[name] = next(
                (e for e in sch.elements(self.schema)
                 if e.get("model_attr") == name), None)
        return self._gates[name]

    @staticmethod
    def _gated_param(name):
        """One property per declared motion parameter, gated by the exact
        `disabled_when` its schema entry already carries (DC-6).

        What is *stored* is untouched: an unparseable value is accepted here
        and substituted for later, leniently, when the frame is built. Mode
        gating and value typing are different questions, and only the first
        belongs at the write boundary -- a write that arrives mid-run is a
        safety question, and the answer has to be "no" before anyone asks what
        the value was.
        """

        def getter(self):
            return self._param_store.get(name, "")

        def setter(self, value):
            element = self._gate_for(name)
            if element is not None and not sch.is_enabled(element, self.mode_name):
                label = element.get("text", name).rstrip(":")
                self._refuse(f"{label} cannot be changed while {self.mode_name}")
            self._param_store[name] = value

        return property(getter, setter)

    # -- schema -----------------------------------------------------------
    @property
    def schema(self):
        P = self.PARAMS
        return sch.schema(
            sch.section(
                "Coordinate Frame",
                sch.readonly("X Position:", "position_x"),
                sch.readonly("Y Position:", "position_y"),
                sch.readonly("Z Position:", "position_z"),
                sch.readonly("Velocity (x, y, z):", "velocity_text"),
                sch.readonly("Position age (s):", "position_age", role="info"),
            ),
            sch.section(
                "Configuration",
                sch.dropdown("Gamepad:", "gamepad_name", "set_gamepad",
                             "gamepad_options"),
                *[sch.entry(P[name].label + ":", name, P[name],
                            disabled_when=_MOTION_GATE)
                  for name in self.ENTRY_PARAMS],
            ),
            sch.section(
                "System Control",
                # Per-device "System Power" and "Full Stop" toggles stay
                # removed: enable/disable is reachable through the mode
                # toggles, and the dashboard's global FULL STOP already
                # reaches every model.
                sch.toggle("Autonomous:", "is_auto", "set_mode",
                           "AUTONOMOUS MODE (Click to Stop)",
                           "Enter Autonomous Mode",
                           on_args=[ProbeMode.AUTO.value],
                           off_args=[ProbeMode.DISABLED.value]),
                sch.toggle("Manual / Gamepad:", "is_manual", "set_mode",
                           "MANUAL MODE (Click to Stop)", "Enter Manual Mode",
                           on_args=[ProbeMode.MANUAL.value],
                           off_args=[ProbeMode.DISABLED.value]),
                # D-5: the distances and the speed travel with the command and
                # are validated as a set. **Not** gated on "autonomous": a
                # second step while already AUTO is the normal way to work
                # (DC-6, review finding 5). `is_moving` is what refuses.
                sch.button("Step", "step",
                           inputs=("x_dist", "y_dist", "z_dist", "full_speed"),
                           role="go", disabled_when=("manual",)),
                sch.log_stream("Gamepad Log:", "gamepad_log"),
            ),
            self._safety_section(),
        )

    #: Entry order in the Configuration section. DCProbe adds two.
    ENTRY_PARAMS = ("x_step", "y_step", "z_step", "x_dist", "y_dist", "z_dist",
                    "full_speed", "man_full_speed")

    @property
    def state(self):
        snapshot = super().state
        snapshot.update({
            "position": list(self._position),
            "position_time": self._position_time,
            "position_age": self.position_age,
            "velocity": list(self._velocity),
            "is_moving": self.is_moving,
            "is_enabled": self.is_enabled,
            "can_kill_coils": self.can_kill_coils,
        })
        return snapshot

    # -- refusals ---------------------------------------------------------
    def _refuse(self, reason):
        events.debug("Refused", reason, source=self.NAME)
        raise Refused(reason)


for _name in Probe.PARAMS:
    setattr(Probe, _name, Probe._gated_param(_name))
del _name


class StepperProbe(Probe):
    """TMC2209 board."""

    NAME = "Stepper Probe"
    IDENTITY = "s"

    PARAMS = {**Probe.PARAMS, **{p.name: p for p in (
        Param("x_step", "int", default=1, minimum=1, label="X Step Size"),
        Param("y_step", "int", default=1, minimum=1, label="Y Step Size"),
        Param("z_step", "int", default=1, minimum=1, label="Z Step Size"),
    )}}


class DCProbe(Probe):
    """Own packet format; this firmware cannot kill coils.

    `firmware/high_polling_rate/high_polling_rate.ino`'s `parseHybridSerial`
    branches on 0xAA and 0x73 (`'s'`) only; everything else, `'e'` and `'d'`
    included, falls through to `parseSerialAuto()` and is read as text
    (SERIAL-10). The host keeps sending the same bytes -- it just no longer
    reports a de-energize this board cannot perform. Adding the handlers is
    D-7, at the bench.
    """

    NAME = "DC Probe"
    IDENTITY = "d"
    FIRMWARE_CONTROL_BYTES = frozenset({b"s"})

    # A DC probe runs at 120, not the stepper's 400. Before this table the
    # fallback was the stepper's number at every call site.
    PARAMS = {**Probe.PARAMS, **{p.name: p for p in (
        Param("x_step", "int", default=1, minimum=1, label="X Step Size"),
        Param("y_step", "int", default=1, minimum=1, label="Y Step Size"),
        Param("z_step", "int", default=1, minimum=1, label="Z Step Size"),
        Param("full_speed", "int", default=120, minimum=1,
              label="Autonomous Speed"),
        Param("man_full_speed", "int", default=120, minimum=1,
              label="Manual Speed"),
    )}}

    ENTRY_PARAMS = Probe.ENTRY_PARAMS + ("slow_speed", "brake_distance")

    def _frame(self):
        fields = super()._frame()
        fields["slow_speed"] = float(self._number("slow_speed"))
        fields["brake_distance"] = float(self._number("brake_distance"))
        return fields


class ChuckPositioner(Probe):
    """Stepper-family board."""

    NAME = "Chuck Positioner"
    IDENTITY = "c"

    PARAMS = {**Probe.PARAMS, **{p.name: p for p in (
        Param("x_step", "int", default=2, minimum=1, label="X Step Size"),
        Param("y_step", "int", default=2, minimum=1, label="Y Step Size"),
        Param("z_step", "int", default=2, minimum=1, label="Z Step Size"),
    )}}
