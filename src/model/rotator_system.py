import threading
import time
from model import schema as sch
from model.params import Param, table as _param_table
from model.base import SchemaCommands
try:
    from lib import smc100
except ImportError:
    smc100 = None

class RotatorSystem(SchemaCommands):
    #: Past this, moving risks damaging physical tubing.
    SAFE_ROTATION_DEG = 30.0

    PARAMS = _param_table(
        Param("target_deg", "float", default=0, minimum=-175, maximum=175,
              decimals=2, unit="deg", label="Target (deg)"),
        Param("step_deg", "float", default=0, minimum=-175, maximum=175,
              decimals=2, unit="deg", label="Step (deg)"),
        Param("position", "text", default="0", label="Position (deg)"),
    )

    def __del__(self):
        print(f"[{self.__class__.__name__}] Destructor called")

    def __init__(self, default_port=None):
        self.port = default_port
        self.smc_id = 1
        
        self._lock = threading.Lock()
        self._position = None
        self._state = "Disconnected"
        self._error = "0"

        # FULL STOP latch (RC-5), the same contract the probes and the heater
        # already carry. Set by `emergency_stop` *before* any I/O and checked
        # immediately before every motion write, so a move already queued on a
        # worker thread cannot land after the stop. Cleared only by an
        # explicit operator action: a latch that clears itself is not a latch.
        #
        # The rotator did not have one. S8 was recorded as closing this for
        # every device, but its test built a probe, so the stage kept the gap
        # (ROTATOR-8).
        self._estop = threading.Event()

        # Where the stage is *going*, not where it last reported being
        # (ROTATOR-4). `position` is whatever the last poll wrote: it is None
        # from construction until the first successful poll and None again
        # after a reconnect, and it does not know about a move already in
        # flight. Computing a relative target from it let five quick clicks of
        # +4 from 20 each look like a move to at most 26, so none of them
        # tripped the +/-30 tubing guard and the stage ended at 40.
        #
        # None means "unknown", which is a state the guard must be able to
        # see. The old `_current_position` turned it into 0.0, so an unknown
        # position silently became a known one at the origin.
        self._commanded_target = None

        # One motion command in flight at a time. Each click used to spawn its
        # own thread straight into `smc.move_relative_deg`, so the PRs raced
        # and their effects accumulated with no ordering (ROTATOR-4).
        self._motion_lock = threading.Lock()
        
        self.smc = None
        self.is_connected = False
        self.error_callback = None
        
        self.target_deg = "0"
        self.step_deg = "0"
        
        if default_port and default_port != "None" and default_port != "SIM":
            self.connect(default_port, self.smc_id)
        
    @property
    def position(self):
        with self._lock: return self._position
        
    @position.setter
    def position(self, value):
        with self._lock: self._position = value

    @property
    def state(self):
        with self._lock: return self._state
        
    @state.setter
    def state(self, value):
        with self._lock: self._state = value

    @property
    def error(self):
        with self._lock: return self._error

    @error.setter
    def error(self, value):
        with self._lock: self._error = value

    #: What a command says when there is no stage behind it (ROTATOR-13).
    NOT_CONNECTED = ("The rotator is not connected. Choose a real port for "
                     "it in setup and reconnect — there is no rotator "
                     "simulator to fall back on.")

    @property
    def connection_status(self):
        """The model's own account of the link, for every renderer.

        **Never "simulated".** A port of "SIM"/"None" skips `connect()` and
        leaves `smc` as None; nothing about the stage is then simulated, so
        a badge reading SIMULATED claims a capability that does not exist
        (ROTATOR-13). Each view used to guess this from the port string —
        the Web adapter checked for "SIM" *before* it checked whether the
        device was connected — which is how that badge appeared next to a
        state line reading "Disconnected".
        """
        with self._lock:
            return "hardware" if (self.is_connected and self.smc) else "disconnected"

    def _run_async(self, func, *args):
        """Helper to run blocking operations in a thread."""
        thread = threading.Thread(target=self._async_wrapper, args=(func, args))
        thread.daemon = True
        thread.start()

    def _async_wrapper(self, func, args):
        # Checked here, on the worker, immediately before the command goes
        # out -- not at the call site. The race ROTATOR-8 describes is a click
        # that spawns a thread, then a FULL STOP, then the thread reaching
        # `sendcmd` and issuing PA *after* the ST. Checking at dispatch would
        # not see that stop; checking here does.
        if self._estop.is_set():
            print(f"[{self.__class__.__name__}] Motion refused: "
                  f"FULL STOP is latched")
            return
        with self._motion_lock:
            # Re-checked after acquiring: this command may have waited here
            # behind another move while a FULL STOP landed.
            if self._estop.is_set():
                print(f"[{self.__class__.__name__}] Queued motion refused: "
                      f"FULL STOP latched while it waited")
                return
            self._run_guarded(func, args)

    def _run_guarded(self, func, args):
        try:
            func(*args)
        except Exception as e:
            # The move did not finish, and nothing on this path stopped the
            # stage: `SMC100WaitTimedOutException` says only that the driver
            # stopped watching (ROTATOR-11). So the stage is somewhere we did
            # not command it to be, and the commanded target is no longer a
            # position the next relative move may be checked against — the
            # same rule `emergency_stop` follows (safety-pattern.md item 6).
            # It was kept, so a timed-out move to 10 left the guard believing
            # the stage was at 10.
            self._forget_target()
            if self.error_callback:
                self.error_callback(e)
            else:
                try:
                    from error_routing import ErrorRouter
                    ErrorRouter.report_error("Rotator Controller Error", f"Action failed:\n{e}", e)
                except Exception:
                    pass

    def connect(self, port: str, smc_id: int = 1):
        with self._lock:
            if self.is_connected:
                return
            self.port = port
            self.smc_id = smc_id
        if smc100 is not None:
            try:
                smc_inst = smc100.SMC100(
                    smcID=self.smc_id,
                    port=self.port,
                    silent=True,
                    sleepfunc=time.sleep,
                )
                smc_inst.get_status(silent=True)
                with self._lock:
                    self.smc = smc_inst
                    self.is_connected = True
                    self._state = "Connected"
            except Exception as e:
                with self._lock:
                    self.smc = None
                    self.is_connected = False
                    self._state = "Disconnected"
                if self.error_callback:
                    self.error_callback(e)
                else:
                    try:
                        from error_routing import ErrorRouter
                        ErrorRouter.report_error("Rotator Connection Error", f"Failed to connect to SMC100 on {port}:\n{e}", e)
                    except Exception:
                        pass

    def disconnect(self):
        with self._lock:
            smc = self.smc
            self.smc = None
            self.is_connected = False
            self._position = None
            self._commanded_target = None
            self._state = "Disconnected"
            self._error = "0"
        if smc:
            try:
                smc.close()
            except Exception:
                pass

    def teardown(self):
        """Stop the stage, then release the port (MANAGER-10, ROTATOR-1).

        This used to call disconnect() alone, so tearing the rotator down
        never sent ST — a stage mid-move kept moving after the port closed,
        with nothing left able to stop it.
        """
        try:
            self.stop(priority=True)
        except Exception as e:
            print(f"[{self.__class__.__name__}] Stop failed during teardown: {e}")
        self.disconnect()

    ESTOP_RETURN_BUDGET = 0.08

    @property
    def estop_latched(self):
        return self._estop.is_set()

    def clear_estop(self):
        """Explicit operator action. Nothing else may call this (RC-5)."""
        self._estop.clear()
        print(f"[{self.__class__.__name__}] FULL STOP latch cleared by operator")

    def emergency_stop(self):
        """Latch first, then dispatch the stop with a bounded wait.

        This was `self.stop()` -- one synchronous call into `smc.stop()`,
        into `sendcmd('ST')`, into `with self._serial_lock`. A poll
        transaction holds that lock for up to about half a second, so FULL
        STOP on the rotator queued behind whatever the poller was doing while
        the stage kept turning. It is the identical shape to the probe defect
        S8 fixed, on the one device S8's test did not cover (ROTATOR-8).

        Two mechanisms, matching `BaseProbe.emergency_stop`:

        1. **The latch is set before any I/O** and is synchronous, so from
           this line on no further motion command can be issued -- including
           one already queued on a worker thread.
        2. **The write is dispatched and joined with a bound.** It goes out on
           a worker so a wedged port cannot hold the caller, and it takes the
           priority path, which refuses to wait on the serial lock. If the
           join expires the stop is still in flight; we stop *waiting* for it.
        """
        self._estop.set()
        # The stage halts wherever it happens to be, which is not where we
        # commanded it. Anything else would let the next relative move be
        # checked against a target that was never reached.
        self._forget_target()

        done = threading.Event()

        def _stop():
            try:
                self.stop(priority=True)
            finally:
                done.set()

        worker = threading.Thread(
            target=_stop, daemon=True,
            name=f"estop-{self.__class__.__name__}")
        worker.start()
        if not done.wait(self.ESTOP_RETURN_BUDGET):
            print(f"[{self.__class__.__name__}] FULL STOP: latched; hardware "
                  f"stop still in flight after {self.ESTOP_RETURN_BUDGET}s")

    def home(self):
        if not self.smc:
            # Refused, not silently skipped (ROTATOR-13). This used to be a
            # bare `if self.smc:` with no else, so with no port every button
            # in the rotator card did nothing and said nothing.
            from results import Refused
            return Refused(self.NOT_CONNECTED)
        self._commit_target(0.0)
        self._run_async(self.smc.home)

    # -- schema commands (D-5): the values are already validated ---------
    #
    # These take no arguments. `execute_command` has committed the declared
    # inputs to the model before calling, so there is nothing left to parse
    # here — which is why the three `safe_float`-then-bail shims these replace
    # are gone. A value that could not be parsed never reaches a command now;
    # it is refused by name, at the field, where the operator can see it.

    def move_absolute(self, confirmed=False):
        # An absolute target needs no reference position: it *is* the target.
        return self._guarded_move(
            self.PARAMS["target_deg"].coerce(self.target_deg),
            lambda target: self.smc.move_absolute_deg(target),
            "move_absolute", confirmed)

    def move_relative_positive(self, confirmed=False):
        step = self.PARAMS["step_deg"].coerce(self.step_deg)
        reference, known = self._reference_position()
        return self._guarded_move(
            reference + step,
            lambda _target: self.smc.move_relative_deg(step),
            "move_relative_positive", confirmed, known=known)

    def move_relative_negative(self, confirmed=False):
        step = self.PARAMS["step_deg"].coerce(self.step_deg)
        reference, known = self._reference_position()
        return self._guarded_move(
            reference - step,
            lambda _target: self.smc.move_relative_deg(-step),
            "move_relative_negative", confirmed, known=known)

    def _reference_position(self):
        """Where the next relative move starts from: `(value, known)`.

        `_commanded_target` wins when it is set, because it is the sum of
        every move this model has accepted and therefore the only value that
        sees a stack of clicks. It falls back to the last polled position, and
        reports `known=False` when there is neither — which the guard turns
        into a confirmation rather than an assumption.
        """
        with self._lock:
            commanded = self._commanded_target
            position = self._position
        if commanded is not None:
            return commanded, True
        try:
            return float(position), True
        except (ValueError, TypeError):
            return 0.0, False

    def _commit_target(self, target):
        """Record an accepted move before it is dispatched, never after.

        After, and two clicks in the same tick both compute from the old
        value. This is the whole of failure scenario B.
        """
        with self._lock:
            self._commanded_target = target

    def _forget_target(self):
        """The stage is somewhere we did not command it to be."""
        with self._lock:
            self._commanded_target = None

    def _guarded_move(self, target, run, command_name, confirmed, known=True):
        """The ±30° tubing check, in one place, for all three frontends.

        **Returns `NeedsConfirmation` rather than calling back into a view.**
        The old `confirm_rotation_callback` was injected into the model by
        whichever view happened to build it — and the Web client never
        injected one, so `_confirm_rotation` fell through to its "blocked
        automatically" branch and the check existed only as a refusal nobody
        was shown. A guard that silently declines is not the same as a guard
        the operator can answer (S10 item 3).
        """
        if not self.smc:
            from results import Refused
            return Refused(self.NOT_CONNECTED)
        if self._estop.is_set():
            # Refused where the operator can see it, rather than dispatched
            # and dropped on the worker. Both happen; only this one is visible.
            print(f"[{self.__class__.__name__}] {command_name} refused: "
                  f"FULL STOP is latched")
            return False
        if not known and not confirmed:
            # An unknown position is not the origin. The stage may be at 25
            # with a +10 step queued, which computes to 10 against a default
            # of 0 and sails past the guard on its way to 35 (ROTATOR-4,
            # failure scenario A).
            return sch.NeedsConfirmation(
                "The stage position is unknown \u2014 it has not been polled "
                "since connecting.\n\n"
                "A relative move cannot be checked against the safe "
                f"\u00b1{self.SAFE_ROTATION_DEG:.0f}\u00b0 range without it, "
                "and moving past that limit risks damaging physical "
                "tubing.\n\nProceed anyway?",
                command_name)
        if abs(target) > self.SAFE_ROTATION_DEG and not confirmed:
            return sch.NeedsConfirmation(
                f"Target rotation {target:.2f}\u00b0 exceeds the safe "
                f"\u00b1{self.SAFE_ROTATION_DEG:.0f}\u00b0 range.\n\n"
                "Moving past this limit risks damaging physical tubing.\n\n"
                "Proceed?",
                command_name)
        # Committed *before* dispatch. Two clicks in the same tick otherwise
        # both compute from the pre-move value and neither sees the other.
        self._commit_target(target)
        self._run_async(run, target)
        return True

    @property
    def ui_schema(self):
        P = self.PARAMS
        return sch.schema(
            sch.section(
                "Device Status",
                sch.readonly("Position (deg):", "position", param=P["position"]),
                sch.readonly("State Code:", "state"),
                sch.readonly("Error Code:", "error", role="warning"),
            ),
            sch.section(
                "Commands",
                sch.button("Home Stage", "home", role="go"),
                sch.button("STOP", "stop", role="danger"),
                sch.button("Reset & Config", "reset_and_configure"),
            ),
            sch.section(
                "Motion Control",
                sch.entry("Target (deg):", "target_deg", P["target_deg"]),
                # **D-5 + the confirm contract.** The target travels with the
                # command, and a target outside +/-30 deg comes back as
                # NeedsConfirmation rather than going through a callback the
                # view injected into the model. That callback was never
                # supplied by the Web client, so the tubing check simply did
                # not exist there — a refusal the operator never sees is not
                # a check (S10 item 3).
                sch.button("Move Absolute", "move_absolute",
                           inputs=("target_deg",), role="go"),
                sch.entry("Step (deg):", "step_deg", P["step_deg"]),
                sch.button("Move +", "move_relative_positive",
                           inputs=("step_deg",)),
                sch.button("Move -", "move_relative_negative",
                           inputs=("step_deg",)),
            ),
        )

    def stop(self, priority=False):
        # `self.smc` is read under the model lock: it can be set to None by a
        # concurrent `disconnect()`, and the old code read it twice, so a
        # disconnect landing between the test and the call raised
        # AttributeError inside the stop path (ROTATOR-8).
        with self._lock:
            smc = self.smc
        if smc:
            try:
                smc.stop(priority=priority)
            except Exception as e:
                if self.error_callback:
                    self.error_callback(e)
                else:
                    try:
                        from error_routing import ErrorRouter
                        ErrorRouter.report_error("Rotator Error", f"Failed to send stop:\n{e}", e)
                    except Exception:
                        pass

    def reset_and_configure(self):
        if not self.smc:
            from results import Refused
            return Refused(self.NOT_CONNECTED)
        self._run_async(self.smc.reset_and_configure)

    # `_confirm_rotation`, `move_absolute(target_deg)` and
    # `move_relative(step_deg)` are gone, replaced by the guarded schema
    # commands above. The view-injected `confirm_rotation_callback` went with
    # them: the confirmation is a value the model returns, which every
    # frontend renders with one generic dialog.

    def _map_state_code(self, code: str) -> str:
        code = str(code).upper()
        try:
            from lib.smc100 import (
                STATE_NOT_REFERENCED_FROM_RESET, STATE_NOT_REFERENCED_FROM_HOMING, STATE_NOT_REFERENCED_FROM_CONFIGURATION,
                STATE_READY_FROM_HOMING, STATE_READY_FROM_MOVING, STATE_READY_FROM_DISABLE,
                STATE_HOMING_FROM_RS232, STATE_HOMING_FROM_SMC_RC,
                STATE_MOVING,
                STATE_DISABLE_FROM_READY, STATE_DISABLE_FROM_MOVING, STATE_DISABLE_FROM_JOGGING
            )
            if code in (STATE_NOT_REFERENCED_FROM_RESET, STATE_NOT_REFERENCED_FROM_HOMING, STATE_NOT_REFERENCED_FROM_CONFIGURATION):
                return "Not referenced - run Home"
            elif code in (STATE_READY_FROM_HOMING, STATE_READY_FROM_MOVING, STATE_READY_FROM_DISABLE):
                return "Ready"
            elif code in (STATE_HOMING_FROM_RS232, STATE_HOMING_FROM_SMC_RC):
                return "Homing"
            elif code == STATE_MOVING:
                return "Moving"
            elif code in (STATE_DISABLE_FROM_READY, STATE_DISABLE_FROM_MOVING, STATE_DISABLE_FROM_JOGGING, "3F"): # 3F not in SMC100 docs
                return "Disabled"
        except ImportError:
            if code in ("0A", "0B", "0C"):
                return "Not referenced - run Home"
            elif code in ("32", "33", "34"):
                return "Ready"
            elif code in ("1E", "1F"):
                return "Homing"
            elif code == "28":
                return "Moving"
            elif code in ("3C", "3D", "3E", "3F"):
                return "Disabled"
        return code

    def poll_status(self):
        """Poll the rotator for current position and state.

        ROTATOR-9: On poll failure, explicitly set state to indicate
        communication loss and clear position rather than leaving stale values.
        The UI should not show a false "Ready" state when the device is
        unreachable.
        """
        if self.is_connected and self.smc:
            try:
                pos = self.smc.get_position_deg()
                err, state = self.smc.get_status(silent=True)

                self.position = pos
                self.state = self._map_state_code(state)
                self.error = str(err)
            except Exception as e:
                # Poll failure: clear state to indicate communication loss
                self.position = None
                self.state = "Communication lost"
                try:
                    from error_routing import ErrorRouter
                    ErrorRouter.report_warning("Rotator Poll Error", f"Failed to read status:\n{e}")
                except Exception:
                    pass
