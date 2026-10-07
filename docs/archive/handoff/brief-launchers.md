# Brief: one launcher per OS family, and the firmware check on the Setup page (branch `rb-launch`, worktree `../rb-launch`)

Base: `e8b857e` or later on `mvc-refactor`. Python:
`/Users/ianalbinogonzalez/Documents/GitHub/transfer-stage-unified/main/.venv/bin/python`.
Read `.claude/agents/worktree-fixer.md` first (its rules bind you even
though you were started as a general agent), then `run.sh`,
`run_macos.sh`, `run.bat`, `run_swap.sh`, `run_swap_macos.sh`,
`update.sh`, `update.bat`, `firmware/flash_firmware.py` in full (its CLI:
`--list`, `--only`, `--yes`, `--dry-run`, `--force`, `--stamp`,
`--no-detect`, `--sketch-root`; the stamp file
`~/transfer-stage-runs/flashed.json` and how a board's sketch hash decides
"out of date"), `src/controller/setup.py` (the Update row and its thread
are the pattern: a background check at construction, state values, two
commands, a tier-1 row first in `_build_schema`), `src/controller/updater.py`
(the subprocess pattern with injectable `run=`), `tests/test_setup.py`
(the Update tests), `README.md` "Software Initialization", `CLAUDE.md`
"Commands", `docs/rebuild/PACKAGING_PLAN.md`, `packaging/station.spec`
(the launchers it names).

## What the owner asked (2026-09-28)

"Consolidate the various run scripts, using OS detection, and retire any
system that doesn't use the SWAP check. Push any setup left in the
terminal to GUI indicators for a professional look."

## Design (the lead's; deviations go in the handoff with reasons)

L1. **Two launchers, not six.** `run.sh` (every POSIX OS; `uname -s`
    picks the macOS PySide6 repair block from today's `run_macos.sh`,
    which is a toolkit-forced branch and allowed) and `run.bat`
    (Windows). Both: find the venv (a local `.venv`, else the active
    one, else a clear message), then `python3 src/app.py "$@"`. They print
    NOTHING on success; a failure to find Python or the venv is the only
    terminal text. The firmware check LEAVES the launcher (L2).
    `run_macos.sh` and `run_swap_macos.sh` become two-line shims that
    `exec ./run.sh "$@"` with a comment "kept for old desktop shortcuts;
    delete after 2026-10-31". `run_swap.sh` is renamed `dev/swap_branch.sh`
    (a developer tool: run `main`'s app on the same boards; it keeps its
    own flash step because `main` has no Setup that flashes), with its
    header saying so. `update.sh` / `update.bat` stay.
L2. **The firmware check is a Setup row.** New `src/controller/firmware.py`:
    `class FirmwareCheck` that, without arduino-cli, works out each
    board's status from the stamp file and the sketch hashes exactly as
    `flash_firmware.py` does (import nothing from `firmware/`; the
    architecture test forbids it; copy the hash rule and pin it with a
    test against the script's own function run as a subprocess, so the
    two cannot drift silently), and that flashes by running
    `firmware/flash_firmware.py --yes --only <boards>` as a subprocess
    (injectable `run=`), streaming its lines into a state value.
    Setup gains a tier-1 row "Firmware", right after "Update": readonly
    "Boards" (`firmware_status`: "all current", "Stepper Probe out of
    date", "never flashed here", "arduino-cli not found: flash by hand",
    "checking…"), readonly "Flashing" (`firmware_progress`, the last line
    of the script while it runs, empty otherwise), button "Flash
    out-of-date boards" (`flash_firmware`, role go, confirm text naming
    the boards, refuses while launched, while a flash runs, and when
    nothing is out of date), button "Check firmware" (`check_firmware`).
    The check runs on a thread at construction (`STATION_NO_FIRMWARE_CHECK=1`
    in tests, autouse in `conftest.py`). **Launch refuses while a flash
    is running**, and WARNS (a `NeedsConfirm`, once) when a launched
    board is out of date: "Stepper Probe's firmware is out of date. Launch
    anyway?" — the operator may mean it (main's boards). Nothing ever
    flashes without the key being pressed.
L3. **Nothing else prints.** Audit what reaches the terminal on a normal
    launch of each view (`grep -rn "print(" src/`, the launchers, the Web
    view's URL line if any) and route each line to `events` (the log and,
    where it matters, the tray) or delete it. The Web view's "serving on
    http://…" line, if it exists, becomes a Setup readonly "Address" on the
    Web view only (a state value the other views leave empty).
L4. **Docs you own in this round:** `README.md` "Software Initialization"
    (two launchers, the Firmware row, `update.sh`), `CLAUDE.md`
    "Commands" and its launcher line, `docs/rebuild/PACKAGING_PLAN.md`
    where it names launchers, `packaging/station.spec` only if it names a
    retired script. `docs/rebuild/STATUS.md` is the lead's.
L5. **Tests first.** `tests/test_firmware_check.py` (status from a seeded
    stamp file and a sketch tree in `tmp_path`; the flash subprocess
    mocked; the hash rule pinned against the script), `tests/test_setup.py`
    (the row first after Update, refusals, the launch confirm),
    `tests/test_launchers.py` (new: `bash -n` on `run.sh`, the shims exec
    `run.sh`, `run.sh --help` under a fake `uname` for Darwin and Linux
    reaches `src/app.py --help` without printing anything else: drive it
    with a stub `python3` on PATH that records argv).

## Write set (exclusive)

- `run.sh`, `run.bat`, `run_macos.sh`, `run_swap.sh` (-> `dev/swap_branch.sh`),
  `run_swap_macos.sh`, NEW `src/controller/firmware.py`,
  `src/controller/setup.py` (the Firmware row, its thread, its two
  commands, the Launch confirm only: another agent adds
  `restart_station` next to `apply_update`, so keep your hunks in the
  schema builder and a new block of your own), `src/views/web/server.py`
  only if L3 needs its URL line moved, `README.md`, `CLAUDE.md`,
  `docs/rebuild/PACKAGING_PLAN.md`, `packaging/station.spec`
- NEW `tests/test_firmware_check.py`, NEW `tests/test_launchers.py`,
  `tests/test_setup.py`, `tests/conftest.py` (additive)

Not yours: `firmware/**` (read it, never edit it), `src/model/**`, the
views' Python files, `src/events.py`, `docs/rebuild/STATUS.md`.

## Gates before you commit

- `STATION_NO_WINDOWS=1 <PY> -m pytest tests -q -p no:cacheprovider -m "not qt"`
  to a file under your scratch dir, exit code unpiped: all green.
- Golden: 78.
- `./run.sh --help` and `RUN_SWAP_DRY_RUN=1 dev/swap_branch.sh main` on
  this Mac print what they should and nothing else. A headless Web launch
  on port 8101 shows the Firmware row in `/api/setup`. Nothing on screen.

## Handoff

`handoff/fix-launchers.md` in the worktree-fixer shape. Commit on
`rb-launch`; never push.
