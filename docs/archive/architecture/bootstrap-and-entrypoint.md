# Application Bootstrap and Entry Point

Living reference for the application startup flow, setup wizards, and hardware discovery mechanisms in `src/app.py` and `src/app_bootstrap.py`.

## Overview

Application startup is split into three conceptual phases:
1. **Entry Point & CLI Parsing (`app.py` main):** Determines which GUI framework to launch (PySide6, Tkinter, or Web).
2. **Setup/Configuration Wizard (`SetupWindow`):** A pre-launch UI that handles port scanning, hardware auto-detection, and manual device assignment.
3. **Dashboard Construction (`build_models`):** Instantiates the underlying data models and injects them into a `SystemManager` before passing control to the main view framework.

## 1. Application Entry Point (`app.py` > `main()`)

The root command `python src/app.py` parses arguments to select a view.

### The Default-to-Tkinter Override
Historically, `app.py`'s default behavior was to launch the Web view on macOS, or PySide6 on Linux/Windows (falling back to Web if PySide6 was unavailable).
As of commit `ada4dbf`, the launcher script `run.sh` explicitly passes `--tkinter` (formerly `--legacy`), forcing `args.view == "legacy"`. This intentionally bypasses the `app.py` fallback logic entirely, cementing Tkinter as the default view to give space for PySide6 fixes without breaking users.

### Execution Paths
Based on the parsed flag, the script enters one of three runner functions:
* `run_legacy_app()`: Loads Tkinter, starts its `SetupWindow`.
* `run_pyside_app()`: Loads PySide6, starts its `SetupWindow`.
* `run_web_app()`: **Skips the setup window entirely.** Bypasses normal hardware detection and injects an empty `SystemManager` into the `WebDashboardWindow`, relying on `WebModelAdapter.initialize_setup()` to handle configuration later.

## 2. Setup/Configuration Wizard (`SetupWindow`)

Both Tkinter and PySide6 define their own internal `SetupWindow` class inside their respective runner functions. These windows present a checklist of devices, assign COM ports/gamepads, and feature an "Autodetect" scanning flow.

| Framework | Implementation Strategy | Concurrency & UI Updates |
|---|---|---|
| **Tkinter** | Subclasses `tk.Tk` | `threading.Thread` polling a `queue.Queue` via `after()` loop (`check_queue`). |
| **PySide6** | Subclasses `QMainWindow` | Custom `QThread` (`ScannerThread`, `app.py:468`) emitting Qt `Signal`s to the main thread. |

**Hardware Scanning Logic (`get_available_ports` / `get_available_controllers`):**
* Ports are fetched from `app_bootstrap.discover_ports()`.
* Controllers are fetched via Pygame. Both versions set `SDL_VIDEODRIVER="dummy"`, initialize pygame, and loop over `pygame.joystick.get_count()`.

## 3. Device Probing (`src/app_bootstrap.py`)

When the user clicks "Refresh Ports" or auto-detection runs, `app_bootstrap.probe_device_at()` identifies the hardware listening on a specific COM port.

### The SMC100 Rotator Fix (Commit `a7a8eaa`)
`probe_device_at` uses a waterfall approach, testing ports at different baud rates. Previously, the SMC100 Rotator was checked last. Because the rotator doesn't respond to the standard custom firmware probe (`b"s\n"`), probing it meant burning through two ~4.5 second timeouts (for 500k and 115200 baud) before finally testing 57600 baud.
* **Current State:** The 57600 baud SMC100 check is now executed **first** (`app_bootstrap.py:58`). It sets a short 0.2s timeout, asks for ID (`1ID?`), and safely bails if it fails. This dramatically speeds up port scanning when a rotator is attached.

### Probe Hierarchy
1. **57600 baud (SMC100 Rotator)**: Expects `1ID...` or `1TS...` response.
2. **500000 baud (High-speed Probes)**: Sends `s\n`, matches regex `(?:DEV:\s*|<)([sdct])>?` for Stepper (`s`), DC (`d`), Chuck (`c`), or Temp Controller (`t`).
3. **115200 baud (Standard Probes)**: Same handshake as 500k baud.

## 4. Model Construction & Wiring

When the user clicks "Launch Unified Application", the setup configurations are gathered and validated:

1. **Validation (`app_bootstrap.validate_assignment`)**: Ensures no two active devices claim the same physical COM port (except "SIM") or gamepad ID.
2. **Instantiation (`app_bootstrap.build_models`)**: Takes the validated `active_configs` and an `active_claims` dictionary and returns initialized model instances (e.g., `StepperProbe`, `TemperatureSystem`).
3. **Wiring (`app.py`)**:
   * Registers each model into a newly created `SystemManager`.
   * **Red Percent Syncing:** Explicitly finds any positioning probes (`hasattr(model, 'pos_x')`) and injects them into the `RedPercentSystem` so its dimensions can be synced.
4. **Handoff**: The setup window is closed/withdrawn, and the primary dashboard (`DashboardWindow`) is launched.

---

## Findings for known-issues.md

During the documentation of these entry points, several inconsistencies were identified that should be logged:

* **Dead Code:** `app.py:3` defines `parse_controller_id()`, but it is completely unused. Both Tkinter and PySide6 parse `"ID X: Name"` purely by regex within their controller mapping routines later in the stack.
* **Swallowed Exceptions in Probing:** `probe_device_at()` wraps every serial attempt in `try... except Exception: pass`. If a user launches the app but the serial port is locked by another process (e.g., Arduino IDE) or requires `sudo` (Linux permission errors), it silently fails and reports "Not Found" instead of throwing an actionable error.
* **Redundant Pygame Joystick Initialization:** In PySide6's `get_available_controllers` (`app.py:545`), it calls `js.init()` on each iterated joystick, whereas Tkinter's version just reads the name and skips `js.init()`.

---
*Verified against `mvc-refactor` branch at commit `a7a8eaa` / `ada4dbf`.*

## Addendum: Comprehensive Call Graph & Method Detail (2026-09-18)

This addendum exhaustively maps the application startup flow, covering every stage from the initial process execution to the display of the first primary dashboard frame, detailing every method in `app.py` and `app_bootstrap.py`.

### 1. Exact Call Graph (Process Start to First Frame)

The application initialization follows one of three distinct paths determined by CLI arguments or platform defaults.

#### Common Entry
1. `app.py`: `if __name__ == "__main__":` invokes `main()`.
2. `main()` parses CLI args (`--view`, `--web`, `--pyside`, `--tkinter`, etc.) using `argparse`.
3. Fallback logic: If no view is explicitly provided, defaults to `web` on macOS; otherwise attempts to load PySide6 and uses `pyside` if successful, falling back to `web`.
4. Dispatches to either `launch_legacy()`, `launch_pyside()`, or `launch_web()`.

#### Path A: Legacy Tkinter (`run_legacy_app()`)
1. `run_legacy_app()` imports Tkinter and required models.
2. The `SetupWindow` class (inheriting from `tk.Tk`) is defined.
3. `app = SetupWindow()` is instantiated:
   - `__init__()` calls `get_available_ports()` and `get_available_controllers()`.
   - `create_widgets()` builds the UI layout.
   - `after(200, self.start_autodetect)` schedules the scanning logic.
   - `after(50, self.check_queue)` schedules the thread-safe UI update polling loop.
4. Error handling is configured via `ErrorPopupManager`.
5. `app.mainloop()` is called, beginning the Tkinter event loop.
6. The event loop fires `start_autodetect()`, which spawns `_scan_ports_thread()` as a daemon.
7. `_scan_ports_thread()` queries `app_bootstrap.probe_device_at()` for each port and pushes updates into `gui_queue`.
8. `check_queue()` processes `gui_queue` items, calling `_update_device_ui()` and `_scan_complete()`.
9. Upon user clicking "Launch Unified Application", `launch_unified()` is triggered:
   - `app_bootstrap.validate_assignment()` checks for port/controller collisions.
   - `self.withdraw()` hides the setup window.
   - `app_bootstrap.build_models()` instantiates the domain models.
   - Positioning probes are mapped into the `RedPercentSystem`.
   - Models are registered into a `SystemManager`.
   - **`DashboardWindow(self, system_manager)` is instantiated. This constitutes the first dashboard frame shown.**

#### Path B: PySide6 (`run_pyside_app()`)
1. `run_pyside_app()` imports PySide6 and required models.
2. Global `QApplication` instance is created/retrieved.
3. `window = SetupWindow()` (inheriting from `QMainWindow`) is instantiated:
   - Calls `get_available_ports()` and `get_available_controllers()`.
   - Builds UI via `create_widgets()`.
   - Triggers `start_autodetect()` synchronously in the constructor.
4. `start_autodetect()` instantiates `ScannerThread` (inheriting from `QThread`), binds its Qt signals (`progress`, `status`, `found`, `complete`) to UI slots, and starts it.
5. `window.show()` displays the setup wizard.
6. `app.exec()` enters the Qt event loop.
7. Upon user clicking "Launch Application", `launch_unified()` is called:
   - Configuration is validated, and models are built and registered to a `SystemManager`.
   - **`dashboard = DashboardWindow(self.manager)` is instantiated, and `dashboard.show()` is called. This constitutes the first dashboard frame shown.**
   - `self.close()` destroys the setup window.

#### Path C: Web (`run_web_app()`)
1. `run_web_app()` is invoked directly.
2. A raw `SystemManager` is instantiated with no predefined active configurations.
3. **`dashboard = WebDashboardWindow(manager)` is instantiated, and `dashboard.show()` starts the server and opens the browser. This is the first dashboard frame shown.**
4. A custom `sys.excepthook` is set to catch web thread errors.
5. The main thread blocks indefinitely in a `while True: time.sleep(1)` loop while the dashboard runs on a daemon thread.

---

### 2. Method-by-Method Exhaustive Detail

#### `src/app.py`
*   `parse_controller_id(controllerID)`: Safely extracts an integer ID from strings like "ID 0: Gamepad" using regex. While present, it is not actively used in the main execution paths.
*   `main()`: The primary entry point. Configures `argparse`, evaluates environment (like OS and module availability) to determine the default view, and dispatches to the correct launcher method.
*   `launch_legacy()`, `launch_pyside()`, `launch_web()`: Wrapper methods that provide CLI logging before executing the actual application runners.

**Inside `run_legacy_app()` (Tkinter Scope):**
*   `SetupWindow.__init__()`: Initializes UI state lists/dictionaries, fetches hardware lists, and kicks off Tkinter's `after()` loops for asynchronous processing.
*   `SetupWindow.check_queue()`: A repeating polling loop (every 50ms) that safely drains `gui_queue`. It updates progress bars, status labels, and triggers device UI updates in the main thread.
*   `SetupWindow.get_available_ports()`: Simple wrapper to cache ports from `app_bootstrap.discover_ports()`.
*   `SetupWindow.get_available_controllers()`: Initializes `pygame`, loops over the joystick count, and constructs display strings for dropdowns. Uses fallback virtual controllers if none exist.
*   `SetupWindow.refresh_devices()`: Wipes and rebuilds dropdown UI elements with fresh hardware lists, then aggressively triggers a forced autodetect scan.
*   `SetupWindow._reset_device_status(device)`: Resets the visual status text of a device to "Waiting...".
*   `SetupWindow.toggle_dropdown_state(device)`: Dynamically enables or disables port/controller combo boxes based on whether the specific device checkbox is ticked.
*   `SetupWindow.create_widgets()`: The primary layout builder using `ttk.LabelFrame`, `ttk.Checkbutton`, and `grid` packing logic.
*   `SetupWindow.start_autodetect(force=False)`: Spawns the daemon thread for scanning, resetting UI elements into a loading state.
*   `SetupWindow._scan_ports_thread()`: The background worker that iterates over all detected ports (excluding "Headless"). It queries `app_bootstrap.probe_device_at()` and injects the resulting data into `gui_queue`.
*   `SetupWindow._update_device_ui(device_name, port)`: Autochecks the device box, pre-selects the port, and updates the label to "✓ Auto-Verified".
*   `SetupWindow._scan_complete()`: Wraps up the scanning UI state and sets any undiscovered devices to "Not Found". Schedules UI cleanup.
*   `SetupWindow._cleanup_scan_ui()`: Removes loading bars from the screen and reenables interaction buttons.
*   `SetupWindow.launch_unified()`: Gathers active configurations, calls validation, injects positioning probes into the `RedPercentSystem`, builds the models, and passes control to the `DashboardWindow`.

**Inside `run_pyside_app()` (PySide6 Scope):**
*   `ScannerThread.__init__()` / `ScannerThread.run()`: A native `QThread` that loops through available ports. Emits granular Qt `Signal`s for `progress`, `status`, `pinging`, `found`, and `complete`.
*   `SetupWindow.__init__()`: Initializes the layout and immediately kicks off autodetect.
*   `SetupWindow.get_available_ports()` / `SetupWindow.get_available_controllers()`: Mirrors Tkinter logic, but explicitly calls `js.init()` on Pygame joysticks.
*   `SetupWindow.create_widgets()`: Builds the layout using native Qt layouts (`QVBoxLayout`, `QGridLayout`).
*   `SetupWindow.refresh_ports()`: Re-fetches hardware, cleanly updating the combo boxes while attempting to preserve current selections, then restarts scanning.
*   `SetupWindow.start_autodetect()`: Wires the `ScannerThread` signals to GUI slot functions and calls `.start()`.
*   `SetupWindow._update_device_ui(device_name, port)`: The slot function for a found device; checks the corresponding box and updates the combo box index.
*   `SetupWindow._scan_complete()` / `SetupWindow._cleanup_scan_ui()`: Updates end-of-scan statuses and handles teardown of the progress bar UI using `QTimer.singleShot`.
*   `SetupWindow.launch_unified()`: Performs final validation, model generation, RedPercent syncing, and constructs the primary `DashboardWindow`.

**Inside `run_web_app()` (Web Scope):**
*   Bypasses all setup windows. Directly initializes a generic `SystemManager`, binds it to `WebDashboardWindow`, exposes the application logic to the web server, and enters an infinite sleep loop.

#### `src/app_bootstrap.py`
*   `discover_ports() -> list[str]`: Leverages `serial.tools.list_ports`. It purposefully strips out Bluetooth/Wireless adapters and invalid Linux `ttyS` devices. Returns a custom-sorted list where USB connections are prioritized over standard serial lines, alongside a mock "Headless" option.
*   `probe_device_at(port: str) -> str | None`: The hardware handshake routine. Operates in three waterfall phases:
    1.  **57600 Baud (SMC100 Rotator):** Sends `1ID?` and `1TS?` with a tight 0.2s timeout. Returns immediately on success.
    2.  **500000 Baud (High-Speed custom firmware):** Opens serial, sleeps 1.5s (to outlast an Arduino bootloader reset), then spins for up to 3.0s continuously sending `s\n`. Matches any incoming buffer against the regex `(?:DEV:\s*|<)([sdct])>?`.
    3.  **115200 Baud (Standard custom firmware):** Identical sleep and spin logic to Phase 2 but at a lower baud rate.
    *Exceptions during this sequence are silently swallowed to prevent OS-level locks from crashing the tool.*
*   `build_models(active_configs: list[dict], active_claims: dict) -> dict[str, object]`: Acts as a factory mapping user configuration strings (e.g., "Stepper Probe") to instantiated model objects. Supplies COM ports and parsed Gamepad IDs to the underlying drivers.
*   `validate_assignment(active_configs: list[dict]) -> list[str]`: Evaluates the requested hardware mapping to prevent structural conflicts. Ensures no two physical hardware modules claim the exact same COM port or Gamepad ID (ignoring "SIM", "None", and the headless "Red Percent Window").
