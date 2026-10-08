# Recording a trial on the Transfer Map (updated 2026-10-07, after the proposal round)

The Transfer Map is the station's end goal: a map over speed and force
whose value is the transferred channel width, built from trials the operator
records at the bench. The map is speed x force class (Low, Medium, High,
Unclassed), width as colour; the tilt is collected with every trial and never
drawn. **Force is not measured by a sensor: it is read from the tip's shade**
(the lab's model, `src/model/tip_shade.py`, the default, owner ruling
2026-10-07: a comparison against the shade's own baseline beats the red
percent's extrema). The red-trace extrema remain as a secondary analysis
setting, the `factor=` (see "The force"). The model is
`src/model/transfer_map.py`.

**Recording philosophy (owner, 2026-10-07): record everything possible during
a trial, trim in analysis.** A trial keeps the RGB-analysis profile, a
full-display video at the display's native resolution with a `frames.csv`
sidecar, a `telemetry.csv` of every model's public state on the same clock
(`src/model/trial_telemetry.py`), the full-resolution still of the stage taken
at Arm and the display at the Mark. Cropping to a region, or to a time, is
analysis.

**Where the store lives.** The operator chooses it (owner decision 4,
2026-09-30): with no choice made the map has no store and every recording
command is refused until Setup's Store section opens or creates one; the choice
is remembered, a store inside the station's own folder is refused (updates
replace that folder), and `--map-db PATH` / `STATION_MAP_DB` override it. Pictures sit beside the
file in `<folder>/<database name>/<trial id>/`, exports in `<folder>/exports/`.
The store is at **version 8**, the lab's (adopted by the 2026-10-07 merge of the
bench's stage push, tag `bench-2026-10-07-stage`): trials carry `chip_id`, `flake_id`,
`cut_id` and `invalid` (the lab's "vacuum was off, data is invalid" flag: the trial
stays on record and in the export, off the map), and six tip-shade columns
(`force_position`, `force_class`, `contact_lowered`, `shade_baseline`, `shade_peak`,
`shade_mark`). An older file is upgraded the first time the station opens it, by
column presence (a column the file has is skipped, so a cut-short upgrade finishes; no backup is written, so copy a store before first opening it with a new version). A v9 is no longer planned. Plan rows: Tier S
in `BUGFIX_PLAN.md`. The Sample Map has its own store (v4, see `docs/rebuild/STATUS.md`).

## The procedure, for the owner

Everything happens on the **Transfer Map** page, which shows the step you are
in as a strip along the top (drawn from the model's `state["phases"]`, with the
step's own words from `state["step_text"]` and the analysis's health word from
`state["analysis_health"]`: settled, unsettled or stalled) and hides the
controls the step does not need (`MODEL_CONTRACT.md`, "Phases: the interactive
procedure"). The steps are **setup -> (new tip) -> region -> live -> marked ->
finish -> setup**; `new_tip` is the prompt you enter from setup and leave back to
it. RGB Analysis (formerly Red Percent) is drawn on the page and has no Setup
row of its own; the Transfer Map row launches both. **Next step** in the Trial
section always names the one thing to do. Abort trial and any stop work from the
region step on, and the stop overrides everything.

1. **Setup.** Launch with the Transfer Map ticked. If no store is chosen yet,
   open or create one (Store section; the store path and the trial count show on the sheet).
   The setup row holds the preliminary information:
   - **Tip**: one dropdown of the tips, each line "T7 · TAP300 · 3 trials" (with
     ", retired" where it is); **Tip status** says where the picked tip stands
     ("new", "in use since trial 3", "broke on trial 12" or "retired"). **New tip...**
     opens the prompt: a **Tip ID** and a **Model** (required; the list is seeded with
     TAP300 and every tip that existed before models is TAP300; a model not on the
     list is added with **New model** / **Add model**), then **Add tip** (refuses an empty or known ID and stays) or
     **Cancel**. A tip's model can be corrected later under Configure Transfer Map -> Tip
     (**Tip model**, `set_tip_model`).
   - **Sample**, **Chip**, **Flake**: three cascading dropdowns that read the Sample Map's
     store read-only (a new sample clears the chip and the flake; each refreshes on
     change). **Arm refuses unless all three are picked**, so every cut is traceable to its
     flake; with no samples registered the refusal points at the Sample Map. **Cut** shows
     the cut number, derived: 1 + the trials already on that flake (every status).
   - **Tilt for this trial** and **Speed for this trial** (a rotator's reading fills
     the tilt when one is connected; without one, type it; the tilt is collected, never
     demanded, and not drawn on the map).
   RGB Analysis must not be running a run of its own (Arm refuses; the trial starts its own
   run). Trials also carry the free-text `sample_id` column of older stores; the Sample Map's
   "Trials for this sample" listing reads the picked flake's trials.
2. **Arm trial.** The station asks for confirmation, and the first question is always
   **"Is the sample vacuum ON? Check it now."** (the station cannot sense it; trials were once cut with it
   off, which is what the `invalid` flag is for). The prompt also names the trial number, the
   tip, the tilt and speed, the cut and the sample > chip > flake, and a broken or retired
   tip adds its own question: one prompt, one Continue. Continue takes a
   **full-display, full-resolution still of the stage**: the trial's first
   asset (`before_full.png`) and the frame the region is picked on. There is
   no screen-capture buffer kept while waiting. Nothing is recording yet. If the
   still cannot be taken, Arm is refused and nothing is written.
3. **Region.** Drag the capture region on that still in the Web page (the drag
   is mapped to the still's own pixels and to the desktop). The region is what RGB
   Analysis measures. Abort here stores nothing, and the staged still is deleted.
4. **Live.** When the region lands, the trial row is created and the full-display video, the
   RGB Analysis run and the telemetry start together. The live row shows the
   **Force estimate** from the tip's shade ("No contact", "Contact · 0.04", "Medium force · 0.43";
   the number is where the shade stands on its peak, 0 at the peak and 1 back at baseline; it reads
   "Unsettled" while the analysis is rejecting frames, the settle gate's counters) and **Video**,
   one word: "Recording" or "Stopped" (its frame and drop counts are in Diagnostics). The analysis's
   own live group sits behind its details on the page; its **Estimators** plot (below) is hosted at
   tier 1 on this page. The live red-percent plots are no longer drawn here; the profile is stored
   and drawn once at review. Lower the tip.
5. **Mark force**, when the force is where you want it. The Mark's time, Z and
   speed are stamped at once; the video's next frame is flagged `marked` in `frames.csv`, and
   the display is kept as `mark_full.png`. The step becomes **marked**. The first shade frame at or
   after the Mark fixes the trial's force columns (see below). At review the cut's speed is also
   **measured** from the Z trace (`speed_measured_steps_s`) beside the typed speed.
6. **End recording** (in live and marked) when the cut is done: the video and the telemetry stop and the
   step becomes **finish**. **Review** shows the stage picture, the display at the Mark, **Video**
   and the trial's profile figure; add a Note, then
   **Finish trial** to keep it (the event log says "Trial 12 recorded, the 3rd on tip T7"), and the page
   returns to setup. If the video stops during a trial (a full disk, say) the event log says
   "Video Stopped" once and the trial goes on: the profile is the measurement, the video is the record.
   The full-display recording uses the ffmpeg the wheel ships (`devices.video.ffmpeg_exe`); with no
   encoder, Diagnostics says "No encoder" and the trial records without a video.
7. **Abort trial**, or any stop, ends the trial as "aborted" without asking. Its profile is
   kept, and the video and telemetry are closed on a worker so the stop never waits
   for the disk. If the tip broke, press **Tip broke** (drawn in marked and finish; it also applies
   under Configure Transfer Map to the last trial); it marks the tip's record "broke on trial N" until undone.
   **Mark trial invalid** / **Mark trial valid** (under Configure Transfer Map -> AFM measurement, with the Trial number) sets the lab's flag on a recorded trial.

8. Later, after AFM: under Configure Transfer Map, type the trial number, the channel width (AFM) and its uncertainty (and, when measured, the thickness, the **channel height** — the AFM step from the substrate to the channel's top, positive up — and the **trench depth** — how deep the tip cut into the flake, positive down), then **Attach AFM**. The trial becomes "measured" and turns from hollow to coloured on the map. **Set tilt for trial** and **Set speed for trial** (same section, using the Trial number and the two entries) correct a recorded trial.
9. An optical width (store version 6): under **Optical measurement**, type the trial number, the channel width read on the capture-region picture (pixels × the Sample Map's µm per pixel; method `capture_px`, or pick another method) and its uncertainty, then **Attach optical width**. It never makes a trial "measured": on the map an optical-only trial draws **ringed**, and the slice and the comparison use AFM widths only unless **Width source** (under Figure) is set to "AFM, else optical", where an optical point counts with 3× the default uncertainty. Every figure says which widths it used.
10. Figures, exports and imports are under Configure Transfer Map. The figure dropdown is Map (speed x force, coloured by width), Heatmap (the Gaussian process over speed x force), Compare and Profile.
11. **Tips**, under Configure Transfer Map → **Tip**: type the tip ID in the Trial section. **Tip note** + **Save tip note** keeps a note on its record. **Retire tip** (it asks first) marks a tip you will not use again; arming on it later asks. **Return tip to use** undoes that. Diagnostics → **Tips** lists every tip with its trial count, its first and last trial, and whether it broke or is retired. **Export tips** (under Data) writes the tips file with each tip's trial count and trial numbers.

## What a trial folder holds

`<store folder>/<database name>/<trial id>/` holds `before_full.png` (the stage
still at Arm, full display, full resolution), `screen.mp4` (the full-display
video, H.264 in a fragmented MP4 with a keyframe every second, so it plays
after a kill), `frames.csv` (one row per written frame: `frame, t_monotonic,
t_wall, marked`; row N is video frame N), `telemetry.csv` (`t, stream, value`:
every model's public state, the RGB Analysis rows and the EventLog lines, on the
recorder's monotonic clock) and `mark_full.png` (the display at the Mark). The
Transfer Map's table also keeps `video_path`, `video_index_path`,
`video_frames` and `video_dropped` (the dropped count is the frames the recorder
could not keep up with; the red-percent profile is never affected). The profile
stored with the trial carries, besides the red share, the green and blue shares, the
r/g/b means (`green`, `blue`, `r_mean`, `g_mean`, `b_mean`) and the tip `shade`,
present by column. Trials recorded before 2026-10-07 keep their `trial.mp4`,
`video_index.csv` and labelled `first_frame.png` / `mark_frame.png` (the region
recorder, 2026-09-28 to 2026-10-06), and trials before 2026-09-28 their `before.png`,
`mark.png` and `after.png`. The trials export carries every picture column plus the video
columns, and a third export file lists the tips. An older database is upgraded
the first time the station opens it and its trials are kept ("Database Upgraded").

## RGB Analysis samples settled frames (CAP-1, 2026-10-07)

RGB Analysis (the model formerly named Red Percent; `src/model/rgb_analysis.py`, class
`RgbAnalysis`, section "RGB analysis details") samples at the source rate (about 15 Hz on
the bench's 7 fps vendor viewer), not "as fast as it can grab". A frame is accepted only
when two reads at least 5 ms apart agree. Black, stale and unsettled grabs are rejected and
counted in the model's Diagnostics (`frames_accepted`, `rejected_black`, `rejected_stale`,
`rejected_unsettled`) and never reach a row, a subscriber or the reading. Why: on real
bench data (40 trials) the vendor viewer repaints mid-grab, so black and stale frames
entered the video, the profile and the force extrema (median 51 % glitch rows). Analysis
then ignores any glitch row that is left (`transfer_map_analysis.settled_mask`, and robust
extrema that are None when nothing is settled).

Every settled sample carries **six channel numbers**: the red, green and blue shares (the
percent of the region's pixels passing each mask) and the region's mean r, g and b. The red
share is the number the older versions called the red percent, unchanged.

## Recovering recorded trials

`python dev/reanalyse_trials.py <db.sqlite> [--out DIR] [--factor F] [--write] [--repair-video]`
re-analyses the trials already recorded: it drops glitch rows, recomputes the extrema and
the force definitions the app's own way, and writes a report (`reanalysis_<date>.md`) of
old against new. It is read-only unless `--write`, which backs the database up
first and updates only `red_min`, `red_max` and `red_baseline`. `--factor` names the
column that drives the extrema (see below). The owner reviews the report before any
`--write`. (`--repair-video` is a visual repair of the video; it never re-derives a red
percent from pixels.) The lab's one-off scripts `dev/merge_cuts.py` and
`dev/set_sample_ids.py` (renumber and back-fill 2026-09-28 trials, with a backup first) live
beside it and are the owner's to delete. `dev/estimators_offline.py` runs the estimator
bank over a recorded video (below).

## Backups (2026-10-07)

Each signed-in user's stores (the Transfer Map's trials and the Sample DB) stay
on this computer and are copied, after every save and at Quit, to the user's
backup folder (`src/controller/backup.py`): their Account setting "Backup folder",
else `$STATION_BACKUP_DIR/<email>/`, else `~/QMDL_Drive/transfer-stage-dbs/<email>/`
when `~/QMDL_Drive` exists. `STATION_BACKUP_DIR=off` turns it off; a Guest is
never backed up. The folder mirrors the store folder: `transfer_map.sqlite` with
its `transfer_map/` (pictures, videos) and `exports/`, `sample_map.sqlite` with
its `images/`. Each database is a consistent SQLite snapshot (online backup API),
renamed into place, so it is never half written. Account shows "Last backup
23:41 → <folder>" and has Back up now. A failure (drive not mounted, read-only)
is one warning per streak.

**To restore:** quit the station; copy the backup folder's `*.sqlite` files and
their folders (`transfer_map/`, `exports/`, `images/`) into an empty local folder
(e.g. `~/transfer-stage-runs/stores/<email>/`), keeping the layout; skip the
hidden `.backup-manifest.json`. Start the station, sign in, and Open store on
each map with the copied `.sqlite`. Trial rows name their pictures by absolute
path, so restore to the SAME folder the store lived in when you can; elsewhere
the trials are intact but their pictures must be re-pointed. To force a full
re-copy, delete `.backup-manifest.json` in the backup folder.

## The force

### The tip's shade (the default, the lab's model)

The map's force is `shade_position` (owner 2026-10-06, confirmed primary 2026-10-07). The
red percent counts pixels with R over 150, G under 100 and B under 100. The tip is orange (about 215, 153, 32), so only its darkest edge pixels pass, a few hundred of tens of thousands, and the count is dominated by one-frame flashes. The profile also logs a row only when the rounded red changes by 0.1 or more, so steady stretches have no rows. Neither says anything about force.

What does: the **median green of the right half** of the recorded tip region (`model/tip_shade.py`). After the tip touches the sample the shade rises, peaks about 19 steps later (width near 10 steps) and falls back; where it stands on that peak at the Mark is the force. For each frame (taken at 15 a second on the picture thread, from the same frames the video records):

- **baseline**: the median shade 0.3 to 1.3 s after Arm; its noise is the robust scatter over the same span.
- **smoothed shade**: a median over the last second of frames.
- **contact**: the smoothed shade stays above baseline by max(5 noise levels, 5% of baseline) for 0.5 s. `contact_lowered` is the steps lowered, from the first Z seen, where that rise began.
- **position**: (highest smoothed shade since contact - smoothed shade now) / (highest - baseline); 0 at the peak, 1 back at baseline, held at 1.2.
- **status** (the live row's **Force estimate**): No contact; Contact below 0.10; Low below 1/3; Medium below 2/3; High above. The 0.10, the thirds and the contact line are the owner's to tune (`tip_shade` constants).

The first frame at or after the Mark fixes the trial's columns: `force_position`, `force_class`, `contact_lowered`, `shade_baseline`, `shade_peak`, `shade_mark`. No contact before the Mark, or no video, leaves them empty. The video index gains a `shade` column. Recording is unchanged (the whole tip region is selected); `rebuild_force` recomputes the columns of recorded trials from their footage, or from the index's shade column, with the same tracker (`model/shade_offline.py`; `model/finalize.py` walks the samples for AFM and optical estimates, a Qt-only finalizer, frozen with the Qt view; a Web finalizer is open).

### The red-trace definitions, a secondary analysis

The other definitions read the trial's red trace and stay in `FORCE_DEFINITIONS` for comparison. On the 5-sample running median of the trace: M = value at the detected peak (the maximum before the operator Mark, or before the end); m = value at the dip (the minimum after the peak); b = baseline (median raw value over the first 1 s after Arm); n(t) = (value(t) - m)/(M - m). All are oriented so that **larger = more force**. None when undefined (flat trace, no Mark, no Z).

The **factor** (`factor=` on `detect`, `force_indices` and the live estimate; `dev/reanalyse_trials.py --factor`) says which profile column the trace is: `red` (the default), or `green`, `blue`, `r_mean`, `g_mean`, `b_mean`, or a ratio such as `red/green`. It changes these definitions only; the shade model does not read it.

| name | formula |
|---|---|
| `shade_position` | **The map's default.** From the tip's shade: see above |
| `shadow_vs_baseline` | (b - m) / (M - m) |
| `shadow_vs_peak` | (M - m) / M |
| `at_operator_mark` | (M - value(Mark)) / (M - m) = 1 - n(Mark). The brief said "normalised red at the Mark"; inverted so it grows with force like the rest |
| `dip_area` | integral from t(M) to the end of max(0, n(b) - n(t)) dt, trapezoid, in normalised units x seconds |
| `fall_slope` | (n(M) - n(m)) / (t(m) - t(M)) = 1 / fall time, per second |
| `z_past_peak` | abs(z(Mark or end) - z(M)), steps lowered past the peak (added; needs Z) |

The registry is `transfer_map_analysis.FORCE_DEFINITIONS` (name -> function(context)). A new line there reaches every figure, dropdown and export. Imported trials add their own names (default "given").

### The estimator bank and the comparison plot

The force model is to be chosen on footage, not assumed (owner 2026-10-07), so
`src/model/estimators.py` computes a bank of per-frame estimators on every accepted frame:
five crops of the capture region (`full`, `right_half`, `left_half`, `centre`, and a `custom`
rectangle the operator types) times nine estimators (the shade's median green, the red, green
and blue shares, the r/g/b means, the luma mean and red minus green), kept in a 60 s ring.
RGB Analysis's **Estimators** section draws them against each curve's own first-second baseline
on a fixed-interval plot (replotted at most once a second) and a fixed-interval image, with a
**Curves** preset dropdown and an "Estimator curves and crop" section for a curve list and the custom
crop. It is hosted at tier 1 on the Transfer Map page, so the comparison is on screen during a run.
`python dev/estimators_offline.py <trial.mp4> [--crop NAMES] [--region l,t,w,h] [--out PREFIX]`
runs the same code over a recorded video and writes the raw curves as CSV and the normalised
ones as a PNG.
