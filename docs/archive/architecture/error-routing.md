# Error-Routing Framework

> **Rewritten 2026-09-21 (ERRORS-11).** The previous version of this file
> documented an API that **no longer exists**. It was last verified against
> `12e9d59` on 2026-09-18 — before S1–S15 — and S11 (RC-8) replaced the
> single-callback `ErrorRouter` with an event bus wholesale. It described
> `set_callbacks`, three `_error_cb`/`_warning_cb`/`_info_cb` slots, a
> `_last_messages` dict and an `_is_spam` 5-second text dedup. **None of
> those are in the code.** It also gave `error_routing.py` as 54 lines; it
> is 340.
>
> ERRORS-11 asked for five table rows to be corrected. The citation *scheme*
> had rotted, not five rows of it, so the fix is to anchor to **symbols**
> instead of line numbers. Line numbers in this file are now marked
> "as of `<sha>`" wherever they appear, and nothing depends on them.

**Purpose:** every model reports errors/warnings/info through one model-side
facade (`ErrorRouter`, `src/error_routing.py`) so models stay
frontend-agnostic. A model never imports `QMessageBox` or
`tkinter.messagebox`; it calls `ErrorRouter.report_*`, which publishes to a
process-wide `EventBus`, and whichever frontends are running have
**subscribed** to that bus.

The difference from the old design is the one RC-8 names: subscribing does
not silence whoever subscribed first. `set_callbacks` was a single global
slot, so the last frontend to register won and the others went dark.

## `error_routing` — the real surface

```python
MAX_EVENTS = 500                                    # bus history for since()
RATE_LIMIT_WINDOW = {INFO: 30.0, WARNING: 60.0, ERROR: 60.0}

class Event:                        # one thing, one moment, one place
    key -> (severity, source, title)         # NOT the message text
    count, first_seen, last_seen, requires_ack
    to_dict()                                # the wire form for the Web client

class EventBus:
    publish(severity, source, title, message, exception=None,
            requires_ack=False) -> Event
    subscribe(fn) / unsubscribe(fn) / subscriber_count()
    since(event_id=0) / latest_id() / snapshot() / clear()

class ErrorRouter:                  # the facade ~40 model call sites use
    report_error(title, message, exception=None, source="app",
                 requires_ack=False)
    report_warning(title, message, exception=None, source="app")
    report_info(title, message, source="app")
    subscribe(fn) / unsubscribe(fn) / since(event_id=0)

install_exception_hooks(target=None, tk_root=None)
```

**Rate limiting is not what the old file said.** There is no `_is_spam` and
no text-keyed 5-second window. A repeat is folded when an event with the same
`(severity, source, title)` — **not** the same message body — arrives inside
that severity's window: 30 s for info, 60 s for warnings and errors. The
folded event's `count` increments and `last_seen` moves; it is not dropped,
so a caller can still see that it happened 40 times. `_prune_recent` drops
dedup keys older than the longest window.

The practical consequence for anyone adding a report call is unchanged and
still worth stating: **a `report_*` in a hot loop will not flood the
operator**, as long as its *title* is stable. Varying the title per call
defeats the fold; varying only the message body does not.

**If nobody has subscribed,** `publish` prints the event (and the traceback,
if an exception was passed) rather than losing it. That is what happens for
any `report_*` made before a frontend has started.

**Exception hooks are installed in one place.** `install_exception_hooks`
sets `sys.excepthook`, `threading.excepthook` and — when given a `tk_root` —
Tk's `report_callback_exception`. The old file said neither frontend covered
`threading.excepthook`; that was true when it was written and **is no longer
true**. `QtErrorPopupManager.setup_excepthook` and Tk's `ErrorPopupManager`
still exist as thin entry points, but the installation itself is shared.

## Per-frontend wiring (three independent implementations)

| Frontend | Class | Cross-thread mechanism | File |
|---|---|---|---|
| PySide6 | `QtErrorPopupManager(QObject)` | Qt `Signal` (`_message_signal`), connected to `_display_popup` — **this is what makes it safe to call `ErrorRouter.report_*` from any background thread** (the poller's own daemon clock when a device is not Tk-driven, serial read threads, the red-percent monitor thread) without violating Qt's main-thread-only widget rule. | `views/pyside/view.py`, class `QtErrorPopupManager` |
| Tkinter | `ErrorPopupManager` (plain class, **not** the same class as PySide6's despite the identical name) | `queue.Queue`, drained by a `self._root.after(100, cls._poll_queue)` recursive poll on the main thread | `views/tkinter/view.py`, class `ErrorPopupManager` |
| Web | `WebErrorManager` (static class) | **Subscribes to the bus.** It used to copy every event into `WebAPIHandler.error_buffer`; S11 moved `/api/errors` onto the bus itself, so the buffer is no longer the source of truth (ERRORS-12). | `views/web/web_view.py`, class `WebErrorManager` |

Both PySide6 and Tkinter implementations independently:
- Truncate messages over 5000 chars with `"... [TRUNCATED]"`.
- Append `f"\n\nDetails:\n{type(exception).__name__}: {str(exception)}"` when
  an exception is passed, guarded by its own try/except in case the
  exception itself isn't printable.
- Provide `setup_excepthook()` — now a thin entry point onto the shared
  `install_exception_hooks`, which covers `sys.excepthook`,
  `threading.excepthook` and Tk's `report_callback_exception` in one place
  (S11). Each view installing its own hook, and none of them covering
  threads, is the state this replaced.

**This is duplicated, parallel-maintained code, not shared** — a bug fixed
in one popup manager (e.g. the truncation logic) isn't automatically fixed
in the other. Worth flagging as a small maintainability risk, not an active
bug.

## Audit: state transitions that don't report anything to the user

> **This section is a snapshot taken on 2026-09-18, against a file that has
> since been rewritten.** Its *judgment* — be generous with `report_info`,
> silence during a multi-second reconnect is worse UX than an extra popup —
> still stands and is still the project's position. Its **line numbers do
> not**, and neither do several of its verdicts: ERRORS-6, ERRORS-7, S11 and
> SERIAL-16 each implemented part of it. The tables are kept because the
> reasoning in them is worth having, and deleted line numbers cannot be
> recovered later.
>
> **Do not delegate work from the old tables' line numbers.** That is the
> exact failure ERRORS-11 was raised for: "an agent delegated the
> `455/457` style rows edits the wrong lines." Use the symbol index below,
> then read the site.

**Judgment call that has not changed:** `__del__`-triggered report calls are
listed below but flagged **not recommended as-is**. Python destructors can
fire during interpreter shutdown or GC, when the Qt/Tk event loop and its
widgets may already be torn down; a popup from `__del__` risks a crash on
exit rather than a helpful message. Route them through the plain `print()`
already present, or verify the app is not shutting down first.

### Current state, by symbol (regenerated 2026-09-21 against `b95a379`)

Counts of reporting sites per file, anchored to the enclosing
`Class.method` rather than a line. Regenerate rather than hand-edit; the
generator is a dozen lines of `ast` and the numbers drift with every commit.

| File | lines | `print` sites | `report_*` sites | bare `except: pass` in |
|---|---|---|---|---|
| `controller/serial.py` | 733 | 21 in 9 symbols | 13 in 8 symbols | `_connect_worker`, `_handshake`, `_mark_lost`, `close`, `read_position` |
| `controller/gamepad.py` | 801 | 12 in 6 symbols | 7 in 5 symbols | `ControllerPoller._is_os_connected`, `T16000MGamepad.update_overrides` |
| `model/probes.py` | 1608 | 33 in 22 symbols | 14 in 10 symbols | `BaseProbe._axis_state`, `_enter_fault`, `_report_power_down_unsupported` |
| `model/rotator_system.py` | 524 | 7 in 6 symbols | 4 in 4 symbols | `_run_guarded`, `connect`, `disconnect`, `poll_status`, `stop` |
| `model/temperature_system.py` | 545 | 14 in 8 symbols | 12 in 5 symbols | `process_raw_data`, `send_settings`, `stop` |
| `model/redpercent_system.py` | 1087 | 18 in 11 symbols | 7 in 7 symbols | `RedPercentSystem._monitor_colors` |
| `model/system_manager.py` | 301 | 1 in 1 symbol | 1 in 1 symbol | `SystemManager._report` |
| `views/pyside/view.py` | 1346 | 2 in 2 symbols | 3 in 2 symbols | `DashboardWindow.closeEvent`, `close_device_view` |

A bare `except: pass` is not automatically a defect — several are deliberate
and carry a docstring saying why (a malformed `POS:` line is ~10 Hz
telemetry, and reporting each one is the popup flood RC-8 exists to prevent;
`EventBus.publish` swallows a broken subscriber so one bad listener cannot
take the reporter down, and it still prints). **Read the site before
believing it is a bug** — this codebase documents its own repairs at length,
and a grep hit is not evidence a defect survives.

### Verdicts from the old audit that are now wrong

Checked against `b95a379` on 2026-09-21. Each of these is listed in the old
tables or the ERRORS-11 entry as an outstanding gap, and each is **already
fixed** — the finding text itself has gone stale:

| Old claim | Actual state |
|---|---|
| "`serial.enable/disable` reports on write failure" is misleading — `serial.enable` swallows the write failure and returns normally | **Both raise now.** `enable()` and `disable()` raise `TransportError` if the write did not reach the hardware; `disable`'s docstring records that it "used to swallow the write exception and return normally". |
| Neither frontend sets `threading.excepthook` | **It is set**, in the shared `install_exception_hooks`, along with `sys.excepthook` and Tk's `report_callback_exception`. |
| The Web path installs its excepthook differently, in `app.py` | Still worth knowing, but the divergence is now the *entry point*, not the coverage — all three routes reach the same installer. |
| PySide's cross-thread note cites "gamepad polling threads" as wrong (the poll is on the main thread) | **The correction itself went stale.** Since RC-13/S5, `start_polling` chooses between a Tk `after` clock and the poller's **own daemon thread**, so a gamepad poll genuinely can be off the main thread. |
| `_is_spam` dedup dict "capped at 100, pruned by age" is not a hard cap | Moot — `_is_spam` and `_last_messages` do not exist. See the rate-limiting note at the top of this file for what replaced them. |

## The old tables (2026-09-18 snapshot — line numbers are stale)

Everything below this heading is the historical snapshot. Its verdicts are
superseded where the table above says so.

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
| 48 | Connection attempt start | print-only | `report_info("Serial Connection", f"Attempting connection to {self.SERIAL_PORT}...")` |
| 83 | Successful connect/verify | print-only | `report_info("Serial Connected", f"Arduino Ready on {self.SERIAL_PORT}")` |
| 259 (`close`) | Port close | print-only | `report_info("Serial Port Closed", "Serial port closed safely.")` |

### `src/controller/gamepad.py`
| Line | Transition | Current | Suggested |
|---|---|---|---|
| 363 (`connect_controller`'s `pygame.quit()`) | Teardown before reconnect | bare except, swallowed | `report_warning("Controller Quit Error", "Failed to cleanly quit pygame before reconnecting.")` |
| 374 | Controller explicitly set to None | print-only | `report_info("Controller Unassigned", "Joystick set to None.")` |
| 393 | Invalid controller ID check | print-only | `report_warning("Invalid Controller", f"Invalid controller ID: {controllerID}")` |
| 450 (successful bind) | Gamepad init success | print-only | `report_info("Gamepad Connected", f"Initialized {joystick.get_name()}")` |
| 467 (hot-swap) | Controller reassigned | print-only | `report_info("Controller Swapped", f"Hot-swapped to {new_controller_id}")` |
| 485 | Polling started | print-only | `report_info("Controller Polling", "Started polling controller input.")` |
| 508 (`close`) | pygame teardown | bare except, swallowed | `report_warning("Controller Close Error", "Failed to cleanly quit pygame joystick.")` |

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
| 93 (`connect` success) | SMC100 connected | fully silent | `report_info("Rotator Connected", "Successfully connected to SMC100.")` |
| 119 (`disconnect` close error) | bare except, swallowed | `report_warning("Rotator Disconnect Error", "Failed to cleanly close SMC100 connection.")` |

### `src/model/temperature_system.py`
| Line | Transition | Current | Suggested |
|---|---|---|---|
| 8 (`__del__`) | Destructor | print-only | not recommended |
| 178 (`close`, stop-write) | bare except | `report_warning("Temperature Stop Error", "Failed to write stop state during close.")` |
| 191 (`close`, port close) | bare except | `report_warning("Temperature Close Error", "Failed to cleanly close serial connection.")` |

### `src/model/redpercent_system.py`
| Line | Transition | Current | Suggested |
|---|---|---|---|
| 58 (`__del__`) | Destructor | print-only | not recommended |
| 95 (`set_stepper_model`) | Probe assignment | print-only | `report_info("Probe Assigned", f"Position probe set to: {probe_name}")` |
| 265/273 (start/stop monitoring) | Monitoring toggled | print-only | `report_info` for both — this is a long-running background thread with real safety/resource implications (screen capture loop), worth surfacing. |

### `src/model/system_manager.py`
| Line | Transition | Current | Suggested |
|---|---|---|---|
| 44 (`reboot_model`) | Model reboot | print-only | `report_info("System Reboot", f"Rebooting model {name}...")` — this one already has a `time.sleep(1)` "simulate hardware reboot delay" right next to it, i.e. it's a multi-second pause with zero UI feedback today. High-value addition. |

### `src/views/pyside/view.py`
| Line | Transition | Current | Suggested |
|---|---|---|---|
| 337 (dropdown rescan) | Controller rescan completes | fully silent | `report_info("Rescan Complete", "Available controllers rescanned successfully.")` |
| 829 (`close_device_view` cleanup) | Widget cleanup error | bare except, swallowed | `report_warning("Cleanup Error", "Error cleaning up view during dock close.")` |
| 847 | Dock close / model destroy | print-only | `report_info("Device Closed", f"Unregistered and destroyed {device_name}.")` |
| 911 (`open_device_view` construct) | Dock open / model construct | fully silent | `report_info("Device Opened", f"Constructed and opened {device_name}.")` |

**Highest-value subset if applying these incrementally rather than all at
once:** the `system_manager.reboot_model` 1-second silent sleep, the dock
open/close pair in `view.py` (directly addresses "verify port
connections... destroy or construct objects" from the original ask), and
`rotator_system.connect`'s success case (failure is already reported;
success isn't, which is an asymmetry worth closing).

---

*Header, API surface and per-frontend wiring rewritten 2026-09-21 against
`b95a379` (ERRORS-11). The tables above are a 2026-09-18 snapshot against
`12e9d59` and are kept for their reasoning, not their line numbers. The
deep-dive addendum that followed them was deleted in the same pass: it
documented `_is_spam` dedup maths for a function that no longer exists, and
"re-verified every line number in the audit tables" against a revision three
weeks and fifteen stages behind.*
