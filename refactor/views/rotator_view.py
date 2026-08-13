import tkinter as tk
from tkinter import ttk
from models.rotator_model import RotatorModel

class RotatorView:
    def __init__(self, root: tk.Tk, model: RotatorModel, controller):
        self.root = root
        self.model = model
        self.controller = controller

        self.root.title("Newport SMC100 Controller")
        self.root.geometry("420x520")
        self.root.resizable(False, False)
        
        self._build_ui()
        
    def _build_ui(self):
        # Frame: Connection
        conn_frame = ttk.LabelFrame(self.root, text=" Connection ", padding=10)
        conn_frame.pack(fill="x", padx=10, pady=5)

        ttk.Label(conn_frame, text="Port:").grid(row=0, column=0, sticky="w", padx=2, pady=2)
        self.port_entry = ttk.Entry(conn_frame, width=15, textvariable=self.model.port)
        self.port_entry.grid(row=0, column=1, padx=5, pady=2)

        ttk.Label(conn_frame, text="ID:").grid(row=0, column=2, sticky="w", padx=2, pady=2)
        self.id_entry = ttk.Entry(conn_frame, width=5, textvariable=self.model.smc_id)
        self.id_entry.grid(row=0, column=3, padx=5, pady=2)

        self.btn_connect = ttk.Button(conn_frame, text="Connect", command=self.controller.toggle_connection)
        self.btn_connect.grid(row=0, column=4, padx=5, pady=2)

        # Frame: Status Display
        status_frame = ttk.LabelFrame(self.root, text=" Device Status ", padding=10)
        status_frame.pack(fill="x", padx=10, pady=5)

        ttk.Label(status_frame, text="Position:", font=("Helvetica", 10, "bold")).grid(row=0, column=0, sticky="w")
        self.lbl_position = ttk.Label(status_frame, textvariable=self.model.position, font=("Helvetica", 12, "bold"), foreground="blue")
        self.lbl_position.grid(row=0, column=1, sticky="w", padx=10)

        ttk.Label(status_frame, text="State Code:").grid(row=1, column=0, sticky="w", pady=(5, 0))
        self.lbl_state = ttk.Label(status_frame, textvariable=self.model.state)
        self.lbl_state.grid(row=1, column=1, sticky="w", padx=10, pady=(5, 0))

        ttk.Label(status_frame, text="Error Code:").grid(row=2, column=0, sticky="w")
        self.lbl_error = ttk.Label(status_frame, textvariable=self.model.error)
        self.lbl_error.grid(row=2, column=1, sticky="w", padx=10)

        # Frame: System Actions
        actions_frame = ttk.LabelFrame(self.root, text=" Commands ", padding=10)
        actions_frame.pack(fill="x", padx=10, pady=5)

        self.btn_home = ttk.Button(actions_frame, text="Home Stage", command=self.controller.cmd_home)
        self.btn_home.grid(row=0, column=0, padx=5, pady=5)

        self.btn_stop = ttk.Button(actions_frame, text="STOP", command=self.controller.cmd_stop)
        self.btn_stop.grid(row=0, column=1, padx=5, pady=5)

        self.btn_reset = ttk.Button(actions_frame, text="Reset & Config", command=self.controller.cmd_reset_config)
        self.btn_reset.grid(row=0, column=2, padx=5, pady=5)

        # Frame: Absolute Motion
        abs_frame = ttk.LabelFrame(self.root, text=" Absolute Motion ", padding=10)
        abs_frame.pack(fill="x", padx=10, pady=5)

        ttk.Label(abs_frame, text="Target (deg):").grid(row=0, column=0, sticky="w")
        self.abs_entry = ttk.Entry(abs_frame, width=12, textvariable=self.model.target_abs)
        self.abs_entry.grid(row=0, column=1, padx=5)

        self.btn_abs_move = ttk.Button(abs_frame, text="Move Absolute", command=self.controller.cmd_move_absolute)
        self.btn_abs_move.grid(row=0, column=2, padx=5)

        # Frame: Relative Motion
        rel_frame = ttk.LabelFrame(self.root, text=" Relative Motion ", padding=10)
        rel_frame.pack(fill="x", padx=10, pady=5)

        ttk.Label(rel_frame, text="Step (deg):").grid(row=0, column=0, sticky="w")
        self.rel_entry = ttk.Entry(rel_frame, width=12, textvariable=self.model.step_rel)
        self.rel_entry.grid(row=0, column=1, padx=5)

        self.btn_rel_neg = ttk.Button(rel_frame, text="Move -", command=lambda: self.controller.cmd_move_relative(-1))
        self.btn_rel_neg.grid(row=0, column=2, padx=2)

        self.btn_rel_pos = ttk.Button(rel_frame, text="Move +", command=lambda: self.controller.cmd_move_relative(1))
        self.btn_rel_pos.grid(row=0, column=3, padx=2)

        self.set_controls_state("disabled")

    def set_controls_state(self, state):
        self.btn_home.config(state=state)
        self.btn_stop.config(state=state)
        self.btn_reset.config(state=state)
        self.btn_abs_move.config(state=state)
        self.btn_rel_neg.config(state=state)
        self.btn_rel_pos.config(state=state)
