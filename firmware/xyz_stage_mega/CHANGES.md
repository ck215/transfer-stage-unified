# xyz_stage_mega: what it takes from where, and what differs (2026-10-09)

New sketch. The frame protocol is `firmware/stepper_firmware`'s (parsing, field meanings, signs, dead-man,
watchdog, the 'e'/'d' paths). The interlock, parked-switch rules, HOME sequence and host timeout are
`firmware/xyz_stage_axis`'s (2026-10-09, review R-4 included), per axis and in counts. The `#` layer is new and
lives in `station_std.h`, written to be copied unchanged into the Stepper Probe, DC and Chuck sketches in phase 2
(MEGA_STANDARD §6). Bench values (run current 600 mA, hold 0.5, the 1000 mA clamp, 8 microsteps, spreadCycle, the
HOME speeds and distances, the 200 us filter, the 250 ms dead-man) are unchanged. `PARKED_TRAVEL` (800 counts,
0.5 mm) stays PROVISIONAL.

## Why a new step generator

AccelStepper steps from whoever calls it. Each `runSpeed()` reads `micros()`, and each step costs three
`digitalWrite()`s and a `delayMicroseconds(1)` (about 15 us on a 16 MHz AVR); `run()` adds float math per step. Called
from `loop()`, as in `stepper_firmware`, a step waits for `loop()` to come round, behind the serial parsing and the
POS print. The interlock must be checked every tick, which needs a timer ISR, and AccelStepper called three times per
tick from a 20 kHz ISR would not fit either. So AccelStepper is not used.

Timer1 (CTC, 20 kHz) runs one ISR with a 31-bit phase accumulator per axis. There is no float in the ISR: the
increment is computed in `loop()`. There are no ramps, as in the frame protocol's `runSpeed()`. Measured on the
compiled code (`dev/firmware_sim/avr/isr_cycles.py`: longest and shortest paths through the ISR's control flow,
ATmega2560 cycle counts, interrupt entry included):

| Tick | Cycles | us at 16 MHz |
|---|---|---|
| nothing to do | 200 | 12.5 (25 % of the tick) |
| worst with no sensor activity: three axes stepping and stopping at once | 504 | 31.5 |
| the out-of-line slow path, per axis whose sensor changed or whose switch is pressed while it moves | 106-468 | 6.6-29.3 |
| theoretical worst, every slow path at its longest in one tick | 1890 | 118 |

Three axes at 3200 counts/s are estimated (from those paths) at about 20 us per tick on average, 40 % of the CPU. The rare path lives in a separate
`noinline` function. Inlined three times, it made the ISR save 26 registers on every tick: 286 cycles idle, and a
1705-cycle worst case. avr-gcc -Os also turned the sensor bit extraction into a 4-iteration shift loop, so the
pins are read with `__builtin_avr_insert_bits` instead. The ISR masks itself and runs with interrupts enabled, so
the 500000-baud host UART (two bytes, 40 us, of slack) is never held off. A tick that overruns only makes the
next one late.

## Departures from MEGA_STANDARD (each a ruling to confirm)

| Where | This firmware | Why |
|---|---|---|
| §5 limit switches "external interrupts" | The pins are the INT0-5 pins as mapped, but the interlock samples them in the step ISR each tick; the INTn/PCINT vectors are unused. | The 200 us filter needs periodic samples anyway. A sampled level cannot miss an edge. An interrupt per contact bounce or chopper-noise edge would load the CPU and jitter the steps. It is still interrupt context, every tick, before the step. |
| §3 `#HOME` needs `home=1` | It also needs the interlock armed (`#ERR HOME no-limits`). | With limits=0, a 55 mm search that misses the flag ends on a hard stop. |
| §4 `tmc=0` "never enabled" | Still never enabled, but a missing driver is probed every 1 s. One that answers later (VM switched on after the Mega) is configured, reported `#EVT TMC A detected`, and enabled by the next `e` after a `d`. | Otherwise a bench powered USB-first loses its axes until a reboot. |
| §4 driver faults | Adds `FAULT driver-readback-mismatch A`: after `e`, each driver's CHOPCONF must read back TOFF 4 and 8 microsteps before motion (the 30 ms settle waits for it). Adds `FAULT pin-map`. | The Teensy's ENABLE reads back its microsteps the same way. A lost write would otherwise count steps that never turned the rotor, or scale them wrong. |
| §3 "exactly one per command" | A `#` line nobody knows gets no reply until the host has spoken ext1. Overlong lines: `#ERR LINE too-long`. | A desynced jog packet can start with `#`; an old host must see no new bytes. |
| §5 "LIMITS NC/NO as on the Teensy" | Contact type is the constant `LIMITS_NC` (false: NO). There is no `#LIMITS` command (the §3 table has none). Hence `limits-both-tripped:check-wiring`, not the Teensy's `...-or-LIMITS-NC\|NO`. | Not in the command table. |
| §3 `#HOSTTIMEOUT` | Replies `ms=` with the clamped value; 0 is `bad-arg`. | The table gives the range, not the out-of-range rule. |
| §3 `#INFO` | Extra keys: `free_ram tick_us max_rate parked_travel home_dir home_flag_level pins`. | Bench check of the flashed values. |
| Lines | Every line ends `\r\n`. | `println`, as POS and DEV always have. |
| Events before ext1 | Dropped, except `tmc-missing` and `pin-map`, which are held. | "No # output unless asked." |

## Differences from stepper_firmware (frame protocol, edge cases)

| Where | stepper_firmware | xyz_stage_mega | Why |
|---|---|---|---|
| Identity | `DEV: s` | `DEV: m caps=...` | MEGA_STANDARD §2 |
| Drivers | three buses (Serial1/2/3) at address 0 | one bus, Serial2, addresses 0-2; writes queued and coalesced, never blocking; status read asynchronously | MEGA_STANDARD §5; `TMCStepper` blocks 2 ms per write and more per read |
| Speed clamp | 6400 counts/s | 4000 (the Teensy's 2.5 mm/s) | this stage's ceiling; the station sends at most 3200 |
| Motion while disabled | pulses counted with EN high (phantom steps) | refused, `#EVT REFUSED A reason=not-enabled` | a homed stage must not count steps it did not make |
| `x_steps` | `int`: wraps above 32767 counts (20.5 mm) on AVR | `long` | a full 50 mm move is 80000 counts |
| Position at boot | `test_connection()`'s result (0, 1 or 2) | 0 | |
| A frame line with no newline | parsed after 2 ms (`readStringUntil`) | the same, without blocking `loop()` | |
| A jog packet short of 42 bytes | waits forever (a following `d` is swallowed into it) | its 0xAA is dropped after 5 ms | resync |
| A frame longer than 79 characters | parsed | a stop | no station frame is near that long |
| A frame during a D-pad step | both run; manual re-engages after | the frame ends the D-pad step | |
| A frame axis whose speed truncates to 0 | target set, never reached (autonomous never ends) | treated as done | |
| `Serial2.println("MANUAL MODE ENGAGED...")` | sent onto the X driver's UART | removed | |
| A running jog | n/a | the interlock owns it; the jog is checked only from rest or on reversal | the parked-window end must be the ISR's decision (first draft defect, below) |
| Watchdog pet | millis advancing, UART on | also: the step ISR ticking | a dead step timer means a dead interlock |

## Differences from xyz_stage_axis (behaviour ported)

- Counts instead of mm, three axes on one board, one HOME at a time.
- No ramps anywhere: HOME's seek stops on the reference edge in the ISR instead of decelerating past it. The
  frame protocol has no acceleration.
- No TEST, STREAM, MOVE, JOGV, SPEED/ACCEL, MICROSTEPS or CURRENT commands; the frame protocol and §3 replace them.
- `homed` is kept through a disable while stationary and lost on one while turning, as there.

## Verification (2026-10-09; nothing run on hardware)

- **Compile:** `arduino-cli compile --fqbn arduino:avr:mega:cpu=atmega2560 --warnings all` (core 1.8.8,
  TMCStepper 0.7.3; AccelStepper not used). 0 warnings from the sketch and its header; the core's own `new.cpp`
  gives its usual 4 `-Wunused-parameter`. 33222 bytes flash (13 %), 1507 bytes of 8192 static RAM.
- **Stack:** 466 bytes worst from `main`, 33 for the step ISR, 22 for one nested UART ISR
  (`dev/firmware_sim/avr/stack_depth.py` on the ELF). No heap. That leaves about 6100 bytes free with all three
  drivers configured; `#INFO free_ram=` reads it on the bench.
- **Host simulation** (`dev/firmware_sim`, kind `mega`; `expected-mega.txt`): 22 scenarios, 239 checks, all
  passing. On `stepper_firmware` as the base, 19 of 21 are red. The two green there assert behaviour both firmwares
  share, and each is red on a deliberately broken Mega build (`MUTATE`):
  - the X stick not inverted -> `parity-frame-protocol`;
  - the dead-man at 2.5 s, or the UART check removed -> `watchdog-deadman`;
  - the host timeout armed at boot -> `host-timeout-unarmed`;
  - review R-4's `!seen` rule removed -> `release-at-rest-then-chatter` (into the hard stop at 1.04 mm/s);
  - the slow path run only on sensor changes -> `parked-driven-into` (past the 0.5 mm window to the hard stop).
- **Defect found by the simulation in the first draft:** `loop()` re-checked a running jog against the parked
  window. It refused the jog as `pressed-both-ways` one tick before the ISR halted it and learned that end
  (`parked-driven-into` red: no `learned=travel`, a wrong reason). Fixed: a running jog is the ISR's; the
  `jog-recheck-every-loop` mutant restores the defect and is red.
- **Not modelled:** ISR preemption inside `loop()` (the cycle budget above stands in for it), lost steps, real
  switch and sensor noise, UART timing (bytes arrive at once), DTR auto-reset.
