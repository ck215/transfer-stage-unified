#!/bin/bash
# run_macos.sh — macOS launcher with automatic PySide6 self-heal.
# Only reinstalls PySide6 when the import fails; skips the ~60s reinstall
# on healthy boots.
set -e
cd "$(dirname "$0")"

source .venv/bin/activate

# Quick sanity-check: can we import QtWidgets?
if ! python3 -c "from PySide6.QtWidgets import QApplication" 2>/dev/null; then
    echo "[macOS Fix] PySide6 appears corrupt or incomplete — rebuilding..."
    pip uninstall -y PySide6 PySide6-Essentials PySide6-Addons 2>/dev/null || true
    pip install --force-reinstall PySide6
    echo "[macOS Fix] PySide6 reinstalled successfully."
else
    echo "[macOS Fix] PySide6 OK — skipping reinstall."
fi

echo "[Launcher] Starting PySide6 Dashboard..."
python3 src/app.py "$@"
