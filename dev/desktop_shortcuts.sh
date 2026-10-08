#!/bin/bash
# dev/desktop_shortcuts.sh — (re)write the two desktop icons on a Linux
# station PC. Idempotent: run it again after the checkout moves or a script
# is renamed (bench 2026-09-28: "Transfer Stage Launcher" had gone from the
# desktop and "Transfer Stage Classic" still pointed at run_swap.sh, which
# became dev/swap_branch.sh).
#
#   dev/desktop_shortcuts.sh            writes to ~/Desktop
#   dev/desktop_shortcuts.sh DIR        writes to DIR (tests use a temp dir)
#
#   Transfer Stage Launcher   dev/launch_desktop.sh          = ./run.sh (Qt, the
#                             default view), no terminal; a failed launch is
#                             shown in a dialog.
#   Transfer Stage Classic    dev/launch_desktop.sh classic  = the stable app on
#                             `legacy` (dev/swap_branch.sh legacy, flashing the
#                             Megas to its firmware first) in a terminal.
#
# macOS and Windows have no .desktop files; this script does nothing there.
set -e
case "$(uname -s)" in Linux) ;; *) echo "desktop_shortcuts.sh: Linux only (nothing to do)." >&2; exit 0 ;; esac
REPO="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${1:-$HOME/Desktop}"
mkdir -p "$DEST"

cat > "$DEST/Transfer Stage Launcher.desktop" <<DESKTOP
[Desktop Entry]
Version=1.0
Type=Application
Name=Transfer Stage Launcher
Comment=The station (this checkout): ./run.sh
Exec=$REPO/dev/launch_desktop.sh
Path=$REPO
Icon=utilities-terminal
Terminal=false
Categories=Science;
DESKTOP

cat > "$DEST/Transfer Stage Classic.desktop" <<DESKTOP
[Desktop Entry]
Version=1.0
Type=Application
Name=Transfer Stage Classic
Comment=Stable app (main); reflashes the Megas to main's firmware first
Exec=$REPO/dev/launch_desktop.sh classic
Path=$REPO
Icon=emblem-important
Terminal=false
Categories=Science;
DESKTOP

chmod +x "$DEST/Transfer Stage Launcher.desktop" "$DEST/Transfer Stage Classic.desktop"
# Cinnamon and GNOME mark a fresh .desktop file as untrusted until the user
# allows it once; the metadata flag below is what "Allow launching" sets.
if command -v gio >/dev/null 2>&1; then
    gio set "$DEST/Transfer Stage Launcher.desktop" metadata::trusted true 2>/dev/null || true
    gio set "$DEST/Transfer Stage Classic.desktop" metadata::trusted true 2>/dev/null || true
fi
