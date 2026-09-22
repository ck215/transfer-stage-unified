#!/bin/bash
cd "$(dirname "$0")"
[ -f .venv/bin/activate ] && source .venv/bin/activate
# No view is forced here: app.py defaults to Tkinter on macOS (D-9) and
# PySide elsewhere. Pass --web/--pyside/--tkinter through to override.
python3 -m station.app "$@"
