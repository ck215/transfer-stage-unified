"""The idle interlock, as a mixin any energizing Model can take (MOD-3).

Was probe code (CON-11). A model that holds hardware live while nobody uses
it declares `class X(IdleInterlock, Model)` and writes two hooks:

    _idle_is_armed         property: True while the clock should run
                           (probe: `is_enabled`)
    _on_idle_expired(idle) power down (probe: leave the mode through
                           `_set_mode(DISABLED, "idle interlock")`)

It calls `_start_interlock()` each time it arms, `_stop_interlock()` when it
disarms, and `_touch_activity()` on every real use. What it gets:

* `idle_remaining` / `idle_warn_seconds` in `state` (the views draw the
  countdown and its Extend from them: MODEL_CONTRACT.md section 9),
* the `extend_idle` command (the model declares it as an `internal` element
  so `run()` lets it through),
* one `events.IDLE_TIMEOUT_SOON` warning per idle period, inside the last
  `IDLE_WARN_SECONDS`, then `events.IDLE_TIMEOUT` when the clock runs out.

The loop is spawned with `Model._spawn` on a per-arming Event (STEPPER-7), so
the base `_stop_threads` stops it at close with every other loop.
"""
import threading
import time

from events import events
from result import Refused


class IdleInterlock:
    """Mixin: put it before `Model` in the bases."""

    #: s between checks. Overridable by tests to avoid a five-minute wait.
    INTERLOCK_POLL_INTERVAL = 5.0
    #: s of real inactivity before `_on_idle_expired`.
    INTERLOCK_TIMEOUT = 300.0
    #: How long before the interlock fires the views are told it is coming
    #: (Tier N, owner 2026-09-26): one warning per idle period, and
    #: `extend_idle` restarts the clock.
    IDLE_WARN_SECONDS = 60.0

    def __init__(self, *args, **kwargs):
        self._activity_time = time.monotonic()
        self._idle_warned = False
        # A fresh Event and generation per arming (STEPPER-7): one reused
        # Event meant a loop stopped by one disarm stayed stopped for the next
        # arming. Set while disarmed.
        self._interlock_stop = threading.Event()
        self._interlock_stop.set()
        self._interlock_generation = 0
        super().__init__(*args, **kwargs)

    # -- hooks the model writes --------------------------------------------
    @property
    def _idle_is_armed(self):
        """True while the idle clock should run."""
        return False

    def _on_idle_expired(self, idle):
        """Power down. Called on the interlock's thread; may raise (reported)."""

    # The operator's words. No seconds in the warning (PM8-4): the tray line
    # is history, the views' countdown is the live number.
    def _idle_soon_text(self):
        return f"{self.NAME} powers down soon unless it is used or you extend."

    def _idle_expired_text(self, idle):
        return f"{self.NAME} was idle for {idle:.0f} s, so it was powered down."

    def _idle_extended_text(self):
        return f"{self.NAME} stays on for another {self.INTERLOCK_TIMEOUT:.0f} s."

    def _idle_nothing_text(self):
        return f"Nothing to extend: {self.NAME} is not on."

    # -- what the model calls ------------------------------------------------
    def _touch_activity(self):
        self._activity_time = time.monotonic()
        self._idle_warned = False       # a fresh period gets its own warning

    def _stop_interlock(self):
        self._interlock_stop.set()

    def _start_interlock(self):
        """Arm the idle interlock for this arming (STEPPER-7, RC-3 item 5).

        **Each arming gets its own Event and generation.** The generation
        lets a stale loop from a previous arming retire itself rather than
        fight the current one -- `is_alive()` alone is not enough, because a
        thread told to stop stays alive until its next tick.
        """
        running = self._thread("interlock")
        if (running is not None and running.is_alive()
                and not self._interlock_stop.is_set()):
            # Already armed: a re-arm (AUTO -> MANUAL, say) is still the
            # operator doing something, so it restarts the clock (rb-pump
            # P2). Returning without this left 10 s on the clock after a
            # mode switch at 4 min 50 s.
            self._touch_activity()
            return
        self._interlock_stop = threading.Event()
        self._interlock_generation += 1
        generation = self._interlock_generation
        stop_event = self._interlock_stop
        self._touch_activity()
        self._spawn("interlock",
                    lambda: self._idle_loop(generation, stop_event),
                    stop=stop_event)

    def _idle_loop(self, generation, stop_event):
        events.debug("Interlock", f"armed, generation {generation}, "
                     f"{self.INTERLOCK_TIMEOUT:.0f} s", source=self.NAME)
        while not stop_event.wait(self.INTERLOCK_POLL_INTERVAL):
            if generation != self._interlock_generation:
                return
            if not self._idle_is_armed:
                return
            # **No flag deferral.** The clock is real inactivity: the model
            # extends it through `_touch_activity` on every real use. D-3
            # (owner): manual mode does idle-time-out.
            idle = time.monotonic() - self._activity_time
            remaining = self.INTERLOCK_TIMEOUT - idle
            if 0 < remaining <= self.IDLE_WARN_SECONDS and not self._idle_warned:
                # Once per idle period, before the power-down, so a view can
                # offer Extend (Tier N). `_touch_activity` re-arms it.
                self._idle_warned = True
                events.warn(events.IDLE_TIMEOUT_SOON, self._idle_soon_text(),
                            source=self.NAME)
            if idle > self.INTERLOCK_TIMEOUT:
                events.debug("Interlock", f"fired after {idle:.1f} s idle "
                             f"in {self.mode_name}", source=self.NAME)
                # Asks for an acknowledgement (events.ATTENTION): manual
                # mode ended by the clock is not a line to miss.
                events.warn(events.IDLE_TIMEOUT, self._idle_expired_text(idle),
                            source=self.NAME, ack=True)
                try:
                    self._on_idle_expired(idle)
                except Exception as exc:
                    events.debug("Idle Disable Failed", repr(exc),
                                 source=self.NAME, exception=exc)
                    events.warn("Idle Disable Failed", "The idle timeout "
                                f"could not disable {self.NAME}. Treat it "
                                "as live and stop it.", source=self.NAME,
                                exception=exc)
                return

    # -- what the views read -------------------------------------------------
    @property
    def idle_remaining(self):
        """Seconds until the interlock fires, or None while it is not armed
        (Tier N: what a view counts down from)."""
        if not self._idle_is_armed:
            return None
        return max(0.0, round(self.INTERLOCK_TIMEOUT
                              - (time.monotonic() - self._activity_time), 1))

    @property
    def idle_warn_seconds(self):
        return self.IDLE_WARN_SECONDS

    def extend_idle(self):
        """The operator's answer to the warning: restart the idle clock. Not
        a motion command, so no guard; refused when the clock is not
        running, since there is nothing to keep awake."""
        if not self._idle_is_armed:
            reason = self._idle_nothing_text()
            events.debug("Refused", reason, source=self.NAME)
            raise Refused(reason)
        self._touch_activity()
        # A new idle period is a new episode: its warning must not fold into
        # the last one's repeat count inside the log's dedupe window.
        events.forget(events.IDLE_TIMEOUT_SOON)
        events.info("Idle Timeout Extended", self._idle_extended_text(),
                    source=self.NAME)
        return True

    @property
    def state(self):
        snapshot = super().state
        snapshot.update({
            "idle_remaining": self.idle_remaining,
            "idle_warn_seconds": self.idle_warn_seconds,
        })
        return snapshot
