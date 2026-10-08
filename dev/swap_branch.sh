#!/bin/bash
# dev/swap_branch.sh — a developer tool, not the station's launcher (that is
# ./run.sh, whose Setup page checks and flashes the firmware). It runs the
# station or the lab's ORIGINAL Tk app (the `legacy` branch) on the same
# boards, flashing first, because the original app has no Setup page that
# flashes.
#
#   dev/swap_branch.sh legacy|station [--no-flash] [-- app args...]
#
#   legacy    the original app, from a SIBLING checkout at ../legacy-app: made
#             with `git worktree add ../legacy-app legacy` when this checkout
#             has the branch, else `git clone --branch legacy <origin>`; its
#             venv ../legacy-app/.venv is made and filled on first use. The
#             boards are flashed by THAT tree's own firmware/flash_firmware.py
#             (its sketches, its wire format), then `python src/mainGUI.py`
#             runs there with the app args.
#   station   this checkout: the boards are flashed from this tree's firmware
#             (in-process controller.flashing: only boards whose sketch hash
#             differs) and `src/app.py --web` runs from its venv. ./run.sh does
#             the same without the flash; this is the symmetric command.
#
# Never switches branches in this checkout, never touches the legacy tree
# beyond creating it. If a flash fails the app is not launched. Silent on
# success; every failure is one sentence on stderr and a non-zero exit.
#
#   --no-flash                 skip the flash step
#   RUN_SWAP_DRY_RUN=1         print every command, run none (no worktree, clone,
#                              venv or flash)
#   STATION_FLASH_ONLY="Stepper Probe,Chuck Positioner"
#                              limit the flash step to these boards
set -u
HERE="$(cd "$(dirname "$0")/.." && pwd)"
PARENT="$(dirname "$HERE")"
LEGACY="$PARENT/legacy-app"
DRY="${RUN_SWAP_DRY_RUN:-0}"

# The legacy app's third-party imports (read off its src/; it ships no
# requirements file), pinned as the station and packaging/requirements-stable.txt
# pin them. tests/test_launchers.py checks these against pyproject.toml.
LEGACY_PINS=(pyserial==3.5 pygame==2.6.1 mss==10.2.0 Pillow==12.3.0 numpy==2.5.2 gcodeparser==0.3.0)

die() { echo "swap_branch: $*" >&2; exit 1; }
show() { local l; l="$(printf '%q ' "$@")"; echo "+ ${l% }"; }
# step "<what failed>" cmd...: print it (dry run) or run it silently.
step() {
    local what="$1"; shift
    if [ "$DRY" = 1 ]; then show "$@"; return 0; fi
    local out
    out="$("$@" 2>&1)" || { printf '%s\n' "$out" | tail -n 15 >&2; die "$what"; }
}

TARGET="${1:-}"
case "$TARGET" in
    legacy|station) shift ;;
    *) die "usage: dev/swap_branch.sh legacy|station [--no-flash] [-- app args...]" ;;
esac
FLASH=1
APP_ARGS=()
while [ $# -gt 0 ]; do
    case "$1" in
        --no-flash) FLASH=0 ;;
        --) shift; APP_ARGS=("$@"); break ;;
        *) die "unknown argument '$1' (usage: dev/swap_branch.sh legacy|station [--no-flash] [-- app args...])" ;;
    esac
    shift
done

if [ "$(basename "$HERE")" = legacy-app ] || [ -f "$HERE/src/mainGUI.py" ]; then
    die "this is the legacy app's own tree; run dev/swap_branch.sh from the station checkout."
fi

ONLY=()
if [ -n "${STATION_FLASH_ONLY:-}" ]; then
    IFS=',' read -r -a RAW <<< "$STATION_FLASH_ONLY"
    for b in "${RAW[@]}"; do
        b="$(echo "$b" | sed 's/^ *//; s/ *$//')"
        [ -n "$b" ] && ONLY+=("$b")
    done
fi

if [ "$TARGET" = legacy ]; then
    command -v git >/dev/null 2>&1 || die "git is not installed, so the legacy checkout cannot be made."
    command -v python3 >/dev/null 2>&1 || die "python3 is not installed, so the legacy venv cannot be made."
    G=(git --no-optional-locks)

    if [ -e "$LEGACY" ]; then
        [ -f "$LEGACY/src/mainGUI.py" ] \
            || die "$LEGACY exists but is not a checkout of the legacy branch; move it aside and rerun."
    elif "${G[@]}" -C "$HERE" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
            && "${G[@]}" -C "$HERE" show-ref --verify --quiet refs/heads/legacy; then
        step "git could not add the legacy worktree at $LEGACY (is legacy checked out elsewhere?)." \
            "${G[@]}" -C "$HERE" worktree add "$LEGACY" legacy
    elif ORIGIN="$("${G[@]}" -C "$HERE" remote get-url origin 2>/dev/null)" && [ -n "$ORIGIN" ]; then
        step "git could not clone the legacy branch from $ORIGIN." \
            "${G[@]}" clone --quiet --branch legacy "$ORIGIN" "$LEGACY"
    else
        die "this checkout has no legacy branch and no origin to clone it from."
    fi

    LPY="$LEGACY/.venv/bin/python"
    if [ ! -x "$LPY" ]; then
        step "python3 could not create $LEGACY/.venv." python3 -m venv "$LEGACY/.venv"
        if [ -f "$LEGACY/requirements.txt" ]; then
            step "pip could not install the legacy app's requirements." \
                "$LPY" -m pip install --quiet -r "$LEGACY/requirements.txt"
        else
            step "pip could not install the legacy app's requirements." \
                "$LPY" -m pip install --quiet "${LEGACY_PINS[@]}"
        fi
    fi

    if [ "$FLASH" = 1 ]; then
        FLASH_CMD=("$LPY" "$LEGACY/firmware/flash_firmware.py" --yes)
        [ ${#ONLY[@]} -gt 0 ] && FLASH_CMD+=(--only "${ONLY[@]}")
        step "flashing failed, so the legacy app was not launched (fix the error, or pass --no-flash)." \
            "${FLASH_CMD[@]}"
    fi

    if [ "$DRY" = 1 ]; then
        echo "+ cd $(printf '%q' "$LEGACY")"
        show "$LPY" src/mainGUI.py ${APP_ARGS[@]+"${APP_ARGS[@]}"}
        exit 0
    fi
    cd "$LEGACY" || die "cannot enter $LEGACY."
    exec "$LPY" src/mainGUI.py ${APP_ARGS[@]+"${APP_ARGS[@]}"}
fi

# station
if [ -x "$HERE/.venv/bin/python3" ]; then PY="$HERE/.venv/bin/python3"
elif [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python3" ]; then PY="$VIRTUAL_ENV/bin/python3"
else die "no .venv in $HERE and no virtualenv active; run ./run.sh once or create the venv first."
fi
if [ "$FLASH" = 1 ]; then
    FLASH_PY='import os, sys
from controller import flashing as f
only = [b.strip() for b in os.environ.get("STATION_FLASH_ONLY", "").split(",") if b.strip()]
r = f.flash(only or None, sketch_root=f.default_sketch_root(), stamp=f.default_stamp(),
            tools=f.tools_for(), on_line=print)
sys.exit(r["returncode"])'
    step "flashing failed, so the station was not launched (fix the error, or pass --no-flash)." \
        env "PYTHONPATH=$HERE/src" "$PY" -c "$FLASH_PY"
fi
if [ "$DRY" = 1 ]; then
    echo "+ cd $(printf '%q' "$HERE")"
    show "$PY" src/app.py --web ${APP_ARGS[@]+"${APP_ARGS[@]}"}
    exit 0
fi
cd "$HERE" || die "cannot enter $HERE."
exec "$PY" src/app.py --web ${APP_ARGS[@]+"${APP_ARGS[@]}"}
