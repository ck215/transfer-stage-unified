# Brief: a labelled video per trial instead of stills (branch `rb-video`, worktree `../rb-video`)

Base: `345763f` or later on `mvc-refactor`. Python:
`/Users/ianalbinogonzalez/Documents/GitHub/transfer-stage-unified/main/.venv/bin/python`.
Read `.claude/agents/worktree-fixer.md` first (its rules bind you), then
`docs/rebuild/RECORDING_A_TRIAL.md`, `src/model/transfer_map.py` in full
(the picture thread, `_take_picture`, the full pictures, `mark_force`,
`finish_trial`, `abort_trial`, the stop's abort writer, `TrialStore._migrate`,
`SCHEMA_VERSION`, `TRIAL_COLUMNS`, the schema's picture elements, the
export), `src/model/red_monitor.py` (`subscribe`: `(t_s, red, positions)`
per logged row; `grab_frame`; the run loop that grabs the region at
~66 Hz), `src/devices/screen.py` (`grab`, `keep_handle`),
`packaging/station.spec` and `pyproject.toml` (dependencies), and
`handoff/fix-mark-and-tips.md` + `handoff/fix-full-pictures.md`.

## What the owner asked (2026-09-28)

"Rather than like 7 pictures, a video where timestamps label the footage
for analysis afterward." Ruled: MP4 through `imageio-ffmpeg`, a JPEG
sequence as the automatic fallback; one whole-screen still at Arm stays;
the region stills go.

## Design (the lead's; deviations go in the handoff with reasons)

V1. **Dependency.** `imageio-ffmpeg` (pinned, current release) in
    `pyproject.toml` dependencies and `requirements.txt`; the PyInstaller
    spec collects its binary (`imageio_ffmpeg` ships it as package data:
    a hook or `collect_data_files`). Import it lazily inside
    `src/devices/` only (a new `src/devices/video.py`: the architecture
    test keeps native libraries in `devices/`), never at module import
    of a model.
V2. **`devices/video.py`: `class TrialRecorder`.** `open(path, fps,
    size)`, `write(frame_rgb, label_lines)`, `close() -> path`. It draws
    the label (Pillow: the trial's monospace text on a dark band at the
    top: `t=12.34 s  red 63.2 %  z -1520  MARK`) then encodes. With
    `imageio_ffmpeg` present it writes H.264 MP4 (`libx264`, `-crf 23`,
    `-pix_fmt yuv420p`, even dimensions padded); without it, a folder of
    `frame_000001.jpg` at quality 85 and the same label, and `close()`
    returns the folder. Frame size fixed at open; a frame of another size
    is resized. A `probe()` classmethod reports which path is available,
    for the sheet's Diagnostics.
V3. **Recording on the map.** From Arm to Finish/Abort (and the stop's
    abort), the map records the capture region: it subscribes to Red
    Percent's rows as today for the profile, and separately asks
    `red.grab_frame()`... NO: a second grab per frame doubles the screen
    work. Instead Red Percent gains one additive hook,
    `subscribe_frames(fn)` / `unsubscribe_frames(fn)`, called from the
    run loop with `(t_s, frame_bgra_or_rgb, red)` for every grabbed frame
    (the same frame it measures), and the map's picture thread drains a
    bounded queue at most `VIDEO_FPS = 15` frames per second (drop, never
    block the loop; count the drops). Each written frame's label carries
    seconds since Arm, red, Z (from the latest profile row), and MARK
    once `mark_force` has been pressed. The sidecar index
    `<trial>/video_index.csv`: `frame, t_s, red, z, marked`. Files:
    `<trial>/trial.mp4` (or `<trial>/frames/`) and the index. Columns:
    `video_path`, `video_index_path`, `video_frames`, `video_dropped`;
    `SCHEMA_VERSION = 5`, migrated in place as before (prove on a
    version-4 file with a trial).
V4. **The stills.** Keep `before_full.png` at Arm (context) and its
    column; DROP the region stills and the Mark/After full pictures and
    their columns from new trials (columns stay in the table for old
    rows; the export keeps them; the schema no longer shows them). The
    sheet's tier 1 shows two `sch.image`s: "First frame" and "Mark
    frame" (from the recording, labelled) and a readonly "Video"
    (`video_status`: "recording, 312 frames" / "trial.mp4, 1240 frames,
    3 dropped" / "no encoder: JPEG frames"). Tier 2 "Full pictures"
    becomes "Context" with the Arm whole-screen picture only.
V5. **Stop path first.** A stop (estop/abort) closes the recorder on the
    picture thread with a bounded join, never on the stop's own thread;
    a recorder that fails mid-trial logs one warning and the trial goes
    on (the profile is the measurement, the video is the record).
V6. **Tests first.** `tests/test_video.py`: the recorder writes MP4 when
    the encoder is present (skip if the wheel is missing on this Mac:
    install it in the venv first, `pip install imageio-ffmpeg`, and say
    the version), the JPEG fallback when it is not (monkeypatch the
    import), labels are drawn (a pixel check on the band), odd sizes are
    padded. `tests/test_transfer_map.py`: frames flow from a fake Red
    Percent through the hook into a recording, the rate cap drops,
    MARK appears in the index from the Mark on, Finish/Abort/stop close
    the file and fill the columns, the migration from version 4.
    `tests/test_red_monitor.py`: the hook is called per grabbed frame and
    a slow subscriber never slows the loop (measure).
V7. **Doc text for the lead** (steps 6–8 and the folder sentence of
    `RECORDING_A_TRIAL.md`), in the handoff.

## Write set (exclusive)

- NEW `src/devices/video.py`, `src/model/transfer_map.py`,
  `src/model/red_monitor.py` (the frame hook only), `pyproject.toml`,
  `requirements.txt`, `packaging/station.spec` (+ a hook file under
  `packaging/hooks/` if needed)
- NEW `tests/test_video.py`, `tests/test_transfer_map.py`,
  `tests/test_red_monitor.py`, `tests/test_packaging.py` (additive)

Not yours: views, `src/controller/**`, `src/schema.py`, `docs/**`.

## Gates before you commit

- `STATION_NO_WINDOWS=1 <PY> -m pytest tests -q -p no:cacheprovider -m "not qt"`
  to a file under your scratch dir, exit code unpiped: all green.
- Golden: 78.
- Headless Web on port 8102 (all rows SIM, `STATION_MAP_DB` in scratch,
  the usual `/api` recipe): one trial armed for ~3 s with a fake moving
  screen if SIM's is still (say how), then `<trial>/trial.mp4` exists,
  plays back frame count = index rows (check with `imageio_ffmpeg`'s
  reader or ffprobe from the wheel), the Mark frame served by
  `/api/data`. Nothing on screen.

## Handoff

`handoff/fix-trial-video.md`. Commit on `rb-video`; never push.
