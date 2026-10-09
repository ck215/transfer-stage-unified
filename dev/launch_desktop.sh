#!/bin/bash
# dev/launch_desktop.sh — what the two desktop icons run (Linux; written by
# dev/desktop_shortcuts.sh). A .desktop Exec line may hold no shell, so the
# shell lives here.
#
#   dev/launch_desktop.sh              "Transfer Stage Launcher": flashes every
#                                      out-of-date board (as Classic does; not
#                                      while a station runs; skip with
#                                      STATION_NO_AUTO_FLASH=1), then ./run.sh, no
#                                      terminal. run.sh prints nothing when all
#                                      is well; if it fails, its last lines are
#                                      shown in a dialog and kept in
#                                      ~/transfer-stage-runs/launcher.log.
#                                      With a station already running, the
#                                      app opens that station's page and
#                                      exits 0: no second server, no dialog.
#   dev/launch_desktop.sh classic      "Transfer Stage Classic": the lab's
#                                      original app (the `legacy` branch)
#                                      through dev/swap_branch.sh legacy, in a
#                                      terminal; it flashes the Megas to the
#                                      legacy sketches and records them, so
#                                      this icon's flash puts them back.
REPO="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO" || exit 1

if [ "${1:-}" = "classic" ]; then
    inner="'$REPO/dev/swap_branch.sh' legacy || read -p 'Launch failed (see above). Press Enter to close.'"
    if command -v gnome-terminal >/dev/null 2>&1; then
        exec gnome-terminal -- bash -c "$inner"
    fi
    exec x-terminal-emulator -e bash -c "$inner"
fi

LOG_DIR="$HOME/transfer-stage-runs"
LOG="$LOG_DIR/launcher.log"
mkdir -p "$LOG_DIR"

show_log() {   # $1: dialog title
    if command -v zenity >/dev/null 2>&1; then
        tail -n 20 "$LOG" | zenity --text-info --title="$1" \
            --width=700 --height=300 2>/dev/null
    elif command -v xmessage >/dev/null 2>&1; then
        xmessage -center -file "$LOG"
    fi
}

# Firmware first, as the Classic icon does (owner 2026-10-08): switching
# between Classic and this icon leaves the boards on the other app's
# sketches, so every board whose recorded hash differs from this tree's is
# flashed before the station starts. Not when a station already runs: its
# ports are held (exclusive), and run.sh then just opens its page.
running=0
INSTANCE="${TRANSFER_STAGE_DATA_ROOT:-$LOG_DIR}/station-instance.json"
if [ -f "$INSTANCE" ]; then
    pid="$(sed -n 's/.*"pid": *\([0-9][0-9]*\).*/\1/p' "$INSTANCE")"
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && running=1
fi
PY=""
if [ -x "$REPO/.venv/bin/python3" ]; then PY="$REPO/.venv/bin/python3"
elif [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python3" ]; then PY="$VIRTUAL_ENV/bin/python3"
fi   # none: run.sh below says so (log + dialog)
if [ "$running" = 0 ] && [ -n "$PY" ] && [ "${STATION_NO_AUTO_FLASH:-}" != 1 ]; then
    echo "== $(date '+%F %T') firmware check before launch" >>"$LOG"
    FLASH_PY='import sys
from controller import flashing as f
r = f.flash(None, sketch_root=f.default_sketch_root(), stamp=f.default_stamp(),
            tools=f.tools_for(), on_line=lambda line: print(line, flush=True))
sys.exit(r["returncode"])'
    if command -v zenity >/dev/null 2>&1; then
        env "PYTHONPATH=$REPO/src" "$PY" -c "$FLASH_PY" 2>&1 | tee -a "$LOG" \
            | sed -u 's/^/# /' | zenity --progress --pulsate --auto-close --no-cancel \
                --title="Transfer Stage" --text="Checking the firmware..." --width=500 2>/dev/null
        flashed=${PIPESTATUS[0]}
    else
        env "PYTHONPATH=$REPO/src" "$PY" -c "$FLASH_PY" >>"$LOG" 2>&1
        flashed=$?
    fi
    if [ "$flashed" -ne 0 ]; then
        echo "flashing failed, so the station was not launched" >>"$LOG"
        show_log "Transfer Stage: firmware update failed"
        exit "$flashed"
    fi
fi

./run.sh "$@" 2>>"$LOG"
status=$?
[ "$status" -eq 0 ] && exit 0
show_log "Transfer Stage did not start"
exit "$status"
