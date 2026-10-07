# Recording a trial on the Transfer Map (updated 2026-10-07)

The Transfer Map is the station's end goal: a map over speed and force
whose value is the transferred channel width, built from trials the operator
records at the bench. (Until 2026-10-07 it was a 3D map over tilt, speed and
force; the map is now speed x force class, width as colour; the tilt is
collected with every trial and never drawn.) Force is not measured by a
sensor: it is read from the red-percent lowering profile, normalised per
trial by its own minimum and maximum (owner ruling 2026-09-27). The model is
`src/model/transfer_map.py`.

**Recording philosophy (owner, 2026-10-07): record everything possible during
a trial, trim in analysis.** A trial keeps the red-percent profile, a
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
The repo's store is at **version 6**. The lab's databases are v7/v8 (the bench PC
runs code that is not in this repo yet); the next repo version will be v9, with
column-presence migration and a `.v8.bak` backup. Nothing here describes v7, v8
or v9 as shipped. Plan rows: Tier S in `BUGFIX_PLAN.md`.

## The procedure, for the owner

Everything happens on the **Transfer Map** page, which shows the step you are
in and hides the controls the step does not need (`MODEL_CONTRACT.md`,
"Phases: the interactive procedure"). The steps are
**setup -> Arm -> region -> live -> marked -> finish -> setup**. Red Percent is drawn on
the page and has no Setup row of its own; the Transfer Map row launches both.
**Next step** at the top of the Trial section always names the one thing to do.
Abort trial and any stop work from the region step on, and the stop overrides everything.

1. **Setup.** Launch with the Transfer Map ticked. If no store is chosen yet,
   open or create one (Store section; the store path and the trial count show on the sheet). Type the **Tip ID**
   (**Trials on this tip** and **Tip** say where the tip stands: "new", "in use
   since trial 3", "broke on trial 12" or "retired"), the **Tilt for this trial**
   and the **Speed for this trial** (a rotator's reading fills the tilt when one is
   connected; without one, type it; the tilt is collected with the trial and
   not drawn on the map). Red Percent must not be running a run of its own (Arm refuses; the trial starts its own run). Trials carry a free-text `sample_id` column, and the Sample Map's "Trials for this sample" listing reads it; the Transfer Map sheet has no entry for it in this tree.
2. **Arm trial.** The station asks for confirmation, then takes a
   **full-display, full-resolution still of the stage**: the trial's first
   asset (`before_full.png`) and the frame the region is picked on. There is
   no screen-capture buffer kept while waiting. Nothing is recording yet. If the
   still cannot be taken, Arm is refused and nothing is written. A tip that broke or is retired asks
   once more before arming.
3. **Region.** Drag the capture region on that still in the Web page (the drag
   is mapped to the still's own pixels and to the desktop). The region is what Red
   Percent measures. Abort here stores nothing, and the staged still is deleted.
4. **Live.** When the region lands, the trial row is created and the full-display video, the
   Red Percent run and the telemetry start together. The sheet shows the **Red**
   reading and the video's status ("recording, 312 frames"). The live red-percent plots are
   no longer drawn here; the profile is still stored and drawn once at review. Lower
   the tip.
5. **Mark force**, when the force is where you want it. The Mark's time, Z and
   speed are stamped at once; the video's next frame is flagged `marked` in `frames.csv`, and
   the display is kept as `mark_full.png`. The step becomes **marked**. At review the cut's speed is also
   **measured** from the Z trace (`speed_measured_steps_s`) beside the typed speed.
6. **End recording** when the cut is done: the video and the telemetry stop and the
   step becomes **finish**. Review the pictures and the profile, add a Note, then
   **Finish trial** to keep it (the video closes: "screen.mp4, 1240 frames, 3 dropped"; the event log says
   "Trial 12 recorded, the 3rd on tip T7"), and the page returns to setup. If the video
   stops during a trial (a full disk, say) the event log says "Video Stopped" once and the trial
   goes on: the profile is the measurement, the video is the record. The
   full-display recording uses the ffmpeg the wheel ships (`devices.video.ffmpeg_exe`); with no encoder, Diagnostics
   says "No encoder" and the trial records without a video.
7. **Abort trial**, or any stop, ends the trial as "aborted" without asking. Its profile is
   kept, and the video and telemetry are closed on a worker so the stop never waits
   for the disk. If the tip broke, press **Tip broke** (it applies to the armed trial or to
   the last one); it also marks the tip's record "broke on trial N" until undone.

8. Later, after AFM: under Configure Transfer Map, type the trial number, the channel width (AFM) and its uncertainty (and, when measured, the thickness, the **channel height** — the AFM step from the substrate to the channel's top, positive up — and the **trench depth** — how deep the tip cut into the flake, positive down), then **Attach AFM**. The trial becomes "measured" and turns from hollow to coloured on the 3D map. **Set tilt for trial** and **Set speed for trial** (same section, using the Trial number and the two entries) correct a recorded trial.
9. An optical width (store version 6): under **Optical measurement**, type the trial number, the channel width read on the capture-region picture (pixels × the Sample Map's µm per pixel; method `capture_px`, or pick another method) and its uncertainty, then **Attach optical width**. It never makes a trial "measured": on the 3D map an optical-only trial draws **ringed**, and the slice and the comparison use AFM widths only unless **Width source** (under Figure) is set to "AFM, else optical", where an optical point counts with 3× the default uncertainty. Every figure says which widths it used.
10. Figures, exports and imports are unchanged, under Configure Transfer Map.
11. **Tips**, under Configure Transfer Map → **Tip**: type the tip ID in the Trial section. **Tip note** + **Save tip note** keeps a note on its record. **Retire tip** (it asks first) marks a tip you will not use again; arming on it later asks. **Return tip to use** undoes that. Diagnostics → **Tips** lists every tip with its trial count, its first and last trial, and whether it broke or is retired. **Export tips** (under Data) writes the tips file with each tip's trial count and trial numbers.


Each trial folder under `data/transfer_map/<trial id>/` holds `before_full.png` (the whole screen at Arm), `trial.mp4` (or `frames/frame_000001.jpg` … without the encoder), `video_index.csv` (one row per video frame: `frame, t_s, red, z, marked`, where row N is frame N of the video and `t_s` is seconds since Arm), and `first_frame.png` / `mark_frame.png`, the two labelled frames the sheet shows. Trials recorded before 2026-09-28 keep their `before.png`, `mark.png`, `after.png` and whole-screen twins. The trials export carries every picture column plus `video_path`, `video_index_path`, `video_frames` and `video_dropped`, and a third export file lists the tips. An older database is upgraded the first time the station opens it and its trials are kept ("Database Upgraded" in the event log).

## What a trial folder holds

`<store folder>/<database name>/<trial id>/` holds `before_full.png` (the stage
still at Arm, full display, full resolution), `screen.mp4` (the full-display
video, H.264 in a fragmented MP4 with a keyframe every second, so it plays
after a kill), `frames.csv` (one row per written frame: `frame, t_monotonic,
t_wall, marked`; row N is video frame N), `telemetry.csv` (`t, stream, value`:
every model's public state, the Red Percent rows and the EventLog lines, on the
recorder's monotonic clock) and `mark_full.png` (the display at the Mark). The
Transfer Map's table also keeps `video_path`, `video_index_path`,
`video_frames` and `video_dropped` (the dropped count is the frames the recorder
could not keep up with; the red-percent profile is never affected). Trials
recorded before 2026-10-07 keep their `trial.mp4`, `video_index.csv` and
labelled `first_frame.png` / `mark_frame.png` (the region recorder, 2026-09-28
to 2026-10-06), and trials before 2026-09-28 their `before.png`, `mark.png`
and `after.png`. The trials export carries every picture column plus the video
columns, and a third export file lists the tips. An older database is upgraded
the first time the station opens it and its trials are kept ("Database Upgraded").

## Red Percent samples settled frames (CAP-1, 2026-10-07)

Red Percent samples at the source rate (about 15 Hz on the bench's 7 fps vendor
viewer), not "as fast as it can grab". A frame is accepted only when two reads at
least 5 ms apart agree. Black, stale and unsettled grabs are rejected and
counted in the model's Diagnostics (`frames_accepted`, `rejected_black`,
`rejected_stale`, `rejected_unsettled`) and never reach a row, a subscriber or
the reading. Why: on real bench data (40 trials) the vendor viewer repaints
mid-grab, so black and stale frames entered the video, the profile and the
force extrema (median 51 % glitch rows). Analysis then ignores any glitch row
that is left (`transfer_map_analysis.settled_mask`, and robust extrema that are
None when nothing is settled).

## Recovering recorded trials

`python dev/reanalyse_trials.py <db.sqlite> [--out DIR] [--write] [--repair-video]`
re-analyses the trials already recorded: it drops glitch rows, recomputes the extrema and
the force definitions the app's own way, and writes a report (`reanalysis_<date>.md`) of
old against new. It is read-only unless `--write`, which backs the database up
first and updates only `red_min`, `red_max` and `red_baseline`. The owner
reviews the report before any `--write`. (`--repair-video` is a visual repair
of the video; it never re-derives a red percent from pixels.)

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

