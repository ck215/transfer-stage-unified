import threading
import time
try:
    from lib import smc100
except ImportError:
    smc100 = None

class RotatorSystem:
    def __init__(self, default_port="COM1"):
        self.port = default_port
        self.smc_id = 1
        
        self._lock = threading.Lock()
        self._position = None
        self._state = "Disconnected"
        self._error = "0"
        
        self.smc = None
        self.is_connected = False
        self.error_callback = None
        
        self.custom_view_class = RotatorView
        
    @property
    def position(self):
        with self._lock: return self._position
        
    @position.setter
    def position(self, value):
        with self._lock: self._position = value

    @property
    def state(self):
        with self._lock: return self._state
        
    @state.setter
    def state(self, value):
        with self._lock: self._state = value

    @property
    def error(self):
        with self._lock: return self._error
        
    @error.setter
    def error(self, value):
        with self._lock: self._error = value

    def _run_async(self, func, *args):
        """Helper to run blocking operations in a thread."""
        thread = threading.Thread(target=self._async_wrapper, args=(func, args))
        thread.daemon = True
        thread.start()

    def _async_wrapper(self, func, args):
        try:
            func(*args)
        except Exception as e:
            if self.error_callback:
                self.error_callback(e)

    def connect(self, port: str, smc_id: int):
        if not self.is_connected:
            self.port = port
            self.smc_id = smc_id
            self.smc = smc100.SMC100(
                smcID=self.smc_id,
                port=self.port,
                silent=True,
                sleepfunc=time.sleep,
            )
            self.is_connected = True

    def disconnect(self):
        if self.smc:
            try:
                self.smc.close()
            except Exception:
                pass
        self.smc = None
        self.is_connected = False
        self.position = None
        self.state = "Disconnected"
        self.error = "0"

    def home(self):
        if self.smc:
            self._run_async(self.smc.home)

    def stop(self):
        if self.smc:
            self.smc.stop()

    def reset_and_configure(self):
        if self.smc:
            self._run_async(self.smc.reset_and_configure)

    def move_absolute(self, target_deg: float):
        if self.smc:
            self._run_async(self.smc.move_absolute_deg, target_deg)

    def move_relative(self, step_deg: float):
        if self.smc:
            self._run_async(self.smc.move_relative_deg, step_deg)

    def poll_status(self):
        if self.is_connected and self.smc:
            try:
                pos = self.smc.get_position_deg()
                err, state = self.smc.get_status(silent=True)
                
                self.position = pos
                self.state = state
                self.error = str(err)
            except Exception:
                pass
import tkinter as tk
from tkinter import ttk, messagebox

class RotatorView(tk.Frame):
    def __init__(self, master, system: RotatorSystem):
        super().__init__(master)
        self.system = system
        
        self.port_var = tk.StringVar(value=self.system.port)
        self.id_var = tk.StringVar(value=str(self.system.smc_id))
        
        self.position_var = tk.StringVar(value="--.-- deg")
        self.state_var = tk.StringVar(value=self.system.state)
        self.error_var = tk.StringVar(value=self.system.error)
        
        self.target_abs_var = tk.StringVar(value="0.0")
        self.step_rel_var = tk.StringVar(value="1.0")

        self.system.error_callback = self._on_system_error
        
        self._build_ui()
        self._poll_system()

    def _on_system_error(self, err):
        self.after(0, lambda: messagebox.showerror("System Error", f"Action failed:\n{err}", parent=self))

    def _build_ui(self):
        # Frame: Connection
        conn_frame = ttk.LabelFrame(self, text=" Connection ", padding=10)
        conn_frame.pack(fill="x", padx=10, pady=5)

        ttk.Label(conn_frame, text="Port:").grid(row=0, column=0, sticky="w", padx=2, pady=2)
        self.port_entry = ttk.Entry(conn_frame, width=15, textvariable=self.port_var)
        self.port_entry.grid(row=0, column=1, padx=5, pady=2)

        ttk.Label(conn_frame, text="ID:").grid(row=0, column=2, sticky="w", padx=2, pady=2)
        self.id_entry = ttk.Entry(conn_frame, width=5, textvariable=self.id_var)
        self.id_entry.grid(row=0, column=3, padx=5, pady=2)

        self.btn_connect = ttk.Button(conn_frame, text="Connect", command=self.toggle_connection)
        self.btn_connect.grid(row=0, column=4, padx=5, pady=2)

        # Frame: Status Display
        status_frame = ttk.LabelFrame(self, text=" Device Status ", padding=10)
        status_frame.pack(fill="x", padx=10, pady=5)

        ttk.Label(status_frame, text="Position:", font=("Helvetica", 10, "bold")).grid(row=0, column=0, sticky="w")
        self.lbl_position = ttk.Label(status_frame, textvariable=self.position_var, font=("Helvetica", 12, "bold"), foreground="blue")
        self.lbl_position.grid(row=0, column=1, sticky="w", padx=10)

        ttk.Label(status_frame, text="State Code:").grid(row=1, column=0, sticky="w", pady=(5, 0))
        self.lbl_state = ttk.Label(status_frame, textvariable=self.state_var)
        self.lbl_state.grid(row=1, column=1, sticky="w", padx=10, pady=(5, 0))

        ttk.Label(status_frame, text="Error Code:").grid(row=2, column=0, sticky="w")
        self.lbl_error = ttk.Label(status_frame, textvariable=self.error_var)
        self.lbl_error.grid(row=2, column=1, sticky="w", padx=10)

        # Frame: System Actions
        actions_frame = ttk.LabelFrame(self, text=" Commands ", padding=10)
        actions_frame.pack(fill="x", padx=10, pady=5)

        self.btn_home = ttk.Button(actions_frame, text="Home Stage", command=self.cmd_home)
        self.btn_home.grid(row=0, column=0, padx=5, pady=5)

        self.btn_stop = ttk.Button(actions_frame, text="STOP", command=self.cmd_stop)
        self.btn_stop.grid(row=0, column=1, padx=5, pady=5)

        self.btn_reset = ttk.Button(actions_frame, text="Reset & Config", command=self.cmd_reset_config)
        self.btn_reset.grid(row=0, column=2, padx=5, pady=5)

        # Frame: Absolute Motion
        abs_frame = ttk.LabelFrame(self, text=" Absolute Motion ", padding=10)
        abs_frame.pack(fill="x", padx=10, pady=5)

        ttk.Label(abs_frame, text="Target (deg):").grid(row=0, column=0, sticky="w")
        self.abs_entry = ttk.Entry(abs_frame, width=12, textvariable=self.target_abs_var)
        self.abs_entry.grid(row=0, column=1, padx=5)

        self.btn_abs_move = ttk.Button(abs_frame, text="Move Absolute", command=self.cmd_move_absolute)
        self.btn_abs_move.grid(row=0, column=2, padx=5)

        # Frame: Relative Motion
        rel_frame = ttk.LabelFrame(self, text=" Relative Motion ", padding=10)
        rel_frame.pack(fill="x", padx=10, pady=5)

        ttk.Label(rel_frame, text="Step (deg):").grid(row=0, column=0, sticky="w")
        self.rel_entry = ttk.Entry(rel_frame, width=12, textvariable=self.step_rel_var)
        self.rel_entry.grid(row=0, column=1, padx=5)

        self.btn_rel_neg = ttk.Button(rel_frame, text="Move -", command=lambda: self.cmd_move_relative(-1))
        self.btn_rel_neg.grid(row=0, column=2, padx=2)

        self.btn_rel_pos = ttk.Button(rel_frame, text="Move +", command=lambda: self.cmd_move_relative(1))
        self.btn_rel_pos.grid(row=0, column=3, padx=2)

        self.set_controls_state("disabled")

    def set_controls_state(self, state):
        self.btn_home.config(state=state)
        self.btn_stop.config(state=state)
        self.btn_reset.config(state=state)
        self.btn_abs_move.config(state=state)
        self.btn_rel_neg.config(state=state)
        self.btn_rel_pos.config(state=state)

    def toggle_connection(self):
        if not self.system.is_connected:
            port = self.port_var.get().strip()
            try:
                smc_id = int(self.id_var.get().strip())
                self.system.connect(port, smc_id)
                self.btn_connect.config(text="Disconnect")
                self.port_entry.config(state="disabled")
                self.id_entry.config(state="disabled")
                self.set_controls_state("normal")
            except Exception as e:
                messagebox.showerror("Connection Error", f"Failed to connect:\n{e}", parent=self)
        else:
            self.system.disconnect()
            self.btn_connect.config(text="Connect")
            self.port_entry.config(state="normal")
            self.id_entry.config(state="normal")
            self.position_var.set("--.-- deg")
            self.state_var.set("Disconnected")
            self.error_var.set("0")
            self.set_controls_state("disabled")

    def cmd_home(self):
        self.system.home()

    def cmd_stop(self):
        try:
            self.system.stop()
        except Exception as e:
            messagebox.showerror("Error", f"Failed to send stop: {e}", parent=self)

    def cmd_reset_config(self):
        self.system.reset_and_configure()

    def cmd_move_absolute(self):
        try:
            target = float(self.target_abs_var.get().strip())
            self.system.move_absolute(target)
        except ValueError:
            messagebox.showwarning("Invalid Input", "Please enter a valid numeric value for target.", parent=self)

    def cmd_move_relative(self, direction):
        try:
            step = float(self.step_rel_var.get().strip()) * direction
            self.system.move_relative(step)
        except ValueError:
            messagebox.showwarning("Invalid Input", "Please enter a valid numeric value for step.", parent=self)

    def _poll_system(self):
        self.system.poll_status()
        if self.system.is_connected:
            if self.system.position is not None:
                self.position_var.set(f"{self.system.position:.4f} deg")
            else:
                self.position_var.set("--.-- deg")
            self.state_var.set(str(self.system.state))
            self.error_var.set(str(self.system.error))
        
        # Schedule the next poll
        self.after(500, self._poll_system)
