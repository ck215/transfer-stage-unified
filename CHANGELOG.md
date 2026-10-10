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
- **Samples, chips and flakes.** The Sample DB holds a sample's chips and
  each chip's flakes, with materials (hBN, graphite, MoS2 to start) and
  photos at every level; a flake needs a photo, a sample and a chip do not
  (the chip itself is always required: a flake belongs to a chip). Pick a
  sample, then one of its chips, then a flake: the Chip list is greyed until
  a sample is chosen and the Flake list until a chip is, and only the
  current level offers its New button (Clear sample / Clear chip go back).
  The photo box's hint reads "Path to a saved microscope image".
- **Every trial names its sample, chip and flake.** The trial's setup row
  has Sample, Chip and Flake dropdowns that read the Sample DB, greyed in
  the same order; Arm will not start without all three, and the cut number follows from the flake.
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
- **Sign in first, then Setup, then the Dashboard.** The station opens on a
  sign-in screen (email and password, or Guest, which has the tool controls
  only and no Transfer Map or Sample DB). Setup is full-screen and becomes
  Settings after the launch; a step strip shows where you are. The account
  menu holds your name, your backup controls and sign-out.
- **The Dashboard** (formerly "Overview"): standard tiles for every device,
  Wide or normal, reordered by drag or by keyboard and kept per browser.
- **Your own store.** The Transfer Map and the Sample DB each keep a store per
  user. A user with no store is asked where to create one, never handed
  another user's. A store on a cloud drive is worked on through a local copy
  that syncs back after saves and at Quit; neither copy is overwritten
  silently.
- **Backups.** After saves and at Quit the stores are copied to
  `QMDL_Drive/transfer-stage-dbs/<email>/<store>-<hash>/` (or the folder you
  set). A backup never goes into a store's own folder, never copies a video
  still being recorded, and an unmounted `QMDL_Drive` says it is unavailable.
- **Hard reset** on every device row: it stops the device, closes it and
  opens it fresh, on a changed port or gamepad too. It is never refused for
  being energized, and one runs at a time.
- **Start a device plugged in after the launch,** from Settings: refresh,
  pick its port, Start. Changing a port and pressing **Apply & reset** moves
  the device to it.
- **One station per computer.** Closing the browser tab asks "Leave site?"
  while anything is energized; leaving stops every device and quits the
  station. A second launch opens the running station's page instead of
  starting another. Serial ports are exclusive. One page is live; **Take
  over** moves it, and Stop works from any window.
- **The Launcher flashes first.** The Launcher icon and Classic flash any
  board whose firmware differs before they start, and never start the app
  unflashed behind held ports.
- **Picture previews** (100x, then 50x, then lower) on the Sample DB and in a
  trial's setup; a missing file falls back to the next picture and says so.
- **Tutorials for every device,** and "End tutorial" in place of a second
  Stop.
- **A local device log.** One file, `device_log.sqlite`, beside the station's
  logs records every event and a once-a-second snapshot of every open
  device, kept 14 days or 200 MB. The heater's readings are also kept with
  each trial.
- **The XYZ Stage.** The 50 mm XYZ stage, one Teensy 3.5 and TMC2209 per
  axis: the Stepper Probe's modes, vector Step and gamepad jog, per-axis Zero
  and Home, readouts in um and um/s. The row launches only when all three
  boards are found (otherwise "fault: Z missing"), and the three are flashed
  all or none. Limit switches stop the axis; a host silent for 10 s disables
  the drivers. Bench values (home speeds and direction, the 0.5 mm parked
  travel) are provisional.
- **The XYZ Stage (Mega)** (branch `fix/xyz-followups`): the same stage on
  one Mega 2560 with three TMC2209 drivers, under the lab's new Mega
  standard (`docs/rebuild/MEGA_STANDARD.md`). The Probe family learns what a
  board can do from its identity and talks to it on a new `#` channel;
  boards that predate it are unchanged on the wire.
- **The Stepper Probe in physical units.** Distances, step sizes and speeds
  are entered in um and um/s with the stored counts in small type beneath
  (the 0.625 um/count scale is unverified). A Step past the board's 16-bit
  move is refused.
- **Firmware guards.** The heater drops to a setpoint of 0 about 5 s after
  the host disappears, and reads setpoint 0 back before any close. The chuck
  has the stepper's runaway guards: coils free until Enable, a 250 ms
  jog dead-man, a stop in a D-pad step. These are on `main` and not yet
  bench-validated.
- **Ramp rate** is a tier-1 setting on the Temperature Controller, 1 to 20
  s/degC.

### Changed

- **The Sample Map is now called the Sample DB** everywhere it is shown.
  Settings saved under the old name still apply.

- **Swap to the original app.** `dev/swap_branch.sh legacy` flashes the boards and runs the lab's original Tk app from a `legacy-app` folder beside this one (made on first use); `station` swaps back.
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
- **The Sample DB is on by default again.** It is where a trial's sample is
  picked from (`STATION_SAMPLE_MAP=0` turns it off).
- **The browser is the only window.** The station runs in the Web view
  alone; there is one launcher, `station-web`. `--tk` and `--qt` say the old
  windows are retired and exit.
- **Branches.** `main` is the pre-release line and `legacy` is the lab's
  original Tk app; the original app's packaging ref stays `stable`.
- **Settings order:** Devices and Launch first, then Update and Firmware,
  then the station defaults; the Update and Flash keys are ink, not coloured.
  At phone width a row's port reads whole.
- **Status dots** are shapes as well as colours: an error is a diamond.
- **Copy.** Real plurals ("1 device detected"), "trial store" throughout,
  the link-lost words say Hard reset it in Settings, Return in a text entry
  presses the key that takes it, and the skip link reads "Skip to the main
  content". A photo's file name gives its magnification; nothing is ever
  silently 10x.
- **Transfer Map Setup** shows Tilt and Speed ("Now:") under the entries that
  override them. A prompt opened in a tile no longer grows without end, and
  focus moves to the next step's first control when a prompt closes.
- **The Rotator in simulation** shows no stale badge or red dot, and its Stop
  confirms.
- **Probe boards' ordinary text** is logged as "Board Says", not counted as a
  garbled packet.
- **A vector Step on the XYZ Stage** scales each axis's acceleration too, so
  the axes arrive together.

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

### Fixed

- A Quit waits for the store backup only after every device has closed, in
  one 15 s budget; closing a device while a Hard reset or Quit runs can no
  longer orphan it.
- An Arm pressed while a user is being switched is refused; a stop during
  Arm's stage still always wins (no "Stage Taken" with nothing armed).
- Typing in an entry while the station polls no longer flips the box back to
  the old value.
- A Classic launch records what it flashed, so the Launcher no longer reads a
  board flashed by Classic as current.
- The wire is unchanged for every existing board (golden 77).
