---
name: verify
description: Run the right test gates for the station at the right time — the fast suite while working, the golden gate plus a launch before anything merges. Use after any change under src/ or tests/, before merging an agent's worktree, and when deciding whether the optional Qt pass is warranted.
---

# Verifying

Run from the repo root (`mvc-refactor/` or your `rb-*` worktree). `$PY` is the
one venv's python, `/Users/ianalbinogonzalez/GitHub/transfer-stage-unified/mvc-refactor/.venv/bin/python`
(made from pyproject's `[dev]` extra); `$S` is the session scratch directory.

## The working loop

```
$PY -m pytest tests -q -p no:cacheprovider -m "not qt"
```

Minutes, not seconds; run after every edit. Baseline (2026-10-07, after the
proposal round, the lab merge and the root cleanup): **4194** (the lead
fills it after the final gate; the 200-odd deselected are the Qt tests of the
frozen view); golden **77** captures (the re-capture test and the three live
frame comparisons against the deleted legacy tree are retired). There is no
legacy gate any more. A count that moved is a finding, not noise.

Known flakes, not regressions (rerun the test alone before calling a red fast gate):

- `test_view_web_tutorial.py`: the first-trial walk is an **xfail** until the
  tutorial is re-anchored to the sample/chip/flake pickers.
- `test_o15` (the pinned head): can fail under load.
- `test_sample_map.py`: a "rated later" test is timing-flaky.
- `test_transfer_map.py::test_mark_appears_in_the_index_and_the_label_from_the_mark_on`
  (the transfer-map mark timing, a 1 ms bound): fails about one run in three.

## Before a merge: the gates and a launch

```
$PY -m pytest tests -q -p no:cacheprovider -m "not qt"                          # the fast gate, see above (STATION_NO_WINDOWS=1 while anyone is at the Mac)
$PY -m pytest tests/test_wire_golden.py -q -p no:cacheprovider                  # 77 passed; replays the stored golden JSON against src/
$PY src/app.py --no-browser --port 8081 &  sleep 8;  curl -s -o /dev/null -w "%{http_code}\n" localhost:8081/api/setup;  kill %1
```

The launch is not optional: a suite that passes on an app that cannot start
has proved nothing.

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

## The Qt pass is optional, and the lead's

The Qt tests exercise only the frozen Qt view (retired 2026-10-07, frozen at
`413f504`). Running them needs `pip install -e .[qt]` and
`QT_QPA_PLATFORM=offscreen`; on macOS first `chflags -R nohidden` on the
PySide6 directory. A native Qt `SIGABRT` kills the pytest session and discards
every already-passed result, which is why Qt tests are a separate pass and why
agents never run it. A Qt-marked test an agent wrote but did not run is
`## UNVERIFIED` in its handoff, never evidence. A merge no longer waits on it.

## What is real and what is a stand-in

The suite under `tests/` runs against the real `serial`, `pygame`, `mss`,
`PIL` and `matplotlib`; the only stand-in is `tkinter` (a `MagicMock` in
`tests/conftest.py`, so the frozen Tk view's tests exercise logic, not widgets).
(The old suite mocked all of them.) **Iterating a MagicMock yields
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
