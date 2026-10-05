# Recording a trial on the Transfer Map (2026-09-27)

The Transfer Map is the station's end goal: a 3D map over tilt, speed and
force whose value is the transferred channel width, built from trials the
operator records at the bench. Force is not measured by a sensor: it is
read from the red-percent lowering profile, normalised per trial by its own
minimum and maximum (owner ruling 2026-09-27). The model is
`src/model/transfer_map.py`; its store is local to this checkout
(`data/transfer_map.sqlite`, git-ignored, made ready when the row launches;
`--map-db PATH` or `STATION_MAP_DB` override it; a bundle keeps it beside
the executable). Since the first real trial (2026-09-27, the Linux bench
PC) the Transfer Map page is the whole trial sheet: Red Percent's region
picker, the Next step line, picture prompts and the session database are
on it. Plan rows: Tier S in
`BUGFIX_PLAN.md`. Agent handoff: `handoff/fix-transfer-map.md`.

## How a session records a trial (for the owner)

Everything happens on the **Transfer Map** page: since 2026-09-28 Red Percent is drawn on it (its live group after the trial sheet, its details behind their own disclosure) and has no page of its own while the map is launched. Red Percent has no Setup row: the Transfer Map row launches both.

1. **Launch** with the Transfer Map ticked (it brings Red Percent; the Rotator and a probe too, ideally). When the Transfer Map opens, it makes its database ready and the event log says where it is and how many trials it holds ("Database Ready: …/data/transfer_map.sqlite: 12 trial(s)"). The same path is on the sheet under **Session → Database**, next to the **Trials** count. `--map-db PATH` or `STATION_MAP_DB` choose another file.
2. **New session database** (optional, under Session) starts a fresh file beside the current one, `transfer_map_<date>_<time>.sqlite`. The old file stays on disk, untouched. Pictures and exports stay in the same folder.
3. Follow **Next step** at the top of the Trial section. It always names the one thing to do next.
4. **Set capture region**: drag the rectangle over the sample on the screen picture. This is Red Percent's region: what it measures, and what the pictures show.
5. Type the **Tip ID** and press Return (or leave the box). Once the region and a tip ID are both set, Red Percent starts polling by itself, from a fresh baseline taken from its first frame. The event log says "Polling Started", and the **Red** readout on the sheet starts to move. **Trials on this tip** shows how many trials the database holds for it, and **Tip** says where the tip stands: "new", "in use since trial 3", "broke on trial 12" or "retired". If Red Percent cannot start (for example, the screen cannot be captured), the event log warns once and **Next step** says what to fix; Arm tries again. Clearing the tip or the region does not stop polling; a stop is yours. Without a rotator, type the tilt under Configure Transfer Map, "Tilt without a rotator". **Tilt for this trial** and **Speed for this trial** sit under the tip: the tilt varies between trials of one tip, and the probe's speed setting is one number for the whole session, so both are typed per trial (a rotator's reading fills the tilt when one is connected). Next step insists on the tilt; the Arm prompt names both.
6. Press **Arm trial**. The station asks: "Is the sample vacuum ON? Check it now. Frame the sample now. Continue takes the whole-screen picture, starts the video and arms trial 12 on tip T7 at 12.5 deg, 300 steps/s." The vacuum line comes first on every Arm: the station cannot sense the vacuum, so it asks (two trials were cut with it off on 2026-10-04). If the tip broke earlier or is retired, "Tip T7 broke on trial 12. Arm on it anyway?" (or "... is retired ...") follows the vacuum line in the same prompt: one question, one Continue. Arm takes over the polling run (or starts one if none is running), so Finish, Abort and any stop end it; a run you started yourself on Red Percent keeps running. The trial's clock starts at your Continue, and the run's first row is its first sample. On Continue the station keeps the **whole screen** once, for context (under Configure Transfer Map → **Context**, "Arm, whole screen"), and starts the **video** of the capture region: up to 15 frames a second of exactly what Red Percent measures, each under a dark band that reads `t=12.34 s  red 63.2 %  z -1520` (seconds since Arm, that frame's red percent, the probe's Z). The sheet shows the video's **First frame**, and **Video** counts it: "recording, 312 frames". If the capture region cannot be grabbed, Arm is refused and nothing is written. A missing whole-screen picture is a "No Full Picture" warning; the trial arms anyway. The first Arm on a tip the database has not seen creates its record ("Tip T7 created").
7. Lower the tip. "Red % since Arm" draws the trace. When the force is where you want it, press **Mark force**. The Mark's time, Z and speed are stamped at once; the Mark takes no picture of its own and never waits. From that moment every frame's band ends in `MARK`, and the first frame after it appears on the sheet as the **Mark frame**. At Finish the cut's speed is also **measured** from the Z trace (the fastest sustained |dz/dt| after the Mark, `speed_measured_steps_s`) and shown beside the typed speed in the Trials log, so the two can be cross-checked.
8. Press **Finish trial**, with a Note if you like. The station asks: "Continue ends trial 12 and closes its video." Press Continue. The video is closed and **Video** reads, for example, "trial.mp4, 1240 frames, 3 dropped" ("dropped" are frames the recorder could not keep up with; the red-percent profile is never affected). The event log says "Trial 12 recorded, the 3rd on tip T7". If the video stops during a trial (a full disk, say), the event log says "Video Stopped" once and the trial goes on: the profile is the measurement, the video is the record. Without the `imageio-ffmpeg` package the video is a folder of labelled JPEG frames instead of an MP4 ("No Video Encoder" in the event log); Diagnostics → **Video encoder** says which this station uses. If Arm started the Red Percent run, Finish ends it.
9. **Abort trial**, or any stop, ends the trial as "aborted" without asking. Its profile is kept, and the Red Percent run ends if the trial started it. Abort, and any stop, also close the trial's video; the stop itself never waits for it. If the tip broke, press **Tip broke** (it applies to the armed trial, or to the last one). **Tip broke** also marks the tip's record: it "broke on trial N" until you undo it.
10. Later, after AFM: under Configure Transfer Map, type the trial number, the channel width and its uncertainty (and the thickness if measured), then **Attach AFM**. The trial becomes "measured" and turns from hollow to coloured on the 3D map. **Set tilt for trial** and **Set speed for trial** (same section, using the Trial number and the two entries) correct a recorded trial.
11. Figures, exports and imports are unchanged, under Configure Transfer Map.
12. **Tips**, under Configure Transfer Map → **Tip**: type the tip ID in the Trial section. **Tip note** + **Save tip note** keeps a note on its record. **Retire tip** (it asks first) marks a tip you will not use again; arming on it later asks. **Return tip to use** undoes that. Diagnostics → **Tips** lists every tip with its trial count, its first and last trial, and whether it broke or is retired. **Export tips** (under Data) writes the tips file with each tip's trial count and trial numbers.


Each trial folder under `data/transfer_map/<trial id>/` holds `before_full.png` (the whole screen at Arm), `trial.mp4` (or `frames/frame_000001.jpg` … without the encoder), `video_index.csv` (one row per video frame: `frame, t_s, red, z, marked`, where row N is frame N of the video and `t_s` is seconds since Arm), and `first_frame.png` / `mark_frame.png`, the two labelled frames the sheet shows. Trials recorded before 2026-09-28 keep their `before.png`, `mark.png`, `after.png` and whole-screen twins. The trials export carries every picture column plus `video_path`, `video_index_path`, `video_frames` and `video_dropped`, and a third export file lists the tips. An older database is upgraded the first time the station opens it and its trials are kept ("Database Upgraded" in the event log).

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

