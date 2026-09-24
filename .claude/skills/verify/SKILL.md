---
name: verify
description: Run the right test gates for the station at the right time — the fast suite while working, all four gates plus a launch before anything merges. Use after any change under src/ or tests/, before merging an agent's worktree, and when deciding whether the Qt pass is warranted.
---

# Verifying

Run from the repo root. `$PY` is the venv's python (`../main/.venv/bin/python`
on this Mac); `$S` is the session scratch directory.

## The working loop

```
$PY -m pytest tests -q -p no:cacheprovider -m "not qt"
```

~50 s. Run after every edit. Baseline **1527 passed, 85 deselected** (the
85 are the Qt tests). A count that moved is a finding, not noise.

## Before a merge: four gates and a launch

```
$PY -m pytest tests -q -p no:cacheprovider -m "not qt"                          # 1527 passed
$PY -m pytest tests/test_wire_golden.py -q -p no:cacheprovider                  # 78 passed; recaptures from legacy/src in a subprocess
QT_QPA_PLATFORM=offscreen $PY -m pytest tests -q -p no:cacheprovider -m qt      # 85 passed; lead only, see below
cd legacy && $PY -m pytest tests -q -p no:cacheprovider -m "not slow and not order_dependent and not qt"
                                                                                # 1038 passed, 1 skipped, 89 deselected, 1 xfailed
$PY src/app.py --web --no-browser --port 8081 &  sleep 8;  curl -s -o /dev/null -w "%{http_code}\n" localhost:8081/api/setup;  kill %1
```

The launch is not optional: a suite that passes on an app that cannot start
has proved nothing. The legacy gate exists only to prove the old tree is
still a valid golden reference; nothing in it is edited on purpose.

On macOS, before the Qt pass: `chflags -R nohidden "$(python -c 'import PySide6,os;print(os.path.dirname(PySide6.__file__))')"`.

## Never read a result through a pipe

```
pytest ... | tail -4          # WRONG: $? is tail's, not pytest's
```

Redirect, capture the code, then grep:

```
$PY -m pytest tests -q -p no:cacheprovider -m "not qt" > "$S/fast.txt" 2>&1
echo "EXIT=$?"; tail -1 "$S/fast.txt"
```

## The Qt pass is the lead's

A native Qt `SIGABRT` kills the pytest session and discards every
already-passed result, which is why Qt tests are a separate pass and why
agents never run it. A Qt-marked test an agent wrote but did not run is
`## UNVERIFIED` in its handoff, never evidence.

## What is real and what is a stand-in

The suite under `tests/` runs against the real `serial`, `pygame`, `mss`,
`PIL` and `matplotlib`; the only stand-in is `tkinter` (a `MagicMock` in
`tests/conftest.py`, so the Tk view's tests exercise logic, not widgets).
The legacy suite mocks all of them. **Iterating a MagicMock yields
nothing**, so a loop-based assertion over one passes vacuously; check what
you are really asserting on before believing a green test.

## Reading a result

- Never mark a test skipped or xfail to get green, never delete a test to
  silence it, never bump a baseline. If a count dropped, find which tests
  and say why.
- The golden gate compares **bytes**, not baud rate, timing or lock
  discipline. Green there says the frames match; it says nothing about
  whether the port was opened at the right speed or whether two threads can
  read each other's replies. The 2026-09-23 audit found both.
