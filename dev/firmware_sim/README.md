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
driving into the hard stop. Set `ACCELSTEPPER_SRC` if AccelStepper is not in
`~/Documents/Arduino/libraries`. Build output goes beside the binary you name
and in `obj/` here (ignored). Run it after any change to a sketch's interlock,
HOME or host-timeout code. It is a host model: bench checks stay the owner's.
