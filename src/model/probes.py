import math
import time
from collections import deque
import copy
import threading
from enum import Enum
from controller.serial import serial, PACKET_FORMAT
from error_routing import ErrorRouter as ErrorPopupManager
from model.numeric import num as _num, safe_float as _safe_float
from model.params import Param, table as _param_table, extend as _extend_params
from model import schema as sch
from model.base import SchemaCommands

try:
    import gcodeparser
except ImportError:
    gcodeparser = None

class ProbeMode(Enum):
    """The modes a probe can be in. Exactly one at a time (RC-3).

    This replaces four independently-writable booleans — `system_enabled`,
    `auton_flag`, `manual_flag`, `is_stepping` — which were set by different
    methods, threads and views with no single place that owned the hardware
    side effects. Those names survive as **read-only derived properties**, so
    every existing schema toggle still renders, but nothing can write them.

    `FAULT` is a mode, not a flag beside one. When a disable is not confirmed
    the hardware state is genuinely unknown, and the honest answer is neither
    "enabled" nor "disabled" — it is "treat this as live until resolved".
    Modelling it as a mode is what stops the code reporting a guess as a fact.
    """
    DISABLED = "disabled"
    ENABLED_IDLE = "enabled_idle"
    AUTONOMOUS = "autonomous"
    MANUAL = "manual"
    FAULT = "fault"


# Modes in which the coils may be energized. FAULT is included deliberately:
# a failed disable leaves the board in an unknown state, and the safe reading
# of unknown is "possibly live".
_ENERGIZED = frozenset({
    ProbeMode.ENABLED_IDLE, ProbeMode.AUTONOMOUS, ProbeMode.MANUAL,
    ProbeMode.FAULT,
})

# Modes reached only through a *confirmed* enable (invariant I-3.1).
_ARMED = frozenset({
    ProbeMode.ENABLED_IDLE, ProbeMode.AUTONOMOUS, ProbeMode.MANUAL,
})


class BaseProbe(SchemaCommands):
    # Overridable by tests to avoid waiting on the real 5-minute timeout.
    _INTERLOCK_POLL_INTERVAL = 5
    _INTERLOCK_TIMEOUT = 300

    # D-8 (owner, 2026-09-20): "warn at N s, FULL STOP at M s while motion
    # is active... Suggested starting values N=5, M=15, to be tuned at the
    # bench." Folded into the interlock watchdog above rather than a second
    # timer -- same thread, same generation, a second threshold. Distinct
    # from `_INTERLOCK_TIMEOUT`: that measures *operator* inactivity across
    # every energized mode; this measures *web client* absence, only while
    # a mode that can move an axis is engaged (AUTONOMOUS/MANUAL) -- an
    # idle-but-armed probe is explicitly left alone. See
    # `touch_client_liveness` for the seam and what "no client has ever
    # checked in" means.
    WEB_CLIENT_WARN_TIMEOUT = 5    # s (N)
    WEB_CLIENT_STOP_TIMEOUT = 15   # s (M)

    # Single-byte control commands this board's firmware is *observed* to
    # handle today, read straight out of `firmware/*/*.ino` (SERIAL-10).
    #
    #   stepper_firmware.ino, chuck_firmware.ino — `parseHybridSerial` has an
    #       explicit branch for 0x64 ('d', TOFF=0 on all three drivers),
    #       0x65 ('e') and 0x73 ('s'). Anything else is read and discarded.
    #   high_polling_rate.ino (the DC probe) — only 0xAA and 0x73 ('s').
    #       Every other byte falls through to `parseSerialAuto()` and is read
    #       as *text*, so a 'd' there is not a disable at all.
    #   'k' (0x6B) has no branch in any .ino, on any board.
    #
    # This is a **record of fact, not a decision.** Whether the protocol
    # should grow ACKs, DC-side 'e'/'d' handlers, or a real 'k' is owner
    # decision **D-7** (plan.md S16 item 2), open, at the bench, and it needs
    # every board reflashed. Nothing here changes a byte on the wire: the
    # only thing it buys is that the model stops asserting an outcome the
    # firmware never produced. `test_declared_control_bytes_match_the_ino_files`
    # is what keeps this honest if the firmware moves.
    FIRMWARE_CONTROL_BYTES = frozenset({b"d", b"e", b"s"})

    #: The outcome of the last `power_down()`, in the model's own vocabulary.
    #: There is deliberately no "confirmed" — no firmware ACKs anything, so a
    #: successful write is the strongest claim available until D-7 lands.
    POWER_DOWN_SENT = "sent"                # a coil-kill handler exists
    POWER_DOWN_UNSUPPORTED = "unsupported"  # this firmware has no such handler
    POWER_DOWN_FAILED = "failed"            # it did not even leave the host

    # Per-class fallbacks for motion parameters (RC-6 item 1).
    #
    # These were hardcoded at each `_num(...)` call site — `_num(self.x_step,
    # 16)`, `_num(self.full_speed, 400)` — which are BaseProbe's numbers. A
    # DCProbe declares full_speed "120" and man_full_speed "120", but if its
    # value failed to parse (an empty field is enough) the fallback handed
    # the firmware **400**: more than three times the speed that probe is
    # configured for. The fallback has to belong to the class, not to the
    # call site.
    PARAMS = _param_table(
        Param("x_step", "int", default=16, minimum=1, label="X Step Size"),
        Param("y_step", "int", default=16, minimum=1, label="Y Step Size"),
        Param("z_step", "int", default=16, minimum=1, label="Z Step Size"),
        Param("x_dist", "float", default=0, label="Target X Dist"),
        Param("y_dist", "float", default=0, label="Target Y Dist"),
        Param("z_dist", "float", default=0, label="Target Z Dist"),
        Param("full_speed", "float", default=400, minimum=1,
              label="Autonomous Speed"),
        Param("man_full_speed", "float", default=400, minimum=1,
              label="Manual Speed"),
        Param("slow_speed", "float", default=0, label="Brake Speed (Slow)"),
        Param("brake_distance", "float", default=0,
              label="Brake Distance (steps)"),
    )

    # Kept as a derived view for the handful of call sites that want only the
    # numbers. The table is the authority; this is never edited directly.
    @classmethod
    def _defaults(cls):
        return {name: param.default for name, param in cls.PARAMS.items()}

    def _param(self, name):
        """A motion parameter, coerced against *this class's* declaration.

        The bounds used to be passed per call site — `minimum=1, integer=True`
        repeated at every use — so a parameter's type lived in however many
        places happened to read it, and the class default lived nowhere. Both
        are in `PARAMS` now, which is also what the schema publishes to the
        views (RC-6 item 2).
        """
        return self.PARAMS[name].coerce(getattr(self, name))

    # The one documented rate for each model-owned loop (RC-4).
    #
    # Manual commands: Tk drove this at 50 ms and PySide at 20 ms, so the two
    # frontends did not feel the same. 20 ms is adopted — the faster of the
    # two, and the one the primary GUI has been using. **Ratified by the owner
    # on 2026-09-20 alongside D-12**: Tk manual mode is faster than it was on
    # main, deliberately, and both frontends now feel identical.
    #
    # Hardware sampling: both frontends had dedicated 100 ms timers, but
    # PySide *additionally* sampled from _poll_model every 50 ms, roughly
    # tripling the serial traffic for one device. 100 ms restores the
    # documented rate, once, in one place.
    MANUAL_COMMAND_INTERVAL = 0.02   # s -> 50 Hz
    SAMPLE_INTERVAL = 0.10           # s -> 10 Hz

    # How long the position must stay unchanged before an autonomous move is
    # considered arrived. Sampled at SAMPLE_INTERVAL, so this is 10 samples.
    _STEP_SETTLE = 1.0               # s

    def __del__(self):
        print(f"[{self.__class__.__name__}] Destructor called")

    def __init__(self, port, controller_id, active_claims=None):
        # Mode (RC-3) and the mode-gated parameter store (DC-6) come first,
        # before anything below can trigger a `PARAMS`-backed property
        # setter — `self.x_step = "16"` two lines down is exactly that. One
        # value, one writer for the mode: `_transition`. The four booleans
        # this replaces were set from `enter_auton`, `enter_manual`,
        # `macro_start_auton`, `run_script`, `send_manual_mode_command`,
        # `_stop_and_disarm` and the Web `set_attr` route — seven writers, no
        # agreed ordering, and the hardware side effects scattered among them.
        self._mode = ProbeMode.DISABLED
        self._mode_lock = threading.RLock()
        # Backing storage for every `PARAMS`-declared attribute (DC-6, see
        # the properties defined below the class). Construction always runs
        # in DISABLED, which no entry's `disabled_when` names, so nothing
        # below is ever refused — this is purely where the values live.
        self._param_store = {}

        self.serial_comm = serial(port) if port and port != "None" else None

        self.packet_format = PACKET_FORMAT  # Standardized 42-byte float format

        # Position variables
        self.pos_x = "0"
        self.pos_y = "0"
        self.pos_z = "0"

        # Connection variables
        self.controller_var = controller_id
        self.serial_port = port
        self.active_claims = active_claims or {}
        self.poller = None
        try:
            from controller.gamepad import ControllerPoller
            self.poller = ControllerPoller(controller_id, self.active_claims, self.__class__.__name__)
        except Exception as e:
            print(f"[{self.__class__.__name__}] Gamepad unavailable, running headless: {e}")

        # Step sizes
        self.x_step = "16"
        self.y_step = "16"
        self.z_step = "16"

        # Step counts (distances)
        self.x_dist = "0"
        self.y_dist = "0"
        self.z_dist = "0"

        # Velocity control
        self.full_speed = "400"
        self.man_full_speed = "400"

        # Autonomous "stepping" is a *timed sub-state*, not a fifth boolean
        # (RC-3 item 2). `is_stepping` used to be set by macro_start_auton and
        # cleared only by a stop, so a move that finished normally left it True
        # forever — and the watchdog deferred on it, which is how an energized
        # probe could sit idle indefinitely with the interlock suppressed
        # (STEPPER-6, DC-1, VIEW-TKINTER-3).
        #
        # The deadline is extended every time the position actually changes,
        # so it expires `_STEP_SETTLE` seconds after the axis stops moving —
        # arrival, observed, rather than a duration computed from step counts
        # and a speed whose units this layer does not get to assume.
        self._stepping_deadline = None
        self._last_position = None

        # Input gate (D-4, RC-4). Closed while the application does not have
        # focus: the manual pump stops sending, so a gamepad bump while the
        # operator is in another window cannot move the stage.
        #
        # **Gating, never stopping.** Losing focus does not emit a stop and
        # does not leave the mode — a move already in flight continues, and
        # alt-tabbing to read a value does not halt the bench. It is an
        # `Event` rather than a bool so the pump sees the change immediately
        # from whichever thread closed it.
        self._input_gate_open = threading.Event()
        self._input_gate_open.set()

        # What the last power_down() actually achieved (SERIAL-10). `None`
        # until one has been attempted. Never "confirmed": see
        # POWER_DOWN_SENT above.
        self.power_down_status = None
        self._power_down_unsupported_reported = False

        # Fault state (RC-2). Set when a command's fate is unknown — a write
        # that failed means the hardware may be in either state, and saying
        # "disabled" would be a guess presented as a fact. It persists until
        # a successful enable/disable proves the actual state.
        self.fault_reason = None

        # FULL STOP latch (RC-5). Set by emergency_stop() *before* any I/O and
        # checked immediately before every motion write, so a command already
        # in flight on another thread cannot land after the stop. It is
        # cleared only by an explicit operator action, never automatically:
        # a latch that clears itself is not a latch.
        self._estop = threading.Event()

        # Auto-disable interlock (mirrors main's stepper_frame.py/chuck_frame.py
        # 5-minute idle timeout). Lives in the model, not the view, so every
        # frontend shares it — the web dashboard previously had none at all.
        self.last_activity_time = time.time()
        self._interlock_stop = threading.Event()
        self._interlock_thread = None
        # Fresh event and generation per arming (STEPPER-7, RC-3 item 5). A
        # single reused Event meant a watchdog stopped by one disable stayed
        # stopped for the next enable, because `_interlock_stop` was still set.
        self._interlock_generation = 0

        # D-8 / WEB-19 liveness deadline. `None` until the first check-in —
        # see `touch_client_liveness` — so a desktop (Tk/PySide) session,
        # which never calls it, is never gated by a deadline meant for a
        # frontend that was never watching.
        self.last_client_seen_time = None
        self._client_liveness_warned = False

        # Model-owned loops (RC-4). These used to live in the views: Tk's
        # _route_input (50 ms) and PySide's input_timer (20 ms) each pumped
        # the gamepad, and each frontend ran its own position/status timers.
        # Three copies meant three sets of rates and three chances to diverge,
        # and the Web frontend — which has no such timers — simply had no
        # manual mode at all.
        # Controller log, owned by the model (RC-7). It used to exist only as
        # a Tk/Qt Toplevel the view opened on a `cmd_name == "open_controller_
        # log"` branch, so the Web client had no way to see controller
        # activity at all. Buffered here, it is a `log_stream` element that
        # all three renderers draw.
        self._controller_log = deque(maxlen=200)

        self._loops_stop = threading.Event()
        self._input_thread = None
        self._sample_thread = None
        self.last_sample_time = 0.0

        # Run generation token (RC-5 item 1, STEPPER-8).
        #
        # run_script used to spawn an untracked thread with no way to tell it
        # to stop and no way to know it had. Halting depended on the thread
        # noticing that is_stepping/auton_flag had been flipped — which any
        # *other* caller could flip back, and which said nothing about *which*
        # run those flags belonged to. Starting a second script, or stopping
        # and starting again, left the first thread still writing to the port.
        # Every run now carries a generation; a run whose generation is stale
        # stops at its next step.
        self._run_lock = threading.Lock()
        self._run_id = 0
        self._script_thread = None

    def _axis_state(self):
        """The poller's mapped state, or `{}` if it could not be read.

        **The read itself is guarded, not just the float cast** (REDPERCENT-4).
        `get_mapped_state()` goes to the shared SDL poller, and a controller
        unplugged mid-session is the ordinary case, not the exotic one. The
        three `vel_*` properties below are sampled by the Red Percent monitor
        thread as `getattr(stepper_model, 'vel_x', 0.0)` — and that default
        shields nothing, because a property that *raises* is not a property
        that is missing. The exception came out through the getattr, out of
        the monitor loop, and took the thread with it: `monitoring` stayed
        True with nothing monitoring, and pressing Start again did nothing.

        Reported, not swallowed. The bus folds a repeat of the same
        `(severity, source, title)` into one event with a count, so reporting
        on every failed read at 60 Hz produces one warning that says how long
        it lasted — which is what I-8.2 asks for — rather than a popup storm.
        """
        if not self.poller:
            return {}
        try:
            return self.poller.get_mapped_state() or {}
        except Exception as e:
            try:
                ErrorPopupManager.report_warning(
                    "Controller Read Failed",
                    f"{self.__class__.__name__}: could not read the gamepad "
                    f"state: {e}. Velocities are reported as 0 until it "
                    f"recovers.", e)
            except Exception:
                pass
            return {}

    @property
    def vel_x(self):
        val = self._axis_state().get("x_axisStatus", 0.0)
        try: return float(val) if val is not None else 0.0
        except (ValueError, TypeError): return 0.0

    @property
    def vel_y(self):
        val = self._axis_state().get("y_axisStatus", 0.0)
        try: return float(val) if val is not None else 0.0
        except (ValueError, TypeError): return 0.0

    @property
    def vel_z(self):
        state = self._axis_state()
        r_val = state.get("z_axisStatusR", -1.0)
        l_val = state.get("z_axisStatusL", -1.0)
        try:
            r = float(r_val) if r_val is not None else -1.0
            l = float(l_val) if l_val is not None else -1.0
            return (r - l) / 2.0
        except (ValueError, TypeError):
            return 0.0

    @property
    def ui_schema(self):
        P = self.PARAMS
        return sch.schema(
            sch.section(
                "Coordinate Frame",
                sch.readonly("X Position:", "pos_x"),
                sch.readonly("Y Position:", "pos_y"),
                sch.readonly("Z Position:", "pos_z"),
                sch.readonly("Connection:", "connection_state", role="info"),
            ),
            sch.section(
                "Configuration",
                sch.readonly("Serial Port:", "serial_port"),
                sch.dropdown("Controller ID:", "controller_var",
                             command="set_controller",
                             options_command="get_available_controllers"),
                *[sch.entry(P[name].label + ":", name, P[name],
                            disabled_when=("autonomous", "manual"))
                  for name in ("x_step", "y_step", "z_step",
                               "x_dist", "y_dist", "z_dist",
                               "full_speed", "man_full_speed")],
            ),
            sch.section(
                "System Control",
                # Per-device "System Power" toggle removed: enable/disable is
                # already reachable via the mode toggles below, and a separate
                # control was a redundant, easy-to-desync third way to the
                # same state.
                sch.toggle("Autonomous:", "auton_flag", "toggle_auton",
                           "AUTONOMOUS MODE (Click to Stop)",
                           "Enter Autonomous Mode"),
                sch.toggle("Manual / Gamepad:", "manual_flag", "toggle_manual",
                           "MANUAL MODE (Click to Stop)",
                           "Enter Manual Mode"),
                # **D-5.** The distances and the speed travel with the
                # command and are validated as a set. Before this the model
                # read whatever it happened to hold, which is one edit behind
                # what was just typed — and Tk papered over it by forcing
                # focus away first, which is a named anti-fix because it only
                # ever worked in Tk.
                # DC-6: refusing this while a run is already active is
                # enforced by `execute_command` below, not only rendered —
                # the same `disabled_when` the entries use, so re-arming
                # mid-run has to go through the toggle (stop) first.
                sch.button("Start Stepping", "macro_start_auton",
                           inputs=("x_dist", "y_dist", "z_dist", "full_speed"),
                           role="go", disabled_when=("autonomous", "manual")),
                # Per-device "Full Stop" removed: the dashboard's global FULL
                # STOP already calls this model's full_stop() directly, so a
                # per-tab button was a redundant second E-stop.
                #
                # Runtime serial reconnect is purged (D-11). The port is
                # assigned once, at setup, and is readonly above: a live
                # reconnect desyncs Python's enable/disable state from the
                # firmware, which persists its own across a reopen.
                # A `log_stream` composite rather than a button that opens
                # a Toplevel only two of the three frontends can build. The
                # Web client gets the controller log for the first time.
                sch.log_stream("Controller Log:", "controller_log"),
            ),
        )

    def get_available_controllers(self):
        if self.poller:
            return self.poller.get_physical_controllers()
        return []

    def set_controller(self, controller_id):
        """Bind a controller. Returns True only if the bind actually happened.

        `controller_var` used to be assigned *before* the bind was attempted,
        so a failed swap left the UI naming a controller that was never bound
        and the mode flipping lazily on some later tick — the reported
        "toggle desync after controller swap" (GAMEPAD-3, GAMEPAD-4,
        VIEW-TKINTER-10). The poller is the source of truth now and the model
        mirrors it (RC-3 item 4).

        A swap that fails while MANUAL is engaged leaves manual mode through
        `_transition`, because manual mode without a bound pad is exactly the
        state I-3.2 forbids.
        """
        print(f"[{self.__class__.__name__}] Swapping controller to: {controller_id}")
        if self.poller is None:
            try:
                from controller.gamepad import ControllerPoller
                self.poller = ControllerPoller(controller_id, self.active_claims, self.__class__.__name__)
            except Exception as e:
                print(f"[{self.__class__.__name__}] Gamepad still unavailable: {e}")
                self._sync_controller_var()
                return False
            else:
                # GAMEPAD-17: this poller exists only because none did at
                # __init__ (gamepad hardware appearing after
                # construction). `start_loops()` already ran, against
                # `self.poller is None`, the last time this probe armed
                # (RC-4) -- so the poll loop for this brand-new poller has
                # never been started, and it would sit bound but inert.
                # That is the same end state the audit named under the old
                # view-owned polling model, reached here instead because
                # the loop is model-owned now. Start it if the probe is
                # already energized; otherwise the next `_transition` into
                # an armed mode starts it the normal way.
                if self.system_enabled:
                    self.start_loops()
        else:
            try:
                self.poller.set_controller(controller_id)
            except Exception as e:
                print(f"[{self.__class__.__name__}] Controller swap failed: {e}")
                self._sync_controller_var()
                return False

        bound = self._sync_controller_var()
        if not bound and self._mode is ProbeMode.MANUAL:
            self._transition(ProbeMode.DISABLED, "controller swap failed")
        return bound

    def _sync_controller_var(self):
        """Mirror the poller's actual binding into `controller_var`."""
        gamepad = getattr(self.poller, "gamepad", None) if self.poller else None
        if gamepad is None:
            self.controller_var = "None"
            return False
        self.controller_var = getattr(self.poller, "controllerID", self.controller_var)
        return True

    def controller_log(self):
        """The buffered controller log, oldest first. The `log_stream` source."""
        return list(self._controller_log)

    def open_controller_log(self):
        """Kept for the detached log window the desktop views still offer."""
        print(f"[{self.__class__.__name__}] Controller log window requested")

    def toggle_manual(self):
        if self.manual_flag:
            self.full_stop()
        else:
            self.enter_manual()

    def toggle_auton(self):
        if self.auton_flag:
            self.full_stop()
        else:
            self.enter_auton()

    def _new_run_generation(self):
        """Invalidate any run in flight and return a token for the new one."""
        with self._run_lock:
            self._run_id += 1
            return self._run_id

    def _generation_is_current(self, generation):
        with self._run_lock:
            return self._run_id == generation

    def cancel_running_script(self):
        """Invalidate any script in flight. It stops at its next step."""
        self._new_run_generation()

    def _enter_fault(self, reason):
        """Move to FAULT: the hardware state is unknown (RC-2, RC-3).

        This is a *mode*, so a fault leaving manual mode also leaves the
        manual pump's notion of manual mode — which is how a dead input pump
        used to keep `manual_flag` True with nothing pumping it.
        """
        self.fault_reason = reason
        self._mode = ProbeMode.FAULT
        self._stepping_deadline = None
        print(f"[{self.__class__.__name__}] FAULT: {reason}")
        try:
            ErrorPopupManager.report_error(
                "Hardware State Unknown",
                f"{self.__class__.__name__}: {reason}\n\n"
                "The command may not have reached the hardware. Treat the "
                "device as live until this is resolved.", None)
        except Exception:
            pass

    @property
    def in_fault(self):
        return self._mode is ProbeMode.FAULT

    def _clear_fault(self):
        self.fault_reason = None

    # -- mode (RC-3) ---------------------------------------------------
    #
    # Read-only on purpose. `auton_flag`, `manual_flag` and `system_enabled`
    # are what the schema toggles render and what the Web dashboard reads, so
    # the names have to survive — but as *views onto* the mode, never as
    # storage. Assigning to any of them now raises AttributeError, which is
    # invariant I-3.4: no schema or API write can change the mode. The Web
    # `set_attr` route used to write `manual_flag` directly, arming a mode
    # without going through the gamepad check or the hardware enable
    # (STEPPER-11, DC-11).

    @property
    def mode(self):
        return self._mode

    @property
    def system_enabled(self):
        return self._mode in _ENERGIZED

    @property
    def auton_flag(self):
        return self._mode is ProbeMode.AUTONOMOUS

    @property
    def manual_flag(self):
        return self._mode is ProbeMode.MANUAL

    @property
    def is_stepping(self):
        """True while an autonomous move is believed to still be moving."""
        if self._mode is not ProbeMode.AUTONOMOUS:
            return False
        deadline = self._stepping_deadline
        return deadline is not None and time.time() < deadline

    def _begin_stepping(self):
        self._stepping_deadline = time.time() + self._STEP_SETTLE
        self._last_position = (self.pos_x, self.pos_y, self.pos_z)

    def _note_position(self):
        """Extend the stepping deadline while the axis is actually moving.

        Called by the sample loop. Motion *is* activity, so a long move does
        not age into the idle interlock; arrival starts the idle clock, which
        is what RC-3 item 2 means by the sub-state ending with a
        `touch_activity()`.
        """
        position = (self.pos_x, self.pos_y, self.pos_z)
        moved = self._last_position is not None and position != self._last_position
        self._last_position = position
        if moved and self._mode is ProbeMode.AUTONOMOUS and self._stepping_deadline is not None:
            self._stepping_deadline = time.time() + self._STEP_SETTLE
            self.touch_activity()

    @property
    def connection_state(self):
        """The transport's link state, as a string, for every renderer.

        S8 introduced `ConnectionState` and wired it to the Web badge; the
        desktop views had no way to see it because it lives on the transport
        and the schema only addresses model attributes. Exposing it here is
        what makes it "a schema readonly field shared by all views" rather
        than a web-only badge.
        """
        state = getattr(self.serial_comm, "connection_state", None)
        if state is None:
            return "CLOSED"
        return getattr(state, "name", str(state))

    @property
    def input_gate_open(self):
        return self._input_gate_open.is_set()

    def set_input_gate(self, is_open):
        """Open or close the manual input gate (D-4).

        Called by the views on focus change. **A child dialog deactivating the
        main window is not focus loss** — that distinction is the actual
        defect behind GAMEPAD-8 / PYSIDE-14 / VIEW-TKINTER-9, and each view
        makes it before calling here.
        """
        if is_open:
            self._input_gate_open.set()
        else:
            self._input_gate_open.clear()

    def _gamepad_bound(self):
        return bool(self.poller and self.poller.gamepad)

    def _transition(self, target, reason, *, quiesce=True):
        """Change mode and own the hardware side effects. The only writer.

        Returns True when the probe ends in `target`.

        The ordering is the safety property, and it is why this is one
        function rather than six:

        * **Leaving any mode sends a zeroed motion frame first.** Every exit
          from MANUAL — controller lost, swap failed, swap to None, stop —
          reaches the hardware through this one line, so the "last non-zero
          command stands and the axis keeps moving" class cannot recur
          (RC-3 item 3, VIEW-TKINTER-13).
        * **Arming requires a confirmed enable** (I-3.1), and MANUAL
          additionally requires a bound gamepad *before* the enable, because
          the enable energizes coils and nothing walks that back (I-3.2).
        * **De-energizing never skips a step because an earlier one raised.**
          A stop frame that fails still reaches the `d`.

        **D-2 (owner, 2026-09-20): leaving a mode disables the coils.** There
        is deliberately no "stop motion but hold torque" target here. A loaded
        or vertical axis can sag on release; that was ruled an accepted cost.
        Adding a hold-torque variant means reopening D-2, not adding a branch.
        """
        with self._mode_lock:
            if target is ProbeMode.DISABLED:
                return self._go_disabled(reason)

            if target is ProbeMode.MANUAL and not self._gamepad_bound():
                # Before the enable, never after. Checking afterward can
                # revert the Python flag, but the firmware has already been
                # told to energize and nothing walks that back.
                msg = "Cannot enter manual mode: no gamepad/controller attached."
                print(f"[{self.__class__.__name__}] {msg}")
                ErrorPopupManager.report_warning("Manual Mode Blocked", msg)
                return False

            if self._refuse_if_estopped(f"transition to {target.value}"):
                return False

            if not self._arm(reason):
                return False

            self._mode = target
            self._stepping_deadline = None
            self._clear_fault()
            self._start_interlock_watchdog()
            self.start_loops()
            # Entering a mode starts from rest. `quiesce=False` is for the one
            # caller that is entering a mode *in order to move* —
            # macro_start_auton — where a zeroed frame immediately followed by
            # the move is a wasted write and a stop-then-go hiccup at the
            # board. Leaving a mode always quiesces regardless: that is the
            # safety half, and it lives in `_go_disabled` where no caller can
            # opt out of it.
            if quiesce:
                self.send_stop_command()
            return True

    def _arm(self, reason):
        """Send the hardware enable unless the probe is already armed.

        `system_enabled` moves to True **only on a successful write**. It used
        to be set even when the write raised, because the transport swallowed
        the exception and returned normally — so the UI showed the system
        armed when nothing had reached the board, and vice versa.
        """
        if self._mode in _ARMED:
            return True
        if self.serial_comm:
            try:
                self.serial_comm.enable()
            except Exception as e:
                print(f"[{self.__class__.__name__}] {e}")
                ErrorPopupManager.report_warning("Enable Failed", str(e))
                return False
        return True

    def _go_disabled(self, reason):
        """Stop motion, then de-energize. Each step isolated from the last."""
        self._stepping_deadline = None
        # Any script still running belongs to a previous generation now, so
        # it stops at its next step instead of writing to a port that the
        # operator believes is stopped (STEPPER-8).
        self._new_run_generation()
        self._stop_interlock_watchdog()
        try:
            self.send_stop_command()
        except Exception as e:
            # A zero-motion frame that failed must not prevent the hardware
            # disable below. Same isolation rule as teardown: the step that
            # de-energizes hardware is never skipped because an earlier step
            # raised.
            print(f"[{self.__class__.__name__}] Stop frame failed, continuing to disable: {e}")
        # Always send the hardware disable, regardless of our own belief: the
        # firmware's 'd' handler is explicitly idempotent (safe to resend any
        # time) and its own system_enabled flag lives on the Arduino,
        # independent of and persisting across this Python model's lifetime
        # (e.g. across a dock close/reopen that reconstructs this model).
        # Gating this send on the Python-side flag let the two go out of sync
        # and left the stepper coils energized with no way to force a disable
        # through Full Stop.
        if self.serial_comm:
            try:
                self.serial_comm.disable()
            except Exception as e:
                # The disable did not reach the board, so the coils may still
                # be energized. Recording DISABLED here — which is what used
                # to happen unconditionally — would report the system safe on
                # the strength of a command that failed.
                self._enter_fault(f"disable not confirmed — coils may be energized: {e}")
                return False
        self._mode = ProbeMode.DISABLED
        self._clear_fault()
        return True

    @property
    def estop_latched(self):
        return self._estop.is_set()

    def clear_estop(self):
        """Explicit operator action. Nothing else may call this (RC-5)."""
        self._estop.clear()
        print(f"[{self.__class__.__name__}] FULL STOP latch cleared by operator")

    def _refuse_if_estopped(self, what):
        """True when the latch forbids `what`. Checked before every motion write."""
        if self._estop.is_set():
            print(f"[{self.__class__.__name__}] {what} refused: FULL STOP is latched")
            return True
        return False

    # -- schema-declared mode gating (DC-6) -----------------------------
    #
    # `ui_schema` has always declared which modes grey an entry or a
    # command out (`disabled_when=("autonomous", "manual")`). Before this,
    # that declaration was consulted only by each view's own greying-out
    # logic — so a request that reached the model directly (`setattr`, the
    # Web `/api/set_attr` route, or `execute_command` called without going
    # through a renderer at all) landed regardless of mode. These two
    # lookups and the refusal helper are what the properties defined below
    # the class, and the `execute_command` override just below, both share
    # — one place reads the schema's own gate, so a control's rendered
    # state and its actual enforcement cannot drift apart.

    def _schema_element_for(self, model_attr):
        """The schema element declaring `model_attr`, or `None`."""
        for element in sch.elements(self.ui_schema):
            if element.get("model_attr") == model_attr:
                return element
        return None

    def _command_element_for(self, command):
        """The schema element whose `command` is `command`, or `None`."""
        for element in sch.elements(self.ui_schema):
            if element.get("command") == command:
                return element
        return None

    def _refuse_if_mode_disallows(self, element, label):
        """True when `element`'s own `disabled_when`/`enabled_when` forbids
        the current mode. `element=None` (nothing in the schema names this
        attribute or command) means unrestricted, matching the pre-DC-6
        behavior for everything the schema does not gate.
        """
        if element is None or sch.is_enabled(element, self.mode.value):
            return False
        print(f"[{self.__class__.__name__}] Rejected: {label} is not "
              f"available while {self.mode.value} (DC-6)")
        return True

    def execute_command(self, name, inputs=None, args=None):
        """DC-6: a command's own `disabled_when` refuses it here, not only
        in whichever renderer happens to be greying it out. "Start
        Stepping" is the motivating case: re-arming it mid-run has to go
        through the toggle (stop) first, and that has to be true even for a
        caller that never rendered the button at all.
        """
        element = self._command_element_for(name)
        label = (element or {}).get("text", name)
        if self._refuse_if_mode_disallows(element, label):
            from results import Refused
            return Refused(f"{label} is not available while "
                            f"{self.mode.value}")
        return super().execute_command(name, inputs, args)

    def send_stop_command(self):
        if self.serial_comm:
            params = {
                "x_step_size": 0, "y_step_size": 0, "z_step_size": 0,
                "full_speed": 0, "slow_speed": 0, "brake_distance": 0,
                "x_dist": 0, "y_dist": 0, "z_dist": 0,
                "command_code_manual": 0, "command_code_auton": 0
            }
            self.serial_comm.send_autonomous_command(params)

    def enter_auton(self):
        return self._transition(ProbeMode.AUTONOMOUS, "enter autonomous")

    def enter_manual(self):
        return self._transition(ProbeMode.MANUAL, "enter manual")

    def macro_start_auton(self):
        if not self._transition(ProbeMode.AUTONOMOUS, "start autonomous move",
                                quiesce=False):
            return False
        self._begin_stepping()
        self.send_autonomous_command()
        return True

    def run_script(self, script_path=None):
        if not script_path or not self.serial_comm:
            return
            
        print(f"[{self.__class__.__name__}] Parsing and executing script: {script_path}")
        self.enter_auton()

        generation = self._new_run_generation()

        def _execute():
            self._begin_stepping()
            try:
                if gcodeparser is None:
                    e = ImportError("gcodeparser not installed")
                    print(f"[{self.__class__.__name__}] Script execution error: {e}")
                    ErrorPopupManager.report_error("Script Execution Error", f"Error running script:\n{e}", e)
                    return
                with open(script_path, 'r', encoding="utf-8") as f:
                    gcode_content = f.read()
                
                # Check if serial connection is active
                if not self.serial_comm.is_open():
                    raise AttributeError("Serial connection not active.")
                
                # Support both GcodeParser and parse_gcode_lines
                if hasattr(gcodeparser, 'GcodeParser'):
                    parsed = gcodeparser.GcodeParser(gcode_content)
                    lines = parsed.lines
                elif hasattr(gcodeparser, 'parse_gcode_lines'):
                    import io
                    lines = list(gcodeparser.parse_gcode_lines(io.StringIO(gcode_content), include_comments=False))
                else:
                    lines = []

                for line in lines:
                    if not self._generation_is_current(generation):
                        print(f"[{self.__class__.__name__}] Script run superseded; stopping.")
                        return
                    if not self.auton_flag:
                        # The mode left AUTONOMOUS under us — a stop, a fault,
                        # or the idle interlock. `is_stepping` is deliberately
                        # NOT checked here any more: it is now a timed
                        # sub-state that expires on arrival, so a script whose
                        # move finished would have halted itself mid-file.
                        print(f"[{self.__class__.__name__}] Script execution halted by user state override.")
                        break
                    # Each step is real work; keep the run off the idle clock.
                    self._begin_stepping()
                    gcode_str = getattr(line, 'gcode_str', str(line))
                    params = getattr(line, 'params', {})
                    command = getattr(line, 'command', ('', 0))
                    
                    if command and command[0] == 'G':
                        # **Only G0 and G1 are moves** (STEPPER-9). Every word
                        # beginning with 'G' used to be dispatched as an
                        # autonomous packet with X/Y/Z defaulted to 0, so the
                        # `G21`/`G90` preamble that opens most files commanded
                        # the stage twice before its first real move. The
                        # firmware has no notion of units, work offsets or
                        # absolute-vs-relative; a word it cannot act on is
                        # skipped and said out loud, not turned into motion.
                        word = command[1] if len(command) > 1 else None
                        if _safe_float(word) not in (0.0, 1.0):
                            msg = (f"{gcode_str.strip()}: this firmware has no "
                                   f"handler for G{word} (only G0/G1 are moves"
                                   f" — there is no absolute/relative or units"
                                   f" handling). Line skipped; it was NOT sent"
                                   f" as a zero-distance move.")
                            print(f"[{self.__class__.__name__}] {msg}")
                            ErrorPopupManager.report_warning(
                                "Unsupported G-code Word", msg)
                            continue

                        # **Validated before anything is dispatched.** The
                        # frame used to be built from raw strings and sent,
                        # and only then did `float(self.x_step)` run — so a
                        # malformed value went to the hardware *first* and
                        # aborted the script afterwards, with the bad command
                        # already on the wire. This is a motion path: a value
                        # that cannot be read is an error to refuse, not a
                        # number to invent (the rule get_params and
                        # TemperatureSystem.send_settings already follow).
                        axes = {}
                        for axis in ('X', 'Y', 'Z'):
                            raw = params.get(axis, 0)
                            value = _safe_float(raw)
                            if value is None:
                                raise ValueError(
                                    f"{gcode_str.strip()}: {axis} is "
                                    f"{raw!r}, which is not a number. "
                                    f"Nothing was sent.")
                            axes[axis] = value

                        if 'F' in params:
                            feedrate = params['F']
                            speed = _safe_float(feedrate)
                            if speed is None:
                                raise ValueError(
                                    f"{gcode_str.strip()}: feedrate is "
                                    f"{feedrate!r}, which is not a number. "
                                    f"Nothing was sent.")
                        else:
                            # No F word: the probe's own configured speed,
                            # coerced against its class's table rather than
                            # read raw off the field (RC-6 item 1).
                            speed = float(self._param("full_speed"))
                            feedrate = speed

                        x_step = self._param("x_step")
                        y_step = self._param("y_step")
                        z_step = self._param("z_step")

                        cmd_params = {
                            "x_step_size": x_step,
                            "y_step_size": y_step,
                            "z_step_size": z_step,
                            "full_speed": str(feedrate),
                            "slow_speed": 0,
                            "brake_distance": 0,
                            "x_dist": str(params.get('X', 0)),
                            "y_dist": str(params.get('Y', 0)),
                            "z_dist": str(params.get('Z', 0)),
                            "command_code_manual": 0,
                            "command_code_auton": 1
                        }
                        if self._refuse_if_estopped("script motion"):
                            break
                        self.serial_comm.send_autonomous_command(cmd_params)

                        x_steps = abs(float(x_step) * axes['X'])
                        y_steps = abs(float(y_step) * axes['Y'])
                        z_steps = abs(float(z_step) * axes['Z'])
                        dist = math.sqrt(x_steps**2 + y_steps**2 + z_steps**2)
                        if speed <= 0:
                            speed = float(self._param("full_speed"))
                        duration = (dist / speed) + 0.05 if speed > 0 else 0.1
                        time.sleep(duration)
                    elif ',' in gcode_str:
                        if self._refuse_if_estopped("script motion"):
                            break
                        if self.serial_comm:
                            self.serial_comm.write_command(gcode_str.strip() + '\n')
                        time.sleep(0.1)
                    else:
                        if self._refuse_if_estopped("script motion"):
                            break
                        if self.serial_comm:
                            self.serial_comm.write_command(gcode_str + '\n')
                        time.sleep(0.1)
            except Exception as e:
                print(f"[{self.__class__.__name__}] Script execution error: {e}")
                ErrorPopupManager.report_error("Script Execution Error", f"Error running script:\n{e}", e)
            finally:
                self.full_stop()

        self._script_thread = threading.Thread(
            target=_execute, daemon=True,
            name=f"script-{self.__class__.__name__}-{generation}")
        self._script_thread.start()

    def get_params(self):
        return {
            "x_step_size": self._param("x_step"),
            "y_step_size": self._param("y_step"),
            "z_step_size": self._param("z_step"),
            "full_speed": self._param("full_speed"),
            "slow_speed": 0,           
            "brake_distance": 0,   
            "x_dist": self._param("x_dist"),
            "y_dist": self._param("y_dist"),
            "z_dist": self._param("z_dist"),
            "command_code_manual": int(self.manual_flag),               
            "command_code_auton": int(self.auton_flag)                      
        }

    def send_autonomous_command(self):
        if self._refuse_if_estopped("autonomous command"):
            return
        self.touch_activity()
        if self.serial_comm:
            self.serial_comm.send_autonomous_command(self.get_params())

    def send_manual_mode_command(self, controller_params):
        if self.manual_flag and not self._gamepad_bound():
            # Losing the controller leaves MANUAL through the one transition,
            # which sends the stop frame and de-energizes (RC-3 item 3). The
            # old code cleared `manual_flag` alone and left `system_enabled`
            # True, so the coils stayed energized in a mode nothing was
            # driving (STEPPER-5, GAMEPAD-3, VIEW-TKINTER-4).
            msg = "Manual mode disabled: no gamepad/controller attached."
            print(f"[{self.__class__.__name__}] {msg}")
            ErrorPopupManager.report_warning("Manual Mode Blocked", msg)
            self._transition(ProbeMode.DISABLED, "controller lost")
            controller_params = {}
            
        if self.serial_comm:
            if (controller_params.get("x_axisStatus", 0.0) or controller_params.get("y_axisStatus", 0.0)
                    or controller_params.get("z_axisStatusR", -1.0) != -1.0
                    or controller_params.get("z_axisStatusL", -1.0) != -1.0
                    or controller_params.get("dpad_LR", 0) or controller_params.get("dpad_UD", 0)
                    or controller_params.get("LBumper", 0) or controller_params.get("RBumper", 0)):
                self.touch_activity()
            params = {
                "x_axisStatus": controller_params.get("x_axisStatus", 0.0),
                "y_axisStatus": controller_params.get("y_axisStatus", 0.0),
                "z_axisStatusR": controller_params.get("z_axisStatusR", -1.0),
                "z_axisStatusL": controller_params.get("z_axisStatusL", -1.0),
                "x_stepSize": self._param("x_step"),
                "y_stepSize": self._param("y_step"),
                "z_stepSize": self._param("z_step"),
                "dpad_LR": controller_params.get("dpad_LR", 0),
                "dpad_UD": controller_params.get("dpad_UD", 0),
                "LBumper": controller_params.get("LBumper", 0),
                "RBumper": controller_params.get("RBumper", 0),
                "manual_jog_speed": self._param("man_full_speed"),
                "packet_format": self.packet_format
            }
            if self._refuse_if_estopped("manual command"):
                return
            self.serial_comm.send_manual_mode_command(params)

    def read_position(self):
        if self.serial_comm:
            pos = self.serial_comm.read_position()
            if pos:
                self.pos_x, self.pos_y, self.pos_z = str(pos[0]), str(pos[1]), str(pos[2])

    # -- model-owned loops (RC-4) --------------------------------------

    def start_loops(self):
        """Start the input pump and the hardware sampler. Idempotent.

        Demand-driven rather than started at construction: an idle or
        test-constructed model should not be running two threads. `enable()`
        starts them, because that is the point at which the model is armed
        and something is worth pumping or sampling. Every frontend reaches
        this through the same path — including the Web dashboard, which had
        no input pump of its own at all.
        """
        self._loops_stop.clear()
        if self.poller is not None:
            # The model starts the poller, with no GUI root, so the poller
            # runs its own thread. The views used to do this and hand in an
            # adapter wrapping their own event loop — which is why the Web
            # frontend, having no such loop to offer, never polled at all.
            def _log(msg):
                print(f"[controllerDrive] {msg}")
                self._controller_log.append(str(msg))
            self.poller.start_polling(
                None, log_updater=_log, activity_callback=self.touch_activity)
        if self._input_thread is None or not self._input_thread.is_alive():
            self._input_thread = threading.Thread(
                target=self._input_loop, daemon=True,
                name=f"input-{self.__class__.__name__}")
            self._input_thread.start()
        if self._sample_thread is None or not self._sample_thread.is_alive():
            self._sample_thread = threading.Thread(
                target=self._sample_loop, daemon=True,
                name=f"sample-{self.__class__.__name__}")
            self._sample_thread.start()

    def stop_loops(self):
        self._loops_stop.set()

    def _input_loop(self):
        """Pump gamepad input to the hardware while manual mode is engaged.

        Exception-isolated: a failure here faults the model rather than
        killing the thread silently, which is what a view-owned timer did —
        the timer died and manual mode simply stopped responding with no
        indication that anything had gone wrong.
        """
        was_pumping = False
        while not self._loops_stop.wait(self.MANUAL_COMMAND_INTERVAL):
            try:
                # The gate is checked here, in the one place that writes motion
                # from controller input. `flush_neutral` used to be the answer
                # and could not work: it zeroed the cached axis state, and the
                # poll loop — four times faster than this pump — simply read
                # the physical stick again and refilled it before the next
                # send. Gating the *send* is what actually holds the axis.
                manual = bool(self.manual_flag) and self._input_gate_open.is_set()
                if manual and not self._estop.is_set():
                    params = self.poller.get_mapped_state() if self.poller else {}
                    self.send_manual_mode_command(params or {})
                elif was_pumping:
                    # Neutral on exit (I-4.2). Leaving manual mode — or having
                    # the gate close under it — has to send one zeroed frame,
                    # or the last non-zero command stands and the axis keeps
                    # moving. One frame, not a stream: the gate is not a stop.
                    self.send_manual_mode_command({})
                was_pumping = manual
            except Exception as e:
                self._enter_fault(f"manual input pump failed: {e}")
                return

    def _sample_loop(self):
        """Sample hardware into the model's cached fields.

        Views and /api/state read the cache and never touch the transport
        (invariant I-4.1). A stalled read therefore cannot block a render
        tick or FULL STOP (I-4.3).
        """
        while not self._loops_stop.wait(self.SAMPLE_INTERVAL):
            try:
                self.read_position()
                self._note_position()
                self.last_sample_time = time.time()
            except Exception as e:
                # Sampling is best-effort: a transport hiccup must not kill
                # the loop, or positions freeze silently for the rest of the
                # session.
                print(f"[{self.__class__.__name__}] Sample failed: {e}")

    def touch_activity(self):
        self.last_activity_time = time.time()

    def touch_client_liveness(self):
        """Record that a Web client is still watching (D-8 / WEB-19).

        **The seam.** No arguments: "now" is read here, with `time.time()`,
        rather than accepted from the caller, so a slow request cannot
        backdate the deadline. The Web adapter's own side of this is to
        call it once per live poll of this device — the natural place is
        wherever it already reads this model's state for `/api/state`, so
        the deadline tracks "a client is actually looking at this device"
        rather than "the server process is up."

        Before the first call, `last_client_seen_time` stays `None` and
        the watchdog does not gate on it at all (see `_check_client_
        liveness`) — a Tk or PySide session, which never calls this, reads
        as a desktop session, not an absent web client. Once a web client
        has checked in, the gate applies for the rest of this model's
        life, and a check-in also clears any pending warning.
        """
        self.last_client_seen_time = time.time()
        self._client_liveness_warned = False

    def _check_client_liveness(self):
        """D-8's second threshold, folded into the interlock watchdog.

        Only while a mode that can actually move an axis is engaged — an
        idle-but-armed probe is left alone by design — and only once a web
        client has checked in at least once.
        """
        if self._mode not in (ProbeMode.AUTONOMOUS, ProbeMode.MANUAL):
            return
        seen = self.last_client_seen_time
        if seen is None:
            return
        silence = time.time() - seen
        if silence > self.WEB_CLIENT_STOP_TIMEOUT:
            msg = (f"No web client has polled in {silence:.1f}s while "
                   f"{self._mode.value}; FULL STOP (D-8).")
            print(f"[{self.__class__.__name__}] {msg}")
            ErrorPopupManager.report_error(
                "Client Liveness FULL STOP", msg, None)
            self.emergency_stop()
        elif (silence > self.WEB_CLIENT_WARN_TIMEOUT
                and not self._client_liveness_warned):
            self._client_liveness_warned = True
            msg = (f"No web client has polled in {silence:.1f}s while "
                   f"{self._mode.value}.")
            print(f"[{self.__class__.__name__}] {msg}")
            ErrorPopupManager.report_warning("Client Liveness Warning", msg)

    def _stop_interlock_watchdog(self):
        self._interlock_stop.set()

    def _start_interlock_watchdog(self):
        """Start the idle interlock for this arming (RC-3 item 5, STEPPER-7).

        **Each arming gets its own Event and generation.** The old code reused
        one `_interlock_stop` for the model's lifetime, so a disable that set
        it left it set: the next enable started a thread that returned on its
        first tick, and the probe ran energized with no interlock at all. The
        generation also lets a stale thread from a previous arming retire
        itself rather than fight the current one.
        """
        # `is_alive()` alone is not enough: a thread that has been told to
        # stop stays alive until its next tick, up to `_INTERLOCK_POLL_INTERVAL`
        # later. Re-arming inside that window would return here and leave the
        # probe energized with a watchdog that is on its way out. A stopped
        # thread is retired by the generation check instead.
        if (self._interlock_thread and self._interlock_thread.is_alive()
                and not self._interlock_stop.is_set()):
            return
        self._interlock_stop = threading.Event()
        self._interlock_generation += 1
        generation = self._interlock_generation
        stop_event = self._interlock_stop
        self.touch_activity()
        # A fresh arming starts with a clean liveness slate: a warning
        # raised in a previous arming must not suppress the one this
        # generation might need to raise on its own account.
        self._client_liveness_warned = False

        def _watch():
            while not stop_event.wait(self._INTERLOCK_POLL_INTERVAL):
                if generation != self._interlock_generation:
                    return
                if not self.system_enabled:
                    return
                # **No flag deferral.** This used to `continue` while
                # `is_stepping or manual_flag`, which meant the interlock was
                # suppressed in exactly the two modes that energize coils —
                # and `is_stepping` was never cleared on normal completion, so
                # one autonomous move disabled the interlock for the rest of
                # the session (STEPPER-6, DC-1, VIEW-TKINTER-3).
                #
                # The clock is real inactivity instead: motion extends it via
                # `_note_position`, and off-neutral gamepad input extends it
                # via `send_manual_mode_command`. **D-3 (owner): manual mode
                # does idle-time-out**, on real input inactivity.
                if time.time() - self.last_activity_time > self._INTERLOCK_TIMEOUT:
                    msg = f"5 minutes of inactivity detected. Disabling {self.__class__.__name__}"
                    print(f"[Timeout] {msg}")
                    ErrorPopupManager.report_info("Idle Timeout", msg)
                    self.disable()
                    return
                # D-8 / WEB-19: the second threshold this same watchdog now
                # carries. Folded in here rather than a second timer, per
                # the owner's own framing of the request.
                self._check_client_liveness()

        self._interlock_thread = threading.Thread(
            target=_watch, daemon=True,
            name=f"interlock-{self.__class__.__name__}-{generation}")
        self._interlock_thread.start()

    def enable(self):
        """Arm the hardware and sit idle. Returns False if it did not happen."""
        return self._transition(ProbeMode.ENABLED_IDLE, "enable")

    def toggle_enable(self):
        if self.system_enabled:
            self.disable()
        else:
            self.enable()

    def disable(self):
        return self._transition(ProbeMode.DISABLED, "disable")

    def full_stop(self):
        return self._transition(ProbeMode.DISABLED, "full stop")

    @property
    def supports_coil_kill(self):
        """True when this board's firmware has a handler that cuts coil current.

        `'d'` is that handler on the stepper and chuck boards: TOFF=0 on all
        three TMC2209 drivers, unconditionally. The DC board has no branch for
        it, so the same byte arrives at `parseSerialAuto()` as text and
        produces the stop fallthrough — a halt, not a de-energize.
        """
        return b"d" in self.FIRMWARE_CONTROL_BYTES

    def power_down(self):
        """Stop, de-energize, and report **what actually happened** (SERIAL-10).

        The bytes sent here are exactly the bytes that were sent before —
        zeroed stop frame, `'d'`, `'k\\n'` — because this is a stop path and
        the standing rule is that a truthfulness fix does not get to change
        it. What changed is the claim: the old code logged "Sent Power Down
        (Kill Coils) command 'k'" on every board, which is false on all three
        (no firmware handles `'k'`) and doubly false on the DC probe, whose
        firmware has no coil-kill handler at all.

        `power_down_status` carries the honest answer instead, and a device
        that cannot do this says so once rather than reporting silent success.
        Making `'k'` real — or deleting it — is **D-7**, not this.
        """
        disabled = self._transition(ProbeMode.DISABLED, "power down")
        if self.serial_comm:
            try:
                self.serial_comm.write_command(b'k\n', priority=True)
            except Exception as e:
                # The kill never reached the board. Say so loudly rather than
                # letting the caller believe the coils are dead (RC-2).
                self.power_down_status = self.POWER_DOWN_FAILED
                self._enter_fault(f"power down not confirmed: {e}")
                return False
        if not disabled:
            # `_go_disabled` has already faulted: the disable did not reach
            # the board, so nothing here may claim the coils are down.
            self.power_down_status = self.POWER_DOWN_FAILED
            return False
        if self.supports_coil_kill:
            self.power_down_status = self.POWER_DOWN_SENT
            print(f"[{self.__class__.__name__}] Power down sent: stop frame "
                  f"and 'd' (TOFF=0). Unacknowledged — no firmware ACKs (D-7).")
        else:
            self.power_down_status = self.POWER_DOWN_UNSUPPORTED
            self._report_power_down_unsupported()
        return disabled

    def _report_power_down_unsupported(self):
        """Tell the operator once that this device has no coil-kill command.

        Once per model, not once per stop: `power_down()` is on the teardown
        and FULL STOP paths, and a popup on every one of those trains the
        operator to dismiss the message that matters. It is a standing
        property of the board, not an event.
        """
        msg = (f"{self.__class__.__name__}: this device's firmware has no "
               f"power-down (kill coils) command — it handles "
               f"{sorted(b.decode() for b in self.FIRMWARE_CONTROL_BYTES)} "
               f"and nothing else. A stop was sent and the motion frame was "
               f"zeroed, but the driver outputs were NOT de-energized. "
               f"Treat the device as live. (SERIAL-10; firmware support is "
               f"owner decision D-7.)")
        print(f"[{self.__class__.__name__}] {msg}")
        self._controller_log.append(msg)
        if self._power_down_unsupported_reported:
            return
        self._power_down_unsupported_reported = True
        try:
            ErrorPopupManager.report_warning("Power Down Not Supported", msg)
        except Exception:
            pass

    def teardown(self):
        """Safety-first, exception-safe shutdown (RC-1, invariant I-1.2).

        Order is hardware stop -> background activity -> transport close, and
        each step is isolated. This used to stop the gamepad poller first with
        no guard, so a poller cleanup that raised skipped power_down()
        entirely: the port then closed with the coils still energized while
        Python reported the system disabled.

        Residual window: the poller can still emit one manual-mode command
        between the stop and its own shutdown. That closes in S5, when the
        control loops move into the model and stop being the view's to drive.
        """
        try:
            self.power_down()
        except Exception as e:
            print(f"[{self.__class__.__name__}] Hardware stop failed during teardown: {e}")
        try:
            self.cancel_running_script()
            thread = self._script_thread
            if thread is not None and thread.is_alive():
                thread.join(timeout=2.0)
        except Exception as e:
            print(f"[{self.__class__.__name__}] Cancelling script failed during teardown: {e}")
        try:
            self.stop_loops()
        except Exception as e:
            print(f"[{self.__class__.__name__}] Stopping loops failed during teardown: {e}")
        try:
            if self.poller:
                try:
                    self.poller.stop_polling()
                finally:
                    self.poller.close()
        except Exception as e:
            print(f"[{self.__class__.__name__}] Poller cleanup failed during teardown: {e}")
        try:
            if self.serial_comm:
                self.serial_comm.close()
        except Exception as e:
            print(f"[{self.__class__.__name__}] Transport close failed during teardown: {e}")

    # FULL STOP must return to its caller within this budget (invariant
    # I-5.2). The caller is frequently the UI thread, and a stop button that
    # freezes the window is a stop button the operator stops trusting.
    ESTOP_RETURN_BUDGET = 0.08

    def emergency_stop(self):
        """Latch FULL STOP, then stop the hardware. Returns promptly (RC-5).

        Two separate guarantees, and it matters that they are separate:

        1. **The latch is set first, before any I/O.** That is what actually
           protects the bench — from this instant no new motion command can
           be issued, including one already queued on the script or gamepad
           thread. It is synchronous and cannot fail.
        2. **The hardware stop is dispatched and joined with a bound.** The
           write itself goes out on a worker so a stalled or wedged transport
           cannot hold the caller. If the join times out the stop is still in
           flight and the priority write path is still forcing it through —
           we simply stop *waiting* for it.

        Returning before the write completes is deliberate. The alternative
        is a UI thread blocked behind a dead serial port, with a FULL STOP
        button that appears to have done nothing.
        """
        self._estop.set()

        done = threading.Event()

        def _stop():
            try:
                self.power_down()
            finally:
                done.set()

        worker = threading.Thread(
            target=_stop, daemon=True,
            name=f"estop-{self.__class__.__name__}")
        worker.start()
        if not done.wait(self.ESTOP_RETURN_BUDGET):
            print(f"[{self.__class__.__name__}] FULL STOP: latched; hardware "
                  f"stop still in flight after {self.ESTOP_RETURN_BUDGET}s")


def _mode_gated_param(name):
    """One `property` per `PARAMS` name, shared by every `BaseProbe`
    subclass (DC-6).

    This is the *only* thing that changes: whether a write to a declared
    motion parameter lands, gated by the exact `disabled_when` its schema
    entry already carries. What is stored is untouched — an unparseable
    value is still accepted here and only ever substituted for later,
    inside `get_params`'s lenient `coerce` (RC-6; see
    `tests/core/test_typed_params.py`). Mode gating and value typing are
    different questions, and only the first one belongs at the write
    boundary: a value that fails to parse is a formatting problem the
    model already knows how to fall back from, but a write that arrives
    mid-autonomous-run is a safety question, and the answer has to be "no"
    before it is ever asked what the value was.
    """

    def getter(self):
        return self._param_store.get(name, "")

    def setter(self, value):
        element = self._schema_element_for(name)
        label = (element or {}).get("text", name).rstrip(":")
        if self._refuse_if_mode_disallows(element, label):
            raise ValueError(f"{label} cannot be changed while "
                              f"{self.mode.value}")
        self._param_store[name] = value

    return property(getter, setter)


for _param_name in BaseProbe.PARAMS:
    setattr(BaseProbe, _param_name, _mode_gated_param(_param_name))
del _param_name


class StepperProbe(BaseProbe):
    PARAMS = _extend_params(
        BaseProbe.PARAMS,
        Param("x_step", "int", default=1, minimum=1, label="X Step Size"),
        Param("y_step", "int", default=1, minimum=1, label="Y Step Size"),
        Param("z_step", "int", default=1, minimum=1, label="Z Step Size"),
    )

    def __init__(self, port, controller_id, active_claims=None):
        super().__init__(port, controller_id, active_claims)
        self.x_step = "1"
        self.y_step = "1"
        self.z_step = "1"


class DCProbe(BaseProbe):
    # `firmware/high_polling_rate/high_polling_rate.ino`'s parseHybridSerial
    # branches on 0xAA and 0x73 ('s') only; everything else, 'e' and 'd'
    # included, falls through to `parseSerialAuto()` and is read as text
    # (SERIAL-10). The host keeps sending the same bytes — see power_down —
    # it just no longer reports a de-energize this board cannot perform.
    # Adding the handlers is D-7, at the bench.
    FIRMWARE_CONTROL_BYTES = frozenset({b"s"})

    # A DC probe runs at 120, not the stepper's 400. Before this table the
    # fallback was the stepper's number at every call site.
    PARAMS = _extend_params(
        BaseProbe.PARAMS,
        Param("x_step", "int", default=1, minimum=1, label="X Step Size"),
        Param("y_step", "int", default=1, minimum=1, label="Y Step Size"),
        Param("z_step", "int", default=1, minimum=1, label="Z Step Size"),
        Param("full_speed", "float", default=120, minimum=1,
              label="Autonomous Speed"),
        Param("man_full_speed", "float", default=120, minimum=1,
              label="Manual Speed"),
    )

    def __init__(self, port, controller_id, active_claims=None):
        super().__init__(port, controller_id, active_claims)
        self.packet_format = PACKET_FORMAT
        self.x_step = "1"
        self.y_step = "1"
        self.z_step = "1"
        self.full_speed = "120"
        self.slow_speed = "0"
        self.brake_distance = "0"
        self.man_full_speed = "120"

    @property
    def ui_schema(self):
        schema = copy.deepcopy(super().ui_schema)
        for section in schema["sections"]:
            if section["title"] == "Configuration":
                for name in ("slow_speed", "brake_distance"):
                    param = self.PARAMS[name]
                    section["elements"].append(
                        sch.entry(param.label + ":", name, param,
                                  disabled_when=("autonomous", "manual")))
                break
        return schema

    def get_params(self):
        params = super().get_params()
        params["slow_speed"] = self._param("slow_speed")
        params["brake_distance"] = self._param("brake_distance")
        return params


class ChuckPositioner(BaseProbe):
    PARAMS = _extend_params(
        BaseProbe.PARAMS,
        Param("x_step", "int", default=2, minimum=1, label="X Step Size"),
        Param("y_step", "int", default=2, minimum=1, label="Y Step Size"),
        Param("z_step", "int", default=2, minimum=1, label="Z Step Size"),
    )

    def __init__(self, port, controller_id, active_claims=None):
        super().__init__(port, controller_id, active_claims)
        self.x_step = "2"
        self.y_step = "2"
        self.z_step = "2"
