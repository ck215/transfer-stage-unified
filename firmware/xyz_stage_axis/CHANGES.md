# Changes from stepper_validator.ino

Base: the bench validator, which now lives at
`dev/equipment_test/stepper_validator/stepper_validator/stepper_validator.ino`. It was copied from the pre-repo
bench copy (sha1 a0d42374, 939 lines; the repo copy differs from it only in one comment)
unedited, then changed as below. The motion core is untouched: the 25 us step ISR, the limit interlock with learned
ends, the TMC2209 configuration and driver-reset re-configure, the R-1..R-8 fixes, the `extern "C" _write` fix and
`DRIVER_HALF_DUPLEX = true`. No validator command, reply key or line format was removed or renamed.

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
| `stepIsr` | home-edge branch also checks the HOME trap (one-shot; direction from `distanceToGo()`) | Zero taken on the edge itself, independent of `loop()` latency and UART reads. The limit logic is unchanged. |
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
