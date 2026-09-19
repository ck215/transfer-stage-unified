# Architecture Documentation

Living reference for the MVC layers in this repo, built up during an ongoing
bugfix/parity investigation (started 2026-09-18). The goal is that someone
picking this up cold can find where a given behavior lives, who owns which
object's lifecycle, and what's already known to be broken — without
re-deriving it from scratch by re-reading every file.

**Status as of last edit:** actively being extended. Treat any claim here
about "current behavior" as a snapshot — verify against the live code before
acting on it for anything non-trivial (see the root CLAUDE.md's guidance on
memory/docs going stale). File:line references are exact as of the commit
noted at the bottom of each doc; re-check them if the file has since moved.

## Files in this directory

- **[models.md](models.md)** — every model class: purpose, key state,
  public method inventory, `ui_schema` contract, who constructs/destroys it.
- **[views.md](views.md)** — PySide6 vs. Tkinter view layers: which are
  schema-driven vs. hand-built, and the parity audit between them (web view
  is currently deprioritized/legacy-fallback, see below).
- **[controllers.md](controllers.md)** — `ControllerPoller` (gamepad) and
  `serial` (controller/serial.py) wrappers: lifecycle, threading, the
  pygame/SDL global-state gotcha.
- **[error-routing.md](error-routing.md)** — the `ErrorRouter` /
  `ErrorPopupManager` framework, how each frontend wires it, and a running
  audit of state transitions that don't yet report anything to the user.
- **[ownership-and-lifecycle.md](ownership-and-lifecycle.md)** — who
  constructs and destroys what, and where the "reconstruct on dock reopen"
  pattern breaks the `ManagedModel` teardown contract.
- **[known-issues.md](known-issues.md)** — the living issues log: fixed and
  still-open findings, safety-tagged, one line of evidence per entry.

## Current framing (as of 2026-09-18)

- **Web view (`src/views/web/`) is deprioritized/legacy for now.** The app's
  default view was switched to Tkinter (`run.sh`, `src/app.py`, commit
  `ada4dbf`) specifically to give room to fix the PySide6 view without
  breaking the frontend people are actually using day-to-day. Parity work
  targets **PySide6 vs. Tkinter only** until stated otherwise.
- **Goal: complete parity between PySide6 and Tkinter** — same buttons, same
  layout intent, same model methods called per field. Where they currently
  diverge structurally (see [views.md](views.md)), that's tracked as an open
  item, not fixed reactively one bug report at a time.
- **Architectural integrity contract:** `src/model/base.py`'s
  `ManagedModel` protocol (`teardown()` + `emergency_stop()`) is meant to be
  the *one* sanctioned way to fully shut a model down. Where a call site
  reimplements its own partial teardown instead of calling `model.teardown()`,
  that's flagged in [ownership-and-lifecycle.md](ownership-and-lifecycle.md)
  as integrity drift, not treated as a one-off bug.
