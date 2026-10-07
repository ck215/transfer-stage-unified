# tk.md — station/views/tk.py

Branch `rb-tk`, worktree `rb-tk`, commit `f10b702`.
Write set honoured: `station/views/tk.py`, `tests/station/test_view_tk.py`.
Nothing else touched. Not pushed.

## DONE

Every member in the stub is implemented; nothing is left `NotImplementedError`.

**`ClosableNotebook(ttk.Notebook)`** — `__init__(master, on_close_tab=None)`,
`close_tab(index)`, `_on_press` / `_on_drag` / `_on_release` (drag to
reorder), `_on_middle_press` (click to close). `on_close_tab` is the public
callback attribute (was `on_close_tab_callback`, which nothing ever
assigned); when it is unset the notebook falls back to `forget`.

**`TkPanelView(PanelView)`** — `__init__(master, controller, name, panel=None)`
plus a `_make_<type>` for all thirteen `schema.ELEMENT_TYPES`:

| type | widget |
|---|---|
| `readonly` | label + `StringVar`, role-coloured when the role is not neutral |
| `entry` | `Entry` + validator from `value_type`, Return/FocusOut commits |
| `button` | `Label` styled as a button (Aqua ignores bg/fg on `tk.Button`) |
| `toggle` | same, text and colours from `theme.toggle_colors` |
| `dropdown` | `ttk.Combobox` + a ⟳ refresh control |
| `region_select` | button + drag overlay + a label showing `format_region` |
| `file_save` | `asksaveasfilename`, run, copy what the model wrote |
| `file_open` | `askopenfilename` → `args=(path,)` |
| `plot` | `Canvas` polyline, no matplotlib |
| `image` | `PhotoImage` from base64 PNG, reference kept |
| `indicator` | lamp, colours from `theme.toggle_colors` |
| `log_stream` | read-only `Text` |
| `internal` | renders nothing |

Also `_validate_entry` (was `_is_valid_float`), `_role_colors`,
`_redraw_plot`, `_refresh_log`, and the base's hooks: `_read_entry`,
`_entry_is_dirty`, `_set_text`, `_set_on`, `_set_data`, `_set_enabled`,
`_set_stale`, `_confirm`, `_show_refused`, `_apply_theme`.

**`TkDashboard(Dashboard)`** — `__init__(controller, setup)` builds the
window; `open()` adds the Setup tab first (a `TkPanelView` over
`panel=setup`), subscribes, gives every already-open model a tab, starts the
tick and enters `mainloop()`; `close()` unsubscribes first, then tears the
widgets down. Plus `_build_event_panel`, `_build_models_menu`,
`_on_model_toggled`, `_on_tab_close`, `_show_event`, `_show_popup`,
`_marshal`, `_confirm`, `_add_panel`, `_remove_panel`, `_on_window_focus`,
`_sync_stop_button`.

Private additions (allowed by the stub header): `_RegionPicker` (the drag
overlay), `_windowing_system`, `_close_tab_button`, `_tcl_error`.

### Decisions the lead should know about

1. **`open()` blocks.** It returns when the window closes, so
   `app.launch()` is `view(controller, setup).open()` for all three views
   rather than "open, then find the toolkit's loop". Under the test harness
   `mainloop()` is a no-op, which is what lets the dashboard be tested.
2. **`open()` does not force a teardown after the loop returns.** The exit
   paths are `WM_DELETE_WINDOW`, `::tk::mac::Quit`, and
   `Controller._hook_exit` (atexit + SIGINT/TERM/HUP), which already exists
   in the frozen core. An unexpected loop exit writes one `events.debug`
   line. **`app.launch()` must call `Controller._hook_exit()`** — if it does
   not, VIEW-TKINTER-8 is back and no view can fix it.
3. **A tab close destructs.** `_on_tab_close` → `Dashboard.close_model` →
   `Controller.remove` (estop, close, drop). The Setup tab is not closable.
4. **Image data commands are cached for 1 s** (`IMAGE_REFRESH_MS`) in an
   override of `_call`. See CORE CHANGE REQUESTS.

## TESTS

`tests/station/test_view_tk.py` — 63 tests, marked `tk`, no window opened,
no real serial port, no gamepad. The harness is `tests/conftest.py`'s Tk
stand-in (it is what makes `class ClosableNotebook(ttk.Notebook)` safe to
import) with richer widget classes patched onto the module globals for the
duration of each test. The command channel is **not** mocked: `DemoPanel` is
a real `station.panel.Panel` carrying one of every element type, so
`Refused`, `NeedsConfirm`, `Result` and input validation are the real ones.

```
python3 -m pytest tests/station -q -p no:cacheprovider
116 passed in 0.29s
```

(53 of those are the pre-existing `test_architecture.py`; 63 are new.)

## MUST-SATISFY

The stub carried no MUST SATISFY block, so this is the set of ledger
findings that live in the code being replaced (`docs/rebuild/carry.json`
clusters homed on the view, plus the VIEW-TKINTER findings whose fix is in
`views/tkinter/view.py`).

| Finding | Ported how | Test |
|---|---|---|
| **VIEW-TKINTER-1** (close = hide; model keeps running; no reopen) | `_on_tab_close` → `Controller.remove`; the Models menu lists `closed_names` and reopens from the remembered config | `test_closing_a_tab_removes_the_model`, `test_the_models_menu_lists_closed_models_to_reopen`, `test_a_model_added_later_gets_a_tab` |
| **VIEW-TKINTER-2** (popup loop dies with the dashboard; later errors lost) | No popup manager: `Dashboard._on_event` is the one policy, the dashboard subscribes in `open()` and unsubscribes in `close()`, and `_marshal` is `root.after(0, …)` guarded | `test_only_a_needs_ack_event_becomes_a_popup`, `test_an_event_is_marshalled_onto_the_tk_thread`, `test_no_popup_once_close_has_begun`, `test_close_unsubscribes_and_closes_the_controller` |
| **VIEW-TKINTER-8** (no exit-time teardown fallback) | `WM_DELETE_WINDOW` and `::tk::mac::Quit` both reach `close()`; `close()` is idempotent; the last-resort hook is the Controller's | `test_the_window_close_button_closes_the_app`, `test_macos_quit_closes_the_app`, `test_close_is_idempotent` |
| **VIEW-TKINTER-9** (focus-out flush defeated; stale snapshot) | `_on_window_focus` → `Controller.set_input_focus`; `focus_get()` distinguishes a child dialog; the Controller iterates live models | `test_focus_gates_input_and_never_stops`, `test_a_child_dialog_taking_focus_is_not_focus_loss`, `test_focus_events_from_a_child_widget_are_ignored` |
| **VIEW-TKINTER-10** (dropdown never re-synced, no live refresh) | Options re-read on a ⟳ control and on a 2 s cadence; `schema.current_text`'s "" keeps an unset value out of the list | `test_dropdown_options_come_from_the_options_command`, `test_dropdown_selection_runs_the_command` |
| **VIEW-TKINTER-12** (hardware I/O on the Tk thread) | The view only reads `Controller.state`; no transport call anywhere in the file. `state["age"] > 1 s` greys the panel instead of the view polling harder | `test_stale_state_greys_the_panel_title` |
| **VIEW-TKINTER-14** (`cget("state")` guard on a widget with no state option; Start enabled without a focus area) | Enablement is tracked per element by `_set_enabled` and checked before any command runs; gating comes from `schema.is_enabled` only, with no per-view special cases | `test_a_disabled_control_does_not_run_when_clicked`, `test_gating_disables_entries_and_commands` |
| **VIEW-TKINTER-17** (view knows model internals; dead widgets; unformatted values) | No `getattr(model, …)`, no hardcoded model names, no injected callbacks; text comes from `Panel.state`, which formats through `Param` | `test_readonly_shows_the_formatted_value`, `test_every_element_type_builds` |
| **VIEW-TKINTER-18** (Aqua button mapping) | `_close_tab_button` resolves the sequence from `tk windowingsystem` instead of hardcoding `<ButtonPress-2>` | `test_a_close_click_on_the_tab_bar_closes_that_tab` (mapping itself: UNVERIFIED) |
| **REDPERCENT-18 / PYSIDE-12** (one drag picker; no modal over the overlay) | `_RegionPicker`: borderless, semi-transparent, topmost, virtual-desktop sized, Escape cancels, <10 px reported on the status line, screen coords out. The captured region is **drawn** (`format_region`), never announced by a modal | `test_region_picker_returns_screen_coordinates`, `test_region_picker_normalises_a_backwards_drag`, `test_a_drag_under_ten_pixels_is_reported_not_captured`, `test_escape_cancels_the_region_picker`, `test_region_select_runs_the_command_with_four_args`, `test_the_captured_region_is_drawn_not_announced` |
| **D-5** (inputs travel with the command) | `PanelView._gather_inputs` reads the widgets; no `focus_set()` anti-fix anywhere | `test_every_entry_travels_with_a_command` |
| **RC-6** (numeric-ness declared, not guessed) | `_validate_entry` reads `value_type`; a cleared box keeps its validator; bounds colour the field and are enforced by the model | `test_keystroke_validation_follows_the_declared_type`, `test_entry_commit_validates_through_the_model`, `test_out_of_range_text_is_flagged_without_blocking_typing` |
| **VIEW cluster** (one theme; toggle state follows the model; no dead widgets) | Every colour and font from `station.views.theme`; a test scans the module's *executable* source (docstrings stripped — they quote the old literals) | `test_no_colour_or_font_literal_in_the_module`, `test_the_theme_is_the_only_palette`, `test_indicator_and_toggle_take_their_colours_from_the_theme` |
| **Refusals reported** | `_show_refused` is a non-modal status line, cleared by the next success | `test_refused_reaches_the_status_line_and_not_a_popup`, `test_a_later_success_clears_the_status_line` |

Diagnostics (Addendum 1): `events.debug` on view open/close, tab open/close,
tab close requested, popup shown, focus gate changes, gate transitions,
staleness transitions, stop-button label changes, region result, panel
build/close, and every swallowed Tk error. Everything inside a loop carries
`every=` (1 s for refresh failures, 5 s for draw failures); `exception=exc`
is passed wherever one was caught. No `print()`; `events.error` is used once
only, for a file-copy failure after a save (a fault the operator must see).

## UNVERIFIED

1. **Display scaling for the region picker.** The overlay is sized from
   `winfo_vrootwidth/height/x/y` and returns `event.x_root/y_root`. On a
   HiDPI or mixed-DPI setup those may be Tk points rather than physical
   pixels, and `mss` (which the Screen device uses to grab the region)
   works in physical pixels. **This needs checking on the station PC with a
   real second monitor**, against a known-size on-screen target. Same
   caveat the Qt `RegionOverlay` carries.
2. **macOS tab-close button.** `_close_tab_button` returns
   `<ButtonPress-2>` on `aqua` and `<ButtonPress-3>` elsewhere, following
   the audit's fix direction ("right-click closes, on every platform").
   Which physical button that is on Aqua is a hypothesis in VIEW-TKINTER-18
   and needs a Mac to confirm. The unit test patches the resolver.
3. **ttk widget styling.** `ttk.Combobox` and `ttk.Frame` do not take
   per-widget colour/font options; they need a `ttk.Style` pass to follow
   the theme. Nothing sets one, so the combobox and the tab strip render in
   the platform's default light colours inside a dark panel. A
   `theme.apply_ttk_style(root)` would be the right home — it is a theme
   change, so I have not written it. Cosmetic only.
4. **No Tk widget was ever instantiated for real.** Every test runs against
   the stand-in, by design (no window may open in the suite). The module
   *imports* cleanly against real tkinter 9.0 and `ClosableNotebook` really
   subclasses `tkinter.ttk.Notebook`, but option names (`insertbackground`,
   `highlightbackground`, `validatecommand`, `-alpha`, `-topmost`,
   `overrideredirect`) are verified by reading the Tk docs, not by running.
   **First bench launch is the test**; every such call is wrapped so a
   `TclError` degrades rather than crashes, and each one logs a debug line
   naming the option that was refused — so a single run of the app with the
   log file open will list anything wrong.
5. **`_entry_is_dirty` while focused** relies on `widget.focus_get() is
   widget`. Under the harness `focus_get` is controlled by the test; on a
   real Tk it returns the focus widget of that widget's toplevel, which is
   what we want, but the "focused" half of the guard is unexercised against
   real Tk.

## CORE CHANGE REQUESTS

**1. `views/base.py`: throttle the data commands in `_refresh`.**
`PanelView._refresh` calls `self._call(element[key])` for every `plot`,
`image` and `log_stream` on every tick — ten times a second. For a series
that is right; for `RedMonitor.figure`, which renders a figure to PNG, it is
a core of CPU spent on a picture nobody asked to be redrawn. I worked around
it by overriding `_call` in `TkPanelView` and caching image sources for 1 s,
but the Qt and Web views will each need the same workaround, which is the
RC-7 shape this base class exists to remove.

```diff
@@ class PanelView:
     REFRESH_MS = 100
+    #: Per element type, the shortest interval between data-command calls.
+    DATA_REFRESH_MS = {"plot": 0, "log_stream": 0, "image": 1000}
@@ def _refresh(self):
             elif kind in ("plot", "image", "log_stream"):
                 key = "source_command" if kind == "log_stream" else "data_command"
-                data = self._call(element[key])
-                if data.is_ok:
-                    self._set_data(element, data.value)
+                if self._data_is_due(element, kind):
+                    data = self._call(element[key])
+                    if data.is_ok:
+                        self._set_data(element, data.value)
+
+    def _data_is_due(self, element, kind):
+        interval = self.DATA_REFRESH_MS.get(kind, 0)
+        if not interval:
+            return True
+        now, last = time.monotonic(), self._data_seen.get(id(element), 0)
+        if (now - last) * 1000 < interval:
+            return False
+        self._data_seen[id(element)] = now
+        return True
```
(plus `import time` and `self._data_seen = {}` in `__init__`.) If this
lands, delete `TkPanelView._call`, `IMAGE_REFRESH_MS`, `_slow_commands` and
`_cached_results`, and the test `test_an_image_is_not_re_rendered_on_every_tick`
moves to the base's own tests.

**2. `views/theme.py`: a ttk style pass.** See UNVERIFIED 3. Suggested
shape only, since it is the theme owner's call:

```python
def apply_ttk_style(root):
    """Give the ttk widgets (Notebook, Combobox, Frame) the same palette.
    ttk ignores per-widget colour options; this is the only way in."""
```
`TkDashboard.__init__` would call it right after building the root.

Neither is blocking. The view works without both.

## NOTES FOR THE LEAD

* **The `_QUIET` set in `panel.py` already covers data commands**
  (`Panel._is_data_command`), so the 10 Hz refresh does not fill the debug
  log. Good — but note it means a *failing* data command is logged as
  `Command Failed` at `events.error` by `Panel.run` on every tick. A model
  whose `series` raises will raise an acknowledged popup ten times a second
  until the dedupe window absorbs it (`DEDUPE_SECONDS = 5`, so it becomes
  one popup and a rising count). That is survivable but worth knowing; the
  Red Percent agent should make sure `series`/`figure` never raise when
  there is no run.
* **`Controller.options(name, command)` raises rather than returning a
  `Result`** (`Panel.options` raises `Refused` directly). Every view has to
  wrap it in `try/except`, which I do. If that is not intentional, it is the
  one call in the view-facing surface that does not follow the Result
  contract.
* **`Panel.state` returns booleans unstringified** for toggles and
  indicators, and strings for everything else. `PanelView._refresh` relies
  on that (it calls `_set_on` for toggle/indicator and `_set_text`
  otherwise), so it is consistent — just flagging that the Web serialiser
  will see a mixed-type `values` dict.
* **`Dashboard.open()` subscribes to `events` before any model tab is
  built**, so an event published during a panel build reaches the log panel
  through `_marshal` and is drawn on the first tick. Nothing is lost, but
  the log panel does not replay events published *before* `open()` — the
  old `ErrorPopupManager.attach_panel` replayed its backlog. If the Setup
  scan publishes before the dashboard opens, those lines will be in the log
  file and not in the panel. A one-line fix in `Dashboard.open()` would be
  `for event in events.since(0): self._show_event(event)` — say the word and
  I will take it in `TkDashboard.open()` instead, but it belongs in the base.
* **The `tk` marker** is declared in `tests/pytest.ini`, so the new file is
  selectable with `-m tk` and excluded by `-m "not tk"` like the old Tk
  tests. It does not need the `qt` marker and never constructs a
  `QApplication`.
