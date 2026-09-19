# view-pyside audit (src/views/pyside/view.py, style.qss)

All line numbers are for `src/views/pyside/view.py` unless prefixed. Tk = `src/views/tkinter/view.py`.

## Object summary

- **Constructor site:** `DashboardWindow(system_manager)` is built in `src/app.py:743-750` (`run_pyside_app` -> `SetupWindow.launch`). Models are built by `app_bootstrap.build_models` (`app.py:731`), the Red Percent probe link is done inline (`app.py:735-741`), and each model is `register_model`'d into a fresh `SystemManager` (`app.py:745-748`). The setup window is then `close()`d.
- **Owner of models:** `SystemManager` (`self.manager` on the setup window, `system_manager` on the dashboard). Each `QtDynamicView` or `RedPercentDynamicView` holds a reference to its model and does not own it.
- **Owner of views:** `DashboardWindow.active_docks: dict[name -> DeviceDock]` (`:772`). Each dock owns one view widget (`:924-941`).
- **Teardown paths:**
  1. Dock X-button or sidebar uncheck -> `close_device_view` (`:813-847`). This is a hand-rolled partial teardown and does not use `SystemManager.remove_model` or `ManagedModel.teardown()`. See PYSIDE-2.
  2. Main window close -> `DashboardWindow.closeEvent` (`:957-967`). It calls `widget.cleanup()` on each dock, then `system_manager.shutdown_all()` (which calls `teardown()` on each model, `system_manager.py:59-68`). This path does honor the contract.
  3. FULL STOP -> `stop_btn.clicked -> system_manager.full_stop_all` (`:756-758`), which calls `emergency_stop()`. Honors the contract.
  4. `reboot_model` is never used by the PySide view (not needed).
- **Verified OK, no finding:**
  - `QtErrorPopupManager` marshals popups from any thread through a Qt signal (`:26, 30, 40-46`). The QObject lives on the GUI thread, so the emit becomes a queued connection and is thread-safe.
  - `DeviceDock.closed` lambda connections (`:933`) die with the dock and do not leak.
  - `ControllerLogWindow.destroyed` handling (`:382`) is fine.
  - In `QtDynamicView.cleanup`, the QTimers are children of the view, are stopped explicitly, and are deleted with the dock.
  - `poller.close()` is idempotent (`gamepad.py:494-500` guard `_closed`), so the double close from `cleanup()` plus `close_device_view` or `teardown()` is safe.

## Doc corrections (existing docs are wrong or misleading)

- **`ownership-and-lifecycle.md:110-113`** says `remove_model` "internally calls `_teardown_model` -> `model.teardown()`". Wrong. `SystemManager.remove_model` only pops the dict (`system_manager.py:24-27`). `teardown` runs only from `reboot_model` and `shutdown_all`. The proposed fix in that doc would therefore not tear down anything.
- **`ownership-and-lifecycle.md:90-97` and `known-issues.md:30-33`** claim reopen "constructs a brand-new `serial(port)` for the same physical port ... two live handles." Wrong. `open_device_view` builds models with `port=None` (`view.py:886, 889, 892, 895, 898`, and `probes.py:22` gives `serial_comm=None`). The real reopen failure is a permanently disconnected, silently no-op device (PYSIDE-1). The leaked old handle is still real.
- **`ownership-and-lifecycle.md:80-81`** says `power_down()` "= `disable()` ... same as above -- OK". Wrong. `power_down` is `_stop_and_disarm()` plus a raw `b'k\n'` kill-coils write (`probes.py:483-491`). `disable()` does not send `'k'` (`probes.py:464-465`). See PYSIDE-2.
- **`views.md:111`** says `populate_sidebar` "Iterates through `SYSTEM_CONFIG`". Wrong. The list is hard-coded (`view.py:787-790`), and `SYSTEM_CONFIG` appears nowhere in the file.
- **`known-issues.md:89` and `views.md:187`** describe "Tkinter runs polls in the UI thread whereas PySide6 uses QTimers." Misleading. Both run on the GUI thread, and QTimers are not threads. The real divergence is that PySide polls three times as often (PYSIDE-9).
- **`known-issues.md` #5** ("redundant duplicate Sync X/Y/Z controls") is verified fixed. `QCheckBox` is now only an unused import (`:9`). However, the Red Percent view still has duplicate Position Source and duplicate Save Log controls (PYSIDE-8).
- **`known-issues.md` #14** is verified fixed: `:778` uses `active_models`. Two residual problems remain (PYSIDE-14).

## Findings

### PYSIDE-1 — Reopened device dock builds a disconnected "None" model; the device silently does nothing
- **Severity:** high
- **Views affected:** PySide6 (Tk has no reopen path)
- **Reference behavior:** `app_bootstrap.build_models` (`app_bootstrap.py:~150-170`) constructs models with the user's chosen port, controller and the shared `active_claims`. Tk builds all tabs from the already-built models (Tk `:197-222`) and has no per-tab model reconstruction.
- **Actual behavior:** `open_device_view` (`:881-916`) lazily creates `StepperProbe(None, "None", {})`, `DCProbe(None, "None", {})`, `ChuckPositioner(None, "None", {})`, `TemperatureSystem(None)`, `RotatorSystem(None)`. Effects:
  - The port is None, so `BaseProbe.serial_comm=None` (`probes.py:22`). `enable()` then just sets `system_enabled=True` (`probes.py:~437-447`) with no hardware traffic.
  - `RotatorSystem(None)` leaves `smc=None`, so `home`, `move_*` and `stop` are all `if self.smc:` no-ops (`rotator_system.py:~128-206`). The schema "Reconnect" button reconnects `self.port`, which is None (`rotator_system.py:~180-182`), so it cannot recover.
  - `TemperatureSystem(None)` has `serial_conn=None`, so `send_settings` does nothing.
  - The claims dict is a fresh `{}`, so `ControllerPoller` collision detection (`gamepad.py:399-414`, keyed by class name) is bypassed for dock-opened probes. A reopened DC Probe can claim the same joystick as a live Stepper Probe.
  - The "Serial Port" entry (`probes.py:107`) is cosmetic. `setattr(model,'serial_port',...)` never reconnects.
- **Failure scenario:** The user launches with Stepper on `/dev/cu.usbmodem1`, unchecks Stepper, then rechecks it. The dock reappears, "Enter Autonomous Mode" toggles green (`system_enabled` and `auton_flag` flip), the stage never moves, and no error is shown. Same for the rotator and temperature controller. Any device started "Headless" or "SIM" and reopened looks identical to a real device.
- **Proposed fix direction:** Do not reconstruct models in the view (MVC strain). Options: (a) treat dock close as hide-only (dock.hide plus stop timers) and keep the model registered; or (b) route reopen through a controller function that reuses the stored config (port, controller, claims) via `app_bootstrap.build_models` and `SystemManager.reboot_model`. At minimum, when a model is created with port None, show a visible "not connected" state and disable the command buttons. Persist the setup config (port and controller per device) on the dashboard or SystemManager.
- **Confidence:** verified (static)

### PYSIDE-2 — `close_device_view` is a hand-rolled partial teardown, not `ManagedModel.teardown()` / `remove_model()`
- **Severity:** high
- **Views affected:** PySide6 (Tk never tears down on tab close; on shutdown it calls `shutdown_all`, `Tk:226-231`)
- **Reference behavior:** `SystemManager.shutdown_all` and `reboot_model` call `model.teardown()` (`system_manager.py:18-22, 59-68`). `BaseProbe.teardown()` is poller stop and close, then `power_down()` (`_stop_and_disarm` plus raw `k`), then `serial_comm.close()` (`probes.py:479-487`).
- **Actual behavior** (`:834-847`):
  - It calls `model.disable()` (no `k` kill-coils write; `probes.py:464-465`).
  - It calls `poller.stop_polling()` and `poller.close()`, already done by `widget.cleanup()` at `:451-453`.
  - `hasattr(model,'disconnect')` is false for all three probe classes, so `serial_comm.close()` never runs (port leak; this part is already documented). It also never calls `teardown()`.
  - `del self.system_manager.active_models[device_name]` bypasses `remove_model` and its lock. The `in` check and the `del` are not atomic.
  - Ordering hazard: the dock is popped and closed (`:814-832`) before any teardown step. If `model.disable()` or `disconnect()` raises (serial error), the exception escapes the slot, the model stays in `active_models`, no dock exists, and the checkbox is already unchecked. A later re-check builds a second model over the stale entry (`get_model` returns the old one, so it actually reuses the half-dead model). No user-visible error is routed through `ErrorRouter`.
  - RedPercentSystem has neither `disable` nor `disconnect`. It relies on `widget.cleanup()` having called `stop_monitoring()`.
- **Failure scenario:** The user closes the Stepper dock while coils are energized. `_stop_and_disarm` sends `'d'`, but `'k'` (power_down) is never sent, unlike app exit, which uses `teardown()`. The serial handle is leaked. If `disable()` raises, the dashboard is left in an inconsistent state.
- **Proposed fix direction:** Replace `:834-847` with `m = self.system_manager.remove_model(name); if m: self.system_manager._teardown_model(name, m)` (or add a public `SystemManager.teardown_model(name)`), wrapped in try/except that calls `ErrorRouter.report_error`. Reorder so teardown happens before `dock.close()`. Delete the per-class `hasattr(disable/disconnect/poller)` ladder. If a "close but might reopen" semantic is wanted, add `BaseProbe.disconnect()` and decide explicitly.
- **Confidence:** verified

### PYSIDE-3 — Red Percent keeps a stale, torn-down probe after its dock closes; newly opened probes are not selected or refreshed
- **Severity:** high (silently wrong experiment data)
- **Views affected:** PySide6 (Tk and main do not support probe close or reopen in-session)
- **Reference behavior:** `RedPercentSystem.available_probes` and `stepper_model` should only reference live models. Tk populates its dropdown once at startup (Tk `:676-687`), so no dynamic problem exists there.
- **Actual behavior:**
  - `close_device_view` (`:813-847`) never removes the closed probe from `red_model.available_probes` and never clears `red_model.stepper_model` or `selected_probe_name`.
  - `_monitor_colors` keeps reading `stepper_model.pos_x` (frozen at its last value) and `vel_x`, which resolves to `{}` from a closed poller and yields 0 (`redpercent_system.py:~318-338`, `probes.py:74-100`). The log then records frozen positions with zero velocity, with no warning.
  - `get_available_probe_names()` (`redpercent_system.py:~172-178`) still lists the dead probe, so the user can select it.
  - When a probe is opened after Red Percent (`:913-916`), `available_probes[device_name]` is set, but `set_stepper_model` is not called if none was selected. Neither the schema dropdown nor the hand-added `probe_combo` (built once at `:641-657`) is refreshed, except by the schema dropdown's manual "⟳".
- **Failure scenario:** Red Percent with Sync X on and Stepper Probe linked. The user closes and reopens the Stepper dock (a new model is created). Red Percent still points at the old model, so recorded X positions are frozen at the value from close time while the stage moves.
- **Proposed fix direction:** On probe close, call a model method such as `RedPercentSystem.unlink_probe(name)` (pop from `available_probes`, clear `stepper_model` if it matches). On probe open, call `link_probe(name, model)`, auto-select if none selected, and have the view rebuild or refresh `probe_combo` (use a signal or the poll loop). Move this linking out of the view into a controller or `SystemManager` hook. It is duplicated in `app.py:735-741` and `view.py:903-916`.
- **Confidence:** verified

### PYSIDE-4 — Closing the Red Percent dock (or unchecking it) silently discards unsaved data
- **Severity:** high (data loss)
- **Views affected:** PySide6
- **Reference behavior:** Tk `RedPercentView.stop_monitoring` prompts "Save Log?" when `has_unsaved_data` (Tk `:762-769`). In Tk, closing a tab does not destroy the model (`close_tab` has no callback, Tk `:158-163`).
- **Actual behavior:** `RedPercentDynamicView.cleanup` (`:711-716`) calls `model.stop_monitoring()` with no save prompt. `close_device_view` then drops the model (and its `data_log`) from the manager (`:845-847`). `closeEvent` (`:957-967`) likewise has no prompt. The prompt exists only on the explicit `stop_monitoring` command path (`:674-684`).
- **Failure scenario:** The user runs a 20-minute red-detection acquisition and clicks the dock's X (or unchecks the sidebar item) instead of "Stop Monitoring". The recorded log is destroyed with no confirmation. Same on window close.
- **Proposed fix direction:** In `close_device_view` and `closeEvent`, if `model.has_unsaved_data`, prompt Save / Discard / Cancel before teardown (allow cancel to abort the close). Better still, do not destroy the model on dock close (see PYSIDE-1).
- **Confidence:** verified

### PYSIDE-5 — Numeric entries may not commit before a button/toggle command reads the model (the Tk "previous value" bug, PySide edition)
- **Severity:** high (hardware parameters: step sizes, target distances, rotator target degrees, temperature setpoint and PID)
- **Views affected:** PySide6 (macOS in particular). Tk was fixed in `b42c13e` via `self.focus_set()` at Tk `:492`.
- **Reference behavior:** Tk `_execute_command` first calls `self.focus_set()` so the pending edit commits (Tk `:482-492`).
- **Actual behavior:** PySide commits only on `editingFinished` (`:259`). `_execute_command` (`:377-399`) calls the model command directly without forcing focus or committing. If the clicked `QPushButton` does not take focus, the `QLineEdit` never loses focus, `editingFinished` never fires, and the command reads the stale model value. Qt's macOS style gives push buttons `Qt::TabFocus` (no focus on click), so the same failure mode as the Tk Aqua bug is plausible here.
- **Failure scenario (macOS):** In Rotator, type 45 into "Target (deg):" and click "Move Absolute" without Tab or Enter. `_move_abs_ui` reads `target_deg` (`rotator_system.py:132-137`) as the previous value. Same for Temperature "Enter Settings" (`send_settings`) and probe "Start Stepping" reading `x_dist` and similar.
- **Proposed fix direction:** In `QtDynamicView._execute_command` (and the dropdown handler), first do `fw = QApplication.focusWidget(); if isinstance(fw, QLineEdit): fw.clearFocus()` (or call `fw.editingFinished.emit()`), or commit all tracked entries. Alternatively set `btn.setFocusPolicy(Qt.StrongFocus)` on all schema buttons. Add a regression note next to the Tk one.
- **Confidence:** hypothesis (mechanism is code-verified; whether the macOS button takes focus needs a run on macOS: type a value in the Rotator target field, click "Move Absolute" without Tab/Enter, and print the model value)

### PYSIDE-6 — Non-numeric entries commit only on `editingFinished` (Tk commits per keystroke); Red Percent metadata can be stale at start and save
- **Severity:** medium
- **Views affected:** PySide6
- **Reference behavior:** Tk non-numeric entries (initial value not float-parsable, or `serial_port`) commit live through `str_var.trace_add("write")` (Tk `:362-370`). Tk `RedPercentView` also binds `probe_name` and `probe_tilt_angle` with a trace on every keystroke (Tk `:632-635`).
- **Actual behavior:** In PySide, `probe_name` and `probe_tilt_angle` start as `""`, so `is_numeric` is False (`:235-241`), and they commit only at `editingFinished` (`:256, 259`). `RedPercentDataLog` captures `probe_name` and `probe_tilt_angle` at the first `start_monitoring` (`redpercent_system.py:~148-152, 14-19`). If the last-edited field was not committed (see PYSIDE-5 for the mechanism), the log header is blank. `save_log_ui` (`:688-709`) writes `data_log.save_to_csv` directly and bypasses `RedPercentSystem.save_log()`, which re-syncs those two fields (`redpercent_system.py:~246-253`; already noted in `known-issues.md:88`). So there is no second chance either.
- **Failure scenario:** The user types the probe name, immediately clicks "Start Monitoring" (macOS: focus never left the field), and the saved CSV header says `# Probe Name,` empty.
- **Proposed fix direction:** Fix PYSIDE-5 generically, and have `save_log_ui` call `self.model.save_log(file_path)` after the file dialog. For non-numeric entries, connect `textChanged` (or both) to match Tk's live commit.
- **Confidence:** hypothesis for the commit-timing part on macOS; verified for the `save_log` bypass

### PYSIDE-7 — Red Percent "Position Source" dropdown crashes its handler with `TypeError` when the model changes, and is duplicated
- **Severity:** medium
- **Views affected:** PySide6 only (Tk `RedPercentView` is hand-built and never renders this schema entry)
- **Reference behavior:** Tk has exactly one Position Source combobox (Tk `:664-671`).
- **Actual behavior:**
  - `redpercent_system.py:200` defines a dropdown with `model_attr` and `options_command` but no `command`. In `_build_ui`, `cmd_name = el.get("command")` is None (`:292`). `make_dropdown_cmd(None)` builds a handler whose first line runs `getattr(self.model, c_name, None)` (`:315`) with `c_name=None`, which raises `TypeError: attribute name must be string`. It sits outside the `try` (`:316-320`) and propagates to `sys.excepthook`, which shows an "Unhandled Exception" popup.
  - The handler fires on the user's own selection in the schema combo, and also programmatically: `_poll_model` calls `widget.setCurrentText(current_val)` (`:416-419`) when `selected_probe_name` changes, e.g. after the user picks a probe in the hand-added `probe_combo` (`:641-657`), which triggers `currentTextChanged`.
  - `RedPercentDynamicView` therefore shows two Position Source controls: the schema one (dropdown plus ⟳) and the hand-added one (`:634-657`). The hand-added one is the only one that works.
- **Failure scenario:** The user selects a different probe in "Position Source" and gets an "Unhandled Exception: attribute name must be string, not 'NoneType'" popup. The two dropdowns can also disagree.
- **Proposed fix direction:** In `make_dropdown_cmd`, `if not c_name: return` (no-op when no command) and move `getattr` into the `try`. Block signals in `_poll_model` when calling `setCurrentText` on combos. Remove the duplicate: either delete `_add_position_source_control` and give the schema dropdown `"command": "set_stepper_model"`, or drop the schema entry.
- **Confidence:** verified (static)

### PYSIDE-8 — Red Percent tab: duplicate Save Log, no button gating or focus-area feedback, raw float readouts (layout-intent divergence vs Tk)
- **Severity:** medium (silent failure when monitoring with no focus area) / low (rest)
- **Views affected:** PySide6
- **Reference behavior:** Tk `RedPercentView`:
  - "Start Monitoring" is disabled until a focus area is selected (Tk `:741, 599-603`).
  - "Stop" and "Reset Baseline" are disabled until monitoring starts (Tk `:605, 648, 756-760`).
  - A "Focus Area:" label shows `WxH at (x,y)` (Tk `:614-616, 740`).
  - `Red %` is shown as `{:.1f}%` and `Red Change` as `{:+.1f}%`, green for positive and red for negative (Tk `:782-790`).
  - Controls are top-to-bottom: buttons row, focus area, metadata, red detection.
- **Actual behavior:**
  - Schema "Save Log" (`redpercent_system.py:233`, mapped to `save_log_ui` at `:672-673`) plus a hand-added "Save Log" (`:622-630`) produce two buttons doing the same thing.
  - All buttons are always enabled. `start_monitoring` with `focus_area=None` sets `monitoring=True` and spins the thread forever, since `capture_focus_area` returns None (`redpercent_system.py:~62-63, 290-296`). There is no message, no data, and Stop shows no prompt because `has_unsaved_data` is False.
  - No focus-area label. After a selection only a transient `QMessageBox` is shown (`:496-497`).
  - `current_red` and `red_change` are `str(float)` (`:409`), e.g. `12.345678901234`.
  - The schema `bg`/`fg` hints are ignored (all buttons blue via `style.qss:44-53`), so the green/red Start/Stop semantics are lost. Same for Temperature "Enter Settings" and "Stop System".
- **Failure scenario:** The user clicks "Start Monitoring" without picking a focus area and sees nothing happen, forever.
- **Proposed fix direction:** In `RedPercentDynamicView`, guard `start_monitoring`: if `model.focus_area is None`, `ErrorRouter.report_warning`. Track button enabled states from `model.monitoring` in `_poll_model`. Add a focus-area label refreshed from `model.focus_area`. Format readouts (`:.1f`). Optionally map schema `bg`/`fg` through a dynamic property to QSS. Remove the duplicate Save Log.
- **Confidence:** verified (static)

### PYSIDE-9 — Every view polls `read_position()`/`poll_status()` three times as often as Tk, and unguarded
- **Severity:** medium
- **Views affected:** PySide6
- **Reference behavior:** Tk calls `read_position()` and `poll_status()` once each per 100 ms (Tk `:566-578`); `_poll_model` does not call them (Tk `:512-540`). Main used a single 50 ms `_poll_position` (`stepper_frame.py:425-444`).
- **Actual behavior:** For a model with `poll_status` (rotator), `poll_status()` runs from three sites: `_poll_model` at 50 ms (`:402-405`, no try/except), `status_timer` at 100 ms (`:150-153`, guarded by `_safe_poll_status`), and again in `_poll_model`. `read_position` is called from `_poll_model` (50 ms) and `pos_timer` (100 ms). That is about 30 Hz of `smc.get_position_deg()` plus `smc.get_status()` round trips (`rotator_system.py:267-280`) on the GUI thread, competing with async moves for `_serial_lock` (`smc100.py:393`). The `_safe_*` wrappers swallow all exceptions (`:186-196`), so the same failures are invisible on those paths but raise from `_poll_model`.
- **Failure scenario:** Rotator dock open: GUI stalls and serial traffic is about three times what is intended. A slow, timing-out SMC100 makes the whole dashboard sluggish, since each call blocks on serial I/O in the GUI thread.
- **Proposed fix direction:** Remove the two calls from `_poll_model` (`:401-405`); keep one timer each. Consider moving blocking `poll_status` I/O off the GUI thread (worker thread updating model fields), which `RotatorSystem` already half-supports with `_lock`.
- **Confidence:** verified (call sites); the effect on serial performance is a hypothesis

### PYSIDE-10 — QTimer slots have no exception isolation; a persistent error creates a popup storm
- **Severity:** medium
- **Views affected:** PySide6 (in Tk an exception in an `after` chain silently stops re-arming instead)
- **Reference behavior:** `ErrorRouter` dedupes identical messages for 5 s (`error_routing.py:12-22, 24-40`). Tk `_route_input` breaks its own chain on exception (no re-arm).
- **Actual behavior:** `_poll_model` (`:401-435`) and `_route_input` (`:173-182`) have no try/except and run on repeating `QTimer`s (50 ms and 20 ms). An uncaught exception goes to `custom_excepthook` (`:84-94`), which calls `report_error` directly. That path bypasses `ErrorRouter._is_spam`, so there is no dedupe. `_display_popup` uses a modal `QMessageBox.critical(None, ...)` (`:75`), whose nested event loop keeps firing the timers, which throw again and stack another dialog.
- **Failure scenario:** The serial cable is unplugged mid-session and `send_manual_mode_command` or `read_position` starts raising. Dozens of stacked modal error dialogs appear at up to 50 per second and the app becomes unusable. Same for the PYSIDE-7 `TypeError` after a programmatic combo update.
- **Proposed fix direction:** Wrap the bodies of `_poll_model` and `_route_input` in try/except that routes through `ErrorRouter` (deduped) and optionally stops the timer after N consecutive failures. Add dedupe to `custom_excepthook`, or make `_display_popup` non-reentrant (skip if a dialog is already open).
- **Confidence:** verified (no try/except, no dedupe in the excepthook path)

### PYSIDE-11 — Re-opening "Plot Data" after closing the plot dialog raises `RuntimeError`
- **Severity:** medium (feature breaks after first use)
- **Views affected:** PySide6
- **Reference behavior:** Tk opens a fresh `Toplevel` each time (Tk `:823-829`).
- **Actual behavior:** `PlotDialog` has `WA_DeleteOnClose` (`:519`). In `_execute_command` (`:667-671`), `if hasattr(self,'plot_dialog') and self.plot_dialog: self.plot_dialog.deleteLater()` is executed on a Python wrapper whose C++ object is already deleted after the user closed it. That raises `RuntimeError: Internal C++ object (PlotDialog) already deleted`. `RedPercentDynamicView.cleanup` (`:715-716`) has the same issue on `.close()`, but `close_device_view` swallows it (`:827-830`).
- **Failure scenario:** The user opens Plot Data, closes the dialog, clicks Plot Data again, and gets an Unhandled Exception popup and no dialog.
- **Proposed fix direction:** Connect `plot_dialog.destroyed` to reset `self.plot_dialog=None` (as done for `log_window`, `:382`), or drop the `deleteLater` (parented dialog with `WA_DeleteOnClose` needs no manual delete) and use `try/except RuntimeError`.
- **Confidence:** verified (static; standard PySide behavior for a deleted C++ object)

### PYSIDE-12 — SelectionOverlay: confirmation `QMessageBox` shown while the always-on-top overlay is still open; no instruction label; direct attribute write
- **Severity:** medium (hypothesis)
- **Views affected:** PySide6
- **Reference behavior:** Tk destroys the overlay first (`selection_window.destroy()`, Tk `:739`), then updates the label. The overlay has an on-screen instruction "Click and drag ... ESC to cancel" (Tk `:752-753`) and a crosshair cursor (Tk `:712`).
- **Actual behavior:** `mouseReleaseEvent` (`:487-498`) sets `self.model.focus_area` and calls `QMessageBox.information(None, ...)` (`:496-497`) before `self.close()` (`:498`). The overlay is `FramelessWindowHint | WindowStaysOnTop | Tool` (`:460`), covers all monitors (`:465-468`), and is parentless, so the parentless message box may appear beneath it. Application-modal input is then blocked with the dialog invisible. Escape handling (`:500-503`) needs keyboard focus, which `Qt.Tool` frameless windows often don't get, and no `setFocus`/`activateWindow` is called. It also uses no instruction text and no cursor. It writes `focus_area` directly to the model (also done in Tk `:738`), although `RedPercentSystem.set_focus_area(x,y,w,h)` exists (`redpercent_system.py:~57-60`).
- **Failure scenario:** After dragging a rectangle the screen stays dark with the confirmation dialog hidden behind the overlay. The user sees the app hang until they press Enter or find the dialog.
- **Proposed fix direction:** Close the overlay first and show the confirmation via `QTimer.singleShot(0, ...)` parented to the view (or write the result into a Focus Area label, see PYSIDE-8). Call `setFocus()`, `activateWindow()`, `setCursor(Qt.CrossCursor)` in `showEvent`, and add an instruction `QLabel`. Use `model.set_focus_area(...)`. See also the Linux transparency issue in `known-issues.md:64-72`.
- **Confidence:** hypothesis (needs a run on macOS and Linux to confirm the dialog stacking)

### PYSIDE-13 — Dead "reopen" branch in `open_device_view` would block the GUI on a 1 s serial reconnect and desync state
- **Severity:** low
- **Views affected:** PySide6
- **Reference behavior:** `probes.py:~150-156` comment says serial reconnect was deliberately removed from the live dashboard.
- **Actual behavior:** `open_device_view` (`:859-879`) has an early-return branch for a name already in `active_docks`. It shows a `QMessageBox` with no buttons, calls `QApplication.processEvents()`, then `model.reconnect_serial()` (`probes.py:161-175`: closes the port, `time.sleep(1)`, reopens, resets `system_enabled`, `auton_flag`, `manual_flag` with no stop). It is effectively unreachable: `itemChanged` fires only on a state change, and a dock leaves `active_docks` (via `close_device_view`) before the checkbox can be re-checked. If it were reachable it would freeze the GUI for at least 1 s and desync the firmware enable state.
- **Failure scenario:** None today; latent trap if a "show existing dock" trigger is added.
- **Proposed fix direction:** Delete the `reconnect_serial` block; keep only `dock.show()/raise_()`.
- **Confidence:** verified (static reachability analysis)

### PYSIDE-14 — Focus-loss flush: iterates the live dict, and is effectively a no-op
- **Severity:** medium (safety intent; the second part also applies to Tk)
- **Views affected:** PySide6 and Tk (the flush itself)
- **Reference behavior:** Tk binds `<FocusOut>` and flushes a snapshot taken at init (Tk `:172-182`).
- **Actual behavior:** `changeEvent` (`:776-781`) iterates `system_manager.active_models.items()` directly, without the lock or `get_active_models_snapshot()` (`system_manager.py:29-32`). All mutation today is on the GUI thread, so this is low risk. Separately, `flush_neutral` only resets `prev_axis_states` and latch state (`gamepad.py:557-570`). `_poll_loop` rewrites `prev_axis_states[i] = current_val` from the live joystick every 5 ms (`gamepad.py:~578-600`), and `get_mapped_state` reads those (`gamepad.py:119-130`). A held stick therefore reasserts within one poll, so the flush does not stop manual-mode motion when the window loses focus. `WindowDeactivate` also fires for any modal popup or floating dock activation.
- **Failure scenario:** The user is jogging with the stick in manual mode, clicks another app, and continues to hold the stick. The stage keeps moving.
- **Proposed fix direction:** Use `get_active_models_snapshot()`. Decide the intended semantics: if focus loss should stop motion, call `model.send_manual_mode_command({})` or `full_stop()` on deactivate (after filtering child-dialog deactivations), rather than `flush_neutral`.
- **Confidence:** verified (code paths); intended semantics is a design question

### PYSIDE-15 — Error popups are parentless and not themed; excepthook path shown as "Unhandled Exception" with no thread hook
- **Severity:** low
- **Views affected:** PySide6
- **Reference behavior:** Tk passes `parent=cls._root` (Tk `:58-62`).
- **Actual behavior:** `QtErrorPopupManager._display_popup` uses `QMessageBox.critical/warning/information(None, ...)` (`:75-79`). The stylesheet is applied on `DashboardWindow` only (`:738-743`), so these popups are unstyled and not modal relative to the dashboard. `sys.excepthook` does not cover non-main-thread exceptions (no `threading.excepthook`); errors in model threads (e.g. `_monitor_colors`, `read_serial_data`) only print.
- **Failure scenario:** Errors appear as light native dialogs that can hide behind the dashboard. Exceptions in background threads produce no popup.
- **Proposed fix direction:** Use the active window as parent (`QApplication.activeWindow()`), set the stylesheet on `QApplication` instead of the window, and install `threading.excepthook`.
- **Confidence:** verified (parent and stylesheet); thread hook gap verified by grep (`setup_excepthook` only sets `sys.excepthook`)

### PYSIDE-16 — Layout-intent divergences from Tk
- **Severity:** low
- **Views affected:** PySide6
- **Reference behavior:** Tk puts FULL STOP at the bottom, deliberately, "Bottom-docked, not top: ... easy accidental-click target when reaching for a tab" (Tk `:184-191`). It shows one device per tab; the schema grid is centered (Tk `:285-295`).
- **Actual behavior:**
  - FULL STOP is the first widget in the sidebar, above the device list (`:756-759`), directly adjacent to the checkboxes that destroy models. It also has no confirm, hover or pressed styling (inline stylesheet at `:757`).
  - `setCentralWidget(QWidget())` (`:770`) is an empty widget that takes a stretch share, so docks (all split horizontally into one row, `:935-938`) are squeezed. With six devices each dock gets about (1200-250)/6 px.
  - `_last_added_dock` is reset to None on close (`:816-817`), so the next dock goes through `addDockWidget(Right)` and stacks in a different arrangement than the horizontal splits.
  - Readonly rows are unstretched and cramped (no column alignment, `:214-229`).
- **Failure scenario:** Accidental FULL STOP near the checkboxes; unreadable, squashed docks with many devices open.
- **Proposed fix direction:** Move FULL STOP to the bottom of the sidebar (or a window-level toolbar); hide the central widget or use tabified docks by default. Not blocking.
- **Confidence:** verified (static); the visual effects are unrun

### PYSIDE-17 — Dead code inventory (view.py and style.qss)
- **Severity:** low
- **Views affected:** PySide6
- **Actual behavior:**
  - `file_picker` branch: unconditional `continue` at `:346`, so `:347-371` is unreachable (already documented).
  - `QSS QPushButton#filePicker` and `QLabel#fileLabel` (`style.qss:35-38, 92-99`) are used only by the dead branch. `QCheckBox` rules (`style.qss:158-167`) and the `QCheckBox` import (`:9`) are orphaned since the sync checkboxes were removed (#5).
  - `disable_timer` cleanup (`:447-448`) refers to a timer never created.
  - `self.log_window = None` set twice (`:133, 135`).
  - Unused imports: `csv`, `Figure`, `QCheckBox` (`:3, 15, 9`).
  - `self.layout = QVBoxLayout(...)` (`:130, 522`) shadows `QWidget.layout()`. It works but is fragile.
  - `_add_custom_buttons`/`_save_log` (`:622-633`) duplicate the schema button (PYSIDE-8).
  - `"type": "internal"` schema elements fall through the `elif` chain but still get an empty `addRow(row_layout)` at `:373`, adding blank spacing.
- **Failure scenario:** None functional, only confusion and drift.
- **Proposed fix direction:** Delete the dead branches and imports, rename `self.layout` to `self._layout`, and skip `addRow` for unknown types.
- **Confidence:** verified

### PYSIDE-18 — PlotDialog / save-file details differ from Tk
- **Severity:** low
- **Views affected:** PySide6
- **Reference behavior:** Tk save uses `defaultextension=".csv"` (Tk `:778`). Tk `open_plot_window` checks `not parsed["red_percents"]` (Tk `:812`), opens with `newline=''` (Tk `:806`), and sets the plot title from the CSV metadata (Tk `:817-821`).
- **Actual behavior:**
  - `save_log_ui` (`:698-700`) uses `QFileDialog.getSaveFileName` with a filter but no default suffix. On Linux, a bare filename saves with no `.csv` extension.
  - `PlotDialog.load_csv` (`:536-549`) rejects a file only when both `dims` and `red_percents` are empty (`:546`). A CSV with a header but zero data rows and a dims column passes and draws an empty plot. A bad file yields the misleading "missing 'Red Percent' column" message.
  - It opens without `newline=''` (`:541`) and ignores `parsed["metadata"]`, so probe name and tilt do not appear in the title.
  - The `select_plot_type` dialog (`:551-595`) returns nothing if closed with the window X, which is fine. This is a superset of Tk's 0D-only plot, so it is a PySide advantage, not a regression.
- **Failure scenario:** The user saves `run1` on Linux and gets a file with no extension.
- **Proposed fix direction:** Append `.csv` when missing. Use `if not parsed["red_percents"]` for the warning. Use `newline=''` and set the title from metadata.
- **Confidence:** verified (static)

### PYSIDE-19 — `QDoubleValidator(-1e9, 1e9, 3)`: 3-decimal cap and locale sensitivity vs Tk's free float validation
- **Severity:** low
- **Views affected:** PySide6
- **Reference behavior:** Tk accepts any float string (Tk `:297-304, 343-358`).
- **Actual behavior:** `:244` blocks input with more than 3 decimals (e.g. PID `0.0005`, a 4-decimal rotator step) and uses the system locale (a comma-decimal locale rejects `.`, and `float("0,5")` would fail in `commit` at `:251-254`). An empty or intermediate value never triggers `editingFinished` (Qt suppresses it for non-Acceptable input), so the commit is skipped. The poll loop then reverts the text once the field loses focus (`:410-412`), which is benign but silent.
- **Failure scenario:** Typing `0.0005` into the Temperature Integral Term is impossible. On a non-US locale the user cannot type a decimal point.
- **Proposed fix direction:** `validator.setLocale(QLocale.c())`, raise decimals (e.g. 6) or use `QRegularExpressionValidator`. Consider showing a red border on invalid input.
- **Confidence:** hypothesis (Qt validator/locale behavior; check by typing `0.0005`)

### PYSIDE-20 — Dead or no-op widgets shared with Tk; controller list no longer auto-refreshes vs main
- **Severity:** low
- **Views affected:** PySide6, Tk
- **Reference behavior:** Main refreshed the controller dropdown every 500 ms (`stepper_frame.py:364-401`) and its "Serial Port" entry fed `serialDrive.SerialArduino(port=...)` on reconnect (`stepper_frame.py:662-666`).
- **Actual behavior:** The "Serial Port" entry (`probes.py:107`) commits to `model.serial_port`, but nothing reads it after construction (reconnect removed, `probes.py:150-156`), so editing it does nothing. The controller dropdown is refreshed only by the manual "⟳" (`:339-343`; Tk `:451-453`). The `⟳` handler with a missing `options_command` would clear all options (`:330`), but no current schema does that.
- **Failure scenario:** The user types a new port and nothing happens. A controller plugged in after launch requires a manual ⟳ click.
- **Proposed fix direction:** Make the Serial Port a readonly label (or wire it to an explicit "Reconnect" via the controller layer). Optionally add a slow QTimer refresh of dropdown options.
- **Confidence:** verified

## Coverage

- **Read in full:** `src/views/pyside/view.py` (1-967), `src/views/pyside/style.qss` (1-167), `src/views/tkinter/view.py` (1-835).
- **Read for cross-checks:**
  - `src/model/system_manager.py` and `src/model/base.py` in full.
  - `src/app.py:380-435, 700-770`.
  - `src/app_bootstrap.py` (`build_models`, `validate_assignment`).
  - `src/model/probes.py:14-260, 355-560`.
  - `src/model/redpercent_system.py:14-400` (schema, save_log, monitoring).
  - `src/model/rotator_system.py:8-300`.
  - `src/model/temperature_system.py:6-215`.
  - `src/controller/gamepad.py:55-140, 240-330, 470-660`.
  - `src/controller/serial.py:113-143, 259-266`.
  - `src/model/plot_data.py:1-80`.
  - `src/error_routing.py:1-60`.
  - `git show main:src/mainGUI.py` (launch and close, lines 295-381) and grep of `main:src/stepper_frame.py` for polling and loop intervals.
  - `docs/architecture/views.md` (full), `known-issues.md` (full), `ownership-and-lifecycle.md` lines 1-130.
- **Not done:**
  - No runtime execution. macOS button focus behavior (PYSIDE-5), overlay z-order (PYSIDE-12) and validator locale (PYSIDE-19) are unverified hypotheses.
  - I did not audit Web views or `main:src/chuck_frame.py` and `temp_control.py` in depth.
  - `SetupWindow` in `app.py` was only skimmed at the launch section.
  - I did not verify QSS inheritance (font propagation, `QListWidget` check indicators on the dark theme).
  - I did not check `controllers.md`, `models.md` and `error-routing.md` claims beyond what overlaps.

DONE view-pyside 20
