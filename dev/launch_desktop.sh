#!/bin/bash
# dev/launch_desktop.sh — what the two desktop icons run (Linux; written by
# dev/desktop_shortcuts.sh). A .desktop Exec line may hold no shell, so the
# shell lives here.
#
#   dev/launch_desktop.sh              "Transfer Stage Launcher": ./run.sh, no
#                                      terminal. run.sh prints nothing when all
#                                      is well; if it fails, its last lines are
#                                      shown in a dialog and kept in
#                                      ~/transfer-stage-runs/launcher.log.
#   dev/launch_desktop.sh classic      "Transfer Stage Classic": the stable app
#                                      on `main` through dev/swap_branch.sh, in
#                                      a terminal (its flash step talks).
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
./run.sh "$@" 2>>"$LOG"
status=$?
[ "$status" -eq 0 ] && exit 0
if command -v zenity >/dev/null 2>&1; then
    tail -n 20 "$LOG" | zenity --text-info --title="Transfer Stage did not start" \
        --width=700 --height=300 2>/dev/null
elif command -v xmessage >/dev/null 2>&1; then
    xmessage -center -file "$LOG"
fi
exit "$status"
