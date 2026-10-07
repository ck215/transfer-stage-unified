# Relayout handoff

Worktree rb-relayout, base 8228991. Two commits: 4927f63 (legacy move), 4bba115 (src relayout). Not pushed.

## Baseline   (gate — count, on base SHA 8228991)
- `pytest tests/station -m "not qt"` — 1498 passed, 85 deselected
- `pytest tests/station/test_wire_golden.py` — 78 passed
- old suite `pytest tests/ -m "not slow and not order_dependent and not qt" --ignore=tests/station` — 1038 passed, 1 skipped, 89 deselected, 1 xfailed, 1 warning

## After      (gate — count, on 4bba115)
- `pytest tests -m "not qt"` — **1528 passed**, 85 deselected. The +30 is exactly the new rule `test_nothing_in_src_imports_legacy`, parametrised over the 30 .py files in src/. `-k "not legacy"` on test_architecture gives the old 54; everything else matches 1:1.
- `pytest tests/test_wire_golden.py` — 78 passed
- `cd legacy && pytest tests -m "not slow and not order_dependent and not qt"` — 1038 passed, 1 skipped, 89 deselected, 1 xfailed. The baseline's "1 warning" is a compile-time SyntaxWarning (`"\|"` in a docstring, legacy/tests/core/test_manager22_clear_estop_is_reachable.py:8). It shows up only when the .pyc is recompiled, so a cached run doesn't print it. It is not a test change.
- `import app, controller.controller, model.base, model.probe, views.base, devices.serial_port` with src on the path: OK. `python3 -c "import station"`: ModuleNotFoundError.
- Importing `tests.golden.capture` in-process leaves legacy/src off sys.path and imports no model/controller/lib/views module (checked).

## Renames    (git diff -M --stat summary)
- 4927f63: 184 renames, 0 adds and 0 deletes. 183 are R100. The one exception is test_serial10_power_down_truth.py at R099 (see below). legacy/src/lib/{smc100,toupcam}.py are still tracked: they were force-added because .gitignore has `lib/`.
- 4bba115: 78 renames, 3 M (launchers), 3 A, 1 D.
  - A tests/pytest.ini: new.
  - A tests/golden/old_frames.py: new; the code in it was moved out of two test files.
  - A tests/conftest.py plus D tests/station/conftest.py: git doesn't pair these because the new file is mostly the ported harness (see below).
  - src/controller/__init__.py is new and empty. Git pairs it with the deleted, empty station/__init__.py. Nothing was created at src/__init__.py.
  - Lowest similarities: test_architecture R073, test_probe_frames R076, test_heater_frames R088.
- The legacy move is its own commit on purpose. The old src/app.py and the new src/app.py (and model/base.py, model/plot_data.py, views/web/__init__.py, index.html) share paths, so with one commit git reports them as modifications plus adds instead of renames.

## Non-import hunks   (file:line — what — why)
1. **tests/test_probe_frames.py and tests/test_heater_frames.py → tests/golden/old_frames.py.** The brief did not list this. Both tests imported the OLD `model.probes` and `model.temperature_system` in-process, which only worked because the old pytest.ini put old src on the path. The old and new `model` packages can't share a process, the same problem as capture.py. Fix: the old-side code (`_OldPoller`, `_old_sequences`, `_old_frames`) moved verbatim into golden/old_frames.py. Only `LEVELS`/`DISTANCES` became parameters and the TemperatureSystem import moved inside the function. The tests call it with `sys.executable` and compare the same bytes (sent as hex). Every assertion is unchanged. `_old_frames` is lru_cached (one subprocess per value set).
   - Side effect: `test_no_test_here_left_a_reader_thread_behind` no longer checks anything, because the old readers now live, and die, in the subprocess.
2. **tests/conftest.py harness port.** The brief did not list this either. As tests/station, the suite inherited the old tests/conftest.py as its parent conftest. test_view_tk.py says in its docstring that it is written against that file's tkinter stand-in; without it, 30 errors and 1 failure. I copied these parts verbatim from legacy/tests/conftest.py:
   - the tkinter stand-in (old lines 313-413)
   - `QT_QPA_PLATFORM=offscreen` and the macOS QApplication subprocess probe (lines 5-54)
   - the qapp/qtbot → `qt` auto-mark and skip, which is the Qt-only part of `pytest_collection_modifyitems`
   
   **Not ported:** the old MagicMocks for pygame, serial, PIL, mss and matplotlib, and the old-code autouse fixtures (thread tracking, ErrorRouter reset, which target legacy classes). The suite passes at the same count against the real libraries, which station-map trap 2 says is the intent. It is still a change in test conditions: those tests used to run under those mocks. Lead's call whether to keep it that way.
3. tests/conftest.py:ROOT — `"..", ".."` → `".."`, because the file is now one level up.
4. tests/test_view_web_client.py:25-26 and tests/test_smc100_frames.py:25-26 — `parents[2]/"station"` → `parents[1]/"src"` and `parents[2]/"src"` → `parents[1]/"legacy"/"src"`. Path roots after the move.
5. legacy/tests/hardware/test_serial10_power_down_truth.py:31 — `parents[2]` → `parents[3]` for `firmware/`. The file is one directory deeper; without this, 2 legacy tests fail.
6. tests/test_wire_golden.py — `_recapture()` subprocess helper, plus imports of subprocess and sys, as the brief asked.
7. tests/golden/capture.py:
   - `_use_legacy_src()` is called from `capture_file` and never at import.
   - `_ROOT` is now two levels up.
   - New `print_capture` and `__main__ <file>`. While capturing, stdout points at stderr, and it stays that way, because the old code prints (including from destructors at exit).
   - `captured_from` → "legacy/src/ …" and `regenerate_with` → "python3 tests/golden/capture.py".
   - **Conflict in the brief, resolved like this:** the three JSON files are byte-for-byte unchanged, so their `_meta` still says `src/` and `tests/station/golden/capture.py`. The currency test only compares scenarios and counts; it already tolerated `_meta` drift before this change. The next regeneration will update those two `_meta` strings and no wire bytes. If you'd rather the stored `_meta` match now, regenerate; the scenarios won't change.
8. test_architecture:
   - Rule 1 prefixes are now `model, devices, legacy, src`. The old `station.model` also matched `station.models`; bare `model` covers both.
   - Rule 2 selects `model/`, `devices/` and `panel.py` and bans the prefixes `views` and `controller`. The bare `controller` prefix now also bans `controller.setup`, which is slightly stronger than the old `station.controller`.
   - Rule 5 is new: nothing under src/ imports legacy.
9. Docstrings and comments that pointed at the old tree say `legacy/src/…`; those that pointed at station/ say `src/…`. This includes three comment lines in views/web/static/{app.js,index.html,styles.css}. Rewrites are mechanical and prose only.
10. tests/TEST_PORTING.md: a 3-line banner and nothing else.

## Runtime artefacts left alone   (`grep -rn station src tests`)
What remains:
- `station-%Y%m%d-%H%M%S.log` (events.py:93)
- `prog="station"` (app.py:133)
- `server_version = "station"` (web/server.py:70)
- `*_station_meta.json` and `_station_meta()` (red_monitor.py, plot_data.py, and the tests that write these sidecars)
- CSS `.station-name` and `class="station-name"`
- JS global `window.station` (app.js:1370-1371). This is a runtime name, so left alone.
- The `tempfile.mkdtemp(prefix="station-web-")` in a test
- Prose that says "the station" / "station's" / "a station"
- Test identifiers: the pytest fixture `station` (test_view_web_server, test_core_views_base), the alias `station_setup` (`from controller import setup as station_setup`), and test names like `test_the_whole_station_state…`. Renaming these would be churn, not package references.
- The two `_meta` strings in the three golden JSONs (item 7)
- TEST_PORTING.md body, which is untouched by instruction
- The tests/conftest.py comment explaining where the harness came from

No `"station.` or `'station.` string is left anywhere in src/ or tests/.

## Launch
`$PY src/app.py --web --no-browser --port 8081`. `GET /api/setup` returned the setup schema JSON (`{"schema": {"version": 2, "sections": [{"title": "Devices", ... "Refresh" ... "Scan:" ...`), and `GET /` returned 200. I stopped it with `kill <pid>` and confirmed it exited. `$PY src/app.py --help` shows the new epilog.

## UNVERIFIED
- The Qt pass was not run: the 85 qt-deselected tests, mainly test_view_qt_widgets.py (qapp) and the qt-marked parts of test_view_qt.py. Their harness (offscreen, probe, auto-mark and skip) is ported verbatim, but the lead needs to run `pytest tests -m qt`.
- Launchers were not executed; they are a one-line change each and match 91b5dc9.
- Old-suite slow and order_dependent passes were not run, same as the baseline.

## Blocked
- Nothing outside my write set was touched. Out of scope, for docs-pruner: docs/**, README.md and CLAUDE.md still mention `station/`, `tests/station` and `python -m station.app`. The machine-read `docs/rebuild/*.json` and `design.rules` were not updated, per the rules.

COMMIT: 4927f63, 4bba115
