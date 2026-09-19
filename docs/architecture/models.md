# Models

All models live in `src/model/`. Every model that `SystemManager` registers
is expected to satisfy the `ManagedModel` protocol in `base.py`
(`teardown()`, `emergency_stop()`) — see
[ownership-and-lifecycle.md](ownership-and-lifecycle.md) for where that
contract is and isn't actually honored.

Most models expose a `ui_schema` property, a dict of `{"sections": [...]}`
that both the PySide6 (`QtDynamicView`) and Tkinter (`DynamicView`) generic
view renderers consume to build their UI without per-field custom code. See
[views.md](views.md) for the element-type contract (`readonly`, `entry`,
`button`, `toggle`, `dropdown`, `file_picker`).

## `base.ManagedModel` (protocol, `src/model/base.py`, 22 lines)

Runtime-checkable `Protocol`, not a base class — models don't inherit from
it, they just need to structurally match it. Two methods:
- `teardown()` — full graceful shutdown, not time-critical, "should not raise."
- `emergency_stop()` — halt hardware immediately by the strongest means
  available, called by the global FULL STOP; "must be fast and must never
  block."

**Every concrete model below implements both**, but the call sites that are
supposed to invoke `teardown()` don't always do — see
[ownership-and-lifecycle.md](ownership-and-lifecycle.md#the-close_device_view-divergence).

## `probes.BaseProbe` + subclasses (`src/model/probes.py`, 516 lines)

Backs the Stepper Probe, DC Probe, and Chuck Positioner device tabs.
`StepperProbe`, `DCProbe`, `ChuckPositioner` are thin `BaseProbe` subclasses
that mostly just override `ui_schema` for device-specific fields
(`probes.py:473-516`) — the actual logic lives entirely in `BaseProbe`.

**Owns:** one `controller.serial.serial` instance (`self.serial_comm`, may be
`None` if constructed with no port) and one `controller.gamepad.ControllerPoller`
instance (`self.poller`, constructed unconditionally in `__init__`, wrapped
in try/except since pygame may be unavailable).

**Key state:** `system_enabled`, `auton_flag`, `manual_flag`, `is_stepping` —
mirrors of what the model *believes* the firmware's state is. **These are
Python-side beliefs only; the firmware keeps its own independent copy that
persists across Python model reconstruction** (see
[known-issues.md](known-issues.md) — this mismatch caused the coil-disable
safety bug fixed 2026-09-18, commit `addb0b8`).

**Method inventory** (line numbers as of this writing):
| Method | Purpose |
|---|---|
| `__init__(port, controller_id, active_claims)` | Constructs `serial_comm` and `poller`; NOTE both are recreated from scratch on every dock reopen — see lifecycle doc. |
| `vel_x` / `vel_y` / `vel_z` (props) | Live gamepad axis readback via `poller.get_mapped_state()`. |
| `ui_schema` (prop, :97) | Base schema: Coordinate Frame, Configuration, System Control sections. |
| `reconnect_serial()` :161 | Rebuilds `serial_comm` for a new port. Not exposed in the live dashboard schema (see comment :148-154 — reconnecting mid-session risks desyncing Python/firmware enable state, the same class of bug as the coil issue). |
| `get_available_controllers()` :177 | Delegates to `poller.get_physical_controllers()`. |
| `set_controller(controller_id)` :182 | Delegates to `poller.set_controller(...)`. |
| `open_controller_log()` :194 | UI-only hook, handled specially by the view's `_execute_command`. |
| `toggle_manual()` / `toggle_auton()` :197,203 | Flip into/out of manual/auton mode; both route through `full_stop()` when already active. |
| `send_stop_command()` :209 | Sends the 12-field zero-motion command over serial. Does **not** touch the driver enable pin by itself — see [controllers.md](controllers.md#firmware-command-surface). |
| `enter_auton()` / `enter_manual()` :219,226 | Call `enable()` first; no-op (silent return) if it fails. |
| `macro_start_auton()` :233 | Enables + starts a stepping run. |
| `run_script(script_path)` :241 | G-code execution path; not currently exposed as a UI entry point (file_picker removed from schema, see [views.md](views.md#the-file_picker-element-type)). |
| `send_autonomous_command()` / `send_manual_mode_command(controller_params)` :341,346 | Per-tick command senders. `send_manual_mode_command` silently reverted `manual_flag` to `False` with only a `print()` when no gamepad was attached — **fixed 2026-09-18** to also call `ErrorPopupManager.report_warning(...)`. |
| `read_position()` :378 | Polls position from `serial_comm`. |
| `touch_activity()` / `_start_interlock_watchdog()` :384,387 | 5-minute idle auto-disable, lives here so every frontend (including web) shares it. |
| `enable()` :411 | Sends `'e'` over serial; reports a warning popup on failure (`ErrorPopupManager.report_warning("Enable Failed", ...)`) — already correctly wired. |
| `toggle_enable()` :424 | Direct enable/disable toggle (not currently exposed — System Power control was removed from the schema, see comment :127-130). |
| `_stop_and_disarm()` :430 | **The actual E-stop implementation.** Zeroes flags, sends the zero-motion command, then unconditionally sends the hardware `'d'` disable (fixed 2026-09-18 — previously gated behind `if self.system_enabled`, see [known-issues.md](known-issues.md)). |
| `disable()` / `full_stop()` / `power_down()` :442,445,448 | All three are thin aliases over `_stop_and_disarm()` — functionally identical today, kept as separate names for call-site clarity (E-stop vs. explicit disable vs. mode-exit). |
| `teardown()` :459 | The `ManagedModel`-contract shutdown: stop+close poller, `power_down()`, close `serial_comm`. **This is the one method that actually releases the OS-level serial handle** — see lifecycle doc for why dock-close doesn't call it. |
| `emergency_stop()` :469 | `power_down()`. |

**No `disconnect()` method exists on `BaseProbe`** — relevant because
`view.py`'s `close_device_view` checks `hasattr(model, 'disconnect')` before
releasing the serial port, and probes never satisfy that check. See
[known-issues.md](known-issues.md).

## `redpercent_system.RedPercentSystem` (`src/model/redpercent_system.py`, 328 lines)

Backs the Red Percent Window tab — screen-region color monitoring, not tied
to a serial device at all (no `serial_comm`/`poller`).

**Owns:** an `mss`-based screenshot thread (`self._monitor_thread`), a
`RedPercentDataLog` (CSV logger), and a reference dict `available_probes`
(other active models that expose `pos_x`/`pos_y`/`pos_z`, wired in by
`app_bootstrap.build_models`/`view.py`'s dynamic-construct path so it can log
stepper position alongside red-percent readings).

**`sync_x`/`sync_y`/`sync_z`** are properties backed by a single
`sync_dimensions` list, not three independent booleans — `toggle_sync_x()`
etc. append/remove from that list.

`ui_schema` (:192) sections: Probe Metadata, Sync Dimensions (the three
toggles — fixed 2026-09-18 to carry `true_text`/`false_text`, previously
`None` and crashing the shared toggle-refresh loop, see known-issues),
Red Detection (readonly), System Control (Start/Stop Monitoring, Reset
Baseline, Set Focus Area, Save Log, Plot Data).

`start_monitoring()`/`stop_monitoring()` toggle `self.monitoring`, consumed
by the background thread's `while self.monitoring:` loop — **note the
PySide6 view's `cleanup()` does call `stop_monitoring()` on dock close
(`RedPercentDynamicView.cleanup()`), but this is a hand-added override, not
the `ManagedModel.teardown()` contract** (`RedPercentSystem` doesn't
implement `teardown()`/`emergency_stop()` at all — it isn't in the
`ManagedModel`-conformant set; flagged in known-issues).

## `rotator_system.RotatorSystem` (`src/model/rotator_system.py`, 281 lines)

Backs the SMC100 Rotator tab — wraps `src/lib/smc100.py`'s SMC100 driver.

Thread-safe via `self._lock`; async operations (`connect`, moves) run via
`_run_async`/`_async_wrapper` on background threads so the UI never blocks
on serial I/O.

| Method | Purpose |
|---|---|
| `connect(port, smc_id)` :75 | Builds the underlying `smc100.SMC100` instance. Reports a `report_error` on failure — already correctly wired. |
| `disconnect()` :108 | Clears state, closes the SMC connection. |
| `teardown()` :122 | = `disconnect()`. |
| `emergency_stop()` :125 | = `stop()`. |
| `home()`, `move_absolute(deg)`, `move_relative(deg)` | Motion commands; `move_absolute` gates through `_confirm_rotation` for the ±30° safety dialog (wired to `confirm_rotation_callback`, set by the view — see views.md). |
| `poll_status()` :267 | Per-tick status/position readback. |

Because `disconnect()` *is* what `teardown()` calls, and `view.py`'s
`close_device_view` checks `hasattr(model, 'disconnect')` before calling it,
**the rotator is teardown-correct on dock close** — unlike the probes. Worth
double-checking whether `disconnect()` alone covers everything `teardown()`
would (it does here, since `teardown = disconnect` verbatim), but this
equivalence should be re-verified any time either method changes.

## `temperature_system.TemperatureSystem` (`src/model/temperature_system.py`, 202 lines)

Backs the Temperature Controller tab (talks to `firmware/temp_controller`).

| Method | Purpose |
|---|---|
| `send_settings()` :70 | Pushes setpoint/PID params over serial. |
| `read_serial_data()` / `process_raw_data(line)` :99,129 | Per-tick telemetry ingestion. |
| `get_history()` :155 | Returns logged temperature history for plotting. |
| `stop()` :160 | Logical stop (not necessarily hardware disable — verify against the firmware's actual PWM-cutoff behavior before treating this as an E-stop equivalent). |
| `close()` :181 | Closes the serial connection. |
| `disconnect()` :194 | Present, satisfies `hasattr(model, 'disconnect')` in `close_device_view`. |
| `teardown()` / `emergency_stop()` :198,201 | Present — verify these actually call `close()`/`stop()` respectively rather than duplicating logic (not yet cross-checked line-by-line; flagged for follow-up). |

## `system_manager.SystemManager` (`src/model/system_manager.py`, 78 lines)

The one central registry — see
[ownership-and-lifecycle.md](ownership-and-lifecycle.md) for the full
picture. Holds `active_models: dict[name, model]` behind a lock. Exposes the
*correct* lifecycle primitives (`register_model`, `remove_model`,
`reboot_model`, `shutdown_all`, `full_stop_all`) — all of which call
`model.teardown()`/`model.emergency_stop()` properly. The divergence is that
`view.py`'s per-dock close/open flow doesn't route through these.

## `plot_data.py` / `numeric.py` (small utilities)

`plot_data.py` (113 lines): `parse_red_percent_csv` + `render_red_percent_figure`,
pure functions, no state — used identically by both the PySide6 `PlotDialog`
and Tkinter's `open_plot_window`. No parity concerns here, this one's already
shared correctly.

`numeric.py` (32 lines): the `_num(val, default, minimum=..., integer=...)`
helper used throughout `probes.py`'s `get_params()` — coerces UI string
fields to safe numeric values.

---
*Last verified against commit `4ffb2e3` (2026-09-18).*
