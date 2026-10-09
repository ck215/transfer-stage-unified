// xyz_stage_mega: the transfer station's 50 mm XYZ stage on one Arduino Mega 2560, all three axes (owner rulings
// 2026-10-09, docs/rebuild/MEGA_STANDARD.md). It speaks the Stepper Probe's frame protocol byte for byte
// (firmware/stepper_firmware), plus the standard '#' extension layer (station_std.h) and the XYZ features: a per-axis
// limit interlock in interrupt context, HOME and ZERO, the host timeout, and TMC2209 status over one UART bus.
// PROTOCOL.md says what it does; CHANGES.md says what differs from stepper_firmware and xyz_stage_axis, and why.

#include <Arduino.h>
#include <avr/io.h>
#include <avr/interrupt.h>
#include <avr/wdt.h>
#include <math.h>
#include <TMCStepper.h>

// ===================== CONSTANTS: bench values are the owner's =====================
// Pins (MEGA_STANDARD §5: the Stepper Probe's map). The step ISR uses the port bits below; setup() checks them
// against the core's pin tables and refuses to enable on a mismatch.
const uint8_t X_STEP_PIN = 32, X_DIR_PIN = 33, X_EN_PIN = 34;    // PC5, PC4, PC3
const uint8_t Y_STEP_PIN = 22, Y_DIR_PIN = 23, Y_EN_PIN = 24;    // PA0, PA1, PA2
const uint8_t Z_STEP_PIN = 42, Z_DIR_PIN = 43, Z_EN_PIN = 44;    // PL7, PL6, PL5 (EN is active low on all three)
const uint8_t X_LS1_PIN = 2, X_LS2_PIN = 3;                      // PE4, PE5 (INT4, INT5)
const uint8_t Y_LS1_PIN = 18, Y_LS2_PIN = 19;                    // PD3, PD2 (INT3, INT2)
const uint8_t Z_LS1_PIN = 20, Z_LS2_PIN = 21;                    // PD1, PD0 (INT1, INT0)
const uint8_t X_HOME_PIN = A8, Y_HOME_PIN = A9, Z_HOME_PIN = A10; // PK0, PK1, PK2 (PCINT16-18)
const unsigned long BAUD_RATE = 500000;                           // the Probe family's host baud rate

// Drivers: three TMC2209 on Serial2 (TX2 through 1 kOhm and RX2 direct to the shared PDN_UART), addresses X 0, Y 1, Z 2
const unsigned long DRIVER_BAUD = 115200;
const float    R_SENSE         = 0.11f;   // sense resistor, ohms (most TMC2209 modules)
const uint16_t RUN_CURRENT_MA  = 600;     // RMS run current: the Teensy axis firmware's and the Stepper Probe's 600
const uint16_t MAX_CURRENT_MA  = 1000;    // the Teensy firmware's current clamp; nothing here sets a current above it
const float    HOLD_MULTIPLIER = 0.5f;    // hold current = run current x this
const uint16_t MICROSTEPS      = 8;       // 1600 counts per mm on the 1 mm lead (MEGA_STANDARD §3); 0.625 um per count
const bool     USE_SPREADCYCLE = true;    // the station's chopper setting

// Motion, in counts and counts/s (the frame protocol's units)
const uint16_t MAX_RATE        = 4000;    // counts/s ceiling of every motion: the Teensy axis firmware's 2.5 mm/s. The
                                          // station's dials stop at 3200; the Stepper firmware clamped at 6400
const float    MANUAL_DEAD_ZONE = 0.05f;  // stick dead zone (stepper_firmware)
const float    MAX_JOG_SPEED   = 6400.0f; // a jog packet asking for more is malformed and ignored (stepper_firmware)
const uint16_t STEP_TICK_US    = 50;      // step ISR period (Timer1 CTC): 20 kHz; 3200 counts/s is 6.25 ticks per step
const uint8_t  SENSOR_FILTER_TICKS = 4;   // a sensor level must hold this many ticks (200 us) to count (chopper noise)
const bool     LIMITS_NC       = false;   // switch contacts: false = normally open (pressed reads LOW), the stage's

// Parked on a switch (pressed, its end not yet known): only a jog moves that axis, at this rate at most, and only this
// far either way from where the switch was found pressed (PROTOCOL.md, Safety behaviour).
const uint16_t PARKED_JOG_RATE = 160;     // counts/s (0.1 mm/s), the Teensy's PARKED_JOG_SPEED_MM
const int32_t  PARKED_TRAVEL   = 800;     // counts (0.5 mm) PROVISIONAL, owner bench check: must exceed the switch's
                                          // release travel from the hard stop (overtravel + differential travel)

// HOME (zeroing on the photo-interrupter), the Teensy axis firmware's values in counts; HOME_DIR and HOME_FLAG_LEVEL
// depend on the flag and the sensor: owner bench check (PROTOCOL.md, HOME)
const int8_t   HOME_DIR            = -1;    // final approach direction (counts)
const uint8_t  HOME_FLAG_LEVEL     = 1;     // home pin level with the flag in the slot (open collector + pull-up: 1)
const uint16_t HOME_SEEK_RATE      = 1600;  // 1.0 mm/s search
const uint16_t HOME_SLOW_RATE      = 160;   // 0.1 mm/s final approach; the ISR halts on the edge itself
const int32_t  HOME_BACKOFF        = 800;   // 0.5 mm: the approach starts this far on the clear side of the coarse edge
const int32_t  HOME_APPROACH_EXTRA = 800;   // 0.5 mm: the approach gives up this far past the coarse edge
const int32_t  HOME_SEEK_MAX       = 88000; // 55 mm: one search leg (the 50 mm travel plus margin)

// Safety
const unsigned long JOG_TIMEOUT_MS   = 250; // jog dead-man (stepper_firmware)
const unsigned long ENABLE_SETTLE_MS = 30;  // motion held this long after 'e' (stepper_firmware)
const unsigned long PRINT_INTERVAL   = 100; // POS line period (stepper_firmware)
const unsigned long FRAME_GAP_MS     = 2;   // a frame line with no byte for this long is parsed as it stands (the old
                                            // readStringUntil timeout)
const unsigned long PACKET_GAP_MS    = 5;   // a jog packet still short of 42 bytes this long after its 0xAA: drop the 0xAA
const unsigned long POLL_ACTIVE_MS   = 50;  // driver status poll per axis while busy
const unsigned long POLL_IDLE_MS     = 250; // ... while idle
const unsigned long PROBE_ABSENT_MS  = 1000;// an axis with no driver is asked again this often (VM switched on late)
const unsigned long TMC_REPLY_MS     = 5;   // a driver read with no reply by then has failed (a reply takes ~1.1 ms)
// ================================================================================

// The standard layer (station_std.h): identity, '#' commands, LOG, HOSTTIMEOUT/HB, INFO, AXISCFG.
#define STD_IDENTITY 'm'
#define STD_FW "xyz_stage_mega"
#define STD_NAXES 3
#define STD_AXES "XYZ"
#define STD_CAP_HOME 1
#define STD_CAP_LIMITS 1
#define STD_CAP_TMC 1
#define STD_EEPROM_ADDR 16
#include "station_std.h"

static_assert(RUN_CURRENT_MA <= MAX_CURRENT_MA, "run current above the clamp");
static_assert(STEP_TICK_US * SENSOR_FILTER_TICKS == 200, "the sensor filter is 200 us");
static_assert(1000000UL / STEP_TICK_US >= 4UL * MAX_RATE, "at least four ticks per step at MAX_RATE");

// ---- fast pin access for the step ISR (ATmega2560 port bits of the pins above) ----
#define X_STEP_HI() (PORTC |= _BV(5))
#define X_STEP_LO() (PORTC &= (uint8_t)~_BV(5))
#define Y_STEP_HI() (PORTA |= _BV(0))
#define Y_STEP_LO() (PORTA &= (uint8_t)~_BV(0))
#define Z_STEP_HI() (PORTL |= _BV(7))      // PORTL is above the sbi range: read-modify-write, so every write to it
#define Z_STEP_LO() (PORTL &= (uint8_t)~_BV(7))   // outside the step ISR runs under IrqGuard (digitalWrite does too); no
                                                  // other ISR touches PORTL

// ---- types (all here, above the first function, for the Arduino prototype generator) ----
// Interrupt guard: nest-safe, and a compiler barrier both ways (cli() clobbers memory; so does the restore).
struct IrqGuard {
  uint8_t s;
  IrqGuard() : s(SREG) { cli(); }
  ~IrqGuard() { __asm__ __volatile__("" ::: "memory"); SREG = s; }
};

// Everything the step ISR owns, one per axis. Not volatile: loop() reads and writes it only under IrqGuard.
struct AxisIsr {
  // motion: a constant rate, bounded (stop at target) or not; a 31-bit phase accumulator, a step when bit 31 sets
  uint32_t inc;          // phase increment per tick = rate x 2^31 / ticks per second
  uint32_t acc;
  int32_t  pos;          // counts
  int32_t  target;       // where a bounded motion stops
  int8_t   dir;          // +1 / -1 while run
  uint8_t  run;          // stepping (or held by g_hold) in dir
  uint8_t  bounded;
  // sensors: filtered pin levels, bit0 LS1, bit1 LS2, bit2 HOME; per-sensor filter counters
  uint8_t  lvl;
  uint8_t  filt;         // a filter counter is running (cnt[] not all 0)
  uint8_t  t;            // tripped switches (from lvl): bit0 LS1, bit1 LS2
  uint8_t  cnt[3];
  uint8_t  homeEdges;    // filtered home edges seen (HOME diagnostic)
  // limit interlock: xyz_stage_axis's stepIsr, per axis (PROTOCOL.md, Safety behaviour)
  uint8_t  limitsOn;     // armed: AXISCFG limits=1, or a switch seen tripping while moving (seen-once)
  int8_t   lsEnd[2];     // the end each switch guards: +1, -1, 0 = not learned
  uint8_t  lsFirm;       // bit i: lsEnd[i] came from a release or a trip from clear while moving
  uint8_t  lsSeen;       // bit i: switch i has read pressed with its end not firm
  uint8_t  lsTravel;     // bit i: the parked travel window taught lsEnd[i] (reported with the halt)
  uint8_t  lsBoth;       // bit i: the window ran out both ways with switch i pressed
  uint8_t  lsReleased;   // bit i: a release taught lsEnd[i] (LOG 2 line)
  uint8_t  lsHit;        // bit i: switch i halted this axis; loop() reports and clears it
  uint8_t  seenNow;      // bit i: switch i armed limits=0 for the session (seen-once); loop() reports and clears it
  int32_t  lsLo[2], lsHi[2];   // switch i parked: its travel window, counts (moves with ZERO and HOME)
  int32_t  lsHitPos[2];
  // HOME trap: one-shot. The first filtered home edge to trapLevel while moving trapDir records its position and,
  // with trapHalt, stops the pulses in the same tick.
  int8_t   trapDir;      // 0 = disarmed
  uint8_t  trapLevel, trapHalt, trapHit;
  int32_t  trapPos;
};

// The Stepper firmware's jog packet: 0xAA, a mode byte, ten floats (station: struct '<BBffffffffff').
struct __attribute__((packed)) ManualControlPacket {
  uint8_t start_marker;       // 0xAA
  uint8_t mode;               // 1 = manual, 0 = stop
  float x_axisStatus, y_axisStatus, z_axisStatus;      // stick, -1..1
  float x_stepSize, y_stepSize, z_stepSize;            // D-pad step sizes, counts
  float dpad_LR, dpad_UD, bumpers;                     // -1, 0, 1
  float manual_jog_speed;                              // counts/s
};

// A TMC2209 whose register writes go to the bus queue below instead of blocking 2 ms each in TMCStepper's write().
// TMCStepper keeps the shadow registers and their encoding; reads are never made through it (tmcService reads).
class MegaTmc : public TMC2209Stepper {
 public:
  explicit MegaTmc(uint8_t addr) : TMC2209Stepper(&Serial2, R_SENSE, addr) {}
 protected:
  void write(uint8_t reg, uint32_t value) override;
  uint32_t read(uint8_t) override { return 0; }
};

enum HomePhase : uint8_t { H_IDLE, H_SEEK, H_BACKOFF, H_APPROACH };
enum LineKind : uint8_t { LK_NONE, LK_FRAME, LK_EXT };
enum TmcReg : uint8_t { R_GCONF = 0x00, R_GSTAT = 0x01, R_IOIN = 0x06, R_IHOLD_IRUN = 0x10, R_TPWMTHRS = 0x13,
                        R_CHOPCONF = 0x6C, R_DRV_STATUS = 0x6F, R_PWMCONF = 0x70 };

// ---- state ----
static AxisIsr g_ax[3];
static volatile uint8_t g_hold = 1;        // ISR: no step while set (boot; the 'e' settle); read once per tick
static volatile uint8_t g_ticks = 0;       // step ISR ticks, wrapping (the watchdog pets only while they advance)
static MegaTmc g_drv[3] = { MegaTmc(0), MegaTmc(1), MegaTmc(2) };
static const uint8_t EN_PIN[3] = { X_EN_PIN, Y_EN_PIN, Z_EN_PIN };
static bool g_pinMapOk = false;

// frame protocol (stepper_firmware's globals, same meanings)
static bool  g_sysEnabled = false;         // system_enabled
static float g_fullSpeed = 400.0f;         // FULL_SPEED
static bool  g_autoOn = true;              // AUTONOMOUS_ON (true at boot, as there)
static bool  g_manualOn = false;           // MANUAL_ON
static bool  g_dpadStep = false;           // DPAD_STEP
static bool  g_allDone = true;             // ALL_AXES_DONE
static float g_man[3] = { 0, 0, 0 };       // manual_x/y/z_value
static int8_t g_dpad[3] = { 0, 0, 0 };     // dpad_LR (X), dpad_UD (Y), bumpers (Z)
static int32_t g_stepSize[3] = { 16, 16, 16 };   // x/y/z_step_size
static uint32_t g_lastPacketMs = 0;
static bool  g_settling = false;
static uint32_t g_settleUntil = 0;
static ManualControlPacket g_pkt;
static uint32_t g_rejectedPackets = 0;
static uint32_t g_lastPrintMs = 0;

// per axis
static uint8_t g_axEn[3];                  // EN pin low: the driver's outputs are on
static bool    g_homed[3];
static uint8_t g_refJog[3];                // a stick jog on this axis was refused: #EVT REFUSED sent, not again until
static uint8_t g_refDpad[3];               // the stick (or that D-pad direction) returns to neutral

// input
static uint8_t g_lineKind = LK_NONE;
static uint32_t g_lineLastMs = 0;
static bool g_aaWaiting = false;
static uint32_t g_aaSinceMs = 0;

// HOME (one axis at a time)
static uint8_t  g_home = H_IDLE, h_axis = 0;
static uint32_t h_start = 0, h_limitMs = 0;
static int8_t   h_dir = 0;
static uint8_t  h_leg = 0, h_ends = 0, h_lsHits = 0, h_edges0 = 0;
static int32_t  h_coarse = 0, h_backoffPos = 0;

// TMC bus: one transaction at a time; register writes coalesce per (axis, register) and go out before any read
static const uint8_t WQ_REGS[6] = { R_GCONF, R_CHOPCONF, R_IHOLD_IRUN, R_PWMCONF, R_TPWMTHRS, R_GSTAT };   // send order
static uint32_t wq_val[3][6];
static uint8_t  wq_dirty[3];
static uint8_t  g_tmc[3];                  // 1: the driver answered (present)
static uint8_t  g_tmcBoot = 0x07;          // bit a: boot detection of axis a not finished
static uint8_t  g_tmcMissingEvt = 0;       // bit a: "#EVT FAULT tmc-missing A" still to send (held until ext1)
static uint8_t  g_tmcFails[3];
static uint8_t  g_tmcPhase[3];             // 0: GSTAT next, 1: DRV_STATUS next
static uint32_t g_tmcNext[3];
static uint8_t  g_tmcVerify = 0;           // bit a: CHOPCONF read-back pending after 'e'
static uint8_t  g_tmcFaultLatch[3];        // bit 0 overtemp, bit 1 short, bit 2 reset: reported once until it clears
static bool     g_enablePending = false;   // 'e' arrived before boot detection finished
static uint8_t  tb_busy = 0, tb_axis = 0, tb_reg = 0, tb_got = 0;
static uint32_t tb_sync = 0, tb_t0 = 0;
static uint8_t  tb_data[5];
static uint8_t  tb_rr = 0;
static int      g_tx2Empty = 0;            // Serial2.availableForWrite() with nothing queued

// watchdog
static uint32_t g_wdtLastMs = 0;
static uint8_t g_wdtLastTicks = 0;

// ================================================================ step ISR
static inline __attribute__((always_inline)) void axisStopIsr(AxisIsr &a) {
  a.run = 0; a.bounded = 0; a.target = a.pos;
}

// Switch i of axis a, one tick: xyz_stage_axis's learning rules (review R-4), unchanged in substance.
static inline __attribute__((always_inline)) void lsTick(AxisIsr &a, const uint8_t i, int8_t dir, uint8_t t,
                                                         uint8_t newTrip, uint8_t newClear, int32_t p, bool &halt) {
  const uint8_t b = (uint8_t)(1u << i);
  if (!(a.lsFirm & b)) {
    if (dir && (newClear & b)) { a.lsEnd[i] = (int8_t)-dir; a.lsFirm |= b; a.lsReleased |= b; }   // left it: guards -dir
    else if (dir && (newTrip & b) && !(a.lsSeen & b) && a.lsEnd[i] == 0) { a.lsEnd[i] = dir; a.lsFirm |= b; }  // from clear
    else if (newTrip & b) {                        // pressed again, end not firm (maybe chatter): stop, learn nothing,
      a.lsLo[i] = p - PARKED_TRAVEL; a.lsHi[i] = p + PARKED_TRAVEL;   // and count the parked travel from here
      if (dir) halt = true;
    } else if ((t & b) && dir && a.limitsOn && (dir > 0 ? p >= a.lsHi[i] : p <= a.lsLo[i])) {   // parked travel spent
      halt = true;
      if (a.lsEnd[i] == 0) { a.lsEnd[i] = dir; a.lsTravel |= b; }  // this way drives into it
      else if (a.lsEnd[i] != dir) a.lsBoth |= b;                   // and the other way did too
    }
    if ((t & b) && !(a.lsFirm & b)) a.lsSeen |= b;
  }
  if ((t & b) && dir && a.lsEnd[i] == dir) halt = true;
}

// Each axis's three sensor pins as bit0 LS1, bit1 LS2, bit2 HOME. On AVR, __builtin_avr_insert_bits moves single bits
// with bst/bld: plain C here is folded into a multi-bit shift, which avr-gcc -Os makes a loop.
#ifdef __AVR__
#define SENSOR_BITS(lsPort, lsMap, homePort, homeMap) \
  __builtin_avr_insert_bits(homeMap, homePort, __builtin_avr_insert_bits(lsMap, lsPort, 0))
#else
#define SENSOR_BITS(lsPort, lsMap, homePort, homeMap) \
  (uint8_t)((((lsPort) >> ((lsMap) & 0xF)) & 1) | ((((lsPort) >> (((lsMap) >> 4) & 0xF)) & 1) << 1) | \
            ((((homePort) >> (((homeMap) >> 8) & 0xF)) & 1) << 2))
#endif
// map nibble n = the source bit for result bit n (0xF: keep): X LS1 PE4, LS2 PE5, HOME PK0; Y PD3, PD2, PK1; Z PD1, PD0, PK2
#define rawX(pe, pk) SENSOR_BITS(pe, 0xFFFFFF54UL, pk, 0xFFFFF0FFUL)
#define rawY(pd, pk) SENSOR_BITS(pd, 0xFFFFFF23UL, pk, 0xFFFFF1FFUL)
#define rawZ(pd, pk) SENSOR_BITS(pd, 0xFFFFFF01UL, pk, 0xFFFFF2FFUL)

// Sensor i of axis a: a level must differ for SENSOR_FILTER_TICKS ticks in a row to count (xyz_stage_axis's filter).
static inline __attribute__((always_inline)) void sensorFilter(AxisIsr &a, uint8_t diff, const uint8_t i, uint8_t &ev) {
  const uint8_t b = (uint8_t)(1u << i);
  if (diff & b) {
    if (++a.cnt[i] >= SENSOR_FILTER_TICKS) { a.cnt[i] = 0; a.lvl ^= b; ev |= b; }
  } else a.cnt[i] = 0;
}

// The rare part of one axis's tick, out of line (it would otherwise make the ISR save every register on every tick): a
// sensor level differs or is still settling, or a switch is pressed while the axis moves. Filters the three sensors,
// springs the HOME trap, runs the interlock. Runs before the axis's step, so a halt decided here stops this tick's pulse.
static void __attribute__((noinline)) axisSlow(AxisIsr &a, uint8_t raw) {
  uint8_t ev = 0;
  const uint8_t diff = raw ^ a.lvl;
  sensorFilter(a, diff, 0, ev);
  sensorFilter(a, diff, 1, ev);
  sensorFilter(a, diff, 2, ev);
  a.filt = a.cnt[0] | a.cnt[1] | a.cnt[2];
  int8_t dir = a.run ? a.dir : 0;                   // 0: at rest (nothing to stop, no direction to learn from)
  if (ev & 4) {
    a.homeEdges++;
    if (a.trapDir && a.trapDir == dir && ((a.lvl >> 2) & 1) == a.trapLevel) {
      a.trapPos = a.pos; a.trapHit = 1; a.trapDir = 0;
      if (a.trapHalt) { axisStopIsr(a); dir = 0; }  // no pulse after the edge
    }
  }
  const uint8_t t = LIMITS_NC ? (uint8_t)(a.lvl & 3) : (uint8_t)(~a.lvl & 3);   // tripped switches
  a.t = t;
  const uint8_t evLs = ev & 3;
  if (!(t | evLs)) return;
  const uint8_t newTrip = evLs & t, newClear = evLs & (uint8_t)~t;
  if (!a.limitsOn && dir && newTrip) { a.limitsOn = 1; a.seenNow |= newTrip; }   // seen-once: present this session
  const int32_t p = a.pos;
  bool halt = dir && t == 3;                        // both at once: a wiring fault or the wrong contact type
  lsTick(a, 0, dir, t, newTrip, newClear, p, halt);
  lsTick(a, 1, dir, t, newTrip, newClear, p, halt);
  if (halt && a.limitsOn) {
    axisStopIsr(a);
    a.lsHit |= t;
    if (t & 1) a.lsHitPos[0] = p;
    if (t & 2) a.lsHitPos[1] = p;
  }
}

// One axis, one tick. Most ticks only test three bytes and, when the axis runs, add to its phase.
static inline __attribute__((always_inline)) void axisTick(AxisIsr &a, uint8_t raw, const uint8_t A, uint8_t hold) {
  if (raw != a.lvl || a.filt || (a.t && a.run)) axisSlow(a, raw);
  if (a.run && !hold) {
    a.acc += a.inc;
    if (a.acc & 0x80000000UL) {
      a.acc &= 0x7FFFFFFFUL;
      if (A == 0) X_STEP_HI(); else if (A == 1) Y_STEP_HI(); else Z_STEP_HI();
      if (a.dir > 0) a.pos++; else a.pos--;
      if (a.bounded && a.pos == a.target) axisStopIsr(a);
    }
  }
}

// Timer1 compare A, every STEP_TICK_US. No Serial, no UART read, no allocation, no float. It masks itself and lets
// the other interrupts in: the host UART at 500000 baud has two bytes (40 us) of slack, and this ISR's rare worst case
// (every sensor of every axis changing in one tick) is longer than that. A tick that overruns makes the next one late.
ISR(TIMER1_COMPA_vect) {
  TIMSK1 &= (uint8_t)~_BV(OCIE1A);
  sei();
  const uint8_t pe = PINE, pd = PIND, pk = PINK, hold = g_hold;
  axisTick(g_ax[0], rawX(pe, pk), 0, hold);
  axisTick(g_ax[1], rawY(pd, pk), 1, hold);
  axisTick(g_ax[2], rawZ(pd, pk), 2, hold);
  X_STEP_LO(); Y_STEP_LO(); Z_STEP_LO();           // the pulse is the ISR's length, far above the TMC2209's 100 ns
  g_ticks++;
  cli();
  TIMSK1 |= _BV(OCIE1A);
}

// ================================================================ motion, loop() side
static uint32_t rateToInc(float rate) {          // counts/s -> phase increment per tick
  if (rate > MAX_RATE) rate = MAX_RATE;
  return (uint32_t)(rate * (2147483648.0f / (1000000.0f / STEP_TICK_US)) + 0.5f);
}
static void setDirPin(uint8_t a, int8_t d) {     // + is DIR high (AccelStepper's DRIVER convention); under IrqGuard
  if (a == 0) { if (d > 0) PORTC |= _BV(4); else PORTC &= (uint8_t)~_BV(4); }
  else if (a == 1) { if (d > 0) PORTA |= _BV(1); else PORTA &= (uint8_t)~_BV(1); }
  else { if (d > 0) PORTL |= _BV(6); else PORTL &= (uint8_t)~_BV(6); }
}
// Run axis a in direction d at rate counts/s; bounded: stop at target. From rest the first step is due on the next
// tick (AccelStepper's runSpeed() also steps at once) and every later one a whole period after it; a running axis keeps
// its phase.
static void axRun(uint8_t a, int8_t d, float rate, bool bounded, int32_t target) {
  uint32_t inc = rateToInc(rate);
  IrqGuard g;
  AxisIsr &x = g_ax[a];
  if (!inc || (bounded && target == x.pos)) { axisStopIsr(x); return; }
  if (!x.run) x.acc = 0x80000000UL - inc;
  if (!x.run || x.dir != d) setDirPin(a, d);
  x.dir = d; x.inc = inc; x.bounded = bounded; x.target = target; x.run = 1;
}
static void axStop(uint8_t a) { IrqGuard g; axisStopIsr(g_ax[a]); }
static void stopAll() { IrqGuard g; for (uint8_t a = 0; a < 3; a++) axisStopIsr(g_ax[a]); }
static bool axMoving(uint8_t a) { IrqGuard g; return g_ax[a].run; }
static bool anyMoving() { IrqGuard g; return g_ax[0].run || g_ax[1].run || g_ax[2].run; }
static int32_t axPos(uint8_t a) { IrqGuard g; return g_ax[a].pos; }
static void axVel(uint8_t a, int8_t &d, uint32_t &inc, bool &bounded) {
  IrqGuard g; d = g_ax[a].run ? g_ax[a].dir : 0; inc = g_ax[a].inc; bounded = g_ax[a].bounded;
}
static void axSetPos(uint8_t a, int32_t p) {     // a new origin (ZERO, HOME): the parked windows keep their carriage positions
  IrqGuard g;
  AxisIsr &x = g_ax[a];
  int32_t d = p - x.pos;
  x.pos = p; x.target += d;
  for (uint8_t i = 0; i < 2; i++) { x.lsLo[i] += d; x.lsHi[i] += d; }
}
static uint8_t trippedBits(uint8_t lvl) { return LIMITS_NC ? (uint8_t)(lvl & 3) : (uint8_t)(~lvl & 3); }
static bool limitsOn(uint8_t a) { IrqGuard g; return g_ax[a].limitsOn; }
// Parked switches of axis a (bits): pressed with the end not confirmed, interlock armed.
static uint8_t parkedBits(uint8_t a) {
  IrqGuard g;
  const AxisIsr &x = g_ax[a];
  return x.limitsOn ? (uint8_t)(trippedBits(x.lvl) & (uint8_t)~x.lsFirm) : 0;
}
static uint8_t homeLevel(uint8_t a) { IrqGuard g; return (g_ax[a].lvl >> 2) & 1; }

// Why motion of axis a toward dir is refused, or nullptr (xyz_stage_axis's limitBlock). Only a jog may move while a
// switch is parked. With the interlock disarmed (limits=0) nothing is refused: the old behaviour.
static PGM_P limitBlock(uint8_t a, int8_t dir, bool jog) {
  uint8_t on, lvl, firm; int8_t end[2]; int32_t p, lo[2], hi[2];
  {
    IrqGuard g;
    const AxisIsr &x = g_ax[a];
    on = x.limitsOn; lvl = x.lvl; firm = x.lsFirm; p = x.pos;
    for (uint8_t i = 0; i < 2; i++) { end[i] = x.lsEnd[i]; lo[i] = x.lsLo[i]; hi[i] = x.lsHi[i]; }
  }
  if (!on) return nullptr;
  uint8_t t = trippedBits(lvl);
  if (t == 3) return PSTR("limits-both-tripped:check-wiring");
  for (uint8_t i = 0; i < 2; i++) {
    if (!(t & (1u << i))) continue;
    if (end[i] == dir) return i ? PSTR("limit-ls2") : PSTR("limit-ls1");
    if (firm & (1u << i)) continue;
    if (!jog) return i ? PSTR("limit-ls2-end-unknown:jog-off-it") : PSTR("limit-ls1-end-unknown:jog-off-it");
    if (dir > 0 ? p >= hi[i] : p <= lo[i])
      return i ? PSTR("limit-ls2-pressed-both-ways:check-switch") : PSTR("limit-ls1-pressed-both-ways:check-switch");
  }
  return nullptr;
}

// ================================================================ events and stops
static void homeFail(PGM_P reason);
static void homedLost(uint8_t a) { g_homed[a] = false; }

// #EVT REFUSED for a frame or jog the frame protocol cannot answer. Jog packets repeat 50 times a second, so a jog's
// refusal goes out once (latch) until that input returns to neutral; a frame's goes out every time.
static void refused(uint8_t a, PGM_P why, uint8_t *latch) {
  if (latch) { if (*latch) return; *latch = 1; }
  stdEvt(0, PSTR("REFUSED %c reason=%s"), stdAxisLetter(a), stdP(why));
}

// The Stepper firmware's haltMotion(): every mode off, every axis stopped where it is, coils holding; and a HOME ends.
static void haltMotion(PGM_P why) {
  g_manualOn = false; g_autoOn = false; g_dpadStep = false; g_allDone = true;
  for (uint8_t a = 0; a < 3; a++) { g_man[a] = 0; g_dpad[a] = 0; }
  stopAll();
  homeFail(why);
}

// Outputs off on every axis (EN high first, then TOFF=0 on each driver present): 'd', a driver fault, the host-timeout
// disable, a dead host UART. homed is lost on an axis that was turning (it coasts unpowered).
static void outputsOff(PGM_P why) {
  bool moving[3];
  { IrqGuard g; for (uint8_t a = 0; a < 3; a++) moving[a] = g_ax[a].run && !g_hold; }
  haltMotion(why);
  for (uint8_t a = 0; a < 3; a++) { digitalWrite(EN_PIN[a], HIGH); g_axEn[a] = 0; if (moving[a]) homedLost(a); }
  for (uint8_t a = 0; a < 3; a++) if (g_tmc[a]) g_drv[a].toff(0);
  if (g_sysEnabled) stdDbg(PSTR("driver off reason=%s"), stdP(why));
  g_sysEnabled = false;
  g_settling = false;
  g_tmcVerify = 0;
  g_enablePending = false;
}

// A driver fault read over the bus (MEGA_STANDARD §4): stop all axes, outputs off, #EVT FAULT <what> A.
static void driverFault(uint8_t a, PGM_P what, const char *extra) {
  stdEvt(0, PSTR("FAULT %s %c%s"), stdP(what), stdAxisLetter(a), extra);
  outputsOff(what);
  homedLost(a);
}

// ================================================================ TMC bus (Serial2)
static uint8_t tmcCrc(const uint8_t *d, uint8_t n) {   // the TMC2209 datagram CRC8 (TMCStepper's calcCRC)
  uint8_t crc = 0;
  for (uint8_t i = 0; i < n; i++) {
    uint8_t c = d[i];
    for (uint8_t j = 0; j < 8; j++) {
      crc = ((crc >> 7) ^ (c & 1)) ? (uint8_t)((crc << 1) ^ 0x07) : (uint8_t)(crc << 1);
      c >>= 1;
    }
  }
  return crc;
}
void MegaTmc::write(uint8_t reg, uint32_t value) {     // TMCStepper's setters land here: queue, coalesce, never block
  uint8_t a = slave_address < 3 ? slave_address : 0;
  for (uint8_t k = 0; k < 6; k++)
    if (WQ_REGS[k] == reg) { wq_val[a][k] = value; wq_dirty[a] |= (uint8_t)(1u << k); return; }
}
// The whole configuration (the Teensy axis firmware's configureDriver, as stepper_firmware sets it up); TOFF=0 until 'e'.
static void tmcConfigure(uint8_t a) {
  MegaTmc &d = g_drv[a];
  d.toff(0);
  d.begin();                                   // pdn_disable, mstep_reg_select: microsteps from the register, UART on PDN
  d.I_scale_analog(false);                     // current from UART, not VREF
  d.TPWMTHRS(0);
  d.microsteps(MICROSTEPS);
  d.rms_current(RUN_CURRENT_MA, HOLD_MULTIPLIER);
  d.intpol(true);
  d.en_spreadCycle(USE_SPREADCYCLE);
  d.pwm_autoscale(!USE_SPREADCYCLE);
  d.GSTAT(0b111);                              // clear the power-up latch, so a later reset bit is a real reset
}
static void tmcSendWrite(uint8_t a, uint8_t k) {
  uint32_t v = wq_val[a][k];
  uint8_t d[8] = { 0x05, a, (uint8_t)(WQ_REGS[k] | 0x80), (uint8_t)(v >> 24), (uint8_t)(v >> 16), (uint8_t)(v >> 8), (uint8_t)v, 0 };
  d[7] = tmcCrc(d, 7);
  Serial2.write(d, 8);
  wq_dirty[a] &= (uint8_t)~(1u << k);
}
static void tmcResult(uint8_t a, uint8_t reg, bool ok, uint32_t v);
// Which read is due next: boot detection, the post-'e' read-back, then the status polls; -1 for none.
static int8_t tmcNextRead(uint32_t now, uint8_t &reg) {
  for (uint8_t k = 0; k < 3; k++) {
    uint8_t a = (uint8_t)((tb_rr + k) % 3);
    if (g_tmcBoot & (1u << a)) { reg = R_IOIN; return (int8_t)a; }
    if (g_tmcVerify & (1u << a)) { reg = R_CHOPCONF; return (int8_t)a; }
  }
  for (uint8_t k = 0; k < 3; k++) {
    uint8_t a = (uint8_t)((tb_rr + k) % 3);
    if (g_tmc[a] && g_tmcPhase[a]) { reg = R_DRV_STATUS; return (int8_t)a; }
    if ((int32_t)(now - g_tmcNext[a]) >= 0) { reg = g_tmc[a] ? R_GSTAT : R_IOIN; return (int8_t)a; }
  }
  return -1;
}
static void tmcService(uint32_t now) {
  if (tb_busy) {
    while (Serial2.available() > 0) {
      uint8_t c = (uint8_t)Serial2.read();
      if (tb_got == 0xFF) {                    // our own echo first, then 05 FF <reg>: the reply's header
        tb_sync = ((tb_sync << 8) | c) & 0xFFFFFFUL;
        if (tb_sync == (0x05FF00UL | tb_reg)) tb_got = 0;
        continue;
      }
      tb_data[tb_got++] = c;
      if (tb_got == 5) {
        uint8_t d[7] = { 0x05, 0xFF, tb_reg, tb_data[0], tb_data[1], tb_data[2], tb_data[3] };
        uint32_t v = ((uint32_t)tb_data[0] << 24) | ((uint32_t)tb_data[1] << 16) | ((uint32_t)tb_data[2] << 8) | tb_data[3];
        tb_busy = 0;
        tmcResult(tb_axis, tb_reg, tmcCrc(d, 7) == tb_data[4], v);
        return;
      }
    }
    if (now - tb_t0 > TMC_REPLY_MS) { tb_busy = 0; tmcResult(tb_axis, tb_reg, false, 0); }
    return;
  }
  for (uint8_t a = 0; a < 3; a++)               // queued writes first, while the TX buffer has room
    for (uint8_t k = 0; k < 6; k++)
      if (wq_dirty[a] & (1u << k)) { if (Serial2.availableForWrite() < 8) return; tmcSendWrite(a, k); }
  if (Serial2.availableForWrite() < g_tx2Empty) return;   // a read starts only on a quiet line
  uint8_t reg;
  int8_t a = tmcNextRead(now, reg);
  if (a < 0) return;
  tb_rr = (uint8_t)((a + 1) % 3);
  while (Serial2.available() > 0) Serial2.read();          // drop the echoes of the writes
  uint8_t d[4] = { 0x05, (uint8_t)a, reg, 0 };
  d[3] = tmcCrc(d, 3);
  Serial2.write(d, 4);
  tb_busy = 1; tb_axis = (uint8_t)a; tb_reg = reg; tb_got = 0xFF; tb_sync = 0; tb_t0 = now;
}

static void tmcBootResolved(uint8_t a) {
  g_tmcBoot &= (uint8_t)~(1u << a);
  g_tmcNext[a] = millis() + (g_tmc[a] ? POLL_IDLE_MS : PROBE_ABSENT_MS);
}
static void tmcResult(uint8_t a, uint8_t reg, bool ok, uint32_t v) {
  uint32_t now = millis();
  if (reg == R_IOIN) {
    bool found = ok && (v >> 24) == 0x21;     // TMC2209 VERSION
    bool boot = g_tmcBoot & (1u << a);
    if (found) {
      g_tmc[a] = 1; g_tmcFails[a] = 0; g_tmcPhase[a] = 0;
      tmcConfigure(a);
      if (!boot) stdEvt(1, PSTR("TMC %c detected version=0x21"), stdAxisLetter(a));
      stdDbg(PSTR("driver configured %c microsteps=%u current_ma=%u"), stdAxisLetter(a), MICROSTEPS, RUN_CURRENT_MA);
    } else if (boot) {                         // MEGA_STANDARD §4: tmc=0, said at boot, never enabled
      if (std_ext) stdEvt(0, PSTR("FAULT tmc-missing %c"), stdAxisLetter(a));
      else g_tmcMissingEvt |= (uint8_t)(1u << a);
    }
    if (boot) tmcBootResolved(a); else g_tmcNext[a] = now + (found ? POLL_IDLE_MS : PROBE_ABSENT_MS);
    return;
  }
  if (!ok) {                                   // no reply, or a bad CRC
    if (reg == R_CHOPCONF && (g_tmcVerify & (1u << a))) {
      if (++g_tmcFails[a] >= 3) { g_tmcFails[a] = 0; driverFault(a, PSTR("uart-lost"), ""); }
      return;
    }
    g_tmcPhase[a] = 0; g_tmcNext[a] = now + (g_sysEnabled ? POLL_ACTIVE_MS : POLL_IDLE_MS);
    if (++g_tmcFails[a] >= 3) {
      g_tmcFails[a] = 0;
      if (g_sysEnabled) driverFault(a, PSTR("uart-lost"), "");   // reported while enabled (xyz_stage_axis)
    }
    return;
  }
  g_tmcFails[a] = 0;
  if (reg == R_CHOPCONF) {                     // after 'e': TOFF and the microstep resolution as written
    g_tmcVerify &= (uint8_t)~(1u << a);
    uint8_t toff = v & 0x0F, mres = (v >> 24) & 0x0F;
    if (toff != 4 || mres != 5) {
      char x[24]; snprintf_P(x, sizeof x, PSTR(" toff=%u mres=%u"), toff, mres);
      driverFault(a, PSTR("driver-readback-mismatch"), x);
    }
    return;
  }
  if (reg == R_GSTAT) {
    g_tmcPhase[a] = 1;
    if (v & 1) {                               // reset: the driver lost VM and is back on its pin/OTP defaults
      tmcConfigure(a);                         // (a reset that will not clear, a driver ignoring writes, is said once)
      if (!(g_tmcFaultLatch[a] & 4)) { g_tmcFaultLatch[a] |= 4; driverFault(a, PSTR("driver-reset"), " reconfigured"); }
    } else g_tmcFaultLatch[a] &= (uint8_t)~4;
    return;
  }
  if (reg == R_DRV_STATUS) {
    g_tmcPhase[a] = 0;
    g_tmcNext[a] = now + ((skBusy() || g_sysEnabled) ? POLL_ACTIVE_MS : POLL_IDLE_MS);
    const bool ot = v & 0x3, sh = v & 0x3C;    // ot | otpw; s2ga s2gb s2vsa s2vsb
    uint8_t was = g_tmcFaultLatch[a];
    g_tmcFaultLatch[a] = (uint8_t)((was & 4) | (ot ? 1 : 0) | (sh ? 2 : 0));
    if (ot && !(was & 1)) {
      char x[16]; snprintf_P(x, sizeof x, PSTR(" ot=%u otpw=%u"), (unsigned)((v >> 1) & 1), (unsigned)(v & 1));
      driverFault(a, PSTR("overtemp"), x);
    } else if (sh && !(was & 2)) {
      char x[24]; snprintf_P(x, sizeof x, PSTR(" s2g=%u s2vs=%u"), (unsigned)((v >> 2) & 3), (unsigned)((v >> 4) & 3));
      driverFault(a, PSTR("short"), x);
    }
  }
}

// ================================================================ HOME (xyz_stage_axis's sequence, per axis, in counts)
// d = HOME_DIR. Reference edge R: where the filtered home level changes TO HOME_FLAG_LEVEL moving d (moving -d, where it
// leaves it). seek: search at HOME_SEEK_RATE toward R; the ISR halts on R (one-shot trap). A switch first reverses the
// search once; a second end means there is no R. backoff: to HOME_BACKOFF on the clear side of R. approach: d at
// HOME_SLOW_RATE from rest; the ISR halts on R itself, and that position becomes 0. The interlock stays live
// throughout; every stop path ends the sequence through homeFail(). No ramps: the frame protocol's motion has none.
static PGM_P homePhaseName(uint8_t ph) {
  return ph == H_SEEK ? PSTR("seek") : ph == H_BACKOFF ? PSTR("backoff") : ph == H_APPROACH ? PSTR("approach") : PSTR("none");
}
static void trapArm(uint8_t a, int8_t dir, uint8_t level) {
  IrqGuard g; AxisIsr &x = g_ax[a]; x.trapHit = 0; x.trapLevel = level; x.trapHalt = 1; x.trapDir = dir;
}
static void trapDisarm(uint8_t a) { IrqGuard g; g_ax[a].trapDir = 0; g_ax[a].trapHit = 0; }
static uint8_t homeEdges(uint8_t a) { IrqGuard g; return g_ax[a].homeEdges; }
static void homeFail(PGM_P reason) {
  if (g_home == H_IDLE) return;
  uint8_t ph = g_home, a = h_axis;
  g_home = H_IDLE;
  trapDisarm(a);
  axStop(a);
  stdEvt(0, PSTR("HOME FAIL %c reason=%s phase=%s pos=%ld legs=%u ends=%u edges=%u elapsed_ms=%lu"), stdAxisLetter(a),
         stdP(reason), stdP(homePhaseName(ph)), (long)axPos(a), h_leg, h_ends, (uint8_t)(homeEdges(a) - h_edges0),
         (unsigned long)(millis() - h_start));
}
// Exactly one switch is tripped and it guards the end that dir moves toward.
static bool endGuarded(uint8_t a, int8_t dir) {
  uint8_t t; int8_t e0, e1;
  { IrqGuard g; t = trippedBits(g_ax[a].lvl); e0 = g_ax[a].lsEnd[0]; e1 = g_ax[a].lsEnd[1]; }
  if (t == 0 || t == 3) return false;
  return (t == 1 ? e0 : e1) == dir;
}
static void homeSeek(int8_t s, PGM_P after) {
  uint8_t a = h_axis;
  PGM_P blk = limitBlock(a, s, false);
  if (blk && endGuarded(a, s)) {               // starting against the switch at that end counts as reaching it
    if (++h_ends >= 2) { homeFail(PSTR("no-edge-between-limits")); return; }
    s = (int8_t)-s; after = PSTR("at-limit");
    blk = limitBlock(a, s, false);
  }
  if (blk) { homeFail(blk); return; }
  h_dir = s; h_leg++;
  trapArm(a, s, s == HOME_DIR ? HOME_FLAG_LEVEL : (uint8_t)!HOME_FLAG_LEVEL);
  axRun(a, s, HOME_SEEK_RATE, true, axPos(a) + (int32_t)s * HOME_SEEK_MAX);
  g_home = H_SEEK;
  stdEvt(1, PSTR("HOME %c phase=seek dir=%s leg=%u speed=%u max=%ld after=%s limit_s=%lu"), stdAxisLetter(a), stdEnd(s),
         h_leg, HOME_SEEK_RATE, (long)HOME_SEEK_MAX, stdP(after), (unsigned long)(h_limitMs / 1000));
}
static void homeStart(uint8_t a) {
  g_manualOn = false; g_autoOn = false; g_dpadStep = false; g_allDone = true;
  h_axis = a; h_start = millis(); h_leg = 0; h_ends = 0; h_lsHits = 0; h_edges0 = homeEdges(a);
  bool wasHomed = g_homed[a];
  homedLost(a);                                // a re-home in progress: the old zero no longer counts, even if it fails
  // Time limit: 1.5 x the worst-case plan (two full legs, backoff, approach) + 10 s, at least 120 s (xyz_stage_axis).
  float sec = 2.0f * HOME_SEEK_MAX / HOME_SEEK_RATE + (HOME_BACKOFF + 3200.0f) / HOME_SEEK_RATE +
              (float)(HOME_BACKOFF + HOME_APPROACH_EXTRA) / HOME_SLOW_RATE;
  float ms = 1500.0f * sec + 10000.0f;
  h_limitMs = ms > 120000.0f ? (uint32_t)ms : 120000UL;
  g_home = H_SEEK;
  if (homeLevel(a) == HOME_FLAG_LEVEL) homeSeek((int8_t)-HOME_DIR, PSTR("start-on-flag"));
  else if (wasHomed && axPos(a) * HOME_DIR > 0) homeSeek((int8_t)-HOME_DIR, PSTR("start-beyond-old-zero"));
  else homeSeek(HOME_DIR, PSTR("start"));
}
static void homeDone(int32_t edge) {
  uint8_t a = h_axis;
  trapDisarm(a);
  int32_t p = axPos(a);
  axSetPos(a, p - edge);                       // the ISR halted on the edge, so p == edge: the carriage sits at 0
  g_home = H_IDLE;
  g_homed[a] = true;
  stdEvt(1, PSTR("HOME %c phase=edge edge=%ld coarse=%ld legs=%u ends=%u elapsed_ms=%lu"), stdAxisLetter(a), (long)edge,
         (long)h_coarse, h_leg, h_ends, (unsigned long)(millis() - h_start));
  stdEvt(0, PSTR("HOMED %c edge=%ld pos=%ld"), stdAxisLetter(a), (long)edge, (long)axPos(a));
}
static void homeStep(uint32_t now) {
  if (g_home == H_IDLE) return;
  uint8_t a = h_axis;
  if (now - h_start > h_limitMs) { homeFail(PSTR("time-limit")); return; }
  bool hit, run, lsPending; int32_t tp;
  { IrqGuard g; hit = g_ax[a].trapHit; tp = g_ax[a].trapPos; run = g_ax[a].run; lsPending = g_ax[a].lsHit != 0; }
  if (lsPending) return;                       // a limit just halted it: serviceSensors() hands it over first
  uint8_t pk = parkedBits(a);
  if (pk) { homeFail(pk & 1 ? PSTR("limit-ls1-end-unknown:jog-off-it") : PSTR("limit-ls2-end-unknown:jog-off-it")); return; }
  uint8_t ls = h_lsHits;
  h_lsHits = 0;
  switch (g_home) {
    case H_SEEK:
      if (hit) {
        { IrqGuard g; g_ax[a].trapHit = 0; }
        h_coarse = tp;
        h_backoffPos = h_coarse - (int32_t)HOME_DIR * HOME_BACKOFF;
        int32_t togo = h_backoffPos - axPos(a);
        if (togo != 0) {
          if (limitBlock(a, togo > 0 ? 1 : -1, false)) { homeFail(PSTR("edge-too-close-to-limit")); return; }
          axRun(a, togo > 0 ? 1 : -1, HOME_SEEK_RATE, true, h_backoffPos);
        }
        g_home = H_BACKOFF;
        stdEvt(1, PSTR("HOME %c phase=backoff edge=%ld to=%ld"), stdAxisLetter(a), (long)h_coarse, (long)h_backoffPos);
      } else if (ls) {
        if (++h_ends >= 2) { homeFail(PSTR("no-edge-between-limits")); return; }
        homeSeek((int8_t)-h_dir, PSTR("limit"));
      } else if (!run) homeFail(PSTR("no-edge-within-search"));   // a whole leg without an edge or a switch
      break;
    case H_BACKOFF:
      if (ls) { homeFail(PSTR("limit-during-backoff")); return; }
      if (run) break;
      if (homeLevel(a) == HOME_FLAG_LEVEL) { homeFail(PSTR("flag-at-backoff")); return; }
      if (limitBlock(a, HOME_DIR, false)) { homeFail(PSTR("limit-before-approach")); return; }
      trapArm(a, HOME_DIR, HOME_FLAG_LEVEL);
      axRun(a, HOME_DIR, HOME_SLOW_RATE, true, h_backoffPos + (int32_t)HOME_DIR * (HOME_BACKOFF + HOME_APPROACH_EXTRA));
      g_home = H_APPROACH;
      stdEvt(1, PSTR("HOME %c phase=approach dir=%s speed=%u max=%ld"), stdAxisLetter(a), stdEnd(HOME_DIR), HOME_SLOW_RATE,
             (long)(HOME_BACKOFF + HOME_APPROACH_EXTRA));
      break;
    case H_APPROACH:
      if (hit && !run) { homeDone(tp); return; }
      if (hit) break;
      if (ls) { homeFail(PSTR("limit-during-approach")); return; }
      if (!run) homeFail(PSTR("edge-lost"));   // the whole approach without the edge
      break;
    default: break;
  }
}

// ================================================================ the frame protocol (stepper_firmware)
static bool fieldIsStep(float v) { return isfinite(v) && v >= 0.0f && v <= 100000.0f && v == (float)(long)v; }
static bool fieldIsUnit(float v) { return isfinite(v) && v >= -1.0001f && v <= 1.0001f; }
static bool fieldIsTri(float v) { return v == -1.0f || v == 0.0f || v == 1.0f; }
static bool packetIsValid() {
  const ManualControlPacket &p = g_pkt;
  return fieldIsUnit(p.x_axisStatus) && fieldIsUnit(p.y_axisStatus) && fieldIsUnit(p.z_axisStatus)
      && fieldIsStep(p.x_stepSize) && fieldIsStep(p.y_stepSize) && fieldIsStep(p.z_stepSize)
      && p.x_stepSize >= 1.0f && p.y_stepSize >= 1.0f && p.z_stepSize >= 1.0f
      && fieldIsTri(p.dpad_LR) && fieldIsTri(p.dpad_UD) && fieldIsTri(p.bumpers)
      && isfinite(p.manual_jog_speed) && p.manual_jog_speed >= 0.0f && p.manual_jog_speed <= MAX_JOG_SPEED;
}
static bool stickNeutral() {
  return sqrt(pow(g_man[0], 2) + pow(g_man[1], 2) + pow(g_man[2], 2)) <= MANUAL_DEAD_ZONE;
}
static bool homeActive() { return g_home != H_IDLE; }

// Engage manual mode (MANUAL_ON) the way the Stepper firmware does: autonomous ends, its axes stop where they are.
static void engageManual() {
  g_autoOn = false; g_manualOn = true; g_allDone = true;
  stopAll();
  stdDbg(PSTR("manual engaged"));
}

static void handlePacket(uint32_t now) {
  if (g_pkt.mode == 1 && !packetIsValid()) { g_pkt.mode = 255; g_rejectedPackets++; }   // a shifted packet: ignored
  if (g_pkt.mode == 0 || g_pkt.mode == 1) g_lastPacketMs = now;
  if (g_pkt.mode == 1) {
    if (homeActive()) {                        // HOME owns the motion; a gamepad at neutral must not end it
      bool neutral = fabs(g_pkt.x_axisStatus) + fabs(g_pkt.y_axisStatus) + fabs(g_pkt.z_axisStatus) <= MANUAL_DEAD_ZONE
                     && g_pkt.dpad_LR == 0 && g_pkt.dpad_UD == 0 && g_pkt.bumpers == 0;
      if (!neutral) refused(h_axis, PSTR("busy"), &g_refJog[h_axis]); else g_refJog[h_axis] = 0;
      return;
    }
    if (!g_manualOn && !g_dpadStep) engageManual();
    g_fullSpeed = g_pkt.manual_jog_speed;
    g_man[0] = -1 * g_pkt.x_axisStatus;        // the stick's X is inverted (stepper_firmware)
    g_man[1] = g_pkt.y_axisStatus;
    g_man[2] = g_pkt.z_axisStatus;
    g_dpad[0] = (int8_t)g_pkt.dpad_LR; g_dpad[1] = (int8_t)g_pkt.dpad_UD; g_dpad[2] = (int8_t)g_pkt.bumpers;
    g_stepSize[0] = (int32_t)g_pkt.x_stepSize; g_stepSize[1] = (int32_t)g_pkt.y_stepSize; g_stepSize[2] = (int32_t)g_pkt.z_stepSize;
  } else if (g_pkt.mode == 0) haltMotion(PSTR("stop"));   // explicit stop: unconditional
}

// An autonomous frame: "xs,ys,zs,0,full,slow,brake,xd,yd,zd,manual,auto". The fields mean what they mean to the Stepper
// firmware, signs included: X and Z distances are negated, speeds are split by distance (not counts) and truncated.
static void handleFrame(char *line) {
  char *s = line;                              // String::trim(): leading and trailing whitespace
  while (*s && isspace((unsigned char)*s)) s++;
  size_t n = strlen(s);
  while (n && isspace((unsigned char)s[n - 1])) s[--n] = 0;
  if (!n) return;
  char *f[12];
  uint8_t k = 0;
  f[k++] = s;
  for (char *p = s; *p && k < 12; p++) if (*p == ',') { *p = 0; f[k++] = p + 1; }
  for (char *p = f[k - 1]; *p; p++) if (*p == ',') { *p = 0; break; }   // getValue(11) stops at the next comma
  static char empty[1] = "";
  while (k < 12) f[k++] = empty;
  bool newManual = atol(f[10]) == 1, newAuto = atol(f[11]) == 1;
  if (newManual) {                             // manual mode by frame: speeds from field 4, sticks from fields 0-2
    if (homeActive()) { refused(h_axis, PSTR("busy"), nullptr); return; }
    g_manualOn = true; g_autoOn = false; g_dpadStep = false;
    stopAll();
    g_fullSpeed = (float)atof(f[4]);
    for (uint8_t a = 0; a < 3; a++) g_man[a] = (float)atof(f[a]);
    stdDbg(PSTR("frame manual"));
    return;
  }
  if (!newAuto) { haltMotion(PSTR("stop")); return; }   // neither mode: a stop
  long size[3], dist[3], steps[3], speed[3];
  for (uint8_t a = 0; a < 3; a++) size[a] = atol(f[a]);
  float fullSpeed = (float)atof(f[4]);
  dist[0] = -1 * atol(f[7]); dist[1] = atol(f[8]); dist[2] = -1 * atol(f[9]);
  double direction_size = sqrt(pow(dist[0], 2) + pow(dist[1], 2) + pow(dist[2], 2));   // as stepper_firmware computes it
  for (uint8_t a = 0; a < 3; a++) {
    steps[a] = size[a] * dist[a];              // long: the Stepper firmware's int wraps above 32767 counts on AVR
    speed[a] = direction_size > 0 ? (long)((fullSpeed * dist[a]) / direction_size) : 0;
  }
  // Refused as a whole if any axis it would move may not: a vector move with one axis missing leaves the line.
  bool ok = true;
  for (uint8_t a = 0; a < 3; a++) {
    if (!steps[a] || !speed[a]) continue;
    PGM_P why = homeActive() ? PSTR("busy") : !g_axEn[a] ? PSTR("not-enabled") : limitBlock(a, steps[a] > 0 ? 1 : -1, false);
    if (why) { refused(a, why, nullptr); ok = false; }
  }
  g_fullSpeed = fullSpeed;
  if (homeActive()) return;                    // a frame never ends a HOME (a stop frame does, above)
  g_autoOn = true; g_manualOn = false; g_dpadStep = false;
  for (uint8_t a = 0; a < 3; a++) g_man[a] = 0;
  g_allDone = true;
  for (uint8_t a = 0; a < 3; a++) {
    if (!ok || !steps[a] || !speed[a]) { axStop(a); continue; }
    axRun(a, steps[a] > 0 ? 1 : -1, (float)labs(speed[a]), true, axPos(a) + steps[a]);
    g_allDone = false;
  }
  stdDbg(PSTR("frame auto steps=%ld,%ld,%ld speed=%ld,%ld,%ld%s"), steps[0], steps[1], steps[2], labs(speed[0]),
         labs(speed[1]), labs(speed[2]), ok ? "" : " refused");
}

static void cmdEnable(uint32_t now) {
  if (g_sysEnabled) return;                    // idempotent, as there
  if (g_tmcBoot) { g_enablePending = true; return; }   // drivers still being detected: enable once they are
  g_sysEnabled = true;
  stopAll();                                   // speeds to 0 before power-up
  g_hold = 1; g_settling = true; g_settleUntil = now + ENABLE_SETTLE_MS;
  for (uint8_t a = 0; a < 3; a++) {
    g_tmcFaultLatch[a] = 0; g_refJog[a] = 0; g_refDpad[a] = 0;
    if (!g_tmc[a] || !g_pinMapOk) continue;    // tmc=0: that axis is never enabled
    g_drv[a].toff(4);
    digitalWrite(EN_PIN[a], LOW); g_axEn[a] = 1;
    g_tmcVerify |= (uint8_t)(1u << a);         // motion waits for the CHOPCONF read-back
  }
  stdDbg(PSTR("driver on x=%u y=%u z=%u"), g_axEn[0], g_axEn[1], g_axEn[2]);
}
static void cmdDisable() { g_enablePending = false; outputsOff(PSTR("disabled")); }

// ---- motion runners (stepper_firmware's runManualMode / runDpadStep / runAutoMode, on the ISR's axes) ----
// Manual velocity for axis a: v counts/s, signed. Refused (once) toward a guarded end; capped while parked.
static void jogAxis(uint8_t a, float v) {
  if (v > MAX_RATE) v = MAX_RATE; else if (v < -(float)MAX_RATE) v = -(float)MAX_RATE;
  int8_t d = v > 0 ? 1 : -1;
  int8_t curD; uint32_t curInc; bool curB;
  axVel(a, curD, curInc, curB);
  if (v == 0) { if (curD) axStop(a); g_refJog[a] = 0; return; }
  float r = fabs(v);
  if (parkedBits(a) && r > PARKED_JOG_RATE) r = PARKED_JOG_RATE;
  // Already jogging this way: only the rate may change. The interlock in the ISR owns a running jog; checking it here
  // too would refuse a parked jog at the end of its window one tick before the ISR halts it and learns that end
  // (xyz_stage_axis likewise checks a jog only when it starts from rest).
  if (curD == d && !curB) { if (curInc != rateToInc(r)) axRun(a, d, r, false, 0); return; }
  PGM_P why = !g_axEn[a] ? PSTR("not-enabled") : limitBlock(a, d, true);
  if (why) { if (curD) axStop(a); refused(a, why, &g_refJog[a]); return; }
  g_refJog[a] = 0;
  axRun(a, d, r, false, 0);
}
// Below the dead zone: a moving axis carries on to the next multiple of its step size, then stops there.
static void gridStop(uint8_t a) {
  int8_t d; uint32_t inc; bool bounded;
  axVel(a, d, inc, bounded);
  if (!d || bounded) return;
  IrqGuard g;
  AxisIsr &x = g_ax[a];
  int32_t size = g_stepSize[a], r = x.pos % size;
  if (r == 0) { axisStopIsr(x); return; }
  x.target = d > 0 ? x.pos - r + (r > 0 ? size : 0) : x.pos - r - (r < 0 ? size : 0);
  x.bounded = 1;
}
static void runManual() {
  if (!stickNeutral()) {
    for (uint8_t a = 0; a < 3; a++) jogAxis(a, g_man[a] * g_fullSpeed);
  } else {
    for (uint8_t a = 0; a < 3; a++) { gridStop(a); g_refJog[a] = 0; }
  }
  if (g_dpad[0] || g_dpad[1] || g_dpad[2]) {   // a D-pad step: each pressed axis moves one step size at FULL_SPEED
    g_dpadStep = true; g_manualOn = false;
    for (uint8_t a = 0; a < 3; a++) {
      int32_t steps = (int32_t)g_dpad[a] * g_stepSize[a];
      if (!steps) { axStop(a); g_refDpad[a] = 0; continue; }
      int8_t d = steps > 0 ? 1 : -1;
      PGM_P why = !g_axEn[a] ? PSTR("not-enabled") : limitBlock(a, d, false);
      if (why) { axStop(a); refused(a, why, &g_refDpad[a]); continue; }
      g_refDpad[a] = 0;
      axRun(a, d, fabs(g_fullSpeed), true, axPos(a) + steps);
    }
  }
}
static void runDpad() { if (!anyMoving()) { g_manualOn = true; g_dpadStep = false; } }
static void runAuto() { if (!g_allDone && !anyMoving()) g_allDone = true; }

// Jog dead-man (stepper_firmware): manual motion and a D-pad step need a packet every JOG_TIMEOUT_MS. Stops every
// axis, keeps the coils holding, stays in manual so the next packet resumes from neutral.
static void jogDeadman(uint32_t now) {
  if (!(g_manualOn || g_dpadStep)) return;
  if (now - g_lastPacketMs <= JOG_TIMEOUT_MS) return;
  for (uint8_t a = 0; a < 3; a++) { g_man[a] = 0; g_dpad[a] = 0; }
  g_dpadStep = false; g_manualOn = true;
  stopAll();
}

// ================================================================ ISR events -> lines, HOME
static void serviceSensors() {
  for (uint8_t a = 0; a < 3; a++) {
    uint8_t hits, trav, both, rel, seen; int32_t hp[2]; int8_t end[2];
    {
      IrqGuard g;
      AxisIsr &x = g_ax[a];
      hits = x.lsHit; trav = x.lsTravel; both = x.lsBoth; rel = x.lsReleased; seen = x.seenNow;
      x.lsHit = x.lsTravel = x.lsBoth = x.lsReleased = x.seenNow = 0;
      hp[0] = x.lsHitPos[0]; hp[1] = x.lsHitPos[1]; end[0] = x.lsEnd[0]; end[1] = x.lsEnd[1];
    }
    const char L = stdAxisLetter(a);
    for (uint8_t i = 0; i < 2; i++) {
      const uint8_t b = (uint8_t)(1u << i);
      if (rel & b) stdDbg(PSTR("limit %c ls%u end=%s learned=release"), L, i + 1, stdEnd(end[i]));
      if (hits & b) {
        const char *why = (trav & b) ? " learned=travel travel=800" : (both & b) ? " pressed_both_ways=1 travel=800" : "";
        stdEvt(0, PSTR("LIMIT %c ls%u pos=%ld end=%s%s%s"), L, i + 1, (long)hp[i], stdEnd(end[i]), why,
               (seen & b) ? " seen=1" : "");
      } else if (seen & b) stdEvt(0, PSTR("LIMIT %c ls%u seen=1"), L, i + 1);
    }
    if (hits && g_home != H_IDLE && h_axis == a) h_lsHits |= hits;
  }
}

// ================================================================ hooks for station_std.h
bool skBusy() { return anyMoving() || g_home != H_IDLE || g_dpadStep; }
bool skEnabled() { return g_sysEnabled; }
void skHostTimeoutStop() { haltMotion(PSTR("host-timeout")); }
void skHostTimeoutDisable() { outputsOff(PSTR("host-timeout")); }
void skStop() { haltMotion(PSTR("stop")); }
bool skAxisCfgBusy() { return g_sysEnabled || anyMoving() || g_home != H_IDLE; }
void skAxisCfgApply(uint8_t a) {               // new meaning: forget the learned ends; a switch pressed now is parked
  IrqGuard g;
  AxisIsr &x = g_ax[a];
  x.limitsOn = stdCfgLimits(a);
  x.lsEnd[0] = x.lsEnd[1] = 0; x.lsFirm = x.lsSeen = 0;
  for (uint8_t i = 0; i < 2; i++) { x.lsLo[i] = x.pos - PARKED_TRAVEL; x.lsHi[i] = x.pos + PARKED_TRAVEL; }
}
uint8_t skTmc(uint8_t a) { return g_tmc[a]; }
uint8_t skLimitsOn(uint8_t a) { return limitsOn(a); }
int8_t skLsEnd(uint8_t a, uint8_t i) { IrqGuard g; return g_ax[a].lsEnd[i]; }
uint8_t skHomed(uint8_t a) { return g_homed[a]; }
PGM_P skHomeCheck(uint8_t a) {
  if (!stdCfgHome(a)) return PSTR("no-home-sensor");
  if (!limitsOn(a)) return PSTR("no-limits");  // a search without the interlock would end on a hard stop
  if (!g_axEn[a]) return PSTR("not-enabled");
  if (g_settling || g_home != H_IDLE || anyMoving() || g_dpadStep) return PSTR("busy");
  uint8_t t; { IrqGuard g; t = trippedBits(g_ax[a].lvl); }
  if (t == 3) return PSTR("limits-both-tripped:check-wiring");
  uint8_t pk = parkedBits(a);
  if (pk) return pk & 1 ? PSTR("limit-ls1-end-unknown:jog-off-it") : PSTR("limit-ls2-end-unknown:jog-off-it");
  return nullptr;
}
void skHomeStart(uint8_t a) { homeStart(a); }
PGM_P skZero(uint8_t a) {
  if (axMoving(a) || (g_home != H_IDLE && h_axis == a)) return PSTR("busy");
  axSetPos(a, 0);
  homedLost(a);
  return nullptr;
}
void skExtOpened() {
  if (!g_pinMapOk) stdEvt(0, PSTR("FAULT pin-map"));   // the ISR's port bits are not the pins named: never enabled
  for (uint8_t a = 0; a < 3; a++)
    if (g_tmcMissingEvt & (1u << a)) stdEvt(0, PSTR("FAULT tmc-missing %c"), stdAxisLetter(a));
  g_tmcMissingEvt = 0;
}
#ifdef __AVR__
extern char __heap_start, *__brkval;
static int freeRam() { char top; return (int)(&top - (__brkval ? __brkval : &__heap_start)); }
#else
static int freeRam() { return -1; }
#endif
void skInfoExtra(char *buf, size_t n) {
  snprintf_P(buf, n, PSTR(" free_ram=%d tick_us=%u max_rate=%u parked_travel=%ld home_dir=%d home_flag_level=%u pins=%u"),
             freeRam(), STEP_TICK_US, MAX_RATE, (long)PARKED_TRAVEL, HOME_DIR, HOME_FLAG_LEVEL, g_pinMapOk ? 1u : 0u);
}

// ================================================================ host input
// One byte dispatcher, the Stepper firmware's: 0xAA jog packet, 'd', 'e', 's', a frame line (a digit or '-'), and now a
// '#' line. Any other byte is dropped. Lines are read without blocking; one line at a time.
static void lineEnd(bool timedOut) {
  uint8_t kind = g_lineKind;
  g_lineKind = LK_NONE;
  if (kind == LK_FRAME) {
    if (std_line.overflow) { haltMotion(PSTR("stop")); stdDbg(PSTR("frame too long: stop")); return; }
    handleFrame(std_line.buf);                 // a frame cut short is parsed as it stands, as readStringUntil did
    return;
  }
  if (timedOut) { stdDbg(PSTR("line dropped: no newline")); return; }
  stdExtLine(std_line.buf, std_line.overflow);
}
static void readHost(uint32_t now) {
  for (uint8_t budget = 0; budget < 200; budget++) {   // bounded work per loop(): the guards below must keep running
    if (g_lineKind != LK_NONE) {
      if (Serial.available() <= 0) {
        if (now - g_lineLastMs >= (g_lineKind == LK_FRAME ? FRAME_GAP_MS : STD_EXT_GAP_MS)) lineEnd(true);
        return;
      }
      char c = (char)stdRead(now);
      g_lineLastMs = now;
      if (stdLineFeed(c)) lineEnd(false);
      continue;
    }
    if (Serial.available() <= 0) return;
    uint8_t c = (uint8_t)Serial.peek();
    if (c == 0xAA) {
      if (Serial.available() >= (int)sizeof g_pkt) {
        uint8_t *p = (uint8_t *)&g_pkt;
        for (uint8_t i = 0; i < sizeof g_pkt; i++) p[i] = (uint8_t)stdRead(now);
        g_aaWaiting = false;
        handlePacket(now);
        continue;
      }
      if (!g_aaWaiting) { g_aaWaiting = true; g_aaSinceMs = now; }
      else if (now - g_aaSinceMs > PACKET_GAP_MS) { stdRead(now); g_aaWaiting = false; continue; }   // resync
      return;
    }
    g_aaWaiting = false;
    if (c == 'd') { stdRead(now); cmdDisable(); }
    else if (c == 'e') { stdRead(now); cmdEnable(now); }
    else if (c == 's') { stdRead(now); stdIdentity(); }
    else if (c == '-' || (c >= '0' && c <= '9')) { g_lineKind = LK_FRAME; stdLineReset(); g_lineLastMs = now; }
    else if (c == '#') { g_lineKind = LK_EXT; stdLineReset(); g_lineLastMs = now; }
    else stdRead(now);
  }
}

// ================================================================ setup / loop
// The step ISR's port bits must be the pins named above; a mismatch (an edit to one and not the other) keeps the
// drivers off for good (#INFO pins=0).
static bool pinMapOk() {
  return portOutputRegister(digitalPinToPort(X_STEP_PIN)) == &PORTC && digitalPinToBitMask(X_STEP_PIN) == _BV(5)
      && portOutputRegister(digitalPinToPort(X_DIR_PIN)) == &PORTC && digitalPinToBitMask(X_DIR_PIN) == _BV(4)
      && portOutputRegister(digitalPinToPort(Y_STEP_PIN)) == &PORTA && digitalPinToBitMask(Y_STEP_PIN) == _BV(0)
      && portOutputRegister(digitalPinToPort(Y_DIR_PIN)) == &PORTA && digitalPinToBitMask(Y_DIR_PIN) == _BV(1)
      && portOutputRegister(digitalPinToPort(Z_STEP_PIN)) == &PORTL && digitalPinToBitMask(Z_STEP_PIN) == _BV(7)
      && portOutputRegister(digitalPinToPort(Z_DIR_PIN)) == &PORTL && digitalPinToBitMask(Z_DIR_PIN) == _BV(6)
      && portInputRegister(digitalPinToPort(X_LS1_PIN)) == &PINE && digitalPinToBitMask(X_LS1_PIN) == _BV(4)
      && portInputRegister(digitalPinToPort(X_LS2_PIN)) == &PINE && digitalPinToBitMask(X_LS2_PIN) == _BV(5)
      && portInputRegister(digitalPinToPort(Y_LS1_PIN)) == &PIND && digitalPinToBitMask(Y_LS1_PIN) == _BV(3)
      && portInputRegister(digitalPinToPort(Y_LS2_PIN)) == &PIND && digitalPinToBitMask(Y_LS2_PIN) == _BV(2)
      && portInputRegister(digitalPinToPort(Z_LS1_PIN)) == &PIND && digitalPinToBitMask(Z_LS1_PIN) == _BV(1)
      && portInputRegister(digitalPinToPort(Z_LS2_PIN)) == &PIND && digitalPinToBitMask(Z_LS2_PIN) == _BV(0)
      && portInputRegister(digitalPinToPort(X_HOME_PIN)) == &PINK && digitalPinToBitMask(X_HOME_PIN) == _BV(0)
      && portInputRegister(digitalPinToPort(Y_HOME_PIN)) == &PINK && digitalPinToBitMask(Y_HOME_PIN) == _BV(1)
      && portInputRegister(digitalPinToPort(Z_HOME_PIN)) == &PINK && digitalPinToBitMask(Z_HOME_PIN) == _BV(2);
}

void setup() {
  // A watchdog reset leaves the watchdog running: clear it before anything slow (stepper_firmware).
  MCUSR = 0;
  wdt_disable();
  for (uint8_t a = 0; a < 3; a++) { pinMode(EN_PIN[a], OUTPUT); digitalWrite(EN_PIN[a], HIGH); }   // outputs off first
  const uint8_t outs[6] = { X_STEP_PIN, X_DIR_PIN, Y_STEP_PIN, Y_DIR_PIN, Z_STEP_PIN, Z_DIR_PIN };
  for (uint8_t i = 0; i < 6; i++) { pinMode(outs[i], OUTPUT); digitalWrite(outs[i], LOW); }
  const uint8_t ins[9] = { X_LS1_PIN, X_LS2_PIN, Y_LS1_PIN, Y_LS2_PIN, Z_LS1_PIN, Z_LS2_PIN, X_HOME_PIN, Y_HOME_PIN, Z_HOME_PIN };
  for (uint8_t i = 0; i < 9; i++) pinMode(ins[i], INPUT_PULLUP);
  delayMicroseconds(50);                       // let the pull-ups settle, then seed the filters so boot is not an edge
  g_pinMapOk = pinMapOk();
  const uint8_t pe = PINE, pd = PIND, pk = PINK;
  g_ax[0].lvl = rawX(pe, pk); g_ax[1].lvl = rawY(pd, pk); g_ax[2].lvl = rawZ(pd, pk);
  for (uint8_t a = 0; a < 3; a++) g_ax[a].t = trippedBits(g_ax[a].lvl);

  Serial.begin(BAUD_RATE);
  Serial2.begin(DRIVER_BAUD);
  g_tx2Empty = Serial2.availableForWrite();
  stdBegin(millis());
  for (uint8_t a = 0; a < 3; a++) {            // a switch pressed at power-up is parked: its travel counts from here
    g_ax[a].limitsOn = stdCfgLimits(a);
    for (uint8_t i = 0; i < 2; i++) { g_ax[a].lsLo[i] = -PARKED_TRAVEL; g_ax[a].lsHi[i] = PARKED_TRAVEL; }
  }
  g_hold = 0;

  // Timer1: CTC on OCR1A, clk/8 (0.5 us), one compare per STEP_TICK_US. Started last, after the axes are set up.
  TCCR1A = 0;
  TCCR1B = _BV(WGM12) | _BV(CS11);
  OCR1A = STEP_TICK_US * 2 - 1;
  TCNT1 = 0;
  TIMSK1 |= _BV(OCIE1A);

  g_wdtLastMs = millis();
  wdt_enable(WDTO_1S);
}

// Pet the watchdog only while the board is demonstrably healthy: millis() advancing, the step ISR ticking, and the host
// UART's receiver, transmitter and RX interrupt still on. If the UART fails the host can no longer be heard: outputs
// off here, and the watchdog resets the board within ~1 s (stepper_firmware's rule, plus the tick check).
static void watchdogCheck(uint32_t now) {
  const uint8_t uart_ok = _BV(RXEN0) | _BV(TXEN0) | _BV(RXCIE0);
  if ((UCSR0B & uart_ok) != uart_ok) {
    g_manualOn = false; g_autoOn = false; g_dpadStep = false; g_home = H_IDLE;
    stopAll();
    for (uint8_t a = 0; a < 3; a++) { digitalWrite(EN_PIN[a], HIGH); g_axEn[a] = 0; }
    g_sysEnabled = false;
    return;                                    // no pet: reset within ~1 s
  }
  uint8_t t = g_ticks;                         // one byte: no guard needed
  if (now != g_wdtLastMs && t != g_wdtLastTicks) { g_wdtLastMs = now; g_wdtLastTicks = t; wdt_reset(); }
}

static void printPosition(uint32_t now) {
  if (now - g_lastPrintMs < PRINT_INTERVAL) return;
  if (Serial.availableForWrite() < 35) return;   // never block on a host that is not reading
  int32_t p[3];
  { IrqGuard g; for (uint8_t a = 0; a < 3; a++) p[a] = g_ax[a].pos; }
  char b[40];
  snprintf_P(b, sizeof b, PSTR("POS:%ld,%ld,%ld"), (long)p[0], (long)p[1], (long)p[2]);
  Serial.print(b); stdNl();
  g_lastPrintMs = now;
}

void loop() {
  uint32_t now = millis();
  watchdogCheck(now);
  readHost(now);
  serviceSensors();                            // limit hits first, so homeStep() never mistakes a halt for a finished leg
  tmcService(now);
  if (g_enablePending && !g_tmcBoot) { g_enablePending = false; cmdEnable(now); }
  jogDeadman(now);
  stdHostService(now);
  if (g_settling && (int32_t)(now - g_settleUntil) >= 0 && !g_tmcVerify) { g_settling = false; g_hold = 0; }
  if (!g_settling) {
    if (g_manualOn) runManual();
    if (g_autoOn) runAuto();
    if (g_dpadStep) runDpad();
  }
  homeStep(now);
  static bool wasMoving[3] = { false, false, false };   // where each motion ended, for the log (LOG 2)
  for (uint8_t a = 0; a < 3; a++) {
    bool m = axMoving(a);
    if (wasMoving[a] && !m) stdDbg(PSTR("stopped %c pos=%ld"), stdAxisLetter(a), (long)axPos(a));
    wasMoving[a] = m;
  }
  printPosition(now);
}
