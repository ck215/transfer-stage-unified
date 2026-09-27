# Recording a trial on the Transfer Map (2026-09-27)

The Transfer Map is the station's end goal: a 3D map over tilt, speed and
force whose value is the transferred channel width, built from trials the
operator records at the bench. Force is not measured by a sensor: it is
read from the red-percent lowering profile, normalised per trial by its own
minimum and maximum (owner ruling 2026-09-27). The model is
`src/model/transfer_map.py`; its store is local to this checkout
(`data/transfer_map.sqlite`, git-ignored; `--map-db PATH` or `STATION_MAP_DB`
override it; a bundle keeps it beside the executable). Plan rows: Tier S in
`BUGFIX_PLAN.md`. Agent handoff: `handoff/fix-transfer-map.md`.

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

