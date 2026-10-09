# Stepper Motor Validator

Bench kit to validate a stepper axis: the motor, both limit switches and the home photo-interrupter, one axis at a time. A TMC2209 driver on a breadboard and a Teensy 3.5 (a 4.1 also builds; same pins). The firmware never moves on its own. Every motion and test is a command sent from `stepper_validator.html` over USB serial. `wiring.html` is the full pin-by-pin wiring sheet for the 50 mm XYZ stage.

Files:
- `stepper_validator/stepper_validator.ino` is the firmware (Teensyduino, libraries TMCStepper and AccelStepper).
- `stepper_validator.html` is the GUI (one file, no network needed, Chrome or Edge).

## WARNING

**Never plug or unplug the motor while VM is powered.** Disconnecting a motor under power can destroy the TMC2209. Power off VM, wait for the bulk capacitor to drain, then change the motor. Set the motor current (see below) before the first run, and never exceed the motor's rated current.

## Wiring

| TMC2209 pin | Connect to |
| --- | --- |
| VM | Motor supply + (8 to 24 V, sized for the motor), with a bulk capacitor (100 uF or more, rated above the supply voltage) across VM and GND, close to the driver |
| GND (power) | Motor supply - and Teensy GND (common ground) |
| VIO | Teensy 3.3 V |
| GND (logic) | Teensy GND |
| EN | Teensy pin 4 (`EN_PIN`) |
| STEP | Teensy pin 2 (`STEP_PIN`) |
| DIR | Teensy pin 3 (`DIR_PIN`) |
| PDN_UART | Teensy TX1 (pin 1) **directly** (half-duplex, `DRIVER_HALF_DUPLEX = true`; a 47 Ohm to 1 kOhm series resistor is optional). RX1 (pin 0) is not connected. With `DRIVER_HALF_DUPLEX = false`: TX1 through 1 kOhm and RX1 direct to PDN_UART |
| MS1, MS2 | Both to GND (driver address 0) |
| DIAG | Optional. Teensy pin of your choice; set `DIAG_PIN` to use it, otherwise leave unconnected (`-1`) |
| A1, A2 | Motor coil A pair |
| B1, B2 | Motor coil B pair |

| Stage sensor (DB9) | Connect to |
| --- | --- |
| Pin 2, LS1 | Teensy pin 5 (`LS1_PIN`), internal pull-up |
| Pin 3, LS2 | Teensy pin 6 (`LS2_PIN`), internal pull-up |
| Pin 5, Vo (photo-interrupter) | Teensy pin 7 (`HOME_PIN`), internal pull-up |
| Pin 4, GND | Teensy GND |
| Pin 1, Vcc | Teensy Vin (5 V from USB). Teensy 3.5 only: its digital pins are 5 V tolerant. On a Teensy 4.x use 3.3 V |

The limit switches act as an interlock in the step interrupt: a tripped switch stops motion toward its end within 25 us (after the 200 us filter). LS1 and LS2 need no fixed assignment: each switch's end (STATUS `ls1_end`/`ls2_end`) is learned from the first change it shows while the axis moves in direction d. If it releases, it guards -d. If it trips, and has not read pressed with its end unknown since boot, it guards d and the axis stops there. A trip after it has read pressed with its end unknown (parked on it, or released at rest) could be release chatter: the axis stops and nothing is learned (`end=+0`). (Until 2026-10-09 a re-trip in the release chatter of a parked switch taught it the wrong end, review R-4; the station firmware has the same rule.)

A switch pressed with its end not yet confirmed that way (parked on it at power-up, or after `LIMITS`) allows only a dead-man JOG, at 0.1 mm/s at most (`PARKED_JOG_SPEED_MM`); MOVE, REVS and TEST are refused (`limit-lsN-end-unknown:jog-off-it`, `limit-tripped:jog-off-it`). If the switch releases, its end is learned. If the axis travels 0.5 mm (`PARKED_TRAVEL_MM`, **provisional**) from where the switch was found pressed and it is still pressed, that way drives into it: the axis stops and learns that end (`EVT LIMIT lsN tripped ... learned=travel`). If the 0.5 mm runs out the other way too, the axis stops (`pressed_both_ways=1`) and jogs both ways are refused: check the switch, then `LIMITS NC|NO` to start over. `PARKED_TRAVEL_MM` must exceed the travel from the hard stop to where the switch releases (overtravel plus differential travel); measure it before trusting the value. `LIMITS NC|NO` sets the contact type (boot default `LIMITS_NC`). Under NC, both switches tripped at centre means either normally-open switches or an open circuit (cable unplugged, GND open), and it blocks all motion. Under NO an open circuit reads clear, so set NO only after the meter check in `wiring.html` showed open-at-rest switches, and see each switch read TRIPPED when pressed before trusting the interlock.

Do not connect the Teensy USB 5 V to VM. Leave the VREF potentiometer alone: current is set over UART (I_scale_analog is off).

### This build: 42BYGH613-01B on a 50 mm XYZ stage, one axis at a time

Motor datasheet: 1.8 deg, 1.7 A, 1.5 ohm, 3.2 mH, 0.4 N.m holding. Leads: black = A, green = A-, red = B, blue = B-, so black/green go to A1/A2 and red/blue to B1/B2. Through the D-sub cable a coil pair reads about 1.5 to 2.5 ohm (cable and meter leads add a little); a closed end switch reads near 0 ohm, so toggle the switch to tell the two apart. The optical interrupter shows a diode drop (about 1.1 V) in diode mode, not a resistance. The switches and the interrupter go to Teensy pins 5, 6 and 7 (table above; meter checks in `wiring.html`).

Stage: 1 mm lead screw, 50 mm travel, so one full step is 5 um. The GUI and the protocol work in mm and mm/s. The step-resolution setting (MICROSTEPS, 1 to 256) only sets the size of one commanded step, 5 um / setting: the TMC2209 interpolates every setting to 1/256 (CHOPCONF intpol, the driver default, left on here and on the station), so smoothness, torque and heating do not change with it. Centre the axis (about 25 mm from each end) before every session: the switch interlock protects an end only once that switch has been seen to trip (press test) and has tripped once while moving; every MOVE, REVS and TEST REVS is clamped to 15 mm, but repeated moves and jogging can still reach the ends. On the stage, the tape-mark check in TEST REVS becomes "the carriage returns to its start" (a dial indicator, or a mark on the carriage against the base).

### Identify the coil pairs with a multimeter

With the motor disconnected and unpowered, measure resistance between wires. Wires of the same coil read a low resistance (typically 1 to 10 ohm); wires of different coils read open circuit. A 4-wire motor gives two pairs. A second check: short two wires together and turn the shaft by hand; if you feel clear resistance (cogging), those two wires are one coil. Connect one pair to A1/A2 and the other to B1/B2. Swapping the two wires within a pair only reverses direction.

## Flashing

Arduino IDE: install Teensyduino, install the libraries **TMCStepper** and **AccelStepper** (Library Manager), choose Tools > Board > Teensy 4.1, and upload `stepper_validator/stepper_validator.ino`.

arduino-cli:

```
arduino-cli core install teensy:avr --additional-urls https://www.pjrc.com/teensy/package_teensy_index.json
arduino-cli lib install TMCStepper AccelStepper
arduino-cli compile --fqbn teensy:avr:teensy41 stepper_validator
arduino-cli upload  --fqbn teensy:avr:teensy41 -p <port> stepper_validator
```

On a Teensy 3.5 use `teensy:avr:teensy35` (Tools > Board > Teensy 3.5); pins and wiring are the same.

On boot the driver is left off (EN high, toff 0) and the firmware reads IOIN VERSION, expecting 0x21. The GUI shows the result as an `EVT BOOT` line in the log.

## Using the GUI

Open `stepper_validator.html` in Chrome or Edge (opening the file directly works). If the browser refuses, run `python3 -m http.server` in this folder and open `http://localhost:8000/stepper_validator.html`. Click Connect and choose the Teensy port.

Safety behaviour: the big STOP button decelerates and keeps holding; **Esc** or the ESTOP button halts instantly and disables the driver. The page polls STATUS every 250 ms and the firmware treats that as a heartbeat: with no line for 2.5 s during motion or a test it stops (`EVT FAULT host-timeout`), and with no line for 10 s more it turns the outputs off (`EVT FAULT host-timeout-disabled`; ENABLE again). Closing the tab, unplugging USB, or dropping DTR also stops the motor. Keep the GUI tab visible during long tests; switching tabs is safe and at worst aborts the test (timeout 2.5 s, since Chrome throttles hidden tabs). Jogging is a dead-man: it stops 250 ms after the last JOG.

### Protocol (for scripting)

Newline-terminated ASCII, case-insensitive; each command gets one `OK <cmd> ...` or `ERR <cmd> <reason>` reply. Unsolicited lines start with `EVT `. Commands: `PING`, `INFO`, `ENABLE`, `DISABLE`, `STOP`, `ESTOP`, `CURRENT <mA>`, `MICROSTEPS <n>` (step resolution, 1..256; position is kept), `SPEED <mm/s>`, `ACCEL <mm/s^2>`, `MODE STEALTH|SPREAD`, `LIMITS NC|NO`, `MOVE <mm>`, `REVS <n>` (whole revolutions), `JOG <-1|0|1>`, `ZERO`, `STATUS`, `TEST UART|COILS|SWEEP|LIMITS|REVS <n>`. Unsolicited sensor lines: `EVT LIMIT lsN tripped pos_mm=... end=±1|+0 [learned=travel travel_mm=...] [pressed_both_ways=1 travel_mm=...]` (see the limit switches above) and `EVT HOME edge level=0|1 pos_mm=...`. A driver that loses VM is reconfigured automatically (`EVT FAULT driver-reset reconfigured`); it stays disabled until ENABLE.

## Test procedure

Mount the motor on a bench with the shaft free (no load for the first pass). Stick a **tape mark** on the shaft or a flag on it, and a reference mark on the frame.

1. [ ] Power the Teensy over USB only. Connect in the GUI. The boot line shows `uart=FAIL` until VM is on: the TMC2209's logic runs from VM, not VIO. That is expected. Set Limit switches to the type the meter check found; LS1 and LS2 must read clear at centre (both TRIPPED under NC = normally-open switches or an open circuit; NO shows both as clear). Press each switch: only that one reads TRIPPED. Block the photo-interrupter slot: Home level changes.
2. [ ] Power VM. Set Current to the motor's rated current or lower (Apply). Press Enable (it re-reads the driver and writes its whole configuration every time).
3. [ ] **TEST UART** (works even disabled).
4. [ ] **TEST COILS**.
5. [ ] **TEST REVS** with n = 2 to 5. Before it starts, align the tape mark with the frame mark. After the test, the mark must be back at the frame mark.
6. [ ] **TEST SWEEP**.
7. [ ] **TEST LIMITS** (stage only): seeks the switch at each end at 1 mm/s, checks each releases on a 1 mm back-off, logs home-sensor edges, reports switch-to-switch travel, then parks midway. Watch the first run; press Esc if the carriage reaches an end without stopping.
8. [ ] Disable, remove VM power, then unplug the motor.

### Reading the results

Results show as the word PASS, FAIL or ABORTED (with colour as a second cue).

**UART**
- FAIL with `version=` not 0x21: the driver is not answering. This is wiring or the driver board, not the motor. Check VM is on (the driver's logic runs from it), TX1 to PDN_UART (direct, half-duplex), VIO 3.3 V, MS1/MS2 to GND, common ground.
- FAIL with a read-back mismatch: noisy UART or a bad driver board. Not a motor fault.

**COILS** (slow spreadCycle motion; the driver samples DRV_STATUS for open and short flags)
- FAIL with `shorts>0`: a coil short to ground or supply. Disconnect the motor and re-measure the coil resistance and wire-to-frame insulation. If wiring is clean, the motor winding is faulty (or the driver is damaged). The firmware also ESTOPs and prints `EVT FAULT short`.
- FAIL with `open_a=1` or `open_b=1`: open-load is flagged in most samples: a broken coil, a loose connector, or a wrong pairing. Re-check continuity of the pair with the multimeter first; if the coil is open the motor is faulty. Note: per the datasheet OLA/OLB are only meaningful while moving in spreadCycle, which is why this test forces spreadCycle.
- FAIL `too-few-samples`: motor did not run at cruise speed; check the current and supply.

**REVS** (n revolutions forward, then n back to the start)
- Tape mark does **not** return to the frame mark: steps were lost. Repeat at lower speed or higher current; if it persists at a safe current, suspect rough bearings, a weak magnet or a shorted turn in the motor. If it returns, there were no missed steps.
- Result also gives `sg_min/avg/max` (StallGuard) and `stalls`. sg_result is only meaningful in StealthChop; REVS reports `mode=` so you know which applies. Compare sg values between motors.
- FAIL with a stall: the motor stalled. Lower speed or accel, or raise current (within rating). A repeated stall at gentle settings points to a bad motor.

**LIMITS** (stage only; start with the axis roughly centred)
- PASS: both switches found, at opposite ends, both released on the back-off, and at least one home edge seen. The result gives `travel_mm` (switch to switch), which switch guards each end, and the home edges relative to the parking point.
- FAIL `no-switch-within-search`: no switch tripped within 30 mm (first search) or twice the first distance plus 5 mm (second). Check the switch wiring and NC/NO, or the start was far off centre.
- FAIL `same-switch-at-both-ends` or `both-switches-tripped-together`: LS1/LS2 wiring is crossed or shorted.
- FAIL `lsN-did-not-release`: a stuck switch, or the back-off is shorter than its hysteresis.
- FAIL `home-not-seen`: no photo-interrupter edge between the switches. Check DB9 pins 1, 4 and 5 and the sensor's supply.

**SWEEP** (speed stages from `SWEEP_MIN_SPEED_MM` to the speed clamp in StealthChop, restoring the previous mode afterwards)
- Each stage prints `EVT SWEEP stage=... sg_avg=... cs_actual=...`. A healthy motor shows a smooth sg_avg trend; sg_avg collapsing at one speed is a resonance or a failing motor.
- FAIL on a stall or fault: the motor cannot follow at that stage. At speeds above its capability this is normal for a small supply; reduce `MAX_SPEED_MM` before blaming the motor.

ABORTED means you pressed STOP/ESTOP, the time limit hit, the host went quiet, the driver was disabled, or a switch was found pressed with its end unknown (`reason=limit-end-unknown`). It is not a verdict. A driver fault during COILS, REVS or SWEEP ends the test as FAIL with `fault=...`.

Software stall detection is off by default (`SGTHRS_VALUE = 0`). Set `SGTHRS_VALUE` (try 20 to 100) after watching the `sg_result` values of a known good run; the test then reports stalls when sg_result falls below 2 x SGTHRS in StealthChop. If you wire DIAG, set `DIAG_PIN` and the DIAG output is used instead.

## Constants to tune (top of the .ino)

- `MAX_CURRENT_MA`: **set from the motor datasheet rated current (rms).** The CURRENT command is clamped to it. Also set `RUN_CURRENT_MA` at or below it. Peak datasheet current / 1.414 = rms.
- `FULL_STEPS_PER_REV`: 200 for 1.8 degree motors, 400 for 0.9 degree.
- `LEAD_MM` (stage travel per motor revolution), `MICROSTEPS`, `R_SENSE` (0.11 on most modules), `HOLD_MULTIPLIER` (lower if the motor gets warm at standstill).
- `DEFAULT_SPEED_MM`, `MAX_SPEED_MM`, `DEFAULT_ACCEL_MM`, `MAX_MOVE_MM`, `MAX_TEST_REVS`: in mm, mm/s and revolutions, so they mean the same at every step resolution; lower them for a weak supply. `MAX_STEP_RATE` (pulses/s) is the step-timer ceiling, which lowers the speed clamp at fine resolutions (1.25 mm/s at 16, 0.078 mm/s at 256).
- `STEP_PIN`, `DIR_PIN`, `EN_PIN`, `DIAG_PIN`, `DRIVER_SERIAL`: if your wiring differs.
- `SGTHRS_VALUE`, `TCOOLTHRS_VALUE`: StallGuard tuning.
- `JOG_TIMEOUT_MS`, `HOST_TIMEOUT_MS`, `TEST_TIME_LIMIT_MS`: safety timings. `HOST_SILENT_OFF_MS` (10 s): after `EVT FAULT host-timeout` stops the motor, the outputs go off if the GUI stays silent this much longer (`EVT FAULT host-timeout-disabled`; ENABLE again).
- `PARKED_JOG_SPEED_MM`, `PARKED_TRAVEL_MM`: jog-off rules for a switch pressed with its end unknown (above). `PARKED_TRAVEL_MM` is provisional until checked against the switch overtravel.

## Notes and limits

- The driver registers match the station: 600 mA rms, 8 microsteps, spreadCycle, pwm_autoscale off, I_scale_analog off, toff 0 while disabled and 4 when enabled, 115200 UART. In StealthChop (MODE STEALTH and TEST SWEEP) pwm_autoscale is turned on so the driver can regulate current, and it is turned off again when returning to spreadCycle.
- Step pulses come from a 25 us timer interrupt, so UART register reads do not disturb stepping.
- The firmware was compile-checked but could not be run against hardware in this build; do a first power-up at low current with the shaft free.
