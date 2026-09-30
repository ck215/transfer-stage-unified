# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: one bundle, three launchers (PACKAGING_PLAN P3).

    pyinstaller --noconfirm --clean packaging/station.spec     (from the repo root)

produces ONE folder, dist/station/, holding station-tk, station-qt and
station-web side by side over one shared _internal/. One-folder mode only:
one-file mode would unpack ~250 MB to a temp dir on every launch and trips
antivirus (refused in the plan).

One Analysis per entry, so each launcher's own module archive holds only its
view: station-tk carries no PySide6, station-web neither PySide6 nor
tkinter. The three COLLECT into one folder; shared libraries are stored once.

`STATION_SRC` (optional) builds from another copy of src/ - used to prove a
proposed core change against the bundle without touching the tree.
"""
import os
import re
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
# app.launch() imports the view with importlib (by name, so no view imports
# another): static analysis cannot see it, so each entry names its own.
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
VIEW_HIDDEN = {
    "tk": ["views.tk", "tkinter", "tkinter.ttk", "tkinter.font",
           "tkinter.filedialog", "tkinter.messagebox"],
    "qt": ["views.qt", "PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets"],
    "web": ["views.web.server"],
}

# Never shipped: the test suites, the old tree, dev tools.
COMMON_EXCLUDES = [
    "tests", "legacy", "pytest", "_pytest", "pytestqt", "IPython",
    "gcodeparser", "PyInstaller", "setuptools", "pkg_resources", "pip",
    # matplotlib draws through Agg ONLY (model/plot_data.py); every GUI
    # backend would drag in a toolkit - Qt into station-web, Tk into
    # station-qt.
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
]
# The Qt view needs QtCore, QtGui and QtWidgets and nothing else from Qt:
# every one of these would multiply the bundle (QtWebEngine alone is >150 MB).
QT_UNUSED = [
    "PySide6." + m for m in (
        "Qt3DAnimation", "Qt3DCore", "Qt3DExtras", "Qt3DInput", "Qt3DLogic",
        "Qt3DRender", "QtBluetooth", "QtCharts", "QtConcurrent",
        "QtDataVisualization", "QtDBus", "QtDesigner", "QtGraphs",
        "QtGraphsWidgets", "QtHelp", "QtHttpServer", "QtLocation",
        "QtMultimedia", "QtMultimediaWidgets", "QtNetwork", "QtNetworkAuth",
        "QtNfc", "QtOpenGL", "QtOpenGLWidgets", "QtPdf", "QtPdfWidgets",
        "QtPositioning", "QtPrintSupport", "QtQml", "QtQuick", "QtQuick3D",
        "QtQuickControls2", "QtQuickTest", "QtQuickWidgets", "QtRemoteObjects",
        "QtScxml", "QtSensors", "QtSerialBus", "QtSerialPort",
        "QtSpatialAudio", "QtSql", "QtStateMachine", "QtSvg", "QtSvgWidgets",
        "QtTest", "QtTextToSpeech", "QtUiTools", "QtWebChannel",
        "QtWebEngineCore", "QtWebEngineQuick", "QtWebEngineWidgets",
        "QtWebSockets", "QtWebView", "QtXml", "QtAxContainer")
]
VIEW_EXCLUDES = {
    "tk": ["PySide6", "shiboken6"],
    "qt": ["tkinter", "_tkinter"] + QT_UNUSED,
    "web": ["PySide6", "shiboken6", "tkinter", "_tkinter"],
}


def analysis(view):
    return Analysis(
        [os.path.join(HERE, f"entry_{view}.py")],
        pathex=[SRC],
        binaries=[],
        datas=(WEB_STATIC if view == "web" else []) + FFMPEG_BINARIES,
        hiddenimports=COMMON_HIDDEN + VIEW_HIDDEN[view],
        hookspath=[p for p in [os.path.join(HERE, "hooks")] if os.path.isdir(p)],
        hooksconfig={"matplotlib": {"backends": "Agg"}},
        runtime_hooks=[],
        excludes=COMMON_EXCLUDES + VIEW_EXCLUDES[view],
        noarchive=False,
        optimize=0,
    )


def executable(view, a):
    pyz = PYZ(a.pure)
    return EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name=f"station-{view}",
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


# What PySide6's hooks still collect that the Qt view never loads: the
# virtual-keyboard input plugin (it drags in QtQuick, QtQml, QtOpenGL and
# QtNetwork), the PDF image plugin (QtPdf), the TUIO touch plugin
# (QtNetwork) and Qt's own translations (the station is English-only).
# About 28 MB on macOS. Matched on the bundle path, so it is OS-neutral:
# a framework on macOS, QtX.dll on Windows, libQt6X.so.6 on Linux.
QT_PRUNE = re.compile(
    r"^PySide6[/\\](Qt[/\\])?("       # macOS/Linux: PySide6/Qt/..., Windows: PySide6/...
    r"plugins[/\\](platforminputcontexts|generic)[/\\]"
    r"|plugins[/\\]imageformats[/\\][^/\\]*qpdf"
    r"|translations[/\\]"
    r"|((lib|bin)[/\\])?(lib)?Qt6?(Quick|Qml|QmlModels|QmlMeta|QmlWorkerScript"
    r"|VirtualKeyboard|VirtualKeyboardQml|Pdf|OpenGL|Network)\b)")


def pruned(toc):
    return [entry for entry in toc if not QT_PRUNE.match(entry[0])]


VIEWS = ("tk", "qt", "web")
analyses = {view: analysis(view) for view in VIEWS}
for a in analyses.values():
    a.binaries = pruned(a.binaries)
    a.datas = pruned(a.datas)


# pygame on macOS / Python 3.14 is built on sdl2-compat, which dlopen()s SDL3
# by name: SDL3 ships beside the shim or `import pygame` hangs behind a modal
# alert (packaging/spec_helpers.py; stable.spec needs the same).
sys.path.insert(0, HERE)
import spec_helpers  # noqa: E402  (packaging/spec_helpers.py)

if sys.platform == "darwin":
    for a in analyses.values():
        a.binaries = a.binaries + spec_helpers.sdl3_for_sdl2_compat(a.binaries, workpath)
executables = {view: executable(view, analyses[view]) for view in VIEWS}

coll = COLLECT(
    *[executables[v] for v in VIEWS],
    *[analyses[v].binaries for v in VIEWS],
    *[analyses[v].datas for v in VIEWS],
    strip=False,
    upx=False,
    name="station",
)

# macOS: pip-installed dylibs carry UF_HIDDEN into dist/ and Qt's plugin
# scanner skips hidden files; clear it on the whole bundle after the copy.
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
