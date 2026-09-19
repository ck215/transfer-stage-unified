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

## `base.ManagedModel` (protocol, `src/model/base.py`, 23 lines)

Runtime-checkable `Protocol`, not a base class — models don't inherit from
it, they just need to structurally match it.

**Methods:**
| Method | Params | Return Type | Line | Purpose / Calls |
|---|---|---|---|---|
| `teardown` | `(self)` | `None` | :12 | Full graceful shutdown, not time-critical, "should not raise." |
| `emergency_stop` | `(self)` | `None` | :18 | Halt hardware immediately by the strongest means available, called by the global FULL STOP; "must be fast and must never block." |

**Every concrete model below implements both**, but the call sites that are
supposed to invoke `teardown()` don't always do — see
[ownership-and-lifecycle.md](ownership-and-lifecycle.md#the-close_device_view-divergence).

## `probes.BaseProbe` (`src/model/probes.py`, lines 14-491)

Backs the Stepper Probe, DC Probe, and Chuck Positioner device tabs.

**Owns:** one `controller.serial.serial` instance (`self.serial_comm`, may be
`None` if constructed with no port) and one `controller.gamepad.ControllerPoller`
instance (`self.poller`, constructed unconditionally in `__init__`, wrapped
in try/except since pygame may be unavailable).

**Attributes (inferred types & initial values):**
- `serial_comm` (`controller.serial.serial | None`): `serial(port)` or `None`.
- `packet_format` (`str`): `PACKET_FORMAT` ("42-byte float").
- `pos_x`, `pos_y`, `pos_z` (`str`): `"0"`.
- `controller_var` (`str`): `controller_id`.
- `serial_port` (`str`): `port`.
- `active_claims` (`dict`): `active_claims or {}`.
- `poller` (`ControllerPoller | None`): Built via `ControllerPoller(controller_id)`.
- `x_step`, `y_step`, `z_step` (`str`): `"16"`.
- `x_dist`, `y_dist`, `z_dist` (`str`): `"0"`.
- `full_speed`, `man_full_speed` (`str`): `"400"`.
- `system_enabled`, `auton_flag`, `manual_flag`, `is_stepping` (`bool`): `False`. Mirrors of the model's *belief* about firmware state.
- `last_activity_time` (`float`): `time.time()`.
- `_interlock_stop` (`threading.Event`): `threading.Event()`.
- `_interlock_thread` (`threading.Thread | None`): `None`.

**Method inventory**:
| Method | Params | Returns | Line | Purpose / Calls |
|---|---|---|---|---|
| `__del__` | `(self)` | `None` | :19 | Prints destructor message. |
| `__init__` | `(self, port, controller_id, active_claims=None)` | :22 | Constructs `serial_comm` and `poller`. |
| `vel_x`, `vel_y`, `vel_z` | `(self)` *(props)* | `float` | :70-95 | Live gamepad axis readback via `poller.get_mapped_state()`. |
| `ui_schema` | `(self)` *(prop)* | `dict` | :96 | Base schema definition. |
| `reconnect_serial` | `(self)` | `None` | :161 | Closes `serial_comm`, rebuilds `serial(port)`. |
| `get_available_controllers` | `(self)` | `list` | :177 | Delegates to `poller.get_physical_controllers()`. |
| `set_controller` | `(self, controller_id)` | `None` | :182 | Delegates to `poller.set_controller()`. |
| `open_controller_log` | `(self)` | `None` | :194 | UI hook. |
| `toggle_manual` | `(self)` | `None` | :197 | Calls `full_stop()` or `enter_manual()`. |
| `toggle_auton` | `(self)` | `None` | :203 | Calls `full_stop()` or `enter_auton()`. |
| `send_stop_command` | `(self)` | `None` | :209 | Calls `serial_comm.send_autonomous_command(params)` with zeros. |
| `enter_auton` | `(self)` | `None` | :219 | Calls `enable()`, `send_stop_command()`. |
| `enter_manual` | `(self)` | `None` | :226 | Checks gamepad; calls `enable()`, `send_stop_command()`. |
| `macro_start_auton` | `(self)` | `None` | :245 | Calls `enable()`, `send_autonomous_command()`. |
| `run_script` | `(self, script_path)` | `None` | :253 | Calls `enter_auton()`, spins thread invoking `serial_comm.send_autonomous_command()`, `full_stop()`. |
| `get_params` | `(self)` | `dict` | :338 | Returns command dictionary. |
| `send_autonomous_command` | `(self)` | `None` | :353 | Calls `touch_activity()`, `serial_comm.send_autonomous_command()`. |
| `send_manual_mode_command` | `(self, controller_params)` | `None` | :358 | Calls `touch_activity()`, `serial_comm.send_manual_mode_command()`. |
| `read_position` | `(self)` | `None` | :390 | Calls `serial_comm.read_position()`. |
| `touch_activity` | `(self)` | `None` | :396 | Refreshes `last_activity_time`. |
| `_start_interlock_watchdog` | `(self)` | `None` | :399 | Spins interlock thread. Calls `disable()`. |
| `enable` | `(self)` | `bool` | :423 | Calls `serial_comm.enable()`, `_start_interlock_watchdog()`. |
| `toggle_enable` | `(self)` | `None` | :436 | Calls `disable()` or `enable()`. |
| `_stop_and_disarm` | `(self)` | `None` | :442 | Calls `send_stop_command()`, `serial_comm.disable()`. |
| `disable` | `(self)` | `None` | :464 | Calls `_stop_and_disarm()`. |
| `full_stop` | `(self)` | `None` | :467 | Calls `_stop_and_disarm()`. |
| `power_down` | `(self)` | `None` | :470 | Calls `_stop_and_disarm()`, writes raw `'k'` directly to serial wrapper. |
| `teardown` | `(self)` | `None` | :479 | Stops poller, calls `power_down()`, closes `serial_comm`. |
| `emergency_stop` | `(self)` | `None` | :489 | Calls `power_down()`. |

**No `disconnect()` method exists on `BaseProbe`** — relevant because `view.py`'s `close_device_view` checks for it.

### `probes` Subclasses (`src/model/probes.py`)

- **`StepperProbe` (lines 493-498):** Overrides `x_step`, `y_step`, `z_step` to `"1"` in `__init__`. Does **not** override `ui_schema`.
- **`DCProbe` (lines 501-528):**
  - **Attributes:** Adds `slow_speed` (`"0"`) and `brake_distance` (`"0"`). Sets `x_step`/`y_step`/`z_step` to `"1"`, `full_speed`/`man_full_speed` to `"120"`.
  - **Methods:** Overrides `ui_schema` (adds Brake params to Configuration) and `get_params()`.
- **`ChuckPositioner` (lines 531-537):** Overrides `x_step`, `y_step`, `z_step` to `"2"`. Does **not** override `ui_schema`.

**Inconsistencies & Anomalies (Probes):**
- **Hardware-critical wrapper bypass:** `power_down()` (:472) and `run_script()` (:324, :328) bypass `self.serial_comm` wrapper functions to write raw bytes directly to `self.serial_comm.ser.write`.
- **Parameter Coupling:** `BaseProbe.send_stop_command()` (:213) explicitly hardcodes `"slow_speed": 0` and `"brake_distance": 0`. `BaseProbe` is tightly coupled to `DCProbe`'s extended parameter space.
- **Race Condition in `run_script`:** `run_script` blindly sets `self.is_stepping = True` and starts execution without checking if a script or autonomous run is already active.
- **Wasted Packet Sends:** `send_manual_mode_command()` correctly flips `manual_flag = False` if no gamepad is connected, but *still proceeds to send a zeroed-out manual-mode packet* over serial anyway during that tick.

## `redpercent_system.RedPercentSystem` (`src/model/redpercent_system.py`, 329 lines)

Backs the Red Percent Window tab — screen-region color monitoring, not tied
to a serial device at all (no `serial_comm`/`poller`).

**Attributes (inferred types & initial values):**
- `red_percent` (`float`): `0.0`.
- `is_monitoring` (`bool`): `False`. *(Never read/used).*
- `stop_event` (`threading.Event`): `threading.Event()`. *(Never read/used).*
- `thread`, `monitor_thread`, `baseline` (`None`): *(Never read/used).*
- `data_log` (`RedPercentDataLog | None`): `None`.
- `stepper_model` (`Any`): `None`.
- `available_probes` (`dict`): `{}`.
- `selected_probe_name` (`str | None`): `None`.
- `sync_dimensions` (`list`): `[]`.
- `monitoring` (`bool`): `False`. *(The actual loop condition).*
- `focus_area` (`dict | None`): `None`.
- `baseline_red`, `current_red`, `red_change` (`float`): `0.0`.
- `_monitor_thread` (`threading.Thread | None`): `None`.
- `probe_name`, `probe_tilt_angle` (`str`): `""`.

**Method inventory**:
| Method | Params | Returns | Line | Purpose / Calls |
|---|---|---|---|---|
| `__init__` | `(self)` | `None` | :60 | Initialization. |
| `set_focus_area` | `(self, x, y, w, h)` | `bool` | :86 | Updates `focus_area` dict. |
| `set_stepper_model` | `(self, probe_name)` | `None` | :91 | Binds model from `available_probes`. |
| `capture_focus_area`| `(self, sct)` | `ndarray` | :97 | Grabs screen region. |
| `detect_red` | `(self, image)` | `float` | :111 | OpenCV/NumPy mask math. |
| `sync_x/y/z` | `(self, value)` *(props)*| `bool` | :124 | Appends/removes from `sync_dimensions`. |
| `save_log_web` | `(self)` | `None` | :157 | Calls `save_log()` with hardcoded path. |
| `ui_schema` | `(self)` *(prop)* | `dict` | :192 | Defines tabs. |
| `save_log` | `(self, file_path)` | `None` | :240 | Calls `data_log.save_to_csv()`. |
| `start_monitoring` | `(self)` | `None` | :261 | Starts `_monitor_colors` thread. |
| `stop_monitoring` | `(self)` | `None` | :272 | Sets `monitoring = False`. |
| `teardown` / `emergency_stop` | `(self)` | `None` | :276 | Both alias `stop_monitoring()`. |
| `_monitor_colors` | `(self)` | `None` | :286 | Thread target polling screen and model position/velocity. |

**Inconsistencies & Anomalies:**
- **Dead Code:** `is_monitoring`, `stop_event`, `thread`, `monitor_thread`, and `baseline` are initialized in `__init__` but never touched again. The thread loop strictly relies on `monitoring` and `_monitor_thread`.
- **Uninitialized Attribute Reference:** `self.last_logged_red` is referenced and dynamically created via `hasattr()` on line 305 inside `_monitor_colors`. It is never initialized in `__init__`.
- **Ghost Check:** `get_available_probe_names()` (:184) checks `getattr(probe, "_disabled_in_setup", False)`, but no probe subclass ever initializes this attribute.

## `rotator_system.RotatorSystem` (`src/model/rotator_system.py`, 282 lines)

Backs the SMC100 Rotator tab — wraps `src/lib/smc100.py`'s SMC100 driver.
Thread-safe via `self._lock`; async operations (`connect`, moves) run via
`_run_async`/`_async_wrapper` on background threads.

**Attributes (inferred types & initial values):**
- `port` (`str | None`): `default_port`.
- `smc_id` (`int`): `1`.
- `_lock` (`threading.Lock`): `threading.Lock()`.
- `_position` (`float | None`): `None`.
- `_state` (`str`): `"Disconnected"`.
- `_error` (`str`): `"0"`.
- `smc` (`smc100.SMC100 | None`): `None`.
- `is_connected` (`bool`): `False`.
- `error_callback`, `confirm_rotation_callback` (`Callable | None`): `None`.
- `target_deg`, `step_deg` (`str`): `"0"`.

**Method inventory**:
| Method | Params | Returns | Line | Purpose / Calls |
|---|---|---|---|---|
| `__init__` | `(self, default_port)` | `None` | :12 | Optionally calls `connect()`. |
| `position`, `state`, `error`| *(props)* | varies | :32 | Lock-guarded getters/setters. |
| `connect` | `(self, port, smc_id)`| `None` | :75 | Builds `smc100.SMC100`. |
| `disconnect` / `teardown`| `(self)` | `None` | :108 | Destroys SMC instance. |
| `emergency_stop`/`stop`| `(self)` | `None` | :125 | Calls `smc.stop()`. |
| `home` | `(self)` | `None` | :128 | Calls `_run_async(smc.home)`. |
| `move_absolute` | `(self, target)` | `None` | :217 | Gates through `_confirm_rotation()`, runs async. |
| `move_relative` | `(self, step)` | `None` | :223 | Computes target, confirms, runs async. |
| `poll_status` | `(self)` | `None` | :267 | Sync readback, sets props. |

## `temperature_system.TemperatureSystem` (`src/model/temperature_system.py`, 203 lines)

Backs the Temperature Controller tab (talks to `firmware/temp_controller`).

**Attributes (inferred types & initial values):**
- `setpoint` (`str`): `"0"`.
- `ramp_rate` (`str`): `"10"`.
- `p_term` (`str`): `"2.0"`.
- `i_term` (`str`): `"0.5"`.
- `d_term` (`str`): `".1"`.
- `offset` (`str`): `"0"`.
- `current_temp` (`str`): `"N/A"`.
- `_lock` (`threading.Lock`): `threading.Lock()`.
- `tempC`, `time`, `sp` (`list`): `[]`.
- `cnt` (`int`): `0`.
- `serial_conn` (`serial | None`): `serial(port, baud_rate=115200)`.
- `continue_reading` (`bool`): `True`.
- `serial_thread` (`threading.Thread`): Polls via `read_serial_data`.

**Method inventory**:
| Method | Params | Returns | Line | Purpose / Calls |
|---|---|---|---|---|
| `__init__` | `(self, port)` | `None` | :10 | Writes init frame, spins thread. |
| `ui_schema` | `(self)` *(prop)* | `dict` | :39 | UI configuration. |
| `send_settings` | `(self)` | `None` | :70 | Pushes setpoint/PID via `ser.write`. |
| `read_serial_data`| `(self)` | `None` | :99 | Loop calling `process_raw_data`. |
| `process_raw_data`| `(self, line)` | `None` | :129 | Telemetry ingestion. |
| `get_history` | `(self)` | `tuple` | :155 | Lock-safe history slice. |
| `stop` / `emergency_stop`| `(self)` | `None` | :160 | Sends `['0', spdelay, '0', '0', '0', ...]`. |
| `close` / `disconnect` / `teardown`| `(self)` | `None` | :181 | Sends zero-frame, closes connection. |

**Inconsistencies & Anomalies:**
- **Hardware-critical wrapper bypass:** Like `BaseProbe`, `send_settings` (:92), `stop` (:176), and `close` (:186) ignore `self.serial_conn.write()` and bypass straight to `self.serial_conn.ser.write()`.
- **E-Stop Cutoff Behavior:** The `stop()` implementation (:160) does *not* just zero the setpoint for a gradual cooldown. It explicitly sends `0` for the P, I, and D parameters (`vals = ['0', spdelay, '0', '0', '0', ...]`), which forces the PID loop to output exactly `0` PWM instantly. This means `emergency_stop()` *is* a true, instant hardware cutoff, directly contradicting prior architectural notes.

## `system_manager.SystemManager` (`src/model/system_manager.py`, 79 lines)

The one central registry. Holds `active_models: dict[name, model]` behind a lock.

**Attributes (inferred types & initial values):**
- `active_models` (`dict`): `{}`.
- `lock` (`threading.Lock`): `threading.Lock()`.

**Method inventory**:
| Method | Params | Returns | Line | Purpose / Calls |
|---|---|---|---|---|
| `register_model` | `(self, name, model)`| `None` | :10 | Inserts to dictionary. |
| `_teardown_model`| `(self, name, model)`| `None` | :18 | Calls `model.teardown()`. |
| `remove_model` | `(self, name)` | `Any` | :24 | Pops from dictionary. |
| `get_active_models_snapshot`| `(self)` | `dict` | :29 | Shallow copy for iteration. |
| `reboot_model` | `(self, name, constructor, ...)`| `Any` | :34 | Tears down, waits 1s, reconstructs. |
| `shutdown_all` | `(self)` | `None` | :59 | Clears dict, tears down all. |
| `full_stop_all` | `(self)` | `None` | :70 | Calls `model.emergency_stop()` globally. |

## `plot_data.py` / `numeric.py` (small utilities)

`plot_data.py` (114 lines): `parse_red_percent_csv` + `render_red_percent_figure`. Pure functions.

`numeric.py` (33 lines): `num` (:3) and `safe_float` (:18) coercion helpers.

---
## Deep-dive addendum (agy, 2026-09-18)

Expanded `models.md` with full attribute/method inventories, type inferences, and call-site graphs. Cross-checked all prior claims against live source (commit `4ffb2e3`).

**Corrected claims:**
- **TemperatureSystem Cooldown:** The previous claim that `TemperatureSystem.stop()` (and thus `emergency_stop()`) initiates a "gradual PID-driven cooldown" is false. The function sends zeroes for the P, I, and D parameters, ensuring an immediate 0-PWM cutoff. This is physically safer but contradicts the older assumption.
- **Probe UI Schemas:** `StepperProbe` and `ChuckPositioner` do *not* override `ui_schema` as previously stated; they only override step scales. Only `DCProbe` overrides the schema.

**New Inconsistencies Discovered (candidates for `known-issues.md`):**
1. **Raw Serial Bypass:** `BaseProbe` (`power_down()`, `run_script()`) and `TemperatureSystem` (`stop()`, `send_settings()`, `close()`) bypass their `serial_conn` wrapper methods entirely and write directly to the raw `pyserial` socket (`self.serial_conn.ser.write`).
2. **Coupled Base Class:** `BaseProbe.send_stop_command()` explicitly zeroes out `"slow_speed"` and `"brake_distance"` — parameters that nominally only exist on the `DCProbe` subclass.
3. **Dead State:** `RedPercentSystem.__init__` declares five lock/thread state variables (`is_monitoring`, `stop_event`, `thread`, `monitor_thread`, `baseline`) that are completely unread by the rest of the file (which uses `monitoring` and `_monitor_thread` instead). Conversely, `last_logged_red` is referenced and built inside the monitoring thread without ever being declared in `__init__`.
4. **Wasted Packet Sends:** `BaseProbe.send_manual_mode_command()` correctly checks if the gamepad dropped out and resets `manual_flag`, but instead of aborting the send, it still writes a full zero-padded manual packet to the firmware on that tick.
5. **G-Code Execution Race:** `BaseProbe.run_script()` sets `self.is_stepping = True` without checking if the stage is already actively executing a routine.
