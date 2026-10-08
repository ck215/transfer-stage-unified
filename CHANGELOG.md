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

### Changed

- **Swap to the original app.** `dev/swap_branch.sh legacy` flashes the boards and runs the lab's original Tk app from a `legacy-app` folder beside this one (made on first use); `station` swaps back.
- **Settled frames only.** Red Percent records a frame only once the viewer
  has finished drawing it, at the rate the viewer draws. Black, stale and
  half-drawn grabs are thrown away and counted (Diagnostics shows how many),
  so they no longer reach the video, the profile or the force numbers.
- **A trial is a procedure.** The Transfer Map walks through setup, region,
  live, marked and finish, shown as a step strip. At Arm a full-resolution
  picture of the whole display is taken and the capture region is drawn on
  it; nothing records until the trial is live. Abort works from the region
  step on, and the stop overrides every step.
- **The browser is the only window.** The station runs in the Web view
  alone; there is one launcher, `station-web`. `--tk` and `--qt` say the old
  windows are retired and exit.
- **Red Percent is now RGB Analysis.** Beside the red share it measures the
  green and blue shares and the region's mean red, green and blue with every
  settled sample (under its details). The red numbers are unchanged.

### Added

- **The whole display is recorded.** Every trial keeps a full-screen video
  at the display's own resolution (`screen.mp4`, which survives a crash),
  the time of every frame (`frames.csv`), every device's readings on one
  clock (`telemetry.csv`) and a full-resolution still at Arm and at Mark.
  Record everything, trim in analysis.
- **Samples, chips and flakes.** The Sample Map holds a sample's chips and
  each chip's flakes, with materials and photos at every level. Pick a
  sample, then one of its chips, then a flake; each list follows the one
  before it, and each level asks only for what it needs.
