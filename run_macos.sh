#!/bin/bash
# run_macos.sh — macOS launcher with automatic PySide6 self-heal.
# Fixes two known macOS issues:
#   1. PySide6 corruption (missing QtWidgets .so) — reinstalls if needed.
#   2. UF_HIDDEN flag on Qt plugin dylibs (macOS pip sets hidden on downloaded
#      files) — removed via chflags after every install/validation.
set -e
cd "$(dirname "$0")"

source .venv/bin/activate

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
python3 src/app.py "$@"
