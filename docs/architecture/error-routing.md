# Error-Routing Framework

**Purpose:** every model reports errors/warnings/info through one
model-side API (`ErrorRouter`, `src/error_routing.py`, 54 lines) so models
stay frontend-agnostic — a model never imports `QMessageBox` or
`tkinter.messagebox` directly, it just calls `ErrorRouter.report_*`, and
whichever frontend is running has already registered callbacks that turn
that into the right kind of popup.

## `error_routing.ErrorRouter` (the model-side API)

```python
class ErrorRouter:
    _error_cb = None; _warning_cb = None; _info_cb = None
    _last_messages: dict[str, float] = {}

    @classmethod
    def set_callbacks(cls, err, warn, info) -> None: ...
    @classmethod
    def report_error(cls, title: str, message: str, exception: Exception|None = None) -> None: ...
    @classmethod
    def report_warning(cls, title: str, message: str, exception: Exception|None = None) -> None: ...
    @classmethod
    def report_info(cls, title: str, message: str) -> None: ...
```

**Class-level state, not per-instance** — one registered set of callbacks
for the whole process. `_is_spam(message)` de-dupes by exact message string
within a 5-second window (dict capped at 100 entries, pruned by age) — this
means **repeatedly calling `report_*` with the same message string is
already safe** against flooding the user, which matters for the "be
generous with alerts" audit below: adding a report call inside a hot loop
won't spam as long as the message text doesn't change every call.
If no callback is registered yet (`_error_cb` etc. still `None`), every
method falls back to a bare `print()` — this is what happens for any
`report_*` call made before whichever frontend's popup manager has
initialized.

## Per-frontend wiring (three independent implementations)

| Frontend | Class | Cross-thread mechanism | File |
|---|---|---|---|
| PySide6 | `QtErrorPopupManager(QObject)` | Qt `Signal` (`_message_signal`), connected to `_display_popup` — **this is what makes it safe to call `ErrorRouter.report_*` from any background thread** (gamepad polling threads, serial read threads, the red-percent monitor thread) without violating Qt's main-thread-only widget rule. | `views/pyside/view.py:20-94` |
| Tkinter | `ErrorPopupManager` (plain class, **not** the same class as PySide6's despite the identical name) | `queue.Queue`, drained by a `self._root.after(100, cls._poll_queue)` recursive poll on the main thread | `views/tkinter/view.py:9-106` |
| Web | (wired in `web_view.py`) | Report calls feed a web-reporting queue/callback (not traced in this pass — web view deprioritized) | `views/web/web_view.py` |

Both PySide6 and Tkinter implementations independently:
- Truncate messages over 5000 chars with `"... [TRUNCATED]"`.
- Append `f"\n\nDetails:\n{type(exception).__name__}: {str(exception)}"` when
  an exception is passed, guarded by its own try/except in case the
  exception itself isn't printable.
- Provide `setup_excepthook()`, installing a `sys.excepthook` that routes
  every unhandled exception through `report_error("Unhandled Exception", ...)`
  — the last-resort safety net.

**This is duplicated, parallel-maintained code, not shared** — a bug fixed
in one popup manager (e.g. the truncation logic) isn't automatically fixed
in the other. Worth flagging as a small maintainability risk, not an active
bug.

## Audit: state transitions that don't report anything to the user

Requested audit (2026-09-18, via `agy` read-only investigation): every
"pausing" transition — serial connect/verify/reconnect, controller
construct/destroy, model construct/teardown — checked for whether it
currently tells the user anything via `ErrorRouter`, versus a bare
`print()` or a fully silent `pass`. Philosophy per explicit direction: **be
generous** — a `report_info` is cheap to add and cheap to delete later, and
the alternative (silence during a multi-second reconnect/rescan) is worse
UX than an occasional extra popup. The `_is_spam` 5s dedup already protects
against genuine flooding.

**Judgment call, not blindly applying all of these:** `__del__`-triggered
report calls are listed but flagged **not recommended as-is** — Python
object destructors can fire during interpreter shutdown or GC, when the
Qt/Tk event loop and its widgets may already be torn down; a popup call
from `__del__` risks a crash-on-exit rather than a helpful message. If
these are wanted, route them through a plain `print()` (already present)
rather than `ErrorRouter`, or verify the app isn't shutting down first.

### Already well-instrumented (no change needed)
- `serial.serial.__init__` — reports both success verification and failure via `report_info`/`report_warning`.
- `serial.enable/disable` — reports on write failure.
- `probes.BaseProbe.enable()` — reports on `serial_comm.enable()` failure.
- `rotator_system.RotatorSystem.connect()` — reports on failure.
- `probes.send_manual_mode_command` — **fixed 2026-09-18** to report the no-gamepad block, previously print-only.
- `probes.enter_manual` — **fixed 2026-09-18** to report the no-gamepad block *before* enabling (see known-issues).

### `src/controller/serial.py`
| Line | Transition | Current | Suggested |
|---|---|---|---|
| 83 | Successful connect/verify | print-only | `report_info("Serial Connected", f"Arduino Ready on {self.SERIAL_PORT}")` |
| 262 (`close`) | Port close | print-only | `report_info("Serial Port Closed", "Serial port closed safely.")` |

### `src/controller/gamepad.py`
| Line | Transition | Current | Suggested |
|---|---|---|---|
| ~363 (`connect_controller`'s `pygame.quit()`) | Teardown before reconnect | bare except, swallowed | `report_warning("Controller Quit Error", "Failed to cleanly quit pygame before reconnecting.")` |
| ~374 | Controller explicitly set to None | print-only | `report_info("Controller Unassigned", "Joystick set to None.")` |
| ~450 (successful bind) | Gamepad init success | print-only | `report_info("Gamepad Connected", f"Initialized {joystick.get_name()}")` |
| ~467 (hot-swap) | Controller reassigned | print-only | `report_info("Controller Swapped", f"Hot-swapped to {new_controller_id}")` |
| ~508 (`close`) | pygame teardown | bare except, swallowed | `report_warning("Controller Close Error", "Failed to cleanly quit pygame joystick.")` |

### `src/model/probes.py`
| Line | Transition | Current | Suggested |
|---|---|---|---|
| 20 (`__del__`) | Destructor | print-only | **not recommended** (see interpreter-shutdown caveat above) |
| 41 | Gamepad construct failure, headless fallback | print-only | `report_warning("Headless Mode", f"Gamepad unavailable: {e}")` |
| 162 (`reconnect_serial`) | Reconnect start | print-only | `report_info("Serial Reconnect", f"Reconnecting to {self.serial_port}...")` |
| 167 | Reconnect close error | print-only | `report_warning("Serial Close Error", f"Failed to close existing port: {e}")` |
| 183 (`set_controller`) | Controller swap | print-only | `report_info("Controller Swapped", f"Changed controller to {controller_id}")` |
| 192 | Controller swap failure | print-only | `report_warning("Controller Swap Failed", f"Gamepad unavailable: {e}")` |
| 455/457 (`teardown`/power-down) | Kill-coils success/failure | print-only | `report_info`/`report_warning` respectively — **arguably should be `report_warning` even on success**, since "coils just got killed" during active use is worth surfacing prominently, not just informationally. |

### `src/model/rotator_system.py`
| Line | Transition | Current | Suggested |
|---|---|---|---|
| 10 (`__del__`) | Destructor | print-only | not recommended |
| ~92 (`connect` success) | SMC100 connected | fully silent | `report_info("Rotator Connected", "Successfully connected to SMC100.")` |
| ~119 (`disconnect` close error) | bare except, swallowed | `report_warning("Rotator Disconnect Error", "Failed to cleanly close SMC100 connection.")` |

### `src/model/temperature_system.py`
| Line | Transition | Current | Suggested |
|---|---|---|---|
| 8 (`__del__`) | Destructor | print-only | not recommended |
| ~188 (`close`, stop-write) | bare except | `report_warning("Temperature Stop Error", "Failed to write stop state during close.")` |
| ~191 (`close`, port close) | bare except | `report_warning("Temperature Close Error", "Failed to cleanly close serial connection.")` |

### `src/model/redpercent_system.py`
| Line | Transition | Current | Suggested |
|---|---|---|---|
| 58 (`__del__`) | Destructor | print-only | not recommended |
| ~95 (`set_stepper_model`) | Probe assignment | print-only | `report_info("Probe Assigned", f"Position probe set to: {probe_name}")` |
| ~265/~273 (start/stop monitoring) | Monitoring toggled | print-only | `report_info` for both — this is a long-running background thread with real safety/resource implications (screen capture loop), worth surfacing. |

### `src/model/system_manager.py`
| Line | Transition | Current | Suggested |
|---|---|---|---|
| 44 (`reboot_model`) | Model reboot | print-only | `report_info("System Reboot", f"Rebooting model {name}...")` — this one already has a `time.sleep(1)` "simulate hardware reboot delay" right next to it, i.e. it's a multi-second pause with zero UI feedback today. High-value addition. |

### `src/views/pyside/view.py`
| Line | Transition | Current | Suggested |
|---|---|---|---|
| ~326 (dropdown rescan) | Controller rescan completes | fully silent | `report_info("Rescan Complete", "Available controllers rescanned successfully.")` |
| ~829 (`close_device_view` cleanup) | Widget cleanup error | bare except, swallowed | `report_warning("Cleanup Error", "Error cleaning up view during dock close.")` |
| ~847 | Dock close / model destroy | print-only | `report_info("Device Closed", f"Unregistered and destroyed {device_name}.")` |
| ~881 (`open_device_view` construct) | Dock open / model construct | fully silent | `report_info("Device Opened", f"Constructed and opened {device_name}.")` |

**Highest-value subset if applying these incrementally rather than all at
once:** the `system_manager.reboot_model` 1-second silent sleep, the dock
open/close pair in `view.py` (directly addresses "verify port
connections... destroy or construct objects" from the original ask), and
`rotator_system.connect`'s success case (failure is already reported;
success isn't, which is an asymmetry worth closing).

---
*Last verified against commit `12e9d59` (2026-09-18). Audit content sourced from an `agy` read-only investigation the same day; line numbers approximate where marked `~` (agy-reported, not independently re-verified line-by-line).*
