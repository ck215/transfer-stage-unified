# fix-link — rb-link (base fb5a045)

Worktree `rb-link`, branch `rb-link`, 13 commits, never pushed. Files touched,
all in the write set: src/devices/serial_port.py, src/events.py,
src/model/base.py, src/model/probe.py, src/model/heater.py (L12, added by
the coordinator), tests/test_serial_port.py, tests/test_probe.py,
tests/test_core_events.py, tests/test_model_contract.py,
tests/test_link_recovery.py (new). Every PROVED run used the fb5a045
versions of the five src files with the new tests (scratch script, restore
chained; `git diff --stat` checked after each).

Commit order is L1, L2, L3, L8, L9, L4, L5, L6, L7, L10, L11, L12, L13
(the coordinator moved L8/L9 ahead as stop-path).

## L1
STATUS: closed
TEST: test_a_lost_link_is_stopped_on_the_still_open_handle_before_it_closes, test_a_lost_link_leaves_the_mode_disabled_not_fault, test_a_failed_stop_attempt_on_a_lost_link_is_logged_never_raised, test_nothing_but_a_stop_reaches_a_lost_links_handle, test_a_jog_write_that_finds_the_link_lost_does_not_fault_the_probe
COMMIT: 242dcbc
PROVED: all 5 red at base. Recorded wire after the failure was `[]`: no zero frame and no 'd' was attempted; the probe stayed AUTO; a failed jog write raised into the pump, which faults.
NOTE: Model.open registers `set_link_handlers(on_lost, on_restored, owner=NAME)` on every owned SerialPort (isinstance, so test doubles are unaffected). On the first failure the port goes LOST and keeps the handle; a worker runs the owner's `_on_link_lost` (halt on the priority lane, then `_leave_mode_for_link_loss`), bounded by `loss_stop_budget` = 3 x (PRIORITY_LOCK_TIMEOUT + WRITE_IO_LOCK_TIMEOUT + write_timeout), then closes the handle. Only priority writes reach a LOST/RECONNECTING handle. The probe goes DISABLED, not FAULT (a stop that could not land on the lost link does not enter FAULT; a fault that existed before the loss is kept). Probe `_send_jog` swallows a TransportError while the link is down, so the manual pump no longer faults the probe (probe.py hook; gamepad_input.py untouched). A port with no owner (Setup's scan, the SMC100's port) closes at once as before.

## L2
STATUS: closed
TEST: test_the_link_reconnects_by_itself_on_the_backoff_schedule, test_the_backoff_goes_on_every_ten_seconds_after_eight, test_close_while_reconnecting_stops_the_loop, test_a_reconnecting_probe_refuses_motion_and_says_why, test_the_reconnect_loop_never_holds_the_transaction_lock_or_blocks_a_stop, test_a_port_with_no_owner_stays_lost_and_does_not_reconnect, test_reconnecting_is_not_a_usable_state
COMMIT: f49446e
PROVED: the 5 reconnect tests red at base (no RECONNECTING, no loop, no restored event).
NOTE: `ConnectionState.RECONNECTING = "reconnecting"`; backoff (1, 2, 4, 8) then every 10 s, indefinitely; `close()`/`open()` cancel it through the generation counter and wake the wait. Entering RECONNECTING bumps the generation so a stale connect worker cannot match. `_is_current` accepts CONNECTING or RECONNECTING; `_handshake` returns None (not False) on a transport failure; `read_line` returns None while RECONNECTING. The model publishes "Connection Restored" and stays DISABLED. `Model._guard` refuses while a port is lost/reconnecting ("... lost its connection to <port> and is reconnecting by itself. Wait until it is back, then try again."), which covers Step, mode entry and the heater's Enter Settings. Deliberately NOT done: a port that fails its FIRST open stays LOST (no loop), as before; say if the owner wants boot-time retry too. The Rotator (SMC100 port, no owner) keeps D-11: smc100.py/rotator.py are not in the write set. tests/test_model_contract.py: "reconnecting" added to the known device statuses.

## L3
STATUS: closed
TEST: test_the_port_counts_its_failures_and_its_losses, test_a_failed_read_is_counted, test_malformed_and_non_pos_lines_are_counted_as_dropped, test_dropped_packets_warn_when_the_count_rises_and_not_more_often, test_the_heartbeat_is_not_touched_by_a_read_that_raised, test_a_stalled_position_stream_warns_once_and_counts_on_recovery, test_a_simulated_port_never_stalls, test_the_link_counters_reach_the_state_after_a_loss_and_a_recovery, test_a_model_with_a_serial_port_publishes_its_link
COMMIT: 9670ed0
PROVED: 12 red at base, including the heartbeat lie (age stayed ~0.01 s while every read raised).
NOTE: SerialPort: `losses`, `reconnects`, `write_failures`, `read_failures`, `last_loss` (HH:MM:SS, bound at import so the handshake tests' virtual clock does not break it), `link_counters`. Probe: `dropped` (a debug line per line; "DEV:" lines are not drops), "Packets Dropped" warning at most every DROPPED_WARN_INTERVAL = 10 s when the count rose; stall = status verified/unverified and no POS line for STREAM_STALL_SECONDS = 1.0 -> one "Position Stream Stalled" warning per episode, `stalled` true; the next POS line logs + info "Position Stream Resumed" and increments `stalls`. `state["link"]` = exactly {status, losses, reconnects, dropped, stalls, stalled, last_loss}, present for every model owning a SerialPort (the probes and the heater, SIM included, status "simulated"), absent otherwise (Rotator, Red Percent, Transfer Map).

## L4
STATUS: closed
TEST: test_a_lost_link_asks_for_attention_in_the_owners_words, test_an_unconfirmed_stop_on_a_lost_link_says_treat_it_as_live, test_the_restored_and_lost_titles_are_named_once, test_the_attention_set_is_exactly_the_titles_the_lead_named, test_the_loss_is_reported_once_not_on_every_later_command
COMMIT: 9c0bd4d
PROVED: 5 red at base.
NOTE: LINK_LOST = "Connection Lost" (ATTENTION, ack), LINK_RESTORED = "Connection Restored" (info), re-exported on `events`. Text: "Stepper Probe lost its serial port /dev/…: <why>. It was stopped and disabled; it will reconnect by itself." When the stop did not land: "... The stop could not be confirmed, so treat it as live until you have checked it; it will reconnect by itself." Source is the owner's NAME. A port with no owning model keeps a tray line, retitled "Port Lost" (every ATTENTION title must be raised with ack=True at every site, and a scan-port loss must not pop a modal); test_serial_port's count assertion updated to that title. BOARD_RESET_SUSPECTED was added in L6 with its only site, since the ATTENTION test pins set == sites. The ATTENTION pin in test_core_events was updated with the owner-ruling comment, not loosened.

## L5
STATUS: closed
TEST: test_a_confirmed_stop_clears_the_fault
COMMIT: 98f2e4b
PROVED: red at base: after fault, toggle_estop(), clear_estop(True), `is_faulted` stayed True ("The disable did not reach the board...").
NOTE: `_halt_hardware` clears the fault when 'd' landed. With L8 this is the only in-app way out of FAULT.

## L6
STATUS: closed
TEST: test_a_snap_to_zero_while_enabled_warns_of_a_board_reset, test_what_is_not_a_board_reset (disabled, small, slow, not_zero)
COMMIT: 7cffcae
PROVED: the warning test and small/slow red at base (no heuristic, no constants).
NOTE: THRESHOLD FOR THE OWNER TO JUDGE: RESET_JUMP_COUNTS = 1280, RESET_WINDOW = 0.2 s. Reasoning: stepper/chuck firmware setMaxSpeed(1600 * microstepMode / 2) with microstepMode = 8 is 6400 steps/s; PRINT_INTERVAL = 100 ms, so one sample moves at most 640 counts; x2 for a late or merged sample = 1280; 6400 x 0.2 s = 1280, so no real move inside the window covers it. Fires only on EXACTLY (0,0,0), mode not DISABLED, previous position farther than 1280 on some axis. The DC board prints every 50 ms and its encoder rate is not stated in its sketch: the stepper number is used there too. The mode is never changed; BOARD_RESET_SUSPECTED is an acknowledged warning.

## L7
STATUS: closed
TEST: test_every_file_line_starts_with_the_date_and_the_time, test_every_mode_line_carries_the_position, test_the_sampler_logs_a_health_line_on_its_interval
COMMIT: cb7bf59
PROVED: all 3 red at base.
NOTE: log-file stamp is now `YYYY-MM-DD HH:MM:SS.mmm` (nothing in src parses the file; test_core_events had no prior pin, the new one is exact). Health every HEALTH_INTERVAL = 5 s from the sampler: mode, link, position, position_age, idle_remaining, gate_open, pad_bound, sampler_alive, pump_alive, latched, fault (pad/pump via the mixin's `_is_gate_open`, `_is_gamepad_bound`, `_thread("gamepad")`). Every probe `Mode` line ends "at (x, y, z)".

## L8
STATUS: closed
TEST: test_rearming_out_of_fault_needs_a_confirmed_disable_first, test_step_is_refused_on_a_faulted_probe_and_sends_nothing, test_no_mode_is_entered_out_of_fault (idle, autonomous, manual), test_step_is_greyed_on_a_faulted_probe
COMMIT: 63463c7
PROVED: 6 red at base: run("step") on a FAULTed probe was ok, wrote b"e" and a move frame, and cleared the fault.
NOTE: Step's disabled_when gains "fault"; `_set_mode` refuses every target but DISABLED while FAULT or faulted; the arming path no longer calls `_clear_fault()`. The old test `test_rearming_out_of_fault_resends_the_hardware_enable` pinned the defect and was rewritten to the new rule (a confirmed disable first, then the enable resends 'e').

## L9
STATUS: closed
TEST: test_a_stop_during_a_mode_entry_leaves_the_probe_disabled_not_manual, test_an_unlatched_halt_during_a_mode_entry_is_not_overwritten
COMMIT: 10606bf
PROVED: both red at base with the auditor's 20 ms widened window (drain_edges): mode ended MANUAL while latched.
NOTE: `_halt_hardware` bumps `_halt_generation`; `_set_mode` re-checks it and the latch under `_mode_lock` after the enable (and the manual edge drain) and backs out through `_deenergize` (zero frame, 'd'), then refuses. Covers a plain halt as well as a latched stop. `clear_estop` was not changed (the back-out makes the armed-while-latched state unreachable through this window).

## L10
STATUS: partly
TEST: test_a_fault_is_published_after_the_mode_lock_is_released, test_a_failed_enable_is_published_after_the_mode_lock_is_released, test_the_no_coil_kill_notice_is_published_with_no_lock_held
COMMIT: 35ad578
PROVED: the first two red at base (Fault and Enable Failed published with `_mode_lock` owned by the publishing thread); the coil-kill one passes at base (it was already published with no lock held) and now pins it.
NOTE: base `_fault` publishes through `_publish_later`; the probe queues events raised inside `_set_mode` (thread-local) and publishes after the lock is released. The other half of SF-4 (Tk `_marshal` calling Tk from worker threads) is src/views/tk.py: not in this write set.

## L11
STATUS: partly
TEST: test_a_stop_that_lands_after_the_budget_is_reported_and_revised, test_a_stop_that_fails_late_is_not_revised, test_the_unconfirmed_stop_names_the_real_budget
COMMIT: 4465481
PROVED: the first and third red at base (never revised, no "Estop Late"/"Stop Landed Late"; the error named no budget).
NOTE: ESTOP_BUDGET not widened. Per-model "Stop Not Confirmed" now reads "did not acknowledge the stop within 80 ms. Treat it as live; a stop that lands later is reported." The Estop debug line carries the budget; a late landing logs "Estop Late" with its real latency, revises `stop_confirmed` to True for the same latch episode, and publishes info "Stop Landed Late" (suppressed during close, which test_core_controller pins to one event). NOT done, needs controller.py (lead): the global copy "did not confirm the stop within 1 s" (controller.py ~261, ~280). SF-6's "one priority write for zero+d+k" was not done (it is a wire-timing change on the stop path; golden bytes would be the same, but it is the lead's call).

## L12
STATUS: closed
TEST: test_a_heater_stop_the_board_never_reports_is_marked_unconfirmed, test_a_heater_stop_the_board_reports_at_zero_is_confirmed
COMMIT: b07b612
PROVED: the unconfirmed test red at base (no warning; the heater read "off" while the board still reported 250.5).
NOTE: after a stop of a HEATING controller on a real, non-SIM SerialPort, a worker waits OFF_CONFIRM_SECONDS = 1.5 s for a telemetry line with setpoint <= OFF_SETPOINT_MAX = 0.5; without one `_commanded_setpoint` is restored (is_active/is_energized stay true, so the Web watchdog keeps caring), `stop_confirmed` goes False for that latch episode, and HEATER_OFF_NOT_SENT (ack) says to switch it off at the controller. `_halt_hardware` still returns on bytes written (the 80 ms budget); the telemetry verdict revises it. Test doubles and SIM are not checked (no telemetry). The heater tests live in tests/test_link_recovery.py because tests/test_heater*.py are not in the write set. Firmware fix (reset ndx on '<') is the owner's.

## L13
STATUS: closed
TEST: test_a_hundred_mode_round_trips_leak_nothing
COMMIT: f701274
PROVED: test only; passes at base and on this tree: no finding.
NOTE: "ends at rest" = the final entry's zero frame, then at most the pump's one neutral packet for leaving manual (I-4.2); one interlock, one pump, one sampler thread alive.

## GATE
At HEAD f701274, STATION_NO_WINDOWS=1 QT_QPA_PLATFORM=offscreen, exit codes read unpiped:
- fast (`-m "not qt"`): EXIT=0, 3240 passed, 12 skipped, 255 deselected, 1 xfailed (base: 3184 passed / 12 skipped / 1 xfailed; +56 new tests, 0 regressions)
- golden (`tests/test_wire_golden.py`): EXIT=0, 78 passed (wire bytes unchanged)

## UNVERIFIED
- No Qt-marked test was written; the Qt pass was not run (profile rule).
- Nothing here was run against a real board. Bench items: the L6 threshold (above), the stall threshold on the DC board (50 ms print interval), the L12 1.5 s window against the board's ~1.7 lines/s, and a real unplug/replug for L1/L2 (the reconnect path on a macOS /dev/cu.* that re-enumerates under the same name).
- Views (agent C) are coded against `state["link"]`; the devices map now also reports "reconnecting", which the views' `lost_devices` filters ("lost" only) do not treat as lost.
