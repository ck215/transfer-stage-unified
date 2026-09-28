"""The update check (owner, 2026-09-28: "This should prompt for an update
check on startup moving forward").

`controller.updater.Updater` ports `update.sh`'s rules into the station:
fast-forward only, refuse on local edits or divergence, reinstall only when
the dependency files changed, flag firmware. Every test here runs real git
against a throwaway bare remote under `tmp_path`, the way `update.sh` was
tested; nothing here touches this checkout, and pip is never really run.
"""
import subprocess
from pathlib import Path
import sys

import pytest

from controller import updater as updater_module
from controller.updater import Updater

GIT_ID = ["-c", "user.name=Station Test", "-c", "user.email=station@test",
          "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main"]


def git(cwd, *args):
    done = subprocess.run(["git", *GIT_ID, *args], cwd=cwd, capture_output=True,
                          text=True, timeout=30)
    assert done.returncode == 0, (args, done.stderr)
    return done.stdout.strip()


def commit(cwd, path, text, message):
    target = cwd / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)
    git(cwd, "add", path)
    git(cwd, "commit", "-q", "-m", message)
    return git(cwd, "rev-parse", "HEAD")


@pytest.fixture
def repos(tmp_path):
    """(remote, station, upstream): a bare remote; the station's clone (the
    checkout the Updater looks after); a second clone that plays GitHub's
    other contributors, pushing what the station has not got yet."""
    remote = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    seed.mkdir()
    git(seed, "init", "-q", "-b", "main")
    commit(seed, "README.md", "station\n", "first")
    commit(seed, "pyproject.toml", "[project]\nname = 'station'\n", "deps")
    git(tmp_path, "clone", "-q", "--bare", str(seed), str(remote))
    station = tmp_path / "station"
    git(tmp_path, "clone", "-q", str(remote), str(station))
    upstream = tmp_path / "upstream"
    git(tmp_path, "clone", "-q", str(remote), str(upstream))
    return remote, station, upstream


def push(upstream, path, text, message):
    sha = commit(upstream, path, text, message)
    git(upstream, "push", "-q", "origin", "main")
    return sha


class Recorder:
    """`run=` for the Updater: the real subprocess.run, with every git
    command it was asked for kept, so a test can say what never ran."""

    def __init__(self):
        self.commands = []

    def __call__(self, argv, **kwargs):
        self.commands.append(list(argv))
        return subprocess.run(argv, **kwargs)

    def verbs(self):
        return [c[1] for c in self.commands if c and c[0] == "git" and len(c) > 1]


class FakePip:
    def __init__(self, ok=True):
        self.calls, self.ok = [], ok

    def __call__(self, root):
        self.calls.append(root)
        return (True, "") if self.ok else (False, "pip exited 1: no network")


def head(cwd):
    return git(cwd, "rev-parse", "HEAD")


# -- check() ---------------------------------------------------------------

def test_a_checkout_level_with_its_remote_is_up_to_date(repos):
    _, station, _ = repos
    result = Updater(root=station).check(timeout=10.0)
    assert result["status"] == "up_to_date"
    assert result["branch"] == "main"
    assert result["behind"] == 0 and result["ahead"] == 0
    assert result["head"] == result["remote"] == head(station)[:7]
    assert result["log"] == []


def test_behind_names_how_many_and_lists_what_is_coming(repos):
    _, station, upstream = repos
    before = head(station)
    for n in range(3):
        push(upstream, f"note{n}.txt", f"{n}\n", f"coming {n}")
    result = Updater(root=station).check(timeout=10.0)
    assert result["status"] == "behind"
    assert result["behind"] == 3 and result["ahead"] == 0
    assert result["head"] == before[:7]
    assert result["remote"] == head(upstream)[:7]
    assert [line.split(" ", 1)[1] for line in result["log"]] == [
        "coming 2", "coming 1", "coming 0"]
    # A check never touches the tree: HEAD and the working files are as they were.
    assert head(station) == before
    assert not (station / "note0.txt").exists()


def test_the_log_is_at_most_eight_lines(repos):
    _, station, upstream = repos
    for n in range(11):
        push(upstream, f"n{n}.txt", "x\n", f"c{n}")
    result = Updater(root=station).check(timeout=10.0)
    assert result["behind"] == 11
    assert len(result["log"]) == 8
    assert result["log"][0].endswith("c10")


def test_local_edits_are_dirty_and_the_edit_is_left_alone(repos):
    _, station, upstream = repos
    push(upstream, "new.txt", "new\n", "coming")
    (station / "README.md").write_text("a bench edit\n")
    result = Updater(root=station).check(timeout=10.0)
    assert result["status"] == "dirty"
    assert result["behind"] == 1
    assert "local edits" in result["reason"]
    assert (station / "README.md").read_text() == "a bench edit\n"


def test_a_local_commit_github_lacks_is_diverged(repos):
    _, station, upstream = repos
    push(upstream, "theirs.txt", "theirs\n", "theirs")
    commit(station, "mine.txt", "mine\n", "mine")
    result = Updater(root=station).check(timeout=10.0)
    assert result["status"] == "diverged"
    assert result["behind"] == 1 and result["ahead"] == 1


def test_a_local_commit_with_nothing_coming_is_still_up_to_date(repos):
    _, station, _ = repos
    commit(station, "mine.txt", "mine\n", "mine")
    result = Updater(root=station).check(timeout=10.0)
    assert result["status"] == "up_to_date"
    assert result["ahead"] == 1


def test_a_remote_that_does_not_answer_is_offline(repos, tmp_path):
    _, station, _ = repos
    git(station, "remote", "set-url", "origin", str(tmp_path / "no-such-remote.git"))
    result = Updater(root=station).check(timeout=5.0)
    assert result["status"] == "offline"
    assert "GitHub" in result["reason"]


def test_a_fetch_that_times_out_is_offline_not_a_hang(repos):
    _, station, _ = repos
    real = Recorder()
    seen = {}

    def slow_fetch(argv, **kwargs):
        if argv[:2] == ["git", "fetch"]:
            seen["timeout"] = kwargs.get("timeout")
            raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))
        return real(argv, **kwargs)

    result = Updater(root=station, run=slow_fetch).check(timeout=0.5)
    assert result["status"] == "offline"
    assert seen["timeout"] == 0.5


def test_a_frozen_bundle_has_no_git_and_runs_none(repos, monkeypatch):
    _, station, _ = repos
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    def no_git(*args, **kwargs):
        raise AssertionError("a bundle must not run git")

    updater = Updater(root=station, run=no_git)
    assert updater.check()["status"] == "bundle"
    assert updater.version() == "bundle"
    result = updater.apply()
    assert result["updated"] is False
    assert result["reason"]


def test_a_directory_that_is_no_checkout_is_not_git(tmp_path):
    bare_dir = tmp_path / "unpacked"
    bare_dir.mkdir()
    updater = Updater(root=bare_dir)
    assert updater.check()["status"] == "not_git"
    assert updater.version() == "unknown"


def test_a_branch_that_tracks_nothing_is_an_error_that_says_what_to_do(repos):
    _, station, _ = repos
    git(station, "checkout", "-q", "-b", "local-only")
    result = Updater(root=station).check(timeout=10.0)
    assert result["status"] == "error"
    assert "git branch -u" in result["reason"]


def test_version_is_the_short_sha_and_the_commit_date(repos):
    _, station, _ = repos
    sha = head(station)[:7]
    date = git(station, "log", "-1", "--format=%cs")
    assert Updater(root=station).version() == f"{sha}, {date}"


def test_the_default_root_is_the_checkout_that_holds_src():
    root = Updater().root
    assert (root / "src" / "controller" / "updater.py").is_file()


# -- apply() ---------------------------------------------------------------

def test_apply_fast_forwards_to_the_remote(repos):
    _, station, upstream = repos
    old = head(station)
    new = push(upstream, "new.txt", "new\n", "coming")
    pip = FakePip()
    result = Updater(root=station, pip=pip).apply()
    assert result["updated"] is True, result
    assert result["old"] == old[:7] and result["new"] == new[:7]
    assert head(station) == new
    assert (station / "new.txt").read_text() == "new\n"
    assert result["deps_changed"] is False and pip.calls == []
    assert result["firmware_changed"] is False
    # No merge commit: HEAD is exactly the remote's commit.
    assert git(station, "rev-list", "--count", "HEAD") == "3"


def test_apply_with_nothing_coming_changes_nothing(repos):
    _, station, _ = repos
    old = head(station)
    result = Updater(root=station, pip=FakePip()).apply()
    assert result["updated"] is False
    assert "up to date" in result["reason"].lower()
    assert head(station) == old


def test_apply_refuses_over_local_edits_and_changes_nothing(repos):
    _, station, upstream = repos
    old = head(station)
    push(upstream, "new.txt", "new\n", "coming")
    (station / "README.md").write_text("a bench edit\n")
    rec = Recorder()
    result = Updater(root=station, run=rec, pip=FakePip()).apply()
    assert result["updated"] is False
    assert "local edits" in result["reason"]
    assert head(station) == old
    assert (station / "README.md").read_text() == "a bench edit\n"
    assert not (station / "new.txt").exists()
    assert "merge" not in rec.verbs()


def test_apply_refuses_when_diverged_and_changes_nothing(repos):
    _, station, upstream = repos
    push(upstream, "theirs.txt", "theirs\n", "theirs")
    mine = commit(station, "mine.txt", "mine\n", "mine")
    rec = Recorder()
    result = Updater(root=station, run=rec, pip=FakePip()).apply()
    assert result["updated"] is False
    assert head(station) == mine
    assert not (station / "theirs.txt").exists()
    assert "merge" not in rec.verbs()


def test_apply_refuses_offline_and_changes_nothing(repos, tmp_path):
    _, station, _ = repos
    old = head(station)
    git(station, "remote", "set-url", "origin", str(tmp_path / "gone.git"))
    result = Updater(root=station, pip=FakePip()).apply()
    assert result["updated"] is False
    assert "GitHub" in result["reason"]
    assert head(station) == old


def test_apply_never_stashes_resets_or_switches_branch(repos):
    _, station, upstream = repos
    push(upstream, "a.txt", "a\n", "a")
    rec = Recorder()
    updater = Updater(root=station, run=rec, pip=FakePip())
    updater.check()
    updater.apply()
    (station / "README.md").write_text("edit\n")
    push(upstream, "b.txt", "b\n", "b")
    updater.apply()
    forbidden = {"stash", "reset", "checkout", "switch", "rebase", "pull",
                 "clean", "restore"}
    assert not forbidden & set(rec.verbs()), rec.verbs()
    merges = [c for c in rec.commands if c[1:2] == ["merge"]]
    assert merges and all("--ff-only" in c for c in merges)


def test_a_dependency_change_reinstalls_through_pip(repos):
    _, station, upstream = repos
    push(upstream, "pyproject.toml", "[project]\nname = 'station'\nversion = '2'\n",
         "deps moved")
    pip = FakePip()
    result = Updater(root=station, pip=pip).apply()
    assert result["updated"] is True
    assert result["deps_changed"] is True
    assert pip.calls == [station]


def test_a_requirements_change_counts_as_a_dependency_change(repos):
    _, station, upstream = repos
    push(upstream, "requirements.txt", "pyserial\n", "reqs")
    pip = FakePip()
    result = Updater(root=station, pip=pip).apply()
    assert result["deps_changed"] is True and len(pip.calls) == 1


def test_a_pip_failure_is_reported_not_hidden(repos):
    _, station, upstream = repos
    push(upstream, "pyproject.toml", "[project]\nname = 'x'\n", "deps")
    result = Updater(root=station, pip=FakePip(ok=False)).apply()
    assert result["updated"] is True        # the code landed; the reinstall did not
    assert result["deps_changed"] is True
    assert result["deps_ok"] is False
    assert "pip" in result["reason"] and "no network" in result["reason"]


def test_the_default_pip_step_is_this_python_installing_the_checkout(
        repos, monkeypatch):
    """Never really pip: the default step's argv is checked, not run."""
    _, station, _ = repos
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"], seen["kwargs"] = argv, kwargs
        return subprocess.CompletedProcess(argv, 0, "", "")

    ok, _ = Updater(root=station, run=fake_run)._install_dependencies(station)
    assert ok is True
    assert seen["argv"][:4] == [sys.executable, "-m", "pip", "install"]
    assert "-e" in seen["argv"] and ".[qt]" in seen["argv"]
    assert seen["kwargs"]["cwd"] == str(station)
    assert seen["kwargs"]["timeout"] == 600


def test_a_firmware_change_points_at_the_firmware_row(repos):
    _, station, upstream = repos
    push(upstream, "firmware/stepper/stepper.ino", "// v2\n", "firmware")
    result = Updater(root=station, pip=FakePip()).apply()
    assert result["updated"] is True
    assert result["firmware_changed"] is True
    assert "Firmware row" in result["reason"]
    assert "run_swap" not in result["reason"]


def test_no_launcher_or_updater_names_the_retired_run_swap():
    root = Path(__file__).resolve().parents[1]
    for name in ("update.sh", "update.bat", "src/controller/updater.py",
                 "src/controller/setup.py", "run.sh", "run.bat"):
        assert "run_swap" not in (root / name).read_text(), name


def test_git_runs_in_the_checkout_with_a_timeout_and_no_prompt(repos):
    _, station, _ = repos
    rec = Recorder()
    seen = []

    def spy(argv, **kwargs):
        seen.append(kwargs)
        return rec(argv, **kwargs)

    Updater(root=station, run=spy).check(timeout=7.0)
    assert seen and all(k["cwd"] == str(station) for k in seen)
    assert all(k.get("timeout") for k in seen)
    assert all(k["env"].get("GIT_TERMINAL_PROMPT") == "0" for k in seen)


def test_the_module_has_no_ui_words_for_a_platform():
    """No platform branch (owner ruling 2026-09-25)."""
    source = open(updater_module.__file__).read()
    assert "sys.platform" not in source and "os.name" not in source


# -- the bundle's update path (brief-bundle-update B3/B5, owner 2026-09-28) ---
#
# A frozen bundle reads its own VERSION and release.json, asks GitHub's
# Releases API with the machine's EXISTING GitHub sign-in (`gh auth token`,
# then `git credential fill`; owner ruling 2026-09-28: no token file), and
# swaps in the downloaded build. `fetch=` is a fake GitHub and `run=` a fake
# gh/git: nothing here reaches the network or a real credential store.

import hashlib
import io
import json
import os
import stat
import zipfile

from events import events

SECRET = "gho_s3cretTOKENvalue"
OWNER, REPO = "lab-owner", "station-repo"
LATEST = f"https://api.github.com/repos/{OWNER}/{REPO}/releases/latest"
ASSET_URL = f"https://api.github.com/repos/{OWNER}/{REPO}/releases/assets/42"


def _release_info():
    return {"owner": OWNER, "repo": REPO, "asset": "station-{os}-{arch}.zip",
            "os": {"Darwin": "macos", "Windows": "windows", "Linux": "linux"},
            "arch": {"arm64": "arm64", "aarch64": "arm64", "x86_64": "x86_64",
                     "AMD64": "x86_64", "amd64": "x86_64"}}


def _write_bundle(path, tag, marker, info=True):
    path.mkdir(parents=True)
    (path / "VERSION").write_text(f"{tag}\n{'a' * 40}\n2026-09-20T10:00:00Z\n")
    if info:
        (path / "release.json").write_text(json.dumps(_release_info()))
    (path / "_internal").mkdir()
    (path / "_internal" / "marker.txt").write_text(marker)
    launcher = path / "station-web"
    launcher.write_text("#!/bin/sh\n")
    launcher.chmod(0o755)
    return path


def _zip_of(tag, marker="new", top="station/", extra=()):
    """What the workflow's `release.py zip` makes: one top folder, an
    executable launcher, a symlink (Qt frameworks on macOS carry them)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        def put(name, data, mode=0o644):
            info = zipfile.ZipInfo(top + name)
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | mode) << 16
            zf.writestr(info, data)
        put("VERSION", f"{tag}\n{'b' * 40}\n2026-09-28T09:00:00Z\n")
        put("release.json", json.dumps(_release_info()))
        put("station-web", "#!/bin/sh\necho new\n", 0o755)
        put("_internal/marker.txt", marker)
        link = zipfile.ZipInfo(top + "_internal/current")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(link, "marker.txt")
        for name, data in extra:
            zf.writestr(name, data)
    return buffer.getvalue()


class FakeGitHub:
    """`fetch(url, headers, timeout, sink=None) -> (status, body)`: the
    latest-release JSON, and this platform's asset streamed to `sink`."""

    def __init__(self, tag="v1.3.0", status=200, body="Faster jog\n\nMore text.",
                 archive=None, size=None, asset_name=None, error=None, raw=None):
        self.tag, self.status, self.body = tag, status, body
        self.archive = archive if archive is not None else _zip_of(tag)
        self.size = size
        self.asset_name = asset_name or updater_module.asset_name(_release_info())
        self.error, self.raw = error, raw
        self.calls = []

    def release(self):
        return {"tag_name": self.tag, "name": f"Station {self.tag}", "body": self.body,
                "assets": [{"name": self.asset_name, "url": ASSET_URL,
                            "size": len(self.archive) if self.size is None else self.size}]}

    def __call__(self, url, headers=None, timeout=None, sink=None):
        self.calls.append({"url": url, "headers": dict(headers or {}),
                           "timeout": timeout, "sink": sink is not None})
        if self.error is not None:
            raise self.error
        if url == LATEST:
            if self.status != 200:
                return self.status, b'{"message": "Not Found"}'
            return 200, (self.raw if self.raw is not None
                         else json.dumps(self.release()).encode())
        if url == ASSET_URL:
            sink.write(self.archive)
            return 200, None
        raise AssertionError(f"unexpected URL {url}")


class FakeLogin:
    """`run=` for the credential lookup: `gh auth token`, then `git
    credential fill`; each answers or not as the test says."""

    def __init__(self, gh=SECRET, git=None):
        self.gh, self.git = gh, git
        self.commands = []

    def __call__(self, argv, **kwargs):
        self.commands.append({"argv": list(argv), **kwargs})
        if argv[0] == "gh":
            if self.gh is None:
                raise FileNotFoundError("gh")
            return subprocess.CompletedProcess(argv, 0 if self.gh else 1,
                                               stdout=f"{self.gh}\n", stderr="")
        if argv[0] == "git" and "credential" in argv:
            if self.git is None:
                return subprocess.CompletedProcess(argv, 128, stdout="",
                                                   stderr="fatal: could not read")
            return subprocess.CompletedProcess(
                argv, 0, stdout=f"protocol=https\nhost=github.com\nusername=x\n"
                                f"password={self.git}\n", stderr="")
        raise AssertionError(f"a bundle runs nothing else: {argv}")


@pytest.fixture
def bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    return _write_bundle(tmp_path / "apps" / "station", "v1.2.0", "old")


def _bundle_updater(bundle, fetch=None, login=None):
    return Updater(root=bundle, run=login or FakeLogin(), fetch=fetch or FakeGitHub())


def test_a_bundle_reads_its_version_file(bundle):
    updater = _bundle_updater(bundle)
    assert updater.version() == "v1.2.0, 2026-09-20"


def test_a_bundle_level_with_the_latest_release_is_up_to_date(bundle):
    github = FakeGitHub(tag="v1.2.0")
    result = _bundle_updater(bundle, github).check(timeout=4.0)
    assert result["status"] == "up_to_date"
    assert result["tag"] == result["latest"] == "v1.2.0"
    [call] = github.calls
    assert call["url"] == LATEST                    # owner/repo from release.json
    assert call["headers"]["Authorization"] == f"Bearer {SECRET}"
    assert call["timeout"] == 4.0


def test_a_bundle_behind_names_the_release_and_its_first_line(bundle):
    result = _bundle_updater(bundle).check()
    assert result["status"] == "behind"
    assert result["behind"] == 1
    assert (result["tag"], result["latest"], result["remote"]) == ("v1.2.0", "v1.3.0", "v1.3.0")
    assert result["title"] == "Station v1.3.0"
    assert result["log"] == ["Faster jog"]


def test_the_first_line_skips_blank_lines_markdown_and_checksums(bundle):
    body = ("\n## Faster jog\n\n" + "0" * 64 + "  station-linux-x86_64.zip\n")
    result = _bundle_updater(bundle, FakeGitHub(body=body)).check()
    assert result["log"] == ["Faster jog"]
    only_sums = "0" * 64 + "  station-linux-x86_64.zip\n"
    assert _bundle_updater(bundle, FakeGitHub(body=only_sums)).check()["log"] == []


def test_the_login_comes_from_gh_first(bundle):
    login = FakeLogin(gh=SECRET, git="from-git")
    github = FakeGitHub()
    _bundle_updater(bundle, github, login).check()
    assert [c["argv"][0] for c in login.commands] == ["gh"]
    assert github.calls[0]["headers"]["Authorization"] == f"Bearer {SECRET}"
    assert all(c.get("timeout") for c in login.commands)


@pytest.mark.parametrize("gh", [None, ""])     # gh not installed / not logged in
def test_the_login_falls_back_to_git_credential_fill(bundle, gh):
    login = FakeLogin(gh=gh, git="from-git-helper")
    github = FakeGitHub()
    assert _bundle_updater(bundle, github, login).check()["status"] == "behind"
    assert github.calls[0]["headers"]["Authorization"] == "Bearer from-git-helper"
    [fill] = [c for c in login.commands if c["argv"][0] == "git"]
    assert fill["argv"][-2:] == ["credential", "fill"]
    assert fill["input"].startswith("protocol=https\nhost=github.com\n")
    assert fill["timeout"]
    # never a prompt: no terminal, no credential-manager window
    assert fill["env"]["GIT_TERMINAL_PROMPT"] == "0"
    assert fill["env"]["GCM_INTERACTIVE"] == "never"


def test_no_login_at_all_is_unauthorised_and_asks_nothing_of_github(bundle):
    github = FakeGitHub()
    result = _bundle_updater(bundle, github, FakeLogin(gh=None, git=None)).check()
    assert result["status"] == "unauthorised"
    assert result["reason"] == ("Sign in to GitHub on this machine first: "
                                "`gh auth login`, or open the repository once with git.")
    assert github.calls == []


def test_a_login_command_that_hangs_is_no_login_not_a_hang(bundle):
    def stuck(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs.get("timeout"))
    result = Updater(root=bundle, run=stuck, fetch=FakeGitHub()).check()
    assert result["status"] == "unauthorised"


@pytest.mark.parametrize("code", [401, 404])
def test_github_refusing_the_login_is_unauthorised(bundle, code):
    result = _bundle_updater(bundle, FakeGitHub(status=code)).check()
    assert result["status"] == "unauthorised"
    assert "gh auth login" in result["reason"]


@pytest.mark.parametrize("error", [OSError("no route"), TimeoutError("slow"),
                                   __import__("urllib.error").error.URLError("dns")])
def test_github_out_of_reach_is_offline(bundle, error):
    result = _bundle_updater(bundle, FakeGitHub(error=error)).check()
    assert result["status"] == "offline"


def test_another_http_answer_or_bad_json_is_an_error(bundle):
    assert _bundle_updater(bundle, FakeGitHub(status=500)).check()["status"] == "error"
    assert _bundle_updater(bundle, FakeGitHub(raw=b"<html>")).check()["status"] == "error"


def test_a_bundle_without_release_json_stays_a_bundle_and_asks_nobody(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    root = _write_bundle(tmp_path / "station", "v1.2.0", "old", info=False)
    github, login = FakeGitHub(), FakeLogin()
    result = Updater(root=root, run=login, fetch=github).check()
    assert result["status"] == "bundle"
    assert github.calls == [] and login.commands == []


@pytest.mark.parametrize("system, machine, name", [
    ("Darwin", "arm64", "station-macos-arm64.zip"),
    ("Darwin", "x86_64", "station-macos-x86_64.zip"),
    ("Windows", "AMD64", "station-windows-x86_64.zip"),
    ("Linux", "x86_64", "station-linux-x86_64.zip"),
])
def test_the_asset_is_named_from_release_json(system, machine, name):
    assert updater_module.asset_name(_release_info(), system, machine) == name


def _names(path):
    return sorted(p.name for p in path.parent.iterdir())


def test_apply_downloads_unpacks_and_swaps_the_install(bundle):
    github = FakeGitHub()
    result = _bundle_updater(bundle, github).apply()
    assert result["updated"] is True, result["reason"]
    assert (result["old"], result["new"]) == ("v1.2.0", "v1.3.0")
    assert result["reason"] == "Updated to v1.3.0. Restart the station to run it."
    assert (bundle / "VERSION").read_text().startswith("v1.3.0\n")
    assert (bundle / "_internal" / "marker.txt").read_text() == "new"
    previous = bundle.with_name("station.previous")
    assert (previous / "_internal" / "marker.txt").read_text() == "old"
    # the temp dir is gone and nothing is staged: install and .previous only
    assert _names(bundle) == ["station", "station.previous"]
    # the download went to the asset's API URL, as a binary, with the login
    download = github.calls[-1]
    assert download["url"] == ASSET_URL and download["sink"]
    assert download["headers"]["Accept"] == "application/octet-stream"
    assert download["headers"]["Authorization"] == f"Bearer {SECRET}"
    assert download["timeout"]


@pytest.mark.skipif(not hasattr(os, "symlink") or sys.platform == "win32",
                    reason="POSIX modes and links")
def test_apply_keeps_the_launchers_executable_and_the_links_links(bundle):
    assert _bundle_updater(bundle).apply()["updated"] is True
    assert os.access(bundle / "station-web", os.X_OK)
    link = bundle / "_internal" / "current"
    assert link.is_symlink() and os.readlink(link) == "marker.txt"


def _untouched(bundle):
    assert (bundle / "VERSION").read_text().startswith("v1.2.0\n")
    assert (bundle / "_internal" / "marker.txt").read_text() == "old"
    assert _names(bundle) == ["station"]


def test_apply_refuses_a_download_of_the_wrong_size(bundle):
    result = _bundle_updater(bundle, FakeGitHub(size=10)).apply()
    assert result["updated"] is False and "size" in result["reason"]
    _untouched(bundle)


def test_apply_checks_the_sha256_the_release_lists(bundle):
    archive = _zip_of("v1.3.0")
    name = updater_module.asset_name(_release_info())
    wrong = f"Faster jog\n\n{'0' * 64}  {name}\n"
    result = _bundle_updater(bundle, FakeGitHub(archive=archive, body=wrong)).apply()
    assert result["updated"] is False and "SHA-256" in result["reason"]
    _untouched(bundle)
    right = f"Faster jog\n\n{hashlib.sha256(archive).hexdigest()}  {name}\n"
    assert _bundle_updater(bundle, FakeGitHub(archive=archive, body=right)).apply()["updated"]


def test_apply_refuses_when_the_release_has_no_build_for_this_machine(bundle):
    result = _bundle_updater(bundle, FakeGitHub(asset_name="station-beos-m68k.zip")).apply()
    assert result["updated"] is False
    assert updater_module.asset_name(_release_info()) in result["reason"]
    _untouched(bundle)


def test_apply_refuses_an_archive_that_escapes_its_folder(bundle):
    evil = _zip_of("v1.3.0", extra=[("../../escaped.txt", "x")])
    result = _bundle_updater(bundle, FakeGitHub(archive=evil)).apply()
    assert result["updated"] is False
    assert not list(bundle.parent.parent.rglob("escaped.txt"))
    _untouched(bundle)


def test_apply_refuses_an_archive_without_a_version_file(bundle):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("station/station-web", "x")
    result = _bundle_updater(bundle, FakeGitHub(archive=buffer.getvalue())).apply()
    assert result["updated"] is False
    _untouched(bundle)


def test_apply_with_nothing_newer_changes_nothing(bundle):
    github = FakeGitHub(tag="v1.2.0")
    result = _bundle_updater(bundle, github).apply()
    assert result["updated"] is False and result["reason"]
    assert [c["url"] for c in github.calls] == [LATEST]
    _untouched(bundle)


def test_a_failed_swap_restores_the_running_version(bundle, monkeypatch):
    real = updater_module._rename
    moves = []

    def second_move_fails(src, dst):
        moves.append((os.path.basename(src), os.path.basename(dst)))
        if len(moves) == 2:
            raise PermissionError("in use")
        real(src, dst)

    monkeypatch.setattr(updater_module, "_rename", second_move_fails)
    result = _bundle_updater(bundle).apply()
    assert result["updated"] is False
    assert "restored" in result["reason"]
    assert moves[:2] == [("station", "station.previous"), ("station.next", "station")]
    assert moves[2] == ("station.previous", "station")
    _untouched(bundle)


def test_a_running_folder_that_cannot_move_is_staged_for_the_restart(bundle, monkeypatch):
    """Windows keeps a running launcher's folder locked: the first move fails,
    so the new version waits in `.next` with an UPDATE_PENDING marker and the
    restart swaps it in (`app.restart_process`)."""
    real = updater_module._rename

    def install_locked(src, dst):
        if os.path.basename(src) == "station":
            raise PermissionError("in use")
        real(src, dst)

    monkeypatch.setattr(updater_module, "_rename", install_locked)
    result = _bundle_updater(bundle).apply()
    assert result["updated"] is True and result["pending"] is True
    assert result["new"] == "v1.3.0"
    assert result["reason"] == "Updated to v1.3.0. Restart the station to run it."
    staged = bundle.with_name("station.next")
    assert (staged / "VERSION").read_text().startswith("v1.3.0\n")
    assert (bundle / "UPDATE_PENDING").read_text().strip() == str(bundle.resolve())
    assert updater_module.pending_update(bundle) == bundle.resolve()
    assert (bundle / "VERSION").read_text().startswith("v1.2.0\n")   # still running


def test_nothing_is_pending_without_the_marker_or_the_staged_folder(bundle):
    assert updater_module.pending_update(bundle) is None
    (bundle / "UPDATE_PENDING").write_text(str(bundle))
    assert updater_module.pending_update(bundle) is None             # no .next


def _staged(bundle):
    staged = _write_bundle(bundle.with_name("station.next"), "v1.3.0", "new")
    (bundle / "UPDATE_PENDING").write_text(str(bundle.resolve()) + "\n")
    return staged


def test_finish_pending_swaps_and_leaves_no_marker(bundle):
    _staged(bundle)
    assert updater_module.finish_pending(bundle) == ""
    assert (bundle / "VERSION").read_text().startswith("v1.3.0\n")
    assert not (bundle / "UPDATE_PENDING").exists()
    assert _names(bundle) == ["station", "station.previous"]


def test_a_finish_that_fails_restores_and_clears_the_marker(bundle, monkeypatch):
    _staged(bundle)
    real, moves = updater_module._rename, []

    def second_fails(src, dst):
        moves.append(src)
        if len(moves) == 2:
            raise PermissionError("in use")
        real(src, dst)

    monkeypatch.setattr(updater_module, "_rename", second_fails)
    assert "restored" in updater_module.finish_pending(bundle)
    assert (bundle / "VERSION").read_text().startswith("v1.2.0\n")
    assert not (bundle / "UPDATE_PENDING").exists()      # no swap loop on every restart


def test_the_swap_script_waits_swaps_starts_and_deletes_itself(tmp_path):
    install = tmp_path / "Program 100%" / "station"
    argv = [str(install / "station-tk.exe"), "--font-size", "14"]
    text = updater_module.swap_script(install, 4242, argv)
    assert 'tasklist /FI "PID eq 4242"' in text
    assert "ping -n 2 127.0.0.1" in text               # a sleep that needs no console
    assert "geq 120" in text                           # the wait is bounded
    esc = str(install).replace("%", "%%")
    assert f'rmdir /s /q "{esc}.previous"' in text
    assert f'ren "{esc}" "station.previous"' in text
    assert f'ren "{esc}.next" "station"' in text
    assert f'ren "{esc}.previous" "station"' in text   # a failed swap puts it back
    assert "station-tk.exe\" \"--font-size\" \"14\"" in text
    assert text.index("ren \"") < text.index('start ""')
    assert 'del "%~f0"' in text


def test_the_login_is_never_logged_or_returned(bundle, tmp_path):
    """Everything the updater says goes through its results and the station
    log; the login must be in neither, on any path."""
    log = events.open_file(str(tmp_path / "logs"))
    try:
        said = []
        for github in (FakeGitHub(), FakeGitHub(status=401), FakeGitHub(status=500),
                       FakeGitHub(size=3), FakeGitHub(error=OSError("down"))):
            updater = _bundle_updater(bundle, github)
            said.append(updater.check())
            said.append(updater.apply())
            events.debug("Update", f"check/apply: {said[-2]} {said[-1]}", source="test")
        events.flush_file()
        text = open(log, encoding="utf-8").read()
    finally:
        events.close_file()
    assert said and SECRET not in repr(said)
    assert SECRET not in text
    assert not list(tmp_path.rglob("*token*"))      # held in memory, never written


def test_the_default_fetch_keeps_the_login_off_redirects():
    """GitHub answers an asset download with a redirect to storage that
    refuses a second credential; urllib must send Authorization only to
    GitHub. Builds the request only: nothing is opened."""
    request = updater_module._request(ASSET_URL, {"Authorization": "Bearer x",
                                                  "Accept": "application/octet-stream"})
    assert request.unredirected_hdrs.get("Authorization") == "Bearer x"
    assert "Authorization" not in request.headers
    assert request.headers.get("Accept") == "application/octet-stream"


def test_src_names_no_repository_owner():
    """owner/repo come from release.json, written at build time."""
    root = Path(__file__).resolve().parents[1]
    for path in (root / "src").rglob("*.py"):
        assert "ck215" not in path.read_text(encoding="utf-8"), path


def test_an_install_in_a_folder_it_cannot_write_is_refused_in_words(bundle, monkeypatch):
    def read_only(*args, **kwargs):
        raise PermissionError("read-only")

    monkeypatch.setattr(updater_module.tempfile, "mkdtemp", read_only)
    result = _bundle_updater(bundle).apply()
    assert result["updated"] is False and "cannot write" in result["reason"]
    _untouched(bundle)
