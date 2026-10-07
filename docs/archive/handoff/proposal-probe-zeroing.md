# Proposal: zeroing / centring for the Stepper Probe

Read-only investigation, 2026-09-26. Paths are relative to `mvc-refactor/` unless they start with `main/`. Tags: **VERIFIED (file:line)** means read in the tree; **ASSUMED** means a bench fact I could not check.

## 1. What the hardware is

| Item | Finding | Tag |
|---|---|---|
| Board | Arduino Mega. Uses Serial1/2/3 and pins 22–44, which only the Mega has. | VERIFIED `firmware/stepper_firmware/stepper_firmware.ino:28-32,5-15`; `main/README.md:109,161` |
| Driver | **TMC2209**, three of them, configured over UART (TMCStepper), `R_SENSE 0.11` | VERIFIED `stepper_firmware.ino:2,19,28-32` |
| Chopper mode | Stepper probe: **SpreadCycle** (`en_spreadCycle(true)`). Chuck: StealthChop. | VERIFIED `stepper_firmware.ino:516,525,534`; `chuck_firmware.ino:516-517` |
| Current | 600 mA RMS on every axis (chuck Z is 400) | VERIFIED `stepper_firmware.ino:513,522,531` |
| Microstepping | `microsteps(8)`. The manual says "a full step of 16 microsteps corresponds to exactly 5 microns". **The two disagree**, so one count is 0.625 µm or 0.3125 µm. | VERIFIED `stepper_firmware.ino:506` vs `main/README.md:67` |
| Enable pins | EN pins are driven LOW (enabled) and never touched again. Power is cut by `toff(0)` over UART. | VERIFIED `:494-500,333-336` |
| Stall/DIAG | No DIAG pin is defined and nothing reads SGTHRS, SG_RESULT or TCOOLTHRS. | VERIFIED (grep of `firmware/` finds nothing) |
| ADC / current sense | No `analogRead` anywhere in the stepper firmware | VERIFIED (grep) |
| Encoders, limit/home switches | None on the stepper board. Only the DC board has quadrature encoders. | VERIFIED `high_polling_rate.ino:6-21` |
| Motors, lead screw, hard stops, travel | Nothing in the tree | ASSUMED unknown |

**Side finding.** `Serial2` is the X driver's UART (`:28`), but the firmware also prints debug text on it (`:296,:377`). ASCII lands on the X TMC2209's single-wire bus. Today that is probably harmless, since the driver drops frames with a bad CRC (ASSUMED). It has to be removed before any firmware reads registers over that bus, which StallGuard needs.

## 2. How position is tracked today

- The firmware prints `POS:x,y,z` every 100 ms. The value is AccelStepper's open-loop step counter (`stepper_firmware.ino:556-579`). VERIFIED.
- At boot the counter is set to `test_connection()`, which returns 0 when the driver answers and a non-zero error code when it does not (`:537-539`). A dead driver therefore starts that axis at 1 or 2 rather than 0. VERIFIED (return values ASSUMED from TMCStepper).
- The host waits out a bootloader on every open (`src/devices/serial_port.py:241,518`). The Mega auto-resets on port open, so **every connect or reconnect re-zeroes the counters**. The reset is ASSUMED, as standard Mega DTR behaviour; the wait is VERIFIED.
- `'d'` (`toff(0)`) and `'e'` do not reset the counter (`:323-356`). That is VERIFIED. While power is off the axis is unheld, and moving it then desyncs the counter silently (ASSUMED). The idle interlock sends `'d'` after 300 s (`src/model/probe.py:135,1001-1008`). VERIFIED.
- The model keeps `_position` in memory only (`src/model/probe.py:153,792-847`). Nothing persists. Probes have **no** set-zero or home; `main` calls it "Absolute Position (from boot zero)" (`main/src/stepper_frame.py:111`). Only the Rotator has `home()`, and that goes through the SMC100's own home (`src/model/rotator.py:238-247`). All VERIFIED.

## 3. The wire, and the constraint

Host to board: `e`, `d`, `s`, a 12-field text move frame, the all-zero frame (stop), the 42-byte `<BBffffffffff` jog packet, and `k\n`, which no firmware handles (D-7) (`src/model/probe.py:1-30`). Board to host: `POS:` and `DEV: s`. Every host byte is pinned by `tests/golden/probes.json` and `tests/test_wire_golden.py`.

The distinction that matters:
- A **host-only** feature that builds moves out of the *existing* move frame changes no firmware.
- A feature that needs a **new command** (home, stall readout, switch state) is a firmware change plus a new wire byte. That is an owner decision and a firmware-v2 item alongside D-7, not something the host can hide.

## 4. Safety

The tip works microns from a specimen. A homing move that drives into a hard stop or stalls the motor risks the following:
- **Tip and specimen:** if Z homes toward the specimen, or X/Y home with the tip lowered, the "stop" is the specimen.
- **Lead screw and stage:** a repeated stall preloads the nut, the flexures or the micrometer barrel. On a fine-pitch screw, 600 mA of holding torque becomes a large axial force (ASSUMED; the pitch is unknown).
- **Driver and motor:** a TMC2209 tolerates a stall at 600 mA thermally, but it still heats (ASSUMED).
- **Position:** after a stall the step count is wrong until re-zeroed.

What any homing routine must guarantee:
1. **Latch first.** `home`, `go_to_zero` and `set_zero` call `_guard()` like every motion command (`src/model/base.py:150-157`). `Model.estop` sets the latch and then runs `_halt_hardware` (`base.py:92-118`).
2. **The stop must still land during a home.** `_halt_hardware` sends the zero frame, `'d'` and `k\n` on the priority lane (`probe.py:459-493`). A firmware homing loop must therefore stay non-blocking and keep calling `parseHybridSerial()` on every iteration, the way `runAutoMode` does today (`:601-613`). A blocking `while(!stalled) step();` is disqualifying.
3. **A stopped home invalidates the zero.** A FULL STOP, fault, disconnect or idle interlock during a home marks the zero invalid. It is never left half-set.
4. **Z first, away from the specimen, at reduced current.** X/Y homing refuses to start unless Z is at its retracted home.
5. **Every autonomous run needs an operator confirmation** (`confirmed=True`), as the Rotator's moves do.
6. **Travel is bounded.** Firmware aborts a home after N steps with no trigger, so a failed sensor cannot drive the axis forever.

## 5. Approaches compared

| | Hardware | Firmware / wire | Model / view | Repeatability | Global zero? | Safety | Cost / effort |
|---|---|---|---|---|---|---|---|
| **A. TMC2209 StallGuard4** | Already fitted. DIAG optional; SG_RESULT can be polled over UART. | Switch to StealthChop for the home move, set SGTHRS/TCOOLTHRS, add a `home` command and a done/stalled reply. New wire bytes. | `home_axis`, homed state | Poor at low speed; tuning needed; tens of µm to mm reported [3][4] | Yes, if a rigid hard stop exists | Drives into a stop by design | $0 hardware; days of firmware and bench tuning |
| **B. External current shunt + ADC** | Shunt or hall sensor on the supply, into a Mega ADC | New stall reply | same as A | Worst of all: the chopper regulates coil current, so supply current barely moves at a stall [5] | Yes, with a hard stop | Same as A | $10–30; the least reliable |
| **C. Reduced-current bump** | None, but needs a hard stop | Lower `rms_current` for the move, drive past the stop, back off. New command, or a host-side long move after a current-change byte. | same | About ±1–2 full steps (±5–10 µm at 5 µm/step): the rotor lands at a stall detent (ASSUMED) | Yes, with a hard stop | Deliberate collision, only softened | $0 hardware; small firmware change |
| **D. Home switches** | Optical fork or Omron D2F-class microswitch per axis, into free Mega pins with interrupts or pull-ups | Read the pin inside a non-blocking home loop, add a `home` command and a switch-state reply | `home_axis`, homed state, switch-lamp readout | Optical about 10 µm; mechanical 5–200 µm depending on switch and mount [6][7]; add a slow second approach | **Yes**: same physical point every session | No collision; a switch doubles as an over-travel limit | About $5–20 per axis; a day of mechanics plus about 150 lines of firmware |
| **E. Vision fiducial** | A mark on the chuck; the existing screen capture (`src/devices/screen.py:133`) | None, if the host does centroiding and uses existing move frames | `centre_on_fiducial` loop; crosshair overlay | Sub-pixel centroid × µm/px (ASSUMED 1–5 µm) | Yes, relative to the specimen or chuck; that is arguably the zero that matters | Moves in small confirmed steps; no collision | $0; about a week (calibration, lighting) |
| **F. Software persist + set zero** | None | **None**; existing frames only | `set_zero`, `go_to_zero`, `clear_zero`, persisted offset | As good as the operator's eye; drifts with any unpowered or by-hand move | Only until something breaks it (below) | Go-to-zero is an ordinary confirmed move | $0; about a day |
| **G. Closed loop / encoders** | Motor-shaft encoder (MKS SERVO42D [8]) or a linear encoder (Renishaw RESOLUTE, absolute [9]) | Replaces the driver path or adds a read-back | Position from the encoder | Shaft: prevents lost steps but still needs a home. Absolute linear: 0.1 µm and absolute at power-on [9]. | Absolute linear: **yes, with no homing move**; shaft: no | Best: no collision, ever | $50–100 per axis (shaft); $1k+ per axis (linear) |

Notes:
- **A.** On the TMC2209, StallGuard4 works only in StealthChop, so a probe running SpreadCycle must switch modes for the home move. **Verified by the lead against the datasheet text (rev 1.03, read 2026-09-26):** feature list "StallGuard4 load and stall detection for StealthChop"; §11.1 "StallGuard4 is optimized for operation with StealthChop, its predecessor StallGuard2 works with SpreadCycle"; §11.4 the DIAG stall pulse "is only enabled in StealthChop mode, and when TCOOLTHRS ≥ TSTEP > TPWMTHRS". Older Marlin text saying the opposite describes StallGuard2 drivers (TMC2130/5160); Marlin PR #16153 [2] is the fix for the 2209. **Datasheet §11.5 adds a limit that matters here:** StallGuard4 "does not operate reliably at extreme motor velocities: very low motor velocities (for many motors, less than one revolution per second) generate a low back EMF and make the measurement unstable". A homing move on a fine-pitch micron stage is slow by nature, so A may have no usable velocity window at all; that is a bench measurement before any firmware work. Klipper warns that the mechanics must take repeated bumps and advises against sensorless Z [3]. This is the owner's current-feedback idea done properly inside the driver.
- **B.** The honest verdict on the naive version. Because the chopper holds phase current constant, supply current is a weak, temperature- and voltage-dependent proxy. Driver vendors measure back-EMF instead [5], which is exactly what StallGuard is. **Do A rather than B.**
- **F breaks when** the power is cycled and the stage moved by hand, the micrometer knobs are turned, the axis is pushed while the coils are off (`'d'`, the idle interlock), a step is lost during a move, or the app crashes mid-move (up to 100 ms of motion goes unrecorded). A reconnect resets the firmware counter to 0, so the model must re-seed from the persisted absolute position, with the zero point stored as an offset in steps: `offset = -last_abs` on reconnect. With no switch, the model cannot *detect* any of these failures. It can only label the zero "unverified since last power-up".

## 6. Recommendation

1. **Now: F plus a manual E.** Add `set_zero` / `go_to_zero` in the host with a persisted offset, and a crosshair overlay on the captured camera region. The operator aligns the tip to a fiducial by eye once per session and presses *Set zero here*. This needs **no firmware or wire change**: go-to-zero is one move frame with `x_dist = -rel_x`, and the golden files stay untouched. The zero is labelled *session* or *carried over (unverified)* until the operator re-confirms it.
2. **Right long-term step: D, optical home switches**, homed Z-up first, then X/Y. Add a slow second approach and bounded travel. Firmware v2 carries it together with D-7's coil kill. This gives a true global zero with no collision. Once D exists, automatic E is a cheap refinement: switch gives coarse, fiducial gives fine.
3. **A (StallGuard)** only if the owner rules out switches *and* the axes have rigid hard stops that tolerate bumps *and* a bench test shows a homing speed above StallGuard4's low-velocity floor (§11.5) that is still safe for the tip. Treat it as a fallback, not the plan. **Reject B.** Reserve **G** (absolute linear) for when µm-level global repeatability becomes a science requirement.

## 7. Minimal model command surface

| Command | Refuses when | Estop / fault behaviour |
|---|---|---|
| `set_zero(confirmed)` | latched; faulted; moving; position stale (`position_age` > 0.5 s) or never sampled; port lost | Pure host state, so no wire traffic. It records `offset = raw` and `zero_source="operator"` and persists them. |
| `clear_zero(confirmed)` | nothing except latched | Drops the zero; readouts go back to raw. |
| `go_to_zero(confirmed)` | latched; faulted; no zero; zero not verified this session (needs a second confirm); moving; disabled | Sends an ordinary move frame through `_send_move` (`abort_if` latch). A stop during it leaves the zero intact, because the counter still tracks. |
| `zero_state` (property) | — | `none`, `session`, `carried` (from disk, unverified) or `homed`. After `'d'` or the interlock it becomes `carried` (coils were off). |
| `home_axis(axis, confirmed)` *(firmware v2 only)* | latched; faulted; not Z-retracted for X/Y; no firmware support (a `CAN_HOME` capability like `can_kill_coils`) | Estop sends the existing stop path; the model sets `zero_state="none"` for that axis; firmware aborts on `'d'` or the zero frame and on max travel. |

The views show one "Zero" group per probe: the state lamp, *Set zero here*, *Go to zero*, and relative/raw readouts. The layout and tier are a design-brief decision.

## 8. Owner decisions (bench facts I could not verify)

1. **The microstep count:** 8 (firmware `:506`) or 16 (README `:67`) per full step, and so how many µm one count is.
2. **Hard stops:** do X, Y and Z have physical stops at all? Where are they, and can they take repeated bumps? This decides whether A or C is even possible.
3. **Tip clearance:** the Z direction that retracts the tip from the specimen, and the minimum clearance before X/Y may move.
4. **Is DIAG wired** from each TMC2209 to the Mega, and which Mega pins are free for switches?
5. **Does the stage have manual knobs or micrometers** an operator turns by hand? If so, approach F can never be trusted across sessions.
6. **Approve firmware v2** (a home command plus a switch readout) and the wire change it brings, bundled with D-7.
7. **What "global zero" means:** a stage-mechanical origin (D) or the specimen/chuck fiducial (E)?
8. **Where the persisted zero lives:** a per-machine file outside the repo, and whether it is keyed by board or port.

## Sources

[1] TMC2209 datasheet (rev 1.09 at analog.com; the lead verified the quoted sentences in rev 1.03: https://www.th3dstudio.com/wp-content/uploads/2019/12/TMC2209_Datasheet_V103.pdf), https://www.analog.com/media/en/technical-documentation/data-sheets/tmc2209_datasheet_rev1.09.pdf
[2] Marlin PR #16153, "Enable stealthChop for TMC2209 homing", https://github.com/MarlinFirmware/Marlin/pull/16153
[3] Klipper TMC drivers, sensorless homing, https://www.klipper3d.org/TMC_Drivers.html
[4] Klipper forum, sensorless repeatability (3.54 mm wander), https://klipper.discourse.group/t/klipper-sensorless-homing-false-triggers-repeatability-and-why-one-working-threshold-isnt-tuned-corexy-tmc5160/26260/2
[5] TI SLVAEI3A, "Sensorless Stall Detection for Stepper Motors", https://www.ti.com/lit/an/slvaei3a/slvaei3a.pdf
[6] Optical vs mechanical endstops, https://mechanical-design-handbook.blogspot.com/2026/02/optical-vs-mechanical-endstop-selection.html
[7] Duet3D forum, endstop accuracy, https://forum.duet3d.com/topic/33723/endstop-accuracy
[8] MKS SERVO42D, https://makerbase3d.com/product/servo42d-nema17-closed-loop-stepper-motor-driver-cnc-3d-printer-for-gen_l-foc-quiet-and-efficient/
[9] Renishaw RESOLUTE, https://www.motioncontroltips.com/renishaw-resolute-absolute-encoder-offers-sub-micron-accuracy/
