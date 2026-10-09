# Changes from stepper_validator.ino

Base: the bench validator, which now lives at
`dev/equipment_test/stepper_validator/stepper_validator/stepper_validator.ino`. It was copied from the pre-repo
bench copy (sha1 a0d42374, 939 lines; the repo copy differs from it only in one comment)
unedited, then changed as below. The motion core is shared with the validator: the 25 us step ISR, the limit interlock
with learned ends (changed in both sketches alike on 2026-10-09, last section), the TMC2209 configuration and
driver-reset re-configure, the R-1..R-8 fixes, the `extern "C" _write` fix and `DRIVER_HALF_DUPLEX = true`. No
validator command, reply key or line format was removed or renamed.

## Constants

| Change | Why |
| --- | --- |
| `LIMITS_NC` true -> **false** | This stage's switches are normally open (SPEC, bench 2026-10-09). |
| `MAX_MOVE_MM` 15 -> **50** | SPEC: one move may cross the full travel; the limit interlock stops the ends. REVS and TEST REVS stay capped at 15 revolutions by `MAX_TEST_REVS`. |
| `HOME_DIR`, `HOME_FLAG_LEVEL`, `HOME_SEEK_SPEED_MM`, `HOME_SLOW_SPEED_MM`, `HOME_BACKOFF_MM`, `HOME_APPROACH_EXTRA_MM`, `HOME_SEEK_MAX_MM` | HOME tuning; the first two depend on the flag and sensor and are an owner bench check. |
| `IDENTITY_LETTER`, `AXIS_EEPROM_ADDR`, `AXIS_TAG_MAGIC` | Identity reply and the EEPROM tag layout, in one place. |
| `HOST_TIMEOUT_MS` is now the default of a run-time value; `HOST_TIMEOUT_MIN_MS/MAX_MS`, `STREAM_MAX_HZ`, `HOME_EDGE_EVT_PER_S` added | HOSTTIMEOUT and STREAM clamps; the edge-report cap. |

## Protocol additions (SPEC "Axis firmware protocol, version 1")

- `S` -> `DEV: x X`; `AXIS` / `AXIS X|Y|Z` with an EEPROM tag (magic + letter + complement, read back after the write;
  refused while enabled, moving, homing or testing); `axis=` in BOOT, INFO, STATUS.
- `HOSTTIMEOUT <ms>` (clamped 250..5000) replaces the fixed 2500 ms window; `HB`.
- `MOVE <mm> [mm_s]` and the new `MOVETO <mm> [mm_s]`: the optional speed is set for that move only (the next move
  without one uses SPEED). Clamped to the step-rate ceiling and up to 1 step/s (a slower speed overflows AccelStepper's
  32-bit step interval far below that, but a floor is cheaper than an argument about it). MOVE/MOVETO replies gain
  `speed_mm_s` and `speed_clamped`.
- `JOGV <mm_s>`: velocity jog. AccelStepper drops the speed in one step if its max speed is lowered below the current
  speed (R-6), so JOGV never does that: a lower speed first decelerates with `stop()` and takes the new max speed once
  the speed has come down to it; a higher one raises the max speed and AccelStepper ramps up; a reversal goes through
  rest. Each transition is one AccelStepper call (repeated `moveTo`/`setMaxSpeed` calls would each advance the ramp by
  a step). Same 250 ms dead-man as JOG, refreshed by JOGV or JOG. `JOGV 0` ends only a jog (a gamepad at neutral must
  not cancel a D-pad MOVE or a HOME).
- `STREAM <hz>`: `P` lines from ISR state only, dropped (not queued) when the host stops draining USB, so a stalled
  host cannot block `loop()` and the guards in it.
- `HOME`: new sequence with its own phase state (`g_home`), a one-shot ISR "trap" on the home-sensor edge (records the
  coarse edge during the search; halts the pulses on the edge in the final approach), and `homeFail()` hooked into every
  stop path. See PROTOCOL.md.
- `homed` flag (STATUS, P lines): rules in PROTOCOL.md.

## Changes inside existing paths, and why

| Where | Change | Why |
| --- | --- | --- |
| `stepIsr` | home-edge branch also checks the HOME trap (one-shot; direction from `distanceToGo()`) | Zero taken on the edge itself, independent of `loop()` latency and UART reads. The limit logic is the validator's (2026-10-09 below). |
| `isBusy()` | also true while a HOME sequence runs | Between HOME legs the motor is briefly at rest; nothing else may start then, and the host-timeout and DTR guards must stay armed. |
| `hardHalt`/`softStop`/`serviceSensors` | `g_jog = false` -> `jogEnd()` (also ends JOGV) | Every stop path ends a velocity jog too. |
| `estop()`, DISABLE | clear `homed` if the motor was turning | See PROTOCOL.md "homed". |
| `pollDriver()` | driver-reset/uart-lost/overtemp/short also end HOME and clear `homed` | Safety paths for the new motion path. |
| `guards()` | DTR drop and host timeout also end HOME (`abortActivity`); host timeout uses the HOSTTIMEOUT value; DTR drop resets STREAM and HOSTTIMEOUT | Per-connection settings; a new host starts from defaults. |
| STOP/ESTOP/DISABLE | `testAbort()` -> `abortActivity()` (test and HOME) | Same. |
| JOG | refused while homing (`ERR JOG busy`), like during a TEST | A jog must not start between HOME legs. |
| ZERO | clears `homed` | SPEC. |
| `serviceSensors` | limit hits handed to HOME; edges counted for HOME; `EVT HOME edge` lines capped at 20/s with `suppressed=n` on the next one | HOME leaves the carriage on the sensor threshold, where noise could produce an edge every 200 us and flood USB. Format otherwise unchanged. |
| `loop()` | runs `jogvService()` and `homeStep()`; deferred SPEED/ACCEL waits for HOME and jogs to end; `streamService()` last | New services; R-6 deferral kept. |
| `bootReport` | `enabled=` prints the real state (was a literal 0), adds `axis=` and `proto=1` | SPEC (axis in BOOT). |
| INFO | printed in two chunks; `host_timeout_ms` is the current value; new keys appended | Line too long for one buffer; SPEC (axis in INFO). |
| STATUS | new keys appended before `reset=` | `homed=` (SPEC), `home_phase`, `axis`, `jog`, `stream_hz`, `host_timeout_ms`. |

## Verification

- Compiled with `arduino-cli compile --warnings all` (-Wall) for `teensy:avr:teensy35` and `teensy:avr:teensy41`
  (Teensy core 1.62.0, TMCStepper 0.7.3, AccelStepper 1.64): zero warnings on both. Results in COMPILE.txt.
- Not run on hardware (none available). Simulated instead: `scratchpad/xyz/sim/` builds the sketch natively with the real
  AccelStepper source against stub Teensy/TMC/EEPROM layers and a stage model (NO switches at 0 and 50 mm, hard stops
  0.5 mm beyond, a home flag with 20 um hysteresis, the carriage moved only by STEP/DIR pulses, loop() delayed 5 ms
  after each driver poll). 25 scenarios, all passing: identity/AXIS/HOSTTIMEOUT/STREAM/DTR reset; HOME with the flag
  ahead, behind, under the start, near either switch, absent, mismatched, inverted, after a hand-moved carriage;
  HOME repeatability from 7 start positions (same physical step every time); HOME aborted by STOP, ESTOP, DISABLE, host
  timeout, DTR drop; JOGV ramps up/down/reversal/clamp with no downward speed step (worst 0.44 x the ACCEL ramp) and
  the dead-man; JOGV 0 during a MOVE; 50 mm moves into both switches; homed rules; driver reset; the validator TESTs
  (UART, COILS, REVS, LIMITS), JOG, REVS and deferred SPEED; edge-report cap under chatter. No scenario ever drove past a
  hard stop. The simulation does not model ISR preemption inside `loop()`, lost steps, or real sensor and switch noise.

## 2026-10-09: limit-end learning (review R-4), parked switches, host-timeout disable

Same change in `stepper_validator.ino` wherever the logic is shared (the interlock, JOG, MOVE/REVS/TEST refusals, the
host timeout); the validator has no JOGV, MOVETO or HOME. PROTOCOL.md "Safety behaviour" states the rules.

| Where | Change | Why |
| --- | --- | --- |
| `stepIsr` | An unknown end is learned from the first filtered change while moving `d`: a **release** teaches `-d`; a **trip** teaches `d` only if the switch has not read pressed with its end unconfirmed (`s_lsSeen`); otherwise the trip halts and teaches nothing. Was: any first trip while moving taught `d`. | R-4 [S2]: parked on LS1 at power-up, a jog off it re-read the switch pressed in its release chatter (>= 200 us), taught LS1 = the + end and halted; LS1 then no longer stopped motion toward -, and the axis drove into the - hard stop (SGTHRS 0: no stall stop). The release is the first change a switch being left can make, and it is unambiguous. |
| `stepIsr`, `limitBlock`, JOG/JOGV | Parked switch (tripped, end not confirmed): the ISR bounds travel to `PARKED_TRAVEL_MM` (0.5 mm, **provisional**) either way from where it was found pressed; running out still pressed halts and learns that direction as its end (`learned=travel` on the LIMIT line); running out both ways halts and confines (`pressed_both_ways=1`, `limit-lsN-pressed-both-ways:check-switch`). JOG/JOGV capped at `PARKED_JOG_SPEED_MM` (0.1 mm/s) while parked. | Owner ruling: allow jog-off only, slowly; a switch held at power-up and driven further into it used to have no stop but the operator's dead-man. |
| MOVE/MOVETO/REVS/HOME/TEST, `testStep`, `homeStep` | Refused while a switch is parked (unknown or travel-learned end); a TEST or HOME that finds one parked ends. | Owner ruling. |
| `guards()` | Still silent `HOST_SILENT_OFF_MS` (10 s) after `EVT FAULT host-timeout`: `estop()`, `EVT FAULT host-timeout-disabled silent_ms=`, ENABLE needed again. | Owner ruling: the axis used to hold energised indefinitely with the port open and the host gone. DTR drop unchanged. |
| `spSetPos`, MICROSTEPS (`spRescale`), LIMITS, `setup` | The parked travel windows move with ZERO/HOME, rescale with MICROSTEPS, restart at LIMITS NC\|NO (which also forgets how ends were learned) and at boot. | A new origin or step size must not reset the budget. |
| INFO (station) | `parked_jog_mm_s= parked_travel_mm= host_silent_off_ms=` appended. | Bench check of the flashed values. |

Bench values are unchanged (pins, currents, speeds, the 200 us filter, HOME constants). `PARKED_TRAVEL_MM` is a new,
provisional value: the owner confirms it against the switch overtravel (PROTOCOL.md, Safety behaviour).

Verification: `arduino-cli compile --warnings all` for `teensy:avr:teensy35` and `teensy:avr:teensy41`, zero warnings
on both sketches. A new host-side simulation (`scratchpad/xyz/fwlimits/sim/`, not in the repo) builds each whole
sketch natively against the real AccelStepper source, a fake 25 us clock and a stage model (switches with 50 um
differential travel and 300 us release chatter, hard stops 0.8 mm past each switch). 14 scenarios per sketch: the R-4
jog-off then jog/MOVE back; first trips; TEST LIMITS and HOME; both tripped at boot and mid-move; parked and driven
into (normal, deep in the overtravel, short overtravel, ZERO and MICROSTEPS while parked); host timeout with and
without the host returning; DTR drop; a release at rest followed by chatter. The pre-fix sketches fail 8 of 14 (into
the hard stop at 0.5 mm/s in seven); the fixed ones pass 14 of 14. Not run on hardware.

## 2026-10-09: per-move acceleration for MOVE and MOVETO (co-arrival of a vector Step)

The station's vector Step gives each axis `|d_i|/|d| x speed`, so the axes arrive together at cruise. All three
boards ramp at one ACCEL, though, so on any move that ramps the shorter legs finish first and the path bows (host
simulation, a 0.6 x 0.2 mm Step at 2.5 mm/s: 421 ms apart, 96 um off the straight line).

| Where | Change | Why |
| --- | --- | --- |
| `handleLine` | A third argument is tokenised (`a3`, upper-cased like the others; the LOG 2 `rx` line shows it). Every command but MOVE and MOVETO ignores it, as before. | MOVE/MOVETO `<mm> [mm_s [mm_s2]]`. |
| MOVE, MOVETO | `mm_s2` sets the acceleration for this move only: not a number above 0 -> `ERR <cmd> bad-accel` (nothing moves); clamped to `MIN_ACCEL_MM..MAX_ACCEL_MM` (0.25..25, ACCEL's bounds); the reply appends `accel_mm_s2= accel_clamped=`; `g_applyPending` puts the board's SPEED and ACCEL back once the move has stopped. | The station sends `|d_i|/|d| x ACCEL` with the speed share, so every axis runs the same trapezoid scaled. A two-argument MOVE, its reply and REVS are byte-for-byte unchanged. |

Bench values are unchanged (`DEFAULT_ACCEL_MM`, `MIN_ACCEL_MM`, `MAX_ACCEL_MM`, speeds). The validator has no
per-move speed or acceleration and is unchanged.

Verification: `arduino-cli compile --warnings all`, zero warnings: Teensy 3.5 90692 bytes flash, 5876 bytes RAM;
Teensy 4.1 code 79284, data 13256, RAM1 variables 15424, RAM2 variables 12416. `dev/firmware_sim` scenario
`move-accel-coarrival` (22 checks: red 12 on the sketch before this change, green after; the other 14 scenarios still
pass, and the validator's 14): the same Step finishes 41 ms apart and 8 um off the line (23 ms and 3 um at 0.5 mm/s,
was 128 ms and 10 um). What is left is AccelStepper's ramp start (PROTOCOL.md, "Per-move acceleration"). After a
per-move-acceleration move, by arrival or by STOP, a two-argument MOVE takes exactly as long as before one; INFO's
`accel_mm_s2` is unchanged; out-of-range values are clamped and bad ones refused. Not run on hardware.
