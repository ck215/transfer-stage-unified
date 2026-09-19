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

## Quick Facts
- **Active Bug Backlog**: 11 active issues (4 high priority, 7 lower priority/unverified) and 2 pending design decisions currently logged in `known-issues.md`.
- **Default Frontend**: `run.sh` explicitly forces the Tkinter UI (via `--tkinter`), making it the stable daily-driver view. However, running `src/app.py` directly without arguments still defaults to `web` (on macOS) or `pyside` (elsewhere).
- **Key File Sizes**:
  - `src/views/pyside/view.py`: ~967 lines
  - `src/app.py`: ~920 lines
  - `src/views/tkinter/view.py`: ~835 lines
  - `src/controller/gamepad.py`: ~639 lines

## Suggested Reading Order
For a brand-new investigator looking to understand the system architecture, read the docs in this order:
1. **[bootstrap-and-entrypoint.md](bootstrap-and-entrypoint.md)**: Understand how the application starts, validates hardware, and wires components together.
2. **[models.md](models.md)**: Learn about the domain models, their expected UI schemas, and the standard teardown protocol.
3. **[views.md](views.md)**: See how the models' `ui_schema` is converted into actual widgets in the two active frontends (PySide6 and Tkinter).
4. **[controllers.md](controllers.md)**: Understand the I/O layer that translates physical input (gamepads, serial) into model commands.
5. **[error-routing.md](error-routing.md)**: Learn how errors from the hardware/model layers bubble up to the views.
6. **[ownership-and-lifecycle.md](ownership-and-lifecycle.md)**: Deep dive into the complex and sometimes violated object lifecycle constraints.
7. **[known-issues.md](known-issues.md)**: Review the active backlog before attempting to fix anything.
8. **[libs-and-web.md](libs-and-web.md)**: (Optional) Review legacy systems and vendor driver integrations.

## Navigational Index

- **[models.md](models.md)**
  Documents all domain model classes located in `src/model/`. It outlines each model's purpose, key state attributes, public method inventory, and `ui_schema` contract used by the dynamic views. It also details the `ManagedModel` protocol (`teardown()`, `emergency_stop()`) that every model is expected to fulfill for graceful shutdown.

- **[views.md](views.md)**
  Compares the two active view layers: PySide6 and Tkinter. It details how both renderers interpret the generic `ui_schema` to build widgets dynamically, reducing per-field custom code. Crucially, it catalogs structural divergences between the two, particularly in their implementation of the Red Percent tab, serving as the basis for ongoing parity work.

- **[controllers.md](controllers.md)**
  Describes the I/O adapter layers located in `src/controller/`, specifically the `gamepad.py` (pygame/SDL) and `serial.py` wrappers. It outlines their threading models, lifecycle cardinality (e.g., one poller per probe), and addresses the complexities of managing process-wide global state for the pygame C library.

- **[error-routing.md](error-routing.md)**
  Explains the global `ErrorRouter` and `ErrorPopupManager` framework that centralizes error reporting. It maps out how different frontends handle these routed errors (e.g., immediate popups vs. background buffering) and provides an audit of state transitions that currently fail to report alerts to the user.

- **[ownership-and-lifecycle.md](ownership-and-lifecycle.md)**
  Catalogs the object lifecycle rules, defining who is responsible for constructing and destroying hardware models and their I/O handlers. It highlights systemic integrity drift where components are manually torn down instead of honoring the `ManagedModel.teardown()` contract, particularly during dock reopen events.

- **[known-issues.md](known-issues.md)**
  The living log of all bugs found and fixed during the current investigation. It catalogs resolved issues along with their root causes, tracks active high-priority and lower-priority bugs (often safety-related or UX regressions), and outlines pending design decisions that require consensus rather than reactive patching.

- **[bootstrap-and-entrypoint.md](bootstrap-and-entrypoint.md)**
  Traces the complete startup sequence from execution (`run.sh` / `src/app.py`) to the primary dashboard. It breaks down the configuration discovery, serial port probing, validation logic in `app_bootstrap.py`, and how domain models are instantiated and wired into the `SystemManager` before handing control to the GUI.

- **[libs-and-web.md](libs-and-web.md)**
  Covers the legacy web frontend and third-party vendor drivers. It documents the SMC100 serial hardware interface and identifies completely unused dependencies (like `toupcam.py`). Additionally, it serves as an architectural reference for the deprioritized schema-driven REST API used by the web view.

## Current framing (as of 2026-09-18)

- **Web view (`src/views/web/`) is deprioritized/legacy for now.** Parity work
  targets **PySide6 vs. Tkinter only** until stated otherwise.
- **Tkinter as the stable fallback:** The app's stable daily-driver view is Tkinter (`run.sh` explicitly launches it via `--tkinter`) to give room to fix the PySide6 view without breaking the frontend people are actually using day-to-day. 
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

---

### Addendum: 2026-09-18 Cross-Doc Inconsistencies & Changes
* **Default View Contradiction**: `README.md` and other docs stated that the default view was switched to Tkinter at commit `ada4dbf`. While `run.sh` correctly passes `--tkinter`, `src/app.py`'s internal argparse fallback logic still explicitly routes to `web` (on macOS) or `pyside` (elsewhere) when run directly without arguments. A human should reconcile whether to update `app.py`'s fallback logic to align with the documentation.
* **Deprioritized Web View Contradiction**: The Web view is heavily marked as deprioritized and legacy across all docs, but `app.py` still falls back to the Web view as the default behavior on macOS. 
* **Fragmented Backlogs**: Documents like `bootstrap-and-entrypoint.md` and `libs-and-web.md` contain their own "Findings for known-issues.md" sections detailing dead code (e.g. `parse_controller_id`, `toupcam.py`), missing exception handling, and bypassed startup logic. However, these findings haven't been migrated into the actual `known-issues.md` tracker yet.
