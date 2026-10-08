# Architecture audit after the 2026-10-08 night merges

Scope: `main` at `129f76d` (the ~10 feature branches merged 2026-10-07 22:13 to
2026-10-08 00:53, `619ba9e..129f76d`). Read-only review of the layer rules, the
model contract, the safety paths, dead code, docs and data safety, with small
fixes landed on branch `worktree-agent-ad682c84f293001fa`. Rankings: **P0**
safety, **P1** correctness or data safety, **P2** structure, **P3** docs and
hygiene. "Fixed" names the commit; everything else is documented only
(behavioural, debatable, in a core file, or the lead's or owner's call).

Line numbers are as of `129f76d` unless a fix moved them.

## Summary

No P0 found. Every new close/reopen path runs the stop first, and the heater's
off read-back runs on every close (Hard reset, Guest switch, tab-close quit,
Quit, restart). Nothing found leaves a device energized without its model. The
layer rules hold (`tests/test_architecture.py` passes and a manual import scan
agrees). The damage the fast merges left is in **concurrency at the edges**
(two Hard resets at once, the backup wait inside the close loop), **backup
data safety** (a backup folder that is the store's own folder, a folder set on
an unmounted drive), the **picture preview's cost on every state poll**, a
**firmware change that the startup flash prompt will offer unvalidated**, and
**docs and comments** that still describe the pre-merge design.

| # | Rank | Finding | Status |
|---|---|---|---|
| 1 | P1 | Two concurrent Hard resets can orphan an open model | Fixed `cfee241` |
| 2 | P1 | A backup folder that is a store's own folder replaces the live database | Fixed `c7e2c51` |
| 3 | P1 | A backup folder set under an unmounted `~/QMDL_Drive` is written into the bare mountpoint | Fixed `c7e2c51` |
| 4 | P1 | The Quit backup wait (up to 20 s) sits inside the Controller's close loop | Documented |
| 5 | P1 | Changed firmware (chuck, temperature controller) is offered by the flash prompt before bench validation | Documented (owner) |
| 6 | P1 | Backups of stores with the same file name overwrite each other | Fixed `04a6f97` |
| 7 | P1 | A live store may be chosen on the rclone drive | Fixed `ffe5ab3` |
| 8 | P2 | The picture preview opens SQLite 12 times per Transfer Map state poll (48/s), in every phase | Documented |
| 9 | P2 | `Controller.add` does not refuse after `close()` began; `remove` drops before it stops | Documented (core) |
| 10 | P2 | `/api/open_model` and `/api/close_model` kept for tests bypass Setup's rules | Fixed `f88942f` |
| 11 | P2 | Guest session gating is enforced in the Web adapter, not below it | Fixed `1740189` |
| 12 | P2 | The Transfer Map duplicates `store_choice`'s install-folder rules | Fixed `02a264d` |
| 13 | P2 | Hard reset is not refused during a scan; Guest switch races an Arm | Guest half fixed `36e5556`; Hard reset documented |
| 14 | P2 | Back up now / the close backup copy a trial video still being recorded | Fixed `f513104` |
| 15 | P2 | Setup's A3 Trial-store commands survive with no control | Partly fixed `ffa978b` |
| 16 | P3 | `Setup._store_section` dead, stale comments in `setup.py` | Fixed `ffa978b` |
| 17 | P3 | "Export/Import sample map" in operator text | Fixed `ffa978b` |
| 18 | P3 | `isinstance(model, User)` filters that can no longer match | Fixed `c706f3c` |
| 19 | P3 | MODEL_CONTRACT, STATUS, DESIGN_BRIEF, RECORDING_A_TRIAL, `user_store` docstring drift | Fixed `cc61d1d` |
| 20 | P3 | CLAUDE.md / `station-map` / `heater.py` say "firmware untouched" / "no watchdog" | Documented (lead) |
| 21 | P3 | "Overview" remains in code comments and CSS | Fixed `f1bed83` |

## P1: correctness and data safety

### 1. Two concurrent Hard resets can orphan an open model (fixed `cfee241`)

- **Evidence.** Setup commands are not serialised: the Web server is a
  threading server and `_run` calls `self.view.setup.run(...)` directly
  (`src/views/web/server.py:487-488`). `Controller.add`
  (`src/controller/controller.py:46-60`) checks the name under the lock, opens
  the model with the lock released, then inserts it. `Setup._hard_reset`
  (`src/controller/setup.py:1254-1330`) does `controller.remove(name)` then
  `reopen`/`add`.
- **Impact.** Two confirmed presses (a second press after a reset that seemed
  stuck, which the refusal text invites) can both pass the check and both open
  the model; the second overwrites the first in `_models`. The first keeps its
  port and threads and is unreachable by `estop_all`. A freshly opened model is
  disabled, so this leaks a port and threads rather than energizing a coil, but
  the next reset then fails on a busy port (exclusive open).
- **Fix landed.** A non-blocking `Setup._reset_lock` around the close/reopen;
  the second press is refused ("A hard reset is already running..."). Test
  `test_a_second_hard_reset_while_one_runs_is_refused` failed before the fix.
- **Still open (core, the lead's).** The root cause is `Controller.add`
  opening outside a name reservation; reserving the name before `open()` would
  cover every caller (Setup's launch, sign-in, `/api/open_model`).

### 2. A backup folder that is a store's own folder replaces the live database (fixed `c7e2c51`)

- **Evidence.** `Setup.set_backup_dir` (`setup.py:2900-2919`) refused only a
  relative path and the install folder. `BackupService._copy_db`
  (`src/controller/backup.py:257-280`) renames the snapshot to `folder / db.name`,
  which is the live database when the folder is the store's own; `_mirror`
  then copies the side folders onto themselves.
- **Impact.** The live file's inode changes under the running station; a write
  that lands between the snapshot and the rename is lost. Easy to do: the store
  prompt suggests `~/transfer-stage-runs/stores/<email>/` and an operator may
  type the same folder as the backup folder.
- **Fix landed.** `backup._clash` refuses a job whose folder is a store's own
  folder or inside a folder it mirrors, before anything is touched (one "Backup
  Failed" warning). Test `test_a_backup_folder_that_is_a_stores_own_folder_is_refused`
  failed before the fix. Setup could additionally refuse it when the folder is
  set (nice-to-have; needs the open stores' folders).

### 3. A backup folder set under an unmounted `~/QMDL_Drive` is written locally (fixed `c7e2c51`)

- **Evidence.** Only the default target had `anchor=drive`
  (`backup.py:109-116` at `129f76d`); a user's `backup_dir` or
  `STATION_BACKUP_DIR` became a `Target` with no anchor and `ready()` did
  `mkdir(parents=True)` (`backup.py:89`).
- **Impact.** With rclone not running, a folder typed as
  `~/QMDL_Drive/whatever` was created inside the bare mountpoint; the files hid
  under the drive once it mounted, exactly what the 2026-10-08 guard exists to
  stop.
- **Fix landed.** `backup._on_drive` anchors any set folder that lies under
  `~/QMDL_Drive` (lexical test; nothing touched on the caller's thread). Test
  `test_a_folder_set_on_the_unmounted_drive_is_unavailable_too` failed before
  the fix. A `STATION_BACKUP_DIR` elsewhere on a dropped mount is still made
  as typed (would need a "must exist" rule; documented).

### 4. The Quit backup wait sits inside the Controller's close loop (documented)

- **Evidence.** `Controller._close_models` (`controller.py:136-156`) closes the
  models one at a time and notifies `"removed"` after each.
  `Setup._on_models_changed` (`setup.py:2864-2886`) then blocks up to
  `BackupService.QUIT_WAIT_S` = 20 s (`backup.py:150`) per store model while
  `controller._closed` is set, before the next model closes. A Hard reset
  re-adds its device last, so after any reset the heater can close after the
  maps.
- **Impact.** `estop_all` at the start of the close has already sent the
  heater's off frame, so nothing is left energized; what is delayed (up to 40 s
  with both maps on a hung mount) is the heater's off **read-back** and the port
  release. A SIGTERM second `close()` waits only `CLOSE_WAIT` = 30 s
  (`controller.py:29`, `:119`) then re-raises, so the process can end with the
  heater's teardown unfinished, the hazard the `close()` docstring describes.
- **Recommended fix.** Wait for the final backup after `controller.close()`
  returns (in `app.py` / the view's close), or close hardware models before
  non-hardware ones in `_close_models`. Behavioural, in a core path: the lead's.

### 5. Changed firmware is offered by the flash prompt before bench validation (documented; owner)

- **Evidence.** `79a9008` changed `firmware/chuck_firmware/chuck_firmware.ino`
  ("NOT FLASHED, needs owner bench check") and `f53ce9c`
  `firmware/temp_controller/temp_controller.ino` (line 39 still reads
  "PROPOSED 2026-10-07 - NOT FLASHED, NEEDS BENCH REVIEW AND FLASHING" although
  the merge `b4449c4` says "owner approved for flashing"). Setup's firmware
  check (`src/controller/firmware.py`) compares each board's stamped sketch hash
  with the sketch in the install, so on the lab station both boards now read
  **out of date** and the startup dialog's **Flash now** (one press) flashes
  them.
- **Impact.** A station updated to this `main` invites flashing the chuck's
  not-bench-validated runaway guards with a single press. Host bytes are
  unchanged (golden 77 passes), so the host works with either firmware.
- **Recommended action.** Owner decision: either keep these sketches off
  `main` until bench-checked, or say in the Firmware row which boards carry
  unvalidated changes. Update the temperature-controller banner to match the
  approval.

### 6. Backups of stores with the same file name overwrite each other (fixed `04a6f97`)

- **Evidence.** One flat folder per user (`backup.py:110-116`); the database is
  written to `folder / db.name` (`backup.py:265`), manifest key `"db:" + db.name`
  (`backup.py:260`).
- **Impact.** Two Transfer Map stores both named `transfer_map` (the default
  name, `transfer_map.py:1104`) in different folders, or a Sample DB named like
  a trial store, back up over each other. The local stores are intact; the
  backup copy of one is silently lost.
- **Recommended fix.** A subfolder per store in the backup (by store folder
  name or a hash of its path), or refuse the clash. Changes the restore steps
  in `RECORDING_A_TRIAL.md`. Behavioural.
- **Fix landed.** Each store backs up into its own subfolder
  `<name>-<8 hex of the SHA-1 of its full path>/` (`backup.store_subfolder`),
  mirroring its folder there. Nothing is migrated: a flat copy an earlier
  version wrote stays where it is; `RECORDING_A_TRIAL.md` "Backups" says how
  to restore from either. A store moved to another folder starts a new
  subfolder. Test `test_stores_with_the_same_name_never_share_a_backup`
  failed before the fix.

### 7. A live store may be chosen on the rclone drive (fixed `ffe5ab3`)

- **Evidence.** The store prompt refuses only the install folder
  (`store_choice.py:237-241`, `transfer_map.py:1073-1076`,
  `sample_map.py:359/391/406`); nothing refuses `~/QMDL_Drive/...`.
- **Impact.** `backup.py`'s promise that SQLite never runs on the drive holds
  for the backup only: a store opened there runs live SQLite locking over FUSE,
  and with the drive unmounted `SampleStore.write` recreates the folder
  locally (`sample_store.py:342`).
- **Recommended fix.** Refuse store paths under `~/QMDL_Drive` (or any path
  whose mount is not the local disk) in `StorePrompt` and the Transfer Map's
  copy. It changes what an operator may choose: the owner's call.
- **Fix landed (lead's brief).** `store_choice.remote_reason` refuses New
  store / Open store (and the Sample DB's legacy copy) under `~/QMDL_Drive`
  by path alone, and on any `fuse.*` or network mount (`/proc/mounts`,
  longest mountpoint; `fuseblk`, a local NTFS disk, is allowed), with a
  sentence saying why. Without a mount table only the path rule holds.
  Tests `test_a_store_under_the_drive_folder_is_refused_even_unmounted`,
  `test_a_store_on_a_fuse_or_network_mount_is_refused`,
  `test_both_maps_refuse_new_and_open_store_on_the_drive` failed before.
  **Still open:** a store already remembered on the drive (a user's
  `map_store`/`sample_store` setting, `STATION_MAP_DB`) still opens at
  sign-in; only the prompt refuses.

## P2: structure

### 8. The picture preview's cost on every state poll (documented)

- **Evidence.** `Panel.state` (`src/panel.py:86-100`) reads every `model_attr`
  in the schema on every poll, whatever the phase. The Transfer Map's
  `preview_key`, `preview_text` and `preview_magnification`
  (`transfer_map.py:3302-3330`) each call `_preview_store_rows`
  (`:3278-3294`), which opens the Sample DB read-only and runs `images()` up to
  twice, each with a `PRAGMA table_info` (`sample_store.py:427-432`, `890-915`).
  Measured on a scratch store with a sample, chip and flake picked: **12
  `sqlite3.connect` per `TransferMap.state`, all from the three preview
  readouts**; the Web polls state at 4 Hz (`app.js:19`), so ~48 connects/s,
  including during a live recording. The Sample DB's own preview readouts
  (`sample_map.py:1906-1956`) do the same against its store.
- **Impact.** Disk and lock traffic beside the recorder and telemetry threads
  during a trial; on a slow or remote store (item 7) a stalled state poll.
- **Recommended fix.** Compute the rows once per state (or cache them keyed
  by store path and level for a poll interval), and read nothing outside the
  phases that show the preview. The contract doc now says a still's key must be
  cheap.

### 9. `Controller.add` after `close()`; `remove` drops before it stops (documented; core)

- **Evidence.** `add` (`controller.py:46-60`) never checks `_closed`; a Hard
  reset or sign-in that lands during a tab-close quit adds a model nobody
  closes. `remove` (`controller.py:68-82`) pops the model and notifies before
  `estop()`/`close()`, so a FULL STOP during a heater's 0.6-4.5 s close does not
  include it (older than tonight, but Hard reset and the Guest switch now use
  `remove` on live hardware).
- **Recommended fix.** Refuse `add` once closing; stop before dropping, or keep
  the model in a "closing" set that `estop_all` also reaches. Core file.

### 10. Test-only routes bypass Setup's rules (fixed `f88942f`)

- **Evidence.** `/api/close_model` and `/api/open_model`
  (`server.py:355-376`) are kept "for the browser tests". `open_model` calls
  `controller.reopen` directly: past the no-relaunch rule and the new
  `_reset_lock` (the Guest rule still holds: `model_from_config` refuses).
- **Recommended fix.** Gate them behind a test-only environment switch, or
  route `open_model` through Setup's Hard reset.
- **Fix landed.** Both answer "no route" (404) unless
  `STATION_TEST_ROUTES=1`; the conftest fixture `test_routes` sets it for
  the six tests that use them (conftest clears it otherwise). Test
  `test_the_close_and_open_routes_are_test_only` failed before.

### 11. Guest gating lives in the Web adapter (fixed `1740189`)

- **Evidence.** `WebView._run` asks `setup.session_refusal(name, command)`
  (`server.py:493-501`) before `controller.run`; the Controller itself has no
  such check. Setup also refuses to build a map for a Guest
  (`model_from_config`, `setup.py:1942-1945`) and removes the maps at a switch
  to Guest, so this is a second guard, but "hiding is never the only guard"
  holds only for the Web route.
- **Recommended fix.** Move the per-command refusal into Setup's hold on the
  Controller (or a Controller hook), so any frontend inherits it.
- **Fix landed.** `Controller.command_gate(name, command)`, asked by
  `Controller.run` (and so `set_value`) for every non-stop command; Setup
  installs `session_refusal` at construction. A stop never reaches the gate
  (`toggle_estop`/`estop`) or is passed by it (`UNGATED_COMMANDS`); a gate
  that raises refuses. `WebView._run` keeps its check as a second layer.
  Test `test_the_controller_itself_refuses_a_guest_the_maps_commands`
  failed before. (`controller.py` is a core file: the lead's review.)

### 12. The Transfer Map duplicates `store_choice` (fixed `02a264d`)

- **Evidence.** `transfer_map.py:136-150` (`_install_root`, `_inside`),
  `:1022`, `:1073-1076` (`_refuse_inside_install` overriding the mixin's) copy
  `store_choice.install_root`, `inside` and `StorePrompt._refuse_inside_install`;
  the Sample DB uses the shared ones. Tests monkeypatch
  `transfer_map._install_root`, which is why the copy survives.
- **Recommended fix.** Point the Transfer Map at `store_choice` and move the
  tests' monkeypatch to `store_choice.install_root`.
- **Fix landed.** `_install_root`, `_inside`, `INSIDE_INSTALL` and the
  `_refuse_inside_install` override are gone; `TransferMap.install_root`
  returns `store_choice.install_root()` and the mixin's rule (same words)
  applies. The four fixtures now patch `store_choice.install_root` (the
  install-folder tests failed against the copy, then passed). The
  Transfer Map's `_SQLITE_MAGIC` read in `open_store` still duplicates
  `store_choice.is_sqlite` (left: its error wording differs).

### 13. Hard reset during a scan; Guest switch versus Arm (Guest half fixed `36e5556`)

- Hard reset is not refused while a scan runs (`setup.py:1066-1068`,
  `1096-1097`); a Refresh in the reset window can probe the port being
  reopened. Exclusive open makes one side "busy"; nothing is energized.
- The Guest switch checks for an open trial outside any lock
  (`setup.py:1826-1848`); an Arm that lands between is aborted and saved as
  aborted. A switch during the region step discards the Arm still, because
  `is_active` (`transfer_map.py:1219`) counts only an armed trial.
  **Fixed `36e5556`:** `TransferMap.is_arming` (the region step or an Arm
  in flight) counts in Setup's busy check, so the Guest switch (and a
  sign-in as someone else) refuses while a trial is being armed, as it does
  for an open trial. Test:
  `test_setup_profile.py::test_switching_to_guest_while_a_trial_is_being_armed_is_refused`.
  The check still runs outside the map's lock: an Arm pressed in the
  instant between the check and the removal is aborted and saved as aborted
  (nothing energized, nothing lost silently). The Hard reset half is open.

### 14. Backups copy a video still being recorded (fixed `f513104`)

- Saves skip the backup while a recording runs (`transfer_map.py:3646-3651`),
  but Back up now (`setup.py:2931-2942`) and the backup when a store model is
  removed (`setup.py:2877-2880`) do not. A partial video lands in the backup
  until a later run replaces it; disk contention with the recorder.
- Fix: `backup_sources` returns only the database while a trial is open.
- **Fix landed.** That, from the region step to Finish/Abort; and
  `BackupService` skips any mirrored file modified in the last `SETTLE_S`
  (5 s), leaving it out of the manifest so the next run copies it. Tests
  `test_the_backup_takes_only_the_database_while_a_trial_records` and
  `test_a_file_still_being_written_waits_for_the_next_backup` failed before.

### 15. Setup's A3 Trial-store commands survive with no control (partly fixed `ffa978b`)

- `Setup.map_store_status`, `open_map_store`, `new_map_store`, `_map_target`
  and the `map_store_*` Params (`setup.py:2688-2725`, `594-603`) back a "Trial
  store" row that `_build_schema` never drew; both maps now choose their store
  on their own `new_store` prompt. The never-called builder `_store_section`
  is removed (with its shape test, replaced by one pinning the row's absence);
  the commands stay because `tests/test_setup.py` and
  `tests/test_setup_profile.py` exercise the per-user store choice through
  them. Recommended: move those tests onto the maps' own commands, then delete
  the A3 block.

## P3: docs and hygiene

- **16 (fixed `ffa978b`).** `Setup._store_section` removed; the "Sample DB and
  accounts are OFF until validated" comment (`setup.py:229-233`) and a dangling
  `backup_dir` comment inside `Setup.PARAMS` (`setup.py:601-602`) corrected;
  `tests/test_model_contract.py:76` no longer calls the Sample DB a "held
  feature".
- **17 (fixed `ffa978b`).** The Sample DB's Data section said "Export sample
  map" / "Import sample map" (`sample_map.py:2374-2376`): now "Export Sample DB
  (JSON)" / "Import Sample DB (JSON)". No operator-visible "Sample Map" or
  "Overview" string remains in `src/` (both survive in comments only, and in
  `profile.RENAMED_MODELS` and `tutorial.js:178` on purpose).
- **18 (fixed `c706f3c`).** `isinstance(model, User)` filters (`setup.py:1901`, `user.py:186`,
  `user.py:245`) can no longer match: a User is never among the Controller's
  models. Removed.
- **19 (fixed `cc61d1d`).** `MODEL_CONTRACT.md` called User "a non-hardware
  model", listed the Transfer Map's `PHASES` without `new_store`, and did not
  describe the `StorePrompt` mixin or an image's `model_attr` still. STATUS had
  no entry for the night's merges and still said "the User model" and "Setup's
  Account section". DESIGN_BRIEF put the secret entries in Setup's Account
  section. RECORDING_A_TRIAL said deleting `.backup-manifest.json` forces a
  full re-copy (it takes effect only at the next start: the manifest is cached
  in memory, `backup.py:323-331`). `user_store.py:20` said "settings: nothing
  reads it yet" although `map_store`, `sample_store` and `backup_dir` are read
  and written. All corrected.
- **20 (the lead's files).** CLAUDE.md ("Firmware is untouched") and the
  `station-map` skill ("Firmware untouched"; phases lists without `new_store`;
  "User (`user.py`...)" among the Models) are stale after `79a9008` and
  `f53ce9c`. `heater.py:437` ("The firmware has no watchdog") is true until the
  proposed host-gone watchdog is flashed; reword once it is.
- **21 (fixed `f1bed83`).** "Overview" remains in ~25 code comments and CSS comments
  (`app.js:2114`, `:2475`, `:3410`, `styles.css:945-1210` and others) next to
  the rename note at `app.js:25`. Not operator-visible. Now "Dashboard" in
  app.js and styles.css; the rename note, the "K4's Overview" cross-references,
  the internal identifiers (`overview-link`, `is-overview`), `tutorial.js:178`
  and the frozen Tk/Qt views keep the old name.

## Checked and sound

- **Layers.** No view imports `model`/`devices`; no model, device or
  `panel.py` imports `views`/`controller`; one owner per hardware library.
  `model/store_choice.py` imports only the standard library, `events` and
  `result` and is shared by two models, so `model/` is right;
  `controller/backup.py` imports it (controller -> model is allowed) and no
  model imports the backup. `controller/single_instance.py` is process
  infrastructure used by `app.py` only. The thumbnail code
  (`sample_store.thumbnail_png`, PIL) is not a hardware library and is cached.
  The Web view reaches the User only through `setup.user` and the reserved
  name `__user__`.
- **User as a Panel.** It is in no device aggregate by construction; its
  secrets are `SECRET_INPUTS`, read back as "" and redacted. The contract test
  rightly does not cover it (it covers `MODEL_TYPES` only).
- **StorePrompt.** Both maps put `new_store` in `PHASES`, return it from
  `phase` while no store is chosen or `_choosing_store` is set, and keep the
  Safety section and every stop control unphased; the contract test's phase
  rules pass for both.
- **Safety paths.** Hard reset refuses while energized or active, re-checks on
  the confirmed run, and closes through `Controller.remove` (estop, then close)
  before building on the new port; `Model.close` never raises half-way. The
  heater's `disable` sends off, flushes and reads the setpoint back on every
  close path. Tab-close quit and the silence backstop call `estop_all` first;
  the leave grace (8 s) is shorter than the 15 s silence FULL STOP;
  `/api/estop_all` works from any tab. A second launch only opens the running
  station's page; the instance lock is released after the view's close.
  Exclusive serial holds are taken after open and released before close; a
  busy port fails without raising. The chuck's firmware deadman (250 ms) is
  well above the host's 50 Hz jog packets.
- **Persistence.** `map_store`, `sample_store` and `backup_dir` are written and
  read per user; a Guest's store choice goes to `station.json`; no migration
  is needed. Database copies are online-backup snapshots into the system temp
  folder, copied to a hidden part file and renamed; SQLite never opens a file
  in the backup folder. The default drive's mount check runs on every backup
  path; requests coalesce; one warning per failure streak; the backup thread
  cannot die.

## Follow-up round (2026-10-08 night, items 6, 7, 10, 11, 12, 14)

Branch `worktree-agent-a41acf06b4b34cd56` from `0361333`, one commit per
item, each with a test that failed first. Items 4, 5, 8, 9, 13, 15 (rest)
and the P3 rows not marked fixed are unchanged by this round.

## Tests run (this branch)

Environment: `STATION_NO_WINDOWS=1 QT_QPA_PLATFORM=offscreen
STATION_NO_UPDATE_CHECK=1 TRANSFER_STAGE_DATA_ROOT=$(mktemp -d)`, `-m "not qt"`.

- `tests/test_setup.py tests/test_model_contract.py tests/test_sample_map.py
  tests/test_architecture.py tests/test_setup_registry.py`: 776 passed, 1
  xfailed (after `ffa978b`).
- `tests/test_store_backup.py tests/test_setup_profile.py tests/test_user.py`:
  69 passed; `tests/test_view_web_account_menu.py`: 4 passed, 4 skipped
  (after `c7e2c51`; the two new tests failed before it).
- `tests/test_user_store.py tests/test_architecture.py`: 180 passed.
- `tests/test_setup.py tests/test_setup_profile.py tests/test_setup_registry.py
  tests/test_setup_identify.py`: 263 passed; `tests/test_heater_off_confirm.py
  tests/test_view_web_client.py`: 115 passed, 33 skipped (after `cfee241`; the
  new test failed before it).
- Final, on the branch head: `tests/test_setup.py tests/test_model_contract.py
  tests/test_sample_map.py tests/test_store_backup.py tests/test_user_store.py
  tests/test_architecture.py tests/test_setup_profile.py
  tests/test_transfer_map.py`: 1103 passed, 1 skipped, 1 xfailed.
- Golden `tests/test_wire_golden.py`: 77 passed.
- The full fast gate and a launch were not run (the lead's gate before merge).

Follow-up round (items 6, 7, 10, 11, 12, 14), same environment:

- On `02a264d`: `tests/test_transfer_map.py tests/test_setup.py
  tests/test_setup_profile.py tests/test_store_isolation.py
  tests/test_store_backup.py tests/test_sample_map.py
  tests/test_architecture.py tests/test_model_contract.py
  tests/test_wire_golden.py` (golden 77 included): 1188 passed, 1 skipped,
  1 xfailed.
- After `1740189`: `tests/test_setup_profile.py tests/test_store_isolation.py
  tests/test_core_controller.py tests/test_architecture.py
  tests/test_user.py`: 309 passed; `tests/test_setup.py
  tests/test_setup_identify.py tests/test_setup_registry.py
  tests/test_view_web_server.py`: 306 passed, 77 skipped (browser).
- Browser (headless Chrome), only what uses the changed routes and backup:
  the three `/api/close_model`/`open_model` server tests, the new 404 test
  and the three dashboard tests that drive them (`test_d_closing_the_host...`,
  `test_w1_...`, `test_sp3_..._returns_there`): 7 passed;
  `tests/test_view_web_account_menu.py`: 8 passed.
