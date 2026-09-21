import tkinter as tk
from tkinter import ttk, messagebox
import queue
import sys
import traceback

from model import schema as sch
from error_routing import ErrorRouter

class ErrorPopupManager:
    """Tk subscriber on the event bus (RC-8 item 3).

    Three defects went with the rewrite.

    * It opened a **modal** `messagebox` for every severity, info included
      (TEMP-12). Only an `error` carrying `requires_ack` may do that now,
      and the bus will not let anything quieter ask.
    * `_poll_queue` returned without rescheduling when `_root` was gone and
      never cleared `_is_polling`, so the popup loop died with the dashboard
      and every later report vanished (ERRORS-5, VIEW-TKINTER-2,
      MANAGER-17). It binds to the **process-lifetime root** now, and the
      reschedule happens in a `finally`.
    * `_queue_message` printed when there was no root and then **queued
      anyway**, with no `return`, so messages accumulated forever in a queue
      nobody was draining.
    """

    _root = None
    _event_queue = queue.Queue()
    _is_polling = False
    _log = []
    _panel = None

    @classmethod
    def initialize(cls, root):
        """Bind to `root` — which must outlive the dashboard — and subscribe."""
        cls._root = root
        ErrorRouter.subscribe(cls._publish)
        if not cls._is_polling:
            cls._is_polling = True
            cls._poll_queue()

    @classmethod
    def shutdown(cls):
        """Unsubscribe and stop. A relaunch in the same process would
        otherwise stack a second subscriber and double every event."""
        ErrorRouter.unsubscribe(cls._publish)
        cls._is_polling = False
        cls._root = None

    @classmethod
    def _publish(cls, event):
        """Bus callback, on the publishing thread. Queue only — Tk is not
        thread-safe and the drain below runs on the main loop."""
        cls._event_queue.put(event)

    @classmethod
    def _poll_queue(cls):
        root = cls._root
        if root is None:
            cls._is_polling = False
            return
        try:
            while not cls._event_queue.empty():
                cls._handle(cls._event_queue.get())
        finally:
            # In a `finally` on purpose: a raise from one popup used to kill
            # the loop for the rest of the session.
            try:
                root.after(100, cls._poll_queue)
            except Exception:
                cls._is_polling = False

    @classmethod
    def _handle(cls, event):
        cls._log.append(event)
        if len(cls._log) > 500:
            del cls._log[:len(cls._log) - 500]
        if cls._panel is not None:
            cls._panel.append_event(event)
        if event.severity == "error" and event.requires_ack:
            messagebox.showerror(event.title, cls._format(event),
                                 parent=cls._root)

    @staticmethod
    def _format(event):
        # `or ""` is not decoration: a `None` message used to make this
        # method raise `TypeError` while formatting an error, which destroys
        # the only report of that error. There is a test for it.
        text = event.message or ""
        if event.exception is not None:
            try:
                text += (f"\n\nDetails:\n{type(event.exception).__name__}: "
                         f"{event.exception}")
            except Exception:
                text += "\n\nDetails: <Unprintable Exception>"
        if event.count > 1:
            text += f"\n\n(repeated {event.count} times)"
        if len(text) > 5000:
            text = text[:5000] + "... [TRUNCATED]"
        return text

    @classmethod
    def attach_panel(cls, panel):
        """Bind a non-modal log widget and replay what it missed."""
        cls._panel = panel
        for event in cls._log:
            panel.append_event(event)

    @classmethod
    def events(cls):
        return list(cls._log)

    @classmethod
    def setup_excepthook(cls):
        """Kept as the name `app.py` calls; the work is shared now.

        Tk missed `report_callback_exception` entirely, which is where every
        exception raised inside a widget callback goes — so a crash in a
        button handler printed to stderr and the operator saw a dead button
        (ERRORS-4).
        """
        from error_routing import install_exception_hooks
        install_exception_hooks(tk_root=cls._root)


class TkEventLogPanel(tk.Frame):
    """The non-modal half of RC-8 item 3, Tk's copy."""

    _COLOURS = {"info": "#8fa3b0", "warning": "#d6a13a", "error": "#d64545"}

    def __init__(self, master=None, **kwargs):
        super().__init__(master, **kwargs)
        self._text = tk.Text(self, height=8, state="disabled", wrap="word")
        self._text.pack(fill="both", expand=True)
        for severity, colour in self._COLOURS.items():
            self._text.tag_configure(severity, foreground=colour)

    def append_event(self, event):
        tail = f" (x{event.count})" if event.count > 1 else ""
        line = (f"[{event.severity.upper()}] {event.source}/{event.title}"
                f"{tail}: {event.message}\n")
        self._text.configure(state="normal")
        self._text.insert("end", line, event.severity)
        self._text.see("end")
        self._text.configure(state="disabled")


class DraggableClosableNotebook(ttk.Notebook):
    """A ttk.Notebook with draggable tabs and middle-click/right-click to close."""
    def __init__(self, master=None, **kwargs):
        super().__init__(master, **kwargs)
        self.bind("<ButtonPress-1>", self.on_press)
        self.bind("<B1-Motion>", self.on_drag)
        self.bind("<ButtonRelease-1>", self.on_release)
        
        # Middle-click closes a tab again (S6). The binding was withdrawn in
        # S2 because closing would have destroyed the widget that owned the
        # device's control loops; S5 moved those into the model.
        self.bind("<ButtonPress-2>", self.on_middle_press)

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

    def on_middle_press(self, event):
        try:
            index = self.index(f"@{event.x},{event.y}")
        except tk.TclError:
            return
        self.close_tab(index)

    def close_tab(self, index):
        """Hide a tab (D-1). The owner decides what that means for the device.

        `on_close_tab_callback` is assigned now. It never was before — the
        attribute existed and nothing wrote it, so this fell through to
        `forget()`, which is a **one-way** removal: ttk keeps no way to bring
        a forgotten tab back, so the device became unreachable while its model
        went on running. That was the "Tk is a one-way hide that leaks" item
        in plan.md S6.
        """
        if self.on_close_tab_callback:
            self.on_close_tab_callback(index)
        else:
            # `hide`, not `forget`. ttk.Notebook.hide() keeps the tab
            # registered, so `add()` on the same frame restores it in place.
            self.hide(index)

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
        
        # D-4: gate controller input while the application is unfocused.
        # Never stop — a move in flight continues, and alt-tabbing to read a
        # value does not halt the bench.
        self.bind("<FocusOut>", lambda e: self._sync_input_gate(e))
        self.bind("<FocusIn>", lambda e: self._sync_input_gate(e))
        
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
        # device_name -> the frame holding its tab, so a hidden tab has a
        # handle to be added back by (S6).
        self.device_frames = {}
        self.device_visible_vars = {}

        for device_name, model in active_models.items():
            # The rotation-confirmation callback the view used to inject here
            # is gone (S10 item 3): the model returns NeedsConfirmation and
            # every renderer asks it with one generic dialog.
            frame = ttk.Frame(self.notebook)
            self.notebook.add(frame, text=device_name)
            
            # Routing by the hint the *model* declares, rather than by a
            # device-name literal here (RC-7, I-7.1).
            view_class = VIEW_CLASSES.get(
                getattr(model, "VIEW_HINT", None), DynamicView)
            view = view_class(frame, model)
                
            view.pack(fill='both', expand=True)
            
            self.tab_metadata[str(frame)] = {'model': model, 'view': view}
            # Keep the frame under its device name so a hidden tab can be
            # added back. Without this there is no handle to re-add, which is
            # why the old close path could only `forget()`.
            self.device_frames[device_name] = frame

        self.notebook.on_close_tab_callback = self.on_tab_close_requested
        self._build_devices_menu()

        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def _sync_input_gate(self, event=None):
        """D-4. `focus_get()` is what tells a child dialog from a real loss.

        Tk fires `<FocusOut>` on this window both when the operator switches
        to another application *and* when this application opens a dialog.
        `focus_get()` returns the widget holding focus **within this
        application**, so a non-None answer means focus is still ours and the
        gate stays open. Treating the dialog case as focus loss is the defect
        behind VIEW-TKINTER-9.

        This replaces `poller.flush_neutral()`, which could not work: it zeroed
        the cached axis state and the poll loop read the physical stick again
        before the next send (GAMEPAD-8).
        """
        if event is not None and getattr(event, "widget", self) is not self:
            return
        try:
            is_open = self.focus_get() is not None
        except Exception:
            # A Tk error here means the window is going away; treat it as
            # unfocused, which is the conservative direction.
            is_open = False
        for model in self.system_manager.get_active_models_snapshot().values():
            setter = getattr(model, "set_input_gate", None)
            if callable(setter):
                setter(is_open)

    def _build_devices_menu(self):
        """The re-add path Tk did not have (plan.md S6 item 3).

        PySide has a sidebar of checkboxes; Tk had nothing, so a closed tab
        was gone for the session. A menubar of checkbuttons is the same
        contract in the idiom Tk already uses: tick to show, untick to hide,
        and the tick state is the device's visibility, read from the manager
        rather than remembered separately.
        """
        menubar = tk.Menu(self)
        devices = tk.Menu(menubar, tearoff=0)
        for device_name in self.device_frames:
            var = tk.BooleanVar(value=True)
            self.device_visible_vars[device_name] = var
            devices.add_checkbutton(
                label=device_name, variable=var,
                command=lambda n=device_name: self._on_device_menu_toggled(n))
        menubar.add_cascade(label="Devices", menu=devices)
        # `configure`, not the `config` alias: the alias is a real-Tk
        # convenience the test harness's Toplevel stand-in does not carry.
        self.configure(menu=menubar)

    def _on_device_menu_toggled(self, device_name):
        if self.device_visible_vars[device_name].get():
            self.show_device(device_name)
        else:
            self.hide_device(device_name)

    def on_tab_close_requested(self, index):
        """Middle-click on a tab. Resolve the tab to its device and hide it."""
        try:
            frame_id = self.notebook.tabs()[index]
        except (IndexError, tk.TclError):
            return
        for device_name, frame in self.device_frames.items():
            if str(frame) == frame_id:
                self.hide_device(device_name)
                return

    def hide_device(self, device_name):
        """D-1: the tab goes away, the device does not.

        The manager owns what hiding means for the hardware (a safe stop and
        de-energize, per D-2); the view owns only the widget.
        """
        frame = self.device_frames.get(device_name)
        if frame is None:
            return
        self.system_manager.hide(device_name)
        try:
            self.notebook.hide(frame)
        except tk.TclError:
            pass
        var = self.device_visible_vars.get(device_name)
        if var is not None:
            var.set(False)

    def show_device(self, device_name):
        """Re-add the existing tab. It never builds a second view or model."""
        frame = self.device_frames.get(device_name)
        if frame is None:
            return
        model = self.system_manager.show(device_name)
        if model is None:
            messagebox.showinfo(
                "Device Not Configured",
                f"{device_name} was not configured at startup.\n\n"
                "Restart and select it in the setup window to use it.",
                parent=self)
            var = self.device_visible_vars.get(device_name)
            if var is not None:
                var.set(False)
            return
        try:
            self.notebook.add(frame, text=device_name)
            self.notebook.select(frame)
        except tk.TclError:
            pass
        var = self.device_visible_vars.get(device_name)
        if var is not None:
            var.set(True)

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
        # Controls whose availability depends on the model's mode
        # (enabled_when / disabled_when), plus the composites that need
        # refreshing on each poll tick.
        self._gated = []
        self._plots = []
        self._log_streams = []
        self._regions = []

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
                        # TEMP-10: Use role-based colors for readonly fields to indicate
                        # staleness/connection state visually (e.g., connection_state with role="info")
                        bg_color, fg_color = self._role_colors(el.get("role"))
                        # If no role specified, use default lightgreen for readonly
                        if el.get("role") is None:
                            fg_color = 'lightgreen'
                            bg_color = self.bg_main
                        tk.Label(container, textvariable=str_var, bg=bg_color, fg=fg_color,
                                 font=('Arial', 10, 'bold')).grid(row=row_counter, column=1, padx=5, pady=2, sticky='w')
                    else: # entry
                        # **Declared, not guessed.** This used to call
                        # `float(val)` on the field's *current contents* and
                        # treat a raised exception as "this is text" — so a
                        # box the operator had cleared was reclassified as
                        # text and silently lost its validator for the rest of
                        # the session (RC-6 item 2).
                        is_numeric = el.get("value_type") in ("int", "float")

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
                    bg_color, fg_color = self._role_colors(el.get("role"))

                    # tk.Button ignores bg/fg on macOS's native Aqua theme (the face
                    # stays system white/gray regardless of the option), which made
                    # white-text buttons like these invisible. A Label styled as a
                    # button — the same trick already used for the toggle controls
                    # above — renders its colors correctly on every platform.
                    btn_lbl = tk.Label(container, text=label_text, bg=bg_color, fg=fg_color,
                                        font=('Arial', 10, 'bold'), relief=tk.RAISED, pady=5,
                                        cursor="hand2")

                    def make_cmd(element, widget):
                        def handler(e):
                            # Don't execute if the widget is disabled (VIEW-TKINTER-14)
                            if widget.cget("state") == "disabled":
                                return
                            return self._run_element(element)
                        return handler

                    btn_lbl.bind("<Button-1>", make_cmd(el, btn_lbl))
                    btn_lbl.grid(row=row_counter, column=0, columnspan=2, padx=5, pady=5, sticky='ew')
                    self._gated.append({"widget": btn_lbl, "element": el})
                        
                elif el_type == "toggle":
                    attr = el.get("model_attr")
                    true_text = el.get("true_text")
                    false_text = el.get("false_text")
                    cmd_name = el.get("command")
                    
                    lbl = tk.Label(container, font=('Arial', 10, 'bold'), relief=tk.RAISED, pady=5, cursor="hand2")

                    def make_cmd(element, widget):
                        def handler(e):
                            # Don't execute if the widget is disabled (VIEW-TKINTER-14)
                            if widget.cget("state") == "disabled":
                                return
                            return self._run_element(element)
                        return handler

                    lbl.bind("<Button-1>", make_cmd(el, lbl))
                    self._gated.append({"widget": lbl, "element": el})
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
                    current_val = sch.current_text(self.model, el)
                    options = list(options_func()) if callable(options_func) else []
                    if current_val and current_val not in options:
                        options = [current_val] + options

                    combo_var = tk.StringVar(value=current_val)
                    combo = ttk.Combobox(container, textvariable=combo_var, values=options, state="readonly")
                    combo.grid(row=row_counter, column=1, padx=5, pady=2, sticky='ew')

                    def make_dropdown_cmd(c_name, var):
                        def handler(event=None):
                            # `command` is mandatory in schema v2, so the
                            # getattr(model, None) that raised TypeError in
                            # PySide (PYSIDE-7) has no shape to occur in.
                            self.model.execute_command(c_name, args=(var.get(),))
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

                elif el_type == "file_save":
                    # Composite (S10 item 2): the *view* supplies the dialog,
                    # the model supplies the command. One contract, three
                    # renderers — Tk had a bespoke file_picker, PySide had an
                    # unreachable branch, and the Web client had neither.
                    bg_color, fg_color = self._role_colors(el.get("role"))
                    save_btn = tk.Label(container, text=label_text, bg=bg_color,
                                        fg=fg_color, font=('Arial', 10, 'bold'),
                                        relief=tk.RAISED, pady=5, cursor="hand2")

                    def make_save(element):
                        def handler(_event=None):
                            from tkinter import filedialog
                            exts = element.get("extensions", ["csv"])
                            path = filedialog.asksaveasfilename(
                                title=element.get("text", "Save"),
                                defaultextension="." + exts[0],
                                filetypes=[(e.upper(), "*." + e) for e in exts])
                            if path:
                                self._run_element(element, args=(path,))
                        return handler

                    save_btn.bind("<Button-1>", make_save(el))
                    save_btn.grid(row=row_counter, column=0, columnspan=2,
                                  padx=5, pady=5, sticky='ew')

                elif el_type == "region_select":
                    bg_color, fg_color = self._role_colors(el.get("role"))
                    region_btn = tk.Label(container, text=label_text, bg=bg_color,
                                          fg=fg_color, font=('Arial', 10, 'bold'),
                                          relief=tk.RAISED, pady=5, cursor="hand2")

                    def make_region(element):
                        def handler(_event=None):
                            region = self._select_region()
                            if region:
                                self._run_element(element, args=region)
                        return handler

                    region_btn.bind("<Button-1>", make_region(el))
                    region_btn.grid(row=row_counter, column=0, columnspan=2,
                                    padx=5, pady=5, sticky='ew')

                    # The captured region, shown rather than announced. See
                    # `schema.format_region` — PySide confirmed a capture with
                    # a modal and Tk showed nothing at all once D-6 deleted
                    # the hand-built label that used to carry it.
                    if el.get("model_attr"):
                        row_counter += 1
                        region_var = tk.StringVar(value=sch.format_region(
                            getattr(self.model, el["model_attr"], None)))
                        tk.Label(container, textvariable=region_var,
                                 bg=self.bg_main, fg=self.fg_accent).grid(
                            row=row_counter, column=0, columnspan=2,
                            padx=5, sticky='w')
                        self._regions.append({"element": el, "var": region_var})

                elif el_type == "plot":
                    # **D-6: the plot is schema-driven now.** Tk hand-built a
                    # RedPercentView around matplotlib and PySide bolted on a
                    # duplicate, each reaching into the data log directly;
                    # neither was reachable from the Web client at all. The
                    # model publishes a series and each renderer draws it.
                    self._plots.append({
                        "element": el,
                        "widget": self._build_plot(container, el, row_counter),
                    })

                elif el_type == "log_stream":
                    text = tk.Text(container, height=8, width=48, bg='#111111',
                                   fg='lightgreen', state='disabled')
                    text.grid(row=row_counter, column=0, columnspan=3,
                              padx=5, pady=5, sticky='ew')
                    self._log_streams.append({"element": el, "widget": text})

                elif el_type == "internal":
                    # Registers a command in the schema-derived allowlist
                    # without rendering anything.
                    pass

                row_counter += 1

    #: `role` -> (background, foreground). The schema names the *meaning*;
    #: mapping it to a palette is each renderer's own business. Elements used
    #: to carry raw `bg`/`fg` hex that only Tk could honour, so the same
    #: control looked different in every frontend for no stated reason.
    ROLE_COLORS = {
        "neutral": ("gray25", "white"),
        "go": ("darkgreen", "white"),
        "danger": ("darkred", "white"),
        "warning": ("darkorange", "black"),
        "info": ("darkblue", "white"),
    }

    def _role_colors(self, role):
        return self.ROLE_COLORS.get(role or "neutral", self.ROLE_COLORS["neutral"])

    def _gather_inputs(self, element):
        """The current *widget* text for each input the command declared (D-5).

        Reading the widgets rather than the model is the whole point: the
        model holds the value as of the last committed edit, which is one
        edit-cycle behind whatever was just typed. Tk used to hide that by
        calling `focus_set()` before every command to force a pending
        `<FocusOut>` to fire — **a named anti-fix**, because it worked only in
        Tk, only for the widget that happened to hold focus, and not at all
        for the Web client. The values travel with the command now and the
        model validates them as a set.
        """
        values = {}
        for name in element.get("inputs", []):
            var = self.vars.get(name)
            values[name] = var.get() if var is not None else getattr(
                self.model, name, "")
        return values

    def _run_element(self, element, args=None):
        """Run a schema element's command, with its inputs and any dialog args."""
        cmd_name = element.get("command")
        # No local try/except (RC-8 item 1): `execute_command` catches,
        # publishes a `Failed` to the bus and returns it, so the report
        # reaches the operator through the event log and one acknowledged
        # modal — the same path PySide now takes, instead of each view
        # catching in its own way.
        result = self.model.execute_command(
            cmd_name, inputs=self._gather_inputs(element), args=args)

        # The confirm contract (S10 item 3). One generic dialog per view,
        # replacing the `confirm_rotation_callback` the views used to inject
        # into the model — a callback the Web client never supplied, so the
        # ±30° tubing check existed there only as a silent refusal.
        if result.needs_confirmation:
            if messagebox.askyesno("Confirm", result.prompt, parent=self):
                result = self.model.execute_command(
                    result.command, args=(True,))

        return result

    def _execute_command(self, cmd_name):
        """Backwards-compatible entry point for a bare command name."""
        return self._run_element({"command": cmd_name})

    def _select_region(self):
        """Ask the operator for a rectangular region. Returns (x, y, w, h).

        Tk has no native region picker, so this is a modal prompt rather than
        a drag selection. Returning None declines, which the caller treats as
        a cancelled command.
        """
        from tkinter import simpledialog
        raw = simpledialog.askstring(
            "Focus Area", "Region as x,y,width,height:", parent=self)
        if not raw:
            return None
        try:
            parts = [int(p.strip()) for p in raw.split(",")]
        except ValueError:
            messagebox.showerror("Focus Area",
                                 "Expected four numbers: x,y,width,height",
                                 parent=self)
            return None
        if len(parts) != 4:
            messagebox.showerror("Focus Area",
                                 "Expected four numbers: x,y,width,height",
                                 parent=self)
            return None
        return tuple(parts)

    def _build_plot(self, container, element, row):
        """A plot the model feeds through its `data_command` (D-6)."""
        canvas = tk.Canvas(container, height=160, width=360, bg='#111111',
                           highlightthickness=0)
        canvas.grid(row=row, column=0, columnspan=3, padx=5, pady=5, sticky='ew')
        return canvas

    def _refresh_log(self, entry):
        source = getattr(self.model, entry["element"].get("source_command"), None)
        if not callable(source):
            return
        try:
            lines = list(source() or [])
        except Exception:
            return
        widget = entry["widget"]
        text = "\n".join(lines[-40:])
        try:
            if widget.get("1.0", "end-1c") == text:
                return
            widget.configure(state="normal")
            widget.delete("1.0", "end")
            widget.insert("1.0", text)
            widget.configure(state="disabled")
            widget.see("end")
        except Exception:
            pass

    def _redraw_plot(self, entry):
        canvas = entry["widget"]
        element = entry["element"]
        source = getattr(self.model, element.get("data_command"), None)
        if not callable(source):
            return
        try:
            series = source() or {}
            ys = list(series.get("y", []))
        except Exception:
            return
        try:
            canvas.delete("all")
        except Exception:
            return
        if len(ys) < 2:
            return
        width, height = 360, 160
        low, high = min(ys), max(ys)
        span = (high - low) or 1.0
        step = width / max(len(ys) - 1, 1)
        points = []
        for i, y in enumerate(ys):
            points.append(i * step)
            points.append(height - ((y - low) / span) * (height - 10) - 5)
        canvas.create_line(*points, fill='red', width=2)

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
                current_val = self._display(attr)
                if var.get() != current_val:
                    var.set(current_val)

        self._sync_gates()
        for entry in self._regions:
            text = sch.format_region(
                getattr(self.model, entry["element"]["model_attr"], None))
            if entry["var"].get() != text:
                entry["var"].set(text)
        for entry in self._plots:
            self._redraw_plot(entry)
        for entry in self._log_streams:
            self._refresh_log(entry)

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

    def _display(self, attr):
        """Render an attribute at its declared precision.

        A float rendered by `str()` shows whatever repr it happens to have,
        which is why the same reading appeared as `20` in one view and `20.0`
        in another. `decimals` is declared per parameter now.

        ROTATOR-9: Format position as readable text when None (cleared on
        poll failure). Show "--.--" instead of "None" for a no-reading state.
        """
        value = getattr(self.model, attr)

        # Special case: rotator position formatting (ROTATOR-9)
        if attr == "position":
            if value is None:
                return "--.--"  # No reading state
            try:
                # Format position as a float with 4 decimal places (matching main)
                float_val = float(value) if isinstance(value, str) else value
                return f"{float_val:.4f}"
            except (ValueError, TypeError):
                return str(value)

        param = getattr(self.model, "PARAMS", {}).get(attr)
        if param is not None and param.is_numeric:
            return param.format(value)
        return str(value)

    def _mode_name(self):
        """The model's current mode, as the schema's gates name it."""
        mode = getattr(self.model, "mode", None)
        if mode is not None:
            return getattr(mode, "value", str(mode))
        if getattr(self.model, "monitoring", False):
            return "monitoring"
        return "idle"

    def _sync_gates(self):
        """Grey out controls the current mode forbids.

        One rule, evaluated by `schema.is_enabled`, so "disabled during a run"
        cannot mean three different things in three frontends — which is what
        it meant when each view hardcoded its own list, where it had one.
        """
        mode = self._mode_name()
        for gate in self._gated:
            enabled = sch.is_enabled(gate["element"], mode)

            # VIEW-TKINTER-14: start_monitoring button should also be disabled
            # if no focus_area is set (it would return Refused from the model)
            element = gate["element"]
            if element.get("command") == "start_monitoring":
                focus_area = getattr(self.model, "focus_area", None)
                if not focus_area:
                    enabled = False

            widget = gate["widget"]
            try:
                widget.configure(state=("normal" if enabled else "disabled"))
            except Exception:
                # tk.Label styled as a button has no state option; dim it.
                try:
                    widget.configure(cursor="hand2" if enabled else "X_cursor")
                except Exception:
                    pass

    # start_polling() was here (RC-4). It started the gamepad poller on this
    # widget's event loop, ran a 50 ms manual-input pump, and ran 100 ms
    # position and status pumps. PySide had the same four loops at different
    # rates, and the Web frontend had none — which is the divergence RC-4
    # exists to remove. All four now belong to the model, so every frontend
    # behaves the same and a closed tab cannot take a control loop with it.

from tkinter import filedialog
import csv
from matplotlib.figure import Figure
try:
    from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
    from matplotlib.colors import LinearSegmentedColormap
except ImportError:
    FigureCanvasTkAgg = None
    LinearSegmentedColormap = None

class RedPercentView(DynamicView):
    """Red Percent, rendered from the schema like every other device (D-6).

    **About 240 lines of hand-built widgets used to live here**: its own
    metadata entries, its own sync-dimension checkboxes, its own Start/Stop
    and Save buttons, its own matplotlib plot window and its own CSV dialog —
    each reaching into the model directly and each drifting from the schema
    the other views rendered. PySide carried a parallel bolt-on of the same
    controls, and the Web client had none of them.

    All of it is schema v2 now: `entry`, `toggle`, `button`, plus the
    `plot`, `file_save` and `region_select` composites. What is left is the
    one thing that is genuinely this view's own — stopping the run when the
    tab goes away.
    """

    def destroy(self):
        print("[color_test] Cleaning up and closing RedPercentView...")
        try:
            self.model.stop_monitoring()
        except Exception:
            pass
        super().destroy()
        print("[color_test] Cleanup complete.")


#: Hint -> Tk widget. The only reason a device needs an entry here is
#: behaviour the schema genuinely cannot express; everything else renders
#: with DynamicView.
VIEW_CLASSES = {
    "red_percent": RedPercentView,
}
