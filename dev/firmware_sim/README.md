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
validator, which has no per-move speed or acceleration, does not list it. Set `ACCELSTEPPER_SRC` if AccelStepper is not in
`~/Documents/Arduino/libraries`. Build output goes beside the binary you name
and in `obj/` here (ignored). Run it after any change to a sketch's interlock,
HOME or host-timeout code. It is a host model: bench checks stay the owner's.

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
dev/firmware_sim/run.sh /tmp/sim-mega /tmp/sim-logs-mega      # 22 scenarios; "failed scenarios: 0"
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

`avr/isr_cycles.py` and `avr/stack_depth.py` read `avr-objdump -d` of the
compiled ELF: the step ISR's best and worst cycle counts (longest path
through its control flow, callees included; `--no-call` for the fast path
alone) and the worst stack depth from `main` and each ISR. Re-measure after
any change to the ISR (CHANGES.md in the sketch folder has the numbers).
