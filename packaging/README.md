# packaging/

One portable bundle per platform, three launchers (`docs/rebuild/PACKAGING_PLAN.md`).

| File | What |
|---|---|
| `station.spec` | PyInstaller spec: one Analysis per view, three EXEs, one COLLECT -> `dist/station/` |
| `entry_tk.py` `entry_qt.py` `entry_web.py` | the three launchers' scripts; each calls `app.main_<view>` |
| `hooks/hook-panel.py` | stops pyinstaller-hooks-contrib's HoloViz `panel` hook firing on the station's own `panel.py` |
| `smoke.sh` | bundle acceptance test, macOS / Linux (P4) |
| `smoke.ps1` | the same for Windows (written, not yet run) |
| `release.py` | stamps `VERSION` and `release.json` into a bundle (the spec calls it), patches a build's pyproject version from the tag, names this machine's asset, zips the bundle with links and modes kept |
| `release.json` | the template: owner and repo left empty (filled at build time), the asset pattern `station-{os}-{arch}.zip` and its OS/arch lookups |

## Build (on the platform you are building for; nothing cross-compiles)

```
python3 -m pip install -r requirements-dev.txt      # the station + pytest + pyinstaller
python3 -m PyInstaller --noconfirm --clean packaging/station.spec
packaging/smoke.sh                                   # or: packaging\smoke.ps1
```

Output: `dist/station/station-tk`, `station-qt`, `station-web` (`.exe` on
Windows) over one `_internal/`. Ship the whole `dist/station/` folder, zipped.
One-folder only: one-file mode unpacks the whole bundle to a temp dir on every
launch and trips antivirus.

## Things the spec handles that are easy to break

- **Views are imported by name** (`app.VIEWS`, `importlib`), so each entry's
  view module is a hidden import. A new view needs a line in `VIEW_HIDDEN`.
- **Web assets** go to `views/web/static`, where `server._STATIC_DIR`
  (`__file__`-relative) finds them.
- **matplotlib is Agg only**; every GUI backend is excluded so Qt never lands
  in `station-web` and Tk never in `station-qt`.
- **Qt**: only QtCore/QtGui/QtWidgets. The virtual-keyboard, PDF and TUIO
  plugins and Qt's translations are pruned (`QT_PRUNE`, ~28 MB on macOS).
  `entry_qt.py` sets `QT_QPA_PLATFORM_PLUGIN_PATH` to the bundle's platforms
  dir and logs what it resolved (stderr, and a `[packaging] Qt Plugin Path:`
  line in the station log).
- **pygame on macOS / Python 3.14** is built against Homebrew's
  `sdl2-compat`, whose libSDL2 `dlopen`s SDL3 by name. PyInstaller cannot
  see that; without SDL3 the launcher shows a modal "Failed loading SDL3"
  alert from inside `import pygame` and hangs silently. The spec ships
  `libSDL3.dylib` beside the shim. A pygame wheel with a real SDL2 skips it.
- **macOS `UF_HIDDEN`**: pip-installed dylibs carry the hidden flag and the
  copy into `dist/` keeps it; Qt's plugin scanner skips hidden files. The spec
  runs `chflags -R nohidden` on the bundle after COLLECT.

## Releases

Push a tag `v1.3.0` (an annotated tag's message becomes the release notes;
its first line is what the Update row shows). `.github/workflows/package.yml`
builds all four zips into a draft release and publishes it once every build
has passed its smoke. Installed bundles pick it up at their next startup check.

## Smoke exit codes

The desktop launchers are stopped with SIGTERM. The Controller's handler
closes every model and then re-raises the signal, so a clean stop is **exit
143** (128 + 15), not 0. An exit of 1 means a toolkit handler ended the
process past `Controller.close()`: nothing was stopped.

## Unsigned

The bundle is not code-signed or notarized (P7). macOS Gatekeeper blocks the
first run (right-click Open); Windows SmartScreen warns.
