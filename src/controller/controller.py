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


class Controller:
    ESTOP_ALL_BUDGET = 1.0   # s, total, however many models

    def __init__(self):
        self._lock = threading.RLock()
        self._models, self._configs, self._locks = {}, {}, {}
        self._remembered = {}       # configs of removed models, for reopen()
        self._subscribers = []
        self._closed = False
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
        """Process exit. Runs once."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
        self._close_models()

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
        return {"models": {n: m.state for n, m in models.items()},
                "is_estopped": self.is_estopped, "is_active": self.is_active,
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
        """Window focus -> every gamepad gate (D-4: gate input, never stop)."""
        with self._lock:
            models = list(self._models.values())
        for model in models:
            gamepad = getattr(model, "gamepad", None)
            if gamepad is not None:
                gamepad.set_gate(is_focused)

    # -- stop --------------------------------------------------------------
    @property
    def is_estopped(self):
        with self._lock:
            return any(m.is_estopped for m in self._models.values())

    @property
    def is_active(self):
        with self._lock:
            return any(m.is_active for m in self._models.values())

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
            events.error("Stop Not Confirmed", "FULL STOP latched on every model, "
                         f"but these did not confirm within {self.ESTOP_ALL_BUDGET}s: "
                         f"{', '.join(unconfirmed)}", source="Controller")
        return results

    def clear_estop_all(self, confirmed=False):
        with self._lock:
            latched = {n: m for n, m in self._models.items() if m.is_estopped}
        if not latched:
            return Result(Result.OK)
        if not confirmed:
            return Result(Result.CONFIRM, command="clear_estop_all",
                          reason="Release the FULL STOP latch on: "
                                 f"{', '.join(sorted(latched))}?\n\nThis does not "
                                 "restart anything.")
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

        def _handler(signum, _frame):
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
