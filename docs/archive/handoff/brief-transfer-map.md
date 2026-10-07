# Brief: the Transfer Map model (branch `rb-map`, worktree `../rb-map`)

Base: `af7a325` (Signature tokens) or later on `mvc-refactor`; confirm with
`git log -1`. Python:
`/Users/ianalbinogonzalez/Documents/GitHub/transfer-stage-unified/main/.venv/bin/python`.
Read the agent profile `.claude/agents/worktree-fixer.md` first (its rules
bind you), then `docs/rebuild/STATUS.md`, `docs/rebuild/MODEL_CONTRACT.md`
(the recipe you follow: a new device is wired by writing its Model alone),
`docs/rebuild/DESIGN_BRIEF.md`, `src/model/red_monitor.py` and
`src/model/plot_data.py` in full, `src/devices/screen.py`, and
`tests/test_model_contract.py` (every rule it asserts applies to your class).

## What the owner wants (ruled 2026-09-27, in the owner's words where it matters)

The end goal of this project is a heatmap: a 3D map over **tilt angle,
speed and force**, whose value of interest is the **channel width** of the
transferred sample. It should build itself as trials are recorded, with the
program prompting for before/after footage and keeping a database of
results, so that later AFM measurements of channel width and sample
thickness can be attached and taken into account, and so that
uncertainties can be placed on the data to assert confidence in the
gradients across the map.

**Force** has no sensor. It is approximated from the red-percent trace
during a lowering: hovering above the sample gives a baseline; as the tip
approaches, the reflection brightens to a maximum; then a shadow overcasts
it; pushed further, the tip flexes until it breaks. The intensity of the
shadow builds a relative force scale. The owner's rulings on this:

- The shape of the curve per trial is **normalised by its own minimum and
  maximum**; the **delta** is the value of concern, not absolute readings.
- Keep **several definitions** of the force index and plot them for cross
  comparison; do not pick one in code.
- **Both** an automatic detector of the maxima and minima in the red
  percent plot **and** a Mark key the operator presses when the desired
  force is set during the lowering. Both marks are stored.

## Design (the lead's, follow it; deviations go in the handoff with reasons)

1. **`src/model/transfer_map.py`: `class TransferMap(Model)`**, `NAME =
   "Transfer Map"`, `IDENTITY = None`, `RESOURCES = ()`, no port, no
   gamepad, sim-agnostic (it works with no hardware at all: trials can be
   imported or typed). Register it in `controller/setup.py` after
   `RedMonitor` in the built-in loop. `devices` returns []; `_expects_heartbeat`
   is False. `is_active` is True while a trial is armed. `_halt_hardware`
   returns True (it moves nothing); estop disarms a trial.
2. **Store**: SQLite (stdlib) at `<output_root>/transfer_map.sqlite`
   (`output_root` as Red Percent defines it; reuse its rule), two tables:
   - `trials`: id, started_at, tip_id (text; per tip or session, operator
     typed, remembered between trials), tilt_deg, speed_steps_s, z_contact,
     mark_operator_t, mark_auto_max_t, mark_auto_min_t, broke (0/1), red_min,
     red_max, red_baseline, width_um NULL, width_sigma_um NULL,
     thickness_nm NULL, thickness_sigma_nm NULL, note, before_path,
     after_path, status ("armed", "recorded", "measured", "aborted").
   - `profile`: trial_id, t_s, red, z (and x, y when present), one row per
     red-percent sample from arming to completion. **The raw slice is the
     truth; every force index is computed from it at plot time.**
   Plus `export_csv` (a `file_save` command) writing both tables under
   `output_root`, and `import_csv` (`file_open`) for trials typed elsewhere
   (tilt, speed, force index given directly when no profile exists).
3. **Live sources**, duck-typed the way Red Percent reads position
   (`on_model_added` / `on_model_removed`, never a class name): tilt from a
   model exposing `position_deg` (the Rotator; add that read-only property
   if it only has a state value), speed from the probe in manual or
   autonomous mode exposing its live speed, red-percent samples from Red
   Percent. Red Percent needs a small **additive** hook so another model
   can receive its samples: a `subscribe(fn)` / `unsubscribe(fn)` pair
   called with `(t_s, red, positions)` from where it already appends to
   its run log, and a `grab_frame()` that returns the current capture
   region as PNG bytes through its Screen device. No behaviour change to
   Red Percent; its tests and the golden gate stay green.
4. **The guided trial** (commands, all schema-declared):
   - `arm_trial` (button, role go, `inputs=("tip_id",)`): refuses if Red
     Percent is not open or not sampling; snapshots tilt, speed and the
     before frame to `<output_root>/transfer_map/<trial_id>/before.png`;
     starts collecting the profile; state "armed".
   - `mark_force` (button, text "Mark force", enabled while armed): stores
     `mark_operator_t` = now and z. UNGATED is not needed; it is not a stop.
   - `finish_trial` (button, role go, enabled while armed): stores the
     after frame, runs the detector over the profile, sets
     `mark_auto_max_t`/`mark_auto_min_t`, red_min/max/baseline (baseline =
     median of the first second), status "recorded"; asks nothing else. A
     note entry beside it travels as an input.
   - `abort_trial` (button, `stop=True`): status "aborted", profile kept.
   - `mark_broke` (toggle on the current or last trial).
   - `attach_afm` (button, `inputs=("afm_trial_id", "width_um",
     "width_sigma_um", "thickness_nm", "thickness_sigma_nm")`): status
     "measured".
   - `export_csv`, `import_csv`, `delete_trial` (NeedsConfirm).
   - The detector: on the normalised profile ((red - min)/(max - min)),
     the maximum is the global max before the operator mark (or before the
     end), the minimum the deepest point after it; smooth with a 5-sample
     median first. Write it as a pure function in `plot_data` or a small
     `transfer_map_analysis.py` module under `model/`, with tests on
     synthetic profiles (rise, dip, noise, a break).
5. **Force definitions**, each a pure function of one profile plus the
   marks, all returned by one `force_indices(profile, marks)` dict, at
   least: `shadow_vs_baseline` ((baseline - min)/(max - min)),
   `shadow_vs_peak` ((max - min)/max), `at_operator_mark` (normalised red
   at the Mark), `dip_area` (area under the normalised curve below the
   baseline after the peak), `fall_slope`. The owner will add more later;
   keep the registry open (a dict of name -> function).
6. **Figures**, through `plot_data.render_figure` or a sibling in the same
   module, shown by `sch.image(..., "figure", ...)` like Red Percent's
   Analysis Plot, with a dropdown for the force definition and one for the
   figure type:
   - `map3d`: scatter of (tilt, speed, force index) coloured by width,
     `_colormap()`; trials without AFM data drawn hollow ("pending").
   - `slice`: heatmap of width over tilt x speed at a force-index band.
   - `compare`: a grid, one panel per force definition, same trials, so
     the definitions can be cross-compared (the owner's request).
   - `profile`: the last (or a chosen) trial's red-percent curve with the
     baseline, both marks and the detector's extrema drawn.
   Phase 2 (only if everything above is green and tested): a numpy-only
   Gaussian process (RBF kernel, closed form, Cholesky) giving mean and
   variance surfaces for `slice`, with a confidence contour; derive the
   gradient variance from the same covariance. Keep it under 120 lines and
   test it on a known function.
7. **Schema and tiers**: tier 1: the figure, the live tilt/speed/red
   readouts, Arm / Mark force / Finish keys, tip id entry, trial count and
   status; tier 2 ("Configure Transfer Map"): the figure-type and
   force-definition dropdowns, the AFM attach entries and key, export and
   import; tier 3 (Diagnostics): the trials table as a log stream or
   readonly text, delete, the detector's numbers for the last trial.
   Copy in the house voice (sentence case, the operator's words, no
   jargon in refusals).

## Write set (exclusive)

- NEW `src/model/transfer_map.py`, NEW `src/model/transfer_map_analysis.py`
- `src/model/plot_data.py` (additive: new figure types; existing behaviour
  and its tests unchanged)
- `src/model/red_monitor.py` (additive hooks only, see 3), `src/model/rotator.py`
  (only a read-only `position_deg` property if missing)
- `src/controller/setup.py` (registration line only)
- NEW `tests/test_transfer_map.py`, NEW `tests/test_transfer_map_analysis.py`,
  `tests/test_plot_data.py`, `tests/test_red_monitor.py`,
  `tests/test_setup.py` (the registered-order test, if it pins six)
- `tests/test_model_contract.py` is NOT yours: it discovers your class
  through the registry and must pass unchanged. If it fails, your model is
  wrong, not the test.

Views are not yours and need no change: if a schema element you want does
not render, say so under "Needs the lead" rather than editing a view. The
Signature view agents are editing `views/tk.py`, `views/qt.py` and
`views/web/**` right now.

## Gates before you commit

- `STATION_NO_WINDOWS=1 <PY> -m pytest tests -q -p no:cacheprovider -m "not qt"` to a file, exit code unpiped: everything green except the two
  Signature red-by-design view tests, which are not yours
  (`test_view_qt.py::test_e_go_is_ink_filled_and_disabled_is_a_dashed_muted_edge`,
  `test_view_web_server.py::test_the_disc_reads_stop_and_clear_with_its_ring`).
- Golden wire: 78, unchanged (you send nothing on any wire).
- A headless launch with the Web view on a spare port, every row SIM plus
  your row, `/api/state` shows the Transfer Map with its figure; record a
  trial against the simulated Red Percent (it produces a red-percent series
  in SIM) and export the CSV. Nothing on screen.

## Documentation you own in this round

- Handoff: `handoff/fix-transfer-map.md` in the worktree-fixer shape, plus a
  section "How a session records a trial" written for the owner, and a
  section "The force definitions" with each formula.
- The lead updates `docs/**` from your handoff. Do not edit `docs/`.
