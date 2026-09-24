# Brief template

Everything invariant — the write-set contract, test-first, prove-the-defect,
commit rules, handoff format — lives in `.claude/agents/worktree-fixer.md`.
A brief carries only what changes between runs. Substitute `<...>`.

---

```
# <batch name> — <one-line scope>. Worktree brief.

Worktree: <../rb-probes>   Branch: <rb-probes>   Base: <sha>
Python: <abs path to ../main/.venv/bin/python>   Handoff: <handoff/fix-probes.md>

## THE WRITE SET — a hard contract

You may create or modify ONLY these paths:

  <src/model/probe.py>
  <src/devices/gamepad.py>
  <tests/test_probe.py  tests/test_gamepad.py>

Everything else is owned by someone working RIGHT NOW in another worktree:

  <src/devices/serial_port.py>   -> the lead
  <src/devices/smc100.py>        -> agent B (rb-rotator)
  <src/controller/setup.py>      -> agent C (rb-setup)
  docs/**, CLAUDE.md, README.md, .claude/**, tests/test_architecture.py, tests/golden/**, legacy/** -> the lead

## The items you own

### <D1> — <title>
Plan: `docs/rebuild/BUGFIX_PLAN.md`, Tier D row <D1>. Audit: `handoff/audit-<area>.md`, <ID>.
<One or two lines on what the audit claims. Note that it may be stale.>

## Explicitly NOT yours

<D6> — needs <src/controller/setup.py>, which is outside this write set. Do
not attempt it; note it and move on.
```

---

## Notes for the lead

- Name the *owner* of each denied path, not just the path.
- Give the base SHA explicitly; the agent needs it to prove defects.
- Keep "not yours" items in the brief. An agent that does not know why a
  neighbouring item is excluded will try to be helpful and take it.
- Do not ask for a Qt pass. Agents cannot run one safely; you run it on merge.
