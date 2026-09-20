# Audit: RedPercentSystem (Red Percent Window)

## Object summary

- Model: `RedPercentSystem` (src/model/redpercent_system.py:56) plus helper `RedPercentDataLog` (:14). Screen-region red-pixel monitor; NumPy/PIL/mss only (no OpenCV import, :1-10; docs/architecture/models.md line calling detect_red "OpenCV/NumPy" is wrong on the OpenCV part).
- Constructor sites (all `RedPercentSystem()` with no args):
  - Tkinter/PySide startup: src/app_bootstrap.py:162-164 (`build_models`).
  - Web: same `build_models` path via `WebModelAdapter.initialize_setup` (probes wired at src/views/web/web_adapter.py:166-174).
  - PySide dynamic reopen: src/views/pyside/view.py:899-901 (fresh model when checkbox re-ticked).
- Probe wiring (`available_probes` + `set_stepper_model`) is copy-pasted in FOUR places: src/app.py:404-412 (Tk), src/app.py:733-741 (PySide), src/views/web/web_adapter.py:166-174 (Web), src/views/pyside/view.py:902-908 and :913-917 (dynamic open). Predicate is `hasattr(model,'pos_x')`.
- Owner: `SystemManager.active_models["Red Percent Window"]`. Teardown path: `SystemManager.shutdown_all` (src/model/system_manager.py:59-68) -> `RedPercentSystem.teardown` (:276) -> `stop_monitoring` (:272) which only flips `self.monitoring=False`. No join, no data_log handling. `emergency_stop` (:279) is the same call.
- Views: Tk `RedPercentView` (src/views/tkinter/view.py:590, hand-written, not schema-driven); PySide `RedPercentDynamicView(QtDynamicView)` (src/views/pyside/view.py:615, schema-driven plus bolt-ons); Web schema-driven cards in app.js `buildElementHtml` (:573) plus a separate hard-wired "plotter" modal.
- Docs corrections: `_disabled_in_setup` is NOT dead. Set at web_adapter.py:161, read at web_adapter.py:223 and redpercent_system.py:184. But it is only ever set by the Web adapter (grep across src confirms), so the filter is inert for Tk/PySide. `is_monitoring`, `stop_event`, `thread`, `monitor_thread`, `baseline`, `red_percent` (:61-72) have no readers anywhere in src (grep verified); docs are right about that.

---

## Findings

### REDPERCENT-1
- Title: `RedPercentDataLog` shares/decouples `sync_dimensions` with the model; toggling a sync dim mid-run or between runs kills the monitor thread and corrupts the log so CSV save fails.
- Severity: high (data loss + silent dead thread)
- Views affected: Model (all three views trigger it via Sync toggles)
- Reference behavior: main had no per-dimension logging (main:src/color_test_new.py:46,229-246 only appended text lines); Tk view intent (view.py:650-660) lets the user tick Sync X/Y/Z at any time.
- Actual behavior: `RedPercentDataLog.__init__` does `self.sync_dimensions = sync_dimensions or []` (:16) and builds `loc_values`/`vel_values` keyed by those dims once (:20-21). `start_monitoring` passes the model's live list (`RedPercentDataLog(self.sync_dimensions, ...)`, :267) only when `self.data_log` is None. Consequences:
  - (a) Non-empty list: the log aliases the model's list. Toggling a NEW dim (e.g. Y) later appends 'Y' to the shared list; `add_entry` (:24-31) appends `red_values` first (:26), then `self.loc_values['Y']` raises KeyError (:30). Uncaught in the monitor thread (`_monitor_colors`, :326 call) -> thread dies, but `self.monitoring` stays True, so `start_monitoring` (:262) early-returns and the UI shows "monitoring" with no updates. `red_values` is now longer than `loc_values['X']`'s tail-consistency, and 'Y' key is missing, so `save_to_csv` (:49-54) raises KeyError/IndexError -> whole log unsavable (caught by save_log :253-257 and surfaced as "File Save Error"; data remains unsaved).
  - (b) Empty list at creation: `[] or []` makes a private list; later ticking X mid-run or in a later session never adds position columns, so the CSV silently has no position data although the UI shows Sync X ON.
  - (c) Un-ticking a dim: shared list loses it, CSV headers drop the column, previously recorded data for it silently dropped.
- Failure scenario: Tick Sync X, Start, tick Sync Y while running (or Stop, tick Y, Start again - `data_log` persists, see REDPERCENT-2). Values freeze, Stop -> Save Log errors, all samples lost.
- Proposed fix direction: Snapshot at log creation (`list(sync_dimensions)`), and have `add_entry` use `self.loc_values.get(dim)`/its own frozen dim list only. Better: create a fresh `RedPercentDataLog` in every `start_monitoring` (or lock the Sync toggles while `self.monitoring`). Wrap the monitor loop body in try/except that reports via ErrorRouter and resets `self.monitoring=False` on unexpected exceptions.
- Confidence: verified (code read :14-31, :49-54, :262-270, :312-326)

### REDPERCENT-2
- Title: `data_log` is never reset between monitoring sessions; `has_unsaved_data` is sticky and sessions concatenate.
- Severity: medium
- Views affected: Model / Tkinter / PySide / Web
- Reference behavior: main appended to one `log_data` list for the app lifetime (main:src/color_test_new.py:46,231,244) and had no "unsaved" prompt at all; Tk intent (view.py:~760-767 stop_monitoring) is "prompt to save on stop".
- Actual behavior: `start_monitoring` only creates `data_log` if falsy (:266-267); nothing ever clears it or clears it after a save. `has_unsaved_data` (:187-189) stays True forever once one sample exists. Also `last_logged_red` (created via `hasattr` at :304-305) is never reset per session, so the first sample of a new session is dedup-skipped if equal to the last of the previous session. Probe name/tilt in the log are only copied at creation (:267) or in `save_log` (:246-247).
- Failure scenario: Run A, save. Stop, change probe, run B. Save prompt appears on every Stop, and file B contains run A's samples plus B's, metadata mixes.
- Proposed fix direction: Create a new `RedPercentDataLog` on each `start_monitoring` (or after successful save) and reset `last_logged_red = -1000.0` in `__init__`/`start_monitoring`; set `has_unsaved_data` False after successful `save_to_csv`; if data exists and user starts again, ask whether to discard/append.
- Confidence: verified

### REDPERCENT-3
- Title: Stop -> Start race can leave two monitor threads running; no join on stop/teardown.
- Severity: medium
- Views affected: Model (reachable via Web double-dispatch; hard in Tk/PySide because Stop shows a modal)
- Reference behavior: main (color_test_new.py:295-314) had the same flag-only stop, but its Start button was disabled until Stop had re-enabled it, and Stop had no modal-free re-entry path; same latent race there.
- Actual behavior: `stop_monitoring` (:272-274) sets `monitoring=False` and returns. Old thread only exits when it next evaluates `while self.monitoring` (:290) after `sleep(0.016)` (:328) and a `grab`. `start_monitoring` (:261-270) checks the flag, sets True, and starts a new thread in `self._monitor_thread` (overwriting the handle). If Start lands before the old thread wakes, the old thread continues (flag True again). Both write `current_red`, `baseline_red`, `last_logged_red` and append to the same `data_log`. `teardown` (:276) also never joins, so `mss.mss()` context exit and `data_log` use can overlap with app shutdown (daemon thread, :269).
- Failure scenario: Web client posts `stop_monitoring` then `start_monitoring` back-to-back (script or double click on the modal Start/Stop, app.js:261-270): duplicate samples and double CPU/screen-capture load until the next Stop (which only ends both after both see False).
- Proposed fix direction: Keep a per-run `threading.Event` (the unused `stop_event` at :63 was clearly meant for this) and a generation token; in `stop_monitoring` set it and `join(timeout=1)` off the GUI thread (or have `start_monitoring` refuse while the old thread `is_alive()`); `teardown` should join.
- Confidence: verified (logic); reproduction timing UNVERIFIED (needs a live run)

### REDPERCENT-4
- Title: Monitor thread has no exception handling; any failure leaves `monitoring=True` with a dead thread; `mss=None` path crashes.
- Severity: medium
- Views affected: Model (all views)
- Reference behavior: main wrapped only the grab in try/except (main:src/color_test_new.py:188-194); mss/PIL/numpy were hard imports there.
- Actual behavior: imports are optional (:3-10) but `_monitor_colors` does `with mss.mss() as sct` (:289) unguarded; if imports failed `mss` is None -> AttributeError in the thread. Also `float(self.stepper_model.pos_x)` is wrapped by bare `except:` (:314-323) but `getattr(self.stepper_model,'vel_x',0.0)` (:316,320,324) calls a property (probes.py:70-75 `vel_x` calls `poller.get_mapped_state()`) that is not wrapped; a poller exception kills the thread. `capture_focus_area` (:97-109) returns None on errors, and the loop then spins at 60 Hz doing nothing while reporting to ErrorRouter (spam-limited to once per 5 s by error_routing.py:18-28). Also `start_monitoring` does not require `focus_area` (see REDPERCENT-9).
- Failure scenario: Uninstalled mss or poller error: user presses Start, buttons flip to "monitoring", nothing happens; Start again is ignored (:262-263).
- Proposed fix direction: Check dependencies in `start_monitoring` (raise/report) and wrap the loop body in try/except that reports once and sets `self.monitoring=False`; wrap vel reads.
- Confidence: verified

### REDPERCENT-5
- Title: Unguarded shared state between monitor thread and UI threads (current_red/red_change/baseline_red/last_logged_red/focus_area/sync_dimensions).
- Severity: low
- Views affected: Model
- Reference behavior: main pushed values to Tk via `root.after` from the thread (main:251) and had the same unguarded floats.
- Actual behavior: No lock protects: `current_red` and `red_change` written as two separate statements (:299-300) so pollers (Tk view.py:786-787, PySide 50 ms `_poll_model`, Web `get_state` web_adapter.py:301-307) can read a mismatched pair; `red_change` reads `self.baseline_red` twice (:300) so a concurrent `reset_baseline` (:282-284) can yield a mixed value; `last_logged_red` is created lazily via `hasattr` inside the thread (:304-305) instead of `__init__`; `sync_dimensions` list is mutated by UI toggles (:131-155) while the thread iterates/`in`-tests it (:313-321, `add_entry`:29). `set_focus_area`/`focus_area` swapped atomically so that one is fine. `reset_baseline` copies `current_red` which is 0.0 before the first sample. Each is benign individually under CPython's GIL except the list mutation (REDPERCENT-1).
- Failure scenario: One-frame glitch in Red Change display right after Reset Baseline.
- Proposed fix direction: Snapshot `red_pct`/`baseline` in local variables before computing; put `last_logged_red`, `_lock` in `__init__`; guard `sync_dimensions` with the lock or snapshot at run start.
- Confidence: verified

### REDPERCENT-6
- Title: PySide "Save Log" bypasses `RedPercentSystem.save_log`, so late probe-name/tilt edits never reach the CSV metadata.
- Severity: medium
- Views affected: PySide (Web and Tk go through `save_log`)
- Reference behavior: Tk `save_log_to_file` calls `self.system.save_log(file_path)` (view.py:780); model re-syncs metadata at redpercent_system.py:245-247 ("Catch late UI edits before saving"). Tk also updates the model on every keystroke (trace_add, view.py:632-635).
- Actual behavior: `RedPercentDynamicView.save_log_ui` (pyside/view.py:688-709) calls `self.model.data_log.save_to_csv(file_path)` directly at :703, skipping :246-247, so `data_log.probe_name`/`probe_tilt_angle` retain whatever they were when `data_log` was created (first Start, :267). Additionally, entry commit in `QtDynamicView._build_ui` only fires on `editingFinished` (:259), so the model attr itself can also lag the widget (on macOS QPushButton clicks typically do not take focus, so the QLineEdit may not have emitted `editingFinished` yet before Save/Stop - UNVERIFIED HYPOTHESIS; check by typing a name and clicking Save Log without tabbing out). It also duplicates the model's `save_log` try/except/ErrorRouter logic (:705-709) and default-name logic that the model doesn't have.
- Failure scenario: User starts monitoring, then types "Probe-7" / "35" in Probe Name/Tilt during the run, presses Stop -> Yes -> Save: CSV header rows (`# Probe Name`, `# Probe Tilt Angle`, redpercent_system.py:39-40) are empty or stale.
- Proposed fix direction: Change :703 to `self.model.save_log(file_path)` (single save path); commit QLineEdit on `textChanged` (matching Tk) or `clearFocus()`/flush pending edits in `save_log_ui` before saving.
- Confidence: verified (bypass); hypothesis for macOS focus subcase

### REDPERCENT-7
- Title: Web "Save Log" writes a CSV to the server process CWD, returns success even when nothing was saved, and never reaches the user.
- Severity: medium
- Views affected: Web (+ Model `save_log_web`)
- Reference behavior: Tk uses a save dialog (`filedialog.asksaveasfilename`, view.py:777-780); PySide uses `QFileDialog` with a probe-named default (pyside/view.py:698-700); main used `asksaveasfilename` too (main:src/color_test_new.py:263-267).
- Actual behavior: Schema button "Save Log" -> `dispatch_command(... 'save_log_web')` (web_adapter.py:379-418) -> `save_log_web` (redpercent_system.py:157-158) -> `save_log(f"redpercent_log_{strftime}.csv")` relative path. File lands in the server's current working directory (`open(filepath,'w')` :35), not a browser download; the log handler returns `{"status":"ok","result":"None"}` and the JS toast says "save_log_web executed" (app.js:1154) even when `save_log` printed "No data to save." (:241-243) or failed (the failure is only in the error buffer). Filename ignores probe name (unlike PySide) and has 1-second granularity (:158) so two saves in one second silently overwrite. There is no unsaved-data prompt on Stop either (app.js:266-270 just dispatches).
- Failure scenario: Remote/other-machine browser user clicks Save Log: nothing downloadable; on the host a file appears in whatever directory the server was launched from.
- Proposed fix direction: Add a GET/POST `/api/redpercent/log.csv` that streams `data_log` content (add `RedPercentDataLog.to_csv_text()`), have JS trigger a Blob download like `exportLogsToFile` (app.js:1058-1068); make `save_log_web` return a status string or raise on no-data so the toast is truthful.
- Confidence: verified

### REDPERCENT-8
- Title: Web probe-source dropdown sets the attribute only; `set_stepper_model` is never called, so the selection has no effect.
- Severity: high (wrong stage positions logged; silent)
- Views affected: Web (Model schema contract)
- Reference behavior: Tk `_on_probe_selected` -> `system.set_stepper_model(selected)` (view.py:~692-695, listed :686-690); PySide custom combo `_on_probe_selected` -> `set_stepper_model` (pyside/view.py:659-661).
- Actual behavior: The schema dropdown (redpercent_system.py:200) declares `model_attr: "selected_probe_name"` and `options_command` but NO `command`. JS change handler (app.js:716-727): `if (cmd) dispatchCommand(...) else if (attr) setDeviceAttribute(...)` -> `POST /api/set_attr` -> `setattr(model,'selected_probe_name',val)` (web_adapter.py:463). `set_stepper_model` (:91-95) is what actually assigns `self.stepper_model`, so the model's `selected_probe_name` diverges from the probe actually sampled. The placeholder option "Select option..." (app.js:642) also lets the user write `""` into `selected_probe_name`.
- Failure scenario: Two probes present (e.g. "Chuck Positioner" and "Stepper Probe"); user picks "Chuck Positioner" in the web UI; UI shows it selected, but CSV position columns still come from the original `stepper_model`.
- Proposed fix direction: Add `"command": "set_stepper_model"` to the dropdown element (PySide's `make_dropdown_cmd` and the Web JS both call `command(text)` when present) and guard against `""`; or make `selected_probe_name` a property whose setter calls `set_stepper_model`.
- Confidence: verified

### REDPERCENT-9
- Title: Start Monitoring is allowed without a focus area (all three views); Tk regressed main's disabled-until-selected Start.
- Severity: medium
- Views affected: Tkinter / PySide / Web / Model
- Reference behavior: main created Start disabled (main:src/color_test_new.py:63-65) and enabled it only after a valid region was selected (:164).
- Actual behavior: Tk `start_btn` is created enabled (tkinter/view.py:602); line 741 that enables it is now redundant. PySide/Web start buttons come from the schema and are always enabled. Model `start_monitoring` (:261-270) has no guard; `capture_focus_area` returns None while `focus_area` is None (:98-99), so the thread spins silently, `current_red` stays 0.0, `data_log` empty. The overlay/ROI selection in PySide (pyside/view.py:494) and Tk (view.py:738) assign `model.focus_area` directly rather than via `set_focus_area` (MVC strain, low).
- Failure scenario: Press Start before selecting an area; UI toggles to "running" but nothing is logged; a subsequent Save shows "No Data".
- Proposed fix direction: Guard in the model (`if not self.focus_area: raise/report and return`), and in Tk restore `state=tk.DISABLED` at :602; views should call `set_focus_area(x,y,w,h)`.
- Confidence: verified

### REDPERCENT-10
- Title: PySide has TWO Position Source controls and TWO Save Log buttons; the schema dropdown has no `command` so its handler throws a TypeError.
- Severity: medium
- Views affected: PySide
- Reference behavior: Tk has a single Position Source combobox (view.py:~663-673) and single Save (dialog on Stop only / no separate button beyond Stop; Save is via stop prompt). Schema authors intend one dropdown per attribute.
- Actual behavior:
  - `RedPercentDynamicView.__init__` (pyside/view.py:616-619) builds the schema UI (which already has the "Position Source:" dropdown and "Save Log" button, redpercent_system.py:200,233) and then adds a hand-rolled `probe_combo` (:634-657) and a second "Save Log" button (:622-630). Result: duplicate controls.
  - Generic dropdown builder: `make_dropdown_cmd(cmd_name)` handler does `getattr(self.model, c_name, None)` at pyside/view.py:315 with `c_name=None` (element has no "command", :292) -> `TypeError: attribute name must be string`, outside the try (:317). It fires when the user changes the schema combo AND when `_poll_model` calls `widget.setCurrentText` (:419) because `currentTextChanged` is not blocked - e.g. after the user picks a probe in the custom combo, the next 50 ms poll updates the schema combo and raises. `sys.excepthook` is routed to a popup (app.py:759, QtErrorPopupManager.setup_excepthook), so this can surface as repeated "Unhandled Exception" popups (popup rate-limited by error_routing.py:18-28) - popup behavior UNVERIFIED HYPOTHESIS (needs a run).
  - Initial state: `selected_probe_name` is `None`, so `current_val = "None"` (:298) is prepended as a bogus option (:300-301).
  - Custom `probe_combo` never refreshes from the model or from later-added probes (built once, :641-654).
- Failure scenario: In PySide, user opens Red Percent, changes Position Source in the custom combo; the schema combo later mismatches/raises.
- Proposed fix direction: Remove `_add_position_source_control` and `_add_custom_buttons` (schema already covers them) and add `"command":"set_stepper_model"` to the schema dropdown (see -8); make `make_dropdown_cmd` skip when `c_name` falsy and use `blockSignals` around `_poll_model` updates.
- Confidence: verified (code); popup consequence hypothesis

### REDPERCENT-11
- Title: PySide dynamic open/close of probes does not keep `available_probes`/`stepper_model` consistent; closing the Red dock destroys the unsaved log.
- Severity: medium
- Views affected: PySide (with `close_device_view`/`open_device_view` hand-rolling SystemManager)
- Reference behavior: Tk and Web build all models before showing the UI (app.py:404-412; web_adapter.py:166-174); ownership via `SystemManager` (`remove_model` + `_teardown_model`, system_manager.py:18-27).
- Actual behavior:
  - `close_device_view` (pyside/view.py:813-847) hand-rolls teardown (`disable`, `poller.stop_polling/close`, `disconnect`, then `del active_models[...]` at :845-846) instead of `SystemManager.remove_model`+`teardown`; it never removes the closed probe from `red_model.available_probes` nor clears `red_model.stepper_model`. Red keeps logging positions from a torn-down probe (frozen strings, `pos_x` at probes.py:28) and still lists it in the dropdown (unless `_disabled_in_setup`, never set on PySide).
  - Opening a probe AFTER Red (:913-917) adds it to `available_probes` but does not call `set_stepper_model` when `stepper_model` is None, and neither combo refreshes (:641-654 built once), so `stepper_model` stays None and `add_entry` writes 0.0 for all sync dims (:310-326 skip when `not self.stepper_model`; `add_entry` defaults at :31-32).
  - Closing the Red dock (or unchecking, :813) runs `cleanup()` (:711-716 -> `stop_monitoring`) then pops the model; `data_log` is lost with no save prompt (the prompt exists only on the explicit Stop command, :674-684). Re-ticking the box constructs a fresh `RedPercentSystem` (:899-901), so probe name/tilt/focus area are lost too.
- Failure scenario: Close Stepper Probe dock while Red is set to it: subsequent Red logs carry a frozen position. Or run a long measurement and close the Red dock: entire log is gone.
- Proposed fix direction: Route through `SystemManager.remove_model` + `_teardown_model`; add `RedPercentSystem.forget_probe(name)`/`refresh_probes(models)` and call it on every open/close; prompt to save if `has_unsaved_data` on close_device_view/closeEvent (:957-967) and on Tk `on_close` (view.py:226-231). Note: this "close loses data" gap is identical in Tk `on_close` (shutdown_all -> `stop_monitoring` only) and Web re-`initialize_setup` (`old_manager.shutdown_all`, web_adapter.py:186-188).
- Confidence: verified

### REDPERCENT-12
- Title: Tk tab-close (right/middle-click) leaves Red monitoring running headless.
- Severity: medium
- Views affected: Tkinter
- Reference behavior: `RedPercentView.destroy` (view.py:831-834) stops monitoring; main's `cleanup` set `monitoring=False` (main:332-350).
- Actual behavior: `DraggableClosableNotebook.close_tab` (view.py:158-162) calls `self.forget(index)` because `on_close_tab_callback` is never assigned anywhere (grep: only view.py:120,159,160). `forget` does not destroy the frame, so `RedPercentView.destroy` is never called, monitoring continues (thread + 60 Hz screen grabs + log growth) with no way to stop it except FULL STOP or closing the dashboard (`shutdown_all` -> teardown), and the poll loop (`poll_display`, :782-790) keeps running on a hidden frame. The model is not removed from `SystemManager` (`tab_metadata`, view.py:222).
- Failure scenario: User closes the Red tab mid-run; screen capture continues; reopening is impossible (no way to re-add the tab).
- Proposed fix direction: Wire `on_close_tab_callback` in `DashboardWindow` to tear down the view/model via `SystemManager.remove_model` + `teardown` (and prompt for unsaved data).
- Confidence: verified

### REDPERCENT-13
- Title: Web live "plotter" mixes `current_red` and `red_change` into one series, pushes samples when idle, and its baseline/reset are client-only.
- Severity: medium
- Views affected: Web
- Reference behavior: Tk shows `current_red` and `red_change` separately (view.py:782-789); model baseline reset is `reset_baseline` (:282-284) and `red_change = (red-baseline)/max(baseline,0.1)*100` (:300).
- Actual behavior: `pollState` (app.js:825-882) calls `pushPlotterSample(val)` for EVERY numeric state attribute whose name contains "red" (:879-881). The Red model's state (via ui_schema `readonly` attrs, redpercent_system.py:217-218; web_adapter.py:301-307) contains both `current_red` and `red_change`, so every poll appends two values (a percent and a relative-change percent) into `plotterData.currentRed` (:1255), interleaving them on the same chart, and any other model with a numeric attribute containing "red" (substring match, e.g. words ending in "-red") would pollute it too (none currently: grep of model_attr shows only these two). Samples are pushed on every poll regardless of `monitoring`, so the chart shows a flat line when stopped. The modal "Reset" buttons (:250-259 and :768-777) set a JS-only `baselineRed` (delta = absolute difference, :1261) and never call the model's `reset_baseline`; the schema "Reset Baseline" button does call the model but does not touch the JS baseline, so the two baselines disagree. `curr = this.plotterData.currentRed.slice(-1)[0]` may itself be a `red_change` value (:252,769).
- Failure scenario: Web plot shows a saw-tooth between e.g. 3% and -20%; Reset in the modal changes only the chart delta while the model's Red Change readout is unchanged.
- Proposed fix direction: Match exact attr `current_red` only (and the model's own `baseline_red`/`red_change` for delta), only push when `monitoring` is true (expose `monitoring` via a `readonly` schema attr), and have the modal's Reset call `dispatchCommand('Red Percent Window','reset_baseline')`.
- Confidence: verified

### REDPERCENT-14
- Title: Web entry fields (probe name/tilt) commit only on Set/Enter; typed-but-unset values are silently dropped from the saved CSV.
- Severity: low
- Views affected: Web
- Reference behavior: Tk writes to the model on every keystroke (view.py:632-635); PySide commits on `editingFinished` (pyside/view.py:259).
- Actual behavior: Web `entry` (app.js:589-600) requires clicking "Set" (:673-682) or Enter (:685-695) to POST `/api/set_attr`; the poll only updates the placeholder, never the value (:848-852). There is no dirty indicator. `save_log`'s re-sync (:246-247) only sees the model value.
- Failure scenario: Type "Probe-7" in Probe Name, click Save Log/Stop without Set -> CSV metadata empty.
- Proposed fix direction: Commit on `change`/`blur` events or send pending entry values before dispatching Save/Stop.
- Confidence: verified

### REDPERCENT-15
- Title: `_disabled_in_setup` (Web only) makes the probe filter inconsistent with default wiring; a disabled probe becomes the silently-selected position source.
- Severity: medium
- Views affected: Web / Model
- Reference behavior: `get_available_probe_names` (redpercent_system.py:179-185) intends to hide probes disabled in setup; Tk/PySide only build enabled devices (app.py:404-412 uses `build_models(active_configs)`), so the flag never exists there.
- Actual behavior: Web sets the flag for every model (web_adapter.py:161) and still registers disabled models, then wires ALL of them into `available_probes` (:166-170) and picks "Stepper Probe" via `set_stepper_model` regardless of the flag (:171-174; `set_stepper_model` :91-95 has no disabled check). `get_available_probe_names` excludes it from the dropdown options but `selected_probe_name` still equals the disabled probe. In JS, the dropdown only selects a value if an option with that text exists (app.js:871-876), so the UI shows "Select option..." while the model samples the disabled probe (`disable()` was called on it, :162-165, so its `pos_x` is stale). The docs claiming the flag is dead are wrong (read at :223 and redpercent_system.py:184), but the flag is only meaningful for Web.
- Failure scenario: Setup wizard with Stepper Probe unchecked, Chuck Positioner checked: Red still logs Stepper Probe's frozen position while the dropdown appears blank.
- Proposed fix direction: Filter disabled probes when building `probe_models` (web_adapter.py:169) and in `set_stepper_model`; fall back to the first enabled probe; ideally have `get_available_probe_names`/`build_models` share one predicate so no view-specific flag is needed.
- Confidence: verified

### REDPERCENT-16
- Title: Logged "velocity" is gamepad stick deflection, and rows are change-triggered, not time-based.
- Severity: low
- Views affected: Model
- Reference behavior: main had no velocity/location logging.
- Actual behavior: `vels[dim] = getattr(self.stepper_model,'vel_x',0.0)` (:316,320,324) resolves to `BaseProbe.vel_x` (probes.py:70-75) which returns `poller.get_mapped_state()['x_axisStatus']`, i.e. the controller axis value, 0.0 without a gamepad or in non-manual mode; column named "Stepper X Velocity" (:45). Rows are appended only when `abs(rounded_red - last_logged_red) >= 0.1` (:307), so a stage moving through constant red produces no rows; the plot's "0D: Index (Time / Samples)" (plot_data.py:76) is not time. Positions are `pos_x` strings from the last `read_position` call, updated only if some view polls that probe (Tk DynamicView poll, PySide `pos_timer`, Web `/api/state`), so they can be stale relative to the 60 Hz loop (:328).
- Failure scenario: Analysis of position vs red looks sparse/stale; velocity column is misleading.
- Proposed fix direction: Rename/derive velocity from position deltas + timestamps, add a timestamp column, log on every sample or at a fixed rate.
- Confidence: verified

### REDPERCENT-17
- Title: Plot path differs per view; Tk is 0D-only, Web cannot pick dims and silently renders empty figures.
- Severity: low
- Views affected: Tkinter / PySide / Web
- Reference behavior: main had no plotting; the model's `plot_data.py` documents all three views as sharing the renderer (plot_data.py:62-68).
- Actual behavior: All three plot a chosen CSV file, never the live log, so there is no stale-data path from the model. Tk: `open_plot_window` always calls `render_red_percent_figure("0D", ...)` (view.py:~816) - no 1D/2D/3D. PySide: `select_plot_type` (pyside/view.py:551-595) lets the user pick; default dims all equal `dims[0]` (dim1/2/3 combos each default index 0, :~571-573) so a default 2D plot uses the same dim twice; closing the dialog aborts silently (`if 'type' in selected`, :594). Web: `/api/plot` uses the first three header dims in file order (web_server.py:169-173), no dim selection; `render_red_percent_figure` returns an empty Figure (no axes) if the requested type lacks dims/data (plot_data.py:94-111), so a 2D request on a 1-dim CSV returns a blank PNG and JS shows nothing (app.js:1105-1108). Server-side `plot_data_ui`/`set_focus_area_ui` are no-ops (redpercent_system.py:160-168) - dispatch is intercepted in JS (app.js:1074,1120).
- Failure scenario: Tk user can never plot vs stage position; Web 2D plot of a 1-dim CSV shows a blank image without error.
- Proposed fix direction: Add plot-type/dim selection to Tk and Web; have `render_red_percent_figure` draw an explanatory message or raise on insufficient dims; add a live-plot of `data_log` (optional).
- Confidence: verified

### REDPERCENT-18
- Title: Screen-capture region/monitor selection differs from main (primary-monitor only vs virtual desktop; coordinate spaces differ).
- Severity: medium
- Views affected: Tkinter / PySide / Web
- Reference behavior: main selected the region with a fullscreen Tk overlay on the primary screen (`-fullscreen`, main:src/color_test_new.py:108-111) and captured with `sct.grab(self.focus_area)` (:189) using Tk pixel coordinates. Focus area size threshold >10 px (:152).
- Actual behavior:
  - Tk view (view.py:~695-741): non-fullscreen borderless Toplevel sized to `winfo_screenwidth/height` at +0+0 (primary only); same coordinate space as main. Escape now cancels; selection window `topmost` + `overrideredirect(True)` (macOS may not give it key focus - UNVERIFIED HYPOTHESIS).
  - PySide `SelectionOverlay` (pyside/view.py:462-500) spans the union of ALL screens (:474-476) and stores Qt global logical coordinates (`globalPosition`, :481-494) directly into `model.focus_area`. mss coordinates are physical pixels of the virtual desktop; on HiDPI-scaled displays (Windows/Linux fractional scaling) Qt logical != mss physical, so the captured region is offset/scaled (UNVERIFIED HYPOTHESIS - needs a scaled-display test; on macOS points match mss). No focus/`activateWindow` call, so Escape cancel may be unreliable on macOS. It also shows a `QMessageBox` while the topmost translucent overlay is still open (:496-497) which can be hidden behind it (UNVERIFIED HYPOTHESIS).
  - Web: `/api/screenshot` always uses `sct.monitors[1]` (web_server.py:127) - a single monitor, thumbnailed to 800x600 (:130) and drawn into an 800x600 canvas (index.html:209); JS scales by `hostOriginalWidth/canvas.width` and offsets by `monitor_left/top` (app.js:1420-1426) so the mapping is consistent with monitors[1] but other monitors can't be chosen, and the canvas is drawn stretched (`drawImage(..,0,0,canvas.width,canvas.height)`, app.js:1396) so a non-4:3 screen is scaled non-uniformly (still inverted correctly by the same factors). On macOS Retina, `monitor["width"]` is in points while `sct_img.size` is pixels (fine because the thumbnail is resized). The screenshot includes the browser and any other window and is served unauthenticated (GET /api/screenshot, no auth/CORS handling in web_server.py - host defaults to 127.0.0.1 per WebDashboardServer.__init__ :301).
  - Web `set_focus_area` (redpercent_system.py:86-89) is the only path that goes through the model method; there is no visible focus-area label in PySide (Tk shows `area_label`, view.py:740).
- Failure scenario: Multi-monitor lab PC: Tk and Web can only target the primary/first monitor; PySide can span but may capture the wrong rectangle on scaled displays.
- Proposed fix direction: One shared focus-area selection helper with explicit monitor choice in model (`set_focus_area(x,y,w,h,monitor=None)`), physical-pixel conversion via `screen.devicePixelRatio()` in PySide, and a focus-area label in PySide/Web.
- Confidence: verified for structural differences; hypothesis for DPI/focus subcases

### REDPERCENT-19
- Title: Display formatting/state parity: PySide and Web show raw floats; Tk/PySide button state does not follow FULL STOP.
- Severity: low
- Views affected: PySide / Web / Tkinter
- Reference behavior: Tk formats `.1f`/`+.1f` and colors change green/red (view.py:782-789; main:src/color_test_new.py:287-290).
- Actual behavior: PySide `_poll_model` does `str(getattr(model, attr))` for readonly labels (pyside/view.py:409,413-415) and Web does `String(val)` (app.js:845), e.g. `12.3456789012` and `-0.0`, no coloring. `emergency_stop`/FULL STOP calls `stop_monitoring` (redpercent_system.py:279-280,system_manager.py:70-78) but the Tk view's Start/Stop button states (view.py:755-767) are not updated, so Start stays disabled until the user presses Stop (which then shows the save prompt). Web/PySide have no such state.
- Failure scenario: Cosmetic; after FULL STOP the Tk Start button is disabled.
- Proposed fix direction: Format in the model (or add a `format` key in ui_schema) and have Tk's `poll_display` sync button state from `system.monitoring`.
- Confidence: verified

### REDPERCENT-20
- Title: Misc. model dead/legacy state.
- Severity: low
- Views affected: Model
- Reference behavior: n/a
- Actual behavior: `red_percent`, `is_monitoring`, `stop_event`, `thread`, `monitor_thread`, `baseline` are never read (grep verified, only `redpercent_system.py:61-72`); duplicate thread handles `_monitor_thread`(:80) vs `thread`/`monitor_thread`; `plot_data_ui`/`set_focus_area_ui` are no-op stubs that exist only to be schema commands (:160-168); `set_focus_area`'s `print` on each ROI commit (:88); `__del__` prints (:57-58). `set_device_attribute` allows `set_attr` on readonly attrs `current_red`/`red_change` because they are `model_attr`s (web_adapter.py:347-356,448-449), letting a client overwrite live readouts (harmless but incorrect).
- Failure scenario: none functional.
- Proposed fix direction: Delete unused fields; use `stop_event` for REDPERCENT-3; restrict `_schema_attrs` writes to `entry`/`dropdown`/`toggle` element types.
- Confidence: verified

### REDPERCENT-21
- Title: A monitoring run has no identity and no addressable output location; the autosave path is relative to the process CWD.
- Severity: high (data integrity — runs are not attributable to the physical trial that produced them)
- Source: **owner instruction, 2026-09-20** — not from the 2026-09-19 audit pass. Recorded here so it moves through the same ledger as the audited findings.
- Views affected: Model (all three views inherit it)
- Reference behavior: an external experiment record (one row per physical trial, with a trial ID) has to join to the run this app produced. The join key must be written by the app at capture time; a filename assigned afterwards by a human is the thing that goes wrong at the bench.
- Actual behavior: `RedPercentDataLog` carries `probe_name` and `probe_tilt_angle` and nothing else identifying (`redpercent_system.py:18-25`). `autosave_log` (:249-257) builds `redpercent_log_%Y%m%d_%H%M%S.csv` as a **bare relative path** and hands it to `save_log`, so an unattended stop writes into whatever directory the launcher happened to start in — different for `run.sh`, `run_macos.sh` and the web server. The attended path is the `file_save` composite (:357), where the operator types a name by hand. Nothing in the model knows which physical trial a run belongs to, so the mapping from CSV to trial exists only in the operator's memory between the bench and the analysis machine.
- Failure scenario: ten runs in an afternoon, two of them autosaved after a FULL STOP. Three `redpercent_log_*.csv` files sit in `~`, four in the repo root, three named by hand. The timestamps are the only evidence of which is which, and the two autosaved ones are precisely the runs whose bench notes are least complete.
- Proposed fix direction: `MonitoringRun` (RC-11 item 1) snapshots a `run_id` alongside its configuration — an operator-set string, defaulting to a timestamp slug when unset — and an `output_root` directory resolved once at construction, never from CWD. Every artifact the run emits is named `<run_id>_<kind>.<ext>` under `output_root/<run_id>/`, so a file is self-describing after it is moved. `autosave_log` writes into that directory rather than a bare name. `run_id` is a schema `entry` with `disabled_when: monitoring`, so it is fixed for the run's duration like the rest of the configuration.
- Confidence: verified (code read :18-25, :249-257, :304-380)

---

### REDPERCENT-22
- Title: Run configuration is written as CSV comment rows, and the parameters that make red percent interpretable as a force proxy are not recorded at all.
- Severity: high (data integrity — a saved run cannot be re-interpreted without the operator present)
- Source: **owner instruction, 2026-09-20** — not from the 2026-09-19 audit pass.
- Views affected: Model
- Reference behavior: a saved run is readable by a standard CSV reader without special-casing, and carries enough of its own configuration that its red-percent column can be converted to the quantity the experiment actually wants.
- Actual behavior: `save_to_csv` (:36-57) prepends `["# Metadata"]`, `["# Probe Name", ...]`, `["# Probe Tilt Angle", ...]` and a blank row *before* the header row. This is not a CSV comment convention — it is four data rows with a `#` in the first cell. `pandas.read_csv` on this file takes `# Metadata` as the header and every real column name as data; it needs `skiprows=4`, a magic number that changes the moment a metadata field is added. Worse, the fields that determine what a red-percent number *means* are never written anywhere: `baseline_red` (:93), the `focus_area` rectangle and its pixel dimensions (:101-103), the `detect_red` threshold (:203), the sample cadence of `_monitor_colors`, and the run's start and stop wall-clock times. Two runs with the same red percent and different focus-area sizes are not comparable, and nothing in the artifact says so.
- Failure scenario: a run is saved on Saturday and analyzed on Monday. The analyst reads it with a default `read_csv`, gets a one-column frame of strings, fixes that with `skiprows=4`, then finds the red-percent column cannot be normalized because neither the baseline nor the ROI size was recorded. The run is not wrong, it is un-interpretable, which is the more expensive failure because it looks like data.
- Proposed fix direction: split the artifact. The CSV becomes a plain rectangle — header row, then samples, no comment block — so any reader opens it correctly. The configuration goes to a sibling `<run_id>_station_meta.json` written by `MonitoringRun` at **stop**, holding the snapshot it already owns: `run_id`, probe name, tilt angle, sync dimensions, focus area (including width/height in px), `baseline_red`, red threshold, nominal sample interval, start/stop ISO-8601 timestamps, sample count, and the source revision. JSON because it is the format the analysis side already reads without a schema. This composes with RC-11 item 4 (timestamp column, flagged invalid samples) rather than competing with it: item 4 makes each row trustworthy, this makes the file interpretable.
- Confidence: verified (code read :36-57, :93-103, :189-216)

---

### REDPERCENT-23
- Title: Per-run specimen annotation has nowhere to live, so it is recorded on paper and re-keyed later.
- Severity: medium (data integrity — the re-keying step is where trials get mislabelled)
- Source: **owner instruction, 2026-09-20** — not from the 2026-09-19 audit pass. This one is a **scope addition**, not a repair: the app never had this field set and nothing today is broken by its absence.
- Views affected: Model, and all three views through the schema (RC-7 / D-6)
- Reference behavior: the facts only the operator knows at the bench — which specimen, which consumable, where on the stage, what was *planned* versus what the station actually did — are captured in the same act as the run, not transcribed afterwards.
- Actual behavior: the model has exactly two operator fields, `probe_name` and `probe_tilt_angle` (:98-99), both free text (`probe_tilt_angle` is declared `float` in `PARAMS` but initialized to `""`, :57-58 vs :99 — worth fixing with this). There is no field for a specimen identifier, no field for the consumable/tip in use, no field for stage position, and no distinction anywhere between a *planned* setpoint and the value the station actually ran at. The operator therefore keeps a paper sheet, and the paper sheet is joined to the CSVs by filename — see REDPERCENT-21 for why that join is unreliable.
- Failure scenario: a batch of trials is run across several consumables. Two weeks later a result looks anomalous and the question is whether that trial was late in a worn consumable's life. The answer is on a sheet of paper, keyed to a filename that was typed by hand at 11pm.
- Proposed fix direction: one extensible annotation block on the run rather than a fixed column list, because the fields are experiment-specific and hardcoding this weekend's set guarantees the next experiment needs a code change. Concretely: a `run_annotations` dict on `MonitoringRun`, declared through the RC-9 `Param` table so all three views render it from one schema (D-6), snapshotted at start with the rest of the configuration, and emitted into the `<run_id>_station_meta.json` of REDPERCENT-22 under its own key. Ship it with the fields the current experiment needs as the default set — specimen id, consumable/tip id, stage X/Y, and a free-text note — and make the set a table, not a set of attributes, so adding a field is a one-line edit. **Planned-versus-actual is the load-bearing part:** the annotation block holds what the operator *intended*, while REDPERCENT-22's meta block holds what the station *did*, and the two are never merged into one field.
- Confidence: verified (code read :57-58, :98-99, :304-360)

---

## Coverage

Read fully: src/model/redpercent_system.py (1-328), src/model/plot_data.py (1-113), main:src/color_test_new.py (whole file), src/views/tkinter/view.py 15-60, 170-300 (Dashboard + DynamicView start), 585-835 (RedPercentView + DraggableClosableNotebook close), src/views/pyside/view.py 20-75, 121-460 (QtDynamicView), 462-800 (overlay, PlotDialog, RedPercentDynamicView, DashboardWindow start), 800-967 (open/close_device_view, closeEvent), src/views/web/web_adapter.py 150-489, src/views/web/web_server.py 100-341, src/views/web/static/js/app.js 20-30 (baseline vars), 236-275, 540-900, 1040-1480 (dispatch, plotter, ROI), src/views/web/static/index.html 166-220, src/app.py 395-415, 575-605, 725-745, 760-830, src/app_bootstrap.py 140-190, src/model/system_manager.py, src/model/base.py 1-30, src/error_routing.py 1-45 (thread-safety of report_error was checked; the three view callbacks marshal to the GUI thread: tk queue view.py:15-40, Qt signal pyside/view.py:20-45, so no thread finding).

Not done: no live run (all race/HiDPI/macOS-focus items are labeled hypothesis); did not read pyside style.qss, the rest of app.js (setup wizard, badges, 900-1040, 1480-1850), other models (out of scope), probes.py beyond vel_x/pos_x, tkinter DynamicView schema build (300-580), or index.html beyond the plot/ROI modals; did not verify `git log` history claims in docs.
