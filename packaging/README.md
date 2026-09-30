# packaging/

One portable bundle per platform (`docs/rebuild/PACKAGING_PLAN.md`): the new
station's three launchers, its firmware, the tools to compile and flash that
firmware with no Python and no network, and a frozen copy of the lab's
original Tk app (the `stable` branch) with its own firmware, so the operator
can switch between the two while the refactor is validated.

| File | What |
|---|---|
| `station.spec` | PyInstaller spec: one Analysis per view, three EXEs, one COLLECT -> `dist/station/`; then assembles the layout below and stamps it |
| `entry_tk.py` `entry_qt.py` `entry_web.py` | the three launchers' scripts; each calls `app.main_<view>` |
| `layout.py` | **the bundle layout contract** and the copies that build it (`assemble`); reads the board table from `firmware/flash_firmware.py` with `ast` |
| `tools.py` | fetches arduino-cli, the cores, the libraries and teensy_loader_cli at build time into `build/tools` (`fetch`, `check`, `size`) |
| `stable.spec` `entry_stable.py` | freezes the `stable` branch's `src/mainGUI.py`, unmodified, into `dist/station-stable/` |
| `requirements-stable.txt` | what the stable freeze needs beyond the station's own dependencies (`gcodeparser`) |
| `spec_helpers.py` | shared by both specs: SDL3 beside pygame's sdl2-compat shim, the macOS `UF_HIDDEN` clear |
| `hooks/hook-panel.py` | stops pyinstaller-hooks-contrib's HoloViz `panel` hook firing on the station's own `panel.py` |
| `smoke.sh` | bundle acceptance test, macOS / Linux (P4) |
| `smoke.ps1` | the same for Windows (written, not yet run) |
| `release.py` | stamps `VERSION` and `release.json` into a bundle (the spec calls it), patches a build's pyproject version from the tag, names this machine's asset, zips the bundle with links and modes kept |
| `release.json` | the template: owner and repo left empty (filled at build time), the asset pattern `station-{os}-{arch}.zip` and its OS/arch lookups |

## The layout (`layout.py`; `src/` consumes it, neither side changes it without the lead)

Relative to the bundle root `dist/station/`:

```
station-web  station-qt  station-tk   (.exe on Windows)   the new app's launchers
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

The stable ref is `stable`; until the lead fast-forwards it, `origin/stable`
is a July commit and today's original app is `origin/main` (use that ref in
step 2 for a local build; the workflow's `stable_ref` input does the same).

Output: `dist/station/` as above. Ship the whole folder, zipped
(`release.py zip`). One-folder only: one-file mode unpacks the whole bundle to
a temp dir on every launch and trips antivirus.

## Sizes (macOS arm64, measured 2026-09-30)

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

- **Views are imported by name** (`app.VIEWS`, `importlib`), so each entry's
  view module is a hidden import. A new view needs a line in `VIEW_HIDDEN`.
- **Web assets** go to `views/web/static`, where `server._STATIC_DIR`
  (`__file__`-relative) finds them.
- **matplotlib is Agg only**; every GUI backend is excluded so Qt never lands
  in `station-web` and Tk never in `station-qt`.
- **Qt**: QtCore/QtGui/QtWidgets and QtSvg (the Qt view's icons). One missing
  module and `views/qt.py`'s single PySide6 import block fails whole: the
  launcher then says "PySide6 is not installed". The virtual-keyboard, PDF and
  TUIO plugins and Qt's translations are pruned (`QT_PRUNE`, ~28 MB on macOS).
  `entry_qt.py` sets `QT_QPA_PLATFORM_PLUGIN_PATH` to the bundle's platforms
  dir and logs what it resolved (stderr, and a `[packaging] Qt Plugin Path:`
  line in the station log).
- **pygame on macOS / Python 3.14** is built against Homebrew's
  `sdl2-compat`, whose libSDL2 `dlopen`s SDL3 by name. PyInstaller cannot
  see that; without SDL3 the launcher shows a modal "Failed loading SDL3"
  alert from inside `import pygame` and hangs silently. Both specs ship
  `libSDL3.dylib` beside the shim (`spec_helpers.py`). A pygame wheel with a
  real SDL2 skips it.
- **macOS `UF_HIDDEN`**: pip-installed dylibs carry the hidden flag and the
  copy into `dist/` keeps it; Qt's plugin scanner skips hidden files. Both
  specs run `chflags -R nohidden` on their bundle after COLLECT.
- **firmware/, tools/, stable/ are copied, not declared as datas**:
  PyInstaller 6 puts every data file under `_internal/`, and the layout wants
  them beside the launchers.

## Releases

Push a tag `v1.3.0` (an annotated tag's message becomes the release notes;
its first line is what the Update row shows). `.github/workflows/package.yml`
builds all four zips into a draft release and publishes it once every build
has passed its smoke. Installed bundles pick it up at their next startup check.
A run by hand (`workflow_dispatch`, once the file is on the default branch)
builds and smokes without releasing; its `stable_ref` input picks the branch
frozen as `stable/`. Every action is pinned to a commit SHA.

## Smoke

`smoke.sh [BUNDLE]` checks the layout first (stamps, sketches, tools offline
through a dead proxy, the stable self-check), then drives the Web launcher
through Setup in SIM, then the Tk and Qt launchers. Under
`STATION_NO_WINDOWS=1` the Tk step is skipped and says so (Tk has no offscreen
platform); Qt runs with `QT_QPA_PLATFORM=offscreen` (`SMOKE_QT_PLATFORM`).
Set `TRANSFER_STAGE_DATA_ROOT` to keep its logs out of `~/transfer-stage-runs`.

Exit codes: the desktop launchers are stopped with SIGTERM. The Controller's
handler closes every model and then re-raises the signal, so a clean stop is
**exit 143** (128 + 15), not 0. An exit of 1 means a toolkit handler ended the
process past `Controller.close()`: nothing was stopped.

## Unsigned

The bundle is not code-signed or notarized (P7). macOS Gatekeeper blocks the
first run (right-click Open); Windows SmartScreen warns. The frozen stable
app and the tools inside the bundle are unsigned too.
