---
name: verify
description: Run the right test pass for the MVC refactor at the right time — fast gate during work, the three gates at a stage boundary. Use before closing a stage, after any change to src/ or tests/, or when deciding whether a fuller run is warranted.
---

# Verifying

Full policy: `docs/implementation/testing.md`. This is the operational part.

Run from the repo root with `python3 -m pytest`.

## The working loop

```
python3 -m pytest tests/ -m "not slow and not order_dependent and not qt"
```

~60 s. This is what you run after every edit. Nothing else, until the stage
is done.

Narrower, when iterating on one concern (markers are assigned per file in
`tests/conftest.py::_FILE_MARKERS`):

```
python3 -m pytest tests/ -m "<concern> and not slow and not order_dependent"
```

## The stage boundary

Three passes, in this order. They are disjoint; together they cover the
suite.

```
python3 -m pytest tests/ -m "not slow and not order_dependent and not qt"   # fast gate
python3 -m pytest tests/ -m "slow and not qt"                                # ~140 s
python3 -m pytest tests/ -m "qt"                                             # ~6 s
```

Record the three counts in the session-log entry.

**The three-pass full sweep is retired** (owner instruction, 2026-09-20). It
re-ran the same tests the three gates already cover and cost more wall-clock
than it bought. Do not reintroduce it. If you suspect genuine flakiness,
re-run *the one suspect pass* three times, not the whole suite.

## Why the passes are separate

Qt tests are split out because a native Qt `SIGABRT` kills the pytest
session and discards every already-passed result — an abort in the Qt pass
must not be able to take the rest of the suite with it. They are auto-marked
by fixture (`qapp`/`qtbot`), not by filename.

`order_dependent` is two web wall-clock tests. They are excluded from the
gates by design; run them alone if you touched web timing.

## Reading a result

- **XPASS is a failure**, deliberately. `xfail(strict=True)` markers in
  `tests/architecture/test_invariants.py` carry the stage that fixes them;
  an XPASS means that stage landed and the marker should now be deleted.
- **Never bump a structural invariant's baseline to make it green.** If the
  hit count dropped but is not zero, find out which remaining hits belong to
  which stage and write that down. A bumped baseline hides the difference
  between "fixed" and "partly fixed" permanently.
- The invariant harness has a vacuity guard: a grep test that matches
  nothing anywhere is a broken test, not a passing one.
