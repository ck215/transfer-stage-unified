# Findings: view-web (src/views/web/*)

> **Input (banner added 2026-09-23).** Audit of the old tree (now `legacy/src/`); current until the rebuild replaced it on 2026-09-23. Kept because the ledger in `docs/implementation/progress.md` and `docs/rebuild/carry.json` cite these finding IDs.

All paths relative to /Users/ianalbinogonzalez/Documents/GitHub/transfer-stage-unified/mvc-refactor. All line numbers were read in this run.

## Object summary

**Construction.** `run_web_app()` (src/app.py:766-809) creates an EMPTY `SystemManager()` (app.py:774), passes it to `WebDashboardWindow(manager, ...)` (app.py:780 -> web_view.py:46-50), which builds `WebDashboardServer(system_manager)` -> `WebModelAdapter(system_manager)` (web_server.py:311, mode="setup", web_adapter.py:21). `WebAPIHandler.adapter` is a class-level singleton, rebound in `start()` (web_server.py:321).

**Model construction/registration.** Only in `WebModelAdapter.initialize_setup` (web_adapter.py:78-192), driven by `POST /api/setup/initialize`. It builds a NEW `SystemManager` (:144), calls `app_bootstrap.build_models` (:148), `register_model` per device (:159), then swaps `self.system_manager` under the lock (:176-179) and calls `old_manager.shutdown_all()` AFTER building the new models (:184-185). `remove_model` and `reboot_model` are never used by the web layer. Reads go straight to `system_manager.active_models` (:208, :221, :284, :362, :392, :444), not through `get_model`/`get_active_models_snapshot`.

**Owner.** Nominally the adapter (`adapter.system_manager`). `WebDashboardWindow.system_manager` (web_view.py:47) still points at the ORIGINAL empty manager forever (see WEB-1).

**Teardown paths.**
- Ctrl-C or unhandled exception in run_web_app -> `dashboard.close()` (app.py:802-809 -> web_view.py:74-78) -> `server.stop()` + `self.system_manager.shutdown_all()` on the STALE manager.
- Re-running setup -> `old_manager.shutdown_all()` (web_adapter.py:184-185).
- Page reload / tab close / second tab / SIGTERM: no teardown path exists at all.
- Emergency stop: `POST /api/system/full_stop` -> `adapter.full_stop_all()` -> `system_manager.full_stop_all()` (web_adapter.py:420-431), which uses the current manager (correct).

**Contract mapping (what SHOULD apply).** The web process is a long-lived server. Models are owned by the server, not by a browser tab. Tab load/reload/close MUST be a no-op on models (no construct, no teardown). Model construction is allowed once per process, or via an explicit, guarded "re-run setup" that tears the old set down BEFORE building the new set. Process exit (SIGINT/SIGTERM/atexit) MUST call `shutdown_all()` on whatever manager the adapter currently holds. FULL STOP stays a `full_stop_all()` on the current manager and must never depend on any per-device lock.

**Doc corrections.**
- The claimed "Web skips RedPercent probe wiring" is FALSE. web_adapter.py:166-174 does the same wiring as app.py:404-412 (Tk). I could not find that sentence in docs/architecture/*.md in the current working tree (grep for "probe wiring" and "skips.*probe" found nothing), so it may live in an uncommitted or other location. The wiring is present and correct, except that it runs after the broken `_disabled_in_setup` assignment (WEB-4).
- libs-and-web.md:59-63 says the adapter "tears down the outgoing SystemManager safely". That is true only for the re-setup path. The doc omits WEB-1 (shutdown on the wrong manager) and the build-before-teardown ordering (WEB-3).
- libs-and-web.md:82 ("browser polls for errors ... popping them") is accurate but omits the destructive multi-tab consequence (WEB-9).
- views.md:190-196 says the Web view was "not covered". This report covers it.

---

## Findings

### WEB-1: Ctrl-C / crash shutdown tears down the wrong (empty) SystemManager; real models are never torn down
- ID: WEB-1
- Severity: high
- Views affected: Web
- Reference behavior: Tk `DashboardWindow.on_close` calls `self.system_manager.shutdown_all()` on the manager that actually holds the models (src/views/tkinter/view.py:226-228). That manager is built from `active_models` at app.py:418-422.
- Actual behavior: `run_web_app` creates an empty manager (app.py:774) and gives it to `WebDashboardWindow`, which stores it as `self.system_manager` (web_view.py:47). `initialize_setup` never updates that reference. It only updates `adapter.system_manager` (web_adapter.py:176-179). `WebDashboardWindow.close()` calls `self.system_manager.shutdown_all()` (web_view.py:77-78) on the empty manager. The `except Exception` branch also calls `dashboard.close()` (app.py:809). There is no SIGTERM handler or atexit hook (app.py:798-809 only catches KeyboardInterrupt).
- Failure scenario: Operator launches web, sets up a Stepper Probe on real hardware, enables it (coils energized), then hits Ctrl-C in the terminal. `shutdown_all` runs on an empty dict. `power_down` (`k` command, probes.py:470-477 via probes.py:479-487 `teardown`) is never sent, serial ports are not closed cleanly, and Red Percent monitor threads are only killed by interpreter exit. Steppers stay energized after the process exits.
- Proposed fix direction: `WebDashboardWindow.close()` should shut down `self.server.adapter.system_manager` (the current one). Better, add `WebModelAdapter.shutdown()` that grabs the manager under `_state_lock` and calls `shutdown_all()`, and call it from `close()`. Also register `signal.SIGTERM` and `atexit` handlers in `run_web_app`. Make `WebDashboardWindow.system_manager` a property delegating to the adapter.
- Confidence: verified

### WEB-2: Web never starts the gamepad poller nor routes manual-mode input, so Manual/Gamepad mode is dead, and it energizes coils and never idles out
- ID: WEB-2
- Severity: high
- Views affected: Web (Model behavior exposed by the web omission)
- Reference behavior: Tk `DynamicView.start_polling` calls `poller.start_polling(dashboard_window, log_updater=print, activity_callback=touch_activity)` (tkinter/view.py:545-550) and runs `_route_input` every 50 ms, which calls `poller.get_mapped_state()` then `model.send_manual_mode_command(...)` (tkinter/view.py:552-566), plus a flush-to-neutral send on exit from manual. PySide6 does the same (pyside/view.py:169).
- Actual behavior: grep of `start_polling` shows callers only in tkinter/view.py:550 and pyside/view.py:169. The web layer never calls `poller.start_polling`. `ControllerPoller.get_mapped_state()` returns `{}` unless `is_polling` (gamepad.py:515-518). `is_polling` is set only inside `start_polling` (gamepad.py:474-488). The web has no server-side loop equivalent to `_route_input`. `web_view.py:56-65` only sets `poller.log_updater`. `get_state` calls `read_position`/`poll_status` only (web_adapter.py:290-299).
- Failure scenario: In the web UI click "Enter Manual Mode" (`toggle_manual` -> `enter_manual`, probes.py:225-241). The gamepad-present check passes (`poller.gamepad` is set in the constructor). `enable()` energizes the coils and starts the watchdog (probes.py:432-433). `manual_flag=True`. Joystick input is never forwarded, so nothing moves. The idle watchdog explicitly defers while `manual_flag` is true (probes.py:409-412), so the 5-minute auto-disable never fires and the coils stay energized until someone manually toggles or full-stops. Joystick activity also never calls `touch_activity` (no `activity_callback` is wired).
- Proposed fix direction: Either (a) add a server-side per-probe manual-input thread owned by the adapter (start on `manual_flag` true, stop on false, send `{}` once on exit, and be torn down with the model), or (b) make `toggle_manual`/`enter_manual` refuse in the web view and remove the Manual toggle from the web schema rendering. Wire `activity_callback=model.touch_activity` and `poller.start_polling()` with a non-Tk GUI adapter (pyside/view.py:~160-169 has a `GUIAdapter` pattern to reuse). The poller loop needs a `gui_root.after`-style scheduler (gamepad.py:637-640), so a small thread-based adapter is required.
- Confidence: verified (code paths); runtime effect follows directly from gamepad.py:515-518.

### WEB-3: initialize_setup builds new models BEFORE tearing down the old ones (port/joystick double-open) and has no "already running" or concurrency guard
- ID: WEB-3
- Severity: high
- Views affected: Web
- Reference behavior: Tk builds models once at launch (app.py:402-422) and only re-enters setup after `shutdown_all()` in `on_close` (tkinter/view.py:226-230). The setup UI is unreachable while models are live.
- Actual behavior: `initialize_setup` calls `build_models` (web_adapter.py:148) while `self.system_manager` still holds the previous live models, then swaps (:176-179), then `old_manager.shutdown_all()` (:184-185). `serial.__init__` opens the port immediately (controller/serial.py:49-52). A failure is only a warning and the model continues "blind" (serial.py:88-93). `active_claims = {}` is fresh (web_adapter.py:145), so joystick claim checks do not see the old pollers. The "Setup Wizard" button is always present (index.html:72, JS app.js:302-306) and the endpoint accepts a POST at any time. There is no `_init_lock`: two overlapping POSTs (two tabs) both build and swap (the second tears down the first's fresh models).
- Failure scenario: Operator re-opens the wizard while the stage is running and presses Launch again with the same COM ports. The new `serial()` cannot open the port already held by the old model. It logs a "Serial Exception" warning and runs blind. The old model is then torn down (`power_down` + `close`). The dashboard shows a "running" card that is not talking to hardware, and the connection badge can still read "hardware" from `_determine_connection_status` (:263-264 depends on `ser.is_open`, though the ser object may be None so this is not certain). An active auton run on the old model is killed mid-motion with no confirmation.
- Proposed fix direction: Serialize setup with a lock. Reject (409) `initialize_setup` when mode is "running" unless a `force`/`reconfigure` flag is passed. When reconfiguring, `shutdown_all()` the old manager FIRST (like Tk's close then relaunch), then build. On build failure, keep the system in "setup" mode with an empty manager, not the old one. Have the JS disable Launch while a request is in flight (it already does: app.js:1794) and confirm before re-setup.
- Confidence: verified (ordering and no guard); the blind-serial effect on same-port relaunch is verified from serial.py:49-93, hardware behavior on a real port not exercised.

### WEB-4: `enabled` flag is dropped during config normalization; `_disabled_in_setup` is always False; disabled devices are built and live
- ID: WEB-4
- Severity: high
- Views affected: Web
- Reference behavior: Tk launch only includes devices whose checkbox is on (`if self.device_vars[device].get()`, app.py:367-379). Disabled devices are never built. main likewise only spawned selected devices (main mainGUI.py ~340-360). `RedPercentSystem.get_available_probe_names` filters `_disabled_in_setup` probes (redpercent_system.py:180-185).
- Actual behavior: Normalization builds dicts with only device/port/controller/mode (web_adapter.py:99-104 for dict input, :115-120 for list input); the `enabled` key is discarded. Later `cfg = next((c for c in configs if c["device"] == dev), {})` then `model._disabled_in_setup = not cfg.get("enabled", True)` (:160-161) reads the normalized dict, so `enabled` is never present and the value is always False. Consequently `get_devices` never sets `schema["_disabled"]` (:223-224), so the JS disabled-tab styling (app.js:418-429) is dead. The `model.disable()`/`system_enabled=False` block (:162-165) runs for every model. Disabled devices are also built with port "None" (:95, :97) rather than skipped, so a real model with a poller is constructed for each.
- Failure scenario: In the wizard uncheck "Stepper Probe" and launch. The server still constructs a StepperProbe (with a ControllerPoller, probes.py:37-41) and registers it. The UI shows an active, clickable card for it. Red Percent lists it as a valid "Position Source" and auto-selects it as `stepper_model` (web_adapter.py:171-172), so sync data logs zeros from an unused probe. A direct API caller that disables every device gets a fully built system (`normalized_configs` is non-empty, :124), even though the JS guards this at app.js:1788-1791.
- Proposed fix direction: Carry `enabled` through normalization into the config dict and skip disabled devices in `build_models` input (match Tk), or set `_disabled_in_setup` from the ORIGINAL config. Preferably do not construct disabled devices. Reject all-disabled with 400 server-side. Add a unit test that asserts a disabled device is absent from `/api/devices` or flagged `_disabled`.
- Confidence: verified

### WEB-5: Poller log wiring only covers models that exist at WebDashboardWindow construction; the Controller Log modal is always empty; latent AttributeError on `pop`
- ID: WEB-5
- Severity: medium
- Views affected: Web
- Reference behavior: Tk routes poller logs to its `ControllerLogWindow` and `print` (tkinter/view.py:245-260, :550).
- Actual behavior: `WebDashboardWindow.__init__` iterates `system_manager.active_models` and sets `poller.log_updater` (web_view.py:56-65). In `run_web_app` the manager is empty at that point (app.py:774-780), so the loop body never runs. Models created later by `initialize_setup` get no `log_updater`. `adapter.append_log` has no other callers (grep: only web_server.py:255 via `_BufferProxy.append`, and web_view.py:61). Even if wired, the logger closure calls `WebAPIHandler.log_buffer.pop(0)` (web_view.py:63), but `_BufferProxy` defines only append/clear/__iter__/__len__/__getitem__ (web_server.py:251-289), so `pop` raises AttributeError once the buffer exceeds 500 entries. The poller also never runs (WEB-2), so no log lines would be produced anyway.
- Failure scenario: Open "Controller Log" in the browser. `/api/logs` always returns `[]`. Nothing indicates why. The schema button "Controller Log Window" (`open_controller_log`) merely prints on the server (probes.py:~185-186 `open_controller_log`).
- Proposed fix direction: Wire `log_updater` inside `initialize_setup` for each new model (`adapter.append_log` already caps at 500 and locks, so use it directly and drop the `pop` in web_view). Implement `pop` or remove the size-check closure. Hide/redirect the "Controller Log Window" button for web to the log modal.
- Confidence: verified

### WEB-6: Position Source dropdown sets `selected_probe_name` directly and never calls `set_stepper_model`
- ID: WEB-6
- Severity: medium
- Views affected: Web
- Reference behavior: Tk `_on_probe_selected` -> `system.set_stepper_model(selected)` (tkinter/view.py:691-692). PySide6 does the same (pyside/view.py:659-661).
- Actual behavior: The schema element is `{"type":"dropdown","model_attr":"selected_probe_name","options_command":"get_available_probe_names"}` with no `command` (redpercent_system.py:200). JS `btn-dispatch-dropdown` change handler takes the `attr` branch when `data-command` is empty: `setDeviceAttribute(dev, attr, val)` (app.js:716-727). The adapter does `setattr(model, "selected_probe_name", value)` (web_adapter.py:463). `stepper_model` (the object actually used for sync data, redpercent_system.py:312-324) is only set inside `set_stepper_model` (:91-95).
- Failure scenario: Operator selects "DC Probe" as Position Source. The UI (and next poll, app.js:870-876) shows "DC Probe". Monitoring keeps logging X/Y/Z from the Stepper Probe chosen at setup. The CSV silently contains the wrong probe's coordinates. Choosing the placeholder "Select option..." sends `""` and blanks `selected_probe_name`.
- Proposed fix direction: Add a schema `command: "set_stepper_model"` (allowlisted automatically via `_schema_commands`) for that dropdown, or make `selected_probe_name` a property whose setter calls `set_stepper_model`. Ignore empty-string selections in the JS.
- Confidence: verified

### WEB-7: Web plotter feeds BOTH `current_red` and `red_change` into the red-percent series; plotter baseline is client-only and lost on reload
- ID: WEB-7
- Severity: low
- Views affected: Web
- Reference behavior: Tk shows `current_red` and `red_change` as separate values (tkinter/view.py:759-762 in `poll_display`). The model owns baseline (`reset_baseline`, redpercent_system.py:282-284).
- Actual behavior: `pollState` calls `pushPlotterSample(val)` for every attr whose name contains "red" and whose value is a number (app.js:878-881). The RedPercent schema exposes `current_red` and `red_change` (redpercent_system.py:216-217), both match. Two samples are pushed per poll and one is a delta. Plotter data (`plotterData`, `baselineRed`, `peakRed`, app.js:34-41) lives only in the tab. The plotter modal's "Reset" only resets the client baseline (app.js:250-259) and is unrelated to `model.reset_baseline`.
- Failure scenario: The live "Current/Delta/Peak" plot is corrupted by interleaved red% and red-change values. Reloading or opening a second tab loses history and the baseline, so the two tabs and the model disagree.
- Proposed fix direction: Key the plotter on the exact attr `current_red` and on the RedPercent device only. Optionally expose a server-side history endpoint, or keep client-only but document it.
- Confidence: verified

### WEB-8: Every device's state read is serialized behind a per-device lock that dispatch also holds; state poll blocks on long commands and on slow serial; polling rate is per-tab and unbounded
- ID: WEB-8
- Severity: medium
- Views affected: Web
- Reference behavior: Tk polls `read_position` at 100 ms and `poll_status` at 100 ms from the UI loop (tkinter/view.py:568-582). Commands run on the UI thread.
- Actual behavior: `get_state` walks all devices sequentially, and for each takes `dev_lock`, calls `read_position()` and `poll_status()` (blocking serial I/O), and swallows exceptions (web_adapter.py:286-299). `dispatch_command` holds the same lock for the entire command (:402-411). `resolve_options` and `set_device_attribute` also take it (:371-372, :451-452). JS default poll interval is 20 ms (app.js:15) while the settings `<select>` marks 50 ms as selected (index.html:346-347), so the initial label/timer and control disagree. Each poll cycle fires `/api/state`, `/api/logs`, `/api/errors` (app.js:813-817). Each fetch is a new HTTP/1.0 connection (BaseHTTPRequestHandler default, web_server.py:16). Every open tab runs its own timer. `read_position` is therefore driven at (tabs x poll rate).
- Failure scenario: A slow command such as `reconnect_serial` (`time.sleep(1)`, probes.py:167) or a long serial write holds a device lock. `/api/state` blocks on that device, and because the loop is sequential, state for ALL later devices stalls too, so the whole UI freezes. `isPolling` (app.js:809-810) then skips ticks, so log and error polls also stall. Two or three open tabs multiply serial polling load onto the same port and can delay motion commands sharing the serial `RLock`.
- Proposed fix direction: Move `read_position`/`poll_status` to a per-model background sampler thread owned by the adapter (or server) at a fixed rate (~10 Hz) and have `/api/state` return the cached snapshot without touching locks. Do not hold a lock for the duration of long commands (or use separate command vs. read locks). Set the JS default to match the select. Add `Connection: keep-alive`/HTTP/1.1 or a lower default rate.
- Confidence: verified (structure). Actual stall duration is a runtime effect (UNVERIFIED HYPOTHESIS for magnitude; settle by timing `/api/state` while issuing `reconnect_serial`).

### WEB-9: `/api/errors` is a destructive pop, so multiple tabs split errors and reload/no-tab loses or replays them
- ID: WEB-9
- Severity: medium
- Views affected: Web
- Reference behavior: Tk/PySide raise a modal popup at the moment of error in the single window (ErrorRouter callbacks). Every error is seen once by the operator.
- Actual behavior: `GET /api/errors` -> `adapter.pop_errors()` copies and clears the buffer (web_adapter.py:485-489; web_server.py:109-111). JS toasts each one for 4.5 s (app.js:1017-1030, :1530-1535). The buffer is capped at 500 (web_adapter.py:479-483).
- Failure scenario: (1) Two tabs open: the tab whose poll fires first consumes the error, the other never sees it. A safety warning (e.g. "Manual Mode Blocked", "Enable Failed") can appear only on a background tab. (2) With no tab open, errors accumulate (up to 500), then a reload floods stale toasts. (3) A 4.5 s auto-dismissing toast is easy to miss for an error that in Tk requires an OK click. (4) The 5-second `ErrorRouter._is_spam` dedupe (error_routing.py:20-27) is global and shared across clients.
- Proposed fix direction: Give errors monotonically increasing ids and let each client request `?since=<id>` (non-destructive, bounded ring buffer). Keep errors/warnings sticky until dismissed (no auto-timeout) for `type=="error"`.
- Confidence: verified

### WEB-10: No CSRF / Origin / Host validation on state-changing endpoints (hardware can be driven by any web page the operator visits)
- ID: WEB-10
- Severity: high
- Views affected: Web
- Reference behavior: Tk/PySide have no network surface.
- Actual behavior: `do_POST` reads the body and calls `json.loads` regardless of `Content-Type` (web_server.py:151-157). There is no Origin, Referer, Host or token check anywhere. The comment at web_server.py:42-45 says omitting CORS headers prevents cross-site hardware control. That is not true for CSRF: a cross-origin `fetch(..., {method:'POST', mode:'no-cors', headers:{'Content-Type':'text/plain'}, body:'{"device":...}'})` is a "simple" request (no preflight), the server executes it, and only reading the response is blocked. Any schema-allowlisted command is reachable (`toggle_auton`, `macro_start_auton`, `toggle_manual`, `set_attr` on step sizes/speeds, `/api/setup/initialize` which tears down live models). The server binds to 127.0.0.1 by default (`WebDashboardServer` host default, web_server.py:301) so the vector is a malicious page in the operator's own browser (or DNS rebinding, since Host is not validated). `/api/screenshot` also serves the host screen (:124-143).
- Failure scenario: Operator has the dashboard running and browses an untrusted page. That page silently POSTs `macro_start_auton` and `set_attr full_speed` to `http://127.0.0.1:8080/api/...`. The stage starts moving.
- Proposed fix direction: Require `Content-Type: application/json` (reject others with 415, which forces preflight for cross-origin), validate `Origin`/`Host` against the bound host:port, and add a per-process random token sent as a header by the static JS (served from the same origin). Keep the default bind on loopback.
- Confidence: verified (code); browser behavior for simple requests is standard, not exercised here.

### WEB-11: Frontend calls commands that no model exposes (log console "send_raw_command", script runner "execute_script"); both fail with a 400
- ID: WEB-11
- Severity: low
- Views affected: Web
- Reference behavior: Tk `file_picker`/Run Script removed from the live dashboard per the comment at probes.py:144-146. No model defines `send_raw_command` or `execute_script` (grep of src/model finds neither).
- Actual behavior: The log console form dispatches `send_raw_command` to `Object.keys(this.devices)[0]` (app.js:1226-1238). The file-picker modal dispatches `execute_script` to the first stepper/probe (app.js:1240-1247). Neither is in any `ui_schema`, so `dispatch_command` returns 400 "Command ... not found" (web_adapter.py:398-400). The failure is a toast only. The file-picker modal (index.html:226-246) has no visible open button in the reviewed HTML (I did not find a caller of `toggleModal(fileModal, true)`; UNVERIFIED HYPOTHESIS that it is unreachable, settle with `grep -n "fileModal" app.js`, which shows only close/cancel/confirm bindings at 102-106, 273-299).
- Failure scenario: Operator types a raw serial command in the log console. The console echoes `> cmd` locally (app.js:1231), then the command is rejected with a red toast. The echo makes it look sent.
- Proposed fix direction: Remove the log-console input and the script modal, or implement `send_raw_command` on models behind a schema entry, with an explicit safety review.
- Confidence: verified (no such methods, allowlist rejection); reachability of the file modal is a hypothesis.

### WEB-12: Silent success toasts for commands the model refused or no-oped; rotation >30 deg is blocked with only a server print
- ID: WEB-12
- Severity: medium
- Views affected: Web
- Reference behavior: Tk/PySide wire `model.confirm_rotation_callback` to a Yes/No dialog for targets beyond +/-30 deg (tkinter/view.py:167, :198-199; pyside/view.py:922).
- Actual behavior: The web layer never sets `confirm_rotation_callback`. `RotatorSystem._confirm_rotation` auto-blocks with `print(...)` only (rotator_system.py:210-216). `dispatch_command` returns `{"status":"ok","result":str(res)}` for any non-raising call (web_adapter.py:405-411), and JS shows "`<cmd>` executed" (app.js:1154). `save_log_web` on Red Percent writes a timestamped CSV into the SERVER's working directory (redpercent_system.py:168-169, :241-259) and, with no data, prints "No data to save" and still returns ok.
- Failure scenario: Operator enters +45 deg and clicks Move: the move silently does nothing, with a green "executed" toast. Clicking "Save Log" reports success but nothing downloads to the browser (on a remote client the file is on the wrong machine). Contrast with Tk, where the operator gets a dialog and a save-as file chooser.
- Proposed fix direction: Return structured results from models (or raise a typed `CommandRefused`) and have the adapter map them to `status:"error"/"warning"`. For rotation, implement a web confirm flow (a `confirm_rotation_callback` that raises a pending-confirmation state the JS resolves, or ask the browser via a two-step request). Make `save_log_web` return the CSV text and have the JS trigger a download.
- Confidence: verified

### WEB-13: Red Percent stop does not offer to save unsaved data; unsaved monitoring data is lost on tab reload or process exit
- ID: WEB-13
- Severity: medium (data loss)
- Views affected: Web
- Reference behavior: Tk `stop_monitoring` prompts to save if `has_unsaved_data` (tkinter/view.py:761-769) and uses a save-as dialog (:774-780). PySide6 prompts on `stop_monitoring` (views.md notes).
- Actual behavior: The web "Stop Monitoring" button is a plain `stop_monitoring` dispatch (schema, redpercent_system.py:225-226) with no `has_unsaved_data` handling in JS or adapter. `RedPercentSystem.teardown` just stops monitoring (redpercent_system.py:276-277). Combined with WEB-1, shutdown never even reaches this.
- Failure scenario: Operator stops a long run and closes the tab. `data_log` stays in server memory with no prompt, and is lost at process exit.
- Proposed fix direction: After a successful `stop_monitoring` dispatch, JS queries `has_unsaved_data` (add to state) and offers a browser download of the CSV via a new endpoint.
- Confidence: verified

### WEB-14: Unhandled exceptions in GET/POST handlers drop the connection (no JSON error), and one bad attribute stalls all device state
- ID: WEB-14
- Severity: medium
- Views affected: Web
- Reference behavior: Tk catches per-widget poll errors locally.
- Actual behavior: `do_GET` routes call `adapter.get_state()`/`get_devices()`/etc. with no try/except (web_server.py:89-99). Inside `get_state`, only `read_position`/`poll_status` are guarded (web_adapter.py:290-299). `getattr(model, attr)` (:307), `_determine_connection_status` (:310) and `json.dumps` in `_send_json` (web_server.py:38) are not. Similarly `initialize_setup` does `cfg.get("mode", "").lower()` which raises AttributeError if a client sends `"mode": null` (web_adapter.py:94, :110). `BaseHTTPRequestHandler` would log a traceback and close the socket with no response.
- Failure scenario: Any property that raises, or an attribute whose value is not JSON-serializable, makes every `/api/state` call fail. JS sets "Connection Dropped" (app.js:927-929) and every device's values freeze at the last good render with no per-device staleness indication. This is a hypothesis about which attribute would trigger it (UNVERIFIED HYPOTHESIS; check by running `json.dumps` over `get_state()` for each model type in tests/web).
- Proposed fix direction: Wrap each per-device read in try/except and report `{"_error": "..."}` for that device only; wrap all route bodies in a try/except returning JSON 500; use `json.dumps(default=str)`. Validate input types in `initialize_setup` and return 400.
- Confidence: hypothesis (trigger), verified (absence of guards)

### WEB-15: Setup wizard differs from Tk: no discovery of device type, "Virtual Controller" fallback fabricated, subprocess uses `python3` not `sys.executable`
- ID: WEB-15
- Severity: low
- Views affected: Web
- Reference behavior: Tk setup does threaded per-port `probe_device_at` autodetect, marks "Not Found" (app.py:340-348) and lists real controllers via pygame in-process (app.py:137-153).
- Actual behavior: `scan_hardware` returns ports (SIM plus `discover_ports()`) only, without autodetect (web_adapter.py:39-56, docstring admits it), and shells out to `python3 -c` with pygame to list joysticks (:59-61). If none are found it appends "Virtual Controller A/B" (:69-70), which `validate_assignment` and `ControllerPoller` treat as "no controller" (app_bootstrap.py:190; gamepad.py:373). The scan is a blocking GET with a 3 s subprocess timeout. `python3` may resolve to a different interpreter than the venv running the app (UNVERIFIED HYPOTHESIS; settle by checking `which python3` vs `sys.executable` in the launch environment). The setup JS defaults every device to SIM, never pre-selects a detected port, and on re-open resets all rows to defaults (app.js:24-31, :1541-1544).
- Failure scenario: With no joystick attached, the picker offers two "Virtual" controllers that behave as None. With a venv-only pygame, the subprocess scan may report zero controllers even though one is attached.
- Proposed fix direction: Use `sys.executable` (or scan in-process via `ControllerPoller.get_physical_controllers`). Add an async scan (background thread + polling) that also runs `probe_device_at`. Remember current config when re-opening the wizard.
- Confidence: verified (code); interpreter mismatch is a hypothesis.

### WEB-16: Server `start()` does not handle port exhaustion; `WebDashboardWindow.show()` prints a possibly wrong URL
- ID: WEB-16
- Severity: low
- Views affected: Web
- Reference behavior: n/a.
- Actual behavior: `start()` loops 10 times catching every `OSError` and incrementing the port (web_server.py:324-329). If all 10 fail `self.server` remains None and `threading.Thread(target=self.server.serve_forever)` raises AttributeError (:331-333). Any OSError (not just EADDRINUSE) is silently treated as "port busy".
- Failure scenario: Ports 8080-8089 busy: crash with an unhelpful AttributeError; or an unrelated bind error (permission, bad host) is masked.
- Proposed fix direction: Catch only `errno.EADDRINUSE`; after the loop raise a clear RuntimeError.
- Confidence: verified

### WEB-17: `showToast` and several render paths inject server-derived strings via innerHTML (reflected/persistent XSS)
- ID: WEB-17
- Severity: low
- Views affected: Web
- Reference behavior: Tk/PySide render text as plain labels.
- Actual behavior: `showToast` puts `message` into `innerHTML` unescaped (app.js:1515-1518). Messages contain exception text and device/command names from the server (e.g. app.js:1151, :1025, and server messages such as web_adapter.py:400). `card-title`, `section-title`, and sidebar labels also use raw `devName`/`sec.title` in innerHTML (app.js:~455, ~500, ~445-447). `escapeHtml` exists (app.js:1835) and is applied only to option and label text.
- Failure scenario: An exception message with markup (or an attacker who reached WEB-10) renders HTML in the dashboard.
- Proposed fix direction: Use `textContent` for toast messages and escape all interpolated strings.
- Confidence: verified

### WEB-18: Global FULL STOP toast is shown before the request result; adapter returns ok even if individual models failed; no UI-side stop when the server is unreachable
- ID: WEB-18
- Severity: medium
- Views affected: Web
- Reference behavior: Tk FULL STOP calls `system_manager.full_stop_all()` synchronously (tkinter/view.py:188).
- Actual behavior: JS shows "GLOBAL EMERGENCY STOP BROADCASTED" before awaiting the fetch (app.js:1206-1209), so success is displayed before it is known. `SystemManager.full_stop_all` catches each model's exception and routes it to `ErrorRouter` (system_manager.py:70-78), so `adapter.full_stop_all` returns `status: ok` (web_adapter.py:426-427) even if one model failed to stop. The failure surfaces only later as a toast via the destructive error poll (WEB-9), which is timing-dependent. `full_stop_all` correctly does NOT take the per-device locks (positive: it is not blocked by WEB-8), but concurrent `power_down` writes `ser.write(b'k\n')` outside the serial layer's `RLock` (probes.py:472-475), racing a `read_position` in the state poll. That race is model-side and flagged for the model audit.
- Failure scenario: A stop fails on one device but the operator sees only "BROADCASTED". If the server is hung or unreachable, the button gives a network-error toast and there is no local recourse.
- Proposed fix direction: Show a pending state and confirm from the response. Return per-device results from `full_stop_all` (collect exceptions) in the JSON. Ensure it bypasses the state-poll path. Consider a keyboard shortcut (Esc/Space) for the stop button.
- Confidence: verified

### WEB-19: Session semantics for reload / second tab / tab close are undefined and partly wrong
- ID: WEB-19
- Severity: medium
- Views affected: Web
- Reference behavior: One process = one window = one model set (Tk/PySide). Closing the window = `shutdown_all()` (tkinter/view.py:226-230).
- Actual behavior:
  - Page reload: `init()` -> `/api/system/status` -> if "running", `fetchDevices()` (app.js:134-157). No model touch. Correct. Client-only state (plotter history, log list, baseline, toast history, setup selections) is lost (app.js:17-41).
  - Second tab: sees the same running system. State/logs shared, but errors are consumed (WEB-9), polling load multiplies (WEB-8), and the second tab can re-run setup (WEB-3). On a fresh server, opening two tabs in setup mode lets both wizards run initialize concurrently (no lock).
  - Tab close: nothing happens. The stage stays enabled and moving, with no watchdog tied to a live client. This is arguably correct for a server, but it has no "client heartbeat" so an auton run continues with no observer. The idle-interlock only covers `BaseProbe` and defers during manual/stepping (probes.py:409-412).
  - Server shutdown: WEB-1.
- Failure scenario: Operator starts an autonomous run, closes the laptop lid (browser suspended), and no client is watching errors or the stop button.
- Proposed fix direction: Document the "server owns models" contract in docs. Add optional last-seen-client timestamp (touch on each `/api/state`) and, if a run is active and no client has polled for N seconds, emit a warning and optionally `full_stop_all`. Make setup single-flight (WEB-3).
- Confidence: verified (code paths); heartbeat proposal is a design suggestion.

### WEB-20: Web reads `SystemManager.active_models` directly (unlocked) and holds stale references across setup swaps
- ID: WEB-20
- Severity: low
- Views affected: Web
- Reference behavior: Tk uses `get_active_models_snapshot()` (tkinter/view.py:171).
- Actual behavior: `get_devices` iterates the live dict `models.items()` (web_adapter.py:220-221), `resolve_options`/`dispatch_command`/`set_device_attribute` use `.active_models.get(...)` (:362, :392, :444) without `SystemManager.lock`. `dispatch_command` captures `func = getattr(model, ...)` under the adapter lock and invokes it later on the device lock (:398-411). If `initialize_setup` swaps and tears down the manager in between, the command executes on a torn-down model (serial closed, poller closed). `_device_locks` is keyed by device NAME and is reused across the swap (:29-33), so old and new models share one lock (benign, but hides the swap).
- Failure scenario: A command arrives during re-setup and runs on a closed serial port. The result is either a swallowed exception or a warning toast.
- Proposed fix direction: Use `get_model`/`get_active_models_snapshot`. Include a generation counter in the adapter and re-check it after taking the device lock (abort with 409 if the manager changed).
- Confidence: verified (structure); race outcome is a hypothesis.

### WEB-21: `/api/plot` and `/api/screenshot` run heavy work on the request thread without limits
- ID: WEB-21
- Severity: low
- Views affected: Web
- Reference behavior: n/a.
- Actual behavior: `do_POST` reads `Content-Length` bytes with no upper bound (web_server.py:151-152). `/api/plot` parses and renders a matplotlib `Figure` per request (web_server.py:161-184). `Figure` (not pyplot) is used, so no global pyplot state (plot_data.py:70-71), and the figure is never explicitly closed (garbage-collected). `/api/screenshot` grabs the full host monitor on every call, and the ROI dialog polls it every 500 ms (app.js:1290-1293). `mss` and `PIL` are imported at module top (web_server.py:7-10), so the whole web view fails to import if either is missing.
- Failure scenario: A large CSV upload or repeated screenshot polling in several tabs adds CPU and memory load on the same process that owns the hardware loop.
- Proposed fix direction: Cap body size, lazy-import `mss`/`PIL` inside the handlers, and reuse a single `mss` instance.
- Confidence: verified

### WEB-22: Client polling has no timeouts, no per-device staleness marker, and Set Focus Area / setup use fetch results without abort
- ID: WEB-22
- Severity: low
- Views affected: Web
- Reference behavior: Tk updates widgets in-process; stale values are impossible because reads are direct.
- Actual behavior: No `AbortController` on any fetch (app.js:141, :370, :827, :1000, :1019, :1133, :1173, :1209, :1305, :1571, :1797). `runPollCycle` awaits all three requests (`Promise.all`, app.js:813-817), so a single slow `/api/state` stalls log and error polling, and `isPolling` skips further ticks. On non-OK, only the header connection pill changes (app.js:828-831, :927-929); the last device values stay on screen as if live. `pollState` updates entry inputs via `placeholder` only (app.js:849-852), which is correct for not stomping edits, but a value edited by another tab or model shows only as placeholder text once the input has content. Devices are rendered once (`fetchDevices` on init, setup and manual refresh only), so schema changes such as the options list for `get_available_controllers` or `get_available_probe_names` (populated once, app.js:654-670) become stale after hot-plugging a gamepad.
- Failure scenario: Server hangs on one device lock: the UI shows "Server Error"/"Connection Dropped" in the header but every card keeps showing old positions with no per-card marker.
- Proposed fix direction: Add fetch timeouts, decouple the three polls, mark cards stale after N failed cycles (gray out and disable command buttons), and refresh dropdown options on focus.
- Confidence: verified

---

## Section-by-section answers to the assignment

1. Lifecycle vs. ManagedModel/SystemManager contract: see Object summary plus WEB-1, WEB-3, WEB-19, WEB-20. The web layer honors `register_model`, `shutdown_all`, and `full_stop_all` in principle. It never uses `remove_model` or `reboot_model` (fine for now). Violations are shutdown on the wrong manager (WEB-1), teardown-after-build ordering (WEB-3), and no process-signal teardown. There is no `ManagedModel` type check anywhere. `teardown`/`emergency_stop` are invoked only through SystemManager (system_manager.py:14-16, :70-78).
2. State sync: pure client polling (`setInterval`, app.js:804), no WebSocket/SSE. Three endpoints per cycle. See WEB-8, WEB-9, WEB-22. Staleness: all values are recomputed on each `/api/state` (read_position on the request thread). There is no cache/age marker.
3. Concurrency: per-device `threading.Lock` shared by state reads and commands; `_state_lock` (RLock) guards only manager reference and buffers; FULL STOP bypasses device locks (good). See WEB-8, WEB-18, WEB-20.
4. Missing/dead endpoints and controls: frontend calls `send_raw_command`/`execute_script` (dead, WEB-11); "Controller Log Window" and "Save Log" are server-side-only actions (WEB-5, WEB-12); position source dropdown does not apply (WEB-6). Backend endpoints all have callers: `/api/devices`, `/api/state`, `/api/system/status`, `/api/setup/scan`, `/api/logs`, `/api/errors`, `/api/options`, `/api/screenshot`, `/api/plot`, `/api/setup/initialize`, `/api/command`, `/api/set_attr`, `/api/system/full_stop` (each matched in app.js). Missing versus Tk: rotation confirm dialog (WEB-12), save-on-stop prompt (WEB-13), focus-loss neutral flush (no `visibilitychange`/`blur` handler in app.js, moot until WEB-2 is fixed), gamepad manual routing (WEB-2), per-device Full Stop button (removed by design, probes.py:~128-133).
5. Setup flow / disabled probes: WEB-3, WEB-4, WEB-15. Probe linking for Red Percent is present (web_adapter.py:166-174) but includes disabled probes because of WEB-4.
6. Error surfacing: WEB-9, WEB-12, WEB-14, WEB-17, WEB-18. `WebErrorManager` wiring itself (web_view.py:8-39) works. `ErrorRouter` callbacks push into the adapter buffer from arbitrary model threads (locked by `_state_lock`).
7. Shutdown/emergency stop: WEB-1 (shutdown), WEB-18 (FULL STOP).

## Coverage

Read in full: src/views/web/web_adapter.py (1-489), web_server.py (1-341), web_view.py (1-78), __init__.py (1-12), docs/architecture/libs-and-web.md (1-134). src/app.py 1-30, 340-460, 755-915 (skimmed run_web_app and launcher). src/app_bootstrap.py 100-195. src/model/system_manager.py (all), base.py (all). Targeted reads: probes.py 1-80, 96-250, 340-395, 395-500; redpercent_system.py 40-110, 150-290; rotator_system.py 1-130, 195-225, 260-300; temperature_system.py 15-45, 190-205; controller/gamepad.py 251-270, 350-372, 474-515, 515-640; controller/serial.py 20-100; error_routing.py 1-60; tkinter/view.py 165-300, 530-600, 590-720, 761-835; views.md 60-110, 185-200. src/views/web/static/js/app.js: 1-160, 159-470 (partial, 470-560 partial), 600-1260, 1250-1850. index.html: grep of buttons plus 40-70, 226-262, 340-353. `git show main:src/mainGUI.py` lines 340-400 only (main is a multiprocess launcher with no web view).

Not read or only grepped: styles.css (skimmed only via absence; no findings), app.js lines ~560-625 (element renderers for entry/readonly/toggle/button), the rest of index.html, tests/web/*, pyside view (out of scope), and hardware behavior at runtime (nothing executed). `libs-and-web.md` Part 1 (SMC100/toupcam) is out of scope. I did not verify the file-picker modal's opener (WEB-11 caveat) or the exact `grep` result for the "Web skips RedPercent probe wiring" claim location.

DONE view-web 22
