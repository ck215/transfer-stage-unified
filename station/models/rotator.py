"""The rotation stage. Was `RotatorSystem`; owns one `SMC100`.

Three things this model exists to get right, all of them ROTATOR-4:

1. **The tubing guard cannot be walked past.** Past +/-30 degrees the stage
   risks damaging physical tubing, so every move -- absolute, relative,
   home -- is judged on *where it lands* and asks the operator first by
   raising `NeedsConfirm`. It is a value the command raises, not a callback
   a view injects: the Web client never injected one, so the check existed
   there only as a refusal nobody was ever shown.

2. **A relative move is computed from the commanded target, not the polled
   position.** `position` is whatever the last poll wrote -- `None` until the
   first one, `None` again after a poll failure -- and it does not know about
   a move already in flight. Computing from it let five quick clicks of +4
   from 20 each look like a move to at most 26, so none tripped the guard and
   the stage ended at 40.

3. **A stop forgets the commanded target.** The stage halts wherever it is,
   which is not where it was going, so the next relative move has nothing
   safe to be checked against and must ask.

And the repair that motivated the rewrite of `home` (review finding 2): the
old `home()` committed a target of 0 *before* dispatching, and the dispatch
then refused because the FULL STOP latch was set. The refused Home left the
guard believing the stage was at the origin while it sat at 40, and the next
+10 step sailed through. `home()` now commits **only after `self._guard()`
passes**, which is `test_home_refused_while_latched_does_not_move_the_reference`.
"""
import threading
import time

from station import schema as sch
from station.devices.smc100 import SMC100, STATE_NAMES
from station.events import events
from station.model import Model
from station.param import Param
from station.result import Refused, NeedsConfirm


class Rotator(Model):
    NAME = "Rotator"
    IDENTITY = None          # an SMC100 answers no handshake byte
    NEEDS_PORT = True
    NEEDS_GAMEPAD = False

    #: Past this, moving risks damaging physical tubing.
    SAFE_ROTATION_DEG = 30.0

    #: 4 Hz. The old view-owned timers had drifted to 10 Hz on a device whose
    #: TS? transaction can run ~0.5 s when it is unhappy; the pre-refactor
    #: `main` polled at 2 Hz. This only refreshes two read-only fields.
    SAMPLE_INTERVAL = 0.25

    #: Long enough to let an in-flight poll return, short enough that closing
    #: a tab never feels hung. Never used on the stop path.
    THREAD_JOIN_TIMEOUT = 1.0

    SMC_ID = 1

    #: What a command says when there is no stage behind it (ROTATOR-13).
    #: There is no rotator simulator, so "simulated" would claim a capability
    #: that does not exist; the honest answer is a refusal with a reason.
    NOT_CONNECTED = ("The rotator is not connected. Choose a real port for it "
                     "in setup and reconnect - there is no rotator simulator "
                     "to fall back on.")

    PARAMS = {
        "target_deg": Param("target_deg", "float", default=0, minimum=-175,
                            maximum=175, decimals=2, unit="deg",
                            label="Target (deg)"),
        "step_deg": Param("step_deg", "float", default=0, minimum=-175,
                          maximum=175, decimals=2, unit="deg",
                          label="Step (deg)"),
    }

    def __init__(self, port=None, gamepad=None, sim=False):
        super().__init__()
        self._lock = threading.RLock()
        self._position = None
        self._motion_state = "Disconnected"
        self._commanded_target = None

        # One motion command in flight at a time. Each click used to spawn its
        # own thread straight into `move_relative_deg`, so the PRs raced and
        # their effects accumulated with no ordering (ROTATOR-4).
        self._motion_lock = threading.Lock()

        # Held for one poll transaction, acquired without blocking, so a slow
        # device cannot pile a second poll on the first. A queue of stacked
        # polls only makes the display more stale.
        self._poll_busy = threading.Lock()
        self._loops_stop = threading.Event()
        self._sample_thread = None
        self._poll_count = 0
        self._poll_ok = None

        self.port_name = port
        # A SIM or absent port is not a simulated rotator: it is no rotator.
        self.is_simulated = bool(sim) or str(port) in ("None", "SIM", "")
        self.smc = None if (self.is_simulated or port is None) else SMC100(
            self.SMC_ID, port, abort_if=self._estop.is_set)
        events.debug("Built", f"port={port!r} sim={sim} -> "
                     f"{'no stage' if self.smc is None else 'SMC100'}",
                     source=self.NAME)

    # -- devices and threads ----------------------------------------------
    @property
    def devices(self):
        return [self.smc] if self.smc is not None else []

    def _start_threads(self):
        """The model owns its sampler (ROTATOR-6 / RC-4).

        Nothing in `src/` called `poll_status` once the view-owned Tk/PySide
        timers were removed, so the card published whatever `connect()` had
        left behind and never moved again. A model with no stage starts no
        thread: construction stays cheap and test-constructed models do not
        leak one each.
        """
        if self.smc is None:
            return
        self._loops_stop.clear()
        if self._sample_thread is None or not self._sample_thread.is_alive():
            self._sample_thread = threading.Thread(
                target=self._sample_loop, daemon=True, name=f"sample-{self.NAME}")
            self._sample_thread.start()
            events.debug("Thread", "sampler started", source=self.NAME)

    def _stop_threads(self):
        self._loops_stop.set()
        thread, self._sample_thread = self._sample_thread, None
        if thread is not None and thread.is_alive():
            thread.join(self.THREAD_JOIN_TIMEOUT)
            events.debug("Thread", f"sampler stopped "
                         f"({'joined' if not thread.is_alive() else 'still running'})",
                         source=self.NAME)

    def disable(self):
        """A stage has nothing to de-energize.

        `Model.close` runs `halt` immediately before this, and `halt` is the
        safe state for a stage: it stops wherever it is. The latch is
        deliberately not set -- closing a tab is not a FULL STOP, and a model
        that is reopened must not need a latch cleared that nobody knowingly
        set.
        """
        return None

    # -- what the views read ----------------------------------------------
    @property
    def position(self):
        """Degrees, or `None` for "no reading".

        Read-only on purpose: `_poll` is the only writer. The old setter let
        a view or a test write a position the hardware never reported, and
        the +/-30 guard was checked against it.
        """
        with self._lock:
            return self._position

    @property
    def motion_state(self):
        """'Ready', 'Moving', 'Homing', 'Communication lost'... Written only
        by `_poll`. (`state` is the Model snapshot every view renders.)"""
        with self._lock:
            return self._motion_state

    @property
    def is_connected(self):
        return bool(self.smc is not None and self.smc.is_open)

    @property
    def is_active(self):
        """Moving right now: a command holds the motion lane, or the last
        poll saw the controller turning."""
        return self._motion_lock.locked() or self.motion_state in ("Moving", "Homing")

    @property
    def mode_name(self):
        """What `disabled_when` is matched against.

        A stage with no live link cannot execute anything, so every control
        that needs one is gated off from the schema -- one declaration, all
        three views, instead of three hardcoded per-view gates (ROTATOR-13).
        """
        if not self.is_connected:
            return "disconnected"
        return "moving" if self.is_active else "ready"

    @property
    def schema(self):
        parameters = self.PARAMS
        gated = ("disconnected",)
        return sch.schema(
            sch.section(
                "Stage",
                sch.readonly("Position (deg):", "position"),
                sch.readonly("Motion state:", "motion_state"),
                sch.indicator("Stage connected", "is_connected",
                              on_role="go", off_role="danger"),
            ),
            sch.section(
                "Motion",
                sch.entry("Target (deg):", "target_deg", parameters["target_deg"],
                          disabled_when=gated),
                sch.button("Move To", "move_to", inputs=("target_deg",),
                           role="go", disabled_when=gated),
                sch.entry("Step (deg):", "step_deg", parameters["step_deg"],
                          disabled_when=gated),
                self._step_button("Move +", 1, gated),
                self._step_button("Move -", -1, gated),
            ),
            sch.section(
                "Commands",
                sch.button("Home", "home", role="go", disabled_when=gated),
                sch.button("STOP", "halt", role="danger", disabled_when=gated),
                sch.button("Reset & Configure", "configure", disabled_when=gated),
            ),
            self._safety_section(),
        )

    def _step_button(self, text, sign, gated):
        """One command, two buttons, and the sign travels with the button."""
        return sch.button(text, "move_by", inputs=("step_deg",), args=(sign,),
                          disabled_when=gated)

    # -- commands ----------------------------------------------------------
    def home(self, confirmed=False):
        """Home the stage, which leaves it at 0.

        The target is committed **only after the guard passes**. Committing
        first meant a Home refused by the FULL STOP latch still reset the
        reference to 0, and the tubing check then measured the next relative
        move from an origin the stage was nowhere near (review finding 2).
        """
        return self._move_guarded(0.0, lambda: self.smc.home(), "home", confirmed,
                                  what="Home")

    def move_to(self, confirmed=False):
        """Absolute move. An absolute target needs no reference: it *is* one."""
        target = self.PARAMS["target_deg"].coerce(self.target_deg)
        return self._move_guarded(target, lambda: self.smc.move_absolute_deg(target),
                                  "move_to", confirmed, what="Move")

    def move_by(self, sign=1, confirmed=False):
        """Relative move of `step_deg * sign`. Was two near-identical commands.

        The target is `reference + step`, where the reference is the
        commanded target when there is one -- the sum of every move this
        model has accepted, and so the only value that can see a stack of
        clicks.
        """
        sign = 1 if float(sign) >= 0 else -1
        step = self.PARAMS["step_deg"].coerce(self.step_deg) * sign
        reference, is_known = self._reference_position()
        return self._move_guarded(reference + step,
                                  lambda: self.smc.move_relative_deg(step),
                                  "move_by", confirmed, is_known=is_known,
                                  args=(sign,), what="Move")

    def configure(self, confirmed=False):
        """Reset the controller and reload the stage's parameters.

        It ends in a home, so it moves -- it takes the same guard as a move.
        It also leaves the controller NOT REFERENCED, so the commanded target
        is forgotten: the next relative move must ask rather than assume.
        """
        self._require_stage()
        self._guard("Reset & Configure")
        if not confirmed:
            raise NeedsConfirm(
                "Reset the controller and reload the stage parameters?\n\n"
                "This resets the SMC100, reloads the stage's own settings and "
                "then homes the stage. It moves.", "configure")
        if not self._motion_lock.acquire(blocking=False):
            raise Refused("Reset & Configure refused: a move is already in flight")
        self._forget_target()
        self._run_motion(lambda: self.smc.reset_and_configure(), "Reset & Configure")
        return True

    # -- the guard --------------------------------------------------------
    def _move_guarded(self, target, send, command, confirmed, is_known=True,
                      args=(), what="Move"):
        """The +/-30 degree tubing check, in one place, for all three views.

        Order matters and is the whole of review finding 2: stage, latch,
        busy, confirmations, **then** commit, then dispatch. A refusal at any
        earlier step must leave the reference exactly as it was.
        """
        self._require_stage()
        self._guard(what)
        if self._motion_lock.locked():
            # ROTATOR-4: stacked moves. Refused where the operator sees it,
            # rather than queued behind the move already running.
            raise Refused(f"{what} refused: a move is already in flight")
        if not is_known and not confirmed:
            # An unknown position is not the origin. The stage may be at 25
            # with a +10 step queued, which computes to 10 against a default
            # of 0 and sails past the guard on its way to 35.
            raise NeedsConfirm(
                "The stage position is unknown - it has not been polled since "
                "connecting.\n\nA relative move cannot be checked against the "
                f"safe +/-{self.SAFE_ROTATION_DEG:.0f} deg range without it, and "
                "moving past that limit risks damaging physical tubing.\n\n"
                "Proceed anyway?", command, args=args)
        if abs(target) > self.SAFE_ROTATION_DEG and not confirmed:
            raise NeedsConfirm(
                f"Target rotation {target:.2f} deg exceeds the safe "
                f"+/-{self.SAFE_ROTATION_DEG:.0f} deg range.\n\nMoving past this "
                "limit risks damaging physical tubing.\n\nProceed?",
                command, args=args)
        # Taking the lane and committing the target happen here, on the
        # caller's thread, and only then is the worker started. Testing
        # `locked()` above and acquiring on the worker would leave a window in
        # which two commands both pass the busy guard, both commit, and only
        # one moves -- which is the stacked-move defect wearing a lock.
        if not self._motion_lock.acquire(blocking=False):
            raise Refused(f"{what} refused: a move is already in flight")
        self._commit_target(target)
        self._run_motion(send, f"{what} to {target:.2f} deg")
        return True

    def _require_stage(self):
        if self.smc is None or not self.smc.is_open:
            raise Refused(self.NOT_CONNECTED)

    def _reference_position(self):
        """Where the next relative move starts from: `(value, is_known)`.

        The commanded target wins when there is one. It falls back to the
        last polled position, and reports `is_known=False` when there is
        neither -- which the guard turns into a confirmation rather than an
        assumption at the origin.
        """
        with self._lock:
            commanded, position = self._commanded_target, self._position
        if commanded is not None:
            return commanded, True
        try:
            return float(position), True
        except (TypeError, ValueError):
            return 0.0, False

    def _commit_target(self, target):
        """Record an accepted move before it is dispatched, never after.

        After, and two clicks in the same tick both compute from the old
        value and neither sees the other.
        """
        with self._lock:
            self._commanded_target = target
        events.debug("Target", f"commanded target -> {target:.2f} deg",
                     source=self.NAME)

    def _forget_target(self):
        """The stage is somewhere we did not command it to be."""
        with self._lock:
            had = self._commanded_target
            self._commanded_target = None
        if had is not None:
            events.debug("Target", "commanded target forgotten; the next "
                         "relative move must ask", source=self.NAME)

    # -- motion ------------------------------------------------------------
    def _run_motion(self, send, what):
        """The one motion wrapper. Was `_run_async` + `_async_wrapper` +
        `_run_guarded`, three nested layers each re-checking the latch.

        The check that counts now happens **inside the port lock**: the
        SMC100 carries `abort_if=self._estop.is_set` into every
        `SerialPort.write`, so a stop that lands while this command is queued
        on the lock aborts it before its bytes go out. What is left here is
        dispatch and what to do when a move does not finish.

        **The caller holds `_motion_lock`**; this releases it when the move
        ends, however it ends.
        """
        def _body():
            started = time.monotonic()
            try:
                send()
                self._clear_fault()
                events.debug("Motion", f"{what} finished in "
                             f"{time.monotonic() - started:.2f}s", source=self.NAME)
            except Exception as exc:
                # The move did not finish, and nothing on this path stopped
                # the stage: a wait timeout says only that the driver stopped
                # watching (ROTATOR-11). So the stage is somewhere we did not
                # command it to be, and the commanded target is no longer
                # something the next relative move may be measured against.
                self._forget_target()
                if self._estop.is_set():
                    events.info("Move Aborted", f"{what} was cut short by FULL "
                                "STOP", source=self.NAME)
                else:
                    self._fault(f"{what} failed: {exc}")
                events.debug("Motion", f"{what} raised after "
                             f"{time.monotonic() - started:.2f}s", source=self.NAME,
                             exception=exc)
            finally:
                self._motion_lock.release()

        threading.Thread(target=_body, daemon=True, name=f"motion-{self.NAME}").start()

    def _halt_hardware(self):
        """ST on the priority lane, and the reference is dropped.

        `Model.estop` latches before calling this and bounds how long it
        waits; the latch is what stops the *next* command, this is what stops
        the stage. The commanded target goes with it: the stage halts wherever
        it happens to be, not where it was going.
        """
        self._forget_target()
        smc = self.smc
        if smc is None:
            # MANAGER-21: a rotator with no controller cannot report that the
            # stage halted, because it cannot see the stage.
            events.debug("Stop", "no stage: nothing was sent", source=self.NAME)
            return False
        started = time.monotonic()
        landed = bool(smc.stop(priority=True))
        events.debug("Stop", f"priority ST {'confirmed' if landed else 'NOT written'}"
                     f" in {(time.monotonic() - started) * 1000:.1f} ms",
                     source=self.NAME)
        if not landed:
            # Log panel, not a popup: `Model.estop`, `toggle_estop` and
            # `Controller.estop_all` own the acknowledged "Stop Not Confirmed"
            # error, and raising a second one from inside a close path would
            # block the exit.
            events.warn("Stop Not Written", "the priority ST was not written; "
                        "treat the stage as live", source=self.NAME)
        return landed

    # -- sampling ----------------------------------------------------------
    def _sample_loop(self):
        """Poll on the model's own thread.

        Views read the cached properties and never touch the transport, so a
        stalled read here cannot delay a render tick or a FULL STOP: the stop
        takes the priority lane and does not wait on whatever this is doing.
        `_poll_busy` is a non-blocking acquire, so a tick that arrives while
        the previous poll is still out is skipped rather than queued.
        """
        while not self._loops_stop.wait(self.SAMPLE_INTERVAL):
            if not self._poll_busy.acquire(blocking=False):
                events.debug("Poll", "tick skipped: the previous poll is still "
                             "out", source=self.NAME, every=5.0)
                continue
            try:
                self._poll()
            except Exception as exc:
                # Best effort: a transport hiccup must not kill the loop, or
                # the display freezes silently for the rest of the session.
                events.debug("Poll", f"sample failed: {exc}", source=self.NAME,
                             exception=exc, every=5.0)
            finally:
                self._poll_busy.release()

    def _poll(self):
        """Read position and state. The only writer of either.

        ROTATOR-9: a failed poll publishes "Communication lost" and clears
        the position. Leaving the last good values on screen tells the
        operator the stage is Ready when it is unreachable -- which is
        exactly when someone reaches for FULL STOP.
        """
        smc = self.smc
        if smc is None:
            return
        self._poll_count += 1
        try:
            position = smc.get_position_deg()
            errors, code = smc.get_status()
        except Exception as exc:
            self._publish(None, "Communication lost")
            if self._poll_ok is not False:
                # Once per transition, not once per tick: the loop rule is
                # that nothing publishes per iteration.
                events.warn("Rotator Unreachable", f"the stage stopped "
                            f"answering: {exc}", source=self.NAME, exception=exc)
            self._poll_ok = False
            events.debug("Poll", f"failed: {exc}", source=self.NAME,
                         exception=exc, every=5.0)
            return
        self._publish(round(position, 4), self._motion_state_name(code))
        if errors:
            self._fault(f"controller error 0x{errors:04X}")
        else:
            self._clear_fault()
        if self._poll_ok is False:
            events.info("Rotator Back", "the stage is answering again",
                        source=self.NAME)
        self._poll_ok = True
        events.debug("Poll", f"{self._poll_count} polls; {position:.4f} deg, "
                     f"state {code} ({STATE_NAMES.get(code, 'unknown')}), "
                     f"errors 0x{errors:04X}; nominal rate "
                     f"{1 / self.SAMPLE_INTERVAL:.1f} Hz", source=self.NAME,
                     every=5.0)

    def _publish(self, position, motion_state):
        with self._lock:
            changed = motion_state != self._motion_state
            self._position = position
            self._motion_state = motion_state
        self._touch()
        if changed:
            events.debug("Motion State", f"-> {motion_state}", source=self.NAME)

    def _motion_state_name(self, code):
        """The controller's two-character state code as an operator reads it."""
        code = str(code).upper()
        if code in ("0A", "0B", "0C", "0D", "0E", "0F", "10", "11"):
            return "Not referenced - run Home"
        if code in ("32", "33", "34", "35"):
            return "Ready"
        if code in ("1E", "1F"):
            return "Homing"
        if code == "28":
            return "Moving"
        if code in ("3C", "3D", "3E", "3F"):
            return "Disabled"
        if code == "14":
            return "Configuring"
        return code
