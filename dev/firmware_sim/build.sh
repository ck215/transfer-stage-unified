#!/bin/bash
# build.sh <sketch.ino> <out-binary> station|validator
# Builds the sketch natively: the .ino is copied with two host-only edits (the Teensy-3.5 _write shim is dropped and
# IrqGuard's `mrs primask` becomes m = 0), then compiled with the real AccelStepper source and the stubs here.
set -e
D=$(cd "$(dirname "$0")" && pwd)
ACCEL="${ACCELSTEPPER_SRC:-$HOME/Documents/Arduino/libraries/AccelStepper/src}"   # the AccelStepper the sketches build with
ino="$1"; out="$2"; kind="$3"
mkdir -p "$D/obj"
src="$D/obj/$(basename "$out").sketch.cpp"
sed -e '/^extern "C" int _write/,/^}/d' -e 's/__asm__ volatile("mrs %0, primask" : "=r"(m));/m = 0;/' "$ino" > "$src"
grep -q '  IrqGuard() { m = 0;' "$src" || { echo "build.sh: IrqGuard asm line not found"; exit 1; }
echo 'uint8_t sim_probe_level(int i) { return s_level[i]; }   // sim probe: the filtered level the ISR acts on' >> "$src"
CXX="clang++ -std=gnu++17 -O1 -g -I$D/include -I$ACCEL -DARDUINO=10819"
def=""; [ "$kind" = validator ] && def="-DSIM_VALIDATOR"
$CXX -Wall -Wno-unused-function -include Arduino.h -c "$src" -o "$D/obj/$(basename "$out").sketch.o"
$CXX -w -c "$ACCEL/AccelStepper.cpp" -o "$D/obj/AccelStepper.o"
$CXX -Wall -c "$D/stubs.cpp" -o "$D/obj/stubs.o"
$CXX -Wall $def -c "$D/sim.cpp" -o "$D/obj/$(basename "$out").sim.o"
$CXX "$D/obj/$(basename "$out").sketch.o" "$D/obj/AccelStepper.o" "$D/obj/stubs.o" "$D/obj/$(basename "$out").sim.o" -o "$out"
echo "built $out"
