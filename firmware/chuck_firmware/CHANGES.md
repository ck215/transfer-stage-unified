# chuck_firmware: changes

## 2026-10-23 — CR-1: the step resolution is a run-time lever (`#RES`, cap `res`)

**Request:** Andrew, relayed by the owner (Ian): the chuck's step resolution, a compile-time `microstepMode = 2` (half
steps: fast, coarse), becomes a lever the operator flips at run time between Fast (coarse, today's 2) and Fine (more
granularity, slower at the same counts/s). The wire is the lead's contract of 2026-10-23; PROTOCOL.md states it as
implemented. Only this sketch changed; `stepper_firmware` and the DC sketch are untouched.

**What changed**
- `microstepMode` moved from a local in `setup()` to file scope (`uint16_t`), still 2 at boot. `setup()` is otherwise
  unchanged, including its max-speed formula.
- `s` answers `DEV: c caps=res` (was `DEV: c`).
- `parseHybridSerial()` gained a branch for a `#` peek (0x23 was unused: it was dropped a byte at a time). It reads the
  line with `Serial.readStringUntil('\n')` (the 2 ms timeout set in `setup()`) and answers `#RES`, `#RES <n>`, or
  `#ERR <CMD> unknown`. Everything else on the wire is byte for byte what it was.
- A set: refused while busy, which means moving (any axis `speed() != 0`, `DPAD_STEP`, or `AUTONOMOUS_ON &&
  !ALL_AXES_DONE`; the contract's first draft also had `distanceToGo() != 0`, dropped the same day: a jog leaves it
  non-zero, so `#RES` was refused after every jog while the station streamed neutral packets); a set to the current value answers `#OK` and writes nothing; otherwise `microsteps(n)` to xUART,
  yUART, zUART, each read back from the driver (the first time this sketch reads a driver register after boot), the
  old value restored on all three on any mismatch; then each position rescaled `(pos * n) / old` in `long` and
  `setMaxSpeed(1600L * n / 2)` per axis.

**Why it is shaped this way**
- The presets are the host's: Fast = 2, Fine = 8 (PROVISIONAL, the Stepper Probe's resolution on the same TMC2209
  stack). The board takes any power of two 1..256, so moving a preset never needs a reflash.
- The readback is the driver-UART truth, not the library's shadow register. TMCStepper returns 0 from a read that
  fails, which decodes as 256 microsteps, so a failed read (`CRCerror`) is treated as a mismatch: without that,
  `#RES 256` on a board whose driver RX is not wired would be confirmed falsely.
- TMCStepper 0.7.3's `microsteps(1)` writes nothing (it spells a full step 0) and its getter answers 0 for a full step;
  the sketch maps 1 <-> 0 both ways, as `firmware/xyz_stage_axis` does. Without it `#RES 1` would always answer
  `driver-readback-mismatch`.
- Step sizes and speeds the host sends stay in counts: a count becomes a finer step, which is the lever's point. At 8
  the same counts/s is a quarter of the physical speed, and the max-speed cap rises to 6400 counts/s.
- No interrupt is involved: the sketch has none, and the command runs from `loop()` like every other.

**Host simulation** (`dev/firmware_sim`, kind `chuck`): `res-query-and-set`, `res-busy`, `res-speed-cap` and
`res-readback-mismatch` are red on the sketch before this change (no `#RES` reply; its `#RES 8` digit stopped a frame
in flight) and green after; `frame-dpad-past-int16`, `watchdog-deadman` and `host-timeout-unarmed` stay green.
`expected-chuck.txt` is the passing run.

**Build** (arduino-cli 1.5.1, `arduino:avr:mega:cpu=atmega2560`, `--warnings all`): flash 20676 -> 22396 bytes, RAM
1293 -> 1449 bytes (the reply strings are in RAM, as the sketch's other literals are); no warning from the sketch
before or after (the four warnings are the AVR core's `new.cpp`).

**Not verified (bench, owner):** NOT FLASHED. That the chuck's driver UARTs read back at all (a boot `POS:` other
than `0,0,0` means `test_connection()` failed, and then every set answers `driver-readback-mismatch`); that the rotor
holds still across an MRES write while enabled; Fine's real speed (the loop rate, not `setMaxSpeed`, may bind first
at 8 microsteps); that 8 microsteps is the right Fine.
