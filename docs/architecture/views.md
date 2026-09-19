# Views

Two active view layers: PySide6 (`src/views/pyside/view.py`, 967 lines) and
Tkinter (`src/views/tkinter/view.py`, 827 lines). **Web
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
| `readonly` | `text`, `model_attr` | `QLabel` bound via `self.vars[attr]`, refreshed in `_poll_model` if `!hasFocus()` (n/a, not editable) | `tk.Label(textvariable=StringVar)`, refreshed unconditionally in `_poll_model` (safe — not editable) |
| `entry` | `text`, `model_attr` | `QLineEdit`, commits on `editingFinished` via a per-widget closure; poll loop skips overwrite `if not widget.hasFocus()` | `tk.Entry`, numeric fields commit on `<FocusOut>`/`<Return>`, non-numeric commit live via `trace_add("write", ...)`. **Poll loop had no focus guard at all until the 2026-09-18 fix (commit `12e9d59`)** — see [known-issues.md](known-issues.md); this made every numeric entry field practically unmodifiable, since a 50ms poll tick reverted every keystroke before FocusOut/Return could commit it. |
| `button` | `text`, `command` | `QPushButton`, `clicked.connect(lambda: self._execute_command(cmd))` | `tk.Label` styled as a button (see note below), bound to `<Button-1>` |
| `toggle` | `text`, `model_attr`, `true_text`, `false_text`, `command` | `QPushButton` with `objectName` swapped between `toggleTrue`/`toggleFalse` for QSS styling (`style.qss:77-89`). **Missing `true_text`/`false_text` used to produce `None` → crashed `setText`, and because `self.toggle_buttons` is one shared list for the whole view, this could starve every toggle registered after the broken one for the rest of the session — fixed 2026-09-18, see known-issues.** | `tk.Label` styled as a button, `.config(text=..., bg=..., fg=...)` directly (hardcoded darkgreen/black vs. darkred/white, not stylesheet-driven) |
| `dropdown` | `text`, `model_attr`, `options_command`, `command` | `QComboBox` + a "⟳" rescan `QPushButton` | `ttk.Combobox` (`state="readonly"`) + a "⟳" rescan `tk.Button` (note: this one *is* a real `tk.Button`, not a Label — rescan buttons don't have the Aqua bg/fg problem since they use the default system face) |
| `file_picker` | `text`, `command` | **Dead code**: the `elif el_type == "file_picker":` branch's first statement is `continue`, so nothing is ever rendered — everything below it in that branch is unreachable. Not currently user-visible because no live schema uses `file_picker` (see `probes.py`'s comment on "Run Script" being intentionally unexposed), but it's latent drift: if a schema ever re-adds one, PySide6 silently shows nothing while Tkinter renders it fully. | Fully implemented: label + `filedialog.askopenfilename` + calls the command with the chosen path. |

**Why Tkinter uses `tk.Label` instead of `tk.Button` for buttons/toggles**
(both view files have this comment): `tk.Button` ignores `bg`/`fg` on
macOS's native Aqua theme, rendering white-on-white/invisible buttons. A
`Label` bound to `<Button-1>` renders colors correctly on every platform.
This is a deliberate, documented workaround, not drift.

## PySide6 class hierarchy (`src/views/pyside/view.py`)

```
QMainWindow
 └─ DashboardWindow (not fully read this pass — top-level shell, owns
                      DeviceDock instances via active_docks: dict[name, DeviceDock])
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
 └─ QtErrorPopupManager      -- singleton, Qt-signal-marshaled popups (see error-routing.md)
```

### `QtDynamicView` (`:121-454`)

**Ownership:** 1 view : 1 model (holds `self.model`, not owned — the model
outlives dock close in some paths, see
[ownership-and-lifecycle.md](ownership-and-lifecycle.md)). Constructs up to
four `QTimer`s in `__init__`: `self.timer` (50ms, `_poll_model`),
`self.pos_timer`/`self.status_timer` (100ms each, only if the model exposes
`read_position`/`poll_status`), `self.input_timer` (20ms, only if the model
has a `poller` — routes gamepad state into `send_manual_mode_command` every
tick, and crucially **also calls `send_manual_mode_command({})` once when
manual mode transitions off** via the `_prev_manual_flag` edge-detect, so
the firmware gets a final zeroed packet on exit).

| Method | Signature | Notes |
|---|---|---|
| `_build_ui` | `()` | Schema → `QFrame` cards, one per section, `QFormLayout` rows. |
| `_execute_command` | `(cmd_name: str)` | Dispatch for `button`/`toggle` commands; special-cases `"open_controller_log"`. Wraps arbitrary model calls in try/except → `QMessageBox.critical` on failure. |
| `_poll_model` | `()` | Refreshes `vars` (skip if focused) and `toggle_buttons` (objectName + text swap). |
| `cleanup` | `()` | Stops all timers, closes `log_window`, calls `model.poller.stop_polling()`+`close()` if present. **Does not call `model.teardown()`** — see ownership doc. References `self.disable_timer`, which is never actually created anywhere in the file — dead defensive code, harmless (the `hasattr` guard is always `False`). |

### `RedPercentDynamicView(QtDynamicView)` (`:615-716`)

Adds, beyond the schema-rendered sections (Probe Metadata, **Sync
Dimensions** — now the small toggle buttons, **Position Source** dropdown
which is present in the schema but non-functional, see below — Red
Detection, System Control):
- `_add_custom_buttons()` — a "Save Log" button.
- `_add_position_source_control()` (renamed from `_add_sync_dimension_controls`
  2026-09-18 — it used to *also* build a redundant second row of `QCheckBox`
  X/Y/Z sync controls duplicating the schema toggles; removed, see
  known-issues) — builds the actual working Position Source combo, with real
  auto-select-first-probe logic. **This exists because the schema's own
  Position Source dropdown entry has no `"command"` key**, so if it were ever
  rendered generically, selecting an option would call
  `getattr(self.model, None, None)` and raise — the hand-built version here
  is what actually works.
- Overrides `_execute_command` for `set_focus_area_ui` (spawns
  `SelectionOverlay`), `plot_data_ui`, `save_log_web`, and `stop_monitoring`
  (adds a "save to CSV?" confirmation dialog on top of the base stop).
- Overrides `cleanup()` to also call `model.stop_monitoring()` and close any
  open `plot_dialog` — **this is the *only* thing that stops the background
  monitoring thread on dock close**; `RedPercentSystem` doesn't implement
  `teardown()`/`emergency_stop()` at all, so it isn't part of the
  `ManagedModel` contract (see [known-issues.md](known-issues.md)).

### `SelectionOverlay(QWidget)` (`:456-511`)

The focus-area drag-select tool. Frameless, `Qt.WindowStaysOnTopHint`,
`WA_TranslucentBackground` + `setStyleSheet("background-color: rgba(0,0,0,100)")`,
spans the union of all screens. `paintEvent` draws a live red rectangle
between `start_pos_local`/`end_pos_local`. `mouseReleaseEvent` commits
`model.focus_area` and (as of 2026-09-18) shows a `QMessageBox.information`
confirming the captured rect.

**Known-broken on Linux**: `WA_TranslucentBackground` on a frameless
override window depends on compositor support, which many Linux WMs don't
provide (or don't provide for this window-flag combination) — reported
symptom is the whole screen going solid grey with no visible selection
rectangle, i.e. the user is dragging blind. The confirmation popup added
2026-09-18 is a partial mitigation (at least the *result* is visible even if
the drag itself isn't) — **not a full fix**; see known-issues for the
proper redesign direction (a screenshot-backed overlay instead of relying on
real window transparency, matching what Tkinter's `-alpha` approach gets
closer to already).

## Tkinter class hierarchy (`src/views/tkinter/view.py`)

```
tk.Toplevel
 ├─ DashboardWindow(parent, system_manager)   -- top-level shell
 │   └─ DraggableClosableNotebook(ttk.Notebook)  -- drag-to-reorder, middle/right-click to close
 │       └─ ttk.Frame (one per device tab)
 │           ├─ RedPercentView(frame, model)        -- for "Red Percent Window" ONLY
 │           ├─ model.custom_view_class(frame, model) -- if a model declares one (none currently do)
 │           └─ DynamicView(frame, model)           -- everything else, generic schema renderer
 └─ ControllerLogWindow(poller)
tk.Frame
 └─ RedPercentView   -- NOT a DynamicView subclass, entirely hand-built (see below)
```

Error popups: `ErrorPopupManager` here is a **separate class from
PySide6's** (both named similarly but distinct implementations) — queues
messages via `queue.Queue` and drains them on a 100ms `after()` poll rather
than Qt signals, since Tkinter has no cross-thread signal primitive. See
[error-routing.md](error-routing.md).

### `DynamicView(tk.Frame)` (`:263-556`)

Structurally the Tkinter counterpart to `QtDynamicView`, and reasonably
close in what it covers — same schema, same six element types (with the
`file_picker` and toggle-styling differences noted in the table above).

| Method | Notes vs. PySide6 equivalent |
|---|---|
| `_build_ui`/`_build_from_schema` | Grid-based instead of QFormLayout rows; wraps content in a sub-frame that gets centered via `pack(expand=True)`. |
| `_execute_command` | Same dispatch shape, special-cases `open_controller_log` identically. |
| `_poll_model` | Runs on `self.after(50, ...)` recursion instead of a `QTimer`. **Fixed 2026-09-18** to skip entry fields with focus (see entry-field row above). |
| `start_polling(dashboard_window)` | Tkinter's version of the constructor-embedded timer setup PySide6 does inline — called externally by `DashboardWindow.__init__` right after construction (`:219-220`), whereas PySide6 wires everything in `QtDynamicView.__init__` itself. Same three concerns either way: gamepad input routing, position polling, status polling. |

### `RedPercentView(tk.Frame)` (`:567-812`) — **the major structural divergence**

**This is not a `DynamicView` subclass and does not consult `ui_schema` at
all.** Every widget is hand-built directly in `__init__`:
- Control buttons: Select Focus Area, Start/Stop Monitoring, Plot CSV —
  these map to the model's `set_focus_area_ui`/`start_monitoring`/
  `stop_monitoring`/`plot_data_ui`-equivalent (`open_plot_window` is a
  Tkinter-only local implementation, not calling `model.plot_data_ui()` at
  all — the model's `plot_data_ui()` is a no-op `pass`, real plotting logic
  lives independently in each view).
- Probe Metadata entries (Probe Name, Probe Tilt Angle) — hand-built
  `tk.StringVar` + `trace_add`, bypassing the schema's `entry` renderer
  entirely (so these are *not* subject to the poll-stomp bug that just got
  fixed, since `RedPercentView` has no `_poll_model` touching these vars).
- **Sync Dimensions**: three `ttk.Checkbutton`s (`:628-639`) — the *only*
  sync control that exists on the Tkinter side. PySide6, after the
  2026-09-18 cleanup, now renders these as the small schema-driven toggle
  buttons instead. **These are structurally different widgets calling the
  same three model methods (`toggle_sync_x/y/z`)** — same behavior, visibly
  different controls. This is the parity gap flagged directly by the user
  (PySide6 previously had both a checkbox row *and* the toggle buttons;
  Tkinter has only ever had the checkbox row). Unifying these to one
  approach on both sides is an open item — see known-issues.
- **Position Source** dropdown (`:641-650`) — hand-built, same
  auto-select-first-probe logic as PySide6's hand-built version. Parity
  here is fine; both sides independently reimplement the same thing because
  the schema's own dropdown entry can't (see above).
- `select_focus_area()` (`:671-731`) — the Tkinter focus-area selector.
  **Different implementation from PySide6's `SelectionOverlay`**: a
  `tk.Toplevel` with `-alpha 0.3` + `bg='gray10'` + `overrideredirect(True)`,
  a `tk.Canvas` for the rubber-band rectangle (`create_rectangle`, deleted
  and redrawn on every `<B1-Motion>` event), *and* an on-screen instruction
  label ("Click and drag to select focus area. Press ESC to cancel.") that
  PySide6's version never had. ESC-to-cancel is also wired here
  (`canvas.bind('<Escape>', ...)`) — PySide6's `SelectionOverlay` does have
  an ESC handler too (`keyPressEvent`), so that part's already at parity.
  Tk's `-alpha` on a toplevel has its own cross-platform quirks but hasn't
  been reported broken the way PySide6's `WA_TranslucentBackground` has —
  worth treating as the more-proven reference implementation when
  redesigning the PySide6 side.
- `destroy()` override (`:808-812`) — explicitly calls
  `self.system.stop_monitoring()` before `super().destroy()`. This is
  Tkinter's version of what `RedPercentDynamicView.cleanup()` does on the
  PySide6 side — both correctly stop monitoring on close, just via
  different override points (`destroy()` vs. a custom `cleanup()` the
  dashboard calls manually before `dock.close()`).

**Bottom line on Red Percent parity:** the *button-level* functionality is
close to equivalent, but the *implementation strategy* is fully diverged —
PySide6 is (mostly) schema-driven, Tkinter is fully hand-built. Getting to
"complete parity down to the layout of buttons and the functions each field
calls" for this tab specifically means picking one strategy and porting the
other to match, not just fixing individual widget bugs as they're reported.

## `DashboardWindow` comparison

| Concern | PySide6 | Tkinter |
|---|---|---|
| Container | `QMainWindow` + `QDockWidget`s, dockable/floatable/splittable | `ttk.Notebook` tabs, draggable/closable via `DraggableClosableNotebook` |
| Global FULL STOP | (not yet located in this pass — follow up) | `tk.Label`-as-button, bottom-docked, calls `system_manager.full_stop_all()` |
| Rotation confirm dialog | `_confirm_rotation_dialog` on `DashboardWindow`, wired to `model.confirm_rotation_callback` | Same wiring, `_confirm_rotation_dialog` on `DashboardWindow`, `messagebox.askyesno` |
| Focus-out behavior | not yet located | `on_focus_out`: flushes every model's gamepad poller to neutral when the whole dashboard window loses OS focus — a safety behavior (stop driving if the app isn't focused). **Verify PySide6 has an equivalent** — not yet confirmed, flagged for follow-up. |

## Web view (`src/views/web/`) — deprioritized, brief notes only

`web_adapter.py`, `web_server.py`, `web_view.py`. Not covered in this
parity pass. Known from earlier grep: `web_view.py` wires `ErrorRouter` to
a web-reporting queue (`ErrorRouter.set_callbacks(cls.report_error, ...)`,
same pattern as the other two frontends). Revisit once PySide6/Tkinter
parity work concludes and the web view's priority is reassessed.

---
*Last verified against commit `12e9d59` (2026-09-18).*
