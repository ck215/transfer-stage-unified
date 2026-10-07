# Brief: the update prompts act (branch `rb-restart`, worktree `../rb-restart`)

Base: `e8b857e` or later on `mvc-refactor` (`rb-ack` is merged). Python:
`/Users/ianalbinogonzalez/Documents/GitHub/transfer-stage-unified/main/.venv/bin/python`.
Read `.claude/agents/worktree-fixer.md` first, then
`handoff/fix-attention-popups.md` (the ack modal you extend, per view),
`src/events.py` (`Event.needs_ack`, `to_dict`), `src/views/base.py`
(where an ack event reaches a view), the ack modal in each view,
`src/controller/updater.py` and the Update code in
`src/controller/setup.py` (`check_updates`, `apply_update`, the startup
check thread, `_updated_to`, the Launch refusals), `src/app.py`
(`launch`, how each view is started and how the process ends), and each
view's Quit path (`/api/quit` in `server.py`, `_quit` in Tk and Qt).

## What the owner asked (2026-09-28)

"Should prompt for restart of program for updates." Today an applied
update only changes the Setup status line and refuses Launch; a ready
update only changes the line. Both should ASK, and the answer should act.

## Design (the lead's; deviations go in the handoff with reasons)

R1. **An action on an acknowledged notice.** `events.error/warn(...,
    ack=True, action=(label, name, command))` carries `action` into
    `Event.to_dict` (`{"label", "name", "command"}` or None). A view's ack
    modal with an action shows TWO keys: the action's label (default:
    Return runs it) and "Later" (Escape). Without an action, the modal is
    as `rb-ack` left it (one "Understood"). Running the action is the
    same path as pressing a button on that panel (`name` "__setup__" for
    Setup, else a model), so refusals and confirms show as usual.
R2. **Two prompts from Setup.** When the startup check (or Check again)
    finds `behind`: `events.warn("Update Ready", "<n> new commits are
    ready: <first line>. Update now, then restart the station.",
    ack=True, action=("Update now", "__setup__", "apply_update"))`,
    once per distinct remote sha. When an update has been applied:
    `events.info` is not enough: `events.warn("Restart Needed", "Updated
    to <sha7>. Restart the station to run it.", ack=True,
    action=("Restart now", "__setup__", "restart_station"))`. "Later"
    leaves the Setup line and the Launch refusal as they are.
R3. **`Setup.restart_station(confirmed=False)`.** Refuses while any model
    is launched and energized (`controller.is_energized`); otherwise asks
    once (`NeedsConfirm`: "Restart the station now? Every model closes
    first.") unless it came from the modal's action (pass
    `confirmed=True` from there, the modal IS the question), then closes
    every model through the Controller, flushes the log, and re-executes
    the same interpreter with the same argv (`os.execv(sys.executable,
    [sys.executable, *sys.argv])`) from the checkout root, so the view,
    port and flags are kept. On Windows `execv` replaces the process the
    same way for our purpose; if a platform quirk needs `subprocess.Popen`
    + `sys.exit`, do that in `app.py` behind one function
    `app.restart_process()` and no platform check anywhere else. A
    frozen bundle re-executes `sys.executable` (its own launcher).
R4. **Each view survives the restart.** Tk and Qt: the process is
    replaced, the window comes back by itself. Web: the server dies and
    a new one listens on the same port within seconds; the page must
    show "Restarting the station…" (its existing connection-lost state
    or a line in the tray) and reload itself when `/api/state` answers
    again (poll every 2 s, up to 60 s, then "The station did not come
    back; start it by hand."). The heartbeat worker from `rb-dash-web`
    must not FULL STOP anything during this (the old server is gone;
    the new one has no client yet, so its watchdog is idle until the
    first beat).
R5. **Tests first.** Events: `action` round-trips. Setup: the ready
    prompt fires once per remote sha with the action; the applied prompt
    fires with Restart; `restart_station` refuses while energized, asks
    once, and calls the injected `restart=` (never the real exec in
    tests). Views: with the fakes, an ack event with an action shows two
    keys and Return runs the action against the named panel (Tk and Web
    headless; Qt tests written, the lead runs them). `app.restart_process`
    is tested with a monkeypatched `os.execv`.

R6. **The alert band goes (owner ruling 2026-09-28, via the lead).** Tk
    and Qt kept the old alert band beside the new dialog. Remove the band;
    the dialog is the acknowledgement and the log keeps the history. Make
    the dialog stay above the main window without a grab: Tk
    `transient(master)` and `lift()` on every show; Qt: parent it to the
    main window with `Qt.Tool` so it floats over it. The stop still works
    while it is open (the existing tests prove it; keep them green).
R7. **The Web logs the acknowledgement.** `POST /api/ack` `{"id": <event
    id>}` -> `events.debug("Acknowledged", ...)`; the page calls it on
    Understood (and on the action key). Same local-only rules as every
    POST.

## Write set (exclusive)

- `src/events.py` (the `action` field), `src/controller/setup.py` (the
  two prompts, `restart_station`), `src/app.py` (`restart_process`)
- `src/views/base.py`, `src/views/tk.py`, `src/views/qt.py`,
  `src/views/web/static/app.js`, `styles.css`, `src/views/web/server.py`
  (the ack modal's action and the Web reload only)
- `tests/test_core_events.py`, `tests/test_setup.py`, `tests/test_app.py`,
  `tests/test_view_tk.py`, `tests/test_view_qt*.py`,
  `tests/test_view_web*.py` (additive)

Not yours: `src/model/**`, `src/controller/updater.py`,
`src/controller/controller.py`, `src/schema.py`, `docs/**`.

## Gates before you commit

- `STATION_NO_WINDOWS=1 <PY> -m pytest tests -q -p no:cacheprovider -m "not qt"`
  to a file under your scratch dir, exit code unpiped: all green.
- Golden: 78.
- Headless Web on port 8100 with a FakeUpdater-free path: point the
  checkout at a throwaway remote? No: never change this checkout's
  remotes. Instead drive the Web with `STATION_NO_UPDATE_CHECK=1` and
  raise the two prompts through a test hook you add to nothing outside
  your write set (say how), capture the modal with its two keys
  (`handoff/shots/restart_web_prompt.png`), and prove the reload path
  against a server you stop and start again by hand on the same port.
  Never run `restart_station` or `apply_update` against this checkout.

## Handoff

`handoff/fix-restart-prompt.md` in the worktree-fixer shape. Commit on
`rb-restart`; never push.
