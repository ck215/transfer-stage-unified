# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: one bundle, one launcher (PACKAGING_PLAN P3).

    pyinstaller --noconfirm --clean packaging/station.spec     (from the repo root)

produces ONE folder, dist/station/, holding station-web over one _internal/.
The Tk and Qt launchers were retired 2026-10-07 (owner ruling: Web is the only
frontend); their views stay frozen at 413f504 in the source tree but are not
bundled. One-folder mode only: one-file mode would unpack ~250 MB to a temp
dir on every launch and trips antivirus (refused in the plan).

One Analysis, one executable: station-web carries neither PySide6 nor tkinter.

`STATION_SRC` (optional) builds from another copy of src/ - used to prove a
proposed core change against the bundle without touching the tree.
"""
import os
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

HERE = os.path.dirname(os.path.abspath(SPEC))           # packaging/
ROOT = os.path.dirname(HERE)
SRC = os.path.abspath(os.environ.get("STATION_SRC") or os.path.join(ROOT, "src"))

# -- data -------------------------------------------------------------------
# views/web/server.py finds its assets `__file__`-relative
# (_STATIC_DIR = dirname(server.py)/static); frozen, __file__ is
# <_internal>/views/web/server.pyc, so the assets go to views/web/static.
WEB_STATIC = [(os.path.join(SRC, "views", "web", "static"),
               os.path.join("views", "web", "static"))]

# The trial video's encoder: imageio_ffmpeg ships the ffmpeg executable as
# package data (imageio_ffmpeg/binaries/), found at run time through
# importlib.resources. pyinstaller-hooks-contrib has a hook that collects it,
# but only if the package is analysed, and src/devices/video.py imports it
# lazily; so it is named below and its binaries collected here as well. A
# bundle without them records JPEG frames instead of an MP4.
FFMPEG_BINARIES = collect_data_files("imageio_ffmpeg", subdir="binaries")

# -- imports ----------------------------------------------------------------
# app.launch() imports the view with importlib (by name): static analysis
# cannot see it, so the entry names it.
# pygame is imported lazily inside devices/gamepad.py; modulegraph follows
# function-level imports, and pygame ships its own PyInstaller hook for the
# SDL2 libraries - named here anyway so a refactor to importlib cannot drop it.
COMMON_HIDDEN = (
    ["pygame", "serial", "serial.tools.list_ports",
     {"darwin": "serial.tools.list_ports_osx",
      "win32": "serial.tools.list_ports_windows"}.get(
         sys.platform, "serial.tools.list_ports_linux"),
     "PIL.Image", "numpy",
     "matplotlib.figure", "matplotlib.backends.backend_agg",
     "imageio_ffmpeg", "imageio_ffmpeg.binaries"]
    # mss picks its backend per OS at run time (mss.darwin / linux / windows).
    + collect_submodules("mss", filter=lambda name: not name.endswith("__main__"))
)
WEB_HIDDEN = ["views.web.server"]

# Never shipped: the test suites, the old tree, dev tools.
COMMON_EXCLUDES = [
    "tests", "legacy", "pytest", "_pytest", "pytestqt", "IPython",
    "gcodeparser", "PyInstaller", "setuptools", "pkg_resources", "pip",
    # matplotlib draws through Agg ONLY (model/plot_data.py); every GUI
    # backend would drag in a toolkit (Qt or Tk) the Web bundle never uses.
    "matplotlib.backends.backend_qt", "matplotlib.backends.backend_qtagg",
    "matplotlib.backends.backend_qtcairo", "matplotlib.backends.backend_qt5",
    "matplotlib.backends.backend_qt5agg", "matplotlib.backends.backend_qt5cairo",
    "matplotlib.backends.qt_compat", "matplotlib.backends.qt_editor",
    "matplotlib.backends.backend_tkagg", "matplotlib.backends.backend_tkcairo",
    "matplotlib.backends._backend_tk", "matplotlib.backends.backend_macosx",
    "matplotlib.backends.backend_webagg", "matplotlib.backends.backend_webagg_core",
    "matplotlib.backends.backend_nbagg", "matplotlib.backends.backend_wx",
    "matplotlib.backends.backend_wxagg", "matplotlib.backends.backend_wxcairo",
    "matplotlib.backends.backend_gtk3", "matplotlib.backends.backend_gtk3agg",
    "matplotlib.backends.backend_gtk3cairo", "matplotlib.backends.backend_gtk4",
    "matplotlib.backends.backend_gtk4agg", "matplotlib.backends.backend_gtk4cairo",
    "PyQt5", "PyQt6", "PySide2",
    # The retired views' toolkits (views/tk.py, views/qt.py are not bundled).
    "PySide6", "shiboken6", "tkinter", "_tkinter", "views.tk", "views.qt",
]


def analysis():
    return Analysis(
        [os.path.join(HERE, "entry_web.py")],
        pathex=[SRC],
        binaries=[],
        datas=WEB_STATIC + FFMPEG_BINARIES,
        hiddenimports=COMMON_HIDDEN + WEB_HIDDEN,
        hookspath=[p for p in [os.path.join(HERE, "hooks")] if os.path.isdir(p)],
        hooksconfig={"matplotlib": {"backends": "Agg"}},
        runtime_hooks=[],
        excludes=COMMON_EXCLUDES,
        noarchive=False,
        optimize=0,
    )


def executable(a):
    pyz = PYZ(a.pure)
    return EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="station-web",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        # A console on every OS: the station logs to stderr as well as to its
        # file, and the Web launcher has no window of its own.
        console=True,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
    )


a = analysis()


# pygame on macOS / Python 3.14 is built on sdl2-compat, which dlopen()s SDL3
# by name: SDL3 ships beside the shim or `import pygame` hangs behind a modal
# alert (packaging/spec_helpers.py; stable.spec needs the same).
sys.path.insert(0, HERE)
import spec_helpers  # noqa: E402  (packaging/spec_helpers.py)

if sys.platform == "darwin":
    a.binaries = a.binaries + spec_helpers.sdl3_for_sdl2_compat(a.binaries, workpath)
exe_web = executable(a)

coll = COLLECT(
    exe_web,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="station",
)

# macOS: pip-installed dylibs carry UF_HIDDEN into dist/ and the loader skips
# hidden files; clear it on the whole bundle after the copy.
spec_helpers.clear_hidden_flags(coll.name)

# The bundle layout contract (packaging/layout.py): the repo's firmware/ goes
# BESIDE the launchers as firmware/ (not under _internal/, so the flasher and
# the operator find it by path), then tools/ (packaging/tools.py) and
# stable/ (packaging/stable.spec) when they have been staged. Copied, not
# declared as datas: PyInstaller 6 puts every data file under _internal/.
import layout  # noqa: E402  (packaging/layout.py)

for part, path in layout.assemble(coll.name).items():
    print(f"[station.spec] {part}/ -> {path}")

# B1: the version the bundle knows. VERSION (tag, commit, build time) and
# release.json (the repository the frozen updater asks for releases) go beside
# the launchers. STATION_TAG names the tag (the workflow sets it); a local
# build gets `git describe`. Written last, after every copy into dist/.
import release  # noqa: E402  (packaging/release.py)

print(f"[station.spec] stamped {coll.name}: {release.stamp(coll.name)}")
