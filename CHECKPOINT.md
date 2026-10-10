# Checkpoint — 2026-10-10, for the agent on the lab PC

Rewritten by the lead session on the owner's Mac (first written 2026-10-07).
Read this, then `CLAUDE.md` (the standing rules) and `docs/rebuild/STATUS.md`
(the long form; its "Open items" is the full list). Delete this file in the
commit that closes the last item below.

## Where things stand

- **`main` (`a5bc7a7`) is the station, now with the XYZ Stage** (PR #1, merged
  2026-10-09), the sign-in / Dashboard / per-user stores / backups / Hard reset
  work of 2026-10-07/08, and the heater and chuck firmware guards. The GitHub
  gate is green on every push to `main` since 2026-10-08. `legacy` is the
  lab's original Tk app, frozen. `mvc-refactor` is gone; the checkout is
  `main/`. `stable` is the packaging ref only.
- **PR #2 (`fix/xyz-followups`, `60b5a8a`) is open and in flight:** the XYZ
  follow-ups and the XYZ Mega (a Mega 2560 version of the stage, firmware,
  simulator, `docs/rebuild/MEGA_STANDARD.md`). It exists only on origin and
  the owner's Mac, so a fresh clone of `main` does not have it. Its Ubuntu CI
  gate is red on one test (`test_a_hundred_mode_round_trips_leak_nothing`).
  Do not merge it from here.
- **Gates:** on the PR #2 tip, fast suite 5011 passed on macOS (2 known macOS
  serial-lock failures, 1 load flake) and 4846 passed, 183 skipped, 1 failed
  on the Ubuntu runner; golden 77 (wire bytes unchanged); Web launch answers
  `/api/setup` with 200. The old 4194 / 4099 figures are retired.
- **No release is cut yet.** `station --version` prints `0.0.0+<sha>` until
  the owner runs `dev/release.sh v1.0.0 --push` on main. Do not cut it.
- **The force model is the lab's tip-shade estimator** (`src/model/tip_shade.py`,
  owner ruling 2026-10-07). The red-percent extrema are a secondary
  `factor=` only. Never lose or demote that work.

## WARNING: the Launcher flashes firmware by itself

`main` carries two firmware changes that are **not bench-validated**: the
Temperature Controller's host-gone watchdog (`b4449c4`) and the Chuck
Positioner's runaway guards (`87a24f1`). Since `2328ac4` and `3b15c68` the
Launcher icon and Classic flash any board whose firmware differs before they
start, so **the first launch of a fresh `main` clone on this PC flashes both
sketches**. Do not take that launch casually: be at the bench with the heater
and chuck watched, and run the checks in `docs/rebuild/STATUS.md` "Open items"
(pull USB, SP=0 within 5 s; the chuck's coils free until Enable, the 250 ms
dead-man, stop in a D-pad step; the Mega bootloader may loop after a watchdog
reset). If the flash should be avoided or postponed, ask the owner first; his
decision on this (architecture audit #5) is not recorded.

## On this machine, first

1. In the old checkout: `git status` and `git stash list`. Push or copy
   anything unpushed before deleting it. The store (sqlite + trial folders)
   lives at the operator-chosen path outside the checkout; it is not touched
   by a re-clone.
2. `git clone --branch main https://github.com/ck215/transfer-stage-unified.git`
   then `python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'` and `./run.sh`
   (read the warning above first).
3. `dev/desktop_shortcuts.sh` refreshes the two desktop shortcuts.
4. Housekeeping on this PC is listed in STATUS "Open items" (the 2.3 GB old
   Sample DB copy in `data/`, the old flat `QMDL_Drive` backup, the empty
   `stores/transfer_map.sqlite`, steppers' holding torque, any `op@lab.test`
   store folders). Do not delete any of it without the owner.

## Open items, functional first (do these one at a time)

1. **First-trial tutorial stalls at Arm.** `src/views/web/static/tutorials/first-trial.json`
   predates the sample/chip/flake pickers and the tip prompt; Arm refuses
   without a sample, so the walk stops at step 3. Re-anchor the steps to the
   current Transfer Map phases (setup → new_tip/pickers → region → live →
   marked → End recording → finish) and remove the `xfail` on
   `tests/test_view_web_tutorial.py::test_your_first_trial_walks_a_sim_transfer_map_to_the_end`.
   Needs headless Chrome (puppeteer); the test skips without it.
2. **Bench-only checks, on this PC with the hardware** (not delegable; the heater and chuck validations above come first):
   - `dev/swap_branch.sh legacy` for real: makes `../legacy-app`, its venv,
     flashes the three Megas with the legacy sketches through this tree's
     flasher (recorded in the stamp as `stable`), runs `src/mainGUI.py`.
     Then the Launcher, or `dev/swap_branch.sh station`, reflashes them.
     (Fixed 2026-10-08, `0ef8771` / `55093c9`; still unverified on the boards. If a board was flashed
     before the fix, run Classic once and then the Launcher. See STATUS "Classic and the station on the same boards".)
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
  failed under full-suite load on macOS and passed alone (a timeout, not a
  wrong answer); `1790d7f` and `d18c374` may have fixed it, unverified.
- `src/model/rgb_analysis.py` ~L2011 comment names the deleted `views/picking.py`.

## Process reminders

- Always `git --no-optional-locks …`; tests with `STATION_NO_WINDOWS=1`,
  output to a file, never to the terminal.
- Agents commit, never push, on their own worktree with an exclusive write
  set (`.claude/skills/parallel-stage`). The lead merges `--no-ff` and runs
  the gates (`.claude/skills/verify`).
- Wire bytes never change: golden stays 77.
