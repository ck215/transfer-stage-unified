"""The station's own update check (owner, 2026-09-28: "This should prompt for
an update check on startup moving forward").

`Updater` is `update.sh` ported into the station, with the same rules. It
speaks in versions (REL-4): a release is a tag `vMAJOR.MINOR.PATCH` on
GitHub (`dev/release.sh` cuts one), and a checkout updates from release to
release, never to a branch head.

    check()   fetch (tags included), then say which version this checkout
              is and whether a newer release exists: "This checkout is at
              1.3.0.post3+gabc1234; the latest release is v1.4.0 (3
              commits ahead)", else "no newer release". Under it, for
              developers, how the branch stands against the branch it
              tracks (`detail`). The working tree is never touched.
    apply()   fast-forward only, and only to the latest release tag's
              commit (`git merge --ff-only <that commit>`): never to the
              branch head, never when that commit does not descend from
              HEAD ("this checkout has diverged from the release"), never
              over local edits, never offline or in a frozen bundle.
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

A frozen bundle (brief-bundle-update B3, owner 2026-09-28) has no git
checkout. When the build stamped it (`VERSION` and `release.json` beside the
launchers, written by `packaging/release.py`), it updates from the
repository's GitHub Releases instead:

    check()   asks the Releases API for the latest release and compares its
              tag with `VERSION` as versions (v1.10.0 is newer than v1.9.0;
              a release older than the running one is no update).
    apply()   downloads this machine's asset (`release.json` names it),
              checks its size and its SHA-256 against the release's
              `SHA256SUMS` asset (or a sum listed in the notes, as before
              REL-2), unpacks it to `<install>.next`, then swaps: `<install>` ->
              `<install>.previous`, `<install>.next` -> `<install>`. A failed
              swap puts `.previous` back.

The repository is public (owner decision 5, 2026-09-30), so a machine with
no GitHub sign-in checks and downloads anonymously. A machine that is signed
in sends its login (owner ruling 2026-09-28: no token file; GitHub gives a
login a higher rate limit): `gh auth token`, else `git credential fill` (the
helper a browser or keychain sign-in filled). It is held in memory for one
call, sent only as the Authorization header to api.github.com, and never
written or logged. `/releases/latest` answering 404 means no release has
been published yet. A bundle without `release.json` (an unstamped local
build) keeps the old answer, `bundle`.

An update never touches anything outside the install folder: the swap moves
`<install>`, `<install>.next` and `<install>.previous`, nothing else. The
Transfer Map's store is chosen outside it (and refused inside it) for that
reason.
"""
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
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
#: `describe --dirty` looks at every tracked file: on a cold cache, a big
#: tree or a busy machine it takes far longer than the other local calls.
DESCRIBE_SECONDS = 60

UP_TO_DATE, BEHIND, DIVERGED, DIRTY = "up_to_date", "behind", "diverged", "dirty"
OFFLINE, NOT_GIT, BUNDLE, ERROR = "offline", "not_git", "bundle", "error"
UNAUTHORISED, NO_RELEASE = "unauthorised", "no_release"
STATUSES = (UP_TO_DATE, BEHIND, DIVERGED, DIRTY, OFFLINE, NOT_GIT, BUNDLE, ERROR,
            UNAUTHORISED, NO_RELEASE)

#: What a stamped bundle carries beside its launchers (`packaging/release.py`).
VERSION_FILE, RELEASE_FILE = "VERSION", "release.json"
#: Written into the install when its folder could not be moved while running
#: (Windows locks it): one line, the install; the restart finishes the swap.
PENDING_FILE = "UPDATE_PENDING"
#: The swap script `app.restart_process` leaves beside a Windows install.
SWAP_SCRIPT = "station-update.cmd"
API = "https://api.github.com"
#: A release asset is ~150 MB over a lab network: the timeout is per read,
#: not for the whole download.
DOWNLOAD_SECONDS = 60
#: A release is a tag `vMAJOR.MINOR.PATCH`, nothing more: a pre-release
#: (`v1.4.0-rc1`) or any other tag is not one.
RELEASE_TAG = re.compile(r"v(\d+)\.(\d+)\.(\d+)")
#: `git describe` over release tags only (the glob keeps `v1.3`, the
#: exclude keeps `v1.4.0-rc1` out); `update.sh` runs the same command.
DESCRIBE = ("describe", "--tags", "--always", "--dirty", "--abbrev=7",
            "--match", "v[0-9]*.[0-9]*.[0-9]*", "--exclude", "*-*")
_DESCRIBED = re.compile(r"v(\d+\.\d+\.\d+)-(\d+)-g([0-9a-f]+)")
_VERSION = re.compile(r"v?(\d+)\.(\d+)\.(\d+)(?:\.post(\d+))?(?:\+[0-9A-Za-z.]+)?")
#: A `sha256sum` line in a release body: "<64 hex>  <asset name>".
_SUM_LINE = re.compile(r"^([0-9a-fA-F]{64})\s+\*?(\S+)\s*$")

#: What each refusal says: the operator's words, and what to do next.
REASONS = {
    UP_TO_DATE: "This checkout is up to date; there is nothing to update.",
    DIRTY: "This checkout has local edits; update by hand (commit or discard "
           "them first; they are not touched).",
    DIVERGED: "This checkout has diverged from the release: it has commits "
              "the release does not, so it cannot fast-forward to it; update "
              "by hand and tell the lead which machine this is.",
    OFFLINE: "Could not reach GitHub; the station runs as it is. Check the "
             "network and try again.",
    NOT_GIT: "Not a git checkout, so the station cannot update itself.",
    BUNDLE: "This station is a packaged bundle; it is updated by installing "
            "a new bundle.",
    UNAUTHORISED: "Sign in to GitHub on this machine first: `gh auth login`, "
                  "or open the repository once with git.",
    NO_RELEASE: "No release has been published yet.",
}
#: The asset every release carries beside its zips (REL-2): `sha256sum`
#: lines, one per zip; the bundle's download is checked against it.
SUMS_ASSET = "SHA256SUMS"
#: GitHub answered 401: it did not take the login this machine sent.
REFUSED_LOGIN = ("GitHub did not accept this machine's sign-in for the "
                 "station's repository (HTTP {code}). Sign in with an account "
                 "that can read it: `gh auth login`.")


class Updater:
    """`check()` and `apply()` over one checkout. `run=` stands in for
    `subprocess.run`, `clock=` for `time.monotonic` and `pip=` for the
    reinstall step (`pip(root) -> (ok, detail)`), so the tests drive it
    against a throwaway remote and never really pip."""

    def __init__(self, root=None, *, run=None, clock=None, pip=None, fetch=None):
        """Frozen, `root` is the install (the folder holding the launchers;
        default: the running launcher's folder), `run=` also answers the
        login lookup and `fetch=` stands in for GitHub
        (`fetch(url, headers, timeout, sink=None) -> (status, body)`)."""
        if root is None:
            root = Path(sys.executable).parent if _is_frozen() else ROOT
        self.root = Path(root)
        self._run = run or subprocess.run
        self._clock = clock or time.monotonic
        self._pip = pip or self._install_dependencies
        self._fetch = fetch or _fetch
        #: Seconds the last check took, for the log file.
        self.elapsed = None
        #: (tag, commit) the last check found to fast-forward to, or None.
        self._target = None

    # -- what the station runs ---------------------------------------------
    def version(self):
        """The station's one version string (REL-1): the release tag `v1.3.0`
        when HEAD is exactly on one; else `git describe` rendered PEP
        440-ish, `1.3.0.post3+gabc1234` (3 commits past v1.3.0), with
        `.dirty` (or `+dirty` on the tag itself) over local edits; with no
        release tag at all `0.0.0+abc1234`; "unknown" when git cannot say.
        Frozen, the tag the build stamped into VERSION (`release.py`
        computed it the same way), or "bundle" without one.
        `packaging/release.py version` and `app.py --version` print this."""
        if _is_frozen():
            stamp = self._stamp()
            return stamp["tag"] if stamp is not None else "bundle"
        # Tried twice: Setup reads the version once per run, and one failed
        # read (a ref repacked by another git, a timeout under load) would
        # leave "unknown" on the Station row until the next start.
        for _ in range(2):
            ok, out = self._git(*DESCRIBE, timeout=DESCRIBE_SECONDS)
            if ok and out:
                return render_version(out)
        return "unknown"

    def check(self, timeout=10.0):
        """How this checkout stands against the latest release.

        -> {"status", "version", "tag", "latest", "behind", "ahead", "log",
        "reason", "branch", "head", "remote", "branch_behind",
        "branch_ahead", "detail"}; `status` is one of `STATUSES`.
        `version` (= `tag`, the running version, as a bundle's) is
        `version()`; `latest` the newest release tag (= `remote`); `behind`
        / `ahead` count commits between HEAD and that release; `log` is the
        release's first line, then what is coming (`git log --oneline`);
        `reason` says it in versions. `detail` is the developers' line: the
        branch against the branch it tracks (`branch_behind` /
        `branch_ahead`), which no update ever follows. The fetch is the only
        call that reaches the network and the only one given `timeout`.
        Nothing here writes the working tree or moves HEAD."""
        started = self._clock()
        try:
            return self._check(timeout)
        finally:
            self.elapsed = self._clock() - started

    def apply(self, timeout=10.0):
        """Fast-forward to the latest release's commit, then reinstall if the
        dependency files changed.

        -> {"updated", "old", "new", "deps_changed", "deps_ok",
        "firmware_changed", "reason"}; `old` / `new` are versions (`new` the
        release tag). Refused (`updated: False`, the tree unchanged) unless
        a fresh check says `behind`: a newer release that descends from
        HEAD, and no local edits."""
        result = {"updated": False, "old": None, "new": None,
                  "deps_changed": False, "deps_ok": True,
                  "firmware_changed": False, "reason": ""}
        if _is_frozen() and self._release_info() is not None:
            return self._apply_release(result, timeout)
        found = self.check(timeout=timeout)
        result["old"] = result["new"] = found.get("version") or found["head"]
        if found["status"] != BEHIND or self._target is None:
            result["reason"] = found["reason"] or REASONS.get(
                found["status"], "The update could not be checked.")
            return result
        latest, target = self._target
        ok, old = self._git("rev-parse", "HEAD")
        if not ok:
            result["reason"] = "The update could not read this checkout; nothing was changed."
            return result
        # The release's commit, never a branch: `--ff-only` refuses anything
        # that is not a fast-forward, so a moved tag cannot rewrite HEAD.
        ok, out = self._git("merge", "--ff-only", "--quiet", target)
        if not ok:
            result["reason"] = "The fast-forward failed; nothing was changed."
            return result
        _, new = self._git("rev-parse", "HEAD")
        result.update(updated=True, new=latest)
        sentences = [f"Updated this checkout from {result['old']} to {latest} "
                     f"({old[:7]} to {new[:7]})."]
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
                  "behind": 0, "ahead": 0, "log": [], "reason": "",
                  "version": None, "tag": None, "latest": None,
                  "branch_behind": None, "branch_ahead": None, "detail": ""}
        self._target = None
        if _is_frozen():
            info, stamp = self._release_info(), self._stamp()
            if info is None or stamp is None:
                return _as(result, BUNDLE)
            return self._check_release(result, info, stamp, timeout)[0]
        ok, top = self._git("rev-parse", "--show-toplevel")
        if not ok or Path(top).resolve() != self.root.resolve():
            return _as(result, NOT_GIT)
        _, branch = self._git("rev-parse", "--abbrev-ref", "HEAD")
        _, sha = self._git("rev-parse", "HEAD")
        result["branch"], result["head"] = branch or None, (sha[:7] or None)
        ok, upstream = self._git("rev-parse", "--abbrev-ref",
                                 "--symbolic-full-name", "@{u}")
        upstream = upstream if ok and upstream else None
        remote = "origin"
        if upstream:
            ok, configured = self._git("config", f"branch.{branch}.remote")
            remote = configured if ok and configured else upstream.split("/", 1)[0]
        if not self._git("config", f"remote.{remote}.url")[0]:
            result["reason"] = (f"This checkout has no remote '{remote}' to take "
                                "releases from; update by hand.")
            return result
        # Tags included: releases are tags. A fetch writes refs, never files.
        ok, _ = self._git("fetch", "--quiet", "--tags", remote, timeout=timeout)
        if not ok:
            return _as(result, OFFLINE)
        version = self.version()
        result["version"] = result["tag"] = version
        self._branch_line(result, branch, upstream)
        releases = self._releases()
        if not releases:
            result["status"] = NO_RELEASE
            result["reason"] = (f"This checkout is at {version}; no release has "
                                "been published yet.")
            return result
        _, latest, target = max(releases)
        result["latest"] = result["remote"] = latest
        if self._is_ancestor(target, "HEAD"):
            result["status"] = UP_TO_DATE
            result["reason"] = (f"This checkout is at {version}; no newer release "
                                f"(the latest is {latest}).")
            return result
        behind = self._count(f"HEAD..{target}")
        ahead = self._count(f"{target}..HEAD")
        if behind is None or ahead is None:
            result["reason"] = ("The update check could not compare this checkout "
                                f"with the release {latest}.")
            return result
        result["behind"], result["ahead"] = behind, ahead
        result["log"] = self._coming(latest, target)
        if not self._is_ancestor("HEAD", target):
            result["status"] = DIVERGED
            result["reason"] = (f"This checkout is at {version}; the latest release "
                                f"is {latest}, but this checkout has diverged from "
                                f"the release (it has {ahead} commit(s) the release "
                                "does not), so it cannot fast-forward; update by "
                                "hand and tell the lead which machine this is.")
            return result
        if self._is_dirty():
            return _as(result, DIRTY)
        result["status"] = BEHIND
        result["reason"] = (f"This checkout is at {version}; the latest release is "
                            f"{latest} ({behind} commit{'s' if behind != 1 else ''} "
                            "ahead).")
        self._target = (latest, target)
        return result

    def _branch_line(self, result, branch, upstream):
        """The developers' line: the branch against the one it tracks. An
        update never follows it (`git pull` does)."""
        if not upstream:
            result["detail"] = (f"Developers: {branch} tracks no branch on GitHub "
                                "(set one with: git branch -u origin/<branch>).")
            return
        behind, ahead = self._count(f"HEAD..{upstream}"), self._count(f"{upstream}..HEAD")
        if behind is None or ahead is None:
            return
        result["branch_behind"], result["branch_ahead"] = behind, ahead
        result["detail"] = (f"Developers: {branch} is {behind} commit(s) behind "
                            f"{upstream} and {ahead} ahead.")

    def _releases(self):
        """[(version key, tag, commit)] for every release tag this checkout
        has after the fetch. Pre-releases and other tags are not releases."""
        ok, out = self._git("for-each-ref",
                            "--format=%(refname:strip=2) %(objectname) %(*objectname)",
                            "refs/tags/")
        if not ok:
            return []
        found = []
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2 and RELEASE_TAG.fullmatch(parts[0]):
                found.append((version_key(parts[0]), parts[0], parts[-1]))
        return found

    def _coming(self, latest, target):
        """The release's first line (its notes are an annotated tag's
        message; a lightweight tag has none), then the commits it brings, at
        most LOG_LINES lines in all."""
        _, out = self._git("for-each-ref", "--format=%(objecttype)%0a%(contents)",
                           f"refs/tags/{latest}")
        kind, _, message = out.partition("\n")
        first = _first_line(message) if kind.strip() == "tag" else ""
        _, log = self._git("log", "--oneline", "--no-decorate",
                           f"-n{LOG_LINES}", f"HEAD..{target}")
        lines = [first] if first else []
        lines += [line for line in log.splitlines() if line.strip()]
        return lines[:LOG_LINES]

    def _is_ancestor(self, older, newer):
        """`older` is `newer` or one of its ancestors. An error is "no",
        which only ever refuses an update."""
        return self._git("merge-base", "--is-ancestor", older, newer)[0]

    # -- the bundle's path (B3) ----------------------------------------------
    def _stamp(self):
        """VERSION -> {"tag", "sha", "built"}, or None."""
        try:
            lines = (self.root / VERSION_FILE).read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            return None
        lines = [line.strip() for line in lines] + ["", "", ""]
        if not lines[0]:
            return None
        return {"tag": lines[0], "sha": lines[1], "built": lines[2]}

    def _release_info(self):
        """release.json -> dict with an owner and a repo, or None."""
        try:
            info = json.loads((self.root / RELEASE_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(info, dict) or not info.get("owner") or not info.get("repo"):
            return None
        return info

    def _login(self):
        """The machine's own GitHub sign-in, or None: `gh auth token`, then
        `git credential fill`. Never a prompt, never written anywhere."""
        env = _git_env()
        env["GH_PROMPT_DISABLED"] = "1"
        env["GCM_INTERACTIVE"] = "never"        # Git Credential Manager: no window
        try:
            done = self._run(["gh", "auth", "token", "--hostname", "github.com"],
                             capture_output=True, text=True,
                             timeout=LOCAL_SECONDS, env=env)
            token = (done.stdout or "").strip() if done.returncode == 0 else ""
            if token and not any(c.isspace() for c in token):
                return token
        except (OSError, subprocess.SubprocessError, ValueError):
            pass
        try:
            done = self._run(["git", "-c", "credential.interactive=false",
                              "credential", "fill"],
                             input="protocol=https\nhost=github.com\n\n",
                             capture_output=True, text=True,
                             timeout=LOCAL_SECONDS, env=env)
        except (OSError, subprocess.SubprocessError, ValueError):
            return None
        if done.returncode != 0:
            return None
        for line in (done.stdout or "").splitlines():
            key, _, value = line.partition("=")
            if key == "password" and value.strip():
                return value.strip()
        return None

    def _check_release(self, result, info, stamp, timeout):
        """-> (result, release or None, login or None)."""
        result.update(tag=stamp["tag"], head=stamp["tag"], version=stamp["tag"],
                      latest=None, title="", asset=asset_name(info))
        login = self._login()           # None: ask anonymously (a public repo)
        url = f"{API}/repos/{info['owner']}/{info['repo']}/releases/latest"
        try:
            code, body = self._fetch(url, headers=_headers(login), timeout=timeout)
        except (OSError, ValueError):       # URLError, timeouts, resets: all OSError
            return _as(result, OFFLINE), None, None
        if code == 404:
            return _as(result, NO_RELEASE), None, None
        if code == 401 and login is not None:
            result["status"] = UNAUTHORISED
            result["reason"] = REFUSED_LOGIN.format(code=code)
            return result, None, None
        if code != 200:
            result["reason"] = (f"GitHub answered the update check with HTTP "
                                f"{code}; the station runs as it is.")
            return result, None, None
        try:
            release = json.loads(body)
            latest = str(release["tag_name"]).strip()
        except (ValueError, TypeError, KeyError):
            result["reason"] = ("GitHub's answer to the update check could not "
                                "be read; the station runs as it is.")
            return result, None, None
        first = _first_line(release.get("body") or "")
        result.update(latest=latest, remote=latest,
                      title=str(release.get("name") or latest))
        if not _is_newer(latest, stamp["tag"]):
            result["status"] = UP_TO_DATE
            return result, release, login
        result.update(status=BEHIND, behind=1, log=[first] if first else [])
        return result, release, login

    def _apply_release(self, result, timeout):
        info, stamp = self._release_info(), self._stamp()
        if stamp is None:
            return dict(result, reason=REASONS[BUNDLE])
        started = self._clock()
        try:
            found, release, login = self._check_release(
                {"status": ERROR, "branch": None, "head": None, "remote": None,
                 "behind": 0, "ahead": 0, "log": [], "reason": ""},
                info, stamp, timeout)
        finally:
            self.elapsed = self._clock() - started
        result["old"] = result["new"] = stamp["tag"]
        if found["status"] != BEHIND:
            result["reason"] = found["reason"] or REASONS.get(
                found["status"], "The update could not be checked.")
            return result
        latest, name = found["latest"], found["asset"]
        asset = next((a for a in release.get("assets") or ()
                      if isinstance(a, dict) and a.get("name") == name), None)
        if asset is None or not asset.get("url"):
            result["reason"] = (f"The release {latest} has no {name} for this "
                                "machine; nothing was changed.")
            return result
        install = self.root.resolve()
        staged = install.with_name(install.name + ".next")
        previous = install.with_name(install.name + ".previous")
        try:
            work = Path(tempfile.mkdtemp(prefix=".station-update-", dir=install.parent))
        except OSError as exc:
            result["reason"] = (f"The station cannot write beside its folder "
                                f"({exc}); nothing was changed. Move the "
                                "station folder somewhere you can write, such "
                                "as your home folder.")
            return result
        try:
            refusal = self._download(asset, name, release, login, work / name, timeout)
            if refusal:
                result["reason"] = refusal
                return result
            _remove(staged)
            refusal = _unpack(work / name, work / "unpacked", staged)
            if refusal:
                _remove(staged)
                result["reason"] = refusal
                return result
            refusal, pending = _swap(install, staged, previous, stage=True), False
            if refusal is PENDING:
                refusal, pending = "", True
            if refusal:
                result["reason"] = refusal
                return result
        finally:
            shutil.rmtree(work, ignore_errors=True)
        result.update(updated=True, new=latest, pending=pending,
                      reason=f"Updated to {latest}. Restart the station to run it.")
        return result

    def _download(self, asset, name, release, login, path, timeout=10.0):
        """Fetch `asset` to `path`; -> a refusal sentence, or "" when the
        file is whole: its size, and its SHA-256 against the release's
        SHA256SUMS asset (else a sum its notes list). SHA256SUMS is read
        first, so a release whose sums cannot be read costs no download."""
        headers = dict(_headers(login), Accept="application/octet-stream")
        listed, refusal = self._expected_sum(release, name, headers, timeout)
        if refusal:
            return refusal
        try:
            with open(path, "wb") as sink:
                code, _ = self._fetch(asset["url"], headers=headers,
                                      timeout=DOWNLOAD_SECONDS, sink=sink)
        except (OSError, ValueError):
            return ("The download stopped before it finished; nothing was "
                    "changed. Check the network and try again.")
        if code != 200:
            return (f"GitHub answered the download with HTTP {code}; nothing "
                    "was changed.")
        size = path.stat().st_size
        expected = asset.get("size")
        if isinstance(expected, int) and size != expected:
            return (f"The download is the wrong size ({size} bytes, the release "
                    f"says {expected}); nothing was changed. Try again.")
        if listed:
            digest = hashlib.sha256()
            with open(path, "rb") as f:
                for block in iter(lambda: f.read(1 << 20), b""):
                    digest.update(block)
            if digest.hexdigest() != listed.lower():
                return ("The download does not match the SHA-256 the release "
                        "lists; nothing was changed. Try again, and tell the "
                        "lead if it happens twice.")
        return ""

    def _expected_sum(self, release, name, headers, timeout):
        """-> (the SHA-256 the release gives for `name` or None, a refusal
        or ""). A release that carries SHA256SUMS must list `name` in it."""
        sums = next((a for a in release.get("assets") or ()
                     if isinstance(a, dict) and a.get("name") == SUMS_ASSET
                     and a.get("url")), None)
        if sums is None:
            return _listed_sums(release.get("body") or "").get(name), ""
        unread = (f"The release's {SUMS_ASSET} could not be read; nothing was "
                  "changed. Check the network and try again.")
        try:
            code, body = self._fetch(sums["url"], headers=headers, timeout=timeout)
        except (OSError, ValueError):
            return None, unread
        if code != 200 or body is None:
            return None, unread
        try:
            listed = _listed_sums(body.decode("utf-8")).get(name)
        except UnicodeDecodeError:
            listed = None
        if not listed:
            return None, (f"The release's {SUMS_ASSET} does not list {name}; "
                          "nothing was changed. Tell the lead.")
        return listed, ""

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


# -- versions (REL-1) ---------------------------------------------------------
def render_version(described):
    """`git describe` (`DESCRIBE`) -> the version string `Updater.version`
    gives. `update.sh` renders the same output with the same rules (its
    `version_of`); `tests/test_updater.py` holds the two together.

        v1.3.0                  -> v1.3.0          (exactly on the release)
        v1.3.0-dirty            -> 1.3.0+dirty
        v1.3.0-3-gabc1234       -> 1.3.0.post3+gabc1234
        v1.3.0-3-gabc1234-dirty -> 1.3.0.post3+gabc1234.dirty
        abc1234                 -> 0.0.0+abc1234   (no release tag yet)
        abc1234-dirty           -> 0.0.0+abc1234.dirty

    Anything else comes back as it is."""
    text = str(described or "").strip()
    dirty = text.endswith("-dirty")
    core = text[:-len("-dirty")] if dirty else text
    if RELEASE_TAG.fullmatch(core):
        return f"{core[1:]}+dirty" if dirty else core
    found = _DESCRIBED.fullmatch(core)
    if found:
        base, ahead, sha = found.groups()
        return f"{base}.post{ahead}+g{sha}" + (".dirty" if dirty else "")
    if re.fullmatch(r"[0-9a-f]{4,40}", core):
        return f"0.0.0+{core}" + (".dirty" if dirty else "")
    return text


def version_key(text):
    """(major, minor, patch, post) of a release tag or of a version string
    `render_version` made, for comparing as versions (v1.10.0 is newer than
    v1.9.0, which a string comparison gets wrong); None for anything else."""
    found = _VERSION.fullmatch(str(text or "").strip())
    if not found:
        return None
    major, minor, patch, post = found.groups()
    return int(major), int(minor), int(patch), int(post or 0)


# -- the bundle's helpers -----------------------------------------------------
#: The one call that moves a folder; the tests make it fail on purpose.
_rename = os.rename


def asset_name(info, system=None, machine=None):
    """This machine's release asset, from release.json's pattern and maps:
    `station-{os}-{arch}.zip` with `platform.system()` / `platform.machine()`
    looked up (Darwin -> macos, AMD64 -> x86_64, ...). A lookup, never a
    branch; the build uses the same function to name what it uploads."""
    system = system or platform.system()
    machine = machine or platform.machine()
    os_name = (info.get("os") or {}).get(system, system.lower())
    arch = (info.get("arch") or {}).get(machine, machine.lower())
    return str(info.get("asset") or "station-{os}-{arch}.zip").format(
        os=os_name, arch=arch)


def _headers(login):
    """GitHub's headers; Authorization only when this machine has a login."""
    headers = {"Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28",
               "User-Agent": "transfer-stage-station"}
    if login:
        headers["Authorization"] = f"Bearer {login}"
    return headers


def _request(url, headers):
    """A urllib request whose Authorization is never carried across a
    redirect: GitHub sends an asset download on to storage that refuses a
    second credential, and the login must reach GitHub alone."""
    request = urllib.request.Request(url)
    for key, value in headers.items():
        if key.lower() == "authorization":
            request.add_unredirected_header(key, value)
        else:
            request.add_header(key, value)
    return request


def _fetch(url, headers=None, timeout=10.0, sink=None):
    """The default `fetch=`: urllib, with a timeout. -> (status, body bytes),
    or (status, None) once the body is streamed into `sink`. An HTTP error
    status is returned, not raised; a network failure raises OSError."""
    try:
        response = urllib.request.urlopen(_request(url, headers or {}), timeout=timeout)
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read()
        except Exception:               # an error with no body to read
            body = b""
        finally:
            exc.close()
        return exc.code, body
    with response:
        if sink is None:
            return response.status, response.read()
        shutil.copyfileobj(response, sink, 1 << 20)
        return response.status, None


#: Keep-a-Changelog's category headings: never the line an operator reads.
_CATEGORIES = {"added", "changed", "deprecated", "removed", "fixed", "security"}


def _first_line(body):
    """The release notes' first line the operator reads: blank lines,
    checksum lines, Markdown markers and the CHANGELOG's category and
    version headings ("### Added", "## [1.4.0] - 2026-10-20") skipped."""
    for line in str(body).splitlines():
        text = line.strip().lstrip("#*->").strip()
        if not text or _SUM_LINE.match(line.strip()):
            continue
        if line.lstrip().startswith("#") and (
                text.lower() in _CATEGORIES or text.startswith("[")):
            continue
        return text
    return ""


def _is_newer(latest, running):
    """`latest` is a newer version than `running`, compared as versions; a
    tag that is no version is newer whenever it differs (the rule before
    REL-4)."""
    latest_key, running_key = version_key(latest), version_key(running)
    if latest_key is None or running_key is None:
        return str(latest).strip() != str(running).strip()
    return latest_key > running_key


def _listed_sums(body):
    sums = {}
    for line in str(body).splitlines():
        found = _SUM_LINE.match(line.strip())
        if found:
            sums[found.group(2)] = found.group(1)
    return sums


def _remove(path):
    if os.path.islink(path) or os.path.isfile(path):
        os.unlink(path)
    elif os.path.isdir(path):
        shutil.rmtree(path)


def _inside(root, path):
    root, path = os.path.normcase(root), os.path.normcase(path)
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _unpack(archive, work, staged):
    """Unpack `archive` into `work`, then move the folder holding VERSION to
    `staged`. Keeps POSIX modes (the launchers must stay executable) and
    symlinks (macOS Qt frameworks); refuses any entry or link that would land
    outside `work`. -> a refusal sentence, or ""."""
    refused = ("The download could not be unpacked ({}); nothing was changed.")
    root = os.path.realpath(work)
    try:
        os.makedirs(root)
        with zipfile.ZipFile(archive) as zf:
            for info in zf.infolist():
                name = info.filename.replace("\\", "/")
                target = os.path.normpath(os.path.join(root, name))
                if os.path.isabs(name) or not _inside(root, target):
                    return refused.format(f"{info.filename} is outside the bundle")
                if name.endswith("/"):
                    os.makedirs(target, exist_ok=True)
                    continue
                parent = os.path.dirname(target)
                os.makedirs(parent, exist_ok=True)
                if not _inside(root, os.path.realpath(parent)):
                    return refused.format(f"{info.filename} is outside the bundle")
                mode = info.external_attr >> 16
                if stat.S_ISLNK(mode):
                    link = zf.read(info).decode("utf-8")
                    if os.path.isabs(link) or not _inside(
                            root, os.path.normpath(os.path.join(parent, link))):
                        return refused.format(f"{info.filename} links outside the bundle")
                    os.symlink(link, target)
                    continue
                with zf.open(info) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst, 1 << 20)
                if mode & 0o777:
                    os.chmod(target, mode & 0o777)
    except (OSError, zipfile.BadZipFile, UnicodeDecodeError, ValueError) as exc:
        return refused.format(exc)
    top = root
    entries = os.listdir(root)
    if VERSION_FILE not in entries and len(entries) == 1:
        top = os.path.join(root, entries[0])
    if not os.path.isfile(os.path.join(top, VERSION_FILE)):
        return refused.format("it carries no VERSION")
    try:
        os.replace(top, staged)             # within the temp dir's filesystem
    except OSError as exc:
        return refused.format(exc)
    return ""


#: `_swap`'s answer when the running folder is locked and the swap is left
#: to the restart.
PENDING = object()


def _swap(install, staged, previous, stage=False):
    """`install` -> `previous`, `staged` -> `install`; the first move undone
    when the second fails. -> a refusal sentence, or "". With `stage`, a
    running folder that cannot move (Windows locks a running launcher's
    folder) is not a failure: `staged` stays, UPDATE_PENDING names the
    install, and the answer is `PENDING`. No platform branch: the move is
    tried everywhere, and only its failure stages."""
    try:
        _remove(previous)
    except OSError as exc:
        if stage:
            return _stage(install)
        _remove_quietly(staged)
        return (f"The last update's {previous.name} could not be cleared ({exc}); "
                "nothing was changed.")
    try:
        _rename(install, previous)
    except OSError as exc:
        if stage:
            return _stage(install)
        _remove_quietly(staged)
        return (f"The running version could not be moved aside ({exc}); nothing "
                "was changed. Quit the station and try again.")
    try:
        _rename(staged, install)
    except OSError as exc:
        try:
            _rename(previous, install)
        except OSError as again:
            return (f"The new version could not be put in place ({exc}) and the "
                    f"running version could not be moved back ({again}). It is "
                    f"in {previous}: rename it to {install.name} before starting "
                    "the station again.")
        _remove_quietly(staged)
        return (f"The new version could not be put in place ({exc}); the running "
                "version was restored and nothing was changed.")
    return ""


def _stage(install):
    try:
        with open(install / PENDING_FILE, "w", encoding="utf-8") as f:
            f.write(f"{install}\n")
    except OSError as exc:
        _remove_quietly(install.with_name(install.name + ".next"))
        return (f"The running version could not be moved aside and the update "
                f"could not be staged ({exc}); nothing was changed.")
    return PENDING


def pending_update(install):
    """The install whose staged update waits for the restart, or None: its
    UPDATE_PENDING marker and its `<install>.next` (holding a VERSION)."""
    install = Path(install).resolve()
    marker = install / PENDING_FILE
    staged = install.with_name(install.name + ".next")
    if marker.is_file() and (staged / VERSION_FILE).is_file():
        return install
    return None


def finish_pending(install):
    """The restart's swap where a folder can move while its launcher runs
    (every OS but Windows; there the swap script does it). Clears the marker
    either way, so a swap that cannot happen is not retried on every
    restart. -> a refusal sentence, or ""."""
    install = Path(install).resolve()
    refusal = _swap(install, install.with_name(install.name + ".next"),
                    install.with_name(install.name + ".previous"))
    _remove_quietly(install / PENDING_FILE)
    return refusal


def _cmd_quote(text):
    return '"' + str(text).replace("%", "%%") + '"'


def swap_script(install, pid, argv, tries=120):
    """The `.cmd` a Windows restart leaves beside the install: wait (at most
    `tries` seconds) for process `pid` to exit, move the install to
    `.previous` and `.next` into its place (the old one put back if that
    fails), start `argv` from the install's path, delete itself. Text only;
    `app.restart_process` writes and starts it."""
    install = Path(install)
    folder = str(install)
    previous, staged = folder + ".previous", folder + ".next"
    name = install.name
    command = " ".join(_cmd_quote(a) for a in argv)
    return "\r\n".join([
        "@echo off",
        "chcp 65001 >NUL",
        f"rem Station update: wait for the station (PID {pid}) to exit, swap in",
        "rem the new version, start it. Written by app.restart_process.",
        "set /a tries=0",
        ":wait",
        f'tasklist /FI "PID eq {pid}" /NH 2>NUL | find " {pid} " >NUL',
        "if errorlevel 1 goto swap",
        "set /a tries+=1",
        f"if %tries% geq {tries} goto swap",
        "ping -n 2 127.0.0.1 >NUL",
        "goto wait",
        ":swap",
        f"if exist {_cmd_quote(previous)} rmdir /s /q {_cmd_quote(previous)}",
        f"ren {_cmd_quote(folder)} {_cmd_quote(name + '.previous')} || goto start",
        f"ren {_cmd_quote(staged)} {_cmd_quote(name)} || "
        f"(ren {_cmd_quote(previous)} {_cmd_quote(name)} & goto start)",
        ":start",
        # A swap that failed leaves the marker in the old install: it goes
        # before the start, or the next start would try the swap again.
        f'if exist {_cmd_quote(folder + chr(92) + PENDING_FILE)} '
        f'del /q {_cmd_quote(folder + chr(92) + PENDING_FILE)}',
        f'start "" {command}',
        '(goto) 2>NUL & del "%~f0"',
        ""])


def _remove_quietly(path):
    try:
        _remove(path)
    except OSError:
        pass


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
    # `describe --dirty` and `status` refresh the index when they may; a
    # check running beside someone's own git must not take its lock.
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env.setdefault("GIT_SSH_COMMAND", "ssh -oBatchMode=yes")
    env.setdefault("LC_ALL", "C")
    return env
