"""Releases (REL-1..3): one version source, the release notes, and the
owner's one command, `dev/release.sh`.

`packaging/release.py` is loaded from its file, as `test_packaging.py`
does. `dev/release.sh` is run for real against a throwaway repository in
`tmp_path` whose `origin` is a bare repository beside it: nothing here
reaches the network or this checkout's remote, and nothing is pushed
anywhere but that bare repository.
"""
import importlib.util
import os
import subprocess
import sys

import pytest

from controller.updater import Updater

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
PACKAGING = os.path.join(ROOT, "packaging")


@pytest.fixture(scope="module")
def release_tool():
    spec = importlib.util.spec_from_file_location(
        "station_release_tool_rel", os.path.join(PACKAGING, "release.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(*argv, cwd=ROOT, env=None):
    return subprocess.run([sys.executable, *argv], cwd=cwd, capture_output=True,
                          text=True, timeout=60, env=env)


# -- REL-1: one version source ------------------------------------------------

def test_release_py_version_is_the_updaters_version_of_this_tree(release_tool):
    done = _run(os.path.join(PACKAGING, "release.py"), "version")
    assert done.returncode == 0, done.stderr
    assert done.stdout == Updater(root=ROOT).version() + "\n"
    assert release_tool.version() == Updater(root=ROOT).version()


def test_app_version_prints_what_release_py_prints():
    env = dict(os.environ, STATION_NO_UPDATE_CHECK="1")
    app = _run(os.path.join(ROOT, "src", "app.py"), "--version", env=env)
    tool = _run(os.path.join(PACKAGING, "release.py"), "version")
    assert app.returncode == 0, app.stderr
    assert app.stdout == tool.stdout and app.stdout.strip()


def test_a_build_stamps_the_tag_from_ci_else_the_trees_version(release_tool, monkeypatch):
    monkeypatch.setenv("STATION_TAG", "v1.4.0")
    assert release_tool.current_tag() == "v1.4.0"
    monkeypatch.delenv("STATION_TAG")
    assert release_tool.current_tag() == Updater(root=ROOT).version()
