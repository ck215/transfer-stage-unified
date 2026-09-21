# S16 — bench checklist

**Owner only. Never delegated.** This file is the procedure and the record
sheet for the work that can only be done on the physical stage.

It **asks** for values. It does not answer any of them. Every blank below is
the owner's to fill at the bench; an agent filling one in is the failure mode
this file exists to prevent. When a value is measured, write it here *and* in
the code location named beside it, in the same commit.

Route: [progress.md](progress.md) · [plan.md](plan.md) §S16 ·
[safety-pattern.md](../architecture/safety-pattern.md) ·
[root-causes.md](../architecture/root-causes.md) RC-12, RC-2

---

## What is already true before you go

Software-side, at the time of writing, these hold and are pinned by tests:

- **I-5.2** — `emergency_stop()` returns in under 100 ms against a stalled
  transport, for **all three** motion subsystems (probes, rotator, heater).
  The rotator and heater residue closed 2026-09-20.
- The stop contract is *latch first, then dispatch the hardware write to a
  daemon worker with a bounded join* — `ESTOP_RETURN_BUDGET`. The latch, not
  the wire write, is what orders a stop against work already in flight.
- "Stop moving" and "de-energize the coils" are **two independent firmware
  actions**. The 12-field text command and the 42-byte binary struct both
  zero motion and **never touch `toff`**. Only `'d'` sets `toff(0)`.
  `_stop_and_disarm()` sends both, deliberately.

None of that is a substitute for watching the stage actually stop.

---

## A. Safety path first — before anything is allowed to move

Standing rule: *safety paths before feature paths*. Do section A before B, C
or D, on every board, and do not proceed past a failure.

| # | Check | Expected | Pass? | Notes |
|---|---|---|---|---|
| A1 | FULL STOP with the stage **idle**, coils energized | Coil current drops (measure or feel holding torque go slack) | ☐ | |
| A2 | FULL STOP **mid-move**, autonomous mode | Motion ceases, then coils de-energize | ☐ | |
| A3 | FULL STOP **mid-move**, manual/gamepad mode | As A2, and stick input is dead afterwards | ☐ | |
| A4 | FULL STOP while the serial link is **physically unplugged** mid-move | UI returns immediately (<100 ms, no freeze); on replug the probe is latched stopped and does **not** resume | ☐ | This is I-5.2 against a real stall, not a mocked one. |
| A5 | Repeat A1–A4 on each board | Stepper, DC, Chuck | ☐ | |
| A6 | Rotator (SMC100) emergency stop mid-rotation | Returns immediately; rotation ceases | ☐ | Was the last subsystem to get a latch. |
| A7 | Temperature controller stop | Heater output drops | ☐ | |

**If A4 freezes the UI**, stop the bench session and report it — that is
I-5.2 regressing against real hardware, and it outranks everything below.

---

## B. D-8 — web client liveness timeouts (closes WEB-19)

The *kind* of gate was ruled 2026-09-20: **warn at N seconds, FULL STOP at M
seconds, while a mode that can move an axis is engaged**. An idle-but-armed
probe is deliberately left alone.

The **values were never ruled.** The shipped numbers are placeholders chosen
to be obviously safe and never measured against a real client on the real lab
network. They stop a physical stage.

Land measured values in **`src/model/probes.py`**:

```
WEB_CLIENT_WARN_TIMEOUT = 5    # s (N) — PROVISIONAL
WEB_CLIENT_STOP_TIMEOUT = 15   # s (M) — PROVISIONAL
```

### Measure first, choose second

| # | Measurement | Value | How |
|---|---|---|---|
| B1 | Normal heartbeat interval, browser focused, lab network | ______ ms | Watch the client heartbeat over ≥2 min of ordinary use. |
| B2 | Worst-case gap during normal use (tab switch, dialog, GC pause) | ______ ms | Same run; take the max, not the mean. |
| B3 | Gap when the laptop lid closes / machine suspends | ______ s | This is the case the gate exists for. |
| B4 | Gap on a deliberate Wi-Fi drop | ______ s | |
| B5 | **N (warn)** — comfortably above B2, below B3 | ______ s | Must not fire during ordinary use, or operators learn to ignore it. |
| B6 | **M (FULL STOP)** — above N, short enough to matter | ______ s | An unattended energized stage is the hazard being priced. |

Then: with motion active, close the browser tab and confirm the warn fires at
N and the FULL STOP fires at M, on the stage. Record: ☐ warn ☐ stop

When both are set and observed, remove the `PROVISIONAL` comment block in
`probes.py`, move **WEB-19** to `closed` with a verification note naming this
bench run, and record the values in [progress.md](progress.md) under D-8.

---

## B2. Two things found on 2026-09-21 that change how you run section B

Both are now built; what is left in each is yours to measure.

### B2.1 — Clearing a latched FULL STOP (MANAGER-22, closed)

Each device now has its own **Clear FULL STOP** button, with a confirmation
dialog, in all three frontends. You no longer need to restart between
liveness measurements: when the gate latches a device, clear it on that
device and carry on. Other devices are unaffected.

  Did Clear FULL STOP work on every device you latched?   yes / no
  Anything about it that slowed the run down? ........................

### B2.2 — D-8 now covers the heater and the rotator (D-8a, WEB-23)

You ruled **heater and rotator too**. Both now have the same gate as the
probes (`src/model/client_liveness.py`): nothing happens until a web client has
checked in, nothing happens while the device is idle, and past M seconds of
silence the device is FULL STOPped and latched.

"Active" means: **heater** — a nonzero setpoint has been *sent* and not since
stopped (a number typed into the box doesn't count); **rotator** — a move is in
flight, or the stage last reported Moving or Homing.

The four values are **placeholders**:

| Device | N (warn) | M (FULL STOP) | Where |
|---|---|---|---|
| Heater | 10 s | 30 s | `TemperatureSystem.WEB_CLIENT_*_TIMEOUT` |
| Rotator | 5 s | 15 s | `RotatorSystem.WEB_CLIENT_*_TIMEOUT` |

**Heater: check this before you pick a number.** `app.js` stops sending
heartbeats while its tab is hidden. So the heater's M is also how long
someone can switch to another browser tab while the heater is running before it
stops. Measure a realistic tab switch (section B, row B2) before you set it.

  Heater  N ........ s   M ........ s    observed: ☐ warn ☐ stop
  Rotator N ........ s   M ........ s    observed: ☐ warn ☐ stop

When all four are set and observed, remove the `PROVISIONAL` markers in both
files and move **WEB-23** to `closed` with a note naming this bench run.

---

## C. RC-12 — the (controller, platform) table

Four findings resolve into **one data table**, not four patches. Per plan
§S16: the result is encoded as one table
(`name, platform → axis map, sign, deadzone`) with an **explicit allowlist
that rejects unknown pads**, and the tests assert the table — never behavior
guessed from a mock.

Do not fix these from the audit text. Every one of them is a code difference
that is *verified*, paired with a hardware truth that is *not*.

### C1 — GAMEPAD-13, D-pad sign. Do this first; it is the one that moves the stage wrong.

`main` negated both hat axes; the refactor passes them unnegated. The commit
that changed it cited a test that is a **mock written during the refactor**,
not a physical check.

| Input | `main` moves stage | this branch moves stage | Which is correct? |
|---|---|---|---|
| D-pad **right** | ______ | ______ | ______ |
| D-pad **up** | ______ | ______ | ______ |

Encode the answer as **one named constant with a comment**, not as scattered
negations at the five call sites that currently carry the sign.

### C2 — GAMEPAD-12, T16000M

One class currently serves both reported names. `main` distinguished them:

| pygame name | `main` binds `[X, Y, Z+(R), Z−(L), LBump, RBump]` | this branch | Correct on hardware |
|---|---|---|---|
| `T.16000M` | `[0,1,9,10,7,9]` | `z_l=10, z_r=9`, bumpers from buttons **4/5** | ______ |
| `Thrustmaster T.16000M` | `[0,1,10,9,7,9]` | same class, same binds | ______ |

Note the Z direction is **swapped between the two names** in `main`, and the
branch implements only the Windows-name variant — so on Linux/MINT the
throttle Z is expected to be inverted. Confirm on the stage.
Also confirm: are the bumpers buttons **4/5** or **7/9** on the physical pad?
______ (the 4/5 fallback to 7/9 is dead code today, because 4 and 5 exist.)

Virtual axes also changed from raw `0/1` to a `2x−1` remap (idle is now
**−1.0**, not `0.0`). Confirm that is what the firmware expects: ______

### C3 — GAMEPAD-11, the allowlist

`get_gamepad_wrapper` matches **substrings**, so a DualShock reporting
`"Wireless Controller"` is silently handed an Xbox layout, and on Linux any
device with `GUID[1:2] == '5'` is treated as Bluetooth-Xbox.

Which pads must the rig actually accept? List exact `joystick.get_name()`
strings **per platform** — read them off the rig, do not guess:

| Exact name (as pygame reports it) | Platform | Wrapper class | Verified |
|---|---|---|---|
| ______ | ______ | ______ | ☐ |
| ______ | ______ | ______ | ☐ |
| ______ | ______ | ______ | ☐ |

Everything not on this list must raise `ValueError`, as `main` did. The
substring rules survive only as documented aliases *of a listed name*.

### C4 — GAMEPAD-14, deadzone

**Re-verified against the code on 2026-09-21, because the audit text has
partly gone stale — read this, not GAMEPAD-14's "Actual behavior" line.**

- The audit says the deadzone is **applied twice**. It is not, any more.
  `_apply_deadzones` has exactly one call site (`_capture_state`), applying
  it once on the way into `_levels`. That half of the finding is fixed.
- What **is** still true: the deadzone is a hard-coded `0.12` for **every**
  wrapper. There is no per-wrapper `DEADZONE`, and the T16000M's intended
  `0.03` still does not exist anywhere — in `main` it was a function-local
  assignment that never took effect, and it was never reinstated here.
- Also still true: the activity/log threshold in `_read_hardware_changes`
  is `0.1`, while the value actually sent to hardware is deadzoned at
  `0.12`. So a stick between 0.10 and 0.12 still logs "Axis changed" and
  fires `touch_activity()` while sending **zero** — motion that is reported
  and not commanded.

The remaining fix shape: **one `DEADZONE` per wrapper class**, still applied
in the single place it already is. Only the numbers are yours:

| Wrapper | Deadzone | Trigger snap | Notes |
|---|---|---|---|
| Xbox / BluetoothXbox | ______ | ______ | |
| LogitechF310 | ______ | ______ | |
| T16000M | ______ | ______ | `main` intended 0.03 but the assignment was function-local and never took effect — so 0.03 has never actually run, on either branch. |

Decide separately whether the log threshold should follow the deadzone so the
two stop disagreeing: ______

Judge these by **low-speed fine-jog feel on the stage**, which is the thing
the double-deadzone costs.

---

## D. D-7 — firmware protocol v2

Ruled **adopt** 2026-09-21. **Requires reflashing every board.** Do this
last: it invalidates A–C if done first, and C's answers are about the host
side.

Four boards, two toolchains (`firmware/flash_firmware.py`):

| Device | Sketch | Board | Reflashed |
|---|---|---|---|
| Stepper Probe | `firmware/stepper_firmware` | mega | ☐ |
| DC Probe | `firmware/high_polling_rate` | mega | ☐ |
| Chuck Positioner | `firmware/chuck_firmware` | mega | ☐ |
| Temperature Controller | `firmware/temp_controller` | teensy | ☐ |

v2 carries, per plan §S16 item 2:

| # | Requirement | Done |
|---|---|---|
| D1 | Versioned identity reply | ☐ |
| D2 | ACKs for `e`, `d` and stop | ☐ |
| D3 | `e` and `d` handlers **on the DC board** | ☐ |
| D4 | Implement **or delete** `k` — no firmware handles it today | ☐ |
| D5 | Host-liveness timeout on the temperature board | ☐ |
| D6 | `serial.__init__` takes `expected_type` and rejects a mismatched board | ☐ |

**D2 is what unblocks SERIAL-10's mis-parse half** — it needs a wire
terminator on a stop path that only v2 carries. Until D2 is flashed and
observed, SERIAL-10 stays open.

After flashing, **re-run section A in full.** Reflashing changes the stop
path; the stop path is verified last as well as first.

---

## Closing the bench session

1. Fill every blank above, in this file, and commit it.
2. Land each measured value in the code location named beside it, in the
   same commit as the row it closes.
3. Move the rows in [progress.md](progress.md)'s ledger — WEB-19,
   GAMEPAD-11/12/13/14, SERIAL-10 — each with a verification note naming
   **this bench run and its date**, not a test name. A bench verification is
   a legitimate closure note; a guess is not.
4. A check that failed is recorded as failed, with what was seen. A partly
   verified row stays `open (partly closed: ...)`. Rounding a partial up to
   `closed` is the one thing this branch has consistently refused to do.
