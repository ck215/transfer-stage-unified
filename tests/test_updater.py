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
