"""XYZ Stage: a 50 mm crossed-roller stage, one Teensy 3.5 + TMC2209 per axis,
each axis on its own serial link (axis firmware protocol v1, the lead's SPEC
of 2026-10-09).

One model, three links (`devices.teensy_axis.TeensyAxis`, one per `port_x`,
`port_y`, `port_z`), one gamepad, one mode. Parity with the Stepper Probe
(`model/probe.py`): the same modes and transitions, the same idle clock, the
same gamepad mapping and gates, the autonomous Step and the manual jog. What
differs is the wire: ASCII lines in millimetres, one reply per command, a P
position stream per axis, and a heartbeat window on each board.

**The stop path comes first** (repo rule) and is the reason for most of the
structure here:

* `_halt_hardware` sends ESTOP to all three axes on the priority lane, each
  axis on its own worker so a wedged link cannot hold the others back, and
  returns True only when all three landed and none of the boards is silent.
* A lost link on any axis is the base's `_on_link_lost`: the same halt, all
  three, then DISABLED (not FAULT: the link recovers by itself).
* A firmware `EVT FAULT` from any axis (host timeout, driver reset, short,
  over-temperature, UART loss) latches the stop: all three halt.
* No P line from an axis for BOARD_SILENT_AFTER while driven latches the
  stop (the Stepper Probe's board-silent rule, bench incident 2026-10-04).
* A powered mode enables all three or none: one refusal disables the others
  and faults.
* Each board is asked which axis it is (`AXIS`, and on a real port the
  identity answer `DEV: x X`); a board on the wrong resource, or untagged,
  is never enabled.
* On every link-up the station sets `HOSTTIMEOUT 1000` and `STREAM 20` on
  each axis and keeps every board inside the window with `HB`, so a station
  that stops talking stops the stage.

Units are physical: positions in micrometres (5 um per full step, 0.625 um
per microstep at 8 microsteps), each with a secondary microstep line; speeds
in um/s with a secondary microsteps/s line. The wire is in mm and mm/s.
"""
import enum
import math
import threading
import time

import schema as sch
from devices import teensy_axis as axis_device
from devices.serial_port import TransportError
from events import events
from model.base import Model
from model.gamepad_input import GamepadInput
from model.idle import IdleInterlock
from param import Param
from result import NeedsConfirm, Refused

#: The axes, in the order everything lists them.
AXES = axis_device.AXES

#: 1 mm lead screw / 200 full steps per revolution (owner, 2026-10-08).
UM_PER_FULL_STEP = 5.0
#: The firmware's speed clamp at 8 microsteps: MAX_SPEED_MM 2.5 mm/s (the
#: validator's; the step-rate ceiling binds only at finer resolutions).
MAX_SPEED_UM_S = 2500
#: One MOVE at most (the SPEC's MAX_MOVE_MM, the full travel).
MAX_MOVE_UM = 50000


class StageMode(enum.Enum):
    """The Stepper Probe's modes (`model.probe.ProbeMode`), word for word, so
    every gate and view reads the same mode names. FAULT is a mode: the
    hardware state is unknown and is treated as live until a confirmed stop."""
    DISABLED = "disabled"
    IDLE = "idle"
    AUTO = "autonomous"
    MANUAL = "manual"
    FAULT = "fault"


_ENERGIZED = frozenset({StageMode.IDLE, StageMode.AUTO, StageMode.MANUAL,
                        StageMode.FAULT})
_ARMED = frozenset({StageMode.IDLE, StageMode.AUTO, StageMode.MANUAL})
#: The host is driving the axes: a silent board is a stop.
_DRIVEN = frozenset({StageMode.AUTO, StageMode.MANUAL})
#: The probe's gates: motion fields lock while a motion mode runs; Manual
#: Speed stays live in manual (owner ruling 2026-09-26).
_MOTION_GATE = ("autonomous", "manual")
_MANUAL_SPEED_GATE = ("autonomous",)


def _and(names):
    names = list(names)
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


class XyzStage(GamepadInput, IdleInterlock, Model):
    """Three axis boards, one per serial link; one gamepad; one mode."""

    NAME = "XYZ Stage"
    IDENTITY = "x"
    NEEDS_PORT = True
    NEEDS_GAMEPAD = True
    RESOURCES = ("port_x", "port_y", "port_z", "gamepad")
    #: Which axis tag each port resource must carry. Setup places each
    #: scanned board by its tag; the model checks it again at every link-up.
    PORT_TAGS = {"port_x": "X", "port_y": "Y", "port_z": "Z"}

    #: Ceiling of both speeds, um/s.
    MAX_SPEED = MAX_SPEED_UM_S

    #: The rates of the model's loops (RC-4).
    GAMEPAD_RATE_HZ = 50.0       # the JOGV stream, well inside the 250 ms dead-man
    READ_INTERVAL = 0.01         # the reader drains all three links at ~100 Hz
    HEARTBEAT_INTERVAL = 0.25    # HB to each board: four inside the 1 s window
    #: What every board is told at each link-up (SPEC).
    HOST_TIMEOUT_MS = 1000
    STREAM_HZ = 20
    #: The firmware's log level on open: 2 adds `EVT DBG` lines (each command
    #: received, driver on/off and configured, where each motion ended).
    #: Every EVT line lands in the station's log, so the board's own account
    #: of a run is kept instead of dying on the board.
    FIRMWARE_LOG_LEVEL = 2
    #: Seconds without a P line, while driven, after which an axis is "not
    #: answering" and the stage is stopped: the Stepper Probe's number
    #: (twenty P periods at 20 Hz).
    BOARD_SILENT_AFTER = 1.0
    #: L3: a usable link with no P line this long is a stalled stream.
    STREAM_STALL_SECONDS = 1.0
    #: How long a mode entry waits for an axis's first P line of this link.
    READY_WAIT = 0.5
    #: An axis with a tiny share of a vector Step still gets a speed the
    #: firmware can parse (0.001 mm/s); its share of the path is tiny too.
    AXIS_SPEED_FLOOR_UM_S = 1.0
    #: A stick level this close to 0 is neutral.
    JOG_DEADBAND = 1e-3
    #: Home all (provisional): Z first (clear of the work), then X and Y.
    HOME_ALL_ORDER = (("Z",), ("X", "Y"))
    #: Seconds Home all waits for one group; the firmware has its own limit.
    HOME_TIMEOUT = 180.0
    #: L7: seconds between the reader's Health lines.
    HEALTH_INTERVAL = 5.0

    PARAMS = {p.name: p for p in (
        Param("x_step", "int", default=5, minimum=1, maximum=1000, unit="µm",
              label="X Step Size"),
        Param("y_step", "int", default=5, minimum=1, maximum=1000, unit="µm",
              label="Y Step Size"),
        Param("z_step", "int", default=5, minimum=1, maximum=1000, unit="µm",
              label="Z Step Size"),
        Param("x_dist", "int", default=0, minimum=-MAX_MOVE_UM, maximum=MAX_MOVE_UM,
              unit="µm", label="Target X Dist"),
        Param("y_dist", "int", default=0, minimum=-MAX_MOVE_UM, maximum=MAX_MOVE_UM,
              unit="µm", label="Target Y Dist"),
        Param("z_dist", "int", default=0, minimum=-MAX_MOVE_UM, maximum=MAX_MOVE_UM,
              unit="µm", label="Target Z Dist"),
        Param("full_speed", "int", default=500, minimum=1, maximum=MAX_SPEED_UM_S,
              unit="µm/s", label="Autonomous Speed"),
        Param("man_full_speed", "int", default=500, minimum=1,
              maximum=MAX_SPEED_UM_S, unit="µm/s", label="Manual Speed"),
    )}
    TARGET_PARAMS = ("x_dist", "y_dist", "z_dist")
    STEP_PARAMS = ("x_step", "y_step", "z_step")
    SPEED_SLIDER = (1, MAX_SPEED_UM_S)

    def __init__(self, sim=False, port_x=None, port_y=None, port_z=None,
                 gamepad=None):
        # As in the probe: the mode and the gated store first, because
        # `Panel.__init__` seeds every default through the gated setters.
        self._mode = StageMode.DISABLED
        self._mode_lock = threading.RLock()
        self._outbox = threading.local()
        self._param_store = {}
        self._gates = {}
        super().__init__()
        ports = {"X": port_x, "Y": port_y, "Z": port_z}
        self.axes = {axis: self._build_axis(axis, ports[axis], sim) for axis in AXES}
        self._attach_gamepad(gamepad)

        #: Bumped by every `_halt_hardware`, so a mode entry can tell that a
        #: stop landed between its enables and its mode write (L9).
        self._halt_generation = 0
        #: (mode, {axis: seconds}) when the watchdog is the one stopping.
        self._silent_trip = None
        self._configured = {}        # axis -> link epoch the config went out for
        self._checked = {}           # axis -> link epoch whose tag was checked
        self._reported = {}          # axis -> link epoch whose board problem was announced
        #: axis -> sequence number of its MOVE reply, for the Step in flight.
        self._step_seq = {}
        #: axis -> None | "starting" | "running": a HOME this model started.
        self._homing = {axis: None for axis in AXES}
        self._home_failed = {axis: None for axis in AXES}
        self._home_phase = {axis: "" for axis in AXES}
        self._home_all_active = False
        self._home_all_stop = None
        self._home_all_token = None
        #: axis -> True while a non-zero JOGV is standing on it.
        self._jogging = {axis: False for axis in AXES}
        #: axis -> (P sequence, position) last seen, for the idle clock.
        self._seen_p = {axis: (0, None) for axis in AXES}
        self._stalls = 0
        self._stalled = False

    # -- devices -------------------------------------------------------------------
    def _build_axis(self, axis, port, sim):
        """A TeensyAxis from a port name ("SIM" / None simulate), or the
        link a test hands in."""
        if port is None or isinstance(port, str):
            name = "SIM" if sim else port
            return axis_device.TeensyAxis(name, axis, owner=self.NAME)
        return port

    @staticmethod
    def _resource(axis):
        return f"port_{axis.lower()}"

    @property
    def devices(self):
        listed = [self.axes[axis] for axis in AXES]
        if self.gamepad is not None:
            listed.append(self.gamepad)
        return listed

    def open(self):
        super().open()
        events.debug("Devices Open", " ".join(
            f"{axis}={self.axes[axis].port}:{self.axes[axis].status}" for axis in AXES)
            + f" gamepad={self.gamepad_name}", source=self.NAME)

    def _start_threads(self):
        self._spawn("reader", self._reader_loop)
        super()._start_threads()

    # -- mode ------------------------------------------------------------------------
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
        return self._mode is StageMode.AUTO

    @property
    def is_manual(self):
        return self._mode is StageMode.MANUAL

    @property
    def is_moving(self):
        """True while a Step or a HOME this model started is still under way,
        or any axis reports motion, in AUTO. Arrival is observed (the P
        stream's `mv`), never computed."""
        if self._mode is not StageMode.AUTO:
            return False
        if self._home_all_active or any(self._homing.values()):
            return True
        for axis, seq in list(self._step_seq.items()):
            if self.axes[axis].p_seq <= seq:
                return True          # no P line yet since the board took the move
        return any(link.p and link.p.get("mv") for link in self.axes.values())

    @property
    def is_active(self):
        return self.is_moving or self._mode is StageMode.MANUAL

    @property
    def is_energized(self):
        return self.is_enabled

    def set_mode(self, target):
        """The one public way to change mode (the schema toggles' command)."""
        return self._set_mode(self._to_mode(target), "operator command")

    def enable(self):
        return self._set_mode(StageMode.IDLE, "enable")

    def disable(self):
        return self._set_mode(StageMode.DISABLED, "disable")

    def _to_mode(self, target):
        if isinstance(target, StageMode):
            return target
        text = str(target).strip().lower()
        for mode in StageMode:
            if text in (mode.value, mode.name.lower()):
                return mode
        self._refuse(f"{target} is not a mode of the {self.NAME}.")

    def _set_mode(self, target, reason, quiesce=True):
        """`_set_mode_locked`, then publish what it raised with no lock held
        (L10, SF-4), as the probe does."""
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
        """Change mode and own the hardware side effects; the only writer.

        As the probe's, and for the same reasons: leaving any mode disables
        all three axes (D-2: no "hold torque" target); arming needs every
        axis ready and a confirmed ENABLE from all three; MANUAL needs a
        bound gamepad before anything is enabled; a stop that lands during
        the entry backs it out (L9)."""
        with self._mode_lock:
            previous = self._mode
            halts = self._halt_generation
            if target is StageMode.DISABLED:
                return self._deenergize(reason)
            if target is StageMode.FAULT:
                self._refuse("Fault is not a mode you can select.")
            if previous is StageMode.FAULT or self.is_faulted:
                self._refuse(f"{self.NAME} is in fault: its last enable or disable "
                             "was not confirmed on every axis. Stop it; a "
                             "confirmed stop clears the fault.")
            if target is StageMode.MANUAL and not self._is_gamepad_bound:
                self._refuse("Manual mode needs a gamepad. Choose one under "
                             "Gamepad first.")
            self._guard(f"Mode change to {target.value}")
            self._check_axes_ready()
            self._energize_all(reason)
            if target is StageMode.MANUAL and previous is not StageMode.MANUAL:
                self._drain_edges()      # a press parked before manual is not one
            if self._halt_generation != halts or self._estop.is_set():
                events.debug("Mode Entry Backed Out", f"a stop landed while "
                             f"entering {target.value}", source=self.NAME)
                self._deenergize(f"stop during entry to {target.value}")
                self._guard(f"Mode change to {target.value}")
                self._refuse(f"A stop arrived while the {self.NAME} was entering "
                             f"{target.value} mode, so it stayed disabled. Enter "
                             "the mode again.")
            self._mode = target
            self._clear_motion_state()
            self._start_interlock()
            self._touch()
            events.debug("Mode", f"{previous.value} -> {target.value} ({reason}) "
                         f"at {self.position}", source=self.NAME)
            if quiesce:
                self._broadcast("STOP")
            return target.value

    def _check_axes_ready(self):
        """Every axis connected, set up for the station, the right board on
        its resource, and reporting. Refuses with the first that is not;
        nothing has been enabled at that point."""
        for axis in AXES:
            link = self.axes[axis]
            if not link.is_usable:
                self._refuse(f"Axis {axis} ({self._resource(axis)}, {link.port}) is "
                             f"not connected ({link.status}). Check its USB cable, "
                             "then try again.")
            link.track_link()
            self._ensure_configured(axis, link)
            self._check_board(axis, link, ask=True)
            for command, line in (
                    ("HOSTTIMEOUT", f"HOSTTIMEOUT {self.HOST_TIMEOUT_MS}"),
                    ("STREAM", f"STREAM {self.STREAM_HZ}")):
                reply = link.last_reply(command)
                if reply is None or not reply.ok:
                    reply = self._request(link, line)
                if not reply.ok:
                    self._refuse(f"Axis {axis} did not accept {line} ({reply.why}). "
                                 "Its firmware may not be the XYZ Stage's "
                                 "(protocol 1): flash it, then try again.")
            deadline = time.monotonic() + self.READY_WAIT
            while link.p_time is None and time.monotonic() < deadline:
                self._poll(link, blocking=False)
                time.sleep(0.01)
            if link.p_time is None:
                self._refuse(f"Axis {axis} has not reported its position yet. Wait "
                             "a moment, then try again.")
            silent_for = self._silent_for(link)
            if silent_for is not None:
                self._refuse(f"Axis {axis} of the {self.NAME} is not answering: no "
                             f"position report for {silent_for:.0f} s. Reset the "
                             "board (or cut its power and reconnect it), then try "
                             "again.")

    def _energize_all(self, reason):
        """ENABLE on every axis, or on none: one refusal (an ERR, no reply, a
        failed write) disables all three and faults (SPEC). Nothing to do
        when already armed (switching between powered modes)."""
        if self._mode in _ARMED:
            return True
        for axis in AXES:
            reply = self._request(self.axes[axis], "ENABLE",
                                  abort_if=self._estop.is_set)
            if reply.aborted:
                self._broadcast("DISABLE")
                self._refuse(f"{self.NAME} is stopped. Clear the stop before "
                             "enabling it.")
            if not reply.ok:
                landed = self._broadcast("DISABLE")
                missed = [a for a in AXES if not landed[a]]
                if missed:
                    outcome = (f"the disable did not reach axis {_and(missed)}, so "
                               "treat the stage as live")
                else:
                    outcome = "so all three axes were disabled"
                why = (f"Axis {axis} refused to enable ({reply.why}), {outcome}. "
                       f"Check axis {axis}'s motor power and driver; the stop "
                       "clears the fault.")
                self._enter_fault(why)
                self._refuse(why)
            events.debug("Enabled", f"axis {axis}: {reply.line} ({reason})",
                         source=self.NAME)
        return True

    def _deenergize(self, reason):
        """DISABLE on all three, each on its own worker, then every axis must
        confirm it. The stage reads DISABLED only when all three did; else it
        is in FAULT, since a motor may still be powered, or moving:

        * a disable that did not reach an axis (the write failed);
        * an axis that was already not answering (no P line for
          BOARD_SILENT_AFTER), read before the writes as `_halt_hardware`
          reads it: its stream is what would show the disable took;
        * an axis that does not confirm within its reply budget, neither
          `OK DISABLE` nor a P line with `en=0` after the write (R-5: a hung
          loop leaves the DISABLE unread in its USB buffer, and inside the
          1 s silence window nothing else would notice)."""
        previous = self._mode
        silent = self._silent_axes()             # read before the writes
        self._clear_motion_state()
        self._stop_interlock()
        marks = {axis: self.axes[axis].last_seq for axis in AXES}
        landed = self._broadcast("DISABLE")
        missed = [axis for axis in AXES if not landed[axis]]
        unconfirmed = self._unconfirmed_disables(
            marks, [axis for axis in AXES if landed[axis] and axis not in silent])
        problems = []
        if missed:
            problems.append(f"The disable did not reach axis {_and(missed)}, so its "
                            "motor may still be powered.")
        if silent or unconfirmed:
            quiet = []
            if silent:
                quiet.append(f"axis {_and(sorted(silent))} has not reported its "
                             f"position for {max(silent.values()):.1f} s")
            if unconfirmed:
                quiet.append(f"axis {_and(unconfirmed)} did not confirm it")
            problems.append(
                f"The disable was sent to the {self.NAME}, but {' and '.join(quiet)}. "
                "The board is not answering, so nothing confirms the stop and it "
                "may still be moving. Cut its power or reset the board.")
        if problems:
            events.debug("Disable Not Confirmed", f"missed={missed} silent="
                         f"{sorted(silent)} unconfirmed={unconfirmed} ({reason})",
                         source=self.NAME)
            self._enter_fault(" ".join(problems) + " Treat the stage as live and "
                              "check the connection; a confirmed stop clears the "
                              "fault.")
            return self._mode.value
        self._mode = StageMode.DISABLED
        self._clear_fault()
        self._touch()
        events.debug("Mode", f"{previous.value} -> disabled ({reason}) at "
                     f"{self.position}", source=self.NAME)
        return StageMode.DISABLED.value

    def _unconfirmed_disables(self, marks, axes):
        """Of `axes`, the ones that have not confirmed a DISABLE written after
        `marks` ({axis: link.last_seq before the write}). Reads the links
        meanwhile; waits at most each link's reply budget (a healthy board
        answers in milliseconds, so only a board that is not answering
        costs the wait)."""
        pending = list(axes)
        if not pending:
            return []
        deadline = time.monotonic() + max(self.axes[a].REPLY_TIMEOUT for a in pending)
        while True:
            for axis in list(pending):
                link = self.axes[axis]
                self._poll(link, blocking=False)
                if self._disable_confirmed(link, marks[axis]):
                    pending.remove(axis)
            if not pending or time.monotonic() >= deadline:
                return pending
            time.sleep(0.005)

    @staticmethod
    def _disable_confirmed(link, mark):
        """The board answered `OK DISABLE`, or reported `en=0`, after `mark`."""
        reply = link.last_reply("DISABLE")
        if reply is not None and reply.ok and reply.seq > mark:
            return True
        seq = link.p_seq             # the sequence first: `p` is at least as new
        p = link.p
        return p is not None and seq > mark and not p.get("en")

    def _enter_fault(self, reason):
        previous = self._mode
        self._mode = StageMode.FAULT
        self._clear_motion_state()
        events.debug("Mode", f"{previous.value} -> fault ({reason})",
                     source=self.NAME)
        self._fault(reason)

    def _clear_motion_state(self):
        """Nothing the model started is in flight any more."""
        self._step_seq = {}
        for axis in AXES:
            self._homing[axis] = None
            self._jogging[axis] = False
        if self._home_all_stop is not None:
            self._home_all_stop.set()

    # -- stopping --------------------------------------------------------------------
    def _stop_write_bound(self):
        """The longest one priority write may take: both lock waits and the
        write timeout (the port's own numbers), plus a margin."""
        return max(link.PRIORITY_LOCK_TIMEOUT + link.WRITE_IO_LOCK_TIMEOUT
                   + link.write_timeout for link in self.axes.values()) + 0.05

    def _broadcast(self, command, *, priority=True, abort_if=None):
        """`command` to all three axes at once, each on its own worker, so a
        link wedged inside a write cannot hold the others back. -> {axis:
        landed}. Bounded; never raises. Stops (ESTOP, DISABLE, STOP) go on
        the priority lane."""
        results = {}

        def one(axis, link):
            try:
                results[axis] = bool(link.send(command, priority=priority,
                                               abort_if=abort_if))
            except Exception as exc:
                results[axis] = False
                events.debug("Write Failed", f"axis {axis} {command!r}: {exc!r}",
                             source=self.NAME, exception=exc)

        workers = []
        for axis in AXES:
            worker = threading.Thread(target=one, args=(axis, self.axes[axis]),
                                      daemon=True,
                                      name=f"{command.lower()}-{axis}-{self.NAME}")
            worker.start()
            workers.append(worker)
        deadline = time.monotonic() + self._stop_write_bound()
        for worker in workers:
            worker.join(max(0.0, deadline - time.monotonic()))
        return {axis: results.get(axis, False) for axis in AXES}

    def _halt_hardware(self):
        """The strongest stop the stage has: ESTOP (halt and power down) to
        all three axes on the priority lane. True only when all three landed
        and every board is answering."""
        started = time.monotonic()
        silent = self._silent_axes()             # read before the writes
        trip, self._silent_trip = self._silent_trip, None
        self._halt_generation += 1
        self._clear_motion_state()
        self._stop_interlock()
        landed = self._broadcast("ESTOP")
        events.debug("Halt", " ".join(f"{a}={landed[a]}" for a in AXES)
                     + f" in {(time.monotonic() - started) * 1000:.1f} ms",
                     source=self.NAME)
        missed = [axis for axis in AXES if not landed[axis]]
        if not missed:
            previous = self._mode
            self._mode = StageMode.DISABLED
            self._clear_fault()          # a confirmed stop is what FAULT waits for
            if previous is not StageMode.DISABLED:
                events.debug("Mode", f"{previous.value} -> disabled (halt)",
                             source=self.NAME)
        elif self._link_loss_in_progress:
            events.debug("Halt Not Confirmed", f"axis {_and(missed)}: the link is "
                         "lost; leaving the mode as DISABLED, not FAULT",
                         source=self.NAME)
        else:
            self._enter_fault(f"The stop did not reach axis {_and(missed)}, so its "
                              "motor may still be powered. Treat the stage as "
                              "live and check the connection.")
        if silent:
            self._report_not_answering(silent, trip)
            return False
        return not missed

    def _report_not_answering(self, silent, trip):
        names = _and(sorted(silent))
        worst = max(silent.values())
        if trip is not None:
            why = (f"Axis {names} of the {self.NAME} stopped reporting its position "
                   f"in {trip[0]} mode (nothing for {worst:.1f} s), so the station "
                   "stopped all three axes.")
        else:
            why = (f"The stop was sent to the {self.NAME}, but axis {names} has not "
                   f"reported its position for {worst:.1f} s.")
        events.debug("Board Not Answering", f"axes {sorted(silent)}: no P for "
                     f"{worst:.2f} s (threshold {self.BOARD_SILENT_AFTER} s); "
                     f"watchdog={trip is not None}", source=self.NAME)
        events.error("Board Not Answering",
                     f"{why} The board is not answering, so nothing confirms the "
                     "stop and it may still be moving. Cut its power or reset the "
                     "board, then clear the stop.", source=self.NAME)

    def _leave_mode_for_link_loss(self, landed):
        """L1: a lost link leaves the mode as DISABLED, never FAULT. The halt
        has already gone to all three axes."""
        with self._mode_lock:
            previous = self._mode
            self._clear_motion_state()
            self._stop_interlock()
            if previous is not StageMode.FAULT:
                self._mode = StageMode.DISABLED
        events.debug("Mode", f"{previous.value} -> {self._mode.value} (link lost; "
                     f"stop {'landed' if landed else 'NOT confirmed'})",
                     source=self.NAME)

    # -- talking to one axis ------------------------------------------------------------
    def _request(self, link, command, **kwargs):
        """`link.request`, with a failed write as a Reply, never a raise."""
        try:
            return link.request(command, **kwargs)
        except TransportError as exc:
            events.debug("Request Failed", f"axis {link.axis} {command!r}: {exc}",
                         source=self.NAME, exception=exc)
            return axis_device.Reply(command.split()[0].upper(), False,
                                     reason="the link failed")

    def _poll(self, link, blocking=True):
        try:
            link.poll(blocking=blocking)
        except TransportError as exc:
            events.debug("Read Failed", f"axis {link.axis}: {exc}",
                         source=self.NAME, every=5.0)

    def _ensure_configured(self, axis, link):
        """Once per link-up: ask the board which axis it is, set the
        heartbeat window and the P stream, and read its settings. The
        replies land as they come (`last_reply`); a mode entry checks them."""
        if self._configured.get(axis) == link.epoch:
            return
        self._configured[axis] = link.epoch
        for line in ("AXIS", f"HOSTTIMEOUT {self.HOST_TIMEOUT_MS}",
                     f"STREAM {self.STREAM_HZ}", f"LOG {self.FIRMWARE_LOG_LEVEL}", "INFO"):
            try:
                link.send(line)
            except TransportError as exc:
                events.debug("Configure Failed", f"axis {axis} {line!r}: {exc}",
                             source=self.NAME)
                self._configured.pop(axis, None)
                return
        events.debug("Configured", f"axis {axis} on {link.port}, link epoch "
                     f"{link.epoch}", source=self.NAME)

    def _check_board(self, axis, link, ask):
        """The tag the board gives (`AXIS`, and the identity answer on a real
        port) must be this resource's. -> the problem sentence, or None.
        With `ask`, a missing tag is asked for and a problem refuses."""
        tag = link.tag
        if tag is None and ask:
            self._request(link, "AXIS")
            tag = link.tag
        resource = self._resource(axis)
        problem = None
        for heard in (link.identity_tag, tag):
            if heard is None or heard == axis:
                continue
            if heard == "?":
                problem = (f"The board on {resource} ({link.port}) has no axis tag. "
                           f"Tag it at the bench (AXIS {axis}), then reconnect it.")
            else:
                problem = (f"The board on {resource} ({link.port}) says it is axis "
                           f"{heard}, not {axis}. Plug each axis board into its "
                           f"own row's port (or retag it, AXIS {axis}), then try "
                           "again.")
            break
        if problem is None and tag is None and ask:
            problem = (f"Axis {axis} ({resource}, {link.port}) did not say which "
                       "axis it is. Check that it runs the XYZ Stage firmware.")
        if problem is not None:
            if self._reported.get(axis) != link.epoch:
                self._reported[axis] = link.epoch
                self._publish_later(lambda: events.error(
                    "Wrong Axis Board", problem, source=self.NAME))
            if ask:
                self._refuse(problem)
        return problem

    def _microsteps(self, axis):
        """The board's resolution, from its ENABLE or INFO reply; 8 (the boot
        setting) until it has said."""
        link = self.axes[axis]
        for command in ("ENABLE", "INFO"):
            reply = link.last_reply(command)
            if reply is not None and reply.ok:
                try:
                    return int(reply.fields["microsteps"])
                except (KeyError, ValueError):
                    pass
        return 8

    # -- the reader ------------------------------------------------------------------------
    def _reader_loop(self):
        """Drain all three links, keep every board inside its heartbeat
        window, act on what the boards report, and watch for silence."""
        beat_at = 0.0
        health_at = time.monotonic()
        while not self._threads_stop.wait(self.READ_INTERVAL):
            now = time.monotonic()
            for axis in AXES:
                try:
                    self._service_link(axis, self.axes[axis])
                except Exception as exc:
                    events.debug("Read Failed", f"axis {axis}: {exc!r}",
                                 source=self.NAME, exception=exc, every=5.0)
            self._touch()
            if now - beat_at >= self.HEARTBEAT_INTERVAL:
                beat_at = now
                self._send_heartbeats()
            for axis in AXES:
                try:
                    for _at, _seq, text in self.axes[axis].take_events():
                        self._on_axis_event(axis, text)
                except Exception as exc:
                    events.debug("Event Failed", f"axis {axis}: {exc!r}",
                                 source=self.NAME, exception=exc, every=5.0)
            # Outside the reads' try: a read that raised is silence too.
            for check in (self._watch_axes, self._check_stream):
                try:
                    check()
                except Exception as exc:
                    events.debug("Watch Failed", repr(exc), source=self.NAME,
                                 exception=exc, every=5.0)
            if now - health_at >= self.HEALTH_INTERVAL:
                health_at = now
                self._log_health()

    def _service_link(self, axis, link):
        link.track_link()
        if not link.is_usable:
            return
        self._ensure_configured(axis, link)
        self._poll(link)
        if self._checked.get(axis) != link.epoch and link.tag is not None:
            self._checked[axis] = link.epoch
            self._check_board(axis, link, ask=False)
        seq, position = self._seen_p[axis]
        if link.p is not None and link.p_seq != seq:
            now_at = link.p["pos"]
            self._seen_p[axis] = (link.p_seq, now_at)
            if position is not None and now_at != position:
                self._touch_activity()       # motion is activity (the probe's rule)

    def _send_heartbeats(self):
        for axis in AXES:
            link = self.axes[axis]
            if not link.is_usable:
                continue
            try:
                link.send("HB")
            except TransportError as exc:
                events.debug("Heartbeat Not Sent", f"axis {axis}: {exc}",
                             source=self.NAME, every=5.0)

    def _on_axis_event(self, axis, text):
        """One `EVT` line from one axis (without the prefix). Every one is
        logged as the board said it; the ones the station acts on are also
        said to the operator below."""
        events.debug("Axis Event", f"axis {axis}: {text}", source=self.NAME)
        words = text.split()
        kind = words[0].upper() if words else ""
        found = dict(w.split("=", 1) for w in words[1:] if "=" in w)
        if kind == "FAULT":
            self._homing[axis] = None
            self._axis_fault(axis, " ".join(words[1:]) or "no reason given")
        elif kind == "LIMIT":
            self._axis_limit(axis, words[1] if len(words) > 1 else "a switch",
                             found.get("pos_mm", "?"))
        elif kind == "HOMED":
            self._homing[axis] = None
            self._home_failed[axis] = None
            self._home_phase[axis] = "homed"
            events.info("Axis Homed", f"Axis {axis} of the {self.NAME} is homed "
                        f"(its reference edge was at {found.get('edge_mm', '?')} "
                        "mm).", source=self.NAME)
        elif kind == "HOME" and len(words) > 1 and words[1].upper() == "FAIL":
            reason = found.get("reason", "no reason given")
            ours = self._homing[axis] is not None
            self._homing[axis] = None
            self._home_failed[axis] = reason
            self._home_phase[axis] = f"failed ({reason})"
            if ours:
                events.warn("Home Failed", f"Axis {axis} of the {self.NAME} did not "
                            f"home ({reason}). It stopped and is holding.",
                            source=self.NAME)
        elif kind == "HOME" and "phase" in found:
            self._home_phase[axis] = found["phase"]
            if self._homing[axis] == "starting":
                self._homing[axis] = "running"
        elif kind == "BOOT":
            self._configured.pop(axis, None)     # it has forgotten every setting
            self._axis_fault(axis, "the board restarted")

    def _axis_fault(self, axis, reason):
        """A fault on one axis halts all three (SPEC): the latch, so a person
        looks before anything moves again."""
        if self._mode is StageMode.DISABLED or self._estop.is_set():
            events.warn("Axis Fault", f"Axis {axis} of the {self.NAME} reported "
                        f"{reason} while it was not running. Check the axis before "
                        "you enable it.", source=self.NAME)
            return
        events.debug("Axis Fault", f"axis {axis}: {reason} in {self.mode_name}; "
                     "stopping all three", source=self.NAME)
        self.estop()
        events.error("Axis Fault", f"Axis {axis} of the {self.NAME} reported a "
                     f"fault ({reason}), so the station stopped all three axes. "
                     f"Check axis {axis}, then clear the stop.", source=self.NAME)

    def _axis_limit(self, axis, switch, position):
        """The firmware's interlock already stopped that axis. A Step must
        not go on along the other two: they are stopped too."""
        if self._homing[axis] is not None:
            events.debug("Limit", f"axis {axis} {switch} at {position} mm while "
                         "homing", source=self.NAME)
            return
        stopped_others = ""
        if self._mode is StageMode.AUTO and self._step_seq and self.is_moving:
            self._step_seq = {}
            self._broadcast("STOP")
            stopped_others = " The other axes were stopped so the Step ends here."
        events.warn("Limit Reached", f"Axis {axis} of the {self.NAME} reached its "
                    f"limit switch {switch.upper()} at {position} mm and stopped."
                    f"{stopped_others}", source=self.NAME)

    # -- silence -----------------------------------------------------------------------------
    def _silent_for(self, link):
        """Seconds since the link's last P line when that exceeds
        BOARD_SILENT_AFTER, else None (None too before its first line)."""
        last = link.p_time
        if last is None:
            return None
        age = time.monotonic() - last
        return age if age > self.BOARD_SILENT_AFTER else None

    def _silent_axes(self):
        silent = {}
        for axis in AXES:
            seconds = self._silent_for(self.axes[axis])
            if seconds is not None:
                silent[axis] = seconds
        return silent

    @property
    def board_silent(self):
        return bool(self._silent_axes())

    def _watch_axes(self):
        """Latch the stop when an axis stops reporting while driven."""
        if self._mode not in _DRIVEN or self._estop.is_set():
            return
        silent = self._silent_axes()
        if not silent:
            return
        self._silent_trip = (self._mode.value, silent)
        events.debug("Board Silent", f"axes {sorted(silent)} in {self._mode.value} "
                     "mode; stopping", source=self.NAME)
        self.estop()

    def _check_stream(self):
        """L3: a real link that is up and sends no P line is a stalled
        stream: one warning per episode, counted."""
        stalled = any(self._silent_for(self.axes[a]) is not None for a in AXES
                      if self.axes[a].status in ("verified", "unverified"))
        if stalled and not self._stalled:
            self._stalls += 1
            events.warn("Position Stream Stalled", f"{self.NAME} has an axis that "
                        f"sent no position for {self.STREAM_STALL_SECONDS:g} s; the "
                        "link is up. Check the board.", source=self.NAME)
        elif self._stalled and not stalled:
            events.info("Position Stream Resumed", f"{self.NAME} is sending every "
                        "axis's position again.", source=self.NAME)
        self._stalled = stalled

    def _link_stream_state(self):
        return {"dropped": int(sum(link.dropped for link in self.axes.values())),
                "stalls": int(self._stalls), "stalled": bool(self._stalled)}

    def _log_health(self):
        try:
            text = (f"mode={self.mode_name} "
                    + " ".join(f"{a}={self.axes[a].status}/p_age="
                               f"{self._p_age(self.axes[a])}" for a in AXES)
                    + f" idle_remaining={self.idle_remaining} "
                    f"gate_open={self._is_gate_open} pad_bound={self._is_gamepad_bound} "
                    f"latched={self.is_estopped} fault={self.fault!r}")
        except Exception as exc:
            text = f"unavailable: {exc!r}"
        events.debug("Health", text, source=self.NAME)

    @staticmethod
    def _p_age(link):
        return None if link.p_time is None else round(time.monotonic() - link.p_time, 2)

    # -- motion: the autonomous Step -------------------------------------------------------
    def step(self):
        """One relative move of (x_dist, y_dist, z_dist) um at the vector
        speed: each axis gets |d_i| / |d| of it, so all three arrive
        together. Available while AUTO (repeated stepping); refused while a
        move is in flight."""
        self._guard("Step")
        if self.is_moving:
            self._refuse("The stage is still moving. Wait for it to stop, then "
                         "step again.")
        moves = {axis: self._number(f"{axis.lower()}_dist") for axis in AXES}
        length = math.sqrt(sum(d * d for d in moves.values()))
        if not length:
            self._refuse("Every target distance is 0. Set a distance, then step.")
        speed = self._number("full_speed")
        self._set_mode(StageMode.AUTO, "step", quiesce=False)
        lines = {}
        for axis, distance in moves.items():
            if distance:
                share = max(abs(distance) / length * speed, self.AXIS_SPEED_FLOOR_UM_S)
                lines[axis] = f"MOVE {distance / 1000:.4f} {share / 1000:.4f}"
        replies = self._send_together(lines)
        refused = [(axis, reply) for axis, reply in replies.items() if not reply.ok]
        if refused:
            self._broadcast("STOP")
            axis, reply = refused[0]
            self._refuse(f"Axis {axis} refused the move ({reply.why}), so all three "
                         "axes were stopped.")
        self._step_seq = {axis: reply.seq for axis, reply in replies.items()}
        self._touch_activity()
        events.debug("Step", f"{lines} at {speed} um/s", source=self.NAME)
        return list(self.position)

    def _send_together(self, lines):
        """{axis: line} written back to back, then every reply collected, so
        the axes start within a write of each other. -> {axis: Reply}."""
        waiting = {}
        for axis, line in lines.items():
            link = self.axes[axis]
            waiter = link.expect(line)
            try:
                sent = link.send(line, abort_if=self._estop.is_set)
            except TransportError as exc:
                link.cancel(waiter)
                waiting[axis] = axis_device.Reply(waiter.command, False,
                                                  reason=f"the link failed: {exc}")
                continue
            if not sent:
                link.cancel(waiter)
                waiting[axis] = axis_device.Reply(waiter.command, False, aborted=True)
                continue
            waiting[axis] = waiter
        return {axis: (found if isinstance(found, axis_device.Reply)
                       else self.axes[axis].wait(found))
                for axis, found in waiting.items()}

    # -- zeroing and homing ------------------------------------------------------------
    def _axis_name(self, axis):
        name = str(axis).strip().upper()
        if name not in AXES:
            self._refuse(f"{axis} is not an axis of the {self.NAME}.")
        return name

    def _axis_busy(self, axis):
        p = self.axes[axis].p
        return bool(self._homing[axis] or self._jogging[axis] or (p and p.get("mv")))

    def zero_axis(self, axis, confirmed=False):
        """Zero here (ZERO): this axis reads 0 where it stands. A homed axis
        asks first, since its home reference is replaced."""
        axis = self._axis_name(axis)
        self._guard(f"Zero {axis}")
        if self._axis_busy(axis):
            self._refuse(f"Axis {axis} is moving. Wait for it to stop, then zero it.")
        if self.homed.get(axis) and not confirmed:
            raise NeedsConfirm(f"Zero axis {axis} here?\n\nAxis {axis} is homed; "
                               "zeroing here replaces its home reference with this "
                               "position.", "zero_axis", args=(axis,))
        reply = self._request(self.axes[axis], "ZERO", abort_if=self._estop.is_set)
        if not reply.ok:
            self._refuse(f"Axis {axis} did not zero ({reply.why}).")
        events.info("Axis Zeroed", f"Axis {axis} of the {self.NAME} reads 0 here now.",
                    source=self.NAME)
        return 0

    def home_axis(self, axis):
        """Home one axis (HOME): the firmware's sequence against the home
        photo-interrupter. Enters AUTO (homing is motion the host started)."""
        axis = self._axis_name(axis)
        self._guard(f"Home {axis}")
        if self.is_moving:
            self._refuse("The stage is still moving. Wait for it to stop, then "
                         "home.")
        self._set_mode(StageMode.AUTO, f"home {axis}", quiesce=False)
        self._start_home(axis)
        self._touch_activity()
        return "started"

    def _start_home(self, axis, abort_if=None):
        """HOME on one axis. -> the Reply. Marked as ours before it is sent,
        so its HOMED cannot arrive before anyone is listening.

        R-8: `abort_if` is checked inside the write lock, at the last moment
        before the bytes go out, so a mode change between a caller's own
        check and the write cannot start homing outside AUTO (where the
        watchdog is off). By default: latched, or not in AUTO."""
        if abort_if is None:
            abort_if = self._home_not_allowed
        self._homing[axis] = "starting"
        self._home_failed[axis] = None
        self._home_phase[axis] = "starting"
        reply = self._request(self.axes[axis], "HOME", abort_if=abort_if)
        if not reply.ok:
            self._homing[axis] = None
            self._home_phase[axis] = f"not started ({reply.why})"
            if reply.aborted:            # never written: nothing to stop
                if not self._estop.is_set():
                    self._home_phase[axis] = "not started (left autonomous)"
                    self._refuse(f"Axis {axis} did not start homing: the {self.NAME} "
                                 "left autonomous mode first.")
                self._refuse(f"Axis {axis} would not start homing ({reply.why}).")
            # R-7: the HOME was written, and a reply that came late (or not at
            # all) is no proof the board is not homing: a late OK HOME means
            # it is, for up to the firmware's own limit. Stop before refusing.
            landed = self._broadcast("STOP")
            missed = [a for a in AXES if not landed[a]]
            outcome = (f"the stop did not reach axis {_and(missed)}, so treat the "
                       "stage as live" if missed else "so all three axes were stopped")
            self._refuse(f"Axis {axis} would not start homing ({reply.why}), "
                         f"{outcome}.")
        return reply

    def _home_not_allowed(self):
        return self._estop.is_set() or self._mode is not StageMode.AUTO

    def home_all(self):
        """Home all (provisional): Z first, then X and Y together, on a
        worker. A stop, leaving the mode or any failure ends it."""
        self._guard("Home all")
        if self.is_moving:
            self._refuse("The stage is still moving. Wait for it to stop, then "
                         "home.")
        self._set_mode(StageMode.AUTO, "home all", quiesce=False)
        if self._home_all_stop is not None:
            self._home_all_stop.set()            # a finished run's thread retires
        stop, token = threading.Event(), object()
        self._home_all_stop, self._home_all_token = stop, token
        generation = self._halt_generation
        self._home_all_active = True
        self._spawn("home_all", lambda: self._home_all_run(generation, stop, token),
                    stop=stop)
        events.info("Home All Started", f"Home all is provisional: the {self.NAME} "
                    "homes Z first, then X and Y together. Watch the stage; Stop "
                    "ends it at once.", source=self.NAME)
        self._touch_activity()
        return "started"

    def _home_all_aborted(self, generation, stop):
        return (stop.is_set() or self._halt_generation != generation
                or self._estop.is_set() or self._mode is not StageMode.AUTO)

    def _home_all_run(self, generation, stop, token):
        try:
            for group in self.HOME_ALL_ORDER:
                if self._home_all_aborted(generation, stop):
                    return
                for axis in group:
                    try:
                        # R-8: the run's own abort test, inside the write lock.
                        self._start_home(axis, abort_if=lambda: self._home_all_aborted(
                            generation, stop))
                    except Refused as refusal:
                        return self._end_home_all(generation, stop, refusal.reason)
                failure = self._wait_homed(group, generation, stop)
                if failure:
                    return self._end_home_all(generation, stop, failure)
            if not self._home_all_aborted(generation, stop):
                events.info("Home All Finished", f"Every axis of the {self.NAME} "
                            "is homed.", source=self.NAME)
        except Exception as exc:
            events.debug("Home All Failed", repr(exc), source=self.NAME, exception=exc)
            self._end_home_all(generation, stop, "it raised an error (see the log)")
        finally:
            if self._home_all_token is token:
                self._home_all_active = False

    def _wait_homed(self, group, generation, stop):
        """None when every axis in `group` homed (or the run was aborted:
        the stop that aborted it reports itself); else what went wrong."""
        deadline = time.monotonic() + self.HOME_TIMEOUT
        while not self._home_all_aborted(generation, stop):
            failed = [axis for axis in group if self._home_failed[axis]]
            if failed:
                return f"axis {failed[0]} did not home ({self._home_failed[failed[0]]})"
            pending = [axis for axis in group if self._homing[axis] is not None]
            if not pending:
                return None
            if time.monotonic() > deadline:
                return (f"axis {pending[0]} did not finish homing within "
                        f"{self.HOME_TIMEOUT:.0f} s")
            stop.wait(0.02)
        return None

    def _end_home_all(self, generation, stop, why):
        if self._home_all_aborted(generation, stop):
            return
        self._broadcast("STOP")
        for axis in AXES:
            self._homing[axis] = None
        events.warn("Home All Stopped", f"Home all stopped: {why}. All three axes "
                    "were stopped and are holding.", source=self.NAME)

    # -- position --------------------------------------------------------------------------
    def _um(self, axis):
        p = self.axes[axis].p
        return None if p is None else p["pos"] * 1000.0

    def _usteps(self, um, axis):
        return None if um is None else int(round(um * self._microsteps(axis)
                                                 / UM_PER_FULL_STEP))

    @property
    def position(self):
        """(x, y, z) in um: the position source the maps and RGB Analysis
        read by duck type."""
        return tuple(float(self._um(axis) or 0.0) for axis in AXES)

    @property
    def position_time(self):
        """Monotonic time of the oldest of the three latest P lines (the
        position is at least this fresh). None until every axis reported."""
        times = [self.axes[axis].p_time for axis in AXES]
        return None if any(t is None for t in times) else min(times)

    @property
    def position_age(self):
        at = self.position_time
        return None if at is None else round(time.monotonic() - at, 2)

    @property
    def position_epoch(self):
        """Grows whenever any axis's link comes up again (its counter may have
        restarted): positions from different epochs are not comparable."""
        return sum(self.axes[axis].epoch for axis in AXES)

    @property
    def velocity(self):
        return tuple(float(self.axes[a].p["v"] * 1000.0) if self.axes[a].p else 0.0
                     for a in AXES)

    @property
    def velocity_text(self):
        return ", ".join(f"{v:.1f}" for v in self.velocity)

    @property
    def homed(self):
        """{axis: True once the board says it is homed} (P `homed=`)."""
        return {axis: bool(self.axes[axis].p and self.axes[axis].p.get("homed"))
                for axis in AXES}

    @property
    def homed_text(self):
        return ", ".join(f"{axis} {'yes' if done else 'no'}"
                         for axis, done in self.homed.items())

    @property
    def switches_text(self):
        parts = []
        for axis in AXES:
            p = self.axes[axis].p
            if p is None:
                parts.append(f"{axis} ?")
                continue
            tripped = [name.upper() for name in ("ls1", "ls2") if p.get(name)]
            parts.append(f"{axis} {'+'.join(tripped) or 'clear'}"
                         f"{', in beam' if p.get('home') else ''}")
        return "; ".join(parts)

    @property
    def home_text(self):
        return "; ".join(f"{axis} {self._home_phase[axis] or '-'}" for axis in AXES)

    @property
    def full_speed_usteps(self):
        return self._usteps(self._number("full_speed"), "X")

    @property
    def man_full_speed_usteps(self):
        return self._usteps(self._number("man_full_speed"), "X")

    # -- gamepad (GamepadInput owns bind, gate, pump; these are its hooks) -----------
    @property
    def _pumps_gamepad(self):
        return self.is_manual

    def _on_gamepad(self, levels, edges):
        """Sticks and triggers jog by velocity (JOGV, re-sent every tick while
        held, so the 250 ms dead-man never lapses); the D-pad and the
        bumpers step by the per-axis step size. `{}` is the one neutral on
        exit or gate close: JOGV 0 to all three. An axis that is not
        answering holds every axis at neutral; the watchdog does the stop."""
        if not levels:
            self._send_neutral()
            return
        speed = self._number("man_full_speed")
        if self.board_silent:
            events.debug("Jog Held", "an axis is not answering; neutral",
                         source=self.NAME, every=1.0)
            velocity = {axis: 0.0 for axis in AXES}
            steps = {axis: 0 for axis in AXES}
        else:
            z_up = (self._level(levels, "trigger_left", -1.0) + 1.0) / 2.0
            z_down = (self._level(levels, "trigger_right", -1.0) + 1.0) / 2.0
            velocity = {"X": self._level(levels, "axis_x", 0.0) * speed,
                        "Y": self._level(levels, "axis_y", 0.0) * speed,
                        "Z": (z_up - z_down) * speed}
            steps = {"X": int(self._level(levels, "hat_x", 0)),
                     "Y": int(self._level(levels, "hat_y", 0)),
                     "Z": (int(self._level(levels, "bumper_left", 0))
                           - int(self._level(levels, "bumper_right", 0)))}
            if self._is_off_neutral(levels):
                self._touch_activity()
        for axis in AXES:
            v = velocity[axis]
            if abs(v) > self.JOG_DEADBAND * speed:
                self._jog_send(axis, f"JOGV {v / 1000:.4f}")
                self._jogging[axis] = True
            elif self._jogging[axis]:
                self._jogging[axis] = False
                self._jog_send(axis, "JOGV 0")
            elif steps[axis]:
                size = self._number(f"{axis.lower()}_step") * (1 if steps[axis] > 0 else -1)
                self._jog_send(axis, f"MOVE {size / 1000:.4f} {speed / 1000:.4f}")

    @staticmethod
    def _level(levels, key, default):
        value = levels.get(key, default)
        return float(default if value is None else value)

    def _jog_send(self, axis, line):
        try:
            return self.axes[axis].send(line, abort_if=self._estop.is_set)
        except TransportError as exc:
            if not self._is_link_down():
                raise
            # L1: the link is lost and its owner stop is on the way; raising
            # would turn a recoverable loss into FAULT.
            events.debug("Jog Not Sent", f"axis {axis}: {exc}", source=self.NAME,
                         every=1.0)
            return False

    def _send_neutral(self):
        for axis in AXES:
            self._jogging[axis] = False
            try:
                self._jog_send(axis, "JOGV 0")
            except TransportError as exc:
                events.debug("Neutral Not Sent", f"axis {axis}: {exc}",
                             source=self.NAME, every=1.0)

    def _on_gamepad_lost(self, reason):
        if reason == self.GAMEPAD_LOST:
            events.warn("Manual Mode Stopped", "The gamepad disconnected, so manual "
                        "mode stopped and the motors were disabled.",
                        source=self.NAME)
        self._set_mode(StageMode.DISABLED, reason)

    def _on_gamepad_fault(self, reason):
        self._enter_fault("Manual control stopped working. Treat the stage as live, "
                          "stop it, and check the gamepad and the connection.")

    # -- idle interlock (the mixin owns the clock and the loop) -------------------------
    @property
    def _idle_is_armed(self):
        return self.is_enabled

    def _on_idle_expired(self, idle):
        self._set_mode(StageMode.DISABLED, "idle interlock")

    def _idle_soon_text(self):
        return (f"{self.NAME} powers its motors down soon unless it moves or you "
                "extend.")

    def _idle_expired_text(self, idle):
        return (f"{self.NAME} was idle for {idle:.0f} s, so its motors were "
                "disabled. Enter a mode again to continue.")

    def _idle_extended_text(self):
        return f"{self.NAME} stays energized for another {self.INTERLOCK_TIMEOUT:.0f} s."

    def _idle_nothing_text(self):
        return f"Nothing to extend: {self.NAME} is not in a mode."

    # -- params ------------------------------------------------------------------------------
    def _number(self, name):
        return self.PARAMS[name].coerce(self._param_store.get(name))

    def _gate_for(self, name):
        if name not in self._gates:
            self._gates[name] = next((e for e in sch.elements(self.schema)
                                      if e.get("model_attr") == name), None)
        return self._gates[name]

    @staticmethod
    def _gated_param(name):
        """The probe's gated parameter (DC-6): refused by the schema's own
        `disabled_when`, strict while a motion mode is live."""

        def getter(self):
            return self._param_store.get(name, "")

        def setter(self, value):
            element = self._gate_for(name)
            if element is not None and not sch.is_enabled(element, self.mode_name):
                ok, parsed = self.PARAMS[name].parse(value)
                if ok and self._same_value(self._param_store.get(name), parsed):
                    return
                label = element.get("text", name).rstrip(":")
                self._refuse(f"{label} cannot be changed in {self.mode_name} mode. "
                             f"Leave {self.mode_name} mode to edit it.")
            if self.mode_name in _MOTION_GATE:
                ok, parsed = self.PARAMS[name].parse(value)
                if not ok:
                    self._refuse(parsed)
                value = parsed
            self._param_store[name] = value

        return property(getter, setter)

    # -- schema ------------------------------------------------------------------------------
    @property
    def schema(self):
        P = self.PARAMS
        gamepad_choice, gamepad_log = self._gamepad_elements()
        position = []
        for axis in AXES:
            low = axis.lower()
            position += [
                sch.readonly(f"{axis}:", f"position_{low}", rail=True, unit="µm"),
                sch.readonly("µsteps", f"position_{low}_usteps", secondary=True,
                             unit="µsteps"),
            ]
        zero_home = [sch.button(f"Zero {axis} here", "zero_axis", args=(axis,),
                                disabled_when=("latched", "fault"))
                     for axis in AXES]
        zero_home += [sch.button(f"Home {axis}", "home_axis", args=(axis,),
                                 disabled_when=("manual", "latched", "fault"))
                      for axis in AXES]
        configure = f"Configure {self.NAME}"
        return sch.schema(
            sch.section("Position", *position),
            sch.section(
                "Autonomous",
                *[sch.entry(P[name].label + ":", name, P[name],
                            disabled_when=_MOTION_GATE)
                  for name in self.TARGET_PARAMS],
                sch.entry(P["full_speed"].label + ":", "full_speed", P["full_speed"],
                          disabled_when=_MOTION_GATE, slider=self.SPEED_SLIDER),
                sch.readonly("µsteps/s", "full_speed_usteps", secondary=True,
                             unit="µsteps/s"),
                sch.toggle("Autonomous:", "is_auto", "set_mode",
                           "Autonomous mode (press to stop)", "Enter Autonomous Mode",
                           on_args=[StageMode.AUTO.value],
                           off_args=[StageMode.DISABLED.value],
                           disabled_when=("latched", "fault")),
                sch.button("Step", "step",
                           inputs=("x_dist", "y_dist", "z_dist", "full_speed"),
                           role="go", disabled_when=("manual", "latched", "fault")),
                layout="group",
            ),
            sch.section(
                "Manual",
                gamepad_choice,
                sch.entry(P["man_full_speed"].label + ":", "man_full_speed",
                          P["man_full_speed"], disabled_when=_MANUAL_SPEED_GATE,
                          slider=self.SPEED_SLIDER),
                sch.readonly("µsteps/s", "man_full_speed_usteps", secondary=True,
                             unit="µsteps/s"),
                sch.toggle("Manual / Gamepad:", "is_manual", "set_mode",
                           "Manual mode (press to stop)", "Enter Manual Mode",
                           on_args=[StageMode.MANUAL.value],
                           off_args=[StageMode.DISABLED.value],
                           disabled_when=("latched", "fault")),
                {"type": "internal", "command": "extend_idle", "writable": False,
                 "role": "neutral"},
                layout="group",
            ),
            sch.section(
                "Zero and home",
                *zero_home,
                sch.button("Home all (provisional)", "home_all",
                           disabled_when=("manual", "latched", "fault")),
                sch.readonly("Homed:", "homed_text"),
                tier=2, disclosure=configure,
            ),
            sch.section(
                "Configuration",
                *[sch.entry(P[name].label + ":", name, P[name],
                            disabled_when=_MOTION_GATE)
                  for name in self.STEP_PARAMS],
                tier=2, disclosure=configure,
            ),
            sch.section(
                "Diagnostics",
                sch.readonly("Velocity (x, y, z) µm/s:", "velocity_text"),
                sch.readonly("Position age (s):", "position_age", role="info"),
                sch.readonly("Limit switches:", "switches_text"),
                sch.readonly("Homing:", "home_text"),
                gamepad_log,
                tier=3, disclosure="Diagnostics",
            ),
            self._safety_section(),
        )

    # -- state -------------------------------------------------------------------------------
    @property
    def state(self):
        snapshot = super().state
        snapshot.update({
            "position": list(self.position),
            "position_time": self.position_time,
            "position_age": self.position_age,
            "position_epoch": self.position_epoch,
            "velocity": list(self.velocity),
            "is_moving": self.is_moving,
            "is_enabled": self.is_enabled,
            "board_silent": self.board_silent,
            # ESTOP and DISABLE power the driver down on every axis.
            "can_kill_coils": True,
            "homed": self.homed,
            "axes": {axis: {"port": str(self.axes[axis].port),
                            "status": self.axes[axis].status,
                            "tag": self.axes[axis].tag,
                            "microsteps": self._microsteps(axis),
                            "p_age": self._p_age(self.axes[axis]),
                            "homing": self._homing[axis]}
                     for axis in AXES},
        })
        return snapshot

    # -- refusals ----------------------------------------------------------------------------
    def _refuse(self, reason):
        events.debug("Refused", reason, source=self.NAME)
        raise Refused(reason)


def _position_readout(axis):
    def getter(self):
        um = self._um(axis)
        return None if um is None else f"{um:.2f}"
    return property(getter)


def _usteps_readout(axis):
    def getter(self):
        return self._usteps(self._um(axis), axis)
    return property(getter)


for _name in XyzStage.PARAMS:
    setattr(XyzStage, _name, XyzStage._gated_param(_name))
for _axis in AXES:
    setattr(XyzStage, f"position_{_axis.lower()}", _position_readout(_axis))
    setattr(XyzStage, f"position_{_axis.lower()}_usteps", _usteps_readout(_axis))
del _name, _axis
