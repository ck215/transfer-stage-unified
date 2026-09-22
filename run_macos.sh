#!/bin/bash
# run_macos.sh — macOS launcher with automatic PySide6 self-heal.
# Fixes two known macOS issues:
#   1. PySide6 corruption (missing QtWidgets .so) — reinstalls if needed.
#   2. UF_HIDDEN flag on Qt plugin dylibs (macOS pip sets hidden on downloaded
#      files) — removed via chflags after every install/validation.
set -e
cd "$(dirname "$0")"

source .venv/bin/activate

# Parse arguments to determine requested view mode or help
VIEW_MODE=""
SHOW_HELP=false

for arg in "$@"; do
    case "$arg" in
        -h|--help)
            SHOW_HELP=true
            ;;
        --web)
            VIEW_MODE="web"
            ;;
        --pyside)
            VIEW_MODE="pyside"
            ;;
        --legacy)
            VIEW_MODE="legacy"
            ;;
    esac
done

# Check for --view <choice> or --view=<choice>
prev_arg=""
for arg in "$@"; do
    if [ "$prev_arg" = "--view" ]; then
        VIEW_MODE="$arg"
    elif [[ "$arg" =~ ^--view=(.+)$ ]]; then
        VIEW_MODE="${BASH_REMATCH[1]}"
    fi
    prev_arg="$arg"
done

# If help was requested, show python help directly without PySide check
if [ "$SHOW_HELP" = true ]; then
    python3 -m station.app "$@"
    exit 0
fi

# On macOS, default view is web unless --pyside was explicitly specified
if [ -z "$VIEW_MODE" ]; then
    VIEW_MODE="web"
fi

# Only perform PySide6 self-heal and plugin inspection if launching PySide
if [ "$VIEW_MODE" = "pyside" ]; then
    PYSIDE6_DIR=$(python3 -c "import PySide6, os; print(os.path.dirname(PySide6.__file__))" 2>/dev/null || echo "")

    # Quick sanity-check: can we import QtWidgets?
    if ! python3 -c "from PySide6.QtWidgets import QApplication" 2>/dev/null; then
        echo "[macOS Fix] PySide6 appears corrupt or incomplete — rebuilding..."
        pip uninstall -y PySide6 PySide6-Essentials PySide6-Addons 2>/dev/null || true
        pip install --force-reinstall PySide6
        PYSIDE6_DIR=$(python3 -c "import PySide6, os; print(os.path.dirname(PySide6.__file__))")
        echo "[macOS Fix] PySide6 reinstalled successfully."
    else
        echo "[macOS Fix] PySide6 OK — skipping reinstall."
    fi

    # Always remove the UF_HIDDEN flag from Qt plugins.
    # macOS marks pip-downloaded .dylib files as hidden, which causes Qt's
    # QDir plugin scanner to skip them and report "plugin not found".
    if [ -n "$PYSIDE6_DIR" ] && [ -d "$PYSIDE6_DIR" ]; then
        echo "[macOS Fix] Clearing hidden flags on Qt plugins..."
        chflags -R nohidden "$PYSIDE6_DIR/" 2>/dev/null || true
    fi

    echo "[Launcher] Starting PySide6 Dashboard..."
else
    echo "[Launcher] Launching in mode '$VIEW_MODE' (skipping PySide6 repair checks)..."
fi

python3 -m station.app "$@"
