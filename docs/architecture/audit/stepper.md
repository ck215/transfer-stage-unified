# Audit: Stepper (StepperProbe / BaseProbe)

> **Input (banner added 2026-09-23).** Audit of the old tree (now `legacy/src/`); current until the rebuild replaced it on 2026-09-23. Kept because the ledger in `docs/implementation/progress.md` and `docs/rebuild/carry.json` cite these finding IDs.

All paths relative to repo root `/Users/ianalbinogonzalez/Documents/GitHub/transfer-stage-unified/mvc-refactor`.

## Object summary

- `StepperProbe` is a thin subclass of `BaseProbe`. It only overrides `x_step/y_step/z_step` to `"1"` (`src/model/probes.py:493-498`), which matches main's entry defaults (`main:src/stepper_frame.py:142,145,148` insert "1"). It does NOT override `ui_schema`; the schema is `BaseProbe.ui_schema` (`probes.py:96-159`). Every stepper behavior therefore lives in `BaseProbe`, shared with DCProbe and ChuckPositioner.
- Constructor sites:
  - Startup / Tk / Web: `app_bootstrap.build_models` -> `StepperProbe(port, controllerID, active_claims)` (`src/app_bootstrap.py:147-149`). Web calls it from `WebModelAdapter.initialize` (`src/views/web/web_adapter.py:148`).
  - PySide6 sidebar re-open: `StepperProbe(None, "None", {})` (`src/views/pyside/view.py:884-886`). This is a headless model, not the configured one.
- Owner: `SystemManager.active_models` (`src/model/system_manager.py:10-14`). `BaseProbe` owns `serial_comm` (`probes.py:23`), `poller` (`probes.py:36-41`) and the interlock watchdog thread (`probes.py:67-68, 399-421`).
- Teardown paths:
  - Tk: window close -> `system_manager.shutdown_all()` (`src/views/tkinter/view.py:228`) -> `BaseProbe.teardown()` (`probes.py:479-487`).
  - PySide6 main window close: `shutdown_all()` (`src/views/pyside/view.py:966`).
  - PySide6 dock close: hand-rolled `close_device_view` (`view.py:813-847`), which does NOT use `teardown`/`remove_model`.
  - Web: `initialize` -> `old_manager.shutdown_all()` (`web_adapter.py:184-185`); `WebDashboardWindow.close` -> `shutdown_all` (`web_view.py:78-81`).
  - Global E-stop bar (all views): `SystemManager.full_stop_all` -> `emergency_stop()` -> `power_down()` (`system_manager.py:70-78`, `probes.py:489-490`).

### Q1 primitives cheat-sheet (verified)

| Primitive | What it does | Called by |
|---|---|---|
| `enable()` `probes.py:423-434` | If not `system_enabled`: `serial_comm.enable()` sends 'e'; ValueError -> warning popup, returns False. No serial -> silently sets `system_enabled=True`. Then starts the watchdog. | `enter_auton`, `enter_manual`, `macro_start_auton`, `toggle_enable` (not in any schema). Views never call it directly. |
| `_stop_and_disarm()` `probes.py:442-462` | Clears manual/auton/is_stepping, `_interlock_stop.set()`, sends all-zero stop packet, always sends 'd', sets `system_enabled=False`. | `disable`, `full_stop`, `power_down`. |
| `disable()` / `full_stop()` `probes.py:464-468` | Identical to `_stop_and_disarm`. | Tk/Qt toggles on exit (`toggle_manual`/`toggle_auton`, `probes.py:197-207`), PySide `close_device_view` (`view.py:838`), watchdog (`probes.py:417`), `run_script` finally (`probes.py:334`). |
| `power_down()` `probes.py:470-477` | `_stop_and_disarm` then raw `ser.write(b'k\n')` (kill coils). | `emergency_stop`, `teardown`. |
| `teardown()` `probes.py:479-487` | Stops and closes the poller, `power_down()`, `serial_comm.close()`. | `SystemManager.shutdown_all`/`reboot_model`. Tk close, PySide window close, Web re-init/close. NOT PySide dock close. |
| `emergency_stop()` `probes.py:489-490` | `power_down()`. | Global FULL STOP bar in all three views (Tk `view.py:188`, PySide `view.py:758`, Web `web_adapter.py:426`). |

---

## Findings

### STEPPER-1
- **Title:** PySide6 dock close leaks the serial handle, never runs `teardown`, and a re-opened Stepper tab is a silent headless model (existing docs describe this wrongly)
- **Severity:** high
- **Views affected:** PySide6
- **Reference behavior:** Tk has no per-device close; only `shutdown_all` -> `teardown()` (`tkinter/view.py:228`, `probes.py:479-487`) which sends 'd' then 'k' and closes serial. `SystemManager.remove_model` + `_teardown_model` are the sanctioned primitives (`system_manager.py:24-32`).
- **Actual behavior:** `close_device_view` (`pyside/view.py:832-844`) calls `model.disable()` (only 'd'; no 'k'), stops/closes the poller, checks `hasattr(model,'disconnect')` (false for BaseProbe), then `del self.system_manager.active_models[device_name]` directly, bypassing `remove_model`'s lock. `serial_comm.close()` is never called. Re-checking the sidebar box runs `open_device_view`, which builds `StepperProbe(None, "None", {})` (`view.py:886`) with port `None` (so `serial_comm=None`, `probes.py:23`) and controller "None" (no gamepad).
- **DOC ERROR:** `docs/architecture/known-issues.md:33-35` and `docs/architecture/ownership-and-lifecycle.md:91-98` claim the reopen "constructs a brand-new `serial()` on the same port ... two live handles on one OS port". Wrong: reopen passes `None`, so no second handle is ever opened. The real defect is (a) a permanently leaked open handle on the old model's port, and (b) the reopened tab is a dead/headless probe.
- **Failure scenario:** Operator closes the "Stepper Probe" dock and re-ticks it. The tab reappears looking normal, "Enter Autonomous Mode" succeeds (`enable()` with no serial just sets `system_enabled=True`, `probes.py:425-432`), but nothing is sent to hardware. "Enter Manual Mode" is blocked with "no gamepad". The original port stays open until process exit, so the port cannot be reopened by another process or by a Setup re-scan. Coils were only 'd'-disabled, never 'k'.
- **Proposed fix direction:** In `close_device_view`, replace the hand-rolled block with `model = system_manager.remove_model(name)` then `model.teardown()` (guarded). On re-open, either refuse to reconstruct a probe (keep the model alive and only hide the dock) or rebuild it from the original config (port + controller_id). Fix the two docs. Also stop constructing `(None, "None", {})` models for hardware devices.
- **Confidence:** verified

### STEPPER-2
- **Title:** Web never runs the gamepad poller or the manual-mode routing loop, so "Manual Mode" engages but nothing ever jogs
- **Severity:** high
- **Views affected:** Web
- **Reference behavior:** Tk (`tkinter/view.py:542-563`) and PySide (`pyside/view.py:154-183`) call `poller.start_polling(...)` and run a timer that, while `manual_flag`, calls `model.send_manual_mode_command(poller.get_mapped_state())`. Main did the same in `_manual_mode_loop` (`main:src/stepper_frame.py:581-611`).
- **Actual behavior:** A grep of `src` finds `start_polling` / `send_manual_mode_command` / `get_mapped_state` only in the Tk/PySide views, `probes.py`, gamepad and serial. `web_view.py:55-65` only rebinds `poller.log_updater`, and `web_adapter.py` has no polling loop. `ControllerPoller.get_mapped_state` returns `{}` unless `is_polling` (`controller/gamepad.py:515-518`), and `is_polling` is only set by `start_polling` (`gamepad.py:474-488`). `enter_manual` only checks `poller.gamepad` exists (`probes.py:234`), which is true after construction. `enter_manual` therefore succeeds, sets `manual_flag=True` and sends only a zero packet (`probes.py:241-243`).
- **Failure scenario:** In the browser: click "Enter Manual Mode" -> toggle goes green, controls lock (`app.js:903-919`), coils are energized, joystick does nothing. Because `manual_flag` is True the watchdog defers (`probes.py:409-410`), so the coils stay energized indefinitely, with no idle auto-disable, until someone clicks the toggle or FULL STOP.
- **Proposed fix direction:** Add a model-owned manual loop (a thread or timer started from `enter_manual`, stopped by `_stop_and_disarm`) that feeds `send_manual_mode_command`, so every view shares it. Alternatively the Web adapter starts the poller and a routing thread per probe. Preferably move the routing into the model and delete the copies at `tkinter/view.py:552-563` and `pyside/view.py:172-184`. Until then, `enter_manual` should refuse when the poller is not polling.
- **Confidence:** verified (code path). Whether `get_mapped_state` is populated from another thread in Web is UNVERIFIED (settle by running a Web session and logging `poller.is_polling`).

### STEPPER-3
- **Title:** Web `initialize` builds the new Stepper (opening the same serial port) BEFORE tearing down the old manager's models
- **Severity:** high
- **Views affected:** Web
- **Reference behavior:** `SystemManager.reboot_model` removes and tears down the old model, then constructs the new one (`system_manager.py:34-55`).
- **Actual behavior:** `web_adapter.py:148` `build_models` -> `StepperProbe(port, ...)` -> `serial(port)` (`probes.py:23`). Only later, after swapping managers (`web_adapter.py:177-178`), does `old_manager.shutdown_all()` run (`web_adapter.py:184-185`) and close the old handle (`probes.py:486-487`).
- **Failure scenario:** Operator re-runs the Setup wizard (reachable while running: `app.js:302-306`, `app.js:1797`) with the same Arduino port. The second open of the same tty happens while the first is open. Outcome is platform-dependent (`open` failure, or two handles that split reads). After the swap the old model's `teardown` runs `power_down()` ('d' + 'k') on the physical device through the OLD handle, after the NEW model has already opened it. Likewise the old poller (claims dict `active_claims`, `probes.py:35`) still holds the joystick when the new poller initializes; `new_manager` uses a fresh `active_claims = {}` (`web_adapter.py:141`), so the collision check is bypassed.
- **Proposed fix direction:** Tear down (or at least `power_down`+close) the outgoing manager's models before `build_models`, or make `initialize` two-phase (stop old -> build new -> swap, with rollback on failure).
- **Confidence:** verified (ordering); the runtime outcome on a given OS is hypothesis.

### STEPPER-4
- **Title:** `_stop_and_disarm` swallows a failed hardware disable (silent, no popup) and still reports `system_enabled=False`
- **Severity:** high
- **Views affected:** Model (all views)
- **Reference behavior:** Main's `enable_button` disable branch: `self.serial.disable()`; a failed verify is only a print, and the GUI is left "Disable System" (`main:src/stepper_frame.py:498-510`). `enable()` in the refactor at least warns (`probes.py:428-431`).
- **Actual behavior:** `probes.py:457-462`:
  ```
  if self.serial_comm:
      try: self.serial_comm.disable()
      except ValueError as e: print(...)      # no ErrorRouter call
  self.system_enabled = False
  ```
  `serial.disable()` raises ValueError when the Arduino is not verified (`controller/serial.py:249-251`). The Python flag is then forced False regardless. `send_stop_command()` at `probes.py:447` also runs first, un-guarded (`serial.send_autonomous_command` returns silently if the port is not verified, `serial.py:152-156`).
- **Failure scenario:** USB cable jiggles while the stage is enabled. Operator hits FULL STOP: the stop packet and 'd' are both dropped, the UI shows everything off/disabled, the popup never appears, and the stepper coils remain energized on the firmware side. `teardown()` has the same gap.
- **Proposed fix direction:** On ValueError in `_stop_and_disarm`, call `ErrorPopupManager.report_error` (an actionable "Could not confirm disable, coils may still be energized"). Consider keeping a `disable_unconfirmed` flag that makes the next `enable()`/`disable()` retry, and do not silently claim `system_enabled=False`.
- **Confidence:** verified

### STEPPER-5
- **Title:** Gamepad-lost path in `send_manual_mode_command` only clears `manual_flag`; it leaves the stage enabled instead of doing what main did (full stop + disable)
- **Severity:** medium
- **Views affected:** Model (Tk + PySide; Web has no loop, see STEPPER-2)
- **Reference behavior:** Main `_manual_mode_loop`: on `joystick is None` -> `manualFlag=False`, `full_stop_button()`, and `enable_button()` if enabled (i.e. disable) (`main:src/stepper_frame.py:581-595`). Main's dropdown handler also called `full_stop_button()` if the swap failed or targeted "None" while manual (`main:src/stepper_frame.py:405-416`).
- **Actual behavior:** `probes.py:359-364` sets `manual_flag=False`, warns, and substitutes `{}`. `system_enabled` stays True, and no stop-and-disarm runs. It then still sends one neutral manual packet (`probes.py:366-388`). The controller swap path (`set_controller`, `probes.py:182-192`) has no equivalent of main's `full_stop` on failure/None.
- **Failure scenario:** Unplug the gamepad mid-jog. The manual flag flips off and a warning shows, but coils stay energized. Only the 5-minute watchdog will eventually disable (`probes.py:413-417`, since `manual_flag` and `is_stepping` are now False). The operator sees the toggle read "Enter Manual Mode" (misleadingly "safe").
- **Proposed fix direction:** In that branch call `self.full_stop()` (or `disable()`) instead of only clearing the flag, and make `set_controller` call `full_stop()` when manual is active and the new controller is None or failed (also update `active_claims`, as main did at `main:src/stepper_frame.py:411-419`).
- **Confidence:** verified (code). It overlaps the unproven "toggle desync after controller swap" hypothesis in `known-issues.md`; this finding is about the missing disable, not the desync.

### STEPPER-6
- **Title:** `is_stepping` is never cleared after "Start Stepping", so the idle watchdog is disabled for the rest of the autonomous session
- **Severity:** medium
- **Views affected:** Model (all views)
- **Reference behavior:** Main resets the 5-minute timer on activity and lets it fire in any mode (`main:src/stepper_frame.py:519-532`, `540`).
- **Actual behavior:** `macro_start_auton` sets `is_stepping=True` (`probes.py:250`) and sends one packet; nothing observes completion. The only clears are `_stop_and_disarm` (`probes.py:445`) and `run_script`'s exit path. The watchdog `continue`s while `is_stepping or manual_flag` (`probes.py:409-410`).
- **Failure scenario:** Click "Start Stepping" once and walk away. The move finishes in seconds, but `is_stepping` stays True, so the coils remain energized until someone manually toggles Autonomous off. The idle interlock never triggers. The same holds for `manual_flag`: any operator who leaves Manual mode engaged with no gamepad input never times out, unlike main where idle activity reset applies in all modes.
- **Proposed fix direction:** Do not defer on `is_stepping`; instead call `touch_activity()` when a move is actually sent and let the watchdog time out on real inactivity, or track a move-complete timestamp (dist/speed) that clears `is_stepping`. Decide explicitly whether manual mode should time out (main did).
- **Confidence:** verified

### STEPPER-7
- **Title:** Interlock watchdog restart race: `enable()` right after a stop returns early against a dying thread and leaves the stop event set
- **Severity:** medium
- **Views affected:** Model (all views)
- **Reference behavior:** Main re-armed a fresh `after()` timer on every enable (`main:src/stepper_frame.py:519-520`).
- **Actual behavior:**
  - `_start_interlock_watchdog` (`probes.py:399-421`): `if self._interlock_thread and self._interlock_thread.is_alive(): return` happens BEFORE `_interlock_stop.clear()` (`probes.py:402`).
  - `_stop_and_disarm` sets the event (`probes.py:446`), but the thread may not have exited yet (it wakes and returns on `wait()==True` or `not system_enabled`).
  - Sequence: full_stop (set) -> immediate `enter_auton()` -> `enable()` -> thread still `is_alive` -> return; the event stays set, the thread then exits, and no watchdog is running while `system_enabled=True`.
  - Also `_INTERLOCK_POLL_INTERVAL=5` (`probes.py:16`): the thread re-checks only every 5s.
- **Failure scenario:** Rapidly toggling Autonomous off then on (or macro Start Stepping after a FULL STOP) can leave the stage enabled with no idle timeout.
- **Proposed fix direction:** Create a fresh `threading.Event` per start (or `join()` the old thread with a short timeout in `_stop_and_disarm`), and do the liveness check against the same event generation. Ensure `reconnect_serial` (`probes.py:173`) also stops the watchdog.
- **Confidence:** verified (logic); the window is small, so the likelihood is hypothesis.

### STEPPER-8
- **Title:** `run_script` has no run token: a stale script thread resumes moving after the user stops and re-enters Autonomous
- **Severity:** high
- **Views affected:** Model (reachable via Web API; Tk/PySide UI entry removed)
- **Reference behavior:** Main's `run_script_button` only parsed and printed lines (`main:src/stepper_frame.py:568-576`); it never moved hardware. The refactor's version is new behavior.
- **Actual behavior:** Loop guard is `if not self.is_stepping or not self.auton_flag: break` (`probes.py:286`), evaluated per line, but each G-line ends with an uninterruptible `time.sleep(duration)` (`probes.py:320`). FULL STOP clears flags (`probes.py:443-445`), but if the operator re-enters Autonomous (`enter_auton`) or clicks Start Stepping (`macro_start_auton` sets `is_stepping=True`, `probes.py:250`) before the old thread wakes, both flags are True again and the old script continues. There is also no guard against two concurrent `run_script` threads. `run_script` ignores `enter_auton()`'s failure (`probes.py:258`; it then exits after the first check). It returns silently with no feedback when `script_path` is empty or `serial_comm` is None (`probes.py:254-255`). `finally: self.full_stop()` (`probes.py:334`) also disables the stage at the end.
- **Failure scenario:** Start a long script -> FULL STOP -> immediately re-enter Autonomous and press Start Stepping -> the earlier script thread wakes, sees both flags True, and continues sending G-code moves alongside the new command.
- **Proposed fix direction:** Add a monotonically increasing `_run_id` (or a per-run `threading.Event`) captured by `_execute`; abort when it changes. Refuse to start when a script thread is alive. Use `Event.wait(duration)` instead of `sleep`. Surface failures via `ErrorPopupManager`.
- **Confidence:** verified

### STEPPER-9
- **Title:** G-code script path: unvalidated numerics and every 'G' word treated as a move
- **Severity:** low
- **Views affected:** Model
- **Reference behavior:** `get_params` sanitizes via `_num` (`probes.py:340-351`).
- **Actual behavior:** `run_script` builds `cmd_params` from raw strings (`probes.py:299-311`) and computes duration with bare `float(self.x_step)`/`float(feedrate)` (`probes.py:314-318`). Any parse error aborts the script through the generic except (`probes.py:330-332`). Every command whose letter is 'G' (G21/G90/G91/G28...) is sent as an auton move with `X/Y/Z` defaulted to 0 (`probes.py:293-312`), with no absolute/relative handling. Non-'G' lines with a comma (`probes.py:321-325`) or without (`probes.py:326-329`) are written raw to the serial port, bypassing `serial_comm`'s lock and API (`probes.py:324,328`).
- **Failure scenario:** A script containing `G90` or `M3 S1000` sends a zero-distance auton packet or pushes raw text to the Arduino.
- **Proposed fix direction:** Whitelist G0/G1, use `_num`, drop the raw `ser.write` fallbacks (or route through a documented serial-layer method with the lock).
- **Confidence:** verified

### STEPPER-10
- **Title:** Web "Run Script" and other Web wiring points at commands the Stepper does not have (dead widget), and the Web interlock disables "Start Stepping"
- **Severity:** medium
- **Views affected:** Web
- **Reference behavior:** Tk `_execute_command` invokes the schema `command` directly on the model (`tkinter/view.py:495-501`).
- **Actual behavior:**
  1. `app.js:1240-1247` `executeScriptFile` dispatches `'execute_script'` to the first device whose name contains "stepper"/"probe". A grep of `src` finds no `execute_script` in any `.py` (model method is `run_script`, `probes.py:253`, and it is no longer in the schema, `probes.py:144-147`). `dispatch_command` allow-lists only schema commands (`web_adapter.py:341-347`), so the call returns HTTP 400 "Command execute_script not found".
  2. `app.js:897-899` keys on `toggle_enable`, `reconnect_serial`, `power_down`, `full_stop`. None exist in the Stepper schema any more (`probes.py:124-157`), so `isStop`/`isPowerDown` never match.
  3. In `app.js:908-919`, once `auton_flag` (or `manual_flag`) is True, every control other than `isStop || isPowerDown || isAutonToggle || isManualToggle` gets `disabled = true`. That includes "Start Stepping" (`macro_start_auton`) and all entries (steps, dist, speed). In Tk/PySide the operator enters Autonomous first and then clicks Start Stepping.
- **Failure scenario:** In Web: "Enter Autonomous Mode" -> "Start Stepping" is greyed out, so a second step requires toggling Autonomous off (which now disables the stage) and back on. Editing "Target X Dist" while autonomous is blocked. The Web script dialog always errors.
- **Proposed fix direction:** Align `executeScriptFile` with a real schema command (or re-expose `run_script` via schema with a path argument); update the interlock so `macro_start_auton` and non-motion entries stay enabled in Autonomous, and key on the current command names (e.g. `data-command === 'toggle_auton'`).
- **Confidence:** verified

### STEPPER-11
- **Title:** Web entry fields commit only via a Set button/Enter, and the API can set any schema `model_attr` (including flags) bypassing `enable()` and validation
- **Severity:** medium
- **Views affected:** Web
- **Reference behavior:** Tk numeric entries commit on FocusOut/Return (`tkinter/view.py:335+` area; fix in commit b42c13e), PySide on `editingFinished` with `QDoubleValidator` (`pyside/view.py:236-262`).
- **Actual behavior:** `app.js:670-680` sends `set_attr` only when the row's Set button (or Enter) is used. `_schema_attrs` permits ANY `model_attr`, including `auton_flag`, `manual_flag`, `pos_x..z`, `serial_port` (`web_adapter.py:363-367`, schema at `probes.py:103-136`). `set_device_attribute` coerces by the current type and does `setattr` (`web_adapter.py:395-407`) with no numeric validation for the string-typed step/speed fields.
- **Failure scenario:** (a) Operator types a new X Step Size, then clicks "Enter Manual Mode" without pressing Set; the model still holds the old value and the jog uses it. (b) `POST /api/set_attr {attr:"manual_flag", value:true}` sets `manual_flag=True` without `enable()` or the gamepad check (`probes.py:226-243`), leaving the flag/hardware state inconsistent (flag set, coils not enabled). (c) Non-numeric text is stored and silently replaced by defaults 16/400 inside `get_params` (`probes.py:340-343`, `_num` in `model/numeric.py:3-15`), differing from Tk/PySide, which reject it.
- **Proposed fix direction:** Commit entries on blur/change in the JS; have `_schema_attrs` exclude `readonly` and `toggle` `model_attr`s (only `entry`/`dropdown`); validate numeric attrs server-side.
- **Confidence:** verified

### STEPPER-12
- **Title:** After a serial port change or `reconnect_serial`, hardware/interlock state is left stale; the "Serial Port" entry is an inert widget
- **Severity:** medium
- **Views affected:** Tk / PySide6 / Web / Model
- **Reference behavior:** Main's Serial Reconnect button reads `entry_serial_port`, closes the old serial, opens a new one (`main:src/stepper_frame.py:662-672`); the entry was live. The refactor deliberately removed the button (`probes.py:148-154`).
- **Actual behavior:**
  - The "Serial Port" `entry` (`probes.py:111`) is editable in all three views (Tk/PySide skip numeric validation for this attr: `tkinter/view.py:335`, `pyside/view.py:236`) and writes `model.serial_port`, but nothing consumes it: `reconnect_serial` is not in any schema, and the only caller is PySide's dock-exists branch (`pyside/view.py:866-875`, reachable only if an already-open dock is re-shown).
  - `reconnect_serial` (`probes.py:161-175`): closes the old serial without sending a stop or 'd' first, sleeps 1s on the caller thread (the UI thread in PySide, `view.py:875`), clears Python flags, but never stops the watchdog (`_interlock_stop` not set) and never sends hardware disable. It leaves `pos_x/y/z` stale.
  - Firmware's enabled state persists across reconnects (comment at `probes.py:448-456`), so Python says disabled while coils may be energized.
  - The model never re-checks `poller`/`active_claims` on port change.
- **Failure scenario:** Operator edits "Serial Port" to a different tty in the tab and expects a reconnect; nothing happens, but the displayed value now differs from the actual port (`serial_comm` is unchanged). In PySide re-show of an open dock: the enabled stage is reconnected with flags cleared but no 'd', so coils remain hot with the UI showing "Enter ..." (looks safe).
- **Proposed fix direction:** Make the "Serial Port" field `readonly` (or wire it to a real "Apply/Reconnect" command). In `reconnect_serial` call `_stop_and_disarm()` BEFORE closing the old serial, reset `pos_*`, and restart nothing until `enable()`.
- **Confidence:** verified

### STEPPER-13
- **Title:** Red Percent keeps a stale `stepper_model` after the probe dock is closed/reopened in PySide
- **Severity:** medium
- **Views affected:** PySide6 (Model consumer)
- **Reference behavior:** Web/PySide construction links probes once (`web_adapter.py:166-174`, `pyside/view.py:902-908`).
- **Actual behavior:** `close_device_view` removes the probe from `active_models` but does not touch `red_model.available_probes` nor `red_model.stepper_model` (`pyside/view.py:832-844`; model attrs at `redpercent_system.py:70-72, 91-94`). On reopen, `view.py:913-917` only does `available_probes[device_name] = model`; `set_stepper_model` is not re-called, so `stepper_model` still points at the destroyed probe.
- **Failure scenario:** Close then reopen "Stepper Probe": Red Percent's "Position Source" continues reading `pos_x/y/z` and `vel_*` from the dead model (frozen at its last value, `redpercent_system.py:312-324`) while the new headless probe shows 0.
- **Proposed fix direction:** On probe close, remove it from `red_model.available_probes` and clear/reselect `stepper_model`; on reopen call `red_model.set_stepper_model(device_name)` if it was the selected source.
- **Confidence:** verified (code); observed effect follows from the reads at `redpercent_system.py:312-324`.

### STEPPER-14
- **Title:** PySide6 double-polls the serial port and swallows read errors
- **Severity:** low
- **Views affected:** PySide6
- **Reference behavior:** Tk polls `read_position` once every 100 ms (`tkinter/view.py:567-572`), unguarded.
- **Actual behavior:** PySide runs `pos_timer` at 100 ms (`pyside/view.py:145-148`) AND `_poll_model` (50 ms) also calls `read_position()`/`poll_status()` (`pyside/view.py:402-405`), i.e. about 30 reads/s vs 10/s. `_safe_read_position` swallows all exceptions with `pass` (`pyside/view.py:186-190`), while `_poll_model` reads are unguarded.
- **Failure scenario:** Extra serial reads race with the 20 ms manual-jog writes on the UI thread and compete for the buffer that `read_position` parses; errors are invisible.
- **Proposed fix direction:** Drop the calls in `_poll_model`; route exceptions through `ErrorPopupManager`/log instead of `pass`.
- **Confidence:** verified

### STEPPER-15
- **Title:** Manual routing cadence and poller start timing diverge from main; activity callback keeps the stage "active" outside manual mode
- **Severity:** low
- **Views affected:** Tk / PySide6
- **Reference behavior:** Main starts polling on entering manual (`main:src/stepper_frame.py:639`), loops every 5 ms (`main:src/stepper_frame.py:608`), and stops the poller on Full Stop / auton entry (`main:src/stepper_frame.py:473,561`).
- **Actual behavior:** Tk routes at 50 ms (`tkinter/view.py:563`), PySide at 20 ms (`pyside/view.py:184`). Both start the poller once at view construction and never stop it on mode exit; `activity_callback=model.touch_activity` (`tkinter/view.py:549-550`, `pyside/view.py:168-169`) therefore fires on any stick movement even when not in manual mode (`gamepad.py:604-605`), resetting the idle timer for an enabled-but-idle Autonomous session. If `poller` is None at view build (gamepad import failed, `probes.py:40-41`), `set_controller` later creates one (`probes.py:188-192`) but no view ever calls `start_polling`, so manual mode never routes.
- **Failure scenario:** 50 ms Tk jog latency versus 5 ms in main; touching the stick while in Autonomous keeps the stage energized. Late-created poller means Manual is inert.
- **Proposed fix direction:** Centralize the loop in the model (see STEPPER-2), and gate `touch_activity` by `manual_flag`.
- **Confidence:** verified (cadence, activity callback); the late-poller gap is verified by grep (no other `start_polling` callers).

---

## Q2 summary: manual-mode toggle ordering (verified)

- Model order (identical for every view because all call `toggle_manual`): `enter_manual` = gamepad check (`probes.py:234-238`) -> `enable()` (sends 'e', starts watchdog) -> set flags -> `send_stop_command()` (`probes.py:239-243`). Exit = `full_stop()` = flags cleared -> stop packet -> 'd'.
- Main order: separate Enable button required first (`main:src/stepper_frame.py:618-620`), joystick check, flags, `start_polling`, loop. Main's Full Stop did NOT disable (`main:src/stepper_frame.py:552-566`); the refactor's toggle-off disables. This is intentional (`probes.py:127-136`), so it is not a bug in itself.
- Per-view difference is only after-toggle: Tk polls flags and re-routes at 50 ms, PySide at 20 ms, and Web has no routing (STEPPER-2). On the falling edge Tk/PySide send `send_manual_mode_command({})` once (`tkinter/view.py:559-561`, `pyside/view.py:179-181`), Web does not; but `_stop_and_disarm` already sent the zero packet.
- `enter_auton` and `macro_start_auton` (no gamepad check needed) call `enable()` similarly. `macro_start_auton` flips `manual_flag` off without a stop packet (`probes.py:245-251`), unlike `enter_auton` (`probes.py:224`); the Tk/PySide falling-edge `{}` send covers it, Web does not.

## Q3 summary: interlock watchdog lifecycle (verified)

- Started only by `enable()` (`probes.py:433`) via `_start_interlock_watchdog`; one daemon thread per model, polling every 5 s (`probes.py:16, 406`). It exits on: `_interlock_stop` set (any `_stop_and_disarm`, `probes.py:446`), `not system_enabled` (`probes.py:407`), or after firing `disable()` on timeout (`probes.py:417-418`).
- Never started by any view. Never explicitly joined (daemon). `reconnect_serial` does not stop it. It runs `disable()` on a background thread without the Web per-device lock (`web_adapter.py` `_get_device_lock`), though the serial layer has its own lock. Popups from that thread are thread-safe in Tk (queue, `tkinter/view.py:14-37`) and PySide (Qt signal, `pyside/view.py:19-56`). Web routing of `report_info` is not audited here.
- Defers while `is_stepping or manual_flag` (STEPPER-6). Restart race in STEPPER-7.

## Q4 summary: bypasses / unwired widgets

- PySide dock close bypasses `teardown`/`remove_model` (STEPPER-1).
- Web `execute_script`, `power_down`, `full_stop`, `toggle_enable`, `reconnect_serial` references are dead (STEPPER-10).
- The "Serial Port" entry is inert (STEPPER-12).
- The Stepper "Controller Log Window" button is handled specially in Tk/PySide (`tkinter/view.py:479-490`, `pyside/view.py:388-400`); Web has no handler for `open_controller_log`, whose model method only prints (`probes.py:194-195`), so it is a no-op (low; not filed separately).
- The `file_picker` element type: Tk supports it (`tkinter/view.py:459-475`), PySide has `continue` before the body (`pyside/view.py:314-316`, dead code below), and the Stepper schema no longer contains one.
- View mutating model state directly: PySide/Tk entry commit does `setattr(self.model, attr, text)` (`pyside/view.py:253-257`), and Web `set_attr` (STEPPER-11). This is the schema-driven contract, not strain per se, but it bypasses any model validation (the model only sanitizes lazily in `get_params`).

## Q5 summary: state stale after reconnect/port change

See STEPPER-1 (reopen creates a fresh headless model and leaks the old port), STEPPER-12 (`reconnect_serial`: no stop/'d', `pos_*` stale, watchdog untouched, `serial_port` edits inert), STEPPER-13 (Red Percent binds to the dead model).

## Docs corrections (vs docs/architecture)

- `known-issues.md:33-35` and `ownership-and-lifecycle.md:91-98`: the reopen does NOT open a second handle on the same port (it passes `None`, `pyside/view.py:886`). The real bug is a leaked old handle plus a headless replacement (STEPPER-1).
- `models.md:92` (StepperProbe overrides only `x/y/z_step="1"`, no `ui_schema`) is correct (`probes.py:493-498`).

## Coverage

Read fully: `src/model/probes.py:1-500` (BaseProbe + StepperProbe; DCProbe/ChuckPositioner not audited); `src/model/system_manager.py:1-80`; `src/views/pyside/view.py:1-456` and `719-975`; `src/views/tkinter/view.py:14-60`, `195-235`, `440-700`; `src/views/web/web_adapter.py:100-200`, `243-470`; `src/views/web/web_view.py:40-80`; `src/views/web/static/js/app.js:606-730, 870-995, 1120-1250` (grep-checked the rest); `main:src/stepper_frame.py` (read the enable/disable, full-stop, manual-loop, dropdown, reconnect, close, and get_gui_params sections); `src/error_routing.py:36-60`; `src/model/numeric.py`; portions of `src/controller/gamepad.py:350-375, 440-520, 515-560, 572-612` and `serial.py:150-260` only to confirm return/raise behavior (not audited); `src/app_bootstrap.py:140-195`; `src/model/redpercent_system.py` (grep only).
Not reached: `main:src/serialDrive.py` beyond method names (serial internals owned by another agent); `web_server.py` handlers other than grep; `index.html`; `style.qss`; Tk `DynamicView` entry-commit code for numeric fields (lines 280-440, referenced only by grep line numbers); runtime behavior (no GUI launched; all outcomes are from code reading).
