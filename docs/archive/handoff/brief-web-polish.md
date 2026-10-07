# Brief: Web polish, two items (branch `rb-web-polish`, worktree `../rb-web-polish`)

Base: `345763f` or later on `mvc-refactor`. Python as usual
(`/Users/ianalbinogonzalez/Documents/GitHub/transfer-stage-unified/main/.venv/bin/python`).
Read `.claude/agents/worktree-fixer.md` first, then
`src/views/web/static/app.js` (the tier-1 pinning rule, "pins up to 60%
of the window"; the hosted group from `rb-dash-web`; how a readonly's
text is cased), `styles.css`, `tests/test_view_web_dashboard.py` and the
other `tests/test_view_web*.py`, `handoff/fix-dashboard-web.md` ("Needs the
lead" 3).

## Items

W1. **No pinning on a host page.** The Transfer Map's tier 1 is ~480 px
    at 1440x900 and gets pinned, so Red Percent's group scrolls under it
    with ~400 px left. On a page whose model hosts another (the state's
    `host` points at it), tier 1 is not pinned: the whole page scrolls.
    Every other page keeps today's rule. Test with the fakes.
W2. **A value is not sentence-cased.** The Web capitalises the first
    letter of readonly values (the version sha shows "D66c462"). Captions
    and event lines keep their sentence case; a value's text is shown as
    the model gives it. Test on a readonly whose value starts lowercase.

## Write set (exclusive)

- `src/views/web/static/app.js`, `styles.css`
- `tests/test_view_web*.py` (additive)

Not yours: everything else.

## Gates

- `STATION_NO_WINDOWS=1 <PY> -m pytest tests -q -p no:cacheprovider -m "not qt"`
  to a file, exit code unpiped: all green. Golden: 78. A headless capture
  of the Transfer Map page at 1440x900 scrolled to the Red Percent group
  (port 8103, the usual recipe) to `handoff/shots/web_host_unpinned.png`.
  Nothing on screen.

## Handoff

`handoff/fix-web-polish.md`. Commit on `rb-web-polish`; never push.
