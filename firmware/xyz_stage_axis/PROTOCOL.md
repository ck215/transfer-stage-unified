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
| `INFO` | `OK INFO k=v ...` | All validator keys (`host_timeout_ms` is now the current value), plus `fw=xyz_stage_axis proto=1 axis= identity= home_dir= home_flag_level= home_seek_mm_s= home_slow_mm_s= home_backoff_mm= home_extra_mm= home_seek_max_mm= stream_max_hz= host_timeout_min_ms= host_timeout_max_ms= parked_jog_mm_s= parked_travel_mm= host_silent_off_ms=`. |
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
| `MOVE <mm> [mm_s [mm_s2]]` | `OK MOVE mm=<applied> target_mm=<abs> clamped=<0\|1> speed_mm_s=<used> speed_clamped=<0\|1>`, then ` accel_mm_s2=<used> accel_clamped=<0\|1>` when `mm_s2` was given | Relative. Clamp +-50 mm (`MAX_MOVE_MM`, the full travel; the limit interlock stops an axis at its ends). The optional speed applies to this move only (else SPEED); it is clamped to the step-rate ceiling (2.5 mm/s at 8 microsteps) and up to 1 step/s. The optional acceleration (added 2026-10-09; it needs the speed before it) applies to this move only (else ACCEL), clamped to ACCEL's own bounds, 0.25..25 mm/s^2; see "Per-move acceleration" below. A two-argument MOVE is unchanged, reply included. |
| `MOVETO <mm> [mm_s [mm_s2]]` | `OK MOVETO mm=<distance> target_mm=<abs> clamped= speed_mm_s= speed_clamped=` [` accel_mm_s2= accel_clamped=`] | Absolute. The distance is clamped to +-50 mm (`clamped=1`, target moved accordingly). `|mm| > 10000`: `ERR MOVETO bad-arg`. Speed and acceleration as for MOVE. |
| `REVS <n>` | `OK REVS mm= target_mm= clamped=` | Validator command, unchanged (clamp 15 revolutions). |
| `JOG <-1\|0\|1>` | `OK JOG dir=<n>` | Validator dead-man jog at SPEED, unchanged; `JOG 0` stops any motion except a TEST or HOME (refused while homing: `ERR JOG busy`). |
| `JOGV <mm_s>` | `OK JOGV mm_s=<applied> clamped=<0\|1>` | Signed velocity jog (analog stick). See below. |
| `STOP` | `OK STOP pos_mm=` | Decelerates and holds; ends any TEST or HOME. |
| `ESTOP` | `OK ESTOP enabled=0 pos_mm=` | Halts within one ISR tick, outputs off; ends any TEST or HOME. |
| `ZERO` | `OK ZERO pos_mm=0` | Refused while busy. Clears `homed`. |
| `HOME` | `OK HOME started` | Zeroing sequence; see HOME below. |
| `TEST UART\|COILS\|REVS [n]\|SWEEP\|LIMITS` | validator replies | Unchanged. |

Motion errors: `not-enabled`, `test-running`, `busy` (a move, jog, HOME or deceleration in progress; one move at a
time), `bad-arg`, `bad-speed`, `bad-accel` (MOVE/MOVETO's third argument is not a number above 0; nothing moves), the
soft-limit reasons (`soft-limit`, `soft-limit-unreferenced:touch-ls1`, `soft-limit-eeprom-damaged:set-SOFTLIMIT`; see
"Soft travel limit" below), and the limit reasons: `limit-ls1`/`limit-ls2` (that switch is tripped and guards the
end the move heads for), `limit-lsN-end-unknown:jog-off-it` (the switch is parked: tripped with its end not confirmed,
see Safety behaviour; only a slow JOG/JOGV may move), `limit-lsN-pressed-both-ways:check-switch` (a jog while parked:
the parked travel is spent in this direction too), `limits-both-tripped:check-wiring-or-LIMITS-NC|NO`.

**Per-move acceleration.** `MOVE <mm> <mm_s> <mm_s2>` ramps this move up and down at `mm_s2` instead of ACCEL. The
board's SPEED and ACCEL are put back once the move has stopped, however it stopped (arrival, STOP, a host timeout, a
limit halt); INFO and STATUS always report the board's own values, and the next move without the argument ramps at
ACCEL. A STOP during such a move decelerates at the move's acceleration. The clamp is ACCEL's own (0.25..25), so the
argument allows no ramp, and no stopping distance (v^2 / 2a), that SPEED and ACCEL could not already set. Its use: for
a vector move the station sends each axis `|d_i|/|d| x speed` and `|d_i|/|d| x ACCEL`, so every axis runs the same
trapezoid scaled to its distance and the axes arrive together on the straight line; with the speed alone they share
one ACCEL, the shorter legs finish first on any move that ramps, and the path bows. What is left is AccelStepper's ramp
start (its first step goes at once, its first interval is c0 = 0.676 sqrt(2/a)): the gentler leg leads by a few c0, a
few tens of ms that do not grow with the move (host simulation, a 0.6 x 0.2 mm Step: 41 ms apart and 8 um off the line
at 2.5 mm/s, 23 ms and 3 um at 0.5 mm/s; with the speed alone 421 ms and 96 um, 128 ms and 10 um). An axis whose
share would need less than 0.25 mm/s^2 is clamped up and leads a little more. These numbers are a host model's; the
owner checks them on the bench.

**JOGV.** `JOGV v` with `|v|` at least 1 step/s (0.000625 mm/s at 8 microsteps) starts or retargets a velocity jog;
`|v|` is clamped to the step-rate ceiling. A new JOGV while jogging changes the speed at the current ACCEL with no step
change up or down: a higher speed ramps up; a lower one decelerates and takes the new speed once reached; a reversal
decelerates to rest and ramps up the other way. Dead-man: the jog decelerates to a stop unless a JOGV or JOG arrives
within 250 ms. `JOGV 0` (or under 1 step/s) ends a jog and replies `OK JOGV mm_s=0.0000 clamped=0`; it does **not**
stop a MOVE, HOME or TEST (a gamepad at neutral can stream it during a D-pad MOVE): STOP and ESTOP stop those.
Refusals: `not-enabled`, `test-running`, `busy` (a MOVE or HOME owns the motion), the limit reasons (a refused JOGV
while jogging also decelerates the jog). While a switch is parked, `|v|` is capped at `PARKED_JOG_SPEED_MM` (0.1 mm/s;
the reply says `clamped=1`); once the switch releases, the next JOGV is not capped. A JOG started while parked keeps
that cap until it ends.

### Soft travel limit (2026-10-10, X-14)

For an axis whose carriage cannot reach one of its switches (the bench probe on axis 1 meets its fixture before LS2):
the most it may travel from LS1, kept in EEPROM. Default: none (an erased EEPROM block).

| Command | Reply | Notes |
| --- | --- | --- |
| `SOFTLIMIT` | `OK SOFTLIMIT mm=<travel> ref=<0\|1>[ ls1_mm=<reference>][ limit_mm=<limit>][ damaged=1]` | Query. `ls1_mm` when referenced; `limit_mm` when referenced and a travel is set; `damaged=1` when the EEPROM block is damaged. |
| `SOFTLIMIT <mm>` | the same, then ` stored=1` | `0..100` mm, 0 = no limit. Written to EEPROM (address 4: `0x5C`, um as 4 bytes little-endian, their complement) and read back. `ERR SOFTLIMIT bad-arg` (not a number, negative, over 100), `busy` (enabled, moving, homing or testing), `eeprom-verify-failed`. |

- **The reference** is where LS1 last tripped while the axis moved toward the end it guards, with that end known (the
  first trip from clear teaches it): the exact step, taken in the step ISR. The limit lies `mm` from it, away from
  LS1's end. Positions in `ls1_mm`/`limit_mm` are the current counter's; ZERO, HOME and MICROSTEPS keep the limit on
  the same carriage position. Every such trip refreshes the reference (`EVT SOFTLIMIT referenced`).
- **Not referenced** (since boot, `LIMITS`, or a position loss below), with a travel set: only JOG/JOGV move the axis,
  or a MOVE/MOVETO/REVS toward LS1 once its end is known (that is how the reference is taken). Everything else, HOME
  and the motion TESTs included: `soft-limit-unreferenced:touch-ls1`.
- **Referenced**: a MOVE/MOVETO/REVS whose target lies past the limit is shortened onto it (`clamped=1`, then `EVT
  SOFTLIMIT clamped`); a jog heading out takes the limit as its far target and decelerates onto it (`EVT SOFTLIMIT
  reached`). At the limit, anything further out is refused `soft-limit`. Toward LS1 nothing changes.
- **The step ISR** halts any motion about to step past the limit (HOME's search, a TEST, an AccelStepper overshoot),
  as at a switch: `EVT SOFTLIMIT stopped`. HOME takes the limit as an end (a search reverses there once; a second end
  is `HOME FAIL reason=no-edge-between-limits`; in backoff or approach `soft-limit-during-backoff|approach`); a TEST
  ends `ABORTED reason=soft-limit`.
- **Lost** (`EVT SOFTLIMIT lost reason=<r>`): by homed's rule for the step count (ESTOP or DISABLE while turning, a
  driver reset, `uart-lost`, overtemperature, a short), and with the ends on `LIMITS`. A stationary DISABLE or ESTOP,
  ZERO, HOME and MICROSTEPS keep it.
- **A damaged EEPROM block** (wrong magic, complement or range) is not "no limit": `damaged=1`, and only jogs move the
  axis (`soft-limit-eeprom-damaged:set-SOFTLIMIT`) until `SOFTLIMIT <mm>` writes it again.
- **Owner bench check, the travel:** with no limit set, jog from LS1 toward the obstruction and read how far the
  carriage can go (`SOFTLIMIT` after the trip gives `ls1_mm`; STATUS `pos_mm` minus it is the travel), then set the
  limit with a margin. The reference is LS1's operating point when approached at speed; its filter (200 us) adds at
  most 0.5 um at 2.5 mm/s.

### Unsolicited lines

| Line | When |
| --- | --- |
| `EVT BOOT version=0x21 expected=0x21 uart=OK\|FAIL enabled=<0\|1> axis=X proto=1` | each time the host connects (DTR rises) |
| `EVT FAULT host-timeout` | no line for HOSTTIMEOUT while moving, jogging, homing or testing: decelerates and holds |
| `EVT FAULT host-timeout-disabled silent_ms=<ms since the last line>` | still no line `HOST_SILENT_OFF_MS` (10 s) after `EVT FAULT host-timeout`: outputs off; `ENABLE` is needed again |
| `EVT FAULT driver-reset reconfigured version=.. microsteps=..` | the driver lost VM; halted, outputs off, configuration rewritten |
| `EVT FAULT uart-lost` / `EVT FAULT overtemp ...` / `EVT FAULT short ...` | driver faults; halted, outputs off |
| `EVT LIMIT lsN tripped pos_mm=<mm> end=<+1\|-1\|+0> [learned=travel travel_mm=<mm>] [pressed_both_ways=1 travel_mm=<mm>]` | the ISR interlock halted motion at a switch. `end=+0`: its end is not known (both switches tripped, or a trip that teaches nothing). `learned=travel`: the parked travel ran out with the switch still pressed, so that direction is its end (provisional until it releases). `pressed_both_ways=1`: the parked travel ran out the other way too (Safety behaviour). |
| `EVT SOFTLIMIT referenced ls1_mm=<mm> limit_mm=<mm> travel_mm=<mm>` | LS1 tripped toward its end with a soft limit set: the reference (above) |
| `EVT SOFTLIMIT clamped cmd=<MOVE\|MOVETO\|REVS> target_mm=<asked> limit_mm=<mm>` | after that command's `OK`: its target was shortened onto the soft limit |
| `EVT SOFTLIMIT reached limit_mm=<mm>` | a jog decelerated onto the soft limit and stopped there |
| `EVT SOFTLIMIT stopped pos_mm=<mm> limit_mm=<mm>` | the step ISR halted a motion on the soft limit (a HOME search, a TEST, an overshoot) |
| `EVT SOFTLIMIT lost reason=<r>` | the reference was lost (a soft limit is set): touch LS1 again |
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

- `0`: only `EVT BOOT`, `EVT FAULT`, `EVT RESULT`, `EVT HOMED`, `EVT HOME FAIL`,
  `EVT LIMIT` and `EVT SOFTLIMIT` lines.
- `1`: every `EVT` line (the validator's set).
- `2`: also `EVT DBG <what> ...`: each command received (not the high-rate
  `JOG`, `JOGV`, `HB`, `STATUS` or the scan's `S`), `driver on`/`driver off`,
  `driver configured` with its settings, and `stopped pos_mm=... target_mm=...`
  where each motion ended. The station sets `LOG 2` on open.

## Safety behaviour

- **Limit interlock (step ISR):** a tripped switch halts the pulses within one 25 us tick (after its 200 us noise
  filter), even while `loop()` is blocked in a UART read; a tripped switch blocks motion toward its own end only. Both
  tripped halts everything. Contacts are **NO** by default for this stage: an open wire reads "clear", so the
  interlock depends on the wiring (bench-check with TEST LIMITS).
- **How a switch's end is learned** (`ls1_end`/`ls2_end`; changed 2026-10-09, review R-4: a re-trip in the release
  chatter of a switch being left used to teach the wrong end, and the axis was later driven into that end's hard
  stop). From the first filtered change the ISR sees while the axis moves in direction `d`:
  - the switch **releases**: the carriage left it, so it guards `-d`;
  - it **trips** and has not read pressed with its end unconfirmed since boot (or `LIMITS`): reached from clear, so it
    guards `d`, and the axis halts there;
  - it trips again after reading pressed with its end unconfirmed (parked on it, or it released at rest): possibly
    chatter, so the axis halts (`end=+0`) and nothing is learned.
- **Parked on a switch** (tripped, end not confirmed by one of the first two rules: e.g. pressed at power-up, or after
  `LIMITS`): MOVE, MOVETO, REVS, HOME and TEST are refused (`limit-lsN-end-unknown:jog-off-it`, TEST
  `limit-tripped:jog-off-it`), and a TEST or HOME that finds a switch parked ends (`ABORTED reason=limit-end-unknown`,
  `HOME FAIL reason=limit-lsN-end-unknown:jog-off-it`). Only JOG/JOGV move, at most `PARKED_JOG_SPEED_MM` = 0.1 mm/s
  (HOME's slow speed), under the 250 ms dead-man. The firmware cannot tell which way is off, so:
  - if the switch releases while jogging `d`, its end is `-d` (rule above); the cap lifts;
  - if the axis travels `PARKED_TRAVEL_MM` = **0.5 mm (provisional)** from where the switch was found pressed and it is
    still pressed, that direction drives into it: the ISR halts, learns that end and says so
    (`EVT LIMIT lsN tripped ... learned=travel travel_mm=0.500`). The switch stays parked until it releases;
  - if the window then runs out the other way too, the ISR halts (`pressed_both_ways=1`) and jogs both ways are refused
    (`limit-lsN` / `limit-lsN-pressed-both-ways:check-switch`): the switch is stuck, or the budget is too small.
    `LIMITS NC|NO` forgets the ends and restarts the window where the carriage is.
  - **Owner bench check, `PARKED_TRAVEL_MM`:** it must exceed the travel from the hard stop to where the switch
    releases (its overtravel plus differential travel). If it is shorter, a carriage parked against the hard stop
    learns the wrong end on its way off, then stalls slowly (<= 0.1 mm/s, <= 0.5 mm of steps) against the hard stop
    and is confined (`pressed_both_ways=1`). Larger costs only this: jogging into a switch parked near the hard stop
    stalls there at 0.1 mm/s for up to `PARKED_TRAVEL_MM` of steps before the halt. The window moves with ZERO, HOME and
    MICROSTEPS.
- **DTR drop** (port closed, page closed): halt, outputs off, TEST/HOME ended (`host-gone`), STREAM off, HOSTTIMEOUT
  back to 2500.
- **Host timeout:** no line for HOSTTIMEOUT while moving, jogging, homing or testing: decelerate, hold,
  `EVT FAULT host-timeout`, TEST/HOME ended. Still no line `HOST_SILENT_OFF_MS` (10 s) after that: outputs off,
  `EVT FAULT host-timeout-disabled`, and `ENABLE` is needed again (`homed` is kept if the axis had stopped, as for a
  stationary DISABLE). Any line in those 10 s cancels it (the axis keeps holding). The DTR-drop path is unchanged.
- **Jog dead-man:** 250 ms for JOG and JOGV.
- **Soft travel limit** (when set): see "Soft travel limit" above. It is a carriage position measured from LS1, so it
  holds only once LS1 has been touched this session; the step ISR stops anything about to pass it.
- **Driver reset / faults:** halt, outputs off, `EVT FAULT ...`, TEST/HOME ended; a reset driver is reconfigured and
  stays disabled until ENABLE.
- **Speed changes** never lower AccelStepper's max speed below the current speed (review R-6): SPEED/ACCEL during
  motion apply on stop; JOGV decelerates first; HOME, tests and a MOVE with its own acceleration restore the user's
  SPEED/ACCEL once stopped.

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

**Preconditions:** enabled and idle (`ERR HOME not-enabled|test-running|busy`); no switch parked (tripped with its end
not confirmed: `ERR HOME limit-lsN-end-unknown:jog-off-it`) and not both tripped (`ERR HOME limits-both-tripped:...`);
with a soft travel limit set, referenced (`ERR HOME soft-limit-unreferenced:touch-ls1`). The soft limit is an end to
the search, as a switch is.

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
| Parked on a switch (end not confirmed) | refused | | `ERR HOME limit-lsN-end-unknown:jog-off-it` |
| A switch trips that had read pressed with its end unconfirmed | ISR halt, nothing learned | | `FAIL reason=limit-lsN-end-unknown:jog-off-it` |
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
