import tkinter as tk
from tkinter import ttk, messagebox
import queue
import sys
import traceback

from error_routing import ErrorRouter

class ErrorPopupManager:
    """
    Centralized error handler for receiving errors from across the application
    and presenting them to the user via visual popups.
    """
    _root = None
    _error_queue = queue.Queue()
    _is_polling = False

    @classmethod
    def initialize(cls, root):
        """Initialize with the main Tk window to allow thread-safe popups."""
        cls._root = root
        ErrorRouter.set_callbacks(cls.report_error, cls.report_warning, cls.report_info)
        if not cls._is_polling:
            cls._poll_queue()
            cls._is_polling = True

    @classmethod
    def _poll_queue(cls):
        """Poll the error queue and display popups in the main thread."""
        if not cls._root:
            return
            
        while not cls._error_queue.empty():
            error_data = cls._error_queue.get()
            cls._display_popup(error_data)
            
        cls._root.after(100, cls._poll_queue)

    @classmethod
    def _display_popup(cls, error_data):
        """Actually display the messagebox."""
        title = error_data.get('title', 'Message')
        message = error_data.get('message', '')
        exception = error_data.get('exception')
        msg_type = error_data.get('type', 'error')
        
        full_message = message
        if exception:
            if not full_message: full_message = ""
            try:
                full_message += f"\n\nDetails:\n{type(exception).__name__}: {str(exception)}"
            except Exception:
                full_message += "\n\nDetails: <Unprintable Exception>"
        if full_message and len(full_message) > 5000:
            full_message = full_message[:5000] + "... [TRUNCATED]"
            
        if msg_type == 'error':
            messagebox.showerror(title, full_message, parent=cls._root)
        elif msg_type == 'warning':
            messagebox.showwarning(title, full_message, parent=cls._root)
        else:
            messagebox.showinfo(title, full_message, parent=cls._root)

    @classmethod
    def report_error(cls, title, message, exception=None):
        cls._queue_message('error', title, message, exception)

    @classmethod
    def report_warning(cls, title, message, exception=None):
        cls._queue_message('warning', title, message, exception)

    @classmethod
    def report_info(cls, title, message):
        cls._queue_message('info', title, message, None)

    @classmethod
    def _queue_message(cls, msg_type, title, message, exception=None):
        if cls._root is None:
            # Fallback if GUI is not initialized
            prefix = f"[{msg_type.upper()}] {title}: "
            print(prefix + message)
            if exception:
                print(f"Exception details: {exception}")
                traceback.print_exc()
        
        cls._error_queue.put({
            'type': msg_type,
            'title': title,
            'message': message,
            'exception': exception
        })

    @classmethod
    def setup_excepthook(cls):
        """Hook into sys.excepthook to catch all unhandled exceptions globally."""
        def custom_excepthook(exc_type, exc_value, exc_traceback):
            try:
                traceback.print_exception(exc_type, exc_value, exc_traceback)
            except Exception:
                print(f"Exception: {exc_value}")
            cls.report_error(
                "Unhandled Exception",
                f"An unexpected error occurred:\n\n{exc_value}",
                exception=exc_value
            )
        sys.excepthook = custom_excepthook

class DraggableClosableNotebook(ttk.Notebook):
    """A ttk.Notebook with draggable tabs and middle-click/right-click to close."""
    def __init__(self, master=None, **kwargs):
        super().__init__(master, **kwargs)
        self.bind("<ButtonPress-1>", self.on_press)
        self.bind("<B1-Motion>", self.on_drag)
        self.bind("<ButtonRelease-1>", self.on_release)
        
        # INTERIM: see plan.md S6. Middle-click-to-close and the right-click
        # "Close Tab" item are unbound. Under D-1 closing a tab means *hide*,
        # which is not safe until S5 moves the control loops out of the views:
        # a hidden device keeps running, and its loops must not belong to a
        # destroyed widget. Tabs stay draggable.
        
        self._active = None
        self.on_close_tab_callback = None

    def on_press(self, event):
        try:
            self._active = self.index(f"@{event.x},{event.y}")
        except tk.TclError:
            self._active = None

    def on_drag(self, event):
        if self._active is None:
            return
        try:
            target = self.index(f"@{event.x},{event.y}")
            if self._active != target:
                self.insert(target, self.tabs()[self._active])
                self._active = target
        except tk.TclError:
            pass

    def on_release(self, event):
        self._active = None

    def close_tab(self, index):
        """INTERIM: see plan.md S6. Kept for the shutdown path only.

        No user gesture reaches this any more. S6 replaces it with hide/show
        once the loops belong to the models.
        """
        if self.on_close_tab_callback:
            self.on_close_tab_callback(index)
        else:
            self.forget(index)

class DashboardWindow(tk.Toplevel):
    def _confirm_rotation_dialog(self, target_deg: float) -> bool:
        msg = f"Target rotation {target_deg:.2f}° exceeds the safe ±30° range.\n\nMoving past this limit risks damaging physical tubing.\n\nAre you sure you want to proceed?"
        return messagebox.askyesno("Rotation Limit Warning", msg, parent=self)

    def __init__(self, parent, system_manager):
        super().__init__(parent)
        self.system_manager = system_manager
        active_models = self.system_manager.get_active_models_snapshot()
        self.title("Unified Control Dashboard")
        self.geometry("1000x800")
        
        def on_focus_out(event):
            if event.widget == self:
                for model in active_models.values():
                    if hasattr(model, 'poller') and model.poller:
                        model.poller.flush_neutral()
                        
        self.bind("<FocusOut>", on_focus_out)
        
        # tk.Button ignores bg/fg on macOS Aqua, so a red/white button renders as
        # an invisible white-on-white face. Use a Label styled as a button instead.
        stop_btn = tk.Label(self, text="FULL STOP", bg="red", fg="white",
                            font=('Arial', 12, 'bold'), relief=tk.RAISED, pady=5, cursor="hand2")
        stop_btn.bind("<Button-1>", lambda e: self.system_manager.full_stop_all())
        # Bottom-docked, not top: sitting directly above the tab bar made it an
        # easy accidental-click target when reaching for a tab.
        stop_btn.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=5)
        
        self.notebook = DraggableClosableNotebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True)
        self.tab_metadata = {}

        for device_name, model in active_models.items():
            if device_name == "SMC100 Rotator" and model:
                model.confirm_rotation_callback = self._confirm_rotation_dialog
                
            frame = ttk.Frame(self.notebook)
            self.notebook.add(frame, text=device_name)
            
            # View Routing Logic
            if device_name == "Red Percent Window":
                view = RedPercentView(frame, model)
            elif hasattr(model, 'custom_view_class'):
                view_class = model.custom_view_class
                view = view_class(frame, model)
            elif hasattr(model, 'ui_schema'):
                view = DynamicView(frame, model)
            else:
                # Fallback to DynamicView with introspection
                view = DynamicView(frame, model)
                
            view.pack(fill='both', expand=True)
            
            # Engage Polling
            if hasattr(view, 'start_polling'):
                view.start_polling(self)
                
            self.tab_metadata[str(frame)] = {'model': model, 'view': view}
            
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def on_close(self):

        self.system_manager.shutdown_all()
                    
        self.destroy()
        self.master.deiconify() # Return to Setup Window


class ControllerLogWindow(tk.Toplevel):
    """Toplevel window to display real-time gamepad / joystick controller logs."""
    def __init__(self, poller=None, master=None):
        super().__init__(master)
        self.poller = poller
        self.title("Controller Log Window")
        self.geometry("500x400")
        self.configure(bg="#121212")
        
        self.text_widget = tk.Text(self, bg="#1E1E1E", fg="lightgreen", font=("Courier", 10), state="disabled", wrap="word")
        self.text_widget.pack(fill=tk.BOTH, expand=True, padx=5, pady=5)
        
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        
    def append_log(self, message):
        if not self.winfo_exists():
            return
        self.text_widget.config(state="normal")
        self.text_widget.insert(tk.END, message + "\n")
        self.text_widget.see(tk.END)
        self.text_widget.config(state="disabled")
        
    def on_close(self):
        if self.poller:
            def print_log(msg):
                print(f"[controllerDrive] {msg}")
            self.poller.log_updater = print_log
        self.destroy()

class DynamicView(tk.Frame):
    """
    A generic View class that constructs its UI dynamically based on the 
    `ui_schema` provided by the model. 
    """
    def __init__(self, parent, model, poll_interval_ms=50):
        super().__init__(parent)
        self.model = model
        self.poll_interval_ms = poll_interval_ms
        self.vars = {}  # Store Tkinter StringVars for binding
        self.entries = {}  # attr -> Entry widget, so _poll_model can skip
                            # overwriting a field the user is actively editing
        
        self.bg_main = 'black'
        self.fg_accent = 'white'
        self.configure(bg=self.bg_main)
        
        self.toggle_buttons = [] # Store references to dynamic toggle buttons
        
        self._build_ui()
        self._poll_model()

    def _build_ui(self):
        # Build the schema-driven grid inside its own sub-frame, then let
        # pack()'s default center anchor place that whole block in the middle
        # of the panel — the same layout RedPercentView already uses (packed
        # sub-frames center automatically without any extra wiring). Gridding
        # straight onto the outer, full-width frame left every row but the
        # spanning section titles pinned to the left edge.
        content = tk.Frame(self, bg=self.bg_main)
        content.pack(expand=True)
        schema = getattr(self.model, 'ui_schema', {"sections": []})
        self._build_from_schema(schema, content)

    def _is_valid_float(self, val):
        if val in ('.', '-', '-.', '+'):
            return True
        try:
            float(val)
            return True
        except ValueError:
            return False

    def _build_from_schema(self, schema, container):
        row_counter = 0
        for section in schema.get("sections", []):
            title = section.get("title", "Section")
            tk.Label(container, text=f"--- {title} ---", font=('Arial', 10, 'bold'),
                     bg=self.bg_main, fg=self.fg_accent).grid(row=row_counter, column=0, columnspan=4, pady=5)
            row_counter += 1
            
            elements = section.get("elements", [])
            for el in elements:
                el_type = el.get("type")
                label_text = el.get("text", "")
                
                if el_type in ["readonly", "entry"]:
                    attr = el.get("model_attr")
                    
                    # Create StringVar and bind it to model value
                    val = getattr(self.model, attr, "")
                    str_var = tk.StringVar(value=str(val))
                    self.vars[attr] = str_var
                    
                    tk.Label(container, text=label_text, bg=self.bg_main, fg=self.fg_accent).grid(
                        row=row_counter, column=0, padx=5, pady=2, sticky='w')

                    if el_type == "readonly":
                        tk.Label(container, textvariable=str_var, bg=self.bg_main, fg='lightgreen',
                                 font=('Arial', 10, 'bold')).grid(row=row_counter, column=1, padx=5, pady=2, sticky='w')
                    else: # entry
                        is_numeric = False
                        try:
                            float(val)
                            is_numeric = True
                        except ValueError:
                            pass

                        if is_numeric:
                            vcmd = (self.register(lambda P: P == "" or (self._is_valid_float(P))), '%P')
                            entry = tk.Entry(container, textvariable=str_var, validate='key', validatecommand=vcmd)
                            entry.grid(row=row_counter, column=1, padx=5, pady=2)
                            self.entries[attr] = entry

                            def on_finish(event, attr_name=attr, var=str_var):
                                text = var.get()
                                try:
                                    if not text:
                                        raise ValueError()
                                    v = float(text)
                                    if v != v or v in (float('inf'), float('-inf')):
                                        raise ValueError()
                                    setattr(self.model, attr_name, text)
                                except ValueError:
                                    var.set(str(getattr(self.model, attr_name, "")))
                            
                            entry.bind("<FocusOut>", on_finish)
                            entry.bind("<Return>", on_finish)
                        else:
                            entry = tk.Entry(container, textvariable=str_var)
                            entry.grid(row=row_counter, column=1, padx=5, pady=2)
                            self.entries[attr] = entry

                            def make_trace(attr_name, var):
                                return lambda *args: setattr(self.model, attr_name, var.get())
                                
                            str_var.trace_add("write", make_trace(attr, str_var))
                        
                elif el_type == "button":
                    cmd_name = el.get("command")
                    bg_color = el.get("bg", "darkgreen")
                    fg_color = el.get("fg", "black")

                    # tk.Button ignores bg/fg on macOS's native Aqua theme (the face
                    # stays system white/gray regardless of the option), which made
                    # white-text buttons like these invisible. A Label styled as a
                    # button — the same trick already used for the toggle controls
                    # above — renders its colors correctly on every platform.
                    btn_lbl = tk.Label(container, text=label_text, bg=bg_color, fg=fg_color,
                                        font=('Arial', 10, 'bold'), relief=tk.RAISED, pady=5,
                                        cursor="hand2")

                    def make_cmd(c_name):
                        return lambda e: self._execute_command(c_name)

                    btn_lbl.bind("<Button-1>", make_cmd(cmd_name))
                    btn_lbl.grid(row=row_counter, column=0, columnspan=2, padx=5, pady=5, sticky='ew')
                        
                elif el_type == "toggle":
                    attr = el.get("model_attr")
                    true_text = el.get("true_text")
                    false_text = el.get("false_text")
                    cmd_name = el.get("command")
                    
                    lbl = tk.Label(container, font=('Arial', 10, 'bold'), relief=tk.RAISED, pady=5, cursor="hand2")
                    
                    def make_cmd(c_name):
                        return lambda e: self._execute_command(c_name)
                        
                    lbl.bind("<Button-1>", make_cmd(cmd_name))
                    lbl.grid(row=row_counter, column=0, columnspan=2, padx=5, pady=5, sticky='ew')
                    
                    self.toggle_buttons.append({
                        "widget": lbl,
                        "attr": attr,
                        "true_text": true_text,
                        "false_text": false_text
                    })
                        
                elif el_type == "dropdown":
                    attr = el.get("model_attr")
                    cmd_name = el.get("command")
                    options_cmd = el.get("options_command")

                    tk.Label(container, text=label_text, bg=self.bg_main, fg=self.fg_accent).grid(
                        row=row_counter, column=0, padx=5, pady=2, sticky='w')

                    options_func = getattr(self.model, options_cmd, None) if options_cmd else None
                    current_val = str(getattr(self.model, attr, ""))
                    options = list(options_func()) if callable(options_func) else []
                    if current_val and current_val not in options:
                        options = [current_val] + options

                    combo_var = tk.StringVar(value=current_val)
                    combo = ttk.Combobox(container, textvariable=combo_var, values=options, state="readonly")
                    combo.grid(row=row_counter, column=1, padx=5, pady=2, sticky='ew')

                    def make_dropdown_cmd(c_name, var):
                        def handler(event=None):
                            func = getattr(self.model, c_name, None)
                            if func:
                                func(var.get())
                        return handler

                    combo.bind("<<ComboboxSelected>>", make_dropdown_cmd(cmd_name, combo_var))

                    def make_refresh(o_func, cb, var):
                        def handler():
                            cur = var.get()
                            opts = list(o_func()) if callable(o_func) else []
                            if cur and cur not in opts:
                                opts = [cur] + opts
                            cb['values'] = opts
                            if cur in opts:
                                var.set(cur)
                        return handler

                    tk.Button(container, text="⟳", width=2,
                              command=make_refresh(options_func, combo, combo_var)).grid(
                        row=row_counter, column=2, padx=2, pady=2)

                elif el_type == "file_picker":
                    cmd_name = el.get("command")
                    lbl = tk.Label(container, text="No Script Selected", bg=self.bg_main, fg='yellow', font=('Arial', 8))
                    lbl.grid(row=row_counter, column=1, padx=5, pady=2, sticky='w')

                    def make_file_cmd(c_name, label_widget):
                        def wrapped():
                            from tkinter import filedialog
                            path = filedialog.askopenfilename(
                                title="Select Script File",
                                filetypes=[("Text and GCode files", "*.txt *.gcode *.nc"), ("All files", "*.*")]
                            )
                            if path:
                                label_widget.config(text=path.split('/')[-1])
                                func = getattr(self.model, c_name, None)
                                if func: func(path)
                        return wrapped

                    # Same Aqua-ignores-bg issue as the "button" element type above;
                    # use the Label-styled-button pattern instead of tk.Button.
                    file_btn = tk.Label(container, text=label_text, bg="darkorange", fg="black",
                                         font=('Arial', 10, 'bold'), relief=tk.RAISED, pady=5, cursor="hand2")
                    file_btn.bind("<Button-1>", lambda e, c=cmd_name, w=lbl: make_file_cmd(c, w)())
                    file_btn.grid(row=row_counter, column=0, padx=5, pady=2, sticky='w')
                              
                row_counter += 1

    def _execute_command(self, cmd_name):
        # Buttons/toggles render as tk.Label (see _build_from_schema -- real
        # tk.Button ignores bg/fg on macOS Aqua), and Labels don't take
        # keyboard focus. Clicking one therefore never fires <FocusOut> on
        # whatever Entry the user was just typing into, so a numeric field's
        # commit-on-FocusOut handler never ran -- the command below would
        # read the model's PREVIOUS value, one edit-cycle behind whatever
        # was just typed (e.g. Temperature Controller's Ramp Rate sending
        # the prior value instead of the one just entered). Force focus
        # away first so any pending edit commits before we read model state.
        self.focus_set()
        if cmd_name == "open_controller_log":
            poller = getattr(self.model, 'poller', None)
            if not hasattr(self, 'log_window') or self.log_window is None or not self.log_window.winfo_exists():
                self.log_window = ControllerLogWindow(poller=poller, master=self)
                if poller:
                    poller.log_updater = self.log_window.append_log
            else:
                if poller:
                    poller.log_updater = self.log_window.append_log
                self.log_window.lift()
            return

        func = getattr(self.model, cmd_name, None)
        if func and callable(func):
            try:
                func()
            except Exception as e:
                messagebox.showerror("Error", f"Command {cmd_name} failed:\n{e}", parent=self)

    def _poll_model(self):
        # Sync StringVars from model by polling (if model is updated elsewhere)
        focused = self.focus_get()
        for attr, var in self.vars.items():
            if attr in self.entries and self.entries[attr] is focused:
                # Don't stomp a field the user is actively typing into --
                # numeric entries only commit to the model on FocusOut/Return,
                # so without this guard every keystroke got overwritten by
                # the model's last-committed value on the very next tick
                # (poll_interval_ms=50, faster than a human can type a
                # second character), making entry fields unmodifiable.
                continue
            if hasattr(self.model, attr):
                current_val = str(getattr(self.model, attr))
                if var.get() != current_val:
                    var.set(current_val)
                    
        # Update toggle buttons
        for tb in self.toggle_buttons:
            val = getattr(self.model, tb["attr"], False)
            widget = tb["widget"]
            if val:
                if widget['text'] != tb["true_text"]:
                    widget.config(text=tb["true_text"], bg='darkgreen', fg='black')
            else:
                if widget['text'] != tb["false_text"]:
                    widget.config(text=tb["false_text"], bg='darkred', fg='white')
                    
        self.after(self.poll_interval_ms, self._poll_model)

    def start_polling(self, dashboard_window):
        # 1. Start the controller poller if available
        if hasattr(self.model, 'poller') and self.model.poller:
            # Idle auto-disable now lives in the model (BaseProbe's interlock
            # watchdog) so every frontend shares it, including the web
            # dashboard, which previously had no auto-disable at all. The
            # view only needs to relay real controller activity into it.
            activity_callback = getattr(self.model, 'touch_activity', None)
            self.model.poller.start_polling(dashboard_window, log_updater=print, activity_callback=activity_callback)
            
            self._prev_manual_flag = False
            def _route_input():
                current_manual = getattr(self.model, 'manual_flag', False)
                if current_manual:
                    controller_params = self.model.poller.get_mapped_state()
                    if hasattr(self.model, 'send_manual_mode_command'):
                        self.model.send_manual_mode_command(controller_params or {})
                elif getattr(self, '_prev_manual_flag', False):
                    if hasattr(self.model, 'send_manual_mode_command'):
                        self.model.send_manual_mode_command({})
                self._prev_manual_flag = current_manual
                self.after(50, _route_input)
            self.after(50, _route_input)
            
        # 2. Start serial position reading
        if hasattr(self.model, 'read_position'):
            def _poll_pos():
                self.model.read_position()
                self.after(100, _poll_pos)
            self.after(100, _poll_pos)

        # 3. Start hardware status / rotator polling
        if hasattr(self.model, 'poll_status'):
            def _poll_stat():
                self.model.poll_status()
                self.after(100, _poll_stat)
            self.after(100, _poll_stat)

from tkinter import filedialog
import csv
from matplotlib.figure import Figure
try:
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.colors import LinearSegmentedColormap
except ImportError:
    FigureCanvasTkAgg = None
    LinearSegmentedColormap = None

class RedPercentView(tk.Frame):
    def __init__(self, master=None, system=None):
        super().__init__(master)
        self.system = system

        # GUI Setup
        control_frame = ttk.Frame(self)
        control_frame.pack(pady=10)

        self.select_btn = ttk.Button(control_frame, text="Select Focus Area", command=self.select_focus_area)
        self.select_btn.pack(side=tk.LEFT, padx=5)

        self.start_btn = ttk.Button(control_frame, text="Start Monitoring", command=self.start_monitoring)
        self.start_btn.pack(side=tk.LEFT, padx=5)

        self.stop_btn = ttk.Button(control_frame, text="Stop Monitoring", state=tk.DISABLED, command=self.stop_monitoring)
        self.stop_btn.pack(side=tk.LEFT, padx=5)

        self.plot_btn = ttk.Button(control_frame, text="Plot CSV", command=self.open_plot_window)
        self.plot_btn.pack(side=tk.LEFT, padx=5)

        status_frame = ttk.Frame(self)
        status_frame.pack(pady=10)

        ttk.Label(status_frame, text="Focus Area:").grid(row=0, column=0, sticky=tk.W)
        self.area_label = ttk.Label(status_frame, text="Not selected")
        self.area_label.grid(row=0, column=1, sticky=tk.W)

        meta_frame = ttk.LabelFrame(self, text="Probe Metadata")
        meta_frame.pack(pady=10, padx=10, fill=tk.X)
        meta_frame.columnconfigure(1, weight=1)

        ttk.Label(meta_frame, text="Probe Name:").grid(row=0, column=0, padx=5, pady=2, sticky=tk.W)
        self.probe_name_var = tk.StringVar(value=getattr(self.system, "probe_name", ""))
        ttk.Entry(meta_frame, textvariable=self.probe_name_var).grid(
            row=0, column=1, padx=5, pady=2, sticky=tk.EW)

        ttk.Label(meta_frame, text="Probe Tilt Angle:").grid(row=1, column=0, padx=5, pady=2, sticky=tk.W)
        self.probe_tilt_angle_var = tk.StringVar(value=getattr(self.system, "probe_tilt_angle", ""))
        ttk.Entry(meta_frame, textvariable=self.probe_tilt_angle_var).grid(
            row=1, column=1, padx=5, pady=2, sticky=tk.EW)

        self.probe_name_var.trace_add(
            "write", lambda *args: setattr(self.system, "probe_name", self.probe_name_var.get()))
        self.probe_tilt_angle_var.trace_add(
            "write", lambda *args: setattr(self.system, "probe_tilt_angle", self.probe_tilt_angle_var.get()))

        color_frame = ttk.LabelFrame(self, text="Red Detection")
        color_frame.pack(pady=10, padx=10, fill=tk.X)

        ttk.Label(color_frame, text="Red %:").grid(row=0, column=0, sticky=tk.W)
        self.red_label = ttk.Label(color_frame, text="0.0%")
        self.red_label.grid(row=0, column=1, sticky=tk.W)

        ttk.Label(color_frame, text="Red Change:").grid(row=1, column=0, sticky=tk.W)
        self.red_change_label = tk.Label(color_frame, text="0.0%", fg="black")
        self.red_change_label.grid(row=1, column=1, sticky=tk.W)

        self.reset_btn = ttk.Button(color_frame, text="Reset Baseline", state=tk.DISABLED, command=self.reset_baseline)
        self.reset_btn.grid(row=2, column=0, columnspan=2, pady=5)
        
        self.sync_vars = {
            'X': tk.BooleanVar(value=False),
            'Y': tk.BooleanVar(value=False),
            'Z': tk.BooleanVar(value=False)
        }
        
        sync_frame = ttk.Frame(color_frame)
        sync_frame.grid(row=3, column=0, columnspan=2, pady=5, sticky=tk.W)
        ttk.Label(sync_frame, text="Sync Dimensions:").pack(side=tk.LEFT)
        for dim in ['X', 'Y', 'Z']:
            chk = ttk.Checkbutton(sync_frame, text=dim, variable=self.sync_vars[dim], command=getattr(self.system, f"toggle_sync_{dim.lower()}"))
            chk.pack(side=tk.LEFT, padx=2)

        probe_frame = ttk.Frame(color_frame)
        probe_frame.grid(row=4, column=0, columnspan=2, pady=5, sticky=tk.W)
        ttk.Label(probe_frame, text="Position Source:").pack(side=tk.LEFT)
        
        self.probe_var = tk.StringVar()
        self.probe_dropdown = ttk.Combobox(probe_frame, textvariable=self.probe_var, state="readonly")
        self.probe_dropdown.pack(side=tk.LEFT, padx=5)
        self.probe_dropdown.bind("<<ComboboxSelected>>", self._on_probe_selected)
        
        self._update_probe_dropdown()
        self.poll_display()

    def _update_probe_dropdown(self):
        probes = self.system.get_available_probe_names()
        if probes:
            self.probe_dropdown['values'] = probes
            if hasattr(self.system, 'selected_probe_name') and self.system.selected_probe_name in probes:
                self.probe_var.set(self.system.selected_probe_name)
            else:
                self.probe_var.set(probes[0])
                self.system.set_stepper_model(probes[0])
        else:
            self.probe_dropdown['values'] = ["None Available"]
            self.probe_var.set("None Available")

    def _on_probe_selected(self, event=None):
        selected = self.probe_var.get()
        if hasattr(self.system, 'set_stepper_model'):
            self.system.set_stepper_model(selected)

    def select_focus_area(self):
        selection_window = tk.Toplevel(self.winfo_toplevel())
        screen_width = selection_window.winfo_screenwidth()
        screen_height = selection_window.winfo_screenheight()
        selection_window.geometry(f"{screen_width}x{screen_height}+0+0")
        selection_window.attributes('-alpha', 0.3)
        selection_window.configure(bg='gray10')
        selection_window.attributes('-topmost', True)
        try:
            selection_window.overrideredirect(True)
        except Exception:
            pass

        self.start_x = None
        self.start_y = None
        self.rect_id = None
        self.dragging = False

        canvas = tk.Canvas(selection_window, highlightthickness=0, width=screen_width, height=screen_height, cursor="crosshair")
        canvas.pack(fill=tk.BOTH, expand=True)

        def start_selection(event):
            self.start_x = event.x
            self.start_y = event.y
            self.dragging = True
            if self.rect_id:
                canvas.delete(self.rect_id)

        def update_selection(event):
            if self.dragging:
                if self.rect_id:
                    canvas.delete(self.rect_id)
                self.rect_id = canvas.create_rectangle(self.start_x, self.start_y, event.x, event.y, outline='red', width=3)

        def end_selection(event):
            if self.dragging:
                self.dragging = False
                end_x = event.x
                end_y = event.y
                left = min(self.start_x, end_x)
                top = min(self.start_y, end_y)
                width = abs(end_x - self.start_x)
                height = abs(end_y - self.start_y)
                if width > 10 and height > 10:
                    self.system.focus_area = {'left': int(left), 'top': int(top), 'width': int(width), 'height': int(height)}
                    selection_window.destroy()
                    self.area_label.config(text=f"{int(width)}x{int(height)} at ({int(left)},{int(top)})")
                    self.start_btn.config(state=tk.NORMAL)

        def cancel_selection(event):
            selection_window.destroy()

        canvas.bind('<Button-1>', start_selection)
        canvas.bind('<B1-Motion>', update_selection)
        canvas.bind('<ButtonRelease-1>', end_selection)
        canvas.bind('<Escape>', cancel_selection)
        selection_window.bind('<Escape>', cancel_selection)

        instruction = tk.Label(selection_window, text="Click and drag to select focus area. Press ESC to cancel.", fg='red', bg='black', font=('Arial', 24, 'bold'))
        instruction.place(relx=0.5, rely=0.05, anchor=tk.CENTER)
        canvas.focus_set()

    def start_monitoring(self):
        self.system.start_monitoring()
        self.start_btn.config(state=tk.DISABLED)
        self.stop_btn.config(state=tk.NORMAL)
        self.reset_btn.config(state=tk.NORMAL)

    def stop_monitoring(self):
        self.system.stop_monitoring()
        self.start_btn.config(state=tk.NORMAL)
        self.stop_btn.config(state=tk.DISABLED)
        
        if self.system.has_unsaved_data:
            if messagebox.askyesno("Save Log", "Monitoring stopped. Would you like to save the data to a CSV?"):
                self.save_log_to_file()

    def reset_baseline(self):
        self.system.reset_baseline()

    def save_log_to_file(self):
        if not self.system.data_log or not self.system.data_log.red_values:
            print("[color_test] No data to save.")
            return
        file_path = filedialog.asksaveasfilename(defaultextension=".csv", filetypes=[("CSV Files", "*.csv")], title="Save Red Detection Log")
        if file_path:
            self.system.save_log(file_path)

    def poll_display(self):
        if not self.winfo_exists():
            return
        red_pct = self.system.current_red
        red_change = self.system.red_change
        self.red_label.config(text=f"{red_pct:.1f}%")
        color = "green" if red_change > 0 else "red" if red_change < 0 else "black"
        self.red_change_label.config(text=f"{red_change:+.1f}%", fg=color)
        self.after(100, self.poll_display)

    def open_plot_window(self):
        if FigureCanvasTkAgg is None:
            messagebox.showerror("Plotting Unavailable",
                                  "matplotlib's Tk backend is not installed.", parent=self)
            return

        file_path = filedialog.askopenfilename(
            title="Select Red Detection Log",
            filetypes=[("CSV Files", "*.csv"), ("All files", "*.*")])
        if not file_path:
            return

        from model.plot_data import parse_red_percent_csv, render_red_percent_figure
        try:
            with open(file_path, newline='') as csvfile:
                parsed = parse_red_percent_csv(csvfile.read())
        except Exception as e:
            messagebox.showerror("Error", f"Could not read CSV file:\n{e}", parent=self)
            return

        if not parsed["red_percents"]:
            messagebox.showwarning("No Data", "The selected file has no plottable Red % data.", parent=self)
            return

        fig = render_red_percent_figure("0D", None, None, None, parsed["red_percents"], parsed["dim_data"])
        metadata = parsed["metadata"]
        if metadata:
            title_bits = [f"{k}: {v}" for k, v in metadata.items() if v]
            if title_bits:
                fig.axes[0].set_title(" | ".join(title_bits))

        plot_win = tk.Toplevel(self.winfo_toplevel())
        plot_win.title(f"Red % Plot — {file_path.split('/')[-1]}")
        plot_win.geometry("800x500")

        canvas = FigureCanvasTkAgg(fig, master=plot_win)
        canvas.draw()
        canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)

    def destroy(self):
        print("[color_test] Cleaning up and closing RedPercentView...")
        self.system.stop_monitoring()
        super().destroy()
        print("[color_test] Cleanup complete.")
