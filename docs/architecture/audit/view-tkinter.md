# view-tkinter audit (reference view)

> **Input (banner added 2026-09-23).** Audit of the old tree (now `legacy/src/`); current until the rebuild replaced it on 2026-09-23. Kept because the ledger in `docs/implementation/progress.md` and `docs/rebuild/carry.json` cite these finding IDs.

Repo: /Users/ianalbinogonzalez/Documents/GitHub/transfer-stage-unified/mvc-refactor (branch mvc-refactor). All paths below are relative to `src/` unless prefixed. `view.py` = `src/views/tkinter/view.py`. Python in `.venv` is 3.14.7 (checked; matters for one tkinter-internals claim).

## Object summary

- Entry: `app.py:41 run_legacy_app()` builds `SetupWindow(tk.Tk)` (`app.py:81`), then `ErrorPopupManager.initialize(app)` + `setup_excepthook()` + `app.mainloop()` (`app.py:428-432`). `launch_legacy()` at `app.py:~810` calls it.
- Model construction: NOT in the view. `SetupWindow.launch_unified` (`app.py:359-423`) validates (`app_bootstrap.validate_assignment`, `app_bootstrap.py:~168`), `self.withdraw()` (`:397`), `app_bootstrap.build_models(active_configs, self.active_claims)` (`:402`), wires Red Percent to probes by direct attribute assignment `red_model.available_probes = ...` (`:404-412`), creates a fresh `SystemManager()` per launch and `register_model`s each (`:418-420`), then `DashboardWindow(self, system_manager)` (`:422`) and `ErrorPopupManager.initialize(dash)` (`:423`).
- Owner of models: `SystemManager.active_models` (`model/system_manager.py:7`). Dashboard holds `system_manager`, plus a construction-time snapshot `active_models` (`view.py:172`) and `tab_metadata` (`view.py:195, 222`).
- Teardown path (only one): WM_DELETE_WINDOW on the dashboard -> `DashboardWindow.on_close` (`view.py:224-231`) -> `system_manager.shutdown_all()` (`system_manager.py:59-68`, per-model `teardown()`), then `destroy()`, then `master.deiconify()` (Setup returns). Emergency path: bottom "FULL STOP" Label (`view.py:186-191`) -> `system_manager.full_stop_all()` (`system_manager.py:70-78`, per-model `emergency_stop()`).
- SystemManager primitives used by Tk: `register_model` (app.py), `get_active_models_snapshot` (view.py:172), `full_stop_all` (view.py:188), `shutdown_all` (view.py:228). NOT used: `remove_model`, `reboot_model`, `get_model` (grep of src/views/tkinter and src/app.py: zero hits). Tk does not hand-roll a partial teardown; it simply has no per-tab teardown at all (Finding 1).
- ManagedModel contract (`model/base.py:5-22`): all four model classes define both `teardown` and `emergency_stop` (BaseProbe `probes.py:479,489`; TemperatureSystem `temperature_system.py:198,201`; RotatorSystem `rotator_system.py:122,125`; RedPercentSystem `redpercent_system.py:276,279`). Tk relies on `SystemManager` duck-typing (`system_manager.py:21,74`), no runtime `isinstance(model, ManagedModel)` check anywhere.
- Big structural difference vs main: main was one OS process + one `tk.Tk` per device (`main:src/mainGUI.py:339-362`, `main:src/stepper_frame.py:689-707`), each with its own close handler doing disable+controller close+serial close (`main:src/stepper_frame.py:675-683`), and per-device Full Stop / Enable / Serial Reconnect / Controller dropdown. The refactor Tk view is one Toplevel with a Notebook; per-device Full Stop, Enable, Serial Reconnect and Run Script were deliberately removed from the schema (`model/probes.py:127-154`), replaced by the global bar.

## Behavior contract table

Column key: model call = method/attr on the model object; trigger = click/commit/poll/event; "view.py" line cites unless noted.

### A. App / Setup window (Tk root, `app.py`)

| window/tab | widget | model method or attribute called | trigger | lifecycle (created where, destroyed where) | file:line |
|---|---|---|---|---|---|
| Setup (tk.Tk root) | window | none (owns `active_claims = {}` plain dict, passed to models) | app start | created `app.py:428`; never destroyed except process exit; `withdraw()` on launch (`:397`), `deiconify()` on dashboard close (`view.py:231`) | app.py:81-100, 428-432 |
| Setup | port/controller OptionMenus, checkboxes | none (pure UI); `toggle_dropdown_state` | click | created `create_widgets` `app.py:204-274` | app.py:189-202, 231-265 |
| Setup | auto-detect scan | `app_bootstrap.probe_device_at(port)` on daemon thread; results via `gui_queue` polled by `after(50)` | auto at +200 ms; Refresh button (`refresh_devices`, force) | thread `app.py:303-305`; `check_queue` self-reschedules forever `app.py:114-129` | app.py:111-112, 276-331 |
| Setup | Refresh Devices button | `get_available_ports/controllers`, `start_autodetect(force=True)` | click | `app.py:270` | app.py:155-179 |
| Setup | Launch Unified Application button | `validate_assignment`, `build_models`, `red_model.available_probes=`, `set_stepper_model`, `SystemManager.register_model`, `DashboardWindow(...)`, `ErrorPopupManager.initialize(dash)` | click | models + SystemManager created here (`:402,418`), no try/except; Setup withdrawn before building (`:397`) | app.py:359-423 |
| Setup | `messagebox` on collisions/no devices | `validate_assignment` errors | click | modal, parent = Setup | app.py:386-395 |

### B. Dashboard window (`DashboardWindow(tk.Toplevel)`)

| window/tab | widget | model method or attribute called | trigger | lifecycle | file:line |
|---|---|---|---|---|---|
| Dashboard | Toplevel 1000x800 | `system_manager.get_active_models_snapshot()` | construct | created `app.py:422`; destroyed in `on_close` `view.py:230` | view.py:169-174 |
| Dashboard | `<FocusOut>` on window (`event.widget == self`) | `model.poller.flush_neutral()` for every model in the construction-time snapshot | window focus loss | bound `view.py:182`, never unbound | view.py:176-182 |
| Dashboard | FULL STOP `tk.Label` (red), packed BOTTOM | `system_manager.full_stop_all()` -> each `model.emergency_stop()` | `<Button-1>` press (not release) | created `view.py:186-191`; lives with window | view.py:186-191 |
| Dashboard | `DraggableClosableNotebook` | none directly | drag `<B1-Motion>` reorders (`insert`), `<Button-2>` close, `<Button-3>` context-menu "Close Tab" | created `view.py:193`; `on_close_tab_callback` is never assigned (grep: only `view.py:120` sets None) so `close_tab` -> `self.forget(index)` only | view.py:108-162, 193-195 |
| Tab (per device) | `ttk.Frame` + tab label = device_name | n/a | construct | one per key of the snapshot, `view.py:197-202`; tab_metadata[str(frame)] = {'model','view'} `:222`; never removed | view.py:197-222 |
| Tab "SMC100 Rotator" | injection | sets `model.confirm_rotation_callback = self._confirm_rotation_dialog` (`messagebox.askyesno`) | construct; invoked by `RotatorSystem._confirm_rotation` (`rotator_system.py:209-215`) on `move_absolute`/`move_relative` when abs(target)>30 | `view.py:198-199` (hard-coded name match) | view.py:165-167, 198-199 |
| Tab routing | view class choice | `device_name == "Red Percent Window"` -> `RedPercentView`; `hasattr(model,'custom_view_class')` (no model defines it, grep) -> that; else `DynamicView` (both later branches identical) | construct | `view.py:204-216` | view.py:204-216 |
| Dashboard close | X button | `system_manager.shutdown_all()` (synchronous on Tk thread) -> `model.teardown()` per model; `destroy()`; `master.deiconify()` | WM_DELETE_WINDOW | `view.py:224-231`; no `try`, no unsaved-data check | view.py:224-231 |

### C. Generic `DynamicView(tk.Frame)` (Stepper/DC/Chuck/Temperature/Rotator tabs)

| window/tab | widget | model method or attribute called | trigger | lifecycle | file:line |
|---|---|---|---|---|---|
| DynamicView | frame | `model.ui_schema` (property, read once at build `view.py:294`) | construct | created per tab `view.py:214`; `_build_ui()` then `_poll_model()` in `__init__`; destroyed only when Dashboard destroyed (child widget) | view.py:268-295 |
| DynamicView | readonly `tk.Label(textvariable)` | reads `getattr(model, attr)` (pos_x/y/z, current_temp, position, state, error, current_red...) | poll every 50 ms (`_poll_model`) | StringVar in `self.vars[attr]` | view.py:319-332, 512-527 |
| DynamicView | numeric `tk.Entry` (validate=key, float regex) | write `setattr(model, attr, text)` | commit on `<FocusOut>` or `<Return>` (invalid/empty/nan/inf reverts to model value) | `self.entries[attr]`; poll skips it while it has focus | view.py:334-361, 514-523 |
| DynamicView | text `tk.Entry` (non-numeric initial value, or attr == "serial_port") | write `setattr(model, attr, var.get())` | StringVar write-trace = every keystroke (and every poll-driven `var.set`) | same | view.py:362-370 |
| DynamicView | button `tk.Label` styled as button | `getattr(model, command)()` inside try/except -> `messagebox.showerror` | `<Button-1>`; first does `self.focus_set()` to force pending numeric edit to commit | none | view.py:372-390, 482-510 |
| DynamicView | toggle `tk.Label` (auton_flag / manual_flag / sync_*) | `getattr(model, command)()` (toggle_auton / toggle_manual); state read via `getattr(model, attr)` | click; label text/colour repainted by poll every 50 ms | `self.toggle_buttons` | view.py:392-411, 529-538 |
| DynamicView | dropdown `ttk.Combobox(readonly)` + "⟳" `tk.Button` (Controller ID) | options: `model.get_available_controllers()` (physical only; current value prepended if absent); on select `model.set_controller(value)` | `<<ComboboxSelected>>`; refresh only on ⟳ click. Combobox StringVar is NOT in `self.vars`, so never re-synced from model | none | view.py:413-453 |
| DynamicView | file_picker branch | `model.<command>(path)` | click | dead: no current schema emits `file_picker` (removed, `probes.py:144-147`) | view.py:455-478 |
| DynamicView | "Controller Log Window" (command `open_controller_log`) | view-intercepted; model method is a `print` stub (`probes.py:194-195`); sets `model.poller.log_updater = log_window.append_log` | click | `ControllerLogWindow` (Toplevel, master=view) created lazily `view.py:496`; reused/lifted if alive; `on_close` resets `poller.log_updater = print` and destroys | view.py:234-261, 493-503 |
| DynamicView | poll loop 1 `_poll_model` | reads model attrs, writes StringVars/toggle labels | `after(50)` self-rescheduling, no try/finally | started in `__init__` `view.py:283`, reschedule at `:540`; dies with widget (Tcl command deleted) | view.py:512-540 |
| DynamicView | poll loop 2 `_route_input` (manual-mode pump) | `model.manual_flag`; `model.poller.get_mapped_state()`; `model.send_manual_mode_command(params)`; on manual->off edge `send_manual_mode_command({})` once | `after(50)`, only if `model.poller` truthy | started in `start_polling` `view.py:564`; no try/finally | view.py:544-564 |
| DynamicView | gamepad poller | `model.poller.start_polling(dashboard_window, log_updater=print, activity_callback=model.touch_activity)`; `ControllerPoller._poll_loop` then self-reschedules on `dashboard.after(5)` while `is_polling` and gamepad present | started once per tab at dashboard construct (not on entering manual mode as in main) | started `view.py:550`; stopped by `teardown` -> `poller.stop_polling()` (`probes.py:483`) or `_handle_disconnect` (`gamepad.py:315-323`) | view.py:544-550; controller/gamepad.py:474-488, 572-640 |
| DynamicView | poll loop 3 | `model.read_position()` (serial read on UI thread) | `after(100)` | started `view.py:571`; if `read_position` raises, loop dies | view.py:566-571 |
| DynamicView | poll loop 4 | `model.poll_status()` (rotator: 2 serial round-trips on UI thread) | `after(100)` | `view.py:578` | view.py:573-578 |
| DynamicView | Enable/Disable, Full Stop (per device), Serial Reconnect, Run Script | NOT PRESENT in Tk (removed from schema `probes.py:127-154`); `toggle_enable/reconnect_serial/run_script` exist on the model with no Tk caller | n/a | n/a | model/probes.py:127-154 |

### D. `RedPercentView(tk.Frame)` (hand-built; ignores `ui_schema`)

| window/tab | widget | model method or attribute called | trigger | lifecycle | file:line |
|---|---|---|---|---|---|
| Red Percent tab | Select Focus Area (fullscreen alpha 0.3 overlay Toplevel, overrideredirect, topmost) | writes `system.focus_area = {...}` directly (bypasses `set_focus_area()`) | click, drag; ESC cancels | overlay destroyed on valid drag (>10 px both) or ESC | view.py:599-600, 694-754 |
| Red Percent | Start Monitoring (starts ENABLED) | `system.start_monitoring()` | click | button states flipped locally `view.py:758-760` | view.py:602-603, 756-760 |
| Red Percent | Stop Monitoring | `system.stop_monitoring()`; if `system.has_unsaved_data` -> askyesno -> `save_log_to_file` -> `system.save_log(path)` | click | `view.py:762-769, 774-780` | view.py:605-606, 762-780 |
| Red Percent | Plot CSV | `model.plot_data.parse_red_percent_csv`, `render_red_percent_figure` (no system call); opens Toplevel with `FigureCanvasTkAgg` | click | plot Toplevel unowned by view | view.py:608-609, 792-829 |
| Red Percent | Probe Name / Tilt entries | `setattr(system, "probe_name"/"probe_tilt_angle", ...)` | every keystroke (StringVar trace) | `view.py:623-635` | view.py:618-635 |
| Red Percent | Reset Baseline (starts DISABLED, enabled on Start, never re-disabled) | `system.reset_baseline()` | click | `view.py:648` | view.py:648-649, 771-772 |
| Red Percent | Sync X/Y/Z `ttk.Checkbutton` (local BooleanVar) | `system.toggle_sync_x/y/z()` | click (BooleanVar flips locally AND model flips; never re-synced) | `view.py:651-662` | view.py:651-662 |
| Red Percent | Position Source combobox | `system.get_available_probe_names()`, `system.selected_probe_name`, `system.set_stepper_model(name)` | populated ONCE at construct (`view.py:673`), on `<<ComboboxSelected>>` | `view.py:676-692` | view.py:668-692 |
| Red Percent | `poll_display` | reads `system.current_red`, `system.red_change`; never reads `system.monitoring` | `after(100)`, guarded by `winfo_exists()` | started `view.py:674`, ends with widget | view.py:782-790 |
| Red Percent | `destroy()` override | `system.stop_monitoring()` | widget destruction (dashboard close, AFTER `shutdown_all` already called `teardown`) | double stop | view.py:831-835 |

### E. Error routing (`ErrorPopupManager`, class-level singleton)

| window/tab | widget | model method or attribute called | trigger | lifecycle | file:line |
|---|---|---|---|---|---|
| global | `ErrorRouter.set_callbacks(report_error, report_warning, report_info)` | `error_routing.ErrorRouter._error_cb` etc. | `initialize()` | set at `app.py:430`, never restored | view.py:19-25; error_routing.py:12-15 |
| global | queue drain `_poll_queue` | `queue.Queue` -> `messagebox.showerror/warning/showinfo(parent=_root)` (modal, up to 5000 chars) | `_root.after(100)` self-rescheduling | started once (`_is_polling` latch) on Setup root; `_root` later REPOINTED to the dashboard (`app.py:423`); loop dies when dashboard is destroyed (Finding 2) | view.py:19-62 |
| global | `sys.excepthook` replacement | `report_error("Unhandled Exception", ...)` | uncaught MAIN-thread exceptions outside Tk callbacks only | `setup_excepthook` once | view.py:93-106 |
| threads | any model thread -> `ErrorRouter.report_*` | dedupe by message for 5 s (`error_routing.py:18-28`) then `queue.put` (thread-safe) | model threads | n/a | view.py:76-91 |

### Requested lifecycle walk-throughs

- **Open device tab**: only at Dashboard construction (`view.py:197-222`). There is no runtime "open device" action in Tk. Devices are chosen and their models built in Setup before the dashboard exists.
- **Close device tab**: middle-click (`<Button-2>`) or right-click menu (`<Button-3>`) -> `close_tab` -> `notebook.forget(index)` (`view.py:158-162`). Frame/view/model are NOT destroyed, model NOT removed from SystemManager, loops 1-4 and the gamepad poller keep running, `tab_metadata` keeps the entry. See Finding 1.
- **Reopen device tab**: does not exist in Tk. A forgotten tab cannot be re-added (no code path calls `notebook.add` after `view.py:202`). The way to "reopen" is close the whole dashboard (full `shutdown_all`) and re-Launch from Setup, which builds brand-new models and a brand-new SystemManager.
- **Poll loop**: four `after` loops per DynamicView (50/50/100/100 ms) plus the poller's 5 ms loop (view.py:512-578, gamepad.py:637), plus Red Percent 100 ms (`view.py:790`), plus ErrorPopupManager 100 ms (`view.py:37`), plus Setup's 50 ms `check_queue` (`app.py:129`). None has try/finally; none is cancelled explicitly (no `after_cancel` anywhere in view.py).
- **Setup flow**: see table A; scan runs on a daemon thread and reports through a queue (thread-safe); launch is synchronous on the Tk thread and blocks during serial handshakes (`controller/serial.py:56-80`: `time.sleep(1.5)` + up to 3 s per device) while the Setup window is already withdrawn.
- **Emergency stop**: FULL STOP label press -> `SystemManager.full_stop_all` -> sequential `emergency_stop()` on the Tk thread (Probes: `power_down` = stop cmd + serial `disable` + `k`, `probes.py:470-477,489-490`; Temperature: `stop()` writes `<0,...>`; Rotator: `smc.stop()` synchronous; RedPercent: `stop_monitoring`). Exceptions per model go to ErrorRouter (`system_manager.py:76-78`).
- **App exit**: only via dashboard X -> `shutdown_all` -> destroy -> Setup reappears. Closing the Setup window itself / process kill has no teardown hook (Finding 11).

## Findings

Order: safety-relevant first. IDs are VIEW-TKINTER-n.

### VIEW-TKINTER-1
- Title: Closing a tab only hides it; the model, poll loops and gamepad routing keep running invisibly; no reopen path; SystemManager.remove_model never used
- Severity: high
- Views affected: Tkinter (design gap); the same gap exists conceptually for any comparator that copies "close = hide"
- Reference behavior: main gave each device its own window whose close handler disabled the stepper, stopped/closed the controller and closed serial (`main:src/stepper_frame.py:675-683`, bound at `:358`). MVC intent: `SystemManager.remove_model` pops + `reboot_model` tears down (`system_manager.py:24-57`).
- Actual behavior: `DraggableClosableNotebook.on_close_tab_callback` is initialised to None (`view.py:120`) and never assigned (grep across src); `close_tab` therefore calls `self.forget(index)` (`view.py:158-162`). The Frame and DynamicView stay alive (only unmapped), so `_poll_model`, `_route_input`, `_poll_pos`, `_poll_stat` and the ControllerPoller `after(5)` loop continue (`view.py:512-578`). Model stays in `SystemManager.active_models`; `tab_metadata` (`view.py:222`) is stale.
- Failure scenario: Operator puts Stepper Probe into Manual mode, then right-clicks the tab and picks "Close Tab" to tidy the screen. The tab vanishes but manual mode is still armed: `_route_input` (`view.py:553-564`) keeps forwarding gamepad axes to the hardware and coils stay energized, with no visible control (only FULL STOP still reaches it). The 5-min interlock does not fire in manual mode either (Finding 4). There is no way to bring the tab back short of closing the whole dashboard.
- Proposed fix direction: set `notebook.on_close_tab_callback` in `DashboardWindow.__init__` to a handler that (1) resolves frame -> `tab_metadata`, (2) cancels/destroys the view (`view.destroy()` and `notebook.forget`), (3) calls `system_manager.remove_model(name)` then `model.teardown()` (or a new `SystemManager.close_model(name)` that does both, matching the "close but might reopen" decision noted in `docs/architecture/ownership-and-lifecycle.md`), (4) `pop`s `tab_metadata` and updates the FocusOut snapshot. If reopen is wanted, add a "+ device" affordance that constructs via `app_bootstrap.build_models` and `register_model`. Decide policy first (docs describe it as an open design call).
- Confidence: verified (grep + view.py read)

### VIEW-TKINTER-2
- Title: ErrorPopupManager root is repointed to the dashboard, so the popup poll loop dies when the dashboard closes and error popups are silently lost on every later launch
- Severity: high
- Views affected: Tkinter
- Reference behavior: popups must keep working for the whole process; `initialize` is meant to be idempotent (`view.py:19-25`).
- Actual behavior: `app.py:430` initialises with the Setup root (`_root=app`, `_is_polling` latched True, first `after` registered). `app.py:423` calls `ErrorPopupManager.initialize(dash)`: `_root=dash`, but `_is_polling` is already True so no new loop starts; the existing loop's next `self._root.after(100, ...)` (`view.py:37`) now registers on `dash`. Tkinter's `Misc.destroy` deletes every Tcl command registered on the widget (`tkinter/__init__.py` `Misc.destroy`, read in the venv's 3.14 install: `for name in self._tclCommands: self.tk.deletecommand(name)`), so when `on_close` calls `self.destroy()` (`view.py:230`) the pending `after` command disappears and the loop is never rescheduled. On the next Launch `initialize(new_dash)` again skips restarting the loop (`_is_polling` True). `_root` is also a dead widget between sessions.
- Failure scenario: Session 1 works. Operator closes the dashboard, returns to Setup, launches again. Serial write failures, "Controller Disconnected", "Enable Failed", rotation errors etc. are `queue.put`-ed (`view.py:86-91`) and never displayed; `ErrorRouter.report_*` does not print when a callback is registered (`error_routing.py:33-36`), so faults are invisible except for whatever the reporter also `print`ed.
- Proposed fix direction: keep `_root` pointing at the process-lifetime Setup root (do not re-`initialize(dash)`; if a per-window parent is wanted keep a separate `_parent` used only for `parent=` and fall back to the root when `winfo_exists()` is false), and make `_poll_queue` always reschedule from the permanent root; wrap `_display_popup` in try/except so one bad popup cannot end the loop. Add a regression test that constructs/destroys two dashboards.
- Confidence: verified (code path) / hypothesis for the exact stderr symptom of the orphaned `after` timer

### VIEW-TKINTER-3
- Title: Auto-disable interlock never fires in manual mode or after "Start Stepping", contradicting main's 5-minute idle disable; Tk comment claims parity
- Severity: high
- Views affected: Model (BaseProbe) -> all views; Tkinter comment at `view.py:545-548` relies on it
- Reference behavior: main armed `disable_timer_id = after(300000, auto_disable)` on enable (`main:src/stepper_frame.py:519-520`), reset it on activity (`:524-527`, controller `activity_callback=reset_disable_timer` at `:635`), and `auto_disable` called `enable_button()` which disables regardless of mode (`:529-533`).
- Actual behavior: `_watch` skips the timeout check entirely `if self.is_stepping or self.manual_flag: continue` (`model/probes.py:409-412`). `is_stepping` is set True by `macro_start_auton` (`:250`) and only cleared by `_stop_and_disarm` (`:445`), so after one "Start Stepping" click the probe can never idle-time-out. Manual mode is excluded outright. Since the Tk schema no longer has an Enable button (`probes.py:127-130`), every enable happens via `enter_auton/enter_manual/macro_start_auton`, i.e. exactly the cases that defer or skip the timeout; only "entered auton, never clicked Start" can time out.
- Failure scenario: Operator enters Manual mode, walks away. Steppers stay energized (TMC2209 toff=4) indefinitely, unlike main where they were cut after 5 min.
- Proposed fix direction: have the watchdog measure `time.time() - last_activity_time` in all modes; treat only real controller/command activity (`touch_activity`) as activity; drop `is_stepping`/`manual_flag` deferral, or make autonomous-motion duration extend `last_activity_time` explicitly (e.g. script thread calls `touch_activity`). Add a unit test with `_INTERLOCK_TIMEOUT` shrunk (hooks exist at `probes.py:16-17`).
- Confidence: verified

### VIEW-TKINTER-4
- Title: Controller loss / controller set to None leaves the system energized (main did full stop + disable)
- Severity: medium
- Views affected: Model (BaseProbe) -> all views; Tkinter exposes it via the Controller ID dropdown and the poller
- Reference behavior: main: selecting "None"/failed swap while in manual calls `full_stop_button()` (`main:src/stepper_frame.py:405-418`); losing the controller inside `_manual_mode_loop` runs `full_stop_button()` then `enable_button()` (toggle -> `serial.disable()`) (`:581-600`).
- Actual behavior: `BaseProbe.send_manual_mode_command` only sets `self.manual_flag = False` and warns when `poller.gamepad` is missing (`probes.py:359-364`); it does not call `full_stop()`/`disable()`, so `system_enabled` stays True and coils energized. `set_controller` (`probes.py:182-192`) sets `controller_var` and swaps the poller without any stop. `ControllerPoller._handle_disconnect` (`gamepad.py:315-323`) only nulls the gamepad and stops polling.
- Failure scenario: Gamepad battery dies mid-jog: manual_flag flips False, one neutral packet is sent by `_route_input`, motors stop but stay enabled/holding until the operator notices (and the watchdog does not count in manual mode, see Finding 3).
- Proposed fix direction: in `send_manual_mode_command`'s no-gamepad branch call `self.full_stop()` (and `report_warning`), and in `set_controller` call `full_stop()` if `manual_flag`. Mirror main's disable-on-controller-loss.
- Confidence: verified

### VIEW-TKINTER-5
- Title: Manual-mode pump lives in the view, has no error guard, and a dead loop leaves the last manual velocity active in firmware
- Severity: medium
- Views affected: Tkinter (and every view that copies it, e.g. PySide `view.py:172-183`)
- Reference behavior: main's `_manual_mode_loop` (`main:src/stepper_frame.py:581-608`) wrapped the send in try/except and logged; it exited only when the flag cleared or the joystick vanished (with full stop).
- Actual behavior: `_route_input` (`view.py:553-564`) has no try/finally; if `poller.get_mapped_state()` or `send_manual_mode_command` raises anything unexpected (`get_mapped_state` only catches `pygame.error`, `gamepad.py:523-527`), the `self.after(50, _route_input)` at `:563` is never reached and Tk merely prints "Exception in Tkinter callback" to stderr (`tkinter.Tk.report_callback_exception`; `sys.excepthook` is NOT invoked for Tk callbacks, so `setup_excepthook` at `view.py:93-106` does not help; there is also no `threading.excepthook` hook in the Tk path). Firmware retains the last manual packet values (see main's "FIX #5" comment `main:src/stepper_frame.py:479`), and `manual_flag` stays True.
- Failure scenario: A one-off exception (e.g. `KeyError` in a gamepad wrapper) kills the pump while the stick is deflected: stage keeps moving at last velocity with the UI still saying MANUAL MODE and no popup.
- Proposed fix direction: move the pump into the model/controller (one place; e.g. `BaseProbe.tick()` called by each view's timer) so views do not duplicate it; wrap the body in try/except that calls `full_stop()` + `ErrorRouter.report_error` and always reschedules in `finally`. Same guard for `_poll_model`, `_poll_pos`, `_poll_stat`.
- Confidence: verified (code), hypothesis for the trigger frequency

### VIEW-TKINTER-6
- Title: Stale controller claims across launches: `SetupWindow.active_claims` is never cleared, causing false "Controller Claim Conflict" on re-launch
- Severity: medium
- Views affected: Tkinter (`app.py:100,402`); PySide has the same construct at `app.py:521,731`; Web builds its own
- Reference behavior: main cleared claims at launch: `self.active_claims.clear()` (`main:src/mainGUI.py:336`).
- Actual behavior: `self.active_claims = {}` once in `SetupWindow.__init__` (`app.py:100`); the same dict is passed to every `build_models` (`app.py:402`). `ControllerPoller` writes `active_claims[process_name] = controllerID` (`gamepad.py:443`) and nothing removes it on `teardown/close` (`gamepad.py:494-513`). Later pollers reject a controller claimed by any OTHER process name (`gamepad.py:398-415`).
- Failure scenario: Session 1: Stepper on "ID 0", Chuck on "ID 1". Close dashboard. Session 2: swap them (Stepper "ID 1", Chuck "ID 0"). Stepper is built first, sees stale `ChuckPositioner: ID 1`, warns "Controller collision", `gamepad=None`; manual mode is blocked ("no gamepad attached") until the operator reselects in the dropdown.
- Proposed fix direction: `self.active_claims.clear()` at the top of `launch_unified` (as main), and have `ControllerPoller.close()` pop its own entry.
- Confidence: verified (code); scenario is derivation from the same code

### VIEW-TKINTER-7
- Title: Launch flow withdraws Setup before building models and has no error handling; any exception leaves live hardware models and an invisible app
- Severity: medium
- Views affected: Tkinter
- Reference behavior: main only withdrew after spawning processes, and each process owned its own failure.
- Actual behavior: `self.withdraw()` (`app.py:397`) precedes `build_models` (`:402`), the Red Percent wiring (`:404-412`), `DashboardWindow(...)` (`:422`), with no try/except. Model constructors block the Tk thread (serial handshake sleeps in `controller/serial.py:63-66`, `RotatorSystem.connect` at `rotator_system.py:30`). If a later constructor or a view constructor raises (e.g. `getattr(self.system, "toggle_sync_x")` at `view.py:661` or an entry whose initial attr is None, `float(val)` at `view.py:337` only catches ValueError), earlier models (open serial ports, gamepad pollers) are orphaned with no teardown and the Setup window stays hidden.
- Failure scenario: One device's model raises during launch: the operator sees no window at all (Setup hidden, no dashboard), Stepper's serial port stays open and the process must be killed.
- Proposed fix direction: build models before `withdraw()`, wrap build+dashboard creation in try/except that calls `system_manager.shutdown_all()` on partial state, `deiconify()`s Setup and reports via `ErrorPopupManager`; optionally move construction to a worker thread with a progress state (Setup already has a progress bar). Also delete dead `assigned_ports/assigned_controllers` locals (`app.py:364-384`, unused since `validate_assignment`).
- Confidence: verified (code); trigger is hypothetical

### VIEW-TKINTER-8
- Title: No exit-time teardown fallback (Setup close / Cmd-Q / Ctrl-C bypass `shutdown_all`)
- Severity: medium
- Views affected: Tkinter
- Reference behavior: main's per-device `WM_DELETE_WINDOW` disabled the system and closed serial (`main:src/stepper_frame.py:358,675-683`).
- Actual behavior: the only teardown trigger is the dashboard's `WM_DELETE_WINDOW` (`view.py:224`). Setup has no protocol handler, and `app.py` has no `atexit`/`signal`/`finally` (grep: none). If the process is terminated another way (Ctrl-C in the terminal, macOS Cmd-Q on the Tk menu, root destroyed) `shutdown_all` never runs; firmware `system_enabled` persists (see comment at `probes.py:447-456`).
- Failure scenario: Operator hits Cmd-Q with steppers enabled: coils remain energized after the app is gone.
- Proposed fix direction: hold `system_manager` on the Setup root; register `app.protocol("WM_DELETE_WINDOW", ...)`, `atexit.register(system_manager.shutdown_all)` and a `try/finally` around `app.mainloop()` in `run_legacy_app`. Make `shutdown_all` idempotent (it already clears the dict).
- Confidence: hypothesis for the macOS Cmd-Q path (needs a manual run); verified that no hook exists

### VIEW-TKINTER-9
- Title: `flush_neutral` on window FocusOut is defeated by the 5 ms poll loop and uses a stale model snapshot
- Severity: low
- Views affected: Tkinter (PySide mirrors intent via `changeEvent`)
- Reference behavior: main had no such hook; intent (docs `views.md`) is "neutralize gamepad state when the window loses focus".
- Actual behavior: `on_focus_out` calls `poller.flush_neutral()` for the `active_models` captured at construct (`view.py:172,176-182`). `flush_neutral` zeroes `prev_axis_states` (`gamepad.py:557-571`), but `_poll_loop` overwrites every axis from the live joystick every 5 ms (`gamepad.py:593-607`), and background events are explicitly enabled (`SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS`, `gamepad.py:3`). Net effect: at most a 5 ms neutral blip. After any future tab removal the snapshot still references removed models.
- Failure scenario: Operator alt-tabs mid-jog expecting motion to stop; stick input keeps driving the stage.
- Proposed fix direction: decide the intended semantic (stop or ignore input while unfocused). If "ignore": add a `poller.input_suppressed` flag honoured in `get_mapped_state`; iterate `self.system_manager.get_active_models_snapshot()` at event time instead of the captured dict.
- Confidence: verified

### VIEW-TKINTER-10
- Title: Controller dropdown never re-synced, lacks "None", no claim filtering, no live hot-plug refresh, and shows a controller that failed to bind
- Severity: medium
- Views affected: Tkinter (model quirk visible to all)
- Reference behavior: main refreshed the option list every 500 ms with `["None"] + hardware controllers not claimed by another process` and set the combobox to the current claim (`main:src/stepper_frame.py:364-402`); selecting None released the claim and full-stopped (`:405-418`).
- Actual behavior: options built once from `model.get_available_controllers()` (physical only) with the current value prepended (`view.py:421-425`); only the manual "⟳" button refreshes (`view.py:440-453`), and a refresh drops "None" from the list. The Combobox StringVar is local (`view.py:427`), not in `self.vars`, so `_poll_model` never re-syncs it. `BaseProbe.set_controller` sets `controller_var` before knowing whether the poller bound (`probes.py:182-186`), and `ControllerPoller` marks failure by `gamepad=None` + `active_claims[name]="None Detected"` (`gamepad.py:409-415`).
- Failure scenario: operator picks a controller already claimed by DC Probe; popup warns, but the combobox keeps showing the chosen ID; manual mode then says "no gamepad". After unplugging a pad the combobox still shows it. Cannot go back to "None" after a refresh.
- Proposed fix direction: include "None" in `get_available_controllers`, filter by other claims (main logic), have the model expose `controller_var` as the actual bound id (set from poller result), register the combobox var in `self.vars` (guard against overwriting while the popdown is open), and add a 500 ms refresh tick.
- Confidence: verified

### VIEW-TKINTER-11
- Title: Manual command rate is 50 ms vs main's 5 ms; edge events (D-pad, bumpers) can be dropped between ticks
- Severity: low
- Views affected: Tkinter (PySide copy)
- Reference behavior: `self.root.after(5, self._manual_mode_loop)` (`main:src/stepper_frame.py:608`), edges read via `get_hat_edge/get_button_edge` each 5 ms.
- Actual behavior: `_route_input` reschedules at 50 ms (`view.py:563`) and calls `poller.get_mapped_state()`, whose edge latch (`gamepad.py:538-553`) only sees state at call time. A press+release shorter than 50 ms produces no step.
- Failure scenario: quick tap on a D-pad/bumper step does nothing intermittently.
- Proposed fix direction: run the pump at 5-10 ms (as main) or have the poller latch edges at poll time (5 ms) and consume them on read.
- Confidence: verified (rates); user-visible impact hypothesis

### VIEW-TKINTER-12
- Title: Hardware serial I/O on the Tk thread at 100 ms cadence (rotator status at 5x main's rate)
- Severity: low
- Views affected: Tkinter
- Reference behavior: main rotator polled status every 500 ms (`main:src/rotator.py:251`); stepper position every 50 ms (`main:src/stepper_frame.py:444`) reading a buffer without blocking.
- Actual behavior: `poll_status` (two SMC100 queries, `rotator_system.py:267-281`) runs from `after(100)` on the UI thread (`view.py:574-578`); a slow/timeouting SMC100 blocks the UI proportionally, and concurrent `_run_async` moves (`rotator_system.py:56-60`) use the same port. Position poll is 100 ms vs 50 ms (`view.py:568-571`).
- Failure scenario: rotator serial timeouts freeze the whole dashboard including the FULL STOP label.
- Proposed fix direction: run status polling on a worker thread that updates locked fields (`RotatorSystem` already has `_lock`-protected properties) and keep the Tk loop read-only; restore 500 ms for the rotator.
- Confidence: hypothesis (depends on smc100 timeout behavior; not read)

### VIEW-TKINTER-13
- Title: Full-stop of Manual mode is followed by a neutral MANUAL packet that re-arms firmware MANUAL_ON while disabled
- Severity: low
- Views affected: Tkinter (`_route_input`), model ordering
- Reference behavior: main's `full_stop_button` just sent the stop and stopped polling (`main:src/stepper_frame.py:552-566`); no follow-up manual packet.
- Actual behavior: `full_stop`/`emergency_stop` set `manual_flag=False` then send stop + `disable()` (`probes.py:442-465`). On the next tick `_route_input` sees `_prev_manual_flag` True and sends `send_manual_mode_command({})` (`view.py:559-562`), a mode-1 binary packet (`serial.py:209-227`). Firmware's mode-1 handler sets `MANUAL_ON = true` when it was off (`firmware/stepper_firmware/stepper_firmware.ino:295-300`) even though `system_enabled` was just set false by 'd' (`:333`).
- Failure scenario: after FULL STOP the firmware sits in MANUAL_ON with zero velocity and drivers disabled; the next `enable` energizes drivers while still in MANUAL mode until an auton text command clears it. (Effect on next enable is a hypothesis; the stale MANUAL_ON is verified.)
- Proposed fix direction: only send the neutral packet if `system_enabled` is still True, or have `full_stop` itself send the neutral manual packet before `disable()` and clear the view-side edge flag.
- Confidence: verified (sequence); hypothesis (downstream effect)

### VIEW-TKINTER-14
- Title: RedPercentView: Start enabled without a focus area; button state and Sync checkboxes never re-synced with the model; FULL STOP / dashboard close skip the save prompt
- Severity: low
- Views affected: Tkinter
- Reference behavior: main's Start button was `state=tk.DISABLED` until a focus area was chosen (`main:src/color_test_new.py:63-65,164`); a Save Log button was always present (`:94-96`).
- Actual behavior: `start_btn` created enabled (`view.py:602`); starting with `focus_area=None` makes the monitor thread loop with `capture_focus_area` returning None (`redpercent_system.py:97-100`). The view flips button states locally (`view.py:756-765`) and `poll_display` never reads `system.monitoring` (`view.py:782-790`), so `emergency_stop`/`teardown` (`redpercent_system.py:272-280`) leave "Stop" enabled/"Start" disabled. Sync BooleanVars flip on their own (`view.py:651-662`) and are never refreshed. The save-on-stop prompt exists only in `RedPercentView.stop_monitoring` (`view.py:767-769`); dashboard close (`view.py:226-231`) and FULL STOP discard `data_log` silently. Probe dropdown is populated once (`view.py:673`). `select_focus_area` assigns `system.focus_area` directly instead of `set_focus_area()` (`view.py:738`, `redpercent_system.py:86-89`).
- Failure scenario: operator clicks Start before choosing an area (nothing measured, UI says monitoring); or presses global FULL STOP while monitoring, then cannot press Start until pressing Stop; or closes the dashboard mid-run and loses the log.
- Proposed fix direction: `start_btn` initial DISABLED; poll `system.monitoring` in `poll_display` to set button states; ask-to-save from a shared model hook (e.g. `has_unsaved_data` check in `DashboardWindow.on_close` before `shutdown_all`); use `system.set_focus_area(...)`.
- Confidence: verified

### VIEW-TKINTER-15
- Title: RedPercentSystem data log dimensions frozen at first Start; toggling Sync after that raises KeyError in the monitor thread or silently drops columns
- Severity: medium
- Views affected: Model (visible in Tkinter because the Sync checkboxes stay live while monitoring)
- Reference behavior: main logged plain red values only.
- Actual behavior: `start_monitoring` builds `RedPercentDataLog(self.sync_dimensions, ...)` only if `data_log` is None (`redpercent_system.py:266-267`). `RedPercentDataLog.__init__` does `self.sync_dimensions = sync_dimensions or []` and builds `loc_values/vel_values` only for those dims (`:16-21`). If the list was non-empty it is the SAME list object as the system's; toggling another dim later appends to it (`:130-133`) so `add_entry` does `self.loc_values[dim]` for a missing key -> `KeyError` in the unguarded `_monitor_colors` thread (`:286-326`), which dies silently while `monitoring` stays True. If the list was empty, `[] or []` makes a private empty list, so later toggles never reach the CSV.
- Failure scenario: Start with X synced, later tick Y: monitoring appears active, red % display stops updating, log stops growing.
- Proposed fix direction: copy dims (`list(sync_dimensions)`) and lazily create per-dim lists (`defaultdict(list)`) in `add_entry`, or lock dims at Start and disable the checkbuttons while monitoring; wrap the thread body in try/except -> `ErrorRouter.report_error`; also reset `data_log` per session.
- Confidence: verified

### VIEW-TKINTER-16
- Title: RotatorSystem.teardown disconnects without stopping motion
- Severity: medium
- Views affected: Model (Tk on_close relies on it)
- Reference behavior: ManagedModel docstring: teardown = "stop all background activity" (`model/base.py:12-16`); emergency_stop is the strongest halt (`base.py:18-22`).
- Actual behavior: `RotatorSystem.teardown()` -> `disconnect()` (`rotator_system.py:108-123`): closes the serial port, never calls `stop()`. `SystemManager.shutdown_all` only calls `teardown` (`system_manager.py:59-68`). Moves run in daemon threads (`_run_async`, `:56-60`).
- Failure scenario: dashboard closed during a 30-degree move: port closed mid-move, the SMC100 completes the move autonomously; `_run_async` thread may throw into `error_callback`/ErrorRouter after teardown.
- Proposed fix direction: call `self.stop()` (guarded) before `disconnect()` in `teardown`; or have `shutdown_all` call `emergency_stop()` before `teardown()` for every model.
- Confidence: verified (code); SMC100 continuing motion is standard controller behavior but unverified here

### VIEW-TKINTER-17
- Title: View knows model internals and hard-coded names (MVC strain), dead/misleading widgets
- Severity: low
- Views affected: Tkinter (and every copier)
- Reference behavior: schema-driven contract (`ui_schema` + command names) should be the only coupling.
- Actual behavior: hard-coded `"SMC100 Rotator"` (`view.py:198`), `"Red Percent Window"` (`view.py:205`), `attr != "serial_port"` (`view.py:335`), `"open_controller_log"` intercepted in the view (`view.py:493`) while the model method is a `print` stub (`probes.py:194-195`, so other views' button does nothing), direct `model.poller.*` control (`view.py:179,544-564`), view-side injection of `model.confirm_rotation_callback` (`view.py:199`) and of `available_probes` in `app.py:404-412` (must be duplicated by other views: `app.py:737`, `pyside/view.py:904`, `web_adapter.py:170`). The `serial_port` entry (`probes.py:111`) writes to `model.serial_port` with nothing consuming it (`reconnect_serial` has no UI caller, `probes.py:148-154`), so editing it is a dead control. `RotatorSystem.position` displays as raw `str(None)`/unformatted float (`view.py:325,525`) vs main `f"{pos:.4f} deg"` (`main:src/rotator.py:~245`). `float(val)` at `view.py:337` would raise TypeError for a None initial attr.
- Failure scenario: cross-view drift (each view re-implements the same glue differently); operator edits Serial Port expecting a reconnect.
- Proposed fix direction: move claim/probe wiring into `app_bootstrap` (one function all views call), give models `open_controller_log` behavior via a callback slot, drop or disable the `serial_port` entry until a reconnect UI exists, format rotator position in the model.
- Confidence: verified

### VIEW-TKINTER-18
- Title: Minor Tk behaviors: right/middle-click mapping on macOS, log-window handling, stdout spam
- Severity: low
- Views affected: Tkinter
- Reference behavior: main opened the log window only with a controller present (`main:src/stepper_frame.py:236-241`) and closed it on entering auton mode / full stop (`:473-476,562`); the log updater targeted only the window.
- Actual behavior: `<Button-2>` closes a tab immediately with no confirmation (`view.py:116,142-147`); on macOS Aqua Button-2 is the RIGHT button (hypothesis, Tk docs), so a right-click intended to open the menu closes the tab (compounds Finding 1). `start_polling` passes `log_updater=print` (`view.py:550`) so every axis/button change prints to stdout at up to 200 Hz even with no log window. Log window opens without a controller and is never closed on full stop/auton (`view.py:493-503`). `ControllerLogWindow` is Toplevel of the view frame.
- Failure scenario: accidental tab close on Mac; noisy stdout hides real errors.
- Proposed fix direction: bind close to Button-3 on Linux/Windows and Button-2 on `aqua` (or use a confirm dialog); default `log_updater=None`; close the log window on full stop/auton as main.
- Confidence: hypothesis (macOS button mapping); verified for the rest

## Doc corrections (docs/architecture)

- `docs/architecture/ownership-and-lifecycle.md:121-123` says the Tkinter flow "constructs a fresh model instance from scratch when a device tab is reopened". False: Tk has no reopen flow; closing a tab is `notebook.forget` (`view.py:158-162`) and the model is never removed or reconstructed. Only PySide6 has open/close dock construction.
- `docs/architecture/views.md:187` describes the manual neutral-send as a "(20ms equivalent)"; the actual interval is `after(50)` (`view.py:563`).
- `docs/architecture/views.md` "Focus-out behavior" row presents Tk `<FocusOut>` flushing as functionally equivalent to PySide's; the flush is overwritten within 5 ms (Finding 9).
- `docs/architecture/views.md:188` (Tkinter `_poll_model` "if the active widget does not hold focus"): guard applies only to Entry widgets in `self.entries` (`view.py:516`), not the combobox or toggles.

## Notes for comparators (PySide6 / Web) drawn from the Tk reference

- Must set `RotatorSystem.confirm_rotation_callback` or moves beyond +-30 degrees are silently blocked (`rotator_system.py:212-215`); Tk sets it at `view.py:198-199`, PySide at `pyside/view.py:922` (not audited here).
- The Tk numeric commit contract: value committed to model on FocusOut/Return only, with the pre-command `focus_set()` flush (`view.py:492`); text entries write through on every keystroke.
- Tk starts the gamepad poller at construct time for every tab with a poller (`view.py:544-550`), not on entering manual mode as main did.
- Tk error routing only works if the popup poll loop outlives dashboard rebuilds (Finding 2), the equivalent hazard applies to any `initialize(...)` that re-parents a global router.
- `SystemManager.remove_model/reboot_model` have no Tk caller, so they are only exercised by PySide's dock flow.

## Coverage

Read in full: `src/views/tkinter/view.py` (1-835); `src/app.py` 1-520 and 736-830 (Tk setup and launch path fully; PySide section only skimmed for claims/available_probes); `src/app_bootstrap.py` 1-195; `src/model/system_manager.py`, `src/model/base.py`, `src/error_routing.py` (full); `src/model/probes.py` (full); `src/model/temperature_system.py`, `rotator_system.py`, `redpercent_system.py` (full); `src/controller/gamepad.py` 1-640; `src/controller/serial.py` 1-260; `src/model/numeric.py`; `firmware/stepper_firmware/stepper_firmware.ino` 280-420 plus greps; Python `tkinter/__init__.py` `Misc.focus_get/nametowidget/destroy/after`, `Tk.report_callback_exception` (venv Python 3.14.7). main: `mainGUI.py` (full), `stepper_frame.py` (full), `chuck_frame.py` via diff vs stepper (only default step sizes/title differ), `color_test_new.py` (full), `rotator.py` lines 205-262 + grep, `temp_control.py` 1-30 and 74-170. Docs: `views.md` (Tk sections), `ownership-and-lifecycle.md` 110-175, `known-issues.md`/`error-routing.md` (grep only).

Not done: no GUI was run (all behavior is from reading; macOS Button-2/3 mapping and orphan-`after` stderr symptom are unconfirmed). `controller/gamepad.py` 640+ and `controller/serial.py` 260+ not read. `lib/smc100.py`, `model/plot_data.py` not read (Finding 12 and plot path unverified). PySide6 and Web views intentionally not audited. `docs/architecture/*.md` other than the sections above were only grepped. Python versions other than 3.14 were not checked: on older Pythons `Misc.focus_get()` (called at `view.py:514`) raises `KeyError: 'popdown'` while a Combobox popdown is open, which would kill `_poll_model`; 3.14 handles it (returns None), so this is dropped as a finding but is a compatibility risk if the lab runs an older interpreter.
