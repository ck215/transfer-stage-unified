import time
import threading

class SystemManager:
    """Maintains background lifecycle of all hardware models."""
    def __init__(self):
        self.active_models = {}  # device_name -> model instance
        self.lock = threading.Lock()

    def register_model(self, name, model):
        with self.lock:
            self.active_models[name] = model

    def get_model(self, name):
        with self.lock:
            return self.active_models.get(name)

    def _teardown_model(self, name, model):
        """Runs hardware teardown for a single model. Not called under self.lock:
        it does blocking serial I/O and must not stall other device access."""
        if hasattr(model, 'poller') and model.poller:
            model.poller.stop_polling()
            model.poller.close()
        if hasattr(model, 'disconnect'):
            model.disconnect()
        if hasattr(model, 'stop'):
            model.stop()
        # power_down (de-energizes coils) is a strict superset of disable
        # (BaseProbe.power_down calls the same stop-and-disarm plus a coil-kill
        # command) so it must be preferred, not shadowed by disable.
        if hasattr(model, 'power_down'):
            model.power_down()
        elif hasattr(model, 'disable'):
            model.disable()
        if hasattr(model, 'serial_conn') and model.serial_conn:
            model.serial_conn.close()
        elif hasattr(model, 'serial_comm') and model.serial_comm:
            model.serial_comm.close()

    def remove_model(self, name):
        """Thread-safely detaches and returns a model, or None if absent."""
        with self.lock:
            return self.active_models.pop(name, None)

    def get_active_models_snapshot(self):
        """Thread-safe shallow copy of active_models for iteration/reads."""
        with self.lock:
            return dict(self.active_models)

    def reboot_model(self, name, constructor, *args, **kwargs):
        """Safely tears down and reconstructs a model."""
        old_model = self.remove_model(name)
        if old_model:
            try:
                self._teardown_model(name, old_model)
            except Exception as e:
                from error_routing import ErrorRouter as ErrorPopupManager
                ErrorPopupManager.report_error("Model Teardown Error", f"Error tearing down old model {name}:\n{e}", e)

        print(f"Rebooting {name}...")
        time.sleep(1) # simulate hardware reboot delay

        try:
            new_model = constructor(*args, **kwargs)
            with self.lock:
                self.active_models[name] = new_model
            return new_model
        except Exception as e:
            from error_routing import ErrorRouter
            msg = f"Failed to reboot {name}:\n{e}"
            print(msg)
            ErrorRouter.report_error("Reboot Failed", msg, e)
            return None

    def shutdown_all(self):
        with self.lock:
            models = list(self.active_models.items())
            self.active_models.clear()
        for name, model in models:
            try:
                self._teardown_model(name, model)
            except Exception:
                pass
