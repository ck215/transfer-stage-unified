# Tk, Tier F fix round (rb-f-tk)

Base `77ac52d`. Write set: `src/views/tk.py`, `tests/test_view_tk.py` only. No Tk window was mapped at any point. Every build used `root.withdraw()`, the stop-path probe also withdrew every `Toplevel` at construction, and `deiconify` and `lift` were made to raise. No `screencapture` was run.

## Screenshots
None taken, per the hard rule. The evidence is:
- **Widget trees, from withdrawn builds:** `shots/round3_tk_tree_before.txt` (base) and `shots/round3_tk_tree_after.txt` (this commit).
- **Probe JSON, in `$S = …/scratchpad/fix/tk/`:**
  - `before_{8,12,20,28}.json` and `after_{8,12,20,28}.json`. These come from the auditor's `tk_probe.py`, re-pointed at a worktree (`probe.py`) and measuring the live discs.
  - `stoppath_{12,28}.json`, from `probe_stop_path.py`. It covers the band, the stop key, a refusal at its control, and a lost serial port.

The auditor's measurements, before and after (real Tk, Aqua, withdrawn):

| font | disc (before → after) | "Clear" fits the main disc | mini disc (before → after) | "Clear" fits the mini disc |
|---|---|---|---|---|
| 8 | 64 → 64 | yes → yes | 34 → 35 | yes → yes |
| 12 | 64 → 64 | yes → yes | 34 → 44 | **no → yes** |
| 20 | 64 → 85 | **no → yes** | 34 → 62 | **no → yes** |
| 28 | 64 → 118 | **no (88 px in 56) → yes** | 34 → 82 | **no → yes** |

| idle, per second | before | after |
|---|---|---|
| `_Mushroom.draw` | 24 | **0** |
| `_paint_command` | 47.5 | **0** |

Other before → after:
- Setup keeps ticking while collapsed: `[True, True]` → `[True, False]`.
- Tab focus colour: `#1f242b` (the strip's own colour) → `#ece9e2` (ink).
- Tab selected after a Models-menu reopen: Red Percent → Stepper Probe.

## Changed
| Row | Defect | What was done | tk.py |
|---|---|---|---|
| F1 | Every `needs_ack` event opened an app-modal `messagebox.showerror`, and the stop took no click while one was up. | Errors now go to a band (`_build_alert_band`) packed `after=` the stop bar, directly above it. It is never placed over anything and never a Toplevel. It lists every waiting error with its source and the word "Error" beside a signal lamp, and one **Acknowledge** clears them all and returns focus to the stop. Confirmations are `_ConfirmDialog`, a Toplevel with **no grab**, so the stop disc and its key work while it is open. The one-at-a-time guard declines a second question. `messagebox` is no longer imported. | 2812, 3283, 696 |
| F7 | The discs were a fixed 64 px and 34 px. | `_Mushroom.diameter_for(face_step, floor)` grows each disc from its floor until both faces, with their line height, sit inside the highlight arc. The canvas holds the pulse and the focus ring. The main face is `size(1)` and the mini face is `size(-1)`. | 792, 843 |
| F17 | The confirmation defaulted to Yes. | Focus starts on No. Return (unless the operator has tabbed to Yes), Escape and the close button all answer No. | 696 |
| F9 | The stop had no global key, and its focus ring was 1 px. | `Ctrl+.` is bound everywhere, and `⌘.` too on Aqua (both confirmed in the real bind table). The key **only stops and never clears**. It is written on the hint ("Stop every model (⌘.)") and on the disc's tooltip, which opens above the pointer. Return, KP_Enter and Space press the disc. The focus ring is `STOP_FOCUS`, 2 px, in both states. | 121, 2788, 2793 |
| F3 | A lost device went unshown. | The panel reads `state.devices`. A `lost` device turns the grouping rule under the title signal at 2 px, mutes every readout, sets the title to "X (connection lost)", and adds an ink line under the rule: "Connection lost: serial port. Its readings are frozen. Press Stop, check the cable, then relaunch from Setup." The stop bar's station line (ink, signal lamp) names the model and the device. Verified live on SIM with `port._mark_lost` (`stoppath_*.json`). | 2397, 3078 |
| F10 | Refusals were cut off far from their control. | A grid row under each control, or under each table row, is reserved (`_notice_slot`). The refusal is drawn there in ink on a trace-tinted band, wrapped to its container, and scrolled into view when needed. It is cleared on the next success, and on a failure, since that error is now on the band. There is no longer one line at the foot of the panel with no wrap (that line remains only as a wrapped fallback). At 28 pt the Step refusal wraps to 512 of 514 px. | 1578, 2450, 2511, 2539 |
| F14 | Severity was shown by colour alone. | Each log line is a `SEVERITY_MARK` dot, then the severity word, then the text in `SEVERITY_INK`, so an error is ink. The fault reason is ink with a signal lamp beside it. | 3267, 2237 |
| F15 | Long names were truncated. | Combobox values are elided in the middle and keep the tail ("/dev/cu.usbm…90123"). They map back to the full name on selection, the full name is the tooltip, and a colliding label keeps its whole name. Readouts that are one unbroken token (a Run ID) are also elided in the middle. | 1878, 289 |
| F21 | Widgets redrew every tick. | `set_latched`, `_set_flag`, the stop hint, toggles (`_paint_command` key cache) and plots (series+size key) now draw only on a change. The hidden Setup panel `pause()`s and resumes when it is restored. | 877, 1651, 1966, 1217, 3161 |
| F25 | Focus rings, borders and targets were too weak. | `_Ring` gives commands, entries, dropdowns and the chrome buttons a 2 px ink ring with no layout shift. Notebook tabs get `focuscolor` ink and `focusthickness` 2, and `enable_traversal()`. Borders are `INPUT_BORDER`, 3.09:1 on the panel. Targets are ≥ 24 px at 8..28 pt (`_target_pady`). Lamps scale with the text. A reopened tab is selected. `STATION_NO_MOTION=1` skips the pulse. Danger commands render neutral. | 618, 2681, 249, 3115 |
| F23 | Private mixer, colour literals and spacing sums. | `_mix` is deleted and replaced by `theme.mix` everywhere. `_rule()` is replaced by `theme.RULE`, and the well by `theme.WELL`. There are no `PAD/GAP/INSET` sums (a test enforces this), and each spacing is a `SPACE[i]`. `_font(step)` is `theme.size(step)` and `RATIO = theme.TYPE_RATIO`. | header |

## Kept on purpose
- **`filedialog` (save and open) stays native and modal.** It only opens on an operator press, and it does not open for a fault.
- **The region picker still grabs a full-desktop overlay** (its drag needs the grab). The stop key is a `bind_all` binding, so `Ctrl/⌘+.` still stops while the picker is up, and Escape cancels the picker.
- **The global key stops but never clears.** Clearing stays a press on the disc followed by the confirmation.
- **At 28 pt the stop disc is 118 px, so the chrome is 378 px (it was 332).** The stop stays packed before the notebook. A smaller disc at 28 pt would cut "Clear" again, which is F7's defect.
- **The warning command variant stays outlined in trace.** No schema uses it today.

## CORE CHANGE REQUESTS
1. **`src/views/theme.py:47` (`RULE_STRONG`)**
   - Problem: it measures **1.98:1** on SURFACE, so it cannot be the "input borders ≥ 3:1" token the brief names.
   - Change: add `INPUT_BORDER = mix(SURFACE, TEXT, 0.40)` (3.09:1 on the panel, 3.62:1 on the well; equal to `DISABLED[1]`). Alternatively, raise `RULE_STRONG` to 0.40.
   - Current workaround: Tk derives it locally as `tk.INPUT_BORDER` (tk.py:135). Qt and Web need the same value.
2. **Cross-view: the stop shortcut.**
   - Tk uses `Ctrl+.` everywhere and `⌘.` on the Mac, and it stops only.
   - Change: Qt and Web should adopt the same chord and the same stop-only rule, so F9 is one gesture in every view.
3. **`src/views/base.py:74` (`PanelView._run`)**
   - Problem: a `failed` result leaves the previous refusal showing. Tk overrides `_run` to clear it.
   - Change: `elif result.is_failed: self._show_refused("")` in base, so Qt gets it too.
4. **`src/views/base.py`: a view-neutral name for the element whose command is running.**
   - Problem: Tk tracks it as `_acting` for F10, and Qt needs the same.
   - Change: have `_run` and `_on_entry_commit` pass the element to `_show_refused(reason, element=None)`.

## Tests
- **Fast suite:** 1620 passed (base 1588, +32 new).
- **Golden gate:** 78 passed.
- **Qt:** not run (not my view, and agents do not run it).
- **Tk tests:** 140. 42 of them fail on base (`$S/tests_on_base.txt`).

Tests updated because the assertion described the old look:
- the command outline: 1 px trace → 2 px ink ring;
- "danger command outlined in signal" → neutral (DS-9);
- the entry's `highlightthickness==1` → the ring's 3:1 border;
- row spacing +1 → +2, for the reserved refusal row;
- `_status` text → a `refusal(view)` helper, since the refusal now sits at its control;
- the event-log tag and colour expectations (F14);
- `showerror` → the alert band;
- the mini disc `== 34` → `>= 34`;
- the stop hint `== STOP_HINT` → starts with it;
- `_setup_button.is_packed` → `_setup_press.frame`.

The `tk_harness` fixture patches `tkmod._confirm` to answer from `confirm_answer`, and `_ConfirmDialog` has its own tests. `messagebox` is patched with `raising=False`.

## UNVERIFIED
- **On-screen look of everything above.** Capture commands for the lead (they map a window):
  ```
  cd /Users/ianalbinogonzalez/Documents/GitHub/transfer-stage-unified/rb-f-tk
  PY=/Users/ianalbinogonzalez/Documents/GitHub/transfer-stage-unified/main/.venv/bin/python
  SP=/private/tmp/claude-501/-Users-ianalbinogonzalez-Documents-GitHub-transfer-stage-unified/2ee6aacc-db86-42fd-aade-d687eaadf08e/scratchpad
  $PY $SP/ui/tk/capture.py $PWD ../rebuild-handoff/shots/round3_tk_after 1400x900     # setup / launched / latched
  $PY src/app.py --tk --font-size 28      # SIM rows, Launch; press Stop; check "Clear" on both discs
  $PY src/app.py --tk --font-size 8       # target heights, ring
  STATION_NO_MOTION=1 $PY src/app.py --tk # latch: face/ring change, no breath
  ```
  Then do the following by hand:
  - Press `⌘.` with focus in an entry, and again with the region picker open.
  - Tab to the stop and to a tab. Both should show a 2 px ink ring.
  - Press Step while latched. The refusal should appear under the Step row.
  - Unplug or `_mark_lost` a port. You should see the signal rule, the muted values, and the stop-bar line.
  - Trigger a Rotator SIM "Stop Not Confirmed". The band should appear, the stop should stay clickable, and Acknowledge should clear it.
  - Clear the latch. The dialog's focus should be on No, and Return should answer No.
- **Does Aqua deliver `Command-period` to a `bind_all` binding, or does the menu bar eat it?** `Control-period` is bound as well.
- **Do `focusthickness` and `focuscolor` draw on the `clam` Tab element in this Tk 9?** The style lookup returns the ink colour, but the ring was not seen.
- **Does a transient, grab-free Toplevel really leave the main window clickable on Aqua?** This is expected from Tk semantics but was not observed.

COMMIT: 59004a7
