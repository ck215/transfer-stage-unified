# Audit: gamepad-steps
Checked: 31 behaviours from `main/src/controllerDrive.py`, `stepper_frame.py`, `DC_frame.py`, `chuck_frame.py`, `serialDrive.py`, `README.md:81`, and the stepper/chuck/DC firmware step handlers. Findings: 5 (1 MISSING, 3 NEGATIVE, 1 NEUTRAL).

**Short answer.** The feature is **DEAD in the rebuild on every layout** (Xbox wired/Windows, Xbox Bluetooth, F310, T.16000M) for the Stepper Probe and the Chuck Positioner. The D-pad and the bumpers never reach the wire: every jog packet carries `dpad_LR = dpad_UD = bumpers = 0.0`. D3's claim is **confirmed in SIM**. It was working in `legacy/src`, which the lab ran, so this is a rebuild regression. The triggers are **not** mapped to Z steps anywhere. They drive continuous Z velocity, as on `main`. The fix is small: about 15 lines in `src/model/probe.py`, plus two test fakes and about 8 tests.

## main: the reference behaviour (question 1)

- **Bind table.** `controllerDrive.py:152` has the format `[X, Y, Z+(R trigger), Z-(L trigger), Z+step (LBumper), Z-step (RBumper)]`.
  | Layout (main) | X / Y axes | Triggers (continuous Z) | Bumpers (Z step) | Source |
  |---|---|---|---|---|
  | Linux Xbox USB, F310 | 0 / 4 | axes 5 (R), 2 (L) | buttons 4 / 5 | `:154`, `:169`, `:180` |
  | Windows "Controller*" / Xbox Series X | 0 / 3 | axes 5 (R), 4 (L) | buttons 4 / 5 | `:156`, `:158-164` |
  | Linux Xbox Bluetooth | 0 / 3 | axes 4 (R), 5 (L) | buttons 6 / 7 | `:171` |
  | "T.16000M" (Windows name) | 0 / 1 | virtual 9 (R), 10 (L) = buttons 2, 3 | buttons **7 / 9** | `:175`, `:303-304` |
  | "Thrustmaster T.16000M" (Mint name) | 0 / 1 | virtual 10 (R), 9 (L) (reversed) | buttons **7 / 9** | `:178` |
- **The D-pad is hat 0 on every layout.** `get_hat_edge(0)` (`controllerDrive.py:357-384`) returns **one** non-zero reading per press. A held D-pad does not repeat; you must release and press again. It returns `(-x, -y)`, which is **negated**. `get_button_edge` (`:341-355`) gives one reading per bumper press in the same way.
- **The step is issued by the firmware, from the manual jog packet.** Nothing else issues it. `get_controller_params` (`stepper_frame.py:656-671`, `chuck_frame.py:658-671`, `DC_frame.py:537-551`) fills `dpad_LR`, `dpad_UD`, `LBumper` and `RBumper`, plus the step sizes read **live** from the Entry boxes on every 5 ms tick. `serialDrive.py:160,175-177` packs `bumpers = LBumper - RBumper`. The firmware `runManualMode` (`firmware/stepper_firmware/stepper_firmware.ino:461-466` on main; `:472-477` on mvc-refactor, same code) runs `x_axis.move(dpad_LR*x_step_size)`, `y.move(dpad_UD*y_step)` and `z.move(bumpers*z_step)`.
- **Signs on main.** D-pad right (hat x=+1) sends -1, so X moves by -x_step. D-pad up (hat y=+1) sends -1, so Y moves by -y_step. LB sends +1 (Z by +z_step) and RB sends -1.
- **Triggers.** They give continuous Z velocity: `z = (L+1)/2 - (R+1)/2` (`serialDrive.py:151-159`). README `:81` says "left trigger to raise, right trigger to lower… D-pad and bumpers … discrete … x/y and z". They never produce a step.
- **Modes.** Steps happen only in manual mode, because the packet is sent only by `_manual_mode_loop` (`stepper_frame.py:588-621`). In autonomous mode the pad is not read.
- **DC board.** Its sketch (`high_polling_rate.ino:113-115`) receives the three fields but has **no step handler**. The DC probe never had D-pad or bumper steps, on any generation.

## Findings

### GP-1: D-pad X/Y steps and bumper Z steps never reach the wire (every layout, Stepper and Chuck)            [MISSING]
main:     `src/stepper_frame.py:657,667-669` and `chuck_frame.py:658-670` put one edge per press into the jog packet, and the firmware steps `x/y/z_step` (`stepper_firmware.ino:472-477`).
station:  In `src/devices/gamepad.py:1119-1124`, `_capture_state` latches the edge into `_pending_edges` and **forces `levels[key] = 0` for all four edge keys**. `drain_edges()` (`:1141`) has no caller anywhere in `src/`: grepping `src` for `drain_edges` finds only its own definition and a docstring at `:1132`. `Probe._axis_state` (`src/model/probe.py:899`) returns `gamepad.levels` only. `_jog_loop` (`:683-684`) sends those levels, so `_jog_bytes` (`:645-660`) always packs `dpad_LR = dpad_UD = bumpers = 0`.
  - Legacy worked. `legacy/src/controller/gamepad.py:640-664` `get_mapped_state()` merged `drain_edges()` into the levels, and `legacy/src/model/probes.py:1207` called it every jog tick. The rebuild dropped `get_mapped_state` and never replaced the merge.
consequence: In manual mode, pressing the D-pad or LB/RB does nothing on the Stepper Probe or the Chuck Positioner. Fine positioning by a known step size, which the README advertises, is gone. The operator can only jog with the sticks and triggers. Coarse-then-fine alignment is now stick-feathering. The encoder is **not** at fault: the golden scenarios `stepper.jog.dpad_left_right`, `dpad_up_down`, `bumper_left` and `bumper_right` (`tests/golden/probes.json`) pin correct bytes, but they feed `_send_jog` a literal dict and bypass the Gamepad.
evidence: SIM `scratchpad/rb-gamepad-audit/sim_dpad.py` wires the real `Gamepad` (the suite's fake SDL) to a real `StepperProbe`/`ChuckPositioner` (the suite's `FakePort`), with `probe.open()` and `set_mode("manual")`. It holds each input for about 120 ms and decodes every 42-byte packet. Output is in `out_unpatched.txt`.
  - Xbox/linux Stepper: D-pad right/left/up/down and LB/RB each gave 5-6 jog packets with **0** carrying a step field. LT gave z=+1.0 and RT gave z=-1.0.
  - Xbox/win32 Chuck: same result, 0 step packets.
  - T.16000M/win32: hat and buttons 4/5/7/9 gave 0 step packets. Throttle buttons 2/3 gave z=∓1.0.
  - Same script with `--patched` (an in-memory merge of `drain_edges()` into the levels, no repo edit), output `out_patched.txt`: every D-pad direction and LB/RB gives **exactly one** step packet per press, then zeros. Example, D-pad right on the Stepper: `aa01 00000000 00000000 00000000 0000803f 0000803f 0000803f 0000803f 00000000 00000000 0000c843`, which decodes to x/y/z_step = 1, dpad_LR = +1.0, speed 400. The layout is identical to golden `stepper.jog.dpad_left_right` (dpad_LR `0000803f` at bytes 30-33).
confidence: high

### GP-2: T.16000M Z-step buttons moved from 7/9 to 4/5            [NEGATIVE]
main:     `controllerDrive.py:175,178` binds LBumper to **button 7** and RBumper to **button 9** under both T.16000M names.
station:  `src/devices/gamepad.py:398`, `_T16000M` `"bumper_left": (4, 7), "bumper_right": (5, 9)` means "the first index present". A 16-button T.16000M always has buttons 4 and 5, so the 7/9 fallbacks are never reached. The file's own comment says so (`:340-342`, GAMEPAD-12).
consequence: Once GP-1 is fixed, a T.16000M operator who presses main's Z-step buttons (7/9) gets nothing, and two other base buttons (4/5) step Z instead. The operator cannot find a Z step they can see working, and pressing buttons 4/5 steps Z unexpectedly.
evidence: SIM `out_patched.txt`, T.16000M: button 4 gives bumpers +1.0 and button 5 gives -1.0. Buttons 7 and 9 give 0 step packets.
confidence: high for the mapping. Which physical buttons the lab wants is **bench (B1)**.

### GP-3: Step size cannot be changed while stepping in manual (D14 still open)            [NEGATIVE]
main:     `stepper_frame.py:663-665` reads `entry_x/y/z_step.get()` on every 5 ms manual tick, so a new step size applies to the next D-pad press without leaving manual.
station:  `src/model/probe.py:1011-1021`, `_gated_param`, refuses a write in manual mode.
consequence: To change from a coarse step to a fine step (the typical use of D-pad stepping), the operator has to leave manual. That de-energizes the coils (D-2), and they must then re-enter. D14 is still open.
evidence: SIM with `make_probe()`, `set_mode("manual")`, then `x_step = 5` raises `Refused: X Step Size cannot be changed in manual mode. Leave manual mode to edit it.`
confidence: high

### GP-4: T.16000M throttle-button Z drive is ±1 (full speed), not main's ±0.5; one Z direction for both OS names (D10)            [NEGATIVE]
main:     The virtual axes 9/10 hold the raw 0/1 button value (`controllerDrive.py:303-304`). The remap `(v+1)/2` then gives 0.5 at idle and 1.0 when pressed, so the net Z is ±0.5 (`serialDrive.py:155-159`). The Mint name also swaps the R/L sides (`:178`).
station:  `gamepad.py:1058-1059` `axis2x` maps 0/1 to -1/+1, so the net Z is ±1.0. One row covers both names (`:424-431`).
consequence: This is continuous Z, not stepping, but it is the owner's "triggers" concern on this pad. On a T.16000M, Z jogs at twice main's speed, and on Linux Mint the direction is reversed relative to main. **No trigger or throttle feeds a step field on any layout.**
evidence: SIM `out_unpatched.txt`, T.16000M: button 2 gives z=-1.0 and button 3 gives z=+1.0.
confidence: high for magnitude. Direction per OS is bench (B1).

### GP-5: D-pad step direction is the opposite of main's (unnegated, same as legacy)            [NEUTRAL]
main:     `controllerDrive.py:384` `return (-out_x, -out_y)`, so D-pad right sends dpad_LR=-1 and D-pad up sends dpad_UD=-1.
station:  `gamepad.py:1043-1046` passes the hat through unnegated (GAMEPAD-13), so right sends +1 and up sends +1. `legacy/src/controller/gamepad.py:119-120` was also unnegated, so the lab ran this sign from Aug 26 to Sep 22.
consequence: Once GP-1 is fixed, an operator who knows `main` sees D-pad right step X the other way and D-pad up step Y the other way. The operator is not worse off than under legacy, but it is a surprise relative to main. Note the stick X is negated in firmware (`stepper_firmware.ino:302`, `manual_x_value = -1*x_axisStatus`) and the D-pad X is not, so D-pad and stick consistency depends on this sign.
evidence: SIM `out_patched.txt`: hat (1,0) gives dpad_LR +1.0 and hat (0,1) gives dpad_UD +1.0.
confidence: high for the sign on the wire. Which way the stage should move is **bench (B1, GAMEPAD-13)**.

## Question 3: are the triggers mapped to Z steps anywhere?
No. Every row sends the triggers to `z_axisStatusL/R`, which becomes the continuous `z_axis` float, and sends the bumpers to `LBumper/RBumper`, which becomes the `bumpers` step field:
- `_XBOX_LINUX` (`gamepad.py:380-383`): triggers are axes 2/5; bumpers are buttons 4/5.
- `_XBOX_DEFAULT` (`:384-387`): triggers are axes 4/5; bumpers are buttons 4/5.
- `_XBOX_BLUETOOTH_LINUX` (`:388-391`): triggers are axes 5/4; bumpers are buttons 6/7.
- `_F310_DINPUT` (`:392-395`): triggers are digital buttons 6/7 read as ±1 continuous Z; bumpers are buttons 4/5.
- `_T16000M` (`:396-399`): Z is driven by throttle buttons 2/3; the Z step is on buttons 4/5 (see GP-2).

`_jog_bytes` (`probe.py:642-646`) combines only LBumper and RBumper into `bumpers`. The SIM agrees: LT/RT change only the z field, with the bumpers field at 0.

## Question 4: what the tests cover and what they miss
- **Covered, device side only.** `tests/test_gamepad.py`: `test_a_tap_shorter_than_a_read_interval_is_not_lost`, `test_reading_levels_does_not_consume_edges`, `test_edges_drain_exactly_once`, `test_holding_a_button_produces_one_edge_not_a_stream`, `test_levels_never_carry_edge_keys`. These prove that edges are latched, and `test_levels_never_carry_edge_keys` itself proves the probe's only input (`levels`) can never carry a step. `tests/test_gamepad_layouts.py:88-134,263` checks the mapped dict from `_read_layout`, which comes before the latch.
- **Covered, encoder side only.** The golden jog scenarios and `tests/test_probe_frames.py` (`LEVELS`, line 41) feed `dpad_LR=1`, `LBumper=1` and so on **straight into `levels`**. The fakes do this too: `FakeGamepad.levels` at `tests/test_probe.py:72` and `_NewGamepad.levels` at `test_probe_frames.py:112`. The real Gamepad never produces such levels. The fakes have no `drain_edges` at all, except `_BoundGamepad` in `test_wire_golden.py:260`, which returns `{}`.
- **Missed.** No test pairs a real `Gamepad` with a `Probe`: `Gamepad(` appears only in `test_gamepad*.py`. So nothing checks the join between the device and the model, and that join is where the feature died. Also untested: one step per press at the probe, no repeat on hold, and no stale step on entering manual.

## Question 5: fix specification (for the lead to route; no code was changed)
1. **Where edges are consumed.** Consume them in `Probe._jog_loop` (`src/model/probe.py:671`), once per 50 Hz tick, as the single consumer the `drain_edges` docstring (`gamepad.py:1141-1146`) already names. Extract the loop body into `_jog_tick()` so tests can drive it deterministically.
   - Every tick, **in every mode**, run `edges = self.gamepad.drain_edges()`, guarded like `_axis_state` and treated as `{}` if the gamepad is None or the read raises.
   - If `pumping and not estop`: `levels = self._axis_state()`, then for each key in `("dpad_LR", "dpad_UD", "LBumper", "RBumper")` set `levels[key] = edges.get(key, 0)`, then `_send_jog(levels)`.
   - Otherwise discard `edges`.
   - Draining unconditionally is **required**. SIM `sim_stale_edge.py` / `out_stale.txt` showed that with the naive merge, a D-pad tap made while IDLE sat in `_pending_edges` (still there after 2 s) and fired a step on the **first** jog packet after entering manual: `dpad_LR` went `[1.0, 0.0, 0.0, 0.0]`. As belt and braces, also discard pending edges in `_set_mode` when the target is MANUAL.
2. **How a step is issued.** Through the **jog packet** (fields 8-10 of `<BBffffffffff`), which is exactly what main and legacy did. **Not** through the Step button: that is the ASCII autonomous move frame, and it is allowed only in AUTO. The firmware's `DPAD_STEP` handler is on the manual path (`stepper_firmware.ino:472-477`). `_jog_bytes` already encodes the fields correctly, so the golden bytes do not change. `_send_jog` already counts a non-zero D-pad or bumper as activity for the idle interlock (`probe.py:627-632`).
3. **Debounce and repeat.** No new debounce is needed. `Gamepad._capture_state` latches one edge per transition to non-zero (`gamepad.py:1119-1123`), so a held press gives one edge and a new step needs a release first, which matches main. Presses shorter than a jog tick are kept (the pad polls at 200 Hz). Two presses of the same key within one 20 ms tick coalesce into one step, which is acceptable and no worse than main. `flush_neutral` already clears edges when the gate closes. Firmware limitation, unchanged from main: a press that arrives while a step is still running is overwritten by the next zero packet and dropped.
4. **Tests.** Write them first and prove them failing on today's code; `out_unpatched.txt` is the pre-fix evidence. Use a real `Gamepad` over the `test_gamepad.py` fake SDL, with a real `StepperProbe`/`ChuckPositioner` on `FakePort`, and drive `pad.poll_once()` and `probe._jog_tick()` directly (no sleeps):
   - (a) Parametrize over the layouts (xbox linux, xbox win32, xbox bluetooth linux with bumpers 6/7, F310 dinput, T.16000M) × {hat (±1,0), (0,±1), LB, RB}. Expect exactly one 42-byte packet whose dpad_LR, dpad_UD or bumpers field is ±1.0 and whose step-size fields equal `x_step/y_step/z_step`. Every later packet has 0 in those fields.
   - (b) Hold the input for 50 jog ticks: exactly one step packet. Release and press again: a second one.
   - (c) Press and release entirely between two jog ticks: one step packet.
   - (d) Press while IDLE or AUTO, then enter manual: no step packet.
   - (e) Press with the gate closed, or with the estop latched then cleared: no step packet.
   - (f) LT/RT at full travel: bumpers field 0, z = ±1 (triggers never step).
   - Replace `levels`-borne D-pad values in `FakeGamepad` (`test_probe.py`) and `_NewGamepad` (`test_probe_frames.py`) with a `drain_edges()` that reports edges the way the real device does.
5. **Size.** Small: about 15 lines in `probe.py`, about 20 lines of fake updates, and 6-8 tests. It touches a core file (probe.py), so the lead merges. Suggested route: `worktree-fixer` / `agy`, test first.
6. **Not part of this fix, all bench or owner.**
   - GP-2 (T.16000M 7/9 vs 4/5) and GP-5 (sign) are B1.
   - GP-4 is D10.
   - GP-3 is D14.

## Intentional
- DC Probe D-pad and bumper steps: never existed. The DC sketch (`firmware/high_polling_rate/high_polling_rate.ino:113-115`, the same on main) has no step handler. Not a finding.
- The jog cadence of 50 Hz against main's 5 ms manual loop, and the pad poll at 200 Hz: ruling D-12 (`gamepad.py:484-491`).
- The F310 DInput row (digital triggers as ±1 Z) is a refactor addition. Main bound the F310 with the XInput table only (`controllerDrive.py:180`). It is not a step behaviour; noted only because it is new.

## Unverified
- Which physical direction the stage moves for D-pad right/up, and which T.16000M buttons the lab uses for the Z step. Both need the bench (B1). The SIM proves the bytes only.
- Real SDL hat and button indices per OS. The SIM uses the suite's fake SDL (`tests/test_gamepad.py:32-134`), so it depends on the LAYOUTS values being right. Layout name matching (a DualShock gets the Bluetooth-Xbox row) is B1 / GAMEPAD-11 and was not re-examined.
- Whether legacy had the same stale-edge hazard. Its `get_mapped_state` drained only while pumping. I read this in the code but did not run it.
- The Qt and Tk views were not exercised. The defect is entirely below the Controller, so it applies to every view.

Scratch: `/private/tmp/claude-501/-Users-ianalbinogonzalez-Documents-GitHub-transfer-stage-unified-mvc-refactor/b12e7bce-b4ad-4079-b8fa-512ada187e83/scratchpad/rb-gamepad-audit/` (`sim_dpad.py`, `sim_stale_edge.py`, `out_unpatched.txt`, `out_patched.txt`, `out_stale.txt`).
