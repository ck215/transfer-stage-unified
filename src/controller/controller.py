"""Owns every Model. The only object a view talks to.

    view -> Controller.schema(name) / state(name) / run(name, command, inputs)
    Controller -> Model.run -> command -> Device

Closing a model's tab destructs it (`remove`); reopening constructs it again
from the remembered config (`reopen`). There is no hidden state.
"""
import atexit
import signal
import threading

from events import events
from result import Result


def _and(names):
    """'A', 'A and B', 'A, B and C' - station order, never sorted."""
    names = list(names)
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


class Controller:
    ESTOP_ALL_BUDGET = 1.0   # s, total, however many models
    #: s a second `close()` waits for the first. Above the heater's worst
    #: read-back (3 x 1.5 s) plus every other model's bounded teardown.
    CLOSE_WAIT = 30.0

    def __init__(self):
        self._lock = threading.RLock()
        self._models, self._configs, self._locks = {}, {}, {}
        self._remembered = {}       # configs of removed models, for reopen()
        self._subscribers = []
        self._closed = False
        self._closing_thread = None
        self._close_done = threading.Event()
        #: A signal that landed on the thread already inside `close()`;
        #: re-raised once that close has finished.
        self._deferred_signal = None
        self._hooked = False
        self.factory = None         # set by Setup: config -> Model

    # -- construct / destruct on demand -----------------------------------
    def add(self, name, model, config=None):
        """Register and open. A model that fails to open is closed and not kept."""
        with self._lock:
            if name in self._models:
                raise ValueError(f"{name} is already open")
        try:
            model.open()
        except Exception:
            model.close()
            raise
        with self._lock:
            others = dict(self._models)
            self._models[name] = model
            self._configs[name] = dict(config or {})
            self._locks[name] = threading.Lock()
            self._remembered.pop(name, None)
        for other_name, other in others.items():
            other.on_model_added(name, model)
            model.on_model_added(other_name, other)
        self._notify("added", name)
        return model

    def remove(self, name):
        """Estop, close, drop. The config is remembered so reopen() works."""
        with self._lock:
            model = self._models.pop(name, None)
            if model is None:
                return False
            self._remembered[name] = self._configs.pop(name, {})
            self._locks.pop(name, None)
            others = list(self._models.values())
        for other in others:
            other.on_model_removed(name, model)
        self._notify("removed", name)
        model.estop()
        model.close()
        return True

    def reopen(self, name):
        with self._lock:
            config = self._remembered.get(name)
        if config is None or self.factory is None:
            raise ValueError(f"{name} was never configured")
        return self.add(name, self.factory(config), config)

    def reset(self):
        """Destruct everything. Setup calls this BEFORE building again, so one
        port never has two handles."""
        self._close_models()
        with self._lock:
            self._remembered.clear()

    def close(self):
        """Process exit. Runs once.

        A second call - SIGTERM or atexit while Quit is closing on another
        thread - WAITS for the first to finish (bounded by CLOSE_WAIT)
        instead of returning at once: the signal handler re-raises after
        `close()` returns, and that used to end the process with a model's
        teardown (the heater's off and its read-back) half done. A call from
        the thread already closing (a signal handler that interrupted it)
        returns at once; there is nothing to wait for that it is not doing.
        """
        with self._lock:
            if self._closed:
                closing_thread = self._closing_thread
                done = self._close_done
            else:
                closing_thread = done = None
                self._closed = True
                self._closing_thread = threading.get_ident()
        if done is not None:
            if closing_thread != threading.get_ident():
                if not done.wait(self.CLOSE_WAIT):
                    events.debug("Close Wait Expired", f"the first close is still "
                                 f"running after {self.CLOSE_WAIT:g} s",
                                 source="Controller")
            return
        try:
            self._close_models()
        finally:
            self._close_done.set()
        deferred, self._deferred_signal = self._deferred_signal, None
        if deferred is not None:
            events.debug("Signal Re-raised", f"signal {deferred} arrived during "
                         "this close; re-raised now that it is done",
                         source="Controller")
            signal.signal(deferred, signal.SIG_DFL)
            signal.raise_signal(deferred)

    def _close_models(self):
        with self._lock:
            models = dict(self._models)
            self._models.clear()
            self._configs.clear()
            self._locks.clear()
        if not models:
            return
        unconfirmed = sorted(n for n, ok in self._estop_concurrently(models).items() if not ok)
        if unconfirmed:
            # never a popup from inside a close path: it would block the exit
            events.error("Stop Not Confirmed", "Shutdown could not confirm the stop "
                         f"of: {', '.join(unconfirmed)}. Treat them as live.",
                         source="Controller", ack=False)
        for name, model in models.items():
            try:
                model.close()
            except Exception as exc:
                events.warn("Close Failed", f"{name}: {exc}", source="Controller",
                            exception=exc)
            self._notify("removed", name)

    # -- what views call ---------------------------------------------------
    @property
    def model_names(self):
        with self._lock:
            return list(self._models)

    @property
    def models(self):
        """A snapshot `{name: model}` of what is open, for the composition
        root (Setup applies a signed-in profile to every open model)."""
        with self._lock:
            return dict(self._models)

    @property
    def closed_names(self):
        """Configured models whose tab is closed; reopen() brings one back."""
        with self._lock:
            return list(self._remembered)

    def config(self, name):
        with self._lock:
            return dict(self._configs.get(name) or self._remembered.get(name) or {})

    def schema(self, name):
        """A closed model has no schema; the view removes its panel on the
        'removed' event, and until then it renders nothing."""
        model = self._model_or_none(name)
        return model.schema if model else {"version": 2, "sections": []}

    def state(self, name=None):
        if name is not None:
            model = self._model_or_none(name)
            return model.state if model else {"name": name, "mode": "closed",
                                              "values": {}, "closed": True}
        with self._lock:
            models = dict(self._models)
        states = {}
        for n, m in models.items():
            state = m.state
            # A hosted model is drawn on its host's page while the host is
            # launched (Red Percent on the Transfer Map); alone, it is its
            # own page. The views read `host`, never a class.
            host = getattr(m, "HOST", None)
            state["host"] = host if host in models and host != n else None
            states[n] = state
        return {"models": states,
                "is_estopped": self.is_estopped, "is_active": self.is_active,
                "energized": [n for n, m in models.items()
                              if getattr(m, "is_energized", False)],
                "stop": self._stop_state(models),
                "closed": self.closed_names, "latest_event": events.latest_id}

    def run(self, name, command, inputs=None, args=()):
        """The only way a command reaches a model. Serialised per model, so a
        second view or tab cannot interleave two commands on one device."""
        try:
            model, lock = self._model(name), self._lock_for(name)
        except KeyError:
            return Result(Result.REFUSED, reason=f"{name} is not open")
        if command in ("toggle_estop", "estop"):   # a stop never queues
            return model.run(command, inputs, args)
        with lock:
            return model.run(command, inputs, args)

    def options(self, name, command):
        model = self._model_or_none(name)
        return model.options(command) if model else []

    def set_value(self, name, attr, value):
        return self.run(name, "_commit", inputs={attr: value})

    def set_input_focus(self, is_focused):
        """Window focus -> every manual-input gate (D-4: gate input, never
        stop). The contract is `Device.set_gate(bool)` on any of a model's
        `devices`, whatever the attribute is called (CON-3); the `gamepad`
        attribute is still reached for a model that keeps it off the list."""
        with self._lock:
            models = list(self._models.values())
        for model in models:
            seen = set()
            for device in list(getattr(model, "devices", None) or ()):
                gate = getattr(device, "set_gate", None)
                if callable(gate):
                    seen.add(id(device))
                    gate(is_focused)
            gamepad = getattr(model, "gamepad", None)
            if gamepad is not None and id(gamepad) not in seen:
                gamepad.set_gate(is_focused)

    # -- stop --------------------------------------------------------------
    @property
    def is_estopped(self):
        """Any model latched. The watchdog and the close path key on this;
        what a view SAYS comes from `stop_state` (round 7, IMP7-1)."""
        with self._lock:
            return any(m.is_estopped for m in self._models.values())

    @property
    def stop_state(self):
        """The three facts a view needs before it says anything about the
        stop: `latched` (names, station order), `unconfirmed` (latched
        models whose hardware did not confirm), and `every` (every open
        model is latched; False for an empty station). One model's own
        switch is a partial stop, never \"every model is stopped\"."""
        with self._lock:
            models = dict(self._models)
        return self._stop_state(models)

    @staticmethod
    def _stop_state(models):
        latched = [n for n, m in models.items() if m.is_estopped]
        unconfirmed = [n for n in latched
                       if getattr(models[n], "stop_confirmed", True) is False]
        since = [getattr(models[n], "latched_at", None) for n in latched]
        since = [t for t in since if t is not None]
        return {"latched": latched, "unconfirmed": unconfirmed,
                "every": bool(latched) and len(latched) == len(models),
                # Wall time the newest latch closed (round 7, Web CCR 1): a
                # view keeps only events newer than this across a reload.
                "since": max(since) if since else None}

    @property
    def is_active(self):
        with self._lock:
            return any(m.is_active for m in self._models.values())

    @property
    def is_energized(self):
        """Any model holding hardware an operator should undo before leaving
        (wider than `is_active`: a probe in a mode but not moving counts).
        The Web watchdog keys on this (round 8, IMP8-7)."""
        with self._lock:
            return any(getattr(m, "is_energized", False) for m in self._models.values())

    def estop_all(self):
        """Every model's estop, wired together. {name: confirmed}. Never hangs."""
        with self._lock:
            models = dict(self._models)
        results = self._estop_concurrently(models)
        unconfirmed = sorted(n for n, ok in results.items() if not ok)
        confirmed = sorted(n for n, ok in results.items() if ok)
        if confirmed:
            events.info("FULL STOP", f"latched and confirmed on: {', '.join(confirmed)}",
                        source="Controller")
        if unconfirmed:
            # The title is what the views key on; the words are the operator's
            # (round 7, IMP7-4): sentence case, the model first, the action.
            events.error("Stop Not Confirmed",
                         f"{_and(unconfirmed)} did not confirm the stop within "
                         f"{self.ESTOP_ALL_BUDGET:g} s. Every model is latched; treat "
                         f"{'them' if len(unconfirmed) > 1 else 'it'} as live until you "
                         "have checked by hand.", source="Controller")
        return results

    def clear_estop_all(self, confirmed=False):
        with self._lock:
            latched = {n: m for n, m in self._models.items() if m.is_estopped}
        if not latched:
            return Result(Result.OK)
        if not confirmed:
            # Round 7 (IMP7-4, TK7-10): sentence case, station order, and the
            # model that never confirmed is named before the operator answers.
            names = list(latched)
            unconfirmed = [n for n in names
                           if getattr(latched[n], "stop_confirmed", True) is False]
            reason = f"Clear the stop on {', '.join(names)}?"
            if unconfirmed:
                reason += (f"\n\n{_and(unconfirmed)} did not confirm "
                           f"{'their' if len(unconfirmed) > 1 else 'its'} stop. "
                           f"Treat {'them' if len(unconfirmed) > 1 else 'it'} as live "
                           "until you have checked by hand.")
            reason += "\n\nNothing restarts by itself."
            return Result(Result.CONFIRM, command="clear_estop_all", reason=reason)
        for model in latched.values():
            model.clear_estop(confirmed=True)
        return Result(Result.OK)

    def _estop_concurrently(self, models):
        import time
        results, results_lock = {}, threading.Lock()

        def _stop(name, model):
            ok = False
            try:
                ok = model.estop() is True
            except Exception as exc:
                events.warn("Stop Raised", f"{name}: {exc}", source="Controller",
                            exception=exc)
            with results_lock:
                results[name] = ok

        threads = [threading.Thread(target=_stop, args=item, daemon=True,
                                    name=f"estop-all-{item[0]}") for item in models.items()]
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + self.ESTOP_ALL_BUDGET
        for thread in threads:
            thread.join(timeout=max(0.0, deadline - time.monotonic()))
        with results_lock:
            return {name: results.get(name, False) for name in models}

    # -- plumbing ----------------------------------------------------------
    def subscribe(self, fn):
        """fn(event, name) for 'added' / 'removed'."""
        with self._lock:
            if fn not in self._subscribers:
                self._subscribers.append(fn)

    def unsubscribe(self, fn):
        with self._lock:
            if fn in self._subscribers:
                self._subscribers.remove(fn)

    def _notify(self, event, name):
        with self._lock:
            subscribers = list(self._subscribers)
        for fn in subscribers:
            try:
                fn(event, name)
            except Exception as exc:
                events.warn("Subscriber Failed", str(exc), source="Controller",
                            exception=exc)

    def _model(self, name):
        with self._lock:
            return self._models[name]

    def _model_or_none(self, name):
        with self._lock:
            return self._models.get(name)

    def _lock_for(self, name):
        with self._lock:
            return self._locks[name]

    def _hook_exit(self):
        """atexit + SIGINT/SIGTERM/SIGHUP -> close(), once. Idempotent."""
        if self._hooked:
            return
        self._hooked = True
        atexit.register(self.close)
        self.hook_signals()

    def hook_signals(self):
        """(Re)install SIGINT/SIGTERM/SIGHUP -> close(). Safe to call again.

        A GUI toolkit may install its own C-level handler when its first
        window is created: Tk 9 on Aqua does for SIGTERM, and then a SIGTERM
        ended the process with exit 1, past `close()` and past atexit, with
        every model live and every port open (found by the packaging smoke
        test, 2026-09-25). Python's `getsignal()` still reported our handler,
        so nothing in-process could see it. `app.launch()` calls this again
        after the view is built.
        """
        def _handler(signum, _frame):
            if (self._closing_thread == threading.get_ident()
                    and not self._close_done.is_set()):
                # This thread is inside close() already (Quit, then the
                # terminal closed): re-raising now would end the process
                # mid-teardown. close() re-raises when it is done.
                self._deferred_signal = signum
                events.debug("Signal Deferred", f"signal {signum} arrived "
                             "inside close(); re-raised when it finishes",
                             source="Controller")
                return
            self.close()
            signal.signal(signum, signal.SIG_DFL)
            signal.raise_signal(signum)

        for sig_name in ("SIGINT", "SIGTERM", "SIGHUP"):
            sig = getattr(signal, sig_name, None)
            if sig is not None:
                try:
                    signal.signal(sig, _handler)
                except (ValueError, OSError):
                    pass   # not the main thread, or unsupported on this OS
