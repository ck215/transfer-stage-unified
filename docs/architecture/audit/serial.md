# Audit: serial (src/controller/serial.py, class `serial`, aliases `SerialArduino`, `Serial`)

> **Input (banner added 2026-09-23).** Audit of the old tree (now `legacy/src/`); current until the rebuild replaced it on 2026-09-23. Kept because the ledger in `docs/implementation/progress.md` and `docs/rebuild/carry.json` cite these finding IDs.

## Object summary

- Class: `serial` in `src/controller/serial.py:20` (aliases at :267-268). Has an `RLock` (`_lock`, :35), `ser` (pyserial handle or None), `_read_buffer`, `device_type` (set at :78, never read anywhere in src/ -- grep verified).
- Constructor sites (all in models, all reached from `app_bootstrap.build_models` or view "reopen" paths):
  - `BaseProbe.__init__` `src/model/probes.py:23`: `serial(port) if port and port != "None" else None` (baud 500000 default). Note port "SIM" DOES construct a `serial('SIM')` (ser=None).
  - `BaseProbe.reconnect_serial` `probes.py:161-175`: close + sleep(1) + new `serial(...)`.
  - `TemperatureSystem.__init__` `src/model/temperature_system.py:26` (baud 115200).
  - No other `serial(` constructor calls in src/ (grep). `SystemManager.reboot_model` (system_manager.py:34) is the generic re-construct path but has ZERO callers outside its own definition (grep for `reboot_model` across src/).
- Owner: the model (`BaseProbe.serial_comm`, `TemperatureSystem.serial_conn`); models are owned by `SystemManager.active_models`.
- Teardown path: `SystemManager.shutdown_all()` (system_manager.py:59-68) -> `model.teardown()` -> `BaseProbe.teardown` (probes.py:479-487) -> `serial_comm.close()` (serial.py:259-263); temperature: `TemperatureSystem.teardown -> close` (temperature_system.py:181-199) -> `serial_conn.close()`.
- Callers of shutdown_all: Tk `DashboardWindow.on_close` (tkinter/view.py:226-231), PySide `DashboardWindow.closeEvent` (pyside/view.py:957-967), Web `WebDashboardWindow.close` (web_view.py:75-78, only on KeyboardInterrupt/exception paths in app.py:798-809) and `WebModelAdapter.initialize_setup` for the outgoing manager (web_adapter.py:181-185).

### Wire protocol as implemented (host -> firmware), verified against firmware/stepper_firmware/stepper_firmware.ino and chuck_firmware.ino (identical parser; they differ only in pin map / current / stepping config, see diff)
| Host sends | Where | Firmware handling |
|---|---|---|
| `s\n` repeated every 50ms for <=3s after 1.5s sleep | serial.py:57-80 | 0x73 -> `Serial.println("DEV: s"/"DEV: c")` (stepper .ino:358-361; chuck .ino:360; DC high_polling_rate.ino:324-327 "DEV: d"; temp .ino:46-50 "DEV: t") |
| `e` (1 byte, no newline) | serial.py:245 | 0x65 -> enable, `toff(4)`, `delay(30)` (stepper .ino:340-356). **DC firmware has no handler** |
| `d` (1 byte, no newline) | serial.py:254 | 0x64 -> `system_enabled=false; toff(0)` (stepper .ino:323-337). **DC firmware has no handler** |
| 12-field ASCII CSV + `\n` | serial.py:164-174 | starts with digit/'-' -> `parseSerialAuto()` (stepper .ino:364-366, :149-282); fields 10/11 = manual/auton codes; all-zero = CASE 3 stop |
| 42-byte binary `<BBffffffffff` (0xAA, mode=1, 10 floats) | serial.py:14,208-223 | matches `ManualControlPacket` packed struct (2 + 10*4 = 42 bytes; stepper .ino:87-100, DC .ino:104-117) |
| `k\n` | probes.py:474 (`power_down`) | **No firmware handles 'k'** (grep for `'k'`/0x6b in firmware/*/*.ino: only unrelated comment hits). Stepper/chuck: 'k' hits the "clear buffer" else branch (.ino:369-371). DC: falls to `parseSerialAuto`, parsed as an all-zero STOP. |
| firmware -> host `POS:x,y,z\n` every ~100ms | serial.py:113-149 | stepper .ino:573-580 |

Firmware never replies to e/d/text commands (no ACK), so there is no reply timeout to block on. The only blocking reads are in the constructor ping loop.

---

## Findings

### SERIAL-1
- ID: SERIAL-1
- Title: `disable()` write failure is swallowed inside `serial.disable()`; model then reports itself disabled while the coils may still be energized
- Severity: high (safety/hardware)
- Views affected: Model / Controller (all three views)
- Reference behavior: main `src/serialDrive.py:182-185` `disable()` did an unguarded `self.ser.write(...)`, so a write failure raised to the caller (chuck_frame.py enable_button caught only ValueError, but the exception was at least not silently converted to "disabled").
- Actual behavior: `serial.disable()` (serial.py:249-256) wraps the write in `try/except Exception` and only calls `ErrorPopupManager.report_error('Serial Write', str(e))` (no exception param). It returns normally. `BaseProbe._stop_and_disarm` (probes.py:442-462) catches only `ValueError` from `disable()` (:459-461) and then unconditionally sets `self.system_enabled = False` (:462). Same in `enable()` (serial.py:240-247 -> probes.py:425-432): a failed `'e'` write is swallowed and `system_enabled = True` is set (:432).
- Failure scenario: cable glitch / write_timeout (serial.py:53, 1s) on the `'d'` byte during Full Stop or dock close -> one popup titled "Serial Write" (dedup'd 5s by error_routing.py `_is_spam`) -> UI shows Disabled, model.system_enabled False, but firmware never got `'d'` and coils stay powered. No retry, no ACK (firmware never replies), no re-send on next Full Stop since flags now say disabled (the unconditional send at probes.py:457-460 does re-send on the next `_stop_and_disarm`, but nothing prompts the operator to press it again).
- Proposed fix direction: make `serial.enable()/disable()` return a bool (or re-raise a dedicated `SerialCommandError`) and have `BaseProbe.enable/_stop_and_disarm` only flip `system_enabled` on success; on disable failure, keep a `disable_pending` flag, retry once immediately, and raise a high-visibility `report_error("COILS MAY BE ENERGIZED ...")`. Consider adding a firmware `ACK` line for e/d and reading it (short timeout, off GUI thread) -- matches the "ACK gap" already noted in probes.py:151-153.
- Confidence: verified

### SERIAL-2
- ID: SERIAL-2
- Title: `BaseProbe.teardown()` runs poller cleanup BEFORE the hardware disable, with no try/except -- an exception in poller.close() skips 'd', 'k' and port close
- Severity: high (safety + resource leak)
- Views affected: Model (reached from Tk on_close, PySide closeEvent, Web close/initialize_setup via `shutdown_all`)
- Reference behavior: `teardown()` docstring (probes.py:480-481) says it applies "the strongest stop ... releases the serial connection"; safety-critical step should come first.
- Actual behavior: probes.py:482-487: `poller.stop_polling(); poller.close()` then `power_down()` then `serial_comm.close()`, no try/except. `SystemManager.shutdown_all` (system_manager.py:63-68) catches the exception per-model and reports it, but the rest of teardown never ran. Also `serial.close()` (serial.py:259-263) has no try/except; if `ser.close()` raises, `shutdown_all` again only reports it.
- Failure scenario: gamepad unplugged/pygame error while quitting -> `poller.close()` raises -> stepper never receives 'd', port never closed; app exits with coils enabled (firmware keeps state until next DTR reset).
- Proposed fix direction: reorder to `power_down()` first, then poller stop/close each wrapped in try/except, then `serial_comm.close()` in a `finally`. Wrap `ser.close()` in try/except in `serial.close()`.
- Confidence: verified (structure read; the poller.close() raise path not executed -- UNVERIFIED that pygame raises in practice)

### SERIAL-3
- ID: SERIAL-3
- Title: PySide6 `close_device_view` never closes the serial port for Stepper/DC/Chuck (BaseProbe has no `disconnect`), bypasses `teardown()`/`remove_model`, and the reopen creates a headless model -- existing docs disagree on what reopen does
- Severity: high (resource leak; hardware left enabled path; silent loss of hardware config)
- Views affected: PySide6 (Tkinter/Web do not have this path)
- Reference behavior: Tk `on_close` and PySide `closeEvent` use `shutdown_all` -> `teardown()` (tkinter/view.py:228, pyside/view.py:966). `SystemManager.remove_model` + `_teardown_model` exist for exactly this (system_manager.py:18-27).
- Actual behavior: pyside/view.py:813-847. Sequence: `widget.cleanup()` (stops timers, stops+closes poller, :437-453) -> `dock.close()` -> `model.disable()` (:837-838; disable = `_stop_and_disarm`, sends stop + 'd' but NOT power_down/'k' and not port close) -> `poller.stop_polling(); poller.close()` AGAIN (:839-841, second close of the same poller) -> `hasattr(model,'disconnect')` (:842): `BaseProbe` has no `disconnect` (grep `def disconnect` in src/model: only rotator_system.py:108 and temperature_system.py:194) so `serial_comm.close()` never runs -> `del self.system_manager.active_models[device_name]` directly (:845-846) without `system_manager.lock` and without `teardown()`.
  Reopen (pyside/view.py:881-895): model not in manager -> `StepperProbe(None, "None", {})` etc. -> `serial(port) if port and port != "None"` (probes.py:23) yields `serial_comm = None`. So the reopened probe is headless: no port, no message.
- Failure scenario: user unchecks "Stepper Probe" in sidebar (or hits dock X) -> old `serial` object is only released when GC finalizes it (pyserial `Serial` derives from `io.RawIOBase`, whose finalizer calls close -- UNVERIFIED for this pyserial version/platform; reference cycles through Qt timers/poller could delay it) so the port may stay busy; user re-checks -> gets a silent SIM-like probe whose buttons do nothing (no serial_comm), configured port/controller lost. Windows: a re-`serial()` on the same port by another path would raise PermissionError.
- Doc note: `docs/architecture/known-issues.md:29-38` says reopen "constructs a brand-new `serial()` on the same port" -- WRONG: reopen passes port None (pyside/view.py:886,889,892; probes.py:23 -> None). `docs/architecture/ownership-and-lifecycle.md:209` ("serial_comm initialized to None") is the correct one.
- Proposed fix direction: replace the body after `dock.close()` with `model = self.system_manager.remove_model(name); if model: self.system_manager._teardown_model(name, model)` (guarantees power_down + serial close), remove the duplicate poller close and the direct dict `del`. Separately decide reopen semantics: persist per-device config (port, controller) in the manager so reopen reconstructs with the original port, or warn the user that reopen is headless (`ErrorRouter.report_info`). Add `disconnect()` to BaseProbe as an alias of teardown only if the hasattr protocol is kept.
- Confidence: verified (code paths); GC-close behavior hypothesis

### SERIAL-4
- ID: SERIAL-4
- Title: Tkinter "Close Tab" (right/middle click) only `forget()`s the tab; model, poller, serial port and coil state stay live and unreachable
- Severity: high (orphaned energized hardware with no UI)
- Views affected: Tkinter
- Reference behavior: PySide close path disables + (attempts to) release model (SERIAL-3); main had no per-tab close.
- Actual behavior: `DraggableClosableNotebook.close_tab` (tkinter/view.py:158-162) calls `on_close_tab_callback` if set, else `self.forget(index)`. `on_close_tab_callback` is initialised to None (:120) and never assigned anywhere (grep: only lines 120, 159, 160). `DashboardWindow` keeps `tab_metadata` and the model stays in `SystemManager`. The frame is forgotten, not destroyed, so the view's `after()` poll loops (tkinter/view.py:557-578, 540) keep running.
- Failure scenario: user middle-clicks the Stepper tab while in Manual mode/enabled -> tab gone, but model still enabled, manual loop still sending packets from the invisible view with the gamepad, port held. Only "FULL STOP" (which acts on all models) or window close recovers it.
- Proposed fix direction: set `notebook.on_close_tab_callback` in `DashboardWindow.__init__` to a handler that resolves the frame via `tab_metadata`, cancels the view's `after` ids, then `remove_model` + `_teardown_model` (same helper as PySide fix in SERIAL-3), then `forget`.
- Confidence: verified

### SERIAL-5
- ID: SERIAL-5
- Title: Web `initialize_setup` builds new models (opens ports, DTR-resets Arduinos) BEFORE tearing down the old manager -> double-open of the same port
- Severity: high (port double-open; blind models on Windows; cross-talk on POSIX)
- Views affected: Web
- Reference behavior: Tk/PySide construct only once per launch (app.py:402, 731). `reboot_model` (system_manager.py:34-45) does teardown -> sleep(1) -> construct (correct order) but is unused.
- Actual behavior: web_adapter.py:148 `app_bootstrap.build_models(configs, active_claims)` runs first (each `serial()` opens the port and blocks 1.5-4.5s), and only at :181-185 is `old_manager.shutdown_all()` called. The "Setup" modal is reopenable while running (`btnOpenSetup`, app.js:113; route `/api/setup/initialize` web_server.py:187-192). The adapter comment at web_adapter.py:181-183 acknowledges the shutdown happens after install.
- Failure scenario: user reopens Setup in the Web view and relaunches with the same real port -> (Windows) second `Serial()` raises `PermissionError` -> serial.py:90-97 warns "Operating blind" -> new probe has `ser=None`; then old manager's `shutdown_all` closes the port and the new model stays blind forever, yet `_connection_status` may show a stale/incorrect state. (POSIX, pyserial default non-exclusive) two handles open; new open DTR-resets board; then old teardown writes 'd','k\n' (probes.py:457-477) and, for temperature, `<0,6.0,0,0,0,0>` (temperature_system.py:186) onto the new session's tty; old `TemperatureSystem.read_serial_data` thread steals lines until `continue_reading` flips.
- Proposed fix direction: in `initialize_setup`, take the old manager out first (swap under `_state_lock`), `old_manager.shutdown_all()`, sleep briefly (>=1s like reboot_model), then `build_models`. On build failure restore a clean empty state and report. Optionally open pyserial with `exclusive=True` (POSIX) so double-open fails loudly.
- Confidence: verified (ordering); platform behavior hypothesis

### SERIAL-6
- ID: SERIAL-6
- Title: Constructor blocks 1.5-4.5s per device on the GUI/request thread with no progress feedback; probe stage runs `build_models` after the launcher window is withdrawn
- Severity: medium
- Views affected: Tkinter, PySide6, Web (request thread)
- Reference behavior: main `serialDrive.py:43` `time.sleep(1)` only, no ping loop.
- Actual behavior: serial.py:60-80: `reset_input_buffer`, `time.sleep(1.5)` (:63), then up to 3.0s ping loop (:66-80). Called synchronously from `build_models` (app_bootstrap.py:149-158): Tk app.py:397 `self.withdraw()` then :402 build_models (window vanishes, event loop frozen); PySide app.py:731 (Launch dialog frozen, no spinner); Web web_adapter.py:148 inside an HTTP handler thread. N serial devices => up to ~4.5s x N (stepper+dc+chuck+temp = 18s worst case) when hardware is absent/unresponsive.
- Failure scenario: two probes with a non-responding firmware -> ~9s frozen UI, appears crashed; unverified ports still get "operating blind" warnings afterwards.
- Proposed fix direction: build serial-backed models on a worker thread with a status callback (setup window shows "Connecting <port>..."), or split `serial.__init__` (open only) from `serial.verify()`; break early once `DEV:` seen (already does) and shorten the fixed boot sleep by polling `in_waiting` instead of a blind 1.5s.
- Confidence: verified

### SERIAL-7
- ID: SERIAL-7
- Title: Unverified ("operating blind") connections are treated as connected; `device_type` is parsed but never checked against the model class
- Severity: medium
- Views affected: Model / Web (connection status) / all views
- Reference behavior: main `serialDrive.py:44` just printed "Arduino Ready" after sleep(1) with no verification (so the refactor is an improvement in intent, but the result is unused).
- Actual behavior: serial.py:82-87: if no `DEV:` reply, only `report_warning` is emitted and `self.ser` stays open. `self.device_type` (:39, :78) is never read (grep `device_type` in src/). `_verify_serial` (:102-109) only checks `ser.is_open`, so `BaseProbe.enable()` (probes.py:423-434) succeeds on an unverified port. Web `_connection_status` (web_adapter.py:255-266) returns "hardware" whenever `ser.is_open`.
- Failure scenario: a Stepper Probe assigned to the temperature controller's port (or the wrong baud / DC vs Stepper board) -> warning popup at launch is easy to dismiss; enable/manual then stream binary packets and 'e'/'d' bytes to the wrong device (temp firmware treats non-'<' bytes as noise, 's' bytes reply); UI shows "hardware/connected". Also mismatch DC probe vs stepper firmware go unnoticed.
- Proposed fix direction: have `serial.__init__` accept `expected_type` ('s'/'c'/'d'/'t' as in `DEV_PATTERN`, app_bootstrap.py:53); if verified and `device_type` mismatches, close the port and report_error; if unverified, expose `serial.verified` and let `BaseProbe.enable()` refuse (or require confirmation) when not verified. Surface `verified` through `_connection_status`.
- Confidence: verified

### SERIAL-8
- ID: SERIAL-8
- Title: Port loss mid-session is never detected: read/write errors are only popup-spam (5s dedupe), state stays "connected/manual"
- Severity: medium
- Views affected: Model / all views
- Reference behavior: main printed and moved on (`serialDrive.py:93-95`, :125-126), with `_poll_position` at chuck_frame.py:431 stopping if `serial.ser is None`.
- Actual behavior: on unplug, `read_position` (serial.py:113-149) hits an exception at `ser.in_waiting` (:120, inside outer try :117) -> `report_error("Serial Read Error", ...)` (:148) every 100ms poll; `_is_spam` (error_routing.py:17-27) dedupes identical messages for 5s only, so a modal error popup re-appears every 5s indefinitely (modal `QMessageBox.critical`/`messagebox.showerror` in pyside/view.py:75, tkinter/view.py:_display_popup). Writes similarly report "Serial Write Error" (:181-184, :235-238). Nothing sets `self.ser = None`/closes the handle, `ser.is_open` may remain True on POSIX, `BaseProbe.manual_flag/system_enabled` are not cleared, and Web status stays "hardware".
- Failure scenario: cable pulled while in Manual mode -> gamepad keeps "driving", model says Manual/Enabled, an error dialog every 5s; after re-plugging there is no way to reconnect (reconnect UI deliberately removed, probes.py:148-154).
- Proposed fix direction: on the first `SerialException/OSError` in read/write, mark the connection dead (`self.connected=False`, close `ser`), report once, and have `BaseProbe` observe it (clear manual/auton/system_enabled, surface "Disconnected"). Provide a supported reconnect action (setup wizard or a model method that does teardown + reconstruct via `SystemManager.reboot_model`).
- Confidence: verified (logic); actual hardware unplug behavior not exercised

### SERIAL-9
- ID: SERIAL-9
- Title: `SIM`-port probes construct `serial('SIM')` whose `enable()` always raises -> SIM/headless probes cannot enter auton/manual; message is misleading
- Severity: low
- Views affected: Model / all views (same in main)
- Reference behavior: main `serialDrive.py:177-180` also raises `ValueError` when `ser is None`, so SIM could never enable in main either; chuck_frame.py:521-522 just printed.
- Actual behavior: probes.py:23 builds `serial('SIM')`, serial.py:41-45 returns early with `ser=None`; `enable()` (:240-242) -> `_verify_serial(verbose=True)` false (verbose branch suppressed for 'SIM' at :104) -> `raise ValueError("... Arduino not detected. Cannot enable system.")` -> `BaseProbe.enable` catches (probes.py:428-431) -> `report_warning("Enable Failed", ...)` -> `enter_auton/enter_manual/macro_start_auton` return early (probes.py:220-221, :237, :243). Also the info popup "Simulator Mode" (serial.py:43-44) is emitted per SIM device but identical text is de-duplicated (error_routing.py `_is_spam`), so only the first shows.
- Failure scenario: user picks "Headless" for a probe to test the UI -> clicking "Enter Autonomous Mode" pops "Enable Failed: Arduino not detected" and nothing happens.
- Proposed fix direction: decide intent: either make `enable()/disable()` no-ops that succeed in SIM (`if self.SERIAL_PORT in ('SIM','None',None): return`), or make the message say "Simulator: no hardware attached". Does not affect real hardware.
- Confidence: verified

### SERIAL-10
- ID: SERIAL-10
- Title: DC probe firmware ignores 'e'/'d'/'k' -> "disable"/"power down" are not real for DC; raw single-byte commands can also be mis-parsed as text
- Severity: medium
- Views affected: Model (DCProbe) / firmware
- Reference behavior: stepper/chuck firmware handle 'd' (toff(0)) and 'e' (stepper .ino:323-356).
- Actual behavior: `firmware/high_polling_rate/high_polling_rate.ino:290-333` `parseHybridSerial` handles only 0xAA, 0x73 ('s') and else -> `parseSerialAuto()`. `'e'`/`'d'` (serial.py:245,254, no newline) go to `parseSerialAuto` -> `readStringUntil('\n')` with `Serial.setTimeout(2)` (:600) -> field 10/11 empty -> CASE 3 (stop; from the same structure as the stepper .ino:259-279; DC copy at high_polling_rate.ino:~200+ UNVERIFIED line-by-line). If a binary manual packet follows within 2ms, `readStringUntil('\n')` could swallow packet bytes until a 0x0A float byte (UNVERIFIED hypothesis; needs firmware test). `'k\n'` (probes.py:474) is unhandled by every firmware.
- Failure scenario: DC probe "Disable" leaves the H-bridge in whatever state stop logic gives (hard brake at .ino:344-347 only on `handleAllStop`); UI claims disabled. The `power_down` "Kill Coils" log line (probes.py:475) is false for all boards.
- Proposed fix direction: add explicit 'e'/'d' (and 'k' if wanted) branches to high_polling_rate.ino before the else; or send `e`/`d` with terminator and document; remove or implement `'k'` (grep shows `'d'` already does the coil kill for stepper/chuck). Flash script: firmware/flash_firmware.py.
- Confidence: verified for missing handlers (grep); mis-parse race hypothesis

### SERIAL-11
- ID: SERIAL-11
- Title: Raw `ser.write` bypasses `serial._lock` and framing from several call sites (power_down, run_script, temperature) -> can interleave with 42-byte binary packets
- Severity: medium
- Views affected: Model
- Reference behavior: `serial` wraps all access in `_lock` (serial.py:118, 173, 226, 243, 252, 260) -- main had no lock at all.
- Actual behavior: `BaseProbe.power_down` writes `b'k\n'` directly (probes.py:472-474); `run_script` writes raw text on a worker thread (probes.py:322-328) while the GUI thread may send manual packets; `TemperatureSystem` writes/reads `serial_conn.ser` directly (temperature_system.py:31, 94, 176, 186, 104) so `close()`'s lock (serial.py:260) does not exclude the reader thread's `readline()`; closing the port under a blocked `readline` raises in the reader thread (handled by `continue_reading=False` check, temperature_system.py:114-115, low impact).
- Failure scenario: `k\n` (2 bytes) injected mid-way through a 42-byte manual packet on the wire -> firmware framing (0xAA start, wait for 42 bytes, stepper .ino:289-291) consumes the wrong bytes and mis-parses the next packet; brief wrong motion command. Low probability, real consequence for motion.
- Proposed fix direction: add `serial.write_raw(bytes)` (locked) and route power_down/run_script/temperature writes through it; add a locked `readline()` wrapper for the temperature thread with short timeout.
- Confidence: verified (call sites); interleave hypothesis

### SERIAL-12
- ID: SERIAL-12
- Title: Manual-mode packet stream runs on the GUI thread with per-tick `print`, blocking `flush()` (tcdrain, unbounded) and 1s `write_timeout`; PySide ticks 2.5x faster than Tk
- Severity: medium
- Views affected: PySide6 (20ms), Tkinter (50ms); Web n/a (poller-thread/handler-driven)
- Reference behavior: Tk `_route_input` every 50ms (tkinter/view.py:551-564); main similar.
- Actual behavior: PySide `input_timer.start(20)` (pyside/view.py:184) calls `model.send_manual_mode_command` -> `serial.send_manual_mode_command` (serial.py:187-238) which does `print(...)` of the packet (:225), `ser.write` + `ser.flush()` under `_lock` (:226-228). A stalled USB CDC endpoint blocks the GUI thread up to write_timeout (1s, :53) per attempt, and `flush()` has no timeout. `read_position` (100ms timers, pyside/view.py:148, Tk :567-571) contends for the same lock on the same thread only, so no deadlock, but web `get_state` calls `read_position` from request threads (web_adapter.py:284-293) holding the device lock, so they contend with command threads.
- Failure scenario: device stalls -> GUI freezes; PySide at 50Hz packet rate vs firmware manual timeout (not audited) may differ from the Tk-validated 20Hz; console flood at 20-50 lines/s hurts performance.
- Proposed fix direction: drop the per-packet print (or rate-limit), drop `flush()` or run writes from a dedicated sender thread with a bounded queue and "latest state wins" semantics; align PySide rate to Tk's unless firmware requires faster.
- Confidence: verified (code); firmware timeout interplay not audited

### SERIAL-13
- ID: SERIAL-13
- Title: Wire-protocol change vs main: 28-byte int16 manual packet and 't' toggle replaced by 42-byte float packet and 'e'/'d' -- requires reflashed firmware; stale firmware silently misframes
- Severity: medium
- Views affected: Model / firmware
- Reference behavior: main `serialDrive.py:7` `PACKET_FORMAT='<BBfffhhhhhhh'` (28 bytes); enable/disable both send `'t'` (:177-185); main firmware handles 0x74 (`git show main:firmware/stepper_firmware/stepper_firmware.ino` line 323).
- Actual behavior: serial.py:14 `'<BBffffffffff'` (42 bytes; test tests/hardware/test_serial.py asserts 42); enable `'e'` (:245), disable `'d'` (:254); repo firmware struct is 42 bytes of floats (stepper .ino:87-100). The ping (serial.py:60-80) only checks that *some* `DEV:` reply comes back, not protocol version, so an old-firmware board verifies fine but then receives 'e'/'d' (which it treats as garbage: main firmware has no such handler) and 42-byte packets (a 28-byte reader mis-frames); the old firmware's 't' toggle is never sent, so the board can never be enabled/disabled -- while the UI says enabled.
- Failure scenario: lab machine with a board flashed from main firmware: verification passes, Enable button shows "enabled", coils not energized (or worse, stale-enabled from an earlier session and 'd' ignored -- coils stay on).
- Proposed fix direction: extend the ident reply to include a protocol version (`DEV: s v2`) and have `serial.__init__` reject/warn on older versions (`device_type`/`fw_version` attributes); note reflash requirement in README / flash_firmware.py.
- Confidence: verified (formats/handlers); old-board behavior hypothesis (main firmware handler contents beyond line 323 not read)

### SERIAL-14
- ID: SERIAL-14
- Title: `BaseProbe.reconnect_serial()` (only caller PySide `open_device_view`) blocks the GUI ~5-6s, closes without disabling, and is effectively unreachable
- Severity: low
- Views affected: PySide6
- Reference behavior: main `chuck_frame.py:663-672` reconnect button (close, sleep 1, new `SerialArduino`, restart position polling); refactor deliberately removed the button (probes.py:148-154, "stays available for the setup wizard").
- Actual behavior: pyside/view.py:858-879: when the dock already exists (`device_name in self.active_docks`) it calls `model.reconnect_serial()` after a modal `QMessageBox.show()` + `processEvents()`. `reconnect_serial` (probes.py:161-175): `serial_comm.close()` without prior `disable()`/stop, `time.sleep(1)`, new `serial()` (1.5s + up to 3s). It resets `system_enabled/auton_flag/manual_flag` (:173-175) but not `is_stepping`, the watchdog event, or the view's position/input timers keep firing during the 6s (processEvents is called once, not in a loop). Reachability: `on_device_item_changed` calls `open_device_view` only on Checked (pyside/view.py:806-809); a device with a live dock is already Checked, and `on_dock_closed` unchecks with signals blocked (:945-951), so the branch is effectively dead (UNVERIFIED: no other caller found by grep `open_device_view`).
- Failure scenario: if reached, GUI freezes for ~6s and any in-flight motion loses its stop channel (no 'd' sent before close; DTR reset on reopen will actually drop the firmware state, masking this).
- Proposed fix direction: delete the branch, or if kept, route through `SystemManager.reboot_model` (teardown first, worker thread, progress indicator).
- Confidence: verified (code); reachability hypothesis

### SERIAL-15
- ID: SERIAL-15
- Title: App-exit paths that bypass `shutdown_all` leave coils enabled and ports open (Tk root quit, Web SIGTERM/window-close of process)
- Severity: medium
- Views affected: Tkinter, Web
- Reference behavior: PySide `closeEvent` (pyside/view.py:957-967) and Tk dashboard `on_close` (tkinter/view.py:226-231) call `shutdown_all`.
- Actual behavior: no `atexit`, `signal`, or `aboutToQuit` handlers anywhere in src/ (grep). Tk: the dashboard is a `Toplevel` of a withdrawn root (app.py:397-422); only the Toplevel has `WM_DELETE_WINDOW` (tkinter/view.py:224); an OS-level quit of the root/process (e.g. macOS Cmd-Q, window-manager kill, uncaught exception) skips it. Web: `run_web_app` only calls `dashboard.close()` for `KeyboardInterrupt`/`Exception` in the sleep loop (app.py:798-809); SIGTERM or closing the terminal does not.
- Failure scenario: quit via Cmd-Q while stepper enabled -> process exits, firmware keeps `system_enabled=true` until the board is next reset; coils energized indefinitely.
- Proposed fix direction: register a single `atexit`/`signal` handler (SIGTERM/SIGINT/SIGHUP) that calls `system_manager.shutdown_all()` from the launcher for each view; for Tk also bind `<Destroy>`/root `WM_DELETE_WINDOW`.
- Confidence: hypothesis for platform-specific quit behavior (not exercised); absence of handlers verified

### SERIAL-16
- ID: SERIAL-16
- Title: Error-routing audit of serial.py -- most conditions are user-visible but throttled/misleading; several are print-only
- Severity: low
- Views affected: all (routing callbacks set before device construction: Tk app.py:429-430, PySide app.py:757-759, Web web_view.py:12,54)
- Reference behavior: main was print-only everywhere (serialDrive.py:44-53, 93-95, 122-126, 171-175).
- Actual behavior (serial.py line -> route):
  - :44 SIM banner -> `report_info` popup (modal in Tk/Qt; identical text de-duplicated for 5s by `_is_spam`, error_routing.py:17-27, so shows once).
  - :85-87 no ping reply -> `report_warning` (user-visible) -- but no state reflects it (SERIAL-7).
  - :91-93 SerialException on open -> `report_warning` w/ exception (user-visible).
  - :94-97 other exception at open -> `report_error` (user-visible). (main did `sys.exit(1)` here, :51.)
  - :102-109 `_verify_serial(verbose=True)` -> `report_warning("Serial Disconnected", ...)` with a message that omits the port, so all devices share one dedupe key: a second device's disconnect is suppressed for 5s.
  - :124, :148 read errors -> `report_error` (SERIAL-8 spam).
  - :177-184, :231-238 write timeouts/errors -> `report_error` (visible).
  - :246-247, :255-256 enable/disable write failures -> `report_error('Serial Write', str(e))` (visible but loses exception object and state; SERIAL-1).
  - :262 close() -> print-only; no failure handling at all.
  - probes.py:167 (close error in reconnect), :474-477 (`power_down` failure: **print-only**, no user-visible warning that the kill-coils write failed), :461 (`disable()` ValueError in `_stop_and_disarm`: print-only, **not user-visible** although it means the port is dead while trying to power off).
  - system_manager.py:41-42, 53-56, 66-68 teardown/reboot errors -> `report_error` (visible).
- Failure scenario: on Full Stop with a dead port the operator gets no message (probes.py:459-461 print-only) though coils cannot be confirmed off.
- Proposed fix direction: include port name in messages (unique dedupe keys); make `_stop_and_disarm`/`power_down` failures `report_error` with an explicit "coils may be energized" message; report close failures at warning level.
- Confidence: verified

### SERIAL-17
- ID: SERIAL-17
- Title: Ping loop artifacts: floods `s\n` every 50ms, leaves queued `DEV:` replies, `DEV:` substring match can capture a truncated line
- Severity: low
- Views affected: Model
- Reference behavior: n/a (new in refactor; app_bootstrap.probe_device_at uses the same pattern with a regex, app_bootstrap.py:53).
- Actual behavior: serial.py:66-80 writes `b"s\n"` on every iteration (~20/s for up to 3s) even after the board is up; each `s` yields a `DEV: x` reply. Loop breaks at first `"DEV:"` in `buffer` (:74), possibly mid-line, so `device_type = line.split("DEV:")[1].strip()` (:78) may be `""` or partial; remaining replies are consumed later by `read_position` (harmlessly ignored as non-POS lines, :137). `reset_input_buffer()` runs before the 1.5s sleep (:60-63), not after, so bootloader garbage may precede the reply (tolerated by substring match).
- Failure scenario: negligible functional impact today; becomes wrong if `device_type` is later used for validation (SERIAL-7).
- Proposed fix direction: send `s\n` at most every ~250ms, require a full line (`\n`) before parsing, and `reset_input_buffer()` after the match.
- Confidence: verified

### SERIAL-18
- ID: SERIAL-18
- Title: `SystemManager.reboot_model` has no callers; no view implements port change or reboot
- Severity: low
- Views affected: all
- Reference behavior: main had a per-tab "Serial Reconnect" button (chuck_frame.py:227-230, :663-672).
- Actual behavior: grep of `reboot_model` across src/ shows only its definition (system_manager.py:34). Port changes therefore require relaunching (Tk/PySide) or Setup re-run (Web, with SERIAL-5). If the constructor fails inside `reboot_model` (system_manager.py:47-57) the model is removed and views keep a stale reference.
- Failure scenario: no way to recover from a wrongly chosen or lost port without restarting.
- Proposed fix direction: either wire "Reconnect" to `reboot_model` (worker thread + user-visible progress) or delete dead code; make `reboot_model` restore a placeholder on failure.
- Confidence: verified

### SERIAL-19
- ID: SERIAL-19
- Title: Doc inaccuracy: known-issues.md misdescribes reopen as double-open
- Severity: low (documentation)
- Views affected: docs
- Actual: `docs/architecture/known-issues.md:32-35` says reopen "constructs a brand-new `serial()` on the same port"; code passes `None` (pyside/view.py:886/889/892 -> probes.py:23 yields `serial_comm=None`). `ownership-and-lifecycle.md:209` is correct. The real double-open risk is Web `initialize_setup` (SERIAL-5) and `reconnect_serial` (SERIAL-14).
- Proposed fix direction: correct known-issues.md; add SERIAL-4/5 to it.
- Confidence: verified

---

## Answers by question (index)

1. Open/verify/reconnect/locking: SERIAL-6, 7, 14, 17; single `RLock` guards read/write/close only (serial.py:35), raw ser access bypasses it (SERIAL-11). Open is `timeout=1, write_timeout=1` (:49-54); no exclusive open; each open DTR-resets AVR boards (so open implicitly clears firmware `system_enabled`, but close does not).
2. Who closes: Tk = `shutdown_all` on dashboard close only (tab close leaks, SERIAL-4); PySide = closeEvent -> `shutdown_all` OK, dock close path leaks for probes and reopens headless (SERIAL-3); Web = `shutdown_all` only in `close()` (Ctrl-C) and `initialize_setup` (double-open, SERIAL-5); app exit outside those (SERIAL-15); port change/reboot: no callers (SERIAL-18); `reconnect_serial` (SERIAL-14) has one dead-ish caller.
3. Protocol vs firmware: table above; SERIAL-10, 13; no replies to commands, only ping blocks (SERIAL-6); GUI-thread writes (SERIAL-12).
4. Error routing: SERIAL-16 (+ SERIAL-1, 8).
5. Diffs vs main serialDrive.py: lock added; verification ping (1.5s + <=3s vs 1s); `sys.exit(1)` removed; error routing added; packet format 28B int16 -> 42B float (`fmt`-driven conversions at serial.py:208-223, always float because `fmt[5:]` contains 'f'); enable 't' -> 'e', disable 't' -> 'd' and no longer raising on write failure (SERIAL-1); bumper None-tolerance (:204-206 vs main :146); SIM check widened to `('SIM','None',None)` (:41 vs main :29); `close()` locked; `read_position` errors routed as popups (SERIAL-8).

### SERIAL-20
- **Title**: `SimulatedPort` has no `flush()`, so every manual-mode frame in simulator mode raised `AttributeError` and was reported as a serial write error
- **Severity**: medium
- Source: **found during the 2026-09-20 fix wave** by the `fix-transport` worktree agent, in its own write set, while adding a bounded `flush()` for TEMP-11 — not from the 2026-09-19 audit pass. Verified independently by the lead before recording.
- **Views affected**: all three, whenever a probe is on a simulated port
- **Reference behavior**: `SimulatedPort` exists so that SIM mode exercises the *same* transport code path as real hardware. Anything the transport calls on a real handle, it must answer.
- **Actual behavior**: `send_manual_mode_command` ends with `self.ser.write(packet)` followed by `self.ser.flush()` under `self._lock` (`serial.py:391-392` at `f188804`, `:315-316` at `0e11280`). `SimulatedPort` implemented `write`, `read`, `readline`, `reset_input_buffer` and `reset_output_buffer`, but not `flush`. So on a simulated port every manual-mode frame raised `AttributeError` inside the write path, was caught by the method's generic handler, and surfaced to the operator as a **"Serial Write Error"** popup — once per frame, at manual-mode frame rate.
- **Failure scenario**: anyone running the application without hardware — which is how the three frontends are normally developed and demonstrated — enters manual mode and is buried in error popups reporting a write failure that is really a missing method on the test double. The simulator is the one configuration in which the operator has no way to tell a real transport fault from this.
- **Proposed fix direction**: give `SimulatedPort` a `flush()`. More generally this is the same shape as SERIAL-9: a method the simulated port forgot, which turns SIM back into a *different* code path — the one thing the class exists to prevent. The durable fix is a test that asserts `SimulatedPort` answers every attribute the transport calls on `self.ser`, so the next omission fails a test instead of reaching an operator.
- **Confidence**: verified; lead-confirmed by grepping every `.flush()` caller in `src/` at both `0e11280` and `f188804`.

## Coverage

Read fully: src/controller/serial.py (1-268); main:src/serialDrive.py (all); src/model/system_manager.py (all); src/model/probes.py lines 1-60, 130-232, 232-330, 340-520; src/model/temperature_system.py 15-60, 90-215; src/app_bootstrap.py 41-195; src/app.py 380-430, 690-830, 310-335, 485-505; src/views/pyside/view.py 20-80, 165-190, 436-460, 715-735, 770-975; src/views/tkinter/view.py 20-75, 100-262, 536-582; src/views/web/web_adapter.py 40-200, 240-300; web_view.py 1-95; web_server.py excerpts (180-200, 330-345); src/error_routing.py 1-55; src/model/rotator_system.py 100-125; firmware: stepper_firmware.ino 1-400 + grep of remainder, chuck_firmware.ino by diff vs stepper, high_polling_rate.ino 100-200 & 284-345 + grep, temp_controller.ino 160-266; main:chuck_frame.py excerpts (296-322, 425-445, 495-533, 663-672); docs known-issues.md 26-45,100-135 and ownership-and-lifecycle.md by grep.

Not done: did not read high_polling_rate.ino parseSerialAuto body past ~line 200 or the loop()/PID code; did not read main firmware beyond the peekChar grep; did not read tests (only test_serial.py head); did not audit gamepad poller, probe stepping logic, run_script beyond serial call sites, or rotator/red-percent lifecycles; did not run any code or hardware, so GC-close, DTR-reset, and unplug behaviors are stated from library/firmware knowledge and labeled hypothesis where relevant. Did not read Web app.js beyond grep of setup wizard hooks.
