# Firmware simulation: the axis interlock on the host

Compiles a Teensy sketch natively (clang++, the real AccelStepper source, the
stubs in `include/` and `stubs.cpp`) and drives its step ISR against a fake
clock and scripted switch, home-flag and host-line traces. Each scenario runs
in its own process. Built for review R-4 and the owner's two rulings of
2026-10-09 (a parked switch allows only a slow jog off; a host silent 10 s
after a host-timeout stop disables the driver).

```
dev/firmware_sim/build.sh firmware/xyz_stage_axis/xyz_stage_axis.ino /tmp/sim-station station
dev/firmware_sim/run.sh /tmp/sim-station /tmp/sim-logs        # one line per scenario; "failed scenarios: 0"
dev/firmware_sim/build.sh dev/equipment_test/stepper_validator/stepper_validator/stepper_validator.ino /tmp/sim-validator validator
dev/firmware_sim/run.sh /tmp/sim-validator /tmp/sim-logs-v
```

`expected-station.txt` and `expected-validator.txt` are the passing runs at
the fix (14 of 14 each). Before the fix both sketches failed 8 of 14, seven by
driving into the hard stop. The station sketch has a fifteenth scenario,
`move-accel-coarrival` (2026-10-09: MOVE/MOVETO's optional per-move
acceleration, and how close a vector Step's axes arrive with it); the
validator, which has no per-move speed or acceleration, does not list it.
Seven more are the soft travel limit (2026-10-10, X-14: `soft-limit-*`,
SOFTLIMIT), run on both sketches: the stage model gains an obstruction short of
a switch (`wall`, `wallLo`: a step past it is lost, as at a hard stop), LS1
wired at the + end (`ls1Plus`) and the EEPROM image (`sim_eeprom()`). The limit
is 28 mm on the station sketch and 12 mm on the validator (its MOVE stops at
15 mm), with the wall 2 mm beyond. Red on each sketch before SOFTLIMIT (7 of 7
on both; a jog ran into the wall at 2.5 mm/s, HOME's search and TEST LIMITS at
1 mm/s), green after: station 22 of 22, validator 21 of 21. Set `ACCELSTEPPER_SRC` if AccelStepper is not in
`~/Documents/Arduino/libraries`. Build output goes beside the binary you name
and in `obj/` here (ignored). Run it after any change to a sketch's interlock,
HOME or host-timeout code. It is a host model: bench checks stay the owner's.

## The bench diagnostic limit_seek (`seek_sim.cpp`, 2026-10-10)

`dev/equipment_test/diagnostics/limit_seek` sweeps one axis slowly until a switch changes state, classifying each
input with a pull-up and a pull-down. Kind `seek` builds it on the same stubs, which now model a pin as OPEN / GND /
HIGH read through whichever pull the sketch selects (`g_simPinDrive`; the other sketches still set `g_simPinIn`,
and their results are unchanged), take the sketch's own timer period, and print with `Serial.printf`.

```
dev/firmware_sim/build.sh dev/equipment_test/diagnostics/limit_seek/limit_seek.ino /tmp/sim-seek seek
dev/firmware_sim/run.sh /tmp/sim-seek /tmp/sim-logs-seek        # 4 scenarios; "failed scenarios: 0"
```

`expected-seek.txt` is the passing run (X-14): no SEEK before `MAXTRAVEL`, none longer than it; with the soft limit
stored, a sweep onto LS1 references it, a sweep past it is shortened onto it short of a wall 2 mm beyond, the
reference is lost with OFF while moving, and a damaged block refuses every SEEK. All four are red on the sketch before
(it swept 10 mm with no MAXTRAVEL and ran into the wall: 32000 steps lost).

## The XYZ Mega (`avr/`, 2026-10-09)

`firmware/xyz_stage_mega` runs on an ATmega2560, so it builds against a
separate stand-in for the AVR core (`avr/include`: port registers as objects
that report STEP edges, Timer1, `ISR()`, the watchdog, EEPROM, PROGMEM, the
four UARTs) and its own simulation, `avr/sim_mega.cpp`: three axes with the
same switch, chatter, home-flag and hard-stop model as above, and three
TMC2209 at addresses 0-2 on Serial2 that echo, check CRCs, keep their
registers, answer reads, and can lose VM, reset, overheat, short or ignore
writes. Fake clock in 5 us steps; Timer1's ISR at the period the sketch
programs, `loop()` every 100 us.

```
dev/firmware_sim/build.sh firmware/xyz_stage_mega/xyz_stage_mega.ino /tmp/sim-mega mega
dev/firmware_sim/run.sh /tmp/sim-mega /tmp/sim-logs-mega      # 27 scenarios; "failed scenarios: 0"
dev/firmware_sim/run.sh /tmp/sim-mega-stepref /tmp/sim-logs-base   # the same scenarios on stepper_firmware
```

`build.sh ... mega` also builds `<out>-stepref`: `firmware/stepper_firmware`
on the same stand-ins (with the Arduino IDE's generated prototypes
emulated). The `parity-frame-protocol` scenario runs one host script on
both and compares what every phase did to the STEP/DIR/EN pins; the other
scenarios, run on the reference, are its base (19 of 21 red;
`host-timeout-unarmed` and `watchdog-deadman` assert behaviour the two
firmwares share). `MUTATE='<sed program>'` builds the sketch with a
deliberate defect, to show a scenario red. `expected-mega.txt` is the
passing run. `seen-once-reboot` and `axiscfg-persists-reboot` reboot by
re-executing the binary with the EEPROM image kept (a state file beside the
binary).

The four `soft-limit-*` scenarios (2026-10-10, X-14) drive `#SOFTLIMIT` and
the cap `soft` against a wall short of LS2 (`Axis::wall`, `wallLo`): red on the
sketch before it (X ran into the wall at 1 mm/s), green after.

The reference builds (`-stepref`, and `stepref` for any sketch) spell the
sketch's `int` and `unsigned int` as `int16_t`/`uint16_t`: avr-gcc makes them
16 bits on the ATmega2560, clang 32, and an overflow the board has must show
on the host too (2026-10-10, X-19). `frame-dpad-past-int16` sends a frame of
16 x 2100 = 33600 counts per axis and a D-pad step of size 40000: red on
`stepper_firmware` and `chuck_firmware` before the fix (the move wrapped to
-31936 and ran into the - hard stop; the D-pad's 40000 became -25536), green
after; the xyz Mega always held both in 32 bits. The chuck is its own build:

```
dev/firmware_sim/build.sh firmware/chuck_firmware/chuck_firmware.ino /tmp/sim-chuck stepref
/tmp/sim-chuck frame-dpad-past-int16                                  # PASS; its other scenarios are the base (red)
```

`avr/isr_cycles.py` and `avr/stack_depth.py` read `avr-objdump -d` of the
compiled ELF: the step ISR's best and worst cycle counts (longest path
through its control flow, callees included; `--no-call` for the fast path
alone) and the worst stack depth from `main` and each ISR. Re-measure after
any change to the ISR (CHANGES.md in the sketch folder has the numbers).
