"""D-8 / D-8a web client-liveness gate for models that are not probes.

WEB-23. The owner ruled (D-8a, 2026-09-21) that D-8's gate - warn after N s
of web-client silence, FULL STOP after M s, only while the device is doing
something - covers the heater and the rotator, not the probes alone.
`BaseProbe` folds its gate into the interlock watchdog it already runs.
`TemperatureSystem` and `RotatorSystem` have no such watchdog, so this mixin
carries its own small one.

The rules are BaseProbe's, unchanged:

* **No gate until a web client checks in.** `last_client_seen_time` is None
  until the first `touch_client_liveness()`, and the watchdog thread is not
  even created before then - a Tk or PySide session, which never calls it,
  carries no thread and can never be stopped by it.
* **Only while active.** Each model says what "active" means for it in
  `_client_liveness_active()`; an idle device is never stopped by silence.
* **Latch first, then report.** The stop is `emergency_stop()`, which sets
  the FULL STOP latch before any I/O (safety-pattern.md rule 1).

One difference from the probe's gate: this one fires **once per silence**.
A probe's FULL STOP moves it out of the active modes, so its check goes quiet
by itself; a heater whose stop frame could not be written still reads as
heating, and without the flag it would re-stop and re-report every tick.
"""
import threading
import time


class ClientLivenessGate:
    #: Subclasses set both, and mark them PROVISIONAL until measured (D-8a).
    WEB_CLIENT_WARN_TIMEOUT = None
    WEB_CLIENT_STOP_TIMEOUT = None
    #: How often the watchdog looks. Not a safety threshold.
    CLIENT_LIVENESS_INTERVAL = 0.5

    def _init_client_liveness(self):
        self.last_client_seen_time = None
        self._client_liveness_warned = False
        self._client_liveness_fired = False
        self._client_liveness_halt = threading.Event()
        self._client_liveness_thread = None
        self._client_liveness_start = threading.Lock()

    # -- to be provided by the model -----------------------------------

    def _client_liveness_active(self):
        """Return a short description of what the device is doing, or None
        when it is idle and silence must not stop it."""
        raise NotImplementedError

    # -- the seam (WebModelAdapter.CLIENT_HEARTBEAT_HOOK) ---------------

    def touch_client_liveness(self):
        """Record that a web client is still watching, and arm the gate.

        Same seam as `BaseProbe.touch_client_liveness`: no arguments, "now"
        is read here so a slow request cannot backdate the deadline.
        """
        self.last_client_seen_time = time.time()
        self._client_liveness_warned = False
        self._client_liveness_fired = False
        self._ensure_client_liveness_watchdog()

    def stop_client_liveness_watchdog(self):
        """End the watchdog for good. Does not wait for it."""
        self._client_liveness_halt.set()

    # -- the gate -------------------------------------------------------

    def _ensure_client_liveness_watchdog(self):
        with self._client_liveness_start:
            if self._client_liveness_halt.is_set():
                return  # torn down: a late heartbeat must not resurrect it
            t = self._client_liveness_thread
            if t is not None and t.is_alive():
                return
            self._client_liveness_thread = threading.Thread(
                target=self._client_liveness_loop, daemon=True,
                name=f"client-liveness-{self.__class__.__name__}")
            self._client_liveness_thread.start()

    def _client_liveness_loop(self):
        while not self._client_liveness_halt.wait(self.CLIENT_LIVENESS_INTERVAL):
            try:
                self._check_client_liveness()
            except Exception as e:
                print(f"[{self.__class__.__name__}] client-liveness check "
                      f"failed: {e}")

    def _check_client_liveness(self):
        seen = self.last_client_seen_time
        if seen is None or self._client_liveness_fired:
            return
        activity = self._client_liveness_active()
        if not activity:
            return
        silence = time.time() - seen
        if silence > self.WEB_CLIENT_STOP_TIMEOUT:
            self._client_liveness_fired = True
            self.emergency_stop()
            msg = (f"No web client has polled in {silence:.1f}s while "
                   f"{activity}; FULL STOP (D-8a).")
            print(f"[{self.__class__.__name__}] {msg}")
            from error_routing import ErrorRouter
            ErrorRouter.report_error("Client Liveness FULL STOP", msg, None)
        elif (silence > self.WEB_CLIENT_WARN_TIMEOUT
                and not self._client_liveness_warned):
            self._client_liveness_warned = True
            msg = (f"No web client has polled in {silence:.1f}s while "
                   f"{activity}.")
            print(f"[{self.__class__.__name__}] {msg}")
            from error_routing import ErrorRouter
            ErrorRouter.report_warning("Client Liveness Warning", msg)
