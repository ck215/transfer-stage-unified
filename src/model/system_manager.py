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

    def reboot_model(self, name, constructor, *args, **kwargs):
        """Safely tears down and reconstructs a model."""
        with self.lock:
            old_model = self.active_models.get(name)
            if old_model:
                try:
                    if hasattr(old_model, 'poller') and old_model.poller:
                        old_model.poller.stop_polling()
                        old_model.poller.close()
                    if hasattr(old_model, 'disconnect'):
                        old_model.disconnect()
                    if hasattr(old_model, 'stop'):
                        old_model.stop()
                    if hasattr(old_model, 'serial_conn') and old_model.serial_conn:
                        old_model.serial_conn.close()
                    elif hasattr(old_model, 'serial_comm') and old_model.serial_comm:
                        old_model.serial_comm.close()
                except Exception as e:
                    from error_routing import ErrorRouter as ErrorPopupManager
                    ErrorPopupManager.report_error("Model Teardown Error", f"Error tearing down old model {name}:\n{e}", e)

            print(f"Rebooting {name}...")
            time.sleep(1) # simulate hardware reboot delay
            
            try:
                new_model = constructor(*args, **kwargs)
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
            for name, model in self.active_models.items():
                try:
                    if hasattr(model, 'poller') and model.poller:
                        model.poller.stop_polling()
                        model.poller.close()
                    if hasattr(model, 'disconnect'):
                        model.disconnect()
                    if hasattr(model, 'stop'):
                        model.stop()
                    if hasattr(model, 'serial_conn') and model.serial_conn:
                        model.serial_conn.close()
                    elif hasattr(model, 'serial_comm') and model.serial_comm:
                        model.serial_comm.close()
                except Exception as e:
                    pass
            self.active_models.clear()
