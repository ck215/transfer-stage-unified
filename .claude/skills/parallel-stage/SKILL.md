---
name: parallel-stage
description: Run several findings at once in separate git worktrees without agents overwriting each other — partition the work into exclusive write sets, brief the agents, verify what they hand back, and merge. Use when more than a handful of independent findings are open and working them one at a time is the bottleneck.
---

# Running a stage in parallel

Proven 2026-09-20: S14 and S15 ran simultaneously in two worktrees while the
lead took the S8 safety residue. Thirteen findings moved in one pass with
**zero file-level collisions**.

The technique is not coordination. It is **exclusive ownership declared up
front** — conflicts are prevented by construction, so there is nothing to
merge and nothing to reconcile.

## 1. Partition before you launch

List the candidate findings and, for each, the files it must touch. Then:

- **Pull out anything cross-cutting**, before drawing any boundaries. Real
  examples: WEB-16 shared `web_server.py` with WEB-14/21 → moved to the other
  agent; REDPERCENT-20 spanned two write sets → split into halves; WEB-19
  needed `probes.py` → deferred entirely; TEMP-9 needed an owner call →
  removed from scope.
- **Safety-path findings stay with the lead.** They are the ones where a
  wrong fix energizes a coil.
- **Shared files are lead-only**: `docs/**` (including `progress.md`) and
  `tests/architecture/test_invariants.py`. Agents never touch them.

Each agent then gets an **allow-list and a deny-list**, and the deny-list
names the owner of every forbidden file. "Do not edit X" is ignorable; "X
belongs to the lead, who is editing it right now" is not.

## 2. Worktrees

Siblings of the repo, matching the existing layout:

```
git worktree add ../s14-web -b s14-web
```

Agents **commit but never push**. One commit per finding. The lead merges.

## 3. Brief them

Use `brief-template.md` in this directory. Keep the brief to *write set +
findings*; everything invariant lives in the `worktree-fixer` agent
definition, so briefs stay short and the rules stay identical between runs.

## 4. Verify what comes back — do not trust it

In this order:

```
# a. write-set compliance and collisions
bash .claude/skills/parallel-stage/partition-check.sh <base> ../s14-web ../s15-local-ok

# b. every claimed test name actually exists
grep -rn "def <name>" tests/
```

A test *file* name is not a test name. 36/36 names checked out last round —
the check still earns its keep, because the one time it does not, a finding
is closed on a test that was never written.

## 5. Run the tests the agents could not

**This is where the last round nearly went wrong.** Agents cannot run the qt
pass — a native Qt `SIGABRT` kills pytest and discards every already-passed
result — so qt-marked tests come back under `## UNVERIFIED`. Two of the
eleven handed back last round **failed**, and both were bad tests: one closed
one of two docks and asserted as though all were closed; the other asserted
on a `matplotlib` that `tests/conftest.py` replaces with a MagicMock, so it
would have passed against code that did nothing.

Run the qt pass yourself, as a background job, before closing any row that
depends on it. See the `verify` skill.

## 6. Merge and close

`git merge --no-ff` each branch, run the fast gate and the qt pass, then close
the rows with `stage-close` and remove the worktrees:

```
git worktree remove ../s14-web
```

## Reading a handoff honestly

- **A write-set boundary is a successful outcome.** An agent reporting
  `partly` because a fix needed a file it did not own did the right thing.
  Route the remainder; do not treat it as a failure.
- **Expect stale-audit reports.** Agents will find findings already fixed.
  That is trap #1 in `CLAUDE.md`, not an excuse.
- Agents may flag ordinary harness system-reminders as prompt injection.
  They are real and come from the harness.
