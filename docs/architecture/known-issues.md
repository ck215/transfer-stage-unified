# Known Issues

> **2026-09-19 note:** this log predates `audit/` and
> [root-causes.md](root-causes.md). Each open item below is mapped to a root
> cause there. Fix it via that root cause, not locally. Corrections to this
> file:
> - Fixed #2, #5, #7, #14 are **committed** in `046533f`, not
>   "uncommitted, pending batch".
> - Serial-port leak item: reopen does **not** open a second handle on the
>   same port. It builds a model with `port=None` (a silent headless
>   device). The old handle is what leaks. See SERIAL-19 / STEPPER-1.
> - Toggle-desync hypothesis: the "transient falsy gamepad" window cannot
>   occur on a successful Tk swap. See GAMEPAD-4 for the traced mechanism
>   (lazy flag flip on a *failed* swap; Web never flips). Root cause: RC-3.
> - #7's generalized `_ensure_pygame_video()` fix treats the aftermath of
>   an unnecessary mid-session `pygame.quit()` (GAMEPAD-2, RC-13).
> - "PySide6 vs Tkinter Polling Threads" was wrong: both poll on the GUI
>   event loop at the same 100 ms. The real divergence is PySide's extra
>   50 ms poll from `_poll_model`. Restated in place below (RC-4 / PYSIDE-9).
> - Fixed #1 cited `addb0b8` for the Python-side fix. That commit is
>   orphaned — real, but unreachable from this branch after a rebase or
>   amend. The reachable equivalent is `4ffb2e3`, now cited instead.

Living log. Newest first within each status group. Safety-tagged where
relevant (**SAFETY** = could leave physical hardware in a dangerous or
unintended state).

## Fixed this session (2026-09-18)

| # | Summary | Root cause | Fix commit(s) | Files |
|---|---|---|---|---|
| 1 | **SAFETY** — Full Stop / disable never actually cut stepper coil current | Firmware's `'d'` handler set `xUART.toff(2)` (nonzero = still enabled for TMC2209) instead of `toff(0)`; also gated behind a `system_enabled` flag that could be stale. Python side had the same class of bug: `_stop_and_disarm()` only sent `'d'` `if self.system_enabled`, and that flag resets to `False` on model reconstruction while the firmware's own copy persists. | `4ffb2e3` (Python), `1896f25` (stepper_firmware), `8267e21` (chuck_firmware) | `probes.py`, `stepper_firmware.ino`, `chuck_firmware.ino` — **requires reflashing both boards** |
| 2 | **SAFETY**-adjacent — entering manual mode with no controller attached still sent the hardware enable command before the block took effect, firmware end up falsely enabled | `enter_manual()` called `self.enable()` (unconditionally sends `'e'`) *before* checking gamepad presence; the only gamepad check was downstream in `send_manual_mode_command()`, which could only revert the Python-side flag, not un-send the enable | uncommitted, pending batch | `probes.py` |
| 3 | Tkinter entry fields effectively unmodifiable | `DynamicView._poll_model()` had no focus guard (PySide6's equivalent loop does: `not widget.hasFocus()`); every 50ms tick reset the StringVar to the model's last-committed value, erasing in-progress keystrokes before FocusOut/Return could commit them | `12e9d59` | `views/tkinter/view.py` |
| 4 | Tkinter numeric fields commit the *previous* value, one edit-cycle behind | Buttons/toggles render as `tk.Label` (works around macOS Aqua ignoring `bg`/`fg` on real `tk.Button`); Labels don't take keyboard focus, so clicking one never fires `<FocusOut>` on the just-edited Entry — the command reads the model's stale pre-edit value. Confirmed via Temperature Controller's Ramp Rate: 5→shows 10 (prior value), 10→shows 5, 4→shows 10 — consistent one-cycle lag, not corruption. Affects every numeric field paired with any button/toggle, every tab. | `b42c13e` | `views/tkinter/view.py` |
| 5 | Redundant duplicate Sync X/Y/Z controls in PySide6 Red Percent tab | `RedPercentDynamicView` rendered both the schema-driven toggle buttons *and* a hand-built `QCheckBox` row doing the same thing | uncommitted, pending batch | `views/pyside/view.py` |
| 6 | Sync X/Y/Z toggle buttons showed blank/`None` labels, and could silently stop refreshing *every* toggle in the view | `redpercent_system.py`'s schema entries omitted `true_text`/`false_text`; `view.py`'s toggle-append dict had no default for them either (unlike the button's own initial-text default), so a missing key propagated to `widget.setText(None)`. Because `self.toggle_buttons` is one shared list for the whole view iterated in a single loop, this plausibly starved every toggle registered after the first broken one for the rest of the session. | `a7a8eaa` | `redpercent_system.py`, `views/pyside/view.py` |
| 7 | "No video instance"/SDL errors recurring on controller reconnect/rescan/dock-reopen | Three independent call sites (`_initialize_pygame_joystick`, `get_physical_controllers`, and the state left behind after `close()`'s `pygame.quit()`) each assumed the dummy video subsystem was already up without guaranteeing it. First narrower fix (`a7a8eaa`) only covered the first site; generalized fix centralizes into one `_ensure_pygame_video()` helper called at all three points. | `a7a8eaa` (partial), uncommitted generalized fix pending batch | `controller/gamepad.py` |
| 8 | Manual mode silently reverted with no user-facing explanation when no gamepad attached | `send_manual_mode_command` only `print()`ed | `a7a8eaa` | `probes.py` |
| 9 | Focus-area drag-select gave zero on-screen confirmation of what was captured (compounds the Linux "blind" issue, see open items) | `SelectionOverlay.mouseReleaseEvent` only `print()`ed | `a7a8eaa`, **re-fixed in S10** | `views/pyside/view.py` |
| 9b | The #9 fix was a modal `QMessageBox` raised over the frameless always-on-top overlay, so it could open *behind* it and block input with the dialog invisible (PYSIDE-12). It also hung the test suite for three sessions. | S10 draws the captured region beside the button instead, in both desktop renderers, from the `model_attr` the `region_select` composite already declared; wording is `schema.format_region`. The dialog is gone. | S10 | `model/schema.py`, `views/pyside/view.py`, `views/tkinter/view.py` |
| 10 | SMC100 rotator autodetection much slower than other devices | `probe_device_at()` tried two ~4.5s custom-firmware handshake timeouts (which the SMC100 never matches) before the cheap SMC100-specific check | `a7a8eaa` | `app_bootstrap.py` |
| 11 | Font sizes too large on Linux | Hardcoded px values in `style.qss` | `a7a8eaa` | `views/pyside/style.qss` |
| 12 | Dead unused local variable | `pwmPinstate` declared, `digitalRead`'d, never used | `a7a8eaa` | `firmware/temp_controller/temp_controller.ino` |
| 13 | `flash_firmware.py` broken after being moved into `firmware/` | `REPO_ROOT = Path(__file__).resolve().parent` silently became `firmware/` instead of the true repo root once the file moved down a directory, breaking the `src` import path and every `DEVICES` sketch path | `e286c92` | `firmware/flash_firmware.py` |
| 14 | PySide6's window-deactivate gamepad-neutral-flush never executed | `changeEvent` referenced `self.system_manager.models`, which doesn't exist (`SystemManager` only has `active_models`) — raised `AttributeError` on every focus-loss event, silently failing the safety flush | uncommitted, pending batch | `views/pyside/view.py` |

## Open — high priority

- **Swallowed Exceptions in Probing:** `app_bootstrap.probe_device_at()` wraps every serial attempt in `try... except Exception: pass`. If a user launches the app but the serial port is locked by another process (e.g., Arduino IDE) or requires `sudo` (Linux permission errors), it silently fails and reports "Not Found" instead of throwing an actionable error.
- **SAFETY-adjacent, serial-port leak on probe dock close.** `close_device_view`
  (PySide6) checks `hasattr(model, 'disconnect')` before releasing a serial
  port, but `BaseProbe` (Stepper/DC/Chuck) has no `disconnect()` method — so
  `serial_comm.close()` never runs for these three device types on dock
  close, while `open_device_view` then constructs a brand-new `serial()` on
  the same port when reopened. Two live handles on one OS serial port,
  platform-dependent failure mode. See
  [ownership-and-lifecycle.md](ownership-and-lifecycle.md#the-close_device_view-divergence)
  for the full analysis and the two fix-direction options (neither applied
  yet — needs a design decision, not a reflexive patch).
- **Manual/Auton toggle visual state possibly desyncing after a controller
  swap mid-session** (reported live, not yet confirmed via targeted
  logging). Reported sequence: enter manual mode → swap controller device
  via the dropdown → click "Enter Manual Mode" again → toggle behaves as
  though state is wrong, while the underlying firmware polling reportedly
  keeps working. **Hypothesis, not yet proven**: `ControllerPoller.set_controller`
  → `_initialize_pygame_joystick` calls `stop_polling()` and rebuilds the
  joystick binding; if `self.poller.gamepad` is transiently falsy during
  that rebuild window, the 20ms `_route_input` timer's call into
  `send_manual_mode_command` could trip its defensive
  `if self.manual_flag and (not self.poller or not self.poller.gamepad): self.manual_flag = False`
  guard, silently flipping `manual_flag` off mid-swap — which the toggle
  widget would then (correctly) render as "Enter Manual Mode" again, making
  a *subsequent* click behave like a fresh entry rather than what the user
  expected. **Needs targeted logging around the swap window to confirm
  before fixing** — per the project's diagnose-before-patch discipline,
  this is not yet at the "concrete, not plausible" bar.
- **Red Percent tab's implementation strategy has fully diverged between
  PySide6 (schema-driven, mostly) and Tkinter (`RedPercentView`, entirely
  hand-built, doesn't consult `ui_schema` at all).** Individual widget bugs
  can keep getting reported and fixed one at a time here indefinitely
  without ever reaching "complete parity" — see
  [views.md](views.md#redpercentviewtkframe-567-812--the-major-structural-divergence)
  for the specific divergences. Needs a decision on which strategy to
  standardize on, not more one-off patches.
- **PySide6's `SelectionOverlay` (focus-area drag-select) is reportedly
  fully broken on Linux** — the screen goes solid grey with no visible
  selection rectangle, because `WA_TranslucentBackground` on a frameless
  always-on-top window depends on compositor support that many Linux WMs
  don't provide for this flag combination. The 2026-09-18 confirmation
  popup (issue #9 above) is a partial mitigation — the *result* is
  confirmed even if the drag itself is invisible — but the actual drag
  experience is still broken. Tkinter's `-alpha`-based overlay (with an
  on-screen instruction label PySide6's never had) is the more field-proven
  reference; consider a screenshot-backed overlay (draw the selection UI
  over an actual captured frame rather than relying on real window
  transparency) as the platform-independent redesign direction.

## Open — lower priority / needs follow-up verification

- `TemperatureSystem.emergency_stop()` = `stop()` = explicitly sets the P, I, and D parameters to 0 (`vals = ['0', spdelay, '0', '0', '0', ...]`), forcing the PID loop to output exactly 0 PWM instantly. Previously suspected to be a gradual PID-driven cooldown, but source confirms it is an immediate hardware cutoff. Confirmed physically safer.
- **Raw Serial Bypass:** `BaseProbe` (`power_down()`, `run_script()`) and `TemperatureSystem` (`stop()`, `send_settings()`, `close()`) bypass their `serial_conn` wrapper methods entirely and write directly to the raw `pyserial` socket (`self.serial_conn.ser.write`).
- **Coupled Base Class:** `BaseProbe.send_stop_command()` explicitly zeroes out `"slow_speed"` and `"brake_distance"` — parameters that nominally only exist on the `DCProbe` subclass.
- **Dead State:** `RedPercentSystem.__init__` declares five lock/thread state variables (`is_monitoring`, `stop_event`, `thread`, `monitor_thread`, `baseline`) that are completely unread by the rest of the file (which uses `monitoring` and `_monitor_thread` instead). Conversely, `last_logged_red` is referenced and built inside the monitoring thread without ever being declared in `__init__`.
- **Wasted Packet Sends:** `BaseProbe.send_manual_mode_command()` correctly checks if the gamepad dropped out and resets `manual_flag`, but instead of aborting the send, it still writes a full zero-padded manual packet to the firmware on that tick.
- **G-Code Execution Race:** `BaseProbe.run_script()` sets `self.is_stepping = True` without checking if the stage is already actively executing a routine.
- **Redundant Pygame Joystick Initialization:** In PySide6's `get_available_controllers` (`app.py:545`), it calls `js.init()` on each iterated joystick, whereas Tkinter's version just reads the name and skips `js.init()`.
- **Dead Code:** `app.py:3` defines `parse_controller_id()`, but it is completely unused.
- **Dead Code:** `src/lib/toupcam.py` is confirmed unused outside of itself and represents dead code that could be safely deleted.
- **PySide6 vs Tkinter Save Logic Bug:** PySide6 uses `save_to_csv` directly on `self.model.data_log` (`views/pyside/view.py:703`), bypassing `RedPercentSystem.save_log()` (`redpercent_system.py:246-247`) which re-syncs `probe_name` and `probe_tilt_angle` before writing. As a result, edited fields silently save stale values in PySide6, whereas Tkinter correctly saves them by routing through the model.
- **PySide6 vs Tkinter polling rate** *(restated 2026-09-19; the earlier "Tk polls in the UI thread, PySide uses QTimers" framing was wrong — both run on the GUI event loop, and both dedicated timers are 100 ms).* The real divergence: PySide6 *additionally* calls `read_position()` and `poll_status()` from `_poll_model` on its 50 ms render tick (`views/pyside/view.py:402-405`), which Tkinter does not — about 3× the hardware traffic, all on the GUI thread, and unguarded on that path. Root cause: RC-4 (PYSIDE-9).
- `RedPercentSystem`'s background monitor thread
  (`_monitor_colors`) reads/writes `current_red`/`red_change`/`baseline_red`
  with no lock, while the view polls the same attributes every tick from
  the main thread — a plausible unguarded race, not yet confirmed as
  producing an observable bug.
- `RedPercentSystem.available_probes` is populated by reference from
  `system_manager.active_models` at construction/assignment time and never
  re-synced — if a probe model is torn down while still referenced there,
  nothing currently detects or corrects the stale reference.
- PySide6's `file_picker` schema element type is dead code (`elif el_type ==
  "file_picker": continue` — everything after is unreachable). Not
  currently user-visible since no live schema uses `file_picker`, but if
  one ever does, PySide6 will silently render nothing while Tkinter renders
  it fully.
- The Arduino serial `enable()`/`disable()` commands (`'e'`/`'d'`) are
  fire-and-forget — no ACK is read back to confirm the firmware actually
  received and executed them. Already flagged in `probes.py`'s own comments
  as "the serial ACK-verification gap tracked separately." Given issue #1
  above (coils not actually disabling) went undetected for some time, an
  ACK protocol for this specific command pair would have caught it much
  earlier.
- `error-routing.md`'s full audit table — ~20 additional state transitions
  (serial connect success, controller hot-swap, dock open/close, dropdown
  rescan, model reboot, etc.) currently report nothing or print-only to the
  user. Not bugs individually, but a systemic UX gap per the explicit
  "verify port connections, re-establish connections, destroy or construct
  objects — are these alerts providing a clear user experience?" review
  request. See that doc for the prioritized subset and the `__del__`
  caveat (destructor-triggered popups are **not recommended** — interpreter
  shutdown timing risk).
- Two independently-maintained `ErrorPopupManager`/`QtErrorPopupManager`
  implementations (PySide6 vs. Tkinter) with the same truncation/exception-
  formatting logic duplicated, not shared. Small maintainability risk, not
  an active bug.

## Design questions raised but not decided

- ~~**Reconstruct-on-reopen vs. persistent-controller-ownership**~~ —
  **DECIDED 2026-09-19 (owner): persistent ownership.** Closing a tab or
  dock *hides*; the model, its `ControllerPoller` and its `serial`
  connection all stay alive, and reopening shows the existing model rather
  than rebuilding one. Reconstruct-on-reopen was the root mechanism behind
  the video-driver issue and plausibly the controller-swap toggle desync.
  Tracked as D-1 in
  [root-causes.md](root-causes.md#answered-decisions), which lists the
  implementation consequences (including that Tk needs a real re-add path
  and that RC-4 becomes a prerequisite).
- **PySide6/Tkinter parity strategy for Red Percent**: pick one rendering
  strategy (schema-driven vs. hand-built) and port the other tab to match,
  rather than continuing to fix individual widget divergences as they're
  reported.
- **Plotting strategy divergence:** Tkinter's `RedPercentView` plotting is fully implemented locally via `FigureCanvasTkAgg` and runs in a `tk.Toplevel`, while PySide6 opens a separate `PlotDialog` class that handles the `FigureCanvasQTAgg` canvas.

---
*Started 2026-09-18. Update this file as issues are found/fixed — it's the
one place meant to answer "what's currently broken and why" without
re-deriving it from source each time.*

*Consolidation pass (agy, 2026-09-18): Merged candidate findings from `models.md`, `views.md`, `bootstrap-and-entrypoint.md`, `libs-and-web.md`, and confirmed `TemperatureSystem` cutoff behavior.*
