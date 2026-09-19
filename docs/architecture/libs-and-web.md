# Libraries and Web Architecture

**Status as of last edit:** This document covers the vendor drivers and the web frontend architecture. Note that the web frontend is explicitly DEPRIORITIZED/legacy as of 2026-09-18 (see [README.md](README.md)), so the documentation here is for reference only.

## Part 1: Vendor Libraries

### `src/lib/smc100.py`

This file is a vendor driver for the Newport SMC100 motion controller, heavily depended on by `model/rotator_system.py`.

#### Public API Surface

**Class: `SMC100`**
The primary interface for communicating with the SMC100 hardware over a serial connection.

| Method | Purpose |
|---|---|
| `__init__(smcID, port, ...)` (`src/lib/smc100.py:85`) | Initializes the connection over the specified serial port. Does not automatically home or configure the hardware. |
| `reset_and_configure()` (`src/lib/smc100.py:133`) | Sends `RS` (reset) and then configures the controller by reading parameters from an ESP-compatible stage. |
| `home(waitStop=True)` (`src/lib/smc100.py:166`) | Homes the stage using the `OR` command and optionally waits until homing completes and the state is `READY_FROM_HOMING` or `READY_FROM_MOVING`. Follows up with an absolute move to 0. |
| `stop()` (`src/lib/smc100.py:188`) | Issues a standard stop (`ST` command). |
| `get_status(silent=False)` (`src/lib/smc100.py:191`) | Queries `TS?` and parses the hexadecimal response into an error code integer and a 2-character state string. |
| `get_position_deg()` (`src/lib/smc100.py:253`) / `_mdeg()` | Retrieves the current absolute position via the `TP?` command. |
| `move_relative_deg(dist_deg, waitStop=True)` (`src/lib/smc100.py:260`) / `_mdeg(...)` | Moves the stage relatively via `PR` and optionally waits for it to become ready again. |
| `move_absolute_deg(position_deg, waitStop=True)` (`src/lib/smc100.py:285`) / `_mdeg(...)` | Moves the stage to an absolute position via `PA` and optionally waits. |
| `wait_states(targetstates, ...)` (`src/lib/smc100.py:309`) | Blocking loop that polls `get_status` until the controller transitions into one of the requested target states. Raises exceptions if disabled states are hit unexpectedly. |
| `sendcmd(command, argument=None, ...)` (`src/lib/smc100.py:356`) | Constructs and sends `<ID><cmd><arg><CR><LF>` via serial. Has a built-in lock (`_serial_lock`) and handles read/write delays. |
| `close()` (`src/lib/smc100.py:484`) | Shuts down the serial connection. |

#### State Constants

State transitions mapping from page 65 of the manual.

* `STATE_NOT_REFERENCED_FROM_RESET` = `'0A'`
* `STATE_NOT_REFERENCED_FROM_CONFIGURATION` = `'0C'`
* `STATE_NOT_REFERENCED_FROM_HOMING` = `'0B'`
* `STATE_HOMING_FROM_RS232` = `'1E'`
* `STATE_HOMING_FROM_SMC_RC` = `'1F'`
* `STATE_MOVING` = `'28'`
* `STATE_READY_FROM_DISABLE` = `'34'`
* `STATE_READY_FROM_HOMING` = `'32'`
* `STATE_READY_FROM_MOVING` = `'33'`
* `STATE_CONFIGURATION` = `'14'`
* `STATE_DISABLE_FROM_READY` = `'3C'`
* `STATE_DISABLE_FROM_MOVING` = `'3D'`
* `STATE_DISABLE_FROM_JOGGING` = `'3E'`
* `STATE_NOT_REFERENCED` (Alias for `0A`)

### `src/lib/toupcam.py`

This file is a ctypes wrapper for a camera SDK (`toupcam.dll` / `libtoupcam.so`).

**Analysis:** A full repository scan confirms that `toupcam.py` is entirely unreferenced by the rest of the `src/` codebase. It is dead vendored code and can be removed safely.

## Part 2: Web View Architecture (Deprioritized)

*Note: The web frontend (`src/views/web/`) is deprioritized and considered legacy fallback as of 2026-09-18. This documentation is provided for reference.*

### SystemManager Wiring

The web layer uses `WebModelAdapter` (`src/views/web/web_adapter.py`) to bridge the web API and the core `SystemManager`. 
* When `/api/setup/initialize` is called, the adapter validates the configuration, constructs the domain models via `app_bootstrap.build_models`, and injects them into a new `SystemManager`. 
* It tears down the outgoing `SystemManager` safely outside the main state lock.

### Model Sharing

The web view **does not** maintain its own parallel set of hardware models. It utilizes the exact same domain objects (e.g., `probes.py`, `redpercent_system.py`) as the PySide6 and Tkinter views. They are instantiated and placed in `SystemManager.active_models`.

### Schema-Driven JSON API

Unlike the PySide6 and Tkinter views which dynamically construct GUI widgets directly from the `ui_schema`, the web view creates a dynamic JSON REST API based on the schema:
* `WebModelAdapter` inspects `ui_schema` to generate allowlists: `_schema_commands()`, `_schema_options_commands()`, and `_schema_attrs()`.
* When a frontend browser hits `/api/state`, it iterates through `ui_schema`'s `model_attr` entries to collect the state.
* `/api/command` and `/api/set_attr` use the allowlist to permit dispatching commands or modifying attributes.
* The actual web server (`WebAPIHandler` in `web_server.py`) serves this JSON, which the browser frontend then consumes. It does not output raw HTML beyond serving the static `/index.html` payload.

### Error Routing

The web view participates in the global error routing system via `WebErrorManager` in `src/views/web/web_view.py`.
* It calls `ErrorRouter.set_callbacks(...)`.
* Instead of displaying popups immediately, it pushes error dictionaries onto a circular buffer (`WebAPIHandler.error_buffer`).
* The browser polls for errors by calling `GET /api/errors`, popping them from the buffer and displaying them via the frontend UI.

## Findings for known-issues.md

* **Dead Code:** `src/lib/toupcam.py` is confirmed unused outside of itself and represents dead code that could be safely deleted. 

---
*Verified against commit d04d386.*

### Dated Addendum: 2026-09-18 (Second Extensive Pass)

#### SMC100 Public Method Signatures
The complete set of public method signatures for `SMC100` are:
* `__init__(self, smcID, port, backlash_compensation=True, silent=True, sleepfunc=None)`
* `reset_and_configure(self)`
* `home(self, waitStop=True)`
* `stop(self)`
* `get_status(self, silent=False)`
* `get_position_deg(self)`
* `get_position_mdeg(self)`
* `move_relative_deg(self, dist_deg, waitStop=True)`
* `move_relative_mdeg(self, dist_mdeg, **kwargs)`
* `move_absolute_deg(self, position_deg, waitStop=True)`
* `move_absolute_mdeg(self, position_mdeg, **kwargs)`
* `wait_states(self, targetstates, ignore_disabled_states=False)`
* `sendcmd(self, command, argument=None, expect_response=False, retry=False)`
* `close(self)`

#### SMC100 `STATE_*` Constants
Here are all `STATE_*` constants defined in `src/lib/smc100.py` and their meaning (cross-referenced with `get_status()`):
* `STATE_NOT_REFERENCED_FROM_RESET = '0A'`: NOT REFERENCED from reset
* `STATE_NOT_REFERENCED_FROM_CONFIGURATION = '0C'`: NOT REFERENCED from CONFIGURATION
* `STATE_NOT_REFERENCED_FROM_HOMING = '0B'`: NOT REFERENCED from HOMING
* `STATE_HOMING_FROM_RS232 = '1E'`: HOMING commanded from RS-232-C
* `STATE_HOMING_FROM_SMC_RC = '1F'`: HOMING commanded by SMC-RC
* `STATE_MOVING = '28'`: MOVING
* `STATE_READY_FROM_DISABLE = '34'`: READY from DISABLE
* `STATE_NOT_REFERENCED` = (Alias for `STATE_NOT_REFERENCED_FROM_RESET`, `'0A'`)
* `STATE_READY_FROM_HOMING = '32'`: READY from HOMING
* `STATE_READY_FROM_MOVING = '33'`: READY from MOVING
* `STATE_CONFIGURATION = '14'`: CONFIGURATION
* `STATE_DISABLE_FROM_READY = '3C'`: DISABLE from READY
* `STATE_DISABLE_FROM_MOVING = '3D'`: DISABLE from MOVING
* `STATE_DISABLE_FROM_JOGGING = '3E'`: DISABLE from JOGGING

#### `toupcam.py` Dead-Code Status
A repo-wide grep execution confirms that the claim of `toupcam.py` being entirely unreferenced in `src/` logic is **true with minor caveats**. 
Command executed: `git grep -in "toupcam"`
Citations of usage outside `src/lib/toupcam.py` are strictly non-functional towards core Python production code:
* `src/views/web/static/js/app.js:30`: A purely descriptive UI string (`desc: 'ToupCam Optical Flake Monitor'`).
* `tests/hardware/test_hal_round2.py:85-90`: Unit tests verifying `toupcam` mock fallback logic.

Conclusion: It is indeed functionally dead code in the primary runtime environment and can be removed (along with its associated test case and JS string reference).
