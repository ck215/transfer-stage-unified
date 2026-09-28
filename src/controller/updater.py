"""The station's own update check (owner, 2026-09-28: "This should prompt for
an update check on startup moving forward").

`Updater` is `update.sh` ported into the station, with the same rules:

    check()   fetch, then say how far this checkout is behind the branch it
              tracks and what is coming. The working tree is never touched.
    apply()   fast-forward only. Refused over local edits, over a checkout
              that has diverged from GitHub, offline, and in a frozen bundle.
              Reinstalls only when `pyproject.toml` / `requirements.txt`
              changed; says so when `firmware/` changed (flashing is
              the Setup page's Firmware row's job, never this one's).

There is no stash, no reset, no checkout and no branch switch anywhere in
this file, and there must never be: an update that can rewrite the bench's
edits is worse than no update. `update.sh` / `update.bat` stay for terminal
use.

Git runs through `subprocess.run` in the checkout that holds `src/`, with a
timeout on every call and no credential prompt, so a hung network or a
remote asking for a password fails the check instead of hanging it.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

#: The checkout this file lives in: src/controller/updater.py -> the repo.
ROOT = Path(__file__).resolve().parents[2]
#: Files whose change means the virtualenv must be reinstalled.
DEPENDENCY_FILES = ("pyproject.toml", "requirements.txt")
#: How many incoming commits the check lists.
LOG_LINES = 8
#: The reinstall's budget: a cold pip over a slow lab network.
PIP_SECONDS = 600
#: Local git commands (rev-parse, log, diff) are quick; a stuck one is not.
LOCAL_SECONDS = 15

UP_TO_DATE, BEHIND, DIVERGED, DIRTY = "up_to_date", "behind", "diverged", "dirty"
OFFLINE, NOT_GIT, BUNDLE, ERROR = "offline", "not_git", "bundle", "error"
STATUSES = (UP_TO_DATE, BEHIND, DIVERGED, DIRTY, OFFLINE, NOT_GIT, BUNDLE, ERROR)

#: What each refusal says: the operator's words, and what to do next.
REASONS = {
    UP_TO_DATE: "This checkout is up to date; there is nothing to update.",
    DIRTY: "This checkout has local edits; update by hand (commit or discard "
           "them first; they are not touched).",
    DIVERGED: "This checkout has commits GitHub does not, so it cannot "
              "fast-forward; update by hand and tell the lead which machine "
              "this is.",
    OFFLINE: "Could not reach GitHub; the station runs as it is. Check the "
             "network and try again.",
    NOT_GIT: "Not a git checkout, so the station cannot update itself.",
    BUNDLE: "This station is a packaged bundle; it is updated by installing "
            "a new bundle.",
}


class Updater:
    """`check()` and `apply()` over one checkout. `run=` stands in for
    `subprocess.run`, `clock=` for `time.monotonic` and `pip=` for the
    reinstall step (`pip(root) -> (ok, detail)`), so the tests drive it
    against a throwaway remote and never really pip."""

    def __init__(self, root=None, *, run=None, clock=None, pip=None):
        self.root = Path(root) if root is not None else ROOT
        self._run = run or subprocess.run
        self._clock = clock or time.monotonic
        self._pip = pip or self._install_dependencies
        #: Seconds the last check took, for the log file.
        self.elapsed = None

    # -- what the station runs ---------------------------------------------
    def version(self):
        """"<sha7>, <commit date>" of HEAD; "bundle" frozen; else "unknown"."""
        if _is_frozen():
            return "bundle"
        ok, out = self._git("log", "-1", "--format=%H %cs")
        if not ok or " " not in out:
            return "unknown"
        sha, date = out.split(" ", 1)
        return f"{sha[:7]}, {date.strip()}"

    def check(self, timeout=10.0):
        """How this checkout stands against the branch it tracks.

        -> {"status", "branch", "head", "remote", "behind", "ahead", "log",
        "reason"}; `status` is one of `STATUSES`. The fetch is the only call
        that reaches the network and the only one given `timeout`. Nothing
        here writes the working tree or moves HEAD."""
        started = self._clock()
        try:
            return self._check(timeout)
        finally:
            self.elapsed = self._clock() - started

    def apply(self, timeout=10.0):
        """Fast-forward to the tracked branch, then reinstall if the
        dependency files changed.

        -> {"updated", "old", "new", "deps_changed", "deps_ok",
        "firmware_changed", "reason"}. Refused (`updated: False`, the tree
        unchanged) unless a fresh check says `behind`."""
        result = {"updated": False, "old": None, "new": None,
                  "deps_changed": False, "deps_ok": True,
                  "firmware_changed": False, "reason": ""}
        found = self.check(timeout=timeout)
        result["old"] = result["new"] = found["head"]
        if found["status"] != BEHIND:
            result["reason"] = found["reason"] or REASONS.get(
                found["status"], "The update could not be checked.")
            return result
        ok, old = self._git("rev-parse", "HEAD")
        if not ok:
            result["reason"] = "The update could not read this checkout; nothing was changed."
            return result
        ok, out = self._git("merge", "--ff-only", "--quiet", self._upstream)
        if not ok:
            result["reason"] = "The fast-forward failed; nothing was changed."
            return result
        _, new = self._git("rev-parse", "HEAD")
        result.update(updated=True, old=old[:7], new=new[:7])
        sentences = [f"Updated {found['branch']}: {old[:7]} to {new[:7]}."]
        if self._changed(old, new, *DEPENDENCY_FILES):
            result["deps_changed"] = True
            try:
                deps_ok, detail = self._pip(self.root)
            except Exception as exc:        # reported, never hidden
                deps_ok, detail = False, repr(exc)
            result["deps_ok"] = bool(deps_ok)
            if deps_ok:
                sentences.append("The dependencies changed and were reinstalled.")
            else:
                sentences.append("The dependencies changed and pip install failed"
                                 f" ({detail}). Run it by hand: "
                                 "pip install -e '.[qt]'.")
        if self._changed(old, new, "firmware"):
            result["firmware_changed"] = True
            sentences.append("The firmware changed: after the restart, the "
                             "Setup page's Firmware row flashes the boards "
                             "that are out of date.")
        result["reason"] = " ".join(sentences)
        return result

    # -- the steps -----------------------------------------------------------
    def _check(self, timeout):
        result = {"status": ERROR, "branch": None, "head": None, "remote": None,
                  "behind": 0, "ahead": 0, "log": [], "reason": ""}
        self._upstream = None
        if _is_frozen():
            return _as(result, BUNDLE)
        ok, top = self._git("rev-parse", "--show-toplevel")
        if not ok or Path(top).resolve() != self.root.resolve():
            return _as(result, NOT_GIT)
        _, branch = self._git("rev-parse", "--abbrev-ref", "HEAD")
        _, sha = self._git("rev-parse", "HEAD")
        result["branch"], result["head"] = branch or None, (sha[:7] or None)
        ok, upstream = self._git("rev-parse", "--abbrev-ref",
                                 "--symbolic-full-name", "@{u}")
        if not ok or not upstream:
            result["reason"] = ("This checkout tracks no branch on GitHub; "
                                "update by hand. Set one with: git branch -u "
                                "origin/<branch>")
            return result
        ok, remote = self._git("config", f"branch.{branch}.remote")
        remote = remote if ok and remote else upstream.split("/", 1)[0]
        ok, _ = self._git("fetch", "--quiet", remote, timeout=timeout)
        if not ok:
            return _as(result, OFFLINE)
        self._upstream = upstream
        _, remote_sha = self._git("rev-parse", upstream)
        result["remote"] = remote_sha[:7] or None
        behind, ahead = self._count(f"HEAD..{upstream}"), self._count(f"{upstream}..HEAD")
        if behind is None or ahead is None:
            result["reason"] = "The update check could not compare this checkout with GitHub."
            return result
        result["behind"], result["ahead"] = behind, ahead
        if behind:
            _, log = self._git("log", "--oneline", "--no-decorate",
                               f"-n{LOG_LINES}", f"HEAD..{upstream}")
            result["log"] = [line for line in log.splitlines() if line.strip()]
        if behind == 0:
            result["status"] = UP_TO_DATE
            result["reason"] = ("" if not ahead else
                                f"This checkout has {ahead} local commit(s) "
                                "GitHub does not.")
            return result
        if self._is_dirty():
            return _as(result, DIRTY)
        if ahead:
            return _as(result, DIVERGED)
        result["status"] = BEHIND
        return result

    def _is_dirty(self):
        """Edits to tracked files, staged or not: `update.sh`'s rule.
        Untracked files (logs, data/) never block an update."""
        ok, out = self._git("status", "--porcelain", "--untracked-files=no")
        return (not ok) or bool(out.strip())

    def _count(self, spec):
        ok, out = self._git("rev-list", "--count", spec)
        try:
            return int(out) if ok else None
        except ValueError:
            return None

    def _changed(self, old, new, *paths):
        ok, out = self._git("diff", "--name-only", old, new, "--", *paths)
        return ok and bool(out.strip())

    def _git(self, *args, timeout=LOCAL_SECONDS):
        """(ok, stdout stripped). Never raises: a missing git, a timeout and
        a non-zero exit are all `ok=False`."""
        try:
            done = self._run(["git", *args], cwd=str(self.root),
                             capture_output=True, text=True, timeout=timeout,
                             env=_git_env())
        except (OSError, subprocess.SubprocessError, ValueError):
            return False, ""
        return done.returncode == 0, (done.stdout or "").strip()

    def _install_dependencies(self, root):
        """The default reinstall: this interpreter's pip, in the checkout."""
        argv = [sys.executable, "-m", "pip", "install", "--quiet", "-e", ".[qt]"]
        try:
            done = self._run(argv, cwd=str(root), capture_output=True, text=True,
                             timeout=PIP_SECONDS)
        except subprocess.TimeoutExpired:
            return False, f"pip took longer than {PIP_SECONDS} s"
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"pip could not start: {exc}"
        if done.returncode != 0:
            tail = ((done.stderr or done.stdout or "").strip().splitlines() or [""])[-1]
            return False, f"pip exited {done.returncode}: {tail}".rstrip(": ")
        return True, ""


def _as(result, status):
    result["status"] = status
    result["reason"] = REASONS.get(status, "")
    return result


def _is_frozen():
    return bool(getattr(sys, "frozen", False))


def _git_env():
    """Never wait on a prompt: no terminal credential prompt, and ssh in
    batch mode unless the operator configured their own."""
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env.setdefault("GIT_SSH_COMMAND", "ssh -oBatchMode=yes")
    env.setdefault("LC_ALL", "C")
    return env
