# Audit: errors (ErrorRouter and per-view popup managers)

> **Input (banner added 2026-09-23).** Audit of the old tree (now `legacy/src/`); current until the rebuild replaced it on 2026-09-23. Kept because the ledger in `docs/implementation/progress.md` and `docs/rebuild/carry.json` cite these finding IDs.

## Object summary

- `ErrorRouter` (`src/error_routing.py`, 53 lines) is a class-level singleton. Callbacks are set through `set_callbacks` (line 12). There is no instance, so nothing is constructed or torn down. Callbacks are never cleared or reset.
- Installers (each replaces the process-wide callbacks):
  - Tkinter: `ErrorPopupManager.initialize(root)` at `src/views/tkinter/view.py:19-25`. Called from `src/app.py:430` (SetupWindow) and again from `src/app.py:423` (dashboard Toplevel).
  - PySide6: `QtErrorPopupManager.initialize(app)` at `src/views/pyside/view.py:34`, called from `src/app.py:758`. The constructor at line 28-31 sets the callbacks.
  - Web: `WebErrorManager.initialize()` at `src/views/web/web_view.py:11`, called from `WebDashboardWindow.__init__` at line 53. Errors go into `WebAPIHandler.error_buffer` (a `_BufferProxy`, `web_server.py:243-293`) and then `WebModelAdapter.error_buffer` (`web_adapter.py:27,479-489`, capped at 500, oldest dropped). The browser polls `GET /api/errors` (`web_server.py:109`), which drains the buffer. `app.js:1017` `pollErrors` shows each entry as a toast.
- Excepthooks:
  - Tkinter: `setup_excepthook` is called once, at `app.py:431`.
  - PySide6: called once, at `app.py:759`.
  - Web: `app.py:790-796` sets both `sys.excepthook` and `threading.excepthook`.
  - Tkinter and PySide6 never set `threading.excepthook`.
  - Tkinter never overrides `report_callback_exception` (grep is empty).
- Teardown: none for any manager.

## Q1 - Thread marshaling per view

- **Tkinter**: `report_*` calls `queue.Queue.put` (`view.py:86-91`), which is thread-safe. The queue is drained by `_root.after(100, _poll_queue)` on the main thread (`view.py:28-37`). No widget is touched from a worker thread on the error path (VERIFIED).
- **PySide6**: `report_*` emits `_message_signal` (`view.py:42,51,58`). The manager `QObject` is created on the main thread, so a call from another thread is delivered as a queued connection. `QMessageBox` is only created in `_display_popup` (`view.py:62-79`). No widget is touched from a worker thread on the error path (VERIFIED).
- **Web**: `report_*` only appends to a list under `_state_lock` (`web_adapter.py:479-483`). No widgets exist. Delivery is by the browser polling, so there is no push.
- **Threads that call `report_*`** (VERIFIED):
  - temperature read thread (`temperature_system.py:36,117-126`)
  - rotator `_run_async` threads (`rotator_system.py:56-73`)
  - red-percent monitor thread (`redpercent_system.py:268`, reports at 108)
  - probes script thread (`probes.py:336`, reports at 266 and 332)
  - interlock watchdog (`probes.py:420`, report at 416)
  - web `ThreadingHTTPServer` handler threads (via `dispatch_command`)
- **Doc error**: `docs/architecture/error-routing.md:43` names "gamepad polling threads" as a source. That is wrong. The gamepad poll is a GUI-loop callback, `self.gui_root.after(POLL_INTERVAL, self._poll_loop)` at `gamepad.py:637-638`. PySide6 uses a `QTimer.singleShot` adapter (`pyside/view.py:157-159`). Web never calls `start_polling`; only `log_updater` is assigned (`web_view.py:65`). Gamepad errors therefore fire on the main thread in Tkinter and PySide6, and never fire in Web because polling is never started.
- **Non-error thread hazard** (adjacent, verified): the rotator `confirm_rotation_callback` runs in the caller's thread (`rotator_system.py:209-214`). That is the main thread for Tkinter and PySide6, so it is fine there.

## Q2 - User-visible in Tkinter or main but print-only or silent in PySide6 or Web

- Every model and controller `ErrorRouter` report is visible in all three views (as a popup, or a toast in Web) as long as the message text differs (see Q4). The gaps are in view-level error surfaces and are covered by ERRORS-1 through ERRORS-4 and ERRORS-9.
- Main baseline (`git show main:src/...`):
  - `mainGUI.py:12-24` monkey-patches `showerror`. On a non-main thread it only prints `[Background Error - ...]`, and it truncates to 5000 characters.
  - `mainGUI.py:317,322,327` are launcher validation popups. The MVC equivalents are `app.py:387,394` (Tkinter) and `app.py:719` (PySide6).
  - `rotator.py` shows `showerror` or `showwarning` at lines 155, 199, 213, 223 and 232. `temp_control.py:35` shows `showerror`. `camera_control.py` also shows dialogs.
  - `controllerDrive.py` and `serialDrive.py` only print, with no messagebox. The refactor is therefore louder than main, not quieter.

## Findings

### ERRORS-1 Web silently blocks rotation past ±30 degrees while showing a success toast
- Severity: medium
- Views affected: Web (Model coupling)
- Reference behavior:
  - Tkinter: `confirm_rotation_callback` is set at `tkinter/view.py:199`. It shows an `askyesno` dialog ("Rotation Limit Warning", `view.py:165-167`).
  - PySide6: set at `pyside/view.py:922`, dialog at 849-856.
  - Main: rotator dialogs.
- Actual behavior:
  - The Web view never sets `confirm_rotation_callback`. Grep for `confirm_rotation` and `limit` in `src/views/web` finds nothing.
  - The model falls back to `print("... blocked automatically (no confirmation handler registered)")` and returns False (`rotator_system.py:209-214`). `move_absolute` and `move_relative` then return normally (217-232).
  - `dispatch_command` returns `status: ok` (`web_adapter.py:410`). The JS shows a "`[dev] cmd executed`" success toast (`app.js:1152-1153`).
- Failure scenario: the user enters a 45 degree target in the web UI. The toast says executed, the stage does not move, and the only explanation is a console print. There is also no way to approve the move.
- Proposed fix direction: make `_confirm_rotation` report a warning through `ErrorRouter.report_warning` when it blocks. Add a web confirm flow (a two-step `force`/`confirmed` argument on the command, with a JS `confirm()`), or return a `status: needs_confirmation` result that the JS turns into a prompt.
- Confidence: verified

### ERRORS-2 Web: toast HTML injection and dropped exception detail
- Severity: medium
- Views affected: Web
- Reference behavior: Tkinter and PySide6 append `Details: <ExcType>: <msg>` and truncate to 5000 characters (`tkinter/view.py:47-55`, `pyside/view.py:63-72`).
- Actual behavior:
  - `WebErrorManager.report_*` stores `str(exception)` in `"exception"` (`web_view.py:20,29`). The JS toast only uses `${err.title}: ${err.message}` (`app.js:1024`). The exception field is never shown, and there is no 5000-character truncation.
  - `showToast` builds `toast.innerHTML` with the raw message (`app.js:1508-1519`). Exception text from serial, OS or file paths is therefore interpreted as HTML. A message containing `<` or `&` is mangled, and a hostile string could inject markup.
  - Toasts auto-dismiss after 4500 ms (`app.js:1535`).
- Failure scenario: a message that includes `<0,6.0,...>` or an SMC error containing angle brackets renders wrong or blank. A safety error is gone after 4.5 seconds, and there is no persistent log.
- Proposed fix direction: use `textContent` for the message. Append `err.exception` in the toast (or in the log console). Keep errors in a persistent "Errors" list (the log console) and keep error toasts until dismissed.
- Confidence: verified

### ERRORS-3 Web error buffering: no delivery guarantee, no console echo, and floods on connect
- Severity: medium
- Views affected: Web
- Reference behavior: Tkinter queues messages and shows every one. PySide6 shows every one. Neither drops anything, and both pop up immediately.
- Actual behavior:
  - `WebErrorManager.report_*` never prints (`web_view.py:15-39`). Once callbacks are set, `ErrorRouter` no longer prints either (`error_routing.py:33-36`). Web errors therefore reach the console only if the reporting module also prints; `ErrorRouter` itself is silent. This includes `_verify_serial`-style warnings.
  - `pop_errors` clears the buffer (`web_adapter.py:485-489`), so it is single-consumer. Two open browser tabs split the errors, and one tab may never see a given error. `runPollCycle` is skipped when `isPolling` is true and the poll interval can be set to 0 (PAUSED, `app.js:792-805`). Buffered errors then sit unseen.
  - With no browser open, up to 500 errors accumulate. The oldest are dropped past 500 (`web_adapter.py:480-483`). On first connect, every buffered item becomes a toast at once, and each toast stays 4.5 seconds.
- Failure scenario: an Arduino disconnect warning is raised while the tab is paused or hidden. The warning is silently dropped (past 500) or shown only to whichever tab polls first, and never reaches the operator.
- Proposed fix direction: give each entry a monotonic id. Keep a ring buffer and let each client ask with `?since=<id>` instead of destructively draining. Print in `WebErrorManager` (`[ERROR] title: message`). Collapse multiple toasts into a count badge.
- Confidence: verified

### ERRORS-4 Tkinter and PySide6 lose thread-crash reports (`threading.excepthook` unset)
- Severity: high
- Views affected: Tkinter, PySide6
- Reference behavior: the Web launcher installs both `sys.excepthook` and `threading.excepthook` (`app.py:790-796`) with the comment "sys.excepthook alone never sees exceptions raised there".
- Actual behavior:
  - `setup_excepthook` in Tkinter and PySide6 only sets `sys.excepthook` (`tkinter/view.py:106`, `pyside/view.py:94`). An uncaught exception in a worker thread goes only to the default `threading.excepthook`, so it prints and shows no popup. Candidates are the interlock watchdog `_watch` (`probes.py:405-420`), the temperature read loop `read_serial_data` (`temperature_system.py:100-126`), `_monitor_colors`, and rotator `_async_wrapper` (which catches `Exception`, so only `BaseException` escapes).
  - Tkinter callbacks that raise are not caught by `sys.excepthook`. Tk's default `Tk.report_callback_exception` prints a traceback (no `report_callback_exception` override exists in `src`). This covers the `_poll_model`, `_route_input`, `_poll_pos` and `_poll_stat` `after` callbacks (`tkinter/view.py:540-578`), and the gamepad `_poll_loop` scheduled through `after`. A raising callback prints a traceback and then that `after` chain silently stops.
  - PySide6 slot exceptions do reach `sys.excepthook`.
- Failure scenario: the interlock watchdog thread dies with a raised exception. There is no popup and no idle-timeout disable, and the operator does not know that protection is gone. In Tkinter, `_route_input` throws once. The `after` loop dies, the gamepad stops steering, and the console shows a traceback nobody sees.
- Proposed fix direction: in both `setup_excepthook`s also set `threading.excepthook` to the same handler. In Tkinter also assign `root.report_callback_exception = lambda et, ev, tb: custom_excepthook(et, ev, tb)`. Extract the excepthook into `error_routing` so all views share it (also removes the duplication that `error-routing.md:56-59` notes).
- Confidence: verified

### ERRORS-5 Tkinter popup poll chain can die permanently when the dashboard is destroyed
- Severity: medium
- Views affected: Tkinter
- Reference behavior: main has no such queue.
- Actual behavior:
  - `initialize(cls, root)` sets `cls._root` and starts the poll only once (`tkinter/view.py:19-25`; `_is_polling` is checked at line 23 and set after `_poll_queue()` returns, at 25). `app.py:423` re-calls `initialize(dash)`, which replaces `_root` with the `DashboardWindow` (`tkinter/view.py:164`, `tk.Toplevel`). On close it does `self.destroy()` (`view.py:230`, and the `on_close` at 256-261).
  - `_display_popup` runs `messagebox.showerror(..., parent=cls._root)` (`view.py:57-62`) and is called before the reschedule at line 37. If `_root` is a destroyed Toplevel, `showerror` raises `TclError` (bad window path). The exception propagates out of `_poll_queue`, so `after(100, ...)` is never re-armed. Because `_is_polling` stays True, a later `initialize()` (a second dashboard launch) will not restart the loop.
  - After the dashboard closes, `_root` stays pointing at the destroyed Toplevel until it is re-initialized.
  - Note that `_queue_message` (`view.py:77-91`) also both prints (when `_root is None`) and still queues the item.
- Failure scenario: launch dashboard, close it, and an error is queued during shutdown (for example a teardown error from `shutdown_all`). The `TclError` kills the poll loop. The relaunched dashboard never shows another error popup. Errors then queue silently forever.
- Proposed fix direction: wrap `_display_popup` in try/except so the reschedule always runs. Use `after` on a still-alive root (the Setup `tk.Tk`), or reset `_root` to it on dashboard close. Set `_is_polling` per root. Also make the `_root is None` branch `else`-only (no double handling).
- Confidence: hypothesis. The code path is verified; the `TclError` on `parent=<destroyed Toplevel>` needs a runtime check (close the dashboard and call `ErrorPopupManager.report_error("t","m")`).

### ERRORS-6 Hardware write failures in `serial.enable/disable` are reported but do not fail the caller
- Severity: high
- Views affected: Model, Controller (all views)
- Reference behavior: `_verify_serial` failure raises `ValueError` (`serial.py:241-242,250-251`), and `probes.enable()` turns that into a warning plus `return False` (`probes.py:426-431`). `probes._stop_and_disarm` catches `ValueError` (`probes.py:459-461`).
- Actual behavior:
  - When the port is verified open but `self.ser.write("e")` raises, `serial.enable()` reports `'Serial Write'` and returns normally (`serial.py:244-247`). `probes.enable()` proceeds and sets `system_enabled = True` (after `probes.py:431`). The UI now shows the stage enabled although the Arduino never received `e`. The manual and script paths then send commands into an unenabled controller.
  - Worse, `serial.disable()` (`serial.py:249-256`) swallows the write failure the same way. `_stop_and_disarm` then unconditionally sets `system_enabled = False` (`probes.py:462`). Full Stop shows "disabled" while the Arduino may still be energized. The write may fail, then Full Stop is reported once through the popup, but the model claims the disable succeeded.
  - `power_down` `'k'` write failure only prints (`probes.py:474-477`). `teardown` (`probes.py:479+`) calls it. A failed kill-coils at shutdown is therefore invisible. The `error-routing.md` table cites it at "455/457"; see ERRORS-11.
- Failure scenario: a USB glitch drops the port between the `is_open` check and `write`. The operator presses Full Stop. The popup says a write error, the UI shows "Disabled", but the coils stay energized. The operator walks away.
- Proposed fix direction: make `serial.enable/disable/kill` re-raise or return False. In `_stop_and_disarm` keep `system_enabled` True if the disable send failed, and surface a persistent "STOP NOT CONFIRMED" error. Route the `power_down` failure through `report_error`.
- Confidence: verified

### ERRORS-7 Swallowed exceptions that hide hardware faults (model/controller)
- Severity: high (temperature) / medium (others)
- Views affected: Model, Controller
- Reference behavior: main `temp_control.py:35` popped a `showerror` on failure.
- Actual behavior (each verified at the line):
  - `temperature_system.py:186-188` `close()`: the heater-off write `b"<0,6.0,0,0,0,0>"` failure is `except Exception: pass`. `191-192`: `serial_conn.close()` failure is also swallowed. A failed "heater off" at close is silent, and the heater could stay at its last setpoint.
  - `temperature_system.py:117-121`: after 5 consecutive read failures the thread reports "Temperature Read Error (Fatal)" and `break`s. There is no attempt to set the heater setpoint to 0, and `current_temp` stays at the last stale value. The UI shows a plausible temperature with no "connection lost" state.
  - `temperature_system.py:152-153`: malformed data lines are dropped with no counter or log.
  - `rotator_system.py:119-120`: `smc.close()` failure is swallowed in `disconnect` (also silent if the port is stuck). `72-73`, `105-106`, `202-203`, `280-281`: nested `except Exception: pass` around the `ErrorRouter` call itself. A broken callback hides the original error. `error_callback` (`rotator_system.py:23`) is only ever `None`, so its branches at 66-67, 99-100, 196-197 are dead.
  - `serial.py:69-71`: a handshake write failure does `break` with no report. It is followed by the "no Arduino response ... Operating blind" warning at line 87, which is the only symptom. `serial.py:142-143`: a malformed `POS:` line is skipped silently.
  - `redpercent_system.py:315,319,323`: bare `except:` (this also catches `KeyboardInterrupt` and `SystemExit`) falls back to position 0.0. Non-numeric stepper positions silently become X/Y/Z = 0.0 in the saved log, which corrupts recorded data.
  - `gamepad.py:169,215-216,281-282,298-299,330-331,343-344,363-364,508-509`: `except Exception: pass` around pygame init/quit and joystick calls (the same list is partly at `error-routing.md:99,105`). `gamepad.py:169-171` and `215-216` swallow the joystick mode probes, so a bad device looks like a valid one.
  - `probes.py:166`, `191`: reconnect and controller-swap failures are print-only (also flagged in the doc's table).
  - `pyside/view.py:829-830`: `widget.cleanup()` failure is swallowed on dock close (a view concern; the doc's line 829 is correct).
- Failure scenario: the temperature model's serial write for "heater off" fails at close, and nothing tells the user that the heater is still on.
- Proposed fix direction: log and report the failures above (`report_warning`, or `report_error` for heater or coil commands). Replace the bare `except:` at `redpercent_system.py:315-323` with `except (TypeError, ValueError)` and flag the sample as invalid instead of 0.0. On a fatal temperature read failure, set an explicit `connection_lost` state and attempt heater off.
- Confidence: verified

### ERRORS-8 Popup flood, dedup and buffering behavior
- Severity: medium
- Views affected: Tkinter, PySide6, Web (`error_routing.py`)
- Reference behavior: main showed messageboxes only on the main thread and only printed otherwise. There was no queue, no dedup and no flood.
- Actual behavior:
  - `_is_spam` (`error_routing.py:17-28`) keys on the message text only. It ignores the title and the severity. The 5 s window is measured from the first delivery, since the timestamp is refreshed only when a message is not spam (line 24). A persistent fault therefore pops up again every 5 seconds, indefinitely. Modal boxes stack: `QMessageBox.critical(None, ...)` (`pyside/view.py:75`) and `messagebox.showerror` (`tkinter/view.py:58`) each run a nested event loop, so timers keep firing and add further dialogs.
  - Repeating sources that hit this:
    - `serial.send_manual_mode_command` and `send_autonomous_command` write errors (`serial.py:177-184,231-238`), driven by the manual mode loop.
    - `_safe_read_position` and `_safe_poll_status` at 100 ms (`pyside/view.py:143-152`).
    - the rotator poll, `report_warning` (`rotator_system.py:276-279`).
  - Dedup misses varying text. `"Serial background read error (transient, retry N/5)"` (`temperature_system.py:125-126`) changes every time. The message text embeds the exception.
  - `report_error` and `report_warning` for a genuine second fault with the same text, in the 5 s window, are dropped without any trace (not even a print).
  - No cap on the Tkinter `queue.Queue` (`tkinter/view.py:15`) or on the Qt event queue. Popups queue up serially after each dismissal.
  - `_is_spam` is not thread-safe. The dict rebind at `error_routing.py:27` iterates `_last_messages` while another thread may insert, which can raise `RuntimeError: dictionary changed size during iteration` inside a worker thread (`report_error` then raises into the hardware thread). Rare, only when there are more than 100 entries.
  - The fallback `traceback.print_exc()` (`error_routing.py:37`; `tkinter/view.py:84`; `pyside/view.py:46`) prints the current exception, which is `None` when the caller passes `exception=` outside an `except` block. `traceback.print_exception(exc)` should be used instead.
  - Info-level popups are also modal. `Temperature Send` (`temperature_system.py:88-91`) is an info popup on every send, with a varying message, so it is never deduped.
  - Some faults produce two popups: `gamepad.py:633` reports "Gamepad Polling Error" and `_handle_disconnect` (`gamepad.py:315-318`) then reports "Controller Disconnected".
- Failure scenario: a manual-mode serial timeout stacks a modal every 5 s. The operator cannot dismiss faster than they arrive, and the main window is blocked.
- Proposed fix direction: key on (severity, title, message), rate-limit per key (for example, one popup per key while it is still on screen). Show a non-modal, coalesced log panel with a repeat count for error and warning. Make `report_info` non-modal (a toast or status bar). Guard `_last_messages` with a lock. Drop the `Temperature Send` info popup or replace it with a print.
- Confidence: verified

### ERRORS-9 PySide6 and Web lack the Tkinter view-level error surfaces
- Severity: low
- Views affected: PySide6, Web
- Reference behavior: Tkinter surfaces the failures below as `messagebox` dialogs (`tkinter/view.py:510,794,809,813`).
- Actual behavior: PySide6 has some of these (`pyside/view.py:320,399,544,547`) and bypasses `ErrorRouter` for them (direct `QMessageBox`). PySide6 also calls `ErrorRouter` directly for the file save (`pyside/view.py:706-709`). Web handles command failures by toast (`app.js:1149-1152`) but has no equivalent for the red-percent plotting or CSV checks. This was a targeted check only, not exhaustive.
- Failure scenario: a CSV with a missing "Red Percent" column gives a message in Tkinter and PySide6 but nothing in Web.
- Proposed fix direction: route view-level failures through `ErrorRouter` (or a shared `ViewErrors` helper) so all three views behave alike.
- Confidence: hypothesis. Only the `messagebox` sites were enumerated; the Web plotting path was not read.

### ERRORS-10 Silent before-initialization behavior and the `_is_spam` ordering bug
- Severity: low
- Views affected: all
- Reference behavior: main printed immediately.
- Actual behavior: `_is_spam` runs before the callback check (`error_routing.py:32`). A message printed by the fallback (no callback registered yet, for example during `SetupWindow` startup or a Web boot before `WebErrorManager.initialize`) starts the 5 s window. The same message reported just after a callback is installed is dropped. For Tkinter, messages queued before `initialize` (`_root is None`) are printed and also queued (`view.py:78-91`), so the popup appears later.
- Failure scenario: "Serial Connection Warning" during autodetect is reported before `initialize` (print) and again after (dropped).
- Proposed fix direction: call `_is_spam` only when a callback is registered, or keep the fallback path outside dedup.
- Confidence: verified

### ERRORS-11 `docs/architecture/error-routing.md` factual errors
- Severity: low
- Views affected: docs
- Reference behavior: the doc's own claims and line-number table.
- Actual behavior (each cell checked against source):
  - Line 4: "54 lines" is wrong. `error_routing.py` has 53 lines (`wc -l`).
  - Line 43: "gamepad polling threads" is wrong (see Q1). The gamepad poll is on the main thread.
  - Line 82-84: "serial.enable/disable reports on write failure" and "BaseProbe.enable() reports on `serial_comm.enable()` failure" are misleading. `serial.enable` swallows the write failure and returns normally (`serial.py:244-247`), so `probes.enable()` does not fail. Only the `ValueError` path reports (see ERRORS-6).
  - Line 47-54: the doc says PySide6 and Tkinter each provide `setup_excepthook`, which is true, but the doc does not mention that the Web path does it differently in `app.py:790-796`, and that neither sets `threading.excepthook`.
  - Table rows that are wrong or off (from reading the cited lines):
    - `serial.py` "259 (`close`)": line 259 is `def close`; the print is at 262. Off by 3.
    - `gamepad.py` "363": `pygame.quit()` is line 362, `except` is 363, `pass` is 364. Off by 1 (borderline).
    - `probes.py` "455/457 (`teardown`/power-down) kill-coils success/failure": WRONG. Lines 455 and 457 are a comment and `if self.serial_comm:` inside `_stop_and_disarm`. The kill-coils write is in `power_down` at lines 474 (write), 475 (success print) and 477 (failure print).
    - `temperature_system.py` "178 (`close`, stop-write) bare except": WRONG. Line 178 is the `ErrorRouter` import inside `stop()`'s reporting except (so it is already reported). The swallowed close stop-write is at 187-188.
    - `pyside/view.py` "337 (dropdown rescan)": WRONG. Line 337 is `return handler`. The rescan tooltip is at 341.
  - Rows verified correct (line content matches): `serial.py` 48, 83; `gamepad.py` 374, 393, 450, 467, 485, 508; `probes.py` 20, 41, 162, 167, 183, 192; `rotator_system.py` 10, 93, 119; `temperature_system.py` 8, 191; `redpercent_system.py` 58, 95, 265, 273; `system_manager.py` 44; `pyside/view.py` 829, 847, 911. Cited class ranges also verified: `pyside/view.py:20-94`, `tkinter/view.py:9-106`, `web_view.py:8-39`.
  - Line 63-66 of the addendum ("dict capped at 100, pruned by age"): the second-pass note that it is not a hard cap is correct. Actual behavior: pruning only removes entries older than 5 s, so a burst of over 100 new messages inside 5 s is never pruned (`error_routing.py:26-27`).
  - Missing from the doc: `serial.py:100-107`, `_verify_serial(verbose)` reports "Serial Disconnected" only when `verbose` is true.
- Failure scenario: an agent delegated the "255/457" style rows edits the wrong lines.
- Proposed fix direction: correct the rows listed above. Add a note on `threading.excepthook`, on the Web toast, and on the serial swallow behavior.
- Confidence: verified

### ERRORS-12 Web `WebErrorManager` and `_BufferProxy` structure (MVC strain)
- Severity: low
- Views affected: Web
- Reference behavior: the Tkinter and PySide6 managers own their own state.
- Actual behavior: `WebErrorManager` writes to `WebAPIHandler.error_buffer`, a proxy class object (`web_server.py:243-293`) that lazily builds a `WebModelAdapter()` with no system manager if none exists (`web_server.py:253`). Error reports before the server starts can therefore create a throwaway adapter. `WebAPIHandler.adapter` is later replaced or kept by the descriptor (`web_server.py:236-240`), and the buffer only survives if the same adapter instance is reused.
- Failure scenario: `report_error` fires before the dashboard is created. A second `WebModelAdapter` replaces the one that holds the buffered error, and the error vanishes.
- Proposed fix direction: make the buffer a module-level object in `web_view.py` that the adapter borrows, or have `WebErrorManager` hold the buffer.
- Confidence: hypothesis. The replacement path in `_SystemManagerDescriptor.__set__` (`web_server.py:236-240`) keeps an existing adapter, so a true loss needs a manual construction order that was not confirmed.

## Coverage

- Fully read: `src/error_routing.py`; `docs/architecture/error-routing.md`; `src/views/tkinter/view.py` 1-110, 160-232, 500-580 (spot); `src/views/pyside/view.py` 1-100, 140-175, 300-345, 392-400, 695-712, 820-925 (spot); `src/views/web/web_view.py` 1-80; `web_server.py` 100-118, 230-335; `web_adapter.py` 400-490; `app.js` 792-822, 1005-1030, 1140-1160, 1508-1540.
- Model and controller: `serial.py` 40-110, 118-190, 225-262; `gamepad.py` (all `report_*` sites plus the swallow lines); `probes.py` 226-240, 258-270, 326-336, 405-480; `rotator_system.py` 55-125, 190-290; `temperature_system.py` 20-130, 145-195; `redpercent_system.py` 100-110, 250-275, 310-325; `system_manager.py` 36-80; `app.py` 405-432, 745-810.
- Main: greps of `mainGUI.py` (the messagebox patch), `rotator.py`, `temp_control.py`, `camera_control.py`, `controllerDrive.py`, `serialDrive.py` for popup and print surfaces only.
- Not covered: the full web JS error flow beyond the lines above (whether every model action surfaces a toast); Tkinter-specific callback error paths beyond the sites grepped; `chuck_frame`/`stepper_frame` in main; whether `_BufferProxy` loses errors in practice; the runtime `TclError` claim in ERRORS-5.

DONE errors 12
