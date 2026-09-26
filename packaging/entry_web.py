"""`station-web` in the PyInstaller bundle: the Web view, nothing else.
Flags pass through: `station-web --no-browser --port 8099`."""
import sys

from app import main_web

if __name__ == "__main__":
    sys.exit(main_web())
