# MANAGER audit: SystemManager, launch paths, exit/teardown/E-stop wiring, setup flow

> **Input (banner added 2026-09-23).** Audit of the old tree (now `legacy/src/`); current until the rebuild replaced it on 2026-09-23. Kept because the ledger in `docs/implementation/progress.md` and `docs/rebuild/carry.json` cite these finding IDs.

All paths relative to /Users/ianalbinogonzalez/Documents/GitHub/transfer-stage-unified/mvc-refactor unless noted.

## Object summary

**SystemManager** (`src/model/system_manager.py`, 78 lines): dict `active_models` guarded by a `threading.Lock`. Primitives: `register_model` (L10, overwrites silently, no teardown of any previous entry), `get_model` (L14), `remove_model` (L24, pops but does NOT tear down), `get_active_models_snapshot` (L29), `reboot_model` (L34-57), `shutdown_all` (L59-68, clears dict then calls `teardown()` per model, errors routed per model), `full_stop_all` (L70-78, snapshot then `emergency_stop()` per model, errors routed per model). `_teardown_model` (L18-22) silently skips models with no `teardown` attribute (`hasattr`), and `full_stop_all` silently skips models with no `emergency_stop` (L74). `ManagedModel` Protocol (`src/model/base.py:4-22`) is never referenced anywhere in `src` (grep `ManagedModel` -> only its own definition): the contract is documentation only, no isinstance/registration check.

**Constructor sites / owners (one manager per launch):**
- Tkinter: `src/app.py:418` `SystemManager()` inside `SetupWindow.launch_unified` (app.py:359-426) -> `DashboardWindow(self, system_manager)` (app.py:422). Owner = `DashboardWindow` (`src/views/tkinter/view.py:169`). Teardown = `DashboardWindow.on_close` (view.py:226-231): `shutdown_all()`, `destroy()`, `master.deiconify()`.
- PySide6: `src/app.py:746` `self.manager = SystemManager()` in `SetupWindow.launch_unified` (app.py:693-753) -> `DashboardWindow(self.manager)` (app.py:750). Teardown = `DashboardWindow.closeEvent` (`src/views/pyside/view.py:957-967`): per-dock `widget.cleanup()` (stops QTimers, `poller.stop_polling()`, `poller.close()`; view.py:437-453) then `shutdown_all()` then `event.accept()`.
- Web: `src/app.py:774` `manager = SystemManager()` (EMPTY) -> `WebDashboardWindow(manager, ...)` (app.py:780; web_view.py:46-65) -> `WebDashboardServer` -> `WebModelAdapter(manager)` (web_server.py:311). Models are built later at `POST /api/setup/initialize` (web_adapter.py:78-192), which builds a NEW `SystemManager` (web_adapter.py:144) and swaps it into the adapter only (web_adapter.py:176-179). Teardown = `WebDashboardWindow.close` (web_view.py:74-78), reachable only from the `KeyboardInterrupt`/generic `Exception` handlers in `run_web_app` (app.py:802-809).

**Q1 launch path per view** (verified `src/app.py:824-914`):
- `--tkinter`/`--view legacy` -> `launch_legacy()` -> `run_legacy_app()` (app.py:41-432): `SetupWindow(tk.Tk)` (autodetect scan thread via `app_bootstrap.probe_device_at`), user checks devices, `validate_assignment`, **`self.withdraw()` (L397)**, `build_models` (L402, only checked devices), Red% probe linking (L404-412), `SystemManager`, `DashboardWindow(Toplevel)`, `ErrorPopupManager.initialize(dash)` (L423). One tab per model; `DynamicView`/`RedPercentView`; views call `poller.start_polling(dashboard_window)` (view.py:542-550).
- `--pyside` -> `launch_pyside()` -> `run_pyside_app()` (app.py:436-763): `QApplication`, `QtErrorPopupManager`, `SetupWindow(QMainWindow)` with `ScannerThread(QThread)`, `build_models` (L731), Red% linking (L733-741), `SystemManager`, `DashboardWindow(manager)`; `dashboard.show()`; `self.close()` (setup window). Sidebar lists all 6 devices; unchecked ones are constructed on demand (view.py:881-919).
- `--web`/`--view web` -> `run_web_app(port, open_browser)` (app.py:766-809): empty manager, `WebDashboardWindow.show()` starts a daemon-thread `ThreadingHTTPServer` on 127.0.0.1 (web_server.py:319-335), sets `sys.excepthook` + `threading.excepthook`, main thread `while True: sleep(1)`.
- **Q5 default-view logic: the docs claim is CORRECT.** app.py:889-901: `selected_view is None` -> `sys.platform == "darwin"` -> web; else `try: from PySide6.QtWidgets import QApplication` -> pyside, `ImportError` -> web. `run.sh` (4 lines) hard-codes `python3 src/app.py "--tkinter"` (so `run.sh` never uses the default logic). See MANAGER-14 for argparse quirks.
- main baseline: `git show main:src/mainGUI.py` L295-367: `launch_modules` spawns one `multiprocessing.Process` per device (each its own Tk root with per-window `WM_DELETE_WINDOW` -> `_on_closing`), then a thread joins them (L364-367, `close` at L369-376 joins and calls a nonexistent `self.manager.shutdown()` inside `try/except`). There was no global manager and no global FULL STOP; per-device `full_stop_button` (`stepper_frame.py:552-566`) and per-window close (`stepper_frame.py:675-683`: `serial.disable()` if enabled -> `_running=False` -> `controller.stop_polling()` -> `controller.close()` -> `serial.close()` -> `gui._on_closing()` -> `root.destroy()`).

**Q2 exit-path matrix (what is called, in what order):**

| Path | Tkinter | PySide6 | Web |
|---|---|---|---|
| Main window X | `on_close`: `shutdown_all` (poller stop + `power_down` + serial close inside `BaseProbe.teardown`, probes.py:479-487) -> `destroy` -> `deiconify` setup (view.py:226-231). Views' `after` loops and view `destroy()` come after teardown (RedPercentView.destroy calls `stop_monitoring` again, view.py:831-834). | view `cleanup()` per dock (timers stop, poller stop+close) THEN `shutdown_all` (view.py:957-967). Best ordering of the three. | No X path exists: closing the browser tab does nothing (no `beforeunload`/`pagehide`/`sendBeacon` in app.js or index.html; grep confirmed). |
| Ctrl-C | `KeyboardInterrupt` in `mainloop`, no handler; no teardown | Qt swallows SIGINT, no handler; no teardown | `dashboard.close()` -> `server.stop()` -> `shutdown_all()` on the STALE manager (MANAGER-1) |
| SIGTERM / kill / terminal close / Cmd-Q | none (grep for atexit, signal, SIGINT, SIGTERM, aboutToQuit in src -> zero hits) | none | none |
| Tab/dock close | `forget(index)` only, no teardown (MANAGER-9) | `close_device_view` hand-rolled partial teardown (MANAGER-7) | no per-device close |

What is left running after each path is enumerated per finding below (heater, rotator, coils, serial ports, daemon threads, HTTP server).

**Q3 E-stop wiring (all three call `SystemManager.full_stop_all` on the live manager; none hand-roll):**
- Tk: `tk.Label` bound to `<Button-1>` -> `lambda e: self.system_manager.full_stop_all()` (view.py:186-191), GUI thread, bottom-docked.
- PySide: `stop_btn.clicked.connect(self.system_manager.full_stop_all)` (view.py:756-758), GUI thread. Bound method of the manager object; the PySide manager never changes, so no stale-binding issue.
- Web: `POST /api/system/full_stop` (web_server.py:222-225) -> `WebModelAdapter.full_stop_all` (web_adapter.py:420-431) -> `self.system_manager.full_stop_all()` using the adapter's CURRENT manager (correct after swap). Runs on an HTTP worker thread and deliberately takes no device lock (good: can preempt an in-flight `dispatch_command`).
- vs main: main had per-device Full Stop (stop polling + send zero autonomous command). Refactor: global E-stop -> `emergency_stop()` per model: probes -> `power_down()` (`_stop_and_disarm` + 'k' kill coils; probes.py:462-489); temperature -> `stop()` (setpoint 0 written, serial stays open; temperature_system.py:158-176, 201-202); rotator -> `stop()`; red% -> `stop_monitoring()`. Consistent across views (the wiring is the same three call sites). All five model classes implement both `teardown` and `emergency_stop` (grep: probes.py:479/489, rotator_system.py:122/125, redpercent_system.py:276/279, temperature_system.py:198/201; `BaseProbe` covers Stepper/DC/Chuck).

---

## Findings

### MANAGER-1
- **Title:** Web `WebDashboardWindow.close()` shuts down the original EMPTY manager, so the real models are never torn down on Ctrl-C
- **Severity:** high
- **Views affected:** Web
- **Reference behavior:** Tkinter `DashboardWindow.on_close` calls `shutdown_all()` on the same manager that holds the live models (view.py:226-231, manager passed at construction app.py:418-422). main: each device's own `_on_closing` disabled+closed its own serial (`stepper_frame.py:675-683`).
- **Actual behavior:** `run_web_app` builds an empty `SystemManager` (app.py:774) and stores it in `WebDashboardWindow.system_manager` (web_view.py:47). `initialize_setup` creates a different `SystemManager` (web_adapter.py:144) and installs it only into the adapter (web_adapter.py:176-179: `self.system_manager = new_manager`). `WebDashboardWindow.close` uses its own stale attribute: `self.system_manager.shutdown_all()` (web_view.py:77-78) on the empty original. `WebDashboardServer.system_manager` (web_server.py:313) is stale too. Nothing ever copies the new manager back.
- **Failure scenario:** Operator sets up stepper + temperature controller in the browser, heater at 150 C, presses Ctrl-C in the terminal. `KeyboardInterrupt` -> `dashboard.close()` -> server stops -> `shutdown_all()` iterates zero models. No `teardown()` runs: no `power_down` 'k', no temperature setpoint-0 write, no serial close, no poller close. Process exits; the Arduino keeps its last state (heater setpoint / coils energized per firmware behaviour).
- **Proposed fix direction:** Make the adapter the single owner of the current manager: `WebDashboardWindow.close()` should call `self.server.adapter.system_manager.shutdown_all()` (read the manager at close time, under `adapter._state_lock`), or have `initialize_setup` mutate the existing manager (register into it) instead of swapping. Remove the duplicated `system_manager` attributes on `WebDashboardWindow` and `WebDashboardServer` (make them properties reading the adapter). Add a test that calls `initialize_setup` then `WebDashboardWindow.close()` and asserts model `teardown` was called.
- **Confidence:** verified

### MANAGER-2
- **Title:** Web has no teardown path other than Ctrl-C: browser close, SIGTERM, terminal close, and any other exit leave hardware live
- **Severity:** high
- **Views affected:** Web
- **Reference behavior:** Tk/PySide tie hardware teardown to closing the dashboard window (view.py:226-231; pyside view.py:957-967). main tied it to each window's close.
- **Actual behavior:** `run_web_app` (app.py:798-809) only handles `KeyboardInterrupt` and a bare `Exception` around a `sleep(1)` loop. No `signal.signal`, `atexit`, or `try/finally` anywhere in `src` (grep). `WebDashboardServer` threads are daemons (web_server.py:296-298, 332), so on process exit nothing shuts models down. Front end has no `beforeunload`/`sendBeacon`/heartbeat (grep on app.js/index.html for `unload`, `pagehide`, `sendBeacon`, `visibilitychange` -> none). There is no "shut down" button/endpoint (routes at web_server.py:83-228: no shutdown route).
- **Failure scenario:** Operator closes the browser and the terminal (or `kill <pid>` / SIGTERM from a supervisor / macOS terminal close = SIGHUP). Python dies with models un-torn-down: no `power_down`, temperature not commanded to 0, serial ports dropped. Because there is also no UI-side stop, walking away from the tab leaves motors/heater in whatever state the model last set (only the model-level idle interlock, `BaseProbe._start_interlock_watchdog`, would time out for probes, not for heater/rotator, and it dies with the process).
- **Proposed fix direction:** Register a single idempotent `shutdown_hardware()` used by `atexit`, `signal.SIGTERM`/`SIGHUP`/`SIGINT` handlers in `run_web_app`, and a `finally:` around the main loop; it must read the CURRENT manager via the adapter (see MANAGER-1). Optionally add a `POST /api/system/shutdown` and a UI button. Consider a client heartbeat that triggers `full_stop_all` after N seconds of no polling (design question).
- **Confidence:** verified (absence of handlers); hypothesis for exact firmware end state after a dropped serial (needs bench check).

### MANAGER-3
- **Title:** Tkinter and PySide have no process-level safety net either (no atexit/signal/aboutToQuit; Cmd-Q and Ctrl-C bypass `on_close`/`closeEvent`)
- **Severity:** medium
- **Views affected:** Tkinter / PySide6
- **Reference behavior:** Teardown lives only in `DashboardWindow.on_close` (view.py:226-231) and `DashboardWindow.closeEvent` (pyside view.py:957-967).
- **Actual behavior:** grep for atexit, signal, SIGINT, SIGTERM, aboutToQuit across `src` returns no hits. Tk: `WM_DELETE_WINDOW` is bound to `on_close` for the Dashboard only (view.py:224). Ctrl-C in the launching terminal raises `KeyboardInterrupt` out of `mainloop()`/`app.exec()` with no cleanup.
- **Failure scenario:** Operator hits Ctrl-C (or macOS Cmd-Q) while the dashboard is open and a stepper is enabled/in manual mode: process exits without `power_down`. UNVERIFIED HYPOTHESIS for Cmd-Q specifically: Tk on macOS routes Cmd-Q to `::tk::mac::Quit` (default exit) rather than `WM_DELETE_WINDOW`; check by launching `--tkinter` on macOS, opening the dashboard, pressing Cmd-Q, and observing whether `on_close` runs (temp print). Qt's macOS Cmd-Q path is likewise unverified.
- **Proposed fix direction:** Same shared idempotent `shutdown_hardware(manager)` helper as MANAGER-2, registered with `atexit` and SIGINT/SIGTERM in `app.py` for all three launchers; for Tk also `createcommand('::tk::mac::Quit', dash.on_close)`; for Qt `app.aboutToQuit.connect(...)`. `SystemManager.shutdown_all` is already idempotent (clears dict first).
- **Confidence:** verified (no handlers exist); hypothesis (Cmd-Q behaviour)

### MANAGER-4
- **Title:** Web re-run of setup builds the new models BEFORE tearing down the old ones (same ports opened twice; old teardown then power-downs hardware the new model now owns)
- **Severity:** high
- **Views affected:** Web
- **Reference behavior:** Tk/PySide launch only once per manager. Tk on close tears down first, and a relaunch creates a fresh manager after `shutdown_all` (view.py:228 then setup relaunch). `SystemManager.reboot_model` does remove -> teardown -> sleep -> construct (system_manager.py:34-57), i.e. teardown first.
- **Actual behavior:** `initialize_setup` calls `app_bootstrap.build_models` (web_adapter.py:148) which opens serial ports, THEN swaps managers (L176-179), THEN calls `old_manager.shutdown_all()` (L184-185). Nothing prevents a second `POST /api/setup/initialize` (web_server.py:186-193) while `mode == "running"`; the handler does not check `adapter.mode`.
- **Failure scenario:** After the stage is running on `/dev/cu.usbmodem1`, an operator (or a double-click on Launch, or a stale second tab) posts setup again with the same port. New `StepperProbe` opens the port (pyserial on POSIX does not take an exclusive lock by default) while the old model still holds it; then `old_manager.shutdown_all()` runs the OLD model's `power_down()` (writes `d` and `k` to its own handle of the same device) and closes it, i.e. the freshly launched stage gets its coils killed / state desynced. At best the second open fails on some OS and the build path leaks (see MANAGER-5).
- **Proposed fix direction:** In `initialize_setup`: reject (409) if `self.mode == "running"` unless an explicit `reconfigure` flag is set; when reconfiguring, `shutdown_all()` the old manager (outside `_state_lock`) BEFORE `build_models`, and hold a setup-in-progress flag so concurrent POSTs serialize. JS disables the button (app.js:1790) but the server must not trust it.
- **Confidence:** verified (ordering, no mode guard); hypothesis for the pyserial shared-open effect (settle by starting setup twice with a real or pty port and observing both handles).

### MANAGER-5
- **Title:** `build_models` has no partial-failure cleanup: a constructor exception leaks every model already built (open serial ports, pollers) in all three views
- **Severity:** high
- **Views affected:** Tkinter / PySide6 / Web
- **Reference behavior:** n/a in main (each device was its own process; a failing process could not leak siblings). Contract in `SystemManager` implies models are torn down through the manager.
- **Actual behavior:** `app_bootstrap.build_models` (app_bootstrap.py:140-166) constructs models in a loop straight into a local dict with no try/except and no rollback; the dict is never registered in a manager until the loop finishes. Callers: Tk app.py:402 (no try/except), PySide app.py:731 (no try/except), Web web_adapter.py:147-156 (catches the exception, returns 500, but the already-built models inside `build_models` are unreachable and never `teardown()`ed).
- **Failure scenario:** Setup checks Stepper (real port) + Temperature (port busy/permission error). `StepperProbe` is built (serial open, gamepad poller created; probes.py:23, 36-40), then `TemperatureSystem(port)` raises. The Stepper model is dropped without `teardown()`: port stays open until GC (and `__del__` only prints, probes.py:15-16), the poller is never `close()`d (pygame stays initialised, `_active_poller_count` never decremented, gamepad.py:494-513), a retry then hits a busy port from the same process.
- **Proposed fix direction:** In `build_models`, wrap construction in try/except; on failure call `teardown()` (best-effort, each in try/except) on all models built so far, then re-raise. Better: make `build_models` take a `SystemManager` and `register_model` as it goes so the caller can `shutdown_all()` in one place. Tk/PySide callers must catch and show a message rather than let the event loop swallow it.
- **Confidence:** verified

### MANAGER-6
- **Title:** Tkinter: setup window is withdrawn BEFORE model construction; any exception leaves an invisible, unkillable process
- **Severity:** medium
- **Views affected:** Tkinter
- **Reference behavior:** main also withdrew first (mainGUI.py `self.withdraw()` before spawning), but failures happened in child processes so the parent survived and the user could see errors. PySide keeps its setup window until `self.close()` after the dashboard is shown (app.py:750-753); Web returns an HTTP 500.
- **Actual behavior:** `launch_unified` calls `self.withdraw()` at app.py:397, then `build_models` (L402), Red% linking (L405-412), and `DashboardWindow(...)` (L422) with no try/except and no `deiconify` on failure. Tk's default `report_callback_exception` prints to stderr; the `mainloop` keeps running.
- **Failure scenario:** ImportError or constructor error in `build_models` -> exception in the button callback -> Setup window is gone, no dashboard, process alive with no visible window (must be killed from the terminal). Combines with MANAGER-5 leaking earlier models.
- **Proposed fix direction:** Wrap L397-423 in try/except; on failure `self.deiconify()`, `shutdown_all()` any partially built manager, `ErrorPopupManager.report_error(...)`. Or move `self.withdraw()` to after `DashboardWindow(...)` succeeds.
- **Confidence:** verified

### MANAGER-7
- **Title:** PySide `close_device_view` hand-rolls a partial teardown that bypasses `SystemManager.remove_model`/`teardown`, leaks the serial port, skips power-down for probes, and leaves stale references
- **Severity:** high
- **Views affected:** PySide6
- **Reference behavior:** Tk/`SystemManager` contract: teardown goes through `model.teardown()` (probes: poller stop+close, `power_down`, `serial_comm.close()`; probes.py:479-487). main: `_on_closing` = `serial.disable()`, `controller.stop_polling()`, `controller.close()`, `serial.close()` (`stepper_frame.py:675-683`).
- **Actual behavior:** On sidebar uncheck or dock X (view.py:806-847, 943-955): calls `widget.cleanup()`, `dock.close()`, then for the model: `model.disable()` (`_stop_and_disarm`, probes.py:464-465, sends stop + `d`), `poller.stop_polling()`, `poller.close()`, `if hasattr(model,'disconnect'): model.disconnect()` (only `RotatorSystem` and `TemperatureSystem` define `disconnect`; `BaseProbe` has none: rotator_system.py:108, temperature_system.py:194), then `del self.system_manager.active_models[device_name]` (view.py:845-846) directly on the dict, without the manager lock and without `remove_model`. It never calls `teardown()`.
  - Probes: no `power_down` ('k'); `serial_comm.close()` is never called (no such call in view.py:813-847), so the handle stays open until the discarded object is garbage collected.
  - Red%: `RedPercentDynamicView.cleanup` calls `stop_monitoring` (view.py:708-714), so monitoring stops; but Red%'s `available_probes` still holds a reference to the deleted probe (only additions at view.py:913-917, no removal), so Red% may keep syncing against a probe that is no longer in the manager and will no longer be torn down at shutdown.
  - Because the model is deleted from `active_models`, `shutdown_all()` at window close never sees it.
- **Failure scenario:** Operator unchecks "Stepper Probe" in the sidebar mid-session, then closes the app. Coils were only `d`-disabled, never `k`-killed; the serial port from the removed model is still open, and Red% keeps a stale probe reference outside the manager. On app exit `shutdown_all` does not include it.
- **Proposed fix direction:** Replace L834-847 with `old = self.system_manager.remove_model(device_name)` then `old.teardown()` (wrapped in try/except with `ErrorRouter`); add a manager-level `release_model(name)` that does remove + teardown and lets Red% drop the entry (`red_model.available_probes.pop(name, None)` and re-run `set_stepper_model` if it was the linked one). Remove the direct `active_models` accesses at view.py:778 and 845-846 in favour of `get_active_models_snapshot()`/manager methods.
- **Confidence:** verified

### MANAGER-8
- **Title:** PySide: a device re-opened from the sidebar is a brand-new model with `port=None`, `active_claims={}`, i.e. silently disconnected from hardware; the "reconnect serial" branch is unreachable
- **Severity:** medium
- **Views affected:** PySide6
- **Reference behavior:** Tk has no re-open; the tab stays. Setup-time assignment (`app_bootstrap.build_models` with configured port/controller/`active_claims`) is the only construction path for hardware models (app.py:402).
- **Actual behavior:** `open_device_view` (view.py:881-908) instantiates missing models as `StepperProbe(None, "None", {})`, `DCProbe(None,"None",{})`, `ChuckPositioner(None,"None",{})`, `TemperatureSystem(None)`, `RotatorSystem(None)`; `BaseProbe.__init__` sets `serial_comm = None` for port None (probes.py:23), likewise temperature (temperature_system.py:26). Devices not selected at setup can also be enabled from the sidebar this way, bypassing `validate_assignment`. The branch at L859-879 (show dock + `reconnect_serial()`) only runs when `device_name in self.active_docks`, but `close_device_view` pops the dock from `active_docks` (L815) before any re-open, and an already-open dock's checkbox cannot be re-checked; so the "Scanning and reconnecting" path is effectively dead.
- **Failure scenario:** Operator closes the "Stepper Probe" dock (real port) then re-checks it: a model appears with all UI live but no serial connection; moves silently do nothing; no error. The original port/controller assignment from setup is lost. New models also bypass the shared `active_claims` created in setup (app.py:521), defeating controller-collision checks (gamepad.py:398-415).
- **Proposed fix direction:** Keep setup `configs` (port/controller) on the manager or dashboard and rebuild via `app_bootstrap.build_models([config], active_claims)`; or do not destroy models on dock close (hide the dock, leave model to `shutdown_all`, matching Tk tab semantics). Route unselected-device creation through `validate_assignment`.
- **Confidence:** verified

### MANAGER-9
- **Title:** Tkinter tab close (right/middle click) only `forget()`s the tab: model, poller, view timers and manual-mode routing keep running with no UI
- **Severity:** high
- **Views affected:** Tkinter
- **Reference behavior:** main: closing a device window ran `_on_closing` (disable, stop polling, close pygame, close serial; `stepper_frame.py:675-683`). PySide: closing a dock destroys the model (with the gaps in MANAGER-7).
- **Actual behavior:** `DraggableClosableNotebook.close_tab` (view.py:158-162) calls `on_close_tab_callback` if set else `self.forget(index)`. `on_close_tab_callback` is initialised to `None` (view.py:120) and never assigned anywhere (grep: only L120, L159-160). `forget` hides the tab; the frame/view is not destroyed, the `DynamicView` `after` loops (`_route_input` 50 ms, `_poll_pos`, `_poll_stat`; view.py:551-578) and the gamepad poller keep running, and `tab_metadata` (view.py:195, 222) is never cleaned.
- **Failure scenario:** In manual/gamepad mode on the Stepper tab, operator middle-clicks the tab to "close" it. Tab disappears but `manual_flag` is still true; `_route_input` continues to read the gamepad and send manual commands; the stage can be driven by joystick input with no visible controls and no way to reopen the tab (only FULL STOP or closing the whole dashboard recovers).
- **Proposed fix direction:** Set `self.notebook.on_close_tab_callback = self._close_tab` in `DashboardWindow.__init__` that (a) `system_manager.remove_model(name)` + `model.teardown()`, (b) destroys the view (cancelling its `after` ids) and `forget`s the frame, (c) drops `tab_metadata[...]`; or remove the close-tab affordance. Decide with the owner which semantics is intended; both must be consistent with PySide (MANAGER-7).
- **Confidence:** verified

### MANAGER-10
- **Title:** `RotatorSystem.teardown()` closes the serial port without ever sending a stop
- **Severity:** medium
- **Views affected:** Model (all views at app exit)
- **Reference behavior:** Probes: `teardown()` = stop poller, `power_down()` (stop + disarm + 'k'), close (probes.py:479-487). Temperature: `close()` writes `<0,6.0,0,0,0,0>` before closing (temperature_system.py:184-192). `ManagedModel.teardown` doc: "stop all background activity and release hardware connections" (base.py:12-16).
- **Actual behavior:** `RotatorSystem.teardown()` = `self.disconnect()` (rotator_system.py:122-123) which nulls `smc` and calls `smc.close()` (L108-120); no `smc.stop()`. `emergency_stop()` is the separate `self.stop()` (L125-126, stop at L191-203). Moves run on daemon threads via `_run_async` (L56-60), not joined.
- **Failure scenario:** Operator commands a large rotation, closes the dashboard (Tk `on_close` / PySide `closeEvent` / Ctrl-C-web after MANAGER-1 is fixed) while it is moving: `teardown` closes the port with the SMC100 still executing the move; no stop is sent and the rotator finishes the move (or the daemon move thread errors on the closed port) unattended.
- **Proposed fix direction:** `RotatorSystem.teardown()`: call `self.stop()` (best effort, try/except) before `disconnect()`. Consider `join(timeout)` on the async move thread or a shared cancel flag.
- **Confidence:** verified (code path); hypothesis for what the controller does after port close (bench check).

### MANAGER-11
- **Title:** `SystemManager.reboot_model` has zero callers in `src`, blocks its caller for 1 s, and can leave a device deregistered
- **Severity:** low
- **Views affected:** Model (no view uses it)
- **Reference behavior:** main had no equivalent. Intended semantics per docstring: "Safely tears down and reconstructs a model." (system_manager.py:35).
- **Actual behavior:** grep for `reboot_model` in `src` -> only the definition (system_manager.py:34); only tests call it (tests/core/test_integration.py:36, tests/core/test_edge_mvc_model.py:138, tests/edge_cases/test_qa_round1.py:63,90). The body does `time.sleep(1)` (L45) with no lock, on whatever thread calls it; a GUI-thread caller (Tk/PySide button) would freeze the UI for at least 1 s plus `teardown` + constructor time (constructors open serial ports and create pygame pollers). If the constructor raises, the old model is already removed and torn down, `None` is returned and the name is absent from `active_models` (L47-57), so any view still holding the old object has a dead reference (no notification). `register_model` (L10-12) silently overwrites an existing entry without teardown.
- **Failure scenario:** If a future "reboot device" button is wired on the GUI thread, the app freezes for 1 s+ and after a failed reboot the tab/dock still shows a model that FULL STOP and shutdown can no longer reach.
- **Proposed fix direction:** Drop the fake `sleep(1)` or make it a documented, short serial-settle wait executed off the GUI thread; return a result object (new_model, error); make `register_model` teardown/raise on duplicate; decide whether to delete the method (dead code) or wire it (and views) properly. Do not call it from the Tk/PySide GUI thread without a worker.
- **Confidence:** verified

### MANAGER-12
- **Title:** Web setup marks disabled devices as NOT disabled: `_disabled_in_setup` is always False because the normalised config drops `enabled`
- **Severity:** medium
- **Views affected:** Web
- **Reference behavior:** Tk/PySide construct only the checked devices (app.py:367-385 Tk; 700-716 PySide), so unchecked devices do not exist as models.
- **Actual behavior:** The web front end sends every device with `enabled: dev.enabled` (app.js:1778-1785). `initialize_setup` normalises to dicts with keys `device, port, controller, mode` only (web_adapter.py:99-104, 115-120): disabled devices get port `"None"` (L95-97/111-113). `build_models` builds ALL of them (app_bootstrap.py:141-166). Then `model._disabled_in_setup = not cfg.get("enabled", True)` (L161) where `cfg` is the normalised dict without an `enabled` key, so it is always `not True == False`. Consumers never see a disabled device: `get_devices` `schema["_disabled"]` (web_adapter.py:223-224; app.js:420, 427) is never set, and `RedPercentSystem.get_available_probe_names` filter (redpercent_system.py:184) never excludes anything. L162-165 also force `system_enabled = False`/`disable()` on every model.
- **Failure scenario:** Operator enables only "Temperature Controller". The web dashboard still builds Stepper/DC/Chuck/Rotator/Red% models (constructed with port "None" -> unconnected but present, with UI, polled every cycle and included in FULL STOP/teardown), and Red% lists disabled probes as selectable sync targets.
- **Proposed fix direction:** Filter `normalized_configs` to enabled entries before `validate_assignment`/`build_models` (matching Tk/PySide), or carry `"enabled"` through the normalised dict so L161 works.
- **Confidence:** verified

### MANAGER-13
- **Title:** Web: poller log wiring in `WebDashboardWindow.__init__` iterates an empty manager, and the web view never starts the gamepad poller or routes manual input
- **Severity:** medium
- **Views affected:** Web
- **Reference behavior:** Tk: `DynamicView.start_polling` starts `poller.start_polling(dashboard, log_updater=print, activity_callback=touch_activity)` plus a 50 ms `_route_input` that feeds `poller.get_mapped_state()` into `send_manual_mode_command` (view.py:542-565). PySide equivalent (view.py:156-193) at 20 ms.
- **Actual behavior:** `WebDashboardWindow.__init__` (web_view.py:56-65) loops `system_manager.active_models` to install `poller.log_updater`; at that time the manager is empty (app.py:774; models are built later), and setup never repeats the linking, so the loop is dead code and the web log buffer never receives controller logs. grep for `start_polling` and `send_manual_mode_command` in `src/views/web` -> no hits: nothing in the web view or adapter starts pollers (`ControllerPoller.get_mapped_state` returns `{}` unless `is_polling`, gamepad.py:515-518) or relays `manual_flag` input to `send_manual_mode_command` (only Tk/PySide call it). No `activity_callback=touch_activity` either.
- **Failure scenario:** In the web UI "Manual / Gamepad" toggles `manual_flag` (app.js:888-915) but no code path reads the gamepad and sends manual packets; the mode appears active but is inert. UNVERIFIED HYPOTHESIS that this is a regression rather than an intentional not-yet-implemented feature; settle by toggling Manual with a physical pad on the web view and watching serial traffic.
- **Proposed fix direction:** After `initialize_setup` succeeds, have the adapter (not the window ctor) start pollers with a background-thread `gui` shim (an object with `after()`), spawn the manual routing loop server-side, and install `log_updater` into `WebModelAdapter.append_log`. Delete the dead ctor loop.
- **Confidence:** verified (missing call sites); hypothesis (observable effect)

### MANAGER-14
- **Title:** Launcher quirks: `parse_known_args` swallows typos, `--port/--no-browser` silently ignored for non-web, dead "unknown view" branch, `run.sh` forces Tk
- **Severity:** low
- **Views affected:** Launcher
- **Reference behavior:** Docs claim default view = web on macOS, pyside elsewhere, web fallback if PySide6 missing: **verified correct** (app.py:889-901).
- **Actual behavior:** `args, unknown = parser.parse_known_args()` (app.py:885): unknown flags (e.g. `--pyside6`) are silently dropped and `unknown` is never inspected, so a typo falls through to the platform default (Web on macOS, which starts a hardware-capable server) instead of erroring. `--port`/`--no-browser` are only passed to `run_web_app` (L908), ignored otherwise. The `else: "Unknown view"` + `launch_web()` (L912-914, L819-822) is unreachable (`choices` and `store_const` restrict values) and `launch_web()` ignores `--port`. On non-darwin the PySide default only tests `import QApplication` (L897), not that a display exists. `run.sh` hard-codes `python3 src/app.py "--tkinter"` and `source .venv/bin/activate` (run.sh:3-4), so the documented default never applies to the wrapper. `run_legacy_app`'s `get_available_controllers` fallback `if not self.detected_controllers` (app.py:152) is unreachable because the list starts as `["None"]`.
- **Failure scenario:** `python3 src/app.py --tkinker` on a lab Mac silently launches the web server and opens a browser.
- **Proposed fix direction:** Use `parse_args()` (or error on non-empty `unknown`); delete `launch_web`/unknown branch or make it honor args; have `run.sh` pass through `"$@"`.
- **Confidence:** verified

### MANAGER-15
- **Title:** Web HTTP API accepts cross-site "simple request" POSTs (no Content-Type/Origin/Host check), so any web page open in the operator's browser can drive the stage
- **Severity:** high
- **Views affected:** Web
- **Reference behavior:** Tk/PySide have no network surface. The code comment at web_server.py:42-45 says omitting CORS headers protects against external sites driving hardware.
- **Actual behavior:** `do_POST` (web_server.py:148-159) reads the body and `json.loads` it without checking `Content-Type`, `Origin`, or `Host`. A cross-origin `fetch(..., {method:'POST', mode:'no-cors', body: JSON.stringify(...)})` with `Content-Type: text/plain` is a CORS "simple request" (no preflight), so the browser sends it and the server acts on it (the attacker just cannot read the response). Routes reachable this way: `/api/command` (schema commands, e.g. moves; L198-208), `/api/set_attr` (L210-220), `/api/system/full_stop` (L222), `/api/setup/initialize` (L186-193, which per MANAGER-4 can replace the running manager). Server binds 127.0.0.1 only (web_server.py:301, 326). No `Host` validation also leaves DNS-rebinding open (rebinding makes responses readable too, incl. `/api/screenshot`, L124-143, which returns the operator's full screen).
- **Failure scenario:** Operator has the dashboard open (Manual mode armed) and visits any page containing a script that POSTs `{"device":"Stepper Probe","command":"...","args":[...]}` to `http://127.0.0.1:8080/api/command`: the stage moves. Scanning 8080-8089 is trivial (server increments on collision, web_server.py:324-329).
- **Proposed fix direction:** Require `Content-Type: application/json` on POST (forces preflight), reject requests whose `Origin` is present and not `http://127.0.0.1:<port>`/`localhost`, reject `Host` values other than 127.0.0.1/localhost:<port>, and optionally add a per-launch random token embedded in served HTML and required in an `X-Token` header. Update the misleading comment.
- **Confidence:** verified for server behaviour; standard browser CORS semantics assumed (settle with a two-origin test page).

### MANAGER-16
- **Title:** Tkinter relaunch reuses `SetupWindow.active_claims` that is never cleared or released, so stale controller claims cause false collisions on the second launch
- **Severity:** medium
- **Views affected:** Tkinter
- **Reference behavior:** main cleared claims on each launch: `self.active_claims.clear()` (mainGUI.py in `launch_modules`, before spawning).
- **Actual behavior:** `SetupWindow.active_claims = {}` is created once (app.py:100) and passed to every `build_models` call (app.py:402). Tk `on_close` returns to the same setup window (view.py:231). `ControllerPoller` writes `active_claims[process_name]` (gamepad.py:320, 376, 395, 414, 443) and no code ever removes/clears entries (grep for `active_claims.pop|clear|del` -> none; `poller.close` at gamepad.py:494-514 does not release). The collision check (gamepad.py:398-415) skips only the model's own `process_name` (class name).
- **Failure scenario:** Session 1: Stepper on `ID 0`. Close dashboard (teardown runs). Session 2 from the same setup window: DC Probe (not Stepper) on `ID 0` -> stale `StepperProbe: ID 0` claim still in the dict -> `DCProbe` gets "Controller collision ... already claimed by StepperProbe" and no controller. PySide/web create fresh dicts per launch and are unaffected (web_adapter.py:145).
- **Proposed fix direction:** `self.active_claims.clear()` at the start of `launch_unified`, and have `ControllerPoller.close()` remove its own claim.
- **Confidence:** verified

### MANAGER-17
- **Title:** Tkinter `ErrorPopupManager` polling likely dies after the first dashboard is closed and is not restarted on relaunch
- **Severity:** low
- **Views affected:** Tkinter
- **Reference behavior:** PySide `QtErrorPopupManager.initialize` is idempotent on `_instance` (pyside view.py:34-38); web buffers errors independently.
- **Actual behavior:** `ErrorPopupManager.initialize(root)` sets `cls._root = root` and starts `_poll_queue` only if `not cls._is_polling` (tkinter view.py:14-37; `_is_polling` is set True and never reset). Tk `launch_unified` re-initialises with the dashboard as root (app.py:423). The polling chain runs `cls._root.after(100, cls._poll_queue)` (view.py:37), i.e. on the Toplevel dashboard; on a later relaunch the guard prevents a fresh loop.
- **Failure scenario:** After closing and relaunching the dashboard once, `ErrorRouter.report_error` (which includes shutdown/teardown errors from `SystemManager`, system_manager.py:41,56,68,78) enqueues into a queue nobody drains, so operators never see popups. UNVERIFIED HYPOTHESIS for whether the destroyed-widget `after` chain actually dies (Tk deletes registered Tcl commands on `destroy`); settle by launching, closing, relaunching, then triggering an error (e.g. a nonexistent port) and checking for a popup.
- **Proposed fix direction:** Reset `_is_polling = False` and `_root = setup window` when the dashboard is destroyed (in `on_close`), or always restart the poll in `initialize`; poll on the persistent setup Tk root.
- **Confidence:** hypothesis

### MANAGER-18
- **Title:** Setup differences: Web scan/build path diverges from Tk/PySide (no autodetect, `python3` subprocess, fake controllers, duplicate SIM/Headless, Red% linking copy-pasted four times)
- **Severity:** medium
- **Views affected:** Web (vs Tkinter/PySide6)
- **Reference behavior:** Tk/PySide: background scan of every port via `app_bootstrap.probe_device_at` (app.py:307-330 Tk; ScannerThread app.py:468-504) auto-checks and assigns detected devices; `get_available_controllers` uses in-process pygame and returns only real pads plus "None" (app.py:136-153, 536-548); Headless mapped to `SIM` at launch (app.py:372-373, 705-706); collisions via `validate_assignment` (app_bootstrap.py:168-195).
- **Actual behavior:** Web `scan_hardware` (web_adapter.py:39-76) lists ports only, prepending a literal `"SIM"` and then `discover_ports()` output which already contains `"Headless"` (app_bootstrap.py:32,37), so the web port list contains both "SIM" and "Headless" (the latter remapped at web_adapter.py:131-133). Controllers are enumerated by spawning a `subprocess` with the literal `"python3"` (L59-61, timeout 3 s): `python3` on PATH is not necessarily the venv interpreter (`sys.executable` would be), so pygame may be missing and the result silently falls back; if no controller is found it fabricates `"Virtual Controller A/B"` (L69-70), which Tk/PySide do not offer. The subprocess sets `SDL_VIDEODRIVER=dummy` but not `SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS` (compare app.py:3-5, 453). Web builds all configured devices, including disabled (MANAGER-12). Red% linking block is duplicated in Tk app.py:404-412, PySide app.py:733-741, PySide on-demand view.py:902-908, and Web web_adapter.py:166-174.
- **Failure scenario:** Web operator with a real gamepad and a system python3 without pygame sees fake "Virtual Controller" entries; picks one and gets no real input. Hardware ports are assigned by hand with no autodetect verification.
- **Proposed fix direction:** Move controller enumeration and Red% linking into `app_bootstrap` (`link_red_percent(models)`, `discover_controllers()`), use `sys.executable`, drop "Virtual" fabrication unless explicitly a sim option, dedupe SIM/Headless, and add the async scan API the docstring (web_adapter.py:42-49) already calls for.
- **Confidence:** verified

### MANAGER-19
- **Title:** `shutdown_all` and `full_stop_all` are sequential with no timeout and run in registration order (probes before Red%); one blocking call delays every later model, including the heater
- **Severity:** low
- **Views affected:** Model (all views)
- **Reference behavior:** main: each device was independent (separate process), so a slow serial close on one device did not delay another.
- **Actual behavior:** `shutdown_all` (system_manager.py:59-68) iterates `list(active_models.items())` in insertion order and calls `_teardown_model` serially on the calling (GUI) thread. Insertion order = setup device order: Stepper, DC, Chuck, Temperature, Rotator, Red% (app.py:89 order and `build_models` loop order). No per-model timeout; serial `close()` takes a lock (`serial.py`, close at ~L264-270) that a reader may hold. `full_stop_all` (L70-78) is also serial, on the GUI thread in Tk/PySide, contradicting `ManagedModel.emergency_stop` "must never block" (base.py:18-22) for any model whose `emergency_stop` waits on a lock.
- **Failure scenario:** A hung stepper serial lock delays the heater's setpoint-0 write during app close or FULL STOP; Tk/PySide UI freezes until done.
- **Proposed fix direction:** Stop models in parallel (thread per model with a bounded join) for `full_stop_all`, and run `teardown` with a per-model timeout; report timeouts through `ErrorRouter`.
- **Confidence:** hypothesis (needs a test with a stalled fake serial)

### MANAGER-20
- **Title:** PySide `SetupWindow` can be closed mid-scan while its `QThread` is running
- **Severity:** low
- **Views affected:** PySide6
- **Reference behavior:** Tk's scan thread is a daemon thread (app.py:304-305), harmless at exit.
- **Actual behavior:** `ScannerThread` is a `QThread` stored on `self.scanner` (app.py:657-663) with no `closeEvent`/`wait()` on the setup window; Launch/Refresh are disabled during scan but the window X is not. `probe_device_at` can block many seconds per port (app_bootstrap.py:58-136: ~1.5 s + 3 s per baud per port).
- **Failure scenario:** Operator closes the setup window during the scan; Qt may print "QThread: Destroyed while thread is still running" and abort, or the process lingers until the scan ends.
- **Proposed fix direction:** In `SetupWindow.closeEvent`, `requestInterruption()`/`wait()` on the scanner with a bounded timeout, and have `probe_device_at` check an abort flag.
- **Confidence:** hypothesis (check by closing the window during a scan with several ports present)

---

## Coverage

Read fully: `src/model/system_manager.py`, `src/model/base.py`, `src/app_bootstrap.py`, `src/app.py` L38-920 (all of `run_legacy_app`, `run_pyside_app`, `run_web_app`, `main`), `src/views/web/web_view.py`, `src/views/web/web_server.py`, `run.sh`.
Read partially: `src/views/tkinter/view.py` L14-40, 100-270, 536-585, 820-835 plus grep for close/destroy/full_stop/protocol; `src/views/pyside/view.py` L30-60, 96-200, 425-455, 700-717, 715-967 plus greps; `src/views/web/web_adapter.py` L15-262, 378-435 plus greps; `src/views/web/static/js/app.js` only via greps (full_stop, unload, setup/initialize, manual, _disabled) and L1760-1815; `src/model/{probes,rotator_system,temperature_system,redpercent_system}.py` only the teardown/emergency_stop/disable/disconnect/stop bodies; `src/controller/gamepad.py` start_polling/stop_polling/close/get_mapped_state/claims L474-530 and claim grep; `src/controller/serial.py` `_verify_serial`/enable/disable/close L102-109, 249-270. main: `git show main:src/mainGUI.py` L55-70, 290-385; `stepper_frame.py` `full_stop_button` (L552-566) and `_on_closing` (L302-306, 675-683); `temp_control.py` protocol grep.
Not done: did not audit model internals (gamepad poll loop, `BaseProbe` interlock thread, reader threads in temperature/rotator), did not read all of app.js, did not exercise anything at runtime (read-only), no tests run. Cmd-Q, Ctrl-C signal behaviour in Tk/Qt, Tk `after`-chain death, pyserial shared-open, and browser CORS simple-request behaviour are labelled hypothesis/assumed where stated. Existing docs in `docs/architecture/` were not re-read line by line; the only doc claim explicitly checked is the default-view fallback (correct).

DONE manager 20
