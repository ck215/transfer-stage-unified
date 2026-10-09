"""Base of every station model. Owns its Devices and the ONE estop latch.

A subclass writes `_halt_hardware()` (the strongest stop it has, on the
priority lane) and its own commands. It never re-implements the latch, the
bounded stop, or the close order.
"""
import threading
import time

import schema as sch
from devices.serial_port import SerialPort
from events import events
from panel import Panel
from result import Refused, NeedsConfirm


class Model(Panel):
    IDENTITY = None          # handshake identity byte, or None
    #: The NAME of the model whose page shows this one (owner ruling
    #: 2026-09-28: Red Percent and the Transfer Map are one dashboard). A
    #: hosted model keeps its own Panel, commands, stop and tests; only
    #: where the views DRAW it changes, and only while its host is launched
    #: (`Controller.state` publishes `host` per model; on its own it is a
    #: page like any other). None for every other model.
    HOST = None
    NEEDS_PORT = False
    NEEDS_GAMEPAD = False
    ESTOP_BUDGET = 0.08      # s the caller waits for the hardware stop
    #: s `_stop_threads` waits for each spawned loop (MOD-2). The largest of
    #: the four per-model values it replaced: Red Percent's 2.0 (probe 1.0,
    #: heater 1.5, rotator 1.0). Never used on the stop path.
    THREAD_JOIN_TIMEOUT = 2.0

    def __init__(self):
        super().__init__()
        self._estop = threading.Event()
        #: None while clear; after a stop, whether the hardware confirmed it
        #: in time (round 7: the views mark an unconfirmed stop from state,
        #: not by parsing the event log).
        self._stop_confirmed = None
        self._latched_at = None      # wall time the latch closed (round 7)
        self._fault_reason = ""
        self._updated_at = time.monotonic()
        #: The one stop flag every long-lived loop waits on (MOD-2). Set by
        #: `_stop_threads`; `_spawn` clears it again.
        self._threads_stop = threading.Event()
        self._spawned = []           # [(name, thread, stop_event)], newest last
        self._spawn_lock = threading.Lock()
        #: True while `_on_link_lost` runs its stop: a stop that cannot land
        #: on a lost link is that loss, not a fault (L1).
        self._link_loss_in_progress = False
        #: Set by `close()`: a late stop landing on the way out is logged,
        #: not announced (nobody is left to read it, and close raises none).
        self._closing = False

    # -- devices and lifecycle --------------------------------------------
    @property
    def devices(self):
        """The Devices this model owns. Subclasses return their list."""
        return []

    def open(self):
        """Open every owned Device, then start this model's threads.

        Each owned SerialPort is told who owns it and how the owner reacts
        to a loss, before it opens (L1): the one place for every model."""
        for port in self._link_ports():
            port.set_link_handlers(on_lost=self._on_link_lost,
                                   on_restored=self._on_link_restored,
                                   owner=self.NAME)
        for device in self.devices:
            device.open()
        self._start_threads()

    # -- the serial link -------------------------------------------------
    def _link_ports(self):
        """The hardware SerialPorts this model owns directly (a recording
        double in a test is not one)."""
        return [d for d in self.devices if isinstance(d, SerialPort)]

    def _on_link_lost(self, why):
        """A port of this model lost its link (L1). Runs on the port's loss
        worker while the handle is still open, bounded by the port.

        The model's strongest stop first, on the priority lane, then the
        model leaves its mode through `_leave_mode_for_link_loss`: to
        DISABLED, never to FAULT, since FAULT is the needs-a-person latch
        and the link recovers by itself. -> True when the stop landed."""
        events.debug("Link Lost", f"{why}; stopping before the handle closes",
                     source=self.NAME)
        self._link_loss_in_progress = True
        try:
            try:
                landed = bool(self._halt_hardware())
            except Exception as exc:
                landed = False
                events.debug("Link Loss Stop Raised", repr(exc),
                             source=self.NAME, exception=exc)
            try:
                self._leave_mode_for_link_loss(landed)
            except Exception as exc:
                events.debug("Link Loss Mode Change Raised", repr(exc),
                             source=self.NAME, exception=exc)
        finally:
            self._link_loss_in_progress = False
        events.debug("Link Loss Stop", f"landed={landed}", source=self.NAME)
        return landed

    def _leave_mode_for_link_loss(self, landed):
        """Leave whatever mode the model is in, without faulting. Nothing by
        default; a model with modes overrides it."""

    def _on_link_restored(self):
        """A port of this model is back (L2). Nothing restarts by itself:
        the model stays where the loss left it (DISABLED) until the operator
        enters a mode again, the same rule as `clear_estop`."""
        names = ", ".join(str(p.port) for p in self._link_ports()) or "its port"
        events.info(events.LINK_RESTORED, f"{self.NAME} is back on {names}. "
                    "Re-enable it when you are ready.", source=self.NAME)

    def _is_link_down(self):
        """True while an owned port is lost or reconnecting."""
        return any(p.status in ("lost", "reconnecting")
                   for p in self._link_ports())

    def close(self):
        """Stop threads, halt, de-energize, close devices. Each step isolated:
        the hardware steps are never skipped because an earlier step raised."""
        self._closing = True
        for step in (self._stop_threads, self.halt, self.disable):
            try:
                step()
            except Exception as exc:
                events.debug("Close Step Failed", f"{step.__name__}: {exc!r}",
                             source=self.NAME, exception=exc)
                events.warn("Close Step Failed", f"{self.NAME} did not close "
                            "cleanly. Check that it is stopped before you "
                            "unplug it.", source=self.NAME, exception=exc)
        for device in self.devices:
            try:
                device.close()
            except Exception as exc:
                events.debug("Device Close Failed", f"{device!r}: {exc!r}",
                             source=self.NAME, exception=exc)
                events.warn("Device Close Failed", f"A device of {self.NAME} "
                            "did not close cleanly. If it will not reconnect, "
                            "unplug it and plug it back in.", source=self.NAME,
                            exception=exc)

    def _start_threads(self):
        """Start this model's loops, each with `_spawn`. Nothing by default."""

    def _spawn(self, name, target, stop=None):
        """Start `target` on a daemon thread and record it for `_stop_threads`.

        `stop` is the Event the loop waits on; by default the model's one
        `_threads_stop`, which this clears (the model is running loops
        again). A loop that owns a narrower Event (the idle interlock's,
        one per arming) passes it: the base sets it at close as well, so the
        loop leaves at once instead of outliving the join.

        A live loop of the same name whose stop is not set is returned as it
        is, not started twice.
        """
        if stop is None:
            stop = self._threads_stop
            stop.clear()
        with self._spawn_lock:
            self._spawned = [e for e in self._spawned if e[1].is_alive()]
            for known, thread, known_stop in reversed(self._spawned):
                if known == name and not known_stop.is_set():
                    return thread
            thread = threading.Thread(target=target, daemon=True,
                                      name=f"{name}-{self.NAME}")
            self._spawned.append((name, thread, stop))
        thread.start()
        events.debug("Thread Started", name, source=self.NAME)
        return thread

    def _thread(self, name):
        """The newest thread spawned under `name`, or None."""
        with self._spawn_lock:
            for known, thread, _stop in reversed(self._spawned):
                if known == name:
                    return thread
        return None

    def _spawned_threads(self):
        with self._spawn_lock:
            return [thread for _name, thread, _stop in self._spawned]

    def _stop_threads(self):
        """Set every loop's stop, then join each within THREAD_JOIN_TIMEOUT.

        `close()` runs this before the devices close, so no loop is inside a
        read on a descriptor closing under it. A loop that does not leave in
        time is reported and left behind (it is a daemon); the close goes on.
        """
        self._threads_stop.set()
        with self._spawn_lock:
            spawned = list(self._spawned)
        for _name, _thread, stop in spawned:
            stop.set()
        current = threading.current_thread()
        for name, thread, _stop in spawned:
            if thread is current or not thread.is_alive():
                continue
            started = time.monotonic()
            thread.join(self.THREAD_JOIN_TIMEOUT)
            events.debug("Thread Stopped", f"{name}: join took "
                         f"{(time.monotonic() - started) * 1000:.1f} ms; "
                         f"alive={thread.is_alive()}", source=self.NAME)
            if thread.is_alive():
                events.warn("Thread Still Running",
                            f"A {self.NAME} loop ({name}) did not stop within "
                            f"{self.THREAD_JOIN_TIMEOUT} s; closing anyway.",
                            source=self.NAME)

    def enable(self):
        pass

    def disable(self):
        pass

    def on_model_added(self, name, model):
        pass

    def on_model_removed(self, name, model):
        pass

    # -- stopping ----------------------------------------------------------
    def _halt_hardware(self):
        """Send the strongest hardware stop this model has. Return True when
        it landed. Must use the priority lane and must not wait on a lock
        without a timeout."""
        return True

    def halt(self):
        """Stop motion / heat now. No latch."""
        return bool(self._halt_hardware())

    def estop(self):
        """Latch first (cannot fail), then stop the hardware on a worker and
        wait at most ESTOP_BUDGET. True only if the stop landed in time.

        L11 (SF-6): the budget is the owner's number and is not widened. A
        stop that lands after it is logged with its real latency, revises
        `stop_confirmed` to True for the same latch episode, and is
        reported with an info line, so "not confirmed" is never left
        standing for a stop that went out."""
        self._estop.set()
        latched_at = self._latched_at = time.time()
        done, landed, decided = threading.Event(), [], threading.Event()
        started = time.monotonic()

        def _stop():
            try:
                landed.append(bool(self._halt_hardware()))
            except Exception as exc:
                landed.append(False)
                events.debug("Stop Raised", repr(exc), source=self.NAME,
                             exception=exc)
                events.warn("Stop Raised", f"The stop on {self.NAME} raised an "
                            "error. Treat it as live and check it by hand.",
                            source=self.NAME, exception=exc)
            finally:
                done.set()
            elapsed = (time.monotonic() - started) * 1000
            decided.wait(1.0)
            if self._stop_confirmed is not False or not landed[0]:
                return
            events.debug("Estop Late", f"the hardware stop landed after "
                         f"{elapsed:.1f} ms (budget "
                         f"{self.ESTOP_BUDGET * 1000:.0f} ms)", source=self.NAME)
            if self._estop.is_set() and self._latched_at == latched_at:
                self._stop_confirmed = True
                if self._closing:
                    return
                events.info("Stop Landed Late", f"The stop on the {self.NAME} "
                            f"went out {elapsed:.0f} ms after the press, later "
                            f"than the {self.ESTOP_BUDGET * 1000:.0f} ms it is "
                            "given to confirm.", source=self.NAME)

        threading.Thread(target=_stop, daemon=True, name=f"estop-{self.NAME}").start()
        in_time = done.wait(self.ESTOP_BUDGET)
        confirmed = bool(in_time and landed and landed[0])
        self._stop_confirmed = confirmed
        decided.set()
        events.debug("Estop", f"latched; hardware stop "
                     f"{'confirmed' if confirmed else 'still in flight' if not in_time else 'reported failure'}"
                     f" after {(time.monotonic() - started) * 1000:.1f} ms "
                     f"(budget {self.ESTOP_BUDGET * 1000:.0f} ms)", source=self.NAME)
        return confirmed

    def clear_estop(self, confirmed=False):
        """Operator action only. Returns the model to refusable, not running."""
        if not confirmed:
            raise NeedsConfirm(
                f"Clear the stop on the {self.NAME}?\n\nClearing lets it "
                "accept commands again; nothing restarts by itself.",
                "clear_estop")
        self._estop.clear()
        self._stop_confirmed = None
        self._latched_at = None
        # The next unconfirmed stop is a new episode, never a repeat count.
        events.forget("Stop Not Confirmed")
        events.info("Stop Cleared", f"The stop on the {self.NAME} was cleared. "
                    "Nothing restarts until you start it.", source=self.NAME)

    def toggle_estop(self, confirmed=False):
        if self.is_estopped:
            return self.clear_estop(confirmed)   # raises NeedsConfirm("clear_estop")
        if not self.estop():
            events.error("Stop Not Confirmed", f"The {self.NAME} is stopped, "
                         "but its hardware did not acknowledge the stop "
                         f"within {self.ESTOP_BUDGET * 1000:.0f} ms. Treat it "
                         "as live; a stop that lands later is reported.",
                         source=self.NAME)

    @property
    def is_estopped(self):
        return self._estop.is_set()

    @property
    def stop_confirmed(self):
        """None while clear; True/False for the stop that is latched."""
        return self._stop_confirmed if self._estop.is_set() else None

    @property
    def latched_at(self):
        """Wall time (time.time()) the latch closed; None while clear."""
        return self._latched_at if self._estop.is_set() else None

    @property
    def gate_mode(self):
        """`latched` while the stop is set, so every control the latch would
        refuse is greyed out before it is pressed (F11); the model's own mode
        otherwise. `state["model_mode"]` keeps the underlying mode."""
        if self.is_estopped:
            return "latched"
        if self.is_faulted:
            return "fault"       # CON-9: any model's fault gates, from the base
        return self.mode_name

    def _guard(self, what="this"):
        """Raise Refused while latched. Pass `self._estop.is_set` as
        `abort_if` to the device write as well: the check that counts is the
        one inside the lock."""
        if self._estop.is_set():
            events.debug("Guard", f"{what} refused: latched", source=self.NAME)
            raise Refused(f"{self.NAME} is stopped. Clear the stop, then try "
                          "again.")
        for port in self._link_ports():
            if port.status in ("lost", "reconnecting"):
                events.debug("Guard", f"{what} refused: link {port.status}",
                             source=self.NAME)
                raise Refused(f"{self.NAME} lost its connection to {port.port} "
                              "and is reconnecting by itself. Wait until it is "
                              "back, then try again.")

    # -- fault -------------------------------------------------------------
    def _fault(self, reason):
        if not str(reason or "").strip():
            # An empty reason used to come up as a blank fault (F19).
            reason = (f"{self.NAME} reported a fault without a reason. Stop "
                      "it and check the device.")
        if reason != self._fault_reason:
            self._fault_reason = reason
            self._publish_later(lambda: events.error("Fault", reason,
                                                     source=self.NAME))

    def _publish_later(self, publish):
        """Publish an event now, or, in a model that holds a lock around
        hardware state, once that lock is released (L10, SF-4: a subscriber
        may block this thread on a UI thread). The base holds no such lock."""
        publish()

    def _clear_fault(self):
        self._fault_reason = ""

    @property
    def fault(self):
        return self._fault_reason

    @property
    def is_faulted(self):
        return bool(self._fault_reason)

    @property
    def is_active(self):
        """Moving, heating or recording right now."""
        return False

    @property
    def is_energized(self):
        """Holding hardware in a state an operator should undo before walking
        away: coils enabled, heater heating, a run recording. Wider than
        `is_active` (a probe in a mode but not moving is energized, not
        active). Tier N: what the Web view's close-tab warning keys on."""
        return self.is_active

    # -- state -------------------------------------------------------------
    def _touch(self):
        self._updated_at = time.monotonic()

    def _expects_heartbeat(self):
        """True when a background loop should be touching this model."""
        return True

    @property
    def state(self):
        snapshot = super().state
        snapshot.update({
            "is_estopped": self.is_estopped, "is_faulted": self.is_faulted,
            "stop_confirmed": self.stop_confirmed, "latched_at": self.latched_at,
            "fault": self.fault, "is_active": self.is_active,
            # Seconds since this model's own loop last reported alive; None
            # when it has no loop to be stale about (an idle recorder).
            "age": (round(time.monotonic() - self._updated_at, 2)
                    if self._expects_heartbeat() else None),
            "devices": {self._device_key(d): d.status for d in self.devices},
            # MOD-5 / CON-6: which of those are real hardware links, as the
            # device declares it, so no view matches a class name.
            "hardware_devices": [self._device_key(d) for d in self.devices
                                 if getattr(d, "is_hardware", False)],
        })
        root = getattr(self, "output_root", None)
        if root is not None:
            snapshot["output_root"] = str(root)   # a view checks downloads against it
        link = self._link_state()
        if link is not None:
            snapshot["link"] = link
        return snapshot

    @staticmethod
    def _device_key(device):
        """The device's name in `state["devices"]`: its class name, unless it
        declares a `state_key` (a model that owns several devices of one
        class, such as one serial link per axis, names each)."""
        return getattr(device, "state_key", None) or type(device).__name__

    #: How bad each link status is, for a model with several ports: the
    #: published status is the worst of them (one lost axis is a lost link).
    _LINK_SEVERITY = {"verified": 0, "simulated": 0, "unverified": 1,
                      "connecting": 2, "reconnecting": 3, "closed": 4, "lost": 5}

    def _link_state(self):
        """L3: `state["link"]` for a model that owns a SerialPort, else None.
        EXACTLY these keys (the views are coded against them): status,
        losses, reconnects, dropped, stalls, stalled, last_loss. With several
        ports: the worst status, summed counts, the latest loss."""
        ports = self._link_ports()
        if not ports:
            return None
        worst = max(ports, key=lambda p: self._LINK_SEVERITY.get(p.status, 5))
        losses = [p.last_loss for p in ports if p.last_loss]
        link = {"status": worst.status,
                "losses": sum(int(p.losses) for p in ports),
                "reconnects": sum(int(p.reconnects) for p in ports),
                "dropped": 0, "stalls": 0, "stalled": False,
                "last_loss": max(losses) if losses else None}
        link.update(self._link_stream_state())
        return link

    def _link_stream_state(self):
        """What this model's own reader knows about the stream: `dropped`,
        `stalls`, `stalled`. Nothing by default."""
        return {}

    def _safety_section(self):
        """Every model's schema ends with this. Inherited, so every model has
        a stop/clear toggle and the Controller's global stop is just these
        wired together."""
        return sch.section(
            "Safety",
            # F20: one vocabulary - the object is "Stop", its latched state
            # "Stopped", the action "Clear". The tooltip names the model, so
            # six per-model stops are not six controls called "Stop".
            # Tier 3 (E, 2026-09-25): the per-model stop is a small switch
            # under Diagnostics; the rail's disc is the stop an operator
            # reaches for. Fault and its reason live beside it. (A latched
            # model still announces itself: the readouts freeze and the rail
            # says so; this is where the control sits, not the only sign.)
            # O16 (round 8): one word per thing. The disc is "Stop"; this
            # switch is "Stop this model" so six of them are not six "Stop"s.
            sch.toggle("Stop", "is_estopped", "toggle_estop",
                       "Stopped", "Stop this model",
                       on_role="danger", off_role="danger",
                       tooltip=f"Stop the {self.NAME}",
                       tooltip_on=f"The {self.NAME} is stopped. Press to "
                                  "clear the stop."),
            sch.indicator("Fault", "is_faulted"),
            sch.readonly("Fault reason:", "fault", role="danger"),
            # Declared so the confirmation re-run of `clear_estop` passes the
            # allow-list; it renders nothing (the toggle is the control).
            {"type": "internal", "command": "clear_estop", "writable": False,
             "role": "neutral"},
            tier=3, disclosure="Diagnostics",
        )
