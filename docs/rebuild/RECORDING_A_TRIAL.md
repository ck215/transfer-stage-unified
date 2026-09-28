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

Everything happens on the **Transfer Map** page. You do not need to open the Red Percent page during a trial; Red Percent only has to be launched.

1. **Launch** with Red Percent and the Transfer Map ticked (the Rotator and a probe too, ideally). When the Transfer Map opens, it makes its database ready and the event log says where it is and how many trials it holds ("Database Ready: …/data/transfer_map.sqlite: 12 trial(s)"). The same path is on the sheet under **Session → Database**, next to the **Trials** count. `--map-db PATH` or `STATION_MAP_DB` choose another file.
2. **New session database** (optional, under Session) starts a fresh file beside the current one, `transfer_map_<date>_<time>.sqlite`. The old file stays on disk, untouched. Pictures and exports stay in the same folder.
3. Follow **Next step** at the top of the Trial section. It always names the one thing to do next.
4. **Set capture region**: drag the rectangle over the sample on the screen picture. This is Red Percent's region: what it measures, and what the pictures show.
5. Type the **Tip ID**. **Trials on this tip** shows how many trials the database already holds for it ("0" for a new tip). Without a rotator, type the tilt under Configure Transfer Map, "Tilt without a rotator".
6. Press **Arm trial**. The station asks: "Frame the sample now. OK takes the before picture and arms trial 12 on tip T7." Frame the sample, then press OK. If Red Percent is not recording, Arm starts its run for you. The **before picture** appears on the sheet. If it cannot be taken (no region, the screen is not open), Arm is refused and nothing is written.
7. Lower the tip. "Red % since Arm" draws the trace. When the force is where you want it, press **Mark force**.
8. Press **Finish trial**, with a Note if you like. The station asks: "OK takes the after picture and ends trial 12." Press OK. The **after picture** appears, and the event log says "Trial 12 recorded, the 3rd on tip T7". If the after picture cannot be taken, the trial stays armed: fix the screen and finish again, or abort. If Arm started the Red Percent run, Finish ends it. A run you started yourself on the Red Percent page keeps running.
9. **Abort trial**, or any stop, ends the trial as "aborted" without asking. Its profile is kept, and the Red Percent run ends if the trial started it. If the tip broke, press **Tip broke** (it applies to the armed trial, or to the last one).
10. Later, after AFM: under Configure Transfer Map, type the trial number, the channel width and its uncertainty (and the thickness if measured), then **Attach AFM**. The trial becomes "measured" and turns from hollow to coloured on the 3D map.
11. Figures, exports and imports are unchanged, under Configure Transfer Map.

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

