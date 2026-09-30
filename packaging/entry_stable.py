"""`station-stable` in the bundle: the lab's original Tk app (the `stable`
branch's src/mainGUI.py), frozen UNMODIFIED. Everything freezing needs lives
here, never in the stable sources:

- `multiprocessing.freeze_support()` first: mainGUI starts each device
  window as a `spawn` child, and a frozen child is this same executable
  re-run with multiprocessing's arguments; without it every child would
  start another Setup window.
- mainGUI runs as `__main__` (runpy), exactly as `python mainGUI.py` does,
  so its own `if __name__ == "__main__"` block (set_start_method('spawn'),
  SetupWindow) is what starts the app, and a spawn child re-imports it by
  name as `__mp_main__`, as from source.
- `--self-check`: import every stable module and exit 0 without building a
  window (the smoke's headless proof that the freeze resolved them all).

Nothing here changes the working directory: the stable app opens files only
through its own file dialogs.
"""
import multiprocessing
import sys

#: What mainGUI imports, directly and through its device frames.
STABLE_MODULES = ("mainGUI", "stepper_frame", "DC_frame", "chuck_frame",
                  "temp_control", "rotator", "serialDrive", "controllerDrive",
                  "color_test_new", "lib.redpercent", "lib.smc100")


def self_check():
    import importlib
    for name in STABLE_MODULES:
        importlib.import_module(name)
    print("station-stable: self-check ok: " + " ".join(STABLE_MODULES), flush=True)
    return 0


def main(argv=None):
    multiprocessing.freeze_support()
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--self-check"]:
        return self_check()
    import runpy
    runpy.run_module("mainGUI", run_name="__main__", alter_sys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
