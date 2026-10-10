# XYZ stage on one Mega 2560: protocol 1 (as implemented)

Firmware: `xyz_stage_mega/xyz_stage_mega.ino` plus the standard layer `station_std.h`, on one Arduino Mega 2560
driving all three axes of the 50 mm XYZ stage. The contract is `docs/rebuild/MEGA_STANDARD.md` (owner rulings
2026-10-09). This file states what is particular to this board and points to the contract for the shared parts.
CHANGES.md lists where it departs from the contract, from `stepper_firmware` and from `xyz_stage_axis`, and why.

## Link

- USB Serial at **500000 baud**. The board resets when the port opens (the Mega's DTR auto-reset), so every
  connection starts from boot.
- Every line the board sends ends `\r\n` (`println`, as the POS and DEV lines always have).
- **Units:** counts and counts/s, 1600 counts per mm (1 mm lead, 200 full steps, 8 microsteps), 0.625 um per count.
  A positive count is DIR high (AccelStepper's DRIVER convention, as in `stepper_firmware`). Positions are relative
  to boot, the last `#ZERO` or the last HOME.

## 1. The frame protocol (MEGA_STANDARD §1)

Byte for byte `stepper_firmware`'s, with its field meanings and signs:

| Bytes | Effect |
|---|---|
| `s` | `DEV: m caps=ext1,log,hostto,home,limits,tmc,soft` (§2 below) |
| `e` | Enable: TOFF 4 on each driver present, EN low on its axis, motion held 30 ms and until each driver's CHOPCONF reads back TOFF 4 and 8 microsteps. Idempotent. |
| `d` | Disable: halt, EN high on all three, TOFF 0 on each driver present. Idempotent. |
| `0xAA` + 41 bytes | Jog packet `<BBffffffffff`: start marker, mode, stick X/Y/Z, step size X/Y/Z, D-pad left-right, up-down, bumpers, jog speed. Mode 1 jogs, mode 0 stops. A mode-1 packet with an implausible field (NaN, out of range, a D-pad value not -1/0/1, a speed above 6400) is ignored and does not feed the dead-man. |
| `xs,ys,zs,0,full,slow,brake,xd,yd,zd,manual,auto\n` | Frame. `manual`=1: manual mode, sticks from fields 0-2, speed from field 4. `auto`=1: an autonomous move. Neither: stop. |
| `POS:x,y,z` (out) | Every 100 ms, skipped when the USB TX buffer has less than 35 bytes free. |

Field meanings, kept from `stepper_firmware`:
- **Autonomous move:** each axis moves `step size x distance` counts. The X and Z distances are negated (the wire's
  `xd`, `zd` are the station's signs). Speeds are split by distance, not counts: axis i runs at
  `trunc(full x d_i / |d|)` counts/s, clamped to 4000. A move is relative to where the axis is.
- **Jog:** an axis runs at `stick x jog speed` counts/s. The stick's X is inverted; Y and Z are not. Below the
  0.05 dead zone (on the stick vector), a moving axis carries on to the next multiple of its step size and stops
  there. D-pad left-right steps X, up-down steps Y, the bumpers step Z, by one step size at the jog speed; a
  D-pad held repeats.
- **Stops:** a mode-0 packet, a frame with neither flag, `#STOP` and `d` stop every axis at once, ending manual,
  autonomous, D-pad and HOME alike.
- **Jog dead-man:** in manual (or during a D-pad step), no mode-0/1 packet for 250 ms stops every axis; the coils
  keep holding and manual stays engaged, so the next packet resumes from neutral.
- **No ramps.** Every motion starts and stops at its full rate, as AccelStepper's `runSpeed()` did in
  `stepper_firmware`. The first step goes on the next 50 us tick.
- **This stage only:** motion needs the drivers enabled. A frame or jog for a disabled axis is refused
  (`#EVT REFUSED A reason=not-enabled` once the host speaks ext1) and moves nothing. `stepper_firmware` counted
  pulses with EN high, and that is not wanted on a stage that homes.

## 2. Identity and caps (MEGA_STANDARD §2)

`s` answers `DEV: m caps=ext1,log,hostto,home,limits,tmc,soft`. Which hardware is present is per axis, in `#INFO`.

## 3. The `#` channel (MEGA_STANDARD §3)

Commands and replies are the contract's. On this board:

- **Nothing that starts with `#` is sent until the host has sent a `#` command this firmware knows.** A host that
  speaks only the frame protocol sees exactly the old bytes. Events raised before that are dropped, except
  `#EVT FAULT tmc-missing A` and `#EVT FAULT pin-map`, which are held and sent right after the first reply.
- A `#` line nobody knows is answered `#ERR <CMD> unknown-command` only once the link speaks ext1. Before that, a
  stray `#` in a desynced jog stream must not produce output.
- Lines: at most 79 characters (`#ERR LINE too-long`). A `#` line with no byte for 50 ms is dropped unanswered.
  Commands and arguments are case-insensitive.
- `#INFO`: the contract's keys, plus `free_ram=<bytes> tick_us=50 max_rate=4000 parked_travel=800 home_dir=-1
  home_flag_level=1 pins=0|1`. `x_limits` is the session's value (EEPROM, or seen-once); `x_home` is EEPROM's.
- `#HOSTTIMEOUT <ms>` clamps to 250..5000 and replies the value applied. 0, a negative value or a non-number gets
  `#ERR HOSTTIMEOUT bad-arg`.
- `#AXISCFG <A> limits=0|1 home=0|1`: either key or both. Refused (`busy`) while enabled, moving or homing. Stored at
  EEPROM 16..22: `0x5A`, one byte per axis (bit 0 limits, bit 1 home), then each byte's complement. A blank or
  corrupt block reads as limits=0 home=0. The value is read back before `stored=1`. A new value forgets that axis's
  learned ends; a switch pressed at that moment is parked.
- `#SOFTLIMIT <A> [counts]` (cap `soft`; §4 below): no count, a query; `0..160000` sets the soft travel limit, 0 =
  none. Refused (`busy`) while enabled, moving or homing; `bad-arg` otherwise. Stored at EEPROM 23..47: `0x5D`, a
  4-byte little-endian count per axis, then each count's complement; read back before `stored=1`. Reply:
  `#OK SOFTLIMIT axis=A counts=<n> ref=<0|1>[ ls1=<counts>][ lim=<counts>][ damaged=1][ stored=1]`. `#INFO` adds per
  axis `x_soft=<counts> x_soft_ref=<0|1>`, then `x_soft_lim=<counts>` when referenced with a limit set and
  `x_soft_damaged=1` when that entry is damaged.
- `#LOG`: level 0 sends only LIMIT, SOFTLIMIT, REFUSED, FAULT, HOMED and HOME FAIL; level 1 every `#EVT` (the default); level 2
  adds `#EVT DBG`: commands received (not `#HB`), frames, manual engaged, driver on/off/configured, and where each
  axis stopped.

### Events (`#EVT ...`)

| Line | When |
|---|---|
| `LIMIT A lsN pos=<counts> end=<+1\|-1\|0>[ learned=travel travel=800\| pressed_both_ways=1 travel=800][ seen=1]` | the interlock halted axis A at switch N |
| `LIMIT A lsN seen=1` | a switch with limits=0 tripped while moving and armed the interlock, without a halt |
| `SOFTLIMIT A referenced ls1=<counts> lim=<counts>` | LS1 tripped toward its end on an axis with a soft limit: its reference (§4) |
| `SOFTLIMIT A stopped pos=<counts> lim=<counts>` | a frame, jog, D-pad step or HOME search was stopped on A's soft limit |
| `SOFTLIMIT A lost reason=<why>` | A's reference was lost (a disable while it turned, a driver fault on A, `#AXISCFG A`): touch LS1 again |
| `REFUSED A reason=<why>` | a frame or jog the frame protocol cannot answer. Frames are refused whole: one line for each axis they would have moved. A jog is refused once, until that input returns to neutral. |
| `FAULT tmc-missing A` | no TMC2209 answered at A's address at boot: that axis is never enabled |
| `FAULT driver-reset A reconfigured` / `FAULT overtemp A ot= otpw=` / `FAULT short A s2g= s2vs=` / `FAULT uart-lost A` | a driver fault (§4 below), sent once while it persists |
| `FAULT driver-readback-mismatch A toff= mres=` | after `e`, A's CHOPCONF did not read back TOFF 4 and 8 microsteps |
| `FAULT host-timeout` / `FAULT host-timeout-disabled silent_ms=` | the host timeout (§4) |
| `FAULT pin-map` | the step ISR's port bits are not the pins in the constants block: drivers never enabled (a build error) |
| `TMC A detected version=0x21` | a driver that was missing at boot answered later (VM switched on). It is configured and stays disabled until the next `e` that follows a `d`. |
| `HOME A phase=seek\|backoff\|approach\|edge ...`, `HOMED A edge=<counts> pos=0`, `HOME FAIL A reason=...` | HOME (§5 below) |
| `DBG ...` | LOG 2 |

## 4. Safety behaviour (MEGA_STANDARD §4)

- **Limit interlock, in the step ISR (Timer1, every 50 us).** Every tick samples all nine sensor pins (LS1, LS2, HOME
  per axis). A level counts once it has held for 4 ticks (200 us), so a switch is acted on 150-200 us after it
  closes. The halt is decided before that tick's step pulse, so no pulse follows it. The learning rules are
  `xyz_stage_axis`'s (its PROTOCOL.md, "Safety behaviour"), per axis:
  - the first filtered change while moving teaches the end: a release teaches `-dir`;
  - a trip from clear teaches `dir`, and the axis halts there;
  - a trip after the switch has read pressed with its end unconfirmed halts and teaches nothing (review R-4).
  Both switches of one axis pressed halts that axis. The other axes are unaffected.
- **Parked switch** (pressed, end unknown: at power-up, or after `#AXISCFG`): frames, D-pad steps and `#HOME` for
  that axis are refused (`limit-lsN-end-unknown:jog-off-it`). The stick moves it at most 160 counts/s, and at most
  800 counts (0.5 mm, PROVISIONAL) either way from where the switch was found pressed. At the end of that window
  the ISR halts and learns that end (`learned=travel`). Out of the window both ways, it halts and confines the axis
  (`pressed_both_ways=1`, `limit-lsN-pressed-both-ways:check-switch`). A running jog is left to the ISR; the
  firmware checks a jog only when it starts from rest or reverses.
- **limits=0** (AXISCFG, or a blank EEPROM): no interlock and no events for that axis, the old behaviour.
  **Seen-once:** a switch of such an axis that trips while it moves arms its interlock for the session
  (`... seen=1`; `#INFO` `limits=1`). It then applies the rules above, including the R-4 rule, so a chatter re-trip
  after a release teaches nothing. Never written to EEPROM.
- **home=0:** `#HOME` refused `no-home-sensor`.
- **Soft travel limit** (2026-10-10, X-14; `#SOFTLIMIT`, cap `soft`), for an axis whose carriage cannot reach one of
  its switches (a probe that meets its fixture first). The limit is `counts` from LS1's reference, away from the end
  LS1 guards. The reference is the count at which LS1 tripped while the axis moved toward that end, with the end known
  and the interlock armed, taken in the step ISR (`#EVT SOFTLIMIT A referenced`); every such trip refreshes it.
  - **In force once referenced.** The step ISR makes no step past the limit: a frame, jog, D-pad step or HOME search
    stops on it (`#EVT SOFTLIMIT A stopped`), exactly, since nothing here ramps. At the limit, motion further out is
    refused `soft-limit` (`#EVT REFUSED`); toward LS1 nothing changes.
  - **Before the reference** (since boot, `#AXISCFG A`, or a loss below), with a limit set: only the stick moves that
    axis, or a frame or D-pad step toward LS1 once its end is known (that is how the reference is taken). Frames and
    D-pad steps otherwise, and `#HOME`: `soft-limit-unreferenced:touch-ls1`. With limits=0 the reference never comes,
    so that axis moves by stick only: a soft limit needs LS1.
  - **Lost** by homed's rule for the step count (a disable while that axis turned, a driver fault on it) and with the
    ends on `#AXISCFG`: `#EVT SOFTLIMIT A lost reason=`. `#ZERO` and HOME move it with the origin; stops keep it.
  - **A damaged EEPROM entry** (complement, range or magic wrong; an erased block is "none") is not "none": `#INFO
    x_soft_damaged=1`, and that axis moves by stick only (`soft-limit-eeprom-damaged:set-SOFTLIMIT`) until
    `#SOFTLIMIT` writes it again. The opposite of AXISCFG's absent-is-inert, on purpose: the owner's limit is unknown.
  - HOME takes the soft limit as an end: a search reverses there once, and a second end fails
    `no-edge-between-limits`; in backoff or approach it fails `soft-limit-during-backoff|approach`.
- **tmc=0:** that axis's EN stays high; `e` skips it. No diagnostics are read for it; it is probed again every 1 s.
- **Driver faults**, read over Serial2 (GSTAT and DRV_STATUS of each driver, every 50 ms enabled or busy, every
  250 ms otherwise): a reset (VM lost and back), over-temperature or its pre-warning, a short to ground or supply,
  or three missed replies while enabled. Each stops every axis, drives every EN high, sets TOFF 0, and sends
  `#EVT FAULT <what> A`; `e` is needed again. A reset driver is reconfigured at once and left disabled.
- **Host timeout:** armed only by `#HOSTTIMEOUT`. No byte from the host for that long while anything moves, homes
  or D-pad-steps stops every axis (`#EVT FAULT host-timeout`; coils hold). Still silent 10 s after that, it
  disables every axis (`#EVT FAULT host-timeout-disabled`); `e` is needed again. Any byte cancels it.
- **Jog dead-man:** 250 ms (§1).
- **Watchdog (1 s):** petted only while `millis()` advances, the step ISR is ticking, and the host UART's receiver,
  transmitter and RX interrupt are on. A dead host UART also halts and disables at once. The board then resets
  within ~1 s and boots with EN high.
- **homed** (per axis): 1 only after a successful HOME. Cleared at boot, when a HOME starts, by `#ZERO`, by a driver
  fault on that axis, and by a disable (`d`, a fault, the host-timeout disable) while that axis was turning. Kept
  through stops and limit halts.

## 5. HOME (`#HOME <A>`)

The Teensy axis firmware's sequence (`firmware/xyz_stage_axis/PROTOCOL.md`, "HOME") in counts, one axis at a time.
- **Constants:** `HOME_DIR = -1`, `HOME_FLAG_LEVEL = 1`, seek 1600 counts/s (1 mm/s), approach 160 counts/s
  (0.1 mm/s), backoff 800 counts, approach reach 800 + 800 counts, one search leg 88000 counts (55 mm).
- **Time limit:** 1.5 x the worst-case plan + 10 s (193.75 s; `limit_s=193` in the seek event).
- **Preconditions,** in this order:
  - `bad-arg`;
  - `no-home-sensor` (home=0);
  - `no-limits` (the interlock is not armed: a 55 mm search would end on a hard stop);
  - `not-enabled`;
  - `busy` (anything moving, a D-pad step, the `e` settle, another HOME);
  - `limits-both-tripped:check-wiring`;
  - `limit-lsN-end-unknown:jog-off-it`;
  - with a soft limit, `soft-limit-unreferenced:touch-ls1` or `soft-limit-eeprom-damaged:set-SOFTLIMIT`.
- **Reference edge R:** the filtered home level changing to `HOME_FLAG_LEVEL` while moving `HOME_DIR`. Moving the
  other way, R is where the level leaves it.
- **Sequence:**
  1. `#OK HOME started`.
  2. **seek** toward R: `-HOME_DIR` from on the flag, or from beyond an earlier HOME's zero. The ISR stops the axis
     on R itself. A switch reverses the search once; a second switch means there is no R.
  3. **backoff** to R + 800 counts on the clear side, at the seek rate.
  4. **approach** in `HOME_DIR` at 160 counts/s from rest. The ISR stops the pulses on the edge and that position
     becomes 0.
  5. `#EVT HOME A phase=edge ...`, then `#EVT HOMED A edge=<R in the old counts> pos=0`.
- **Failures** (`#EVT HOME FAIL A reason=`):
  - `no-edge-between-limits`;
  - `no-edge-within-search`;
  - `edge-too-close-to-limit`;
  - `limit-during-backoff`, `limit-before-approach`, `limit-during-approach`;
  - `flag-at-backoff`, `edge-lost`, `time-limit`;
  - a parked switch;
  - `stop` (a stop frame, packet, `#STOP` or a host timeout), `disabled`, or a driver fault.
  Every FAIL leaves the axis stopped, `homed=0`.
- Frames and jogs during HOME are refused `busy`. A jog packet at neutral is accepted and does nothing to HOME.

## 6. Step generation

Timer1 in CTC mode at 20 kHz (50 us). Per axis, a 31-bit phase accumulator advances by `rate x 2^31 / 20000` each
tick, and a step is due when bit 31 sets. No float math runs in the ISR (the increment is computed in `loop()`).
Every step period is a whole number of ticks within one tick of the ideal (6 or 7 ticks at 3200 counts/s). STEP
pulses last from the axis's step to the end of the ISR (at least ~2 us, on Z; the TMC2209 needs 100 ns). The ISR masks its own interrupt and re-enables
the others, so the 500000-baud host UART (40 us of slack) is never held off by a long tick. A tick that overruns
makes the next one late; nothing is lost but time.

CHANGES.md gives the cycle budget measured on the compiled code (2026-10-10, with the soft limit):
- 12.5 us for a tick with nothing to do;
- at most 35.4 us for a tick in which all three axes step and stop with no sensor activity;
- 7.2-34.9 us for each axis whose sensor changed or whose switch is pressed while it moves.

## 7. Bench checks (owner)

MEGA_STANDARD §7, plus:
- 3200 counts/s on all three axes at once with no lost steps. There are no ramps: a full-rate start and stop at
  400 full steps/s. Check that a long move returns to its start.
- STEP and DIR on a scope: DIR set ahead of the first STEP; pulses of several us.
- `#INFO free_ram=` with all three drivers configured (expected ~6100 bytes).
- `HOME_DIR` and `HOME_FLAG_LEVEL` against each axis's flag (the `xyz_stage_axis` procedure).
- `PARKED_TRAVEL` (800 counts) against each switch's overtravel.
- The soft limit, on an axis that needs one: with none set, jog from LS1 toward the obstruction and read the travel
  (`#SOFTLIMIT A` after the trip gives `ls1=`; POS minus it is the travel); set it with a margin; check a frame, the
  stick and a D-pad step each stop on it, that it survives a power cycle (`#INFO x_soft=`) and needs LS1 again after
  one.
- Pull VM while enabled: `#EVT FAULT uart-lost A`. Restore VM: `#EVT FAULT driver-reset A reconfigured`, then the
  axis stays off until `d`, `e`.
- The watchdog's UART and tick checks cannot be provoked from outside (unplugging USB does not turn the UART off);
  the host simulation covers them.
