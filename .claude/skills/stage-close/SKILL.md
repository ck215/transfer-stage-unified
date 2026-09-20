---
name: stage-close
description: Close a stage of the MVC refactor — update progress.md (stage row, ledger rows, session log) and write the commit, in the one order that satisfies the project's protocol. Use when a stage's code and tests are green and it needs to land, or when any commit on this branch touches docs/implementation/progress.md.
---

# Closing a stage

The protocol lives in `docs/implementation/plan.md` ("Commit and push
protocol"). This skill is the executable form. Read the plan section only if
something here disagrees with it — the plan wins.

## Order of operations

Do all four before committing. They go in **one** commit.

### 1. Stage row — `docs/implementation/progress.md`, the table under `## Stage status`

```
| S<n> | <Title> (RC-<n>) | done | | <YYYY-MM-DD> | <what landed, in one or two sentences> |
```

**Leave the Commit column empty.** A commit cannot contain its own hash, and
amending to insert it orphans the hash. Fill the SHA in the *next* commit.
This repo has already been bitten by this once (`addb0b8`, unreachable).

Before citing any SHA in a doc: `git merge-base --is-ancestor <sha> HEAD`.

### 2. Ledger rows — the table under `## Finding ledger`

A row moves to `closed` **only** with a test name or a verification note:

```
| <ID> | RC<n> | S<n> | root cause | closed (<why>; <test_name>) |
```

Verify every test name you cite actually exists:
`grep -rn "def <test_name>" tests/`

Partial fixes stay `open` with the closed half named. `open (mitigated)` and
`open (partly closed: ...)` are legitimate, honest verdicts. Never round a
partial up to `closed`.

### 3. Session log entry — appended at the end of `## Session log`, before `## Finding ledger`

Newest last. Record, at minimum:
- which plan items landed;
- **any place the previous session's resume note turned out to be wrong**,
  stated as a correction with the evidence. This file's value is that it is
  not retconned;
- design decisions taken inside the stage, so they are not re-litigated;
- what was deliberately *not* done, and which stage now owns it;
- the verification numbers (see the `verify` skill).

### 4. Commit message

```
S<n>(<RC-id or "explicit">): <imperative summary>

<what changed structurally, and why this is the root fix rather than a patch>

Stage: S<n> complete
Closes: <finding IDs>

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>
```

Use `Stage: S<n> in progress` for a mid-stage commit. One stage per commit.

## Then push, immediately

```
git push origin mvc-refactor
```

A half-done pushed stage is recoverable. A done unpushed stage is not.

## Refuse to close if

- a finding would be marked `closed` with no test and no verification note;
- an open owner decision (`D-n`) would have to be answered to proceed —
  mark the stage `BLOCKED` and stop instead;
- an `xfail` was flipped to passing by bumping its baseline rather than by
  fixing the code.
