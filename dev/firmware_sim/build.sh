#!/bin/bash
# build.sh <sketch.ino> <out-binary> station|validator|mega|stepref
# station, validator: a Teensy sketch, copied with two host-only edits (the Teensy-3.5 _write shim is dropped and
#   IrqGuard's `mrs primask` becomes m = 0), compiled with the real AccelStepper source and the stubs in include/.
# mega: a Mega 2560 sketch (firmware/xyz_stage_mega) against the stub AVR core in avr/, plus <out-binary>-stepref,
#   firmware/stepper_firmware built the same way: the frame-protocol reference its parity scenario runs, and the base
#   its other scenarios are red on. stepref: that reference alone, from the sketch given.
set -e
D=$(cd "$(dirname "$0")" && pwd)
ACCEL="${ACCELSTEPPER_SRC:-$HOME/Documents/Arduino/libraries/AccelStepper/src}"   # the AccelStepper the sketches build with
ino="$1"; out="$2"; kind="$3"
mkdir -p "$D/obj"

# A Mega sketch: compiled as it is (the AVR core's stand-ins take everything it touches), with its folder on the
# include path for its own headers. $MUTATE, if set, is a sed program applied to the copy (a deliberate defect, to
# show a scenario red).
build_avr() {   # <ino> <out> <sim defines> <warning flags> [prototypes]
  local sk="$1" bin="$2" defs="$3" warn="$4" name; name=$(basename "$2")
  local A="$D/avr" src="$D/obj/$name.sketch.cpp"
  local CXXA="clang++ -std=gnu++17 -O1 -g -I$A/include -I$ACCEL -DARDUINO=10819"
  if [ -n "$MUTATE" ]; then sed -e "$MUTATE" "$sk" > "$src"; cmp -s "$sk" "$src" && { echo "build.sh: MUTATE changed nothing"; exit 1; }
  else cp "$sk" "$src"; fi
  # stepper_firmware calls functions above their definitions and relies on the Arduino IDE's generated prototypes:
  # declare every top-level function of the copy after its last #include, as the IDE does.
  if [ "$5" = prototypes ]; then
    python3 - "$src" <<'PY'
import re, sys
p = sys.argv[1]; lines = open(p).read().split("\n")
head = re.compile(r"^(?!(?:if|for|while|switch|return|else|struct|class|enum|typedef|static_assert)\b)"
                  r"([A-Za-z_][\w<>]*(?:\s+[A-Za-z_][\w<>]*)*[\s\*&]+)([A-Za-z_]\w*)\s*\(([^;{}]*)\)\s*\{?\s*$")
protos = [f"{m.group(1).strip()} {m.group(2)}({m.group(3)});" for l in lines for m in [head.match(l)] if m and m.group(2) not in ("setup", "loop")]
last = max(i for i, l in enumerate(lines) if l.startswith("#include"))
open(p, "w").write("\n".join(lines[:last + 1] + protos + lines[last + 1:]))
PY
  fi
  $CXXA $warn -I"$(cd "$(dirname "$sk")" && pwd)" -include Arduino.h -c "$src" -o "$D/obj/$name.sketch.o"
  $CXXA -w -c "$ACCEL/AccelStepper.cpp" -o "$D/obj/AccelStepper.avr.o"
  $CXXA -Wall -Wextra -c "$A/stubs.cpp" -o "$D/obj/stubs.avr.o"
  $CXXA -Wall -Wextra $defs -c "$A/sim_mega.cpp" -o "$D/obj/$name.sim.o"
  $CXXA "$D/obj/$name.sketch.o" "$D/obj/AccelStepper.avr.o" "$D/obj/stubs.avr.o" "$D/obj/$name.sim.o" -o "$bin"
  echo "built $bin"
}
if [ "$kind" = mega ]; then
  build_avr "$ino" "$out" "" "-Wall -Wextra"
  MUTATE="" build_avr "$D/../../firmware/stepper_firmware/stepper_firmware.ino" "$out-stepref" "-DSIM_STEPREF" "-w" prototypes
  exit 0
fi
if [ "$kind" = stepref ]; then build_avr "$ino" "$out" "-DSIM_STEPREF" "-w" prototypes; exit 0; fi

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
