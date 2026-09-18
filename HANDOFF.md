# Handoff — mvc-refactor safety/stability audit & fix pass

Session date: 2026-09-17. Read this before doing anything else in this repo.

## What this was

`mvc-refactor/` is an in-progress MVC rewrite of `main/` (the original working
Tkinter app for the lab's transfer-stage instrument), done largely by an
automated agent ("AGY"). It was never checked for feature/safety parity
against `main`, and its test suite mocks hardware/GUI libraries broadly
enough that real regressions shipped undetected. A prior session ran four
parallel audits (model layer, controller/HAL layer, view/UI layer, app
wiring + deps + tests), verified the findings, and produced a phased fix
plan. This session executed Phase 0–2 of that plan.

**Full plan, with every item's rationale, exact file/line targets, and
verification steps:** `~/.claude/plans/calm-cooking-pond.md` on this
machine. Read it — this file is a status summary, not a replacement.

## Current repo state

- Branch `mvc-refactor`, HEAD `3dd8978`. That commit (`feat(web): red-percent
  focus selector, disabled-device pass-through, SIM port handling`) is
  **Phase 0** — pre-existing uncommitted AGY work from before this session,
  committed as a clean baseline on request.
- **Uncommitted on top of that** (not yet committed — no one asked for a
  commit past Phase 0):
  ```
  M src/app.py
  M src/controller/gamepad.py
  M src/model/probes.py
  M src/model/system_manager.py
  M src/model/temperature_system.py
  M src/view.py
  M src/view_pyside.py
  M tests/core/test_edge_mvc_model.py
  M tests/core/test_model_interactions.py
  M tests/core/test_temperature_subsystem.py
  M tests/hardware/test_gamepad.py
  ```
  This is all of Phase 1 + Phase 2 below. Decide whether to commit (probably
  as 2-3 logical commits: interlock/safety fixes, temperature fixes, test
  updates) before doing more work on top of it.

## Done and verified this session

**Phase 1 (P0 safety) — all code items complete:**
1. `gamepad.py` `get_gamepad_wrapper()` — reinstated the unsupported-joystick
   rejection (was silently defaulting unknown devices to Xbox mapping).
2. `probes.py` `BaseProbe` — moved the 5-minute auto-disable interlock from
   view-layer timers (which the web dashboard never had) into the model, so
   all three frontends share it. Semantics: **defers while actively
   stepping/manual** (your explicit choice, not main's unconditional
   disable). `view.py`/`view_pyside.py` timer duplicates removed, replaced
   with a `touch_activity()` relay.
3. `probes.py` `StepperProbe` — step-size default fixed 16→1 (was moving the
   stage 16x further than intended on identical input; DC/Chuck were
   already correct).
4. `view.py` + `system_manager.py` (`shutdown_all`/`reboot_model`) — added a
   `disable()`/`power_down()` fallback so probes actually de-energize on
   window close/reboot (they only exposed `disable`, not `disconnect`/`stop`,
   so the old checks silently no-opped).
5. **NOT a code fix — needs you physically.** Enable/disable serial protocol
   changed from a shared toggle byte (`'t'`) to explicit `'e'`/`'d'` bytes.
   The in-repo firmware `.ino` sources were correctly co-updated, but nobody
   can confirm from source whether the *physical Arduino* on each lab box
   was reflashed. If not, enable/disable becomes a **silent no-op** — no
   exception, port still opens fine. Before trusting mvc-refactor on real
   hardware: reflash (or check firmware version) and smoke-test enable/
   disable physically.

**Phase 2 (P1 stability) — done except explicit backlog:**
6. `app.py` `run_web_app()` — installs `sys.excepthook` + `threading.excepthook`
   (web is the default view on macOS and previously had neither; verified
   end-to-end with a synthetic background-thread exception reaching
   `WebAPIHandler.error_buffer`).
7. `temperature_system.py` `read_serial_data()` — added an unconditional
   `time.sleep(0.01)` floor. Previously free-spun with zero throttle whenever
   `readline()` returned truthy data instantly (mock, or a misbehaving
   non-blocking port) — **reproduced live during this session**: RSS grew
   2GB+ in under a minute before I killed the process. Regression test added.
8. `temperature_system.py` `stop()` — fixed `.2f`→`.1f` formatting mismatch
   (the three existing tests required different precision per method; don't
   "fix" this by making all three the same format, that was already checked
   and breaks a passing test).
9. Multiprocess crash-isolation regression (main isolates each device in its
   own OS process; mvc-refactor runs everything in one process) — **backlog,
   deliberately untouched**, too large for this pass.
10. Qt SIGABRT in `QApplication.__init__` — **root cause found, not a Python
    3.14/PySide6 incompatibility.** macOS marks pip-downloaded `.dylib` files
    `UF_HIDDEN`, which makes Qt's plugin scanner silently skip them. Fix
    already existed in `run_macos.sh` (`chflags -R nohidden` on the PySide6
    dir) — I applied it to this venv. **If pytest crashes with a Qt
    `qt_check_pointer`/`QMessageLogger::fatal` abort again, re-run:**
    ```
    chflags -R nohidden .venv/lib/python3.14/site-packages/PySide6/
    ```
    This can reset if the venv is reinstalled/upgraded. You were verifying
    the remaining pytest-qt flakiness by hand when this session ended —
    isolated files passed for me (`test_model_interactions.py`,
    `test_port_scanning.py`, `test_ui_schema.py` all green standalone after
    the chflags fix); running the full `tests/ui/` directory together
    intermittently re-hit the abort and I did not finish isolating why
    (possibly a fixture/teardown ordering issue between files, not the
    UF_HIDDEN issue itself). Don't re-chase the UF_HIDDEN angle — that part
    is solved. If it recurs, look at fixture teardown order instead.

## Known-broken things found but NOT fixed (out of approved scope)

- `tests/edge_cases/test_edge_mvc_state_transitions.py::test_ui_schema_no_longer_has_system_enabled_toggle`
  — fails, pre-existing, unrelated to anything this session touched.
- `tests/hardware/test_gamepad.py::test_xbox_gamepad_mapping` — D-pad
  polarity test contradicts both the current code and main's confirmed
  original behavior. This is **plan item 13** (Phase 3, not started) —
  recommendation is to fix the test, not the code, but pending a 30-second
  physical D-pad hardware check first. Don't just flip it.
- **New find, not in the original plan:** `RedPercentSystem` is missing a
  `set_focus_area_ui` method that its own `ui_schema` references (from the
  Phase-0-committed AGY red-percent/web work). Fails
  `tests/ui/test_ui_schema.py::test_ui_schema_commands_exist_and_callable`.
  Needs triage — either implement the method or remove the schema entry.

## Not started

- Plan item 5 (yours — physical firmware check, see above).
- Phase 3 / P2 (plan items 11–15): web setup validation bug (missing `port`
  returns 200 not 400), Tkinter red-percent "Plot Data" stub, D-pad polarity
  test fix (see above), `view.py`/`view/` package collision cleanup
  (currently patched via an `importlib` double-load shim — works but
  fragile), `seiral.py` name-shadowing landmine in `app.py`.
- Phase 4 / P3 backlog (plan items 16–19): `tests/in_progress/` cleanup,
  `pytest.ini` `addopts` silently excluding ui/web/concurrency from a bare
  `pytest tests/` run, the blanket hardware-library mocking in `conftest.py`
  that let several of the above bugs ship undetected, tracked `.DS_Store` /
  stray `.coverage` / missing `requirements-dev.txt`.

## Execution model this session used (if resuming with fleet/AGY delegation)

Judgment calls (firmware coordination, the "defer while active" decision,
ramp-rate unit semantics, D-pad polarity sign-off, the Qt root-cause dig)
stayed with Claude. Fully-specified mechanical diffs (items 1, 3, 4, and 6's
excepthook wiring) were drafted via the local fleet router
(`router refactor` / `router patch-plan`, local-27b) and reviewed/applied/
tested by Claude before landing. `router` is on PATH at
`/opt/homebrew/bin/router`.

## Quick verification commands

```bash
cd mvc-refactor
chflags -R nohidden .venv/lib/python3.14/site-packages/PySide6/   # if Qt aborts
.venv/bin/python -m pytest tests/core/ tests/hardware/test_gamepad.py \
  tests/edge_cases/ tests/scripting/ tests/web/ -v
```
Expect 2 pre-existing failures (D-pad polarity, orphaned ui_schema test) and
otherwise green, including the new interlock/step-size/throttle/formatting
regression tests added this session.

## Phase 5 — Deep MVC Architectural Audit (New Findings)

### 1. Controller Layer Fallacy & Direct View-Model Coupling
- **Missing Software Controller**: `src/controller/` strictly contains Hardware Abstraction Layers (HAL) like `gamepad.py` and `seiral.py` (typo). There is no actual MVC software Controller mediating the flow. 
- **Direct Reflection Routing**: In both PySide (`view_pyside.py`) and Web (`src/view/web_adapter.py`), UI inputs use dynamic `getattr`/`setattr` reflection to directly mutate the Model layer.
- **Inverted Ownership**: Models (like `probes.py` and `temperature_system.py`) directly import and instantiate the HAL drivers (`ControllerPoller` and `serial`) themselves, violating MVC boundaries.

### 2. View-Driven Control Loops & Concurrency Disasters
- **Web View Lacks Manual Control**: The physical gamepad loop is completely driven by PySide/Tkinter GUI timers (`QTimer`/`after`). The Web view (`web_server.py`) has no such GUI event loop, meaning gamepad inputs are **completely inoperative** in the web dashboard.
- **Interlock Hang in Web Mode**: Triggering "Manual Mode" in the web dashboard sets `manual_flag = True`, which permanentely disables the 5-minute safety interlock. Since the manual polling loop doesn't exist on the web, it hangs indefinitely in a dangerous state.
- **Serial Port Concurrency Clashes**: Background model threads (`_execute`, `read_serial_data`) directly write/read to `self.serial_comm.ser` without acquiring `_lock`. Concurrent UI polling intercepts these streams, leading to corrupted buffers and timeouts. 
- **Web Server Polling Contention**: The frontend polls `/api/state` every 50ms. Each poll synchronously queries all models in a tight multi-threaded loop without global locks on mutable model attributes (`pos_x`, flags).

### 3. Model Presentation Leaks & Legacy Regression
- **Dead Code in SystemManager**: `power_down()` (which cuts physical motor current to prevent overheating via byte `'k'`) is shadowed by an unreachable `elif hasattr(model, 'power_down')` block, as `disable()` always takes precedence.
- **RotatorSystem GUI Crash**: `RotatorSystem._confirm_rotation()` directly imports Qt's `QMessageBox`. In headless or web environments, attempting rotations past ±30° causes silent blocking or hard Qt `SIGABRT` crashes. 
- **Schema Tight Coupling**: Domain models construct a `ui_schema` that dictates presentation-level styling (e.g. `{"bg": "darkblue", "fg": "white"}`).
- **Dropped Multiprocessing Guarantees**: `main` cleanly isolated every device into separate `multiprocessing.Process` instances. `mvc-refactor` collapses everything into one OS process, risking the entire instrument on a single thread exception (like the free-spinning temp loop memory leak).

**Recommendation**: The `mvc-refactor` branch currently masquerades as MVC. To achieve feature parity with `main` and restore safety, a dedicated Controller broker thread is required to sever the direct REST-to-Model and GUI-to-Model reflection pipelines, properly multiplex serial I/O, and re-implement a headless-compatible manual polling loop.

### 4. View Layer Architecture & Feature Parity Failures
- **Gamepad Control is Inoperative**: Joystick polling and `send_manual_mode_command()` were completely omitted from the Web view. Manual stage motion via physical controller is entirely broken on the dashboard.
- **Uncommitted Text Inputs**: In PySide6/Tkinter, `<FocusOut>` automatically commits parameters. In `app.js`, inputs are only committed if the user explicitly presses `Enter`. Clicking a command button immediately after typing runs the command on stale values.
- **Schema & UI Contract Disconnects**: 
  - Dropdowns render empty because they expect static array `options` instead of `options_command`.
  - `file_picker` schema types are dropped in HTML generation, breaking script loading.
  - RedPercent focus area selection (`set_focus_area_ui`) and sync dimension toggles fail with HTTP 400 because commands are improperly intercepted or incorrectly named in the model.
- **Resource Leaks**: Re-running the Web Setup wizard overwrites `system_manager` without closing existing models, permanently leaking serial ports. Additionally, `WebDashboardWindow.close()` holds a stale reference to the initial manager, failing to de-energize hardware on exit.
- **Synchronous Server Thread Block**: `web_adapter.py` queries `model.read_position()` directly on the HTTP handler thread, blocking the server on serial I/O.
- **Missing Tab-Blur Safety**: PySide6/Tkinter trigger `model.poller.flush_neutral()` when the window loses focus to prevent runaway stages. The Web view has no `window.onblur` handler, posing a physical hardware hazard if a user changes tabs.
- **Variable Scope Leak**: In `web_adapter.py:207`, `is_enabled` leaks from a previous loop scope, causing all devices to be wrongly flagged as disabled if the final configuration in the list was disabled.
- **Import Shadowing & Subprocess**: Gamepad detection uses `subprocess.run(["python3"])` instead of `sys.executable`, failing silently in venvs. Additionally, `src/view/__init__.py` has a fragile double-load `importlib` monkeypatch masking the `src/view.py` collision.

## Loop 1 Findings: Controller & MVC Intersections
* **Architectural Strain**: The `src/controller` directory is actually a Hardware Abstraction Layer (HAL), containing `gamepad.py` and `seiral.py` (which is a typo). The Domain Models directly instantiate these HAL drivers, and dictate their own UI layouts via a `ui_schema` property (inverted MVC coupling). The only true MVC Controller is `SystemAdapter` inside `src/view/web_adapter.py`.
* **Typo & Import Poisoning**: `seiral.py` was renamed from `serial_comm.py` and contains `class serial:`. In `app.py`, this shadows `pyserial`, causing silent clobbering.
* **Leaky Abstraction**: `TemperatureSystem` bypasses `seiral.py` API and directly reaches into the raw socket `self.serial_conn.ser`.
* **Global Lock Bottleneck**: `SystemManager` (`src/model/system_manager.py`) uses a single global lock for all operations. A `reboot_model` sleep freezes access to all other hardware models across all threads.
* **Module Collision**: `src/view.py` and `src/view/` exist in the same directory, handled via a fragile `importlib` double-loading shim.

## Loop 1 Findings: Model Layer
* **Architectural Inversion (`ui_schema` Pollution)**: All domain models dictate their own UI layouts and styling (e.g., button colors) through a `ui_schema` property. Models also contain UI adapter methods with `_ui` and `_web` suffixes, completely breaking MVC separation.
* **Hardcoded GUI Imports in Domain**: `RotatorSystem` imports `PySide6.QtWidgets.QMessageBox` directly in the domain model. In non-Qt environments (Web, headless), `QApplication.instance()` is None, which auto-aborts all rotations exceeding ±30°.
* **Stringly-Typed Models & Silent Error Masking**: Core numerical states (`pos_x`, `step_size`) are stored as strings for UI two-way binding. The internal `_num()` conversion catches errors and silently falls back to arbitrary defaults (0 or 1), masking invalid inputs.
* **"Looking Nice" vs Functional Correctness**: Commit `3dd8978` changed `ui_schema` commands to `set_focus_area_ui` and `save_log_web` for the Web dashboard. `view_pyside.py` still expects the old names, so clicking these buttons in PySide6 is now completely dead.
* **State Simulation**: Movement flags (`is_stepping`, `manual_flag`) are local Python booleans rather than hardware telemetry. If the microcontroller stalls, `is_stepping` stays `True`, permanently suppressing the 5-minute inactivity watchdog.
* **Thread Affinity Violation**: The 5-minute inactivity watchdog daemon directly invokes `ErrorPopupManager.report_info()`, violating GUI thread affinity.
* **Missing Features vs Legacy**: The legacy DC Probe color test and interactive plotting features were dropped or replaced by empty stubs (`pass`).

## Loop 1 Findings: View Layer & Web Parity
* **Synchronous Blocking I/O in HTTP Handler**: `WebModelAdapter.get_state()` loops over all devices sequentially, executing blocking serial I/O (e.g., Arduino, SMC100) inside the `ThreadingHTTPServer` worker thread. A single timeout freezes state polling and control commands for the entire dashboard.
* **Protocol Mismatch & Latency**: The Web View relies on 1000ms HTTP polling instead of native views' sub-50ms deterministic event loops, introducing unacceptable jitter and state staleness for a nanometer-scale micromanipulator.
* **Gamepad Driving Inoperative**: The web view never invokes `ControllerPoller.start_polling()`. The polling thread and manual input routing loops do not exist, rendering physical gamepads completely non-functional.
* **Focus Area ROI Regression**: Native views used a transparent, frameless full-desktop canvas (`SelectionOverlay`) for direct interaction over live camera feeds. The Web View attempts desktop screen capture via `mss` and streams base64 PNGs over HTTP, resulting in severe lag (2 FPS), high CPU, and multi-monitor scaling breakage.
* **Missing Multi-Baud Autodetection**: Native views actively probed hardware across 3 baud rates for auto-identification. The Web setup only lists raw OS comports, forcing manual user guessing.
* **Broken Global Emergency Stop (Critical Hazard)**: The web UI searches for any command containing `"stop"`. Physical probes use `full_stop()`, so the web UI falls back to non-existent `model.stop()` and silently fails to halt physical motion.
* **Data Loss & Hardcoded Overwrites**: Stopping monitoring in the web view simply halts without prompting to save (unlike PySide6). The "Save Log" web button hardcodes output to `"redpercent_log.csv"`, silently overwriting previous experimental data with no browser download trigger.
* **Broken Dynamic Dropdowns & Toggles**: Schema dropdowns declaring `"options_command"` are not evaluated in `web_adapter.py`, rendering them empty. Toggle attributes (e.g., `Sync X`) dispatch undefined commands instead of setting the model attribute.

## Loop 2 Findings: Test Suite Blind Spots & Mocking Fallacies
* **Global `sys.modules` Poisoning (`conftest.py`)**: `serial`, `pygame`, `tkinter`, and vision libs are replaced with `MagicMock` at import time. This provides tautological success for all serial I/O, ignoring framing, timeouts, and baud rates.
* **Dangerous Exception Collapsing**: `conftest.py` sets `SerialException = Exception`. This causes any `except SerialException` block to catch `KeyError`, `AttributeError`, etc., masking genuine software bugs as "serial disconnects".
* **Toy Mock Fixtures in Web Tests**: The 21 web server tests run against a synthetic `MockDeviceModel`. The real hardware models are never tested through the HTTP API, granting false confidence while real schemas fail.
* **Superficial Schema Verification**: `test_ui_schema.py` ignores `options_command` entirely (allowing dynamic dropdowns to render empty in the UI) and skips command checks for UI toggles, leading to toggles dispatching `undefined` and throwing HTTP 400s.
* **Zero Emergency Stop Coverage**: There are no tests for the global emergency stop. The JavaScript frontend attempts to call `stop()` on devices (which use `full_stop()`) and silently swallows the HTTP 400 error via `.catch(() => {})`, leaving hardware actively driving during a physical emergency.
* **Broken Collection Masking Latent Failures**: Running pytest directly fails collection. With `PYTHONPATH=src`, `test_ui_schema.py` immediately fails because `RedPercentSystem` is missing `set_focus_area_ui`.

## Loop 2 Findings: Hardware Libraries & Protocol Intersections
* **Firmware Desynchronization (`'e'`/`'d'` vs `'t'`)**: Legacy firmware toggles state on `'t'`, while `mvc-refactor` sends explicit `'e'` and `'d'`. Cross-version execution causes silent discard of commands or permanent phase inversion.
* **Buffer Poisoning (28-byte vs 42-byte structs)**: Legacy `main` expected 28-byte structs for stepper/chuck, while `mvc-refactor` sends 42-byte structs. Sending a 42-byte struct to legacy firmware leaves 14 bytes in the serial ring buffer, permanently corrupting all subsequent `0xAA` start markers.
* **Phantom Coil Shutdown (`b'k\n'`)**: `BaseProbe.power_down()` transmits `b'k\n'` to cut motor power. This command does not exist in *any* firmware and is silently eaten.
* **Binary Stop Ignored**: In stepper firmware, `incomingPacket.mode == 0` simply breaks execution without actually invoking `handleAllStop()`.
* **Blocking I/O Freezes**: `poll_status()` for the SMC100 Rotator is executed synchronously in the PySide GUI thread and the Web HTTP handler. Any serial latency completely freezes the desktop UI and web clients. DTR auto-reset blocks UI initialization for up to 9 seconds during port scanning.
* **Temperature Daemon Fragility**: The background temperature polling thread executes `break` on *any* transient serial error, permanently terminating thermal monitoring with no reconnect attempt. Write collisions also occur because `TemperatureSystem.send_settings` bypasses the driver mutex.
* **Control Polarity Inversion**: D-Pad states in `gamepad.py` were explicitly negated (e.g., `-self.prev_hat_states`), causing physical stage hardware to move in reverse compared to legacy `main`, and explicitly failing unit tests.

## Loop 2 Findings: Concurrency & Process Isolation
* **Zero Crash Blast Isolation**: Legacy `main` ran true multiprocess isolation for each hardware component. `mvc-refactor` is a single-process monolith. A segfault in any C-extension (Toupcam, SMC100, Pygame) terminates the entire stage suite simultaneously.
* **Thread Affinity Violations**: In Tkinter mode, background port-scanning threads directly read Tk string variables (`check_var.get()`), risking interpreter memory corruption. In PySide6, gamepad hardware polling executes entirely on the Qt GUI event loop, meaning any USB joystick stall freezes the desktop interface.
* **Deadlock Vulnerabilities & Coarse Locking**: `SystemManager` utilizes a single, non-reentrant `threading.Lock()`. During `reboot_model`, this lock is held across a 1-second `time.sleep()`, stalling all threads globally. It is also held during error reporting (potentially waiting on GUI modal dialogs) and risks self-deadlock if any teardown hook calls back into the manager.
* **Race Conditions**: Both `view_pyside.py` and `web_adapter.py` directly mutate and iterate over `system_manager.active_models` and `active_claims` without acquiring the manager's lock, leading to `RuntimeError: dictionary changed size during iteration` when connecting/disconnecting devices.
* **Energized Coils on Teardown**: The teardown sequence in `SystemManager` checks `if hasattr(model, 'disable')` before `elif hasattr(model, 'power_down')`. Because `BaseProbe` implements `disable()`, the `power_down()` method (which cuts motor holding current) is permanently shadowed and never executed, leaving hardware coils fully energized upon application exit.

## Loop 3 Findings: Web API Security & Validation
* **Unrestricted Method Execution (RCE risk)**: `/api/command` dynamically executes `getattr(model, command)`. There is no allowlist, meaning unauthenticated local network clients can execute any private or dunder method on the hardware models (e.g., `__del__`, `disconnect`).
* **Arbitrary Attribute Mutation**: `/api/set_attr` allows writing to any model attribute, bypassing the `ui_schema` entirely. Clients can overwrite `serial_comm` or `pos_x` directly, or inject new arbitrary attributes.
* **Host Desktop Exposure**: `/api/screenshot` captures and streams the host machine's primary desktop via `mss` without authentication, exposing the user's screen to the local network.
* **Permissive CORS Vulnerability**: The server responds with `Access-Control-Allow-Origin: *`. If the host user visits a malicious external website, that site can issue cross-origin requests to `http://127.0.0.1:8080/api/command` and drive the physical hardware.
* **Information Disclosure**: API endpoints return raw Python stack traces (`traceback.format_exc()`) directly in HTTP 500 responses.
* **Unbounded Memory Leak**: The `error_buffer` list grows infinitely and is only cleared when a web client explicitly polls `/api/errors`. If no client is connected, hardware errors will leak memory until the process crashes.

## Loop 3 Findings: Error Routing & Swallowed Exceptions
* **Swallowed Hardware Faults**: `except Exception: pass` is aggressively used throughout `view_pyside.py`, `web_adapter.py`, and `gamepad.py` when reading hardware state. Disconnects and framing errors are silently eaten without alerting the UI.
* **Fragile Polling Threads**: In Tkinter, the `.after()` hardware polling loop lacks try/catch blocks; a single transient read error permanently breaks the loop. The `TemperatureSystem` background thread catches errors but executes `break`, permanently terminating the thread without reconnect attempts.
* **Missing Thread Excepthooks**: While the Web mode installs `threading.excepthook`, both Tkinter and PySide6 frontends fail to do so. Background threads that crash with unhandled exceptions terminate in total silence.
* **Unsynchronized Error Broker**: `ErrorRouter._last_messages` is mutated across arbitrary UI and background threads without a lock, risking `RuntimeError` during iteration.
* **Context Destruction**: `ErrorRouter.report_error()` incorrectly calls `traceback.print_exc()` outside of active except frames (printing `NoneType: None`). The Web view explicitly strips tracebacks from exceptions before transmitting them to the client.
* **Modal Dialog Blocking & Unparented Focus**: PySide6 error popups pass `parent=None`, creating unparented dialogs that steal OS focus and stack behind windows. Tkinter error popups block the main event loop while open, completely stalling the hardware interface.
* **Destructive Web Polling**: The web `/api/errors` endpoint destructively pops the error buffer. In a multi-tab scenario, the first tab steals the errors, leaving the others blind.

## Loop 3 Findings: State Persistence & Calibration Volatility
* **Silent Experimental Data Overwrite**: The `save_log_web()` method hardcodes the output path to `redpercent_log.csv` and opens it in write (`'w'`) mode. Every invocation silently obliterates previous experimental runs in the working directory without any prompt, timestamping, or browser download.
* **Bypassed Native File Dialogs**: Because the `ui_schema` command was renamed to `save_log_web`, PySide6 fails to intercept it (it still looks for `save_log`). Consequently, clicking "Save Log" in PySide6 bypasses the native `QFileDialog` and also silently overwrites the hardcoded CSV.
* **Ephemeral Calibration (Hardware Risk)**: Crucial physical parameters like `TemperatureSystem` offsets, step bounds, and optical ROI coordinates are stored purely as ephemeral strings in memory. They reset to defaults on every application restart. Forgetting to re-enter a thermal offset risks physically burning the sample.
* **Dropped Configuration Manager**: Legacy `main` included a `CameraPresetManager` (`camera_control.py`) to serialize exposure/gain settings to JSON. This entire feature and module was omitted from `mvc-refactor`.
* **Logging Engine Crashes & Blind Spots**: The CSV logger completely omits timestamps. It also employs a "dead-zone" filter (only logging if values change by >= 0.1), which halts coordinate tracking during uniform sample translations. Additionally, toggling an axis (Sync X/Y/Z) dynamically after monitoring has started triggers an unhandled `KeyError` and crashes the logging daemon.
