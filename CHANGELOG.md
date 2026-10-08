# Changelog

What changes for the people at the bench, release by release. The format
follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). A version is
a git tag `vMAJOR.MINOR.PATCH` and nothing else: `pyproject.toml` says 0.0.0
in git, and `packaging/release.py version` (or `src/app.py --version`) says
what any tree is.

How this file is kept:

- A change an operator would notice goes under **Unreleased** as it lands,
  in the operator's words, under Added, Changed, Removed or Fixed.
- `dev/release.sh vX.Y.Z` cuts a release: it moves everything under
  Unreleased into a new `## [X.Y.Z] - <date>` section, commits that, and tags
  the commit with the section as the tag's message. It refuses while
  Unreleased is empty.
- That section is the release's notes on GitHub (`packaging/release.py
  notes vX.Y.Z`); its first line is what the Setup page's Update row quotes.

## [Unreleased]

### Added

- **The whole display is recorded.** Every trial keeps a full-screen video
  at the display's own resolution (`screen.mp4`, which survives a crash),
  the time of every frame (`frames.csv`), every device's readings on one
  clock (`telemetry.csv`) and a full-resolution still at Arm and at Mark.
  Record everything, trim in analysis.
- **Samples, chips and flakes.** The Sample Map holds a sample's chips and
  each chip's flakes, with materials (hBN, graphite, MoS2 to start) and
  photos at every level; a sample and a flake need a photo. Pick a sample,
  then one of its chips, then a flake; each list follows the one before it,
  and each level asks only for what it needs.
- **Every trial names its sample, chip and flake.** The trial's setup row
  has Sample, Chip and Flake dropdowns that read the Sample Map; Arm will not
  start without all three, and the cut number follows from the flake.
- **Tips have a model.** "New tip..." opens a small prompt for the tip's ID
  and its model (TAP300 to start; add others there); existing tips were
  marked TAP300. The Tip dropdown shows each tip's model and trial count.
- **The vacuum question.** Every Arm first asks "Is the sample vacuum ON?"
  A recorded trial can be marked invalid (and valid again); it stays on record
  and in the export, off the map.
- **The force, live.** The trial row shows the force estimate from the tip's
  shade ("Medium force · 0.43", "No contact", or "Unsettled" while the picture
  is not steady). The lab's shade method is the force on the map; the
  red-percent definitions remain as an analysis option.
- **RGB Analysis records six numbers** per settled sample: the red, green and
  blue shares and the mean red, green and blue of the region. The analysis
  can be run on any of them (`dev/reanalyse_trials.py --factor`).
- **A bank of estimators.** Five crops of the capture region times nine
  estimators run on every frame and are drawn together, relative to their own
  baseline, on the Estimators plot (on the trial page during a run);
  `dev/estimators_offline.py` does the same over a recorded video.
- **Accounts.** Setup's Account section signs you in with email and password,
  creates an account or opens a guest session (the station's defaults). An
  account keeps its own defaults and stamps its email on the trials and samples
  it records.
- **Tutorials.** A Tutorials button on the rail walks through "Your first
  trial" and "Register a sample" by pointing at the real controls (simulation
  only).
- **Versions.** `station-web --version` and the Setup page's Station row say
  which version this is; a version is a release tag.
- **Releases publish themselves.** A release is a tag on `main` cut with
  `dev/release.sh`; the build uploads `SHA256SUMS` and publishes the release
  itself. The updater, `update.sh` and `update.bat` follow releases.
- **The lab's stage changes** (2026-10-07): the Transfer Map store at version
  8 (chip, flake and cut on every trial, the invalid flag, six tip-shade
  columns), the tip-shade force, and the lab's offline shade tools.

### Changed

- **A trial is a procedure.** The Transfer Map walks through setup, region,
  live, marked and finish, shown as a step strip with the next step in words
  and a health word for the analysis. At Arm a full-resolution picture of the
  whole display is taken and the capture region is drawn on it; nothing
  records until the trial is live. Abort works from the region step on, and
  the stop overrides every step. Video reads "Recording" or "Stopped"; the
  counts are in Diagnostics.
- **Prompts are dialogs.** New tip, new sample, new chip and new flake open a
  card dialog (Escape cancels, Return adds); dropdowns that depend on another
  refresh when it changes.
- **Settled frames only.** RGB Analysis records a frame only once the viewer
  has finished drawing it, at the rate the viewer draws. Black, stale and
  half-drawn grabs are thrown away and counted (Diagnostics shows how many),
  so they no longer reach the video, the profile or the force numbers.
- **Speeds are percent.** The stepper's and chuck's speed dials read 0-100 %
  of the device's ceiling (stepper 3200, chuck 600 steps/s), with the steps
  per second in small type underneath.
- **Red Percent is now RGB Analysis.** The red numbers are unchanged; its
  details section is "RGB analysis details".
- **The Sample Map is on by default again.** It is where a trial's sample is
  picked from (`STATION_SAMPLE_MAP=0` turns it off).
- **The browser is the only window.** The station runs in the Web view
  alone; there is one launcher, `station-web`. `--tk` and `--qt` say the old
  windows are retired and exit.
- **Branches.** `main` is the pre-release line and `legacy` is the lab's
  original Tk app; the original app's packaging ref stays `stable`.

### Removed

- The legacy tree, the archived planning notes, the frozen finding ledger,
  the design data files and the screenshots are gone from the repository
  (history: the tag `pre-root-cleanup-2026-10-07`); the bench checklist is now
  in `docs/rebuild`, and the lab's `merge_cuts.py` and `set_sample_ids.py` are
  in `dev/`.
- The live red-percent plots are no longer drawn during a trial; the profile
  is stored and drawn once at review.
- The trial's tilt is collected with every trial but no longer drawn or
  demanded.
