# Organisation audit: mvc-refactor, 2026-09-27

Read-only audit by an Explore agent (saved here by the lead; the agent had no write tool). Line counts come from `wc -l` on the tracked tree at `5afb4bc`.

## Ten-line summary

1. The three views repeat about 15 copy and rule tables. At least 7 of them already **disagree in behaviour**: the simulation-line rule, device words, lost-device sentence, int display (Qt rounds, Tk and Web truncate), key glyphs, lamp colours, and the empty readout ("--" vs "—").
2. `server.py` already serves `/api/theme.json` (`GATE_WORDS`, `QUIET_VALUES`, `TIER_LABELS`, event titles). Extending that route plus a new `views/words.py` would remove about 300 duplicated lines.
3. `tk.py` (7010 lines) is `TkPanelView` 1913–5110 (3198), `TkDashboard` 5288–7010 (1723), widgets 953–1897, helpers 54–952. `qt.py` (6458) already carries banners that match a split.
4. The blocker for a zero-behaviour split is the tests: `test_view_tk.py` monkeypatches about 25 module globals on `tkmod` (`tk`, `ttk`, `_confirm`, `_text_width`, `_PIXEL_FONTS`…), and several tests read `tkmod.__file__` / `qt.__file__` source.
5. Moving tests into subdirectories breaks 11 bare cross-imports (`from test_core_fakes import …`) and 8 `__file__`-relative paths, unless `tests/` goes on the pythonpath and the fakes move to `tests/support/`.
6. `legacy/` is not "golden captures only". `firmware/flash_firmware.py:69-71` imports `legacy/src/app_bootstrap` (BUGFIX D-12), and 4 test files (5 legacy-running tests) execute legacy code at run time.
7. TEST_PORTING: 78 of 135 old files are not cited by any new test (69 non-VOID). 6 of the 26 safety rows are not cited by name.
8. `docs/implementation/gen_ledger.py` rewrites the frozen `progress.md`. The 12 audit pages and 10 of the 11 archive pages have zero inbound path references. `bench-checklist.md` links to a moved `plan.md`.
9. Root: `.DS_Store` and `images/.DS_Store` are tracked. The 1.5 GB `.venv` is picked up by `run.sh`/`run_macos.sh` ahead of `main/.venv`. `scratch/` and `src/lib/` are ignored, stale, and safe to delete. 3 of 11 images are unreferenced. README describes the old GUI.
10. Naming: `FULL STOP` event title, sentence-case titles only in `devices/gamepad.py`, `_lamp_colours` and `_lamp_colors` both in `tk.py`, the DC firmware folder named `high_polling_rate`, `test_core_*` files that test views.

## 1. Duplication across the three views

`base.py` (359 lines) already shares `join_names`, `event_line`, `hardware_links`/`LEGACY_LINK_DEVICES`, `GATE_WORDS`/`gate_reason` and `stop_words`. The Web gets words through `/api/theme.json` (`server.py:173-190`) and `state["stop_words"]` (`server.py:144`). Everything below still exists two or three times.

| # | Item | Tk (`tk.py`) | Qt (`qt.py`) | Web (`app.js`) | Same or different |
|---|---|---|---|---|---|
| D1 | Device words | `DEVICE_WORDS` + `_device_list` 1899-1905 | `device_word` 366 (CamelCase split; SMC100→"SMC100", Screen→"screen") | `DEVICE_WORDS` + `deviceWord` 623-634 | **Differ.** Tk/Web say "SMC100 controller", "screen capture"; Qt does not. |
| D2 | Lost devices / sentence | `_set_lost` 5102; rail "X: serial port connection lost" 6694; entry "Connection lost: …" 4870 | `lost_devices` 375, `lost_sentence` 384 "X lost its serial port" | `lostDevices` 637; "X lost its …" 2355, 3173 | **Differ.** Tk wording vs Qt/Web. |
| D3 | Simulation line | `SIMULATION_LINE` 183; `_sync_sim_line` 6711: all links simulated → line, else "" | `SIM_LINE` 245; `simulation_line` 389: fallback=None, mixed → "Simulated: A, B" | `simLineText` 662: *any* device simulated; mixed → names | **Three rules.** Tk never names a partial simulation; Web counts non-link devices. |
| D4 | Gate reason incl. `enabled_by` | `_gate_reason` 4767: latch, fault, then "`<caption>` is off" | `gate_reason` 450: "Tick `<caption>` first" / "Not selected" | `gateReason` 216: "Tick Launch on this row first" / "Not available yet" | Mode part is shared. **The `enabled_by` sentence differs three ways.** |
| D5 | `is_enabled` | uses `sch.is_enabled` | same | `isEnabled` 116 (mirror) | Same today, but hand-mirrored. |
| D6 | Stop commands | none (the Panel decides) | none | `STOP_COMMANDS` 235 vs `Panel.UNGATED_COMMANDS` (`panel.py:22`) | JS omits the schema `stop=True` rule (`panel.py:200`). |
| D7 | Stop copy | `STOP_LINE`, `LATCHED_LINE`, `STOPPED_HEADLINE`, `UNCONFIRMED_LINE`, `FAULTED_LINE`, `STOP_NOT_CONFIRMED` literal 141-164 | `UNCONFIRMED_LINE`, `FAULT_LINE` 246-252; title from `events` | literals at 247, 1780, 2413; `NO_STOP_WORDS` 46 | Strings identical. Tk re-types `"Stop Not Confirmed"` and `"Idle Timeout Soon"` (175) instead of using `events.*`. |
| D8 | Quit / clear / close questions | `QUIT_PROMPT` 5303, energized variant 5909, `CLEAR_DIALOG` 179, close 5887 | `quit_prompt` 275, `CLEAR_WORDS` 271, `close_model_words` 315 | literals 3028, 3644 | **Clear title differs:** Tk "Clear the stop" vs Qt "Clear the stop?". |
| D9 | Idle countdown | `IDLE_LINE` 171, loop 6560 | `idle_countdowns` 295, `countdown_text` 311 | 3497-3522 | Same rule, three times (Web uses a no-break space). |
| D10 | Event line | `_event_line` 256 = severity word + `base.event_line` | `base.event_line` | `eventText` 332 | **Differ.** Python lowercases a whole non-sentence title ("SMC100 Error" → "Smc100 error"); JS `sentenceCase` keeps initialisms and also unshouts the message. Tk adds a severity word, Qt does not. |
| D11 | Sentence case | `_sentence`/`_label` 399-418 | `sentence`/`sentence_case` 428-448 | `sentence`/`sentenceCase`/`unshout` 296-330 | Near-identical; JS also lowercases "(Word)". |
| D12 | Unit split | `_split_unit` 423 (no unit words) | `split_unit` 463 (+ `UNIT_WORDS` C→°C) | none | **Differ:** Qt shows °C, Tk shows C. |
| D13 | Axis letter | `_axis_of` 446 (X/Y/Z) | `axis_letter` 476 (any single capital) | `axisLetter` 446 (X/Y/Z + "X position") | **Three rules.** |
| D14 | Quiet / normal values | `theme.QUIET_VALUES` | own `QUIET_VALUES` 236 **plus** `theme.QUIET_VALUES` | `QUIET_WORDS` 609 **plus** served `QUIET_VALUES` | Two parallel lists in Qt and Web with different members. |
| D15 | Number / int display | `_is_number` 431 (float parse); `_display_text` 4495 truncates | `is_number` 494 (regex); `display_text` 1369 **rounds** | `readoutKind` 615; `intText` 465 truncates | **"2.7" shows "3" in Qt and "2" elsewhere.** |
| D16 | Empty readout | `EMPTY_READOUT = "--"` 122 | `"—"` 239 | `'--'` 568 | **Qt differs.** |
| D17 | Tier / disclosure | `_tier_of` 441; `theme.TIER_LABELS.get(tier,"Details")` 2335 | `tier_of` 585, `disclosure_text` 589 | `sectionTier` 496; default `{2:'Details'}` 37 before the theme loads | JS fallback "Details" vs theme "Configure" (`theme.py:187`). |
| D18 | Key glyphs | `KEY_GLYPHS` by command 579 | `KEY_GLYPHS` by legend prefix 657 (+ "3d analysis"→link) | `COMMAND_GLYPHS` + type rules 419 | **Three rules, different sets.** |
| D19 | Lamp colours | `_lamp_colors` 4182 (unlit fill "", ring MUTED) | `lamp_colours` 413 (unlit `LAMP["off"]`, danger-off ring TEXT) | CSS | **Differ.** |
| D20 | Rail row / action row | (inside the panel) | `rail_elements` 513, `is_action_row` 629, `entry_rows` 598 | `railElements` 539, `isCommandRow` 510, `sheetAcross` 431 | Same idea, two copies. |
| D21 | Middle elide | `_elide_middle` 891 | `ElidedLabel` 2543 | `elideMiddle` 597 | Pure text half is identical. |

**Proposed shared home.** A new `src/views/words.py`, toolkit-neutral and importing only `schema`, `events` and `theme`, which the import-rules test already allows. Or grow `base.py`, which would pass 700 lines.

- Pure functions and tables: `DEVICE_WORDS`/`device_word`, `lost_devices`/`lost_sentence`, `simulation_line(states)`, `enabled_by_reason(element, values, caption)`, `quit_prompt`/`clear_words`/`close_model_words`, `idle_countdowns`/`countdown_text`, `sentence`/`sentence_case`/`unshout`, `split_unit` + `UNIT_WORDS`, `axis_letter`, `is_number`, `int_text`, `EMPTY_READOUT`, `tier_of`/`disclosure_text`, `KEY_GLYPHS` (keyed by command/type), `lamp_colours` (move beside `theme.toggle_colors`), `rail_elements`, `elide_middle(text, limit)`, `STOP_COMMANDS = Panel.UNGATED_COMMANDS`.
- Qt's pure block (`qt.py:344-1406`) is the natural seed: it is already toolkit-free and unit-tested in `test_view_qt.py`.
- For the Web, add the tables to `/api/theme.json` (device words, unit words, glyphs, empty readout, the question words) and keep thin JS readers. `app.js` must keep its own copies of the *functions*, because the browser cannot import Python. Pin their parity with the existing Node harness in `test_view_web_client.py` by feeding both implementations the same fixtures.

**What stays per toolkit:** fonts, measuring and elide by pixel width (`tk._elide`, `ElidedLabel`), `_marshal`, widgets, focus and accessibility, Qt's `STOP_SHORTCUT` platform branch (`qt.py:205`, toolkit-forced), QSS `stylesheet()`, Tk ttk styles, and the DOM.

**Note:** unifying D1, D2, D3, D10, D12, D13, D15, D16, D18 and D19 **changes visible behaviour in at least one view.** Each one is an owner or lead call on which wording wins, not a pure refactor.

## 2. The two desktop monoliths

### `src/views/tk.py`: 7010 lines

| Lines | Section | Kind |
|---|---|---|
| 1-53 | docstring, imports | |
| 54-255 | constants: copy, sizes, stop words, dialogs, keys | copy (→ `words.py`) and toolkit constants |
| 256-270 | `_event_line` | copy |
| 271-538 | fonts, measure, `_label`/`_sentence`, `_split_unit`, `_tier_of`, pads | toolkit helpers + pure text |
| 539-760 | key look, lamp, glyph strokes, icons (`_draw_lamp`, `_draw_glyph`, `_icon_image`) | drawing primitives |
| 761-861 | geometry and log-window placement (`_log_window_rect`) | pure geometry |
| 862-952 | elide, Tcl error, windowing system, Tk version | toolkit helpers |
| 953-1015 | `ClosableNotebook` | pure widget |
| 1016-1151 | `_RegionPicker` | pure widget (overlay) |
| 1152-1229 | `_Tooltip` | pure widget |
| 1233-1324 | `_Ring` | pure widget |
| 1325-1415 | `_Press` | pure widget (chrome key) |
| 1416-1493 | `_Switch` | pure widget |
| 1494-1565 | `_Disclosure` | pure widget |
| 1566-1666 | `_ConfirmDialog`, `_confirm` | dialog |
| 1667-1897 | `_Mushroom` | **the stop object** |
| 1899-1912 | `DEVICE_WORDS`, `_device_list`, `_DISCLOSED` | copy / panel state |
| 1913-5110 | `TkPanelView` (147 methods): scroll 2083, tick 2235, layout 2305-3237, commands 3238, renderers 3388-4212, detached log 4213-4460, base hooks 4461-5110 | **the panel view** |
| 5111-5287 | `_Sheet` | dashboard (sheet) |
| 5288-7010 | `TkDashboard`: construction 5402, rail 5611, sheet 5923, stop 6106, band/tray 6157, lifecycle 6370, tick 6460, panels 6750, Setup page 6789, events 6913, focus 6991 | **dashboard / rail** |

### `src/views/qt.py`: 6458 lines, with its own banners

| Lines | Section | Kind |
|---|---|---|
| 1-136 | imports, PySide6 stub (the module imports without Qt) | |
| 137-339 | constants and copy | copy |
| 340-786 | "Pure functions. No Qt" (sentence, gate_reason, tier, keys…) | pure (→ `words.py`) |
| 787-1169 | `stylesheet()` (QSS) | style |
| 1170-1406 | accessibility, fonts, pixels, series and polyline maths, validators | helpers |
| 1409-1806 | section containers `FlowLayout`, `FlowSection`, `PanelTable`, `TableRow`, `RowTitle`, `TableBar` | layout |
| 1807-1930 | `SeriesPlot`, fonts | pure widget |
| 1931-2169 | `StopButton` | **the stop object** |
| 2170-2838 | `SwitchButton`, `KeySlider`, `paint_key`, `DisclosureKey`, `Nameplate`, `ChordLabel`, `TickBox`, labels, `MiddleCombo` | pure widgets |
| 2839-3080 | glyph, icon and lamp painting | drawing primitives |
| 3081-3132 | `ask`, `cancel_pending_confirms` | dialog |
| 3133-3326 | `RegionOverlay`, `FlagWindow` | pure widgets |
| 3327-3658 | `EntryHead`, `SheetEntry`, `RailItem` | dashboard pieces |
| 3663-5081 | `QtPanelView` | **the panel view** |
| 5086-6458 | `QtDashboard`: rail 5306, idle 5692, Setup 5840, sheet 5860, empty 6048, tray 6077, panels 6321, global stop 6386, focus 6430 | **dashboard / rail** |

### Proposed split (no behaviour change)

```
views/tk/__init__.py   re-exports TkDashboard, TkPanelView and every name tests patch
         _common.py    imports tkinter as tk/ttk ONCE; constants, fonts, measure, elide
         draw.py       lamp/glyph/icon/key-look primitives (539-760)
         widgets.py    ClosableNotebook … _Disclosure, _ConfirmDialog (953-1666)
         stop.py       _Mushroom (1667-1897)
         panel.py      TkPanelView (1913-5110)
         dashboard.py  _Sheet + TkDashboard (5111-7010)
views/qt/ same shape: _common (stub + constants), style (787-1169), layout (1409-1806),
         widgets, stop (StopButton), sheet (EntryHead/SheetEntry/RailItem), panel, dashboard
```

**Import rules.** `test_architecture.py:26` walks `src/**/*.py` with `"views/" in path`, so the new files are covered automatically.

**What must change alongside the split** (none of it touches the app; all of it is in tests or packaging):

1. **Monkeypatch targets.** `test_view_tk.py:802-815` and 30 other sites patch `tkmod.tk`, `tkmod.ttk`, `tkmod.filedialog`, `tkmod._confirm`, `_text_width`, `_PIXEL_FONTS`, `_RegionPicker`, `_LOG_POSITIONS`, `_windowing_system` and `_tk_version`. `test_view_qt_widgets.py` patches `qt.ask` 9 times, plus `announce` and `QAccessible`. After a split, patching the package does not reach the submodule that uses the name. Either every submodule reads these through one `_common` module attribute (`_common.tk.Frame`, `_common._confirm(...)`) and the tests patch `_common`, or the tests are re-pointed per use. This is the real cost of the split.
2. **Source-reading tests.** `test_view_tk.py:1833, 2630, 3006, 4067` (`_executable_source(tkmod.__file__)`), `:3591` (a dirname chain that assumes `views/tk.py` depth), `:5753` (`inspect.getsource(tkmod)`: the no-colour lint would then see only `__init__`), and `test_view_qt.py:338, 370, 563, 632, 655, 885` all read one file. They must iterate the package.
3. **Packaging.** `pyproject.toml` `packages` must add `views.tk` and `views.qt`; `test_packaging.py:110` enforces this. `station.spec` `VIEW_HIDDEN` can stay as is, because the package `__init__` pulls in its submodules.
4. `app.py:35-36` keeps working if `__init__` exports the dashboards.

## 3. Tests

The flat layout is 43 `test_*.py` files plus `conftest.py`. Two of those files are not tests: `test_core_fakes.py` (456 lines, 0 tests) and `test_heater_fakes.py` (133 lines, 0 tests). Largest files: `test_view_tk.py` 5804 lines / 291 tests; `test_view_qt_widgets.py` 4254 / 224; `test_view_web_server.py` 4038 / 121.

**Proposed layout:**

```
tests/conftest.py, pytest.ini          stay (pythonpath=../src; add "." so helpers import)
tests/support/  core_fakes.py heater_fakes.py tk_fakes.py (FakeTkModule…) web_fakes.py
tests/core/     architecture app controller device events model panel param result schema
                model_contract idle gamepad_input packaging
tests/model/    probe probe_frames heater heater_frames rotator red_monitor plot_data manual_speed_live
tests/devices/  serial_port serial_port_handshake gamepad gamepad_layouts smc100 smc100_frames screen
tests/setup/    setup setup_identify setup_registry
tests/views/    base theme  tk/  qt/ (pure, widgets)  web/ (server, client, watchdog, browser)
tests/golden/   wire_golden + capture.py old_frames.py *.json (as now)
```

**What must move or change:**

- **Bare sibling imports.** These resolve today only because every file sits in `tests/` (rootdir insertion):
  - `from test_core_fakes import` in 10 files: core_{controller, device, events, model, panel, schema, theme, views_base}, idle, probe
  - `from test_heater_fakes import` in heater and heater_frames
  - `from test_probe import FakePort` in gamepad:856
  - `from test_probe import LEVELS, make_probe` in manual_speed_live
  - `from test_view_tk import …` and `from test_view_web_server import …` in manual_speed_live
  - `from test_gamepad_input import JoystickStage` in model_contract
  - `from tests.test_setup` and `from tests.test_setup_identify` in setup_identify and setup_registry (a namespace import through `ROOT` in `conftest.py:7-9`)

  Move the shared fakes to `tests/support/` and import them as `support.x`, or add `tests` to `pythonpath` and import by package.
- **`__file__`-relative paths:** `test_architecture.py:8`, `test_packaging.py:18`, `test_smc100_frames.py:25`, `test_view_web_client.py:25-26`, `test_gamepad.py:858`, `test_probe_frames.py:57`, `test_heater_frames.py:42`, `golden/old_frames.py:19`, `golden/capture.py:96`. Each needs one more `..` or a shared `ROOT`.
- **Markers.** They survive a move: `pytestmark` is per file and `conftest.py:99-116` adds `qt`/`window` by fixture name. **The descriptions in `tests/pytest.ini:8-17` are stale:** they cite S2–S11, `SystemManager` and `ui_schema`.

**Files that mix concerns:**

- `test_view_web_server.py`: the HTTP server, plus puppeteer browser tests from line 786 (node + puppeteer at a hard-coded `/opt/homebrew` path, 792). Those belong with `test_view_web_client.py`.
- `test_manual_speed_live.py`: a feature slice across the model, Tk and Web; it imports fakes from two view test files.
- `test_view_tk.py` and `test_view_qt_widgets.py` are organised by audit round (banners "Tier F", "G3", "E", "Tier L", "Tier N and O" at tk 2430, 3013, 3666, 4289, 4995), not by subject. Rail, stop and panel tests recur in every round.
- `test_core_theme.py` and `test_core_views_base.py` test `views/`, not core.
- `test_gamepad.py:851-858` holds a golden-frame block that imports the model.

## 4. docs/

| File | Lines | Pointed at by | Banner | Recommendation |
|---|---|---|---|---|
| `architecture/audit/*.md` (12 files) | 3194 | glob only: `CLAUDE.md:47`, `.claude/agents/docs-pruner.md:29`; `carry.json` cites their IDs | "Input (banner added 2026-09-23)" | Keep until `legacy/` is deleted, then archive. |
| `architecture/root-causes.md` | 1152 | `CLAUDE.md`, `TEST_PORTING.md`, `src/devices/gamepad.py`, docs-pruner | Input banner | Keep; its RC invariants still bind. |
| `architecture/safety-pattern.md` | 117 | `CLAUDE.md`, `test_core_model.py`, `test_view_web_watchdog.py` | Input banner | Keep. |
| `implementation/progress.md` | 3646 | `CLAUDE.md` (frozen rule), docs-pruner | "Frozen input" | Keep in place (`carry.json` IDs). |
| `implementation/bench-checklist.md` | 282 | `CLAUDE.md`, `PACKAGING_PLAN.md` | Input banner | Keep. **Fix the broken link `[plan.md](plan.md)` at :13**; the file now lives in `docs/archive/implementation/`. |
| `implementation/gen_ledger.py` | 160 | none (only prose in `progress.md:267`) | none | **Archive or delete.** It rewrites the frozen `progress.md` (:132-148), which contradicts `CLAUDE.md:104`. |
| `archive/README.md` | 32 | 6 files in `.claude/` | is the banner | Keep. |
| `archive/architecture/README.md` | 102 | `.claude/*` | the archive README covers it | Keep. |
| `archive/architecture/*.md` (8 pages) | 1662 | none | the directory README | Keep (history), or delete and rely on git. Owner call. |
| `archive/implementation/plan.md` | 658 | `bench-checklist.md` (broken link), `legacy/tests` comments | directory README | Keep. |
| `archive/implementation/testing.md` | 372 | `legacy/tests/{conftest.py, pytest.ini, architecture/test_invariants.py}` | directory README | Keep while `legacy/tests` exists. |
| `tests/TEST_PORTING.md` | 287 | `STATUS.md`, `BUGFIX_PLAN.md`, `CLAUDE.md`, station-map, docs-pruner | path-map banner | Keep. It needs a status column (see below). |

**TEST_PORTING and the legacy deletion criterion.** The inventory has no done or not-done column. A proxy: each old file's basename grepped in `tests/*.py` ("Ported from …" docstrings). **57 of 135 are cited; 78 are not** (9 VOID, 66 PORT-ADAPTED, 3 PORT). Of the 26 safety rows, **20 are cited and 6 are not**: `test_manager24_hide_brings_hardware_safe`, `test_model_owned_loops`, `test_rotator6_sampler_write_timeout`, `test_tkinter_full_stop`, `test_edge_mvc_state_transitions`, `test_redpercent19_tk_full_stop_gate`. They may be ported under other names; unverified.

**`legacy/` has more dependents than CLAUDE.md states.** These also run legacy code: `firmware/flash_firmware.py:67-71` (imports `app_bootstrap`, BUGFIX D-12; `run_swap.sh:99` calls it on the bench); `test_wire_golden.py:79-100` (re-captures from legacy); `test_probe_frames.py:57-70` and `test_heater_frames.py` (through `golden/old_frames.py:19`); `test_smc100_frames.py:25-30` (loads `legacy/src/lib/smc100.py`).

## 5. Root-level clutter

| Item | Facts | Referenced by | Recommendation |
|---|---|---|---|
| `run.sh` (7) | activates `.venv` if present, then `python3 src/app.py "$@"` | README, CLAUDE.md, `src/app.py:41` | Keep. |
| `run_macos.sh` (94) | prefers local `.venv`, else the active venv; PySide6 self-heal | README, CLAUDE.md, STATUS, `conftest.py:32`, `station.spec:220` | Keep. |
| `run.bat` (4) | **requires** `.venv\Scripts`, no fallback | README, CLAUDE.md | Keep; add the same fallback as run.sh. |
| `run_swap.sh` (134) | bench launcher; main tree defaults to `../transfer-stage-unified-main` (on this Mac it is `../main`) | STATUS, BUGFIX_PLAN | Keep at root (desktop shortcuts point at it). Mode is 644 (not executable), same as `run_swap_macos.sh`. |
| `run_swap_macos.sh` (64) | macOS variant | `run_macos.sh` comment only; **not in CLAUDE.md or STATUS** | Keep or move to `scripts/`; owner call. |
| `requirements.txt` / `requirements-dev.txt` | thin wrappers: `-e .[qt]`, `-e .[qt,dev]` + `gcodeparser` (legacy only) | PACKAGING_PLAN, `packaging/README.md` | Keep (pip-friendly entry points). Drop `gcodeparser` when `legacy/` goes. |
| `pyproject.toml` | explicit modules and packages; `test_packaging` enforces it | tests | Keep. |
| `packaging/` | self-contained; `test_packaging.py` covers it | tests, PACKAGING_PLAN | Right place. |
| `images/` (11 PNG + tracked `.DS_Store`) | README uses 8 | `README.md:9-89` | Keep for README. **Unreferenced:** `GRUB_full.png`, `GUI_chosen_port_controller.png`, `IMG_0609.png`. The GUI images show the old app, so README is stale against the Setup panel. Could move to `docs/images/`. |
| `.DS_Store` (root) and `images/.DS_Store` | **tracked** since `8b306d3` although `.gitignore:107` ignores them | `test_packaging.py:123` skips the name | `git rm --cached` both. |
| `scratch/` (2 files, 2026-09-18) | ignored by `.gitignore:84`. `update_docs.py` writes to a path that no longer exists | CLAUDE.md:105 rule | Delete. |
| `src/lib/` | holds only `__pycache__`; ignored by the `lib/` rule (`.gitignore:22`) | none | Delete. Also stale and pycache-only: `src/views/{pyside,tkinter}/`, `tests/{architecture,core,edge_cases,hardware,pyside,scripting,station,ui,views,web}/`, root `__pycache__/`, `src/.DS_Store`, `.coverage`. |
| `.gitignore` `lib/` | would silently ignore any future `lib/` anywhere | none | Anchor it (`/lib/`) or drop it; owner call. |
| `.venv/` (1.5 GB) | **`run.sh`, `run_macos.sh`, `run.bat` and `run_swap*.sh` use it ahead of `main/.venv`.** STATUS:32 and the skills say `main/.venv` | launchers | Delete if `main/.venv` is canonical. Otherwise the launchers and the skills disagree. |

## 6. src/model, src/devices and tools

- `model/idle.py` and `model/gamepad_input.py` (mixins): **placement is right.** An optional rename would make the kind obvious (`model/mixins/`).
- `model/plot_data.py`: pure CSV parsing and an Agg figure, used by `red_monitor.py` and now `transfer_map.py`. **Right.**
- `controller/setup.py` (1366) holds the model registry (`register`, `resources_of`, `MODEL_TYPES`) as well as the Setup panel. A `controller/registry.py` split would be a lead-only core change.
- **Nothing in `src/` is a script.** Only `app.py` has `__main__`.
- Tools living elsewhere: `tests/golden/capture.py` (926) and `old_frames.py` (146) are capture scripts that die with `legacy/`; `docs/implementation/gen_ledger.py` (see §4); STATUS:156 names an unmerged `tools/heater_plot.py`.
- `firmware/flash_firmware.py` (424): a host-side Python tool inside the sketch folder that imports legacy. Beside the sketches is defensible (`run_swap.sh:99` and `--sketch-root` find sketches relative to it). Better: `tools/flash_firmware.py`, ported to `controller/setup.py` (D-12). Moving it is **not** zero-risk.
- `packaging/`: right place.

## 7. Naming inconsistencies (real ones only)

| Where | Inconsistency |
|---|---|
| Event titles | `controller.py:246` `events.info("FULL STOP", …)` is shouted. `devices/gamepad.py` alone uses sentence case (19 titles) while every other file uses Title Case. `"Port Unverified"` is both `info` and `warn` (`serial_port.py:551, 554`). |
| Stop vocabulary | Code says `estop`/`toggle_estop`/`clear_estop_all`; the UI ruling is "Stop"; the event title is "FULL STOP". |
| Model names | Class `Heater` / file `heater.py` / NAME "Temperature Controller"; `RedMonitor` / "Red Percent"; `Rotator` vs device `SMC100`. Deliberate, but not stated anywhere. |
| colour / color | `tk.py` has both `_lamp_colours` (597) and `_lamp_colors` (4182), different meanings. Qt has `lamp_colours`, `lamp_colour`, `_severity_colour`; theme has `colors`/`toggle_colors`. |
| Private vs public helpers | Tk prefixes every module helper with `_`; Qt's equivalents are public because tests import them. |
| Test files | `test_core_theme.py` and `test_core_views_base.py` test `views/`. `test_core_device.py` tests `devices/`. `test_*_fakes.py` are not tests. |
| Firmware folders | `stepper_firmware/`, `chuck_firmware/`, `temp_controller/`, but the DC probe's sketch is `high_polling_rate/`. |
| Constants | Tk `SIMULATION_LINE` vs Qt `SIM_LINE`; `FAULTED_LINE` vs `FAULT_LINE`; `OVERVIEW_PAGE` vs `OVERVIEW`; `KEY_GLYPHS` a dict in Tk, a tuple of pairs in Qt. |

## Safe to do without an owner decision

1. `git rm --cached .DS_Store images/.DS_Store`.
2. Delete ignored, stale directories and files: `scratch/`, `src/lib/`, `src/views/{pyside,tkinter}/`, the ten pycache-only `tests/<old>/` directories, root `__pycache__/`, `src/.DS_Store`, `.coverage`.
3. Fix the broken link `bench-checklist.md:13` to `../archive/implementation/plan.md`.
4. Refresh the marker descriptions in `tests/pytest.ini:8-17`.
5. Correct `CLAUDE.md:22` / `STATUS.md` to list every legacy dependent.
6. Add a "ported to" column to `TEST_PORTING.md`, filled from the 57 docstring citations.
7. Replace Tk's literal `"Stop Not Confirmed"` / `"Idle Timeout Soon"` (`tk.py:164, 175`) with the `events.*` constants. Run the gates.
8. Move the pure, identical-today helpers to `views/words.py`: `sentence`/`sentence_case`, `tier_of`, `elide_middle`, idle countdown, `STOP_COMMANDS := Panel.UNGATED_COMMANDS`. Re-export from `qt`.
9. Test reshuffle, step 1 only: move `test_core_fakes.py` and `test_heater_fakes.py` to `tests/support/` and update 12 imports; check the collection count is unchanged.

## Questions for the owner

1. **Local `.venv` in `mvc-refactor`:** delete it so every launcher uses `main/.venv`, or keep it and correct STATUS and the skills?
2. **View wording that differs today** (D1, D2, D3, D4, D8, D10, D12, D13, D15, D16, D18, D19): for each, is the Tk/Web wording or the Qt wording canonical?
3. **Split the monoliths** into `views/tk/` and `views/qt/` packages now, with the ~40 monkeypatch and source-read test edits, or only after the shared-words extraction shrinks them?
4. **Test layout:** subject directories, or keep the files flat and only split the three giants by subject instead of by audit round?
5. **`gen_ledger.py`:** delete it or move it to `docs/archive/implementation/`?
6. **`docs/archive/architecture/*`** (8 pages, nothing points at them): keep as history or delete and rely on git?
7. **`docs/architecture/audit/*` + `root-causes.md`:** move to `docs/archive/` at `legacy/` deletion or keep permanently beside `carry.json`?
8. **`firmware/flash_firmware.py`:** leave it beside the sketches or move it to `tools/` when D-12 ports it off legacy?
9. **`images/` and README:** refresh README to the current Setup panel (and drop the 3 unused images) or freeze README as the `main`-branch procedure?
10. **DC firmware folder:** rename `high_polling_rate/` to `dc_firmware/` or leave the name?
11. **`.gitignore` `lib/`:** anchor it to `/lib/` or keep the broad rule?
12. **The `FULL STOP` info title:** rename it to "Stopped" or "Stop Confirmed", or keep it?
