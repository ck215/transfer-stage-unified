// limit_seek: drive the bench axis slowly toward one end and stop the moment a limit switch changes state, however
// the switch is wired (to the stage GND or to +5 V). The validator senses its switches with pull-ups only, so a switch
// that closes to +5 V is invisible to it (open and pressed both read high); this sketch classifies each input with a
// pull-up AND a pull-down, as limits_diag does, while it moves. Bench tool (Teensy 3.5); never flashed by Setup.
//
// Same pins and driver setup as stepper_validator: STEP 2, DIR 3, EN 4, LS1 5 (DB9 2), LS2 6 (DB9 3), Vo 7 (DB9 5);
// TMC2209 on Serial1 single-wire half-duplex (TX1 to PDN_UART), 600 mA, 8 microsteps, spreadCycle.
//
// Commands (USB serial, one per line):
//   SEEK +|- <mm> <mm_s>   move at most <mm> (<= 50) at <mm_s> (<= 1.0); stops when LS1 or LS2 changes from its state
//                          at the start (a switch pressed or released), at the travel bound, on STOP, or when the host is
//                          silent for 1 s. The driver holds position after a stop.
//   STOP                   stop now (holding)      OFF   driver outputs off      STATE   print the inputs
// Lines out: BOOT, STATE, P (5 Hz while moving), HIT, HOME (home-sensor edges), END, STOP, ERR.
// Input states: OPEN (nothing drives it: switch open, or wire open), GND (closed to ground), HIGH (driven to +5 V).

#include <Arduino.h>
#include <TMCStepper.h>

extern "C" int _write(int file, char *ptr, int len) {   // Teensy 3.5 newlib needs this when printf-family code links
  if (file >= 0 && file <= 2) file = (int)&Serial;
  return ((Print *)file)->write((const uint8_t *)ptr, len);
}

const uint8_t  STEP_PIN = 2, DIR_PIN = 3, EN_PIN = 4, LS1_PIN = 5, LS2_PIN = 6, HOME_PIN = 7;
const float    R_SENSE = 0.11f;
const uint8_t  DRIVER_ADDRESS = 0;
const uint16_t RUN_CURRENT_MA = 600;
const float    HOLD_MULTIPLIER = 0.5f;
const uint16_t MICROSTEPS = 8;
const float    STEPS_PER_MM = 200.0f * MICROSTEPS / 1.0f;  // 200 full steps per rev, 1 mm lead
const float    MAX_SEEK_MM = 50.0f;                       // the stage's full travel
const float    MAX_SPEED_MM = 1.0f;
const uint32_t HOST_SILENT_MS = 1000;                     // dead-man while moving

TMC2209Stepper driver(&Serial1, R_SENSE, DRIVER_ADDRESS);
IntervalTimer stepTimer;

static volatile long    s_left = 0;     // steps still to go
static volatile long    s_pos = 0;      // steps from boot, + toward DIR high
static volatile int8_t  s_dir = 1;
static bool     g_enabled = false, g_moving = false, g_wasConnected = false;
static uint8_t  g_version = 0;
static uint32_t g_lastRx = 0, g_lastP = 0;
static uint8_t  g_base[2], g_last[3], g_pending[2];
static char     g_line[64];
static uint8_t  g_len = 0;

enum { OPEN = 0, GND = 1, HIGH_ = 2, ODD = 3 };
static const char *const NAME[] = {"OPEN", "GND", "HIGH", "ODD"};

static void stepIsr() {
  if (s_left <= 0) return;
  digitalWriteFast(STEP_PIN, HIGH);
  delayMicroseconds(2);
  digitalWriteFast(STEP_PIN, LOW);
  s_left--;
  s_pos += s_dir;
}

static float posMm() { noInterrupts(); long p = s_pos; interrupts(); return p / STEPS_PER_MM; }

static uint8_t classify(uint8_t pin) {
  pinMode(pin, INPUT_PULLUP);   delayMicroseconds(150); uint8_t u = digitalReadFast(pin);
  pinMode(pin, INPUT_PULLDOWN); delayMicroseconds(150); uint8_t d = digitalReadFast(pin);
  pinMode(pin, INPUT_PULLUP);
  return u ? (d ? HIGH_ : OPEN) : (d ? ODD : GND);
}

static void enableDriver(bool on) {
  if (on) { driver.toff(4); digitalWriteFast(EN_PIN, LOW); }
  else    { digitalWriteFast(EN_PIN, HIGH); driver.toff(0); }
  g_enabled = on;
}

static void stopMotion(const char *why) {
  stepTimer.end();
  noInterrupts(); s_left = 0; interrupts();
  if (g_moving) {
    g_moving = false;
    Serial.printf("STOP %s pos_mm=%.4f ls1=%s ls2=%s home=%s\n", why, posMm(), NAME[classify(LS1_PIN)],
                  NAME[classify(LS2_PIN)], NAME[classify(HOME_PIN)]);
  }
}

static void printState() {
  Serial.printf("STATE pos_mm=%.4f ls1=%s ls2=%s home=%s enabled=%d uart=%s version=0x%02X\n", posMm(),
                NAME[classify(LS1_PIN)], NAME[classify(LS2_PIN)], NAME[classify(HOME_PIN)], g_enabled,
                g_version == 0x21 ? "OK" : "FAIL", g_version);
}

static void startSeek(int8_t dir, float mm, float mmS) {
  if (g_moving) { Serial.println("ERR SEEK busy"); return; }
  g_version = driver.version();
  if (g_version != 0x21) { Serial.printf("ERR SEEK uart version=0x%02X (VM on?)\n", g_version); return; }
  if (!(mm > 0 && mm <= MAX_SEEK_MM) || !(mmS > 0 && mmS <= MAX_SPEED_MM)) {
    Serial.printf("ERR SEEK bad-arg mm<=%.0f mm_s<=%.1f\n", MAX_SEEK_MM, MAX_SPEED_MM); return;
  }
  g_base[0] = classify(LS1_PIN); g_base[1] = classify(LS2_PIN);
  g_pending[0] = g_pending[1] = 0;
  g_last[2] = classify(HOME_PIN);
  if (!g_enabled) { enableDriver(true); delay(30); }
  digitalWriteFast(DIR_PIN, dir > 0 ? HIGH : LOW);
  delayMicroseconds(50);
  noInterrupts(); s_dir = dir; s_left = (long)(mm * STEPS_PER_MM + 0.5f); interrupts();
  g_moving = true;
  g_lastRx = g_lastP = millis();
  Serial.printf("SEEK dir=%+d max_mm=%.3f speed_mm_s=%.3f from_mm=%.4f ls1=%s ls2=%s home=%s\n", dir, mm, mmS, posMm(),
                NAME[g_base[0]], NAME[g_base[1]], NAME[g_last[2]]);
  stepTimer.begin(stepIsr, 1e6f / (mmS * STEPS_PER_MM));
}

static void command(char *line) {
  for (char *c = line; *c; c++) *c = toupper(*c);
  if (!strncmp(line, "SEEK", 4)) {
    char sign = 0; float mm = 0, mmS = 0;
    if (sscanf(line + 4, " %c %f %f", &sign, &mm, &mmS) != 3 || (sign != '+' && sign != '-')) {
      Serial.println("ERR SEEK usage: SEEK +|- <mm> <mm_s>"); return;
    }
    startSeek(sign == '+' ? 1 : -1, mm, mmS);
  } else if (!strcmp(line, "STOP")) { stopMotion("host"); Serial.println("OK STOP"); }
  else if (!strcmp(line, "OFF")) { stopMotion("off"); enableDriver(false); Serial.println("OK OFF"); }
  else if (!strcmp(line, "STATE")) printState();
  else if (line[0]) Serial.printf("ERR unknown %s\n", line);
}

void setup() {
  pinMode(EN_PIN, OUTPUT); digitalWriteFast(EN_PIN, HIGH);        // outputs off, first
  pinMode(STEP_PIN, OUTPUT); digitalWriteFast(STEP_PIN, LOW);
  pinMode(DIR_PIN, OUTPUT); digitalWriteFast(DIR_PIN, LOW);
  pinMode(LS1_PIN, INPUT_PULLUP); pinMode(LS2_PIN, INPUT_PULLUP); pinMode(HOME_PIN, INPUT_PULLUP);
  Serial.begin(115200);
  Serial1.begin(115200, SERIAL_8N1_HALF_DUPLEX);
  driver.begin();
  driver.toff(0);
  driver.pdn_disable(true);
  driver.mstep_reg_select(true);
  driver.I_scale_analog(false);
  driver.microsteps(MICROSTEPS);
  driver.rms_current(RUN_CURRENT_MA, HOLD_MULTIPLIER);
  driver.en_spreadCycle(true);
  driver.GSTAT(0b111);
  g_version = driver.version();
}

void loop() {
  bool connected = (bool)Serial;
  if (connected && !g_wasConnected) {
    Serial.printf("BOOT limit_seek uart=%s version=0x%02X\n", g_version == 0x21 ? "OK" : "FAIL", g_version);
    printState();
  }
  if (!connected && g_wasConnected) { stopMotion("host-gone"); enableDriver(false); }
  g_wasConnected = connected;

  while (Serial.available()) {
    char c = Serial.read();
    g_lastRx = millis();
    if (c == '\n' || c == '\r') { g_line[g_len] = 0; if (g_len) command(g_line); g_len = 0; }
    else if (g_len < sizeof g_line - 1) g_line[g_len++] = c;
  }

  if (!g_moving) return;
  uint32_t now = millis();
  if (now - g_lastRx > HOST_SILENT_MS) { stopMotion("host-silent"); enableDriver(false); return; }

  // A limit switch that differs from its state at the start on two samples in a row ends the seek.
  for (uint8_t i = 0; i < 2; i++) {
    uint8_t st = classify(i ? LS2_PIN : LS1_PIN);
    g_pending[i] = (st != g_base[i]) ? g_pending[i] + 1 : 0;
    if (g_pending[i] >= 2) {
      stepTimer.end();
      noInterrupts(); s_left = 0; interrupts();
      g_moving = false;
      Serial.printf("HIT ls%u %s->%s pos_mm=%.4f\n", i + 1, NAME[g_base[i]], NAME[st], posMm());
      printState();
      return;
    }
  }
  uint8_t h = classify(HOME_PIN);
  if (h != g_last[2]) { Serial.printf("HOME %s->%s pos_mm=%.4f\n", NAME[g_last[2]], NAME[h], posMm()); g_last[2] = h; }

  noInterrupts(); long left = s_left; interrupts();
  if (left <= 0) {
    stepTimer.end();
    g_moving = false;
    Serial.printf("END travel pos_mm=%.4f (no limit switch changed)\n", posMm());
    printState();
    return;
  }
  if (now - g_lastP >= 200) {
    g_lastP = now;
    Serial.printf("P pos_mm=%.4f ls1=%s ls2=%s home=%s\n", posMm(), NAME[classify(LS1_PIN)], NAME[classify(LS2_PIN)],
                  NAME[h]);
  }
}
