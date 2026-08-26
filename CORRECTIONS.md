# MVC Refactor Corrections Log

This file documents all bug fixes, architectural repairs, and regressions addressed after comparing the `mvc-refactor` branch to the legacy `main` branch. The corrections are grouped chronologically by development phase to maintain a clear history of the project's evolution.

---

## Phase 1: Hardware & Serial Protocol Fixes
Early fixes focusing on device communication, port scanning, and peripheral inputs.

### Application Initialization & Port Scanning
- **File:** `src/main_app.py`
- **Correction:** The `_scan_ports_thread` auto-detection logic was failing because it only gave a 1.5s window. Most Arduino bootloaders take 1.5–2s to run after a DTR reset upon opening the serial port. Increased the timeout to 3.0s and added an initial 1.5s sleep to wait for the bootloader before sending the `s\n` ping.
- **Correction:** Fixed a UI bug where the controller dropdowns were completely disabled/missing. The boolean evaluation used bitwise `&` operators for string comparisons, which failed. Swapped to logical `and`.

### Serial Communication Pipeline
- **File:** `src/hardware/seiral.py`
- **Correction:** Increased the same Arduino bootloader timeout in the `serial` initialization to 3.0s.
- **Correction:** Fixed a chunking bug where fast polling caused the firmware's `DEV:` response to arrive in multiple fragmented packets. Replaced the strict string overwrite with a `buffer` string that accumulates responses, ensuring the handshake succeeds even if fragmented.

### Gamepad Mappings & Overrides
- **File:** `src/hardware/gamepad.py`
- **Correction:** Restored the Y-Axis binding. The refactored abstraction incorrectly mapped `y_axisStatus` to the Left Stick Y (axis 1). The machines were originally wired to use the Right Stick Y (axis 3 for Windows/Mac, axis 4 for Linux).
- **Correction:** Fixed `BluetoothXboxGamepad` mapping on Linux, which accidentally mapped `y_axisStatus` and `z_axisStatusL` (Z-) both to axis 4 (Left Trigger).
- **Correction:** Restored the platform-specific logic for the `T16000MGamepad`, correctly assigning Z+ and Z- to axes 9 and 10 based on whether it is running on Windows or Linux.

### Model-Specific Hardware Fixes
- **Temperature System (`src/domain_models/temperature_system.py`)**
  - Fixed a crash where the system attempted to call `.write()` directly on the `serial` wrapper object rather than its underlying PySerial `ser` object.
  - Repaired a math bug in the `stop()` method. It was sending the raw degrees/minute value to the firmware instead of properly calculating the `spdelay` (seconds/degree).
  - Restored the initial burst command `b"<0,10,0,0,0,0>"` in the constructor to zero out and synchronize the firmware upon connection.
- **Rotator System (`src/domain_models/rotator_system.py`)**
  - Fixed a critical indentation error. The initialization variables `self.smc`, `self.is_connected`, and `self.error_callback` were indented inside the `error` property's setter. This caused a crash on instantiation and resulted in the serial connection being wiped out every time the system polled the error state.
- **Probes & Probe Views (`src/domain_models/probes.py`, `src/ui_views/probe_view.py`)**
  - Removed an errant `self.active_claims = {}` from the `run_script` method, which was unintentionally wiping out all controller mappings when called.
  - Re-implemented the explicit `self.model.full_stop()` command when the user switches between Manual and Autonomous modes, ensuring the hardware halts immediately and drops any stale commands.
- **Red Percent Logging (`src/domain_models/redpercent_system.py`)**
  - Added a `threading.Lock()` to `RedPercentDataLog` to prevent `IndexError` vulnerabilities. The background monitor thread could append to arrays while the main thread simultaneously attempted to iterate over them during `save_to_csv()`.
  - Re-implemented the `0.1` percentage change threshold. The new system was logging blindly at 60FPS (~0.016s), causing immense CSV bloat. Now it only logs when a measurable change occurs.
  - Prevented `start_monitoring()` from overwriting `self.data_log`. If a user paused and restarted monitoring, their previous CSV data was wiped from memory before saving.

---

## Phase 2: Architecture Standardization & True MVC Alignment
Refactoring the codebase structure, implementing explicit Controller and View separation, and decoupling the UI from data models.

### Protocol Standardization
- **Firmware:** Refactored `stepper_firmware.ino` and `chuck_firmware.ino` to use strict 42-byte float structs (`ManualControlPacket`), aligning completely with `high_polling_rate.ino`. This prevents 16-bit vs 32-bit `int` sizing vulnerabilities.
- **Python:** Updated `BaseProbe` packet format to `<BBffffffffff` strictly across all systems.
- **Polling Loop Decoupled:** Shifted the controller polling loop from `main_app.py` into the respective UI Views (`ProbeView.start_polling()`). This cleans up the main app and adheres to MVC by making the View responsible for manipulating the Domain Model continuously via its own Tkinter `after` hooks.

### MVC Realignment & Restructuring
- **Naming & Directory Swap:** Swapped the contents of `src/model` and `src/controller` to adhere strictly to standard MVC definitions. Hardware communicators (`seiral.py`, `gamepad.py`) are now categorized as `controllers`, while stateful hardware abstractions (`probes.py`, `temperature_system.py`) are now `models`. Internal references were updated: `model` (formerly `hardware`), `controller` (formerly `models` / `domain_models`), and `view` (formerly `ui` / `ui_views`).
- **Dependency Injection:** Removed the "patchwork" dependency injection inside `main_app.py`. The Domain Models now directly instantiate and encapsulate their own `serial` and `ControllerPoller` instances, restoring true object ownership.
- **Import Path Fixes:** Deployed a fleet of subagents to dynamically rewrite all import statements across the codebase, ensuring correct loading paths post-directory swap.

### Generic View Engine
- **Project Flattening:** The `src/view` folder was entirely removed to reduce clutter. The `DashboardWindow` parent container and `DynamicView` engine have been moved to a single top-level `src/view.py`. The `SetupWindow` in `src/app.py` (formerly `main_app.py`) simply generates domain models and passes them into the `DashboardWindow`.
- **Model-driven UI Generation:** All standard models now possess a `ui_schema` property defining their view structures. `DynamicView` constructs the interface dynamically at runtime based on these schemas, supporting full two-way bindings.
- **Custom View Embeddings:** Highly-specialized views (like `RedPercentView` and `RotatorView`) were placed directly within their respective model files in `src/model/`. Models specify them via `self.custom_view_class`, allowing `DashboardWindow` to fall back to bespoke UI objects seamlessly.

---

## Phase 3: Regression Audits & Test Suite
Catching and fixing regressions introduced during the massive MVC refactor, and building automated testing.

### Regression Fleet Fixes
- **Gamepad Bindings:** Fixed a double-swapping regression for the Thrustmaster T.16000M Z-axis bindings.
- **Probe Instantiation:** Fixed a CRITICAL crash where `DCProbe` and `ChuckPositioner` mistakenly used `(self, serial_comm)` in their `__init__` instead of the required `(self, port, controller_id)`.
- **Dynamic Schema Additions:** 
  - Restored `slow_speed` and `brake_distance` entries for `DCProbe` via `ui_schema` overrides.
  - Implemented a `file_picker` type in `DynamicView` and completely restored `gcodeparser` script parsing in `BaseProbe`.
  - Added a manual "Serial Reconnect" button to the `BaseProbe` schema.
- **Temperature Controller:** 
  - Fixed a regression where hitting "Stop System" inadvertently closed the serial port, permanently killing the connection instead of just zeroing the setpoint.
  - Corrected the hardware handshake initialization string `b"<0,6.0,0,0,0,0>"` to properly reflect the new default 10 degrees/minute ramp rate.
  - **Serial Read Fix:** Restored the background thread responsible for reading `ser.readline()` which was lost during the refactor. The system correctly performs non-blocking reads from the serial port to update `self.current_temp`.
- **Rotator Auto-Detect Fix:** Fixed a regression where the auto-detect logic was scanning for the literal string `"SMC100"` instead of `"1ID"` or `"1TS"`. Restored exact detection behavior and software flow control (`xonxoff=True`).
- **Auton and Manual State Bug:** Fixed an issue in `probes.py` where `enter_auton` and `enter_manual` were calling `self.full_stop()`, which inadvertently reset both state flags to `False`. Implemented `send_stop_command()` which zeros out movement without clearing internal flags.
- **Serial Packet Parsing:** Restored manual packet format for legacy compatibility to `<BBfffhhhhhhh` (28 bytes) instead of the erroneous 42-byte format in `controller/seiral.py`.
- **MVC Regressions Restored:** Subagents successfully added missing UI mappings (`serial_port`, `color_test_window`) for `probes.py`. Re-implemented realtime console prints (`BASELINE SET`, `RED: X%`) and the "Save Log" button for `redpercent_system.py`.
- **GUI Engine Migrations:** Migrated the orphaned `DraggableClosableNotebook` logic from `app.py` to `view.py`. Restored the 57600-baud fallback in the scanner thread.

### Testing Suite Implementation
- **Directory:** `tests/`
- **Addition:** Implemented a new pytest suite focusing on the edge-case interactions between models and controllers (e.g., `test_gamepad.py`, `test_model_interactions.py`, `test_serial.py`). This isolates hardware and successfully detects when refactors break functional application flows.

---

## Phase 4: PySide6 Overhaul & Dual-Boot Integration
Migrating the UI engine from Tkinter to a modern Qt framework and resolving system-level framework collisions.

### SystemManager & PySide6 Architecture Migration
- **Files:** `src/model/system_manager.py`, `src/view_pyside.py`, `src/app.py`
- Implemented a platform-agnostic PySide6 overhaul. 
- Created `SystemManager` to decouple object lifecycles from the UI (allowing model reboots without killing the main window).
- Created `QtDynamicView` and `DashboardWindow` to replace `ttk.Notebook` with native tear-off `QDockWidget` floating windows.

### UI Layouts & Framework Stability
- Changed PySide6 `DashboardWindow` to utilize `splitDockWidget` when launching multiple devices, automatically organizing them into clean vertical partitions (side-by-side columns).
- Disabled the close button on the `Device Manager` sidebar, making it a permanent embedded widget.
- Resolved a critical crash (segmentation fault) that occurred upon closing PySide6 floating docks by safely decoupling their memory destruction lifecycle from the visibility event handler.
- **Rotator UI Restoration:** Completely purged legacy Tkinter `RotatorView` frame. Generated a native PySide6 `ui_schema` property for the Rotator model. Fixed a bug where toggle colors (green/red) would not initialize properly on boot.

### Headless Mode Integration
- Added native "Headless" support inside `src/app.py`. Any physical hardware device can now be assigned "Headless" from the COM port dropdown, routing them to the `SerialDrive` simulator (`'SIM'`).
- Modified port collision checks to explicitly ignore simulated headless ports, meaning multiple devices can be launched entirely virtually.

### Dual-Boot Application Launcher
- **Framework Collisions:** Encountered macOS framework collisions where `tkinter` and `PySide6` fundamentally cannot exist in the same Python process without triggering security crashes.
- **Process Launcher (`src/app.py`):** Implemented a process-replacing launcher. Upon execution, a launcher window asks the user which engine to boot into. It leverages `os.execv` to completely overwrite the Python process in memory, ensuring perfectly clean framework loads for both PySide6 and Tkinter.
- **Legacy Tkinter Retained:** Re-added the complete original Tkinter Setup/View stack as `src/app_legacy.py`, fully preserving embedded 3D Matplotlib plotting tools that could not be mapped to PySide6 `ui_schema`.

### Subagents, Repository & Script Consolidation
- **Active Subagents:** 
  - `feature_validator`: Mapped and unit-tested all PySide6 `ui_schema` fields against backend object models.
  - `mvc_refactor_fixer`: Debugged and solved a catastrophic macOS initialization conflict between SDL (Pygame) and Qt (PySide6).
- **Repository Consolidation:** Cleaned up a severe Git sync conflict that had duplicated files. Resolved oversegmentation by merging `app_legacy.py` and `app_pyside.py` into isolated functions within `src/app.py`.
- **Run Scripts:** Modernized `run.sh` and `run.bat` by replacing hardcoded absolute paths with dynamic directory targeting (`cd "$(dirname "$0")"`), injecting `.venv` activation, and setting them to instantly bypass the launcher to boot PySide6 directly via the `--pyside` flag.
- **Cache Purge:** Performed a final manual flush and forced extraction of the PySide6 binaries to clear macOS `dyld` cache corruption caused by intentional `test_tk.py` collision testing, permanently restoring `libqcocoa.dylib` Cocoa framework hooks.

---

## Phase 5: Controller & Interface Polishing
Recent fixes focusing on gamepad interaction stability and UI refinements.

- **Controller Enumeration Fix:** Updated PySide6 Setup Window to properly query device names from PyGame (e.g., "Joy 0: Xbox Series X Controller") and implemented safe string parsing to prevent application crash on launch.
- **Dpad/Shoulder Edge Detection:** Fixed a bug where holding Dpad/Bumper buttons caused excessive, continuous stepping in the manual polling loop. Implemented edge-detection logic so that these buttons trigger exactly one step per physical press.
- **Controller Log Window Fix:** Fully implemented the `ControllerLogWindow` as a floating PySide6 `QDialog` that is hidden by default and only pops up when the dedicated UI button is clicked, preventing it from automatically spamming the screen on system enable or manual mode entry.

## Phase 6: Validation Testing & Hotfixes
Deep validation suite executed to identify hidden vulnerabilities, memory leaks, and edge-cases across both `main` and `mvc-refactor` branches.

### MVC Refactor Architecture Fixes
- **Robust Controller Parsing:** The `controllerID` parsing logic was highly fragile when dealing with missing spaces, nulls, and Unicode/emojis. It was extracted into a standalone, robust `parse_controller_id()` module-level function.
- **Enable Logic Redesign:** Replaced a generic "Enable System" toggle with mutually exclusive `toggle_auton()` and `toggle_manual()` commands to eliminate state conflicts, while successfully preserving the automated 5-minute inactivity watchdog.
- **Async run_script Execution:** The `run_script` method for gcode was executing synchronously, entirely freezing the PySide6 UI loop. It was wrapped in a daemon thread, with robust error routing injected via `ErrorRouter.report_error()`.
- **Global Exception Hooks:** Added a `try...except` wrapper inside the custom PySide6 `sys.excepthook` to prevent total GUI collapse when corrupted traceback objects (e.g., strings) were passed to it.
- **Memory Leak Resolution:** Implemented `Qt.WA_DeleteOnClose` and `deleteLater()` on all PySide6 dialog windows (`PlotDialog`, `ControllerLogWindow`) to ensure underlying C++ objects are garbage collected when users spam open/close, preventing severe memory exhaustion.
- **Serial Thread Safety:** Added `threading.Lock()` to the serial command queue to prevent autonomous string packets from interleaving with high-speed manual controller packets, which would otherwise crash the hardware parser.

### Legacy Main Branch Fixes
- **Modal Hang Fix:** Discovered the legacy Tkinter `messagebox.showerror` dialog could permanently lock the main thread if passed massive strings. Implemented a monkeypatch in `mainGUI.py` to truncate error strings to 5,000 characters and safely cast dictionaries/lists to string format before displaying.
- **Runaway Recursion Loop:** Fixed a bug where Tkinter UI polling functions (like position readers) would infinitely re-schedule themselves (`root.after()`) even when the modes were disabled or the application was attempting to close. Added rigorous `_is_polling` flags and `after_cancel()` cleanups to all `_frame.py` files.
- **Malformed COM Port Robustness:** The `get_arduino_port()` autodetect function would crash with an `AttributeError` if a highly malformed USB device without a `description`, `vid`, or `pid` attribute was scanned. Added safe `getattr()` checks with default fallback strings.
- **SetupWindow Double-Destroy Bug:** Fixed a race condition where calling `app.destroy()` multiple times during tear-down raised fatal `TclError`s. Overrode the `destroy()` method in `SetupWindow` to gracefully catch and discard redundant closure calls.
- **Pygame/Tkinter macOS Conflict:** Mitigated an initialization conflict (segfault) by deferring the global `pygame.init()` until after Tkinter finishes spawning its root window frame.
