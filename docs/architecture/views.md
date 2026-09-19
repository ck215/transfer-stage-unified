# Views

Two active view layers: PySide6 (`src/views/pyside/view.py`, 967 lines) and
Tkinter (`src/views/tkinter/view.py`, ~830 lines). **Web
(`src/views/web/`) is deprioritized/legacy as of 2026-09-18** (see
[README.md](README.md)) — documented briefly at the bottom for completeness,
excluded from active parity work.

**Parity goal:** same buttons, same layout intent, same model methods called
per field, between PySide6 and Tkinter. Both implement the same core idea —
a generic renderer that walks a model's `ui_schema` dict and builds widgets
from it, so adding a field to a model's schema doesn't require touching
either view's code — but the two renderers, and especially the Red Percent
tab, have drifted apart. This doc catalogs both class hierarchies and the
concrete divergences found so far.

## The `ui_schema` element-type contract

Both `QtDynamicView._build_ui`/`_build_from_schema` (pyside) and
`DynamicView._build_ui`/`_build_from_schema` (tkinter) walk the same shape:
`{"sections": [{"title": str, "elements": [...]}]}`. Six element `type`s are
defined; here's how each is actually implemented on each side, and where
they disagree.

| `type` | Schema keys used | PySide6 (`view.py`) | Tkinter (`view.py`) |
|---|---|---|---|
| `readonly` | `text`, `model_attr` | `QLabel` bound via `self.vars[attr]`, refreshed in `_poll_model`. *(Corrected 2026-09-18: PySide6's `!hasFocus()` guard only applies to `QLineEdit` and `QComboBox`; `QLabel` updates unconditionally, matching Tkinter's safety).* | `tk.Label(textvariable=StringVar)`, refreshed unconditionally in `_poll_model` (safe — not editable) |
| `entry` | `text`, `model_attr` | `QLineEdit`, commits on `editingFinished` via a per-widget closure; poll loop skips overwrite `if not widget.hasFocus()`. Uses `QDoubleValidator(-1e9, 1e9, 3)` for numeric inputs. | `tk.Entry`, numeric fields commit on `<FocusOut>`/`<Return>` and use custom float validation; non-numeric commit live via `trace_add("write", ...)`. **Poll loop had no focus guard at all until the 2026-09-18 fix (commit `12e9d59`)** — see [known-issues.md](known-issues.md). |
| `button` | `text`, `command` | `QPushButton`, `clicked.connect(lambda: self._execute_command(cmd))` | `tk.Label` styled as a button (see note below), bound to `<Button-1>` |
| `toggle` | `text`, `model_attr`, `true_text`, `false_text`, `command` | `QPushButton` with `objectName` swapped between `toggleTrue`/`toggleFalse` for QSS styling (`style.qss:77-89`). **Missing `true_text`/`false_text` crashed `setText` prior to 2026-09-18.** | `tk.Label` styled as a button, `.config(text=..., bg=..., fg=...)` directly (hardcoded darkgreen/black vs. darkred/white, not stylesheet-driven) |
| `dropdown` | `text`, `model_attr`, `options_command`, `command` | `QComboBox` + a "⟳" rescan `QPushButton`. | `ttk.Combobox` (`state="readonly"`) + a "⟳" rescan `tk.Button`. |
| `file_picker` | `text`, `command` | **Dead code**: the `elif el_type == "file_picker":` branch's first statement is `continue` (`view.py:345`). | Fully implemented: label + `filedialog.askopenfilename` + calls the command with the chosen path. |

**Why Tkinter uses `tk.Label` instead of `tk.Button` for buttons/toggles**
(both view files have this comment): `tk.Button` ignores `bg`/`fg` on
macOS's native Aqua theme, rendering white-on-white/invisible buttons. A
`Label` bound to `<Button-1>` renders colors correctly on every platform.
This is a deliberate, documented workaround, not drift.

## PySide6 class hierarchy (`src/views/pyside/view.py`)

```
QMainWindow
 └─ DashboardWindow
     └─ QDockWidget
         └─ DeviceDock (adds a `closed` Signal on WA_DeleteOnClose)
             └─ QWidget
                 └─ QtDynamicView(model)               -- generic schema renderer
                     └─ RedPercentDynamicView(model)    -- + custom position-source control
QDialog
 ├─ ControllerLogWindow(poller)
 ├─ PlotDialog
 └─ SelectionOverlay(model)  -- NOT a QDialog, a frameless QWidget
QObject
 └─ QtErrorPopupManager      -- singleton, Qt-signal-marshaled popups
```

### `QtDynamicView` (`:121-454`)

**Ownership:** 1 view : 1 model.
- `__init__(self, model, parent=None)` (`:126-184`): Sets up UI, 50ms polling timer, position (100ms) and status (100ms) timers if model supports them, and 20ms gamepad polling loop if `model.poller` exists. Crucially, the 20ms loop executes `send_manual_mode_command({})` once when exiting manual mode to flush hardware.
- `_safe_read_position(self)` (`:186-190`): Calls `self.model.read_position()` inside try/except.
- `_safe_poll_status(self)` (`:192-196`): Calls `self.model.poll_status()` inside try/except.
- `_build_ui(self)` (`:198-375`): Walks `self.model.ui_schema` and constructs PySide6 widgets. Binds elements to UI vars and connects signals.
- `_execute_command(self, cmd_name)` (`:377-399`): Dispatches schema commands to `getattr(self.model, cmd_name)()`. Opens `ControllerLogWindow` directly for `"open_controller_log"`.
- `_poll_model(self)` (`:401-436`): Re-calls `model.read_position()` and `model.poll_status()`. Updates `self.vars` and toggles based on current model attributes.
- `cleanup(self)` (`:437-454`): Stops all QTimers (`timer`, `pos_timer`, `status_timer`, `input_timer`), closes `log_window`, calls `self.model.poller.stop_polling()` and `self.model.poller.close()`. **Does not call `model.teardown()`**.

### `RedPercentDynamicView(QtDynamicView)` (`:615-717`)

- `__init__(self, model, parent=None)` (`:616-620`): Adds custom buttons and position source controls on top of schema fields.
- `_add_custom_buttons(self)` (`:622-630`): Adds a "Save Log" button pointing to `self._save_log()`.
- `_save_log(self)` (`:632-633`): Calls `self.save_log_ui()`.
- `_add_position_source_control(self)` (`:634-657`): Constructs combobox reading from `self.model.get_available_probe_names()`. Sets default with `self.model.set_stepper_model()`.
- `_on_probe_selected(self, text)` (`:659-661`): Calls `self.model.set_stepper_model(text)`.
- `_execute_command(self, cmd_name)` (`:663-686`): Overrides standard dispatching. 
    - `"set_focus_area_ui"` spawns `SelectionOverlay`.
    - `"plot_data_ui"` spawns `PlotDialog`.
    - `"save_log_web"` maps to `self.save_log_ui()`.
    - `"stop_monitoring"` calls `super()`, then checks `self.model.has_unsaved_data` and prompts to save.
- `save_log_ui(self)` (`:688-709`): Calls `self.model.data_log.save_to_csv()`.
- `cleanup(self)` (`:711-717`): Extends `QtDynamicView.cleanup()`, calling `self.model.stop_monitoring()`.

### `SelectionOverlay(QWidget)` (`:456-514`)
Focus-area drag-select tool, known-broken on Linux compositors.
- `__init__(self, model)` (`:457-474`): Frameless, topmost window spanning all screens.
- `mousePressEvent`, `mouseMoveEvent` (`:475-486`): Tracking coordinates.
- `mouseReleaseEvent(self, event)` (`:487-498`): Commits to `self.model.focus_area = {'top': ..., 'left': ..., 'width': ..., 'height': ...}`.
- `keyPressEvent(self, event)` (`:500-503`): Escapes to close.
- `paintEvent(self, event)` (`:505-514`): Draws red rectangle.

### `PlotDialog(QDialog)` (`:516-613`)
Standalone viewer for red percent CSVs.
- `__init__(self, parent=None)` (`:517-534`)
- `load_csv(self)` (`:536-549`): Opens QFileDialog, calls `model.plot_data.parse_red_percent_csv`.
- `select_plot_type(self, dims_found, red_percents, dim_data)` (`:551-595`)
- `draw_plot(self, plot_type, dim1, dim2, dim3, red_percents, dim_data)` (`:597-613`): Calls `model.plot_data.render_red_percent_figure`.

### `ControllerLogWindow(QDialog)` (`:97-119`)
- `__init__(self, poller=None, parent=None)` (`:99-108`)
- `append_log(self, message)` (`:110-112`)
- `closeEvent(self, event)` (`:113-119`): Re-routes `poller.log_updater` back to `print`.

### `DeviceDock(QDockWidget)` (`:719-728`)
- `__init__(self, *args, **kwargs)` (`:722-724`): Calls `super().__init__` and sets the `Qt.WA_DeleteOnClose` attribute to ensure the dock is deleted from memory when closed.
- `closeEvent(self, event)` (`:726-728`): Overrides default to emit a custom `closed` Signal, then calls `super().closeEvent(event)`. Used by `DashboardWindow` to trigger cleanup when a user closes a dock.

### `DashboardWindow(QMainWindow)` (`:730-968`)
- `__init__(self, system_manager)` (`:731-774`): Top-level shell. Builds a sidebar dock containing a list of devices (via `populate_sidebar`) and a global `stop_btn` mapped to `self.system_manager.full_stop_all()`.
- `changeEvent(self, event)` (`:776-781`): Catches `QEvent.WindowDeactivate` and fires `model.poller.flush_neutral()` for all active models to prevent runaway hardware on focus loss.
- `populate_sidebar(self)` (`:783-804`): Iterates a **hardcoded device-name list** (`:786-789`), creating checkable `QListWidgetItem`s. Initially checks them if `self.system_manager.get_model()` confirms they are active. *(Corrected 2026-09-19: earlier text said "`SYSTEM_CONFIG` devices". No `SYSTEM_CONFIG` exists anywhere in `src/` — do not go looking for it. The hardcoded list is itself a finding; see root-causes.md RC-7/RC-9.)*
- `on_device_item_changed(self, item)` (`:806-811`): Triggered when a sidebar checkbox changes state. Routes to `open_device_view` if checked, `close_device_view` if unchecked.
- `close_device_view(self, device_name)` (`:813-847`): Closes the `DeviceDock` and cleans up its view (stopping QTimers via `cleanup()`). It then attempts to tear down the model by hand using `hasattr()` guards (`model.disable()`, `model.poller.stop_polling()`, `model.poller.close()` at `:840`, `model.disconnect()`) and deregistering it from `system_manager` via `del self.system_manager.active_models[...]` (`:846`).

  This conditional bypass of `ManagedModel.teardown()` has **two** defects, not one:
  1. Probe models have no `disconnect()` method, so the serial port is never released (RC-1).
  2. `model.poller.close()` decrements the process-global poller refcount, which can fire `pygame.quit()` while another poller is still live — the mechanism behind the recurring "no video instance" bug (RC-13 / GAMEPAD-2).

  **Do not "fix" this by adding a `disconnect()` method to `BaseProbe`.** That keeps the view-owned `hasattr` ladder and leaves defect 2 untouched; root-causes.md lists it as an anti-fix. The path is replaced by `SystemManager` lifecycle authority.
- `_confirm_rotation_dialog(self, target_deg)` (`:849-856`): Safety warning specific to SMC100 to prevent twisting physical tubing.
- `open_device_view(self, device_name)` (`:858-941`): Spawns the `DeviceDock`. If the model doesn't exist, instantiates it based on hardcoded `device_name` strings, links active probes (for Red Percent), and registers with `system_manager`. If the model exists, calls `reconnect_serial()`. Connects `dock.closed` to `on_dock_closed`.
- `on_dock_closed(self, device_name)` (`:943-955`): Acknowledges dock closure, temporarily unblocks signals to uncheck the corresponding sidebar item, and calls `close_device_view` to tear down the model.
- `closeEvent(self, event)` (`:957-967`): Global window closure handler. Calls `cleanup()` on all active dock widgets to stop timers, then invokes `self.system_manager.shutdown_all()`.

## Tkinter class hierarchy (`src/views/tkinter/view.py`)

```
tk.Toplevel
 ├─ DashboardWindow(parent, system_manager)
 │   └─ DraggableClosableNotebook(ttk.Notebook)
 │       └─ ttk.Frame
 │           ├─ RedPercentView(frame, model)        -- hand-built
 │           └─ DynamicView(frame, model)           -- generic schema renderer
 └─ ControllerLogWindow(poller)
tk.Frame
 └─ RedPercentView
```

### `DynamicView(tk.Frame)` (`:263-578`)

- `__init__(self, parent, model, poll_interval_ms=50)` (`:268-283`): Instantiates loops.
- `_build_ui(self)` (`:285-295`): Centers sub-frame.
- `_is_valid_float(self, val)` (`:297-304`): Custom input validation.
- `_build_from_schema(self, schema, container)` (`:306-480`): Walk logic mapping to Tkinter widgets. Trace variables.
- `_execute_command(self, cmd_name)` (`:482-510`): Special-cases `"open_controller_log"`, otherwise wraps `getattr(self.model, cmd_name)()`. Focus-sets away first to ensure numeric edits commit.
- `_poll_model(self)` (`:512-540`): Updates `self.vars` string vars from model state if the active widget does not hold focus.
- `start_polling(self, dashboard_window)` (`:542-578`): Evaluates loops. Sets up `model.poller.start_polling()` and manually calls `self.model.send_manual_mode_command({})` iteratively on manual-mode exit, re-armed every **50 ms** (`:563-564`). Starts `after(100)` loops for `model.read_position()` and `model.poll_status()` (`:565-578`). *(Corrected 2026-09-19: earlier text said "20ms equivalent". 20 ms is PySide's rate (`pyside/view.py:184`); Tk is 50 ms, so PySide routes manual input 2.5× as often.)*

### `RedPercentView(tk.Frame)` (`:590-835`)
Hand-built implementation disconnected from `ui_schema`.
- `__init__(self, master=None, system=None)` (`:591-674`): Builds all buttons and dropdowns. Syncs properties with `getattr(self.system, "probe_name")`. Binds trace updates to sync back to the model.
- `_update_probe_dropdown(self)` (`:676-687`): Populates probe list from `self.system.get_available_probe_names()`, triggers `self.system.set_stepper_model()`.
- `_on_probe_selected(self, event=None)` (`:689-692`): Calls `self.system.set_stepper_model()`.
- `select_focus_area(self)` (`:694-754`): Toplevel overlay canvas. Commits to `self.system.focus_area = {'left': ..., 'top': ..., 'width': ..., 'height': ...}`.
- `start_monitoring(self)` (`:756-760`): Calls `self.system.start_monitoring()`.
- `stop_monitoring(self)` (`:762-769`): Calls `self.system.stop_monitoring()`. Prompts to save if `self.system.has_unsaved_data`.
- `reset_baseline(self)` (`:771-772`): Calls `self.system.reset_baseline()`.
- `save_log_to_file(self)` (`:774-780`): Calls `self.system.save_log()`.
- `poll_display(self)` (`:782-790`): Direct access to `self.system.current_red` and `self.system.red_change`.
- `open_plot_window(self)` (`:792-829`): Local plotting using FigureCanvasTkAgg.
- `destroy(self)` (`:831-835`): Safety override calling `self.system.stop_monitoring()`.

### `ControllerLogWindow(tk.Toplevel)` (`:234-261`)
- `__init__(self, poller=None, master=None)` (`:236-246`)
- `append_log(self, message)` (`:248-254`)
- `on_close(self)` (`:256-261`): Resets `poller.log_updater`.

### `DraggableClosableNotebook(ttk.Notebook)` (`:108-163`)
- Pure UI tab interactions: `on_press` (`:122-126`), `on_drag` (`:128-137`), `on_release` (`:139-140`), `on_middle_click` (`:142-147`), `on_right_click` (`:149-156`), `close_tab` (`:158-163`).

### `DashboardWindow(tk.Toplevel)` (`:164-232`)
- `_confirm_rotation_dialog(self, target_deg: float)` (`:165-167`): MessageBox logic calling `messagebox.askyesno()`.
- `__init__(self, parent, system_manager)` (`:169-224`): Wires `<FocusOut>` to `model.poller.flush_neutral()`. Places `FULL STOP` tk.Label at bottom invoking `self.system_manager.full_stop_all()`. Sets `model.confirm_rotation_callback = self._confirm_rotation_dialog`. Resolves active models.
- `on_close(self)` (`:226-231`): Triggers `self.system_manager.shutdown_all()`.

## `DashboardWindow` comparison

| Concern | PySide6 | Tkinter |
|---|---|---|
| Container | `QMainWindow` + `QDockWidget`s, dockable/floatable/splittable | `tk.Toplevel` + `ttk.Notebook` tabs, draggable/closable via `DraggableClosableNotebook` |
| Global FULL STOP | Sidebar `QPushButton` directly connected to `system_manager.full_stop_all()`. *(Located 2026-09-18)* | Bottom-packed `tk.Label` mapped to `<Button-1>` bound to `system_manager.full_stop_all()`. |
| Rotation confirm dialog | `_confirm_rotation_dialog` using custom `QMessageBox`, exact same messaging. | `_confirm_rotation_dialog` using `messagebox.askyesno`, exact same messaging. |
| Focus-out behavior | `changeEvent` intercepting `QEvent.WindowDeactivate` and flushing gamepad neutral. *(Confirmed 2026-09-18)* | `<FocusOut>` event bound to flush gamepad neutral. |

## Deep-dive addendum (agy, 2026-09-18)
- **Resolved Flags**:
    - Located PySide6 global FULL STOP: it's a red QPushButton in the Device Manager sidebar layout (`view.py:756`).
    - Confirmed PySide6 window-focus-loss gamepad-neutral flush: it's implemented via overriding `changeEvent` and intercepting `QEvent.WindowDeactivate` (`view.py:776`).
    - Clarified PySide6 `readonly` schema element: the update loop actually does NOT use a `!hasFocus()` guard for `QLabel` (unlike `QLineEdit`); it updates unconditionally, mirroring Tkinter's behavior.
- **New Inconsistencies / Findings (to log in known-issues)**:
    - `file_picker` schema element on PySide6 has an unconditional `continue` at the top of its handler (`view.py:346`), making the rest of the file picker code entirely unreachable dead code, whereas Tkinter implements it fully.
    - PySide6 uses `save_to_csv` on `self.model.data_log` (`RedPercentDynamicView`), while Tkinter directly calls `self.system.save_log()` (`RedPercentView`).
    - ~~Tkinter's `DynamicView.start_polling` triggers the hardware position and status polls using `self.after(100)`, running them directly in the UI thread loop, whereas PySide6 uses `QTimer`s.~~ **Wrong — corrected 2026-09-19.** Both run on the GUI event loop (a `QTimer` is not a thread), and both dedicated poll timers are 100 ms (`tkinter/view.py:565-578`, `pyside/view.py:148,153`). The real divergence: PySide *also* calls `read_position()` and `poll_status()` from `_poll_model` on its 50 ms render tick (`pyside/view.py:402-405`), which Tk does not — roughly 3× the hardware traffic, on the GUI thread. See root-causes.md RC-4 / PYSIDE-9.
    - Tkinter's `RedPercentView` plotting is fully implemented locally via `FigureCanvasTkAgg` and runs in a `tk.Toplevel`, while PySide6 opens a separate `PlotDialog` class that handles the `FigureCanvasQTAgg` canvas.

## Web view (`src/views/web/`) — deprioritized, brief notes only

`web_adapter.py`, `web_server.py`, `web_view.py`. Not covered in this
parity pass. Known from earlier grep: `web_view.py` wires `ErrorRouter` to
a web-reporting queue (`ErrorRouter.set_callbacks(cls.report_error, ...)`,
same pattern as the other two frontends). Revisit once PySide6/Tkinter
parity work concludes and the web view's priority is reassessed.

---
*Last verified against commit `12e9d59` (2026-09-18).*
