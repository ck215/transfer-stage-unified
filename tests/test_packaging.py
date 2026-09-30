"""Packaging (PACKAGING_PLAN P2-P4): what `pyproject.toml` and
`packaging/station.spec` declare must match what `src/` is.

Static checks only: nothing here builds, installs or launches. The bundle
itself is proven by `packaging/smoke.sh`.
"""
import ast
import fnmatch
import os
import re
import sys
import tomllib

import pytest

import app

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SRC = os.path.join(ROOT, "src")
PACKAGING = os.path.join(ROOT, "packaging")


@pytest.fixture(scope="module")
def pyproject():
    with open(os.path.join(ROOT, "pyproject.toml"), "rb") as f:
        return tomllib.load(f)


def _top_level_modules():
    return sorted(n[:-3] for n in os.listdir(SRC)
                  if n.endswith(".py") and os.path.isfile(os.path.join(SRC, n)))


def _packages():
    found = []
    for directory, subdirs, files in os.walk(SRC):
        subdirs[:] = [d for d in subdirs if d != "__pycache__"]
        if "__init__.py" in files:
            found.append(os.path.relpath(directory, SRC).replace(os.sep, "."))
    return sorted(found)


def _requirement_names(specs):
    return {re.split(r"[\s<>=!~;\[]", spec, maxsplit=1)[0].lower()
            for spec in specs}


# -- P2: pyproject ------------------------------------------------------------

VIEW_SCRIPTS = {"station-tk": "main_tk", "station-qt": "main_qt",
                "station-web": "main_web"}


def test_pyproject_declares_one_script_per_view(pyproject):
    scripts = pyproject["project"]["scripts"]
    assert scripts == {name: f"app:{func}" for name, func in VIEW_SCRIPTS.items()}


def test_pyproject_scripts_resolve_to_callables_in_app(pyproject):
    for target in pyproject["project"]["scripts"].values():
        module, _, attribute = target.partition(":")
        assert module == "app"
        assert callable(getattr(app, attribute))


def test_pyproject_runtime_dependencies_exclude_legacy_and_dev_tools(pyproject):
    runtime = _requirement_names(pyproject["project"]["dependencies"])
    assert "gcodeparser" not in runtime
    assert not {"pytest", "pytest-qt", "pyinstaller"} & runtime
    # Qt is an extra: only `station-qt` needs it.
    assert "pyside6" not in runtime
    assert "pyside6" in _requirement_names(
        pyproject["project"]["optional-dependencies"]["qt"])


def test_pyproject_runtime_dependencies_are_pinned(pyproject):
    for spec in pyproject["project"]["dependencies"]:
        assert "==" in spec, spec


def test_pyproject_runtime_dependencies_cover_every_third_party_import(pyproject):
    """Each third-party import under src/ has a declared distribution."""
    import_to_dist = {"serial": "pyserial", "pygame": "pygame", "mss": "mss",
                      "PIL": "pillow", "numpy": "numpy",
                      "matplotlib": "matplotlib", "PySide6": "pyside6"}
    declared = _requirement_names(pyproject["project"]["dependencies"]) | \
        _requirement_names(pyproject["project"]["optional-dependencies"]["qt"])
    seen = set()
    for directory, _, files in os.walk(SRC):
        for name in files:
            if not name.endswith(".py"):
                continue
            with open(os.path.join(directory, name), encoding="utf-8") as f:
                tree = ast.parse(f.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    seen.update(a.name.split(".")[0] for a in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    seen.add(node.module.split(".")[0])
    for module, dist in import_to_dist.items():
        if module in seen:
            assert dist in declared, f"{module} is imported but {dist} is not declared"


def test_pyproject_lists_every_top_level_module(pyproject):
    listed = sorted(pyproject["tool"]["setuptools"]["py-modules"])
    assert listed == _top_level_modules()


def test_pyproject_lists_every_package_and_nothing_outside_src(pyproject):
    listed = sorted(pyproject["tool"]["setuptools"]["packages"])
    assert listed == _packages()
    assert not any(p.split(".")[0] in ("tests", "legacy") for p in listed)
    assert pyproject["tool"]["setuptools"]["package-dir"] == {"": "src"}


def test_pyproject_package_data_covers_every_web_static_file(pyproject):
    patterns = pyproject["tool"]["setuptools"]["package-data"]["views.web"]
    web = os.path.join(SRC, "views", "web")
    static = os.path.join(web, "static")
    files = [os.path.relpath(os.path.join(d, n), web).replace(os.sep, "/")
             for d, _, names in os.walk(static) for n in names
             if n != ".DS_Store"]
    assert files, "no static files found"
    for path in files:
        assert any(fnmatch.fnmatch(path, p) for p in patterns), path


# -- P3: the PyInstaller spec -------------------------------------------------

@pytest.fixture(scope="module")
def spec_source():
    with open(os.path.join(PACKAGING, "station.spec"), encoding="utf-8") as f:
        return f.read()


def test_spec_is_tracked_despite_the_spec_ignore_rule():
    with open(os.path.join(ROOT, ".gitignore"), encoding="utf-8") as f:
        lines = f.read().splitlines()
    assert "!packaging/station.spec" in lines
    assert lines.index("!packaging/station.spec") > lines.index("*.spec")


def test_spec_builds_one_folder_with_three_launchers(spec_source):
    assert 'VIEWS = ("tk", "qt", "web")' in spec_source
    assert 'name=f"station-{view}"' in spec_source
    assert spec_source.count("COLLECT(") == 1
    # one-folder: binaries stay out of the executables
    assert "exclude_binaries=True" in spec_source


@pytest.mark.parametrize("view", ["tk", "qt", "web"])
def test_spec_entry_scripts_call_the_matching_main(view):
    with open(os.path.join(PACKAGING, f"entry_{view}.py"), encoding="utf-8") as f:
        tree = ast.parse(f.read())
    imported = {(node.module, alias.name) for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) for alias in node.names}
    assert ("app", f"main_{view}") in imported
    assert callable(getattr(app, f"main_{view}"))


def test_spec_hidden_imports_name_every_view_module_app_loads_by_name(spec_source):
    """app.launch() imports views with importlib; the spec must name them."""
    for module, _ in app.VIEWS.values():
        assert f'"{module}"' in spec_source, module


def test_spec_ships_the_web_static_dir_where_server_looks(spec_source):
    assert 'os.path.join("views", "web", "static")' in spec_source
    from views.web import server
    assert server._STATIC_DIR.replace(os.sep, "/").endswith("views/web/static")


def test_spec_excludes_tests_legacy_and_gui_matplotlib_backends(spec_source):
    for name in ('"tests"', '"legacy"', '"pytest"', '"IPython"',
                 '"matplotlib.backends.backend_qtagg"',
                 '"matplotlib.backends.backend_tkagg"',
                 '"matplotlib.backends.backend_macosx"'):
        assert name in spec_source, name
    assert '"QtWebEngineCore"' in spec_source


def _spec_namespace(spec_source):
    """The spec's QT_PRUNE, without running PyInstaller."""
    start = spec_source.index("QT_PRUNE = re.compile(")
    end = spec_source.index("\n\n\ndef pruned")
    namespace = {"re": re}
    exec(spec_source[start:end], namespace)
    return namespace


@pytest.mark.parametrize("path, pruned", [
    ("PySide6/Qt/lib/QtNetwork.framework/Versions/A/QtNetwork", True),
    ("PySide6/Qt/lib/QtQuick.framework/QtQuick", True),
    ("PySide6/Qt/plugins/platforminputcontexts/libqtvirtualkeyboardplugin.dylib", True),
    ("PySide6/Qt/plugins/imageformats/libqpdf.dylib", True),
    ("PySide6/Qt/translations/qtbase_de.qm", True),
    ("PySide6/Qt6Network.dll", True),
    ("PySide6/Qt/lib/libQt6Quick.so.6", True),
    # what the Qt view needs must survive
    ("PySide6/Qt/lib/QtCore.framework/Versions/A/QtCore", False),
    ("PySide6/Qt/lib/QtGui.framework/QtGui", False),
    ("PySide6/Qt/lib/QtWidgets.framework/QtWidgets", False),
    ("PySide6/Qt/lib/QtDBus.framework/QtDBus", False),
    ("PySide6/Qt/plugins/platforms/libqcocoa.dylib", False),
    ("PySide6/Qt/plugins/platforms/libqoffscreen.dylib", False),
    ("PySide6/plugins/platforms/qwindows.dll", False),
    ("PySide6/Qt6Core.dll", False),
    ("PySide6/QtWidgets.abi3.so", False),
])
def test_spec_prunes_only_qt_parts_the_view_never_loads(spec_source, path, pruned):
    assert bool(_spec_namespace(spec_source)["QT_PRUNE"].match(path)) is pruned


def test_spec_ships_sdl3_beside_an_sdl2_compat_shim_and_clears_uf_hidden(spec_source):
    # the helpers are shared with stable.spec (packaging/spec_helpers.py)
    with open(os.path.join(PACKAGING, "spec_helpers.py"), encoding="utf-8") as f:
        helpers = f.read()
    assert "def sdl3_for_sdl2_compat(binaries, workpath)" in helpers
    assert '("libSDL3.dylib", staged, "BINARY")' in helpers
    assert '["chflags", "-R", "nohidden", bundle]' in helpers
    assert "spec_helpers.sdl3_for_sdl2_compat(a.binaries, workpath)" in spec_source
    assert "spec_helpers.clear_hidden_flags(coll.name)" in spec_source


# -- the Qt entry point's plugin path -----------------------------------------

@pytest.fixture
def entry_qt():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "station_entry_qt", os.path.join(PACKAGING, "entry_qt.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)       # imports nothing from Qt at load
    return module


def test_entry_qt_changes_nothing_from_source(entry_qt, monkeypatch):
    monkeypatch.delenv("QT_QPA_PLATFORM_PLUGIN_PATH", raising=False)
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert entry_qt._bundle_plugin_dirs() == (None, None)
    resolved = entry_qt._set_plugin_path()
    assert "QT_QPA_PLATFORM_PLUGIN_PATH" not in os.environ
    assert resolved["frozen"] is False


@pytest.mark.parametrize("layout", [("PySide6", "Qt", "plugins"),     # macOS, Linux
                                    ("PySide6", "plugins")])          # Windows
def test_entry_qt_points_qt_at_the_bundles_platform_plugins(entry_qt, monkeypatch,
                                                            tmp_path, layout):
    platforms = tmp_path.joinpath(*layout, "platforms")
    platforms.mkdir(parents=True)
    (platforms / "libqoffscreen.dylib").write_bytes(b"")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.delenv("QT_QPA_PLATFORM_PLUGIN_PATH", raising=False)
    resolved = entry_qt._set_plugin_path()
    assert os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] == str(platforms)
    assert resolved["platform_plugins"] == "libqoffscreen.dylib"


def test_entry_qt_keeps_a_plugin_path_the_caller_set(entry_qt, monkeypatch, tmp_path):
    tmp_path.joinpath("PySide6", "Qt", "plugins", "platforms").mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    monkeypatch.setenv("QT_QPA_PLATFORM_PLUGIN_PATH", "/elsewhere")
    entry_qt._set_plugin_path()
    assert os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] == "/elsewhere"


def test_entry_qt_writes_the_plugin_path_into_the_station_log(entry_qt, monkeypatch,
                                                              tmp_path):
    from events import events
    monkeypatch.setattr(events, "open_file", events.open_file)   # restored after
    entry_qt._log_when_the_log_opens("QT_QPA_PLATFORM_PLUGIN_PATH=/x")
    path = events.open_file(str(tmp_path))
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    finally:
        events.close_file()
    assert "[packaging] Qt Plugin Path: QT_QPA_PLATFORM_PLUGIN_PATH=/x" in text


# -- P4: the smoke scripts ----------------------------------------------------

def test_smoke_sh_is_executable_and_parses():
    import subprocess
    path = os.path.join(PACKAGING, "smoke.sh")
    assert os.access(path, os.X_OK)
    assert subprocess.run(["bash", "-n", path]).returncode == 0


@pytest.mark.parametrize("script", ["smoke.sh", "smoke.ps1"])
def test_smoke_scripts_drive_every_setup_row(script):
    """The smokes name every row (seven since the Transfer Map); they must be the Setup panel's rows."""
    from controller.setup import MODEL_TYPES
    with open(os.path.join(PACKAGING, script), encoding="utf-8") as f:
        text = f.read()
    for name in MODEL_TYPES:
        assert name.lower().replace(" ", "_") in text, name
    for route in ("/api/state", "/api/theme.css", "/api/estop_all", "/api/quit"):
        assert route in text, route


# -- B1: the version a bundle knows; B2 (P5): the builds ----------------------

WORKFLOW = os.path.join(ROOT, ".github", "workflows", "package.yml")
#: runner -> (platform.system(), platform.machine()) on that runner.
RUNNERS = {"macos-14": ("Darwin", "arm64"), "macos-15-intel": ("Darwin", "x86_64"),
           "windows-latest": ("Windows", "AMD64"), "ubuntu-22.04": ("Linux", "x86_64")}


@pytest.fixture(scope="module")
def release_tool():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "station_release_tool", os.path.join(PACKAGING, "release.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def workflow():
    with open(WORKFLOW, encoding="utf-8") as f:
        return f.read()


def test_release_json_template_names_the_asset_and_no_repository():
    import json
    with open(os.path.join(PACKAGING, "release.json"), encoding="utf-8") as f:
        info = json.load(f)
    assert info["owner"] == "" and info["repo"] == ""      # filled at build time
    assert info["asset"] == "station-{os}-{arch}.zip"
    assert set(info["os"]) >= {"Darwin", "Windows", "Linux"}


def test_stamp_writes_version_and_release_json_beside_the_launchers(
        release_tool, tmp_path, monkeypatch):
    import json
    from controller.updater import Updater
    bundle = tmp_path / "station"
    bundle.mkdir()
    release_tool.stamp(str(bundle), tag="v1.3.0", sha="c" * 40,
                       built="2026-09-28T09:00:00Z", repository="lab/station")
    assert (bundle / "VERSION").read_text() == f"v1.3.0\n{'c' * 40}\n2026-09-28T09:00:00Z\n"
    info = json.loads((bundle / "release.json").read_text())
    assert (info["owner"], info["repo"]) == ("lab", "station")
    # ... and the frozen Updater reads it
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert Updater(root=bundle).version() == "v1.3.0, 2026-09-28"


@pytest.mark.parametrize("remote, repository", [
    ("https://github.com/lab/station.git", "lab/station"),
    ("git@github.com:lab/station.git", "lab/station"),
    ("https://github.com/lab/station", "lab/station"),
])
def test_the_repository_is_read_from_the_remote_when_ci_does_not_say(
        release_tool, remote, repository):
    assert release_tool.repository_from_remote(remote) == repository


@pytest.mark.parametrize("tag, version", [
    ("v1.3.0", "1.3.0"), ("1.2", "1.2"), ("v1.2.0-3-gabc1234", "1.2.0+3.gabc1234"),
    ("main", None), ("", None)])
def test_the_pyproject_version_follows_the_tag(release_tool, tmp_path, tag, version):
    source = open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8").read()
    copy = tmp_path / "pyproject.toml"
    copy.write_text(source)
    assert release_tool.pep440(tag) == version
    release_tool.patch_pyproject(str(copy), tag)
    with open(copy, "rb") as f:
        patched = tomllib.load(f)["project"]["version"]
    assert patched == (version or "0.1.0")
    assert copy.read_text().count("\nversion = ") == 1


def test_the_pyproject_in_git_is_not_the_version_source(pyproject):
    with open(os.path.join(ROOT, "pyproject.toml"), encoding="utf-8") as f:
        text = f.read()
    assert pyproject["project"]["version"] == "0.1.0"
    assert "release.py" in text and "tag" in text


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX modes and links")
def test_the_zip_keeps_modes_and_links_and_unpacks_through_the_updater(
        release_tool, tmp_path):
    from controller import updater
    bundle = tmp_path / "dist" / "station"
    (bundle / "_internal" / "Qt.framework" / "Versions" / "A").mkdir(parents=True)
    (bundle / "_internal" / "Qt.framework" / "Versions" / "A" / "Qt").write_text("lib")
    os.symlink("A", bundle / "_internal" / "Qt.framework" / "Versions" / "Current")
    (bundle / "station-web").write_text("#!/bin/sh\n")
    (bundle / "station-web").chmod(0o755)
    release_tool.stamp(str(bundle), tag="v1.3.0", sha="c" * 40,
                       built="2026-09-28T09:00:00Z", repository="lab/station")
    archive = tmp_path / "station-test.zip"
    release_tool.make_zip(str(bundle), str(archive))
    staged = tmp_path / "out" / "station.next"
    staged.parent.mkdir()
    assert updater._unpack(archive, tmp_path / "out" / "work", staged) == ""
    assert os.access(staged / "station-web", os.X_OK)
    current = staged / "_internal" / "Qt.framework" / "Versions" / "Current"
    assert current.is_symlink() and os.readlink(current) == "A"
    assert (staged / "VERSION").read_text().startswith("v1.3.0\n")


def test_the_asset_name_command_is_the_updaters_own(release_tool):
    from controller import updater
    import json
    with open(os.path.join(PACKAGING, "release.json"), encoding="utf-8") as f:
        info = json.load(f)
    assert release_tool.asset_name() == updater.asset_name(info)


def test_the_spec_stamps_the_bundle(spec_source):
    assert "release.stamp(" in spec_source or "stamp(coll.name" in spec_source


def test_the_workflow_builds_on_a_tag_and_by_hand(workflow):
    assert re.search(r"^on:\s*$", workflow, re.M)
    assert re.search(r"tags:\s*\[\s*[\"']v\*[\"']\s*\]", workflow)
    assert "workflow_dispatch:" in workflow


def test_the_workflow_names_the_four_runners_and_their_assets(workflow):
    from controller import updater
    import json
    with open(os.path.join(PACKAGING, "release.json"), encoding="utf-8") as f:
        info = json.load(f)
    pairs = re.findall(r"runner:\s*(\S+)\s*\n\s*asset:\s*(\S+)", workflow)
    assert dict(pairs) == {runner: updater.asset_name(info, *platform)
                           for runner, platform in RUNNERS.items()}
    # the build refuses to upload under a name the updater would not look for
    assert "release.py asset-name" in workflow and "matrix.asset" in workflow


def test_the_workflow_pins_python_313_from_setup_python_and_builds_the_spec(workflow):
    assert "actions/setup-python@" in workflow
    assert re.search(r"python-version:\s*[\"']3\.13[\"']", workflow)
    assert "pip install -e \".[qt,dev]\"" in workflow
    assert "PyInstaller --noconfirm --clean packaging/station.spec" in workflow


def test_the_workflow_smokes_the_bundle_headless(workflow):
    assert "packaging/smoke.sh" in workflow and "packaging/smoke.ps1" in workflow
    assert "xvfb-run" in workflow                      # Tk on Linux
    assert "SMOKE_QT_PLATFORM: offscreen" in workflow


def test_the_workflow_uploads_to_the_tags_release_and_publishes_last(workflow):
    assert "gh release create" in workflow and "--draft" in workflow
    assert "gh release upload" in workflow
    assert "gh release edit" in workflow and "--draft=false" in workflow
    assert "sha256sum" in workflow                     # the updater checks these
    assert "contents: write" in workflow
    # nothing signs (P7 is the owner's)
    assert "codesign" not in workflow and "signtool" not in workflow


def test_the_workflow_never_echoes_a_secret(workflow):
    assert "secrets." not in workflow or "secrets.GITHUB_TOKEN" in workflow
    assert "set -x" not in workflow


# -- V1 (2026-09-28): the trial video's encoder -------------------------------

IMAGEIO_FFMPEG = "imageio-ffmpeg==0.6.0"


def test_pyproject_pins_the_video_encoder(pyproject):
    assert IMAGEIO_FFMPEG in pyproject["project"]["dependencies"]


def test_requirements_pin_the_video_encoder_as_pyproject_does(pyproject):
    """`requirements.txt` installs the project (`-e .`), which brings the
    pin; it names the encoder too, and the two must never drift."""
    with open(os.path.join(ROOT, "requirements.txt"), encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    pinned = [l for l in lines if l.lower().startswith("imageio-ffmpeg")]
    assert pinned == [IMAGEIO_FFMPEG]
    assert "-e .[qt]" in lines


def test_imageio_ffmpeg_is_imported_lazily_and_only_by_the_video_device():
    """A native library lives in `devices/`, and this one is imported inside
    a function, never at module import (a model imports `devices.video`)."""
    users = []
    for directory, _, files in os.walk(SRC):
        for name in files:
            if not name.endswith(".py"):
                continue
            path = os.path.join(directory, name)
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read())
            for node in ast.walk(tree):
                names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                         else [node.module] if isinstance(node, ast.ImportFrom)
                         and node.module else [])
                if any(n.split(".")[0] == "imageio_ffmpeg" for n in names):
                    users.append(os.path.relpath(path, SRC).replace(os.sep, "/"))
                    assert node not in tree.body, f"{path}: a module-level import"
    assert set(users) == {"devices/video.py"}, users


def test_spec_collects_the_ffmpeg_binary(spec_source):
    """`imageio_ffmpeg` ships ffmpeg as package data and is imported
    lazily: the spec names the package and collects its binaries, so a
    bundle records MP4 rather than falling back to JPEG frames."""
    assert '"imageio_ffmpeg"' in spec_source
    assert '"imageio_ffmpeg.binaries"' in spec_source
    assert 'collect_data_files("imageio_ffmpeg", subdir="binaries")' in spec_source
    assert "FFMPEG_BINARIES" in spec_source.split("def analysis", 1)[1]


# -- dist-build B1: firmware beside the launchers ------------------------------

@pytest.fixture(scope="module")
def layout():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "station_layout", os.path.join(PACKAGING, "layout.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FIRMWARE = os.path.join(ROOT, "firmware")


def _tree(base):
    """{relative posix path: bytes} for every file under `base`."""
    out = {}
    for directory, _, files in os.walk(base):
        for name in files:
            path = os.path.join(directory, name)
            with open(path, "rb") as f:
                out[os.path.relpath(path, base).replace(os.sep, "/")] = f.read()
    return out


def test_the_layout_reads_the_sketch_table_without_importing_the_flasher(layout):
    assert layout.sketch_dirs() == sorted(
        ["stepper_firmware", "high_polling_rate", "chuck_firmware", "temp_controller"])
    constants = layout.flash_constants()
    assert constants["MEGA_FQBN"].startswith("arduino:avr:")
    assert constants["TEENSY_FQBN"].startswith("teensy:avr:")


def test_the_firmware_copy_is_byte_identical_and_skips_caches(layout, tmp_path):
    source = tmp_path / "firmware"
    import shutil
    shutil.copytree(FIRMWARE, source, ignore=shutil.ignore_patterns("__pycache__", "build"))
    (source / "__pycache__").mkdir()
    (source / "__pycache__" / "flash_firmware.cpython-313.pyc").write_bytes(b"x")
    (source / "temp_controller" / "build").mkdir()
    (source / "temp_controller" / "build" / "temp_controller.ino.hex").write_text(":00")
    bundle = tmp_path / "station"
    bundle.mkdir()
    layout.copy_firmware(str(source), str(bundle))
    copied = _tree(bundle / "firmware")
    expected = {k: v for k, v in _tree(source).items()
                if "__pycache__" not in k and "/build/" not in k}
    assert copied == expected
    for sketch in layout.sketch_dirs():
        assert f"{sketch}/{sketch}.ino" in copied, sketch
    assert any(k.startswith("libraries/LiquidCrystal_I2C/") for k in copied)
    assert not (bundle / "_internal" / "firmware").exists()


def test_the_spec_assembles_the_layout_after_collect_and_before_the_stamp(spec_source):
    collect = spec_source.index("coll = COLLECT(")
    assemble = spec_source.index("layout.assemble(coll.name)")
    stamp = spec_source.index("release.stamp(coll.name)")
    assert collect < assemble < stamp
    # firmware is copied beside the launchers, never declared as data (which
    # PyInstaller 6 would put under _internal/)
    datas = spec_source.split("def analysis", 1)[1].split("def executable", 1)[0]
    assert "firmware" not in datas


def test_assemble_copies_what_is_staged_and_warns_about_the_rest(layout, tmp_path):
    bundle = tmp_path / "station"
    bundle.mkdir()
    tools = tmp_path / "tools"
    tools.mkdir()
    (tools / layout.exe("arduino-cli")).write_text("cli")
    (tools / "arduino-data").mkdir()
    warnings = []
    done = layout.assemble(str(bundle), tools_stage=str(tools),
                           stable_dist=str(tmp_path / "no-stable"),
                           require_all=False, log=warnings.append)
    assert set(done) == {"firmware", "tools"}
    assert (bundle / "tools" / layout.exe("arduino-cli")).read_text() == "cli"
    assert (bundle / "firmware" / "stepper_firmware" / "stepper_firmware.ino").is_file()
    assert len(warnings) == 1 and "stable/" in warnings[0]


def test_assemble_refuses_a_partial_bundle_when_the_workflow_asks(layout, tmp_path):
    bundle = tmp_path / "station"
    bundle.mkdir()
    with pytest.raises(SystemExit, match="arduino-cli"):
        layout.assemble(str(bundle), tools_stage=str(tmp_path / "none"),
                        stable_dist=str(tmp_path / "none"), require_all=True)


def test_assemble_copies_the_stable_bundle_under_stable(layout, tmp_path):
    bundle = tmp_path / "station"
    bundle.mkdir()
    stable = tmp_path / "station-stable"
    (stable / "_internal").mkdir(parents=True)
    (stable / "firmware" / "stepper_firmware").mkdir(parents=True)
    (stable / layout.exe("station-stable")).write_text("exe")
    done = layout.assemble(str(bundle), tools_stage=str(tmp_path / "none"),
                           stable_dist=str(stable), require_all=False,
                           log=lambda *_: None)
    assert "stable" in done
    assert (bundle / "stable" / layout.exe("station-stable")).is_file()
    assert (bundle / "stable" / "_internal").is_dir()
    assert (bundle / "stable" / "firmware" / "stepper_firmware").is_dir()


# -- dist-build B2: arduino-cli, the cores and the Teensy loader, offline -----

@pytest.fixture(scope="module")
def tools():
    import importlib.util
    sys.path.insert(0, PACKAGING)
    spec = importlib.util.spec_from_file_location(
        "station_tools", os.path.join(PACKAGING, "tools.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_arduino_cli_is_pinned_with_its_published_checksum_per_platform(tools):
    assert tools.ARDUINO_CLI_VERSION == "1.5.1"
    assert tools.ARDUINO_CLI_ASSETS == {
        ("macos", "arm64"): ("arduino-cli_1.5.1_macOS_ARM64.tar.gz",
                             "cb952e8c1621c95ef5f1d17831c945e3d0ec5973f89c557a7ec8feb9c4f7d4c9"),
        ("macos", "x86_64"): ("arduino-cli_1.5.1_macOS_64bit.tar.gz",
                              "c982e940027996bea9901050e95fae99c59c1dcfee54beedecaf28141e7bf2e7"),
        ("linux", "x86_64"): ("arduino-cli_1.5.1_Linux_64bit.tar.gz",
                              "28a8e119c498a25607821c36cb2dc49e8463941b261a0d99091baa7bc692dd2b"),
        ("linux", "arm64"): ("arduino-cli_1.5.1_Linux_ARM64.tar.gz",
                             "1e69e077479f300614d4551334e0a33f08ee40b04315d83b8e7e0e94f0d0ee62"),
        ("windows", "x86_64"): ("arduino-cli_1.5.1_Windows_64bit.zip",
                                "fabe42e0eb04d00e776a66178299ff95a46c623dbc260f997e58fd514853dd40"),
    }
    for asset, digest in tools.ARDUINO_CLI_ASSETS.values():
        assert re.fullmatch(r"[0-9a-f]{64}", digest)
        assert tools.ARDUINO_CLI_VERSION in asset


def test_every_ci_runner_has_a_pinned_arduino_cli(tools):
    for system, machine in RUNNERS.values():
        assert tools.platform_key(system, machine) in tools.ARDUINO_CLI_ASSETS


def test_the_cores_are_the_ones_the_flashers_fqbns_name(tools, layout):
    constants = layout.flash_constants()
    platforms = {":".join(constants[k].split(":")[:2]) for k in ("MEGA_FQBN", "TEENSY_FQBN")}
    assert set(tools.CORES) == platforms == {"arduino:avr", "teensy:avr"}
    assert all(re.fullmatch(r"\d+\.\d+\.\d+", v) for v in tools.CORES.values())


def test_every_library_the_flasher_names_is_pinned_plus_max6675(tools, layout):
    constants = layout.flash_constants()
    for lib in constants["MEGA_LIBS"] + constants["TEENSY_LIBS"]:
        assert lib in tools.LIBRARIES, lib
    # temp_controller.ino includes MAX6675.h; no core ships it
    with open(os.path.join(ROOT, "firmware", "temp_controller", "temp_controller.ino"),
              encoding="utf-8") as f:
        assert "#include <MAX6675.h>" in f.read()
    assert tools.LIBRARIES["MAX6675"] == "0.3.4"
    assert tools.pinned_libraries() == [f"{n}@{v}" for n, v in tools.LIBRARIES.items()]


def test_a_library_the_flasher_names_without_a_pin_is_refused(tools, tmp_path):
    flasher = tmp_path / "flash_firmware.py"
    flasher.write_text('MEGA_LIBS = ["AccelStepper", "Servo"]\nTEENSY_LIBS = []\n')
    with pytest.raises(SystemExit, match="Servo"):
        tools.pinned_libraries(str(flasher))


def test_the_teensy_loader_is_pinned_by_commit_and_checksum(tools):
    assert re.fullmatch(r"[0-9a-f]{40}", tools.TEENSY_LOADER_COMMIT)
    assert tools.TEENSY_LOADER_COMMIT in tools.TEENSY_LOADER_URL
    assert tools.TEENSY_LOADER_SHA256 == \
        "8e10e19d51244699b003a0a8614bc7bb9cf5d21938748c05efd8fd79e54efa7d"
    # flash_firmware.py uploads the Teensy with teensy_loader_cli
    with open(os.path.join(ROOT, "firmware", "flash_firmware.py"), encoding="utf-8") as f:
        assert '"teensy_loader_cli"' in f.read()


def test_the_config_keeps_every_directory_inside_arduino_data(tools):
    text = tools.config_text()
    assert "  data: arduino-data\n" in text
    assert "  downloads: arduino-data/staging\n" in text
    assert "  user: arduino-data/user\n" in text
    assert tools.TEENSY_INDEX_URL in text


def _fake_cli_archive(path, key, tools):
    import io
    import tarfile
    import zipfile
    name = tools.exe("arduino-cli", key)
    if path.endswith(".zip"):
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr(name, "cli")
            zf.writestr("LICENSE.txt", "license")
    else:
        with tarfile.open(path, "w:gz") as tf:
            for member, data in ((name, b"cli"), ("LICENSE.txt", b"license")):
                info = tarfile.TarInfo(member)
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))


def _fake_world(tools, key, monkeypatch, tmp_path, teensy_source=b"int main(){}"):
    """A downloader that serves a fake CLI archive and loader source, with
    the pins pointed at them; a runner that records and fakes the compiler."""
    import hashlib
    asset, _ = tools.ARDUINO_CLI_ASSETS[key]
    archive = tmp_path / ("served-" + asset)
    _fake_cli_archive(str(archive), key, tools)
    monkeypatch.setitem(tools.ARDUINO_CLI_ASSETS, key,
                        (asset, hashlib.sha256(archive.read_bytes()).hexdigest()))
    monkeypatch.setattr(tools, "TEENSY_LOADER_SHA256",
                        hashlib.sha256(b"int main(){}").hexdigest())
    fetched, ran = [], []

    def download(url, dest):
        fetched.append(url)
        data = archive.read_bytes() if url.endswith(asset) else teensy_source
        with open(dest, "wb") as f:
            f.write(data)
        return dest

    def run(cmd, cwd=None, env=None):
        ran.append((cmd, cwd, env))
        if "-o" in cmd:                                   # the loader's compile
            with open(cmd[cmd.index("-o") + 1], "w") as f:
                f.write("loader")
        elif cmd[-2:] == ["core", "update-index"]:        # a download cache to prune
            os.makedirs(os.path.join(cwd, "arduino-data", "staging", "packages"))
    return download, run, fetched, ran


@pytest.mark.parametrize("key", [("macos", "arm64"), ("linux", "x86_64"),
                                 ("windows", "x86_64")])
def test_fetch_stages_the_cli_config_cores_libraries_and_loader(tools, monkeypatch,
                                                                 tmp_path, key):
    import json
    download, run, fetched, ran = _fake_world(tools, key, monkeypatch, tmp_path)
    stage = tmp_path / "tools"
    tools.fetch(str(stage), key=key, download=download, run=run)
    assert fetched == [tools.arduino_cli_url(key), tools.TEENSY_LOADER_URL]
    cli = stage / tools.exe("arduino-cli", key)
    assert cli.read_text() == "cli" and os.access(cli, os.X_OK)
    assert (stage / "arduino-cli.yaml").read_text() == tools.config_text()
    commands = [cmd[1:] for cmd, _, _ in ran[:-1]]
    assert commands == [
        ["--config-file", "arduino-cli.yaml", "core", "update-index"],
        ["--config-file", "arduino-cli.yaml", "core", "install", "arduino:avr@1.8.8"],
        ["--config-file", "arduino-cli.yaml", "core", "install", "teensy:avr@1.62.0",
         "--additional-urls", tools.TEENSY_INDEX_URL],
        ["--config-file", "arduino-cli.yaml", "lib", "install", "AccelStepper@1.64.0",
         "TMCStepper@0.7.3", "LiquidCrystal_I2C@2.0.0", "MAX6675@0.3.4"]]
    for cmd, cwd, env in ran[:-1]:
        assert cmd[0] == str(cli) and cwd == str(stage)
        assert env["ARDUINO_DIRECTORIES_DATA"] == str(stage / "arduino-data")
    loader = ran[-1][0]
    assert tools.TEENSY_LOADER_FLAGS[key[0]][2] in loader     # -DUSE_<backend>
    assert (stage / tools.exe("teensy_loader_cli", key)).read_text() == "loader"
    assert not (stage / "arduino-data" / "staging").exists()  # pruned
    manifest = json.loads((stage / "tools.json").read_text())
    assert manifest["arduino_cli"]["version"] == "1.5.1"
    assert manifest["cores"] == tools.CORES and manifest["libraries"] == tools.LIBRARIES
    assert manifest["fqbn"]["teensy"] == "teensy:avr:teensy35"


def test_fetch_refuses_an_archive_whose_checksum_does_not_match(tools, monkeypatch,
                                                               tmp_path):
    key = ("linux", "x86_64")
    download, run, _, ran = _fake_world(tools, key, monkeypatch, tmp_path)
    asset, _ = tools.ARDUINO_CLI_ASSETS[key]
    monkeypatch.setitem(tools.ARDUINO_CLI_ASSETS, key, (asset, "0" * 64))
    with pytest.raises(SystemExit, match="SHA-256"):
        tools.fetch(str(tmp_path / "tools"), key=key, download=download, run=run)
    assert ran == []                                    # nothing installed
    assert not (tmp_path / "tools" / "arduino-cli").exists()


def test_fetch_refuses_a_teensy_loader_source_that_changed(tools, monkeypatch, tmp_path):
    key = ("macos", "arm64")
    download, run, _, ran = _fake_world(tools, key, monkeypatch, tmp_path,
                                        teensy_source=b"tampered")
    with pytest.raises(SystemExit, match="teensy_loader_cli.c"):
        tools.fetch(str(tmp_path / "tools"), key=key, download=download, run=run)
    assert not any("-o" in cmd for cmd, _, _ in ran)    # never compiled


def test_an_unpinned_platform_is_refused(tools):
    with pytest.raises(SystemExit):
        tools.platform_key("Plan9", "mips")


# -- dist-build B3: the frozen stable app --------------------------------------

@pytest.fixture(scope="module")
def stable_spec():
    with open(os.path.join(PACKAGING, "stable.spec"), encoding="utf-8") as f:
        return f.read()


@pytest.fixture
def entry_stable():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "station_entry_stable", os.path.join(PACKAGING, "entry_stable.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)       # imports nothing from the stable app
    return module


def test_the_stable_spec_freezes_a_checkout_of_the_stable_ref(stable_spec):
    assert 'os.environ.get("STATION_STABLE_SRC")' in stable_spec
    assert 'os.path.join(ROOT, "build", "stable-src")' in stable_spec
    assert 'SRC = os.path.join(STABLE_SRC, "src")' in stable_spec
    assert "pathex=[SRC]" in stable_spec
    assert 'os.path.join(HERE, "entry_stable.py")' in stable_spec
    # a missing checkout is refused, never frozen from somewhere else
    assert 'raise SystemExit(f"stable.spec: no src/mainGUI.py' in stable_spec


def test_the_stable_spec_names_every_stable_module_and_third_party_import(stable_spec,
                                                                          entry_stable):
    for module in entry_stable.STABLE_MODULES:
        assert f'"{module}"' in stable_spec, module
    for module in ("serial", "serial.tools.list_ports", "pygame", "PIL.Image",
                   "numpy", "gcodeparser", "tkinter"):
        assert f'"{module}"' in stable_spec, module
    assert 'collect_submodules("mss"' in stable_spec
    # the new station's Qt, plots and encoder stay out of the old app
    for module in ("PySide6", "matplotlib", "imageio_ffmpeg"):
        assert f'"{module}"' in stable_spec.split("EXCLUDES = [", 1)[1], module


def test_the_stable_spec_outputs_the_contracts_launcher_and_its_firmware(stable_spec,
                                                                         layout):
    assert layout.STABLE_NAME == "station-stable"
    assert "name=layout.STABLE_NAME" in stable_spec
    assert stable_spec.count("COLLECT(") == 1 and "exclude_binaries=True" in stable_spec
    assert "layout.copy_firmware(os.path.join(STABLE_SRC, 'firmware'), coll.name)" \
        in stable_spec
    assert "spec_helpers.sdl3_for_sdl2_compat(a.binaries, workpath)" in stable_spec
    assert "spec_helpers.clear_hidden_flags(coll.name)" in stable_spec


def test_the_stable_spec_writes_nothing_into_the_stable_sources(stable_spec):
    writes = re.findall(r"open\(([^)]*)\)", stable_spec)
    assert writes and all("coll.name" in w for w in writes), writes


def test_the_stable_gcode_parser_is_a_pinned_build_requirement():
    with open(os.path.join(PACKAGING, "requirements-stable.txt"), encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    assert lines == ["gcodeparser==0.3.0"]


def test_entry_stable_runs_maingui_as_main_after_freeze_support(entry_stable, monkeypatch):
    import multiprocessing
    import runpy
    calls = []
    monkeypatch.setattr(multiprocessing, "freeze_support", lambda: calls.append("freeze"))
    monkeypatch.setattr(runpy, "run_module",
                        lambda name, **kw: calls.append(("run", name, kw)))
    assert entry_stable.main([]) == 0
    assert calls == ["freeze", ("run", "mainGUI",
                                {"run_name": "__main__", "alter_sys": True})]


def test_entry_stable_self_check_imports_every_module_and_opens_nothing(entry_stable,
                                                                        monkeypatch,
                                                                        capsys):
    import multiprocessing
    import runpy
    import types
    for name in entry_stable.STABLE_MODULES:
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setattr(multiprocessing, "freeze_support", lambda: None)
    monkeypatch.setattr(runpy, "run_module", lambda *a, **k: pytest.fail("ran the app"))
    assert entry_stable.main(["--self-check"]) == 0
    assert "station-stable: self-check ok" in capsys.readouterr().out


def test_the_stable_spec_is_tracked_despite_the_spec_ignore_rule():
    import subprocess
    done = subprocess.run(["git", "ls-files", "--error-unmatch", "packaging/stable.spec"],
                          cwd=ROOT, capture_output=True, text=True)
    if done.returncode not in (0, 1):
        pytest.skip("no git here")
    assert done.returncode == 0, "packaging/stable.spec is not tracked (*.spec is ignored)"
