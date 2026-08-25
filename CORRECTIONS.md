# MVC Refactor Corrections Log

This file documents all bug fixes, architectural repairs, and regressions addressed after comparing the `mvc-refactor` branch to the legacy `main` branch.

## 1. Application Initialization & Port Scanning
- **File:** `src/main_app.py`
- **Correction:** The `_scan_ports_thread` auto-detection logic was failing because it only gave a 1.5s window. Most Arduino bootloaders take 1.5–2s to run after a DTR reset upon opening the serial port. Increased the timeout to 3.0s and added an initial 1.5s sleep to wait for the bootloader before sending the `s\n` ping.
- **Correction:** Fixed a UI bug where the controller dropdowns were completely disabled/missing. The boolean evaluation used bitwise `&` operators for string comparisons, which failed. Swapped to logical `and`.

## 2. Serial Communication Pipeline
- **File:** `src/hardware/seiral.py`
- **Correction:** Increased the same Arduino bootloader timeout in the `serial` initialization to 3.0s.
- **Correction:** Fixed a chunking bug where fast polling caused the firmware's `DEV:` response to arrive in multiple fragmented packets. Replaced the strict string overwrite with a `buffer` string that accumulates responses, ensuring the handshake succeeds even if fragmented.

## 3. Gamepad Mappings & Overrides
- **File:** `src/hardware/gamepad.py`
- **Correction:** Restored the Y-Axis binding. The refactored abstraction incorrectly mapped `y_axisStatus` to the Left Stick Y (axis 1). The machines were originally wired to use the Right Stick Y (axis 3 for Windows/Mac, axis 4 for Linux).
- **Correction:** Fixed `BluetoothXboxGamepad` mapping on Linux, which accidentally mapped `y_axisStatus` and `z_axisStatusL` (Z-) both to axis 4 (Left Trigger).
- **Correction:** Restored the platform-specific logic for the `T16000MGamepad`, correctly assigning Z+ and Z- to axes 9 and 10 based on whether it is running on Windows or Linux.

## 4. Temperature System
- **File:** `src/domain_models/temperature_system.py`
- **Correction:** Fixed a crash where the system attempted to call `.write()` directly on the `serial` wrapper object rather than its underlying PySerial `ser` object.
- **Correction:** Repaired a math bug in the `stop()` method. It was sending the raw degrees/minute value to the firmware instead of properly calculating the `spdelay` (seconds/degree).
- **Correction:** Restored the initial burst command `b"<0,10,0,0,0,0>"` in the constructor to zero out and synchronize the firmware upon connection.

## 5. Rotator System
- **File:** `src/domain_models/rotator_system.py`
- **Correction:** Fixed a critical indentation error. The initialization variables `self.smc`, `self.is_connected`, and `self.error_callback` were indented inside the `error` property's setter. This caused a crash on instantiation and resulted in the serial connection being wiped out every time the system polled the error state.

## 6. Probes & Probe Views
- **File:** `src/domain_models/probes.py`
- **Correction:** Removed an errant `self.active_claims = {}` from the `run_script` method, which was unintentionally wiping out all controller mappings when called.
- **File:** `src/ui_views/probe_view.py`
- **Correction:** Re-implemented the explicit `self.model.full_stop()` command when the user switches between Manual and Autonomous modes, ensuring the hardware halts immediately and drops any stale commands.

## 7. Red Percent Logging
- **File:** `src/domain_models/redpercent_system.py`
- **Correction:** Added a `threading.Lock()` to `RedPercentDataLog` to prevent `IndexError` vulnerabilities. The background monitor thread could append to arrays while the main thread simultaneously attempted to iterate over them during `save_to_csv()`.
- **Correction:** Re-implemented the `0.1` percentage change threshold. The new system was logging blindly at 60FPS (~0.016s), causing immense CSV bloat. Now it only logs when a measurable change occurs.
- **Correction:** Prevented `start_monitoring()` from overwriting `self.data_log`. If a user paused and restarted monitoring, their previous CSV data was wiped from memory before saving.

## 8. Protocol & Architecture Standardization
- **Firmware Standardized:** Refactored `stepper_firmware.ino` and `chuck_firmware.ino` to use strict 42-byte float structs (`ManualControlPacket`), aligning completely with `high_polling_rate.ino`. This prevents 16-bit vs 32-bit `int` sizing vulnerabilities.
- **Python Standardized:** Updated `BaseProbe` packet format to `<BBffffffffff` strictly across all systems.
- **Architecture Standardized:** Removed the "patchwork" dependency injection inside `main_app.py`. The Domain Models (`BaseProbe`, `TemperatureSystem`) now directly instantiate and encapsulate their own `serial` and `ControllerPoller` instances, restoring true object ownership.
- **Polling Loop Decoupled:** Shifted the controller polling loop from `main_app.py` into the respective UI Views (`ProbeView.start_polling()`). This cleans up the main app and adheres to MVC by making the View responsible for manipulating the Domain Model continuously via its own Tkinter `after` hooks.

## 9. MVC Naming Standardization
- **Directory Structure:** Aligned the internal package references with the new explicitly named directories: `model` (formerly `hardware`), `controller` (formerly `models` / `domain_models`), and `view` (formerly `ui` / `ui_views`).
- **Import Path Fixes:** Deployed a fleet of subagents to dynamically crawl and rewrite all import statements across the codebase.
  - `src/main_app.py`: Updated all module loading paths to correctly load from `model.*`, `controller.*`, and `view.*`.
  - `src/controller/probes.py` & `src/controller/temperature_system.py`: Updated internal imports of `serial` and `ControllerPoller` to target `model.seiral` and `model.gamepad`.
  - `src/view/rotator_view.py` & `src/view/redpercent_view.py`: Updated their respective model initializations to correctly import from the `controller.*` packages.

## 10. True MVC Alignment & View Architecture
- **MVC Directory Swap:** Swapped the contents of `src/model` and `src/controller` to adhere strictly to standard MVC definitions. Hardware communicators (`seiral.py`, `gamepad.py`) are now correctly categorized as `controllers`, while stateful hardware abstractions (`probes.py`, `temperature_system.py`, etc.) are now correctly categorized as `models`. All internal import dependencies across the codebase were dynamically rewritten to support this swap.
- **View Decoupling Evaluation:** Evaluated the feasibility of a single, universal `View` class. Drafted a prototype (`src/view/dynamic_view_prototype.py`) demonstrating a Schema-driven UI generation approach. Concluded that a `DynamicView` is optimal for standard parameterized hardware (e.g., probes, temperature) to reduce boilerplate, but the architecture must remain flexible to support bespoke views (like `RedPercentView`'s transparent screen-capture overlays) without violating MVC boundaries.

## 11. Generic View Engine & Project Flattening
- **Project Structure flattened:** The `src/view` folder was entirely removed to significantly reduce clutter.
  - The `DashboardWindow` parent container and `DynamicView` engine have been moved to a single top-level `src/view.py`.
  - The `SetupWindow` in `src/app.py` (renamed from `main_app.py`) was massively refactored to simply generate domain models and pass them into the `DashboardWindow`, fully completing the decoupling process.
- **Model-driven UI Generation:** All standard models (`BaseProbe`, `TemperatureSystem`) now possess a `ui_schema` property defining their view structures. `DynamicView` constructs the interface dynamically at runtime based on these schemas, supporting full two-way bindings for Entry inputs and dynamic Background/Foreground changes for Toggle buttons.
- **Custom View Embeddings:** Highly-specialized views (like `RedPercentView` and `RotatorView`) have been placed directly within their respective model files in `src/model/`. Their corresponding models specify them via `self.custom_view_class`, allowing `DashboardWindow` to fall back from `DynamicView` to these precise bespoke UI objects without breaking generic routing logic.

## 12. Regression Fleet Audit & Fixes
- **Gamepad Bindings:** Fixed a double-swapping regression for the Thrustmaster T.16000M Z-axis bindings.
- **Probe Instantiation:** Fixed a CRITICAL crash where `DCProbe` and `ChuckPositioner` mistakenly used `(self, serial_comm)` in their `__init__` instead of the required `(self, port, controller_id)`.
- **Dynamic Schema Additions:** 
  - Restored `slow_speed` and `brake_distance` entries for `DCProbe` via `ui_schema` overrides.
  - Implemented a `file_picker` type in `DynamicView` and completely restored `gcodeparser` script parsing in `BaseProbe`.
  - Added a manual "Serial Reconnect" button to the `BaseProbe` schema.
- **Temperature Controller:** 
  - Fixed a regression where hitting "Stop System" inadvertently closed the serial port, permanently killing the connection instead of just zeroing the setpoint.
  - Corrected the hardware handshake initialization string `b"<0,6.0,0,0,0,0>"` to properly reflect the new default 10 degrees/minute ramp rate.
- **GUI Engine:** 
  - Migrated the orphaned `DraggableClosableNotebook` logic from `app.py` to `view.py` and integrated it into the new `DashboardWindow`.
  - Restored the 57600-baud fallback in the `app.py` scanner thread to successfully auto-detect the SMC100 Rotator.

## 13. Temperature System Serial Read Fix
- **File:** `src/model/temperature_system.py`
- **Correction:** Restored the background thread responsible for reading `ser.readline()` which was lost during the MVC refactor. The system now correctly performs non-blocking reads from the serial port, passes lines to `process_raw_data`, and updates `self.current_temp`. Also ensured clean shutdown when `stop()` is called.

## 14. Rotator System Auto-Detect Fix
- **File:** `src/app.py`
- **Correction:** Fixed a regression in the MVC refactor where the auto-detect logic was scanning for the literal string `"SMC100"` instead of `"1ID"` or `"1TS"`. Restored exact detection behavior from the `main` branch, ensuring software flow control (`xonxoff=True`) and the fallback checks are functioning to correctly claim the COM port.

## 15. Testing Suite Implementation
- **Directory:** `tests/`
- **Addition:** Implemented a new pytest suite focusing on the edge-case interactions between models and controllers (e.g., `test_gamepad.py`, `test_model_interactions.py`, `test_serial.py`). This isolates hardware and successfully detects when refactors break previously functional application flows. The suite correctly uncovered a critical state bug in `src/model/probes.py`.

## 16. Probes Auton and Manual State Bug
- **File:** `src/model/probes.py`
- **Correction:** `enter_auton` and `enter_manual` were calling `self.full_stop()`, which reset both `auton_flag` and `manual_flag` to `False` inadvertently preventing the system from entering either state. Implemented `send_stop_command()` which zeros out movement without clearing internal flags.
- **File:** `src/controller/seiral.py`
- **Correction:** The manual packet format had been mistakenly changed to `<BBffffffffff` (42 bytes), preventing parsing on the Arduino. Restored it to the `main` branch format `<BBfffhhhhhhh` (28 bytes) and ensured integer fields are properly cast.

## 17. SystemManager & PySide6 Architecture Migration
- **File:** `src/model/system_manager.py`, `src/view_pyside.py`, `src/app.py`
- **Correction:** Implemented the platform agnostic PySide6 overhaul. Created `SystemManager` to decouple object lifecycles from the UI (allowing model reboots without killing the main window). Created `QtDynamicView` and `DashboardWindow` to replace `ttk.Notebook` with native tear-off `QDockWidget` floating windows.

## 18. Probes MVC Regressions Restored
- **File:** `src/model/probes.py`
- **Correction:** Subagents successfully added missing `ui_schema` mappings and model stub methods for the `serial_port` field, `color_test_window` button, and the `open_controller_selector` / `open_controller_log` UI triggers, restoring parity with the original `main` branch.

## 19. RedPercent MVC Regressions Restored
- **File:** `src/model/redpercent_system.py`
- **Correction:** Subagents successfully restored the standalone "Save Log" button by adding it to the `ui_schema`. They also re-implemented the realtime console prints (`BASELINE SET`, `RED: X%`) inside the monitoring loop to ensure parity with the old `color_test_new.py` logging behavior.

## Layout & Dock Updates
- Changed PySide6 `DashboardWindow` to utilize `splitDockWidget` when launching multiple devices, automatically organizing them into clean, draggable vertical partitions (side-by-side columns).
- Disabled the close button on the `Device Manager` sidebar, making it a permanent embedded widget that cannot be accidentally closed.
- Resolved a critical crash (segmentation fault) that occurred upon closing PySide6 floating docks by safely decoupling their memory destruction lifecycle from the visibility event handler.

## Headless Mode Integration
- Added native "Headless" support inside `src/app.py`. Any physical hardware device (e.g. Stepper, Chuck) can now be assigned "Headless" from the COM port dropdown.
- "Headless" mapping secretly translates to `'SIM'` under the hood, hooking into the `SerialDrive` simulator mode.
- Modified port collision checks to explicitly ignore simulated headless ports, meaning multiple devices can be launched entirely virtually.

## Rotator UI Restoration
- Completely purged legacy Tkinter `RotatorView` frame from `src/model/rotator_system.py`.
- Generated a native PySide6 `ui_schema` property for the Rotator model to seamlessly link it back into the `QtDynamicView` engine.
- Fixed a bug in `src/view_pyside.py` toggle button rendering where toggle colors (green/red) would not initialize properly on boot.

## Active Subagents
- `feature_validator`: Launched to comprehensively map and unit-test all PySide6 `ui_schema` fields against backend object models.
- `mvc_refactor_fixer`: Launched to debug and solve a catastrophic macOS initialization conflict between SDL (Pygame) and Qt (PySide6) causing `cocoa` framework drops.

## Dual-Boot Application Launcher
- Encountered macOS framework collisions where `tkinter` and `PySide6` fundamentally cannot exist in the same Python process without triggering security crashes.
- Implemented a process-replacing launcher in `src/app.py`. Upon execution, a tiny launcher window asks the user which engine to boot into.
- It leverages `os.execv` to completely overwrite the Python process in memory, ensuring perfectly clean framework loads for both PySide6 and Tkinter.
- Re-added the complete, unmodified original Tkinter Setup/View stack as `src/app_legacy.py`, fully preserving the embedded `RedPercentView` 3D Matplotlib plotting tools that could not be mapped to PySide6 `ui_schema`.

## Repository & Script Consolidation
- Cleaned up a severe Git sync conflict that had duplicated models (`probes.py`, `rotator_system.py`, etc.) into the `src/controller` directory, and controllers (`gamepad.py`) into the `src/model` directory.
- Resolved oversegmentation of the application entry points. Merged `app_legacy.py` and `app_pyside.py` entirely back into a single `src/app.py` file, utilizing isolated functions (`run_legacy_app()` and `run_pyside_app()`) to maintain the mutually exclusive Tkinter/PySide6 dual-boot safety within one script.
- Modernized `run.sh` and `run.bat`. Replaced hardcoded absolute paths with dynamic directory targeting (`cd "$(dirname "$0")"`), injected automatic `.venv` activation, and set them to instantly bypass the launcher and boot the PySide6 dashboard using the `--pyside` flag.
- Performed a final manual flush and forced extraction of the PySide6 binaries to clear macOS `dyld` cache corruption caused by intentional `test_tk.py` collision testing, permanently restoring `libqcocoa.dylib` Cocoa framework hooks.
