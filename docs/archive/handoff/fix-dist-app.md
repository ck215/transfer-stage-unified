# fix-dist-app — rb-dist-app (base fb5a045), head c224861 (2026-09-30)

Commits: 1c253c7 (A1), dba25d5 (A2), 8f1f0e3 (A3), ba30238 (A4), f0394b1 (A5), c224861 (A6; its
subject says "A1 follow-up", written before the lead named it A6; not amended, by rule). Not pushed.
Every file touched is in the brief's write set. `firmware/flash_firmware.py` is the one
file under `firmware/` touched (the brief's explicit exception); no sketch or library changed.

## A1
STATUS: closed with A6 (the contract gap below was settled by agent E's packaging/layout.py + tools.py; A6 aligns)
TEST: test_the_board_table_matches_the_sketch_directories_on_disk, test_the_chips_are_three_megas_and_one_teensy,
test_a_port_answering_dev_s_is_the_stepper_probe[COM7], test_a_port_answering_dev_s_is_the_stepper_probe[/dev/ttyACM0],
test_detection_skips_nothing_but_sim_and_names_what_it_found, test_a_port_whose_handshake_raises_is_one_line_not_a_crash,
test_setup_is_a_port_probe, test_a_checkout_flashes_with_the_tools_on_path,
test_a_frozen_bundle_flashes_with_its_own_tools_and_data_dir, test_a_bundle_config_file_is_passed_explicitly_when_it_ships_one,
test_a_frozen_bundle_without_its_tools_names_them_missing, test_the_stamp_is_written_with_the_channel_and_the_version,
test_a_failed_upload_records_nothing_and_fails, test_a_current_board_opens_no_port_and_runs_nothing,
test_a_board_not_plugged_in_is_said_and_is_not_a_failure, test_missing_tools_refuse_before_any_port,
test_a_runner_that_raises_fails_that_board, test_a_runner_that_times_out_fails_that_board,
test_the_default_runner_passes_the_environment, test_the_command_line_imports_nothing_from_legacy,
test_the_command_line_keeps_its_flags, test_the_command_lines_dry_run_prints_the_commands_and_records_nothing
(tests/test_flashing.py); test_flash_runs_in_this_process_never_through_sys_executable,
test_a_failed_flash_is_not_ok_and_keeps_the_last_line, test_a_runner_that_raises_is_a_failed_flash_not_an_exception,
test_a_detection_that_raises_is_a_failed_flash_not_an_exception, test_a_board_not_plugged_in_says_not_connected
(tests/test_firmware_check.py)
COMMIT: 1c253c7
PROVED: base `firmware/flash_firmware.py` detect_devices() with discover_ports -> ["COM7", "/dev/ttyACM0"]
(a scratch script, the base file importing legacy/src) returned {'DC Probe': '/dev/ttyACM0'}: the Stepper Probe
on COM7 was never probed (OP-12). New tests against base: test_flashing.py cannot import (no flashing module);
test_firmware_check.py 5 failed (the in-process flash tests: base spawned `sys.executable -u flash_firmware.py`).
NOTE: `src/controller/flashing.py` owns the board table, the hash rule, the stamp (entries gain `"channel"` and
`"version"`), tool lookup, detection and the flash; one runner `run(argv, cwd, on_line, timeout, env=None)`.
Detection is Setup's own handshake: `identify`, `scan_ports` and helpers moved verbatim into a new mixin
`controller.setup.PortProbe`, `class Setup(PortProbe, Panel)` (no behaviour change; test_setup_identify and
test_setup_registry unchanged and green). `FirmwareCheck.flash` calls it in-process. The CLI keeps every flag and
adds `--channel station|stable`; with `--list` or detection it imports `controller.setup` (the whole model
stack), so `dev/swap_branch.sh`'s `python3 firmware/flash_firmware.py` needs the venv's python, not a bare python3.
**Contract gap (was why partly; superseded by A6):** frozen, every arduino-cli call runs with ARDUINO_DIRECTORIES_DATA =
ARDUINO_DIRECTORIES_USER = `tools/arduino-data`, DOWNLOADS = `tools/arduino-data/staging`, and
`--config-file tools/arduino-data/arduino-cli.yaml` only if that file exists. The contract says the cores AND the
sketch libraries are pre-installed in `tools/arduino-data/` but not where the libraries sit: arduino-cli looks for
them under the USER directory's `libraries/`, i.e. `tools/arduino-data/libraries/` with the settings above. Agent E
must install with those same three environment values (or the lead names another layout and I change
`flashing.Tools.env`). The vendored `firmware/libraries/` is not passed with `--libraries` (the old script never did).

## A2
STATUS: closed
TEST: test_a_frozen_bundle_checks_the_sketches_beside_its_launchers, test_a_frozen_bundle_uses_its_own_tools_not_the_path,
test_a_frozen_bundles_flash_is_stamped_with_its_version, test_a_checkout_still_checks_its_own_firmware
(test_firmware_check.py); test_a_view_that_subscribes_after_setup_is_built_still_gets_the_offer,
test_the_offer_waits_for_a_check_still_running_when_the_view_listens, test_nothing_is_offered_before_the_view_listens,
test_on_next_read_the_offer_goes_out_with_the_first_state_read (test_setup.py);
test_a_desktop_views_subscription_starts_the_startup_offer[tk], test_a_desktop_views_subscription_starts_the_startup_offer[qt],
test_the_web_offers_at_the_pages_first_read, test_a_view_that_never_subscribes_leaves_the_event_log_as_it_was (test_app.py)
COMMIT: dba25d5
PROVED: against A1's head, 9 failed: the subscriber attached after construction received nothing (`[offer] = ...`
unpacked an empty list: the dialog had gone out before anyone listened); a frozen check's root was the checkout
(`assert PosixPath('.../rb-dist-app') == .../station`) and its version None.
NOTE: frozen, `FirmwareCheck` roots at `Path(sys.executable).resolve().parent` (firmware/, tools/, VERSION).
The honest point: `Dashboard.open` subscribes inside `view.open()`, just before the loop, and Tk reads Setup's
state before that (TkPanelView's first refresh), so neither "after construction" nor "first state read" works for
all three. `Setup.startup_checks()` releases the offer (or the check releases it when it finishes later).
`app.launch` calls it on the first `events.subscribe` (a one-shot hook on the EventLog instance, removed at that
call or when `view.open()` returns); for the Web it calls `startup_checks(on_next_read=True)` after open(): the
offer goes out with the page's first `/api/setup` read, after the page has fetched `/api/events?since=0` (older
events it replays as history, never as a dialog). The existing startup-offer tests now call `startup_checks()`.
Not done, same shape: the UPDATE_READY dialog from the startup update check is published from its thread the same
way; a fast network could beat the view. Worth the same gate; not in the brief.

## A3
STATUS: partly (the Transfer Map side is closed; Setup's row is built but not inserted, see NOTE)
TEST: test_with_nothing_chosen_the_map_has_no_store_and_asks, test_opening_without_a_store_warns_and_creates_nothing,
test_every_recording_command_is_refused_without_a_store (11 cases), test_import_is_refused_without_a_store,
test_a_new_store_is_created_where_the_operator_says_and_remembered, test_a_new_store_never_overwrites_an_existing_file,
test_an_existing_store_is_opened_and_remembered, test_opening_what_is_not_a_store_is_refused,
test_a_store_inside_the_install_is_refused, test_a_store_left_in_the_install_is_offered_never_taken,
test_the_environment_overrides_the_remembered_choice, test_a_chosen_store_is_refused_while_a_trial_is_armed
(test_transfer_map.py); test_the_default_file_is_beside_the_flash_stamp, test_the_environment_names_the_file,
test_nothing_recorded_reads_as_nothing_and_creates_nothing, test_a_write_goes_straight_to_disk,
test_the_file_is_read_once, test_an_unreadable_file_is_nothing_chosen, test_only_known_keys_are_kept
(test_user_config.py); test_the_trial_store_row_block_builds_the_brief_shape,
test_the_store_row_says_nothing_is_chosen_then_what_was, test_opening_a_store_from_setup_moves_an_open_map_onto_it,
test_setup_refuses_a_store_inside_the_install, test_setup_offers_the_store_an_earlier_build_left_in_the_install (test_setup.py)
COMMIT: 8f1f0e3
PROVED: base TransferMap with no STATION_MAP_DB (scratch script over `git archive fb5a045 src`): db_path =
`<checkout>/data/transfer_map.sqlite`; frozen default `/Apps/station/data/transfer_map.sqlite` - inside the folder
an update replaces; no open_store. The new tests against base error (no `TransferMap.choices`, no `_install_root`).
NOTE: `controller/user_config.py` as briefed (`read`/`write`/`forget`, key `map_store` only). model/ may not import
controller/ (test_architecture), so the choice reaches the model as `TransferMap.choices`, set to `user_config` by
`controller.setup` at import. Store section (tier 1, between Session and Trial; test_the_sheet_reads_in_the_order_a_trial_is_run
updated to ["Session", "Store", "Trial"]): Trial store line, Store file + Open store (must be an SQLite file), Folder
for a new store + New store name + New store (creates folder and schema, never over an existing file). Refusal
while no store: "Choose a trial store first (Transfer Map, Store)." on arm, tips, AFM/tilt/speed corrections,
delete, new session database, export, import. No store: `db_path` and `output_root` None (the Web serves no
download), `_NoStore` answers every read empty and refuses every write. Opening with no store warns
**"Trial Store Not Chosen"** (literal title; should join ATTENTION so it is a dialog, not a tray line).
Contradiction in the brief, resolved conservatively: the legacy `<install>/data/transfer_map.sqlite` is pre-filled
"so the operator can Open it", but a store inside the install is refused. Opening it is refused; the warning and
the refusal say to move it (with its pictures folder) out of the station's folder and open it there. If the owner
wants Open to copy it out in one press, that is a small follow-up.
**Blocking file:** `tests/test_setup_registry.py:171` pins Setup's section titles exactly (…, "Piezo Stage",
"Launch"). `Setup._store_section()` (readonly `map_store_status`, entries `map_store_path` / `map_store_dir` /
`map_store_name`, commands `open_map_store` / `new_map_store`, all implemented and tested by direct call) is not in
`_build_schema`, the precedent being the Firmware row's own wait. To finish: `sections.append(self._store_section())`
just before the Launch section, then add "Trial store" before "Launch" in test_setup_registry.py:171 and
test_setup.py:164. Check the Tk and Qt Setup tables in the Qt pass: they lay the model rows out as a grid, and this
row's elements are entries, not a checkbox and a dropdown.

## A4
STATUS: closed (code and fakes; never run in a real bundle)
TEST: test_a_checkout_has_no_switch_to_stable, test_a_bundle_with_stable_beside_it_offers_the_switch,
test_the_switch_closes_flashes_stable_launches_it_then_exits, test_a_board_that_fails_to_flash_stops_the_switch,
test_the_switch_waits_for_a_scan_and_a_flash (test_setup.py); test_after_a_stable_flash_every_station_board_is_out_of_date
(test_firmware_check.py); test_launch_hands_setup_the_way_to_end_the_station, test_exit_process_closes_the_log_then_exits (test_app.py)
COMMIT: ba30238
PROVED: new feature: 6 of the 8 fail against A3's head (no `stable_root` / `switch_to_stable` / `exit_process`);
the way-back test and the checkout pin pass there by design (A2 already made the way back).
NOTE: a "Stable" section right after Firmware, only when `stable/station-stable(.exe)` is beside the launcher
(`flashing.stable_root()`); a checkout's section list is unchanged, so the registry pin is untouched. Confirm text
verbatim from the brief. Refused while a flash, an update or a scan runs, and while a model is energized (as
Restart). On confirm: `controller.reset()`, then on the flash thread (so Launch and Refresh wait) `FirmwareCheck(
sketch_root=stable/firmware, channel="stable")` flashes the four boards that are plugged in and not already on the
stable sketch; lines go to the log and the Flashing cell. Any FAILED board: "Switch to Stable Failed" names it,
nothing starts, the station stays. Else `stable/station-stable` starts detached (POSIX `start_new_session`,
Windows DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP, stdio to DEVNULL, cwd `stable/`) and `app.exit_process`
(flush, close the log, `os._exit(0)`: it runs on Setup's worker thread). A launch that fails after a good flash
warns and says how to come back. Literal event titles: "Switch to Stable", "Switch to Stable Failed" (the second
should join ATTENTION).

## A5
STATUS: closed
TEST: test_no_login_asks_github_anonymously, test_no_login_downloads_anonymously_too,
test_a_login_command_that_hangs_is_no_login_not_a_hang (updated), test_github_refusing_the_login_is_unauthorised (401 only now),
test_no_release_yet_says_so_not_a_refused_sign_in[signed-in], test_no_release_yet_says_so_not_a_refused_sign_in[anonymous],
test_the_swap_script_waits_swaps_starts_and_deletes_itself (extended), test_the_swap_never_touches_a_store_outside_the_install,
test_a_store_inside_the_install_is_the_only_thing_at_risk (test_updater.py);
test_a_pending_update_is_swapped_in_before_anything_opens, test_on_windows_the_startup_swap_goes_to_the_script,
test_without_a_pending_update_the_start_is_as_before, test_a_checkout_never_looks_for_a_pending_update (test_app.py);
test_no_release_yet_reads_as_such_on_the_update_line (test_setup.py)
COMMIT: f0394b1
PROVED: with the tests written and the code unchanged: 8 failed (no-login was `unauthorised` and asked GitHub
nothing; 404 said "GitHub did not accept this machine's sign-in"; the swap script left UPDATE_PENDING; app.main
opened the view on the old version with an update pending). The two "store" swap tests and the Setup copy test
passed before too: they pin behaviour the base already had.
NOTE: `_headers(None)` sends no Authorization; a login is still sent when found. 404 -> new status `no_release`,
"No release has been published yet." (check and apply; Setup's Update line says the same). 401 with a login stays
`unauthorised`. `app.main`, right after argparse and before `--no-motion`, the view, the log: frozen and
`pending_update(dirname(sys.executable))` -> `restart_process()` (non-Windows: finish_pending + execv the new
launcher; Windows: the swap script + `os._exit`). The swap script now deletes `<install>\UPDATE_PENDING` before
it starts the station, so a swap that failed on Windows is not retried on every start (without it the new startup
swap would loop).

## A6 (agent E's findings, rb-dist-build)
STATUS: closed
TEST: test_a_frozen_bundle_flashes_with_its_own_tools_and_data_dir (extended: absolute ARDUINO_DIRECTORIES_*,
USER = arduino-data/user, cwd = tools/), test_the_bundles_config_file_beside_the_cli_is_passed_explicitly,
test_the_command_line_carries_the_table_as_literals_the_bundle_build_reads, test_the_teensy_needs_max6675_which_its_core_does_not_ship,
test_no_command_passes_the_vendored_libraries, test_a_tool_failure_names_what_to_do (4 cases: Rosetta 2, libusb-0.1-4,
dialout, Teensy udev rules), test_a_teensy_that_never_reaches_its_bootloader_says_so, test_an_ordinary_failure_adds_no_hint
(test_flashing.py); test_a_failed_flash_warns_with_what_to_do (test_setup.py). All through the fake runner.
COMMIT: c224861
PROVED: against f0394b1 (A5 head): 11 failed (no MAX6675; USER = arduino-data; config looked for in arduino-data/;
cwd = the sketch root; no hints; flash_firmware.py's table not literal).
NOTE: (1) TEENSY_LIBS = ["LiquidCrystal_I2C", "MAX6675"] in flashing.py and in flash_firmware.py.
(2) Frozen: every tool runs with cwd=<bundle>/tools, `--config-file tools/arduino-cli.yaml` when present, and
ARDUINO_DIRECTORIES_DATA=tools/arduino-data, _USER=tools/arduino-data/user, _DOWNLOADS=tools/arduino-data/staging
(absolute) - matches packaging/tools.py (config_text / cli_env).
(3) Library decision: the registry LiquidCrystal_I2C 2.0.0 wins; `--libraries` is never passed. Checked by compiling
temp_controller against rb-dist-build's staged build/tools (offline, dead proxy, compile only, no port):
without --libraries EXIT=0; with `--libraries firmware/libraries` the vendored NewLiquidCrystal is used and the
compile fails ("'int LiquidCrystal_I2C::init()' is private"). The exact argv/env/cwd flashing.py builds then compiled
all four sketches EXIT=0 against that staged tools/ (the Mega commands with --upload -p dropped).
Left for the lead: the vendored firmware/libraries/LiquidCrystal_I2C is unused by any compile but is still inside
the Teensy's sketch hash (the hash rule counts a vendored library whose header the sketch includes). Harmless
(an edit there would only mark the Teensy out of date); deleting that folder is a firmware/ decision (the lead's),
and would change the Teensy's hash once, so every machine would be offered one Teensy flash.
(4) Tool failures -> operator sentences, keyed on the tool's output, never on the platform: "bad CPU type" ->
install Rosetta 2; libusb-0.1 missing -> sudo apt install libusb-0.1-4; port permission denied -> dialout group;
"Unable to open device" -> Teensy udev rules; teensy_loader_cli timing out -> press the button / udev rules.
Each is a `[HINT]` line in the Flashing cell and log, `hints` in the result, and is quoted in the Firmware Flash
Failed and Switch to Stable Failed warnings.
(5) Needed for the merge with rb-dist-build: packaging/layout.py and tools.py read DEVICES, MEGA_FQBN, TEENSY_FQBN,
MEGA_LIBS, TEENSY_LIBS from firmware/flash_firmware.py with `ast`. A1 had replaced them with expressions (which
would have made layout.sketch_dirs() exit "no literal DEVICES table"); they are literals again, pinned equal to
controller.flashing.

## GATE
fast (STATION_NO_WINDOWS=1 QT_QPA_PLATFORM=offscreen, -m "not qt") on c224861: EXIT=0, 3285 passed, 12 skipped,
255 deselected, 1 xfailed (base 3184 passed; +101). golden (tests/test_wire_golden.py): EXIT=0, 78 passed.
Qt pass not run (by rule). Nothing written to ~/transfer-stage-runs by the suite (checked after the run: no station.json, no flashed.json).

## UNVERIFIED
- No port or board was touched and nothing was uploaded: every flash in the suite ran through fake runners and a
  fake handshake. The one real tool use was compile-only against rb-dist-build's staged tools/ (A6). The frozen tool paths, the `ARDUINO_DIRECTORIES_*` environment and the detached launch are
  tested against faked `sys.frozen` / `sys.executable` only; no bundle was built.
- The one-shot subscription hook against the real Tk and Qt dashboards (tested with a stand-in view whose open()
  subscribes like `Dashboard.open`); the Web offer against the real page (tested at the Setup level).
- The Stable and Store rows in the three views (views render schema; the Qt pass and a look at Tk/Web are the lead's).
- Windows: the swap script's new `del` line, the startup swap handing over to it, and the detached stable launch.

## Event names that should join ATTENTION (events.py is not mine)
"Trial Store Not Chosen" (Transfer Map opened with no store), "Switch to Stable Failed".

## Docs the lead must change
- README "## Install": updates work without a GitHub sign-in (public repo; a signed-in machine is still used);
  "No release has been published yet" is normal before the first tag; the Transfer Map asks where to keep trials
  on first use (Store: Open store / New store), choose a folder OUTSIDE the station folder, remembered in
  `~/transfer-stage-runs/station.json`; `--map-db` / STATION_MAP_DB still override; a store left in
  `<station>/data/` by an earlier build must be moved out, then opened. The bundle flashes its boards itself
  (Flash now), and "Switch to stable" (Setup) runs the lab's original app; coming back = start the station, Flash now.
- PACKAGING_PLAN: the flashing runs in-process from `src/controller/flashing.py`; the bundle's arduino-cli runs with
  cwd=tools/, `--config-file tools/arduino-cli.yaml`, absolute ARDUINO_DIRECTORIES_* as packaging/tools.py sets them
  (A6); the registry LiquidCrystal_I2C is the one compiled, never firmware/libraries (A6); tool failures name
  Rosetta 2 / libusb-0.1-4 / dialout / Teensy udev rules; stamp entries carry `channel` and `version`; startup swap of a pending update in app.main.
- RECORDING_A_TRIAL, the store section: replace "one SQLite file inside the project checkout, <repo root>/data/…"
  with the operator's choice (no default; Open/New store; never inside the install; remembered; STATION_MAP_DB /
  --map-db override; recording commands refused until chosen).
- CLAUDE.md / station-map "Commands": `dev/swap_branch.sh` calls `firmware/flash_firmware.py`, which now imports
  `src/` (run it with the venv's python); it records main's sketches as channel `station` unless given `--channel stable`.
