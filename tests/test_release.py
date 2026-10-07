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


# -- REL-2: the release's notes and its assets ---------------------------------

CHANGELOG_TEXT = """# Changelog

Preamble that is no section.

## [Unreleased]

### Added

- A thing that has not shipped.

## [1.4.0] - 2026-10-20

### Changed

- **Faster jog.** The probes jog faster.

### Fixed

- The stop is never late.

## [1.3.0] - 2026-10-08

- The first one.

[Unreleased]: https://example.invalid/compare/v1.4.0...HEAD
[1.4.0]: https://example.invalid/compare/v1.3.0...v1.4.0
"""


@pytest.mark.parametrize("name", ["v1.4.0", "1.4.0"])
def test_a_section_is_its_body_without_the_heading(release_tool, name):
    body = release_tool.changelog_section(CHANGELOG_TEXT, name)
    assert body == ("### Changed\n\n- **Faster jog.** The probes jog faster.\n\n"
                    "### Fixed\n\n- The stop is never late.")


def test_the_last_section_ends_at_the_link_definitions(release_tool):
    assert release_tool.changelog_section(CHANGELOG_TEXT, "v1.3.0") == "- The first one."
    assert release_tool.changelog_section(CHANGELOG_TEXT, "unreleased") == (
        "### Added\n\n- A thing that has not shipped.")
    assert release_tool.changelog_section(CHANGELOG_TEXT, "v9.9.9") is None


def test_notes_are_the_tags_section(release_tool, tmp_path, monkeypatch):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(CHANGELOG_TEXT, encoding="utf-8")
    monkeypatch.setattr(release_tool, "_git", lambda *a: pytest.fail("asked git"))
    assert release_tool.notes("v1.4.0", str(changelog)).startswith("### Changed\n")


def test_notes_fall_back_to_the_tags_message_then_the_tag(release_tool, tmp_path,
                                                          monkeypatch):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(CHANGELOG_TEXT, encoding="utf-8")
    asked = []

    def fake_git(*args):
        asked.append(args)
        return "Hand-made release\n\nwith a message" if args[-1] == "v2.0.0" else ""

    monkeypatch.setattr(release_tool, "_git", fake_git)
    assert release_tool.notes("v2.0.0", str(changelog)) == "Hand-made release\n\nwith a message"
    assert asked == [("tag", "-l", "--format=%(contents)", "v2.0.0")]
    assert release_tool.notes("v2.0.1", str(changelog)) == "v2.0.1"
    assert release_tool.notes("v2.0.1", str(tmp_path / "missing.md")) == "v2.0.1"


def test_the_notes_command_prints_the_section(tmp_path):
    done = _run(os.path.join(PACKAGING, "release.py"), "notes", "Unreleased")
    assert done.returncode == 0, done.stderr
    assert done.stdout.startswith("### ") and done.stdout.endswith("\n")


def test_the_assets_command_names_one_zip_per_target(release_tool):
    done = _run(os.path.join(PACKAGING, "release.py"), "assets")
    assert done.returncode == 0, done.stderr
    assert done.stdout.splitlines() == release_tool.assets() == [
        "station-macos-arm64.zip", "station-macos-x86_64.zip",
        "station-windows-x86_64.zip", "station-linux-x86_64.zip"]


def test_this_repositorys_changelog_has_an_unreleased_section_to_release(release_tool):
    with open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8") as f:
        text = f.read()
    unreleased = release_tool.changelog_section(text, "Unreleased")
    assert unreleased and any(line.startswith("- ") for line in unreleased.splitlines())
    assert "dev/release.sh" in text.split("## [Unreleased]", 1)[0]
