import threading
import time
from tkinter import messagebox
from lib import smc100

class RotatorController:
    def __init__(self, model):
        self.model = model
        self.view = None
        self.root = None

    def set_view(self, view, root):
        self.view = view
        self.root = root
        
        self._poll_status()
        self.toggle_connection()

    def toggle_connection(self):
        if not self.model.is_connected:
            port = self.model.port.get().strip()
            try:
                smc_id = int(self.model.smc_id.get().strip())
                self.model.smc = smc100.SMC100(
                    smcID=smc_id,
                    port=port,
                    silent=True,
                    sleepfunc=time.sleep,
                )
                self.model.is_connected = True
                self.view.btn_connect.config(text="Disconnect")
                self.view.port_entry.config(state="disabled")
                self.view.id_entry.config(state="disabled")
                self.view.set_controls_state("normal")
            except Exception as e:
                messagebox.showerror(
                    "Connection Error", f"Failed to connect:\n{e}"
                )
        else:
            self._disconnect()

    def _disconnect(self):
        if self.model.smc:
            try:
                self.model.smc.close()
            except Exception:
                pass
        self.model.smc = None
        self.model.is_connected = False
        self.view.btn_connect.config(text="Connect")
        self.view.port_entry.config(state="normal")
        self.view.id_entry.config(state="normal")
        self.model.position.set("--.-- deg")
        self.model.state.set("Disconnected")
        self.model.error.set("0")
        self.view.set_controls_state("disabled")

    def run_async(self, func, *args):
        """Helper to run blocking SMC operations in a thread so Tkinter does not freeze."""
        thread = threading.Thread(
            target=self._async_wrapper, args=(func, args)
        )
        thread.daemon = True
        thread.start()

    def _async_wrapper(self, func, args):
        try:
            func(*args)
        except Exception as e:
            if self.root:
                self.root.after(
                    0,
                    lambda err=e: messagebox.showerror(
                        "Controller Error", f"Action failed:\n{err}"
                    ),
                )

    def cmd_home(self):
        if self.model.smc:
            self.run_async(self.model.smc.home)

    def cmd_stop(self):
        if self.model.smc:
            try:
                self.model.smc.stop()
            except Exception as e:
                messagebox.showerror("Error", f"Failed to send stop: {e}")

    def cmd_reset_config(self):
        if self.model.smc:
            self.run_async(self.model.smc.reset_and_configure)

    def cmd_move_absolute(self):
        if self.model.smc:
            try:
                target_deg = float(self.model.target_abs.get().strip())
                self.run_async(self.model.smc.move_absolute_deg, target_deg)
            except ValueError:
                messagebox.showwarning(
                    "Invalid Input", "Please enter a valid numeric value for target."
                )

    def cmd_move_relative(self, direction):
        if self.model.smc:
            try:
                step_deg = float(self.model.step_rel.get().strip()) * direction
                self.run_async(self.model.smc.move_relative_deg, step_deg)
            except ValueError:
                messagebox.showwarning(
                    "Invalid Input", "Please enter a valid numeric value for step."
                )

    def _poll_status(self):
        if self.model.is_connected and self.model.smc:
            try:
                pos = self.model.smc.get_position_deg()
                err, state = self.model.smc.get_status(silent=True)

                self.model.position.set(f"{pos:.4f} deg")
                self.model.state.set(state)
                self.model.error.set(str(err))
            except Exception:
                pass

        if self.root:
            self.root.after(500, self._poll_status)
