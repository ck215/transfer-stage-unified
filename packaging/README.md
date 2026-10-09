# packaging/

One portable bundle per platform (`docs/rebuild/PACKAGING_PLAN.md`): the new
station's one launcher (`station-web`; the Tk and Qt launchers were retired on 2026-10-07), its firmware, the tools to compile and flash that
firmware with no Python and no network, and a frozen copy of the lab's
original Tk app (the `stable` branch) with its own firmware, so the operator
can switch between the two while the refactor is validated.

| File | What |
|---|---|
| `station.spec` | PyInstaller spec: one Analysis, one EXE (`station-web`), one COLLECT -> `dist/station/`; then assembles the layout below and stamps it |
| `entry_web.py` | the one launcher's script; it calls `app.main_web` |
| `layout.py` | **the bundle layout contract** and the copies that build it (`assemble`); reads the board table from `firmware/flash_firmware.py` with `ast` |
| `tools.py` | fetches arduino-cli, the cores, the libraries and teensy_loader_cli at build time into `build/tools` (`fetch`, `check`, `size`) |
| `stable.spec` `entry_stable.py` | freezes the `stable` branch's `src/mainGUI.py`, unmodified, into `dist/station-stable/` |
| `requirements-stable.txt` | what the stable freeze needs beyond the station's own dependencies (`gcodeparser`) |
| `spec_helpers.py` | shared by both specs: SDL3 beside pygame's sdl2-compat shim, the macOS `UF_HIDDEN` clear |
| `hooks/hook-panel.py` | stops pyinstaller-hooks-contrib's HoloViz `panel` hook firing on the station's own `panel.py` |
| `smoke.sh` | bundle acceptance test, macOS / Linux (P4) |
| `smoke.ps1` | the same for Windows (written, not yet run) |
| `release.py` | the version of a tree (`version`, the one version string), stamps `VERSION` and `release.json` into a bundle (the spec calls it), patches a build's pyproject version from the tag, names this machine's asset and every asset a release carries (`asset-name`, `assets`), zips the bundle with links and modes kept, gives a release's notes (`notes`) and cuts CHANGELOG.md for `dev/release.sh` (`changelog`) |
| `release.json` | the template: owner and repo left empty (filled at build time), the asset pattern `station-{os}-{arch}.zip`, its OS/arch lookups, and `targets`: the four builds a release carries (the one place that names the assets) |

## The layout (`layout.py`; `src/` consumes it, neither side changes it without the lead)

Relative to the bundle root `dist/station/`:

```
station-web   (.exe on Windows)                          the new app's launcher
_internal/                                               PyInstaller's
VERSION  release.json                                    release.py's stamps
firmware/<sketch dirs>/  firmware/libraries/             the repo's firmware/, byte-identical
                                                         (flash_firmware.py included; no
                                                         __pycache__, no sketch build/)
tools/arduino-cli(.exe)                                  arduino-cli 1.5.1 for this OS/arch
tools/arduino-cli.yaml                                   data/downloads/user inside arduino-data/
tools/arduino-data/                                      arduino:avr 1.8.8, teensy:avr 1.62.0,
                                                         AccelStepper, TMCStepper,
                                                         LiquidCrystal_I2C, MAX6675
tools/teensy_loader_cli(.exe)                            the Teensy upload (flash_firmware.py
                                                         uses it, not arduino-cli's upload)
tools/tools.json                                         what tools.py installed: versions,
                                                         FQBNs, where the loader came from
stable/station-stable(.exe)  stable/_internal/           the frozen stable app
stable/firmware/<sketch dirs>/                           that branch's firmware/
stable/SOURCE                                            the ref and commit it was frozen from
```

**arduino-cli resolves the config's relative paths against the working
directory, not the config file.** Run it as
`cd <bundle>/tools && ./arduino-cli --config-file arduino-cli.yaml ...`,
or set `ARDUINO_DIRECTORIES_DATA`, `ARDUINO_DIRECTORIES_DOWNLOADS` and
`ARDUINO_DIRECTORIES_USER` to absolute paths inside `tools/arduino-data/`.
Run from anywhere else with neither, it tries to create `arduino-data/`
wherever it is.

## Build (on the platform you are building for; nothing cross-compiles)

In this order, from the repo root (the workflow does the same):

```
python3 -m pip install -r requirements-dev.txt -r packaging/requirements-stable.txt

# 1. tools (the only step that needs the network; ~3 min)
python3 packaging/tools.py fetch build/tools
python3 packaging/tools.py check build/tools            # offline: CLI, cores, libraries

# 2. the stable app, from a checkout of the stable ref (never edited)
mkdir -p build/stable-src && git archive origin/stable | tar -x -C build/stable-src
STATION_STABLE_REF=stable STATION_STABLE_SHA=$(git rev-parse origin/stable) \
    python3 -m PyInstaller --noconfirm --clean packaging/stable.spec

# 3. the station; assembles firmware/, tools/ and stable/ beside the launchers
STATION_REQUIRE_FULL=1 python3 -m PyInstaller --noconfirm --clean packaging/station.spec
packaging/smoke.sh                                       # or: packaging\smoke.ps1
```

Without `STATION_REQUIRE_FULL=1` a station build with no staged tools or
stable app still succeeds and says which part it left out (a quick local
build); the workflow sets it, so a release can never ship without them.
`STATION_TOOLS_DIR` and `STATION_STABLE_DIST` point the assembly elsewhere;
`STATION_STABLE_SRC` points `stable.spec` at another checkout.

The stable ref is `stable`, the original app's packaging ref. `origin/main` is
the station; the lab's original app is `legacy` (`dev/swap_branch.sh legacy`
runs it from `../legacy-app`); whether `origin/stable` carries that app's latest changes is
unverified, so use whichever ref you mean in step 2 for a local build (the
workflow's `stable_ref` input does the same).

Output: `dist/station/` as above. Ship the whole folder, zipped
(`release.py zip`). The repository's checkouts live under
`~/GitHub/transfer-stage-unified/` (the one checkout, `main/`, holds the one venv, made from
pyproject's `[dev]` extra). One-folder only: one-file mode unpacks the whole bundle to
a temp dir on every launch and trips antivirus.

## Sizes (macOS arm64, measured 2026-09-30, with the three-launcher bundle)

The station row below predates the single-launcher bundle (RET-3, which drops
PySide6 and tkinter); it has not been re-measured.

| Part | Unpacked |
|---|---|
| `_internal/` + launchers (the station) | 161 MB |
| `tools/` | 922 MB |
| - `arduino-data/packages/teensy` (teensy-compile, the ARM toolchain, alone is 360 MB) | 520 MB |
| - `arduino-data/packages/arduino` (avr-gcc 215 MB) | 264 MB |
| - `arduino-data/library_index.json` | 56 MB |
| - `arduino-cli` | 36 MB |
| `stable/` | 53 MB |
| `firmware/` | 0.5 MB |
| **bundle** | **1.16 GB** |
| **release zip** (`release.py zip`) | **398 MB** |

The other three platforms are estimates until the workflow runs (the
toolchains dominate and are of the same order): about 1.1-1.3 GB unpacked,
350-450 MB zipped.

On macOS arm64, arduino:avr's `avr-gcc` and `avrdude` are x86_64 binaries:
compiling and flashing the Mega boards needs Rosetta 2. The Teensy toolchain
is native arm64.

## The tools step (`tools.py`)

- arduino-cli **1.5.1**, the release asset for the build's OS/arch, checked
  against the SHA-256 in the release's published checksum file (the table is
  pinned in `tools.py`, never fetched beside the archive).
- `core update-index`, then `arduino:avr@1.8.8` and `teensy:avr@1.62.0`
  (with the pjrc additional index, also written into the config), then every
  library `firmware/flash_firmware.py` names, pinned, plus **MAX6675** (Rob
  Tillaart's): `temp_controller.ino` includes `MAX6675.h` and no core ships
  it, whatever the flasher's comment says. A library the flasher names with
  no pin in `tools.py` stops the build.
- **teensy_loader_cli** publishes no binaries: `tools.py` downloads the one C
  file at tag 2.3's commit, checks its SHA-256 and compiles it with the
  upstream Makefile's flags (IOKit on macOS; libusb-0.1 on Linux, apt
  `libusb-dev` at build time and `libusb-0.1-4` on the machine that flashes;
  MinGW `gcc` on Windows). The Teensy core's own `teensy-tools` has no
  command-line loader.
- The download cache (`arduino-data/staging`) is deleted after the install;
  an offline compile reads only the installed cores and libraries.

## The stable app (`stable.spec`, `entry_stable.py`)

The stable sources are frozen as they are. `entry_stable.py` holds the
shims: `multiprocessing.freeze_support()` first (mainGUI starts each device
window as a `spawn` child, which in a bundle is this same executable re-run),
then mainGUI run as `__main__` through runpy, exactly as `python mainGUI.py`.
`station-stable --self-check` imports every stable module and exits without a
window; the smoke uses it. Its third-party imports are pyserial, pygame,
Pillow, mss, numpy and gcodeparser; `camera_control.py` and `lib/toupcam.py`
are not reachable from mainGUI and are left out, and nothing imports cv2 or
matplotlib.

`*.spec` is gitignored; `station.spec` has an exception line in `.gitignore`
and `stable.spec` was added with `git add -f` (the exception line for it is
the lead's to add).

## Things the spec handles that are easy to break

- **Views are imported by name** (`app.VIEWS`, `importlib`), so the view
  module is a hidden import (`WEB_HIDDEN` in `station.spec`). A new view would
  need a line there.
- **Web assets** go to `views/web/static`, where `server._STATIC_DIR`
  (`__file__`-relative) finds them.
- **matplotlib is Agg only**; every GUI backend is excluded, and PySide6,
  shiboken6, tkinter and the frozen `views.tk` / `views.qt` are excluded too,
  so neither toolkit lands in `station-web`.
- **No Qt, no Tk in the bundle** (RET-3, 2026-10-07): the Qt plugin pruning
  and plugin-path code of the earlier three-launcher bundle went with the
  launchers. The frozen views stay in the source tree only.
- **pygame on macOS / Python 3.14** is built against Homebrew's
  `sdl2-compat`, whose libSDL2 `dlopen`s SDL3 by name. PyInstaller cannot
  see that; without SDL3 the launcher shows a modal "Failed loading SDL3"
  alert from inside `import pygame` and hangs silently. Both specs ship
  `libSDL3.dylib` beside the shim (`spec_helpers.py`). A pygame wheel with a
  real SDL2 skips it.
- **macOS `UF_HIDDEN`**: pip-installed dylibs carry the hidden flag and the
  copy into `dist/` keeps it; loaders that skip hidden files then fail. Both
  specs run `chflags -R nohidden` on their bundle after COLLECT.
- **firmware/, tools/, stable/ are copied, not declared as datas**:
  PyInstaller 6 puts every data file under `_internal/`, and the layout wants
  them beside the launchers.

## Releases

A version is a git tag `vMAJOR.MINOR.PATCH`, and nothing else names one:
`pyproject.toml` says 0.0.0 in git. `python packaging/release.py version`
(or `src/app.py --version`, or the Setup page's Station row) says what any
tree is: `v1.3.0` exactly on a release, `1.3.0.post3+gabc1234` three commits
past it (`.dirty` with local edits), `0.0.0+abc1234` before the first one. A
bundle says the tag it was built from. `CHANGELOG.md` at the repository root
holds what changed, in the operator's words; a release's notes on GitHub are
its section there.

### Branches

| Branch | What it is |
|---|---|
| `main` | The station, the pre-release line. It receives pull requests from short-lived branches. **Releases are tags on main**; `dev/release.sh` runs on main only. |
| `mvc-refactor` | Merged into main and deleted (2026-10-07); not kept as an integration branch. |
| `feat/<topic>`, `fix/<topic>`, `agent/<topic>` | Short-lived branches that open a pull request into main. |
| `legacy` | The lab's original Tk app (the old main), frozen. `dev/swap_branch.sh legacy [--no-flash] [-- app args]` runs it on the same boards: it makes `../legacy-app` (a worktree of this checkout's `legacy` branch, else a clone of origin's) and its venv on first use, flashes the three Megas with that tree's sketches through this tree's `firmware/flash_firmware.py` (recorded in the stamp as `stable`, so the station's flash puts them back; 2026-10-08), then runs `src/mainGUI.py` there; `station` flashes from this tree and runs the Web view. It never switches branches in this checkout; `RUN_SWAP_DRY_RUN=1` prints the commands and runs none. |
| `stable` | The original app's packaging ref: what `package.yml` freezes as `stable/` (`stable_ref`). Unchanged. |

`.github/workflows/gate.yml` runs on every pull request into, and every
push to, main: the fast suite, the golden wire gate and a
launch of the Web view, on Ubuntu, within 30 minutes.

A checkout's Update row, `update.sh` and `update.bat` take releases (tags on
main) from the remote of the branch the checkout tracks, and their
developers' line compares the checkout with that branch. The lab PC clones fresh from main
(`git clone --branch main <url>`) once its own work is pushed (step 1 below),
then `update.bat` fast-forwards main to the latest release. No sibling `main/`
checkout exists after a fresh clone; `dev/swap_branch.sh legacy` creates
`../legacy-app` from the `legacy` branch when the old app is needed.

### Cutting a release

On a clean `main`, level with origin/main:

```
dev/release.sh v1.0.0            # checks, cuts CHANGELOG.md, commits, tags; pushes nothing
git push origin main             # the two commands it prints, branch first
git push origin v1.0.0
```

`dev/release.sh` refuses, changing nothing, a tag that is not
`vMAJOR.MINOR.PATCH`, one that exists (here or on GitHub) or is not newer
than the latest release, any branch but main, local edits, a HEAD that is
not exactly origin/main, and an empty Unreleased section. It
moves Unreleased under `## [1.0.0] - <date>`, commits that alone ("Release
v1.0.0") and makes an annotated tag whose message is the section.
`dev/release.sh v1.0.0 --push` runs the two pushes too.

The tag's push starts `.github/workflows/package.yml`:

1. **draft**: the release is created as a draft, its notes the tag's
   CHANGELOG section. A draft is invisible to every installed station.
2. **build**, four runners at once (macOS arm64 and x86_64, Windows, Linux):
   the tools, the stable app, the station, the smoke, the zip, uploaded to
   the draft. Expect most of an hour (the job timeout is two hours; the
   first run will give the real figure).
3. **publish**, only when all four builds passed and all four zips are on
   the draft: it adds `SHA256SUMS`, sets the notes and publishes the release
   as latest. If any build failed, the release stays a draft and the run's
   summary says which step stopped it; fix the cause and re-run the failed
   jobs from the Actions page (the zips already uploaded stay).

A run by hand (`workflow_dispatch`, once the file is on the default branch)
builds and smokes without releasing; its `stable_ref` input picks the branch
frozen as `stable/`. Every action is pinned to a commit SHA.

### What an installed station sees

- **A bundle** checks GitHub's latest release at startup (and on Check
  again) and compares it with its own `VERSION` as versions. The Update row
  says "v1.0.0 is ready: <the notes' first entry>". Update now downloads
  this machine's zip (about 400 MB), checks its size and its SHA-256 against
  the release's `SHA256SUMS`, unpacks it to `<install>.next` and swaps it in
  (`<install>` becomes `<install>.previous`); Restart runs it. On Windows,
  where a running folder cannot move, the swap happens at the restart.
- **A checkout** (`./run.sh`, the lab PC today) does the same in git: its
  Update row, `update.sh` and `update.bat` fetch the tags, say "This
  checkout is at X; the latest release is v1.0.0 (N commits ahead)", and
  fast-forward ONLY to that release's commit: never to the branch head,
  never over local edits, never when the checkout has diverged from the
  release. Commits on the branch past the latest release are for
  developers (`git pull`); the Update row's developers' line counts them.

### From a checkout to an installed release (the lab PC)

The lab PC runs a git checkout with local code GitHub does not have. In
this order:

1. **Push the bench's work first.** (The lab's 2026-10-07 stage push is already
   merged: tag `bench-2026-10-07-stage`, Transfer Map store v8, the tip-shade
   estimator.) For anything the PC has changed since, in the checkout: `git status` to see
   what changed, then commit the source changes on their own branch and
   push it, so nothing lives only on that PC:

   ```
   git switch -c bench/2026-10            # a new branch at the bench's HEAD
   git add <the changed source files>     # not data/, logs or trial folders
   git commit -m "Bench code as it ran in the lab, 2026-10"
   git push -u origin bench/2026-10
   ```

   Tell the lead the branch's name: it is merged (by pull request into main) before the first release the lab installs. Then switch the checkout
   to main once ("Branches" above).
2. **Wait for a release that holds that work.** The repository's Transfer Map
   store code is v8, the lab's (as of 2026-10-07); the lab's own stores may
   still be v7. A store is upgraded by column presence on first open (no backup is
   written: copy it first), so do not point an installed release at the lab's data until its notes say the
   bench's work is in.
3. **Install the bundle beside the checkout**, never inside it: download
   `station-windows-x86_64.zip` from the repository's latest release, unzip
   it to a folder the lab account can write (for example
   `C:\Users\<lab account>\station`, not Program Files), and start
   `station-web.exe` (SmartScreen: More info, Run anyway, until P7).
4. **The trials carry over.** The Transfer Map's store is the operator's
   file, chosen outside any install (Setup refuses one inside it), and the
   choice is remembered in `~/transfer-stage-runs/station.json`, which the
   checkout and the bundle share on one account. The bundle opens the same
   store the checkout used; an update replaces the install folder and never
   touches the store.
5. **The checkout stays** as the development tree (`run.bat`,
   `update.bat`). Run one station at a time: one process holds the serial
   ports.

### Rolling back

- **A bundle** keeps the version it replaced in `<install>.previous` (one
  only: the next update replaces it). Quit the station, rename `<install>`
  to `<install>.bad` and `<install>.previous` to `<install>`, and start it.
  Its Update row then offers the newer release again; it is not installed
  until someone presses Update now. Any older release can also be
  downloaded from GitHub and unzipped beside it.
- **A checkout** goes back with git, by hand: `git switch -c rollback
  v1.0.0` (a branch at the release; Update now and `update.sh` take it
  forward again from there).

## Smoke

`smoke.sh [BUNDLE]` checks the layout first (stamps, sketches, tools offline
through a dead proxy, the stable self-check), then drives the Web launcher
through Setup in SIM; it is the only launcher there is. Set `TRANSFER_STAGE_DATA_ROOT` to keep its logs out of `~/transfer-stage-runs`.

Exit codes: the Web launcher exits 0 via `POST /api/quit`. A SIGTERM stop
goes through the Controller's handler, which closes every model and then
re-raises the signal, so it ends in **exit 143** (128 + 15), not 0.

## Unsigned

The bundle is not code-signed or notarized (P7). macOS Gatekeeper blocks the
first run (right-click Open); Windows SmartScreen warns. The frozen stable
app and the tools inside the bundle are unsigned too.
