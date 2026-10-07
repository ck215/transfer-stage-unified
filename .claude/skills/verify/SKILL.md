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

Minutes, not seconds; run after every edit. Baseline: the lead's last full
run was **3796 passed, 10 skipped, 227 deselected, 1 xfailed at `79ae0bd`**
(2026-10-07); 474 web-view tests were added after it. A collect-only count at
`7f509cb` (`--collect-only -q -m "not qt"`) reads **3831/4058 collected,
227 deselected** (the 227 are the Qt tests of the frozen view). Re-measure
with that command and use the number you get; the two figures above were not
reconciled by the docs pass. A count
that moved is a finding, not noise. Known flake, not a regression:
`test_transfer_map.py::test_mark_appears_in_the_index_and_the_label_from_the_mark_on`
fails about one run in three on an unchanged tree (a 1 ms timing bound);
rerun it alone before calling a red fast gate.

## Before a merge: the gates and a launch

```
$PY -m pytest tests -q -p no:cacheprovider -m "not qt"                          # the fast gate, see above (STATION_NO_WINDOWS=1 while anyone is at the Mac)
$PY -m pytest tests/test_wire_golden.py -q -p no:cacheprovider                  # 78 passed; recaptures from legacy/src in a subprocess
cd legacy && $PY -m pytest tests -q -p no:cacheprovider -m "not slow and not order_dependent and not qt"
                                                                                # 1038 passed, 1 skipped, 89 deselected, 1 xfailed
$PY src/app.py --no-browser --port 8081 &  sleep 8;  curl -s -o /dev/null -w "%{http_code}\n" localhost:8081/api/setup;  kill %1
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
