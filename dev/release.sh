#!/bin/bash
# dev/release.sh — cut a release: the owner's one command (REL-3).
#
#   dev/release.sh vX.Y.Z          check, move CHANGELOG.md's Unreleased under
#                                  vX.Y.Z, commit that, tag it; print the pushes
#   dev/release.sh vX.Y.Z --push   the same, then push the branch and the tag
#
# Run it on a clean `main`, level with origin/main: releases are tags on main
# (packaging/README.md, "Branches"), in the checkout this script lives in. It
# refuses, changing nothing: a tag that is not vMAJOR.MINOR.PATCH, one that
# exists here or on origin, one not newer than the latest release, any branch
# but main, local edits, a HEAD that is not exactly origin/main, and an empty
# Unreleased section.
# Nothing is pushed without --push. Pushing the tag starts
# .github/workflows/package.yml, which builds every bundle and publishes the
# release when all of them are on it (packaging/README.md, "Cutting a release").
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
say()  { echo "[release] $*"; }
fail() { echo "[release] $*" >&2; exit 1; }

TAG=""
PUSH=0
for arg in "$@"; do
    case "$arg" in
        --push) PUSH=1 ;;
        -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
        -*) fail "unknown option '$arg' (try --help)" ;;
        *) [ -z "$TAG" ] || fail "one tag at a time ('$TAG' and '$arg')"; TAG="$arg" ;;
    esac
done
[ -n "$TAG" ] || fail "usage: dev/release.sh vX.Y.Z [--push]"
[[ "$TAG" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]] \
    || fail "'$TAG' is not a release tag: vMAJOR.MINOR.PATCH, such as v1.0.0."

ROOT="$(cd "$HERE/.." && git rev-parse --show-toplevel 2>/dev/null)" \
    || fail "$HERE/.. is not a git checkout."
cd "$ROOT" || exit 1
PY="${PYTHON:-}"
if [ -z "$PY" ]; then
    if [ -x .venv/bin/python ]; then PY=.venv/bin/python; else PY=python3; fi
fi

# 1. What is being released, and from where.
BRANCH="$(git symbolic-ref --short -q HEAD)" \
    || fail "HEAD is detached. Releases are cut on main: git switch main"
[ "$BRANCH" = main ] \
    || fail "Releases are cut on main only, and this is $BRANCH. Merge it into main first (its pull request, or mvc-refactor's merge), then run this on main."
if ! git diff --quiet || ! git diff --cached --quiet; then
    fail "There are local edits (git status). Commit and push them, or discard them, first."
fi
git rev-parse -q --verify "refs/tags/$TAG" > /dev/null \
    && fail "The tag $TAG already exists."
[ -f CHANGELOG.md ] || fail "There is no CHANGELOG.md in $ROOT."

# 2. Origin: its tags and its copy of the branch (a fetch touches no file here).
say "fetching origin ..."
git fetch --quiet --tags origin || fail "Could not reach origin; nothing was changed."
git rev-parse -q --verify "refs/tags/$TAG" > /dev/null \
    && fail "The tag $TAG already exists on origin."
ORIGIN_HEAD="$(git rev-parse -q --verify "refs/remotes/origin/$BRANCH")" \
    || fail "origin has no branch $BRANCH. Push it first: git push -u origin $BRANCH"
if [ "$(git rev-parse HEAD)" != "$ORIGIN_HEAD" ]; then
    if git merge-base --is-ancestor HEAD "$ORIGIN_HEAD"; then
        fail "origin/$BRANCH has commits this checkout lacks. Pull them first (git pull --ff-only)."
    fi
    fail "HEAD is not on origin. Push $BRANCH first (git push origin $BRANCH), then cut the release."
fi
LATEST="$(git tag -l --sort=-version:refname 'v*' | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | head -n 1)"
if [ -n "$LATEST" ]; then
    NEWEST="$(printf '%s\n%s\n' "$LATEST" "$TAG" | sort -t. -k1.2,1n -k2,2n -k3,3n | tail -n 1)"
    [ "$NEWEST" = "$TAG" ] || fail "$TAG is not newer than the latest release, $LATEST."
fi

# 3. CHANGELOG.md: Unreleased moves under the tag (release.py refuses an empty one).
NOTES="$(mktemp "${TMPDIR:-/tmp}/station-release.XXXXXX")" || fail "no temporary file"
trap 'rm -f "$NOTES"' EXIT
"$PY" "$HERE/../packaging/release.py" changelog "$TAG" "$(date +%Y-%m-%d)" CHANGELOG.md \
    > "$NOTES" || fail "Nothing was released."

# 4. One commit, one annotated tag whose message is the section.
git add CHANGELOG.md || fail "git add failed; CHANGELOG.md is edited but not committed."
git commit -q -m "Release $TAG" \
    || fail "The commit failed; CHANGELOG.md is edited but not committed (git diff)."
git tag -a "$TAG" --cleanup=whitespace -F "$NOTES" \
    || fail "The tag failed. The release commit is $(git rev-parse --short HEAD); tag it by hand: git tag -a $TAG"
say "$TAG is cut on $BRANCH: commit $(git rev-parse --short HEAD), annotated tag $TAG."

# 5. Pushes: printed, or run with --push. The branch first, then the tag.
if [ "$PUSH" = 1 ]; then
    git push origin "$BRANCH" || fail "Pushing $BRANCH failed; the tag was not pushed."
    git push origin "$TAG" || fail "Pushing $TAG failed; push it by hand: git push origin $TAG"
    say "pushed. GitHub Actions now builds every bundle and publishes $TAG when all are on it."
else
    say "nothing was pushed. To publish the release, run:"
    echo "    git push origin $BRANCH"
    echo "    git push origin $TAG"
fi
