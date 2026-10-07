# fix-link-views — rb-link-views (base fb5a045)

Worktree `../rb-link-views`, branch `rb-link-views`. Nothing was pushed. Every file I touched is in the write set
(src/views/{base,tk,qt}.py, src/views/web/server.py, src/views/web/static/{app.js,styles.css}, tests/test_view_*.py).
No schema, panel, model, controller or docs change was needed.

Commits, oldest first:
a57868c V1 · aea4261 V2 · 8cc97f7 V5 · e516c3f V3 · 6c538f1 V4 · f0ab36a V4 (follow-up) · 5f0e0da V1 (follow-up test)

Design: the words, the tiers and the gating are decided once in `views.base`, from state only, with no model names:
`link_notice`, `link_is_down`, `link_holds`, `link_gate_reason`, `link_counters`, `with_link_row`, `held_notice`,
`entry_notices` and `worst_severity`. `PanelView._refresh` applies them, and each toolkit draws them through one hook,
`_set_notices`. The Web server serves the same results the way it already serves `stop_words`: `link_words` in
/api/state, and the counters row in /api/schema. Tiers use the theme's severity vocabulary. "error" is the danger tier:
signal rule, warning glyph in signal. "warning" is the attention tier: glyph in the warning ink, rule left ink.
No new colours.

## V1
STATUS: closed
TEST: test_v1_the_link_is_said_in_operator_words, test_v1_a_stall_with_no_position_age_falls_back_to_the_models_age, test_v1_a_down_link_holds_modes_and_go_commands_never_the_stop, test_v1_a_reconnecting_link_greys_the_modes_mutes_the_numbers_and_says_so, test_v1_a_stalled_link_is_a_warning_and_leaves_the_controls_live, test_v1_a_model_without_a_link_is_untouched (tests/test_view_link.py); test_v1_a_reconnecting_link_turns_the_entry_signal_and_holds_its_modes, test_v1_a_stalled_link_is_the_attention_tier_and_holds_nothing, test_v1_the_rail_line_of_a_down_link_shows_its_tier (tests/test_view_tk.py); test_v1_the_rail_mark_carries_the_entrys_link_tier (tests/test_view_qt.py, non-qt); Qt widget tests under UNVERIFIED
COMMIT: a57868c (+ 5f0e0da: test_the_fold_ranks_exactly_the_marks_rail_mark_can_return updated for the two new rail kinds, with the reason in the test)
PROVED: all 18 V1 cases fail against fb5a045's base/tk/qt. On Tk, "reconnecting" left the rule ink, the health line empty, the station line empty and the Run toggle live.
NOTE: On `lost`/`reconnecting`, the view shows "Link lost HH:MM:SS, reconnecting…" or "…; not reconnecting". The head rule turns signal and the readouts wear the existing stale mark. Mode toggles and `go` commands are held with the reason "Link lost: wait for it to reconnect" (or "…press Stop, check the cable, then relaunch from Setup"); the stop is never held. A stalled link shows "No position for N s; link up, check the board" in the attention tier, and nothing is held. N comes from `values.position_age`, else the state's `age`; the contract has no stall-duration field, so if A adds one, `_stall_seconds` is the one place to read it. The link's words replace the old device-lost sentence when `link` is present. With no `link` key, the old `devices`-"lost" path is unchanged.
Rail: Tk draws the glyph mark on the model's rail line, in signal or ink. Qt adds the new rail kinds "lost" (the loud square) and "attention" (an open ink square with "!"). The Web lamp is `is-link-lost` / `is-attention`.
Two choices for you to confirm: (1) `go` commands are held along with the mode toggles (e.g. Step, since it would fail on a dead port anyway); (2) "reconnecting" is the danger tier, like "lost".

## V2
STATUS: closed
TEST: test_v2_the_counters_are_one_compact_line, test_v2_a_linked_models_diagnostics_carries_the_counters, test_v2_no_link_no_row, test_v2_without_a_diagnostics_title_the_first_tier_three_section_takes_it (tests/test_view_link.py); test_v2_the_link_counters_are_a_readout_in_diagnostics (tests/test_view_tk.py); Web: test_v4_the_server_serves_the_link_words_and_the_counters_row, test_v4_a_reconnecting_link_is_the_danger_tier_on_the_page (row text)
COMMIT: aea4261
PROVED: all 5 fail against the V1 base (no row, no `link_counters`).
NOTE: One readonly row, "Losses / reconnects / dropped / stalls:" with the value "1 / 1 / 5 / 2; last loss 12:41:07" ("never" before any loss). It goes at the end of the model's own "Diagnostics" section, else its first tier-3 section. It is added by `views.base.with_link_row` to a copy of the schema at build time, when the state has `link`; the model's schema and src/schema.py are untouched, so no schema entry is needed. A model with no port gets no row. Cost: `PanelView._schema()` now reads `controller.state(name)`. It is called at build time only (Tk `_plan`/`_make_section`, Qt `__init__`).

## V3
STATUS: closed
TEST: test_v3_only_a_gamepad_driven_entry_says_its_input_is_held, test_v3_the_focus_change_is_shown_on_every_gamepad_driven_entry_and_cleared (tests/test_view_link.py); test_v3_a_held_input_gate_is_said_on_every_gamepad_driven_entry, test_v3_a_model_launched_while_unfocused_opens_held (tests/test_view_tk.py); Qt widget test under UNVERIFIED
COMMIT: e516c3f
PROVED: all 4 fail against the V2+V5 base. Before, the focus change reached `controller.set_input_focus` and nothing was drawn.
NOTE: `Dashboard._on_focus_change` still forwards the focus unchanged; D-4's timing is untouched. It now also sets `input_held` and pushes it to each model view (`_input_views`: Tk and Qt override it). Every entry whose `devices` has a bound Gamepad (not "unbound" or "closed") says "Input held: window not focused" in the attention tier, and nothing is greyed. A model launched while the window is unfocused opens held. Tk asserts on the view's own computed `notices` and its `_health` label and marks, never on widget iteration. The gamepad-driven test keys on the device class `Gamepad` (`views.base.INPUT_DEVICES`); the state has no generic "input device" flag, which is a possible MODEL_CONTRACT addition for the lead. Web: nothing (it never gates).

## V4
STATUS: closed
TEST: test_v4_browser_silent_tells_the_operator_how_to_recover (tests/test_view_web_watchdog.py); test_v4_the_server_serves_the_link_words_and_the_counters_row, test_v4_a_model_without_a_port_has_no_link_words, test_v4_a_reconnecting_link_is_the_danger_tier_on_the_page, test_v4_a_stalled_link_is_the_attention_tier_on_the_page, test_v4_a_well_link_says_nothing (tests/test_view_web_dashboard.py; headless Chrome through the existing `_browse` harness)
COMMIT: 6c538f1, f0ab36a (a follow-up, so V4 is two commits: the stalled rail line had kept the signal glyph)
PROVED: 5 of 6 fail against fb5a045's server.py, app.js and styles.css. test_v4_a_well_link_says_nothing passes on both, as it should.
NOTE: /api/state gains `link_words` {name: {tier, line, down, reason, counters, attr}}, computed from views.base. app.js guards an absent `link` (`linkWords` null).
- Down: the card is `is-lost` (signal rule, "Connection lost" badge, muted readings); modes and go commands are held with the link's reason; the stop is never held.
- Stalled: `card-alert.severity-warning`, glyph in the warning ink.
- Rail: the line reads "Fake probe: Link lost …", with `severity-warning` for the attention tier, and the lamp is `is-link-lost` / `is-attention`.
- Browser Silent now ends: "If this tab is in the background, the browser may be throttling or sleeping it: keep the station in its own window."
- `linkHolds` in app.js mirrors `views.base.link_holds`, the way `gateReason` mirrors `gate_reason`.
- test_gating_covers_every_element_including_entries (test_view_web_client.py) was updated to the new literal (`... && !held)`), with the reason in the test.
Captures (headless, 1400 and 900 wide, Overview): mvc-refactor/handoff/shots/link-views_{before,after}_{reconnecting,stalled}_{1400,900}.png. The "before" shots are fb5a045's app.js/styles.css served against the same state, with the new server additions disabled.

## V5
STATUS: closed
TEST: test_v5_a_worker_publishing_never_waits_for_the_tk_thread, test_v5_marshalled_calls_keep_their_order_across_threads (tests/test_view_tk.py)
COMMIT: 8cc97f7
PROVED: test_v5_a_worker_publishing_never_waits_for_the_tk_thread fails against fb5a045's tk.py: "the publisher waited for the Tk thread". It uses a root whose `after` from another thread blocks until the Tk thread is free (tkinter's Tkapp_ThreadSend semantics), and the worker is `events.warn("Power Down Not Supported", …)` through the real bus. The ordering test passes on both; it is a regression guard.
NOTE: `TkDashboard._marshal` puts every call on one `queue.SimpleQueue`. Off the Tk thread it only puts and returns. On the Tk thread it also schedules an immediate drain. `_on_inbox_tick` drains every 50 ms from `open()` and is cancelled in `close()`. The Tk thread is the thread that built the dashboard. "Marshal Failed" stays debug on purpose: a warn would re-enter `_marshal`, and the call is already queued. Not done, because the files are outside my write set (lead): SF-4's other two suggestions, moving `_report_no_coil_kill` out of `_halt_hardware` and never publishing while holding `_mode_lock` (src/model/probe.py). The Qt view already marshals with a queued signal.

## GATE
Fast (`-m "not qt"`, STATION_NO_WINDOWS=1): 3219 passed, 12 skipped, 1 xfailed (base 3184 passed; +35 new, EXIT 0).
Golden wire: 78 passed.
Flake seen under load (load average ~66 from parallel agents): test_f_a_hidden_tab_keeps_checking_in failed 1 run in 3 on my tree, with heartbeat_age 5–10 s measured after browser teardown. It passed on fb5a045's app.js the one time I ran it there. One Chrome launch timeout in test_a_disclosure_leads_focus_straight_into_what_it_opens passed on rerun.

## UNVERIFIED
Qt-marked, written but not run (never run the Qt pass), all in tests/test_view_qt_widgets.py:
- test_v1_a_reconnecting_link_turns_the_entry_signal_and_holds_the_modes
- test_v1_a_stalled_link_is_the_attention_tier
- test_v3_a_held_input_gate_is_said_on_a_gamepad_driven_entry
- test_v2_the_link_counters_are_a_readout_in_diagnostics
The Qt view code (Entry.set_link_tier, QtPanelView._set_notices/_sync_notice, QtDashboard._show_lost/_head_line/_entry_notices, rail "attention" icon) was only checked with `py_compile` and the non-qt pure tests (rail_mark, RAIL_STOP_WORDS, the host fold).
