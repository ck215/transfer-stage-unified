# transfer-stage-unified — MVC refactor

A lab-instrument control app: stepper and DC probes, a chuck positioner, a
temperature controller, an SMC100 rotator, and a screen-capture Red Percent
monitor. **Three frontends** — Tkinter, PySide6, and a Web client (HTTP +
vanilla JS) — over one set of models.

This branch (`mvc-refactor`) is a staged repair of 13 root causes across 213
audited findings. It is not feature work.

## Read these first, in this order

1. **`docs/implementation/progress.md`** — the single source of truth.
   Stage status, owner decisions, session log, and the 213-finding ledger.
   Its header has the full cold-resume procedure.
2. **`docs/implementation/plan.md`** — the 17 stages (S0–S16), the standing
   rules, and the commit protocol.
3. **`docs/architecture/root-causes.md`** — RC-1..RC-13, each with the
   invariants that prove it fixed and an **anti-fix table** naming the
   patches that must *not* be applied.
4. **`docs/architecture/audit/*.md`** — the 213 findings, by subsystem.

## Skills

Invoke these rather than re-deriving the procedure from the docs:

| Skill | When |
|---|---|
| `verify` | after any change to `src/` or `tests/`; before closing a stage |
| `stage-close` | a stage is green and needs to land, or any commit touches `progress.md` |
| `reconcile-ledger` | open-finding counts look inflated, or before planning off them |

## Standing rules

- **One stage per commit**, `progress.md` updated in the *same* commit, and
  pushed immediately to `origin mvc-refactor`.
- **Never amend a commit to insert its own SHA.** Leave the stage row's
  Commit cell blank and fill it in the next commit. This branch already has
  one orphaned citation (`addb0b8`) from getting this wrong.
- **A ledger row moves to `closed` only with a verified test name or a
  verification note.** `open (mitigated)` and `open (partly closed: ...)`
  are honest answers; rounding a partial up to `closed` is not.
- **Never answer an owner decision (`D-n`).** If a stage is blocked on one,
  mark it `BLOCKED` and stop. **D-7** (firmware v2) is the only one still
  open, and it is S16, at the bench, owner-only.
- **Safety paths before feature paths** within a stage. If it can energize a
  coil or move an axis, the stop path is implemented and tested first.
- **Tests before implementation**, and never bump a structural invariant's
  baseline to make it green.
- Scratch files, patches and logs never land in the repo root — use the
  session scratch directory.

## Two traps this codebase sets

1. **Comments quote the old broken code.** `src/` documents its own repairs
   at length, so a grep hit for a defect is not evidence the defect
   survives. `_active_poller_count` and `subprocess` both still match, and
   both matches are prose about their own removal. Read the matching line.
2. **A stage row saying `done` does not mean its findings are closed.** The
   ledger drifts in one direction: fixes land, rows are not flipped. A
   2026-09-20 reconciliation found 28 of 66 such rows were already fixed —
   and, going the other way, that S8's `I-5.2` holds only for probes; the
   rotator's `emergency_stop` still blocks on the SMC100 serial lock.

## Running things

```
python3 -m pytest tests/ -m "not slow and not order_dependent and not qt"
```

is the working loop (~60 s). See the `verify` skill for the rest. The
three-pass full sweep is **retired** (owner instruction, 2026-09-20).

Launchers: `run.sh` / `run_macos.sh` / `run.bat`.
