import threading
import time

from model.base import ManagedModel


class SystemManager:
    """The single authority over model lifetime (RC-1).

    Nothing outside this class writes to `active_models` (invariant I-1.5).
    Views ask for models and render them; they do not construct or destroy.
    """

    def __init__(self):
        self.active_models = {}  # device_name -> model instance
        self.configs = {}        # device_name -> the config it was built from
        self.lock = threading.Lock()

    # -- registration --------------------------------------------------

    def register(self, name, model, config=None):
        """Take ownership of `model` under `name`.

        Enforces the ManagedModel contract at the boundary rather than
        guessing method names later: the `hasattr` ladders this replaces
        meant every new model class had to be matched against a shifting set
        of duck-typed names, and a model that happened to miss one was
        silently skipped at shutdown.

        Raises on a duplicate name. Overwriting silently would drop a live
        model on the floor without ever tearing it down, which is exactly
        the leak I-1.1 forbids; callers that mean to replace a model call
        `release()` first, or go through `reconfigure()`.
        """
        if not isinstance(model, ManagedModel):
            raise TypeError(
                f"{name}: {type(model).__name__} does not implement ManagedModel "
                "(needs teardown() and emergency_stop())"
            )
        with self.lock:
            if name in self.active_models:
                raise ValueError(f"{name} is already registered; release it first")
            self.active_models[name] = model
            if config is not None:
                self.configs[name] = config
        return model

    def get_model(self, name):
        with self.lock:
            return self.active_models.get(name)

    def get_config(self, name):
        with self.lock:
            return self.configs.get(name)

    def get_active_models_snapshot(self):
        """Thread-safe shallow copy of active_models for iteration/reads."""
        with self.lock:
            return dict(self.active_models)

    # -- teardown ------------------------------------------------------

    def _stop_then_teardown(self, name, model):
        """Stop the hardware, then tear the model down. Never raises.

        The stop runs first and separately so that no model's teardown can
        skip it — a teardown that raises early still leaves hardware stopped.
        """
        try:
            model.emergency_stop()
        except Exception as e:
            self._report(f"Failed to stop {name} during shutdown: {e}", e, "Stop Error")
        try:
            model.teardown()
        except Exception as e:
            self._report(f"Failed to tear down {name}: {e}", e, "Shutdown Error")

    def release(self, name):
        """Detach `name` and tear it down. No-op if it is not registered.

        This is remove + teardown. `remove_model` used to do only the first
        half while the documentation claimed it did both, which is how ports
        were left open on every device close.

        Under D-1 (closing a tab or dock means *hide*), this is reached only
        by shutdown and reconfigure — never by a tab close.
        """
        with self.lock:
            model = self.active_models.pop(name, None)
            self.configs.pop(name, None)
        if model is not None:
            self._stop_then_teardown(name, model)
        return model

    def shutdown_all(self):
        """Stop and tear down every model. Idempotent."""
        with self.lock:
            models = list(self.active_models.items())
            self.active_models.clear()
            self.configs.clear()
        for name, model in models:
            self._stop_then_teardown(name, model)

    def reconfigure(self, builder):
        """Replace the whole set of models: tear down first, then build.

        `builder(manager)` registers the new models and is expected to roll
        back its own partial work if it raises. Tearing down *first* is the
        point: building before releasing meant two live handles on one port
        (I-1.4), which is what made Web re-setup fail intermittently.
        """
        self.shutdown_all()
        return builder(self)

    # -- emergency stop ------------------------------------------------

    # FULL STOP waits no longer than this in total, however many devices are
    # registered (RC-5 item 2).
    FULL_STOP_BUDGET = 1.0

    def full_stop_all(self):
        """Stop every model at once. Returns {name: ok} and never hangs.

        This used to stop models **one after another** on the caller's
        thread, so a device whose transport was wedged delayed the stop of
        every device behind it in the dict — with the order decided by
        registration, not by which axis is actually moving. Each model now
        gets its own thread and the whole fan-out shares one bounded join.

        A model reported as False is a model whose stop did not confirm in
        time. It is not a model that was skipped: its `emergency_stop` has
        already latched, and its hardware write is still in flight.
        """
        models = self.get_active_models_snapshot()
        if not models:
            return {}

        results = {}
        results_lock = threading.Lock()

        def _stop(name, model):
            ok = False
            try:
                model.emergency_stop()
                ok = True
            except Exception as e:
                self._report(f"Failed to stop {name}: {e}", e, "Stop Error")
            with results_lock:
                results[name] = ok

        threads = [
            threading.Thread(target=_stop, args=(name, model), daemon=True,
                             name=f"fullstop-{name}")
            for name, model in models.items()
        ]
        for th in threads:
            th.start()

        deadline = time.monotonic() + self.FULL_STOP_BUDGET
        for th in threads:
            th.join(timeout=max(0.0, deadline - time.monotonic()))

        with results_lock:
            for name in models:
                results.setdefault(name, False)
            unconfirmed = [n for n, ok in results.items() if not ok]
        if unconfirmed:
            self._report(
                "FULL STOP latched on every device, but these did not confirm "
                f"within {self.FULL_STOP_BUDGET}s: {', '.join(sorted(unconfirmed))}",
                None, "Stop Not Confirmed")
        return dict(results)

    @staticmethod
    def _report(message, exc, title):
        print(f"[SystemManager] {message}")
        try:
            from error_routing import ErrorRouter
            ErrorRouter.report_error(title, message, exc)
        except Exception:
            pass
