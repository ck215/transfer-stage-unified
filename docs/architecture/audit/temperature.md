# Audit: TemperatureSystem (temperature)

## Object summary

- Class: `src/model/temperature_system.py` `TemperatureSystem` (202 lines). Serial 115200 to `firmware/temp_controller/temp_controller.ino`.
- Constructor sites:
  - Tk + PySide startup: `app_bootstrap.build_models` `src/app_bootstrap.py:156-158` (`TemperatureSystem(port)`), registered into a `SystemManager` at `src/app.py:405-420` (Tk) and `src/app.py:735-748` (PySide).
  - Web: `WebModelAdapter.initialize_setup` -> `build_models` at `src/views/web/web_adapter.py:148`, registered into a NEW `SystemManager` (:159).
  - PySide re-open only: `src/views/pyside/view.py:893-895` constructs `TemperatureSystem(None)` (always simulated, see TEMP-6).
- Owner: `SystemManager.active_models` (`src/model/system_manager.py:7`).
- Model teardown surface: `close()` :181, `disconnect()` :194, `teardown()` :198 all -> `close()`; `emergency_stop()` :201 -> `stop()` :160. Neither `disable` nor `poller` exists on this model.
- Threads: one daemon `serial_thread` (`read_serial_data` :99) started in `__init__` :36-37 ONLY if the port opened.
- Teardown call paths per view:
  - Tk: `DashboardWindow.on_close` `src/views/tkinter/view.py:226-232` -> `system_manager.shutdown_all()` -> `teardown()`. Tab close (X / middle-click / right-click) does NOT tear down (TEMP-5).
  - PySide: `closeEvent` `src/views/pyside/view.py:957-967` -> `shutdown_all()`; per-dock close `close_device_view` :813-847 hand-rolls (`model.disconnect()` :843, `del active_models[...]` :845-846).
  - Web: `WebDashboardWindow.close` `src/views/web/web_view.py:74-78` -> `shutdown_all()` on the WRONG manager (TEMP-1). Browser-tab close does nothing.
- FULL STOP: Tk `view.py:188`, PySide `view.py:758`, Web `web_adapter.py:420-431` -> `SystemManager.full_stop_all` (`system_manager.py:70-78`) -> `emergency_stop()` -> `stop()`.
- Firmware facts verified (relevant to "heater off" semantics): `parseData` `temp_controller.ino:246-262` sets endpoint, spdelay, kp, ki, kd, offset from a `<a,b,c,d,e,f>` frame. Heater is cut when `newdelay <= 0` (:107-ish `else if(newdelay <= 0 || temp-endpoint > 10)`). `newdelay = kp*err + ki*sum*.008333 + kd*diff` (:141-149), so `kp=ki=kd=0` gives `newdelay=0` and the heater is off. There is NO host-liveness watchdog in the firmware (grep for timeout/watchdog found nothing): if the host dies, the last command persists. Every accepted frame resets `setpoint = current temp` (`showNewData` :232-242).
- Safety semantics confirmed: `stop()` (:160-179) and `close()` (:186) both send P=I=D=0, endpoint 0 -> heater off on the wire. Docs (`models.md:244`, `known-issues.md:79`) are right on this point.
- No temperature plot exists in ANY view or in `plot_data.py`, and none existed on main (see TEMP-9).

## Findings

### TEMP-1
- ID: TEMP-1
- Title: Web shutdown (Ctrl+C) tears down the stale/empty initial SystemManager; temperature heater is never commanded off
- Severity: high
- Views affected: Web (plus Controller entry `src/app.py`)
- Reference behavior: Tk `on_close` `src/views/tkinter/view.py:226-228` and PySide `closeEvent` `pyside/view.py:966` call `shutdown_all()` on the manager that owns the live models; main `git show main:src/temp_control.py:147-166` `stop_plot` writes `<0,10,2.0,0.5,.1,0>` before closing.
- Actual behavior: `run_web_app` builds an empty `manager` (`src/app.py:~775`) and `WebDashboardWindow(manager,...)` (`app.py:780`). The view stores it (`web_view.py:47`) and passes it to `WebDashboardServer` which creates a private `WebModelAdapter(system_manager)` (`web_server.py:304-313`). `initialize_setup` then builds models into a NEW manager and swaps `self.system_manager = new_manager` in the adapter only (`web_adapter.py:148,159,175-179`). `WebDashboardWindow.close()` (`web_view.py:74-78`), invoked on Ctrl+C at `app.py:804` and on exception at `app.py:809`, calls `self.system_manager.shutdown_all()` on the original, permanently empty manager. No `atexit`, `signal.signal`, or `aboutToQuit` handler exists anywhere in `src` (grep verified, excluding `src/lib`).
- Failure scenario: In Web mode user sets setpoint 150 C, clicks Enter Settings, then Ctrl+C in the terminal (or kills the process). `TemperatureSystem.teardown()` never runs, so no `<0,6.0,0,0,0,0>` is sent; the firmware keeps the last endpoint/PID indefinitely (no host watchdog). Heater is left on (unattended). Same for every other model (out of scope) but this is the worst case for temperature.
- Proposed fix direction: Make `WebDashboardWindow.close()` shut down `self.server.adapter.system_manager` (or `WebAPIHandler.adapter.system_manager`), not its own constructor arg; have the adapter own a `shutdown()` that calls `shutdown_all()` on its current manager. Additionally register `atexit` and SIGTERM/SIGINT handlers in `run_web_app`. Consider also a firmware-side host watchdog (out of scope for the model).
- Confidence: verified (code path). Consequence "heater keeps running" depends on the firmware lacking a watchdog (verified by reading the .ino).

### TEMP-2
- ID: TEMP-2
- Title: Reader thread "gives up" after 5 failures with no real backoff, no restart path, and the UI keeps showing a frozen temperature and "hardware" status
- Severity: high
- Views affected: Model, Tk, PySide, Web
- Reference behavior: main `temp_control.py:91-102` gives up on the FIRST exception (`break`) with only a print; no user-visible state either. Refactor intent (per docs `ownership-and-lifecycle.md:166` "backoff") is resilience.
- Actual behavior: `read_serial_data` `temperature_system.py:99-127`. Retry delay is a fixed `time.sleep(0.1)` (:127), so "5 consecutive failures" = about 0.4-0.5 s of USB/serial glitch, then `break` (:122) and the thread is dead for the rest of the session. There is no exponential backoff (docs claim of "backoff" is inaccurate: it is a fixed-delay retry counter). `consecutive_failures` is reset at :112 after ANY non-exception loop pass, including the `else: time.sleep(0.1)` branch (:110-111) taken when `ser.is_open` is False, so a port that reports closed spins forever silently with no error. After give-up nothing resets `current_temp`, nothing changes `self.serial_conn.ser.is_open`, nothing exposes a reconnect command in `ui_schema` (:39-68). User sees: up to 5 popups (4 "Temperature Read Error" + 1 "(Fatal)"; each message text is unique via "retry n/5" so `ErrorRouter._is_spam` (`error_routing.py:16-26`) does not collapse them; Tk `messagebox` popups queue serially, PySide `QMessageBox` modal, Web toasts/buffer), then the tab looks normal: `current_temp` frozen at last value, Web badge stays HARDWARE (`web_adapter.py:228-276` derives status from `ser.is_open`, which stays True), "Enter Settings" still writes.
- Failure scenario: Heating to 200 C; USB hub glitches for 0.5 s -> reader thread dies. Operator sees the old temperature unchanged while the plate keeps heating (the heater remains under firmware control at the last setpoint). Operator believes the reading is live. Only Stop System / FULL STOP / quitting stops it.
- Proposed fix direction: (a) Real backoff (0.1 -> 0.2 -> ... up to ~2 s) and retry indefinitely, or on give-up attempt a serial reopen; (b) do not reset the counter in the `else` (port-closed) branch, count that as a failure too; (c) on give-up set `self.current_temp = "Disconnected"` (or a `connection_status` attr shown in the schema) and expose a `reconnect` command; (d) add `last_sample_time` and render "stale" after N seconds; (e) consider sending a heater-off frame when the read loop dies.
- Confidence: verified

### TEMP-3
- ID: TEMP-3
- Title: Web set_attr accepts unvalidated strings for setpoint/PID/ramp/offset, allowing empty/garbage values that shift the firmware's strtok fields
- Severity: high
- Views affected: Web (Model has no validation either)
- Reference behavior: Tk numeric entries reject empty/NaN/inf and revert (`tkinter/view.py:345-360`, `on_finish`); PySide `commit` `pyside/view.py:247-256` does `float(text)` and reverts on failure. main sent raw entry text but had no API surface.
- Actual behavior: `WebModelAdapter.set_device_attribute` `web_adapter.py:433-467`: `curr` is a `str` for every temperature attribute (`temperature_system.py:11-16`), so no cast branch runs and `setattr(model, attr, value)` (:463) stores whatever the client sent (empty string, "abc", "1,2", ">", "inf"). JS sends `inputEl.value` verbatim (`app.js:670-682`, `setDeviceAttribute` :1165-1180). `send_settings` (:92) interpolates the raw strings into `<{setpoint},{spdelay},{p},{i},{d},{offset}>`. Only `ramp_rate` is sanitized via `num()` (:75-82). The firmware uses `strtok(receivedChars, ",")` (`temp_controller.ino:248-262`), which collapses consecutive delimiters, so an empty setpoint field produces `<,10.00,2.0,0.5,.1,0>` -> endpoint=10.00, spdelay=2.0, kp=0.5, ki=.1, kd=0, offset stays previous. A value containing "," or ">" also injects/terminates frames.
- Failure scenario: In Web, user clears the Setpoint box and clicks Set (or presses Enter in the box, `app.js:684-694`), then Enter Settings: the firmware receives shifted fields (endpoint = ramp rate, e.g. 10 C, PID gains garbled, kp=0.5 instead of 2). Or setting P to "1,90" sends an extra field that changes the offset semantics. The plate is driven to an unintended target with mis-tuned gains.
- Proposed fix direction: Validate in the model, not per view: give `TemperatureSystem` property setters (or a `set_param(name, value)`) that use `num()`/`safe_float` and reject empty/NaN/inf/non-numeric and reject non-numeric strings before assignment; in `send_settings` refuse to write when any field fails `safe_float` and surface an error; optionally clamp setpoint to a configured max. Additionally make `set_device_attribute` reject values for numeric-looking string attrs that do not parse.
- Confidence: verified (adapter/JS/model path and firmware strtok semantics read); the effect of strtok collapsing empty tokens is standard C library behavior (not run on hardware).

### TEMP-4
- ID: TEMP-4
- Title: Web "Enter Settings" sends stale committed values: entry boxes are never auto-committed (no Tk-style focus flush) and never show the current value
- Severity: medium
- Views affected: Web, PySide (hypothesis)
- Reference behavior: Tk `DynamicView._execute_command` `tkinter/view.py:482-497` calls `self.focus_set()` first so the pending numeric edit commits on FocusOut before the command reads the model (fix commit b42c13e). Numeric entries also commit on `<Return>` (:361).
- Actual behavior:
  - Web: entries are plain `<input>` + separate "Set" button per field (`app.js:590-599`); the "Enter Settings" dispatch (`app.js:698-704`) sends no input values; `pollState` only writes `placeholder` (`app.js:842-853`), never `.value`. So a value typed but not "Set" is silently ignored and the frame is sent with the old model values. Multi-field edit requires six Set clicks.
  - PySide: numeric entries commit on `editingFinished` (`pyside/view.py:247-261`); `_execute_command` (:377-398) has no focus flush. UNVERIFIED HYPOTHESIS: on macOS QPushButton defaults to Qt::TabFocus so a mouse click does not take focus and `editingFinished` does not fire before `clicked`, reproducing the Tk one-cycle-behind bug (known-issues #4). Check: run PySide on macOS, type a new Ramp Rate, click Enter Settings without pressing Return/Tab, inspect the console print at `temperature_system.py:85`.
- Failure scenario: User types setpoint 80 in Web, clicks "Enter Settings" (the obvious button), and the previous setpoint (e.g. 0 or a lower target) is sent while the UI toast says nothing wrong. Operator thinks heating to 80 has started.
- Proposed fix direction: Web: on any dispatch of a command on a device, first flush all non-empty `.schema-input` of that card through `set_attr` (await them), or add a "send with values" API taking the whole form; show the committed value in `.value`. PySide: in `QtDynamicView._execute_command` call `self.setFocus()`/`clearFocus()` on the focused `QLineEdit` (or invoke its commit) before dispatch.
- Confidence: verified (Web); hypothesis (PySide macOS focus).

### TEMP-5
- ID: TEMP-5
- Title: Tk tab close only hides the tab; the temperature model stays running and heating with no visible UI
- Severity: medium
- Views affected: Tkinter
- Reference behavior: PySide dock close disconnects the model (heater-off frame + port close) `pyside/view.py:813-846`.
- Actual behavior: `DraggableClosableNotebook.close_tab` (`tkinter/view.py:158-162`) calls `on_close_tab_callback` if set, else `self.forget(index)`. `on_close_tab_callback` is initialised to `None` (:120) and never assigned anywhere (grep: only lines 120 and 159-160). So middle-click / right-click "Close Tab" just `forget`s the tab: the model stays in `SystemManager`, serial thread continues, the last setpoint stays active, and the DynamicView's `after` poll loop keeps running on the orphaned frame (`view.py:512-540`). There is no way to re-open the tab or reach Stop System except FULL STOP or closing the dashboard.
- Failure scenario: User is heating to 120 C, closes the "Temperature Controller" tab to declutter. The heater keeps running unseen; the temperature readout and Stop button are gone. Only FULL STOP (`view.py:188`, works because the model remains registered) or dashboard close halts it.
- Proposed fix direction: Set `notebook.on_close_tab_callback` in `DashboardWindow.__init__` to a handler that, for the tab's model, calls `system_manager.remove_model(name)` then `model.teardown()` (via `SystemManager` primitives), cancels the view's `after` loop, then `forget`s the tab. Or disable tab closing.
- Confidence: verified

### TEMP-6
- ID: TEMP-6
- Title: PySide dock close hand-rolls model destruction (bypasses SystemManager), and re-opening constructs a SIMULATED TemperatureSystem(None), losing the real port
- Severity: medium
- Views affected: PySide
- Reference behavior: `SystemManager.remove_model` (`system_manager.py:24-27`) + `teardown()` (`:18-22`); Tk keeps the model.
- Actual behavior: `close_device_view` `pyside/view.py:813-846`: calls `model.disconnect()` (:843; for temperature this sends the zero frame and closes the port, OK for heater safety) then `del self.system_manager.active_models[device_name]` (:845-846) directly, without `remove_model` and outside `SystemManager.lock`. `open_device_view` (:858-915) then finds no model and builds `TemperatureSystem(None)` (:893-895): `port=None` -> `serial_conn=None`, so the reopened tab is a dead simulation: `current_temp` stays "N/A" forever, Enter Settings silently does nothing, no error/warning. The original configured port is not remembered anywhere.
- Failure scenario: With real hardware, user unchecks then re-checks "Temperature Controller" in the sidebar: the tab returns, looks normal, but is not connected. Setpoints entered never reach the plate (silent), while the previous session's port is closed (which did zero the heater, so no hazard, but a control-loss surprise).
- Proposed fix direction: Route through `system_manager.remove_model(name)` + `_teardown_model`; remember the original config (`port`) in the manager or model, and on reopen re-create with that port (or `reboot_model(name, TemperatureSystem, port)`). Show "not connected" state when the model has no serial connection.
- Confidence: verified

### TEMP-7
- ID: TEMP-7
- Title: Web full_stop_all runs without the per-device lock; can race an in-flight send_settings so heater-on lands after the stop
- Severity: medium
- Views affected: Web
- Reference behavior: Tk/PySide are single-threaded on the GUI thread for both actions, so stop cannot interleave with send.
- Actual behavior: `dispatch_command` holds `dev_lock` while calling `send_settings` (`web_adapter.py:379-417`), but `full_stop_all` (:420-431) calls `system_manager.full_stop_all()` without taking any device lock. `ThreadingHTTPServer` (`web_server.py:296-298`) runs requests concurrently. `send_settings` computes its frame string (:92) and writes (:94); `stop()` sets `self.setpoint = "0"` (:162) and writes (:176). If the Enter Settings request has already read `self.setpoint` (:92) before `stop()` assigns "0", its write can follow the stop's write and re-arm the heater. Both write to `serial_conn.ser` directly, bypassing the serial wrapper's `RLock` (`controller/serial.py:35`) (also docs "Raw Serial Bypass").
- Failure scenario: Operator double-fires Enter Settings and FULL STOP in quick succession; the firmware ends on the heat frame while the UI shows setpoint 0.
- Proposed fix direction: Give `TemperatureSystem` its own write lock (or use the `serial_conn._lock`/a wrapper `write()`), make `send_settings` and `stop` mutually exclusive and add an `_estopped` flag that `send_settings` checks; take device locks in `full_stop_all` or make it lock-free by design in the model.
- Confidence: hypothesis (race window is narrow; ordering not exercised). Code paths verified.

### TEMP-8
- ID: TEMP-8
- Title: Web re-setup builds new TemperatureSystem on the same port BEFORE tearing down the old manager's model
- Severity: medium
- Views affected: Web
- Reference behavior: `SystemManager.reboot_model` (`system_manager.py:34-57`) tears down the old model first, then constructs.
- Actual behavior: `initialize_setup` builds all new models (`web_adapter.py:148`) and registers them (:159), swaps managers (:175-179), and only then calls `old_manager.shutdown_all()` (:184-185). `validate_assignment` (`app_bootstrap.py:168-193`) only checks collisions within the new config, not against the still-open old models. So two `TemperatureSystem`s (two `pyserial.Serial` handles, two reader threads) exist on one port during the overlap; the old model's `close()` (`temperature_system.py:181-192`) then writes `<0,6.0,0,0,0,0>` to the shared device AFTER the new model's initial frame/handshake and closes its own fd.
- Failure scenario: User re-runs setup in the browser with hardware. On Windows the second open fails (`serial.py:93-96` "Operating blind": new model gets `ser=None`, dead tab); on POSIX two readers split the telemetry lines (missing samples) and the old model's zero-frame may hit after the user's first Enter Settings if it is fast.
- Proposed fix direction: Tear down the old manager (`shutdown_all`) BEFORE `build_models` (with a guard to restore on failure), or compare ports and reuse.
- Confidence: hypothesis for the consequences (platform-dependent); ordering verified.

### TEMP-9
- ID: TEMP-9
- Title: Temperature history arrays and get_history() are dead: no view plots or reads them; no temperature plot exists anywhere (main did not have one either)
- Severity: low
- Views affected: Tk / PySide / Web / Model
- Reference behavior: main `temp_control.py:23-27,124-133` filled `tempC/time/sp` and never plotted them (only updated a label at :136); the `.ino` marks the serial line "PLOT THIS" (`temp_controller.ino:181`). No plotting on main either.
- Actual behavior: `temperature_system.py:21-24,140-151` appends under `self._lock`; `get_history()` :155-158 returns copies under the lock; repo-wide grep for `get_history|tempC` in `src/**/*.py|js` finds no caller outside the model. `src/model/plot_data.py` contains only red-percent code (`parse_red_percent_csv`, `render_red_percent_figure`); Web `/api/plot` and `app.js:1073-1140` handle only the red-percent CSV modal. `get_state` only forwards `ui_schema` attrs (`web_adapter.py:298-306`), which excludes history. Lock coverage itself is correct: all mutation of the 3 lists and `cnt` is in one `with self._lock` (:140-151) and the only reader takes the lock. The list is capped at 200 samples (length stays 200 once `cnt > 200`, correct). `current_temp` is written inside the lock but read by views without it (:151; atomic str assignment, benign). Views read `current_temp` by polling: Tk `_poll_model` 50 ms (`tkinter/view.py:512-533`), PySide QTimer 50 ms (`pyside/view.py:401-435`), Web `/api/state` poll `app.js:825-853` (interval from `setPollingInterval`). The 200-sample window is also small (about 2 min at ~0.58 s/line).
- Failure scenario: The lab user expecting a live temperature trace (the firmware comment and the arrays imply one) gets only a text readout; history is silently discarded beyond 200 samples with no export.
- Proposed fix direction: If a plot is wanted, add `get_history()` consumers per view (Tk: after-poll + matplotlib `FigureCanvasTkAgg`; PySide: `FigureCanvasQTAgg` + QTimer; Web: extend `/api/state` or add `/api/temperature/history`); add a `render_temperature_figure(t, temp, sp)` to `plot_data.py` mirroring the red-percent pattern. Otherwise remove the dead arrays/lock.
- Confidence: verified

### TEMP-10
- ID: TEMP-10
- Title: Stale/blank readout states: SIM or no-port shows "N/A" forever, Enter Settings is silent, frozen value has no staleness indicator
- Severity: low
- Views affected: Tk / PySide / Web / Model
- Reference behavior: main `temp_control.py:78-80` prints "Serial port is not open." on Enter with no port and shows a messagebox at startup (:35). Refactor serial wrapper reports `report_info("Simulator Mode", ...)` (`controller/serial.py:41-45`) once.
- Actual behavior: `send_settings` (:84-97) has no `else` branch: with `serial_conn=None` or `ser=None` (SIM, open failure) it does nothing, no message. Init writes/thread start are skipped entirely (:29-37). `current_temp` is initialised "N/A" (:18) and is only ever updated from telemetry (:151). In SIM there is no simulated data. `stop()` still zeroes `self.setpoint` (:162) so the UI shows a stop that did not go anywhere.
- Failure scenario: Simulation demo or failed port open: operator presses Enter Settings / Stop and sees nothing happen, cannot tell whether the model is disconnected.
- Proposed fix direction: Emit `ErrorRouter.report_warning("Temperature", "Not connected")` in the no-serial branch of `send_settings`/`stop`; expose a `connection_status` attr; optionally add a simulated temperature generator for SIM.
- Confidence: verified

### TEMP-11
- ID: TEMP-11
- Title: close() sends the heater-off frame then immediately closes the port with errors swallowed and no flush/join; docs describing those excepts as warnings are wrong
- Severity: medium
- Views affected: Model (all views via teardown)
- Reference behavior: main `stop_plot` `temp_control.py:147-166` also writes then closes (same lack of flush), but reports the write error via print.
- Actual behavior: `close()` `temperature_system.py:181-192`: sets `continue_reading=False` (:183), writes `b"<0,6.0,0,0,0,0>"` inside `try/except Exception: pass` (:185-188), then `self.serial_conn.close()` `try/except Exception: pass` (:189-192). No `ser.flush()`, no `serial_thread.join()`; the reader can be blocked in `readline()` (timeout 1 s) on the fd being closed (its exception is silently absorbed by the `continue_reading` check :114-115, fine). If the write fails (port dead) the user is never told the heater-off was not delivered. The docs `error-routing.md:129-130` state these excepts are at lines 178 and 191 and describe them as candidate `report_warning` sites, but line 178 is inside `stop()`'s `report_error` (:177-179, which already reports), and the two swallowed excepts in `close()` are at :187 and :191.
- Failure scenario: On quitting with the cable half-dead or the port already errored, the zero frame is not delivered, nothing is shown, the window closes, heater remains at the last setpoint (firmware has no watchdog).
- Proposed fix direction: In `close()`: `ErrorRouter.report_error` (or a blocking modal via the view) when the write fails; call `ser.flush()` before close; `serial_thread.join(timeout=1.5)` after setting `continue_reading=False`; fix docs line references.
- Confidence: verified (code); flush behavior on close is platform dependent (UNVERIFIED HYPOTHESIS that an un-flushed write can be dropped; check by capturing the serial line after quit).

### TEMP-12
- ID: TEMP-12
- Title: Deviations from main's temp_control.py (behavioral diff table)
- Severity: low
- Views affected: Model
- Reference behavior (main, `git show main:src/temp_control.py`):
  - Separate `multiprocessing.Process` + own Tk root (`mainGUI.py:353-354`); window close = teardown (`temp_control.py:17,147-166`).
  - Open `serial.Serial(PORT,115200,timeout=1)` + `reset_input_buffer()` + write `<0,10,0,0,0,0>` (:31-33), no boot wait, no handshake.
  - Reader thread puts raw lines in a `queue`; GUI thread parses at 50 ms (:91-145). Breaks on first exception (:100-102).
  - Send: raw entry strings unchanged (:82-85). No Stop button. Quit sends `<0,10,2.0,0.5,.1,0>` (endpoint 0, gains retained) (:153-160).
- Actual behavior (refactor):
  - Runs in-process as a tab; teardown depends on the view (TEMP-1, TEMP-5, TEMP-6).
  - Serial wrapper (`controller/serial.py:47-103`): 1.5 s boot wait, then up to 3 s of `s\n` writes each 50 ms until "DEV:" is seen; then model writes `<0,6.0,0,0,0,0>` (:31) (main used ramp 10). Firmware answers 's' with `DEV: t` (`temp_controller.ino:204-208`); the reader thread ignores these lines (fewer than 3 CSV fields, :134).
  - Parsing in the reader thread under `_lock` (:129-153).
  - `ramp_rate` is sanitized: `num()`, `.2f` in `send_settings` (:75-82) but `.1f` in `stop()` (:166) (harmless inconsistency).
  - Stop/teardown/E-stop frame is `<0,ramp,0,0,0,offset>` / `<0,6.0,0,0,0,0>` (gains zeroed = guaranteed heater cut) vs main's `<0,10,2.0,0.5,.1,0>` (gains retained; heater goes off only because error<0). This is an improvement, but note the firmware ends up with kp=ki=kd=0 after every stop/teardown/E-stop, and the UI (P=2.0, I=0.5, D=.1 unchanged) is not updated to match.
  - Every Enter Settings emits `ErrorRouter.report_info("Temperature Send", ...)` (:85-91): a modal `messagebox.showinfo` in Tk (`tkinter/view.py:40-58`), modal `QMessageBox.information` in PySide (`pyside/view.py:62-80`), a toast/buffered item in Web. Main just printed. (Not deduped for changed values because the message includes the values; `ErrorRouter._is_spam` only blocks identical text within 5 s.)
  - `stop()` also exists as the Stop System button and via FULL STOP (new).
  - No error when no serial (see TEMP-10).
  - Backoff/give-up differs (TEMP-2).
- Failure scenario: informational only.
- Proposed fix direction: Move the info popup to a non-modal log/toast; align `stop()` format to `.2f`; after stop, optionally restore/mark gains in UI as "controller reset to 0 gains, press Enter to re-arm".
- Confidence: verified

### TEMP-13
- ID: TEMP-13
- Title: Model has no setpoint upper bound or type contract; views hold and mutate raw string attributes directly (MVC strain)
- Severity: low
- Views affected: Tk / PySide / Web / Model
- Reference behavior: main also had no bound (Entry text sent raw), so parity, but the MVC layer is the place to fix it.
- Actual behavior: All setpoint/PID/offset/ramp fields are `str` (`temperature_system.py:11-16`) and views `setattr(self.model, attr, text)` (Tk `view.py:358`, PySide `view.py:253`, Web adapter `:463`). Web additionally returns numbers if a client posts numeric JSON (`curr` is str so no cast, `value` stays whatever type JSON gave: int/float/bool/None), making `f"<{self.setpoint},...>"` produce `None`/`True`. No maximum temperature clamp exists in model or firmware path other than the firmware's `problem`/`temp-endpoint>10` logic. `QDoubleValidator(-1e9, 1e9, 3)` (`pyside/view.py:241-242`) allows 1e9.
- Failure scenario: A typo (e.g. 2000 instead of 200) is sent unmodified.
- Proposed fix direction: Give the model typed properties with range checks and a configurable `max_setpoint`, and have `send_settings` refuse and report on violation.
- Confidence: verified

## Corrections to existing docs
- `docs/architecture/ownership-and-lifecycle.md:166`: "own loop with backoff on repeated failure" is inaccurate: fixed 0.1 s retry, counter reset semantics per TEMP-2.
- `docs/architecture/error-routing.md:129-130`: line numbers/claims wrong (see TEMP-11).
- `docs/architecture/ownership-and-lifecycle.md:99`: says `disconnect()` "is called" for TemperatureSystem on dock close; true (`pyside/view.py:843`), but it omits that `full_stop`/dock-close/re-open lose the port (TEMP-6). Docs statement about the heater cutoff (models.md:244, known-issues.md:79) is CONFIRMED correct.

## Coverage
Read fully: `src/model/temperature_system.py` (1-202); `src/model/plot_data.py` (1-113, red-percent only); `git show main:src/temp_control.py` (1-174); `src/model/system_manager.py` (1-78); `src/error_routing.py` (1-80); `firmware/temp_controller/temp_controller.ino` lines 60-135, 135-166, 180-275 (PID, PWM, serial parse).
Read in part: `src/controller/serial.py` 1-135, 250-275; `src/views/tkinter/view.py` 1-40, 60-95, 108-232, 263-410, 482-545; `src/views/pyside/view.py` 20-160, 198-270, 377-455, 719-730, 806-967; `src/views/web/web_adapter.py` 20-40, 78-200, 228-312, 379-470; `src/views/web/web_server.py` 200-232, 296-345; `src/views/web/web_view.py` 1-90; `src/views/web/static/js/app.js` 570-600, 670-745, 795-1000, 1073-1095, 1155-1235; `src/app.py` 405-425, 735-760, 770-830; `src/app_bootstrap.py` 40-60, 140-193; `git show main:src/mainGUI.py` temperature lines (31, 74, 147, 175, 335-375).
Not done: no runtime execution (hard rule); did not read `style.qss`, `index.html`, or PySide `RedPercentDynamicView`; did not audit the Tk setup-window close path (`SetupWindow`) for quit-before-dashboard teardown; did not exercise macOS focus behavior (TEMP-4 PySide part is a hypothesis); did not verify ErrorRouter popup stacking behavior at runtime.
