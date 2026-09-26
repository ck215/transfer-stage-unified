"""`station-tk` in the PyInstaller bundle: the Tkinter view, nothing else.

`app` is a top-level module because `src/` is on the spec's `pathex`, the
same shape as `python3 src/app.py`.
"""
import sys

from app import main_tk

if __name__ == "__main__":
    sys.exit(main_tk())
