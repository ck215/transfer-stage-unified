# Audit: gamepad (ControllerPoller, BaseGamepad family, controller binding in probes + 3 views)

Repo: /Users/ianalbinogonzalez/Documents/GitHub/transfer-stage-unified/mvc-refactor (branch mvc-refactor). Read-only audit; all line refs verified this run.

## Object summary

- **Constructor site**: `BaseProbe.__init__` `src/model/probes.py:36-41` builds `ControllerPoller(controller_id, self.active_claims, self.__class__.__name__)` inside a `try/except Exception` (failure -> `self.poller = None`, headless). Only StepperProbe / DCProbe / ChuckPositioner (subclasses at probes.py:493-536) have pollers. Second construction site: `BaseProbe.set_controller` fallback `probes.py:188-192` (only when `self.poller` is None).
- **Model construction callers**: Tk `src/app.py:402` (`build_models(..., self.active_claims)` where `active_claims` is a SetupWindow attribute created once, app.py:100), PySide `src/app.py:731` (attribute app.py:521) and lazily re-created in `views/pyside/view.py:886-892` with `active_claims={}`, Web `views/web/web_adapter.py:145-148` (fresh dict per `initialize_setup`).
- **Owner**: the probe (1 probe : 1 poller). Claims dict is shared per launch, keyed by class name (`"StepperProbe"` etc.), never cleaned by `close()`.
- **Teardown path**: `BaseProbe.teardown()` `probes.py:479-487` -> `poller.stop_polling()`, `poller.close()` (gamepad.py:494-513), `power_down()`, `serial_comm.close()`. Reached via `SystemManager.shutdown_all` (`model/system_manager.py:60-68`): Tk `DashboardWindow.on_close` (tkinter/view.py:226-231), PySide `closeEvent` (pyside/view.py:957-966), Web `WebDashboardWindow.close` (web_view.py:74-78) and `initialize_setup` old-manager teardown (web_adapter.py:184-185). PySide per-dock close does NOT use it (see GAMEPAD-9).
- **pygame global state**: `_ensure_pygame_video()` = `pygame.init()` (gamepad.py:23-36). Call sites, all verified:
  - gamepad.py:36 (module import; runs at first probe construction because `probes.py:38` imports lazily), :327 (`get_physical_controllers`), :419 (`_initialize_pygame_joystick`), :513 (end of `close()`).
  - `pygame.joystick.init()`: gamepad.py:329, 334, 423 (with SDL video-error swallowing 424-429).
  - `pygame.quit()`/`joystick.quit()`: gamepad.py:506-507 (`close()`, when refcount <= 0) and :362 (`connect_controller`, dead code, GAMEPAD-17). No other `pygame.quit` in `src` (grep verified).
  - `app.py`: Tk setup `app.py:68-77` (env + `pygame.init()` at function scope), `get_available_controllers` `app.py:136-153` (`pygame.init`, `joystick.init`, `event.pump`, `Joystick(i)` objects never released); PySide setup `app.py:448-458`, `app.py:536-546` (also calls `js.init()`). None ever quit.
  - Web: `web_adapter.py:57-70` enumerates in a **separate `python3 -c` subprocess** (different interpreter possibly; comma-split names); in-process init happens only via gamepad.py at model build.
  - Refcount: module globals `_poller_lock`, `_active_poller_count` gamepad.py:18-19; `+=1` only at gamepad.py:451-453 (every successful bind, including re-binds by the same poller); `-=1` only at :499-502 (once per poller, `_closed` guard :495-497, even if the poller never bound anything). This is the root of GAMEPAD-2.
- **Polling driver per view**:
  - Tk: `DashboardWindow.__init__` calls `view.start_polling(self)` (tkinter/view.py:219-220) -> `DynamicView.start_polling` (view.py:542-564): `poller.start_polling(dashboard_window, log_updater=print, activity_callback=model.touch_activity)`; pygame poll = `gui_root.after(5, _poll_loop)` (gamepad.py:637-638; 5 ms, comment on :253 says 50 Hz / 20 ms, stale); manual input route = `self.after(50, _route_input)` (view.py:553-564). Stops: only via `teardown()` on dashboard close. Tab close (`DraggableClosableNotebook.close_tab`, view.py:158-162) with `on_close_tab_callback` never assigned (only `= None` at :120, grep verified) just `notebook.forget(index)`: model, poller, and both `after` chains keep running hidden.
  - PySide: `QtDynamicView.__init__` (pyside/view.py:155-184): `GUIAdapter.after -> QTimer.singleShot` (157-159) is passed as `gui_root`; `QTimer(20 ms)` `_route_input` (171-184). Stops in `cleanup()` (437-453) called from `close_device_view` (826-829) and `closeEvent` (959-965).
  - Web: **nothing**. `grep start_polling|get_mapped_state|send_manual` over `views/web/*.py` and `static/js/app.js` returns nothing; only `web_view.py:55-65` assigns `poller.log_updater`. See GAMEPAD-1.

## Findings

### GAMEPAD-1
- **Title**: Web view never starts the poller or routes manual input; manual mode is inert, toggle sticks ON, coils stay energized with watchdog deferred
- **Severity**: high
- **Views affected**: Web
- **Reference behavior**: Tk starts poller (tkinter/view.py:550) and a 50 ms route loop that calls `send_manual_mode_command(poller.get_mapped_state())` (view.py:553-564). PySide same (pyside/view.py:169-184). main started polling on entering manual mode and ran `_manual_mode_loop` every 5 ms (`git show main:src/stepper_frame.py` lines 581-608, 634-640).
- **Actual behavior**: No web code calls `poller.start_polling`, and nothing calls `model.send_manual_mode_command` (grep of web_adapter.py/web_server.py/web_view.py/app.js). `ControllerPoller.get_mapped_state()` returns `{}` while `is_polling` is False (gamepad.py:517-518), and `_poll_loop` can only self-reschedule through `gui_root.after` (gamepad.py:637-640), so even a bare `start_polling()` would run exactly once and stop. `BaseProbe.enter_manual` (probes.py:226-243) only checks `poller.gamepad`, then `enable()` (sends hardware enable, starts interlock watchdog) and sets `manual_flag=True`. The watchdog skips while `manual_flag` (probes.py:409-412), so the 5-minute idle disable never fires.
- **Failure scenario**: Web user with a controller bound clicks "Enter Manual Mode": UI shows "MANUAL MODE (Click to Stop)", coils energized, stick input does nothing, the model never auto-disables; if user then swaps/unplugs the controller nothing ever clears `manual_flag` (see GAMEPAD-4). Red Percent `vel_*` logging also reads 0 (GAMEPAD-15).
- **Proposed fix direction**: Give the web adapter its own driver: a daemon thread (or per-probe thread started in `initialize_setup` after `build_models`) that runs `poller.pump_once()` at ~5 ms plus a 20-50 ms manual-route tick calling `model.send_manual_mode_command(...)` under the device lock (`_get_device_lock`). Requires refactoring `ControllerPoller` so the poll body is a scheduler-agnostic `poll_once()` and `_poll_loop` merely reschedules (Tk/Qt) - e.g. `start_polling(scheduler=callable)` instead of the `gui_root.after` duck-typing. Better still, move the "route input while manual_flag" tick into the model (`BaseProbe.tick_input()`), leaving each view to only supply a timer. Also have `enter_manual` refuse when poller `is_polling` is False.
- **Confidence**: verified (absence by grep; behavior by reading gamepad.py:517-518, 637-640, probes.py:226-243, 409)

### GAMEPAD-2
- **Title**: `_active_poller_count` refcount is not a poller count; closing a never-bound poller can `pygame.quit()` under a live one, and swaps leak the count
- **Severity**: high
- **Views affected**: Controller (visible in PySide dock close/reopen, Web re-setup, Tk any teardown)
- **Reference behavior**: main `close()` quits pygame once at app exit (`git show main:src/controllerDrive.py` `close`); each device was a separate process. Commit 05c2e69 intent: "only call the pygame-level teardown when the last poller closes".
- **Actual behavior**: increment happens only on successful bind (gamepad.py:451-453) and on **every** successful bind including re-binds by the same poller (set_controller/change_controller call `_initialize_pygame_joystick`); decrement happens in `close()` for every poller once (gamepad.py:499-502), including pollers that never incremented (constructed with "None"/failed) . `count <= 0` (:504) then calls `pygame.joystick.quit(); pygame.quit()` (:506-507). `docs/architecture/controllers.md` describes the variable as "how many pollers currently exist"; wrong.
- **Failure scenario**: PySide with Stepper bound to "ID 0" (count=1) and Chuck Positioner on "None" (count contribution 0). User unchecks/closes the Chuck dock -> `cleanup()` (pyside/view.py:451-453) -> `close()` -> count 0 -> pygame quit; Stepper's `Joystick` object is now stale, next `get_axis` raises `pygame.error`, caught by `_poll_loop`'s generic except (gamepad.py:630-635) -> "Gamepad Polling Error" popup + `_handle_disconnect` -> Stepper controller lost mid-run. Inverse: Stepper swapped controllers 3 times then closed leaves count=2 forever (pygame never quit; harmless leak).
- **Proposed fix direction**: Track bound state per poller (`self._counted: bool`); increment only when transitioning unbound->bound, decrement in `_handle_disconnect`/unbind/close only if `_counted`. Or drop the refcount and never call `pygame.quit()` except at process exit (`atexit`), since `_ensure_pygame_video` already makes init idempotent. Add tests for {bound+none} closing order.
- **Confidence**: verified (logic read at gamepad.py:451-453, 495-513); runtime effect of stale Joystick after `pygame.quit()` is standard pygame behavior but not executed here.

### GAMEPAD-3
- **Title**: Swap to a controller that fails to bind (or to none) does not full-stop; model flag flips lazily and hardware stays enabled; `controller_var` is not reverted
- **Severity**: medium
- **Views affected**: Model, Tkinter, PySide6, Web
- **Reference behavior**: main `on_controller_dropdown_selected` (`git show main:src/stepper_frame.py` 405-420): `if not success or "None" in selected: ... if self.manualFlag: self.full_stop_button()`. main `_manual_mode_loop` (581-596) also runs `full_stop_button()` and re-`enable_button()` when joystick is None.
- **Actual behavior**: `BaseProbe.set_controller` (probes.py:182-192) sets `self.controller_var = controller_id` (:184) unconditionally *before* the bind, ignores the boolean result of `poller.set_controller`, never touches `manual_flag`, never full-stops. On failure `_initialize_pygame_joystick` sets `gamepad=None` (gamepad.py:413/375/394 or via `_handle_disconnect` :319). The only place `manual_flag` is cleared is the defensive check in `send_manual_mode_command` (probes.py:359-364), which merely sets the Python flag False (no `full_stop`, `system_enabled` stays True, watchdog keeps running, no `send_stop_command`) and only runs if a view calls it (Tk view.py:557-561, PySide view.py:177-181; Web never, GAMEPAD-1).
- **Failure scenario**: Manual mode on with controller A; user picks B which is claimed by another probe (gamepad.py:398-415), is not present, or is an unsupported name (`get_gamepad_wrapper` raises, gamepad.py:249). Poller goes to None; within 50 ms (Tk) / 20 ms (PySide) `manual_flag` silently flips off with a "Manual Mode Blocked" popup on top of "Controller Claim Conflict"/"Joystick Not Found" + "Controller Disconnected" popups; the dropdown still displays B (controller_var) although `active_claims` says "None Detected"; motors remain enabled (`system_enabled` True) until 5-minute idle disable.
- **Proposed fix direction**: In `BaseProbe.set_controller`: capture `ok = self.poller.set_controller(id)`; if `not ok` and (`manual_flag`) call `self.full_stop()`; on `not ok` set `controller_var` back to the last good value (or "None") and return ok. Also make `send_manual_mode_command`'s no-gamepad branch call `full_stop()` rather than only clearing the flag. Mirror main's behavior explicitly.
- **Confidence**: verified

### GAMEPAD-4
- **Title**: Reported bug "manual-mode toggle desyncs after controller swap": exact per-view trace (known-issues hypothesis is only partly right)
- **Severity**: medium
- **Views affected**: Tkinter, PySide6, Web
- **Reference behavior**: main: toggle and manualFlag are one variable and swap failure calls `full_stop_button()` (stepper_frame.py:414-415).
- **Actual behavior / trace**:
  - **Common model path**: dropdown -> `BaseProbe.set_controller` (probes.py:182) -> `ControllerPoller.set_controller` (gamepad.py:350) -> `_initialize_pygame_joystick` (:370): `stop_polling()` (:371), claim check (:398-415), `Joystick()` (:440), `self.gamepad = get_gamepad_wrapper(...)` (:447), `_latch_state.clear()` (:448-449); on success `start_polling` again if `gui_root` set (:354-355). `manual_flag` is never touched (probes.py:182-192). Toggle widgets render `model.manual_flag` by polling.
  - **Tk**: `DynamicView._poll_model` (tkinter/view.py:512-540) re-renders toggle text every 50 ms from `model.manual_flag`; `set_controller` runs synchronously inside the `<<ComboboxSelected>>` handler (view.py:431-438) with no event-loop yield (Tk ErrorPopupManager only *queues* popups, view.py:70-93, 44-48), so the "transient falsy gamepad during rebuild" hypothesis in docs/architecture/known-issues.md:39-55 cannot occur on a successful swap. Successful swap: `manual_flag` stays True, toggle correctly stays "MANUAL MODE (Click to Stop)"; input continues with new pad. Failed swap: gamepad becomes permanently None; `_route_input` (view.py:553-564) -> `get_mapped_state()` returns `{}` -> `send_manual_mode_command({})` flips `manual_flag` False (probes.py:359-360); toggle flips to "Enter Manual Mode" <= 50 ms later. Model and toggle stay consistent but hardware is left enabled (GAMEPAD-3).
  - **PySide**: `QComboBox.currentTextChanged` handler calls `set_controller` synchronously (pyside/view.py:311-323). `QtErrorPopupManager` emits the signal from the GUI thread with a direct connection so `QMessageBox.warning` runs a **nested modal event loop inside the swap** (pyside/view.py:36-79), during which the 20 ms `_route_input` timer fires. On the failure path `gamepad` is still the OLD wrapper while `is_polling` is False (stop_polling ran at :371), so `get_mapped_state` returns `{}` and `send_manual_mode_command({})` is called with `manual_flag` still True, then after the dialog gamepad becomes None and the flag flips on the next tick (extra "Manual Mode Blocked" dialog). `_poll_model` toggle sync (pyside/view.py:421-435) keys on `widget.property("toggle_state")` and re-polishes; this is consistent with the model. So PySide has a real transient window but only on the failure path.
  - **Web**: `pollState` toggles from `attrs.manual_flag` (app.js:854-866) so the *display* matches the model, but nothing ever clears `manual_flag` after a swap/unbind (no route loop, GAMEPAD-1), so after `set_controller` to a bad/none/placeholder value the button shows "MANUAL MODE (Click to Stop)" indefinitely with no bound controller. This is a true model/reality desync and is the only view where a swap leaves the toggle "wrong" persistently.
  - **Not explained by static reading**: the original reported sequence (swap then click "Enter Manual Mode" behaves oddly) on a *successful* swap in Tk/PySide. The button still reads "MANUAL MODE (Click to Stop)" so a click calls `full_stop()` (probes.py:197-201), which is "wrong" only relative to the user's expectation that swap reset the mode. Plausible actual trigger: swap target failed to bind (index renumbering after re-plug, claim conflict, unsupported name such as GAMEPAD-11 in reverse) so the flag flipped lazily.
- **Failure scenario**: see above.
- **Proposed fix direction**: (1) apply GAMEPAD-3 fix so the model clears/stops deterministically at swap time rather than lazily; (2) decide semantic: on any successful swap while manual, either keep manual (document) or exit manual+neutral; (3) add targeted logging in `set_controller` (before/after `manual_flag`, `poller.gamepad`, `is_polling`) to settle the unproven success-path case. Suggested settle-check: run Tk view with two pads, swap A->B while manual, log `manual_flag` at gamepad.py:447 and probes.py:359 (expect no flip).
- **Confidence**: verified for traces; success-path Tk/PySide cause = UNVERIFIED HYPOTHESIS (needs the logging above).

### GAMEPAD-5
- **Title**: Controller dropdown regressions vs main: no "None" entry, no claim filtering, no live refresh, no rebind of the same controller after disconnect
- **Severity**: medium
- **Views affected**: Tkinter, PySide6, Web
- **Reference behavior**: main `_update_controller_dropdown_loop` (stepper_frame.py:364-401): every 500 ms options = ["None"] + physical controllers not claimed by other processes; selection shown = `active_claims[process]`. Selecting "None" full-stops (415).
- **Actual behavior**: options come from `poller.get_physical_controllers()` only (gamepad.py:325-348; no "None", no claim filtering) plus the current value prepended (Tk view.py:421-425, PySide view.py:297-301); built once, manual ⟳ rescan only (Tk view.py:440-453, PySide view.py:325-343; PySide rescan also blocks signals so it never rebinds). Web fetches options once per select (`populated` flag, app.js:653-669) and has no rescan control. After `_handle_disconnect` (gamepad.py:315-323) `controller_var` still shows the old ID while claims say "None Detected". Re-selecting the *same* entry to reconnect works in Tk (`<<ComboboxSelected>>` fires) but not in PySide (`currentTextChanged` is not emitted for unchanged text) nor Web (`change` event not fired). There is no periodic reconnect and `connect_controller` (gamepad.py:358) is dead code (GAMEPAD-17).
- **Failure scenario**: PySide: controller unplugged -> warning popup -> replugged; user cannot rebind Stepper's controller without first selecting a different item and back; nobody can ever choose "None" to release a controller for another probe (claim stays set, see GAMEPAD-10).
- **Proposed fix direction**: Add a model-level `get_available_controllers()` that returns `["None"] + physical` minus claims held by other probes (keep current), refresh on a timer (~500 ms like main) or on dropdown open; use `activated`/`textActivated` (PySide) and re-fire on same value; expose a "Reconnect" schema button that calls a model method wrapping `poller.set_controller(current)`.
- **Confidence**: verified

### GAMEPAD-6
- **Title**: Web dropdown placeholder option dispatches `set_controller("")` which silently unbinds; `controller_var` is directly writable via `set_attr`
- **Severity**: low
- **Views affected**: Web
- **Reference behavior**: Tk/PySide dropdowns have no empty option (`current_val` prepended only if truthy, view.py:424-425 / 300-301; PySide handler ignores empty text, pyside/view.py:313-314).
- **Actual behavior**: web select renders `<option value="">Select option...</option>` (app.js:639-644), change handler dispatches `set_controller([""])` for `val=""` (app.js:716-727); `_initialize_pygame_joystick("")` falls into "no numeric id" -> `gamepad=None`, claims "None Detected", returns False (gamepad.py:373-396) without any warning. Also `set_device_attribute` allows setting `controller_var` (schema model_attr, web_adapter.py:346-356, 448) without rebinding.
- **Failure scenario**: user clicks the placeholder -> controller silently unbound; manual toggle stays ON (GAMEPAD-1/4).
- **Proposed fix direction**: mark placeholder `disabled`/`hidden`, ignore empty values in the JS handler and in `BaseProbe.set_controller`; exclude `controller_var` from the writable-attrs allowlist for dropdown elements with a `command`.
- **Confidence**: verified

### GAMEPAD-7
- **Title**: Every successful swap starts a second `_poll_loop` chain (loop-generation race)
- **Severity**: low
- **Views affected**: Tkinter, PySide6 (same class)
- **Reference behavior**: main `change_controller` -> `_initialize_pygame_joystick`, polling restarted by the next manual-mode entry only (stepper_frame.py:405-408, 634-640).
- **Actual behavior**: `_initialize_pygame_joystick` calls `stop_polling()` (gamepad.py:371, flag False) and `set_controller` immediately calls `start_polling` -> `_poll_loop()` directly (gamepad.py:354-355, 486-488) setting the flag True again. The previously scheduled `after`/`singleShot` callback (<= 5 ms pending) later runs `_poll_loop`, sees `is_polling` True (:573) and re-arms itself (:637-638). Result: N swaps -> N+1 concurrent chains, no identity/generation token.
- **Failure scenario**: extra pump/`get()` work per swap (200 Hz x N); no data corruption because chains share `prev_*` state, but log/activity callbacks may fire from whichever chain sees the change first.
- **Proposed fix direction**: keep a `self._loop_gen` counter incremented in `stop_polling`/`start_polling`; each scheduled callback captures its generation and exits if stale. Or hold a single re-armed timer handle.
- **Confidence**: verified by reading; not executed.

### GAMEPAD-8
- **Title**: `flush_neutral()` (focus-loss safety) is effectively a no-op and, on Linux Xbox, writes -1.0 into the Y stick axis
- **Severity**: medium
- **Views affected**: Tkinter, PySide6 (callers: tkinter/view.py:176-182, pyside/view.py:776-781); Controller
- **Reference behavior**: main has no flush; intent in refactor: stop motion when window loses focus.
- **Actual behavior**: `flush_neutral` rewrites `gamepad.prev_axis_states` (gamepad.py:557-570) but `_poll_loop` overwrites `prev_axis_states[i]` from the hardware every 5 ms (gamepad.py:596-607, `ALLOW_BACKGROUND_EVENTS` set at :3 keeps reading a held stick), so a held stick is restored within one poll tick. Button/hat caches are not reset at all (loop only, :612-628); only `_latch_state` is cleared. Additionally the reset treats axes `(2, 4, 5)` as triggers idle at -1.0 (:565-566) regardless of platform, but on Linux `XboxGamepad` axis 4 is the right-stick **Y** (`y_axis = 4`, gamepad.py:115) -> Y momentarily maps to -1.0 (full-speed jog) until the next poll tick.
- **Failure scenario**: Linux + Xbox, manual mode, user alt-tabs: `flush_neutral` sets Y = -1.0; if the poll loop is on the same thread this is corrected in <= 5 ms but the 50 ms/20 ms route loop can read the flushed value in between (order-dependent) and send a full-speed Y packet. Nothing actually prevents motion while the stick is physically held, contradicting the feature's purpose.
- **Proposed fix direction**: implement flush as a "gate" flag (`self._flushed = True`) that makes `get_mapped_state()` return neutral (0 / -1.0 per mapped key via the wrapper, not raw axis numbers) until the next physical change or a re-arm on window re-focus; use per-wrapper `idle_state()` instead of the hard-coded `(2,4,5)` set.
- **Confidence**: verified (code); Tk `FocusOut` semantics on child-widget focus changes UNVERIFIED HYPOTHESIS (event.widget == self check at tkinter/view.py:177 may or may not filter inferior-focus events; check by logging).

### GAMEPAD-9
- **Title**: Lifecycle divergence on device close: Tk tab close is a no-op hide; PySide dock close hand-rolls teardown (no `teardown()`, serial never closed, claims not released) and reopens with `None` port/controller and a fresh claims dict
- **Severity**: high
- **Views affected**: Tkinter, PySide6
- **Reference behavior**: ManagedModel contract `teardown()` (probes.py:479-487) via `SystemManager.remove_model`+`_teardown_model`/`shutdown_all` (system_manager.py:18-45, 60-68).
- **Actual behavior**:
  - PySide `close_device_view` (pyside/view.py:813-847): `widget.cleanup()` (already `stop_polling`+`close`, 451-453), `model.disable()`, `poller.stop_polling(); poller.close()` again (no-op), `hasattr(model,'disconnect')` (probes define no `disconnect`; only rotator_system.py:108 / temperature_system.py:194 do), then `del active_models[...]` directly. `serial_comm.close()` is never called for probes; `power_down`/`emergency_stop` never sent (only `disable()` -> `_stop_and_disarm`, probes.py:442-465). Bypasses `remove_model`/`teardown`.
  - PySide reopen (`open_device_view`, pyside/view.py:881-893) constructs `StepperProbe(None, "None", {})` etc.: no serial port, no controller, and a private claims dict so cross-probe collision detection (gamepad.py:398-415) does not see the other pollers. The port/controller chosen in setup are lost.
  - Tk `close_tab` (tkinter/view.py:158-162) -> `forget(index)`; `on_close_tab_callback` never assigned; hidden tab's model, poller (5 ms loop), `_route_input`, `_poll_pos`, `_poll_stat` loops keep running with no UI; manual mode can stay engaged unseen.
- **Failure scenario**: PySide: close Stepper dock (serial handle leaks; port stays busy so "reopen" cannot reattach even if it tried), reopen -> dead, port-less probe that can still be commanded; close a dock of a bound probe and count semantics (GAMEPAD-2) may kill another probe's joystick. Tk: middle-click a tab while manual mode is on: input continues invisibly.
- **Proposed fix direction**: Use `system_manager.remove_model(name)` + `_teardown_model` (or a public `close_model(name)`) in both views; for reopen call `system_manager.reboot_model(name, constructor, port, controller, claims)` with the original config kept by the manager; wire Tk `notebook.on_close_tab_callback` to the same path; release `active_claims[process_name]` in `ControllerPoller.close()` (GAMEPAD-10).
- **Confidence**: verified (serial internals not audited)

### GAMEPAD-10
- **Title**: `active_claims` entries are never released; Tk relaunch inherits stale claims and can spuriously block a legitimate bind
- **Severity**: medium
- **Views affected**: Tkinter (persistent claims dict), Controller
- **Reference behavior**: main claims lived in a Manager dict for the launch; unclaimed on process exit.
- **Actual behavior**: `close()` (gamepad.py:494-513) and `_handle_disconnect` only write `"None Detected"` on disconnect (:320); `close()` leaves `active_claims[process_name]` = last ID. Tk `SetupWindow.active_claims = {}` is created once (app.py:100) and passed to every launch (app.py:402); dashboard close returns to setup (tkinter/view.py:228-231) and next launch reuses it.
- **Failure scenario**: Run 1: Stepper->"ID 0", DC->"ID 1". Close dashboard, relaunch with only DC Probe on "ID 0": stale `"StepperProbe": "ID 0: ..."` claim makes DC's bind fail with "Controller Claim Conflict" (gamepad.py:409-415) and DC gets `gamepad=None`.
- **Proposed fix direction**: `close()` should `active_claims.pop(process_name, None)`; Tk should create a fresh dict per launch (`build_models(..., {})`).
- **Confidence**: verified

### GAMEPAD-11
- **Title**: Gamepad whitelist is much looser than main's exact-name list; foreign pads silently get Xbox axis layouts
- **Severity**: medium (hardware motion with wrong axes)
- **Views affected**: Controller (all views)
- **Reference behavior**: main accepted exact names (`"Xbox Series X Controller"`, `"T.16000M"`, `"Thrustmaster T.16000M"`, `"Logitech Gamepad F310"`, Windows names starting `"Controller"`), raising `ValueError("Unsupported joystick detected!")` otherwise; Linux Xbox with unrecognized bus GUID also raised (`git show main:src/controllerDrive.py`).
- **Actual behavior**: `get_gamepad_wrapper` (gamepad.py:236-249) matches substrings: `"thrustmaster"` (any Thrustmaster device), `"f310"`/`"dual action"`, `"wireless"` (any wireless pad, e.g. PS4/PS5 "Wireless Controller" -> `BluetoothXboxGamepad`), `"controller"`/`"xbox"`/`"x-box"` catch-all -> Xbox. On Linux any device whose GUID[1:2]=='5' is treated as Bluetooth-Xbox (:245). Commit 39e3a51 claims the whitelist was "reinstated", but it is only reinstated for names containing none of those substrings.
- **Failure scenario**: user binds a DualShock ("Wireless Controller"); mapping (axes 3/4/5, buttons 6/7 on Linux BT) does not match its layout -> unexpected jog direction/speed on real stage with no error.
- **Proposed fix direction**: restore an explicit allowlist (exact names or (name, platform) table) with the substring rules only as documented aliases; keep raising `ValueError`; add a test that "Wireless Controller"/"Nintendo Switch Pro Controller" raise.
- **Confidence**: verified (code); the actual DualShock axis layout mismatch is standard knowledge, not executed.

### GAMEPAD-12
- **Title**: T16000M mapping differs from main: Z up/down is swapped for the "Thrustmaster T.16000M" name, and bumpers read buttons 4/5 instead of 7/9
- **Severity**: medium
- **Views affected**: Controller
- **Reference behavior**: main binds `[X, Y, Z+(R), Z-(L), LBumper, RBumper]`: `"T.16000M"` -> `[0,1,9,10,7,9]`, `"Thrustmaster T.16000M"` -> `[0,1,10,9,7,9]` (controllerDrive.py, `case "T.16000M"` / `case "Thrustmaster T.16000M"`); `get_controller_params` uses `controller_binds[2]` for `z_axisStatusR`, `[3]` for `z_axisStatusL`, `[4]/[5]` for bumper buttons (stepper_frame.py:646-660).
- **Actual behavior**: one class for both names: `z_l=10`, `z_r=9` (gamepad.py:223-224) = the Windows-name variant only; bumpers `prev_button_states.get(4, prev_button_states.get(7,0))` / `get(5, get(9,0))` (gamepad.py:232-233): the fallback is never used because buttons 4 and 5 exist on a 16-button T16000M, so bumpers come from buttons 4/5. Virtual axes also changed from raw 0/1 to 2x-1 remap (gamepad.py:228-229) versus main's raw button values (a deliberate cleanup but a behavior change: pressed = +1.0, idle = -1.0 instead of 1.0/0.0).
- **Failure scenario**: on Linux/MINT where pygame reports "Thrustmaster T.16000M", the throttle-button Z direction is inverted relative to main; bumper/step buttons differ.
- **Proposed fix direction**: split `T16000MGamepad` by name (or pass a bind table) and use buttons 7 and 9 for bumpers; verify on hardware (user judgment, hardware verification stays direct).
- **Confidence**: verified (code comparison); hardware truth UNVERIFIED.

### GAMEPAD-13
- **Title**: D-pad sign convention changed from main (negation removed) on both axes
- **Severity**: medium
- **Views affected**: Controller
- **Reference behavior**: main `get_hat_edge` returns `(-out_x, -out_y)` (controllerDrive.py end) and is what `get_controller_params` sends as `dpad_LR/UD` (stepper_frame.py:647,654-655); firmware moves `x_axis.move(dpad_LR*x_step_size)`, `y_axis.move(dpad_UD*y_step_size)` (firmware/stepper_firmware/stepper_firmware.ino:475-476).
- **Actual behavior**: refactor passes `hat` unnegated (gamepad.py:126-127, 147-148, 182-183, 203-204, 230-231) after commit 05c2e69 "correct D-pad inversion ... against the existing hardware contract test". The test (`tests/hardware/test_gamepad.py:32-34`) is a mock unit test written in the refactor, not a physical verification.
- **Failure scenario**: D-pad right/up moves the stage the opposite way from main if the firmware axis sign convention was calibrated to main's negated input.
- **Proposed fix direction**: do not change code blindly; owner should confirm on the physical stage which direction D-pad right produces in main vs refactor, then encode the sign in one named constant with a comment.
- **Confidence**: hypothesis (physical check required; code difference itself is verified)

### GAMEPAD-14
- **Title**: Deadzone/trigger-snap behavior differs from main and is applied twice; T16000M 0.03 deadzone intent never worked in main either
- **Severity**: low
- **Views affected**: Controller
- **Reference behavior**: main: single deadzone 0.1 in `_poll_loop` (`DEADZONE = 0.1`); `DEADZONE = 0.03` assigned inside `_initialize_pygame_joystick` for T16000M is a function-local assignment with no effect.
- **Actual behavior**: raw axes zeroed below 0.1 in `_poll_loop` (gamepad.py:596-597, this also zeroes mid-travel trigger values in (-0.1,0.1)), then x/y zeroed again below 0.12 in `get_mapped_state` (:529-531), and triggers < -0.9 snapped to -1.0 (:533-536). Effective stick deadzone is 0.12 for all pads (not 0.1, not 0.03), and the log/activity callback threshold in the loop (0.1) differs from the value sent to hardware (0.12).
- **Failure scenario**: minor loss of low-speed fine-jog range vs main; stick values between 0.10-0.12 produce "Axis changed" logs and `touch_activity()` but send 0.
- **Proposed fix direction**: one `DEADZONE` per wrapper class (T16000M 0.03 if desired), applied in exactly one place (mapped state), leave raw poll values undeadzoned.
- **Confidence**: verified

### GAMEPAD-15
- **Title**: `BaseProbe.vel_x/y/z` call `poller.get_mapped_state()` from the Red Percent background thread, consuming edge latches and racing the GUI thread; `vel_z` sign is opposite to the serial combine
- **Severity**: medium
- **Views affected**: Model (redpercent_system), Controller; all views
- **Reference behavior**: main polled edges only in the manual loop (`get_hat_edge`/`get_button_edge`, single caller).
- **Actual behavior**: `vel_*` properties (probes.py:70-94) invoke `get_mapped_state()` (gamepad.py:515-555) which mutates `_latch_state` (:538-553). `RedPercentSystem` reads them from its monitoring thread (`redpercent_system.py:316,320,324`, loop with `time.sleep(0.016)` at :335) concurrently with the view's route timer. Whichever caller reads first sets `_latch_state[key] = current_val`; the other then sees "unchanged" and reports 0, so a D-pad/bumper edge can be swallowed and the manual jog step lost. No lock guards `_latch_state`. Separately `vel_z = (r - l) / 2.0` (probes.py:93) while serial combines `z_up(L) - z_down(R)` = `(L-R)/2` (controller/serial.py:198-209 "UP (L) is positive"), so logged Z velocity has the opposite sign of commanded motion.
- **Failure scenario**: Red Percent logging active while user taps D-pad in manual mode -> occasional missed steps; velocity CSV Z column sign inverted; in Web always 0 (GAMEPAD-1).
- **Proposed fix direction**: make the edge latch private to the manual-route consumer (separate `read_edges()` from `read_levels()`), and let `vel_*` read levels only; guard shared state with a lock; fix `vel_z` sign (or document convention) after checking the Red Percent consumer.
- **Confidence**: verified (sign check reads serial.py only at the cited lines, no serial audit)

### GAMEPAD-16
- **Title**: Input cadence and polling scope changed from main: continuous 200 Hz pygame poll independent of mode, Tk route at 50 ms (main 5 ms)
- **Severity**: low
- **Views affected**: Tkinter, PySide6
- **Reference behavior**: main polled only while in manual mode (`start_polling` at `enter_manual_mode_button`, stepper_frame.py:634-640; `stop_polling` on full stop :561) and sent manual packets every 5 ms (`root.after(5, ...)` :608).
- **Actual behavior**: poller runs from dashboard creation regardless of mode (tkinter/view.py:550, pyside/view.py:169); Tk route loop `after(50)` (view.py:563), PySide 20 ms timer (pyside/view.py:184). `activity_callback=touch_activity` fires for any gamepad activity even when not in manual mode, resetting the idle watchdog (probes.py:396-397, 413). Edge latch (gamepad.py:541-553) compares only what the 5 ms poll last stored, so a D-pad tap shorter than the route interval (<50 ms Tk) is lost (main's 5 ms loop could not miss it).
- **Failure scenario**: quick D-pad tap in Tk manual mode is dropped; bumping the stick while idle keeps stepper enabled beyond the 5-minute interlock.
- **Proposed fix direction**: latch pressed edges inside `_poll_loop` (set a "pending edge" on press, cleared on read) instead of comparing at read time; gate `activity_callback` on `manual_flag`; unify route interval (<=20 ms).
- **Confidence**: verified

### GAMEPAD-17
- **Title**: Dead / unreachable / inconsistent paths in gamepad.py and probes.py
- **Severity**: low
- **Views affected**: Controller, Model
- **Reference behavior**: n/a
- **Actual behavior**:
  - `connect_controller` (gamepad.py:358-368) and `change_controller` (:466-472) have no callers in `src` (grep verified); `set_controller` (:350-356) is the live duplicate. `connect_controller` would call `pygame.quit()` unconditionally (:362), bypassing the refcount, and does not re-arm video afterwards except via `_initialize`.
  - `get_mapped_state`'s `except pygame.error` (gamepad.py:523-527) is effectively unreachable (wrapper `get_mapped_state` only does dict lookups, gamepad.py:102-130 etc.) and if reached it sets `gamepad=None` without resetting the claim like `_handle_disconnect`; also `pygame.error` raises `AttributeError` if `pygame is None`.
  - `probes.py:188-192` (fallback poller creation) is only reachable if `ControllerPoller(...)` raised at construction; the view already decided (at build) not to start polling/route input for that probe (pyside/view.py:156, tkinter/view.py:544), so a poller created here never polls (`gui_root` None -> `set_controller` does not start it, gamepad.py:354).
  - `_poll_loop` re-scheduling (`gamepad.py:637-640`) is outside the try block: a `TclError` from a destroyed `gui_root` propagates.
  - `POLL_INTERVAL = 5` with comment "50 times per second (1000ms / 20ms)" (gamepad.py:253-254) is wrong (200 Hz).
  - `app.py:3` `parse_controller_id` unused (grep: no callers) - already in docs.
- **Proposed fix direction**: delete or wire dead methods; fix comment; move re-arm into try/except that calls `_handle_disconnect`.
- **Confidence**: verified

### GAMEPAD-18
- **Title**: Web enumerates controllers in a subprocess with fragile parsing; Web HTTP threads call pygame directly
- **Severity**: low
- **Views affected**: Web
- **Reference behavior**: Tk/PySide enumerate in-process via `pygame.joystick` (app.py:136-153, 536-548).
- **Actual behavior**: `scan_hardware` (web_adapter.py:57-70) shells out to `python3 -c ...`, splits on `,` (a device name containing a comma corrupts indices), assumes `python3` is the venv interpreter, falls back to "Virtual Controller A/B" (which `_initialize_pygame_joystick` treats as None, gamepad.py:373). After setup, `resolve_options`/`dispatch_command('set_controller')` run `pygame.joystick`/`pygame.event.pump()` (gamepad.py:325-348) on `ThreadingHTTPServer` worker threads (web_server.py:296, 332) under a per-device lock only; other devices' pollers are not locked against this (no cross-device lock on global pygame state).
- **Failure scenario**: two simultaneous requests (options for device A, set_controller for device B) initialize/enumerate pygame concurrently; SDL joystick API is not documented as thread-safe on all platforms.
- **Proposed fix direction**: serialize all pygame access behind one module lock in gamepad.py; use `sys.executable` for the subprocess and a delimiter that cannot occur in names (or JSON).
- **Confidence**: subprocess parsing: verified; thread-safety of pygame calls: hypothesis (settle by stress test on macOS/Linux).

### GAMEPAD-19
- **Title**: macOS disconnect detection only inspects the old joystick object
- **Severity**: low
- **Views affected**: Controller (darwin)
- **Reference behavior**: main `_is_os_connected` returned `True` on macOS (no check).
- **Actual behavior**: darwin branch (gamepad.py:303-312) returns True if `self.gamepad.joystick.get_name()` does not raise; during a swap `_initialize_pygame_joystick` runs this against the OLD gamepad (still assigned) before the new index is validated, so a swap to a non-existent index passes `_is_os_connected` and is only caught by `Joystick(n)` raising (:440). Unplug detection depends on the earlier `controller_index >= get_count()` check (:277-282) plus pygame hotplug behavior.
- **Failure scenario**: (hypothesis) unplugged pad on macOS keeps reporting neutral values without triggering `_handle_disconnect`.
- **Proposed fix direction**: validate index against `pygame.joystick.get_count()` first, then instance id via `Joystick.get_instance_id()`/`JOYDEVICEREMOVED` events.
- **Confidence**: hypothesis (needs macOS unplug test)

### GAMEPAD-20
- **Title**: docs/architecture/controllers.md factual errors about gamepad
- **Severity**: low
- **Views affected**: docs
- **Reference behavior**: code as cited.
- **Actual behavior (docs vs code)**:
  - docs: `_active_poller_count` "how many pollers currently exist" -> code counts successful joystick binds (gamepad.py:451-453), see GAMEPAD-2.
  - docs: `get_mapped_state` "else a zeroed dict" -> returns `{}` (gamepad.py:518) and also requires `is_polling` (:517).
  - docs: `set_controller` "restarts polling if it was already running" -> restarts whenever `gui_root` is set and it is not currently polling (gamepad.py:354), even if it never was.
  - docs: `_poll_loop` "gui root's `after` or similar async task dispatch" -> only `gui_root.after` duck-typing (:637); anything else stops the loop (:640).
  - docs known-issues.md:39-55 hypothesis about transient-falsy gamepad: not reachable on a successful swap (GAMEPAD-4).
- **Proposed fix direction**: update docs after fixes above.
- **Confidence**: verified

### GAMEPAD-21
- **Title**: A successful controller swap permanently stops the poller on **every** frontend
- **Severity**: high
- Source: **found during the 2026-09-20 fix wave** by the `fix-input` worktree agent while working GAMEPAD-7 — not from the 2026-09-19 audit pass. Verified independently by the lead before recording.
- **Views affected**: Tkinter, PySide6, Web — all three. The lead initially recorded this as Web-only and that was wrong; see below.
- **Reference behavior**: S5 (RC-13 item 2) gave `ControllerPoller` its own clock so polling no longer depends on a Tk widget's `after`. `test_polling_continues_without_a_tk_event_loop` pins that, and GAMEPAD-1 was closed on it.
- **Actual behavior**: the repair did not reach the swap path. `set_controller` (`gamepad.py:364-370`) calls `_initialize_pygame_joystick`, whose first act is `stop_polling()` (:380). It then resumes under `if success and self.gui_root and not self.is_polling` (:368). `gui_root` defaults to `None` (:261) and is only assigned when `start_polling` is passed a `gui` (:489-491) — which the threaded clock path, by construction, does not. So on the web frontend the guard is falsy, the resume never runs, and the poller stays stopped. `change_controller` carries the identical condition at :465.
- **Failure scenario**: a web user with a controller bound swaps to a second controller. The swap reports success and the UI shows the new controller selected. Polling has been torn down and never restarts, so `get_mapped_state()` returns `{}` for the rest of the process's life and stick input is silently dead. If the probe was in manual mode, the coils stay energised while input does nothing — the same end state GAMEPAD-1 described, reached by a different route after GAMEPAD-1 was closed.
- **Proposed fix direction**: the resume condition must test what actually matters — that polling was running and should continue — not that a Tk root happens to exist. Gate on the prior `is_polling` state captured before the teardown, and call `start_polling()` with no `gui` argument so the existing `gui_root`/threaded-clock selection in `start_polling` makes the scheduler choice in one place. Fix both call sites. Do not reintroduce a `gui_root` requirement anywhere on the resume path.
**Scope correction (lead, 2026-09-20).** First recorded as affecting the Web
frontend only, on the reasoning that `gui_root` is set when a view passes a
`gui`. It is not set anywhere. The only live external caller of
`start_polling` in `src/` is `probes.py:882`, which passes `None` explicitly
— S5 moved poller startup into the model precisely so the Web frontend,
having no event loop to offer, would poll at all. So `gui_root` is `None` on
**every** frontend, the resume gate was falsy for all three, and every
controller swap in the application killed input until the process restarted.
The `gui_root is not None` branch in `start_polling` (:527) is dead code on
the current tree; it is left in place deliberately, because removing it is a
separate decision and RC-13's anti-fix table is specific about what may be
done to this path.

This is also why the finding is `high` rather than `medium`: it is the same
end state as GAMEPAD-1 — manual mode inert, coils energised, no auto-disable
because the watchdog skips while `manual_flag` — and GAMEPAD-1 was rated
high.

- **Confidence**: verified (structure), lead-confirmed at `0e11280`; the
  all-frontends scope verified by exhaustive grep of `start_polling` callers.

## Coverage

Read fully: `src/controller/gamepad.py` (1-640); `src/model/probes.py` (1-537); `src/views/pyside/view.py` 20-96, 97-118, 120-455 (QtDynamicView), 719-967 (DashboardWindow/DeviceDock); `src/views/tkinter/view.py` 20-98 (error popups), 100-283, 285-580 (DashboardWindow, DynamicView); `src/views/web/web_adapter.py` (1-489), `web_view.py` (1-78); `src/views/web/static/js/app.js` 590-740, 780-935 (dropdown, toggle, pollState) plus greps of the whole file; `src/app.py` 1-20, 62-80, 130-165, 388-462, 530-552, 780-920; `src/app_bootstrap.py` 135-197; `src/model/system_manager.py` (all); main: `controllerDrive.py` (all), `stepper_frame.py` 362-425 and 580-720, `mainGUI.py` grep of controller handling; `src/controller/serial.py` only lines 195-225 (to compare Z/dpad combining, not audited); `firmware/stepper_firmware/stepper_firmware.ino` grep for dpad; `redpercent_system.py` 300-335; tests/hardware/test_gamepad.py 1-70; docs known-issues.md 36-60 and controllers.md 60-120; git log/show for commits 05c2e69 and 39e3a51.

Not done: did not run any code, GUI or hardware; not verified physical axis/sign behavior (GAMEPAD-12, 13, 19); did not read `chuck_frame.py`/`DC_frame.py` controller code beyond greps (assumed identical to stepper_frame per grep of binds); did not audit `tests/` beyond the gamepad test, `RedPercent` views, `web_server.py` beyond greps, or `style.qss` beyond toggle rules; app.js render/re-render lifecycle (lines outside 590-935) only grepped.
