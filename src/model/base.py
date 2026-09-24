"""Base of every station model. Owns its Devices and the ONE estop latch.

A subclass writes `_halt_hardware()` (the strongest stop it has, on the
priority lane) and its own commands. It never re-implements the latch, the
bounded stop, or the close order.
"""
import threading
import time

import schema as sch
from events import events
from panel import Panel
from result import Refused, NeedsConfirm


class Model(Panel):
    IDENTITY = None          # handshake identity byte, or None
    NEEDS_PORT = False
    NEEDS_GAMEPAD = False
    ESTOP_BUDGET = 0.08      # s the caller waits for the hardware stop

    def __init__(self):
        super().__init__()
        self._estop = threading.Event()
        self._fault_reason = ""
        self._updated_at = time.monotonic()

    # -- devices and lifecycle --------------------------------------------
    @property
    def devices(self):
        """The Devices this model owns. Subclasses return their list."""
        return []

    def open(self):
        """Open every owned Device, then start this model's threads."""
        for device in self.devices:
            device.open()
        self._start_threads()

    def close(self):
        """Stop threads, halt, de-energize, close devices. Each step isolated:
        the hardware steps are never skipped because an earlier step raised."""
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
        pass

    def _stop_threads(self):
        pass

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
        wait at most ESTOP_BUDGET. True only if the stop landed in time."""
        self._estop.set()
        done, landed = threading.Event(), []

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

        started = time.monotonic()
        threading.Thread(target=_stop, daemon=True, name=f"estop-{self.NAME}").start()
        in_time = done.wait(self.ESTOP_BUDGET)
        confirmed = bool(in_time and landed and landed[0])
        events.debug("Estop", f"latched; hardware stop "
                     f"{'confirmed' if confirmed else 'still in flight' if not in_time else 'reported failure'}"
                     f" after {(time.monotonic() - started) * 1000:.1f} ms", source=self.NAME)
        return confirmed

    def clear_estop(self, confirmed=False):
        """Operator action only. Returns the model to refusable, not running."""
        if not confirmed:
            raise NeedsConfirm(
                f"Release the FULL STOP latch on {self.NAME}?\n\nCheck that the "
                "cause has been dealt with. This does not restart anything.",
                "clear_estop")
        self._estop.clear()
        events.info("FULL STOP Cleared", "latch released by operator", source=self.NAME)

    def toggle_estop(self, confirmed=False):
        if self.is_estopped:
            return self.clear_estop(confirmed)   # raises NeedsConfirm("clear_estop")
        if not self.estop():
            events.error("Stop Not Confirmed", f"{self.NAME} latched, but its "
                         "hardware stop did not confirm. Treat it as live.",
                         source=self.NAME)

    @property
    def is_estopped(self):
        return self._estop.is_set()

    @property
    def gate_mode(self):
        """`latched` while the stop is set, so every control the latch would
        refuse is greyed out before it is pressed (F11); the model's own mode
        otherwise. `state["model_mode"]` keeps the underlying mode."""
        return "latched" if self.is_estopped else self.mode_name

    def _guard(self, what="this"):
        """Raise Refused while latched. Pass `self._estop.is_set` as
        `abort_if` to the device write as well: the check that counts is the
        one inside the lock."""
        if self._estop.is_set():
            events.debug("Guard", f"{what} refused: latched", source=self.NAME)
            raise Refused(f"{self.NAME} is stopped. Clear the stop, then try "
                          "again.")

    # -- fault -------------------------------------------------------------
    def _fault(self, reason):
        if not str(reason or "").strip():
            # An empty reason used to come up as a blank fault (F19).
            reason = (f"{self.NAME} reported a fault without a reason. Stop "
                      "it and check the device.")
        if reason != self._fault_reason:
            self._fault_reason = reason
            events.error("Fault", reason, source=self.NAME)

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
            "fault": self.fault, "is_active": self.is_active,
            # Seconds since this model's own loop last reported alive; None
            # when it has no loop to be stale about (an idle recorder).
            "age": (round(time.monotonic() - self._updated_at, 2)
                    if self._expects_heartbeat() else None),
            "devices": {type(d).__name__: d.status for d in self.devices},
        })
        root = getattr(self, "output_root", None)
        if root is not None:
            snapshot["output_root"] = str(root)   # a view checks downloads against it
        return snapshot

    def _safety_section(self):
        """Every model's schema ends with this. Inherited, so every model has
        a stop/clear toggle and the Controller's global stop is just these
        wired together."""
        return sch.section(
            "Safety",
            sch.toggle("FULL STOP", "is_estopped", "toggle_estop",
                       "LATCHED - click to clear", "FULL STOP",
                       on_role="danger", off_role="danger"),
            sch.indicator("Fault", "is_faulted"),
            sch.readonly("Fault reason:", "fault", role="danger"),
            # Declared so the confirmation re-run of `clear_estop` passes the
            # allow-list; it renders nothing (the toggle is the control).
            {"type": "internal", "command": "clear_estop", "writable": False,
             "role": "neutral"},
        )
