# Docs prune
Inventory: 56 files read (32 .md/.py under docs/ by headings or in full, 4 JSON/rules data files by name only, 18 screenshots by name, plus CLAUDE.md, README.md). Worktree rb-docs, base 8228991. The relayout had not landed in ../rb-relayout when I read it (still the old layout), so the target layout comes from the brief, not the code.

## Corrected (file — what was stale — what it says now — evidence in code)
- CLAUDE.md — rebuild paragraph over the whole old repair-branch text; skills table of old-process skills — rewritten: the tree, read-first order STATUS → BRIEF → WEB_DESIGN_BRIEF → BUGFIX_PLAN, the brief's five commands, `station-map` as the only current skill, standing rules (safety first; tests first / never bump a baseline; never answer D-n; "work lands by worktree, not by stage" replaces one-stage-per-commit, taken from STATUS "Process notes"; never push/amend; progress.md frozen; no scratch in root), the two traps, `## History` (10 lines) that lists verify/stage-close/reconcile-ledger/fix-a-finding/parallel-stage as old-process skills pending rewrite. Evidence: station/app.py has --web/--qt/--tk/--no-browser/--port; Controller.add/remove/reopen/estop_all in station/controller.py.
- README.md — two lines only. (1) Launch: added how to start it from a terminal (`./run.sh`, `./run_macos.sh`, `run.bat`, or `python3 src/app.py` with `--web|--qt|--tk`). (2) Temperature "Click **Quit**" → close the module's tab; reopening starts it fresh. Evidence: no "Quit" button anywhere in station/ (only Tk/Qt app-quit hooks); ruling "close = destruct, reopen = construct".
- docs/rebuild/STATUS.md — the new/old app rows, commands, test paths, item 3, item 4. New-app row now `src/` with the layout. Old-app row is now `legacy/src/` + `legacy/tests/`. Added a Tests row (`tests/`, `tests/golden/`). Commands: `python3 src/app.py`, `pytest tests`, `tests/test_wire_golden.py`, and the legacy suite. Item 3 now says "before `legacy/` is deleted". Item 4 says the cutover is partly done: done = the move plus the docs prune; not done = deleting `legacy/` (waits until all 135 TEST_PORTING files have ported equivalents), then merge to main and push. design.rules is noted as using pre-move paths. **Golden count corrected 71 → 78**: `pytest tests/station/test_wire_golden.py --collect-only` gives 78; BUGFIX_PLAN already said 78.
- docs/rebuild/BUGFIX_PLAN.md — mechanical path rewrite: `station/models/` → `src/model/`, `station/setup.py` → `src/controller/setup.py`, `station/` → `src/`, `tests/station/` → `tests/`, cutover "delete `src/`" → "delete `legacy/`". Added a note that line numbers are as of 8228991. If the relayout changes imports, some cited line numbers may shift by a line or two. Re-check A1–A9 and B1–B7 line numbers after the merge.
- docs/rebuild/WEB_DESIGN_BRIEF.md — `station/palette.py` → `src/palette.py`, `tests/station/test_view_web_*.py` → `tests/test_view_web_*.py`.
- docs/rebuild/BRIEF.md — **banner, body kept** (my call). The body's import rules name module paths (`station.controller`, `station.models/**`). How those names change depends on how rb-relayout does imports with `src/app.py` run as a script, which I can't see yet, so the edit is not mechanical. The banner maps old paths to new ones and names `tests/test_architecture.py` as the authority.

## Archived (file — why) — all via git mv to docs/archive/<same path>; index at docs/archive/README.md
- architecture/README.md — indexes the old-tree pages; frames old src/ as the live app.
- architecture/bootstrap-and-entrypoint.md — SetupWindow/build_models/run_*_app; none exist in the rebuild.
- architecture/controllers.md — ControllerPoller + old serial wrapper; replaced by devices/.
- architecture/error-routing.md — old error_routing bus; replaced by events.py.
- architecture/known-issues.md — pre-audit log, already superseded by audit/ on the old branch.
- architecture/libs-and-web.md — old smc100/toupcam/web adapter; says Web is deprioritised (now it is the candidate primary frontend).
- architecture/models.md — ManagedModel/BaseProbe/*System/SystemManager; replaced by model/.
- architecture/ownership-and-lifecycle.md — SystemManager lifecycle, hide/show (purged).
- architecture/views.md — old PySide/Tk class hierarchies.
- implementation/plan.md — S0–S16 stage plan; stages ended. The open S16 bench work is now BUGFIX_PLAN Tier B.
- implementation/testing.md — policy for the old suite only. Comments in legacy/tests/conftest.py, legacy/tests/pytest.ini and legacy/tests/architecture/test_invariants.py still cite its old path. I left them alone (code/tests are out of my scope); the archive index says where it went.

## Kept as input, banner added (file)
- docs/implementation/progress.md (the banner also says plan.md/testing.md moved to archive; gen_ledger.py's table parsing is unaffected)
- docs/implementation/bench-checklist.md — **not on the role's input list; my judgement.** It is the only written bench procedure behind BUGFIX_PLAN Tier B (B1/B3/B4 map to its sections C/B/D). Archive it if you disagree.
- docs/architecture/root-causes.md, docs/architecture/safety-pattern.md
- docs/architecture/audit/*.md (12 files)

## Unchanged (file)
- docs/implementation/gen_ledger.py (stays, because progress.md stays)
- docs/rebuild/design.json, design.rules, carry.json, narrative.json (machine-read; design.rules paths are pre-move, and STATUS now says so)
- docs/rebuild/shots/*.png (18)
- tests/station/TEST_PORTING.md (not mine; untouched)

## Conflicts (STATUS/BRIEF vs code; left alone)
- STATUS "Agent worktrees: … Worktrees now: `main`, `mvc-refactor`". rb-docs and rb-relayout exist right now. They are temporary, so I left the sentence alone. Update it after you merge and remove them.
- STATUS item 3 says "26 safety tests" and "135 old test files"; carry.json says 229 findings while the ledger says 213. I did not reconcile either, only reproduced them.
- Not a conflict, but outside my brief's correction classes: the README's UI labels come from the original app ("Enable System", "Start Stepping", "Full Speed", "Manual Mode Max Speed", "Launch Controllers", "Refresh Devices", the terminal "select a controller" prompt, temperature "Enter"). The rebuild's labels differ ("Step", "Launch", "Refresh", "Enter Settings", toggles "Autonomous:"/"Manual / Gamepad:"). Figures 4–6 show old windows. None of these is a relayout change or a purged feature, so I left them. They need an operator-manual pass after the bench.
- Assumptions to check against rb-relayout: `tests/station/TEST_PORTING.md` → `tests/TEST_PORTING.md`; `tests/station/test_core_model.py` → `tests/test_core_model.py` (the safety-pattern banner cites it); the launchers still pass `--web|--qt|--tk` through (CLAUDE.md and README say so).

## Proposed deletions (for the lead; nothing deleted)
- docs/archive/architecture/known-issues.md — superseded twice (by audit/, then by the rebuild). Nothing reads it.
- Once `legacy/` is deleted: all of docs/archive/, plus docs/implementation/gen_ledger.py, which only regenerates a frozen ledger.

Note: the role file says `Co-Authored-By: Claude Opus 5`. The commit uses the session's attribution line, `Claude Opus 5.5 (1M context)`.

COMMIT: 89d2fcc
