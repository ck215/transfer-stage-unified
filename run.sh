#!/bin/bash
# To run main or this branch on the same boards (flashing only if needed): run_swap.sh.
cd "$(dirname "$0")"
[ -f .venv/bin/activate ] && source .venv/bin/activate
# No view is forced here: app.py defaults to Qt on every platform (owner
# ruling 2026-09-28). Pass --web/--qt/--tk through to override.
python3 src/app.py "$@"
