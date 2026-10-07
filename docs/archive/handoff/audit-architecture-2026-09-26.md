# Architecture audit — 2026-09-26 (HEAD 21608c6, branch mvc-refactor)

Read-only. Gates run here: `tests/test_architecture.py` + `tests/test_wire_golden.py` with `STATION_NO_WINDOWS=1 QT_QPA_PLATFORM=offscreen` → **162 passed** (78 golden scenarios collected). Nothing else run; no window, no Qt pass.

## Executive summary

1. **Verdict: intact with debt.** No S1. Layering holds: no view imports a model, a device or `legacy`; no view touches a Controller private; there is exactly one latch (`Model._estop`, `src/model/base.py:24`); every stop entry point reaches `Model.estop` → `_halt_hardware`; the golden wire gate is byte-identical.
2. The debt is **copy and presentation logic written three times**. Tier L added core words (`stop_words`, `event_line`) but the views kept their own gate-reason, event-line, quiet-value, dialog and empty-state tables. They have already drifted.
3. **One drift is operator-visible and wrong.** Step is `disabled_when=("manual","latched")`. Tk and Web say "Not in manual mode" when the probe *is* in manual mode, while Qt correctly says "In manual mode". Two tests pin the wrong words (ARCH-1).
4. Parity gaps: Qt closes a model without asking (Tk and Web ask). Tk's window close and OS Quit skip the Quit question (Qt asks). Web's device page lets tier 1 scroll away (ARCH-6, ARCH-7).
5. Tests: three hand-written `FakeController`s re-implement the Controller surface, and the Tk one keeps the old (pre-L1) semantics: one latched model counts as "every model stopped". Web browser tests use 88 fixed sleeps. The architecture test is a blacklist; the BRIEF contract is a whitelist.
6. Size: `tk.py` grew 3907 → 6169 lines in two days; `TkPanelView` is one class of 3047 lines and 141 methods.
7. **Top three actions:**
   - **(a)** Create one `views/words.py` (or extend `views/base.py`) for `gate_reason`, `sentence`/`label`, `split_unit`, the quiet check, the dialog/Quit copy and the unconfirmed title. The server serves it to the Web client. This fixes ARCH-1 through ARCH-4 and ARCH-13.
   - **(b)** Parity worktrees: Qt close-model confirm, Tk window-close/OS-Quit confirm, Web tier-1 pin.
   - **(c)** Replace the three test fakes with a real `Controller` over fake models, and turn `test_architecture` into a whitelist plus a "no `controller._`" check.
8. Nothing blocks the bench; ARCH-1 should land before the next owner session. Docs drift is modest (ARCH-15). Findings: 0 × S1, 10 × S2, 6 × S3.

---

## 1. Layering

### ARCH-10 — The architecture test no longer expresses the contract [S2]
- **Where:** `tests/test_architecture.py:26-37`; `docs/rebuild/BRIEF.md` "Import rules".
- **Observed:**
  - The BRIEF is a whitelist: views import only controller, setup (type only), result, schema, events and views.\*. The test is a blacklist that only forbids `model`, `devices`, `legacy` and `src` prefixes. A view could import `panel`, `param`, `palette` or `controller.setup` concretely and pass.
  - Only `model/`, `devices/` and `panel.py` are checked for importing views or the controller. `schema`, `events`, `result`, `param` and `palette` are not.
  - The BRIEF's three-call view API (`schema/state/run`) is now a 13-member surface in practice: `stop_state, estop_all, clear_estop_all, remove, reopen, model_names, closed_names, config, options, set_input_focus, subscribe, is_active, is_estopped`. The surface is clean today (grep: no `controller._` in any view, and `app.py:92` `_hook_exit` is the composition root), but nothing enforces that.
- **Expected:** The test encodes the whitelist and the public surface.
- **Smallest fix:** A views whitelist: `{events, result, schema, controller.controller, controller.setup, views.*}` plus stdlib/toolkits. Add the same backend check for the core modules. Add an AST check that no `views/` file has an attribute starting `_` on a name `controller`.
- **Confidence:** high.

Checked and fine: `server.py:43-46` imports `events, result, schema, views.theme, views.base`. The screen image goes through a model's `region_select` data command (`server.py:427-450`), not `mss`.

## 2. The stop path

Traced, and correct:
- Tk disc `tk.py:5895` → `estop_all`/`clear_estop_all`.
- Tk chord `tk.py:5386`.
- Qt disc `qt.py:5261` → `base.Dashboard.toggle_estop_all`.
- Qt chord `qt.py:5268`.
- Web disc `app.js:3152` keyed on the server's `stop_words.action` (`server.py:130`).
- Web chord `app.js:2319`.
- Watchdog `server.py:877` `estop_all`.
- Per-model switch → `Controller.run("toggle_estop")`, which never queues (`controller.py:161`) → `Model.toggle_estop` (`base.py:142`).
- Quit / close → `Dashboard.close` → `Controller.close` → `_close_models` → `_estop_concurrently`.
- SIGTERM → `hook_signals` (`controller.py:328`, re-armed at `app.py:117`).

All of them end in `Model.estop` → `_halt_hardware` (probe:468, heater:327, rotator:418, red_monitor:694). `events.forget("Stop Not Confirmed")` sits in `Model.clear_estop` (`base.py:138`). That is the right layer, and it is reached from both the per-model and the all-clear paths.

### ARCH-5 — Tk re-derives the stop instead of using the base [S2]
- **Where:**
  - `tk.py:5895-5907` `_on_stop_clicked` re-implements `Dashboard.toggle_estop_all` (`base.py:233-243`). Qt calls the base.
  - `tk.py:5397-5410` `_stop_state` falls back to `latched = all panels if controller.is_estopped` with `every=bool(latched)`. That is exactly the pre-L1 S1 semantics (one model's switch reads as "every model").
- **Observed:** The fallback is dead in production (`Controller.stop_state` always returns a dict) but alive as a trap. The copy of `toggle_estop_all` means the next change to the disc rule lands in two places.
- **Expected:** Tk's disc calls `self.toggle_estop_all()`, and `_stop_state` returns `self.controller.stop_state`, as `qt.py:4635` does.
- **Smallest fix:** Delete the fallback and the duplicated body (about 20 lines). Tk's `_confirm` already satisfies the base hook.
- **Confidence:** high.

### ARCH-4 — "Stop Not Confirmed" is an untyped string key in five files [S2]
- **Where:** `model/base.py:138,146`; `controller.py:109,232`; `tk.py:149`; `qt.py:250`; `app.js:49`.
- **Observed:** L2's tray clearing depends on every view matching the exact title string. `events.forget` also matches by title across all sources, so clearing one model's latch ends the dedupe episode of another model that is still unconfirmed. That is benign today (a repeat becomes a new line, not a lost one).
- **Expected:** One constant in `events.py`, served to Web via `/api/rules`.
- **Smallest fix:** `events.STOP_NOT_CONFIRMED = "Stop Not Confirmed"`, imported in core, Tk and Qt, and served to Web.
- **Confidence:** high.

`is_estopped` keying: the remaining view uses are per-model (the entry freezes in `qt.py:3938` and `app.js:2065`; the switch in `tk.py:3449` and `app.js:843`). Those are correct. The top-level `is_estopped` in `/api/estop_all` (`server.py:223`) and in `Controller.state()` is read by no client. It is dead API, noted under ARCH-14.

## 3. Schema as the contract

The `_make_` check is intact (`base.py:89-92`; the Web client covers all 14 types). `tier`, `disclosure`, `slider`, `unit`, `empty` (plot/image), `enabled_by`, `rail` and `detached` are each read by all three views.

### ARCH-1 — Gate-reason words: four tables, and two say the opposite of the truth [S2]
- **Where:**
  - `tk.py:194-200,465-467,4208-4229`
  - `qt.py:373-402`
  - `app.js:189-224`
  - core's own `panel.py:36-61` `GATE_REASONS`
  - Schema: `probe.py:1131` Step `disabled_when=("manual","latched")`, role `go`.
- **Observed:**
  - In manual mode, Tk (`_gate_word("manual")`) and Web (`modeWords`) say **"Not in manual mode"** under Step. Qt says "In manual mode".
  - Tests pin the wrong copy: `tests/test_view_web_client.py:1052,1063`, where `gateReason({disabled_when:['manual','latched']},'manual')` is asserted to be "Not in manual mode"; and `tests/test_view_web_server.py:2625-2641` (`tprobe.mode="manual"`).
  - The `enabled_by` wording also differs three ways: Tk "<Launch> is off", Qt "Tick Launch first", Web "Tick Launch on this row first".
- **Expected:** One `gate_reason(element, mode, values)` in `views/base.py`, split by direction: `disabled_when` hit → "In X mode", unmet `enabled_when` → "Not in X mode" / what it waits for. Serve it to Web. This is M3, plus the correctness bug M3 does not name.
- **Smallest fix:** Move Qt's `gate_reason` (the correct one) to `views/base.py`. Tk calls it. The server adds the tables to `/api/rules`. Fix the two tests' expectations.
- **Confidence:** high (read the schema, the three functions and the tests).

### ARCH-11 — Schema fields no view honours, and view copy that shadows a field [S3]
- **Where:**
  - `schema.py:92-93` `format`: set on `red_monitor.py:1209-1211` and read by no view. It is redundant with `Param.decimals=2`.
  - `schema.py:132-133` button `confirm`: read by no view and set by no schema.
  - Plot `x_label`/`y_label`: Tk ignores both (`tk.py:3630`); Qt puts them in a tooltip only (`qt.py:3529`); Web draws `x_label` in place of a tick and ignores `y_label` (`app.js:1466`).
  - `tooltip_on` is not read by Web. Web hard-codes the per-model switch copy "Stop this model only" (`app.js:905-915`), while Tk and Qt show the schema's "Stop"/"Stopped". This is J3, still open, and now also a parity gap.
- **Smallest fix:** Drop `format` and button `confirm` (or implement them). Put the switch caption in `base._safety_section` (J3). Decide on axis labels once.
- **Confidence:** high.

### ARCH-12 — `log_stream` has no `empty`; three empty-state rules, one stale [S3]
- **Where:** `tk.py:3806-3813` (derived from the label), `qt.py:3618` (`"gamepad" in caption`), `app.js:250-259` (keyed on model command names `series`/`figure`/`gamepad_log`).
- **Observed:** Web says "Choose a gamepad under **Configuration**". Since K1 the gamepad is tier 1.
- **Smallest fix:** `log_stream(..., empty=)` as for plot/image. The three views read it, and Web drops `EMPTY_STATES`.
- **Confidence:** high.

### ARCH-2 — Toolkit-neutral presentation helpers written twice in Python, once in JS [S2]
- **Where:**
  - `_sentence` tk:346 / `sentence` qt:348 / `sentence` app.js
  - `_label` tk:357 / `sentence_case` qt:359
  - `_split_unit` tk:370 / `split_unit` qt:405
  - `_is_number` tk:378 / `is_number` qt:436
  - `_tier_of` tk:388 / `tier_of` qt:527
  - `_axis_of` tk:393 / `axis_letter` qt:418
  - `_motion_reduced` tk:336 / `motion_reduced` qt:284
  - `lost_devices`/`simulation_line` qt:300-333, with Tk's equivalents inline
  - Qt's private `QUIET_VALUES` (qt.py:232) and Web's `QUIET_WORDS` (app.js:533) beside `theme.QUIET_VALUES`
- **Observed:** Each is a small pure function, and each round edits them per view. That is how ARCH-1 happened.
- **Expected:** `views/base.py` (or a new `views/words.py`) holds them; Tk and Qt import them; the Web server serves the tables.
- **Smallest fix:** Lift Qt's module-level versions verbatim (they are already free functions) and repoint Tk. Tests move from `test_view_qt_widgets.py` to `test_core_views_base.py`.
- **Confidence:** high.

## 4. Three views, one instrument (parity)

| Behaviour | Tk | Qt | Web |
|---|---|---|---|
| Overview + device page | yes | yes | yes (`styles.css:563`) |
| Disclosure memory per model | yes | yes | yes |
| Tier 1 pinned on device page (L5) | yes `tk.py:1947-2042` | yes `qt.py:4893` | **no**: document scrolls |
| Close-model asks | yes `tk.py:5176` | **no** `qt.py:2666→2718→5250` | yes `app.js:3059` |
| Quit asks | button yes `tk.py:5181`; **window close / OS Quit no** `tk.py:4786,5632` | yes, both `qt.py:4340-4357` | button yes; tab close never quits (G2) |
| Gamepad log window, empty state | yes | yes | yes (stale copy, ARCH-12) |
| Rail marks per model | yes | yes | yes |
| Disabled-command reason | yes (wrong for manual) | yes | yes (wrong for manual) |
| Slider keys, Home/End inert | yes `tk.py:205-207` | yes `qt.py:1889-1893` | yes |
| Event line | own `_event_line` tk:235 ("Error: Title. msg") | `base.event_line` | own `eventText` app.js:303 |
| Per-model switch caption | schema "Stop" | schema "Stop" | "Stop this model only" |
| Setup surface | tab | dock | drawer (brief) |

### ARCH-6 — A close that stops hardware asks in some views and not others [S2]
- **Where:** Qt: `qt.py:2666` (the entry's close button → `closeEvent` emits `closed` → `_on_entry_closed` → `close_model`, with no question). Tk: `tk.py:4786` (`WM_DELETE_WINDOW` → `self.close`) and `tk.py:5632` (`::tk::mac::Quit` → `self.close`), neither asking.
- **Observed:** The direction is safe (everything stops), so this is not S1. But one mis-press destructs a model, or the whole station, in one view and not in the others.
- **Smallest fix:** Qt routes the entry close through `ask(...)` with Tk's copy. Tk points `WM_DELETE_WINDOW` at `_on_quit_clicked`. Keep the `tk::mac::Quit` hook unconfirmed only if the owner rules so (P3).
- **Confidence:** high.

### ARCH-7 — Web device page lets tier 1 scroll away [S2]
- **Where:** `styles.css:555-565`; L5 was scoped to Tk and Qt only (BUGFIX_PLAN L5).
- **Expected:** DESIGN_BRIEF: "Tier 1 never scrolls away."
- **Smallest fix:** On `.sheet.is-device`, make the head and tier-1 body `position: sticky` (or give the well its own `overflow:auto` height, as Tk and Qt do).
- **Confidence:** medium (read from the CSS; not driven in a browser).

### ARCH-3 — Three event-line wordings despite `views.base.event_line` [S2]
- **Where:** `base.py:25-38` (Qt uses it); `tk.py:235-246` prefixes the severity word and uses ". "; `app.js:303-310`. `events.py:38-43` `Event.text` still says "One wording for all three views" with `[Source]`, and is still serialised as `text`.
- **Smallest fix:** Tk calls `event_line` (the band can prefix the severity mark itself). The server adds `line` to `Event.to_dict` via `event_line`. Correct the `Event.text` docstring.
- **Confidence:** high.

## 5. Size and shape

| File | 9a117f3 | 1882706 | HEAD |
|---|---|---|---|
| tk.py | 3907 | 5232 | **6169** |
| qt.py | 3691 | 4555 | 5331 |
| app.js | 2541 | 2962 | 3404 |
| styles.css | 1429 | 1845 | 2006 |
| server.py | 873 | 882 | 888 |
| views/base.py | 210 | 210 | 277 |
| src total | 22794 | 25848 | 28355 |
| tests total | 25231 | 26712 | 29773 |

### ARCH-14 — Mixed-responsibility classes and residue from the redesigns [S3]
- **Size:**
  - `TkPanelView` is 3047 lines with 141 methods (`tk.py:1458`): widgets, the tier/disclosure layout, the well scroll, gate captions, the log windows and the slider keys. `TkDashboard` is 1486 lines.
  - `QtPanelView` is 1388 lines; `qt.stylesheet()` is 324 lines (`qt.py:595`).
  - Web: `build()` at app.js:1627 is 132 lines; the constructor at app.js:2228 is 123.
  - Functions over 80 lines: tk `__init__`:1487 (112), `_configure_styles`:4790 (111), `_field`:2472 (89), `_lay_out_sheet`:5236 (81), `_make_row_section`:2347 (81); qt `_build_rail`:4379 (102); `red_monitor.schema`:1197 (116).
- **Residue:**
  - Web still calls entries "card"/`PanelCard` (80 + 21 hits in JS, 65 in CSS).
  - `theme.font()` is unused.
  - `/api/estop_all`'s `is_estopped` field is unused.
  - Tk's `STOPPED_HEADLINE` and `LATCHED_LINE` (`tk.py:139-140`) are placeholder copy, overwritten from `stop_words`.
  - `base.py:3-5` still says "model tabs, the global FULL STOP toggle".
  - No TODO/FIXME/XXX/HACK under `src/`.
- **Smallest fix:** Split `TkPanelView` along its seams (a `_Well`/tier layout helper, the log-window manager, the slider helper) in its own worktree, after ARCH-2 has shrunk it. Rename card → entry in Web in one mechanical pass.
- **Confidence:** high on the counts; the split points are judgement.

### ARCH-13 — Copy tables per view [S3]
- **Where:**
  - Quit prompt: tk.py:4699, qt.py:252, app.js:2633.
  - Dialog verbs: tk.py:154-156, qt.py:254-255.
  - "Stop not confirmed. Treat as live.": tk.py:142, qt.py:242, app.js:1558.
  - Web `TIER_LABELS` default `{2:'Details'}` (app.js:37, 1765) versus theme `{2:"Configure"}`.
- **Smallest fix:** Put them in the same `views/words.py` as ARCH-2, served by `/api/rules`.
- **Confidence:** high.

## 6. Tests

### ARCH-8 — Three hand-written FakeControllers re-implement the Controller [S2]
- **Where:** `test_view_tk.py:642-720` (its `stop_state` sets `latched = all panels if is_estopped`, `every=bool(latched)`, the pre-L1 rule); `test_view_qt_widgets.py:256-330`; `test_view_web_watchdog.py:25`.
- **Observed:** Each view is tested against a different idea of the Controller. The Tk suite cannot catch an L1 regression in Tk.
- **Smallest fix:** A shared `tests/fakes_controller.py` that builds a real `Controller` over `test_core_fakes` models. Migrate one file at a time.
- **Confidence:** high.

### ARCH-9 — Browser tests depend on fixed sleeps [S2]
- **Where:** `test_view_web_server.py`: 88 `sleep(100–600)` calls inside puppeteer scripts (e.g. :806, :818, :870, :2637).
- **Smallest fix:** Replace them with the existing `until(...)` predicate helper.
- **Confidence:** high.

### ARCH-16 — `window` marker detection is heuristic [S3]
- **Where:** `conftest.py:88-97`. Detection keys on the name `_real_build` appearing in the test function's own `co_names`. A test that reaches the harness through another helper would map a window under `STATION_NO_WINDOWS=1`. Today all 11 are caught (10 direct plus `test_app.py:253`).
- **Smallest fix:** A `real_tk` fixture that carries the marker.
- **Confidence:** medium.

Also noted:
- View tests re-test core logic (Qt's pure `gate_reason`/`sentence`/`split_unit`; Web's `gateReason`); they become core tests with ARCH-2.
- Trend: 1956 → 2056 tests (+5%) against src +10% since 1882706. Log-string assertions checked still match `controller.py`; the known-wrong pinned copy is ARCH-1.

## 7. Docs drift

### ARCH-15 — Statements the code no longer matches [S3]
- `STATUS.md:3-5` says to read "`WEB_DESIGN_BRIEF.md`". That brief is superseded; it should be `DESIGN_BRIEF.md`.
- `DESIGN_BRIEF.md` "Rules that do not move": the disc "reads `Clear` when latched". Since L1 it reads Clear only when *every* model is latched.
- `DESIGN_BRIEF.md` "Two pages": the headline is "Every model is stopped.". It now varies (`stop_words`: "Stopped. X did not confirm.", or no headline for a partial stop).
- `DESIGN_BRIEF.md` Layout: "The opened model's readings are `focal`, a closed probe's `compact`" is pre-K4 language. It should say device page `focal`, overview `compact`.
- `BRIEF.md` import rules (whitelist) versus the test (blacklist): ARCH-10. The view API is listed as three calls; the real surface is 13.
- `schema.py:57-58`: default "Configure"/"Details". The theme has only "Configure"; Web falls back to "Details".
- `controller.py:185-186`: "The watchdog and the close path key on [`is_estopped`]". The watchdog keys on `is_active`; nothing on the close path reads it.
- `events.py:40` `Event.text` "one wording for all three views": false since L11.
- `.claude/skills/station-map/SKILL.md:24,58` still says "1675 fast tests, 127 Qt, 13.7k lines" (now 2056 / 195 / 28.4k). Line 83 still has "Off / SIM / port"; G3 removed "Off".
- CLAUDE.md's counts are current (2056 / 195 / 78): not drift.

## 8. Wire and firmware

- The golden gate passed: 78/78 scenarios byte-identical to `legacy/src`, run once here.
- Since 9a117f3 the model/device diffs add no command methods: only the `stop_confirmed`, `latched_at` and `next_step` properties, `SerialPort(probe=)` (log level only, `serial_port.py:547-549`), and the Setup button relabel.
- `Panel._allows` (`panel.py:156-181`) refuses any command the schema does not declare, so no undeclared surface can be reached from any view or the Web API.
- Minor: `setup.py:678` sets `device.probe = True` after construction instead of passing the new `probe=True` keyword argument [S3, not numbered].

---

## Refactor plan (by value)

| # | Item | Files | Route | Risk |
|---|---|---|---|---|
| 1 | ARCH-1 + ARCH-2 + ARCH-13 + ARCH-4: `views/words.py` (`gate_reason` split by direction, `sentence`/`label`/`split_unit`/quiet check/`tier_of`/axis, Quit/Clear/Close dialog copy, the unconfirmed title and line), served by `/api/rules`; fix the two web tests' pinned copy | `views/base.py` or new `views/words.py`, `events.py`, `server.py`, then `tk.py`/`qt.py`/`app.js` | direct (core) → one worktree per view | low: pure functions; the golden gate is unaffected |
| 2 | ARCH-6: Qt close-model confirm; Tk window close → Quit question | `qt.py`, `tk.py` | worktree per view (small) | low; owner call on the `tk::mac::Quit` hook (P3) |
| 3 | ARCH-5: Tk disc → `toggle_estop_all`; delete the `_stop_state` fallback | `tk.py` | router `refactor` + lead verify | low |
| 4 | ARCH-8: shared real-Controller fake; migrate the Tk, Qt-widgets and watchdog suites | `tests/` | agy (multi-file, needs its own test pass) | medium: large test churn |
| 5 | ARCH-10: whitelist architecture test + `controller._` AST check + core-module checks | `tests/test_architecture.py` | direct | low |
| 6 | ARCH-7: Web tier-1 sticky on the device page | `styles.css`, `app.js` | worktree web | low |
| 7 | ARCH-3: Tk and Web use `event_line`; server serves `line` | `tk.py`, `events.py`/`server.py`, `app.js` | direct (core) → router per view | low |
| 8 | ARCH-12 + ARCH-11: `log_stream(empty=)`; drop `format` and button `confirm`; J3 switch caption in `_safety_section` | `schema.py`, `model/base.py`, `probe.py`, views | direct (core) → router per view | low |
| 9 | ARCH-9: sleeps → `until` | `test_view_web_server.py` | router `transform` | low |
| 10 | ARCH-15: docs pass (STATUS pointer, DESIGN_BRIEF stop/Overview wording, BRIEF API list, station-map counts) | `docs/rebuild/*`, `.claude/skills/station-map` | docs-pruner or direct | none |
| 11 | ARCH-14: split `TkPanelView`; card → entry rename in Web; drop `theme.font` and the dead `is_estopped` field | `tk.py`, `app.js`, `styles.css`, `theme.py`, `server.py` | agy in a worktree, after #1 | medium: large mechanical diff; needs the window tests on a free display |
| 12 | ARCH-16: `real_tk` fixture | `tests/conftest.py`, `test_view_tk.py` | router | low |

## Not findings (checked, fine)

- No view imports `model`/`devices`/`legacy`, and no view or `app.py` reads a Controller private (`_models`, `_lock`, `_model_or_none`, ...). `app.py:92` `_hook_exit` is the composition root.
- `mss`, `serial` and `pygame` each have one owner (the test passes).
- One latch: `threading.Event` only at `model/base.py:24`; `_halt_hardware` is overridden in the four models and nothing else sets or clears `_estop`.
- Every stop entry point reaches `Model.estop`. A stop never queues behind the per-model command lock (`controller.py:161`). The chord never clears, in all three views. Qt answers a pending question No before stopping (`qt.py:5264`); Web does the same (`app.js:3175`).
- The Web client derives its disc face and action only from the server's `stop_words` (`app.js:2779-2783`, `3107-3135`). No view computes the headline or rail line itself (Tk's constants are placeholders overwritten from `stop_words`).
- `events.forget` is called on the model's clear, which both per-model and all-clear paths reach.
- No colour literal in any view file; `QUIET_VALUES`/`TIER_LABELS` served to Web (`server.py:165-166`). Qt's `Meta+.` (`qt.py:201`) is toolkit-forced, allowed by the ruling. CLAUDE.md counts are current.
