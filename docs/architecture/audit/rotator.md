# Audit: RotatorSystem (SMC100 rotator)

> **Input (banner added 2026-09-23).** Audit of the old tree (now `legacy/src/`); current until the rebuild replaced it on 2026-09-23. Kept because the ledger in `docs/implementation/progress.md` and `docs/rebuild/carry.json` cite these finding IDs.

All paths relative to repo root `/Users/ianalbinogonzalez/Documents/GitHub/transfer-stage-unified/mvc-refactor`. Every cited line was re-read in this run.

## Object summary

- Class: `src/model/rotator_system.py:8` `RotatorSystem`. It does NOT subclass `ManagedModel`, but it satisfies the Protocol structurally with `teardown()` (:122, calls `disconnect()`) and `emergency_stop()` (:125, calls `stop()`).
- Driver: `src/lib/smc100.py` `SMC100`. It has an internal `_serial_lock` (:106) held for the whole of every `sendcmd` (:393).
- Constructor sites:
  - `src/app_bootstrap.py:159-161` `build_models` -> `RotatorSystem(port)`. Used by the Tk setup, the PySide setup (`src/app.py` ~:733) and the web `WebModelAdapter.initialize_setup` (`web_adapter.py:148`).
  - `src/views/pyside/view.py:896-898` builds `RotatorSystem(None)` when a dock is opened from the sidebar.
  - `__init__` (:29-30) calls `connect()` synchronously. That opens the serial port and does `get_status` with retry=10, so it can block the caller for about 0.5 s or more.
- Owner: `SystemManager.active_models["SMC100 Rotator"]`.
  - Tk and PySide: the `SystemManager` built in `app.py`.
  - Web: `WebModelAdapter.system_manager` (replaced wholesale by `initialize_setup`, `web_adapter.py:176-179`).
- Teardown paths:
  - Tk: `DashboardWindow.on_close` (`tkinter/view.py:226-231`) -> `shutdown_all` -> `teardown` -> `disconnect` -> `smc.close()`.
  - PySide, window close: `DashboardWindow.closeEvent` (`pyside/view.py:957-967`) -> `shutdown_all`.
  - PySide, dock close or unchecking the sidebar item: hand-rolled in `close_device_view` (`pyside/view.py:813-846`). It calls `model.disconnect()` directly and does `del system_manager.active_models[...]`. It does not use `teardown`, `remove_model` or the manager lock.
  - Web: `WebDashboardWindow.close` (`web_view.py:73-77`) -> `self.system_manager.shutdown_all()` on a STALE manager (see ROTATOR-2).
- FULL STOP:
  - Tk: `stop_btn` Label -> `full_stop_all()` (`tkinter/view.py:188`).
  - PySide: `stop_btn.clicked` -> `full_stop_all` (`pyside/view.py:~758`).
  - Web: POST `/api/system/full_stop` -> `adapter.full_stop_all` (`web_adapter.py:420-431`).
  - All three converge on `emergency_stop()` -> `stop()` -> `smc.stop()`, which sends "ST" synchronously.
- Async ops: `_run_async` (:56-60) spawns one daemon thread per call. This applies to `home`, `reset_and_configure`, `move_absolute` and `move_relative`. `connect`, `disconnect` and `stop` are synchronous. There is no busy flag and no cancel token. The model's `_lock` guards only position/state/error/connect bookkeeping, never motion commands.
- `main` reference (`git show main:src/rotator.py`, `mainGUI.py:355-356`):
  - The rotator was its own `multiprocessing.Process` with its own Tk window (`SMC100GUI`).
  - It had no +/-30 degree confirmation. That safety feature exists only in the refactor, so "Tk/main is reference" applies to Tk only.
  - It had no FULL STOP integration.
  - Closing the window just ended the process, with no ST sent (`SMC100.__del__` closes the port).
  - Poll errors were silently swallowed (`rotator.py` `_poll_status` `except: pass`).
  - The controls were disabled while disconnected (`_set_controls_state`).
  - Invalid numeric input showed an "Invalid Input" warning.
  - Position was shown as `f"{pos:.4f} deg"`, and a "--.-- deg" placeholder when disconnected.

## Findings

### ROTATOR-1
- Title: Closing a window, dock or tab, or re-connecting, mid-move closes the port without sending STOP. The stage keeps moving unattended, and the worker thread errors out.
- Severity: high
- Views affected: Tkinter / PySide6 / Web / Model
- Reference behavior: `main` had the same gap (process exit, no ST, `rotator.py` has no stop-on-close). The MVC contract for `teardown()` (`model/base.py`) says "stop all background activity and release hardware connections". `RotatorSystem` does not stop motion.
- Actual behavior:
  - `disconnect()` (`rotator_system.py:108-120`) nulls `self.smc` and calls `smc.close()` (`smc100.py:484-491`). `close()` sets `_port=None`. It never calls `smc.stop()`.
  - `teardown` = `disconnect` (:122-123).
  - Callers: Tk `on_close` (`tkinter/view.py:228`), PySide `close_device_view` (`pyside/view.py:842-843`), PySide `closeEvent` (`:966`), web `close` (`web_view.py:77`), `reconnect()` (:187-189).
  - The in-flight worker (`smc.move_absolute_deg` -> `wait_states`, `smc100.py:291-297,309-354`) has the old SMC100 object. After close, `sendcmd`'s `while self._port is not None` (:394) exits and returns `None`. `get_status` then does `resp[0:4]` on `None` -> `TypeError`.
  - The `TypeError` goes to `_async_wrapper` -> `ErrorRouter.report_error("Rotator Controller Error", ...)` (`rotator_system.py:62-73`), so the user gets a spurious error popup or toast while the stage is still travelling.
- Failure scenario: The user clicks Move Absolute 25 (confirmed), then closes the dashboard (Tk) or unchecks the dock (PySide) or restarts the app. The controller had already accepted `PA25` and keeps rotating with no host attached and no ST. If the path ends beyond the confirmed range, or the stage collides with tubing, nothing stops it. Clicking Reconnect mid-move also leaves the stage moving while the new SMC100 instance starts with unknown state.
- Proposed fix direction:
  - In `RotatorSystem.disconnect()`, before `smc.close()`, best-effort call `smc.stop()` (guarded, short timeout) when a move may be in flight. Track this with an `_op_in_flight` counter or event set by `_run_async`.
  - Make `_async_wrapper` swallow the "port closed" case: check `self.smc is None` or use a `_closing` flag, and do not report an error after an intentional disconnect.
  - Add a regression test with a fake SMC100 that records calls.
- Confidence: verified

### ROTATOR-2
- Title: Web close path calls `shutdown_all()` on the original, empty `SystemManager`. The rotator is never torn down on Ctrl-C.
- Severity: medium (port and controller state left to process exit; no stop sent)
- Views affected: Web
- Reference behavior: Tk `on_close` and PySide `closeEvent` call `shutdown_all` on the manager that owns the live models.
- Actual behavior:
  - `run_web_app` builds `manager = SystemManager()` (`app.py:774`) and passes it to `WebDashboardWindow` (:780). `WebDashboardWindow.__init__` stores `self.system_manager` (`web_view.py:~52`).
  - `initialize_setup` creates a different `new_manager` and swaps it into the ADAPTER only (`web_adapter.py:143-179`).
  - `WebDashboardWindow.close()` (`web_view.py:73-77`) uses its own `self.system_manager`, which is still the original manager holding no models.
  - The rotator (and every other model) that the web setup created is never given `teardown()`. The port is closed only if the OS reclaims it when the process exits.
- Failure scenario: Launch web view -> run setup with the rotator on COM3 -> start a move -> Ctrl-C the launcher. `dashboard.close()` runs `shutdown_all` on the empty manager, so the rotator is never stopped or disconnected.
- Proposed fix direction: `WebDashboardWindow.close()` should shut down `self.server.adapter.system_manager` (the current one), or the adapter should expose `shutdown()`. Alternatively make `set_system_manager` update the window's reference. Add ST-before-close as in ROTATOR-1.
- Confidence: verified

### ROTATOR-3
- Title: Web view never registers `confirm_rotation_callback`. Any move beyond +/-30 degrees is silently refused, and the UI reports "executed".
- Severity: medium (safe failure direction, but the feature is missing and misleading)
- Views affected: Web
- Reference behavior:
  - Tk sets `model.confirm_rotation_callback = self._confirm_rotation_dialog` (`tkinter/view.py:198-199`), an `askyesno` on the UI thread.
  - PySide does the same (`pyside/view.py:921-922`, dialog at :849-856 with No as the default).
- Actual behavior:
  - `grep confirm_rotation src` shows setters only in the Tk and PySide views. Nothing under `src/views/web/` sets it, and `app.js` has no rotator confirmation code.
  - So `_confirm_rotation` (`rotator_system.py:209-215`) hits the fallback: it prints to stdout and returns `False`.
  - `move_absolute` and `move_relative` return quietly.
  - `dispatch_command` (`web_adapter.py:404-411`) returns `{"status":"ok"}`, and `app.js:1133-1153` shows the success toast "`_move_abs_ui executed`".
- Failure scenario: In the web view, an operator types 45, clicks Set, then clicks Move Absolute. The toast says executed, nothing moves, and no reason is shown. The operator concludes the hardware is broken. There is no way to legitimately move past 30 from the web UI.
- Proposed fix direction:
  - Give the model a way to say "needs confirmation". For example, `_confirm_rotation` with no handler could `ErrorRouter.report_warning("Rotation blocked", ...)`. Better, add a two-step web flow: the command returns a `needs_confirm` status and the JS shows a modal, then re-posts with a `confirmed` arg.
  - At minimum, route the block message via `ErrorRouter` so the toast shows it.
  - Also make `dispatch_command` surface a non-ok result when the model declines the action.
- Confidence: verified

### ROTATOR-4
- Title: The +/-30 degree tubing confirmation can be bypassed for relative moves (stale or `None` position, stacked moves). There is no busy guard.
- Severity: high (tubing damage is the reason the check exists)
- Views affected: Tkinter / PySide6 / Web / Model
- Reference behavior: Tk and PySide intend to confirm any move whose final target exceeds 30 degrees (`_confirm_rotation`, `rotator_system.py:209`). `main` had no check at all.
- Actual behavior:
  - `move_relative` (:223-232) computes `target = float(self.position) + step`. If `position` is `None` or unparseable it silently uses `0.0` (:226-228).
  - It uses whatever the last poll wrote, not the pending commanded target. `position` is `None` from construction until the first successful `poll_status`. It is also `None` after `reconnect()` (`disconnect` sets `_position=None`, :113).
  - `_run_async` has no lock or busy flag, so each click spawns another thread running `smc.move_relative_deg(step)`.
  - Each `PR` executes on the controller and the effects accumulate.
- Failure scenario A: The stage is at 0 with an unpolled position, and the step is 40. `current=0.0` gives `target=40` -> confirm prompt. That case works. The real hole is when the position is `None` but the true position is 25: step 10 -> computed 10 -> no prompt -> stage goes to 35.
- Failure scenario B (stacked moves): The stage is at 20 with step 4. The user clicks Move+ five times within a fraction of a second. Each click computes from the current polled position (20..22), so each is at most 26 -> no prompts. Five `PR4` commands are queued on separate threads and the stage ends at 40 with no confirmation.
- Failure scenario C: Poll failures (see ROTATOR-9) leave `position` stale, so the confirm decision is made on old data.
- Proposed fix direction:
  - Maintain a model-side `_commanded_target`, updated under `_lock` by every move.
  - Compute relative targets from `_commanded_target` if set, else from a fresh `smc.get_position_deg()` read. If the position is unknown, refuse or require confirmation, not assume 0.
  - Serialize motion: add a `_motion_lock` or an in-flight flag and drop or queue overlapping move requests (or confirm against the sum).
  - Apply the check in the worker immediately before `sendcmd`, using the actual position.
- Confidence: verified (code paths). The physical consequence depends on the stage speed.

### ROTATOR-5
- Title: PySide dock close hand-rolls model destruction. It bypasses `remove_model` and `teardown` and mutates the manager dict without its lock. Reopening the dock creates an unconnectable `RotatorSystem(None)` and the port config is lost.
- Severity: medium
- Views affected: PySide6
- Reference behavior: Tk has no per-device removal, and the model lives until `shutdown_all`. `SystemManager.remove_model` (`system_manager.py:23-26`) and `reboot_model` (`:32-51`) are the sanctioned primitives (thread-safe removal, teardown outside the lock).
- Actual behavior:
  - `close_device_view` (`pyside/view.py:813-846`): `hasattr(model,'disable')` is False for the rotator. `hasattr(model,'disconnect')` -> `model.disconnect()` (:842-843). Then `del self.system_manager.active_models[device_name]` (:846) with no `self.system_manager.lock`.
  - `open_device_view` (:891-898) rebuilds `RotatorSystem(None)`. `default_port=None` means no `connect()`, and `self.port=None`.
  - The UI (schema `rotator_system.py:153-185`) has a Reconnect button and no port field. `reconnect()` -> `connect(None, 1)` -> `smc100.SMC100(port=None)` -> `assert port is not None` (`smc100.py:111`) -> `AssertionError` caught at `rotator_system.py:94` -> "Rotator Connection Error" popup.
  - The rotator that worked at startup can never be reconnected in that session after unchecking and rechecking the sidebar item.
  - No ST is sent (ROTATOR-1).
  - `docs/architecture/ownership-and-lifecycle.md:92-97` describes this rebuild as "a brand-new serial(port) for the same physical port". For the rotator, the rebuild has `port=None`. The doc is inaccurate for this model.
- Failure scenario: The lab launches PySide with the rotator on COM3. The user unchecks "SMC100 Rotator" to hide the panel, then rechecks it. A dead panel shows Disconnected, and Reconnect raises an assertion error popup. The only cure is restarting the app.
- Proposed fix direction: Use `system_manager.remove_model` plus `teardown()`. Hide/show should not destroy the model, or `close_device_view` should retain `model.port` and construct with it on reopen. Add a port field or dropdown to the rotator schema for runtime-created models.
- Confidence: verified

### ROTATOR-6
- Title: Rotator polling runs synchronous serial I/O on the GUI thread, with up to about 1 s (Tk) or about 2 s per tick (PySide, double-polled) stalls when the controller stops answering. STOP is unreachable while the UI is frozen.
- Severity: medium
- Views affected: Tkinter / PySide6 (Web polls on HTTP threads, so no UI freeze)
- Reference behavior: `main` polled every 500 ms on the Tk thread (`rotator.py` `_poll_status`, `root.after(500)`) with the same blocking calls. The refactor is 5x more frequent.
- Actual behavior:
  - Tk: `start_polling` (`tkinter/view.py:573-578`) schedules `_poll_stat` -> `model.poll_status()` every 100 ms on the UI thread.
  - `poll_status` (`rotator_system.py:267-281`) does `get_position_deg` + `get_status`. Each uses `sendcmd(..., retry=10)` with a 50 ms read timeout (`smc100.py:129,197,254,417-422`), holding `_serial_lock`. That is up to about 0.5 s per call while the device is silent.
  - PySide: `QtDynamicView.__init__` starts BOTH `status_timer` (100 ms, `pyside/view.py:150-153`) calling `poll_status` via `_safe_poll_status`, AND the general `timer` (50 ms, :140-142) whose `_poll_model` (:401-405) calls `poll_status` again. That is roughly 30 blind polls per second, each up to 1 s if the device is unresponsive.
  - The FULL STOP click handlers run on the same GUI thread, so a frozen UI cannot deliver ST (Tk `stop_btn` :188, PySide :~758).
- Failure scenario: The controller is powered off or a cable is jostled during a move. The dashboard hangs for seconds at a time. The operator cannot click STOP. The move continues.
- Proposed fix direction:
  - Move `poll_status` to a background thread, or make it non-blocking. Give the model a small poller thread that writes the lock-guarded props, as the temperature and probe models do. Views then only read the props.
  - Remove the duplicate PySide poll (drop `_poll_model`'s hardware calls or the `status_timer`).
  - Use a short single-shot timeout for status polls (no 10-retry) or skip a poll if `_serial_lock` is busy.
- Confidence: verified (structure). The stall duration is an estimate from the timeout constants.

### ROTATOR-7
- Title: Web per-device lock serializes STOP behind status polls, and a blocking `reconnect` under the same lock stalls all state polling.
- Severity: medium
- Views affected: Web
- Reference behavior: Tk/PySide call `stop` directly on the GUI thread, with no cross-request lock.
- Actual behavior:
  - `get_state` (`web_adapter.py:278-312`) holds `dev_lock` while calling `model.poll_status()` (:287-299) on each `/api/state` request. Requests come from every open browser tab, every poll cycle.
  - `dispatch_command` (:402-411) takes the same `dev_lock` for every command. The per-device "STOP" button (`command: "stop"`, `rotator_system.py:170`) therefore waits for any in-progress poll (up to about 1 s if the device is silent, ROTATOR-6).
  - `reconnect` and `reset_and_configure` -> `reconnect` runs `disconnect()` and `connect()` synchronously under `dev_lock`. Every `/api/state` call for the rotator blocks until it finishes.
  - The global FULL STOP (`full_stop_all`, :420-431) bypasses `dev_lock` but still waits on `smc._serial_lock` (see ROTATOR-8).
- Failure scenario: The controller is slow or unresponsive, and multiple browser tabs are polling. The operator presses the rotator STOP button. The HTTP request queues behind poll threads and the stop is delayed by seconds. The UI still shows the toast "STOP executed" only after it returns.
- Proposed fix direction: Do not take `dev_lock` for `stop` or `emergency_stop`. Have the rotator poll off-thread and let `/api/state` read cached props. Run `reconnect` off the request thread and return immediately.
- Confidence: verified (locking structure). The delay magnitude is an estimate.

### ROTATOR-8
- Title: `emergency_stop()` is neither non-blocking nor cancelling. It can be overtaken by a not-yet-sent move, and it can block behind the serial lock.
- Severity: medium
- Views affected: Model (all views)
- Reference behavior: The `ManagedModel.emergency_stop` contract (`model/base.py`): "must be fast and must never block". `main`'s `cmd_stop` sent ST directly.
- Actual behavior:
  - `emergency_stop` -> `stop` (`rotator_system.py:191-203`) -> `smc.stop()` -> `sendcmd('ST')`, which takes `_serial_lock` (`smc100.py:393`) and, for a non-responding command, sleeps up to 60 ms inside the lock (:426-431).
  - The lock is held for whole poll transactions (up to about 0.5 s each with retry=10).
  - Race: a move click spawns a thread (`_run_async`) that will send `PA`/`PR` later. If STOP is pressed after the click but before the thread reaches `sendcmd`, the sequence is ST then PA. The stage moves anyway, and STOP looks like a no-op.
  - `stop()` also reads `self.smc` without the model lock. It can be `None` after a concurrent `disconnect()`, which is silently ignored.
- Failure scenario: The user double-clicks Move Absolute and immediately hits FULL STOP. The stop lands first and the move then executes after it.
- Proposed fix direction: Add an `_estop` event checked by workers immediately before sending motion commands. Set it in `emergency_stop`, clear it on the next explicit user command. Use `_serial_lock.acquire(timeout=...)` with a raw-write fallback for ST, or a separate priority path.
- Confidence: verified (code). The race window is small.

### ROTATOR-9
- Title: Stale and misleading state: poll failures leave the last position/state on screen forever, and the display differs from `main`.
- Severity: medium
- Views affected: Tkinter / PySide6 / Web / Model
- Reference behavior:
  - `main`: `pos:.4f deg`, "--.-- deg" when disconnected, raw controller state code, poll errors swallowed silently.
  - Tk (intended): a mapped human state and no false readings.
- Actual behavior:
  - `poll_status` (`rotator_system.py:267-281`) on exception only calls `ErrorRouter.report_warning` and leaves `position`/`state`/`error` unchanged. `is_connected` is never cleared. A permanently dead link therefore shows the last "Ready" and position indefinitely, and the web badge (`_determine_connection_status` -> `is_connected` True) stays "HARDWARE".
  - Every distinct-message failure raises a modal warning popup in Tk/PySide (`ErrorRouter` spam filter is 5 s per message text, `error_routing.py:14-25`). A disconnected rotator therefore pops a modal warning about every 5 s. `main` was silent.
  - Position is stored as a raw float, so the readonly field shows `str(float)`, for example `12.345600000001` (`tkinter/view.py:323-331` and `pyside/view.py:_poll_model` use `str(getattr(...))`). Before the first poll it shows `None`.
  - `_map_state_code` (:234-265) leaves several real SMC100 codes unmapped and returns the raw code: 0D, 0E, 0F (not referenced from disable/ready/moving), 10, 11, 14 (configuration), 35 (ready from jogging), 46, 47. It also invents `"3F"` (:252, the comment admits it is not in the docs). The fallback branch under `except ImportError` is a duplicate hard-coded table.
- Failure scenario: The cable falls out mid-session. The dashboard keeps showing "Ready" and the last angle while popups repeat every 5 s. The operator believes the stage is idle at that angle.
- Proposed fix direction:
  - On N consecutive poll failures, set `_state="Communication lost"`, `_position=None`, and consider clearing `is_connected`. Rate-limit warnings (single warning on transition).
  - Format position (`f"{pos:.4f}"`) in a display property, or keep it numeric with a formatter in the schema.
  - Map all documented state codes from `smc100.py` constants (add the missing constants).
- Confidence: verified

### ROTATOR-10
- Title: Tk notebook "Close Tab" (middle-click or right-click) forgets the tab without teardown. The rotator stays connected, still registered and still polled, with the confirm callback still bound.
- Severity: medium
- Views affected: Tkinter
- Reference behavior: PySide's uncheck path at least disconnects (ROTATOR-5). Docs (`ownership-and-lifecycle.md`) describe removal semantics as per-device destruction.
- Actual behavior:
  - `DraggableClosableNotebook.on_close_tab_callback` is set to `None` at `tkinter/view.py:120` and never assigned anywhere (grep shows only definition :120 and use :159-160).
  - `close_tab` (:158-162) therefore falls through to `self.forget(index)`. Tk does not destroy the frame. The `DynamicView` keeps its 50 ms `_poll_model`, the `_poll_stat` 100 ms `after` loop continues, and `system_manager.active_models` still holds the model. Only the widget is hidden.
  - Consequences: the serial port stays open, FULL STOP still reaches the model (good), but the user believes the device is closed. Any pending timers keep calling `poll_status` on the hidden view. There is no way to reopen the tab.
  - Related: `DashboardWindow.on_close` `destroy()`s without cancelling those `after` loops (`tkinter/view.py:226-231`). The loops keep firing on torn-down models. This is harmless for the rotator (`is_connected` False), but it is a leak.
- Failure scenario: The operator middle-clicks the rotator tab to "close" it, then powers down the controller. The hidden poll loop starts raising warning popups (ROTATOR-9). The tab is gone and the port is still open, so another tool cannot open the port.
- Proposed fix direction: Set `notebook.on_close_tab_callback` in `DashboardWindow` to a handler that calls `system_manager.remove_model(name)` + `teardown()` (with ST, per ROTATOR-1), cancels the view's `after` ids, and destroys the frame. Or remove the close affordance.
- Confidence: verified

### ROTATOR-11
- Title: `wait_states` has a hard 12 s cap. A long move or homing raises `SMC100WaitTimedOutException` while the stage is still moving, and `home()` can leave a stale move.
- Severity: low
- Views affected: Model / driver (all views)
- Reference behavior: `main` used the same driver (`lib/smc100.py`). Errors there went to a `messagebox` from `_async_wrapper`. The behavior is the same, but the refactor's spam-filtered `ErrorRouter` may hide repeats.
- Actual behavior:
  - `MAX_WAIT_TIME_SEC = 12` (`smc100.py:8`). `wait_states` (:330-336) raises after 12 s.
  - The worker ends (with an error popup "Action failed: Wait timed out") but the stage continues. `home()` (:166-186) calls `wait_states` after "OR". A slow home can exceed 12 s. `move_absolute_deg` of a large angle at low velocity also can. Whether the real stage exceeds 12 s depends on its velocity (UNVERIFIED for the real hardware).
  - After the timeout, the model has no "moving" flag, so the state line is only the raw poll.
  - `wait_states` also treats `SMC100DisabledStateException` as an error mid-wait. That is correct behavior, but it is reported as "Action failed" with no hint to press Reset.
- Failure scenario: A 60 degree move takes 15 s. The user sees "Wait timed out" error and assumes failure. Meanwhile the move completes and the state flips to Ready.
- Proposed fix direction: Make the wait ceiling proportional to distance/velocity or treat a timeout on a move as a warning. `poll_status` already reflects "Moving".
- Confidence: verified (constants); hardware trigger is a hypothesis.

### ROTATOR-12
- Title: Numeric input handling is inconsistent across views, and invalid or uncommitted input silently no-ops, unlike `main`'s "Invalid Input" warning.
- Severity: low
- Views affected: Tkinter / PySide6 / Web
- Reference behavior:
  - `main`: `cmd_move_absolute` reads the entry at click time and warns on `ValueError` (`rotator.py` `cmd_move_absolute` / `cmd_move_relative`).
  - Tk: commit-on-FocusOut/Return, with `self.focus_set()` at click (`tkinter/view.py:492`) to flush the edit.
- Actual behavior:
  - Model: `_move_abs_ui` etc. (`rotator_system.py:132-151`) use `safe_float`. On `None` they `return` silently. `model/numeric.py` documents why NaN/inf must abort, which is the right safety choice, but no message is emitted.
  - PySide: `QtDynamicView._execute_command` (`pyside/view.py:377-399`) has no focus flush. Numeric entries commit on `editingFinished`. UNVERIFIED HYPOTHESIS: on macOS, `QPushButton` gets `Qt::TabFocus` only, so a click does not take focus and `editingFinished` may not fire before `clicked`, so Move Absolute would use the previous `target_deg`. Check: on macOS, type a value in Target, click Move Absolute without pressing Enter, and see which angle is sent. On Windows/Linux, buttons take focus and the order is fine. Also the validator `QDoubleValidator(-1e9,1e9,3)` (:~245) limits input to 3 decimals.
  - Web: entries commit only through the "Set" button or Enter (`app.js:673-692`). Typing a value and clicking Move Absolute uses the previously set `target_deg`. `set_device_attribute` (`web_adapter.py:451-467`) applies no numeric validation because the existing value is a `str` (`target_deg="0"`), so `"abc"` is stored and later silently ignored.
  - Defaults differ from `main`: the model's `target_deg`/`step_deg` are `"0"`/`"0"` (:26-27), while `main` used `0.0`/`1.0`. A step of 0 makes Move+/- do nothing until edited.
- Failure scenario (web): The operator types 10 in "Step" and clicks Move+ without pressing Set. The stage moves by the previous step (0 = nothing, or an older value), silently.
- Proposed fix direction: Emit `ErrorRouter.report_warning("Invalid input", ...)` from the `_move_*_ui` helpers. In the web JS, have the Move button read and send the sibling input value (or auto-`set_attr` before dispatch). In PySide, call `self.setFocus()`/`clearFocus()` before running a command, mirroring Tk. Restore the `main` defaults (1.0 step).
- Confidence: verified (Tk, Web, model). The PySide macOS behavior is a hypothesis.

### ROTATOR-13
- Title: SIM/None port and disconnected states leave every control silently inert, and the web badge says "SIMULATED" for a model that simulates nothing.
- Severity: low
- Views affected: Tkinter / PySide6 / Web / Model
- Reference behavior: `main` disabled every control while disconnected (`_set_controls_state("disabled")`) and showed an error dialog on failed connect, with a Connect/Disconnect toggle and an editable port field.
- Actual behavior:
  - `RotatorSystem.__init__` (:29) skips `connect` for `None`/"None"/"SIM". `home`, `move_*`, `reset_and_configure`, `stop` all begin with `if self.smc:` and silently return (:128-130, :191-193, :205-207, :217-223).
  - No control is disabled in any view, and the schema has no connection-state field. The only signs are "Disconnected" and a `None` position.
  - Web `_determine_connection_status` (`web_adapter.py:242-245`) checks `port in ("SIM",...)` BEFORE `is_connected`, so a rotator configured for SIM shows a "SIMULATED" badge alongside state "Disconnected".
  - `reconnect()` (:187-189) reuses the stale `self.port`. There is no way to change the port at runtime (no port entry, unlike `main`'s toggle_connection).
  - The web schema exposes `_move_abs_ui`, `_move_rel_pos_ui`, `_move_rel_neg_ui` (leading-underscore methods) as API-dispatchable commands via `_schema_commands` (`web_adapter.py:315-327`). This works but couples the API surface to private helper names.
- Failure scenario: A user picks "Headless/SIM" for the rotator to "test" the UI. Buttons do nothing, no errors appear, and the badge says SIMULATED.
- Proposed fix direction: Either implement a simulated SMC100 (like other models' SIM), or show an explicit "not connected" state and disable/warn on commands when `smc is None`. Add a Disconnect/port field. Consider renaming the `_..._ui` methods to public names.
- Confidence: verified

### ROTATOR-14
- Title: Web setup re-initialization creates the new rotator (opening the port) BEFORE tearing down the old one.
- Severity: low
- Views affected: Web
- Reference behavior: `SystemManager.reboot_model` (`system_manager.py:32-51`) tears down first and only then constructs.
- Actual behavior:
  - `initialize_setup` calls `app_bootstrap.build_models` (`web_adapter.py:148`), which constructs `RotatorSystem(port)` and opens the serial port. Only later (:184-185) does it `old_manager.shutdown_all()`.
  - If a previous manager already holds the same port, the new `SMC100(...)` open can fail or double-open depending on the OS. On failure the new model is left "Disconnected" (failure is only reported through the error queue), and the old model is then closed.
  - Reachable only by POSTing `/api/setup/initialize` a second time (the UI wizard does not reappear when mode is "running", `app.js:140-160`), or from two browser tabs racing during setup.
- Failure scenario: A second setup POST with the rotator on the same COM port. The new instance fails to open, the old one is closed, and the rotator ends up disconnected with only a toast to reflect that.
- Proposed fix direction: Shut down the outgoing manager before building the new models, or reject `initialize_setup` when `mode == "running"`.
- Confidence: verified (ordering). Platform behavior is a hypothesis.

### ROTATOR-15
- Title: Dead `error_callback` hook, inconsistent error routing in the model, and documentation errors.
- Severity: low
- Views affected: Model / docs
- Reference behavior: Tk errors go through `ErrorPopupManager` (queue polled on the UI thread, `tkinter/view.py:9-37`); PySide through a Qt signal (`pyside/view.py:20-95`); web through the adapter error buffer (`web_view.py:8-40`, `/api/errors`, polled by `app.js:1017-1030`). All three are thread-safe.
- Actual behavior:
  - `error_callback` is initialised to `None` (:23) and never assigned outside `rotator_system.py` (grep over `src`), so the branches at :66-67, :99-100 and :196-197 are dead. Every path reaches `ErrorRouter`.
  - The `except Exception: pass` around the `ErrorRouter` import (:72-73, :105-106, :202-203, :280-281) hides any failure to report.
  - `disconnect()` swallows `smc.close()` errors silently (:116-120).
  - `connect()` failure is reported only through `ErrorRouter` (fine), and success is silent. The `docs/architecture/error-routing.md:118-123` table matches the code ("fully silent" for success).
  - Repeat errors within 5 s with identical text are dropped by `ErrorRouter._is_spam` (`error_routing.py:14-25`). A repeated failing STOP or move could go unreported.
  - Doc inaccuracies: `docs/architecture/models.md:150-151` says `connect` runs via `_run_async`/`_async_wrapper`. In fact `connect` is synchronous (`rotator_system.py:75-106`), and only `home`/`reset_and_configure`/`move_*` are async. `models.md:171` ("Builds smc100.SMC100") omits that `__init__` blocks on it. The `ownership-and-lifecycle.md:92-97` "same physical port" claim does not hold for the rotator in PySide (see ROTATOR-5). `ownership-and-lifecycle.md:100-101` says `teardown = disconnect`. It is a wrapper method (:122-123), not an alias, which is equivalent in effect.
  - MVC strain: the view injects `confirm_rotation_callback` into the model (acceptable dependency inversion). The PySide view directly mutates `system_manager.active_models` (ROTATOR-5), and `WebDashboardWindow` reads `system_manager.active_models` directly (`web_view.py:~50`).
- Failure scenario: A developer sets `model.error_callback` expecting routing and it does nothing. A move fails twice with the same text within 5 s and the second failure is invisible.
- Proposed fix direction: Delete `error_callback` or wire it. Log (do not silently pass) when the `ErrorRouter` import fails. Include a sequence counter or timestamp in messages that must not be de-duplicated. Correct the docs.
- Confidence: verified

## Answers to the assignment questions (summary)

1. Connect / disconnect / teardown / emergency_stop:
   - `teardown()` = `disconnect()` (wrapper). `emergency_stop()` = `stop()` = "ST" only.
   - Tk: FULL STOP -> `full_stop_all`. Window close -> `shutdown_all` -> `teardown`. The Tk tab-close affordance does nothing to the model (ROTATOR-10).
   - PySide: FULL STOP -> `full_stop_all`. Window close -> `shutdown_all`. Dock close or uncheck -> hand-rolled `disconnect()` plus a dict delete (ROTATOR-5).
   - Web: FULL STOP -> `full_stop_all`. The window-close path targets a stale manager (ROTATOR-2). `reconnect` and `stop` go through `dispatch_command` under `dev_lock` (ROTATOR-7).
   - Neither `teardown` nor `disconnect` sends ST (ROTATOR-1).
2. Async threading: one daemon thread per operation, with no cancel, no busy flag and no serialization at the model level (ROTATOR-4, ROTATOR-8). The only serialization is the driver's `_serial_lock`. Closing mid-move closes the port under the worker (ROTATOR-1).
3. Tubing confirmation:
   - Tk: enforced via `_confirm_rotation_dialog` (`tkinter/view.py:165-167,198-199`).
   - PySide: enforced (`pyside/view.py:849-856,921-922`). The dialog runs on the GUI thread because the buttons are clicked there.
   - Web: NOT enforced. Moves over 30 degrees are silently refused with a success toast (ROTATOR-3).
   - All views: the relative-move check can be bypassed (ROTATOR-4).
   - `main` had no confirmation.
   - `home` is not checked. The target 0 is in range, and this matches `main`.
4. Refresh path:
   - Tk: 100 ms `_poll_stat` on the UI thread plus 50 ms `_poll_model` for field sync.
   - PySide: 100 ms `status_timer` and the 50 ms `_poll_model` both call `poll_status` (double poll), all on the UI thread (ROTATOR-6).
   - Web: `/api/state` calls `poll_status` in the HTTP thread under `dev_lock`. The JS then writes the text (`app.js:827-850`).
   - Stale state on failure: ROTATOR-9.
5. Error routing: async and poll errors reach `ErrorRouter` in all views (thread-safe). Failures are not reflected in state (ROTATOR-9). A blocked or refused command produces no user-visible feedback in the web view (ROTATOR-3, ROTATOR-12, ROTATOR-13). `error_callback` is dead (ROTATOR-15).

## Coverage

Read in full:
- `src/model/rotator_system.py` (1-281)
- `src/lib/smc100.py` (1-526)
- `src/model/base.py`, `src/model/system_manager.py`, `src/error_routing.py`
- `src/views/web/web_adapter.py` (1-489), `web_server.py` (1-341), `web_view.py` (1-78)
- `git show main:src/rotator.py` (all)

Partially read:
- `src/views/tkinter/view.py`: 1-118 (error manager), 108-235 (notebook, DashboardWindow), 263-545 (DynamicView schema build, `_execute_command`, `_poll_model`), 540-580 (`start_polling`). Not read: the RedPercentView beyond :540-700.
- `src/views/pyside/view.py`: 20-100, 121-300, 377-455, 660-690, 715-967. Not read: SelectionOverlay/PlotDialog and other schema branches (:300-376).
- `src/views/web/static/js/app.js`: 130-175, 395-470, 540-700, 690-795 (bind events), 795-1060 (poll cycle, badges, logs/errors), 1085-1250 (dispatch, set attr, e-stop). Not read: the rest (setup wizard, plotter and focus panels, :1250-1850 except greps).
- `src/app.py`: 700-818 (PySide setup launch, `run_web_app`). `src/app_bootstrap.py`: 40-180.
- `git show main:src/mainGUI.py` around the rotator spawn/close (:335-375).
- docs: `error-routing.md` (112-130), `models.md` (147-200), `ownership-and-lifecycle.md` (92-106).

Not done:
- `src/views/web/static/index.html` and `style.qss` were not inspected.
- No runtime execution (per rules), so the PySide macOS focus behavior and the serial timing estimates remain hypotheses.
- Tk legacy setup code in `app.py` (:1-600) was not audited for rotator-specific flow beyond the model construction site.
- No test files were examined for rotator coverage.
