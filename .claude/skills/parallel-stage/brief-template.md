# Brief template

Everything invariant — the write-set contract, test-first, prove-the-defect,
commit rules, handoff format — lives in `.claude/agents/worktree-fixer.md`.
A brief carries only what changes between runs. Substitute `<...>`.

---

```
# <STAGE> — <one-line scope>. Worktree brief.

Worktree: <../s14-web>   Branch: <s14-web>   Base: <sha>

## THE WRITE SET — a hard contract

You may create or modify ONLY these paths:

  <src/views/web/**>
  <src/model/system_manager.py>
  <tests/web/**>

Everything else is owned by someone working RIGHT NOW in another worktree:

  <src/model/probes.py>          -> the lead
  <src/model/rotator_system.py>  -> the lead
  <src/controller/gamepad.py>    -> agent B (s15-local-ok)
  <src/views/pyside/**>          -> agent B (s15-local-ok)
  docs/**                        -> the lead (includes progress.md)
  tests/architecture/test_invariants.py -> the lead

If a fix needs a file outside your write set, STOP that finding, leave it,
and report it `partly` with the blocking file named. That is a successful
outcome, not a failure. Do not edit it "just a little".

## The findings you own

### <WEB-14>
Audit: `docs/architecture/audit/<view-web.md>`, entry `### <WEB-14>`
<One or two lines on what the audit claims. Note that it may be stale.>

### <WEB-21>
...

## Explicitly NOT yours

<WEB-19> — needs <the D-8 client-liveness watchdog in probes.py>, which is
outside this write set. Do not attempt it; note it and move on.

## What to hand back

Write `<SCRATCH>/handoff-<stage>.md`. Nothing else outside the write set.
```

---

## Notes for the lead

- Name the *owner* of each denied path, not just the path.
- Give the base SHA explicitly; the agent needs it to prove defects.
- Keep "not yours" findings in the brief. An agent that does not know why a
  neighbouring finding is excluded will try to be helpful and take it.
- Do not ask for a qt pass. Agents cannot run one safely; you run it on merge.
