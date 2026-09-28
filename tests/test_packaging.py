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
    assert "def sdl3_for_sdl2_compat" in spec_source
    assert '("libSDL3.dylib", staged, "BINARY")' in spec_source
    assert '["chflags", "-R", "nohidden", coll.name]' in spec_source


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
