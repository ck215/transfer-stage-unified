# Equipment test kit

Bench tools for checking stage hardware one axis at a time, before it joins
the station. This folder is the kit's only home: every later addition to it
(a new sketch, diagnostic, GUI or wiring sheet) lands here under `dev/`,
never as a loose copy elsewhere. The first bench copy lived in `~/Downloads`
until 2026-10-09 and has been retired. Nothing here is part of the app: the station's flash and
packaging pipeline reads `firmware/` only, so these sketches are never
flashed by Setup and never shipped in a release.

| Path | What it is |
|---|---|
| `stepper_validator/` | The one-axis validator: Teensy 3.5 (or 4.1) firmware, a browser GUI, the README with the test procedure, and the pin-by-pin wiring sheet. Tests a motor, its TMC2209, both limit switches and the home photo-interrupter. |
| `stepper_validator/stepper_validator/stepper_validator.ino` | The validator firmware (TMCStepper + AccelStepper; single-wire half-duplex UART to the driver, ISR limit interlock, mm units). |
| `stepper_validator/stepper_validator.html` | The GUI. Open it in Chrome or Edge (Web Serial); it needs no server. |
| `stepper_validator/wiring.html` | Wiring: every Teensy pin by position, the BIGTREETECH TMC2209 V1.2 by J1/J2 pin, the motor supply and C1, the common ground, the DB9. |
| `stepper_validator/stage_db9_wiring.html` | The stage's own DB9 pinout sheet, with our annotations. Its pin 4 vs pin 5 contradiction for the limit-switch return is called out; settle it with the meter check in `wiring.html`. |
| `xyz_mega/` | The XYZ stage on one Arduino Mega 2560 with three TMC2209 drivers: `wiring.html` is the pin-by-pin sheet (every Mega pin, each driver's J1/J2 and address strap, the shared PDN_UART junction, supply and capacitors, common ground, each axis's DB9, the pre-power checklist), per `docs/rebuild/MEGA_STANDARD.md` section 5. |
| `diagnostics/uart_diag/` | Tries the driver UART both ways (single-wire half-duplex, then TX/RX) at addresses 0-3 and prints the raw bytes. Driver held off. |
| `diagnostics/limit_seek/` | Drives the axis slowly (<= 1 mm/s, <= 50 mm per SEEK) and stops the instant LS1 or LS2 changes state, classifying each input with a pull-up AND a pull-down (OPEN / GND / HIGH), so a switch or a home sensor is seen however it is wired; logs home-sensor edges. 1 s host dead-man; outputs off when the port closes. Built 2026-10-09 when the validator's pull-up-only inputs could not see a mis-wired switch. Run TEST COILS on the validator first: a stalled motor still counts steps. |
| `diagnostics/limits_diag/` | Prints the raw level of Teensy pins 5-9 every 100 ms with no pull, pull-up and pull-down, so a switch press or a blocked slot shows as a state change. Driver held off. |
| `reference/42BYGH613-01B_motor_datasheet.jpg` | The axis motor's datasheet (the seller's listing image): 1.8 deg, 1.7 A, 1.5 ohm, 3.2 mH, 0.4 N.m; leads black/green = A, red/blue = B. The source of the driver current limits. |
| `reference/xyz_stage_db9_schematic.jpg` | The stage vendor's DB9 schematic (listing image): limit switches, photo-interrupter (200 ohm LED resistor, 5 V 15 mA) and motor on the nine pins. `stage_db9_wiring.html` is our annotated reading of it. |

## Flashing (arduino-cli with the Teensy core)

```
arduino-cli core install teensy:avr --additional-urls https://www.pjrc.com/teensy/package_teensy_index.json
arduino-cli lib install TMCStepper AccelStepper
arduino-cli board list                       # the Teensy shows as usb:<address> (teensy) and a serial port
arduino-cli compile --fqbn teensy:avr:teensy35 stepper_validator/stepper_validator
arduino-cli upload  --fqbn teensy:avr:teensy35 -p usb:<address> stepper_validator/stepper_validator
```

The same two commands flash a diagnostic (`diagnostics/uart_diag`,
`diagnostics/limits_diag`); re-flash the validator afterwards. Read a
diagnostic with `arduino-cli monitor -p <serial port>` or any serial
monitor. Flash with the motor supply off. Use `-p usb:<address>`: a
station with several Teensy boards attached (the heater is one) must never
be flashed by "whichever Teensy answers".

## Before trusting a run

- `uart=FAIL` at boot is expected until VM is on: the TMC2209's logic runs
  from the motor supply.
- Prove each limit switch with a press (it alone reads TRIPPED) before
  trusting the interlock, and before TEST LIMITS. Under NO, an open wire
  reads "clear".
- Never plug or unplug the DB9 or a coil wire with VM on.

History: built on the bench 2026-10-08/09 for the 50 mm XYZ stage (axis
motors 42BYGH613-01B, 1 mm lead screws); the XYZ Stage device's own
firmware (`firmware/xyz_stage_axis/`) grew from this sketch and keeps its
commands, so the GUI also drives a station axis board on the bench.
