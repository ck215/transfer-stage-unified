"""Probes: the mode machine, the move/jog frames, position sampling and the
motion interlock.

Was `BaseProbe` (1682 lines) plus the probe-shaped half of the old `serial`
transport. What is gone, by owner ruling: G-code scripts and run generations,
web client liveness, and the gamepad plumbing (which is the Gamepad device's
job now). What is inherited rather than re-implemented: the FULL STOP latch,
the bounded stop, the ordered close, the fault record — all from `Model`.

**Firmware is untouched.** Every byte this module puts on the wire is the byte
`legacy/src/` sends today:

    enable      b"e"
    disable     b"d"
    move/step   b"<x_step>,<y_step>,<z_step>,0,<full>,<slow>,<brake>,"
                b"<x_dist>,<y_dist>,<z_dist>,<manual>,<auto>\\n"
    zero frame  b"0,0,0,0,0,0,0,0,0,0,0,0\\n"
    jog         42-byte struct `<BBffffffffff`, START_MARKER first
    kill coils  b"k\\n"   (no firmware handles it; see can_kill_coils, D-7)

`tests/test_probe_frames.py` drives the *old* classes in simulator
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

import schema as sch
from devices import gamepad as gamepad_device
from devices import serial_port as serial_device
from events import events
from model.base import Model
from model.gamepad_input import GamepadInput
from model.idle import IdleInterlock
from param import Param
from result import Refused


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

#: Manual Speed's gate (owner ruling 2026-09-26): adjusting the jog speed on
#: the fly while jogging is a feature, so the field is live in MANUAL and the
#: 50 Hz jog stream picks the new value up on its next frame. It stays locked
#: during an autonomous run, and every other motion field keeps
#: `_MOTION_GATE`. A write that gets past a gate while the motors are live is
#: parsed strictly by the setter, so a bad value is refused and the previous
#: speed stays in effect.
_MANUAL_SPEED_GATE = ("autonomous",)

#: Ceiling for both speeds, steps/s, on every probe (owner ruling 2026-09-26).
#: Arbitrary for now: per-device limits, adapted to each board's steppers,
#: come out of the architecture audit (BUGFIX_PLAN Q1).
MAX_SPEED = 3200

#: 42-byte jog packet: start marker, packet type, then ten floats.
PACKET_FORMAT = "<BBffffffffff"
START_MARKER = 0xAA


class Probe(GamepadInput, IdleInterlock, Model):
    """A three-axis probe: one SerialPort, one Gamepad, one mode."""

    NAME = "Probe"
    IDENTITY = None
    NEEDS_PORT = True
    NEEDS_GAMEPAD = True

    #: All three probe sketches run `Serial.begin(500000)`; SerialPort's
    #: default is the heater's 115200, which the boards read as garbage.
    BAUD_RATE = 500000

    PARAMS = {p.name: p for p in (
        Param("x_step", "int", default=16, minimum=1, label="X Step Size"),
        Param("y_step", "int", default=16, minimum=1, label="Y Step Size"),
        Param("z_step", "int", default=16, minimum=1, label="Z Step Size"),
        Param("x_dist", "int", default=0, label="Target X Dist"),
        Param("y_dist", "int", default=0, label="Target Y Dist"),
        Param("z_dist", "int", default=0, label="Target Z Dist"),
        Param("full_speed", "int", default=400, minimum=1,
              maximum=MAX_SPEED, label="Autonomous Speed", unit="steps/s"),
        Param("man_full_speed", "int", default=400, minimum=1,
              maximum=MAX_SPEED, label="Manual Speed", unit="steps/s"),
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
    GAMEPAD_RATE_HZ = 50.0    # the manual jog stream (GamepadInput's pump)
    SAMPLE_INTERVAL = 0.01    # s -> ~100 Hz drain of a 10 Hz POS stream
    #: How many lines one drain pass will take before yielding. The firmware
    #: prints one POS line per 100 ms (PRINT_INTERVAL); a cap keeps a flooded
    #: buffer from holding the transaction lock long enough to delay a jog
    #: frame or a priority write.
    MAX_DRAIN_LINES = 32

    #: How long the position must stay unchanged before an autonomous move is
    #: considered arrived.
    STEP_SETTLE = 1.0

    # The idle interlock's INTERLOCK_TIMEOUT (300 s), INTERLOCK_POLL_INTERVAL
    # and IDLE_WARN_SECONDS (60 s) come from `model.idle.IdleInterlock`.

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
        self._attach_gamepad(gamepad)

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
            return serial_device.SerialPort(name, baud_rate=self.BAUD_RATE)
        return port

    @property
    def devices(self):
        return [d for d in (self.port, self.gamepad) if d is not None]

    def open(self):
        super().open()      # GamepadInput binds the chosen pad after this
        events.debug("Devices Open", f"port={getattr(self.port, 'status', '?')} "
                     f"gamepad={self.gamepad_name}", source=self.NAME)

    # -- threads ----------------------------------------------------------
    def _start_threads(self):
        """The sampler here, the gamepad pump in GamepadInput, both through
        the base (MOD-2), which stops and joins them at close."""
        self._spawn("sample", self._sample_loop)
        super()._start_threads()
        events.debug("Threads Started", "sample ~100 Hz, jog 50 Hz",
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

    @property
    def is_energized(self):
        return self.is_enabled

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
        self._refuse(f"{target} is not a mode of the {self.NAME}.")

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
                self._refuse("Fault is not a mode you can select.")
            if target is ProbeMode.MANUAL and not self._is_gamepad_bound:
                # Before the enable, never after: checking afterwards can
                # revert the Python flag, but the firmware has already been
                # told to energize.
                self._refuse("Manual mode needs a gamepad. Choose one under "
                             "Gamepad first.")
            self._guard(f"Mode change to {target.value}")

            self._energize(reason)
            if target is ProbeMode.MANUAL and previous is not ProbeMode.MANUAL:
                # Entering manual starts with no step pending (D3): a press
                # parked before this point was not made in manual mode.
                self._drain_edges()
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
            events.debug("Enable Failed", f"b'e' not written: {exc!r}",
                         source=self.NAME, exception=exc)
            events.warn("Enable Failed", "The enable did not reach the board. "
                        "Check the connection and try again.",
                        source=self.NAME, exception=exc)
            self._refuse("The enable did not reach the board. Check the "
                         "connection and try again.")
        events.debug("Frame", f"enable {b'e'.hex()} written={bool(written)} "
                     f"({reason})", source=self.NAME)
        if not written:
            self._refuse(f"{self.NAME} is stopped. Clear the stop before "
                         "enabling it.")
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
                events.debug("Disable Failed", f"b'd' not written: {exc!r}",
                             source=self.NAME, exception=exc)
                self._enter_fault("The disable did not reach the board, so "
                                  "the motors may still be powered. Treat it "
                                  "as live and check the connection.")
                return self._mode.value
            events.debug("Frame", f"disable {b'd'.hex()} written={bool(written)} "
                         f"({reason})", source=self.NAME)
            if not written:
                self._enter_fault("The disable did not reach the board, so "
                                  "the motors may still be powered. Treat it "
                                  "as live and check the connection.")
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
            self._enter_fault("The stop did not reach the board, so the "
                              "motors may still be powered. Treat it as live "
                              "and check the connection.")
        if not self.can_kill_coils:
            self._report_no_coil_kill()
        return all(landed.values())

    def _write_stop(self, label, payload):
        if self.port is None:
            # A1: a stop that wrote nothing is unconfirmed, as the rotator's
            # is (MANAGER-21). There is no board to have received it.
            return False
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
        events.debug("Power Down Not Supported",
                     f"firmware handles "
                     f"{sorted(b.decode() for b in self.FIRMWARE_CONTROL_BYTES)} "
                     f"only; no coil-kill byte (SERIAL-10, owner decision D-7)",
                     source=self.NAME)
        events.warn("Power Down Not Supported",
                    f"The {self.NAME} cannot power down its motors: its "
                    f"firmware has no command for it. A stop halts the motion, "
                    f"but the motors stay powered, so treat it as live.",
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
            self._refuse("The stage is still moving. Wait for it to stop, "
                         "then step again.")
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
            self._refuse(f"The move was not sent: {self.NAME} is stopped. "
                         "Clear the stop first.")
        return written

    def _send_jog(self, levels):
        """One 42-byte jog packet built from gamepad levels.

        `levels` is in the gamepad contract's channels
        (`devices.gamepad.NEUTRAL`). The golden captures record their jog
        inputs in the legacy names; the golden test translates them.
        """
        if self.port is None:
            return False
        if self._is_off_neutral(levels):
            self._touch_activity()
        payload = self._jog_bytes(levels)
        written = bool(self.port.write(payload, abort_if=self._estop.is_set))
        events.debug("Jog", f"50 Hz stream; last frame written={written}",
                     source=self.NAME, every=1.0)
        return written

    def _jog_bytes(self, levels):
        # Triggers idle at -1; remap [-1, 1] -> [0, 1]. UP (L) is positive,
        # DOWN (R) negative.
        z_up = (self._level(levels, "trigger_left", -1.0) + 1.0) / 2.0
        z_down = (self._level(levels, "trigger_right", -1.0) + 1.0) / 2.0
        bumpers = (int(self._level(levels, "bumper_left", 0))
                   - int(self._level(levels, "bumper_right", 0)))
        fmt = self.PACKET_FORMAT
        cast = float if "f" in fmt[5:] else int
        return struct.pack(
            fmt,
            START_MARKER,
            1,
            float(self._level(levels, "axis_x", 0.0)),
            float(self._level(levels, "axis_y", 0.0)),
            float(z_up - z_down),
            cast(self._number("x_step")),
            cast(self._number("y_step")),
            cast(self._number("z_step")),
            cast(self._level(levels, "hat_x", 0)),
            cast(self._level(levels, "hat_y", 0)),
            cast(bumpers),
            cast(self._number("man_full_speed")),
        )

    @staticmethod
    def _level(levels, key, default):
        value = levels.get(key, default)
        return default if value is None else value

    # -- loops ------------------------------------------------------------
    # The jog pump is GamepadInput's (`_gamepad_loop` / `_gamepad_tick`); the
    # probe's part is the four hooks under "gamepad" below.

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

    # -- gamepad (MOD-1: GamepadInput owns bind, gate, pump; these are hooks) -
    @property
    def _pumps_gamepad(self):
        return self.is_manual

    def _on_gamepad(self, levels, edges):
        """D-pad and bumper steps (D3) travel in this packet, one per press:
        `levels` arrives with the drained edges already merged in."""
        self._send_jog(levels)

    def _on_gamepad_lost(self, reason):
        """Leave MANUAL through the one transition, which sends the stop frame
        and de-energizes (STEPPER-5)."""
        if reason == self.GAMEPAD_LOST:
            events.warn("Manual Mode Stopped",
                        "The gamepad disconnected, so manual mode stopped and "
                        "the motors were disabled.", source=self.NAME)
        self._set_mode(ProbeMode.DISABLED, reason)

    def _on_gamepad_fault(self, reason):
        self._enter_fault("Manual control stopped working. Treat the "
                          "probe as live, stop it, and check the "
                          "gamepad and the connection.")

    # -- idle interlock (MOD-3: the mixin owns the clock and the loop) ------
    @property
    def _idle_is_armed(self):
        return self.is_enabled

    def _on_idle_expired(self, idle):
        """Leave the mode through the one transition, which quiesces and
        de-energizes. **No flag deferral**: motion extends the clock through
        `_note_position`, off-neutral gamepad input through `_send_jog`, and
        D-3 (owner) is that manual mode does idle-time-out."""
        self._set_mode(ProbeMode.DISABLED, "idle interlock")

    def _idle_soon_text(self):
        return (f"{self.NAME} powers its motors down soon unless it moves or "
                "you extend.")

    def _idle_expired_text(self, idle):
        return (f"{self.NAME} was idle for {idle:.0f} s, so its motors were "
                "disabled. Enter a mode again to continue.")

    def _idle_extended_text(self):
        return (f"{self.NAME} stays energized for another "
                f"{self.INTERLOCK_TIMEOUT:.0f} s.")

    def _idle_nothing_text(self):
        return f"Nothing to extend: {self.NAME} is not in a mode."

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
                # A write of the value already held is not a change: compare
                # the normalised value, so 5, "5" and 5.0 are one write.
                ok, parsed = self.PARAMS[name].parse(value)
                if ok and self._same_value(self._param_store.get(name), parsed):
                    return
                label = element.get("text", name).rstrip(":")
                self._refuse(f"{label} cannot be changed in {self.mode_name} "
                             f"mode. Leave {self.mode_name} mode to edit it.")
            if self.mode_name in _MOTION_GATE:
                # Live while the motors are (Manual Speed in MANUAL): the
                # lenient store-now-substitute-later rule would hand the
                # next jog frame the class default in place of a bad value.
                # Strict here, so a refused write leaves the previous value.
                ok, parsed = self.PARAMS[name].parse(value)
                if not ok:
                    self._refuse(parsed)
                value = parsed
            self._param_store[name] = value

        return property(getter, setter)

    # -- schema -----------------------------------------------------------
    @property
    def schema(self):
        P = self.PARAMS
        gamepad_choice, gamepad_log = self._gamepad_elements()
        # Tiers (owner ruling 2026-09-25, canvas row E): position and speed
        # are what an operator adjusts every session, so they are always
        # drawn; step sizes, targets and brakes sit one disclosure away;
        # velocity, position age, the gamepad log and the per-model stop
        # are diagnostics. The gamepad CHOICE is tier 1 (owner, 2026-09-26,
        # Tier K): it is picked every session, right before Manual mode.
        return sch.schema(
            sch.section(
                "Position",
                sch.readonly("X:", "position_x", rail=True, unit="steps"),
                sch.readonly("Y:", "position_y", rail=True, unit="steps"),
                sch.readonly("Z:", "position_z", rail=True, unit="steps"),
            ),
            sch.section(
                "Speeds",
                # A slider beside the entry, never instead of it: the entry
                # keeps the precision. The slider's travel is a display range;
                # the Param still validates what is typed.
                sch.entry(P["full_speed"].label + ":", "full_speed", P["full_speed"],
                          disabled_when=_MOTION_GATE, slider=self.SPEED_SLIDER),
                sch.entry(P["man_full_speed"].label + ":", "man_full_speed",
                          P["man_full_speed"], disabled_when=_MANUAL_SPEED_GATE,
                          slider=self.SPEED_SLIDER),
            ),
            sch.section(
                "System Control",
                # Per-device "System Power" and "Full Stop" toggles stay
                # removed: enable/disable is reachable through the mode
                # toggles, and the dashboard's global FULL STOP already
                # reaches every model.
                # F11: greyed out while latched. A latched probe is never in
                # AUTO or MANUAL (the halt leaves both), so the toggle's "off"
                # direction is not what this takes away.
                gamepad_choice,
                # Round 8 (IMP8-2, Tk CCR 1): a probe whose disable FAILED is
                # in FAULT with its motors possibly powered; the toggles are
                # greyed from the schema in every view, and the stop is the
                # way out (a confirmed disable clears the fault).
                sch.toggle("Autonomous:", "is_auto", "set_mode",
                           "Autonomous mode (press to stop)",
                           "Enter Autonomous Mode",
                           on_args=[ProbeMode.AUTO.value],
                           off_args=[ProbeMode.DISABLED.value],
                           disabled_when=("latched", "fault")),
                sch.toggle("Manual / Gamepad:", "is_manual", "set_mode",
                           "Manual mode (press to stop)", "Enter Manual Mode",
                           on_args=[ProbeMode.MANUAL.value],
                           off_args=[ProbeMode.DISABLED.value],
                           disabled_when=("latched", "fault")),
                # D-5: the distances and the speed travel with the command and
                # are validated as a set. **Not** gated on "autonomous": a
                # second step while already AUTO is the normal way to work
                # (DC-6, review finding 5). `is_moving` is what refuses.
                sch.button("Step", "step",
                           inputs=("x_dist", "y_dist", "z_dist", "full_speed"),
                           role="go", disabled_when=("manual", "latched")),
                # Declared so `run("extend_idle")` passes the allow-list; it
                # renders nothing. The views draw the countdown and its
                # Extend from `idle_remaining` in state (Tier N).
                {"type": "internal", "command": "extend_idle", "writable": False,
                 "role": "neutral"},
            ),
            sch.section(
                "Configuration",
                *[sch.entry(P[name].label + ":", name, P[name],
                            disabled_when=_MOTION_GATE)
                  for name in self.CONFIG_PARAMS],
                # The disclosure names the device (Tier K): once the press
                # sits above its well instead of beside the model's name,
                # "Configure" alone did not say what it configured.
                tier=2, disclosure=f"Configure {self.NAME}",
            ),
            sch.section(
                "Diagnostics",
                sch.readonly("Velocity (x, y, z):", "velocity_text"),
                sch.readonly("Position age (s):", "position_age", role="info"),
                gamepad_log,
                tier=3, disclosure="Diagnostics",
            ),
            self._safety_section(),
        )

    #: Every editable field, in the order the D-5 command set travels.
    #: DCProbe adds two. The two speeds are tier 1 (with a slider); the rest
    #: are the tier-2 Configuration.
    ENTRY_PARAMS = ("x_step", "y_step", "z_step", "x_dist", "y_dist", "z_dist",
                    "full_speed", "man_full_speed")
    SPEED_PARAMS = ("full_speed", "man_full_speed")
    #: The slider's travel in steps/s: the full range the Param accepts.
    SPEED_SLIDER = (1, MAX_SPEED)

    @property
    def CONFIG_PARAMS(self):
        return tuple(n for n in self.ENTRY_PARAMS if n not in self.SPEED_PARAMS)

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
              maximum=MAX_SPEED, label="Autonomous Speed", unit="steps/s"),
        Param("man_full_speed", "int", default=120, minimum=1,
              maximum=MAX_SPEED, label="Manual Speed", unit="steps/s"),
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
