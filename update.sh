#!/bin/bash
# update.sh — bring this checkout up to the latest RELEASE without GitHub Desktop.
#
#   ./update.sh            fetch, say which version this checkout is and which
#                          release is the latest, fast-forward to that release,
#                          then reinstall or reflash only if the update asks
#   ./update.sh --check    fetch and report only; the tree is not touched
#
# A release is a tag vMAJOR.MINOR.PATCH on GitHub (dev/release.sh cuts one).
# This moves the checkout to the newest release's commit, never to a branch
# head (developers: git pull), exactly as the Setup page's Update now does.
# It never stashes, never resets, never makes a merge commit: if the station
# is running, if there are local edits, or if this checkout has diverged from
# the release, it stops and says what to do. Run it between trials, with the
# station closed.
set -u
cd "$(dirname "$0")" || exit 1

CHECK=0
case "${1:-}" in
    --check) CHECK=1 ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    "") ;;
    *) echo "update.sh: unknown option '$1' (try --check)" >&2; exit 2 ;;
esac

say()  { echo "[update] $*"; }
fail() { echo "[update] $*" >&2; exit 1; }

# The version string controller/updater.py's render_version gives, from the
# same `git describe` (tests/test_updater.py holds the two together).
version_of() {
    local described
    described=$(git describe --tags --always --dirty --abbrev=7 \
        --match 'v[0-9]*.[0-9]*.[0-9]*' --exclude '*-*' 2>/dev/null) || { echo unknown; return; }
    echo "$described" | sed -E \
        -e 's/^v([0-9]+\.[0-9]+\.[0-9]+)-dirty$/\1+dirty/' \
        -e 's/^v([0-9]+\.[0-9]+\.[0-9]+)-([0-9]+)-g([0-9a-f]+)-dirty$/\1.post\2+g\3.dirty/' \
        -e 's/^v([0-9]+\.[0-9]+\.[0-9]+)-([0-9]+)-g([0-9a-f]+)$/\1.post\2+g\3/' \
        -e 's/^([0-9a-f]{4,40})-dirty$/0.0.0+\1.dirty/' \
        -e 's/^([0-9a-f]{4,40})$/0.0.0+\1/'
}

# 1. Where releases come from: the remote of the branch this checkout tracks,
#    else origin.
BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null) || fail "This folder is not a git repository."
UPSTREAM=$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null) || UPSTREAM=""
REMOTE=origin
if [ -n "$UPSTREAM" ]; then
    REMOTE=$(git config "branch.$BRANCH.remote" 2>/dev/null) || REMOTE="${UPSTREAM%%/*}"
fi
git config "remote.$REMOTE.url" > /dev/null || fail "This checkout has no remote '$REMOTE' to take releases from."

# 2. Fetch, tags included (a fetch never touches the working tree).
say "fetching $REMOTE ..."
git fetch --quiet --tags "$REMOTE" || fail "Could not reach GitHub. Check the network and try again."

VERSION=$(version_of)
TAG=$(git tag -l --sort=-version:refname 'v*' | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | head -n 1)
branch_line() {
    if [ -n "$UPSTREAM" ]; then
        say "developers: $BRANCH is $(git rev-list --count "HEAD..$UPSTREAM") commit(s) behind $UPSTREAM and $(git rev-list --count "$UPSTREAM..HEAD") ahead (git pull takes those; this script does not)."
    else
        say "developers: $BRANCH tracks no branch on GitHub (set one with: git branch -u origin/<branch>)."
    fi
}

# 3. The version line and the latest release.
if [ -z "$TAG" ]; then
    say "this checkout is at $VERSION; no release has been published yet."
    branch_line
    exit 0
fi
TARGET=$(git rev-list -n 1 "$TAG")
OLD=$(git rev-parse HEAD)
if git merge-base --is-ancestor "$TARGET" HEAD; then
    say "this checkout is at $VERSION; no newer release (the latest is $TAG)."
    branch_line
    exit 0
fi
BEHIND=$(git rev-list --count "HEAD..$TARGET")
say "this checkout is at $VERSION; the latest release is $TAG ($BEHIND commit(s) ahead)."
branch_line
git --no-pager log --oneline --no-decorate "HEAD..$TARGET" | sed 's/^/    /'
echo
git --no-pager diff --stat "HEAD..$TARGET" | tail -n 1 | sed 's/^/    /'
DIVERGED=0
git merge-base --is-ancestor HEAD "$TARGET" || DIVERGED=1

if [ "$CHECK" = 1 ]; then
    [ "$DIVERGED" = 1 ] && say "note: this checkout has diverged from the release $TAG; ./update.sh will refuse."
    say "--check: nothing changed. Run ./update.sh to apply."
    exit 0
fi

# 4. Refuse anything that could land mid-trial, over bench edits, or off the release.
if pgrep -f "src/app.py" > /dev/null 2>&1; then
    fail "The station is running. Quit it first (Ctrl+. stops, then Quit), then rerun."
fi
if ! git diff --quiet || ! git diff --cached --quiet; then
    fail "There are local edits in this checkout (git status). Commit or discard them first; they are not touched."
fi
if [ "$DIVERGED" = 1 ]; then
    fail "This checkout has diverged from the release $TAG (it has commits the release does not), so it cannot fast-forward. Tell the lead which machine this is."
fi

# 5. Fast-forward to the release's commit only: a merge commit or a conflict never happens here.
git merge --ff-only --quiet "$TARGET" || fail "Fast-forward failed; nothing was changed."
NEW=$TARGET
say "updated $BRANCH from $VERSION to $TAG (${OLD:0:7} -> ${NEW:0:7})."

# 6. Only what the update asks for.
changed() { git diff --name-only "$OLD" "$NEW" -- "$@" | grep -q .; }

if changed pyproject.toml requirements.txt; then
    say "dependencies changed: reinstalling ..."
    if [ -f .venv/bin/activate ]; then
        # shellcheck disable=SC1091
        source .venv/bin/activate
    elif [ -z "${VIRTUAL_ENV:-}" ]; then
        fail "No .venv here and none active. Activate the project's venv and run: pip install -e '.'"
    fi
    python3 -m pip install --quiet -e '.' || fail "pip install failed. Run it by hand: pip install -e '.'"
    say "dependencies reinstalled."
fi

if changed firmware; then
    say "firmware changed: the Setup page's Firmware row flashes the boards that are out of date"
    say "(dev/swap_branch.sh when you run the original app)."
    git --no-pager diff --name-only "$OLD" "$NEW" -- firmware | sed 's/^/    /'
fi

if changed src/model/transfer_map.py; then
    say "the Transfer Map changed: your trial database is kept (data/ is never touched); check its Diagnostics line after launch."
fi

say "done. Launch with ./run.sh; its Setup page checks the firmware."
