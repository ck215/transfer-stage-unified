# Handoff: the Transfer Map model (`rb-map`, worktree `../rb-map`)

Base `af7a325`. Six commits, never pushed: `dfe437f` `b18c361` `9498b70`
`2fc7f82` `fb44af7` `2205e83`. Write set respected; `src/controller/setup.py`
is unchanged on the branch (see MAP-5). Nothing under `docs/`, `.gitignore`
or the views was touched.

Owner ruling received mid-round and applied (local database): the SQLite
file defaults to `<repo root>/data/transfer_map.sqlite`, the repo root
derived from `__file__` (`Path(__file__).resolve().parents[2]`, the
directory holding `src/`). It is created with its schema on the **first
write** (Arm or Import), never at construction, open, state, figure or any
refused command. Override: **env var `STATION_MAP_DB=<path>`** (my call; a
`--map-db` flag would need `app.py`, outside my write set). Pictures in
`data/transfer_map/<trial_id>/{before,after}.png`, exports in
`data/exports/`, Web uploads in `data/uploads/`; the model's `output_root`
is the database's folder, so Web downloads and uploads are checked against
it. The resolved path is a readonly in Diagnostics (`db_path`). Every test
sets `STATION_MAP_DB` to a tmp dir (autouse fixture); the contract suite
(which builds the class without the env var) was checked to leave no
`data/` behind.

## MAP-1 analysis: detector, force registry, Gaussian process
STATUS: closed
TEST: tests/test_transfer_map_analysis.py (23): test_median5_removes_a_single_sample_spike_and_keeps_length, test_detector_finds_the_peak_and_the_dip_of_a_clean_lowering, test_detector_ignores_single_sample_spikes_in_noise, test_the_maximum_is_taken_before_the_operator_mark, test_the_minimum_is_after_the_maximum_even_when_the_hover_was_darker, test_a_break_does_not_move_the_peak_and_the_dip_stays_before_the_recovery, test_the_baseline_is_the_median_of_the_first_second, test_the_force_formulas_on_a_known_profile, test_a_deeper_shadow_scores_higher_on_every_definition, test_a_flat_profile_gives_no_index_rather_than_a_number, test_a_definition_that_raises_is_reported_as_none_not_raised, test_z_past_peak_uses_the_z_column_when_it_was_recorded, test_given_marks_override_the_detector, test_gp_recovers_a_known_smooth_function_and_its_uncertainty_shrinks_near_data, test_gp_gradient_matches_the_function_and_carries_a_variance, test_gp_with_one_point_or_none_answers_prior (+7)
COMMIT: dfe437f (GP per-point noise added in 2fc7f82)
PROVED: new module; tests written first, red on import.
NOTE: `src/model/transfer_map_analysis.py`. GP (phase 2) is ~60 lines: RBF, closed form, Cholesky, mean + latent variance, gradient + gradient variance from the same covariance; noise may be one value or one per point.

## MAP-2 Red Percent hooks, Rotator.position_deg
STATUS: closed
TEST: test_red_monitor.py: test_a_subscriber_receives_every_logged_row, test_a_subscriber_gets_its_own_copy_of_the_positions, test_a_failing_subscriber_never_ends_the_run, test_unsubscribe_stops_the_calls_and_is_idempotent, test_the_estop_path_is_unchanged_with_a_subscriber_attached, test_grab_frame_returns_the_capture_region_as_png, test_grab_frame_is_none_without_a_region_or_a_frame; test_transfer_map.py: test_the_rotator_reads_its_tilt_as_position_deg
COMMIT: b18c361 (Red Percent), 9498b70 (Rotator; b18c361's message names it but it was left unstaged, so it landed separately)
PROVED: the seven Red Percent tests ran red (AttributeError) before the change.
NOTE: additive only. `subscribe(fn)`/`unsubscribe(fn)` keep a tuple swapped whole (no lock on the run thread); the loop calls `fn(t_s, red, dict(positions))` right after `run.rows += 1`, only when subscribers exist; a raising subscriber is logged (debug, 1/s) and skipped. `grab_frame()` grabs the region through the Screen device on the caller's thread and returns PNG. All 98 pre-existing Red Percent tests unchanged and green.

## MAP-3 figures in plot_data
STATUS: closed
TEST: test_plot_data.py: test_the_map3d_request_splits_measured_from_pending, test_a_trial_without_the_chosen_index_is_left_off_the_map, test_no_trials_explains_itself_for_every_figure, test_the_slice_uses_measured_trials_in_the_force_band, test_the_slice_carries_a_gp_mean_and_sigma_surface_with_enough_trials, test_the_compare_request_has_one_panel_per_definition, test_the_profile_request_carries_the_marks, test_every_transfer_figure_renders_to_a_png, test_the_transfer_figures_never_draw_in_the_stop_red
COMMIT: 2fc7f82
PROVED: red before (no `transfer_request`).
NOTE: `transfer_request` (pure) + `render_transfer_figure` (Agg). map3d: tilt x speed x force index coloured by width with `_colormap()`, pending drawn hollow (MUTED edge). slice: GP mean of width over tilt x speed (axes scaled to [0,1], length 0.35, each AFM sigma² as that point's noise, 5 % of the spread when absent), sigma contours labelled, measured points overlaid; the force band is a tercile of the chosen index (scale-free), dropdown "All forces / Low / Middle / High third"; needs 2 measured trials in the band. compare: one panel per definition, index vs width with sigma error bars (vs trial number before any AFM width). profile: red vs time since Arm, baseline, operator Mark, auto peak/dip. Never SIGNAL. Renders checked by eye in scratch.

## MAP-4 the TransferMap model
STATUS: closed
TEST: tests/test_transfer_map.py (38), among them test_the_class_declares_a_portless_model, test_the_database_defaults_to_the_projects_data_directory, test_station_map_db_overrides_the_path, test_nothing_creates_the_database_until_a_trial_is_written, test_arm_refuses_without_red_percent, test_arm_refuses_while_red_percent_is_not_running, test_arm_refuses_without_a_tip_id, test_arm_refuses_while_latched, test_arm_snapshots_tilt_speed_and_the_before_frame, test_speed_follows_the_probes_mode, test_tilt_is_none_until_a_rotator_reads_or_the_operator_types_one, test_a_recorded_trial_keeps_its_raw_profile_marks_and_frames, test_the_profile_matches_what_red_percent_logged, test_estop_aborts_the_armed_trial_and_keeps_its_profile, test_the_stop_never_waits_on_a_held_lock, test_the_station_stop_confirms_every_model_with_a_trial_armed, test_abort_is_a_stop_that_ignores_bad_entry_text, test_close_with_an_armed_trial_saves_it_as_aborted, test_mark_broke_on_the_armed_then_the_last_trial, test_attach_afm_measures_a_trial, test_delete_asks_then_removes_the_trial_and_its_profile, test_export_writes_both_tables_inside_the_output_root, test_import_typed_trials_and_they_reach_the_map, test_an_export_imports_back, test_the_figure_is_cached_until_something_changes, test_tier_one_holds_the_trial_keys_and_tier_two_the_configuration
COMMIT: fb44af7, 2205e83
PROVED: new class; tests written first. 2205e83 fixes a defect the headless launch found: with no rotator reading, tilt fell back to Red Percent's `probe_tilt_angle`, whose default 0.0 recorded every such trial at 0 deg.
NOTE: the model contract passes for the class once registered: 27 cases of `tests/test_model_contract.py` for Transfer Map, the file 215 passed + 1 xfail, run with the MAP-5 patch applied and then reverted.

Safety (the brief's condition): the model moves nothing; `devices` is [], `_expects_heartbeat` False. `_halt_hardware` takes the armed trial under a 50 ms lock timeout (and takes it anyway if the lock is held), unsubscribes from Red Percent, starts a daemon writer for the "aborted" row, and returns True at once. No I/O on the stop path. `disable()` joins that writer (bounded 2 s) so `close()` saves an armed trial as aborted. Proven: estop confirms in under 0.5 s with a trial armed; a held model lock does not block the stop; `Controller.estop_all` with Red Percent + an armed Transfer Map returns both confirmed and ends the run; Abort is `stop=True` and runs past bad entry text. Arm is `_guard`ed and `disabled_when=("armed","latched")`.

Deviations from the brief, with reasons:
- Four extra trial columns: `origin` (recorded/imported), `tilt_source`, `speed_source`, `force_given` (JSON {definition: value} for imported trials, which have no profile).
- Tilt: Rotator `position_deg`, else a Transfer Map text entry "Tilt without a rotator (deg)" (tier 2, travels with Arm). Blank = no tilt (never a default 0).
- Speed: `live_speed` if a probe has one, else `man_full_speed` in manual / `full_speed` in autonomous, of the probe Red Percent follows (else the first probe in a mode). Snapshot at Arm, **replaced by the value at Mark force** (the speed during the lowering). Z from Red Percent's positions when Z is synced, else read from that probe.
- Arm refuses without a tip ID (traceability). Two file_saves (Export trials / Export profiles), since a `file_save` returns one path; both write both tables, and the trials CSV carries every force index as `force_<name>` columns. Import reads `tilt_deg`, `speed_steps_s`, optional `force_index` (+ `force_definition`, default "given"), `force_<name>` columns, AFM columns, `tip_id`, `note`, `broke`; it also re-imports its own export.
- `attach_afm`: width must be > 0; a sigma or thickness of 0 is stored as NULL (not given). An aborted trial keeps status aborted.
- Tier 1 also holds Abort, Tip broke, the Note entry and a live "Red % since Arm" plot (a native plot element, no matplotlib), so the operator sees the shadow while lowering.
- The profile figure shows the chosen (`trial_pick`, 0 = latest) recorded trial. A live matplotlib re-render every poll would have been too costly.
- Samples capped at 500 000 per trial, with the overflow counted and warned.

## MAP-5 registration in controller/setup.py
STATUS: partly
TEST: (the contract run above)
COMMIT: none; the branch leaves setup.py unchanged
PROVED: with the registration applied the fast suite went from 3 red to 9 failed + 12 errors. Every new failure is a test that pins six models, outside my write set.
NOTE: blocking files: `tests/test_setup_registry.py` (test_the_six_built_ins_are_registered_in_todays_display_order, test_register_appends_in_order_and_returns_the_class, test_a_registered_class_gets_a_row_identifies_and_builds: expected lists end at Red Percent / Piezo Stage index 6), `tests/test_view_web_server.py` (fixture `sim_station`: `assert len(setup.launch()) == 6` -> 12 errors), `tests/test_packaging.py::test_smoke_scripts_drive_every_setup_row[smoke.sh|smoke.ps1]` (needs `transfer_map` rows in `packaging/smoke.sh` and `smoke.ps1`), and `tests/test_setup.py::test_model_types_is_every_model_class_keyed_by_its_name` (mine, but only meaningful with the others). The patch:
```
+from model.transfer_map import TransferMap
-                  RedMonitor):
+                  RedMonitor, TransferMap):
```
(saved at scratchpad `rb-map/registration.patch`).

## How a session records a trial (for the owner)

1. Launch with Red Percent, the Transfer Map and (ideally) the Rotator and a probe. The Diagnostics line "Database" shows which file is in use: `data/transfer_map.sqlite` in this checkout unless `STATION_MAP_DB` says otherwise. It is created the first time you arm a trial.
2. In Red Percent, set the capture region and Start run. The Transfer Map records only while Red Percent is recording.
3. Type the **Tip ID** (it stays filled in for the next trial). Without a rotator, type the tilt under Configure Transfer Map, "Tilt without a rotator".
4. Press **Arm trial** while hovering. The station saves a *before* picture of the capture region, the tilt, and the probe's speed. From here every red-percent row is kept, and "Red % since Arm" draws it.
5. Lower the tip. When the force is where you want it, press **Mark force**. The time, Z and the speed at that moment are stored.
6. Press **Finish trial**, with a Note if you like. The station saves an *after* picture, finds the peak and the dip itself, and computes the baseline. If the tip broke, press **Tip broke** (it applies to the armed trial, or to the last one). **Abort trial**, or any stop, ends the trial as "aborted" and keeps its profile.
7. Later, after AFM: under Configure Transfer Map, type the trial number, the channel width and its uncertainty (and thickness if measured), then **Attach AFM**. The trial becomes "measured" and turns from hollow to coloured on the 3D map.
8. Figures (Configure Transfer Map): **3D map**, **Slice at a force band** (a smoothed width surface with uncertainty contours), **Compare force definitions**, **Trial profile**. Pick the force definition and band from the dropdowns. **Export trials / Export profiles** write CSVs; **Import trials** takes a CSV typed elsewhere.

## The force definitions

On the 5-sample running median of the trial's red trace. M = red at the detected peak (the maximum before the operator Mark, or before the end); m = red at the dip (the minimum after the peak); b = baseline (median raw red over the first 1 s after Arm); n(t) = (red(t) - m)/(M - m). All are oriented so that **larger = more force**. None when undefined (flat trace, no Mark, no Z).

| name | formula |
|---|---|
| `shadow_vs_baseline` | (b - m) / (M - m) |
| `shadow_vs_peak` | (M - m) / M |
| `at_operator_mark` | (M - red(Mark)) / (M - m) = 1 - n(Mark). The brief said "normalised red at the Mark"; inverted so it grows with force like the rest |
| `dip_area` | integral from t(M) to the end of max(0, n(b) - n(t)) dt, trapezoid, in normalised units x seconds |
| `fall_slope` | (n(M) - n(m)) / (t(m) - t(M)) = 1 / fall time, per second |
| `z_past_peak` | abs(z(Mark or end) - z(M)), steps lowered past the peak (added; needs Z) |

The registry is `transfer_map_analysis.FORCE_DEFINITIONS` (name -> function(context)). A new line there reaches every figure, dropdown and export. Imported trials add their own names (default "given").

## GATE
- Fast (`STATION_NO_WINDOWS=1 ... -m "not qt"`, committed tree 2205e83): **2572 passed, 3 failed, 11 skipped, 1 xfailed**, exit 1. The 3 are pre-existing and not mine: the brief's two Signature red-by-design tests (`test_view_qt.py::test_e_go_is_ink_filled_and_disabled_is_a_dashed_muted_edge`, `test_view_web_server.py::test_the_disc_reads_stop_and_clear_with_its_ring`), plus `test_view_tk.py::test_indicator_and_toggle_take_their_colours_from_the_theme` (theme colour `#f5f7f6` vs `#e7eae9`). That one is also red on this worktree with setup.py at base, and none of my files touch the views or the theme. It is outside the brief's list, so the lead should look.
- Golden wire: **78 passed**.
- Launch (headless Web, port 8791, `STATION_MAP_DB` and `TRANSFER_STAGE_DATA_ROOT` in scratch, a wrapper that calls `setup.register(TransferMap)` before `app.main`, the MAP-5 line): all 7 rows SIM and launched; `/api/state` lists the Transfer Map; `/api/schema` gives Trial, Figure, AFM measurement, Data, Diagnostics, Safety; Red Percent region set and run started; Arm -> trial 1, Mark force at 2.006 s, Finish -> recorded; tilt 12.5 (typed), before/after PNGs on disk; `/api/data ... figure` returned a 5942-byte PNG; `/api/file ... export_csv` downloaded the trials CSV; `/api/quit` and the server exited. Nothing on screen.

## UNVERIFIED
- The launch's profile had **0 samples**. Red Percent logs a row only when red changes, and the headless screen region never changed. The whole subscribe -> profile -> detector path is proven only with the fake capture in tests (real run thread, real RedMonitor), not on a live screen. The first real trial is the bench check. For the same reason the launch figure was the "no tilt, speed and index" message PNG.
- Qt/Tk rendering of the new schema (no Qt pass run by me; Tk only via the mocked suite). The Web launch served the schema, but no browser rendered it.
- Detector behaviour on real footage: a broken tip that lets the reflection come back brighter than the approach peak before the Mark would move the peak.
- Performance of the figure with hundreds of trials: every figure change recomputes the uncached force indices from each profile.

## Needs the lead
1. MAP-5: the two-line registration plus the test and smoke-script updates listed above.
2. `.gitignore`: `/data/` (owner ruling; not edited by me). Packaging: `default_db_path` derives from `__file__`, so inside a PyInstaller bundle it would point into the bundle. Set `STATION_MAP_DB` in the launchers or decide a bundle rule.
3. A `--map-db PATH` flag in `app.py` if wanted (it would set `STATION_MAP_DB`).
4. `views/base.GATE_WORDS` has no "armed": greyed Mark force reads "Not in armed mode" and Arm while armed reads "In armed mode". Suggest `"armed": ("A trial is armed", "Arm a trial first")`.
5. An armed trial counts as `is_energized` (it equals `is_active`, which the contract requires). So the Quit/close-tab warnings name the Transfer Map, and the Web watchdog stops everything if the browser goes silent while a trial is armed. It only adds stops and never blocks one; decide if that is wanted.
6. `Probe.live_speed` would be a cleaner speed source than reading `man_full_speed`/`full_speed` by mode (probe.py was not mine; the duck-type already prefers it).
7. Pre-existing red `test_view_tk.py::test_indicator_and_toggle_take_their_colours_from_the_theme` (see GATE).
