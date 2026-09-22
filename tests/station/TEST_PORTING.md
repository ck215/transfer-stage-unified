# Porting inventory: the old suite against `station/`

One row per test file under `tests/` (excluding `tests/station/`, which is the
rebuild's own). 135 files, about 1000 test functions. This is the work
list for the second wave of test agents: it says, for every old file, what it
actually defends, which new file inherits that job, and whether the test
survives the redesign.

**Read the verdict column as an instruction, not a grade.**

* **PORT** - the behaviour and the API both survive. Re-point the imports,
  rename what `docs/rebuild/design.rules` renames, and the test stands.
* **PORT-ADAPTED** - the behaviour survives but the shape does not: methods
  merged, a hook moved to another class, three view copies collapsed into one.
  The test has to be re-authored against the new surface. Several of these get
  *smaller*, because the thing they were guarding is now structural.
* **VOID** - the feature is gone by owner ruling or designed out by the new
  architecture. Do not port. The five VOID families are:

  1. **scripts / G-code** - owner ruling 2026-09-21; removes finding 8 with it;
  2. **hide/show** - same ruling, replaced by destruct-on-close
     (`Controller.remove` / `Controller.add`);
  3. **client liveness inside a model** - one `WebView` watchdog calling
     `Controller.estop_all` replaces the per-model gates;
  4. **the web session token** - purged; localhost bind plus an Origin check
     is the whole boundary;
  5. **per-view setup wizards** - one `Setup` panel rendered by all three
     views replaces three nested wizards.

Two cautions carried from `CLAUDE.md` and the rebuild brief:

1. **A VOID verdict is about the mechanism, never about the guarantee.**
   `test_manager24_hide_brings_hardware_safe.py` tests `hide()`, which is
   void - but the guarantee it defends (removing a device de-energizes it
   first) retargets onto `Controller.remove` and is listed in the safety
   table below. Deleting the file without re-pinning the guarantee is exactly
   how a fixed defect comes back.
2. **Test-function counts are approximate**: a `^def test_` count, so
   parametrised cases count once and class-based helpers are not counted.
   They are for sizing the second wave, not for a ledger.

Owner files are given relative to `station/`.

## Totals by verdict

| Verdict | Files | Approx. test functions |
| --- | ---: | ---: |
| PORT | 22 | 213 |
| PORT-ADAPTED | 103 | 702 |
| VOID | 10 | 85 |
| **Total** | **135** | **1000** |

## Totals by new owner file

A file is counted once per owner it names, so these sum to more than the
table above. This is the second wave's workload, by agent.

| Owner in `station/` | Old files | Approx. test functions |
| --- | ---: | ---: |
| `devices/serial_port.py` | 15 | 147 |
| `models/probe.py` | 17 | 144 |
| `controller.py` | 16 | 133 |
| `model.py` | 9 | 118 |
| `views/web/server.py` | 20 | 115 |
| `models/rotator.py` | 9 | 107 |
| `models/red_monitor.py` | 16 | 101 |
| _(none - VOID)_ | 10 | 85 |
| `devices/gamepad.py` | 6 | 70 |
| `setup.py` | 5 | 63 |
| `models/heater.py` | 11 | 56 |
| `views/qt.py` | 7 | 46 |
| `panel.py` | 6 | 44 |
| `views/base.py` | 10 | 39 |
| `events.py` | 8 | 30 |
| `param.py` | 5 | 30 |
| `tests/station/test_architecture.py (whole package)` | 1 | 28 |
| `schema.py` | 6 | 27 |
| `views/tk.py` | 6 | 19 |
| `devices/smc100.py` | 3 | 18 |
| `models/plot_data.py` | 3 | 12 |
| `result.py` | 3 | 11 |
| `views/theme.py` | 2 | 7 |
| `devices/screen.py` | 1 | 4 |

## The inventory

| Old test file | What it covers | New owner | Verdict | ~Tests | Notes |
| --- | --- | --- | --- | ---: | --- |
| `tests/architecture/test_invariants.py` | The RC-1..RC-13 grep invariants from root-causes.md, each xfail(strict) against `src/`'s layout. | `tests/station/test_architecture.py (whole package)` | PORT-ADAPTED | 28 | Every path and name it greps is gone. The *shape* of the check survives in the rebuild's own architecture test; re-derive one invariant per RC against `station/`. |
| `tests/core/test_app_bootstrap.py` | `discover_ports`, `probe_device_at`, `build_models`, `validate_assignment`. | `setup.py` | PORT-ADAPTED | 17 | Renames: scan_ports / identify / build / validate. `normalize_config` is designed out (one Setup produces one config shape). |
| `tests/core/test_composition_root.py` | I-9.1 the monitor's probe list is a subset of live models; I-9.2 the three launchers build identical managers. | `setup.py` `controller.py` | PORT-ADAPTED | 23 | I-9.2 collapses: there is one `launch()`. I-9.1 becomes `on_model_added` / `on_model_removed` and must still be pinned. |
| `tests/core/test_dc6_mode_gated_entries.py` | DC-6: `disabled_when` enforced by the model, not only rendered. | `panel.py` `models/probe.py` | PORT-ADAPTED | 10 | Enforcement moves into `Panel.run` / `Panel._allows`, so it guards all three views at once. |
| `tests/core/test_edge_mvc_model.py` | Corrupt/hostile input to probes and the manager; the rotator's +/-30 tubing confirmation. | `models/probe.py` `models/rotator.py` `controller.py` | PORT-ADAPTED | 23 | SAFETY: the tubing block (`move_absolute`, `move_relative_*` past 30 deg) must port. `NeedsConfirmation` becomes a raised `NeedsConfirm`. |
| `tests/core/test_errors7_controller_swap_reporting.py` | A gamepad swap that fails is reported, not printed. | `devices/gamepad.py` `events.py` | PORT-ADAPTED | 2 | `set_controller` -> `Gamepad.bind`. |
| `tests/core/test_errors7_rotator_share.py` | A move refused by the FULL STOP latch was reported to the operator as success. | `models/rotator.py` `result.py` | PORT-ADAPTED | 5 | SAFETY. Designed out by `Refused` being an exception, but the behaviour still needs a test. |
| `tests/core/test_errors7_temperature_reporting.py` | Swallowed exceptions around the heater-off frame and the port close. | `models/heater.py` | PORT | 4 | SAFETY: an off-frame that did not land must be reported. |
| `tests/core/test_gamepad_interlock.py` | MANUAL requires a bound gamepad; losing the pad leaves MANUAL through the one transition. | `models/probe.py` | PORT | 3 | SAFETY (I-3.2). `BaseProbe(port=, controller_id=)` -> `Probe(port=, gamepad=)`. |
| `tests/core/test_hide_show.py` | D-1/D-2 hide/show semantics on the manager. | - | VOID | 13 | Owner ruling 2026-09-21: closing a tab destructs the model. No hidden state exists (carry.json VOID cluster). |
| `tests/core/test_integration.py` | `shutdown_all` reaches every registered model. | `controller.py` | PORT-ADAPTED | 2 | `shutdown_all` -> `Controller.close`. |
| `tests/core/test_lifecycle_exit.py` | I-1.3: atexit / SIGINT / SIGTERM hooks stop the hardware. | `controller.py` | PORT-ADAPTED | 7 | SAFETY. `lifecycle.install_exit_hooks` -> `Controller._hook_exit`; module globals are gone. |
| `tests/core/test_lifecycle_teardown.py` | I-1.2: teardown order (hardware stop -> threads -> transport) with each step isolated. | `model.py` | PORT-ADAPTED | 21 | SAFETY. The order is now inherited in `Model.close`, so one test set covers every model instead of three. |
| `tests/core/test_manager20_scan_abort.py` | A port scan can be told to give up mid-probe. | `setup.py` | PORT-ADAPTED | 6 | `probe_device_at(should_abort=)` -> `Setup.identify`; cancellation is part of `Setup.state`. |
| `tests/core/test_manager20_scanner_wiring.py` | AST check that `app.py`'s Qt `SetupWindow` defines and wires `closeEvent` / `should_abort`. | - | VOID | 17 | Per-view setup wizards are designed out: one `Setup` panel rendered by every view's PanelView. |
| `tests/core/test_manager21_stop_confirmation.py` | FULL STOP may not report a stop it could not confirm. | `controller.py` `model.py` | PORT | 8 | SAFETY. `full_stop_all` -> `Controller.estop_all`; `Model.estop` already returns the confirmation. |
| `tests/core/test_manager22_clear_estop_is_reachable.py` | A latched device must be clearable by an operator, through a schema command. | `model.py` `panel.py` | PORT | 8 | SAFETY. `Model.clear_estop` / `toggle_estop` are inherited, so every model gets it. |
| `tests/core/test_manager24_hide_brings_hardware_safe.py` | Removing a device from view de-energizes it first (D-2). | `controller.py` | PORT-ADAPTED | 5 | SAFETY. `hide()` is void, but the guarantee retargets onto `Controller.remove` = estop, close, drop. |
| `tests/core/test_manager25_shutdown_stops_all_first.py` | Every device is stopped before any device is torn down; one hanging stop cannot block the rest. | `controller.py` | PORT | 4 | SAFETY. `_stop_concurrently` -> `Controller._estop_concurrently`, one 1.0 s budget. |
| `tests/core/test_model_interactions.py` | Probe <-> Red Percent interaction through the manager. | `models/probe.py` `models/red_monitor.py` | PORT-ADAPTED | 12 | The registry seam becomes `on_model_added` / `on_model_removed`. |
| `tests/core/test_model_owned_loops.py` | I-4.2 / I-4.3: the jog pump and sampler live in the model; a zeroed frame is sent on leaving MANUAL. | `models/probe.py` | PORT | 11 | SAFETY. This is the Web-parity proof and the neutral-on-exit guarantee. `start_loops` -> `Model.open`. |
| `tests/core/test_model_round1.py` | Broad construction/teardown smoke across every model plus the manager. | `model.py` `controller.py` | PORT-ADAPTED | 8 | Split by model; most of it is now the inherited `Model` contract and belongs in one file. |
| `tests/core/test_model_round2.py` | Heater PID/thermal drift simulation and rotator/monitor odds and ends. | `models/heater.py` | PORT-ADAPTED | 2 |  |
| `tests/core/test_monitoring_run.py` | RC-11 `MonitoringRun`: a run freezes its configuration and owns its log, thread and stop event. | `models/red_monitor.py` | PORT-ADAPTED | 28 | `MonitoringRun` -> `MonitorRun`, `RedPercentDataLog` -> `RunLog`, `monitoring` -> `is_running`. |
| `tests/core/test_plot_data.py` | Red Percent CSV parsing and figure rendering. | `models/plot_data.py` | PORT | 5 | Renames only: `parse_red_percent_csv` -> `parse_run_csv`, `render_red_percent_figure` -> `render_figure`. |
| `tests/core/test_probe_mode.py` | RC-3 I-3.1..I-3.4: one mode, confirmed enable, gamepad-gated MANUAL, idle interlock in every energized mode, no API write of a mode. | `models/probe.py` | PORT-ADAPTED | 21 | SAFETY, and the single most important probe file. `ENABLED_IDLE` -> `IDLE`, `AUTONOMOUS` -> `AUTO`; the six transition methods become `set_mode`. |
| `tests/core/test_probes.py` | BaseProbe unit tests with every dependency mocked (includes `run_script`). | `models/probe.py` | PORT-ADAPTED | 13 | Drop the script tests (VOID); the rest ports. |
| `tests/core/test_redpercent_datalog.py` | `RedPercentDataLog` add/save behaviour. | `models/red_monitor.py` | PORT-ADAPTED | 6 | `RunLog.add` / `RunLog.save`. |
| `tests/core/test_redpercent_errors7_reporting.py` | `save_log`'s no-data guard and success path reach the operator. | `models/red_monitor.py` `events.py` | PORT-ADAPTED | 4 | The three save entry points collapse to one `save`. |
| `tests/core/test_redpercent_run_artifacts.py` | REDPERCENT-21/22/23: run identity, output root, rectangular CSV, sidecar, operator annotation. | `models/red_monitor.py` | PORT | 13 |  |
| `tests/core/test_redpercent_schema.py` | Every toggle in every model's schema names a command that exists. | `panel.py` `schema.py` | PORT-ADAPTED | 4 | Overlaps tests/ui/test_schema_v2.py; merge the two into one conformance file. |
| `tests/core/test_redpercent.py` | Velocity casting on the probe, plus early Red Percent features. | `models/probe.py` `models/red_monitor.py` | PORT-ADAPTED | 6 | `vel_x/y/z` become one `velocity` tuple. |
| `tests/core/test_redpercent13_monitoring_schema.py` | The model publishes whether a run is active so the client can stop sampling while idle. | `models/red_monitor.py` | PORT-ADAPTED | 1 | Published through `Model.state`. |
| `tests/core/test_redpercent17_plot_blank_figure_message.py` | A plot request the data cannot satisfy gets an explanatory figure, not a blank one. | `models/plot_data.py` | PORT | 4 |  |
| `tests/core/test_redpercent19_format_schema.py` | Readonly numerics declare a format in the schema. | `schema.py` `param.py` | PORT | 1 | Formatting is `Param.format` in the new design, applied by `PanelView._text_for`. |
| `tests/core/test_redpercent4_velocity_reads.py` | A gamepad read that raises must not kill the monitor thread. | `models/probe.py` | PORT-ADAPTED | 8 | Reads `Probe.velocity` through the `Gamepad` device. |
| `tests/core/test_rotator15_error_routing.py` | The dead `error_callback` hook is gone; errors go through the event log. | `models/rotator.py` `events.py` | PORT-ADAPTED | 3 |  |
| `tests/core/test_rotator6_model_owned_sampler.py` | The rotator has a model-owned sampler that tracks hardware, cannot delay FULL STOP, and dies with the connection. | `models/rotator.py` | PORT-ADAPTED | 5 | SAFETY. Threads now start in `Model.open` and end in `Model.close`. |
| `tests/core/test_rotator6_sampler_extras.py` | Poll failure during sampling, recovery, and no thread leak across reconnect. | `models/rotator.py` | PORT-ADAPTED | 4 |  |
| `tests/core/test_rotator6_sampler_write_timeout.py` | The priority stop's own `port.write` is bounded, not just its lock acquisition. | `devices/smc100.py` `devices/serial_port.py` | PORT-ADAPTED | 3 | SAFETY. SMC100's raw I/O moves onto the shared `SerialPort`, so the bound is the transport's now. |
| `tests/core/test_rotator9_poll_failures.py` | A poll failure clears position/state instead of leaving a stale reading on screen. | `models/rotator.py` | PORT | 5 |  |
| `tests/core/test_stepper11_probes_half.py` | The typed Param table and the read-only mode flags, exercised through the web boundary. | `param.py` `models/probe.py` | PORT-ADAPTED | 4 | The web adapter is gone; drive `Controller.set_value` / `Panel.run` instead. |
| `tests/core/test_system_manager.py` | `full_stop_all` calls `emergency_stop` on every registered model. | `controller.py` | PORT-ADAPTED | 3 | SAFETY. -> `Controller.estop_all`. |
| `tests/core/test_temp10_connection_state.py` | The heater exposes the transport's link state as a schema field. | `model.py` `devices/serial_port.py` | PORT-ADAPTED | 5 | Generic now: every owned Device's status appears in `Model.state`. |
| `tests/core/test_temp10_no_port_feedback.py` | With no port, Enter Settings / Stop tell the operator instead of doing nothing. | `models/heater.py` `result.py` | PORT-ADAPTED | 4 | Becomes `raise Refused(...)`, which is the designed-in answer. |
| `tests/core/test_temp10_no_port_state.py` | With no port the temperature reads Disconnected, not "N/A" forever. | `models/heater.py` | PORT | 4 |  |
| `tests/core/test_temp2_reader_backoff.py` | The reader backs off exponentially and retries indefinitely. | `models/heater.py` | PORT | 5 |  |
| `tests/core/test_temp2_shutdown_seam.py` | A reader parked in its backoff must not outlive `close()`. | `models/heater.py` `model.py` | PORT | 2 | SAFETY-adjacent: the join is what gives the off-frame its window to drain. |
| `tests/core/test_temp9_plot_series.py` | `temp_series` feeds the generic schema plot element. | `models/heater.py` | PORT-ADAPTED | 5 | `temp_series` -> `series` (same name as `RedMonitor.series`). |
| `tests/core/test_temperature_subsystem.py` | Heater transport behaviour against a transport double: settings, stop, close, write ordering. | `models/heater.py` | PORT | 16 | Largest heater file; the golden capture now pins the frames it sends. |
| `tests/core/test_temperature.py` | Heater construction and basic accessors. | `models/heater.py` | PORT | 6 |  |
| `tests/core/test_tkinter_full_stop.py` | Clicking the Tk FULL STOP label reaches `full_stop_all`. | `views/tk.py` `views/base.py` | PORT-ADAPTED | 1 | SAFETY. -> `Dashboard.toggle_estop_all`, which is shared by Tk and Qt. |
| `tests/core/test_tkinter_teardown.py` | Closing the Tk dashboard goes through the manager's ordered shutdown. | `views/tk.py` `controller.py` | PORT-ADAPTED | 7 | SAFETY. Needs a Tk harness; mark `tk` and keep it out of the fast gate. |
| `tests/core/test_transport_truth.py` | RC-2/RC-5: a failed write is never a success, the FULL STOP latch holds, and the ROTATOR-4 tubing guard cannot be walked past. | `devices/serial_port.py` `model.py` `models/rotator.py` | PORT | 55 | SAFETY, and the single largest file in the suite. Split it: transport truth -> serial_port, latch -> model, ROTATOR-4 -> rotator. |
| `tests/core/test_typed_params.py` | RC-6: a parameter's fallback belongs to its class, not to the call site. | `param.py` | PORT | 16 | `Param` is carried over unchanged, so this is close to a straight copy. |
| `tests/core/test_view_round1.py` | PySide view construction smoke over the dashboard, panels and dialogs. | `views/qt.py` | PORT-ADAPTED | 8 | Mark `qt`. `RedPercentDynamicView`, `ControllerLogWindow` and `PlotDialog` are purged. |
| `tests/core/test_web19_client_liveness_watchdog.py` | D-8's client-liveness deadline living inside `BaseProbe`'s interlock watchdog. | - | VOID | 8 | Client liveness no longer exists in any model: one `WebView` watchdog calls `Controller.estop_all`. Re-pin there, not here. |
| `tests/core/test_web23_heater_rotator_liveness.py` | The same per-model liveness gate extended to the heater and the rotator. | - | VOID | 14 | Same reason. The behaviour it defends (a browser that stops polling stops the station) becomes one station-wide watchdog. |
| `tests/edge_cases/test_edge_mvc_boundary.py` | Numeric and struct boundary values through the transport, heater and rotator. | `devices/serial_port.py` `models/heater.py` `models/rotator.py` | PORT-ADAPTED | 4 |  |
| `tests/edge_cases/test_edge_mvc_concurrency.py` | A slow serial write must not block gamepad polling. | `devices/serial_port.py` `devices/gamepad.py` | PORT-ADAPTED | 3 |  |
| `tests/edge_cases/test_edge_mvc_disconnects.py` | Port and gamepad disconnection mid-session. | `devices/serial_port.py` `devices/gamepad.py` | PORT-ADAPTED | 7 |  |
| `tests/edge_cases/test_edge_mvc_error_router.py` | The reporting path survives inputs that should never reach it (a formatter that raises). | `events.py` | PORT-ADAPTED | 8 | `ErrorRouter`/`EventBus` -> the `events` singleton. |
| `tests/edge_cases/test_edge_mvc_invalid_params.py` | Invalid motion parameters never reach the wire. | `param.py` `models/probe.py` | PORT-ADAPTED | 6 | Validation is `Panel.run`'s all-or-nothing input commit now. |
| `tests/edge_cases/test_edge_mvc_state_transitions.py` | Rapid manual/autonomous toggling leaves the hardware in a defined state. | `models/probe.py` | PORT-ADAPTED | 6 | SAFETY. |
| `tests/edge_cases/test_qa_round1.py` | Cross-subsystem QA sweep: manager, probes, heater, rotator. | `controller.py` `model.py` | PORT-ADAPTED | 10 |  |
| `tests/hardware/test_controller_round1.py` | Transport plus gamepad wrapper behaviour under patched SDL. | `devices/gamepad.py` `devices/serial_port.py` | PORT-ADAPTED | 17 |  |
| `tests/hardware/test_gamepad.py` | All five gamepad layout classes, mapped state, deadzones, claims. | `devices/gamepad.py` | PORT-ADAPTED | 39 | The five classes become rows of `Gamepad.LAYOUTS`; re-point the tests at the table. Largest device file. |
| `tests/hardware/test_gamepad17_fallback_poller_starts.py` | A poller built after the probe armed still starts polling. | `devices/gamepad.py` `models/probe.py` | PORT-ADAPTED | 2 |  |
| `tests/hardware/test_hal_round1.py` | SMC100 exceptions, state codes, `wait_states` behaviour. | `devices/smc100.py` | PORT-ADAPTED | 11 | The five exception classes merge into one `SMC100Error`. |
| `tests/hardware/test_hal_round2.py` | SMC100 moves, homing and invalid responses. | `devices/smc100.py` | PORT-ADAPTED | 4 | Wire-level expectations are now pinned by `tests/station/golden/smc100.json`. |
| `tests/hardware/test_port_scanning.py` | The `DEV:` identity pattern and the scan loop. | `setup.py` `devices/serial_port.py` | PORT-ADAPTED | 5 | `_device_from` -> `SerialPort._identity_from`; the scan is `Setup.scan_ports`. |
| `tests/hardware/test_redpercent_dead_fields.py` | Dead attributes and duplicate thread handles removed from the monitor. | `models/red_monitor.py` | PORT-ADAPTED | 5 | Mostly designed out by the rewrite; keep one lint that the dead names do not come back. |
| `tests/hardware/test_serial_flush.py` | A bounded `flush()` so a shutdown frame is not discarded by the close that follows it. | `devices/serial_port.py` | PORT | 12 | SAFETY. `flush(timeout)` is on the pinned `SerialPort` interface. |
| `tests/hardware/test_serial.py` | Frame packing for autonomous and manual commands. | `devices/serial_port.py` `models/probe.py` | PORT-ADAPTED | 5 | Frame building moves to `Probe._send_move` / `_send_jog`. Superseded in part by the golden capture. |
| `tests/hardware/test_serial10_power_down_truth.py` | The model does not claim a de-energize the firmware cannot perform. | `models/probe.py` | PORT-ADAPTED | 9 | SAFETY. `supports_coil_kill` -> `can_kill_coils`; the report folds into `_halt_hardware`. |
| `tests/hardware/test_serial12_print_and_flush.py` | No 50 Hz print and no unbounded flush in the jog write path. | `devices/serial_port.py` `events.py` | PORT-ADAPTED | 6 | Re-point at the `no print() in station/` rule and `events.debug(every=)`. |
| `tests/hardware/test_serial16_error_routing.py` | Per-site audit of which transport conditions report versus print. | `devices/serial_port.py` `events.py` | PORT-ADAPTED | 3 |  |
| `tests/hardware/test_serial17_handshake.py` | The identity handshake pings once per interval, parses a whole line, and drains after the match. | `devices/serial_port.py` | PORT | 9 |  |
| `tests/hardware/test_serial6_async_connect.py` | The constructor does not block for the handshake, and a connect in flight cannot delay or swallow a stop. | `devices/serial_port.py` | PORT-ADAPTED | 7 | SAFETY. `wait_connected` -> `wait_open`; `open()` is explicit rather than done in `__init__`. |
| `tests/hardware/test_temp17_write_ordering.py` | A priority byte never interleaves with a frame on the wire; a frame superseded by a stop is dropped at the wire via `abort_if`. | `devices/serial_port.py` | PORT | 6 | SAFETY. This is the `abort_if` contract, which the brief pins on `SerialPort.write`. |
| `tests/pyside/test_pyside21_error_popup_queued.py` | A `requires_ack` error must not open its modal inside the publisher's call stack. | `views/qt.py` `views/base.py` | PORT-ADAPTED | 2 | SAFETY-adjacent: it is what stops a modal opening mid-teardown. Popup policy is `Dashboard._on_event` now. |
| `tests/pyside/test_redpercent17_pyside_plot_dialog.py` | PlotDialog dimension defaults and cancel handling. | `models/red_monitor.py` | PORT-ADAPTED | 3 | `PlotDialog` is purged; `select_plot_type` -> `RedMonitor.set_plot_dims`, so the test drops its Qt dependency. |
| `tests/scripting/test_edge_mvc_parser.py` | Probe construction with an odd controller id (nothing to do with scripts, despite the directory). | `models/probe.py` | PORT-ADAPTED | 1 |  |
| `tests/scripting/test_edge_mvc_scripting.py` | `run_script` threading, generations and cancellation. | - | VOID | 7 | Scripts / G-code purged by owner ruling 2026-09-21. |
| `tests/scripting/test_stepper9_script_validation.py` | STEPPER-9: only G0/G1 are moves, and numerics are validated before dispatch. | - | VOID | 10 | Same ruling. Removes finding 8 with it. |
| `tests/ui/test_composites.py` | The four schema composites actually render in a view, not just resolve on the model. | `views/base.py` | PORT-ADAPTED | 12 | Becomes `PanelView._make_<type>`; the abstract-per-type rule means a missing renderer fails at construction. |
| `tests/ui/test_edge_mvc_ui.py` | PySide widget edge cases: bad parents, empty schemas, hide paths. | `views/qt.py` | PORT-ADAPTED | 20 | Mark `qt`. The hide-path tests are VOID. |
| `tests/ui/test_errors9_pyside_csv_surfaces.py` | PySide CSV surfaces report through the bus, not a direct modal. | `views/qt.py` `events.py` | PORT-ADAPTED | 2 |  |
| `tests/ui/test_manager20_setup_window_close.py` | Closing the Qt setup window mid-scan against a real QThread. | - | VOID | 2 | Per-view setup wizards are designed out. |
| `tests/ui/test_pyside_12.py` | SelectionOverlay instruction label, cursor and focus. | `views/qt.py` | PORT-ADAPTED | 5 | -> `RegionOverlay`. Mark `qt`. carry.json lists the region picker as BENCH. |
| `tests/ui/test_pyside16_layout_intent.py` | Dashboard widget order and dock bookkeeping. | `views/qt.py` | PORT-ADAPTED | 5 | Mark `qt`. |
| `tests/ui/test_pyside17_dead_code.py` | Dead code inventory in `views/pyside/view.py` and the blank row an unknown element type produced. | - | VOID | 4 | Greps a file that will not exist, and the unknown-element case is designed out by `PanelView._make_element`'s per-type dispatch. |
| `tests/ui/test_pyside18_plotdialog_savefile.py` | PlotDialog and save-file details (default suffix, cancel). | `views/base.py` `models/red_monitor.py` | PORT-ADAPTED | 5 | The dialog is purged; what survives is the `file_save` element's contract. |
| `tests/ui/test_pyside4_discard_unsaved.py` | Closing the Red Percent dock prompts before discarding unsaved data. | `models/red_monitor.py` `controller.py` | PORT-ADAPTED | 4 | The view-injected `confirm_discard` hook is purged; `start_run` / `Controller.remove` raise `NeedsConfirm` instead. |
| `tests/ui/test_pyside4_hide_does_not_destroy.py` | D-1: a dock close hides rather than destroys. | - | VOID | 3 | Destruct-on-close is the ruling now. |
| `tests/ui/test_redpercent18_monitor_selection.py` | Region and monitor selection, including HiDPI logical-to-physical conversion. | `devices/screen.py` `views/qt.py` | PORT-ADAPTED | 4 | carry.json: BENCH. The conversion needs checking on the station PC. |
| `tests/ui/test_redpercent6_save_log_metadata.py` | Save Log goes through the model so late metadata edits reach the CSV. | `models/red_monitor.py` | PORT-ADAPTED | 2 | Three save entry points become one `save`. |
| `tests/ui/test_rotator_13_controls_disabled.py` | Both desktop views disable rotator controls when no stage is connected. | `views/base.py` | PORT-ADAPTED | 6 | One `PanelView._sync_gates` for both views. |
| `tests/ui/test_rotator_9_position_formatting.py` | A missing reading renders as `--.--`, not `None`. | `param.py` `models/rotator.py` | PORT-ADAPTED | 3 |  |
| `tests/ui/test_rotator_9_pyside_formatting.py` | PySide formats rotator position the same way Tk does. | `views/base.py` | PORT-ADAPTED | 2 | Designed out: `PanelView._text_for` is shared, so the two cannot diverge. Keep one test, not two. |
| `tests/ui/test_schema_v2.py` | Schema conformance across every model: every element resolves on the model it describes. | `schema.py` `panel.py` | PORT | 16 | High value and cheap to re-point; merge with tests/core/test_redpercent_schema.py. |
| `tests/ui/test_temp_10_pyside_staleness.py` | The readonly renderer applies the schema's declared role. | `views/theme.py` `views/base.py` | PORT-ADAPTED | 3 | Roles are a single table in `theme`. |
| `tests/ui/test_temp_10_staleness_indicator.py` | `connection_state` is in the schema and rendered visually distinct. | `models/heater.py` `views/theme.py` | PORT-ADAPTED | 4 |  |
| `tests/ui/test_ui_schema.py` | Every model's `ui_schema` binds to real attributes. | `panel.py` | PORT-ADAPTED | 2 | Subsumed by test_schema_v2's successor. |
| `tests/ui/test_view_tkinter_14_integration.py` | Start refuses without a focus area; sync checkboxes declare `disabled_when`. | `models/red_monitor.py` `schema.py` | PORT-ADAPTED | 4 | The three sync toggles become one `sync_axes` Param and one `set_sync`. |
| `tests/ui/test_view_tkinter_14_simple.py` | The same, asserted by reading `src/views/tkinter/view.py`. | `views/base.py` | PORT-ADAPTED | 4 | Source-level greps must be re-pointed at `station/views/`; prefer behaviour over grep where possible. |
| `tests/ui/test_view_tkinter_17.py` | The Tk view no longer hard-codes model internals. | `views/tk.py` `views/base.py` | PORT-ADAPTED | 3 | Largely designed out: the renderer is schema-driven and `_mode_name` is gone. |
| `tests/ui/test_view_tkinter_18_button_mapping.py` | Where the tab-close binding lives (deliberately does not pin the mapping). | `views/tk.py` | PORT-ADAPTED | 1 | -> `ClosableNotebook`. Still bench-blocked on macOS. |
| `tests/ui/test_view_tkinter_18.py` | Right/middle-click mapping, the separate log window, stdout spam. | `views/tk.py` | PORT-ADAPTED | 4 | The separate gamepad-log window is purged (`log_stream` shows it inline); the stdout rule becomes `no print() in station/`. |
| `tests/ui/test_w5_both_views_hide_does_not_destroy.py` | D-1 hide/show pinned at source level for both desktop views. | - | VOID | 7 | Destruct-on-close. |
| `tests/views/test_redpercent17_tk_plot_not_0d_only.py` | Tk's Red Percent plot is the generic schema plot element, not a hand-built 0D window. | `views/tk.py` `models/plot_data.py` | PORT-ADAPTED | 3 |  |
| `tests/views/test_redpercent19_tk_full_stop_gate.py` | The Tk monitor button's state follows FULL STOP. | `views/base.py` `models/red_monitor.py` | PORT-ADAPTED | 1 | SAFETY-adjacent: control state must follow the latch (carry.json VIEW cluster). |
| `tests/web/test_dc_11_readonly_attrs.py` | The write allowlist admits entry elements only; readonly/toggle/dropdown are refused. | `panel.py` | PORT-ADAPTED | 4 | The allow-list moves into `Panel`, so it guards all three views instead of only the Web. |
| `tests/web/test_dc5_web_manual_polling.py` | Entering manual mode from the Web starts the model's own input pump. | `models/probe.py` | PORT-ADAPTED | 4 | Designed out by model-owned loops; keep one parity test. |
| `tests/web/test_dc6_web_refusal_is_403.py` | A mode refusal reaches the browser as a refusal, not a 500. | `views/web/server.py` `result.py` | PORT-ADAPTED | 2 | `Result.is_refused` -> 403. |
| `tests/web/test_errors_3_console_echo.py` | Errors echo to the console and a first connect does not flood toasts. | `events.py` `views/web/server.py` | PORT-ADAPTED | 2 | Source grep of `web_view.py`; re-point at `events.since` semantics instead. |
| `tests/web/test_redpercent13_web_plotter.py` | The client samples the plotter only while a run is active, and reset reaches the model. | `views/web/server.py` | PORT-ADAPTED | 1 | Browser half; lives with the static app.js. |
| `tests/web/test_redpercent17_api_plot_dims.py` | `/api/plot` honours an explicit dim pick and falls back to file order. | `views/web/server.py` `models/red_monitor.py` | PORT-ADAPTED | 4 | The `SESSION_TOKEN` request pattern is VOID (token purged); the route behaviour ports. |
| `tests/web/test_redpercent17_plot_dims.py` | The browser plot dialog populates dim pickers and surfaces the server's real error. | `views/web/server.py` | PORT-ADAPTED | 1 | Node vm harness over app.js; carries over if app.js does. |
| `tests/web/test_redpercent19_web_format_schema.py` | The client applies the schema's `format` key to readonly numerics. | `views/web/server.py` `schema.py` | PORT-ADAPTED | 1 | Browser half. |
| `tests/web/test_rotator_13_web_connection_gate.py` | The client disables a disconnected device's controls. | `views/web/server.py` | PORT-ADAPTED | 1 | Browser half. |
| `tests/web/test_web_11_dead_commands.py` | The client offers no UI for commands no model has. | `views/web/server.py` | PORT-ADAPTED | 2 | Designed out once the client renders only from the schema; keep it as a guard. |
| `tests/web/test_web_13_stop_monitoring_offers_save.py` | Stopping a run offers to save unsaved data, in the browser too. | `models/red_monitor.py` `views/web/server.py` | PORT-ADAPTED | 3 | Becomes `NeedsConfirm` rendered by every view. |
| `tests/web/test_web_19_client_heartbeat.py` | The browser half of the liveness watchdog: it beats, and a stale beat stops the station. | `views/web/server.py` | PORT-ADAPTED | 7 | This is the half that SURVIVES: `WebView.beat` / `_check_heartbeat` -> `Controller.estop_all`. The model half is VOID. |
| `tests/web/test_web_20_active_models.py` | Command dispatch reads a model snapshot, not a live dict that can be swapped underneath it. | `controller.py` | PORT-ADAPTED | 3 |  |
| `tests/web/test_web_20_status_devices_generation.py` | The status and devices routes use the same snapshot, and a generation counter detects a re-setup. | `controller.py` `views/web/server.py` | PORT-ADAPTED | 5 |  |
| `tests/web/test_web_22_staleness_and_dropdown_refresh.py` | Per-device staleness marking and dropdown refresh on focus. | `views/web/server.py` `model.py` | PORT-ADAPTED | 1 | `Model.state` now carries a last-updated age, which is the server-side half of this. |
| `tests/web/test_web_7_plotter.py` | The plotter series uses `current_red` only. | `views/web/server.py` | PORT-ADAPTED | 1 | Browser half. |
| `tests/web/test_web_adapter.py` | `WebModelAdapter` and `WebDashboardWindow`: state, commands, logs, options. | `controller.py` `views/web/server.py` | PORT-ADAPTED | 18 | The adapter is purged - the Web view holds the Controller like the other two. Most of this becomes Controller tests. |
| `tests/web/test_web_security.py` | The dashboard's security boundary: cross-site POST, Origin/Host checks, screenshot exposure, session token. | `views/web/server.py` | PORT-ADAPTED | 15 | SAFETY-adjacent. Localhost bind + Origin/Host + JSON content type PORT; the session-token tests are VOID (token purged, owner ruling 2026-09-21). |
| `tests/web/test_web_server.py` | The HTTP server: routing, JSON shapes, static serving, error codes, port binding. | `views/web/server.py` | PORT-ADAPTED | 34 | Largest web file; routes are stable, the handler class is renamed `ApiHandler`. |
| `tests/web/test_web_setup.py` | The web setup wizard's scan / validate / build endpoints. | `setup.py` `views/web/server.py` | PORT-ADAPTED | 12 | The three-wizard structure is VOID; the behaviour (scan, validate, build) becomes `Setup` and ports. |
| `tests/web/test_web_ui_js.py` | The shared fetch wrapper's timeout. | `views/web/server.py` | PORT-ADAPTED | 1 | Skips cleanly without Node. |
| `tests/web/test_web19_seam.py` | The two halves of the liveness gate actually meet (the name mismatch that made D-8 unreachable). | `views/web/server.py` `controller.py` | PORT-ADAPTED | 3 | The seam moves, it does not disappear: `WebView._check_heartbeat` must really reach `Controller.estop_all`. Keep this test; it is the one that caught a silent duck-typed miss. |
| `tests/web/test_web24_schema_gate.py` | The client gates controls from each element's own `enabled_when`/`disabled_when`, never from label text. | `views/web/server.py` `schema.py` | PORT-ADAPTED | 1 |  |

## Safety-guarding tests that MUST have a ported equivalent before cutover

Stop paths, the FULL STOP latch, the tubing confirmation, and the interlocks.
No cutover until every row here has a named, passing test against `station/`.
These are the tests whose absence is not a coverage gap but a live hazard:
each one exists because the defect it names actually happened on this bench.

| Old test file | The safety property | Must land on |
| --- | --- | --- |
| `tests/core/test_transport_truth.py` | A failed write is never recorded as success; the FULL STOP latch holds; ROTATOR-4's tubing guard cannot be walked past. | `devices/serial_port.py` `model.py` `models/rotator.py` |
| `tests/core/test_probe_mode.py` | I-3.1 confirmed enable, I-3.2 MANUAL needs a bound gamepad, I-3.3 the idle interlock fires in every energized mode, I-3.4 no API write of a mode. | `models/probe.py` |
| `tests/core/test_lifecycle_teardown.py` | Teardown order (stop -> threads -> transport) with every step isolated. | `model.py` |
| `tests/core/test_lifecycle_exit.py` | Every process exit path stops the hardware. | `controller.py` |
| `tests/core/test_manager21_stop_confirmation.py` | FULL STOP may not report a stop it could not confirm. | `controller.py` `model.py` |
| `tests/core/test_manager22_clear_estop_is_reachable.py` | A latched device is clearable by an operator, and only by an operator. | `model.py` `panel.py` |
| `tests/core/test_manager24_hide_brings_hardware_safe.py` | Removing a device de-energizes it first (retarget onto Controller.remove). | `controller.py` |
| `tests/core/test_manager25_shutdown_stops_all_first.py` | Every model is stopped before any model is torn down. | `controller.py` |
| `tests/core/test_model_owned_loops.py` | A zeroed jog frame is sent on leaving MANUAL; no loop can block a stop. | `models/probe.py` |
| `tests/core/test_gamepad_interlock.py` | Losing the gamepad leaves MANUAL through the one transition that de-energizes. | `models/probe.py` |
| `tests/core/test_system_manager.py` | estop_all reaches every registered model. | `controller.py` |
| `tests/core/test_edge_mvc_model.py` | The +/-30 deg tubing confirmation on absolute and relative moves. | `models/rotator.py` |
| `tests/core/test_errors7_rotator_share.py` | A move refused by the latch is not reported as success. | `models/rotator.py` `result.py` |
| `tests/core/test_errors7_temperature_reporting.py` | A heater-off frame that did not land is reported, not swallowed. | `models/heater.py` |
| `tests/core/test_temp2_shutdown_seam.py` | A reader in backoff cannot outlive close(), which is the off-frame's drain window. | `models/heater.py` |
| `tests/core/test_rotator6_model_owned_sampler.py` | The rotator's sampler cannot delay FULL STOP. | `models/rotator.py` |
| `tests/core/test_rotator6_sampler_write_timeout.py` | The priority stop's own write is bounded, not just its lock acquisition. | `devices/smc100.py` `devices/serial_port.py` |
| `tests/core/test_tkinter_full_stop.py` | The global FULL STOP control is actually bound to estop_all. | `views/tk.py` `views/base.py` |
| `tests/core/test_tkinter_teardown.py` | Closing the dashboard goes through the ordered shutdown. | `views/tk.py` `controller.py` |
| `tests/edge_cases/test_edge_mvc_state_transitions.py` | Rapid mode toggling leaves the hardware in a defined state. | `models/probe.py` |
| `tests/hardware/test_temp17_write_ordering.py` | abort_if is evaluated inside the lock, so a frame superseded by a stop is dropped at the wire. | `devices/serial_port.py` |
| `tests/hardware/test_serial_flush.py` | A shutdown frame is drained before the port is released. | `devices/serial_port.py` |
| `tests/hardware/test_serial6_async_connect.py` | A connect in flight cannot delay or swallow a stop. | `devices/serial_port.py` |
| `tests/hardware/test_serial10_power_down_truth.py` | The model does not claim a de-energize this firmware cannot perform. | `models/probe.py` |
| `tests/pyside/test_pyside21_error_popup_queued.py` | An acknowledged popup never opens inside the publisher's stack, i.e. never mid-teardown. | `views/qt.py` `views/base.py` |
| `tests/views/test_redpercent19_tk_full_stop_gate.py` | Control state follows the latch after a FULL STOP. | `views/base.py` |

### Two gaps this walk found

* **`Rotator.home()`'s target commit has no test anywhere.**
  `grep -rn '\.home(' tests/` returns exactly one hit, in
  `tests/hardware/test_hal_round2.py`, and that one drives `SMC100.home`, not
  the model. carry.json's ROTATOR-4 row requires that `home()` commit its
  target *only after the guard passes* (review finding 2) - so this has to be
  **written**, not ported. It belongs beside the ported ROTATOR-4 block from
  `tests/core/test_transport_truth.py`.
* **The tubing guard is not in a file of its own.** It lives in two places -
  `test_transport_truth.py`'s ROTATOR-4 block and `test_edge_mvc_model.py`'s
  rotator block - and is easy to lose in a split. Port both into one rotator
  safety file so the next person can find it.

### Wire-format coverage is not in this table

`tests/station/test_wire_golden.py` and `tests/station/golden/*.json` pin the
exact bytes every scenario puts on the wire, captured from `src/`. Several
rows above - `tests/hardware/test_serial.py`,
`tests/hardware/test_hal_round2.py`, parts of
`tests/core/test_temperature_subsystem.py` - assert on frame contents by hand.
Port their *behavioural* half and let the golden files own the bytes; do not
re-derive a frame layout in a second place.
