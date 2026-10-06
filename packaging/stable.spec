# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: the lab's original Tk app, frozen from the `stable`
branch, so the operator can switch between it and the new station while the
refactor is validated (brief-dist-build B3).

    git archive "origin/$STABLE_REF" | tar -x -C build/stable-src    (or a checkout)
    pyinstaller --noconfirm --clean packaging/stable.spec             (from the repo root)

produces dist/station-stable/: station-stable over its own _internal/, plus
the stable ref's firmware/ sketches. station.spec then copies the folder into
the bundle as stable/ (packaging/layout.py). Build this one FIRST.

The stable sources are never edited: `STATION_STABLE_SRC` (default
build/stable-src) is a checkout of the ref, and every shim freezing needs
(multiprocessing, running mainGUI as __main__) is in entry_stable.py.
`STATION_STABLE_REF` / `STATION_STABLE_SHA` (optional) are written to
stable/SOURCE so the bundle says which commit it froze.
"""
import os
import sys

from PyInstaller.utils.hooks import collect_submodules

HERE = os.path.dirname(os.path.abspath(SPEC))           # packaging/
ROOT = os.path.dirname(HERE)
STABLE_SRC = os.path.abspath(os.environ.get("STATION_STABLE_SRC")
                             or os.path.join(ROOT, "build", "stable-src"))
SRC = os.path.join(STABLE_SRC, "src")
if not os.path.isfile(os.path.join(SRC, "mainGUI.py")):
    raise SystemExit(f"stable.spec: no src/mainGUI.py under {STABLE_SRC}; check out "
                     "the stable ref there first (see the docstring)")

sys.path.insert(0, HERE)
import layout  # noqa: E402  (packaging/layout.py)
import spec_helpers  # noqa: E402  (packaging/spec_helpers.py)

# entry_stable.py runs mainGUI through runpy, which static analysis cannot
# follow: mainGUI and everything it reaches are named. Its third-party
# imports (read from the stable src/): pyserial (+ the OS's list_ports
# backend), pygame, Pillow, mss (per-OS backend), numpy, gcodeparser
# (stepper/chuck frames). camera_control.py / lib/toupcam.py are not
# reachable from mainGUI and are left out; nothing imports cv2 or matplotlib.
HIDDEN = [
    "mainGUI", "stepper_frame", "DC_frame", "chuck_frame", "temp_control",
    "rotator", "serialDrive", "controllerDrive", "color_test_new",
    "lib.redpercent", "lib.smc100",
    "serial", "serial.tools.list_ports",
    {"darwin": "serial.tools.list_ports_osx",
     "win32": "serial.tools.list_ports_windows"}.get(
        sys.platform, "serial.tools.list_ports_linux"),
    "pygame", "PIL.Image", "numpy", "gcodeparser",
    "tkinter", "tkinter.ttk", "tkinter.filedialog", "tkinter.messagebox",
    # mss picks its backend per OS at run time (mss.darwin / linux / windows).
] + collect_submodules("mss", filter=lambda name: not name.endswith("__main__"))
EXCLUDES = [
    "tests", "pytest", "_pytest", "IPython", "PyInstaller", "setuptools", "pip",
    "matplotlib", "PySide6", "shiboken6", "PyQt5", "PyQt6", "PySide2",
    "imageio_ffmpeg", "cv2", "camera_control", "lib.toupcam",
]

a = Analysis(
    [os.path.join(HERE, "entry_stable.py")],
    pathex=[SRC],
    binaries=[],
    datas=[],
    hiddenimports=HIDDEN,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=EXCLUDES,
    noarchive=False,
    optimize=0,
)
if sys.platform == "darwin":
    a.binaries = a.binaries + spec_helpers.sdl3_for_sdl2_compat(a.binaries, workpath)

pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=layout.STABLE_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    # As the station's launchers: the stable app prints its diagnostics.
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=layout.STABLE_NAME)

spec_helpers.clear_hidden_flags(coll.name)

# That branch's own sketches: its firmware speaks the stable protocol.
print(f"[stable.spec] firmware/ -> "
      f"{layout.copy_firmware(os.path.join(STABLE_SRC, 'firmware'), coll.name)}")
with open(os.path.join(coll.name, "SOURCE"), "w", encoding="utf-8", newline="\n") as f:
    f.write(f"{os.environ.get('STATION_STABLE_REF', 'unknown')}\n"
            f"{os.environ.get('STATION_STABLE_SHA', 'unknown')}\n")
