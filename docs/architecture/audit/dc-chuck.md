# Audit: DC Probe / Chuck Positioner (ui_schema, numeric commit, limits, teardown)

> **Input (banner added 2026-09-23).** Audit of the old tree (now `legacy/src/`); current until the rebuild replaced it on 2026-09-23. Kept because the ledger in `docs/implementation/progress.md` and `docs/rebuild/carry.json` cite these finding IDs.

## Object summary

- `DCProbe` (src/model/probes.py:501-528) and `ChuckPositioner` (probes.py:531-536) are thin subclasses of `BaseProbe` (probes.py:14-490). Chuck overrides only step defaults ("2"); DC overrides step defaults ("1"), speeds ("120"), adds `slow_speed`/`brake_distance` (probes.py:508-511), extends `ui_schema` (514-522) and `get_params` (524-528). All command paths (enable, enter_auton, enter_manual, macro_start_auton, full_stop, power_down, teardown, emergency_stop, watchdog) live in BaseProbe. `src/model/numeric.py` (`num`, `safe_float`) and `src/model/base.py` (`ManagedModel` Protocol) are as described below.
- Constructors: `app_bootstrap.build_models` (src/app_bootstrap.py:148-155) for Tk/Web-setup; Web via `WebModelAdapter.initialize_setup` -> `build_models` (web_adapter.py:148); PySide hot-add in `DashboardWindow.open_device_view` (pyside/view.py:884-892) with `(None, "None", {})`.
- Owner: `SystemManager.active_models` (src/model/system_manager.py:7). Teardown contract: `BaseProbe.teardown()` (probes.py:479-487) = poller.stop_polling + poller.close, then `power_down()` (stop + 'd' + 'k'), then `serial_comm.close()`. `emergency_stop()` (probes.py:489-490) = `power_down()`. Both are satisfied by BaseProbe (ManagedModel OK). SystemManager primitives: `shutdown_all` (system_manager.py:59), `full_stop_all` (70), `remove_model` (24), `reboot_model` (34).
- View teardown paths: Tk `DashboardWindow.on_close` -> `shutdown_all` (tkinter/view.py:226-231) OK. PySide `closeEvent` -> per-view `cleanup()` then `shutdown_all` (pyside/view.py:957-967) OK for whole-window close, but per-dock close hand-rolls a partial teardown (finding DC-2). Web `WebDashboardWindow.close` -> `shutdown_all` (web_view.py:74-78) OK.
- Numeric layer: `num()` (numeric.py:3-17) coerces to float, falls back to a caller-supplied default on parse failure/NaN/Inf, optional `minimum` clamp only (no maximum). `safe_float` (numeric.py:19-32) returns None on failure but is NOT used by any probe path (grep of probes.py imports only `num`, line 7).

### Existing-docs note
I did not diff against docs/architecture (task said read relevant ones; I prioritized verified code reading). Every claim below is from code read in this run.

## Command paths and where limits/interlocks are enforced (Q1 summary)

| Command | Entry | Enforced where |
|---|---|---|
| Toggle Autonomous | `toggle_auton` (probes.py:203) -> `enter_auton` (219) / `full_stop` (467) | Model: `enable()` must succeed (423-434); no gamepad requirement |
| Toggle Manual | `toggle_manual` (197) -> `enter_manual` (226) | Model: gamepad presence checked BEFORE `enable()` (234-238); per-tick recheck in `send_manual_mode_command` (359-364) |
| Start Stepping | `macro_start_auton` (245) | Model: auto-enables, forces auton; sets `is_stepping=True` (250). No "must already be in auton" gate (main had one, chuck_frame.py:535-540 / 457-460) |
| Full Stop / disable | `_stop_and_disarm` (442-462) | Model: clears flags, stop packet, serial 'd' (always sent), `system_enabled=False` |
| Global FULL STOP | `SystemManager.full_stop_all` -> `emergency_stop` -> `power_down` (470-477: stop + 'd' + 'k') | Model; Tk `stop_btn` (tkinter/view.py:186-188), PySide (pyside/view.py:756-758), Web `/api/system/full_stop` (web_server.py:222) |
| Idle interlock | `_start_interlock_watchdog` (399-421), 300 s | Model thread; deferred when `is_stepping or manual_flag` (409) |
| Numeric limits | `get_params` (338-351, DC 524-528), `send_manual_mode_command` (378-385) | Model: `minimum=1` on steps and speeds only; no maxima, no dist limits, slow_speed/brake_distance no minimum, no cross-field checks. Views: Tk/PySide reject non-numeric text at commit; Web does not (finding DC-4) |
| Soft travel limits | none anywhere for DC/Chuck (main also had none; firmware only) | n/a |

## Findings

### DC-1
- ID: DC-1
- Title: Idle-disable watchdog is permanently suppressed after any "Start Stepping" (is_stepping never cleared) and never fires in manual mode
- Severity: high (coils stay energized indefinitely; safety interlock defeated)
- Views affected: Model (all views)
- Reference behavior: main re-armed a 5-minute timer on Start Stepping and on controller activity, and `auto_disable` disabled the system regardless of mode (chuck_frame.py:519-533, 540; manual activity via `activity_callback=self.reset_disable_timer` at 640).
- Actual behavior: `macro_start_auton` sets `self.is_stepping = True` (probes.py:250). Nothing except `_stop_and_disarm` (445) resets it (grep: no other assignment besides `__init__` and run_script's own thread). Watchdog `_watch` does `continue` while `self.is_stepping or self.manual_flag` (409-412). So after one Start Stepping click the probe stays "stepping" forever and the 300 s timeout can never trigger; likewise in manual mode the timeout is skipped entirely (deliberate per comment 410-411 but a deviation from main, where idle manual mode also auto-disabled).
- Failure scenario: Operator presses Start Stepping on Chuck for a 10-step move, walks away. Move finishes in seconds; `is_stepping` remains True; steppers stay energized (holding current, heat) until someone clicks a toggle or global FULL STOP. Same if left in Manual Mode with no input.
- Proposed fix direction: Make `is_stepping` a time-boxed state (e.g. record `last_step_command_time` and treat as stepping only for computed move duration, or clear when position reaches target / after N s); minimally, have `_watch` only defer for `manual_flag` when `touch_activity` has been called recently (controller input) and stop deferring for `is_stepping`; touch_activity is already called by `send_autonomous_command` (354). Add a unit test with `_INTERLOCK_TIMEOUT` overridden.
- Confidence: verified

### DC-2
- ID: DC-2
- Title: PySide per-dock close hand-rolls a partial teardown: no serial close, no power_down, bypasses SystemManager, and re-open builds a hardware-less model
- Severity: high (resource leak + silent loss of hardware connection)
- Views affected: PySide6
- Reference behavior: SystemManager contract: `remove_model` then `teardown()` (system_manager.py:18-27; probes.py:479-487 closes serial). Tk has no per-tab model removal (see DC-9).
- Actual behavior: `close_device_view` (pyside/view.py:813-847) calls `model.disable()` (837-838: stop + 'd', no 'k', no serial close), then `poller.stop_polling()/close()` (839-841; already done in `QtDynamicView.cleanup` 451-453), `hasattr(model,'disconnect')` (842; BaseProbe has no `disconnect`, dead branch), then `del self.system_manager.active_models[device_name]` (845-846) directly, without the lock and without `remove_model`/`teardown`. `model.serial_comm.close()` is never called (only `teardown` does, probes.py:486-487). Re-checking the sidebar box calls `open_device_view` (858-892) which builds `DCProbe(None, "None", {})` / `ChuckPositioner(None, "None", {})` (888-892): no serial port, and a fresh private `active_claims={}` (not the shared claims dict), so gamepad claim arbitration is bypassed.
- Failure scenario: Operator closes the Chuck dock (X or uncheck) to tidy layout, then reopens it: the old serial handle stays open until GC, so a later reconnect/port grab can hit "port busy"; the reopened Chuck panel is a hardware-less model (`serial_comm=None`), all commands silently no-op in `send_*` (`if self.serial_comm`) and there is no serial-port control to fix it (reconnect_serial removed from schema, probes.py:148-154). Position never updates.
- Proposed fix direction: Replace lines 834-847 with `m = self.system_manager.remove_model(device_name); if m: m.teardown()` (guarded). On reopen, do not fabricate `(None,"None",{})`: either disallow re-adding a hardware device closed this session, or reboot via `system_manager.reboot_model(name, constructor, port, controller, active_claims)` using the original config (retain configs in app/bootstrap). Delete dead `disconnect` branch and the redundant poller close (cleanup already does it).
- Confidence: verified

### DC-3
- ID: DC-3
- Title: `teardown()` is not exception-safe: a failing poller stop/close skips power_down and serial close
- Severity: high (hardware left energized on shutdown)
- Views affected: Model (all views via shutdown_all / reboot_model)
- Reference behavior: main `_on_closing` sent `serial.disable()` first, then stopped controller (chuck_frame.py:676-684).
- Actual behavior: probes.py:482-487 runs `poller.stop_polling(); poller.close()` before `power_down()` with no try/finally. If `poller.close()` raises (pygame quit errors are only partly wrapped in gamepad.py:504-513), `power_down()` and `serial_comm.close()` never run. SystemManager only reports the error (system_manager.py:63-68), it does not retry hardware stop.
- Failure scenario: Dashboard closed while gamepad unplugged/pygame in bad state -> exception in `poller.close()` -> steppers remain enabled after the app exits.
- Proposed fix direction: Reorder to hardware-first: `try: self.power_down() finally: try: poller stop/close finally: serial_comm.close()`; wrap each step separately so one failure cannot skip the others.
- Confidence: verified (structure); whether poller.close() can raise in practice is UNVERIFIED HYPOTHESIS (settle by reading gamepad.py close paths / fault-injecting).

### DC-4
- ID: DC-4
- Title: Web accepts any string for numeric entries; invalid values silently become hard-coded defaults that differ from DC/Chuck defaults
- Severity: high (DC runs at 400 instead of 120; step size 16 instead of 1/2)
- Views affected: Web (trigger), Model (silent fallback)
- Reference behavior: Tk rejects empty/NaN/Inf/unparseable text and restores the model value (tkinter/view.py:348-361, validator 343); PySide restores on ValueError (pyside/view.py:246-257) plus `QDoubleValidator` (244). Main passed the raw entry text (chuck_frame.py:286-300).
- Actual behavior: Web `set_device_attribute` only checks the attr is in the schema (web_adapter.py:448-449), then `setattr(model, attr, value)` with no numeric validation (463; the type-cast branch 456-462 is a no-op for str-valued attrs since `curr` is a str). JS sends `inputEl.value` unvalidated (app.js:677-679). Model then calls `_num(self.full_speed, 400, minimum=1)` (probes.py:343) with hard-coded fallbacks: steps 16 (340-342), full_speed 400 (343), man speed 400 (385), independent of the subclass defaults DC 1/120 (probes.py:505-511) or Chuck 2 (534-536). DC's own `slow_speed`/`brake_distance` fall back to 0 (526-527).
- Failure scenario: In the Web UI on the DC card, user clears "Autonomous Speed" (or types "12O") and clicks Set. Toast reports success ("set to ..."). Next Start Stepping sends full_speed=400 (3.3x the DC design speed 120) and Chuck step size blank -> 16 instead of 2 (8x coarser). No error shown.
- Proposed fix direction: (1) Server: in `set_device_attribute` validate via `safe_float` for schema entries flagged numeric (add `"numeric": true` to schema entries or infer from current value like Tk/PySide) and return 400 on failure; (2) Model: make `get_params`/`send_manual_mode_command` use per-instance defaults (e.g. store `_default_full_speed`) or use `safe_float` and abort the send on invalid, per numeric.py:19-32's own rationale; (3) add client-side numeric check in app.js before POST.
- Confidence: verified

### DC-5
- ID: DC-5
- Title: Web has no manual-mode input routing at all; Manual Mode toggle energizes coils and does nothing
- Severity: high (misleading state: coils enabled, flag true, no motion, watchdog suppressed by manual_flag)
- Views affected: Web
- Reference behavior: Tk `start_polling` starts `poller.start_polling(...)` and a 50 ms `_route_input` that calls `model.send_manual_mode_command` while `manual_flag` (tkinter/view.py:542-564); PySide same at 20 ms (pyside/view.py:155-184).
- Actual behavior: grep of src for `start_polling` / `send_manual_mode_command` / `_route_input` finds callers only in tkinter/view.py and pyside/view.py plus probes.py itself; neither web_view.py, web_server.py, web_adapter.py nor app.js do. `WebDashboardWindow.__init__` only rewires `poller.log_updater` (web_view.py:55-65). The Web view therefore never starts the poller loop nor forwards gamepad state to the serial layer. `toggle_manual` passes the gamepad-present check (poller constructed in BaseProbe.__init__, probes.py:37-41), `enable()` energizes ('e'), `manual_flag=True`.
- Failure scenario: Web operator clicks "Enter Manual Mode" with a gamepad attached: card shows MANUAL MODE, coils enabled, stick input never reaches the stage; because `manual_flag` is True the idle watchdog never disables (probes.py:409), so coils stay energized until FULL STOP.
- Proposed fix direction: Add a server-side manual-routing thread in the web layer (or better, move the route-input loop into the model/controller so all views share it, given the interlock was already moved there): while `manual_flag`, call `model.send_manual_mode_command(poller.get_mapped_state())` at ~20-50 Hz and send the `{}` neutral packet on the falling edge; start poller with `start_polling(gui=None-compatible adapter)`. Requires checking `ControllerPoller._poll_loop` for a GUI-scheduler dependency (gamepad.py:474-488; owned by another agent).
- Confidence: verified (absence of call sites); consequence for gamepad_poll timing UNVERIFIED (settle by reading `_poll_loop`).

### DC-6
- ID: DC-6
- Title: Web interlock JS disables "Start Stepping" and all entries in Autonomous mode, so stepping is one-shot; its system_enabled branch is dead
- Severity: high (core workflow broken)
- Views affected: Web
- Reference behavior: main/Tk: enter Autonomous, then edit distances and press Start Stepping repeatedly (chuck_frame.py:535-550; Tk never disables controls).
- Actual behavior: `pollState` (app.js:888-923) computes `autonOn` from `attrs.auton_flag`; when `autonOn`, every control in the card except those matching `isStop`/`isPowerDown`/`isAutonToggle`/`isManualToggle` is set `disabled = true` (911-913). `isStop` matches innerText "Full Stop" or command `full_stop` (899); `isPowerDown` matches "Power Down"/`power_down` (898): neither exists in the current schema (probes.py:124-156; removed "Per-device Full Stop" comment 138-142), so they are dead. "Start Stepping" (`macro_start_auton`) and the Set buttons/inputs are therefore disabled while `auton_flag` is True (and `macro_start_auton` itself sets `auton_flag=True`, probes.py:248). The first branch (903-908) keys on `attrs.system_enabled`, which is not in `get_state` because `system_enabled` is not a schema `model_attr` (get_state only emits schema attrs, web_adapter.py:302-307), so it is dead code too.
- Failure scenario: Operator toggles Autonomous ON, then finds Start Stepping and X/Y/Z Steps greyed out; the only way to step again is toggle off (which now runs full_stop -> 'd' disable) and press Start Stepping from idle (auto-enables, then everything greys out again). Distances/speeds cannot be changed while running. Also: Controller ID dropdown and "Controller Log Window" get disabled during modes.
- Proposed fix direction: Delete the client-side interlock or make it schema-driven (only disable elements that the model declares unsafe in a mode, e.g. add `"disable_when": ["auton","manual"]` per element); do not pattern-match button text. Interlocks belong in the model (`macro_start_auton` etc. already check enable()).
- Confidence: verified

### DC-7
- ID: DC-7
- Title: PySide numeric entries are not flushed before a command on macOS (stale-value, one-edit-behind) and blank/intermediate text is never committed
- Severity: medium
- Views affected: PySide6
- Reference behavior: Tk `_execute_command` calls `self.focus_set()` first to force FocusOut commit (tkinter/view.py:482-492; fix commit b42c13e). Main read entry `.get()` at command time (chuck_frame.py:286-300), so it was never stale.
- Actual behavior: PySide commits only on `editingFinished` (pyside/view.py:259). `_execute_command` (377-399) has no `clearFocus()`/commit-all step, and grep of pyside/view.py shows no `clearFocus`/`setFocusPolicy`. With `QDoubleValidator(-1e9, 1e9, 3, ...)` set (244), Qt emits `editingFinished` only when the validator returns Acceptable, so blank, "-", "1e" etc. never commit (the `commit()` ValueError branch 253-254 is effectively unreachable for typed text) and the widget shows the invalid text until it loses focus and `_poll_model` overwrites it (411-412).
- Failure scenario: (macOS; Qt QPushButton typically does not take focus on click there) user types "50" in Target X Dist and clicks Start Stepping without pressing Enter/Tab: `editingFinished` never fires, model still holds the old x_dist, stage moves by the previous value. UNVERIFIED HYPOTHESIS for the macOS button-focus behavior (settle: run on macOS, type then click, print `model.x_dist`); the missing flush in code is verified.
- Proposed fix direction: In `_execute_command` first call `self.setFocus()`/`self.clearFocus()` on the view (or iterate `self.vars` QLineEdits and invoke each `commit()` closure stored in a dict) before dispatch. Also commit on `textChanged` for numeric fields or normalise via a shared `commit_all()`.
- Confidence: verified (no flush in code); hypothesis (macOS symptom)

### DC-8
- ID: DC-8
- Title: Web entries commit only via "Set"/Enter; typed-but-unset values are silently ignored by commands, and no current value is shown in the field
- Severity: medium
- Views affected: Web
- Reference behavior: Tk commits on Return/FocusOut and forces commit before commands (tkinter/view.py:348-361, 492); main used live `.get()`.
- Actual behavior: Entry has a separate Set button (app.js:589-600; handlers 673-695). `dispatchCommand` (1073-1164) sends only the command name with `args=[]`; it does not read or flush pending inputs. `pollState` writes only `placeholder`, never `.value` (849-852), so the operator sees the model value only as grey placeholder text that disappears on focus, and edited-but-unset text looks identical to committed text.
- Failure scenario: Operator types Y Steps "200" and clicks Start Stepping without Set: stage moves by the old y_dist; toast says "macro_start_auton executed".
- Proposed fix direction: In `dispatchCommand`, before POST, collect all `.schema-input` in the same card whose `.value !== ''` and POST `set_attr` for each (await), or mark dirty inputs visually (amber border) and block dispatch until Set. Populate `.value` when not focused and not dirty.
- Confidence: verified

### DC-9
- ID: DC-9
- Title: Tk tab close only `forget()`s the tab; model, poller and manual routing keep running
- Severity: medium
- Views affected: Tkinter
- Reference behavior: SystemManager contract requires `remove_model` + `teardown()` on removal; PySide closes a dock via `close_device_view` (albeit partially, DC-2).
- Actual behavior: `DraggableClosableNotebook.close_tab` (tkinter/view.py:158-162) calls `on_close_tab_callback` if set else `self.forget(index)`. Grep of src for `on_close_tab_callback` finds only its definition (view.py:120) and use (159-160): nothing ever assigns it, so right/middle-click "Close Tab" just hides the frame. The DynamicView's `_poll_model`, `_route_input` (553-564) and `_poll_pos` (568-571) `after` loops keep running against a hidden tab, and the model remains in `active_models` (so FULL STOP still reaches it).
- Failure scenario: Operator in Manual Mode closes the Chuck tab: gamepad still drives the stage with no visible UI; toggle to stop is gone (only global FULL STOP remains). Position/serial polling continue.
- Proposed fix direction: Set `notebook.on_close_tab_callback` in `DashboardWindow.__init__` to a handler that stops the view's after-loops, calls `system_manager.remove_model(name)` then `teardown()`, then `forget`. Keep `tab_metadata` (222) in sync.
- Confidence: verified

### DC-10
- ID: DC-10
- Title: Full Stop / mode-off now also disables the drivers (coils off); main's Full Stop only stopped motion
- Severity: medium (behavior change with mechanical consequences on a vertical/holding axis)
- Views affected: Model (all views)
- Reference behavior: main `full_stop_button` clears flags and sends a stop packet only; `system_enabled` stayed True (chuck_frame.py:552-567); disabling was a separate Enable/Disable button (498-522).
- Actual behavior: `full_stop`, `disable` and toggle-off all call `_stop_and_disarm` which always sends serial 'd' and sets `system_enabled=False` (probes.py:442-468). The dashboard global FULL STOP additionally sends 'k' (power_down, 470-477). There is no way in the UI to stop motion while keeping holding torque.
- Failure scenario: Operator toggles Manual off to reposition the Chuck; coils release, a loaded axis can sag/drift, and position counter drift vs. firmware step count is possible after coils are released.
- Proposed fix direction: Decide intended semantics with owner. If holding torque matters, split `stop_motion()` (stop packet, keep enabled, keep flags cleared) from `disable()`; wire toggle-off to `stop_motion()` and leave FULL STOP/E-stop as the strong path.
- Confidence: verified (code); the physical consequence is an owner decision

### DC-11
- ID: DC-11
- Title: Web API write allowlist includes readonly attrs, mode flags and controller_var: mode/interlock bypass and inconsistent state
- Severity: medium
- Views affected: Web (API) / Model
- Reference behavior: UI contract: mode changes only through `toggle_auton`/`toggle_manual`/`set_controller`, which run `enable()` and the gamepad check (probes.py:219-243, 182-192).
- Actual behavior: `_schema_attrs` collects every `model_attr` in the schema (web_adapter.py:346-356) including readonly `pos_x/y/z`, toggle attrs `auton_flag`/`manual_flag`, `controller_var`, `serial_port`. `set_device_attribute` then does raw `setattr` (463), so `POST /api/set_attr {attr:"manual_flag", value:"true"}` sets `manual_flag=True` without `enable()`, gamepad check or stop packet; `auton_flag=true` yields `command_code_auton=1` in later `get_params` with `system_enabled` False; `controller_var` changes without `poller.set_controller`. Comment at 314-318 claims "only what the UI can already trigger".
- Failure scenario: Buggy or scripted client posts `auton_flag=true`, then invokes `send_autonomous_command`-bearing paths with flag/hardware state out of sync (Python believes auton, firmware never enabled; watchdog not started since `enable()` never ran).
- Proposed fix direction: Restrict `_schema_attrs` to `entry` elements only (exclude `readonly`, `toggle`, `dropdown`); dropdown writes go through their `command`.
- Confidence: verified

### DC-12
- ID: DC-12
- Title: `_disabled_in_setup` is always False (reads a key normalized configs no longer have)
- Severity: low (dead flag, disabled devices not greyed in Web)
- Views affected: Web
- Reference behavior: Intent: devices unchecked in setup are marked `_disabled` in `get_devices` (web_adapter.py:223-224).
- Actual behavior: `initialize_setup` normalizes configs into dicts with only `device/port/controller/mode` (web_adapter.py:99-104, 115-120); line 161 then evaluates `not cfg.get("enabled", True)` on that normalized dict, which never has `enabled`, so it is always `False`.
- Failure scenario: Operator disables Chuck in the setup table; the card is still fully interactive (port "None" model built anyway).
- Proposed fix direction: Carry `"enabled": is_enabled` into the normalized dicts (lines 99-104 and 115-120).
- Confidence: verified

### DC-13
- ID: DC-13
- Title: Web connection badge trusts editable `serial_port`; typing "SIM" relabels a hardware probe as SIMULATED
- Severity: low
- Views affected: Web
- Reference behavior: n/a (main had no badge).
- Actual behavior: `_determine_connection_status` reads `serial_port` first (web_adapter.py:243-245) and returns "simulated" if it is "SIM"; `serial_port` is a schema entry writable via Set (probes.py:111, web_adapter.py:463).
- Failure scenario: Operator edits the "Serial Port" box (a no-op for hardware, see DC-14) to SIM: badge lies.
- Proposed fix direction: Base the status on the live `serial_comm` object, not on the editable attribute; or make `serial_port` readonly.
- Confidence: verified

### DC-14
- ID: DC-14
- Title: "Serial Port" entry is a dead control in all three views
- Severity: low
- Views affected: Tkinter / PySide6 / Web
- Reference behavior: main "Serial Reconnect" button re-opened the port from the entry (chuck_frame.py:663-674).
- Actual behavior: Schema still has an editable `serial_port` entry (probes.py:111), but `reconnect_serial` was removed from the schema (148-154), so editing only rewrites `self.serial_port` (Tk per-keystroke trace, tkinter/view.py:363-370; PySide editingFinished setattr 256; Web Set). Nothing reads it except `reconnect_serial` and the Web badge (DC-13). PySide has a lone caller of `reconnect_serial` (pyside/view.py:866-875) that is unreachable in normal flow (see DC-16).
- Failure scenario: Operator changes the port after a USB re-enumeration expecting reconnect; nothing happens.
- Proposed fix direction: Make `serial_port` a `readonly` element, or re-expose a wizard-side reconnect (`reconnect_serial` must first send 'd' before closing; it currently does not, probes.py:161-175, leaving the firmware enabled while Python flags `system_enabled=False`).
- Confidence: verified

### DC-15
- ID: DC-15
- Title: DC-specific brake fields have no validation, no minimum and no cross-checks
- Severity: medium (DC hardware parameters)
- Views affected: Model (all views)
- Reference behavior: main DC_frame forwarded raw `entry_slow_speed`/`entry_brake_distance` (main:src/DC_frame.py:245-252, defaults "0" at 148/151).
- Actual behavior: `DCProbe.get_params` (probes.py:524-528) sends `_num(slow_speed, 0)` and `_num(brake_distance, 0)`: no `minimum`, so negative values pass to firmware; no rule that slow_speed <= full_speed or brake_distance <= |dist|. Manual mode ignores both (378-385).
- Failure scenario: Operator enters brake distance -50 or slow speed > full speed; firmware receives it verbatim.
- Proposed fix direction: Add `minimum=0` to both and clamp `slow_speed` to `full_speed`; document brake units ("steps", schema text 520).
- Confidence: verified (code); firmware handling of negatives UNVERIFIED HYPOTHESIS (settle by reading firmware .ino)

### DC-16
- ID: DC-16
- Title: PySide double-polls read_position/poll_status; `open_device_view` re-open path calls `reconnect_serial` blocking the GUI and leaving hardware enabled
- Severity: low
- Views affected: PySide6
- Reference behavior: Tk polls position once at 100 ms (tkinter/view.py:567-571).
- Actual behavior: `QtDynamicView.__init__` starts `pos_timer` (100 ms `_safe_read_position`, exception-guarded, pyside/view.py:145-148, 186-190) and `_poll_model` (50 ms) also calls `model.read_position()` unguarded (401-405). Separately `open_device_view` for an already-docked device calls `model.reconnect_serial()` behind a modal on the GUI thread (865-877): `reconnect_serial` closes the serial, sleeps 1 s (probes.py:168), reopens, and sets `system_enabled=False` without sending 'd' (161-175). Path is reachable only if an already-open device is re-checked (I found no caller producing it; UNVERIFIED HYPOTHESIS that it is dead).
- Failure scenario: (double poll) two readers race for the same rx buffer; harmless but 3x serial polling load. (reconnect path) if ever hit, GUI freezes 1 s and firmware stays enabled.
- Proposed fix direction: Remove the duplicate `read_position`/`poll_status` calls from `_poll_model`; delete the reconnect branch.
- Confidence: verified (double poll); hypothesis (reconnect path reachability)

### DC-17
- ID: DC-17
- Title: Manual-mode input rate is 10x-4x slower than main and differs between views; controller-loss handling no longer disables the system
- Severity: low
- Views affected: Tkinter (50 ms) / PySide6 (20 ms) / Model
- Reference behavior: main `_manual_mode_loop` re-armed every 5 ms (chuck_frame.py:609); on controller disconnect it called `full_stop_button` and `enable_button` (disable) (582-596).
- Actual behavior: Tk `_route_input` 50 ms (tkinter/view.py:563-564), PySide 20 ms (pyside/view.py:184). On gamepad loss, `send_manual_mode_command` only clears `manual_flag`, warns, and sends a neutral packet (probes.py:359-364); `system_enabled` stays True, no 'd'.
- Failure scenario: Jog latency/step-rate differs between views; unplugging the controller mid-jog leaves coils energized until the (deferred-when-idle) watchdog fires after 5 minutes.
- Proposed fix direction: Unify the route loop in one place (model/controller) with one rate (measure firmware expectations); in the gamepad-loss branch call `self.full_stop()` instead of only clearing the flag. Firmware dependence on 5 ms cadence is UNVERIFIED HYPOTHESIS (settle by reading firmware manual-packet timeout).
- Confidence: verified (code); hypothesis (firmware sensitivity)

### DC-18
- ID: DC-18
- Title: Race window between `_stop_and_disarm` and a manual packet from the view thread; emergency 'k' written outside the serial lock
- Severity: low
- Views affected: Model / Web (server threads)
- Reference behavior: n/a (main was single-threaded).
- Actual behavior: `power_down` writes `b'k\n'` directly via `serial_comm.ser.write` (probes.py:472-475), bypassing `serial._lock` that guards all other writes (serial.py:35, 181-182, 235-237, 245-247). Under Web (server threads) or watchdog thread `disable()` vs the GUI thread's manual packet, the 'k' could interleave with a multi-byte packet. Watchdog also flips flags from another thread (probes.py:417) while the GUI thread reads them; no model lock.
- Failure scenario: FULL STOP from a Web request thread coinciding with a manual packet write corrupts one frame.
- Proposed fix direction: Add `SerialArduino.kill_coils()` that writes 'k' under `_lock`; guard `_stop_and_disarm` with a small model RLock.
- Confidence: hypothesis (needs concurrent Web+manual path, which DC-5 says does not exist in Web; only watchdog thread remains)

### DC-19
- ID: DC-19
- Title: Dropdown placeholders / web controller dropdown can call `set_controller("")`
- Severity: low
- Views affected: Web
- Reference behavior: Tk/PySide dropdowns are readonly lists of real options (tkinter/view.py:428; pyside/view.py:303-323 skips empty text).
- Actual behavior: Web select includes `<option value="">Select option...</option>` (app.js:642, 664) and the change handler dispatches `set_controller` with `[val]` even for "" (717-726). `BaseProbe.set_controller("")` (probes.py:182-192) sets `controller_var=""` and calls `poller.set_controller("")`.
- Failure scenario: User picks the placeholder; gamepad is dropped; if manual_flag was on it is auto-reverted by the per-tick check (359-364) with a warning, coils stay enabled.
- Proposed fix direction: Ignore empty values in the JS handler (and validate in `set_controller`).
- Confidence: verified (code path); effect of `poller.set_controller("")` UNVERIFIED (gamepad.py owner)

## Missing / dead controls per view (Q4)

- Removed on purpose in ALL views (schema, probes.py:127-154): per-device Enable/Disable ("System Power"), per-device Full Stop, Run Script/Select Script (framework left, `run_script` unreachable), Serial Reconnect, "Red Percent" launcher (separate device tab). Net effect vs main: only the global FULL STOP and the two mode toggles remain as stop paths.
- Dead in all views: Serial Port entry (DC-14).
- Tk: none missing vs its own schema; tab close does not tear down (DC-9); DC brake fields render (probes.py:519-520).
- PySide: `file_picker` branch is `continue` (pyside/view.py:345-346, with dead code after); fine since none in schema. Dead `disable_timer` cleanup reference (447-448).
- Web: "Controller Log Window" button dispatches `open_controller_log`, which only `print`s server-side (probes.py:194-195) -> dead in Web; manual mode dead (DC-5); Start Stepping/Set disabled in autonomous mode (DC-6); dead JS branches for `system_enabled`, "Full Stop", "Power Down" (app.js:897-905).

## Limits / clamping summary vs main (Q2)

- Numeric commit timing: Tk commit on Return/FocusOut with forced focus_set before any command (tkinter/view.py:348-361, 492); Tk non-numeric entries (only serial_port) commit per keystroke via write trace (363-370). PySide commit on editingFinished (pyside/view.py:259), no pre-command flush (DC-7). Web commit on Set/Enter only, no pre-command flush, no validation (DC-4, DC-8).
- Validation: Tk validate-on-key regex float (343) plus NaN/Inf reject (354-355), invalid -> revert (357-358). PySide QDoubleValidator(-1e9..1e9, 3 decimals) restricts to <=3 decimals and |v|<=1e9 (244), NaN/Inf can't be typed. Web none.
- Clamping: only in model `_num(..., minimum=1)` on steps/speeds (probes.py:340-343, 378-385, 526-527 none); no maxima (main's "<= 1600" label at chuck_frame.py was advisory, not enforced either; step-size "power of 2" also not enforced anywhere). Steps int-truncated (`integer=True`, numeric.py:16-17): "1.9" -> 1.
- Stale values: Tk poll skips only the focused entry (tkinter/view.py:516-523) — OK; PySide skips only entries with focus (411-412) — OK; Web only updates placeholder (849-852).
- Serial format: refactor sends floats via `_num` (e.g. `400.0`, `10.0`) where main sent raw text; UNVERIFIED HYPOTHESIS that firmware parses these identically (settle by reading the Arduino sketch's field parser).

## Teardown / emergency_stop coverage (Q3)

- `full_stop_all` iterates a snapshot and calls `emergency_stop` per model with per-model try/except (system_manager.py:70-78): good. DC/Chuck `emergency_stop` -> `power_down` covers stop packet, 'd', 'k'.
- Tk: shutdown_all on dashboard close (OK); no per-tab teardown (DC-9). Global stop is a Label with `<Button-1>` bind (tkinter/view.py:186-188) — works.
- PySide: window close cleans view timers then `shutdown_all` (OK); dock close partial (DC-2). `changeEvent` flush iterates `system_manager.active_models` directly without the lock (pyside/view.py:778) and only on WindowDeactivate.
- Web: `full_stop_all` bypasses device locks (good) (web_adapter.py:420-431); `initialize_setup` replaces manager then `old_manager.shutdown_all()` (184-185) (OK). Web mode-flag safety depends on nothing being routed (DC-5).
- `reboot_model`, `remove_model`: not used by any view for DC/Chuck (grep found no callers outside system_manager for these three models; PySide uses direct dict deletion).

## Coverage

Read fully: src/model/probes.py (1-536), src/model/numeric.py, src/model/base.py, src/model/system_manager.py, src/views/web/web_adapter.py (all), src/views/tkinter/view.py 108-590 (DashboardWindow, notebook, DynamicView), src/views/pyside/view.py 121-455 and 719-967, src/views/web/static/js/app.js 495-750, 795-1000, 1060-1235, src/views/web/web_view.py 40-100, main:src/chuck_frame.py 97-320 and 440-700 (diff against main:src/stepper_frame.py shows only title/step defaults/labels), main:src/DC_frame.py (grep only), src/controller/serial.py 100-275 (for lock/format context), src/controller/gamepad.py 340-372, 455-514 (context only).
Not read / not done: docs/architecture/*.md (not diffed for errors); static/index.html and CSS; web_server.py beyond route lines 86-225; app.py/app_bootstrap.py teardown on setup-window close; firmware sketch; style.qss; RedPercent code; StepperProbe/BaseProbe stepping internals, gamepad and serial internals (owned by others). Did not run the app or tests. Tk `after`-loop behavior after dashboard destroy was not verified and is not claimed.

DONE dc-chuck 19
