# MVC Refactor Corrections Log

This file documents all bug fixes, architectural repairs, and regressions addressed after comparing the `mvc-refactor` branch to the legacy `main` branch.

## 1. Application Initialization & Port Scanning
- **File:** `src/main_app.py`
- **Correction:** The `_scan_ports_thread` auto-detection logic was failing because it only gave a 1.5s window. Most Arduino bootloaders take 1.5–2s to run after a DTR reset upon opening the serial port. Increased the timeout to 3.0s and added an initial 1.5s sleep to wait for the bootloader before sending the `s\n` ping.
- **Correction:** Fixed a UI bug where the controller dropdowns were completely disabled/missing. The boolean evaluation used bitwise `&` operators for string comparisons, which failed. Swapped to logical `and`.

## 2. Serial Communication Pipeline
- **File:** `src/hardware/serial_comm.py`
- **Correction:** Increased the same Arduino bootloader timeout in the `SerialArduino` initialization to 3.0s.
- **Correction:** Fixed a chunking bug where fast polling caused the firmware's `DEV:` response to arrive in multiple fragmented packets. Replaced the strict string overwrite with a `buffer` string that accumulates responses, ensuring the handshake succeeds even if fragmented.

## 3. Gamepad Mappings & Overrides
- **File:** `src/hardware/gamepad.py`
- **Correction:** Restored the Y-Axis binding. The refactored abstraction incorrectly mapped `y_axisStatus` to the Left Stick Y (axis 1). The machines were originally wired to use the Right Stick Y (axis 3 for Windows/Mac, axis 4 for Linux).
- **Correction:** Fixed `BluetoothXboxGamepad` mapping on Linux, which accidentally mapped `y_axisStatus` and `z_axisStatusL` (Z-) both to axis 4 (Left Trigger).
- **Correction:** Restored the platform-specific logic for the `T16000MGamepad`, correctly assigning Z+ and Z- to axes 9 and 10 based on whether it is running on Windows or Linux.

## 4. Temperature System
- **File:** `src/domain_models/temperature_system.py`
- **Correction:** Fixed a crash where the system attempted to call `.write()` directly on the `SerialArduino` wrapper object rather than its underlying PySerial `ser` object.
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
- **Architecture Standardized:** Removed the "patchwork" dependency injection inside `main_app.py`. The Domain Models (`BaseProbe`, `TemperatureSystem`) now directly instantiate and encapsulate their own `SerialArduino` and `ControllerPoller` instances, restoring true object ownership.
- **Polling Loop Decoupled:** Shifted the controller polling loop from `main_app.py` into the respective UI Views (`ProbeView.start_polling()`). This cleans up the main app and adheres to MVC by making the View responsible for manipulating the Domain Model continuously via its own Tkinter `after` hooks.

## 9. MVC Naming Standardization
- **Directory Structure:** Aligned the internal package references with the new explicitly named directories: `model` (formerly `hardware`), `controller` (formerly `models` / `domain_models`), and `view` (formerly `ui` / `ui_views`).
- **Import Path Fixes:** Deployed a fleet of subagents to dynamically crawl and rewrite all import statements across the codebase.
  - `src/main_app.py`: Updated all module loading paths to correctly load from `model.*`, `controller.*`, and `view.*`.
  - `src/controller/probes.py` & `src/controller/temperature_system.py`: Updated internal imports of `SerialArduino` and `ControllerPoller` to target `model.serial_comm` and `model.gamepad`.
  - `src/view/rotator_view.py` & `src/view/redpercent_view.py`: Updated their respective model initializations to correctly import from the `controller.*` packages.

## 10. True MVC Alignment & View Architecture
- **MVC Directory Swap:** Swapped the contents of `src/model` and `src/controller` to adhere strictly to standard MVC definitions. Hardware communicators (`serial_comm.py`, `gamepad.py`) are now correctly categorized as `controllers`, while stateful hardware abstractions (`probes.py`, `temperature_system.py`, etc.) are now correctly categorized as `models`. All internal import dependencies across the codebase were dynamically rewritten to support this swap.
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
