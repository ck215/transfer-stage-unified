# Bugfix plan — post-push sweep

Written 2026-09-23 from a sweep of `station/`, `tests/station/` and
`docs/rebuild/` for swallowed exceptions, unverified constants, stop-path
gaps and dead test scaffolding. Baseline at the time of writing: 1498 fast
tests pass (47 s), 78 golden wire scenarios byte-identical, pushed to
`origin/mvc-refactor` (the `rebuild` branch was fast-forwarded onto it and retired).

Each item names its **route** (direct / `router` / `agy`) up front, per the
delegation rule: decided at plan time, not improvised. Bench items are
owner-only and never delegated.

Ground rule for every code item: **test first, prove the defect against the
pre-fix code, then fix.** No baseline is ever bumped to go green.

---

## Tier A — code defects, fixable now (no bench needed)

| # | Where | Defect | Fix | Route |
|---|---|---|---|---|
| A1 | `station/models/probe.py:471` `_write_stop` | Returns `True` when `self.port is None`, so `_halt_hardware` reports a confirmed stop with nothing written. `Rotator._halt_hardware` (`rotator.py:414`) returns `False` in the same situation. The two models disagree on what a portless stop means. | **Decision first (direct):** a stop that wrote nothing is *unconfirmed*, matching the rotator, unless the owner rules that a closed/portless model's stop is vacuously true. Then align probe to rotator, add `test_a_stop_with_no_port_is_not_confirmed` (probe) and `test_a_stop_with_no_controller_is_not_confirmed` (rotator, currently untested). | direct decision → `agy` |
| A2 | `station/views/web/server.py:704` `_watch_loop` | A raising `_check_heartbeat` is logged at `events.debug` only. The heartbeat watchdog is the one thing that turns a dead browser into `estop_all`; if it is broken, nothing visible says so. | Log at `events.warn` (once per fault, `every=`) and add `test_a_failing_watchdog_check_is_reported`. Consider counting consecutive failures and calling `estop_all` after N, since a watchdog that cannot check is a watchdog that cannot protect. | `agy` |
| A3 | `station/models/red_monitor.py:420` `position_age` | `except Exception: return None` makes a broken probe indistinguishable from "no sample yet". Velocity silently goes missing. | Narrow to the exceptions a missing attribute can raise, log the rest at `events.debug` with `every=`. Test: a source whose `position_age` raises is logged, not hidden. | `agy` |
| A4 | `station/models/plot_data.py:161` sidecar load | Corrupt `_station_meta.json` is dropped silently; the plot renders with no metadata and no message. | `events.warn("Run Metadata Unreadable", path)`; still return the CSV rows. Test with a truncated sidecar. | `agy` |
| A5 | `station/views/web/server.py:527` request body | Any exception maps to "invalid JSON" with no log. A non-JSON failure (encoding, size) is misreported. | Catch `ValueError` for the JSON case; log anything else at `events.debug` with the type. | `agy` |
| A6 | `station/setup.py:526` port probe | `except Exception: return False` hides a real fault on a real port during scan; the port just reads as "not ours". | Keep the `False`, add `events.debug("Port Probe Failed", port, exc)`. The docstring cites SERIAL-17; the branch should say so in the log. | `agy` |
| A7 | `station/events.py:117` log-file write | `except (OSError, ValueError): pass`. If the run log cannot be written, every later diagnostic is lost with no sign. | One-time `sys.stderr` note and a flag so the view can show "logging off". Test: unwritable path → stderr line, no raise. | `agy` |
| A8 | `tests/station/test_wire_golden.py:20-46` | Docstring says the replays are `xfail(strict=False)` and an `AWAITING` dict carries reasons; no `xfail` marker exists, all 78 pass. Dead scaffolding that misdescribes the gate. | Delete the `AWAITING` dict and rewrite the docstring: the gate is strict. | `router patch-plan` → direct edit |
| A9 | `tests/station/test_view_web_client.py` (7 tests) | `skipif(NODE is None)` silently drops the JS client tests where node is absent. On a CI box without node the Web client is untested and the run is green. | Make the skip loud (`pytest -rs` in the verify recipe, or a single always-on test that fails when node is missing on a box that has `STATION_REQUIRE_NODE=1`). | `agy` |

Write set for the `agy` batch: `station/models/probe.py`, `station/models/rotator.py`,
`station/models/red_monitor.py`, `station/models/plot_data.py`,
`station/views/web/server.py`, `station/setup.py`, `station/events.py`, and
their tests under `tests/station/`. One worktree, one branch (`rb-bugfix-a`),
one handoff in `../rebuild-handoff/bugfix-a.md`. Lead re-runs the three
verify commands and hand-drives A1/A2 in the Web view before merge.

## Tier B — bench only (owner; never delegated)

These are the constants and directions the code pins to *today's* value and
that no test can settle. Each has a test that will need its expected value
changed if the bench disagrees, so the fix is "measure, then edit the number".

| # | Where | Question |
|---|---|---|
| B1 | `station/devices/gamepad.py:322-361`, `tests/station/test_gamepad_layouts.py` | GAMEPAD-11 name match (a DualShock silently gets the Bluetooth-Xbox row); GAMEPAD-12 throttle → Z direction; GAMEPAD-13 D-pad right direction; GAMEPAD-14 deadzone 0.12 vs 0.1. |
| B2 | `station/models/heater.py:54-57` | `max_setpoint` 300 °C ceiling, PID/ramp bounds, 31-char frame limit. All PROVISIONAL. |
| B3 | `station/views/web/server.py:562` | Heartbeat warn/stop at 5 s / 15 s (D-8a, WEB-19, WEB-23). |
| B4 | `station/models/probe.py:1081` | D-7: the DC board has no coil kill. Firmware v2 is the only fix; owner-only. |
| B5 | `station/devices/serial_port.py:676` | Stop wait when a stop "feels slow". |
| B6 | `station/devices/smc100.py` | `READ_TIMEOUT_SEC` now bounds a whole line: confirm against the real SMC100. |
| B7 | `station/views/tk.py:111,841` | Which physical button on a Mac; the ttk theme pass. |
| B8 | all views | Region-picker display scaling; achieved Red Percent capture rate (every sidecar records it). |

Deliverable for Tier B is a one-page bench checklist with a blank per row;
the code change afterwards is a number edit plus its test, routed `router`.

## Tier C — hygiene, low risk

| # | Item | Route |
|---|---|---|
| C1 | Scan time: two junk macOS ports get the full handshake (~18 s). A name filter is one line in `Setup.scan_ports`. Owner picks the filter; the line is trivial. | direct after owner ruling |
| C2 | `station/views/tk.py` has 18 bare `except: pass` sites (285, 308, 312, 437, 504, 797, 887, 999, 1121, 1167, 1242, 1254, 1274, 1289, 1296, 1424, 1515, 1668) and ~40 `events.debug`-only swallows. Most are Tk teardown races. Audit each: keep, narrow, or log. | `router review` produces the keep/narrow/log table; `agy` applies it |
| C3 | `station/devices/gamepad.py` silent swallows at 198, 219, 273, 305, 419, 762, 791. Same audit as C2. | same as C2 |
| C4 | `STATUS.md` said 547 commits; `git rev-list --count mvc-refactor..rebuild` says 98. Corrected in this commit. | done |

## Out of scope here

- The second test wave (135 old files, 26 safety tests: `tests/station/TEST_PORTING.md`) is a programme, not a bugfix batch; it stays under STATUS.md item 3.
- Cutover (delete `src/`, merge to `main`) stays under STATUS.md item 4 and follows the test wave.

## Order

1. A1 decision (owner or lead) — it changes what "FULL STOP confirmed" means for the probe, so it goes first.
2. `agy` batch A1–A7, A9 in `rb-bugfix-a`; A8 directly.
3. Lead verification: fast suite, golden gate, Qt suite, screenshot ritual for the Web console with a forced watchdog fault.
4. Bench checklist (Tier B) printed for the next lab visit.
5. Tier C audits when the bench items are back.
