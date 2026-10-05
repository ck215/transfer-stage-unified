# Bench validation: the Rotator turns the chip, the Sample Map follows it

Written 2026-10-05 for the first bench run of `mvc-refactor` with the
rotator-frame merge. **Owner only; never delegated.** It asks for bench
values and does not answer any of them: every blank is filled at the bench.

What changed (owner ruling 2026-10-04): the SMC100 Rotator spins the chip
in-plane about a centre c. A registration records the Rotator's angle phi0;
at phi the station places the chip as p = c + R(s(phi - phi0))(p_reg - c),
with c and the sense s found by a **calibration** (one feature marked at
three angles). Nothing in the Sample Map moves the Rotator or the stage.
Code: `src/model/sample_frame.py` (`rotation_centre`, `RotatedFrame`),
`src/model/sample_map.py` (Configure Sample Map > Rotator).

## 0. Before anything moves: back up, then update

Linux Mint (the default boot), in a terminal in the station's checkout:

```
git status -sb                  # expect: ## mvc-refactor...origin/mvc-refactor, nothing modified
./update.sh --check             # lists the incoming commits; changes nothing
B=~/station-backup-2026-10-05; mkdir -p "$B"
git rev-parse HEAD > "$B/HEAD.txt"          # the commit to roll back to
cp -a data "$B/"                            # the Transfer Map store (the real trials): required
cp -a ~/transfer-stage-runs "$B/"           # run folders, logs, flashed.json (videos can be large)
./update.sh                     # fast-forward; prints "updated mvc-refactor: OLD -> NEW"
```

Windows: the same with `update.bat --check`, then copy `data\` to a backup
folder, note `git rev-parse HEAD`, then `update.bat`.

- [ ] Backup made: `data/` copied (and `~/transfer-stage-runs`), HEAD noted: ________
- [ ] If `git status -sb` names another upstream: stop, do not retarget; tell the lead.
- [ ] No dependency or firmware change is in this update (the updater says so if there is one).
- [ ] The desktop shortcut runs `run.sh` (`run_swap.sh` and `run_macos.sh` are gone).
- [ ] First launch: the Transfer Map store upgrades itself to version 6
      ("Database Upgraded ... Its trials are kept."). Additive; the trial count is unchanged.

## 1. Bench facts the Sample Map asks for (`BenchFactMissing`)

The stepper's um per count is known (0.625). The chuck's and the DC
probe's are not: marking with them is refused until typed.

- [ ] Which axes put the chip under the objective (Chuck Positioner, or a probe)? ________
- [ ] um per count of those axes (move 1000 counts against a stage micrometer slide): X ______ Y ______
      Same screw on X and Y? ______ (the turn is computed in counts and assumes X = Y)
- [ ] Typed under Sample Map > Configure Sample Map > Locating axes > um per count > **Set um per count**.
      The lead writes the measured value into `sample_frame.UM_PER_COUNT` afterwards.

## 2. One Rotator calibration run

1. On Setup, tick the Rotator (real port; there is no Rotator simulator), the
   locating axes (real ports: a SIM probe sends no positions, so its marks
   are refused as stale) and the **Sample Map** row (no port), then Launch.
   Press **Home** on the Rotator page. Its Motion state must read `Ready`, not
   `Not referenced - run Home`; until then the Sample Map's marks are greyed out
   with "Rotator angle unknown: home or reconnect it".
2. Sample Map: type the Sample ID, **Save sample**, pick the Locating axes.
3. Put the crosshair on one sharp feature (a flake edge, a scratch) that is
   **not corner A** and is well away from where the chip turns.
4. Rotator to -15 deg (Configure Rotator > Target > **Move To**), re-centre the
   feature with the stage, press **Mark calibration point**. Again at 0 and at
   +15 deg. **Stay within +/-30 deg** (tubing); if the Rotator asks to go past 30, answer no.
5. Press **Fit rotation centre**. The Rotator line reads
   `centre calibrated (circle, 3 marks, N um (good|check))`.

- [ ] Residual: ______ um, word: ______ (good < 10 um, check < 30, refused at 30 or more)
- [ ] Centre and sense from the "Rotator Calibrated" event: ______ / ______
- [ ] **It spins the chip in-plane, not a tilt.** Watching the camera while it turns:
      the feature stays in focus and travels round a fixed point; it does not slide
      along a line or drift out of focus. And the fit was accepted: a tilt is refused
      with "These marks are not a turn about one centre ... spins the chip rather
      than tilting it". Spin? ______

## 3. The Sample Map follows a turn

1. Rotator at 0. Mark corners **A** and **B** (and **D**). The Frame line reads
   `Registered (...); Rotator 0.000 deg at the marks`.
2. **Flag flake** on a flake (F01). With the crosshair on it, note the Position
   line's `chip: (x, y) um`: ______
3. Turn the Rotator to +10 deg. The Frame line adds `turned +10.000 (calibrated)`
   and stays registered. Drive the stage as **Guidance** says until it reads `On F01`.

- [ ] At `On F01` the flake is under the crosshair in the camera. Off by about ______ um.
- [ ] The Position line's chip coordinates match step 2's within about 10 um: ______
- [ ] Still at +10: go to corner A as the camera shows it, press **Check corner A**.
      Checks line: `Rotator closure: N um (word)`: ______ (good < 10 um)
- [ ] Turn back to 0: `turned` disappears and Guidance still finds F01.

A poor Rotator closure after a good calibration means the model does not
match the mounting (for example the Rotator turns the XY axes rather than
the chip, or its axis is not vertical). Stop there, note the numbers, and
tell the lead: do not record trials against that registration.

## 4. Roll back

Close the station, then in the checkout:

```
git reset --hard "$(cat ~/station-backup-2026-10-05/HEAD.txt)"
```

`data/` is not under git and is not touched. The older code opens a version 6
Transfer Map store and leaves it alone (the new columns are ignored), so
trials recorded tonight are kept. Restore the `data/` copy only if the store
itself looks wrong; that loses everything recorded after the backup.
`./update.sh` later takes the new code again.

## Owner note

The Transfer Map records the Rotator's angle as each trial's **tilt**
(`tilt_deg`). Under the 2026-10-04 ruling that angle is the chip's in-plane
turn, not a tilt. Whether to rename it is the owner's call; nothing was renamed.
