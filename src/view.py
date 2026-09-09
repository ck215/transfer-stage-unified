import tkinter as tk
from tkinter import ttk, messagebox
import time
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
        
        self.bind("<Button-2>", self.on_middle_click) # Middle click to close on some systems
        self.bind("<Button-3>", self.on_right_click)  # Right click to close
        
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

    def on_middle_click(self, event):
        try:
            index = self.index(f"@{event.x},{event.y}")
            self.close_tab(index)
        except tk.TclError:
            pass

    def on_right_click(self, event):
        try:
            index = self.index(f"@{event.x},{event.y}")
            menu = tk.Menu(self, tearoff=0)
            menu.add_command(label="Close Tab", command=lambda: self.close_tab(index))
            menu.tk_popup(event.x_root, event.y_root)
        except tk.TclError:
            pass

    def close_tab(self, index):
        if self.on_close_tab_callback:
            self.on_close_tab_callback(index)
        else:
            self.forget(index)

class DashboardWindow(tk.Toplevel):
    def __init__(self, parent, active_models):
        super().__init__(parent)
        self.title("Unified Control Dashboard")
        self.geometry("1000x800")
        
        def on_focus_out(event):
            if event.widget == self:
                for model in active_models.values():
                    if hasattr(model, 'poller') and model.poller:
                        model.poller.flush_neutral()
                        
        self.bind("<FocusOut>", on_focus_out)
        
        self.notebook = DraggableClosableNotebook(self)
        self.notebook.pack(fill=tk.BOTH, expand=True)
        self.tab_metadata = {}

        for device_name, model in active_models.items():
            frame = ttk.Frame(self.notebook)
            self.notebook.add(frame, text=device_name)
            
            # View Routing Logic
            if hasattr(model, 'custom_view_class'):
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
        # Handle graceful shutdown of pollers
        for meta in self.tab_metadata.values():
            model = meta['model']
            if hasattr(model, 'poller') and model.poller:
                model.poller.stop_polling()
                model.poller.close()
            if hasattr(model, 'disconnect'):
                model.disconnect()
            if hasattr(model, 'stop'):
                try: model.stop()
                except: pass
            if hasattr(model, 'serial_conn') and model.serial_conn:
                if hasattr(model.serial_conn, 'close'):
                    model.serial_conn.close()
            elif hasattr(model, 'serial_comm') and model.serial_comm:
                if hasattr(model.serial_comm, 'close'):
                    model.serial_comm.close()
                    
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
        
        self.bg_main = 'black'
        self.fg_accent = 'white'
        self.configure(bg=self.bg_main)
        
        self.toggle_buttons = [] # Store references to dynamic toggle buttons
        
        self._build_ui()
        self._poll_model()

    def _build_ui(self):
        schema = getattr(self.model, 'ui_schema', {"sections": []})
        self._build_from_schema(schema)

    def _is_valid_float(self, val):
        if val in ('.', '-', '-.', '+'):
            return True
        try:
            float(val)
            return True
        except ValueError:
            return False

    def _build_from_schema(self, schema):
        row_counter = 0
        for section in schema.get("sections", []):
            title = section.get("title", "Section")
            tk.Label(self, text=f"--- {title} ---", font=('Arial', 10, 'bold'), 
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
                    
                    tk.Label(self, text=label_text, bg=self.bg_main, fg=self.fg_accent).grid(
                        row=row_counter, column=0, padx=5, pady=2, sticky='w')
                        
                    if el_type == "readonly":
                        tk.Label(self, textvariable=str_var, bg=self.bg_main, fg='lightgreen', 
                                 font=('Arial', 10, 'bold')).grid(row=row_counter, column=1, padx=5, pady=2, sticky='w')
                    else: # entry
                        is_numeric = False
                        if attr != "serial_port":
                            try:
                                float(val)
                                is_numeric = True
                            except ValueError:
                                pass
                                
                        if is_numeric:
                            vcmd = (self.register(lambda P: P == "" or (self._is_valid_float(P))), '%P')
                            entry = tk.Entry(self, textvariable=str_var, validate='key', validatecommand=vcmd)
                            entry.grid(row=row_counter, column=1, padx=5, pady=2)
                            
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
                            entry = tk.Entry(self, textvariable=str_var)
                            entry.grid(row=row_counter, column=1, padx=5, pady=2)
                            
                            def make_trace(attr_name, var):
                                return lambda *args: setattr(self.model, attr_name, var.get())
                                
                            str_var.trace_add("write", make_trace(attr, str_var))
                        
                elif el_type == "button":
                    cmd_name = el.get("command")
                    bg_color = el.get("bg", "darkgreen")
                    fg_color = el.get("fg", "black")
                    
                    def make_cmd(c_name):
                        return lambda: self._execute_command(c_name)
                        
                    tk.Button(self, text=label_text, bg=bg_color, fg=fg_color, 
                              font=('Arial', 10, 'bold'), command=make_cmd(cmd_name)).grid(
                        row=row_counter, column=0, columnspan=2, padx=5, pady=5, sticky='ew')
                        
                elif el_type == "toggle":
                    attr = el.get("model_attr")
                    true_text = el.get("true_text")
                    false_text = el.get("false_text")
                    cmd_name = el.get("command")
                    
                    lbl = tk.Label(self, font=('Arial', 10, 'bold'), relief=tk.RAISED, pady=5, cursor="hand2")
                    
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

                    tk.Label(self, text=label_text, bg=self.bg_main, fg=self.fg_accent).grid(
                        row=row_counter, column=0, padx=5, pady=2, sticky='w')

                    options_func = getattr(self.model, options_cmd, None) if options_cmd else None
                    current_val = str(getattr(self.model, attr, ""))
                    options = list(options_func()) if callable(options_func) else []
                    if current_val and current_val not in options:
                        options = [current_val] + options

                    combo_var = tk.StringVar(value=current_val)
                    combo = ttk.Combobox(self, textvariable=combo_var, values=options, state="readonly")
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

                    tk.Button(self, text="⟳", width=2,
                              command=make_refresh(options_func, combo, combo_var)).grid(
                        row=row_counter, column=2, padx=2, pady=2)

                elif el_type == "file_picker":
                    cmd_name = el.get("command")
                    lbl = tk.Label(self, text="No Script Selected", bg=self.bg_main, fg='yellow', font=('Arial', 8))
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
                        
                    tk.Button(self, text=label_text, bg="darkorange", fg="black", font=('Arial', 10, 'bold'), 
                              command=make_file_cmd(cmd_name, lbl)).grid(row=row_counter, column=0, padx=5, pady=2, sticky='w')
                              
                row_counter += 1

    def _execute_command(self, cmd_name):
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
        for attr, var in self.vars.items():
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
            def _reset_disable_timer(model_ref=self.model):
                model_ref.last_activity_time = time.time()
                if hasattr(model_ref, 'disable_timer_id') and model_ref.disable_timer_id:
                    dashboard_window.after_cancel(model_ref.disable_timer_id)
                    model_ref.disable_timer_id = None
                if getattr(model_ref, 'system_enabled', False):
                    model_ref.disable_timer_id = dashboard_window.after(300000, lambda: _auto_disable(model_ref))
            def _auto_disable(model_ref):
                if getattr(model_ref, 'is_stepping', False) or getattr(model_ref, 'manual_flag', False):
                    print(f"[Timeout] {model_ref.__class__.__name__} is actively stepping or in manual mode, deferring inactivity disable.")
                    model_ref.disable_timer_id = dashboard_window.after(30000, lambda: _auto_disable(model_ref))
                    return
                msg = f"5 minutes of inactivity detected. Disabling {model_ref.__class__.__name__}"
                print(f"[Timeout] {msg}")
                ErrorPopupManager.report_info("Idle Timeout", msg)
                if hasattr(model_ref, 'disable'):
                    model_ref.disable()
                    
            self.model.poller.start_polling(dashboard_window, log_updater=print, activity_callback=_reset_disable_timer)
            
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
