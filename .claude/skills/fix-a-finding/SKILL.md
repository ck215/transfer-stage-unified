---
name: fix-a-finding
description: Work a single ledger finding end to end — check the audit text against the current tree before writing anything, test first, prove the defect was real against the pre-fix code, then close the row. Use when about to fix any finding from the ledger in progress.md, and especially before believing an audit entry that says code is broken.
---

# Working one finding

The ledger and the audit both lag the code. The audit describes the tree as
it was in 2026-09-19; `src/` has been repaired underneath it ever since. So
the first question is never "how do I fix this" — it is **"is this still
true?"**

## 1. Preflight: is the defect still real?

Find the audit entry. Filenames are not what you would guess:

```
docs/architecture/audit/view-web.md  view-pyside.md  view-tkinter.md  dc-chuck.md
```

Headings are inconsistent (`### WEB-5: title`, `### GAMEPAD-17`,
`### PYSIDE-16 — title`), so match on the ID as a field, not the whole line:

```
awk -v want="WEB-5" '/^### /{p=($2==want||$0=="### "want||index($0,want))} p' <file>
```

Read the **"Actual behavior"** clause. That is the defect. Now check the
*current* tree for it — search by construct (function name, attribute, the
specific call), **never by the audit's line numbers**, which are stale.

Three outcomes:

- **Defect present** → continue to step 2.
- **Defect gone** → the finding needs **tests, not a fix**. That is a
  legitimate close, and it is what DC-18 turned out to be: `power_down` had
  used the locked priority path since S8 and the four racing booleans died in
  S7. Pin it with tests so it cannot regress into being true again.
- **Audit describes code that does not exist at all** → say so in the note
  and test for its continued absence. Three of agent B's sub-items were this.

Remember trap #1 from `CLAUDE.md`: **comments in `src/` quote the old broken
code**, so a grep hit is not evidence the defect survives. Read the line.

## 2. Test first, then implement

Safety paths before feature paths. If it can energize a coil or move an axis,
the stop path is written and tested first. For anything touching an
emergency-stop path, conform to `docs/architecture/safety-pattern.md` rather
than inventing a second pattern.

## 3. Prove the defect was real

A test that passes after the fix proves nothing on its own. Run the new test
against the **pre-fix** code.

**Do not use `git stash` for this.** Use a scratch copy, and chain the
restore with `;` so it runs even when pytest is killed:

```
S="$CLAUDE_SCRATCH"                       # session scratch dir, never the repo
cp src/model/foo.py "$S/foo.orig" && git checkout -- src/model/foo.py \
  && (timeout 120 .venv/bin/python -m pytest tests/core/test_x.py -q -k sel 2>&1 | tail -8); \
  cp "$S/foo.orig" src/model/foo.py && git diff --stat src/model/foo.py
```

Reading the result:

- **Red tests** — the ordinary outcome. Record the counts.
- **A hang or deadlock is a *stronger* result than a red test.** ROTATOR-8's
  check deadlocked on the held `_serial_lock` and had to be killed by the
  `timeout`; that *is* the defect, demonstrated. Record it as such.
- **Some tests will pass both ways.** Regression guards are supposed to.
  Name which ones and say why, rather than treating it as a failed check.

If a `git stash` variant was used anyway and the command hung, the repo is
left stashed: `git stash list` will show it. Restore before doing anything
else.

## 4. Close the row

Use the `stage-close` skill — it owns the row format, the test-name check
(`grep -rn "def <name>" tests/`), and the commit protocol. Do not restate
them here.

A row moves to `closed` only with a verified test name or a verification
note. `open (partly closed: ...)` is an honest answer; rounding up is not.
