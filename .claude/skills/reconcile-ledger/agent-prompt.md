# Agent prompt templates

Two waves. Wave 1 decides the verdict; wave 2 exists only because wave 1
reliably fails the evidence bar on its own. Do not merge them — wave 1
agents that are also asked for test names produce worse verdicts.

Substitute `<REPO>`, `<IDS>`, `<AUDIT FILES>`, `<OUT>`.

---

## Wave 1 — verdict

```
Repo: <REPO>

STRICT RULE: You are READ-ONLY on the repository. Do not edit, create, or
delete ANY file inside the repo. Your only write is one output file in the
scratch directory, named at the end.

Findings to audit, with the audit file each lives in:
<AUDIT FILES + IDS>

For each finding do exactly this:

1. Read its entry in its audit file (entries start with `### <ID>`). Note
   the "Actual behavior" — that is the defect, with file:line references to
   the ORIGINAL code.
2. Check whether that defect still exists in the CURRENT src/ tree. The line
   numbers in the audit are stale; search by the actual code construct
   (function name, attribute name, the specific call).
3. Decide a verdict.

CRITICAL TRAP — read carefully. This codebase documents its own repairs in
comments and docstrings. A grep hit for the buggy construct is NOT evidence
the bug survives if the hit is inside a comment, a docstring, or a string
literal. Example: `_active_poller_count` matches once in src/, but that match
is a docstring explaining that the refcount was REMOVED. That finding is
CLOSED, not open. ALWAYS look at the matching line in context.

4. If you conclude CLOSED, cite the src file:line where the fix lives.
5. If the defect still exists in live (non-comment) code, verdict is OPEN,
   and you cite the current file:line where it lives.
6. If you cannot tell, verdict is UNSURE with one sentence on what blocked
   you. UNSURE is acceptable and is much better than a wrong CLOSED.

NEVER cite docs/implementation/progress.md as evidence. That file is what
you are auditing; using it as proof is circular.

Write your results to this exact path: <OUT>

Format, one block per finding, nothing else in the file:

## <FINDING-ID>
VERDICT: CLOSED | OPEN | UNSURE
EVIDENCE: <src path:line>
NOTE: <one sentence, plain English, what you actually found>

Your final reply: a compact tally only — how many CLOSED, OPEN, UNSURE, and
the IDs in each bucket.
```

## Wave 2 — test names for the CLOSED set

```
Repo: <REPO>

You are READ-ONLY on the repo. Your only write is one scratch file.

A previous pass established that these defects are FIXED in src/. Your job
is only to find the exact test function name that proves each fix, or to
establish that none exists.

Findings: <IDS>

For each: read the audit entry to learn what the defect was, then search
tests/ for a test that would fail if the defect came back.

ABSOLUTE REQUIREMENT — the previous pass failed on exactly this. A test FILE
name is NOT an answer. `tests/hardware/test_gamepad.py` is a file.
`test_closing_a_poller_never_tears_sdl_down` is a test. Your answer must be a
function name that literally appears after `def ` in a file under tests/.

Before writing any name, verify it:
  grep -rn "def <name>" tests/
If that prints nothing, the name is wrong — do not use it.

If after genuine searching no test covers it, answer NONE and give a one-line
VERIFICATION NOTE: the src file:line where the fix is visible.

Write to: <OUT>

## <FINDING-ID>
TESTS: <comma-separated verified function names>   OR   NONE
FILE: <tests/path.py>   OR   -
NOTE: <why these prove it, or the src file:line verification note>
```

---

## What went wrong the first time, so it is not repeated

- **Tallies are unreliable.** Three of six agents returned counts that
  disagreed with their own lists. Read the verdict files, never the summary.
- **19 of 28 CLOSED verdicts cited a file, not a test.** Hence wave 2.
- **One agent cited `progress.md` as corroboration** — circular, since that
  is the artefact under audit. The prompt now forbids it explicitly.
- **False OPENs happen.** DC-16 was reported OPEN; `pyside/view.py` in fact
  has exactly one `QTimer` and a test (`test_view_keeps_one_render_tick_and
  _no_device_loops`) pinning it. Spot-check any OPEN that contradicts a
  stage's completion claim.
- **False OPENs are cheap, false CLOSEDs are not.** A false OPEN costs one
  verification; a false CLOSED erases a real defect from the backlog.
  Verify every CLOSED yourself before flipping a row.
