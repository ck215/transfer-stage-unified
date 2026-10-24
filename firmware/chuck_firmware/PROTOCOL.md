# Chuck Positioner on a Mega 2560: the wire (as implemented, 2026-10-23)

Firmware: `chuck_firmware/chuck_firmware.ino`, one Arduino Mega 2560 driving the chuck's three axes through three
TMC2209 (X on Serial2, Y on Serial3, Z on Serial1, each at UART address 0). The frame protocol is
`firmware/stepper_firmware`'s, byte for byte. The one addition is the `#RES` lane (cap `res`), the owner-sanctioned
extension of 2026-10-23 (the lead's contract; `docs/rebuild/MEGA_STANDARD.md` §2-3 for the framing). CHANGES.md says
what changed and why.

## Link

- USB Serial at **500000 baud**; the board resets when the port opens (DTR auto-reset), so every connection starts
  from boot: 2 microsteps, positions 0.
- Every line the board sends ends `\r\n`.
- **Units:** counts and counts/s. A count is one microstep at the current resolution: a half step at the boot
  default 2, an eighth at 8. Step sizes, distances and speeds from the host are counts and are never rescaled.

## The frame protocol (unchanged)

| Bytes | Effect |
|---|---|
| `s` | `DEV: c caps=res` (was `DEV: c`; Setup reads the first letter, so an old host still names it Chuck Positioner) |
| `e` | Enable: TOFF 4 on all three drivers, EN low, motion held 30 ms. Idempotent. |
| `d` | Disable: halt, EN high, TOFF 0 on all three. Idempotent. |
| `0xAA` + 41 bytes | Jog packet `<BBffffffffff`. Mode 1 jogs, mode 0 stops; an implausible mode-1 packet is ignored. |
| `xs,ys,zs,0,full,slow,brake,xd,yd,zd,manual,auto\n` | Frame: manual, autonomous move, or (neither flag) stop. |
| `POS:x,y,z` (out) | Every 100 ms, skipped when the USB TX buffer has less than 35 bytes free. |

The jog dead-man (250 ms), the watchdog and the unconditional stops are as `stepper_firmware`'s. Every axis's speed
is capped by `setMaxSpeed(1600 * microsteps / 2)` counts/s: 1600 at the boot default 2, 6400 at 8.

## `#RES`: the step resolution (cap `res`)

The `#` lane on this board carries `#RES` and nothing else (no `ext1`: no INFO, LOG, HOSTTIMEOUT, HB, HOME, ZERO,
STOP). Host to board, one line:

    #RES\n           query
    #RES <n>\n       set; n a power of two, 1..256

Board to host, exactly one line per command, never a `#` line unasked:

| Reply | When |
|---|---|
| `#OK RES microsteps=<n> pos=<x>,<y>,<z>` | a query; a set that took; a set to the current value (a no-op, nothing written) |
| `#ERR RES bad-arg-power-of-2-1..256` | the argument is not decimal digits alone, or not a power of two in 1..256 |
| `#ERR RES busy` | a set while anything moves (below); nothing changes |
| `#ERR RES driver-readback-mismatch` | a driver did not read back the new value; the old one is written back to all three and reads back |
| `#ERR RES driver-revert-failed` | as above, but a driver did not read back the old value either: the board disables (the `d` path: halt, EN high, TOFF 0 on all three) and keeps the old resolution |
| `#ERR <CMD> unknown` | any other `#<CMD>` line; nothing else happens |

- `pos` is the three positions after the command, in the new counts: the numbers the next `POS:` line carries.
- **Busy means moving:** any axis with `speed() != 0`, a D-pad step in progress, or an autonomous move not yet done.
  A jog that has ended (sticks centred, speed 0) is not busy, though the station still streams neutral packets and
  `distanceToGo()` is left non-zero by the jog (contract amended 2026-10-23). A query answers while busy. Enabled or disabled does not matter (standstill does: the TMC2209 keeps its
  microstep counter across an MRES write, so the rotor does not move).
- **A set** writes the new resolution to all three drivers over their UARTs, then reads each back over the same UART
  (a read that fails its CRC or gets no reply counts as a mismatch). On success each position becomes
  `(pos * n) / old` in `long` arithmetic (truncated toward zero: going coarser can drop a fraction of a count), and
  each axis's max speed `1600 * n / 2`. On a mismatch, positions and max speeds are untouched, and the revert is read
  back as well: if a driver still disagrees, the axes would be mixed, so the board disables its drivers and answers
  `driver-revert-failed` (R-6). The host treats that as a board fault: the stop latches until a person has looked.
- `n = 1` is a full step; TMCStepper spells it 0, and the sketch maps it both ways.
- `<CMD>` is the first word after `#`, upper-cased, cut at 16 characters, a byte outside printable ASCII sent as `?`.
- A set blocks `loop()` for its UART transactions, about 20 ms when every driver answers and up to about 70 ms when
  none does (TMCStepper's retries and timeouts); nothing moves meanwhile, as `e` and `d` also block for their writes.
- That block is an RX-buffer hazard: the 50 Hz jog stream keeps arriving into the Mega's 64-byte USB receive buffer,
  so two packets in one block can overflow it and drop bytes. The station therefore pauses its jog stream from just
  before `#RES <n>` until the reply or its timeout (the 250 ms dead-man holds position meanwhile), and re-sends a stop
  that landed in that window.

## For the host

Never send a `#` byte to a board whose identity has no `caps=` (an old chuck): it drops `#RES` byte by byte but reads
the digits of `#RES 8` as a stop frame.
