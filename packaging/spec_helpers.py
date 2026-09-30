"""What both PyInstaller specs need (station.spec, stable.spec): both
bundles import pygame, so both hit the same macOS trap. Stdlib only."""
import os
import shutil
import subprocess
import sys


def sdl3_for_sdl2_compat(binaries, workpath):
    """pygame built against sdl2-compat (Homebrew's `sdl2`, the only SDL2 a
    Python 3.14 pygame builds against on macOS today) ships a libSDL2 shim
    that dlopen()s SDL3 BY NAME at load time. PyInstaller cannot see a
    dlopen, so SDL3 is left behind, and the shim's load-time initializer
    then shows a MODAL "Failed loading SDL3 library" alert and waits
    forever: the launcher hangs at `import pygame` with no output.

    The shim looks for @loader_path/libSDL3.dylib first, so SDL3 goes next
    to it under that name. A pygame wheel with a real SDL2 needs none of this
    and this returns [].
    """
    sdl2 = next((src for dest, src, _ in binaries
                 if os.path.basename(dest).startswith("libSDL2-2.0")), None)
    if sdl2 is None:
        return []
    with open(sdl2, "rb") as f:
        if b"Failed loading SDL3 library" not in f.read():
            return []                               # a real SDL2
    real = os.path.realpath(sdl2)
    candidates = [os.path.join(os.path.dirname(sdl2), "libSDL3.dylib"),
                  os.path.join(os.path.dirname(real), "libSDL3.dylib"),
                  "/opt/homebrew/lib/libSDL3.dylib", "/usr/local/lib/libSDL3.dylib",
                  os.path.join(sys.prefix, "lib", "libSDL3.dylib")]
    found = next((c for c in candidates if os.path.isfile(c)), None)
    if found is None:
        raise SystemExit(f"{sdl2} is sdl2-compat and no libSDL3.dylib was found "
                         f"in {candidates}: the bundle would hang at import pygame")
    staged = os.path.join(workpath, "libSDL3.dylib")
    if not os.path.exists(staged):          # once per build, not per Analysis
        shutil.copyfile(os.path.realpath(found), staged)   # Homebrew's is 0444
        os.chmod(staged, 0o755)
    return [("libSDL3.dylib", staged, "BINARY")]


def clear_hidden_flags(bundle):
    """macOS: pip marks some downloaded dylibs UF_HIDDEN (run.sh clears them
    in the venv) and the copy into dist/ keeps the flag; Qt's plugin scanner
    skips hidden files and then aborts with no platform plugin. Flags are not
    part of a code signature, so this leaves the ad-hoc signatures valid."""
    if sys.platform == "darwin":
        subprocess.run(["chflags", "-R", "nohidden", bundle], check=True)
