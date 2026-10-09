// xyz_stage_axis: per-axis firmware for the transfer station's 50 mm XYZ stage, protocol version 1.
// One Teensy 3.5 per axis, identical firmware on all three; each board knows its axis (X, Y or Z) from EEPROM (AXIS).
// Built on the stepper-motor-validator sketch, which stays the motion core: every validator command and reply key
// still works, so stepper_validator.html drives this firmware unchanged. PROTOCOL.md describes the protocol and the
// HOME sequence; CHANGES.md lists what changed from the validator and why.

// ===================== TUNABLE CONSTANTS (edit these first) =====================
// Pins (same numbers on Teensy 3.5 and 4.1: Serial1 is RX1 0 / TX1 1 on both)
const int STEP_PIN = 2;                 // STEP input of the TMC2209; change if you wire STEP elsewhere
const int DIR_PIN  = 3;                 // DIR input of the TMC2209; change if you wire DIR elsewhere
const int EN_PIN   = 4;                 // EN input (active LOW, HIGH = outputs off); change with your wiring
const int DIAG_PIN = -1;                // TMC2209 DIAG output for StallGuard; -1 = not wired / unused
#define DRIVER_SERIAL Serial1           // Hardware UART used for the TMC2209 (Serial1 = pins 0 RX1 / 1 TX1)
const bool DRIVER_HALF_DUPLEX = true;   // true: TX1 alone to PDN_UART (a series resistor is optional), RX1 unconnected; the core turns
                                        // the TX pin around to receive, so it never fights the driver's reply.
                                        // false: classic wiring, TX1 through ~1 kOhm plus RX1 direct to PDN_UART

// Driver (match the station's driver settings)
const float    R_SENSE        = 0.11f;  // Sense resistor in ohms; change only if your board is not 0.11 (most TMC2209 modules)
const unsigned char  DRIVER_ADDRESS = 0b00;   // UART address set by MS1/MS2 (both GND = 0b00); change if you wire MS1/MS2 differently
const unsigned long DRIVER_BAUD    = 115200; // Driver UART baud; the station uses 115200, change only to match another setup

// Motor: 42BYGH613-01B (1.8 deg, 1.7 A, 1.5 ohm, 3.2 mH, 0.4 N.m) on a 50 mm stage, 1 mm lead screw
// At 8 microsteps: 1600 microsteps = 1 rev = 1 mm of travel, 0.625 um per microstep.
const unsigned int FULL_STEPS_PER_REV = 200;   // 200 = 1.8 deg motor, 400 = 0.9 deg; set from the datasheet
const float    LEAD_MM            = 1.0f;  // Stage travel per motor revolution in mm (lead-screw lead); every mm figure goes through this
const unsigned int RUN_CURRENT_MA     = 600;   // Initial RMS run current; station uses 600 (35 % of the 1.7 A rating)
const unsigned int MAX_CURRENT_MA     = 1000;  // Hard clamp for CURRENT: under 1.2 A (1.7 A read as peak / 1.414) and the breadboard TMC2209's ~1.2 A no-heatsink limit
const float    HOLD_MULTIPLIER    = 0.5f;  // Hold current = run current x this (0..1); lower it if the motor runs warm at standstill
const unsigned int MICROSTEPS         = 8;     // Initial step resolution: STEP pulses per full step (1..256, power of 2); the station uses 8.
                                               // The TMC2209 interpolates every setting to 1/256 (CHOPCONF intpol, left on), so this sets the
                                               // size of one commanded step (5 um / MICROSTEPS on this stage), not how smoothly the coils move.
const bool     USE_SPREADCYCLE    = true;  // true = spreadCycle at boot (station setting); StealthChop only for StallGuard work

// Motion, in mm and mm/s (1 rev = LEAD_MM). Commands and replies use the same units, so no number changes meaning
// when the step resolution changes; only the size of one commanded step does.
const float    DEFAULT_SPEED_MM = 0.5f;    // Initial SPEED in mm/s; lower it for a heavy load
const float    MAX_SPEED_MM     = 2.5f;    // Clamp for SPEED and the SWEEP top stage in mm/s (150 rpm; the motor's corner speed is far above this)
const float    MAX_STEP_RATE    = 4000.0f; // STEP pulses/s ceiling: the 25 us step ISR keeps period jitter <= 10 % up to here. It lowers the
                                           // speed clamp at fine resolutions (1.25 mm/s at 16, 0.078 mm/s at 256)
const float    DEFAULT_ACCEL_MM = 2.5f;    // Initial ACCEL in mm/s^2; lower it if the motor stalls on start
const float    MIN_ACCEL_MM     = 0.25f;   // ACCEL clamp, low end: below this the ramps get longer than the moves they serve
const float    MAX_ACCEL_MM     = 25.0f;   // ACCEL clamp, high end: keeps AccelStepper's first step interval under MAX_STEP_RATE at 256
const float    TEST_ACCEL_MM    = 2.5f;    // Fixed acceleration for TEST SWEEP and TEST LIMITS, so their distances do not depend on ACCEL
const float    MAX_MOVE_MM      = 50.0f;   // Clamp per MOVE/MOVETO/REVS command: the full 50 mm travel. The limit interlock, not this
                                           // clamp, is what stops an axis at its ends (validator: 15)
const int      MAX_TEST_REVS    = 15;      // Clamp for REVS n and TEST REVS n (15 revs = 15 mm here)
const float    COILS_SPEED_MM   = 0.25f;   // Speed used by TEST COILS; slow but not crawling (open-load flags need movement)
const int      COILS_REVS       = 1;       // Revolutions moved by TEST COILS
const int      SWEEP_STAGES     = 8;       // Number of speed stages in TEST SWEEP
const float    SWEEP_MIN_SPEED_MM = 0.125f; // Lowest SWEEP stage speed in mm/s; raise if the motor will not run reliably this slow
const float    SWEEP_CRUISE_MM  = 2.0f;    // Distance at cruise speed per SWEEP stage; with the ramps a stage is at most 4.5 mm (2 + 2.5^2 / TEST_ACCEL_MM)

// Stage sensors on the DB9: limit switches LS1 (DB9 pin 2) and LS2 (pin 3) switch to the stage GND (pin 4);
// the photo-interrupter output Vo is DB9 pin 5. All three use the Teensy's internal pull-up.
const int      LS1_PIN          = 5;       // DB9 pin 2
const int      LS2_PIN          = 6;       // DB9 pin 3
const int      HOME_PIN         = 7;       // DB9 pin 5 (photo-interrupter Vo)
const bool     LIMITS_NC        = false;   // Boot contact type. false = normally open (this stage, bench 2026-10-09): reads HIGH at rest, LOW
                                           // when tripped. true = normally closed. LIMITS NC|NO changes it at run time; a wrong setting shows
                                           // both switches tripped
const unsigned char SENSOR_FILTER_SAMPLES = 8;  // A sensor level must hold for this many 25 us ISR samples (200 us) to count: rejects chopper noise on the DB9 cable
const float    LIMIT_SEEK_SPEED_MM = 1.0f; // TEST LIMITS search speed
const float    LIMIT_SEEK_MM    = 30.0f;   // TEST LIMITS first search reaches this far from the start (just past half the 50 mm travel)
const float    LIMIT_MARGIN_MM  = 5.0f;    // Second search: twice the distance to the first switch plus this, for an off-centre start
const float    LIMIT_BACKOFF_MM = 1.0f;    // Back-off after each trip; the switch must release within it

// HOME (zeroing on the photo-interrupter). The reference edge is where the filtered HOME_PIN level changes TO
// HOME_FLAG_LEVEL while the axis moves in HOME_DIR; the final approach always runs that way at HOME_SLOW_SPEED_MM.
// Both constants depend on the flag's geometry and the sensor's polarity: owner bench check (PROTOCOL.md, HOME).
const int      HOME_DIR           = -1;    // Final approach direction in motor steps (-1 or +1)
const unsigned char HOME_FLAG_LEVEL = 1;  // HOME_PIN level (1 = HIGH, 0 = LOW) while the flag blocks the slot (open-collector + pull-up: 1)
const float    HOME_SEEK_SPEED_MM = 1.0f;  // Coarse search speed, mm/s (TEST LIMITS uses the same; a limit trip halts from it)
const float    HOME_SLOW_SPEED_MM = 0.1f;  // Final approach speed, mm/s; the ISR halts on the edge itself, so this sets repeatability
const float    HOME_BACKOFF_MM    = 0.5f;  // The final approach starts this far on the clear side of the coarse edge: must exceed the
                                           // sensor hysteresis plus the lead-screw backlash
const float    HOME_APPROACH_EXTRA_MM = 0.5f; // The final approach gives up this far past the coarse edge (EVT HOME FAIL reason=edge-lost)
const float    HOME_SEEK_MAX_MM   = 55.0f; // Reach of one search leg: the 50 mm travel plus margin, so a leg always meets a switch

// Identity (the station's port scan) and the axis tag in EEPROM
const char     IDENTITY_LETTER    = 'x';   // The scan's `s` is answered `DEV: x <axis>` (the heater already answers `t`)
const int      AXIS_EEPROM_ADDR   = 0;     // 3 bytes: AXIS_TAG_MAGIC, the axis letter, its bitwise complement
const unsigned char AXIS_TAG_MAGIC = 0xA7;

// Safety
const unsigned long JOG_TIMEOUT_MS   = 250;     // JOG/JOGV dead-man: a jog stops if no JOG or JOGV arrives within this time
const unsigned long HOST_TIMEOUT_MS  = 2500;    // Boot/per-connection default of HOSTTIMEOUT: no line for this long stops motion/tests. Chrome
                                                // throttles hidden-tab timers to ~1 Hz, so 1000 ms aborted bench GUI tests on tab switch; a closed
                                                // page/port still ESTOPs at once via the DTR guard. The station sets 1000.
const unsigned long HOST_TIMEOUT_MIN_MS = 250;  // HOSTTIMEOUT clamp
const unsigned long HOST_TIMEOUT_MAX_MS = 5000;
const unsigned int  STREAM_MAX_HZ    = 50;      // STREAM clamp
const unsigned int  HOME_EDGE_EVT_PER_S = 20;   // At most this many "EVT HOME edge" lines per second (the next one says suppressed=n)
const unsigned long TEST_TIME_LIMIT_MS = 120000; // Floor of each TEST's time limit; a test that plans longer gets 1.5 x its planned time + 10 s
const unsigned long POLL_ACTIVE_MS   = 50;      // Driver status poll period while moving/testing; lower = finer fault catch, more UART jitter
const unsigned long POLL_IDLE_MS     = 250;     // Driver status poll period while idle

// StallGuard
const unsigned char  SGTHRS_VALUE     = 0;       // StallGuard threshold; 0 = software stall detection OFF (sg_result only logged). Tune 20..100 on your motor in StealthChop
const unsigned long TCOOLTHRS_VALUE  = 0xFFFFF; // StallGuard active when TSTEP <= this; 0xFFFFF = at all speeds, lower to ignore very slow speeds
const float    SG_MIN_SPEED_MM  = 0.0625f;      // Software stall check only above this speed (mm/s); sg_result is meaningless near standstill
// ================================================================================

#include <Arduino.h>
#include <TMCStepper.h>
#include <AccelStepper.h>
#include <EEPROM.h>
#include <ctype.h>
#include <stdlib.h>
#include <string.h>

// newlib's stdio (pulled in by vsnprintf) needs _write. The core's weak _write lives in Print.cpp, which the
// Teensy 3.5 link never takes from the core archive for this sketch, so without this the 3.5 build fails to link.
// Same behaviour as the core's version: fds 0-2 go to USB Serial, any other fd is a Print object (Print::printf).
extern "C" int _write(int file, char *ptr, int len) {
  if (file >= 0 && file <= 2) file = (int)&Serial;
  return ((Print *)file)->write((const uint8_t *)ptr, len);
}

static const uint8_t EXPECTED_VERSION = 0x21;   // TMC2209 IOIN VERSION
static const long JOG_SPAN = 1000000000L;   // far target used for continuous jogging

TMC2209Stepper driver(&DRIVER_SERIAL, R_SENSE, DRIVER_ADDRESS);
AccelStepper stepper(AccelStepper::DRIVER, STEP_PIN, DIR_PIN);

// ---- interrupt-driven stepping -------------------------------------------------
// TMCStepper blocks >= 2 ms per register read (delay(replyDelay)), so stepper.run() in loop() would stall the
// step pulses for milliseconds during every status poll and lose steps at speed. Instead an IntervalTimer ISR
// calls stepper.run() every 25 us, and loop() only touches `stepper` through the wrappers below, which hold
// a nest-safe interrupt guard. Never hold the guard across a UART call or a Serial print.
IntervalTimer stepTimer;

// ---- stage sensors: limit interlock and home edges, sampled in the step ISR -------
// A tripped limit stops the pulses within one ISR tick, even while loop() is blocked in a UART read. A limit stops
// motion only toward its own end; the firmware learns which end that is the first time the switch trips while
// moving, so LS1/LS2 need no fixed assignment. A switch tripped with its end not yet learned (stage parked on it at
// power-up) does not stop motion: the command layer then allows only a dead-man JOG to move off it.
static volatile uint8_t s_level[3];             // filtered pin levels: LS1, LS2, HOME
static uint8_t          s_count[3];             // filter counters (ISR only)
static volatile bool    s_limitsNc = LIMITS_NC;
static volatile int8_t  s_lsEnd[2];             // end each limit guards: +1, -1, or 0 = not learned
static volatile uint8_t s_lsHit;                // bit i: limit i halted motion; loop() reports and clears it
static volatile long    s_lsHitPos[2];
static const uint8_t    HOME_EDGES = 8;         // ring of home edges (position, new level) for loop() to report
static volatile long    s_homeEdgePos[HOME_EDGES];
static volatile uint8_t s_homeEdgeLvl[HOME_EDGES];
static volatile uint8_t s_homeHead, s_homeTail;
// HOME trap: one-shot, armed by loop(). The first filtered home edge to s_trapLevel while moving in s_trapDir records
// its position and, with s_trapHalt, halts the pulses right there (the final approach stops on the edge itself, so
// neither loop() latency nor a UART read can move the zero).
static volatile int8_t  s_trapDir;              // 0 = disarmed; else the motion direction (+1/-1) the trap watches
static volatile uint8_t s_trapLevel;            // the new filtered level that springs it
static volatile bool    s_trapHalt;
static volatile bool    s_trapHit;
static volatile long    s_trapPos;

static inline bool trippedLevel(uint8_t level) { return s_limitsNc ? level == HIGH : level == LOW; }

static void stepIsr() {
  const uint8_t raw[3] = { (uint8_t)digitalReadFast(LS1_PIN), (uint8_t)digitalReadFast(LS2_PIN), (uint8_t)digitalReadFast(HOME_PIN) };
  uint8_t newTrip = 0;
  for (uint8_t i = 0; i < 3; i++) {
    if (raw[i] == s_level[i]) { s_count[i] = 0; continue; }
    if (++s_count[i] < SENSOR_FILTER_SAMPLES) continue;
    s_count[i] = 0;
    s_level[i] = raw[i];
    if (i < 2) { if (trippedLevel(raw[i])) newTrip |= 1 << i; }
    else {
      long p = stepper.currentPosition();
      uint8_t next = (s_homeHead + 1) % HOME_EDGES;
      if (next != s_homeTail) { s_homeEdgePos[s_homeHead] = p; s_homeEdgeLvl[s_homeHead] = raw[i]; s_homeHead = next; }
      if (s_trapDir != 0 && raw[i] == s_trapLevel) {
        long g = stepper.distanceToGo();
        if ((g > 0 && s_trapDir > 0) || (g < 0 && s_trapDir < 0)) {
          s_trapPos = p; s_trapHit = true; s_trapDir = 0;
          if (s_trapHalt) stepper.setCurrentPosition(p);   // zero speed, target here: no pulse after the edge
        }
      }
    }
  }
  long togo = stepper.distanceToGo();
  if (togo != 0) {
    int8_t dir = togo > 0 ? 1 : -1;
    bool t0 = trippedLevel(s_level[0]), t1 = trippedLevel(s_level[1]);
    bool halt = t0 && t1;                         // both at once: wiring fault or the wrong LIMITS NC|NO
    for (uint8_t i = 0; i < 2; i++) {
      if (!(i ? t1 : t0)) continue;
      if ((newTrip & (1 << i)) && s_lsEnd[i] == 0) s_lsEnd[i] = dir;   // learn this switch's end on its first trip while moving
      if (s_lsEnd[i] == dir) halt = true;
    }
    if (halt) {
      long p = stepper.currentPosition();
      stepper.setCurrentPosition(p);              // zero speed, target here
      for (uint8_t i = 0; i < 2; i++) if (i ? t1 : t0) { s_lsHit |= 1 << i; s_lsHitPos[i] = p; }
    }
  }
  stepper.run();
}

struct IrqGuard {
  uint32_t m;
  IrqGuard() { __asm__ volatile("mrs %0, primask" : "=r"(m)); __disable_irq(); }
  ~IrqGuard() { if (m == 0) __enable_irq(); }
};
static void spMoveTo(long p)            { IrqGuard g; stepper.moveTo(p); }
static void spMove(long d)              { IrqGuard g; stepper.move(d); }
static void spStop()                    { IrqGuard g; stepper.stop(); }
static void spSetMaxSpeed(float v)      { IrqGuard g; stepper.setMaxSpeed(v); }
static void spSetAccel(float a)         { IrqGuard g; stepper.setAcceleration(a); }
static void spSetPos(long p)            { IrqGuard g; stepper.setCurrentPosition(p); }
static void spHalt()                    { IrqGuard g; stepper.setCurrentPosition(stepper.currentPosition()); }
static long spPos()                     { IrqGuard g; return stepper.currentPosition(); }
static long spTarget()                  { IrqGuard g; return stepper.targetPosition(); }
static bool spRunning()                 { IrqGuard g; return stepper.isRunning(); }
static float spSpeed()                  { IrqGuard g; return stepper.speed(); }
static void spSnapshot(long &p, long &t, float &v, bool &r) {
  IrqGuard g; p = stepper.currentPosition(); t = stepper.targetPosition(); v = stepper.speed(); r = stepper.isRunning();
}

// ---------- state ----------
enum Mode { MODE_SPREAD, MODE_STEALTH };
static Mode     g_mode = USE_SPREADCYCLE ? MODE_SPREAD : MODE_STEALTH;
static bool     g_enabled = false;
static uint16_t g_currentMa = RUN_CURRENT_MA;
static uint16_t g_microsteps = MICROSTEPS;
static float    g_speedMm = DEFAULT_SPEED_MM;   // mm/s
static float    g_accelMm = DEFAULT_ACCEL_MM;   // mm/s^2
static bool     g_jog = false;
static uint32_t g_lastJogMs = 0;
static uint32_t g_lastRxMs = 0;
static uint8_t  g_bootVersion = 0;
static bool     g_wasConnected = false;
static bool     g_hostTimedOut = false;
static uint32_t g_lastPollMs = 0;
static uint8_t  g_zeroDrvCount = 0;
static bool     g_applyPending = false;         // speed/accel changed while moving; applied once stopped (lowering
                                                // AccelStepper's max speed mid-move drops the speed in one step)
// protocol v1 additions
static char     g_axis = '?';                   // axis tag from EEPROM: 'X', 'Y', 'Z' or '?' (none)
static uint32_t g_hostTimeoutMs = HOST_TIMEOUT_MS;   // HOSTTIMEOUT; back to the default when the host disconnects
static uint16_t g_streamHz = 0;                 // STREAM rate; 0 = off; off again when the host disconnects
static uint32_t g_lastStreamMs = 0;
static bool     g_homed = false;                // 1 after a successful HOME; see homedLost() for what clears it
// JOGV: velocity jog. AccelStepper drops the speed in one step if its max speed is lowered below the current speed
// (review R-6), so a lower JOGV speed first decelerates (stop()) and only takes the new max speed once the speed has
// fallen to it; a higher one raises the max speed and AccelStepper ramps up. Max speed is never set below |speed|.
enum JogvPhase : uint8_t { JV_RUN, JV_SLOW, JV_STOP };
static bool     g_jogv = false;                 // a JOGV jog owns the motion (g_jog is set too)
static float    g_jogvSteps = 0;                // commanded velocity, steps/s, signed, never 0 while g_jogv
static float    g_jogvMax = 0;                  // the max speed jogvService last applied, steps/s
static uint8_t  g_jogvPhase = JV_RUN;

// latest driver sample (filled by pollDriver)
static uint32_t g_drv = 0;
static uint16_t g_sg = 0;
static bool     g_newSample = false;

// DRV_STATUS bit masks (TMC2209 datasheet)
static const uint32_t B_OTPW = 1UL << 0, B_OT = 1UL << 1, B_S2GA = 1UL << 2, B_S2GB = 1UL << 3,
                      B_S2VSA = 1UL << 4, B_S2VSB = 1UL << 5, B_OLA = 1UL << 6, B_OLB = 1UL << 7,
                      B_STST = 1UL << 31;
static const uint32_t B_SHORTS = B_S2GA | B_S2GB | B_S2VSA | B_S2VSB;
static inline uint8_t csOf(uint32_t drv) { return (drv >> 16) & 0x1F; }

// ---------- helpers ----------
// Unit conversion: positions live in steps inside AccelStepper; everything a user sees or sends is mm.
static float stepsPerMm()      { return (float)FULL_STEPS_PER_REV * g_microsteps / LEAD_MM; }
static float toMm(long steps)  { return steps / stepsPerMm(); }
static long  toSteps(float mm) { return lroundf(mm * stepsPerMm()); }
static float stepUm()          { return 1000.0f / stepsPerMm(); }
static float maxSpeedMm()      { float cap = MAX_STEP_RATE / stepsPerMm(); return MAX_SPEED_MM < cap ? MAX_SPEED_MM : cap; }
static float minSpeedMm()      { return 1.0f / stepsPerMm(); }   // 1 step/s floor for JOGV and per-move speeds; AccelStepper's
                                                                 // 32-bit step interval overflows below ~0.0003 steps/s
static void  applyMotion()     { spSetMaxSpeed(g_speedMm * stepsPerMm()); spSetAccel(g_accelMm * stepsPerMm()); }
static const char *modeName(uint8_t m) { return m == MODE_SPREAD ? "SPREAD" : "STEALTH"; }
enum HomePhase : uint8_t { H_IDLE, H_SEEK, H_SEEK_STOP, H_BACKOFF, H_APPROACH };
static uint8_t  g_home = H_IDLE;                // HOME sequence phase
static bool isBusy() { return spRunning() || g_jog || g_home != H_IDLE; }
static uint32_t planMs(float mm, float speedMm) {         // a test's time limit: 1.5 x planned travel time + 10 s, at least the floor
  float ms = 1500.0f * mm / speedMm + 10000.0f;
  return ms > (float)TEST_TIME_LIMIT_MS ? (uint32_t)ms : TEST_TIME_LIMIT_MS;
}

// TMCStepper codes full step as 0, so microsteps(1) would silently do nothing: map 1 <-> 0 both ways.
static void     setDriverMicrosteps(uint16_t ms) { driver.microsteps(ms == 1 ? 0 : ms); }
static uint16_t readDriverMicrosteps()           { uint16_t ms = driver.microsteps(); return ms == 0 ? 1 : ms; }

static bool lsTripped(uint8_t i) { return trippedLevel(s_level[i]); }
// Why a move toward dir (+1/-1) is refused, or nullptr. Only a JOG may leave a switch whose end is not learned.
static const char *limitBlock(int dir, bool jog) {
  bool t0 = lsTripped(0), t1 = lsTripped(1);
  if (t0 && t1) return "limits-both-tripped:check-wiring-or-LIMITS-NC|NO";
  for (uint8_t i = 0; i < 2; i++) {
    if (!(i ? t1 : t0)) continue;
    if (s_lsEnd[i] == dir) return i ? "limit-ls2" : "limit-ls1";
    if (s_lsEnd[i] == 0 && !jog) return i ? "limit-ls2-end-unknown:jog-off-it" : "limit-ls1-end-unknown:jog-off-it";
  }
  return nullptr;
}

static void reply(const char *cmd, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
static void reply(const char *cmd, const char *fmt, ...) {
  char buf[200];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(buf, sizeof buf, fmt, ap);
  va_end(ap);
  Serial.print("OK ");
  Serial.print(cmd);
  if (buf[0]) { Serial.print(' '); Serial.print(buf); }
  Serial.print('\n');
}
static void err(const char *cmd, const char *reason) {
  Serial.print("ERR "); Serial.print(cmd); Serial.print(' '); Serial.print(reason); Serial.print('\n');
}
static void ok(const char *cmd) {                        // a bare "OK <cmd>" (reply() with an empty format trips -Wformat)
  Serial.print("OK "); Serial.print(cmd); Serial.print('\n');
}
// Log verbosity (LOG 0|1|2). The station forwards every firmware line into its own log, so the firmware logs
// generously: 0 = only boot, faults, results, homing outcomes and limit events; 1 = every EVT line (the default);
// 2 = also `EVT DBG ...`: commands received (not the high-rate JOG/JOGV/HB/STATUS), driver on/off/configure with its
// read-back, and where each motion ended.
static uint8_t g_logLevel = 1;
static bool essentialEvent(const char *text) {
  static const char *const KEEP[] = {"BOOT", "FAULT", "RESULT", "HOMED", "HOME FAIL", "LIMIT"};
  for (const char *k : KEEP) if (!strncmp(text, k, strlen(k))) return true;
  return false;
}
static void evt(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void evt(const char *fmt, ...) {
  char buf[220];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(buf, sizeof buf, fmt, ap);
  va_end(ap);
  if (g_logLevel == 0 && !essentialEvent(buf)) return;
  Serial.print("EVT "); Serial.print(buf); Serial.print('\n');
}
static void dbg(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
static void dbg(const char *fmt, ...) {
  if (g_logLevel < 2) return;
  char buf[200];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(buf, sizeof buf, fmt, ap);
  va_end(ap);
  Serial.print("EVT DBG "); Serial.print(buf); Serial.print('\n');
}

// ---------- test machinery (declared early; defined below) ----------
enum TestId { T_NONE, T_UART, T_COILS, T_REVS, T_SWEEP, T_LIMITS };
static TestId   g_test = T_NONE;
static const char *testName(uint8_t t) {
  switch (t) {
    case T_UART: return "UART"; case T_COILS: return "COILS"; case T_REVS: return "REVS"; case T_SWEEP: return "SWEEP";
    case T_LIMITS: return "LIMITS"; default: return "";
  }
}
static void testAbort(const char *reason);
static void testFault(const char *kind);
static void homeFail(const char *reason);             // HOME machinery (defined below the tests)
// Every path that ends motion from outside a sequence ends the test and the HOME sequence with it.
static void abortActivity(const char *reason) { testAbort(reason); homeFail(reason); }

// ---------- driver configuration ----------
static void applyMode() {
  if (g_mode == MODE_SPREAD) {
    driver.en_spreadCycle(true);
    driver.pwm_autoscale(false);   // station setting
  } else {
    driver.en_spreadCycle(false);
    driver.pwm_autoscale(true);    // StealthChop needs autoscale to regulate current and for StallGuard4 to read
  }
}
static void applyCurrent() { driver.rms_current(g_currentMa, HOLD_MULTIPLIER); }

static void jogEnd() { g_jog = false; g_jogv = false; }
static void hardHalt() {            // stop pulses immediately, keep position count
  spHalt();
  jogEnd();
}
static void softStop() {            // decelerate to a stop, keep holding
  jogEnd();
  spStop();
}
static void driverOff() {
  digitalWrite(EN_PIN, HIGH);
  driver.toff(0);
  if (g_enabled) dbg("driver off");
  g_enabled = false;
}
// homed=0 whenever the step count may no longer match the carriage: the outputs went off while the motor turned (the
// rotor coasts unpowered, and re-enabling snaps it to the nearest matching electrical angle), or the driver reset or
// faulted (its microstep counter restarts). A stationary DISABLE/ESTOP keeps it: the driver keeps its microstep
// counter while VM is up and the lead screw holds the carriage, so ENABLE puts the rotor back where it was.
static void homedLost() { g_homed = false; }
static void estop() {
  bool moving = spSpeed() != 0.0f;
  hardHalt();
  driverOff();
  if (moving) homedLost();
}
static void driverOn() {
  driver.toff(4);
  digitalWrite(EN_PIN, LOW);
  g_enabled = true;
  dbg("driver on current_ma=%u microsteps=%u mode=%s", g_currentMa, g_microsteps, g_mode == MODE_SPREAD ? "SPREAD" : "STEALTH");
}

static void configureDriver() {
  driver.toff(0);                                        // driver stays disabled until ENABLE
  driver.pdn_disable(true);                              // PDN_UART pin used for UART
  driver.mstep_reg_select(true);                         // microsteps from the register, not MS1/MS2
  driver.I_scale_analog(false);                          // current from UART, not the VREF pot
  driver.TPWMTHRS(0);
  driver.TCOOLTHRS(TCOOLTHRS_VALUE);
  driver.SGTHRS(SGTHRS_VALUE);
  setDriverMicrosteps(g_microsteps);
  applyCurrent();
  applyMode();
  driver.GSTAT(0b111);                                   // clear the power-up latch so later reset/uv_cp/drv_err are real
  dbg("driver configured microsteps=%u current_ma=%u hold=%.2f", g_microsteps, g_currentMa, (double)HOLD_MULTIPLIER);
}

static void bootReport() {
  evt("BOOT version=0x%02X expected=0x%02X uart=%s enabled=%d axis=%c proto=1", g_bootVersion, EXPECTED_VERSION,
      g_bootVersion == EXPECTED_VERSION ? "OK" : "FAIL", g_enabled, g_axis);
}

// ---------- axis tag in EEPROM ----------
// Teensy 3.x keeps its EEPROM across uploads (the bootloader erases program flash only): owner bench check.
static char readAxisTag() {
  uint8_t m = EEPROM.read(AXIS_EEPROM_ADDR), a = EEPROM.read(AXIS_EEPROM_ADDR + 1), c = EEPROM.read(AXIS_EEPROM_ADDR + 2);
  if (m != AXIS_TAG_MAGIC || (uint8_t)~a != c) return '?';                // erased (0xFF) or never written
  return (a == 'X' || a == 'Y' || a == 'Z') ? (char)a : '?';
}
static bool writeAxisTag(char a) {
  EEPROM.update(AXIS_EEPROM_ADDR, AXIS_TAG_MAGIC);
  EEPROM.update(AXIS_EEPROM_ADDR + 1, (uint8_t)a);
  EEPROM.update(AXIS_EEPROM_ADDR + 2, (uint8_t)~(uint8_t)a);
  return readAxisTag() == a;                                               // read back, never assume
}

// ---------- driver polling and guards ----------
static void pollDriver() {
  g_drv = driver.DRV_STATUS();
  g_sg = (g_test == T_REVS || g_test == T_SWEEP) ? driver.SG_RESULT() : 0;
  g_newSample = true;

  // GSTAT.reset: the driver lost VM (or restarted) and is back on its pin/OTP defaults: 1/8 from MS1/MS2, current
  // from the VREF pot, chopper on. Stop, write our configuration back, and say so. A driver with no VM reads 0 here.
  if (driver.GSTAT() & 1) {
    estop(); homedLost();                                // the driver's microstep counter restarted with it
    configureDriver();                                   // also clears GSTAT
    g_bootVersion = driver.version();
    evt("FAULT driver-reset reconfigured version=0x%02X microsteps=%u", g_bootVersion, readDriverMicrosteps());
    abortActivity("driver-reset");
  }

  if (g_enabled) {                  // all-zero DRV_STATUS while enabled means the UART is not answering
    if (g_drv == 0) {
      if (++g_zeroDrvCount >= 3) {
        g_zeroDrvCount = 0; estop(); homedLost(); evt("FAULT uart-lost"); testFault("uart-lost"); homeFail("uart-lost");
      }
    }
    else g_zeroDrvCount = 0;
  }
  if (g_drv & (B_OT | B_OTPW)) {
    estop(); homedLost(); evt("FAULT overtemp ot=%d otpw=%d", !!(g_drv & B_OT), !!(g_drv & B_OTPW));
    testFault("overtemp"); homeFail("overtemp");
  } else if (g_drv & B_SHORTS) {
    estop(); homedLost(); evt("FAULT short s2ga=%d s2gb=%d s2vsa=%d s2vsb=%d", !!(g_drv & B_S2GA), !!(g_drv & B_S2GB),
                 !!(g_drv & B_S2VSA), !!(g_drv & B_S2VSB));
    testFault("short"); homeFail("short");
  }
}

static void guards() {
  uint32_t now = millis();
  // USB host gone (DTR dropped)
  bool connected = (bool)Serial;
  if (connected && !g_wasConnected) { g_lastRxMs = now; g_hostTimedOut = false; bootReport(); }
  if (!connected && g_wasConnected) {                    // per-connection settings: the next host starts from the defaults
    g_streamHz = 0; g_hostTimeoutMs = HOST_TIMEOUT_MS;
  }
  g_wasConnected = connected;
  if (!connected && (g_enabled || isBusy() || g_test != T_NONE)) {
    estop(); abortActivity("host-gone");
  }
  // heartbeat: any received line counts
  if (!g_hostTimedOut && (isBusy() || g_test != T_NONE) && (now - g_lastRxMs) > g_hostTimeoutMs) {
    g_hostTimedOut = true;
    softStop();
    evt("FAULT host-timeout");
    abortActivity("host-timeout");
  }
  // jog dead-man
  if (g_jog && (now - g_lastJogMs) > JOG_TIMEOUT_MS) softStop();
  // driver flags
  uint32_t period = (isBusy() || g_test != T_NONE) ? POLL_ACTIVE_MS : POLL_IDLE_MS;
  if ((now - g_lastPollMs) >= period) { g_lastPollMs = now; pollDriver(); }
}

// =============================== TESTS ===============================
struct Stats {
  uint32_t n = 0; uint32_t sgMin = 0xFFFF, sgMax = 0; uint64_t sgSum = 0, csSum = 0;
  void reset() { n = 0; sgMin = 0xFFFF; sgMax = 0; sgSum = 0; csSum = 0; }
  void add(uint16_t sg, uint8_t cs) { n++; if (sg < sgMin) sgMin = sg; if (sg > sgMax) sgMax = sg; sgSum += sg; csSum += cs; }
  uint32_t sgAvg() const { return n ? (uint32_t)(sgSum / n) : 0; }
  uint32_t csAvg() const { return n ? (uint32_t)(csSum / n) : 0; }
  uint32_t mn() const { return n ? sgMin : 0; }
};

static uint32_t t_start = 0, t_legStart = 0;
static int      t_phase = 0, t_n = 0;
static long     t_startPos = 0, t_steps = 0;
static Stats    t_stats;
static uint32_t t_stalls = 0, t_stallRun = 0;
static Mode     t_savedMode;
static float    t_savedSpeed;
static uint32_t t_samples = 0, t_ola = 0, t_olb = 0, t_shorts = 0;
static int      t_stage = 0;
static float    t_stageSpeed = 0;
static uint8_t  t_ifcnt0 = 0, t_origTbl = 0;
static bool     t_faultSeen = false;
static char     t_faultKind[16] = "";
static uint32_t t_limitMs = TEST_TIME_LIMIT_MS;
// TEST LIMITS
static uint8_t  t_lsHits = 0;                   // limit hits handed over by serviceSensors()
static long     t_hitPos[2];                    // where each switch tripped (steps)
static int8_t   t_endSw[2];                     // switch found at the - end [0] and the + end [1]
static long     t_endPos[2];
static uint8_t  t_homeN = 0;
static long     t_homePos[4];
static uint8_t  t_homeLvl[4];
// HOME sequence state (g_home holds the phase)
static uint32_t h_start = 0, h_limitMs = 0;
static int8_t   h_dir = 0;                      // direction of the current search leg
static uint8_t  h_leg = 0;                      // search legs started
static uint8_t  h_ends = 0;                     // ends reached (limit trips, or a leg that started against a switch)
static uint8_t  h_lsHits = 0;                   // limit hits handed over by serviceSensors()
static uint16_t h_edges = 0;                    // home-sensor edges seen during this HOME (diagnostic)
static long     h_coarse = 0;                   // coarse reference edge (steps), from the search
static long     h_backoffPos = 0;               // start of the final approach
static float    h_seekMm = 0, h_slowMm = 0;     // speeds actually used (clamped to the step-rate ceiling)

static void testRestore() {
  if (g_mode != t_savedMode) { g_mode = t_savedMode; applyMode(); }
  if (spRunning()) g_applyPending = true;          // still decelerating: apply the user's speed once stopped
  else applyMotion();
}

static void testEnd(const char *verdict, const char *fmt, ...) __attribute__((format(printf, 2, 3)));
static void testEnd(const char *verdict, const char *fmt, ...) {
  char buf[180];
  va_list ap;
  va_start(ap, fmt);
  vsnprintf(buf, sizeof buf, fmt, ap);
  va_end(ap);
  uint8_t id = g_test;
  g_test = T_NONE;
  testRestore();
  evt("RESULT %s %s %s", testName(id), verdict, buf);
}

static void testAbort(const char *reason) {
  if (g_test == T_NONE) return;
  if (g_test == T_UART) { driver.tbl(t_origTbl); }   // undo the scratch write
  softStop();
  testEnd("ABORTED", "reason=%s elapsed_ms=%lu", reason, (unsigned long)(millis() - t_start));
}

// A driver fault during a test is a test FAIL (the motor/wiring is bad), not just an abort.
static void testFault(const char *kind) {
  if (g_test == T_NONE) return;
  strncpy(t_faultKind, kind, sizeof t_faultKind - 1);
  t_faultSeen = true;
  if (g_test == T_UART) { testEnd("FAIL", "fault=%s", kind); return; }
  testEnd("FAIL", "fault=%s elapsed_ms=%lu", kind, (unsigned long)(millis() - t_start));
}

// Software stall check. Uses DIAG when wired; otherwise sg_result < 2*SGTHRS (StealthChop only).
static bool stallNow() {
  if (DIAG_PIN >= 0 && digitalRead(DIAG_PIN)) return true;
  if (SGTHRS_VALUE > 0 && g_mode == MODE_STEALTH && fabsf(spSpeed()) > SG_MIN_SPEED_MM * stepsPerMm() && g_sg < 2U * SGTHRS_VALUE) return true;
  return false;
}
static const char *stallMethod() {
  if (DIAG_PIN >= 0) return "diag";
  if (SGTHRS_VALUE > 0 && g_mode == MODE_STEALTH) return "sg";
  return "none";
}
static void stallUpdate() {   // call once per new sample; 3 consecutive hits = a stall
  if (stallNow()) { if (++t_stallRun == 3) t_stalls++; } else t_stallRun = 0;
}

static void testBegin(uint8_t id) {
  g_test = (TestId)id; t_start = millis(); t_phase = 0; t_stats.reset(); t_stalls = 0; t_stallRun = 0;
  t_samples = t_ola = t_olb = t_shorts = 0; t_faultSeen = false; t_faultKind[0] = 0;
  t_savedMode = g_mode; t_savedSpeed = g_speedMm; g_newSample = false;
  t_limitMs = TEST_TIME_LIMIT_MS; t_lsHits = 0; t_homeN = 0;
}

// Report limit hits and home edges captured by the ISR. A hit ends any test except TEST LIMITS, which consumes it,
// and is handed to the HOME sequence, which decides (a search reverses once; any other phase fails).
static void serviceSensors() {
  uint8_t hits; long hp[2];
  { IrqGuard g; hits = s_lsHit; s_lsHit = 0; hp[0] = s_lsHitPos[0]; hp[1] = s_lsHitPos[1]; }
  if (hits) {
    jogEnd();
    for (uint8_t i = 0; i < 2; i++) {
      if (!(hits & (1 << i))) continue;
      t_hitPos[i] = hp[i];
      evt("LIMIT ls%u tripped pos_mm=%.4f end=%+d", i + 1, (double)toMm(hp[i]), s_lsEnd[i]);
    }
    if (g_test == T_LIMITS) t_lsHits |= hits;
    else if (g_test != T_NONE) testAbort("limit");
    if (g_home != H_IDLE) h_lsHits |= hits;
  }
  for (;;) {
    long p; uint8_t lvl;
    {
      IrqGuard g;
      if (s_homeTail == s_homeHead) break;
      p = s_homeEdgePos[s_homeTail]; lvl = s_homeEdgeLvl[s_homeTail]; s_homeTail = (s_homeTail + 1) % HOME_EDGES;
    }
    // Report budget: a carriage resting on the edge (where HOME leaves it) can sit on the sensor threshold, and noise
    // there could make an edge every 200 us. The edges are still consumed and counted; only the lines are capped.
    static uint32_t winMs = 0; static uint16_t inWin = 0, suppressed = 0;
    uint32_t now = millis();
    if (now - winMs >= 1000) { winMs = now; inWin = 0; }
    if (inWin < HOME_EDGE_EVT_PER_S) {
      inWin++;
      if (suppressed) { evt("HOME edge level=%u pos_mm=%.4f suppressed=%u", lvl, (double)toMm(p), suppressed); suppressed = 0; }
      else evt("HOME edge level=%u pos_mm=%.4f", lvl, (double)toMm(p));
    } else if (suppressed < 0xFFFF) suppressed++;
    if (g_test == T_LIMITS && t_homeN < 4) { t_homePos[t_homeN] = p; t_homeLvl[t_homeN] = lvl; t_homeN++; }
    if (g_home != H_IDLE && h_edges < 0xFFFF) h_edges++;
  }
}

static void testStep() {
  if (g_test == T_NONE) return;
  uint32_t now = millis();
  if (now - t_start > t_limitMs) { testAbort("time-limit"); return; }
  if (s_lsHit) return;                  // a limit just halted the motor: serviceSensors() hands it over first, next pass
  bool fresh = g_newSample;
  g_newSample = false;

  switch (g_test) {
    case T_UART: {
      if (t_phase == 0) {
        uint8_t v = driver.version();
        if (v != EXPECTED_VERSION) { testEnd("FAIL", "version=0x%02X expected=0x%02X readback=skipped", v, EXPECTED_VERSION); return; }
        t_origTbl = driver.tbl();                 // CHOPCONF is one of the few readable+writable registers
        t_ifcnt0 = driver.IFCNT();
        driver.tbl((t_origTbl + 1) & 3);          // blank time field: harmless while the driver is not chopping
        t_phase = 1;
      } else {
        uint8_t back = driver.tbl();
        uint8_t ifc = driver.IFCNT();
        uint8_t want = (t_origTbl + 1) & 3;
        driver.tbl(t_origTbl);                    // restore
        bool ok = (back == want) && ((uint8_t)(ifc - t_ifcnt0) == 1);
        testEnd(ok ? "PASS" : "FAIL", "version=0x%02X wrote_tbl=%u read_tbl=%u ifcnt_delta=%u", EXPECTED_VERSION,
                want, back, (uint8_t)(ifc - t_ifcnt0));
      }
      break;
    }
    case T_COILS: {
      // NOTE: the TMC2209 datasheet says OLA/OLB (open load) are only meaningful while the motor is moving
      // in spreadCycle (and at moderate speed), so this test forces spreadCycle and moves slowly.
      if (t_phase == 0) {
        t_startPos = spPos();
        if (g_mode != MODE_SPREAD) { g_mode = MODE_SPREAD; applyMode(); }
        t_steps = (long)COILS_REVS * FULL_STEPS_PER_REV * g_microsteps;
        float vMm = COILS_SPEED_MM < maxSpeedMm() ? COILS_SPEED_MM : maxSpeedMm();
        t_stageSpeed = vMm * stepsPerMm();                        // steps/s, for the cruise check below
        spSetMaxSpeed(t_stageSpeed);
        spSetAccel(g_accelMm * stepsPerMm());
        spMoveTo(t_startPos + t_steps);
        t_legStart = now; t_phase = 1;
        evt("PROGRESS COILS moving mm=%.3f speed_mm_s=%.4f", (double)toMm(t_steps), (double)vMm);
      } else if (t_phase == 1) {
        if (fresh && (now - t_legStart) > 400 && fabsf(spSpeed()) > t_stageSpeed * 0.9f) {
          t_samples++;
          if (g_drv & B_OLA) t_ola++;
          if (g_drv & B_OLB) t_olb++;
          if (g_drv & B_SHORTS) t_shorts++;
        }
        if (!spRunning()) {
          bool openA = t_samples >= 5 && t_ola * 10UL > t_samples * 6UL;   // "persists across most samples" = >60 %
          bool openB = t_samples >= 5 && t_olb * 10UL > t_samples * 6UL;
          bool few = t_samples < 5;
          bool fail = t_shorts > 0 || openA || openB || few;
          testEnd(fail ? "FAIL" : "PASS", "samples=%lu ola=%lu olb=%lu shorts=%lu open_a=%d open_b=%d%s",
                  (unsigned long)t_samples, (unsigned long)t_ola, (unsigned long)t_olb, (unsigned long)t_shorts,
                  openA, openB, few ? " reason=too-few-samples" : "");
        }
      }
      break;
    }
    case T_REVS: {
      if (t_phase == 0) {
        t_startPos = spPos();
        t_steps = (long)t_n * FULL_STEPS_PER_REV * g_microsteps;
        spMoveTo(t_startPos + t_steps);
        t_phase = 1;
        evt("PROGRESS REVS leg=forward mm=%.3f", (double)toMm(t_steps));
      } else if (t_phase == 1 || t_phase == 2) {
        if (fresh) { t_stats.add(g_sg, csOf(g_drv)); stallUpdate(); }
        if (t_stalls) { softStop(); }
        if (!spRunning()) {
          if (t_stalls) {
            testEnd("FAIL", "n=%d stalls=%lu faults=0 detect=%s sg_min=%lu sg_avg=%lu sg_max=%lu mode=%s",
                    t_n, (unsigned long)t_stalls, stallMethod(), (unsigned long)t_stats.mn(),
                    (unsigned long)t_stats.sgAvg(), (unsigned long)t_stats.sgMax, modeName(g_mode));
          } else if (t_phase == 1) {
            spMoveTo(t_startPos); t_phase = 2;
            evt("PROGRESS REVS leg=back mm=%.3f", (double)toMm(t_steps));
          } else {
            testEnd("PASS", "n=%d mm=%.3f stalls=0 faults=0 detect=%s sg_min=%lu sg_avg=%lu sg_max=%lu cs_avg=%lu mode=%s "
                    "end_pos_ok=%d check=return-to-start",
                    t_n, (double)toMm(t_steps), stallMethod(), (unsigned long)t_stats.mn(), (unsigned long)t_stats.sgAvg(),
                    (unsigned long)t_stats.sgMax, (unsigned long)t_stats.csAvg(), modeName(g_mode),
                    spPos() == t_startPos);
          }
        }
      }
      break;
    }
    case T_SWEEP: {
      // StallGuard4 on the TMC2209 reads in StealthChop, so SWEEP switches to StealthChop for its run
      // and testRestore() puts the previous mode back afterwards.
      if (t_phase == 0) {
        t_startPos = spPos();
        g_mode = MODE_STEALTH; applyMode();
        t_stage = 0; t_phase = 1; t_legStart = 0;
      }
      if (t_phase == 1) {        // launch a stage
        float top = maxSpeedMm();
        float lo = SWEEP_MIN_SPEED_MM < top ? SWEEP_MIN_SPEED_MM : top;   // at 256 the step-rate ceiling sits below the low stage: flat sweep
        float vMm = lo + (top - lo) * t_stage / (SWEEP_STAGES > 1 ? SWEEP_STAGES - 1 : 1);
        float v = vMm * stepsPerMm(), a = TEST_ACCEL_MM * stepsPerMm();   // steps/s and steps/s^2; fixed accel caps a stage at 4.5 mm
        t_stageSpeed = v;
        long ramp = (long)(v * v / a) + 1;                       // accel + decel distance
        long dist = toSteps(SWEEP_CRUISE_MM) + ramp;
        spSetMaxSpeed(v);
        spSetAccel(a);
        spMove((t_stage & 1) ? -dist : dist);
        t_stats.reset(); t_phase = 2; t_stallRun = 0;
      } else if (t_phase == 2) {
        if (fresh && fabsf(spSpeed()) >= t_stageSpeed * 0.95f) { t_stats.add(g_sg, csOf(g_drv)); stallUpdate(); }
        if (t_stalls) softStop();
        if (!spRunning()) {
          evt("SWEEP stage=%d speed_mm_s=%.4f sg_avg=%lu cs_actual=%lu samples=%lu stalls=%lu mode=%s", t_stage + 1,
              (double)(t_stageSpeed / stepsPerMm()), (unsigned long)t_stats.sgAvg(), (unsigned long)t_stats.csAvg(),
              (unsigned long)t_stats.n, (unsigned long)t_stalls, modeName(g_mode));
          if (t_stalls) { testEnd("FAIL", "stage=%d stalls=%lu detect=%s", t_stage + 1, (unsigned long)t_stalls, stallMethod()); return; }
          if (++t_stage >= SWEEP_STAGES) {
            testEnd("PASS", "stages=%d max_speed_mm_s=%.4f stalls=0 faults=0 detect=%s net_mm=%.4f", SWEEP_STAGES,
                    (double)(t_stageSpeed / stepsPerMm()), stallMethod(), (double)toMm(spPos() - t_startPos));
          } else t_phase = 1;
        }
      }
      break;
    }
    case T_LIMITS: {
      // Find the switch at each end at LIMIT_SEEK_SPEED_MM, check each releases on a back-off, log the home edges
      // passed on the way, measure switch-to-switch travel, then park midway. The ISR interlock does the stopping.
      float v = LIMIT_SEEK_SPEED_MM < maxSpeedMm() ? LIMIT_SEEK_SPEED_MM : maxSpeedMm();
      if (t_phase == 0) {
        t_startPos = spPos();
        spSetMaxSpeed(v * stepsPerMm()); spSetAccel(TEST_ACCEL_MM * stepsPerMm());
        spMove(-toSteps(LIMIT_SEEK_MM));
        t_phase = 1;
        evt("PROGRESS LIMITS seek dir=-1 max_mm=%.1f speed_mm_s=%.3f", (double)LIMIT_SEEK_MM, (double)v);
      } else if (t_phase == 1 || t_phase == 3) {         // seeking: - end, then + end
        uint8_t end = t_phase == 1 ? 0 : 1;
        if (t_lsHits) {
          if (t_lsHits == 3) { testEnd("FAIL", "reason=both-switches-tripped-together end=%+d", end ? 1 : -1); return; }
          int8_t sw = (t_lsHits & 1) ? 0 : 1;
          if (end == 1 && sw == t_endSw[0]) { testEnd("FAIL", "reason=same-switch-at-both-ends ls%d", sw + 1); return; }
          t_lsHits = 0; t_endSw[end] = sw; t_endPos[end] = t_hitPos[sw];
          spMove(end ? -toSteps(LIMIT_BACKOFF_MM) : toSteps(LIMIT_BACKOFF_MM));
          t_phase = end ? 4 : 2;
        } else if (!spRunning()) {
          testEnd("FAIL", "reason=no-switch-within-search dir=%+d searched_mm=%.2f", end ? 1 : -1, (double)toMm(spPos() - t_startPos));
        }
      } else if (t_phase == 2 || t_phase == 4) {         // backing off: the switch must have released
        uint8_t end = t_phase == 2 ? 0 : 1;
        if (!spRunning()) {
          if (lsTripped(t_endSw[end])) {
            testEnd("FAIL", "reason=ls%d-did-not-release backoff_mm=%.2f", t_endSw[end] + 1, (double)LIMIT_BACKOFF_MM); return;
          }
          if (end == 0) {                                // search the other way: twice the distance to the first switch, plus margin
            long reach = 2 * (t_startPos - t_endPos[0]) + toSteps(LIMIT_MARGIN_MM);
            spMove(reach);
            t_phase = 3;
            evt("PROGRESS LIMITS seek dir=+1 max_mm=%.1f", (double)toMm(reach));
          } else {
            spMoveTo((t_endPos[0] + t_endPos[1]) / 2);   // park midway between the switches
            t_phase = 5;
          }
        }
      } else if (t_phase == 5 && !spRunning()) {
        bool homeSeen = t_homeN > 0;
        char hb[60] = "";
        for (uint8_t k = 0; k < t_homeN && k < 2; k++) {
          size_t n = strlen(hb);
          snprintf(hb + n, sizeof hb - n, " home%u_mm=%.3f lvl=%u", k + 1, (double)toMm(t_homePos[k] - spPos()), t_homeLvl[k]);
        }
        testEnd(homeSeen ? "PASS" : "FAIL", "travel_mm=%.3f minus=ls%d plus=ls%d centre_mm=%.3f home_edges=%u%s%s",
                (double)toMm(t_endPos[1] - t_endPos[0]), t_endSw[0] + 1, t_endSw[1] + 1, (double)toMm(spPos()),
                t_homeN, hb, homeSeen ? "" : " reason=home-not-seen");
      }
      break;
    }
    default: break;
  }
}

// =============================== HOME ===============================
// Zeroing on the photo-interrupter; PROTOCOL.md has the scenario table. With d = HOME_DIR and F = HOME_FLAG_LEVEL,
// the reference edge R is where the filtered home level changes to F while moving in d (moving -d, where it leaves F).
//  seek      search at HOME_SEEK_SPEED_MM toward R: -d when the start reads F (R is behind), else d (or -d when an
//            earlier HOME's zero says R is behind). The ISR records R (trap, record only) and loop() decelerates.
//            A limit before R reverses the search once; reaching the second end means there is no R: FAIL.
//  backoff   to HOME_BACKOFF_MM on the clear side of the coarse R, at search speed; the sensor must read clear there.
//  approach  in d at HOME_SLOW_SPEED_MM from rest; the ISR halts the pulses on the edge itself (trap, halt) and that
//            position becomes 0. So the zero is always taken moving d, slowly, after >= HOME_BACKOFF_MM of travel.
// The limit interlock stays live in the ISR throughout. STOP, ESTOP, DISABLE, a host timeout or DTR drop, a driver
// fault and the time limit all end the sequence through homeFail().
static uint8_t homeClearLevel() { return HOME_FLAG_LEVEL ? LOW : HIGH; }
static const char *homePhaseName(uint8_t ph) {
  switch (ph) {
    case H_SEEK: return "seek"; case H_SEEK_STOP: return "seek-stop"; case H_BACKOFF: return "backoff";
    case H_APPROACH: return "approach"; default: return "none";
  }
}
static void trapArm(int8_t dir, uint8_t level, bool halt) {
  IrqGuard g; s_trapHit = false; s_trapLevel = level; s_trapHalt = halt; s_trapDir = dir;
}
static void trapDisarm() { IrqGuard g; s_trapDir = 0; s_trapHit = false; }
static void homeRestore() {                     // the user's SPEED/ACCEL back, once stopped (never lowered mid-move, R-6)
  if (spRunning()) g_applyPending = true; else applyMotion();
}

static void homeFail(const char *reason) {
  if (g_home == H_IDLE) return;
  uint8_t ph = g_home;
  g_home = H_IDLE;
  trapDisarm();
  softStop();                                   // decelerate and hold; a no-op after ESTOP/DISABLE or an ISR halt
  homeRestore();
  evt("HOME FAIL reason=%s phase=%s pos_mm=%.5f legs=%u ends=%u edges=%u elapsed_ms=%lu", reason, homePhaseName(ph),
      (double)toMm(spPos()), h_leg, h_ends, h_edges, (unsigned long)(millis() - h_start));
}

// Exactly one switch is tripped and it guards the end that dir moves toward (learned end).
static bool endGuarded(int dir) {
  bool t0 = lsTripped(0), t1 = lsTripped(1);
  if (t0 == t1) return false;
  return s_lsEnd[t0 ? 0 : 1] == dir;
}

// Start a search leg toward s. Starting against the switch at that end counts as reaching that end.
static void homeSeek(int8_t s, const char *after) {
  const char *blk = limitBlock(s, false);
  if (blk && endGuarded(s)) {
    if (++h_ends >= 2) { homeFail("no-edge-between-limits"); return; }
    s = -s; after = "at-limit";
    blk = limitBlock(s, false);
  }
  if (blk) { homeFail(blk); return; }
  h_dir = s; h_leg++;
  trapArm(s, s == HOME_DIR ? HOME_FLAG_LEVEL : homeClearLevel(), false);
  spSetMaxSpeed(h_seekMm * stepsPerMm());
  spMove((long)s * toSteps(HOME_SEEK_MAX_MM));
  g_home = H_SEEK;
  evt("HOME phase=seek dir=%+d leg=%u speed_mm_s=%.4f max_mm=%.1f after=%s limit_s=%lu", s, h_leg, (double)h_seekMm,
      (double)HOME_SEEK_MAX_MM, after, (unsigned long)(h_limitMs / 1000));
}

// Called right after "OK HOME started"; the command handler has checked enabled, idle and the limit switches.
static void homeStart() {
  h_start = millis(); h_leg = 0; h_ends = 0; h_lsHits = 0; h_edges = 0;
  bool wasHomed = g_homed;
  homedLost();                                  // a re-home in progress: the old zero no longer counts, even if this fails
  float top = maxSpeedMm();
  h_seekMm = HOME_SEEK_SPEED_MM < top ? HOME_SEEK_SPEED_MM : top;
  h_slowMm = HOME_SLOW_SPEED_MM < top ? HOME_SLOW_SPEED_MM : top;
  float sec = 2.0f * HOME_SEEK_MAX_MM / h_seekMm + (HOME_BACKOFF_MM + 2.0f) / h_seekMm +
              (HOME_BACKOFF_MM + HOME_APPROACH_EXTRA_MM) / h_slowMm;   // worst case: both legs, backoff, approach
  h_limitMs = planMs(sec, 1.0f);                // sec holds seconds: 1.5 x plan + 10 s, at least TEST_TIME_LIMIT_MS
  spSetAccel(TEST_ACCEL_MM * stepsPerMm());     // fixed ramps, so the sequence's distances do not depend on ACCEL
  g_home = H_SEEK;
  if (s_level[2] == HOME_FLAG_LEVEL) homeSeek(-HOME_DIR, "start-on-flag");
  else if (wasHomed && spPos() * (long)HOME_DIR > 0) homeSeek(-HOME_DIR, "start-beyond-old-zero");
  else homeSeek(HOME_DIR, "start");
}

static void homeDone(long edge) {
  trapDisarm();
  long p = spPos();
  spSetPos(p - edge);                           // the ISR halted on the edge, so p == edge: the carriage sits at 0
  g_home = H_IDLE;
  g_homed = true;
  homeRestore();
  evt("HOME phase=edge edge_mm=%.5f coarse_mm=%.5f coarse_minus_edge_um=%.2f legs=%u ends=%u elapsed_ms=%lu",
      (double)toMm(edge), (double)toMm(h_coarse), (double)(toMm(h_coarse - edge) * 1000.0f), h_leg, h_ends,
      (unsigned long)(millis() - h_start));
  long z = spPos();
  if (z == 0) evt("HOMED edge_mm=%.5f pos=0", (double)toMm(edge));
  else evt("HOMED edge_mm=%.5f pos=%.5f", (double)toMm(edge), (double)toMm(z));
}

static void homeStep() {
  if (g_home == H_IDLE) return;
  if (millis() - h_start > h_limitMs) { homeFail("time-limit"); return; }
  bool hit, run, lsPending; long tp;
  { IrqGuard g; hit = s_trapHit; tp = s_trapPos; run = stepper.isRunning(); lsPending = s_lsHit != 0; }
  if (lsPending) return;                        // a limit just halted the motor: serviceSensors() hands it over first, next pass
  uint8_t ls = h_lsHits;
  h_lsHits = 0;
  switch (g_home) {
    case H_SEEK:
      if (hit) {
        { IrqGuard g; s_trapHit = false; }
        h_coarse = tp;
        spStop();                               // decelerate past R; its position is already exact
        g_home = H_SEEK_STOP;
      } else if (ls) {
        if (++h_ends >= 2) { homeFail("no-edge-between-limits"); return; }
        homeSeek(-h_dir, "limit");
      } else if (!run) {
        homeFail("no-edge-within-search");      // a whole leg without an edge or a switch: check the limit switches
      }
      break;
    case H_SEEK_STOP: {
      if (run) break;                           // a limit trip while decelerating is fine: the backoff checks its own path
      h_backoffPos = h_coarse - (long)HOME_DIR * toSteps(HOME_BACKOFF_MM);
      long togo = h_backoffPos - spPos();
      if (togo != 0) {
        if (limitBlock(togo > 0 ? 1 : -1, false)) { homeFail("edge-too-close-to-limit"); return; }
        spMoveTo(h_backoffPos);                 // still at search speed
      }
      g_home = H_BACKOFF;
      evt("HOME phase=backoff edge_mm=%.5f to_mm=%.5f", (double)toMm(h_coarse), (double)toMm(h_backoffPos));
      break;
    }
    case H_BACKOFF:
      if (ls) { homeFail("limit-during-backoff"); return; }
      if (run) break;
      if (s_level[2] == HOME_FLAG_LEVEL) { homeFail("flag-at-backoff"); return; }
      if (limitBlock(HOME_DIR, false)) { homeFail("limit-before-approach"); return; }
      trapArm(HOME_DIR, HOME_FLAG_LEVEL, true);
      spSetMaxSpeed(h_slowMm * stepsPerMm());
      spMoveTo(h_backoffPos + (long)HOME_DIR * toSteps(HOME_BACKOFF_MM + HOME_APPROACH_EXTRA_MM));
      g_home = H_APPROACH;
      evt("HOME phase=approach dir=%+d speed_mm_s=%.4f max_mm=%.3f", HOME_DIR, (double)h_slowMm,
          (double)(HOME_BACKOFF_MM + HOME_APPROACH_EXTRA_MM));
      break;
    case H_APPROACH:
      if (hit && !run) { homeDone(tp); return; }
      if (hit) break;                           // cannot happen (the ISR halted on it); wait for the stop
      if (ls) { homeFail("limit-during-approach"); return; }
      if (!run) { homeFail("edge-lost"); return; }   // the whole approach without the edge: sensor or flag not repeatable
      break;
    default: break;
  }
}

// =============================== JOGV ===============================
// One transition per call toward the commanded velocity; see the g_jogv comment. Runs from loop() and from JOGV.
static void jogvService() {
  if (!g_jogv) return;
  long p, t; float v; bool r;
  spSnapshot(p, t, v, r);
  int8_t want = g_jogvSteps > 0 ? 1 : -1;
  float vt = fabsf(g_jogvSteps);
  if (!r) {                                     // at rest: (re)start toward want
    if (limitBlock(want, true)) { jogEnd(); return; }   // the next JOGV gets the ERR with the reason
    spSetAccel(g_accelMm * stepsPerMm());
    spSetMaxSpeed(vt); g_jogvMax = vt;
    spMoveTo(p + (long)want * JOG_SPAN);
    g_jogvPhase = JV_RUN;
    return;
  }
  if (v == 0.0f) return;
  int8_t moving = v > 0 ? 1 : -1;
  if (moving != want) {                         // reversal: decelerate to rest, restart above
    if (g_jogvPhase == JV_RUN) spStop();
    g_jogvPhase = JV_STOP;
    return;
  }
  if (fabsf(v) > vt * 1.001f) {                 // too fast: decelerate, keep the max speed until |v| has come down to vt
    if (g_jogvPhase == JV_RUN) spStop();
    g_jogvPhase = JV_SLOW;
    return;
  }
  if (g_jogvPhase != JV_RUN) {                  // down to vt (or a new command at or above |v|): cruise at vt
    spSetMaxSpeed(vt); g_jogvMax = vt;
    spMoveTo(p + (long)want * JOG_SPAN);
    g_jogvPhase = JV_RUN;
  } else if (vt != g_jogvMax) {                 // a new speed at or above |v|: AccelStepper ramps to it at ACCEL
    spSetMaxSpeed(vt); g_jogvMax = vt;
  }
}

// =============================== STREAM ===============================
// P lines from ISR state only (no driver UART read). Dropped, never queued, when the host is not draining USB, so a
// stalled host cannot block loop() and with it the guards.
static void streamService() {
  if (!g_streamHz) return;
  uint32_t now = millis();
  if (now - g_lastStreamMs < 1000UL / g_streamHz) return;
  g_lastStreamMs = now;
  if (Serial.availableForWrite() <= 0) return;
  long p, t; float v; bool r;
  spSnapshot(p, t, v, r);
  char b[176];
  int n = snprintf(b, sizeof b, "P pos=%.5f tgt=%.5f v=%.4f en=%d mv=%d ls1=%d ls2=%d home=%u homed=%d\n",
                   (double)toMm(p), (double)toMm(t), (double)(v / stepsPerMm()), g_enabled, r, lsTripped(0),
                   lsTripped(1), s_level[2], g_homed);
  if (n > 0 && n < (int)sizeof b) Serial.write((const uint8_t *)b, (size_t)n);
}

// =============================== COMMANDS ===============================
static void doStatus() {
  long sPos, sTgt; float sSpd; bool sRun;
  spSnapshot(sPos, sTgt, sSpd, sRun);                    // one atomic snapshot; UART reads below run unguarded
  uint32_t drv = driver.DRV_STATUS();
  uint16_t sg = driver.SG_RESULT();
  uint32_t tstep = driver.TSTEP();
  uint8_t gstat = driver.GSTAT();
  Serial.print("OK STATUS");
  char b[160];
  snprintf(b, sizeof b, " pos_mm=%.5f target_mm=%.5f speed_mm_s=%.4f enabled=%d mode=%s current=%u microsteps=%u step_um=%.4f",
           (double)toMm(sPos), (double)toMm(sTgt), (double)(sSpd / stepsPerMm()), g_enabled, modeName(g_mode),
           g_currentMa, g_microsteps, (double)stepUm());
  Serial.print(b);
  snprintf(b, sizeof b, " cs_actual=%u sg_result=%u tstep=%lu", csOf(drv), sg, (unsigned long)tstep);
  Serial.print(b);
  snprintf(b, sizeof b, " ot=%d otpw=%d s2ga=%d s2gb=%d s2vsa=%d s2vsb=%d ola=%d olb=%d stst=%d", !!(drv & B_OT),
           !!(drv & B_OTPW), !!(drv & B_S2GA), !!(drv & B_S2GB), !!(drv & B_S2VSA), !!(drv & B_S2VSB), !!(drv & B_OLA),
           !!(drv & B_OLB), !!(drv & B_STST));
  Serial.print(b);
  snprintf(b, sizeof b, " ls1=%d ls2=%d ls1_end=%d ls2_end=%d home=%u limits=%s", lsTripped(0), lsTripped(1), s_lsEnd[0],
           s_lsEnd[1], s_level[2], s_limitsNc ? "NC" : "NO");
  Serial.print(b);
  snprintf(b, sizeof b, " homed=%d home_phase=%s axis=%c jog=%s stream_hz=%u host_timeout_ms=%lu", g_homed,
           homePhaseName(g_home), g_axis, g_jogv ? "v" : g_jog ? "dir" : "none", g_streamHz, (unsigned long)g_hostTimeoutMs);
  Serial.print(b);
  snprintf(b, sizeof b, " reset=%d drv_err=%d uv_cp=%d test=%s uart=%s\n", gstat & 1, (gstat >> 1) & 1, (gstat >> 2) & 1,
           g_test == T_NONE ? "none" : testName(g_test), drv ? "OK" : "FAIL");
  Serial.print(b);
}

static void doInfo() {
  char b[640];
  snprintf(b, sizeof b,
           "step_pin=%d dir_pin=%d en_pin=%d diag_pin=%d r_sense=%.3f addr=%u baud=%lu full_steps=%u lead_mm=%.3f run_ma=%u max_ma=%u "
           "hold_mult=%.2f microsteps=%u step_um=%.4f spread=%d speed_mm_s=%.4f max_speed_mm_s=%.4f accel_mm_s2=%.4f "
           "max_move_mm=%.3f max_revs=%d max_step_rate=%.0f "
           "jog_timeout_ms=%lu host_timeout_ms=%lu test_limit_ms=%lu sgthrs=%u tcoolthrs=%lu boot_version=0x%02X "
           "ls1_pin=%d ls2_pin=%d home_pin=%d limits=%s",
           STEP_PIN, DIR_PIN, EN_PIN, DIAG_PIN, (double)R_SENSE, DRIVER_ADDRESS, (unsigned long)DRIVER_BAUD,
           FULL_STEPS_PER_REV, (double)LEAD_MM, RUN_CURRENT_MA, MAX_CURRENT_MA, (double)HOLD_MULTIPLIER, g_microsteps,
           (double)stepUm(), g_mode == MODE_SPREAD, (double)g_speedMm, (double)maxSpeedMm(), (double)g_accelMm,
           (double)MAX_MOVE_MM, MAX_TEST_REVS, (double)MAX_STEP_RATE,
           (unsigned long)JOG_TIMEOUT_MS, (unsigned long)g_hostTimeoutMs, (unsigned long)TEST_TIME_LIMIT_MS, SGTHRS_VALUE,
           (unsigned long)TCOOLTHRS_VALUE, g_bootVersion, LS1_PIN, LS2_PIN, HOME_PIN, s_limitsNc ? "NC" : "NO");
  Serial.print("OK INFO "); Serial.print(b);   // not reply(): its 200-byte buffer would cut this line short
  snprintf(b, sizeof b,
           " fw=xyz_stage_axis proto=1 axis=%c identity=%c home_dir=%+d home_flag_level=%u home_seek_mm_s=%.4f "
           "home_slow_mm_s=%.4f home_backoff_mm=%.3f home_extra_mm=%.3f home_seek_max_mm=%.1f stream_max_hz=%u "
           "host_timeout_min_ms=%lu host_timeout_max_ms=%lu",
           g_axis, IDENTITY_LETTER, HOME_DIR, HOME_FLAG_LEVEL, (double)HOME_SEEK_SPEED_MM, (double)HOME_SLOW_SPEED_MM,
           (double)HOME_BACKOFF_MM, (double)HOME_APPROACH_EXTRA_MM, (double)HOME_SEEK_MAX_MM, STREAM_MAX_HZ,
           (unsigned long)HOST_TIMEOUT_MIN_MS, (unsigned long)HOST_TIMEOUT_MAX_MS);
  Serial.print(b); Serial.print('\n');
}

static bool parseLong(const char *s, long &out) {
  if (!s || !*s) return false;
  char *e; long v = strtol(s, &e, 10);
  if (*e) return false;
  out = v; return true;
}
static bool parseFloat(const char *s, float &out) {
  if (!s || !*s) return false;
  char *e; float v = strtof(s, &e);
  if (*e || isnan(v) || isinf(v)) return false;
  out = v; return true;
}

static void handleLine(char *line) {
  g_lastRxMs = millis();
  g_hostTimedOut = false;
  char *saveptr;
  char *cmd = strtok_r(line, " \t", &saveptr);
  if (!cmd) return;
  for (char *p = cmd; *p; p++) *p = toupper((unsigned char)*p);
  char *a1 = strtok_r(nullptr, " \t", &saveptr);
  char *a2 = strtok_r(nullptr, " \t", &saveptr);
  if (a1) for (char *p = a1; *p; p++) *p = toupper((unsigned char)*p);
  if (a2) for (char *p = a2; *p; p++) *p = toupper((unsigned char)*p);
  long iv; float fv;
  if (strcmp(cmd, "JOG") && strcmp(cmd, "JOGV") && strcmp(cmd, "HB") && strcmp(cmd, "STATUS") && strcmp(cmd, "S"))
    dbg("rx %s%s%s%s%s", cmd, a1 ? " " : "", a1 ? a1 : "", a2 ? " " : "", a2 ? a2 : "");

  if (!strcmp(cmd, "S")) {                               // the station's port scan: exactly "DEV: x X", no OK prefix
    Serial.print("DEV: "); Serial.print(IDENTITY_LETTER); Serial.print(' '); Serial.print(g_axis); Serial.print('\n');
  }
  else if (!strcmp(cmd, "PING")) { reply("PING", "uptime_ms=%lu", (unsigned long)millis()); }
  else if (!strcmp(cmd, "HB")) { ok("HB"); }             // explicit heartbeat (any line is one)
  else if (!strcmp(cmd, "INFO")) { doInfo(); }
  else if (!strcmp(cmd, "STATUS")) { doStatus(); }
  else if (!strcmp(cmd, "AXIS")) {
    if (!a1) { reply("AXIS", "axis=%c", g_axis); return; }
    if (strlen(a1) != 1 || (a1[0] != 'X' && a1[0] != 'Y' && a1[0] != 'Z')) { err("AXIS", "bad-arg-X|Y|Z"); return; }
    if (g_enabled || isBusy() || g_test != T_NONE) { err("AXIS", "busy"); return; }
    if (!writeAxisTag(a1[0])) { g_axis = readAxisTag(); err("AXIS", "eeprom-verify-failed"); return; }
    g_axis = a1[0];
    reply("AXIS", "axis=%c stored=1", g_axis);
  }
  else if (!strcmp(cmd, "HOSTTIMEOUT")) {
    if (a1) {
      if (!parseLong(a1, iv) || iv <= 0) { err("HOSTTIMEOUT", "bad-arg"); return; }
      bool clamped = iv < (long)HOST_TIMEOUT_MIN_MS || iv > (long)HOST_TIMEOUT_MAX_MS;
      g_hostTimeoutMs = iv < (long)HOST_TIMEOUT_MIN_MS ? HOST_TIMEOUT_MIN_MS : iv > (long)HOST_TIMEOUT_MAX_MS ? HOST_TIMEOUT_MAX_MS : (uint32_t)iv;
      reply("HOSTTIMEOUT", "ms=%lu clamped=%d", (unsigned long)g_hostTimeoutMs, clamped);
    } else reply("HOSTTIMEOUT", "ms=%lu clamped=0", (unsigned long)g_hostTimeoutMs);
  }
  else if (!strcmp(cmd, "STREAM")) {
    if (a1) {
      if (!parseLong(a1, iv) || iv < 0) { err("STREAM", "bad-arg"); return; }
      bool clamped = iv > (long)STREAM_MAX_HZ;
      g_streamHz = clamped ? STREAM_MAX_HZ : (uint16_t)iv;
      g_lastStreamMs = millis() - 1000UL;                // first P line on the next pass
      reply("STREAM", "hz=%u clamped=%d", g_streamHz, clamped);
    } else reply("STREAM", "hz=%u clamped=0", g_streamHz);
  }
  else if (!strcmp(cmd, "ENABLE")) {
    if (isBusy() || g_test != T_NONE) { err("ENABLE", "busy"); return; }
    g_bootVersion = driver.version();                     // check every time: VM may have been cycled since boot
    if (g_bootVersion != EXPECTED_VERSION) { err("ENABLE", "driver-uart-not-ok"); return; }
    configureDriver();                                    // a driver that lost VM is back on 1/8 and VREF current
    if (readDriverMicrosteps() != g_microsteps) { err("ENABLE", "driver-readback-mismatch"); return; }
    driverOn(); reply("ENABLE", "enabled=1 microsteps=%u", g_microsteps);
  }
  else if (!strcmp(cmd, "DISABLE")) {
    bool moving = spSpeed() != 0.0f;
    hardHalt(); abortActivity("disabled"); driverOff();
    if (moving) homedLost();
    reply("DISABLE", "enabled=0");
  }
  else if (!strcmp(cmd, "STOP")) { softStop(); abortActivity("stop"); reply("STOP", "pos_mm=%.5f", (double)toMm(spPos())); }
  else if (!strcmp(cmd, "ESTOP")) { estop(); abortActivity("estop"); reply("ESTOP", "enabled=0 pos_mm=%.5f", (double)toMm(spPos())); }
  else if (!strcmp(cmd, "CURRENT")) {
    if (!parseLong(a1, iv) || iv < 0) { err("CURRENT", "bad-arg"); return; }
    bool clamped = iv > (long)MAX_CURRENT_MA;
    g_currentMa = clamped ? MAX_CURRENT_MA : (uint16_t)iv;
    applyCurrent();
    reply("CURRENT", "ma=%u clamped=%d", g_currentMa, clamped);
  }
  else if (!strcmp(cmd, "MICROSTEPS")) {
    if (!parseLong(a1, iv) || iv < 1 || iv > 256 || (iv & (iv - 1))) { err("MICROSTEPS", "bad-arg-power-of-2-1..256"); return; }
    if (isBusy() || g_test != T_NONE) { err("MICROSTEPS", "busy"); return; }
    if (driver.version() != EXPECTED_VERSION) { err("MICROSTEPS", "driver-uart-not-ok"); return; }   // the read-back below needs it
    // The rotor does not move when the resolution changes (the driver keeps its microstep counter), so rescale the
    // step count to keep pos_mm true; a coarser setting rounds it to the nearest new step.
    long oldPos = spPos(); uint16_t oldMs = g_microsteps;
    setDriverMicrosteps((uint16_t)iv);
    if (readDriverMicrosteps() != iv) { setDriverMicrosteps(oldMs); err("MICROSTEPS", "driver-readback-mismatch"); return; }
    g_microsteps = (uint16_t)iv;
    spSetPos(lround((double)oldPos * g_microsteps / oldMs));
    bool slowed = g_speedMm > maxSpeedMm();               // only the step-rate ceiling can bind, at fine resolutions
    if (slowed) g_speedMm = maxSpeedMm();
    applyMotion();
    reply("MICROSTEPS", "microsteps=%u step_um=%.4f pos_mm=%.5f speed_mm_s=%.4f max_speed_mm_s=%.4f speed_clamped=%d", g_microsteps,
          (double)stepUm(), (double)toMm(spPos()), (double)g_speedMm, (double)maxSpeedMm(), slowed);
  }
  else if (!strcmp(cmd, "SPEED")) {
    if (!parseFloat(a1, fv) || fv <= 0) { err("SPEED", "bad-arg"); return; }
    bool clamped = fv > maxSpeedMm(); g_speedMm = clamped ? maxSpeedMm() : fv;
    bool later = isBusy() || g_test != T_NONE;            // never change the ramp mid-move; apply once stopped
    if (later) g_applyPending = true; else applyMotion();
    reply("SPEED", "speed_mm_s=%.4f clamped=%d applies=%s", (double)g_speedMm, clamped, later ? "on-stop" : "now");
  }
  else if (!strcmp(cmd, "ACCEL")) {
    if (!parseFloat(a1, fv) || fv <= 0) { err("ACCEL", "bad-arg"); return; }
    bool clamped = fv < MIN_ACCEL_MM || fv > MAX_ACCEL_MM;
    g_accelMm = fv < MIN_ACCEL_MM ? MIN_ACCEL_MM : fv > MAX_ACCEL_MM ? MAX_ACCEL_MM : fv;
    bool later = isBusy() || g_test != T_NONE;
    if (later) g_applyPending = true; else applyMotion();
    reply("ACCEL", "accel_mm_s2=%.4f clamped=%d applies=%s", (double)g_accelMm, clamped, later ? "on-stop" : "now");
  }
  else if (!strcmp(cmd, "MODE")) {
    if (!a1 || (strcmp(a1, "STEALTH") && strcmp(a1, "SPREAD"))) { err("MODE", "bad-arg-STEALTH|SPREAD"); return; }
    if (g_test != T_NONE) { err("MODE", "busy"); return; }
    g_mode = !strcmp(a1, "STEALTH") ? MODE_STEALTH : MODE_SPREAD; applyMode();
    reply("MODE", "mode=%s", modeName(g_mode));
  }
  else if (!strcmp(cmd, "MOVE") || !strcmp(cmd, "MOVETO") || !strcmp(cmd, "REVS")) {
    bool isRevs = !strcmp(cmd, "REVS"), isAbs = !strcmp(cmd, "MOVETO");
    if (!g_enabled) { err(cmd, "not-enabled"); return; }
    if (g_test != T_NONE) { err(cmd, "test-running"); return; }
    bool clamped = false;
    float mm;                                             // MOVE: relative mm; MOVETO: absolute mm; REVS: whole revolutions (LEAD_MM each)
    if (isRevs) {
      if (!parseLong(a1, iv)) { err(cmd, "bad-arg"); return; }
      if (iv > MAX_TEST_REVS) { iv = MAX_TEST_REVS; clamped = true; }
      if (iv < -MAX_TEST_REVS) { iv = -MAX_TEST_REVS; clamped = true; }
      mm = iv * LEAD_MM;
    } else {
      if (!parseFloat(a1, fv)) { err(cmd, "bad-arg"); return; }
      mm = fv;
    }
    float vMm = g_speedMm; bool vClamped = false;         // MOVE/MOVETO <mm> [mm_s]: a speed for this move only
    if (!isRevs && a2) {
      if (!parseFloat(a2, fv) || fv <= 0) { err(cmd, "bad-speed"); return; }
      vClamped = fv > maxSpeedMm() || fv < minSpeedMm();
      vMm = fv > maxSpeedMm() ? maxSpeedMm() : fv < minSpeedMm() ? minSpeedMm() : fv;
    }
    if (isBusy()) { err(cmd, "busy"); return; }           // one move at a time; a move never re-ramps one in progress
    long steps;
    if (isAbs) {
      if (fabsf(mm) > 10000.0f) { err(cmd, "bad-arg"); return; }  // keeps toSteps() inside a long at 256 microsteps
      steps = toSteps(mm) - spPos();
      long cap = toSteps(MAX_MOVE_MM);
      if (steps > cap) { steps = cap; clamped = true; }
      if (steps < -cap) { steps = -cap; clamped = true; }
    } else {
      if (mm > MAX_MOVE_MM) { mm = MAX_MOVE_MM; clamped = true; }
      if (mm < -MAX_MOVE_MM) { mm = -MAX_MOVE_MM; clamped = true; }
      steps = toSteps(mm);
    }
    if (steps != 0) { const char *why = limitBlock(steps > 0 ? 1 : -1, false); if (why) { err(cmd, why); return; } }
    spSetMaxSpeed(vMm * stepsPerMm());                    // applyMotion() with this move's speed
    spSetAccel(g_accelMm * stepsPerMm());
    spMove(steps);
    if (isRevs) reply(cmd, "mm=%.5f target_mm=%.5f clamped=%d", (double)toMm(steps), (double)toMm(spTarget()), clamped);
    else reply(cmd, "mm=%.5f target_mm=%.5f clamped=%d speed_mm_s=%.4f speed_clamped=%d", (double)toMm(steps),
               (double)toMm(spTarget()), clamped, (double)vMm, vClamped);
  }
  else if (!strcmp(cmd, "JOGV")) {
    if (!parseFloat(a1, fv)) { err("JOGV", "bad-arg"); return; }
    if (fabsf(fv) < minSpeedMm()) {                       // neutral (or under 1 step/s): ends a jog; a MOVE, HOME or TEST is left alone
      if (g_jog) softStop();
      reply("JOGV", "mm_s=%.4f clamped=0", 0.0);
      return;
    }
    if (!g_enabled) { err("JOGV", "not-enabled"); return; }
    if (g_test != T_NONE) { err("JOGV", "test-running"); return; }
    if (g_home != H_IDLE || (spRunning() && !g_jog)) { err("JOGV", "busy"); return; }
    bool clamped = fabsf(fv) > maxSpeedMm();
    if (clamped) fv = fv > 0 ? maxSpeedMm() : -maxSpeedMm();
    const char *why = limitBlock(fv > 0 ? 1 : -1, true);
    if (why) { if (g_jog) softStop(); err("JOGV", why); return; }
    if (!g_jogv) { g_jogvPhase = JV_RUN; g_jogvMax = -1.0f; }   // taking over (or starting) a jog: re-derive its state
    g_jog = true; g_jogv = true; g_jogvSteps = fv * stepsPerMm(); g_lastJogMs = millis();
    jogvService();
    reply("JOGV", "mm_s=%.4f clamped=%d", (double)fv, clamped);
  }
  else if (!strcmp(cmd, "HOME")) {
    if (!g_enabled) { err("HOME", "not-enabled"); return; }
    if (g_test != T_NONE) { err("HOME", "test-running"); return; }
    if (isBusy()) { err("HOME", "busy"); return; }
    bool t0 = lsTripped(0), t1 = lsTripped(1);            // a switch with an unknown end could be either end: jog off it first
    if (t0 && t1) { err("HOME", "limits-both-tripped:check-wiring-or-LIMITS-NC|NO"); return; }
    if (t0 && !s_lsEnd[0]) { err("HOME", "limit-ls1-end-unknown:jog-off-it"); return; }
    if (t1 && !s_lsEnd[1]) { err("HOME", "limit-ls2-end-unknown:jog-off-it"); return; }
    reply("HOME", "started");
    homeStart();                                          // EVT HOME phase=seek ... follows the reply
  }
  else if (!strcmp(cmd, "JOG")) {
    if (!g_enabled) { err("JOG", "not-enabled"); return; }
    if (g_test != T_NONE) { err("JOG", "test-running"); return; }
    if (g_home != H_IDLE) { err("JOG", "busy"); return; }
    if (!parseLong(a1, iv) || iv < -1 || iv > 1) { err("JOG", "bad-arg"); return; }
    if (iv == 0) { softStop(); reply("JOG", "dir=0"); return; }
    const char *why = limitBlock((int)iv, true);
    if (why) { softStop(); err("JOG", why); return; }
    bool fresh = !g_jog || !spRunning();                  // the host repeats JOG every 100 ms: only a new jog sets the ramp
    if (fresh) { if (spRunning()) { err("JOG", "busy"); return; } applyMotion(); spMove(iv > 0 ? JOG_SPAN : -JOG_SPAN); }
    else if ((spTarget() - spPos() > 0) != (iv > 0)) { softStop(); err("JOG", "reverse:release-first"); return; }
    g_jog = true; g_lastJogMs = millis();
    reply("JOG", "dir=%ld", iv);
  }
  else if (!strcmp(cmd, "ZERO")) {
    if (isBusy() || g_test != T_NONE) { err("ZERO", "busy"); return; }
    spSetPos(0); homedLost(); reply("ZERO", "pos_mm=0");
  }
  else if (!strcmp(cmd, "TEST")) {
    if (!a1) { err("TEST", "missing-name"); return; }
    if (g_test != T_NONE) { err("TEST", "test-running"); return; }
    if (isBusy()) { err("TEST", "busy"); return; }
    if (!strcmp(a1, "UART")) { testBegin(T_UART); reply("TEST", "UART started"); return; }
    if (!g_enabled) { err("TEST", "not-enabled"); return; }
    if (lsTripped(0) || lsTripped(1)) { err("TEST", "limit-tripped:jog-off-it"); return; }   // every motion test starts clear of both ends
    if (!strcmp(a1, "COILS")) {
      float v = COILS_SPEED_MM < maxSpeedMm() ? COILS_SPEED_MM : maxSpeedMm();
      testBegin(T_COILS); t_limitMs = planMs(COILS_REVS * LEAD_MM, v);
      reply("TEST", "COILS started limit_s=%lu", (unsigned long)(t_limitMs / 1000));
    }
    else if (!strcmp(a1, "SWEEP")) {
      float top = maxSpeedMm(), lo = SWEEP_MIN_SPEED_MM < top ? SWEEP_MIN_SPEED_MM : top, ms = 0;
      for (int k = 0; k < SWEEP_STAGES; k++) {            // same stage plan as testStep: cruise plus ramps at each stage speed
        float v = lo + (top - lo) * k / (SWEEP_STAGES > 1 ? SWEEP_STAGES - 1 : 1);
        ms += (SWEEP_CRUISE_MM + v * v / TEST_ACCEL_MM) / v;
      }
      testBegin(T_SWEEP); t_limitMs = planMs(ms, 1.0f);    // ms holds seconds here: distance/speed summed per stage
      reply("TEST", "SWEEP started limit_s=%lu", (unsigned long)(t_limitMs / 1000));
    }
    else if (!strcmp(a1, "LIMITS")) {
      float v = LIMIT_SEEK_SPEED_MM < maxSpeedMm() ? LIMIT_SEEK_SPEED_MM : maxSpeedMm();
      float plan = LIMIT_SEEK_MM + (2 * LIMIT_SEEK_MM + LIMIT_MARGIN_MM) + 2 * LIMIT_BACKOFF_MM + LIMIT_SEEK_MM;   // worst case
      testBegin(T_LIMITS); t_limitMs = planMs(plan, v);
      reply("TEST", "LIMITS started limit_s=%lu", (unsigned long)(t_limitMs / 1000));
    }
    else if (!strcmp(a1, "REVS")) {
      long n = 1;
      if (a2 && !parseLong(a2, n)) { err("TEST", "bad-arg"); return; }
      if (n < 1) { err("TEST", "bad-arg"); return; }
      long maxN = (long)(MAX_MOVE_MM / LEAD_MM); if (maxN > MAX_TEST_REVS) maxN = MAX_TEST_REVS;
      bool clamped = n > maxN; if (clamped) n = maxN;
      testBegin(T_REVS); t_n = (int)n; applyMotion(); t_limitMs = planMs(2 * n * LEAD_MM, g_speedMm);
      reply("TEST", "REVS started n=%ld clamped=%d limit_s=%lu", n, clamped, (unsigned long)(t_limitMs / 1000));
    } else err("TEST", "unknown-test");
  }
  else if (!strcmp(cmd, "LOG")) {
    if (!parseLong(a1, iv) || iv < 0 || iv > 2) { err("LOG", "bad-arg-0|1|2"); return; }
    g_logLevel = (uint8_t)iv;
    reply("LOG", "level=%u", g_logLevel);
  }
  else if (!strcmp(cmd, "LIMITS")) {
    if (!a1 || (strcmp(a1, "NC") && strcmp(a1, "NO"))) { err("LIMITS", "bad-arg-NC|NO"); return; }
    if (isBusy() || g_test != T_NONE) { err("LIMITS", "busy"); return; }
    { IrqGuard g; s_limitsNc = !strcmp(a1, "NC"); s_lsEnd[0] = s_lsEnd[1] = 0; }   // new meaning: forget the learned ends
    reply("LIMITS", "limits=%s ls1=%d ls2=%d ends=forgotten", s_limitsNc ? "NC" : "NO", lsTripped(0), lsTripped(1));
  }
  else { err(cmd, "unknown-command"); }
}

// =============================== setup / loop ===============================
void setup() {
  pinMode(EN_PIN, OUTPUT); digitalWrite(EN_PIN, HIGH);   // outputs off first, before anything else
  pinMode(STEP_PIN, OUTPUT); digitalWrite(STEP_PIN, LOW);
  pinMode(DIR_PIN, OUTPUT); digitalWrite(DIR_PIN, LOW);
  if (DIAG_PIN >= 0) pinMode(DIAG_PIN, INPUT);
  pinMode(LS1_PIN, INPUT_PULLUP); pinMode(LS2_PIN, INPUT_PULLUP); pinMode(HOME_PIN, INPUT_PULLUP);
  delayMicroseconds(50);                                 // let the pull-ups settle, then seed the filters so boot is not an edge
  s_level[0] = digitalReadFast(LS1_PIN); s_level[1] = digitalReadFast(LS2_PIN); s_level[2] = digitalReadFast(HOME_PIN);
  g_axis = readAxisTag();                                // before the first BOOT line or identity reply
  Serial.begin(115200);                                  // baud ignored on Teensy USB

  DRIVER_SERIAL.begin(DRIVER_BAUD, DRIVER_HALF_DUPLEX ? SERIAL_8N1_HALF_DUPLEX : SERIAL_8N1);
  driver.begin();
  configureDriver();
  g_bootVersion = driver.version();                      // expect 0x21

  if (g_speedMm > maxSpeedMm()) g_speedMm = maxSpeedMm(); // the step-rate ceiling can bind at a fine boot MICROSTEPS
  stepper.setMaxSpeed(g_speedMm * stepsPerMm());         // stepTimer is not running yet, so no guard needed
  stepper.setAcceleration(g_accelMm * stepsPerMm());
  stepper.setCurrentPosition(0);
  g_lastRxMs = millis();
  stepTimer.begin(stepIsr, 25);                          // start stepping last, after the stepper is configured
}

void loop() {
  static char buf[96];
  static uint8_t len = 0;
  static bool overflow = false;

  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n') {
      buf[len] = 0;
      if (overflow) { err("LINE", "too-long"); }
      else if (len) { if (buf[len - 1] == '\r') buf[len - 1] = 0; handleLine(buf); }
      else g_lastRxMs = millis();
      len = 0; overflow = false;
    } else if (len < sizeof buf - 1) buf[len++] = c;
    else overflow = true;
  }

  serviceSensors();                                      // limit hits first, so testStep()/homeStep() never mistake a halt for a finished leg
  guards();
  jogvService();                                         // before the line below: a JOGV reversal passes through rest
  if (g_jog && !spRunning()) jogEnd();
  homeStep();
  if (g_applyPending && !spRunning() && g_test == T_NONE && g_home == H_IDLE && !g_jog) { g_applyPending = false; applyMotion(); }
  testStep();
  streamService();
  static bool wasRunning = false;                        // where each motion ended, for the log (LOG 2)
  bool running = spRunning();
  if (wasRunning && !running) dbg("stopped pos_mm=%.5f target_mm=%.5f", (double)toMm(spPos()), (double)toMm(spTarget()));
  wasRunning = running;
}
