# XYZ stage axis firmware: protocol version 1 (as implemented)

Firmware: `xyz_stage_axis/xyz_stage_axis.ino`, one Teensy 3.5 per axis (a 4.1 also builds), one TMC2209 on
single-wire UART. Contract: `scratchpad/xyz/SPEC.md` (lead, 2026-10-09). Every stepper-validator command and reply key is
kept, so `stepper_validator.html` drives this firmware unchanged.

## Transport and line types

USB serial; the baud rate is ignored. Newline-terminated ASCII lines (`\r\n` accepted); commands and arguments are
case-insensitive; at most 95 characters per line (longer: `ERR LINE too-long`). Each command gets exactly one reply.

| Line | Meaning |
| --- | --- |
| `OK <CMD> k=v ...` | success reply (some keys are fixed words, e.g. `OK HOME started`, `OK HB`) |
| `ERR <CMD> <reason>` | refusal; nothing changed unless the reason says so |
| `DEV: x X` | the one reply to `S` (identity), no `OK` prefix |
| `EVT ...` | unsolicited event; can arrive between any two lines, including before the reply that caused it |
| `P ...` | unsolicited position line (STREAM) |

Units: positions mm, speeds mm/s, accelerations mm/s^2 (1 motor rev = 1 mm). One step = 5 um / MICROSTEPS
(0.625 um at the boot setting of 8). `+` is the motor's positive direction (DIR high). Positions are relative to
the last ZERO or HOME (0 at boot).

## Commands

### Link and identity

| Command | Reply | Notes |
| --- | --- | --- |
| `S` | `DEV: x X` | The station's port scan. `X` is the EEPROM axis tag `X`/`Y`/`Z`, or `?` when the board has none. The letter `x` is `IDENTITY_LETTER`. |
| `AXIS` | `OK AXIS axis=X` | Query the tag (`axis=?` if none). |
| `AXIS X\|Y\|Z` | `OK AXIS axis=X stored=1` | Writes the tag to EEPROM and reads it back. `ERR AXIS busy` while enabled, moving, homing or testing; `ERR AXIS bad-arg-X\|Y\|Z`; `ERR AXIS eeprom-verify-failed`. Layout: address 0 = 0xA7, 1 = letter, 2 = ~letter. |
| `PING` | `OK PING uptime_ms=<n>` | |
| `HB` | `OK HB` | Explicit heartbeat. Any received line is a heartbeat. |
| `HOSTTIMEOUT [ms]` | `OK HOSTTIMEOUT ms=<n> clamped=<0\|1>` | Heartbeat window, clamped to 250..5000 (default 2500, the bench GUI's). No argument: query. `ERR HOSTTIMEOUT bad-arg`. |
| `STREAM [hz]` | `OK STREAM hz=<n> clamped=<0\|1>` | P-line rate, 0 = off, clamped to 50. No argument: query. `ERR STREAM bad-arg` (negative or not an integer). |
| `INFO` | `OK INFO k=v ...` | All validator keys (`host_timeout_ms` is now the current value), plus `fw=xyz_stage_axis proto=1 axis= identity= home_dir= home_flag_level= home_seek_mm_s= home_slow_mm_s= home_backoff_mm= home_extra_mm= home_seek_max_mm= stream_max_hz= host_timeout_min_ms= host_timeout_max_ms=`. |
| `STATUS` | `OK STATUS k=v ...` | All validator keys (reads the driver over UART), plus `homed=<0\|1> home_phase=<none\|seek\|seek-stop\|backoff\|approach> axis=<X\|Y\|Z\|?> jog=<none\|dir\|v> stream_hz= host_timeout_ms=`. |

`HOSTTIMEOUT` and `STREAM` are per connection: both return to their defaults (2500 ms, off) when the host drops DTR
(closes the port). A host must send them again after every (re)connect.

### Driver (unchanged from the validator)

`ENABLE` (`OK ENABLE enabled=1 microsteps=<n>`; re-reads the driver version and rewrites its whole configuration;
`ERR ENABLE busy|driver-uart-not-ok|driver-readback-mismatch`), `DISABLE` (`OK DISABLE enabled=0`; halts at once,
outputs off), `CURRENT <mA>` (`ma= clamped=`, clamp 1000), `MICROSTEPS <1..256, power of 2>` (`microsteps= step_um=
pos_mm= speed_mm_s= max_speed_mm_s= speed_clamped=`; position is rescaled and kept; `ERR ... busy` while moving or
homing), `MODE STEALTH|SPREAD`, `LIMITS NC|NO` (`limits= ls1= ls2= ends=forgotten`; boot default now **NO**),
`SPEED <mm/s>` and `ACCEL <mm/s^2>` (`applies=now|on-stop`: never changed mid-move).

### Motion

| Command | Reply | Notes |
| --- | --- | --- |
| `MOVE <mm> [mm_s]` | `OK MOVE mm=<applied> target_mm=<abs> clamped=<0\|1> speed_mm_s=<used> speed_clamped=<0\|1>` | Relative. Clamp +-50 mm (`MAX_MOVE_MM`, the full travel; the limit interlock stops an axis at its ends). The optional speed applies to this move only (else SPEED); it is clamped to the step-rate ceiling (2.5 mm/s at 8 microsteps) and up to 1 step/s. |
| `MOVETO <mm> [mm_s]` | `OK MOVETO mm=<distance> target_mm=<abs> clamped= speed_mm_s= speed_clamped=` | Absolute. The distance is clamped to +-50 mm (`clamped=1`, target moved accordingly). `|mm| > 10000`: `ERR MOVETO bad-arg`. |
| `REVS <n>` | `OK REVS mm= target_mm= clamped=` | Validator command, unchanged (clamp 15 revolutions). |
| `JOG <-1\|0\|1>` | `OK JOG dir=<n>` | Validator dead-man jog at SPEED, unchanged; `JOG 0` stops any motion except a TEST or HOME (refused while homing: `ERR JOG busy`). |
| `JOGV <mm_s>` | `OK JOGV mm_s=<applied> clamped=<0\|1>` | Signed velocity jog (analog stick). See below. |
| `STOP` | `OK STOP pos_mm=` | Decelerates and holds; ends any TEST or HOME. |
| `ESTOP` | `OK ESTOP enabled=0 pos_mm=` | Halts within one ISR tick, outputs off; ends any TEST or HOME. |
| `ZERO` | `OK ZERO pos_mm=0` | Refused while busy. Clears `homed`. |
| `HOME` | `OK HOME started` | Zeroing sequence; see HOME below. |
| `TEST UART\|COILS\|REVS [n]\|SWEEP\|LIMITS` | validator replies | Unchanged. |

Motion errors: `not-enabled`, `test-running`, `busy` (a move, jog, HOME or deceleration in progress; one move at a
time), `bad-arg`, `bad-speed`, and the limit reasons: `limit-ls1`/`limit-ls2` (that switch is tripped and guards the
end the move heads for), `limit-lsN-end-unknown:jog-off-it` (tripped, end not yet learned: only JOG/JOGV may leave it),
`limits-both-tripped:check-wiring-or-LIMITS-NC|NO`.

**JOGV.** `JOGV v` with `|v|` at least 1 step/s (0.000625 mm/s at 8 microsteps) starts or retargets a velocity jog;
`|v|` is clamped to the step-rate ceiling. A new JOGV while jogging changes the speed at the current ACCEL with no step
change up or down: a higher speed ramps up; a lower one decelerates and takes the new speed once reached; a reversal
decelerates to rest and ramps up the other way. Dead-man: the jog decelerates to a stop unless a JOGV or JOG arrives
within 250 ms. `JOGV 0` (or under 1 step/s) ends a jog and replies `OK JOGV mm_s=0.0000 clamped=0`; it does **not**
stop a MOVE, HOME or TEST (a gamepad at neutral can stream it during a D-pad MOVE): STOP and ESTOP stop those.
Refusals: `not-enabled`, `test-running`, `busy` (a MOVE or HOME owns the motion), the limit reasons (a refused JOGV
while jogging also decelerates the jog).

### Unsolicited lines

| Line | When |
| --- | --- |
| `EVT BOOT version=0x21 expected=0x21 uart=OK\|FAIL enabled=<0\|1> axis=X proto=1` | each time the host connects (DTR rises) |
| `EVT FAULT host-timeout` | no line for HOSTTIMEOUT while moving, jogging, homing or testing: decelerates and holds |
| `EVT FAULT driver-reset reconfigured version=.. microsteps=..` | the driver lost VM; halted, outputs off, configuration rewritten |
| `EVT FAULT uart-lost` / `EVT FAULT overtemp ...` / `EVT FAULT short ...` | driver faults; halted, outputs off |
| `EVT LIMIT lsN tripped pos_mm=<mm> end=<+1\|-1>` | the ISR interlock halted motion at a switch |
| `EVT HOME edge level=<0\|1> pos_mm=<mm> [suppressed=<n>]` | every filtered home-sensor edge, any time (validator line). At most 20 lines/s; the next line after a cap says how many were not printed. Not part of the HOME sequence. |
| `EVT HOME phase=seek\|backoff\|approach\|edge ...` | HOME progress (below) |
| `EVT HOME FAIL reason=<r> phase=<p> pos_mm= legs= ends= edges= elapsed_ms=` | HOME ended without a zero |
| `EVT HOMED edge_mm=<edge in the old coordinates> pos=0` | HOME succeeded; always the sequence's last event |
| `EVT RESULT ...`, `EVT PROGRESS ...`, `EVT SWEEP ...` | TEST lines, unchanged |
| `P pos=<mm %.5f> tgt=<mm %.5f> v=<mm/s %.4f> en=<0\|1> mv=<0\|1> ls1=<0\|1> ls2=<0\|1> home=<0\|1> homed=<0\|1>` | STREAM; from ISR state only (no driver UART read). Dropped, not queued, when the host is not draining USB. |

P-line fields: `tgt` is the motion target; during a jog it is the far jog target (+-1e9 steps), not a destination.
`mv` = the motor is stepping or decelerating. `ls1`/`ls2` = that switch is tripped. `home` is the filtered sensor
**level**, as in STATUS; the flag is in the slot when `home` equals INFO `home_flag_level` (1 by default).

### Logging (added by the lead at integration, 2026-10-09)

The station forwards every firmware line into its own event log, so the
firmware logs generously. `LOG <0|1|2>` -> `OK LOG level=N` (default 1):

- `0`: only `EVT BOOT`, `EVT FAULT`, `EVT RESULT`, `EVT HOMED`, `EVT HOME FAIL`
  and `EVT LIMIT` lines.
- `1`: every `EVT` line (the validator's set).
- `2`: also `EVT DBG <what> ...`: each command received (not the high-rate
  `JOG`, `JOGV`, `HB`, `STATUS` or the scan's `S`), `driver on`/`driver off`,
  `driver configured` with its settings, and `stopped pos_mm=... target_mm=...`
  where each motion ended. The station sets `LOG 2` on open.

## Safety behaviour

- **Limit interlock (step ISR, unchanged):** a tripped switch halts the pulses within one 25 us tick (after its 200 us
  noise filter), even while `loop()` is blocked in a UART read. Each switch's end is learned on its first trip while
  moving (`ls1_end`/`ls2_end`); a tripped switch then blocks motion toward its end only. A switch tripped with its end
  unknown (parked on it at power-up) does not halt and only JOG/JOGV may leave it. Both tripped halts everything.
  Contacts are **NO** by default for this stage: an open wire reads "clear", so the interlock depends on the wiring
  (bench-check with TEST LIMITS).
- **DTR drop** (port closed, page closed): halt, outputs off, TEST/HOME ended (`host-gone`), STREAM off, HOSTTIMEOUT
  back to 2500.
- **Host timeout:** no line for HOSTTIMEOUT while moving, jogging, homing or testing: decelerate, hold,
  `EVT FAULT host-timeout`, TEST/HOME ended.
- **Jog dead-man:** 250 ms for JOG and JOGV.
- **Driver reset / faults:** halt, outputs off, `EVT FAULT ...`, TEST/HOME ended; a reset driver is reconfigured and
  stays disabled until ENABLE.
- **Speed changes** never lower AccelStepper's max speed below the current speed (review R-6): SPEED/ACCEL during
  motion apply on stop; JOGV decelerates first; HOME and tests restore the user's SPEED/ACCEL once stopped.

## homed

`homed=1` only after a successful HOME. It returns to 0: at boot; when a HOME starts (and stays 0 if it fails); on
ZERO; on a driver reset or driver fault (uart-lost, overtemp, short); on ESTOP, DISABLE or DTR drop **while the motor
is turning** (the rotor coasts unpowered, and re-enabling snaps it to the nearest matching electrical angle).
Kept: MICROSTEPS (position rescaled), STOP, host timeout (both decelerate), limit-interlock halts (energised hard stop
at <= 2.5 mm/s), and a DISABLE/ESTOP at standstill (the driver keeps its microstep counter while VM is up and the
lead screw holds the carriage). Moving the carriage by hand while disabled is not detected.

## HOME

Constants (top of the sketch): `HOME_DIR = -1` (final approach direction), `HOME_FLAG_LEVEL = 1` (sensor level with
the flag in the slot), `HOME_SEEK_SPEED_MM = 1.0`, `HOME_SLOW_SPEED_MM = 0.1`, `HOME_BACKOFF_MM = 0.5`,
`HOME_APPROACH_EXTRA_MM = 0.5`, `HOME_SEEK_MAX_MM = 55`. HOME uses a fixed 2.5 mm/s^2 ramp (TEST_ACCEL_MM), and
both speeds are clamped to the step-rate ceiling.

**Reference edge R:** the position where the filtered sensor level changes **to** `HOME_FLAG_LEVEL` while the axis
moves in `HOME_DIR` (seen moving the other way, R is where the level leaves `HOME_FLAG_LEVEL`). The zero is always
taken moving `HOME_DIR`, from rest, at the slow speed, after at least `HOME_BACKOFF_MM` of travel (which takes up
lead-screw backlash and keeps the sensor's direction-dependent hysteresis out of the zero).

**Preconditions:** enabled and idle (`ERR HOME not-enabled|test-running|busy`); no switch tripped with an unknown end
(`ERR HOME limit-lsN-end-unknown:jog-off-it`) and not both tripped (`ERR HOME limits-both-tripped:...`).

**Sequence** (d = HOME_DIR):
1. `OK HOME started`; `homed` becomes 0.
2. **seek** `EVT HOME phase=seek dir=<s> leg=<n> speed_mm_s= max_mm=55.0 after=<why> limit_s=<time limit>`: search at
   the seek speed toward R: `-d` when the start reads the flag (R is behind), otherwise `d`, or `-d` when an earlier
   HOME's zero says the start is beyond R (`after=start-beyond-old-zero`; a stale hint only costs one switch). The
   step ISR records R's position exactly (trap); `loop()` then decelerates. A switch before R reverses the search
   once (`after=limit`, or `after=at-limit` when the leg would start against a switch whose end is known); reaching a
   second end means there is no R.
3. **backoff** `EVT HOME phase=backoff edge_mm=<coarse R> to_mm=<R - d x 0.5>`: move at the seek speed to 0.5 mm on
   the clear side of the coarse R. The sensor must read clear there.
4. **approach** `EVT HOME phase=approach dir=<d> speed_mm_s=0.1000 max_mm=1.000`: move in d at the slow speed. The
   ISR halts the pulses on the edge itself, in the same tick (no loop latency, no UART read in between), and that
   position becomes 0.
5. `EVT HOME phase=edge edge_mm= coarse_mm= coarse_minus_edge_um= legs= ends= elapsed_ms=` (diagnostic: the coarse
   minus final edge is the sensor hysteresis when the coarse edge was found moving -d), then
   `EVT HOMED edge_mm=<R in the old coordinates> pos=0`. The carriage rests on R, holding, `homed=1`.

Time limit: 1.5 x the worst-case plan (two full search legs, backoff, approach) + 10 s, at least 120 s (193 s at the
defaults; `limit_s` in the seek events).

### Scenarios (d = -1 by default; "flag" = sensor reads HOME_FLAG_LEVEL)

| Start / event | Behaviour | Switches touched | Ends with |
| --- | --- | --- | --- |
| Clear, R ahead in d | seek d, R found, backoff, approach, zero | none | `HOMED` |
| Clear, flag on the -d side | seek d, d-end switch halts it, seek -d over the flag, R found leaving the flag, backoff, approach | the d end, at 1 mm/s | `HOMED` |
| Same, homed before and beyond the old zero | seek -d first | none | `HOMED` |
| On the flag | seek -d, R found leaving the flag, backoff, approach | none | `HOMED` |
| On a switch whose end is known | the leg toward it counts as reaching that end; seek the other way | (already on one) | as above |
| On a switch whose end is unknown | refused | | `ERR HOME limit-lsN-end-unknown:jog-off-it` |
| No flag between the switches | seek d to a switch, seek -d to the other | both | `FAIL reason=no-edge-between-limits edges=0` |
| Constants do not match the geometry (e.g. a shutter whose only edge goes the other way) | as above, edges seen but none is R | both | `FAIL reason=no-edge-between-limits edges>0` |
| A 55 mm leg meets neither R nor a switch | a switch did not trip (check its wiring) | | `FAIL reason=no-edge-within-search` |
| R within ~0.7 mm (decel + backoff) of a switch on the backoff side | | | `FAIL reason=edge-too-close-to-limit` or `limit-during-backoff` |
| The backoff point reads the flag | flag narrower than 0.5 mm with an inverted HOME_FLAG_LEVEL, or noise | | `FAIL reason=flag-at-backoff` |
| The approach covers 1.0 mm without the edge | sensor or flag not repeatable | | `FAIL reason=edge-lost` |
| A switch trips during backoff or approach | ISR halt | | `FAIL reason=limit-during-backoff\|limit-during-approach` |
| STOP | decelerate, hold | | `FAIL reason=stop` |
| ESTOP / DISABLE | halt, outputs off | | `FAIL reason=estop\|disabled` |
| Host timeout | decelerate, hold, `EVT FAULT host-timeout` | | `FAIL reason=host-timeout` |
| DTR drop | halt, outputs off | | `FAIL reason=host-gone` |
| Driver reset or fault | halt, outputs off, `EVT FAULT ...` | | `FAIL reason=driver-reset\|uart-lost\|overtemp\|short` |
| Time limit | decelerate, hold | | `FAIL reason=time-limit` |

Every FAIL leaves the axis stopped: holding (enabled) unless the cause disabled it; `homed=0`. During HOME, MOVE,
MOVETO, REVS, JOG, JOGV (non-zero), TEST, ZERO, MICROSTEPS, LIMITS, ENABLE and AXIS are refused `busy`; SPEED/ACCEL
apply after it. `JOGV 0` is accepted and does nothing to HOME.

With a vane flag, an inverted `HOME_FLAG_LEVEL` still homes repeatably, on the vane's other edge (or fails
`flag-at-backoff` if the vane is narrower than 0.5 mm). It is not unsafe, but it moves the zero.

### Choosing HOME_DIR and HOME_FLAG_LEVEL (owner bench check)

1. With the driver disabled, read STATUS `home=` with the slot clear and with a card in the slot. `HOME_FLAG_LEVEL`
   is the level with the card in (an open-collector sensor on the internal pull-up reads 1).
2. Run `TEST LIMITS`: it logs each home edge between the switches (`EVT HOME edge level= pos_mm=`) and the result
   lists them relative to the parking point.
3. Pick the edge to zero on and the approach direction: moving `HOME_DIR`, the level must change to
   `HOME_FLAG_LEVEL` at that edge. Keep it at least 1 mm from the switch on the far side of the approach.
4. Repeatability: HOME from several starts; after the first, each `EVT HOMED edge_mm=` should be within a step or two
   of 0.000 (0.625 um steps at 8 microsteps).
