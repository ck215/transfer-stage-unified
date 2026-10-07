"""Releases (REL-1..3): one version source, the release notes, and the
owner's one command, `dev/release.sh`.

`packaging/release.py` is loaded from its file, as `test_packaging.py`
does. `dev/release.sh` is run for real against a throwaway repository in
`tmp_path` whose `origin` is a bare repository beside it: nothing here
reaches the network or this checkout's remote, and nothing is pushed
anywhere but that bare repository.
"""
import datetime
import importlib.util
import os
import shutil
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



# -- REL-3: the changelog cut ----------------------------------------------------

SEED = """# Changelog

Preamble: dev/release.sh moves Unreleased under the new tag.

## [Unreleased]

### Added

- **Faster jog.** The probes jog faster.

### Fixed

- The stop is never late.

## [0.9.0] - 2026-09-01

- An old one.

[0.9.0]: https://example.invalid/releases/v0.9.0
"""
SECTION = ("### Added\n\n- **Faster jog.** The probes jog faster.\n\n"
           "### Fixed\n\n- The stop is never late.")


def test_the_cut_moves_unreleased_under_the_tag_and_leaves_it_empty(release_tool):
    text, body = release_tool.release_changelog(SEED, "v1.0.0", "2026-10-08")
    assert body == SECTION
    assert text == SEED.replace(
        "## [Unreleased]\n\n", "## [Unreleased]\n\n## [1.0.0] - 2026-10-08\n\n")
    assert release_tool.changelog_section(text, "Unreleased") == ""
    assert release_tool.changelog_section(text, "v1.0.0") == SECTION
    assert release_tool.changelog_section(text, "v0.9.0") == "- An old one."


@pytest.mark.parametrize("text, tag, words", [
    (SEED, "v0.9.0", "already has a section for v0.9.0"),
    (SEED, "v1.0", "not a release tag"),
    (SEED.replace("## [Unreleased]", "## Unreleased things"), "v1.0.0", "no '## [Unreleased]'"),
    ("# Changelog\n\n## [Unreleased]\n\n### Added\n\n<!-- nothing yet -->\n\n"
     "## [0.9.0] - 2026-09-01\n\n- An old one.\n", "v1.0.0", "Unreleased in CHANGELOG.md is empty"),
], ids=["already-released", "two-part-tag", "no-unreleased", "empty-unreleased"])
def test_the_cut_refuses(release_tool, text, tag, words):
    with pytest.raises(ValueError, match=__import__("re").escape(words)):
        release_tool.release_changelog(text, tag, "2026-10-08")


# -- REL-3: dev/release.sh, run for real against a throwaway origin --------------

#: What dev/release.sh needs from its own checkout.
RELEASE_FILES = ("dev/release.sh", "packaging/release.py", "packaging/release.json",
                 "src/controller/__init__.py", "src/controller/updater.py")


class ReleaseRepo:
    """A checkout of `mvc-refactor` holding dev/release.sh and its helpers,
    pushed to a bare `origin` beside it. Git sees only the config written
    here (identity, no signing): never the user's own."""

    def __init__(self, tmp_path):
        config = tmp_path / "gitconfig"
        config.write_text("[user]\n\tname = Station Owner\n\temail = owner@test\n"
                          "[commit]\n\tgpgsign = false\n[tag]\n\tgpgSign = false\n")
        self.env = {key: value for key, value in os.environ.items()
                    if not key.startswith("GIT_")}
        self.env.update(GIT_CONFIG_GLOBAL=str(config), GIT_CONFIG_NOSYSTEM="1",
                        PYTHON=sys.executable, TMPDIR=str(tmp_path),
                        PYTHONDONTWRITEBYTECODE="1")
        self.tmp = tmp_path
        self.origin = tmp_path / "origin.git"
        self.work = tmp_path / "work"
        self.git(tmp_path, "init", "-q", "--bare", "-b", "mvc-refactor", str(self.origin))
        self.work.mkdir()
        self.git(self.work, "init", "-q", "-b", "mvc-refactor")
        for rel in RELEASE_FILES:
            (self.work / rel).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(os.path.join(ROOT, rel), self.work / rel)
        (self.work / "CHANGELOG.md").write_text(SEED)
        (self.work / "README.md").write_text("station\n")
        self.commit_all("first")
        self.git(self.work, "remote", "add", "origin", str(self.origin))
        self.git(self.work, "push", "-q", "-u", "origin", "mvc-refactor")

    def git(self, cwd, *args):
        done = subprocess.run(["git", *args], cwd=cwd, env=self.env, capture_output=True,
                              text=True, timeout=30)
        assert done.returncode == 0, (args, done.stderr)
        return done.stdout.strip()

    def commit_all(self, message):
        self.git(self.work, "add", "-A")
        self.git(self.work, "commit", "-q", "-m", message)

    def release(self, *args):
        return subprocess.run(["bash", str(self.work / "dev" / "release.sh"), *args],
                              cwd=self.work, env=self.env, capture_output=True,
                              text=True, timeout=60)

    def state(self):
        """Everything a refusal must leave as it was. A tag the fetch brought
        from origin is origin's, not a change: `tags` is the tags only here."""
        origin_tags = set(self.git(self.origin, "tag", "-l").split())
        return {"head": self.git(self.work, "rev-parse", "HEAD"),
                "tags": set(self.git(self.work, "tag", "-l").split()) - origin_tags,
                "status": self.git(self.work, "status", "--porcelain"),
                "changelog": (self.work / "CHANGELOG.md").read_text(),
                "origin": self.git(self.origin, "for-each-ref",
                                   "--format=%(refname) %(objectname)")}


@pytest.fixture
def release_repo(tmp_path):
    if shutil.which("bash") is None or shutil.which("git") is None:
        pytest.skip("needs bash and git")
    return ReleaseRepo(tmp_path)


def test_release_sh_is_executable_and_parses():
    script = os.path.join(ROOT, "dev", "release.sh")
    assert os.access(script, os.X_OK)
    done = subprocess.run(["bash", "-n", script], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_release_sh_cuts_commits_and_tags_and_pushes_nothing(release_repo):
    repo = release_repo
    origin_before = repo.state()["origin"]
    done = repo.release("v1.0.0")
    assert done.returncode == 0, done.stderr
    today = datetime.date.today().isoformat()
    text = (repo.work / "CHANGELOG.md").read_text()
    assert text == SEED.replace("## [Unreleased]\n\n",
                                f"## [Unreleased]\n\n## [1.0.0] - {today}\n\n")
    # one commit, the CHANGELOG alone, and an annotated tag on it
    assert repo.git(repo.work, "log", "-1", "--format=%s") == "Release v1.0.0"
    assert repo.git(repo.work, "show", "--name-only", "--format=", "HEAD") == "CHANGELOG.md"
    assert repo.git(repo.work, "cat-file", "-t", "v1.0.0") == "tag"
    assert (repo.git(repo.work, "rev-parse", "v1.0.0^{commit}")
            == repo.git(repo.work, "rev-parse", "HEAD"))
    # the tag's message is the section, "### " headings and all
    assert repo.git(repo.work, "tag", "-l", "--format=%(contents)", "v1.0.0") == SECTION
    assert repo.git(repo.work, "status", "--porcelain") == ""
    # nothing went to origin; the two pushes are printed, branch first
    assert repo.state()["origin"] == origin_before
    branch_push = done.stdout.index("git push origin mvc-refactor\n")
    assert branch_push < done.stdout.index("git push origin v1.0.0\n")


def test_release_sh_push_sends_the_branch_and_the_tag(release_repo):
    repo = release_repo
    done = repo.release("v1.0.0", "--push")
    assert done.returncode == 0, done.stderr
    head = repo.git(repo.work, "rev-parse", "HEAD")
    assert repo.git(repo.origin, "rev-parse", "refs/heads/mvc-refactor") == head
    assert repo.git(repo.origin, "cat-file", "-t", "refs/tags/v1.0.0") == "tag"
    assert repo.git(repo.origin, "rev-parse", "refs/tags/v1.0.0^{commit}") == head


def _dirty(repo):
    (repo.work / "README.md").write_text("a bench edit\n")


def _staged(repo):
    _dirty(repo)
    repo.git(repo.work, "add", "README.md")


def _tag_here(repo):
    repo.git(repo.work, "tag", "-a", "v1.0.0", "-m", "made by hand")


def _tag_on_origin(repo):
    _tag_here(repo)
    repo.git(repo.work, "push", "-q", "origin", "v1.0.0")
    repo.git(repo.work, "tag", "-d", "v1.0.0")


def _unpushed(repo):
    (repo.work / "later.txt").write_text("later\n")
    repo.commit_all("not pushed yet")


def _behind_origin(repo):
    other = repo.tmp / "other"
    repo.git(repo.tmp, "clone", "-q", str(repo.origin), str(other))
    (other / "theirs.txt").write_text("theirs\n")
    repo.git(other, "add", "theirs.txt")
    repo.git(other, "commit", "-q", "-m", "theirs")
    repo.git(other, "push", "-q", "origin", "mvc-refactor")


def _detached(repo):
    repo.git(repo.work, "checkout", "-q", "--detach")


def _pushed_changelog(text):
    def setup(repo):
        (repo.work / "CHANGELOG.md").write_text(text)
        repo.commit_all("changelog")
        repo.git(repo.work, "push", "-q", "origin", "mvc-refactor")
    return setup


def _a_newer_release(repo):
    repo.git(repo.work, "tag", "-a", "v1.2.0", "-m", "newer")
    repo.git(repo.work, "push", "-q", "origin", "v1.2.0")


@pytest.mark.parametrize("setup, tag, words", [
    (None, "v1.0", "not a release tag"),
    (None, "1.0.0", "not a release tag"),
    (None, "v1.0.0-rc1", "not a release tag"),
    (_dirty, "v1.0.0", "local edits"),
    (_staged, "v1.0.0", "local edits"),
    (_tag_here, "v1.0.0", "The tag v1.0.0 already exists."),
    (_tag_on_origin, "v1.0.0", "The tag v1.0.0 already exists on origin."),
    (_unpushed, "v1.0.0", "HEAD is not on origin"),
    (_behind_origin, "v1.0.0", "commits this checkout lacks"),
    (_detached, "v1.0.0", "HEAD is detached"),
    (_pushed_changelog(SEED.replace(
        "### Added\n\n- **Faster jog.** The probes jog faster.\n\n"
        "### Fixed\n\n- The stop is never late.\n\n", "### Added\n\n")),
     "v1.0.0", "Unreleased in CHANGELOG.md is empty"),
    (_pushed_changelog(SEED.replace("## [Unreleased]\n\n", "")), "v1.0.0",
     "no '## [Unreleased]'"),
    (_a_newer_release, "v1.0.0", "v1.0.0 is not newer than the latest release, v1.2.0."),
], ids=["two-part", "no-v", "pre-release", "dirty", "staged", "tag-here",
        "tag-on-origin", "unpushed", "behind-origin", "detached", "empty-unreleased",
        "no-unreleased", "not-newer"])
def test_release_sh_refuses_and_changes_nothing(release_repo, setup, tag, words):
    repo = release_repo
    if setup is not None:
        setup(repo)
    before = repo.state()
    done = repo.release(tag, "--push")
    assert done.returncode == 1, (done.stdout, done.stderr)
    assert words in done.stderr, done.stderr
    assert repo.state() == before
