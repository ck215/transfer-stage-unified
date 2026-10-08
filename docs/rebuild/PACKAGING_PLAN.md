# Packaging plan — one portable build per platform, one launcher

> Updated 2026-10-07. The Web view is the station's only frontend (owner
> ruling), so the bundle has ONE launcher, `station-web`; the Tk and Qt
> launchers, their Qt plugin code and the "three versions" below are history
> (kept where they record a decision, marked as superseded). `packaging/README.md`
> is the current bundle contract.

Written 2026-09-25 from the owner's ask: "what would it take to package
this into a portable application for the three platforms, with three
versions of the app, one per view" (the three versions are superseded; see the update note). Routes per step follow the delegation
rule (direct / `router` / `agy`); bench and account matters are the owner's.

## What ships

| Piece | Weight | Needed by |
|---|---|---|
| `src/` + Web assets (`views/web/static/`, three Plex woff2) | 3 MB + 212 KB | all |
| pyserial, mss, Pillow, numpy, matplotlib (Agg only) | ~60 MB | all |
| pygame (SDL2, gamepad) | ~15 MB | probes' gamepad |
| ~~Tcl/Tk (Tk 9 with Python 3.14)~~ | ~15 MB | retired 2026-10-07: not bundled |
| ~~PySide6 Essentials + Addons + Qt plugins~~ | 150–250 MB | retired 2026-10-07: not bundled |
| `firmware/flash_firmware.py` | shells out to an Arduino CLI we do not ship | not packaged (bench tool) |

`gcodeparser` served the removed `legacy/` tree only (scripts are purged
from `src/`); it left the runtime requirements, and survives only in
`packaging/requirements-stable.txt` for the frozen stable app.

## Decisions

- **D-9 amendment (owner, 2026-09-25): Tk is the default on every OS** (superseded: DEFAULT_VIEW became qt on 2026-09-28, then Web, the only view, on 2026-10-07)
  ("simple and lightweight and local"). It removes the last visible platform
  branch (audit P8); packaging bakes it in. `--web` / `--qt` stay explicit.
- **One bundle per platform** (originally three entry points, `station-web`,
  `station-qt`, `station-tk`; only `station-web` remains since 2026-10-07), not three packages: the views share every
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
| P5 | **Done 2026-10-07 (rb-release).** `.github/workflows/package.yml`: a tag `v*` (cut by `dev/release.sh`) or `workflow_dispatch` runs three jobs. **draft** creates the release as a DRAFT with the tag's CHANGELOG section as notes; **build** runs one runner per target in `release.json` (`macos-14` arm64, `macos-15-intel`, `windows-latest`, `ubuntu-22.04`) on Python 3.13: `pip install -e .[dev]`, the tools (`packaging/tools.py`), the stable app (`packaging/stable.spec`), the station (`packaging/station.spec`, `STATION_REQUIRE_FULL=1`, stamping `VERSION` and `release.json`), the headless smoke of `station-web` and the zip, uploaded to the draft; **publish** adds `SHA256SUMS` and publishes as latest only when every build passed and every asset is on the draft. Unsigned (P7). The bundle's updater (`controller/updater.py`) compares versions, checks size and SHA-256 and swaps `<install>` -> `<install>.previous`; a checkout's fast-forwards only to a release tag. The step-by-step flow, the branch model and rolling back are in `packaging/README.md` ("Releases"). Not yet run on GitHub as of 2026-10-07. | `.github/workflows/package.yml` | direct |
| P6 | Platform plumbing, bench: Windows USB-serial drivers and COM enumeration; Linux `dialout` group and udev; macOS Gatekeeper first-run (right-click Open) until P7; the region picker on a scaled display (B8). | notes in `docs/rebuild/bench-checklist.md` | owner |
| P7 | Signing: Apple Developer ID + notarization (`codesign --deep --options runtime`, `notarytool`), Windows Authenticode or an accepted SmartScreen warning. An account and a certificate, not code. | signed artifacts | owner (account) → direct (CI steps) |
| P8 | **Done 2026-09-28 (rb-bundle), amended 2026-10-07.** README "Install": download the zip for your OS from the latest GitHub Release, unzip it where it will stay, run `station-web` (Gatekeeper right-click Open / SmartScreen Run anyway until P7); updates need the machine signed in to GitHub once (`gh auth login`, or git's credential helper), then Update now and Restart on the Setup panel. The lab deploys by git until the first installed release (after v1.0.0): `git fetch && git checkout main`, `pip install -e .`, `run.sh`. | README | direct |

## Branches and releases (owner, 2026-10-07)

`main` is the station, the pre-release line; it receives pull requests from
short-lived branches (`feat/*`, `fix/*`, `agent/*`); `mvc-refactor` is deleted
once merged into it. A release is a tag `vX.Y.Z` on main, cut with `dev/release.sh`
(CHANGELOG's Unreleased moves under the tag); the tag starts `package.yml`,
which builds, uploads `SHA256SUMS` and publishes the draft itself. `legacy`
is the lab's original Tk app, frozen; `stable` is the original app's
packaging ref (frozen into the bundle as `stable/`). `gate.yml` runs the fast
gate, golden and a Web launch on PRs and pushes to main (the mvc-refactor trigger goes away with the branch). The
version is the tag (`pyproject.toml` says 0.0.0). Details, the release
steps and rollback: `packaging/README.md`.

## Order and effort

P1 today (one line plus tests). P2–P4 in one `agy` worktree (`rb-pack`),
about a day, proven on this Mac. P5 direct, half a day, once P4 passes
locally. P6–P8 follow the first artifact on the lab Windows PC.

## Risks

- Every device library is native (SDL, Qt, Tcl/Tk, mss's screen backends):
  each bundle needs one real launch on real hardware before it counts. The
  smoke test is necessary, not sufficient.
- (Retired 2026-10-07 with the Qt launcher: PySide6 plugin discovery inside a bundle.)
- Unsigned macOS bundles are blocked by Gatekeeper on first run; Windows
  SmartScreen warns. Both are P7, and P7 is money and an account.
- (Retired with the Tk view, 2026-10-07: `--tk` on Windows needed the Tcl/Tk
  DLLs from the python.org build.) The CI interpreter stays pinned to
  `actions/setup-python`.

## Acceptance (before any artifact goes to the lab)

Gates as `verify` (fast, golden) on the source tree; P4 smoke on the
bundle for each OS; the screenshot ritual run FROM the bundle for the Web view;
the stop path exercised from each bundle in SIM.
