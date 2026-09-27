#!/bin/bash
# run_swap.sh — run either branch's app on the same boards, flashing only
# what the chosen tree's firmware says is out of date.
#
#   ./run_swap.sh [new|mvc-refactor|main|classic] [--no-flash] [--force-flash] [view flags...]
#
#   new / mvc-refactor (default)  this checkout:   python3 src/app.py [view flags]
#                                  (Tkinter unless --web / --qt is passed)
#   main / classic                 ../transfer-stage-unified-main (a git worktree
#                                  of `main`): python3 src/mainGUI.py (Tk only)
#
# Before launching, firmware/flash_firmware.py (this checkout's copy) flashes
# the chosen tree's firmware/ onto every board whose recorded sketch hash
# (~/transfer-stage-runs/flashed.json) differs; when all are current it opens
# no port. Both branches ship the same Mega sketches, so in steady state a
# swap flashes nothing. If a flash fails the app is not launched.
#
#   --no-flash       skip the flash step
#   --force-flash    flash every connected board even if already current
#   RUN_SWAP_DRY_RUN=1          print the flash and launch commands, run nothing
#   STATION_FLASH_ONLY="Stepper Probe,Chuck Positioner"
#                    limit the flash step to these boards (e.g. to stop a board
#                    that is never plugged in here from triggering a port scan)
#   STATION_MAIN_TREE=PATH      where the main worktree lives
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
MAIN_TREE="${STATION_MAIN_TREE:-$(dirname "$HERE")/transfer-stage-unified-main}"
DRY="${RUN_SWAP_DRY_RUN:-0}"

BRANCH="new"
case "${1:-}" in
    new|mvc-refactor) BRANCH="new"; shift ;;
    main|classic)     BRANCH="main"; shift ;;
esac

FLASH=1
FORCE=""
APP_ARGS=()
for arg in "$@"; do
    case "$arg" in
        --no-flash)    FLASH=0 ;;
        --force-flash) FORCE="--force" ;;
        *)             APP_ARGS+=("$arg") ;;
    esac
done

if [ "$BRANCH" = "main" ]; then
    TREE="$MAIN_TREE"
    if [ ! -f "$TREE/src/mainGUI.py" ]; then
        echo "[run_swap] The main worktree is missing: $TREE" >&2
        echo "[run_swap] Create it once with:" >&2
        echo "    git -C \"$HERE\" worktree add \"$TREE\" main" >&2
        exit 2
    fi
    APP=(python3 src/mainGUI.py)
    if [ ${#APP_ARGS[@]} -gt 0 ]; then
        echo "[run_swap] main's app is Tkinter only; ignoring: ${APP_ARGS[*]}" >&2
    fi
else
    TREE="$HERE"
    APP=(python3 src/app.py ${APP_ARGS[@]+"${APP_ARGS[@]}"})
fi

# One venv serves both trees (the main worktree has none of its own).
if [ -f "$HERE/.venv/bin/activate" ]; then
    # shellcheck disable=SC1091
    source "$HERE/.venv/bin/activate"
elif [ -z "${VIRTUAL_ENV:-}" ]; then
    echo "[run_swap] No .venv in $HERE and none active: create or activate the project's venv first." >&2
    exit 1
fi

FLASH_CMD=(python3 "$HERE/firmware/flash_firmware.py" --sketch-root "$TREE/firmware" --yes)
[ -n "$FORCE" ] && FLASH_CMD+=("$FORCE")
if [ -n "${STATION_FLASH_ONLY:-}" ]; then
    IFS=',' read -r -a ONLY <<< "$STATION_FLASH_ONLY"
    FLASH_CMD+=(--only "${ONLY[@]}")
fi

show() { printf '%q ' "$@"; echo; }

echo "[run_swap] branch: $BRANCH  tree: $TREE"
if [ "$FLASH" = 1 ]; then
    if [ "$DRY" = 1 ]; then
        echo "[run_swap] would flash:"; printf '    '; show "${FLASH_CMD[@]}"
    else
        echo "[run_swap] checking firmware (flashes only boards that are out of date)..."
        if ! "${FLASH_CMD[@]}"; then
            echo "[run_swap] Flashing failed, so the app was NOT launched." >&2
            echo "[run_swap] The boards may be half-flashed or running the other tree's firmware." >&2
            echo "[run_swap] Fix the error above and rerun, or pass --no-flash to launch anyway." >&2
            exit 1
        fi
    fi
else
    echo "[run_swap] --no-flash: firmware left as it is"
fi

if [ "$DRY" = 1 ]; then
    echo "[run_swap] would launch (in $TREE):"; printf '    '; show "${APP[@]}"
    exit 0
fi
cd "$TREE" || exit 1
exec "${APP[@]}"
