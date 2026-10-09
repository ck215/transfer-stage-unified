# Checkpoint — 2026-10-07, for the agent on the lab PC

Written by the lead session on the owner's Mac when work paused. Read this,
then `CLAUDE.md` (the standing rules) and `docs/rebuild/STATUS.md` (the
long form). Delete this file in the commit that closes the last item below.

## Where things stand

- **`main` is the station.** Everything from the 2026-10-07 rounds is merged
  and pushed; the GitHub gate (`.github/workflows/gate.yml`) is green on
  main. `legacy` is the lab's original Tk app, frozen. `mvc-refactor` is
  gone. `stable` is the packaging ref only.
- **Gates at this checkpoint:** fast suite 4194 on macOS / 4099 on the
  Ubuntu runner (the difference is skips), golden 77, launchers and
  packaging 139, Web launch answers `/api/setup` with 200.
- **No release is cut yet.** `station --version` prints `0.0.0+<sha>` until
  the owner runs `dev/release.sh v1.0.0 --push` on main. Do not cut it.
- **The force model is the lab's tip-shade estimator** (`src/model/tip_shade.py`,
  owner ruling 2026-10-07). The red-percent extrema are a secondary
  `factor=` only. Never lose or demote that work.

## On this machine, first

1. In the old checkout: `git status` and `git stash list`. Push or copy
   anything unpushed before deleting it. The store (sqlite + trial folders)
   lives at the operator-chosen path outside the checkout; it is not touched
   by a re-clone.
2. `git clone --branch main https://github.com/ck215/transfer-stage-unified.git`
   then `python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'` and `./run.sh`.
3. `dev/desktop_shortcuts.sh` refreshes the two desktop shortcuts.

## Open items, functional first (do these one at a time)

1. **First-trial tutorial stalls at Arm.** `src/views/web/static/tutorials/first-trial.json`
   predates the sample/chip/flake pickers and the tip prompt; Arm refuses
   without a sample, so the walk stops at step 3. Re-anchor the steps to the
   current Transfer Map phases (setup → new_tip/pickers → region → live →
   marked → End recording → finish) and remove the `xfail` on
   `tests/test_view_web_tutorial.py::test_your_first_trial_walks_a_sim_transfer_map_to_the_end`.
   Needs headless Chrome (puppeteer); the test skips without it.
2. **Bench-only checks, on this PC with the hardware** (not delegable):
   - `dev/swap_branch.sh legacy` for real: makes `../legacy-app`, its venv,
     flashes the three Megas with the legacy sketches through this tree's
     flasher (recorded in the stamp as `stable`), runs `src/mainGUI.py`.
     Then the Launcher, or `dev/swap_branch.sh station`, reflashes them.
     (2026-10-08: see STATUS "Classic and the station on the same boards".)
   - Settled-frame capture on the Mint box: Diagnostics shows the counts
     (accepted / black / stale / unsettled). Confirm the rate sits near the
     viewer's ~7 fps and that a trial's `screen.mp4`, `frames.csv` and
     `telemetry.csv` land beside the trial row.
   - ToupCam direct stream: `src/devices/camera.py` is bench-gated SIM-only
     until libtoupcam/amcam is confirmed to open the MU1003 (the vendor
     viewer must be closed; the two cannot share the device).
   - Full-display grab + x264 cost on this CPU; note hardware encoders.
3. **Re-analysis of the 40 recorded trials:** `dev/reanalyse_trials.py <db>`
   writes a report; the owner reviews it before anyone passes `--write`
   (it backs up first and touches only red extrema). Never the Drive originals.
4. **`file_open` carries no `inputs`.** The Sample DB sets instrument and
   magnification as Params before "Add photo"; a prompt on the file dialog
   itself would be cleaner. Cosmetic-functional, low.

## Cosmetic (last)

- `tests/test_view_web_server.py::test_o15_the_device_page_pins_its_head_and_tier_one`
  fails under full-suite load on macOS and passes alone (a timeout, not a
  wrong answer). Widen its budget or isolate it.
- `src/model/rgb_analysis.py` ~L1983 comment names the deleted `views/picking.py`.

## Process reminders

- Always `git --no-optional-locks …`; tests with `STATION_NO_WINDOWS=1`,
  output to a file, never to the terminal.
- Agents commit, never push, on their own worktree with an exclusive write
  set (`.claude/skills/parallel-stage`). The lead merges `--no-ff` and runs
  the gates (`.claude/skills/verify`).
- Wire bytes never change: golden stays 77.
