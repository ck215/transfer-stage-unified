#!/bin/bash
cd "$(dirname "$0")"
source .venv/bin/activate

echo "[macOS Fix] Purging corrupted PySide6 installation..."
pip uninstall -y PySide6 PySide6-Essentials PySide6-Addons

echo "[macOS Fix] Reinstalling PySide6 from requirements..."
pip install PySide6

echo "[Launcher] Starting PySide6 Dashboard..."
python3 src/app.py "$@"
