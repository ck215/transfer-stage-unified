#!/bin/bash
# run.sh — the station's one launcher on macOS and Linux (run.bat on Windows).
#
#   ./run.sh [app flags...]      ./run.sh --help lists them
#
# The Web dashboard is the station's only view (the Tk and Qt views were
# retired 2026-10-07; --tk / --qt print that and exit 2).
#
# Finds the project's Python (a .venv in this folder, else the virtualenv
# already active in the shell: a git worktree shares the main checkout's),
# then runs src/app.py with every argument. It prints nothing when all is
# well: the firmware check, the update check and the Web address are rows on
# the Setup page, not terminal lines. Not finding Python is the only message.
cd "$(dirname "$0")" || exit 1

if [ -f .venv/bin/activate ]; then
    # shellcheck disable=SC1091
    source .venv/bin/activate
elif [ -z "${VIRTUAL_ENV:-}" ]; then
    echo "run.sh: no .venv in $(pwd) and no virtualenv active: create one (python3 -m venv .venv; .venv/bin/pip install -e '.') or activate the project's, then run ./run.sh again." >&2
    exit 1
fi
PY=python3
[ -x "$VIRTUAL_ENV/bin/python3" ] && PY="$VIRTUAL_ENV/bin/python3"
if ! command -v "$PY" >/dev/null 2>&1; then
    echo "run.sh: python3 not found in $VIRTUAL_ENV: recreate the venv (python3 -m venv .venv)." >&2
    exit 1
fi

exec "$PY" src/app.py "$@"
