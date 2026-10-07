# Tier N — timeouts an operator can see coming. Shared spec (2026-09-26).

Owner: "Add a popup to the web view that warns against closing the tab w/o
disabling devices … validate the idle timeout … across all views. Popups
should warn about an incoming timeout and provide an option to extend the
session."

Read first: `.claude/agents/ui-refiner.md` (profile; brief = DESIGN_BRIEF.md;
Operate), `.claude/skills/station-map/SKILL.md`, `docs/rebuild/BUGFIX_PLAN.md`
Tier N, your view's Tier L handoff `handoff/fix-l-<view>.md`.

STRICTLY BACKGROUND (owner at the Mac): `STATION_NO_WINDOWS=1` on every
pytest; Qt offscreen only; headless Chrome only; no Tk window; no captures on
screen (Web headless captures are fine).

## Core that landed (use it)

- `Probe.idle_remaining` in every probe's state: seconds until the idle
  interlock disables the motors (None while not in a mode). `Probe.IDLE_WARN_SECONDS`
  = 60: inside that window the model publishes ONE warning event "Idle Timeout
  Soon" per idle period; `run("extend_idle")` restarts the clock (declared in
  the schema as an internal element, so the allow-list passes; renders nothing).
- `controller.state()["energized"]`: the names of models holding hardware an
  operator should undo before leaving (a probe in any mode, a heating heater, a
  recording run). Wider than `is_active`.
- The Web watchdog is validated headless by the lead (warn at 5 s, FULL STOP at
  15.2 s of browser silence while active; every model latched). Nothing to change
  there; the page's copy about it must match those numbers.

## N1 — the idle countdown with Extend (every view)

While any probe's `idle_remaining` is not None and ≤ `IDLE_WARN_SECONDS`
(read the threshold from state if the lead exposes it; else 60), the view
shows ONE non-modal line per probe, where the eye is (under the disc in the
rail, or as the tray's live line): "Stepper Probe powers down in 42 s." with
an **Extend** button (accessible name "Extend Stepper Probe"). The number
counts down every second from state (no local timer that can drift; render
from the polled value). Pressing Extend runs `extend_idle` on that model; the
line goes away when `idle_remaining` climbs back above the window or becomes
None. It never covers the disc, never steals focus, never pops a modal. When
the interlock fires anyway, the existing "Idle Timeout" warning shows as
today. Two probes at once: two lines, station order.

## N2 (web only) — closing the tab

`beforeunload` today fires only while `is_active`. Key it on
`state.energized` being non-empty (a probe merely in a mode counts), and
keep it off after Quit / when nothing is energized. The browser's own
dialog cannot carry custom copy, so the page also carries the warning where
the operator can read it before they reach for the tab: a rail line under
"Stop: Ctrl+." while anything is energized: "Devices are energized. Disable
them before closing this tab; the station stops them 15 s after the tab
goes." (the 15 s is `STOP_SECONDS`; serve it from `/api/theme.json` or
`/api/state` rather than hard-coding — a one-line server.py change is in
your write set). Sentence case; one red only for stop/latch/fault, so this
line is ink.

## N3 (tk, qt) — nothing extra beyond N1

Window close and Quit already confirm (L9/L14). Check the confirmation's
words mention energized devices when `energized` is non-empty: "Quit the
station? Stepper Probe and Temperature Controller are energized; quitting
stops and disconnects them." else the existing sentence.

## Tests (first, at the base SHA)

Per view: (1) the countdown line appears with the model's name and the
seconds from state, only inside the window; (2) Extend runs `extend_idle`
on that model and the line clears when state says so; (3) two probes give
two lines in station order; (4) the line never covers the disc (geometry /
DOM order) and is not a modal; (5) Web: `beforeunload` is armed exactly when
`energized` is non-empty (drive it through the DOM: dispatch a
`beforeunload` Event and check `defaultPrevented`); the rail line's text
carries the served seconds; (6) Tk/Qt: the quit prompt names energized
models. Keep every existing test green or update it with its reason.

## Handoff

`handoff/fix-n-<view>.md` with tests by name, CORE CHANGE REQUESTS, and (Web)
headless captures `handoff/shots/n_web_{countdown,energized}_1400x900.png`.
One commit per item; never push; never amend; no Qt pass.
