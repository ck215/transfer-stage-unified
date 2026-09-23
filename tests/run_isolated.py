#!/usr/bin/env python3
"""Run every tests/test_*.py in its own pytest process and summarise.

Why: nine of these test files replace `serial`, `pygame` or `numpy` in
sys.modules with MagicMocks at import time. In one shared pytest process the
first file collected decides what those names mean for every file after it,
and six tests that pass on their own fail on the pollution. Until those
preambles are rewritten as fixtures, per-file processes are the honest run.

Usage:
    python tests/run_isolated.py            # all files
    python tests/run_isolated.py serial     # only files whose name contains "serial"
Extra pytest args go after "--":
    python tests/run_isolated.py -- -x
"""
import subprocess
import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
PER_FILE_TIMEOUT_S = 300  # a Tk modal that nobody clicks must fail, not stall


def main(argv):
    extra = []
    if "--" in argv:
        i = argv.index("--")
        argv, extra = argv[:i], argv[i + 1:]
    files = sorted(TESTS_DIR.glob("test_*.py"))
    if argv:
        files = [f for f in files if any(k in f.name for k in argv)]
    if not files:
        print("no test files matched")
        return 2

    results = {}
    for f in files:
        cmd = [sys.executable, "-m", "pytest", str(f), "-q", "-p", "no:cacheprovider", *extra]
        print(f"\n=== {f.name}", flush=True)
        try:
            proc = subprocess.run(cmd, timeout=PER_FILE_TIMEOUT_S)
            results[f.name] = "ok" if proc.returncode == 0 else f"FAILED (exit {proc.returncode})"
        except subprocess.TimeoutExpired:
            results[f.name] = f"TIMEOUT (> {PER_FILE_TIMEOUT_S}s -- a blocking dialog or input())"

    print("\nSummary:")
    width = max(len(n) for n in results)
    for name, status in results.items():
        print(f"  {name:<{width}}  {status}")
    return 0 if all(s == "ok" for s in results.values()) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
