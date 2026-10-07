"""What a release build writes into the bundle, and how it is shipped
(brief-bundle-update B1/B2). Used by `station.spec` after COLLECT and by
`.github/workflows/package.yml`; stdlib only.

    python packaging/release.py version               this tree's version (REL-1)
    python packaging/release.py stamp BUNDLE          VERSION + release.json
    python packaging/release.py pyproject TAG         pyproject's version <- tag
    python packaging/release.py asset-name            this machine's asset name
    python packaging/release.py zip BUNDLE OUT.zip    the release asset

The version is the git tag `vMAJOR.MINOR.PATCH` and nothing else
(`pyproject.toml` says 0.0.0 in git). `version` prints what the tree it runs
in is: the tag when HEAD is exactly on one, else `git describe` rendered
PEP 440-ish (`1.3.0.post3+gabc1234`), `0.0.0+<sha7>` before the first
release, "unknown" without git. It is `controller.updater`'s own
`Updater.version()`, so the Setup page, `src/app.py --version`, a bundle's
VERSION and this command can never disagree.

`VERSION` is three lines: the tag, the commit, the build time (ISO, UTC).
`release.json` is `packaging/release.json` with the repository's owner and
name filled in (from `GITHUB_REPOSITORY` in CI, else the `origin` remote), so
`src/` never names the repository. The frozen `controller.updater` reads
both. The asset name comes from the updater's own `asset_name`, so what the
build uploads is what a bundle looks for.
"""
import datetime
import json
import os
import re
import stat
import subprocess
import sys
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
TEMPLATE = os.path.join(HERE, "release.json")
sys.path.insert(0, os.path.join(ROOT, "src"))

from controller.updater import RELEASE_FILE, VERSION_FILE  # noqa: E402
from controller.updater import Updater, render_version  # noqa: E402
from controller.updater import asset_name as _asset_name  # noqa: E402

#: The folder a release zip unpacks to (the updater accepts either shape).
TOP = "station"


def _git(*args):
    try:
        done = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                              text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return ""
    return done.stdout.strip() if done.returncode == 0 else ""


def template():
    with open(TEMPLATE, encoding="utf-8") as f:
        return json.load(f)


def asset_name(system=None, machine=None):
    return _asset_name(template(), system, machine)


def repository_from_remote(url):
    """"owner/repo" from an https or ssh GitHub remote URL, or ""."""
    found = re.search(r"github\.com[:/]+([^/]+)/([^/]+?)(?:\.git)?/?$", url.strip())
    return f"{found.group(1)}/{found.group(2)}" if found else ""


def version(root=ROOT):
    """The version of the tree at `root` (REL-1): `Updater.version()`."""
    return Updater(root=root).version()


def current_tag():
    """What a build stamps: CI's tag (`STATION_TAG`), else this tree's version."""
    return os.environ.get("STATION_TAG") or version()


def stamp(bundle, tag=None, sha=None, built=None, repository=None):
    """Write VERSION and release.json into `bundle` (beside the launchers)."""
    tag = tag or current_tag()
    sha = sha or os.environ.get("GITHUB_SHA") or _git("rev-parse", "HEAD") or "unknown"
    built = built or datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    repository = (repository or os.environ.get("GITHUB_REPOSITORY")
                  or repository_from_remote(_git("remote", "get-url", "origin")))
    owner, _, repo = repository.partition("/")
    info = dict(template(), owner=owner, repo=repo)
    with open(os.path.join(bundle, VERSION_FILE), "w", encoding="utf-8", newline="\n") as f:
        f.write(f"{tag}\n{sha}\n{built}\n")
    with open(os.path.join(bundle, RELEASE_FILE), "w", encoding="utf-8") as f:
        json.dump(info, f, indent=2)
        f.write("\n")
    return tag


def pep440(tag):
    """A tag or a version string -> what pyproject's version line says:
    "v1.3.0" -> "1.3.0"; `git describe`'s "v1.2.0-3-gabc1234" and the
    rendered "1.2.0.post3+gabc1234" -> "1.2.0.post3+gabc1234";
    "0.0.0+abc1234" stays; anything else -> None (pyproject left alone)."""
    text = render_version((tag or "").strip())
    found = re.fullmatch(r"v?(\d+(?:\.\d+)*(?:\.post\d+)?(?:\+[0-9A-Za-z.]+)?)", text)
    return found.group(1) if found else None


def patch_pyproject(path, tag):
    """The build's pyproject version follows the tag. The file in git keeps
    its placeholder; only a build's working copy is patched."""
    version = pep440(tag)
    if version is None:
        print(f"release.py: {tag!r} is not a version tag; pyproject left as it is")
        return None
    with open(path, encoding="utf-8") as f:
        text = f.read()
    text, count = re.subn(r'(?m)^version = "[^"]*"$', f'version = "{version}"', text, count=1)
    if count != 1:
        raise SystemExit(f"release.py: no version line in {path}")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)
    return version


def make_zip(bundle, out):
    """Zip `bundle` under one top folder, keeping POSIX modes and storing
    symlinks as links (macOS Qt frameworks); the updater unpacks both."""
    bundle = os.path.abspath(bundle)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for directory, subdirs, files in os.walk(bundle):
            subdirs.sort()
            rel = os.path.relpath(directory, bundle)
            base = TOP if rel == "." else f"{TOP}/{rel.replace(os.sep, '/')}"
            for name in sorted(files) + [d for d in subdirs
                                         if os.path.islink(os.path.join(directory, d))]:
                path = os.path.join(directory, name)
                arcname = f"{base}/{name}"
                if os.path.islink(path):
                    info = zipfile.ZipInfo(arcname)
                    info.create_system = 3
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                    zf.writestr(info, os.readlink(path))
                else:
                    zf.write(path, arcname)
            subdirs[:] = [d for d in subdirs if not os.path.islink(os.path.join(directory, d))]
    return out


def main(argv):
    if hasattr(sys.stdout, "reconfigure"):
        # "\n" on every OS: CI's bash reads these lines on Windows too, and a
        # trailing "\r" would make a name that matches nothing.
        sys.stdout.reconfigure(newline="\n")
    if argv[:1] == ["version"]:
        print(version())
    elif len(argv) >= 2 and argv[0] == "stamp":
        print(stamp(argv[1]))
    elif len(argv) >= 2 and argv[0] == "pyproject":
        patch_pyproject(os.path.join(ROOT, "pyproject.toml"), argv[1])
    elif argv[:1] == ["asset-name"]:
        print(asset_name())
    elif len(argv) >= 3 and argv[0] == "zip":
        make_zip(argv[1], argv[2])
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
