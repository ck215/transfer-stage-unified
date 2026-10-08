# Open issues — morning of 2026-10-08

Overnight round (2026-10-07 21:00 to 2026-10-08 04:30). Everything requested
was merged to `main` and pushed; the code froze at 03:34 except for one
safety fix found by the final test run (`df392d9`). Final full suite on
`df392d9`: **4429 passed, 1 failed** (a known flake that passes alone).

Detail and evidence: `docs/rebuild/audit-architecture-2026-10-08.md`,
`docs/ux-audit-2026-10-08.md`.

## 1. Bench validation before flashing (owner)

Two firmware changes are on `main` and **not validated on the hardware**. The
startup firmware check reports the Temperature Controller and the Chuck
Positioner out of date; one "Flash now" flashes both. Press it only at the
bench, ready to run these checks.

**Heater host-gone watchdog** (`b4449c4`, `firmware/temp_controller`)
- Set a temperature, then pull the Teensy's USB (or kill the station): within
  about 5 s the board drops to SP=0 by itself.
- A normal session must never trip it (the station holds DTR while the port
  is open): run a normal heat for a few minutes and watch for drops.
- Its comments still say "PROPOSED - NOT FLASHED". Left on purpose: editing
  them re-hashes the sketch and asks for a second flash.

**Chuck runaway guards** (`87a24f1`, `firmware/chuck_firmware`)
- After power-up the coils are free until Enable (changed: they used to hold
  from boot).
- Jog every axis (stick, D-pad, bumpers) at low speed and at 600: feels
  unchanged, no stutter.
- Hold a jog and pull USB: stops within about 250 ms, coils holding.
- Hold a jog and click away from the station window: stops, holding.
- Stop during a large D-pad step: stops at once.
- Disable frees the coils.
- The watchdog-reset path is untested on the chuck's Mega bootloader (some old
  Mega bootloaders loop after a watchdog reset).

**Also worth a bench check (new tonight, unit-tested only)**
- Settings, after launch: change a device's Port, press **Apply & reset**:
  the device stops, closes, reopens on the new port.
- Hard reset on an energized device: asks once, stops it, comes back fresh.
- **Start** a device plugged in after launch (Refresh, pick its port, Start).
- Close the browser tab: Chrome asks "Leave site?"; Leave stops every device
  (heater confirmed off) and the station exits within about 8 s.
- Double-click the Launcher while a station runs: the running station's page
  opens; no second station starts.

## 2. Owner questions

1. **Settings order** is now Devices, Launch, Update/Firmware, Station
   defaults. This reverses the 2026-09-28 "Update first" ruling. Keep?
2. ~~**A store already remembered on the cloud drive** (a user setting or
   `STATION_MAP_DB`) still opens at sign-in; only the store prompt refuses
   the drive. Refuse at sign-in too, and prompt to move it?~~ **Decided**
   (owner, 2026-10-08 morning): "Store on cloud is fine, just make a local
   copy for stability of db ops." A store on the drive (New, Open, remembered,
   or `--map-db` / `--sample-db`) is now worked on through a local working
   copy that syncs back to the drive after saves and at Quit; nothing on the
   drive is refused any more, and neither copy is ever silently overwritten
   (`RECORDING_A_TRIAL.md`, "Backups").

## 3. Housekeeping (owner)

- Old Sample DB copy in `<checkout>/data/` (2.3 GB): delete once the store in
  `~/transfer-stage-runs/stores/ialbinog/` looks right (8 samples, 23 chips,
  191 flakes, 363 pictures).
- Backups now go to a per-store subfolder (`<name>-<hash>/`). The first backup
  after this update re-copies about 2.3 GB; the old flat copy in
  `QMDL_Drive/transfer-stage-dbs/ialbinog@uci.edu/` can be deleted after the
  new one is checked.
- An empty `~/transfer-stage-runs/stores/transfer_map.sqlite` (0 trials,
  created 2026-10-07 23:48, origin unclear) can be deleted.
- Steppers: check holding torque and power-cycle the boards (from the
  2026-10-07 "unconfirmed stop" incident, caused by a second station resetting
  live boards; single instance and exclusive ports now prevent it).
- The running station on port 8080 was started before tonight's changes:
  Quit it and start it again to get them.

## 4. Still open in the code

Ranked. None blocks operation or safety.

**P2**
- Guest switch vs Arm: the switch is refused while a trial is armed or being
  armed, but the check runs outside the Transfer Map's lock; an Arm pressed in
  the same instant is saved as aborted (not lost).
- The Sample DB's sample picker still reads the Transfer Map's trial file once
  per state poll for trial labels (not covered by the change counter).
- A preview whose picture file is missing: the "Shown" line names the picture
  while the frame says "No picture".
- Setup's old Trial-store commands survive with no control (partly cleaned,
  audit #15).

**P2, UX (proposals in `docs/ux-audit-2026-10-08.md`)**
- Transfer Map Setup step: Tilt and Speed readouts beside their entries
  (layout decision).
- Store prompt: the Folder entry is narrow.
- Return in a photo-path entry does nothing (a `file_open` widget).
- Phone-width ports, Settings rows and the take-over key (#19 remainder).
- Status-dot contrast and Rotator SIM false alarms (2026-10-07 audit
  proposals) not re-checked tonight.

**Tests**
- Known flakes under load (pass alone): `test_web5_a_file_open_…`,
  `test_the_slider_keyboard_…`, `test_the_rail_leads_…`,
  `test_closing_the_shown_device_…`, `test_restart_an_action_that_asks_…`.
  Worth hardening with state waits instead of sleeps.
- The `impeccable` and `web-design-guidelines` skills are not installed on the
  lab PC; tonight's design and accessibility passes used design-critique,
  accessibility-review and ux-copy. A pass with the primary skills is owed.

## 5. What landed tonight (on `main`)

- One station per computer; closing the tab quits it in an orderly way;
  exclusive serial ports; one live browser window (take over, Stop works from
  any window).
- Sign in or Guest, then Setup, then the Dashboard, with a step indicator.
  User is an account menu (with backup controls), not a device. Guest has the
  tool controls only.
- Setup becomes Settings after launch; Hard reset forces a clean restart of a
  device (on a changed port too); Start a device after launch, from Settings
  only; Relaunch removed; rail Stop appears at launch.
- "Overview" is now the Dashboard: standard tiles, Wide, drag/keyboard reorder
  kept per browser.
- Sample Map is now the Sample DB: sample, chip, flake pickers in order; chips
  required; only flakes need a picture; picture previews (100x, else 50x, else
  lower); magnification from the file name or chosen, never a silent 10x.
- Per-user stores: a user with no store is asked where to create one, never
  given another user's. Live databases stay local; automatic backups go to
  `QMDL_Drive/transfer-stage-dbs/<email>/` after saves and at Quit.
- Drive import: 191 flakes from the Drive exfoliation folders, with the owner's
  corrections (Izzie's Gift 22April25; 7/27/26 90mm).
- Heater: setpoint 0 confirmed by read-back on every close path.
- Two audits (architecture, UI/UX) with their fixes; Controller add/remove/Quit
  races closed.
