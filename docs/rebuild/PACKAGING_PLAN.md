# Packaging plan — one portable build per platform, three launchers

Written 2026-09-25 from the owner's ask: "what would it take to package
this into a portable application for the three platforms, with three
versions of the app, one per view". Routes per step follow the delegation
rule (direct / `router` / `agy`); bench and account matters are the owner's.

## What ships

| Piece | Weight | Needed by |
|---|---|---|
| `src/` + Web assets (`views/web/static/`, three Plex woff2) | 3 MB + 212 KB | all |
| pyserial, mss, Pillow, numpy, matplotlib (Agg only) | ~60 MB | all |
| pygame (SDL2, gamepad) | ~15 MB | probes' gamepad |
| Tcl/Tk (Tk 9 with Python 3.14) | ~15 MB | `--tk` only |
| PySide6 Essentials + Addons + Qt plugins | 150–250 MB | `--qt` only |
| `firmware/flash_firmware.py` | shells out to an Arduino CLI we do not ship | not packaged (bench tool) |

`gcodeparser` in `requirements.txt` is `legacy/` only (scripts are purged
from `src/`); it leaves the runtime requirements.

## Decisions

- **D-9 amendment (owner, 2026-09-25): Tk is the default on every OS**
  ("simple and lightweight and local"). It removes the last visible platform
  branch (audit P8); packaging bakes it in. `--web` / `--qt` stay explicit.
- **One bundle per platform, three entry points** (`station-web`,
  `station-qt`, `station-tk`), not three packages: the views share every
  library except Qt, so three packages would ship the same ~100 MB three
  times. A `--view` flag on one binary is the same thing with one icon.
- **Tool: PyInstaller** (6.22.3 added Python 3.14 support, 2026-09). Built
  ON each platform; nothing here cross-compiles. Briefcase is the fallback
  for real installers, but its Windows embeddable Python has no tkinter, so
  it cannot produce the Tk version there.
- **Interpreter:** 3.14 is three months old. If any hook misbehaves, pin the
  build interpreter to 3.13; the app has no 3.14-only code.

## Steps

| # | Step | What it produces | Route |
|---|---|---|---|
| P1 | **Done 2026-09-25.** D-9 amendment: default view Tk on every OS; `pick_view` loses its platform branch; `run_macos.sh` no longer announces Web while starting Tk (D16; since 2026-09-28 `run_macos.sh` is a shim to `run.sh`, which carries the macOS PySide6 repair); `app.main_tk/main_qt/main_web` entry points added for P2; tests in `tests/test_app.py`. | commit on `mvc-refactor` | done (direct) |
| P2 | **Done 2026-09-25.** `pyproject.toml` (`transfer-stage-station`, three console scripts `station-tk/qt/web`, runtime deps pinned, PySide6 as the `qt` extra, gcodeparser out), `requirements.txt` = `-e .[qt]`, `requirements-dev.txt`; `tests/test_packaging.py`. | merged | done (`agy`) |
| P3 | **Done 2026-09-25** on this Mac: `dist/station/` 134 MB (Qt part 47 MB after pruning virtual-keyboard/PDF/TUIO plugins and translations; first build 159 MB), three 8.6 MB launchers. Findings: pygame on Python 3.14 here is built on sdl2-compat and needs `libSDL3.dylib` shipped beside it or `import pygame` hangs behind an invisible dialog; the macOS hidden flag does not survive into `dist/`; builds take ~13 min under Seafile-synced `~/Documents` and cannot run two at once. Original row: `packaging/station.spec`: one PyInstaller spec, three `EXE`s over one `COLLECT` (one-folder mode: one-file mode unpacks 250 MB to a temp dir on every launch and trips antivirus). Data: `views/web/static/**`. Hooks: pygame (SDL dylibs, hidden imports), PySide6 (platform plugins, `QT_QPA_PLATFORM_PLUGIN_PATH` set at runtime; the macOS `UF_HIDDEN` quirk `run.sh` clears on macOS (formerly `run_macos.sh`) must be checked inside the bundle), matplotlib with `backend_agg` only (exclude the Qt/Tk backends it would otherwise pull), mss, `tkinter` with Tcl/Tk data. Excludes: tests, legacy, docs, pytest. Run log path and run output stay under `~/transfer-stage-runs/` (already outside the bundle). | the spec; `dist/station/` on this Mac | `agy` |
| P4 | **Done 2026-09-25** (`smoke.sh` 42 checks; passed on the CCR-1-fixed tree; `smoke.ps1` written, unrun). It found a safety defect: under Tk on macOS a SIGTERM skipped `Controller.close()` (Tk 9 installs its own handler); fixed in core the same day (`Controller.hook_signals()`, re-armed after the view is built). A clean SIGTERM stop is exit 143, not 0; the Web launcher exits 0 via `/api/quit`. Original row: Bundle acceptance test, scripted (`packaging/smoke.sh` / `.ps1`): each entry launches; the Web one serves and answers `/api/state`; SIM launch of all six models; the stop latches; `POST /api/quit` exits 0; log written; gamepad hub opens and closes cleanly; serial enumeration lists ports. | script + a passing run on this Mac | `agy` |
| P5 | **Written 2026-09-28 (rb-bundle), not yet run on GitHub.** `.github/workflows/package.yml`: on a tag `v*` (and `workflow_dispatch`) a DRAFT release is made from the tag's message; a matrix `macos-14` (arm64), `macos-13` (x86_64), `windows-latest`, `ubuntu-22.04` on Python 3.13 (`actions/setup-python`) runs `pip install -e .[qt,dev]`, patches the working copy's pyproject version from the tag, builds the spec (which stamps `VERSION` = tag / commit / build time and `release.json` = owner, repo, asset naming beside the launchers via `packaging/release.py`), smokes headless (Web and Tk, Tk under `xvfb-run` on Linux; Qt offscreen), zips `station-<os>-<arch>.zip` (links and modes kept) and uploads it to the draft; a last job appends every asset's SHA-256 to the notes and publishes the draft as latest. Unsigned (P7). The bundle's updater (`controller/updater.py`, frozen path) reads the latest release with the machine's own GitHub sign-in (`gh auth token`, else `git credential fill`; no token file, owner 2026-09-28), downloads its asset, checks size and SHA-256, unpacks to `<install>.next` and swaps `<install>` -> `<install>.previous`. Original row: Continuous builds: GitHub Actions matrix `macos-14` (arm64), `macos-13` (x86_64), `windows-latest`, `ubuntu-22.04`; artifacts `station-<os>-<arch>.zip` on every tag; the smoke script runs in CI where it can (Web and Tk headless; Qt offscreen). | `.github/workflows/package.yml` | direct |
| P6 | Platform plumbing, bench: Windows USB-serial drivers and COM enumeration; Linux `dialout` group and udev; macOS Gatekeeper first-run (right-click Open) until P7; the region picker on a scaled display (B8). | notes in `docs/implementation/bench-checklist.md` | owner |
| P7 | Signing: Apple Developer ID + notarization (`codesign --deep --options runtime`, `notarytool`), Windows Authenticode or an accepted SmartScreen warning. An account and a certificate, not code. | signed artifacts | owner (account) → direct (CI steps) |
| P8 | **Done 2026-09-28 (rb-bundle).** README "Install": download the zip for your OS from the latest GitHub Release, unzip it where it will stay, run the launcher for the view you want (Gatekeeper right-click Open / SmartScreen Run anyway until P7); updates need the machine signed in to GitHub once (`gh auth login`, or git's credential helper), then Update now and Restart on the Setup panel; the source install stays for developers. | README | direct |

## Order and effort

P1 today (one line plus tests). P2–P4 in one `agy` worktree (`rb-pack`),
about a day, proven on this Mac. P5 direct, half a day, once P4 passes
locally. P6–P8 follow the first artifact on the lab Windows PC.

## Risks

- Every device library is native (SDL, Qt, Tcl/Tk, mss's screen backends):
  each bundle needs one real launch on real hardware before it counts. The
  smoke test is necessary, not sufficient.
- PySide6 plugin discovery inside a bundle is the classic failure; the Qt
  entry point must set the plugin path itself and log it at startup.
- Unsigned macOS bundles are blocked by Gatekeeper on first run; Windows
  SmartScreen warns. Both are P7, and P7 is money and an account.
- `--tk` on Windows needs the Tcl/Tk DLLs PyInstaller collects from the
  python.org build; a Microsoft Store Python will not do. Pin the CI
  interpreter to `actions/setup-python`.

## Acceptance (before any artifact goes to the lab)

Gates as `verify` (fast, golden, Qt) on the source tree; P4 smoke on the
bundle for each OS; the screenshot ritual run FROM the bundle for each view;
the stop path exercised from each bundle in SIM.
