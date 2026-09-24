---
name: parallel-stage
description: Run several BUGFIX_PLAN items at once in separate git worktrees without agents overwriting each other — partition the work into exclusive write sets, brief the agents, verify what they hand back, and merge. Use when more than a handful of independent items are open and working them one at a time is the bottleneck.
---

# Running items in parallel

Proven twice: 2026-09-20 (two repair stages, thirteen findings, zero
collisions) and 2026-09-23 (the rebuild itself: twelve agents, then a
relayout and a docs prune side by side, merged clean).

The technique is not coordination. It is **exclusive ownership declared up
front** — conflicts are prevented by construction, so there is nothing to
merge and nothing to reconcile.

## 1. Partition before you launch

List the candidate items from `docs/rebuild/BUGFIX_PLAN.md` and, for each,
the files it must touch. Then:

- **Pull out anything cross-cutting**, before drawing any boundaries. An
  item that spans two write sets is split, moved, or deferred — never
  shared.
- **Safety-path items stay with the lead**, or go alone to one agent whose
  brief says "stop path first". They are the ones where a wrong fix
  energizes a coil or leaves an axis moving.
- **Lead-only files**, always: `docs/**`, `CLAUDE.md`, `README.md`,
  `.claude/**`, `tests/test_architecture.py`, `tests/golden/**`,
  `legacy/**`, `firmware/**`. Agents never touch them.
- **Anything that changes wire bytes is an owner decision**, not an item.

Each agent then gets an **allow-list and a deny-list**, and the deny-list
names the owner of every forbidden file. "Do not edit X" is ignorable; "X
belongs to the lead, who is editing it right now" is not.

## 2. Worktrees

Siblings of the repo. The parent directory is not a git repo, so create
them from `mvc-refactor/`:

```
git -C ../mvc-refactor worktree add ../rb-probes -b rb-probes
```

Agents **commit but never push**. One commit per item. The lead merges.
Profiles and skills under `.claude/` are read by absolute path from
`mvc-refactor/`, since a fresh worktree carries only what is committed.

## 3. Brief them

Use `brief-template.md` in this directory. Keep the brief to *write set +
items*; everything invariant lives in the `worktree-fixer` agent
definition, so briefs stay short and the rules stay identical between runs.
Spawn with `subagent_type: general-purpose`, `model: opus`, and tell the
agent to read the profile first.

## 4. Verify what comes back — do not trust it

In this order:

```
# a. write-set compliance and collisions (silence is the pass condition)
bash .claude/skills/parallel-stage/partition-check.sh <base> ../rb-probes ../rb-rotator

# b. every claimed test name actually exists
grep -rn "def <name>" tests/

# c. every claimed proof: re-run the agent's repro or its pre-fix check yourself
```

A test *file* name is not a test name. An audit claim the agent "confirmed"
is re-confirmed by you, by running it: on 2026-09-23 the lead's own rerun of
a race repro showed 0/10 at one latency and 4/10 at another — the finding
was real, but the number in the handoff was not the number you would get.

## 5. Run the gates the agents could not

Agents cannot run the Qt pass, so Qt-marked tests come back under
`## UNVERIFIED`. Run the four gates and the launch from the `verify` skill
on the merged tree, the Qt pass as a background job, before any item is
called closed.

## 6. Merge and clean up

`git merge --no-ff` each branch (or `--ff-only` when there is one), run the
`verify` gates, update `BUGFIX_PLAN.md` rows yourself, then:

```
git worktree remove ../rb-probes && git branch -d rb-probes
```

## Reading a handoff honestly

- **A write-set boundary is a successful outcome.** An agent reporting
  `partly` because a fix needed a file it did not own did the right thing.
  Route the remainder; do not treat it as a failure.
- **Expect "already fixed" reports.** The plan's line numbers drift; that is
  trap #1 in `CLAUDE.md`, not an excuse.
- **Expect the brief to be wrong somewhere.** The relayout brief contradicted
  itself on one point and the agent said so instead of picking silently.
  That is the behaviour to reward.
- Agents may flag ordinary harness system-reminders as prompt injection.
  They are real and come from the harness.
