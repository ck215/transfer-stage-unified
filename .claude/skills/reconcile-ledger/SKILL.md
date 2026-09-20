---
name: reconcile-ledger
description: Check the finding ledger in progress.md against the code and close the rows that stages already fixed but never flipped. Fans the check out across parallel read-only subagents. Use when open-finding counts look inflated, when a stage row says "done" but its findings still say "open", or before planning which work actually remains.
---

# Reconciling the ledger against the code

The ledger's Status column drifts in one direction only: stages fix things
and the rows are not flipped, because flipping needs a test name and that is
slower than moving on. The result is a backlog that overstates itself. Audit
it before planning off it.

Count the drift first:

```
awk -F'|' 'NF>=6 && $2 ~ /-[0-9]+ *$/ {
  st=$6; gsub(/^ +| +$/,"",st); sg=$4; gsub(/ /,"",sg);
  if (st=="open") print sg }' docs/implementation/progress.md | sort | uniq -c | sort -rn
```

Open rows whose stage is already `done` in the Stage status table are the
suspects.

## Fan it out

This work is read-only on `src/` and splits perfectly by audit file
(`docs/architecture/audit/*.md`). Launch one **haiku** subagent per one or
two audit files, ~10 findings each.

**Agents never touch `progress.md`.** Each writes a verdict file to the
scratch directory; you apply every verdict yourself in one scripted pass.
That makes write conflicts impossible by construction rather than by
coordination.

Give each agent: the repo path, its exact finding-ID list with the audit
file each lives in, the read-only rule, the trap below, the evidence rule,
and its output path. **Copy the prompt from
[agent-prompt.md](agent-prompt.md)** rather than writing one — it carries
the failure modes this pass has already hit, including a second wave that
exists solely because wave-1 agents cite test *files* instead of test
*names*.

### The trap every agent must be warned about

This codebase documents its own repairs. Long docstrings and comments quote
the **old broken code** to explain why it was replaced. A grep hit is not
evidence the defect survives — check whether the matching line is live code
or prose. `_active_poller_count` and `subprocess` both still match in
`src/`, and both matches are comments about their own removal.

### The evidence rule

- `CLOSED` requires a src file:line for the fix **and** a test name verified
  to exist with `grep -rn "def <name>" tests/`. Never invent a test name; if
  there is none, say `no test found`.
- `OPEN` requires the current file:line where the defect still lives.
- `UNSURE` is a good answer. It is much cheaper than a wrong `CLOSED`.

### Verdict file format

```
## <FINDING-ID>
VERDICT: CLOSED | OPEN | UNSURE
EVIDENCE: <src path:line> ; test: <test_name in tests/path.py>
NOTE: <one sentence on what was actually found>
```

## Applying the verdicts

Verify every cited test name yourself before flipping anything:

```
grep -rn "def <name>" tests/
```

A false OPEN costs one verification. A false CLOSED erases a real defect
from the backlog permanently — so the burden of proof is asymmetric, and
the agents' CLOSED verdicts are a shortlist for you to check, not a
result to apply.

Apply `CLOSED` rows with a script, matching the exact existing row text so a
miss raises rather than silently no-ops. Leave every `UNSURE` alone and
check those yourself — they are where the real remaining work hides.

Then close the pass with the `stage-close` skill: this is a `progress.md`
change and needs a session-log entry like any other.
