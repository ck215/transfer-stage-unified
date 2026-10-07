#!/bin/bash
# update.sh — bring this checkout up to date with GitHub without GitHub Desktop.
#
#   ./update.sh            fetch, show what is coming, fast-forward, then
#                          reinstall or reflash only if the update asks for it
#   ./update.sh --check    fetch and report only; the tree is not touched
#
# It follows the branch this checkout tracks (`git branch -u` changes it).
# It never merges, never stashes, never overwrites: if the station is running,
# if there are local edits, or if this tree has diverged from GitHub, it stops
# and says what to do. Run it between trials, with the station closed.
set -u
cd "$(dirname "$0")" || exit 1

CHECK=0
case "${1:-}" in
    --check) CHECK=1 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    "") ;;
    *) echo "update.sh: unknown option '$1' (try --check)" >&2; exit 2 ;;
esac

say()  { echo "[update] $*"; }
fail() { echo "[update] $*" >&2; exit 1; }

# 1. The branch this checkout follows.
UPSTREAM=$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null) \
    || fail "This checkout tracks no remote branch. Set one: git branch -u origin/<branch>"
BRANCH=$(git rev-parse --abbrev-ref HEAD)

# 2. Fetch (the working tree is untouched by a fetch).
say "fetching $UPSTREAM ..."
git fetch --quiet "${UPSTREAM%%/*}" || fail "Could not reach GitHub. Check the network and try again."

OLD=$(git rev-parse HEAD)
NEW=$(git rev-parse "$UPSTREAM")
BEHIND=$(git rev-list --count "HEAD..$UPSTREAM")
AHEAD=$(git rev-list --count "$UPSTREAM..HEAD")

if [ "$BEHIND" = 0 ]; then
    say "up to date: $BRANCH at ${OLD:0:7}"
    [ "$AHEAD" != 0 ] && say "note: this tree has $AHEAD local commit(s) GitHub does not."
    exit 0
fi

say "$BEHIND new commit(s) on $UPSTREAM:"
git --no-pager log --oneline --no-decorate "HEAD..$UPSTREAM" | sed 's/^/    /'
echo
git --no-pager diff --stat "HEAD..$UPSTREAM" | tail -n 1 | sed 's/^/    /'

if [ "$CHECK" = 1 ]; then
    say "--check: nothing changed. Run ./update.sh to apply."
    exit 0
fi

# 3. Refuse anything that could land mid-trial or over bench edits.
if pgrep -f "src/app.py" >/dev/null 2>&1; then
    fail "The station is running. Quit it first (Ctrl+. stops, then Quit), then rerun."
fi
if ! git diff --quiet || ! git diff --cached --quiet; then
    fail "There are local edits in this checkout (git status). Commit or discard them first; they are not touched."
fi
if [ "$AHEAD" != 0 ]; then
    fail "This tree has $AHEAD local commit(s) GitHub does not, so it cannot fast-forward. Tell the lead which machine this is."
fi

# 4. Fast-forward only: a merge commit or a conflict never happens here.
git merge --ff-only --quiet "$UPSTREAM" || fail "Fast-forward failed; nothing was changed."
say "updated $BRANCH: ${OLD:0:7} -> ${NEW:0:7}"

# 5. Only what the update asks for.
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
    say "(dev/swap_branch.sh when you run main's app)."
    git --no-pager diff --name-only "$OLD" "$NEW" -- firmware | sed 's/^/    /'
fi

if changed src/model/transfer_map.py; then
    say "the Transfer Map changed: your trial database is kept (data/ is never touched); check its Diagnostics line after launch."
fi

say "done. Launch with ./run.sh; its Setup page checks the firmware."
