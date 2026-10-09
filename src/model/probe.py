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

**The Mega standard** (docs/rebuild/MEGA_STANDARD.md, 2026-10-09). A board
may list capabilities in its identity answer (`DEV: m caps=ext1,...`). Only
a board whose caps include `ext1` ever receives a `#` line: on link-up
`#INFO`, `#LOG 2` and `#HOSTTIMEOUT 1000`, then `#HB` every 250 ms; Zero and
Home per axis when the caps list `home`. Its `#` lines are routed: `#OK` /
`#ERR` answer the pending request, `#EVT` is logged and LIMIT, FAULT,
HOME/HOMED and REFUSED are acted on. A board that lists nothing (every
board built before the standard, `DEV: s`) gets exactly the bytes and the
schema it always did.

`tests/test_probe_frames.py` drives the *old* classes in simulator
mode and asserts these are byte-identical, per probe type, so a refactor here
cannot quietly change what the boards receive.

What did change is the *lane*: the stop path (zero frame, `'d'`, `'k\\n'`)
goes out on the priority lane and never waits on a blocking lock, and every
motion write carries `abort_if=self._estop.is_set` so the latch is checked
inside the transport's lock rather than before it.
"""
import collections
import enum
import math
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
from model.sample_frame import UM_PER_COUNT
from param import Param
from result import NeedsConfirm, Refused


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

#: Modes in which the host is driving the axes, so a board that stops
#: answering is a stop (bench incident 2026-10-04). IDLE is armed but
#: commands no motion; arming a silent board is refused instead.
_DRIVEN = frozenset({ProbeMode.AUTO, ProbeMode.MANUAL})

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

#: The stepper board's ceiling for both speeds, steps/s (owner ruling
#: 2026-09-26). Each probe class declares its own `MAX_SPEED` (owner,
#: 2026-10-07: the chuck positioner's bench value is 600); the operator reads
#: a percent of it, and the steps/s underneath is what travels on the wire.
MAX_SPEED = 3200


def _speed_params(default, ceiling):
    """The two stored speeds (steps/s) for a class with this ceiling."""
    return (
        Param("full_speed", "int", default=default, minimum=1,
              maximum=ceiling, label="Autonomous Speed", unit="steps/s"),
        Param("man_full_speed", "int", default=default, minimum=1,
              maximum=ceiling, label="Manual Speed", unit="steps/s"),
    )


#: step/s attribute <-> the percent dial the operator sees.
_PCT_OF = {"full_speed": "full_speed_pct", "man_full_speed": "man_full_speed_pct"}

#: 42-byte jog packet: start marker, packet type, then ten floats.
PACKET_FORMAT = "<BBffffffffff"
START_MARKER = 0xAA

#: The axes, in the order the extension channel names them.
AXES = ("X", "Y", "Z")
#: The capability that opens the `#` extension channel, protocol 1
#: (MEGA_STANDARD section 2). No `#` byte goes to a board without it.
EXT = "ext1"
#: The features the schema offers when the caps list them (section 6).
EXT_FEATURES = ("home", "limits")


def caps_of(identity):
    """The capability tokens of an identity answer (the text after `DEV:`).

    `m caps=ext1,log` -> frozenset({"ext1", "log"}); a board that lists none
    (`s`, `x X`) -> frozenset(); no answer yet (None, "") -> None, which is
    "unknown", never "has none"."""
    text = str(identity or "").strip()
    if not text:
        return None
    for word in text.split()[1:]:
        key, eq, value = word.partition("=")
        if eq and key.lower() == "caps":
            return frozenset(t.strip().lower() for t in value.split(",") if t.strip())
    return frozenset()


class ExtReply:
    """The answer to one `#` command (`#OK <CMD> k=v ...` / `#ERR <CMD>
    reason`), or the lack of one."""

    __slots__ = ("command", "ok", "fields", "reason", "line", "timed_out", "aborted")

    def __init__(self, command, ok, *, fields=None, reason="", line="",
                 timed_out=False, aborted=False):
        self.command = command
        self.ok = bool(ok)
        self.fields = dict(fields or {})
        self.reason = reason
        self.line = line
        self.timed_out = timed_out
        self.aborted = aborted

    @property
    def why(self):
        """The reason in a few words, for an operator sentence."""
        if self.ok:
            return "ok"
        if self.aborted:
            return "not sent: the probe is stopped"
        if self.timed_out:
            return "no reply"
        return self.reason or "refused"

    def __repr__(self):
        return f"<ExtReply {self.line or self.command} ok={self.ok}>"


class _ExtWaiter:
    __slots__ = ("command", "event", "reply")

    def __init__(self, command):
        self.command = command
        self.event = threading.Event()
        self.reply = None


#: The board's reason words for a refused HOME, in an operator's words.
_HOME_REASONS = {
    "no-home-sensor": "No home sensor on {axis}",
    "not-enabled": "its drives are not enabled",
    "busy": "the board is still moving",
}


class Probe(GamepadInput, IdleInterlock, Model):
    """A three-axis probe: one SerialPort, one Gamepad, one mode."""

    NAME = "Probe"
    IDENTITY = None
    #: Ceiling of both speeds, steps/s; 100 % on the dials. Per class.
    MAX_SPEED = MAX_SPEED
    NEEDS_PORT = True
    NEEDS_GAMEPAD = True
    #: stored count Param -> the physical view the operator edits instead
    #: (the Stepper Probe's um fields). The view carries the gate.
    VIEW_OF = {}
    #: The caps the SCHEMA assumes while the board has not answered yet (a
    #: Web card is built from the schema before the handshake ends). Never
    #: the wire's: no `#` byte goes out on an assumption. The base assumes
    #: none, so a board is today's until it says otherwise.
    ASSUMED_CAPS = frozenset()

    #: The extension channel (MEGA_STANDARD section 3), for ext1 boards only:
    #: the log level and host timeout set at every link-up, the heartbeat
    #: (four inside the 1 s window), and how long a `#` request waits.
    EXT_LOG_LEVEL = 2
    EXT_HOST_TIMEOUT_MS = 1000
    EXT_HEARTBEAT_INTERVAL = 0.25
    EXT_REPLY_TIMEOUT = 0.5
    #: After the board's own ZERO or HOMED, a jump to 0 is not a reset (L6).
    EXT_JUMP_GRACE = 0.5

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
        *_speed_params(400, MAX_SPEED),
        # The operator's dials: a view of the stored steps/s above, never a
        # second value (owner, 2026-10-07). Not seeded; see `_defaults`.
        Param("full_speed_pct", "int", default=13, minimum=0, maximum=100,
              label="Autonomous Speed", unit="%"),
        Param("man_full_speed_pct", "int", default=13, minimum=0, maximum=100,
              label="Manual Speed", unit="%"),
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

    #: Seconds without a POS line after which the board is "not answering".
    #: Every probe sketch prints POS unasked from `loop()` in every mode,
    #: disabled included (stepper/chuck every 100 ms, DC every 50 ms), so
    #: silence means the loop is not running -- the host's bytes are landing
    #: in a buffer nothing reads. Bench incident 2026-10-04 17:40: the
    #: Stepper Probe's loop hung mid-jog, the host streamed jog frames into
    #: the silence for 4 s, and FULL STOP reported "confirmed" because its
    #: bytes were written. 1.0 s is ten stepper/chuck periods (twenty DC):
    #: on that day every steady 5-s window read 9.8-10.2 lines/s, and the
    #: only dips were the ~1.5 s handshake at open, when the probe is
    #: DISABLED and nothing watches. At the incident it would have stopped
    #: the probe at ~17:40:15.0, before the full-deflection frame at 15.7.
    #: At 3200 steps/s, a second is the travel this costs before it trips.
    BOARD_SILENT_AFTER = 1.0

    #: L3: the firmware prints POS every 100 ms (50 ms on the DC board), so a
    #: usable link with no POS line for this long is a stalled stream.
    STREAM_STALL_SECONDS = 1.0
    #: L7: seconds between the sampler's `Health` debug lines.
    HEALTH_INTERVAL = 5.0
    #: L3: at most one "Packets Dropped" warning per this many seconds.
    DROPPED_WARN_INTERVAL = 10.0

    #: L6: a jump to exactly (0,0,0) from farther than this, within
    #: RESET_WINDOW, is a board that reset (its setup() zeroes the counts),
    #: not motion. The stepper/chuck firmware caps each axis at
    #: setMaxSpeed(1600 * microstepMode / 2) = 6400 steps/s (microstepMode
    #: 8) and prints every PRINT_INTERVAL = 100 ms, so one sample can move at
    #: most 640 counts; twice that allows for a late or merged sample. The
    #: DC board's encoder rate is not stated in its sketch: this is a
    #: stepper-derived number there, for the owner to judge at the bench.
    RESET_JUMP_COUNTS = 1280
    #: Two print intervals: 6400 steps/s x 0.2 s = 1280, so no real move
    #: inside the window can cover RESET_JUMP_COUNTS.
    RESET_WINDOW = 0.2

    # The idle interlock's INTERLOCK_TIMEOUT (300 s), INTERLOCK_POLL_INTERVAL
    # and IDLE_WARN_SECONDS (60 s) come from `model.idle.IdleInterlock`.

    def __init__(self, port=None, gamepad=None, sim=False):
        # Mode and the gated parameter store come first: `Panel.__init__`
        # assigns every declared default through the property setters below,
        # and those consult the mode. Construction always runs in DISABLED,
        # which no entry's `disabled_when` names, so nothing is refused here.
        self._mode = ProbeMode.DISABLED
        self._mode_lock = threading.RLock()
        #: L10 (SF-4): events raised while this thread holds `_mode_lock`
        #: wait here and are published once `_set_mode` has released it.
        self._outbox = threading.local()
        self._param_store = {}
        self._gates = {}
        # The extension channel's state (ext1 boards only; inert otherwise).
        self._ext_lock = threading.Lock()
        self._ext_waiters = {}            # COMMAND -> [_ExtWaiter]
        self._ext_replies = {}            # COMMAND -> latest ExtReply this link
        self._ext_events = collections.deque(maxlen=256)
        self._ext_epoch = None            # the link epoch configured
        self._ext_beat_at = 0.0
        self._ext_info = {}               # the latest #OK INFO fields
        self._ext_warned = set()
        self._homing = {axis: None for axis in AXES}   # None | starting | running
        self._home_phase = {axis: "" for axis in AXES}
        self._homed = {axis: False for axis in AXES}
        self._last_limit = {axis: "" for axis in AXES}
        self._limits_seen = set()
        self._jump_ok_until = 0.0
        super().__init__()

        self.port = self._build_port(port, sim)
        self._attach_gamepad(gamepad)

        # Position, sampled from the stream the firmware sends unasked.
        self._position = (0, 0, 0)
        self._position_time = None
        self._velocity = (0.0, 0.0, 0.0)
        self._samples_seen = 0
        # flake-coords section 6: the firmware's position counter restarts
        # every time the port comes up, so a stage position is meaningful
        # only within one epoch. Counted here, from the port's status.
        self._position_epoch = 0
        self._link_up = False

        # L3: the stream's health. `dropped` lines that were not a whole POS
        # line; `stalls` episodes of a usable link with no POS line.
        self._dropped = 0
        self._dropped_warned = 0          # the count at the last warning
        self._dropped_warned_at = None    # monotonic time of that warning
        self._stalls = 0
        self._stalled = False
        self._stream_since = None         # monotonic: the link became usable

        # Autonomous "stepping" is a *timed sub-state*, not a fifth boolean
        # (RC-3 item 2). `is_stepping` used to be set by the step command and
        # cleared only by a stop, so a move that finished normally left it True
        # forever -- and the interlock deferred on it, which is how an
        # energized probe sat idle indefinitely (STEPPER-6, DC-1).
        self._moving_deadline = None
        self._coil_kill_reported = False
        #: (mode, seconds silent) when the watchdog is the one stopping the
        #: probe, so the halt's report says why. None otherwise.
        self._silent_trip = None
        #: L9 (SF-5): bumped by every `_halt_hardware`, so a mode entry can
        #: tell that a stop landed between its enable and its mode write.
        self._halt_generation = 0

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
        if any(self._homing.values()):
            return True               # a HOME this model started (ext1)
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
        """`_set_mode_locked`, then publish what it raised with no lock held
        (L10, SF-4)."""
        depth = getattr(self._outbox, "depth", 0)
        if depth == 0:
            self._outbox.pending = []
        self._outbox.depth = depth + 1
        try:
            return self._set_mode_locked(target, reason, quiesce)
        finally:
            self._outbox.depth = depth
            if depth == 0:
                pending, self._outbox.pending = self._outbox.pending, []
                for publish in pending:
                    try:
                        publish()
                    except Exception as exc:
                        events.debug("Publish Failed", repr(exc),
                                     source=self.NAME, exception=exc)

    def _publish_later(self, publish):
        if getattr(self._outbox, "depth", 0):
            self._outbox.pending.append(publish)
        else:
            publish()

    def _set_mode_locked(self, target, reason, quiesce=True):
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
            halts = self._halt_generation
            if target is ProbeMode.DISABLED:
                return self._deenergize(reason)
            if target is ProbeMode.FAULT:
                self._refuse("Fault is not a mode you can select.")
            if previous is ProbeMode.FAULT or self.is_faulted:
                # L8 (SF-1): FAULT is an unconfirmed disable. Arming out of it
                # sent 'e' and cleared the fault on the strength of nothing;
                # only a confirmed 'd' (a Stop, or leaving the mode) ends it.
                self._refuse(f"{self.NAME} is in fault: its last disable was "
                             "not confirmed. Stop it; a confirmed stop clears "
                             "the fault.")
            if target is ProbeMode.MANUAL and not self._is_gamepad_bound:
                # Before the enable, never after: checking afterwards can
                # revert the Python flag, but the firmware has already been
                # told to energize.
                self._refuse("Manual mode needs a gamepad. Choose one under "
                             "Gamepad first.")
            self._guard(f"Mode change to {target.value}")
            silent_for = self._silent_for()
            if silent_for is not None:
                # 2026-10-04 17:41:30: after clearing the stop, manual mode
                # was entered again on the still-hung board and the host
                # jogged it. A board that is not answering is never armed.
                self._refuse(f"The {self.NAME} is not answering: no position "
                             f"report for {silent_for:.0f} s. Reset the board "
                             "(or cut its power and reconnect it), then try "
                             "again.")
            if self._ext_on:
                self._check_ext_ready()

            self._energize(reason)
            if target is ProbeMode.MANUAL and previous is not ProbeMode.MANUAL:
                # Entering manual starts with no step pending (D3): a press
                # parked before this point was not made in manual mode.
                self._drain_edges()
            if self._halt_generation != halts or self._estop.is_set():
                # L9 (SF-5): a stop from another thread (the Web, the
                # watchdog) landed after the enable. Writing the target now
                # would overwrite its DISABLED and leave the probe latched in
                # MANUAL, and Clear would resume the jog stream. Back out
                # through the one de-energize, which ends on the wire with
                # the zero frame and 'd'.
                events.debug("Mode Entry Backed Out", f"a stop landed while "
                             f"entering {target.value}", source=self.NAME)
                self._deenergize(f"stop during entry to {target.value}")
                self._guard(f"Mode change to {target.value}")
                self._refuse(f"A stop arrived while the {self.NAME} was "
                             f"entering {target.value} mode, so it stayed "
                             "disabled. Enter the mode again.")
            self._mode = target
            self._moving_deadline = None
            self._clear_ext_motion()
            self._start_interlock()
            self._touch()
            events.debug("Mode", f"{previous.value} -> {target.value} ({reason}) "
                         f"at {self._position}",
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
            self._publish_later(lambda exc=exc: events.warn(
                "Enable Failed", "The enable did not reach the board. Check "
                "the connection and try again.", source=self.NAME,
                exception=exc))
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
        self._clear_ext_motion()
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
        events.debug("Mode", f"{previous.value} -> disabled ({reason}) "
                     f"at {self._position}",
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
        self._clear_ext_motion()
        events.debug("Mode", f"{previous.value} -> fault ({reason}) "
                     f"at {self._position}",
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
        # Read before the writes: whether the board was answering when the
        # stop went out. Every byte below is still sent; silence changes what
        # is reported, never what is attempted.
        silent_for = self._silent_for()
        trip, self._silent_trip = self._silent_trip, None
        self._halt_generation += 1
        self._moving_deadline = None
        self._clear_ext_motion()
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
            # L5: a confirmed 'd' is exactly what a FAULT was waiting for
            # (the schema's "Fault" comment says the stop is the way out).
            self._clear_fault()
            if previous is not ProbeMode.DISABLED:
                events.debug("Mode", f"{previous.value} -> disabled (halt) "
                             f"at {self._position}",
                             source=self.NAME)
        elif self._link_loss_in_progress:
            # L1: the link itself is gone. The loss is reported by the port,
            # with this outcome in it; FAULT would block the recovery.
            events.debug("Halt Not Confirmed", "the link is lost; leaving the "
                         "mode as DISABLED, not FAULT", source=self.NAME)
        elif self.port is not None:
            self._enter_fault("The stop did not reach the board, so the "
                              "motors may still be powered. Treat it as live "
                              "and check the connection.")
        if not self.can_kill_coils:
            self._report_no_coil_kill()
        if silent_for is not None:
            # "Written" is the host's half only. A board whose loop is not
            # running has not read the stop, so it is not confirmed.
            self._report_not_answering(silent_for, trip)
            return False
        return all(landed.values())

    def _report_not_answering(self, silent_for, trip):
        if trip is not None:
            mode, _ = trip
            why = (f"The {self.NAME} stopped reporting its position while in "
                   f"{mode} mode (nothing for {silent_for:.1f} s), so the "
                   "station stopped it.")
        else:
            why = (f"The stop was sent to the {self.NAME}, but it has not "
                   f"reported its position for {silent_for:.1f} s.")
        events.debug("Board Not Answering",
                     f"no POS for {silent_for:.2f} s (threshold "
                     f"{self.BOARD_SILENT_AFTER} s); stop bytes written, not "
                     f"acknowledged; watchdog={trip is not None}",
                     source=self.NAME)
        events.error("Board Not Answering",
                     f"{why} The board is not answering, so nothing confirms "
                     "the stop and it may still be moving. Cut its power or "
                     "reset the board, then clear the stop.",
                     source=self.NAME)

    def _leave_mode_for_link_loss(self, landed):
        """L1: a lost link leaves the mode as DISABLED, never FAULT (FAULT
        is the needs-a-person latch and would block the recovery). The stop
        has already been attempted on the still-open handle."""
        with self._mode_lock:
            previous = self._mode
            self._moving_deadline = None
            self._clear_ext_motion()
            self._stop_interlock()
            if previous is not ProbeMode.FAULT:
                self._mode = ProbeMode.DISABLED
        events.debug("Mode", f"{previous.value} -> {self._mode.value} (link "
                     f"lost; stop {'landed' if landed else 'NOT confirmed'}) "
                     f"at {self._position}",
                     source=self.NAME)

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
        try:
            written = bool(self.port.write(payload, abort_if=self._estop.is_set))
        except serial_device.TransportError as exc:
            if not self._is_link_down():
                raise
            # L1: the link is lost and its owner stop is on its way. Raising
            # here would reach the pump's fault hook and turn a recoverable
            # loss into FAULT (and end the pump).
            events.debug("Jog Not Sent", f"link lost: {exc}", source=self.NAME,
                         every=1.0)
            return False
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
        health_at = time.monotonic()
        while not self._threads_stop.wait(self.SAMPLE_INTERVAL):
            if time.monotonic() - health_at >= self.HEALTH_INTERVAL:
                health_at = time.monotonic()
                self._log_health()
            try:
                position = self._read_position()
                # The loop is alive AND its read worked: only now is the
                # heartbeat honest (L3). Data freshness is position_age.
                self._touch()
                self._check_stream(time.monotonic())
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
            # The `#` channel (ext1 boards only): configure on link-up, act
            # on what the board reported, keep its host timeout fed.
            try:
                self._service_ext()
            except Exception as exc:
                events.debug("Extension Failed", repr(exc), source=self.NAME,
                             exception=exc, every=5.0)
            # Outside the try: a read that raises (the port went away) is
            # silence too, and the watchdog must still see it.
            try:
                self._watch_board()
            except Exception as exc:
                events.debug("Board Watch Failed", repr(exc), source=self.NAME,
                             exception=exc, every=5.0)

    def _silent_for(self):
        """Seconds since the last POS line when that exceeds
        BOARD_SILENT_AFTER, else None.

        None as well before the first line: a board that has never reported
        (just opened, a sketch without the stream, SIM) has no silence to
        measure, and calling it silent would refuse every mode."""
        last = self._position_time
        if last is None:
            return None
        age = time.monotonic() - last
        return age if age > self.BOARD_SILENT_AFTER else None

    @property
    def board_silent(self):
        """True while the board has stopped reporting its position."""
        return self._silent_for() is not None

    def _watch_board(self):
        """Stop the probe if the board stops answering while it is driven.

        Run by the sampler right after a drain, so a stall of the sampler
        itself cannot trip it: what was buffered has just been read. The
        stop is the model's own latch (`estop`): an explicit, confirmed
        operator clear, and while the board stays silent no mode can be
        entered again (`_set_mode`)."""
        if self._mode not in _DRIVEN or self._estop.is_set():
            return
        silent_for = self._silent_for()
        if silent_for is None:
            return
        self._silent_trip = (self._mode.value, silent_for)
        events.debug("Board Silent", f"no POS for {silent_for:.2f} s in "
                     f"{self._mode.value} mode; stopping", source=self.NAME)
        self.estop()

    #: Port statuses under which the firmware is running a counter.
    LINK_UP = ("verified", "unverified", "simulated")

    def _track_epoch(self):
        """A new `position_epoch` each time the port comes up again (a
        reconnect restarts the firmware's counter). Called on every sample."""
        up = getattr(self.port, "status", None) in self.LINK_UP
        if up and not self._link_up:
            self._position_epoch += 1
        self._link_up = up

    @property
    def position_epoch(self):
        """How many times the link has come up since construction: positions
        from different epochs are not comparable (the Sample DB invalidates
        a registration when it changes)."""
        return self._position_epoch

    def _read_position(self):
        """The latest complete `POS:x,y,z` line, or None. Never blocks."""
        if self.port is None:
            return None
        self._track_epoch()
        latest = None
        for _ in range(self.MAX_DRAIN_LINES):
            line = self.port.read_line(timeout=0)
            if not line:
                break
            if isinstance(line, bytes):
                # "replace", not "ignore": a stray 0xFF must stay visible as
                # garble, not vanish and leave a clean-looking line.
                line = line.decode("utf-8", errors="replace")
            line = line.strip()
            if not line or line.startswith("DEV:"):
                continue   # the handshake's answer is not a dropped packet
            if line.startswith("#") and self._ext_on:
                self._on_ext_line(line)
                continue
            if not line.startswith("POS:"):
                if line.isascii() and line.isprintable():
                    # The board's own words (a boot banner, a log line): kept
                    # in the log, not garble. Mirrors the axis link.
                    events.debug("Board Says", line[:200], source=self.NAME)
                else:
                    self._note_dropped(line)
                continue
            parts = line[4:].split(",")
            if len(parts) != 3:
                self._note_dropped(line)
                continue
            try:
                latest = tuple(int(part) for part in parts)
            except ValueError:
                self._note_dropped(line)   # malformed line, skip
                continue
        self._warn_dropped()
        return latest

    def _note_dropped(self, line):
        self._dropped += 1
        events.debug("Packet Dropped", f"#{self._dropped}: {line[:80]!r}",
                     source=self.NAME)

    def _warn_dropped(self):
        """One warning when the count has risen, at most one per
        DROPPED_WARN_INTERVAL."""
        if self._dropped <= self._dropped_warned:
            return
        now = time.monotonic()
        if (self._dropped_warned_at is not None
                and now - self._dropped_warned_at < self.DROPPED_WARN_INTERVAL):
            return
        new = self._dropped - self._dropped_warned
        self._dropped_warned, self._dropped_warned_at = self._dropped, now
        events.warn("Packets Dropped", f"{self.NAME} received {new} garbled "
                    f"line(s) from its board ({self._dropped} this session). "
                    "Check the cable if this keeps rising.", source=self.NAME)

    def _check_stream(self, now):
        """L3: a usable link with no POS line for STREAM_STALL_SECONDS is a
        stalled stream. One warning per episode; SIM never streams."""
        status = getattr(self.port, "status", None)
        if status not in ("verified", "unverified"):
            self._stream_since = None
            self._stalled = False
            return
        if self._stream_since is None:
            self._stream_since = now
        last = max(self._position_time or 0.0, self._stream_since)
        silent = now - last
        if self._stalled or silent < self.STREAM_STALL_SECONDS:
            return
        self._stalled = True
        events.debug("Position Stream Stalled", f"no POS line for {silent:.2f} s "
                     f"with the link {status}", source=self.NAME)
        events.warn("Position Stream Stalled", f"{self.NAME} has sent no "
                    f"position for {self.STREAM_STALL_SECONDS:g} s; the link "
                    "is up. Check the board.", source=self.NAME)

    def _log_health(self):
        """L7: one line with everything the next bench occurrence needs.
        Never raises (it runs inside the sampler)."""
        try:
            pump = self._thread("gamepad")
            sampler = self._thread("sample")
            text = (f"mode={self.mode_name} "
                    f"link={getattr(self.port, 'status', None)} "
                    f"position={self._position} "
                    f"position_age={self.position_age} "
                    f"idle_remaining={self.idle_remaining} "
                    f"gate_open={self._is_gate_open} "
                    f"pad_bound={self._is_gamepad_bound} "
                    f"sampler_alive={bool(sampler and sampler.is_alive())} "
                    f"pump_alive={bool(pump and pump.is_alive())} "
                    f"latched={self.is_estopped} fault={self.fault!r}")
        except Exception as exc:
            text = f"unavailable: {exc!r}"
        events.debug("Health", text, source=self.NAME)

    def _check_reset(self, position, previous, previous_time, now):
        """L6: warn when the position snaps to zero while enabled. Warning
        only: the mode is not touched (a false positive mid-move would be a
        stop the operator did not ask for)."""
        if time.monotonic() < self._jump_ok_until:
            return     # the board's own ZERO or HOMED: a jump the operator asked for
        if (tuple(position) != (0, 0, 0) or previous_time is None
                or self._mode is ProbeMode.DISABLED
                or now - previous_time > self.RESET_WINDOW
                or max(abs(v) for v in previous) <= self.RESET_JUMP_COUNTS):
            return
        events.debug("Board Reset Suspected", f"{previous} -> (0, 0, 0) in "
                     f"{(now - previous_time) * 1000:.0f} ms in mode "
                     f"{self._mode.value}", source=self.NAME)
        events.warn(events.BOARD_RESET_SUSPECTED, f"{self.NAME}'s position "
                    "snapped to zero while enabled; the board may have reset "
                    "and its drivers are off. Leave the mode and enter it "
                    "again.", source=self.NAME, ack=True)

    def _link_stream_state(self):
        return {"dropped": int(self._dropped), "stalls": int(self._stalls),
                "stalled": bool(self._stalled)}

    @property
    def dropped(self):
        return self._dropped

    def _note_position(self, position):
        """Record one sample, and the velocity between it and the last one.

        Velocity is computed **only between two distinct samples**, so it is
        never a division by a zero interval and never survives as a stale
        number: an unchanged position between two POS lines is a real zero.
        """
        now = time.monotonic()
        previous, previous_time = self._position, self._position_time
        moved = position != previous
        self._check_reset(position, previous, previous_time, now)
        if previous_time is not None and now > previous_time:
            span = now - previous_time
            self._velocity = tuple(
                (new - old) / span for new, old in zip(position, previous))
        self._position = position
        self._position_time = now
        self._samples_seen += 1
        self._touch()
        if self._stalled:
            self._stalled = False
            self._stalls += 1
            events.debug("Position Stream Resumed", f"stall #{self._stalls} "
                         f"ended at {position}", source=self.NAME)
            events.info("Position Stream Resumed", f"{self.NAME} is sending "
                        "its position again.", source=self.NAME)
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
        `levels` arrives with the drained edges already merged in.

        A board that is not answering is sent neutral, never the stick: the
        sampler's watchdog is what stops the probe, and this keeps a
        non-zero frame off the wire even if that watchdog has not run."""
        if levels and self.board_silent:
            events.debug("Jog Held", "board not answering; sending neutral",
                         source=self.NAME, every=1.0)
            levels = {}
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


    # -- the extension channel (MEGA_STANDARD, ext1 boards only) ------------
    @property
    def caps(self):
        """The board's capability tokens, from its identity answer: a
        frozenset (empty for a board that lists none), or None while it
        has not answered."""
        return caps_of(getattr(getattr(self, "port", None), "identity", None))

    @property
    def _ext_on(self):
        """The board listed `ext1`: the only condition for a `#` byte."""
        caps = self.caps
        return bool(caps) and EXT in caps

    def _schema_caps(self):
        """The caps the schema draws from: the board's, or, before it has
        answered, the class's assumption (never used for the wire)."""
        caps = self.caps
        return self.ASSUMED_CAPS if caps is None else caps

    @property
    def _ext_shown(self):
        return EXT in self._schema_caps()

    def _features_shown(self):
        caps = self._schema_caps()
        if EXT not in caps:
            return frozenset()
        return frozenset(f for f in EXT_FEATURES if f in caps)

    def _with_ext(self, built):
        """The schema, plus the standard features the caps list (section 6).
        A board without ext1 gets `built` back untouched: today's schema."""
        if not self._ext_shown:
            return built
        features = self._features_shown()
        sections = built["sections"]
        if "home" in features:
            elements = [sch.button(f"Zero {axis} here", "zero_axis", args=(axis,),
                                   disabled_when=("latched", "fault"))
                        for axis in AXES]
            for axis in AXES:
                home = sch.button(f"Home {axis}", "home_axis", args=(axis,),
                                  disabled_when=("manual", "latched", "fault"))
                home["enabled_by"] = f"{axis.lower()}_home_ready"
                home["enabled_by_reason"] = f"No home sensor on {axis}"
                elements.append(home)
            elements.append(sch.readonly("Homed:", "homed_text"))
            section = sch.section("Zero and home", *elements, tier=2,
                                  disclosure=f"Configure {self.NAME}")
            at = next((i for i, s in enumerate(sections) if s["title"] == "Configuration"),
                      len(sections) - 1)
            sections.insert(at, section)
        diagnostics = [sch.readonly("Board:", "board_text")]
        if "limits" in features:
            diagnostics.append(sch.readonly("Limit switches:", "limits_text"))
        if "home" in features:
            diagnostics.append(sch.readonly("Homing:", "home_text"))
        found = next((s for s in sections if s["title"] == "Diagnostics"), None)
        if found is None:
            sections.insert(len(sections) - 1, sch.section(
                "Diagnostics", *diagnostics, tier=3, disclosure="Diagnostics"))
        else:
            found["elements"].extend(diagnostics)
        return built

    def _clear_ext_motion(self):
        """Nothing the model started on the board is in flight any more (a
        stop, a mode change, a fault): a late HOME FAIL is not news."""
        for axis in AXES:
            self._homing[axis] = None

    def _service_ext(self):
        """The sampler's turn at the `#` channel: configure a link that came
        up, act on what the board reported, keep its host timeout fed."""
        if not self._ext_on or not self._link_up:
            return
        if self._ext_epoch != self._position_epoch:
            self._ext_link_up()
        while self._ext_events:
            self._on_ext_event(self._ext_events.popleft())
        now = time.monotonic()
        if now - self._ext_beat_at >= self.EXT_HEARTBEAT_INTERVAL:
            self._ext_beat_at = now
            self._ext_send("HB")

    def _ext_link_up(self):
        """Once per link-up (a reconnected board may have rebooted and
        forgotten everything): what the last board said is forgotten, then
        INFO, the log level and the host timeout, in the standard's order."""
        self._ext_epoch = self._position_epoch
        with self._ext_lock:
            self._ext_replies.clear()
        self._ext_events.clear()
        self._ext_info = {}
        self._ext_warned.clear()
        self._limits_seen.clear()
        for axis in AXES:
            self._homing[axis] = None
            self._home_phase[axis] = ""
            self._homed[axis] = False
            self._last_limit[axis] = ""
        for command in ("INFO", f"LOG {self.EXT_LOG_LEVEL}",
                        f"HOSTTIMEOUT {self.EXT_HOST_TIMEOUT_MS}"):
            if not self._ext_send(command):
                self._ext_epoch = None          # try again on the next pass
                return
        self._ext_beat_at = time.monotonic()
        events.debug("Board Configured", f"caps={sorted(self.caps or ())} link epoch "
                     f"{self._position_epoch}", source=self.NAME)

    def _ext_line(self, command):
        return f"#{command}\n".encode("ascii")

    def _ext_send(self, command):
        """One `#` line on the ordinary lane, not waiting for its reply.
        -> True when written. Never to a board without ext1."""
        if self.port is None or not self._ext_on:
            return False
        try:
            return bool(self.port.write(self._ext_line(command)))
        except serial_device.TransportError as exc:
            events.debug("Extension Not Sent", f"{command!r}: {exc}",
                         source=self.NAME, every=5.0)
            return False

    def _ext_reply(self, command):
        """The latest reply to `command` on this link, or None."""
        with self._ext_lock:
            return self._ext_replies.get(str(command).upper())

    def _ext_request(self, command, *, abort_if=None, timeout=None):
        """Send one `#` command and wait for its one reply. -> ExtReply.
        Never raises for a refusal, a silent board, an abort or a failed
        write (the reply says which). The sampler reads the reply; with
        no sampler running, the wait reads the link itself."""
        word = command.split()[0].upper()
        if self.port is None or not self._ext_on:
            return ExtReply(word, False, reason="the board has no extension channel")
        waiter = _ExtWaiter(word)
        with self._ext_lock:
            self._ext_waiters.setdefault(word, []).append(waiter)
        try:
            written = self.port.write(self._ext_line(command), abort_if=abort_if)
        except serial_device.TransportError as exc:
            self._ext_cancel(waiter)
            return ExtReply(word, False, reason=f"the link failed: {exc}")
        if not written:
            self._ext_cancel(waiter)
            return ExtReply(word, False, aborted=True)
        budget = self.EXT_REPLY_TIMEOUT if timeout is None else timeout
        deadline = time.monotonic() + budget
        while not waiter.event.wait(0.005):
            if time.monotonic() >= deadline:
                self._ext_cancel(waiter)
                if waiter.event.is_set():
                    return waiter.reply
                return ExtReply(word, False, timed_out=True)
            sampler = self._thread("sample")
            if sampler is None or not sampler.is_alive():
                try:
                    position = self._read_position()
                    if position is not None:
                        self._note_position(position)
                except Exception as exc:
                    events.debug("Read Failed", repr(exc), source=self.NAME,
                                 every=5.0)
        return waiter.reply

    def _ext_cancel(self, waiter):
        with self._ext_lock:
            pending = self._ext_waiters.get(waiter.command, [])
            if waiter in pending:
                pending.remove(waiter)

    def _on_ext_line(self, line):
        """One `#` line from the board, in wire order (the sampler's read).
        A reply wakes its request now; an event waits for `_service_ext`."""
        head, _, rest = line[1:].strip().partition(" ")
        head = head.upper()
        if head in ("OK", "ERR"):
            words = rest.split()
            command = words[0].upper() if words else ""
            if head == "OK":
                found = dict(w.split("=", 1) for w in words[1:] if "=" in w)
                reply = ExtReply(command, True, fields=found, line=line)
            else:
                reply = ExtReply(command, False, reason=" ".join(words[1:]), line=line)
            with self._ext_lock:
                self._ext_replies[command] = reply
                waiting = self._ext_waiters.pop(command, [])
            if command != "HB":
                events.debug("Board Reply", line[:200], source=self.NAME)
            if command == "INFO" and reply.ok:
                self._take_info(reply.fields)
            if not reply.ok and command in ("INFO", "LOG", "HOSTTIMEOUT"):
                self._warn_once(command, f"The {self.NAME} refused #{command} "
                                f"({reply.reason or 'no reason'}).")
            for waiter in waiting:
                waiter.reply = reply
                waiter.event.set()
        elif head == "EVT":
            events.debug("Board Event", rest[:200], source=self.NAME)
            self._ext_events.append(rest)
        else:
            events.debug("Board Says", line[:200], source=self.NAME)

    def _warn_once(self, key, text):
        if key in self._ext_warned:
            return
        self._ext_warned.add(key)
        events.warn("Board Refused Setup", text, source=self.NAME)

    def _take_info(self, fields):
        self._ext_info = dict(fields)
        for axis in AXES:
            homed = fields.get(f"{axis.lower()}_homed")
            if homed in ("0", "1"):
                self._homed[axis] = homed == "1"

    def _check_ext_ready(self):
        """Before anything is enabled on an ext1 board: its host timeout is
        armed, so a station that goes quiet stops it (section 4). Asked
        again here when the link-up's answer has not arrived."""
        reply = self._ext_reply("HOSTTIMEOUT")
        if reply is None or not reply.ok:
            reply = self._ext_request(f"HOSTTIMEOUT {self.EXT_HOST_TIMEOUT_MS}")
        if not reply.ok:
            self._refuse(f"The {self.NAME} did not accept HOSTTIMEOUT "
                         f"{self.EXT_HOST_TIMEOUT_MS} ({reply.why}), so it would not "
                         "stop itself if the station went quiet. Reset the board, "
                         "then try again.")

    # -- what the board reports --------------------------------------------
    def _on_ext_event(self, text):
        """One `#EVT` line (without the prefix), already logged as the board
        said it. The ones the station acts on are also said to the operator."""
        words = text.split()
        kind = words[0].upper() if words else ""
        found = dict(w.split("=", 1) for w in words[1:] if "=" in w)
        bare = [w for w in words[1:] if "=" not in w]
        axis = next((w.upper() for w in bare if w.upper() in AXES), None)
        if kind == "FAULT":
            what = next((w for w in bare if w.upper() not in AXES), "no reason given")
            if axis:
                self._homing[axis] = None
            self._board_fault(what, axis)
        elif kind == "LIMIT" and axis:
            switch = next((w for w in bare if w.lower().startswith("ls")), "a switch")
            self._board_limit(axis, switch.upper(), found)
        elif kind == "HOMED" and axis:
            self._homing[axis] = None
            self._homed[axis] = True
            self._home_phase[axis] = "homed"
            # Its jump to 0 is neither a reset (L6) nor a move to wait out.
            self._jump_ok_until = time.monotonic() + self.EXT_JUMP_GRACE
            if not any(self._homing.values()):
                self._moving_deadline = None
            events.info("Axis Homed", f"Axis {axis} of the {self.NAME} is homed (its "
                        f"reference edge was at {found.get('edge', '?')} steps).",
                        source=self.NAME)
        elif kind == "HOME" and axis and bare and bare[0].upper() == "FAIL":
            reason = found.get("reason", "no reason given")
            ours = self._homing[axis] is not None
            self._homing[axis] = None
            self._home_phase[axis] = f"failed ({reason})"
            if ours:
                events.warn("Home Failed", f"Axis {axis} of the {self.NAME} did not "
                            f"home ({reason}). It stopped and is holding.",
                            source=self.NAME)
        elif kind == "HOME" and axis and "phase" in found:
            self._home_phase[axis] = found["phase"]
            if self._homing[axis] == "starting":
                self._homing[axis] = "running"
        elif kind == "REFUSED":
            reason = found.get("reason", "no reason given")
            if self._mode is ProbeMode.AUTO:
                self._moving_deadline = None     # the frame did not run
            where = f"axis {axis}" if axis else "an axis"
            events.warn("Move Refused", f"The {self.NAME} refused to move {where} "
                        f"({reason}), so nothing moved.", source=self.NAME)

    def _board_fault(self, what, axis):
        """A FAULT latches the stop, as on the XYZ Stage: a person looks
        before anything moves again."""
        where = f" on axis {axis}" if axis else ""
        if self._mode is ProbeMode.DISABLED or self._estop.is_set():
            events.warn("Board Fault", f"The {self.NAME} reported {what}{where} while "
                        "it was not running. Check it before you enable it.",
                        source=self.NAME)
            return
        events.debug("Board Fault", f"{what}{where} in {self.mode_name}; stopping",
                     source=self.NAME)
        self.estop()
        check = f"axis {axis}" if axis else "the board"
        events.error("Board Fault", f"The {self.NAME} reported a fault ({what}{where}), "
                     f"so the station stopped it. Check {check}, then clear the stop.",
                     source=self.NAME)

    def _board_limit(self, axis, switch, found):
        """The board's interlock already stopped that axis. A Step must not
        go on along the others: it is stopped too (the XYZ Stage's rule)."""
        if found.get("seen") == "1":
            self._limits_seen.add(axis)
            events.info("Limit Switch Seen", f"Axis {axis} of the {self.NAME} has a "
                        f"limit switch after all ({switch} tripped): its interlock is "
                        "on for this session.", source=self.NAME)
            self._ext_send("INFO")
            return
        position = found.get("pos", "?")
        self._last_limit[axis] = f"{switch} at {position}"
        if self._homing[axis] is not None:
            events.debug("Limit", f"axis {axis} {switch} at {position} while homing",
                         source=self.NAME)
            return
        stopped = ""
        if (self._mode is ProbeMode.AUTO and self._moving_deadline is not None
                and self.is_moving):
            self._moving_deadline = None
            try:
                self._send_zero_frame(f"limit {switch} on axis {axis}")
                stopped = " The Step was stopped there, on every axis."
            except Exception as exc:
                events.debug("Stop Frame Failed", repr(exc), source=self.NAME,
                             exception=exc)
        extra = ""
        if found.get("learned") == "travel":
            extra = " It learned which end that switch guards from the travel."
        elif found.get("pressed_both_ways") == "1":
            extra = " The switch reads pressed both ways: check it."
        events.warn("Limit Reached", f"Axis {axis} of the {self.NAME} reached its "
                    f"limit switch {switch} at {position} steps and stopped.{stopped}"
                    f"{extra}", source=self.NAME)
        self._ext_send("INFO")                  # its learned ends

    # -- zero and home -----------------------------------------------------
    def _ext_axis(self, axis):
        name = str(axis).strip().upper()
        if name not in AXES:
            self._refuse(f"{axis} is not an axis of the {self.NAME}.")
        return name

    def _require_ext(self, feature, what):
        caps = self.caps
        if caps is None:
            self._refuse(f"{what} is not available yet: the {self.NAME} has not "
                         "said what it can do. Wait for its link, then try again.")
        if EXT not in caps or feature not in caps:
            self._refuse(f"{what} is not available: this {self.NAME}'s firmware "
                         f"has no {feature.upper()}.")

    def _hardware(self, axis):
        """What INFO said about one axis: {tmc, limits, home} as True/False
        (None before INFO) and the learned ends of LS1 and LS2."""
        info, low = self._ext_info, axis.lower()

        def flag(key):
            value = info.get(f"{low}_{key}")
            return None if value is None else value == "1"

        def end(key):
            try:
                return int(info[f"{low}_{key}"])
            except (KeyError, ValueError):
                return None

        limits = flag("limits")
        if axis in self._limits_seen:
            limits = True
        return {"tmc": flag("tmc"), "limits": limits, "home": flag("home"),
                "ls1_end": end("ls1_end"), "ls2_end": end("ls2_end")}

    def _home_ready(self, axis):
        """False when the board's caps lack HOME or INFO says the axis has no
        home sensor; True otherwise (before INFO, the board decides)."""
        caps = self.caps
        if caps is not None and "home" not in caps:
            return False
        return self._hardware(axis)["home"] is not False

    def _axis_busy(self, axis):
        """Homing, a Step in flight, or jogging in manual. The board refuses
        a ZERO of a moving axis itself (`busy`); this says so first."""
        jogging = self.is_manual and abs(self._velocity[AXES.index(axis)]) > 0.0
        return bool(self._homing[axis] or self.is_moving or jogging)

    def _auto_motion_not_allowed(self):
        """The in-lock gate of motion the host starts in AUTO (a HOME):
        latched, or no longer in AUTO."""
        return self._estop.is_set() or self._mode is not ProbeMode.AUTO

    def zero_axis(self, axis, confirmed=False):
        """Zero here (`#ZERO`): this axis reads 0 where it stands. A homed
        axis asks first, since its home reference is replaced."""
        axis = self._ext_axis(axis)
        self._require_ext("home", f"Zero {axis}")
        self._guard(f"Zero {axis}")
        if self._axis_busy(axis):
            self._refuse(f"Axis {axis} is moving. Wait for it to stop, then zero it.")
        if self._homed[axis] and not confirmed:
            raise NeedsConfirm(f"Zero axis {axis} here?\n\nAxis {axis} is homed; "
                               "zeroing here replaces its home reference with this "
                               "position.", "zero_axis", args=(axis,))
        # Checked again inside the write lock, so a stop or a move that
        # starts after the checks above never meets a ZERO.
        reply = self._ext_request(f"ZERO {axis}", abort_if=lambda: (
            self._estop.is_set() or self._axis_busy(axis)))
        if not reply.ok:
            if reply.aborted and not self._estop.is_set():
                self._refuse(f"Axis {axis} is moving. Wait for it to stop, then "
                             "zero it.")
            self._refuse(f"Axis {axis} did not zero ({reply.why}).")
        self._homed[axis] = False
        # Its jump to 0 is neither a reset (L6) nor a move to wait out.
        self._jump_ok_until = time.monotonic() + self.EXT_JUMP_GRACE
        self._moving_deadline = None
        events.info("Axis Zeroed", f"Axis {axis} of the {self.NAME} reads 0 here now.",
                    source=self.NAME)
        return 0

    def home_axis(self, axis):
        """Home one axis (`#HOME`): the board's sequence against its home
        sensor. Enters AUTO (homing is motion the host started); the board
        reports the phases, then HOMED or HOME FAIL."""
        axis = self._ext_axis(axis)
        what = f"Home {axis}"
        self._require_ext("home", what)
        self._guard(what)
        if not self._home_ready(axis):
            self._refuse(f"{what} is not available: No home sensor on {axis}.")
        if self.is_manual:
            self._refuse(f"{what} is not available in manual mode. Leave manual "
                         "mode first.")
        if self.is_moving:
            self._refuse("The stage is still moving. Wait for it to stop, then home.")
        self._set_mode(ProbeMode.AUTO, f"home {axis}", quiesce=False)
        self._homing[axis] = "starting"
        self._home_phase[axis] = "starting"
        reply = self._ext_request(f"HOME {axis}", abort_if=self._auto_motion_not_allowed)
        if reply.ok:
            self._touch_activity()
            return "started"
        self._homing[axis] = None
        self._home_phase[axis] = f"not started ({reply.why})"
        if reply.aborted:                       # never written: nothing to stop
            if not self._estop.is_set():
                self._refuse(f"Axis {axis} did not start homing: the {self.NAME} "
                             "left autonomous mode first.")
            self._refuse(f"Axis {axis} would not start homing ({reply.why}).")
        if reply.timed_out:
            # Written, and no answer is no proof it is not homing: stop it.
            try:
                self._send_zero_frame(f"HOME {axis} unanswered")
            except Exception as exc:
                events.debug("Stop Frame Failed", repr(exc), source=self.NAME,
                             exception=exc)
            self._refuse(f"Axis {axis} did not answer HOME, so the {self.NAME} was "
                         "stopped.")
        words = _HOME_REASONS.get(reply.reason, reply.reason or "refused")
        self._refuse(f"Axis {axis} would not start homing: {words.format(axis=axis)}.")

    # -- what the page reads -----------------------------------------------
    @property
    def homed_text(self):
        return ", ".join(f"{axis} {'yes' if self._homed[axis] else 'no'}"
                         for axis in AXES)

    @property
    def home_text(self):
        return "; ".join(f"{axis} {self._home_phase[axis] or '-'}" for axis in AXES)

    @property
    def limits_text(self):
        def end(value):
            return {1: "+ end", -1: "- end"}.get(value, "end unknown")

        parts = []
        for axis in AXES:
            hw = self._hardware(axis)
            if hw["limits"] is None:
                text = f"{axis} ?"
            elif not hw["limits"]:
                text = f"{axis} none (no interlock)"
            else:
                text = f"{axis} LS1 {end(hw['ls1_end'])}, LS2 {end(hw['ls2_end'])}"
            if self._last_limit[axis]:
                text += f" (last: {self._last_limit[axis]})"
            parts.append(text)
        return "; ".join(parts)

    @property
    def board_text(self):
        caps = self.caps
        if caps is None:
            return "waiting for the board to say what it is"
        if EXT not in caps:
            return "the frame protocol only (no extension channel)"
        if not self._ext_info:
            return f"caps {', '.join(sorted(caps))}; waiting for INFO"
        parts = []
        for axis in AXES:
            hw = self._hardware(axis)
            words = ["driver ok" if hw["tmc"] else "driver missing (will not enable)",
                     "limit switches" if hw["limits"] else "no limit switches (no interlock)",
                     "home sensor" if hw["home"] else "no home sensor"]
            parts.append(f"{axis}: {', '.join(words)}")
        return (f"{self._ext_info.get('fw', '?')} proto {self._ext_info.get('proto', '?')}; "
                + "; ".join(parts))

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
            # The steps/s speeds have no control of their own any more: the
            # percent dial carries the gate for both representations.
            wanted = (_PCT_OF.get(name), self.VIEW_OF.get(name), name)
            self._gates[name] = next(
                (e for want in wanted if want for e in sch.elements(self.schema)
                 if e.get("model_attr") == want), None)
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
        # drawn; step sizes and brakes sit one disclosure away (the targets
        # moved up into the Autonomous group, 2026-10-07, below);
        # velocity, position age, the gamepad log and the per-model stop
        # are diagnostics. The gamepad CHOICE is tier 1 (owner, 2026-09-26,
        # Tier K): it is picked every session, right before Manual mode.
        #
        # The two control systems are two groups (owner, 2026-10-07: "a
        # division for autonomous and manual controls"): Autonomous holds
        # the Step's targets, its speed, its mode and Step; Manual holds the
        # gamepad, the jog speed and its mode. `layout="group"` draws each
        # one's title as a visible heading in the Web view (a tier-1 title
        # is otherwise for a screen reader only). The step sizes serve both
        # systems (a Step's distance and a D-pad press), so they stay in
        # Configure. Only the grouping moved: every element, gate and
        # command is the one it was.
        return self._with_ext(sch.schema(
            sch.section(
                "Position",
                sch.readonly("X:", "position_x", rail=True, unit="steps"),
                sch.readonly("Y:", "position_y", rail=True, unit="steps"),
                sch.readonly("Z:", "position_z", rail=True, unit="steps"),
            ),
            sch.section(
                "Autonomous",
                *[sch.entry(P[name].label + ":", name, P[name],
                            disabled_when=_MOTION_GATE)
                  for name in self.TARGET_PARAMS],
                # A slider beside the entry, never instead of it: the entry
                # keeps the precision. The slider's travel is a display range;
                # the Param still validates what is typed.
                sch.entry(P["full_speed_pct"].label + ":", "full_speed_pct",
                          P["full_speed_pct"], disabled_when=_MOTION_GATE,
                          slider=self.SPEED_SLIDER),
                sch.readonly("steps/s", "full_speed", secondary=True,
                             unit="steps/s"),
                # Per-device "System Power" and "Full Stop" toggles stay
                # removed: enable/disable is reachable through the mode
                # toggles, and the dashboard's global FULL STOP already
                # reaches every model.
                # F11: greyed out while latched. A latched probe is never in
                # AUTO or MANUAL (the halt leaves both), so the toggle's "off"
                # direction is not what this takes away.
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
                # D-5: the distances and the speed travel with the command and
                # are validated as a set. **Not** gated on "autonomous": a
                # second step while already AUTO is the normal way to work
                # (DC-6, review finding 5). `is_moving` is what refuses.
                # L8 (SF-1): greyed in fault as well; `_set_mode` refuses it.
                sch.button("Step", "step",
                           inputs=("x_dist", "y_dist", "z_dist", "full_speed_pct"),
                           role="go", disabled_when=("manual", "latched",
                                                     "fault")),
                layout="group",
            ),
            sch.section(
                "Manual",
                gamepad_choice,
                sch.entry(P["man_full_speed_pct"].label + ":", "man_full_speed_pct",
                          P["man_full_speed_pct"], disabled_when=_MANUAL_SPEED_GATE,
                          slider=self.SPEED_SLIDER),
                sch.readonly("steps/s", "man_full_speed", secondary=True,
                             unit="steps/s"),
                sch.toggle("Manual / Gamepad:", "is_manual", "set_mode",
                           "Manual mode (press to stop)", "Enter Manual Mode",
                           on_args=[ProbeMode.MANUAL.value],
                           off_args=[ProbeMode.DISABLED.value],
                           disabled_when=("latched", "fault")),
                # Declared so `run("extend_idle")` passes the allow-list; it
                # renders nothing. The views draw the countdown and its
                # Extend from `idle_remaining` in state (Tier N).
                {"type": "internal", "command": "extend_idle", "writable": False,
                 "role": "neutral"},
                layout="group",
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
        ))

    #: Every editable field, in the order the D-5 command set travels.
    #: DCProbe adds two. The two speeds (with a slider) and the Step's
    #: targets are tier 1, in their control system's group; the rest are the
    #: tier-2 Configuration.
    ENTRY_PARAMS = ("x_step", "y_step", "z_step", "x_dist", "y_dist", "z_dist",
                    "full_speed_pct", "man_full_speed_pct")
    SPEED_PARAMS = ("full_speed_pct", "man_full_speed_pct")
    TARGET_PARAMS = ("x_dist", "y_dist", "z_dist")
    #: The slider's travel: the whole dial, percent.
    SPEED_SLIDER = (0, 100)

    # -- the two representations of a speed --------------------------------
    @classmethod
    def pct_to_steps(cls, pct):
        """Steps/s for a percent of this class's ceiling; never below 1."""
        return max(1, int(float(pct) / 100.0 * cls.MAX_SPEED + 0.5))

    @classmethod
    def steps_to_pct(cls, steps):
        """The nearest whole percent of the ceiling (halves round up)."""
        return int(float(steps) / cls.MAX_SPEED * 100.0 + 0.5)

    def _defaults(self):
        seeded = super()._defaults()
        for pct in _PCT_OF.values():
            seeded.pop(pct, None)   # derived from the stored steps/s
        return seeded

    def _apply_inputs(self, inputs):
        """The stored steps/s stay writable by name (profiles, tests, older
        callers) though only the percent dials are drawn: a steps/s input is
        validated by its own Param and goes through the dial's gate."""
        if inputs and any(n in _PCT_OF for n in inputs):
            inputs = dict(inputs)
            for name in [n for n in inputs if n in _PCT_OF]:
                ok, value = self.PARAMS[name].parse(inputs.pop(name))
                if not ok:
                    raise Refused(value)
                if not self._is_enabled(self._gate_for(name)):
                    if self._same_value(getattr(self, name, None), value):
                        continue
                    raise Refused(f"{self.PARAMS[name].label} cannot be changed "
                                  f"in {self.mode_name} mode. Leave "
                                  f"{self.mode_name} mode to edit it.")
                setattr(self, name, value)
        super()._apply_inputs(inputs)

    @property
    def CONFIG_PARAMS(self):
        return tuple(n for n in self.ENTRY_PARAMS
                     if n not in self.SPEED_PARAMS + self.TARGET_PARAMS)

    @property
    def state(self):
        snapshot = super().state
        snapshot.update({
            "position": list(self._position),
            "position_time": self._position_time,
            "position_age": self.position_age,
            "position_epoch": self._position_epoch,
            "velocity": list(self._velocity),
            "is_moving": self.is_moving,
            "is_enabled": self.is_enabled,
            "can_kill_coils": self.can_kill_coils,
            "board_silent": self.board_silent,
        })
        if self._ext_shown:
            caps = self.caps
            features = self._features_shown()
            if "home" in features:
                # The Home buttons' `enabled_by` booleans (a view greys them
                # from these; the Panel refuses with the element's reason).
                for axis in AXES:
                    snapshot["values"][f"{axis.lower()}_home_ready"] = self._home_ready(axis)
            snapshot["board"] = {
                "caps": sorted(caps) if caps is not None else None,
                "fw": self._ext_info.get("fw"), "proto": self._ext_info.get("proto"),
                "axes": {axis: self._hardware(axis) for axis in AXES},
                "homed": dict(self._homed),
                "homing": dict(self._homing),
            }
        return snapshot

    # -- refusals ---------------------------------------------------------
    def _refuse(self, reason):
        events.debug("Refused", reason, source=self.NAME)
        raise Refused(reason)


def _pct_property(steps_name):
    """The percent dial over a stored steps/s speed: one value, two views.

    Writing the percent the stored speed already displays changes nothing, so
    a Step that re-sends "13" leaves 400 steps/s at 400 instead of 416."""

    def getter(self):
        return self.steps_to_pct(self.PARAMS[steps_name].coerce(
            self._param_store.get(steps_name)))

    def setter(self, value):
        pct = int(float(value))
        if pct == getter(self):
            return
        setattr(self, steps_name, self.pct_to_steps(pct))

    return property(getter, setter)


for _name in Probe.PARAMS:
    if _name not in _PCT_OF.values():
        setattr(Probe, _name, Probe._gated_param(_name))
for _steps, _pct in _PCT_OF.items():
    setattr(Probe, _pct, _pct_property(_steps))
for _axis in AXES:
    # The Home buttons' `enabled_by` (ext1 boards with HOME).
    setattr(Probe, f"{_axis.lower()}_home_ready",
            property(lambda self, _a=_axis: self._home_ready(_a)))
del _name, _steps, _pct, _axis


#: um per count of the Stepper Probe's axes: the repo's one constant
#: (`sample_frame.UM_PER_COUNT`), a 1 mm lead screw over 1600 counts/rev
#: (200 full steps x 8 microsteps, owner, 2026-10-09). The lead is NOT
#: measured; the operator is told once (`StepperProbe.scale_note`).
STEPPER_UM_PER_COUNT = UM_PER_COUNT["stepper"]


def um_to_counts(um, um_per_unit=STEPPER_UM_PER_COUNT):
    """Whole units for a length in um (counts by default): the nearest, exact
    halves away from zero (0.9375 um is 1.5 counts -> 2; -0.9375 -> -2). The
    ratio is rounded to 9 places first so float noise never decides a half."""
    scaled = round(abs(float(um)) / um_per_unit, 9)
    counts = int(math.floor(scaled + 0.5))
    return -counts if float(um) < 0 else counts


def counts_to_um(counts):
    """A count's length in um (exact to 6 places: 0.625 = 5/8)."""
    return round(float(counts) * STEPPER_UM_PER_COUNT, 6)


def _um_property(counts_name, view_name):
    """A physical view over a stored count Param: one value, two views.

    Typing um converts to whole counts and stores THOSE (through the count's
    own gated setter, so the mode gate and strict parse apply unchanged); the
    getter reports the achieved length. Writing the length already displayed
    changes nothing, so a Step that re-sends the readout keeps the counts."""

    def getter(self):
        return counts_to_um(self.PARAMS[counts_name].coerce(
            self._param_store.get(counts_name)))

    def setter(self, value):
        ok, um = self.PARAMS[view_name].parse(value)
        if not ok:
            self._refuse(um)
        counts = um_to_counts(um)
        if counts == int(self.PARAMS[counts_name].coerce(
                self._param_store.get(counts_name))):
            return
        setattr(self, counts_name, counts)

    return property(getter, setter)


def _stored(self, name):
    return int(self.PARAMS[name].coerce(self._param_store.get(name)))


#: The most steps one Step may move an axis: the Mega multiplies step size
#: by distance into a 16-bit `int` (stepper_firmware.ino: x_steps =
#: XAXIS_SIZE * XAXIS_DIST), so past this it wraps and the probe moves the
#: other way. Review R-2, 2026-10-09.
STEPPER_MAX_MOVE_STEPS = 32767


def _refuse_past_the_move_limit(self, axis, units, step):
    if abs(units * step) > STEPPER_MAX_MOVE_STEPS:
        self._refuse(f"Target {axis.upper()} dist must stay within "
                     f"{STEPPER_MAX_MOVE_STEPS * STEPPER_UM_PER_COUNT:g} µm "
                     f"({STEPPER_MAX_MOVE_STEPS} steps) per Step: the board "
                     "counts a move in 16 bits, and past that it would move "
                     "the other way.")


def _dist_um_property(axis):
    """The target distance in um: what the probe MOVES. The board moves
    step size x distance counts (stepper_firmware.ino: x_steps =
    XAXIS_SIZE * XAXIS_DIST), so the stored distance is in units of the
    axis's step size, and the view converts through both. Typing um stores
    the nearest whole number of step-size units through the count's own
    gated setter; the getter reports the achieved length."""
    dist, step, view = f"{axis}_dist", f"{axis}_step", f"{axis}_dist_um"

    def getter(self):
        return counts_to_um(_stored(self, dist) * _stored(self, step))

    def setter(self, value):
        ok, um = self.PARAMS[view].parse(value)
        if not ok:
            self._refuse(um)
        units = um_to_counts(um, _stored(self, step) * STEPPER_UM_PER_COUNT)
        _refuse_past_the_move_limit(self, axis, units, _stored(self, step))
        if units != _stored(self, dist):
            setattr(self, dist, units)

    return property(getter, setter)


def _step_um_property(axis):
    """The step size in um (one D-pad press; the Step frame's multiplier).
    A new step size keeps the um target: the distance is re-expressed in
    the new units, so a tier-2 change never makes the next Step move
    farther than the target the operator reads."""
    dist, step, view = f"{axis}_dist", f"{axis}_step", f"{axis}_step_um"

    def getter(self):
        return counts_to_um(_stored(self, step))

    def setter(self, value):
        ok, um = self.PARAMS[view].parse(value)
        if not ok:
            self._refuse(um)
        counts = um_to_counts(um)
        old = _stored(self, step)
        if counts == old:
            return
        target_um = _stored(self, dist) * old * STEPPER_UM_PER_COUNT
        units = um_to_counts(target_um, counts * STEPPER_UM_PER_COUNT)
        _refuse_past_the_move_limit(self, axis, units, counts)
        setattr(self, step, counts)
        if units != _stored(self, dist):
            setattr(self, dist, units)

    return property(getter, setter)


def _move_steps_property(axis):
    """The counts a Step moves this axis: step size x distance."""
    return property(lambda self: _stored(self, f"{axis}_dist") * _stored(self, f"{axis}_step"))


def _position_um_property(index):
    return property(lambda self: counts_to_um(self._position[index]))


class StepperProbe(Probe):
    """TMC2209 board. The operator reads and types um and um/s (0.625 um per
    count, lead unmeasured); the stored Params, the profile keys and every
    byte on the wire stay whole counts and counts/s."""

    NAME = "Stepper Probe"
    IDENTITY = "s"

    _MAX_UM_S = round(Probe.MAX_SPEED * STEPPER_UM_PER_COUNT, 6)

    PARAMS = {**Probe.PARAMS, **{p.name: p for p in (
        Param("x_step", "int", default=1, minimum=1, label="X Step Size"),
        Param("y_step", "int", default=1, minimum=1, label="Y Step Size"),
        Param("z_step", "int", default=1, minimum=1, label="Z Step Size"),
        # The physical views (never seeded or stored; see `_defaults`).
        *[Param(f"{a}_step_um", "float", default=STEPPER_UM_PER_COUNT,
                minimum=STEPPER_UM_PER_COUNT, unit="µm",
                label=f"{a.upper()} Step Size") for a in "xyz"],
        *[Param(f"{a}_dist_um", "float", default=0, unit="µm",
                label=f"Target {a.upper()} Dist") for a in "xyz"],
        Param("full_speed_um_s", "float", default=400 * STEPPER_UM_PER_COUNT,
              minimum=STEPPER_UM_PER_COUNT, maximum=_MAX_UM_S, unit="µm/s",
              label="Autonomous Speed"),
        Param("man_full_speed_um_s", "float", default=400 * STEPPER_UM_PER_COUNT,
              minimum=STEPPER_UM_PER_COUNT, maximum=_MAX_UM_S, unit="µm/s",
              label="Manual Speed"),
        *[Param(f"position_{a}_um", "float", default=0, decimals=2, unit="µm",
                label=f"{a.upper()}:") for a in "xyz"],
    )}}

    VIEW_OF = {**{f"{a}_{k}": f"{a}_{k}_um" for a in "xyz" for k in ("step", "dist")},
               "full_speed": "full_speed_um_s",
               "man_full_speed": "man_full_speed_um_s"}

    ENTRY_PARAMS = ("x_step_um", "y_step_um", "z_step_um",
                    "x_dist_um", "y_dist_um", "z_dist_um",
                    "full_speed_um_s", "man_full_speed_um_s")
    SPEED_PARAMS = ("full_speed_um_s", "man_full_speed_um_s")
    TARGET_PARAMS = ("x_dist_um", "y_dist_um", "z_dist_um")
    #: The slider travel, um/s: one count/s up to the ceiling (a slider may
    #: not start below its Param's minimum).
    SPEED_SLIDER = (STEPPER_UM_PER_COUNT, _MAX_UM_S)

    def step(self):
        """A Step, refused before anything is sent when an axis's move would
        wrap the board's 16-bit step count (the count route, a loaded profile)."""
        self._guard("Step")
        for axis in "xyz":
            _refuse_past_the_move_limit(self, axis, _stored(self, f"{axis}_dist"),
                                        _stored(self, f"{axis}_step"))
        return super().step()

    @property
    def scale_note(self):
        """The one place the unmeasured lead is said."""
        return f"{STEPPER_UM_PER_COUNT:g} µm per count (lead unmeasured)"

    def _defaults(self):
        seeded = super()._defaults()
        for name in self.VIEW_OF.values():
            seeded.pop(name, None)      # derived from the stored counts
        return seeded

    @property
    def schema(self):
        P = self.PARAMS
        gamepad_choice, gamepad_log = self._gamepad_elements()

        def position(axis):
            return [sch.readonly(f"{axis.upper()}:", f"position_{axis}_um",
                                 param=P[f"position_{axis}_um"], rail=True),
                    sch.readonly("steps", f"position_{axis}", secondary=True,
                                 unit="steps")]

        def physical(name, counts, gate, small_text="steps", small_unit="steps",
                     slider=None):
            """An entry in um with its count line directly beneath it."""
            return [sch.entry(P[name].label + ":", name, P[name],
                              disabled_when=gate, slider=slider),
                    sch.readonly(small_text, counts, secondary=True,
                                 unit=small_unit)]

        return self._with_ext(sch.schema(
            sch.section(
                "Position",
                *[e for a in "xyz" for e in position(a)],
                sch.readonly("Scale:", "scale_note", role="info"),
            ),
            sch.section(
                "Autonomous",
                *[e for a in "xyz" for e in physical(
                    f"{a}_dist_um", f"{a}_move_steps", _MOTION_GATE)],
                *physical("full_speed_um_s", "full_speed", _MOTION_GATE,
                          "steps/s", "steps/s", slider=self.SPEED_SLIDER),
                sch.toggle("Autonomous:", "is_auto", "set_mode",
                           "Autonomous mode (press to stop)",
                           "Enter Autonomous Mode",
                           on_args=[ProbeMode.AUTO.value],
                           off_args=[ProbeMode.DISABLED.value],
                           disabled_when=("latched", "fault")),
                sch.button("Step", "step",
                           inputs=("x_dist_um", "y_dist_um", "z_dist_um",
                                   "full_speed_um_s"),
                           role="go", disabled_when=("manual", "latched",
                                                     "fault")),
                layout="group",
            ),
            sch.section(
                "Manual",
                gamepad_choice,
                *physical("man_full_speed_um_s", "man_full_speed",
                          _MANUAL_SPEED_GATE, "steps/s", "steps/s",
                          slider=self.SPEED_SLIDER),
                sch.toggle("Manual / Gamepad:", "is_manual", "set_mode",
                           "Manual mode (press to stop)", "Enter Manual Mode",
                           on_args=[ProbeMode.MANUAL.value],
                           off_args=[ProbeMode.DISABLED.value],
                           disabled_when=("latched", "fault")),
                {"type": "internal", "command": "extend_idle", "writable": False,
                 "role": "neutral"},
                layout="group",
            ),
            sch.section(
                "Configuration",
                # One step size per axis: a D-pad press moves it and the
                # Step command carries it.
                *[e for a in "xyz" for e in physical(
                    f"{a}_step_um", f"{a}_step", _MOTION_GATE)],
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
        ))


for _axis, _index in (("x", 0), ("y", 1), ("z", 2)):
    setattr(StepperProbe, f"position_{_axis}_um", _position_um_property(_index))
    setattr(StepperProbe, f"{_axis}_dist_um", _dist_um_property(_axis))
    setattr(StepperProbe, f"{_axis}_step_um", _step_um_property(_axis))
    setattr(StepperProbe, f"{_axis}_move_steps", _move_steps_property(_axis))
for _counts, _view in (("full_speed", "full_speed_um_s"),
                       ("man_full_speed", "man_full_speed_um_s")):
    setattr(StepperProbe, _view, _um_property(_counts, _view))
del _axis, _index, _counts, _view


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
        *_speed_params(120, MAX_SPEED),
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
    #: The chuck's bench ceiling (owner, 2026-10-07).
    MAX_SPEED = 600

    PARAMS = {**Probe.PARAMS, **{p.name: p for p in (
        *_speed_params(400, 600),
        Param("x_step", "int", default=2, minimum=1, label="X Step Size"),
        Param("y_step", "int", default=2, minimum=1, label="Y Step Size"),
        Param("z_step", "int", default=2, minimum=1, label="Z Step Size"),
    )}}
