from lib import smc100
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk

class SMC100GUI:

    def __init__(self, root, port):
        self.root = root
        self.root.title("Newport SMC100 Controller")
        self.root.geometry("420x520")
        self.root.resizable(False, False)

        self.smc = None
        self.is_connected = False

        self._build_ui(port)
        self._poll_status()

        self.toggle_connection()

    def _build_ui(self, port):
        # Frame: Connection
        conn_frame = ttk.LabelFrame(self.root, text=" Connection ", padding=10)
        conn_frame.pack(fill="x", padx=10, pady=5)

        ttk.Label(conn_frame, text="Port:").grid(
            row=0, column=0, sticky="w", padx=2, pady=2
        )
        self.port_entry = ttk.Entry(conn_frame, width=15)
        self.port_entry.insert(0, f"{port}")
        self.port_entry.grid(row=0, column=1, padx=5, pady=2)

        ttk.Label(conn_frame, text="ID:").grid(
            row=0, column=2, sticky="w", padx=2, pady=2
        )
        self.id_entry = ttk.Entry(conn_frame, width=5)
        self.id_entry.insert(0, "1")
        self.id_entry.grid(row=0, column=3, padx=5, pady=2)

        self.btn_connect = ttk.Button(
            conn_frame, text="Connect", command=self.toggle_connection
        )
        self.btn_connect.grid(row=0, column=4, padx=5, pady=2)

        # Frame: Status Display
        status_frame = ttk.LabelFrame(
            self.root, text=" Device Status ", padding=10
        )
        status_frame.pack(fill="x", padx=10, pady=5)

        ttk.Label(
            status_frame, text="Position:", font=("Helvetica", 10, "bold")
        ).grid(row=0, column=0, sticky="w")
        self.lbl_position = ttk.Label(
            status_frame,
            text="--.-- mm",
            font=("Helvetica", 12, "bold"),
            foreground="blue",
        )
        self.lbl_position.grid(row=0, column=1, sticky="w", padx=10)

        ttk.Label(status_frame, text="State Code:").grid(
            row=1, column=0, sticky="w", pady=(5, 0)
        )
        self.lbl_state = ttk.Label(status_frame, text="Disconnected")
        self.lbl_state.grid(row=1, column=1, sticky="w", padx=10, pady=(5, 0))

        ttk.Label(status_frame, text="Error Code:").grid(
            row=2, column=0, sticky="w"
        )
        self.lbl_error = ttk.Label(status_frame, text="0")
        self.lbl_error.grid(row=2, column=1, sticky="w", padx=10)

        # Frame: System Actions
        actions_frame = ttk.LabelFrame(
            self.root, text=" Commands ", padding=10
        )
        actions_frame.pack(fill="x", padx=10, pady=5)

        self.btn_home = ttk.Button(
            actions_frame, text="Home Stage", command=self.cmd_home
        )
        self.btn_home.grid(row=0, column=0, padx=5, pady=5)

        self.btn_stop = ttk.Button(
            actions_frame, text="STOP", command=self.cmd_stop
        )
        self.btn_stop.grid(row=0, column=1, padx=5, pady=5)

        self.btn_reset = ttk.Button(
            actions_frame, text="Reset & Config", command=self.cmd_reset_config
        )
        self.btn_reset.grid(row=0, column=2, padx=5, pady=5)

        # Frame: Absolute Motion
        abs_frame = ttk.LabelFrame(
            self.root, text=" Absolute Motion ", padding=10
        )
        abs_frame.pack(fill="x", padx=10, pady=5)

        ttk.Label(abs_frame, text="Target (mm):").grid(
            row=0, column=0, sticky="w"
        )
        self.abs_entry = ttk.Entry(abs_frame, width=12)
        self.abs_entry.insert(0, "0.0")
        self.abs_entry.grid(row=0, column=1, padx=5)

        self.btn_abs_move = ttk.Button(
            abs_frame, text="Move Absolute", command=self.cmd_move_absolute
        )
        self.btn_abs_move.grid(row=0, column=2, padx=5)

        # Frame: Relative Motion
        rel_frame = ttk.LabelFrame(
            self.root, text=" Relative Motion ", padding=10
        )
        rel_frame.pack(fill="x", padx=10, pady=5)

        ttk.Label(rel_frame, text="Step (mm):").grid(row=0, column=0, sticky="w")
        self.rel_entry = ttk.Entry(rel_frame, width=12)
        self.rel_entry.insert(0, "1.0")
        self.rel_entry.grid(row=0, column=1, padx=5)

        self.btn_rel_neg = ttk.Button(
            rel_frame, text="Move -", command=lambda: self.cmd_move_relative(-1)
        )
        self.btn_rel_neg.grid(row=0, column=2, padx=2)

        self.btn_rel_pos = ttk.Button(
            rel_frame, text="Move +", command=lambda: self.cmd_move_relative(1)
        )
        self.btn_rel_pos.grid(row=0, column=3, padx=2)

        self._set_controls_state("disabled")

    def toggle_connection(self):
        if not self.is_connected:
            port = self.port_entry.get().strip()
            try:
                smc_id = int(self.id_entry.get().strip())
                self.smc = smc100.SMC100(
                    smcID=smc_id,
                    port=port,
                    silent=True,
                    sleepfunc=time.sleep,
                )
                self.is_connected = True
                self.btn_connect.config(text="Disconnect")
                self.port_entry.config(state="disabled")
                self.id_entry.config(state="disabled")
                self._set_controls_state("normal")
            except Exception as e:
                messagebox.showerror(
                    "Connection Error", f"Failed to connect:\n{e}"
                )
        else:
            self._disconnect()

    def _disconnect(self):
        if self.smc:
            try:
                self.smc.close()
            except Exception:
                pass
        self.smc = None
        self.is_connected = False
        self.btn_connect.config(text="Connect")
        self.port_entry.config(state="normal")
        self.id_entry.config(state="normal")
        self.lbl_position.config(text="--.-- mm")
        self.lbl_state.config(text="Disconnected")
        self.lbl_error.config(text="0")
        self._set_controls_state("disabled")

    def _set_controls_state(self, state):
        self.btn_home.config(state=state)
        self.btn_stop.config(state=state)
        self.btn_reset.config(state=state)
        self.btn_abs_move.config(state=state)
        self.btn_rel_neg.config(state=state)
        self.btn_rel_pos.config(state=state)

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
            self.root.after(
                0,
                lambda: messagebox.showerror(
                    "Controller Error", f"Action failed:\n{e}"
                ),
            )

    # Command Callbacks
    def cmd_home(self):
        self.run_async(self.smc.home)

    def cmd_stop(self):
        # Stop is executed directly without thread to guarantee immediate response
        try:
            self.smc.stop()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to send stop: {e}")

    def cmd_reset_config(self):
        self.run_async(self.smc.reset_and_configure)

    def cmd_move_absolute(self):
        try:
            target_mm = float(self.abs_entry.get().strip())
            self.run_async(self.smc.move_absolute_mm, target_mm)
        except ValueError:
            messagebox.showwarning(
                "Invalid Input", "Please enter a valid numeric value for target."
            )

    def cmd_move_relative(self, direction):
        try:
            step_mm = float(self.rel_entry.get().strip()) * direction
            self.run_async(self.smc.move_relative_mm, step_mm)
        except ValueError:
            messagebox.showwarning(
                "Invalid Input", "Please enter a valid numeric value for step."
            )

    # Status Poller
    def _poll_status(self):
        if self.is_connected and self.smc:
            try:
                pos = self.smc.get_position_mm()
                err, state = self.smc.get_status(silent=True)

                self.lbl_position.config(text=f"{pos:.4f} mm")
                self.lbl_state.config(text=state)
                self.lbl_error.config(text=str(err))
            except Exception:
                # Handle occasional read timeouts or drops gracefully
                pass

        # Re-trigger poll loop every 500 ms
        self.root.after(500, self._poll_status)

def main(port):
    root = tk.Tk()
    app = SMC100GUI(root, port)
    root.mainloop()

if __name__ == "__main__":
    root = tk.Tk()
    app = SMC100GUI(root, "COM1")
    root.mainloop()