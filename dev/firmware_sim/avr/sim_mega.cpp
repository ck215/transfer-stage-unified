// Host-side simulation of the XYZ stage on one Mega 2560: the whole sketch (firmware/xyz_stage_mega, or
// firmware/stepper_firmware with -DSIM_STEPREF: the frame-protocol reference and the base the scenarios are red on)
// built natively against the real AccelStepper source and the stub AVR core in include/.
//
// Fake clock in 5 us steps: Timer1's compare ISR at the period the sketch programs (50 us), loop() every 100 us
// (stepper_firmware, which steps from loop(), every 5 us), a host model (jog packets every 20 ms like the station's
// 50 Hz stream, #HB every 250 ms once a scenario turns it on). Stage model, per axis: the carriage moves only on STEP
// pulses while EN is low and the driver's output stage is on (TOFF > 0, VM present); LS1 operates at 0 mm and releases
// above +DT, LS2 operates at 50 mm and releases below 50-DT; hard stops OVT beyond each (a step into one is lost and
// counted, with the speed at contact); contact bounce after each snap (a release re-reads pressed for 300 us: review
// R-4's chatter); a home flag between 10 and 12 mm. Normally-open contacts on the internal pull-ups: pressed reads LOW.
// TMC bus model on Serial2: three TMC2209 at addresses 0-2 that echo every byte (RX2 sits on the junction), check
// CRCs, keep their registers, answer reads at once, and can lose VM, reset, overheat, short or ignore writes.
//
// Usage: sim <scenario> [-q]   (prints the trace, then one SCENARIO line; exit 0 = every check passed)
//        sim list                the scenarios;  sim parity-trace   the frame-protocol trace (PT lines)
#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>
#include <unistd.h>
#include <avr/io.h>
#include "sim_api.h"

#ifdef SIM_STEPREF
static const bool STEPREF = true;
static const uint64_t LOOP_US = 5;
#else
static const bool STEPREF = false;
static const uint64_t LOOP_US = 100;
#endif
static const uint64_t STEP_US = 5;
static bool g_verbose = true;
static const char *g_self = "";
static const char *g_scenario = "";

// ------------------------------------------------------------------ pins (MEGA_STANDARD §5)
struct AxisPins { uint8_t step, dir, en, ls1, ls2, home; };
static const AxisPins PINS[3] = { {32, 33, 34, 2, 3, 62}, {22, 23, 24, 18, 19, 63}, {42, 43, 44, 20, 21, 64} };
static const char AXL[3] = { 'X', 'Y', 'Z' };

// ------------------------------------------------------------------ TMC2209 bus model (Serial2)
struct Drv {
  bool vm = true;                // motor supply on: the driver answers and can drive the coils
  bool ignoreWrites = false;     // a driver whose UART receive is broken
  uint32_t gconf, chop, ihr, pwm, tpwm, drvStatus;
  uint8_t gstat, ifcnt;
  int writes = 0, reads = 0;
};
static Drv drv[3];
static void drvPowerUp(Drv &d) {
  d.gconf = 0x101; d.chop = 0x10000053UL; d.ihr = 0x10000UL; d.pwm = 0xC10D0024UL; d.tpwm = 0;
  d.drvStatus = 0x80100000UL;    // stst, cs_actual 16
  d.gstat = 1;                   // the power-up reset flag
  d.ifcnt = 0;
}
static uint8_t crc8(const uint8_t *d, int n) {
  uint8_t crc = 0;
  for (int i = 0; i < n; i++) {
    uint8_t c = d[i];
    for (int j = 0; j < 8; j++) { crc = ((crc >> 7) ^ (c & 1)) ? (uint8_t)((crc << 1) ^ 0x07) : (uint8_t)(crc << 1); c >>= 1; }
  }
  return crc;
}
static std::vector<uint8_t> s_dg;
static int s_badCrc = 0;
static uint32_t drvRead(int a, uint8_t reg) {
  Drv &d = drv[a];
  switch (reg) {
    case 0x00: return d.gconf;
    case 0x01: return d.gstat;
    case 0x02: return d.ifcnt;
    case 0x06: return (0x21UL << 24) | (uint32_t)((a & 1) << 2) | (uint32_t)((a & 2) << 2);   // VERSION, MS1/MS2 straps
    case 0x6C: return d.chop;
    case 0x6F: return d.drvStatus;
    case 0x70: return d.pwm;
    default: return 0;
  }
}
static void drvDatagram() {
  const std::vector<uint8_t> &g = s_dg;
  int n = (int)g.size();
  if (crc8(g.data(), n - 1) != g[n - 1]) { s_badCrc++; return; }
  uint8_t a = g[1], reg = g[2] & 0x7F;
  if (a > 2 || !drv[a].vm) return;
  Drv &d = drv[a];
  if (g[2] & 0x80) {
    if (d.ignoreWrites) return;
    uint32_t v = ((uint32_t)g[3] << 24) | ((uint32_t)g[4] << 16) | ((uint32_t)g[5] << 8) | g[6];
    d.writes++; d.ifcnt++;
    switch (reg) {
      case 0x00: d.gconf = v; break;
      case 0x01: d.gstat &= (uint8_t)~v; break;
      case 0x10: d.ihr = v; break;
      case 0x13: d.tpwm = v; break;
      case 0x6C: d.chop = v; break;
      case 0x70: d.pwm = v; break;
      default: break;
    }
    return;
  }
  d.reads++;
  uint32_t v = drvRead(a, reg);
  uint8_t r[8] = { 0x05, 0xFF, reg, (uint8_t)(v >> 24), (uint8_t)(v >> 16), (uint8_t)(v >> 8), (uint8_t)v, 0 };
  r[7] = crc8(r, 7);
  for (uint8_t c : r) sim_uart_rx(2, c);
}
void sim_on_uart_tx(uint8_t uart, uint8_t c) {
  if (uart != 2) return;                        // stepper_firmware's Serial1/Serial3: nothing listens in the model
  sim_uart_rx(2, c);                            // the echo: TX2 through 1 kOhm, RX2 on the same junction
  if (s_dg.empty() && c != 0x05) return;        // resync on the sync byte
  s_dg.push_back(c);
  size_t want = (s_dg.size() >= 3 && (s_dg[2] & 0x80)) ? 8 : 4;
  if (s_dg.size() >= 3 && s_dg.size() == want) { drvDatagram(); s_dg.clear(); }
}
static int drvToff(int a) { return (int)(drv[a].chop & 0xF); }
static int drvMres(int a) { return (int)((drv[a].chop >> 24) & 0xF); }
static int drvMicrosteps(int a) {
  if (drv[a].gconf & 0x80) { int m = drvMres(a); return m > 8 ? 1 : 256 >> m; }
  static const int STRAP[3] = { 8, 32, 64 };   // MS1/MS2 address straps decide it until mstep_reg_select is set
  return STRAP[a];
}

// ------------------------------------------------------------------ stage model
struct StepRec { uint64_t us; int8_t dir; bool moved; double x; bool p0, p1; };
struct Axis {
  double x = 25.0;
  double op1 = 0.0, op2 = 50.0, dt = 0.05, ovt = 0.8;
  bool wired[2] = { true, true };
  bool homeWired = true;
  double homeLo = 10.0, homeHi = 12.0;
  bool pressed[2] = { false, false };
  bool snapped[2] = { false, false }, snapRelease[2] = { false, false };
  uint64_t snapUs[2] = { 0, 0 };
  uint32_t relB0 = 300, relB1 = 600;    // after a release snap the contact reads pressed again in [relB0, relB1) us
  uint32_t preB0 = 300, preB1 = 450;    // after a press snap it reads released in [preB0, preB1) us (under the filter)
  int forced[2] = { -1, -1 };
  uint64_t forcedFrom[2] = { 0, 0 }, forcedUntil[2] = { 0, 0 };
  long lost = 0; double crashV = 0; uint64_t crashUs = 0;
  uint64_t lastStepUs = 0; int lastDir = 0;
  long net = 0;                         // pulses issued, signed: what the firmware counts
  std::vector<StepRec> log;
  void init() { pressed[0] = x <= op1; pressed[1] = x >= op2; }
  void snapCheck(uint64_t now) {
    for (int i = 0; i < 2; i++) {
      bool p = pressed[i];
      if (i == 0) { if (x <= op1) p = true; else if (x > op1 + dt) p = false; }
      else { if (x >= op2) p = true; else if (x < op2 - dt) p = false; }
      if (p != pressed[i]) { pressed[i] = p; snapped[i] = true; snapUs[i] = now; snapRelease[i] = !p; }
    }
  }
  bool contactPressed(int i, uint64_t now) const {
    if (forced[i] >= 0 && now >= forcedFrom[i] && now < forcedUntil[i]) return forced[i] != 0;
    if (!wired[i]) return false;
    bool p = pressed[i];
    if (snapped[i]) {
      uint64_t d = now - snapUs[i];
      if (snapRelease[i] ? (d >= relB0 && d < relB1) : (d >= preB0 && d < preB1)) p = !p;
    }
    return p;
  }
  uint8_t level(int i, uint64_t now) const { return contactPressed(i, now) ? 0 : 1; }   // NO contact to GND, pull-up
  uint8_t homeLevel() const { return !homeWired ? 1 : (x >= homeLo && x <= homeHi) ? 1 : 0; }
};
static Axis st[3];
static bool s_dead = false;             // the watchdog reset the board: outputs float, the sketch stops
static int s_wdtResets = 0;
static uint64_t s_wdtResetUs = 0;

static bool outputsOn(int a) {
  if (s_dead || sim_pin_out(PINS[a].en) != 0) return false;
  if (STEPREF) return true;             // stepper_firmware's driver writes go nowhere in the model: treat them as made
  return drv[a].vm && drvToff(a) != 0;
}
static double spm(int a) { return STEPREF ? 1600.0 : 200.0 * drvMicrosteps(a); }
static void onStep(int a, int dir) {
  Axis &s = st[a];
  uint64_t now = g_simUs;
  s.net += dir;
  bool moved = false;
  if (outputsOn(a)) {
    double nx = s.x + dir / spm(a);
    double v = (s.lastStepUs && dir == s.lastDir && now > s.lastStepUs) ? (1.0 / spm(a)) / ((now - s.lastStepUs) * 1e-6) : 0.0;
    if (nx < s.op1 - s.ovt - 1e-9 || nx > s.op2 + s.ovt + 1e-9) {
      s.lost++;
      if (!s.crashUs) s.crashUs = now;
      if (v > s.crashV) s.crashV = v;
    } else { s.x = nx; moved = true; }
    s.snapCheck(now);
  }
  s.lastStepUs = now; s.lastDir = dir;
  s.log.push_back({ now, (int8_t)dir, moved, s.x, s.pressed[0], s.pressed[1] });
}
void sim_on_port_write(uint8_t port, uint8_t oldv, uint8_t newv) {
  for (int a = 0; a < 3; a++) {
    if (sim_pin_port(PINS[a].step) != port) continue;
    uint8_t m = (uint8_t)(1u << sim_pin_bit(PINS[a].step));
    if (!(oldv & m) && (newv & m)) onStep(a, sim_pin_out(PINS[a].dir) ? 1 : -1);
  }
}
static void updatePins() {
  for (int a = 0; a < 3; a++) {
    sim_set_pin_in(PINS[a].ls1, st[a].level(0, g_simUs));
    sim_set_pin_in(PINS[a].ls2, st[a].level(1, g_simUs));
    sim_set_pin_in(PINS[a].home, st[a].homeLevel());
  }
}

// ------------------------------------------------------------------ serial capture
struct Line { uint64_t us; std::string s; };
static std::vector<Line> g_out;
static std::string g_partial;
void sim_on_serial_output(const char *b, size_t n) {
  for (size_t k = 0; k < n; k++) {
    if (b[k] == '\n') {
      if (!g_partial.empty() && g_partial.back() == '\r') g_partial.pop_back();
      g_out.push_back({ g_simUs, g_partial });
      if (g_verbose && g_partial.rfind("POS:", 0) != 0 && g_partial != "#OK HB")
        printf("%9.4f  < %s\n", g_simUs / 1e6, g_partial.c_str());
      g_partial.clear();
    } else g_partial += b[k];
  }
}

// ------------------------------------------------------------------ host model
struct Pkt {
  uint8_t mode = 1;
  float sx = 0, sy = 0, sz = 0, xs = 16, ys = 16, zs = 16, lr = 0, ud = 0, bump = 0, speed = 3200;
};
static void sendRaw(const std::string &s, bool echo = true) {
  if (g_verbose && echo) {
    std::string t = s;
    while (!t.empty() && (t.back() == '\n' || t.back() == '\r')) t.pop_back();
    printf("%9.4f  > %s\n", g_simUs / 1e6, t.c_str());
  }
  sim_serial_input((const uint8_t *)s.data(), s.size());
}
static void sendLine(const std::string &s) { sendRaw(s + "\n"); }
static std::string pktBytes(const Pkt &p) {
  std::string b;
  b += (char)0xAA; b += (char)p.mode;
  const float f[10] = { p.sx, p.sy, p.sz, p.xs, p.ys, p.zs, p.lr, p.ud, p.bump, p.speed };
  b.append((const char *)f, sizeof f);          // little-endian floats, as '<BBffffffffff'
  return b;
}
static uint64_t s_lastPktUs = 0;
static void sendPkt(const Pkt &p) { std::string b = pktBytes(p); sim_serial_input((const uint8_t *)b.data(), b.size()); s_lastPktUs = g_simUs; }
struct Host { bool hb = false; uint64_t nextHb = 0; bool stream = false; Pkt pkt; uint64_t nextPkt = 0; };
static Host host;
static void hostPoll() {
  if (host.hb && g_simUs >= host.nextHb) { sendRaw("#HB\n", false); host.nextHb = g_simUs + 250000; }
  if (host.stream && g_simUs >= host.nextPkt) { sendPkt(host.pkt); host.nextPkt = g_simUs + 20000; }
}
static std::string frame(long xs, long ys, long zs, double full, double xd, double yd, double zd, int manual = 0, int aut = 1) {
  char b[160];
  snprintf(b, sizeof b, "%ld,%ld,%ld,0,%.1f,0,0,%.1f,%.1f,%.1f,%d,%d", xs, ys, zs, full, xd, yd, zd, manual, aut);
  return b;
}
// A move in counts per axis at speed (the station's frame with step size 1: X and Z distances are negated on the wire)
static std::string moveFrame(long dx, long dy, long dz, double speed) { return frame(1, 1, 1, speed, (double)-dx, (double)dy, (double)-dz); }
static const std::string ZERO_FRAME = "0,0,0,0,0,0,0,0,0,0,0,0";

// ------------------------------------------------------------------ the clock
static void (*s_timerIsr)() = nullptr;
static uint64_t s_nextTimerUs = 0, s_nextLoopUs = 25;
static uint64_t timerPeriodUs() {
  static const uint32_t PS[8] = { 0, 1, 8, 64, 256, 1024, 0, 0 };
  uint32_t ps = PS[TCCR1B & 7];
  return ps ? (uint64_t)(OCR1A + 1) * ps / 16 : 0;
}
static void stepClock() {
  hostPoll();
  updatePins();
  if (!s_dead) {
    if (!s_timerIsr) s_timerIsr = sim_isr("TIMER1_COMPA_vect");
    uint64_t per = timerPeriodUs();
    if (s_timerIsr && per && (TIMSK1 & _BV(OCIE1A))) {
      if (g_simUs >= s_nextTimerUs) { s_timerIsr(); s_nextTimerUs += per; if (s_nextTimerUs <= g_simUs) s_nextTimerUs = g_simUs + per; }
    } else s_nextTimerUs = g_simUs + (per ? per : 50);
    if (g_simUs >= s_nextLoopUs) { loop(); s_nextLoopUs += LOOP_US; }
    if (g_simWdtArmed && g_simUs - g_simWdtPetUs > g_simWdtTimeoutUs) {
      s_dead = true; s_wdtResets++; s_wdtResetUs = g_simUs;
      if (g_verbose) printf("%9.4f  ** watchdog reset (no wdt_reset for %u ms)\n", g_simUs / 1e6, g_simWdtTimeoutUs / 1000);
    }
  }
  g_simUs += STEP_US;
}
static void runUs(uint64_t us) { uint64_t end = g_simUs + us; while (g_simUs < end) stepClock(); }
static void runMs(double ms) { runUs((uint64_t)(ms * 1000)); }
static uint64_t lastStepAny() { uint64_t t = 0; for (int a = 0; a < 3; a++) if (st[a].lastStepUs > t) t = st[a].lastStepUs; return t; }
static void waitIdle(double maxMs, double quietMs = 300) {
  uint64_t end = g_simUs + (uint64_t)(maxMs * 1000);
  runMs(20);
  while (g_simUs < end && g_simUs - lastStepAny() < (uint64_t)(quietMs * 1000)) runMs(20);
}
static void boot(double x0 = 25, double y0 = 25, double z0 = 25) {
  st[0].x = x0; st[1].x = y0; st[2].x = z0;
  for (int a = 0; a < 3; a++) { st[a].init(); drvPowerUp(drv[a]); }
  updatePins();
  setup();
  runMs(50);
}

// ------------------------------------------------------------------ helpers over the wire
static std::string upperWord(const std::string &c) {
  size_t a = c[0] == '#' ? 1 : 0;
  std::string w = c.substr(a, c.find(' ') == std::string::npos ? std::string::npos : c.find(' ') - a);
  for (auto &ch : w) ch = (char)toupper((unsigned char)ch);
  return w;
}
// Send one '#' command, run waitMs, return its #OK/#ERR line ("" if none).
static std::string ext(const std::string &c, double waitMs = 5) {
  size_t mark = g_out.size();
  sendLine(c);
  runMs(waitMs);
  std::string w = upperWord(c);
  for (size_t k = mark; k < g_out.size(); k++) {
    const std::string &s = g_out[k].s;
    if (s.rfind("#OK " + w, 0) == 0 || s.rfind("#ERR " + w, 0) == 0) return s;
  }
  return "";
}
static size_t countReplies(const std::string &w, size_t from) {
  size_t n = 0;
  for (size_t k = from; k < g_out.size(); k++)
    if (g_out[k].s.rfind("#OK " + w, 0) == 0 || g_out[k].s.rfind("#ERR " + w, 0) == 0) n++;
  return n;
}
static std::string kv(const std::string &line, const std::string &key) {
  size_t p = line.find(" " + key + "=");
  if (p == std::string::npos) return "?";
  p += key.size() + 2;
  return line.substr(p, line.find(' ', p) - p);
}
static size_t findLine(const std::string &needle, size_t from = 0) {
  for (size_t k = from; k < g_out.size(); k++) if (g_out[k].s.find(needle) != std::string::npos) return k;
  return (size_t)-1;
}
static size_t countLines(const std::string &needle, size_t from = 0) {
  size_t n = 0;
  for (size_t k = from; k < g_out.size(); k++) if (g_out[k].s.find(needle) != std::string::npos) n++;
  return n;
}
static const std::string &lineAt(size_t k) { static const std::string none = "(none)"; return k == (size_t)-1 ? none : g_out[k].s; }
static long stepsIn(int a, uint64_t t0, uint64_t t1) {
  long n = 0;
  for (const StepRec &r : st[a].log) if (r.us >= t0 && r.us < t1) n++;
  return n;
}
static double rateIn(int a, uint64_t t0, uint64_t t1) {   // mean steps/s between the first and last step in [t0, t1)
  uint64_t f = 0, l = 0; long n = 0;
  for (const StepRec &r : st[a].log) if (r.us >= t0 && r.us < t1) { if (!n) f = r.us; l = r.us; n++; }
  return n > 1 ? (n - 1) / ((l - f) * 1e-6) : 0.0;
}
static uint64_t maxIntervalDev(int a, uint64_t t0, uint64_t t1, double rate) {   // worst |interval - 1/rate|, us
  uint64_t prev = 0; double worst = 0, ideal = 1e6 / rate;
  for (const StepRec &r : st[a].log) {
    if (r.us < t0 || r.us >= t1) continue;
    if (prev) worst = fmax(worst, fabs((double)(r.us - prev) - ideal));
    prev = r.us;
  }
  return (uint64_t)worst;
}
// Highest speed (mm/s) over steps in [t0, t1) taken while switch i of axis a was pressed.
static double vmaxPressed(int a, int i, uint64_t t0, uint64_t t1) {
  double vm = 0;
  const std::vector<StepRec> &L = st[a].log;
  for (size_t k = 1; k < L.size(); k++) {
    const StepRec &p = L[k - 1], &q = L[k];
    if (q.us < t0 || q.us >= t1 || p.dir != q.dir || !q.moved) continue;
    if (!(i == 0 ? p.p0 && q.p0 : p.p1 && q.p1)) continue;
    vm = fmax(vm, (1.0 / spm(a)) / ((q.us - p.us) * 1e-6));
  }
  return vm;
}
static uint8_t en(int a) { return sim_pin_out(PINS[a].en); }

// EEPROM: the AXISCFG block (station_std.h: STD_EEPROM_ADDR 16: magic 0x5A, a byte per axis, their complements)
static void presetCfg(uint8_t x, uint8_t y, uint8_t z) {
  uint8_t *e = sim_eeprom();
  e[16] = 0x5A; e[17] = x; e[18] = y; e[19] = z; e[20] = (uint8_t)~x; e[21] = (uint8_t)~y; e[22] = (uint8_t)~z;
}
static const uint8_t CFG_L = 1, CFG_H = 2;

// ------------------------------------------------------------------ checks
static int g_checks = 0, g_failed = 0;
static std::vector<std::string> g_report;
static void expect(bool ok, const std::string &what, const std::string &got = "") {
  g_checks++;
  if (!ok) g_failed++;
  std::string s = std::string(ok ? "  ok   " : "  FAIL ") + what + (got.empty() ? "" : "  [" + got + "]");
  g_report.push_back(s);
  if (g_verbose) printf("%9.4f  %s\n", g_simUs / 1e6, s.c_str());
}
static bool has(const std::string &s, const std::string &sub) { return s.find(sub) != std::string::npos; }
static std::string fmt(const char *f, double v) { char b[80]; snprintf(b, sizeof b, f, v); return b; }
static std::string crashTxt(int a) {
  const Axis &s = st[a];
  if (!s.lost) return std::string(1, AXL[a]) + ": no hard-stop contact";
  char b[140];
  snprintf(b, sizeof b, "%c: hard stop hit at t=%.3f s, %ld steps lost, contact speed %.3f mm/s", AXL[a], s.crashUs / 1e6, s.lost, s.crashV);
  return b;
}
static bool noCrash() { return !st[0].lost && !st[1].lost && !st[2].lost; }
static std::string crashAll() { return crashTxt(0) + "; " + crashTxt(1) + "; " + crashTxt(2); }
static std::string xs(int a) { return std::string(1, AXL[a]) + " x=" + fmt("%.4f", st[a].x); }

// A simulated reboot: the EEPROM image and the checks so far are kept; the process starts over with "--resume".
static void rebootInto(int phase) {
  std::string path = std::string(g_self) + "." + g_scenario + ".state";
  FILE *f = fopen(path.c_str(), "wb");
  if (!f) { printf("cannot write %s\n", path.c_str()); exit(3); }
  fwrite(sim_eeprom(), 1, 4096, f);
  fprintf(f, "%d %d\n", g_checks, g_failed);
  for (const std::string &r : g_report) fprintf(f, "%s\n", r.c_str());
  fclose(f);
  fflush(stdout);
  if (g_verbose) printf("%9.4f  ** reboot (phase %d)\n", g_simUs / 1e6, phase);
  char ph[8]; snprintf(ph, sizeof ph, "%d", phase);
  execl(g_self, g_self, g_scenario, "--resume", path.c_str(), ph, g_verbose ? "-v" : "-q", (char *)nullptr);
  printf("exec failed\n"); exit(3);
}
static int resumeFrom(const char *path, int phase) {
  FILE *f = fopen(path, "rb");
  if (!f) { printf("cannot read %s\n", path); exit(3); }
  if (fread(sim_eeprom(), 1, 4096, f) != 4096) { printf("short state file\n"); exit(3); }
  if (fscanf(f, "%d %d\n", &g_checks, &g_failed) != 2) { printf("bad state file\n"); exit(3); }
  char b[512];
  while (fgets(b, sizeof b, f)) { size_t n = strlen(b); if (n && b[n - 1] == '\n') b[n - 1] = 0; g_report.push_back(b); }
  fclose(f);
  unlink(path);
  return phase;
}
static int g_phase = 1;

// ------------------------------------------------------------------ frame-protocol parity trace
// The same host script on stepper_firmware and on xyz_stage_mega (limits=0 home=0: the Mega's features are inert, so
// the frame protocol is all that is left). Each phase records what the board did with its STEP/DIR/EN pins.
struct Phase { std::string name; long pos[3]; long steps[3]; double rate[3]; uint8_t en[3]; double extra; };
static std::vector<Phase> g_trace;
static uint64_t s_phaseUs = 0;
static void phaseBegin() { s_phaseUs = g_simUs; }
static void phaseEnd(const std::string &name, double extra = 0) {
  Phase p; p.name = name; p.extra = extra;
  for (int a = 0; a < 3; a++) {
    p.pos[a] = st[a].net; p.steps[a] = stepsIn(a, s_phaseUs, g_simUs); p.rate[a] = rateIn(a, s_phaseUs, g_simUs); p.en[a] = en(a);
  }
  g_trace.push_back(p);
}
static void jogFor(Pkt p, double ms, double neutralMs) {
  host.pkt = p; host.stream = true; host.nextPkt = g_simUs;
  runMs(ms);
  Pkt n = p; n.sx = n.sy = n.sz = 0; n.lr = n.ud = n.bump = 0;
  host.pkt = n;
  runMs(neutralMs);
}
static void parityScript() {
  boot();
  phaseBegin(); sendRaw("s"); runMs(5); phaseEnd("identity");
  phaseBegin(); sendRaw("e"); runMs(100); phaseEnd("enable");
  phaseBegin(); sendLine(frame(16, 16, 16, 3200.0, -100, 50, 25)); waitIdle(3000); phaseEnd("frame-vector");
  phaseBegin(); sendLine(frame(10, 20, 5, 1000.0, 7, -3, 12)); waitIdle(3000); phaseEnd("frame-sizes");
  Pkt p; p.speed = 3200;
  p.sx = 0.5f; p.sy = -0.25f;                                  // X -1600 counts/s (the stick's X is inverted), Y -800
  phaseBegin(); jogFor(p, 505, 300); phaseEnd("jog-grid-stop");
  p = Pkt(); p.speed = 3200; p.sx = -1; p.sy = 1; p.sz = 1;    // all three at 3200 counts/s
  phaseBegin(); jogFor(p, 402.5, 300); phaseEnd("jog-3200-xyz");
  p = Pkt(); p.speed = 3200; p.xs = p.ys = p.zs = 160;
  Pkt d = p; d.lr = 1;
  phaseBegin(); jogFor(d, 20, 200); phaseEnd("dpad-x");
  d = p; d.bump = -1;
  phaseBegin(); jogFor(d, 20, 200); phaseEnd("dpad-z");
  d = p; d.ud = 1;
  phaseBegin(); jogFor(d, 20, 200); phaseEnd("dpad-y");
  host.stream = false; runMs(400);                               // let the dead-man settle what is left of manual
  phaseBegin(); sendLine(frame(16, 16, 16, 3200.0, -1000, 0, 0)); runMs(300); sendLine(ZERO_FRAME); runMs(200);
  phaseEnd("stop-frame");
  p = Pkt(); p.speed = 1600; p.sx = -1;
  phaseBegin(); host.pkt = p; host.stream = true; host.nextPkt = g_simUs; runMs(200);
  host.stream = false; Pkt stop; stop.mode = 0; sendPkt(stop); runMs(200); phaseEnd("stop-packet");
  p = Pkt(); p.speed = 1600; p.sy = 1;
  phaseBegin(); host.pkt = p; host.stream = true; host.nextPkt = g_simUs; runMs(200);
  host.stream = false; uint64_t lastPkt = s_lastPktUs; runMs(600);
  phaseEnd("dead-man", (st[1].lastStepUs - lastPkt) / 1000.0);
  p = Pkt(); p.speed = 1600; p.sx = NAN;
  phaseBegin(); sendPkt(p); runMs(100); phaseEnd("bad-packet");
  phaseBegin(); sendRaw("d"); runMs(20); phaseEnd("disable");
}
static void printTrace() {
  for (const Phase &p : g_trace)
    printf("PT %s pos=%ld,%ld,%ld steps=%ld,%ld,%ld rate=%.1f,%.1f,%.1f en=%u,%u,%u extra=%.3f\n", p.name.c_str(), p.pos[0],
           p.pos[1], p.pos[2], p.steps[0], p.steps[1], p.steps[2], p.rate[0], p.rate[1], p.rate[2], p.en[0], p.en[1], p.en[2], p.extra);
  for (const Line &l : g_out) printf("PTL %s\n", l.s.c_str());
}

// ------------------------------------------------------------------ scenarios
static std::string axisKey(int a, const char *k) { return std::string(1, (char)tolower(AXL[a])) + "_" + k; }
static Pkt stickOn(int a, int dir, float speed) {   // a stick deflection that moves axis a in dir (+1/-1)
  Pkt p; p.speed = speed;
  if (a == 0) p.sx = (float)-dir; else if (a == 1) p.sy = (float)dir; else p.sz = (float)dir;   // X is inverted
  return p;
}
// The stick held for ms, then neutral for neutralMs, then the stream stops (the station streams only in MANUAL, and a
// manual packet ends an autonomous move, on stepper_firmware as here).
static void jogAxis(int a, int dir, float speed, double ms, double neutralMs = 300) {
  jogFor(stickOn(a, dir, speed), ms, neutralMs);
  host.stream = false;
}
static long moveAxis(int a, long counts, double speed) {   // one-axis frame; returns the net steps it made
  long n0 = st[a].net;
  long d[3] = { 0, 0, 0 }; d[a] = counts;
  sendLine(moveFrame(d[0], d[1], d[2], speed));
  return n0;
}
static void openExt() { ext("#INFO"); }
static void enable() { sendRaw("e"); runMs(100); }

// 1. The frame protocol is the Stepper firmware's: the same script, the same motion (MEGA_STANDARD §1).
static bool parseTrace(const std::string &txt, std::vector<Phase> &ph, std::vector<std::string> &lines) {
  size_t p = 0;
  while (p < txt.size()) {
    size_t e = txt.find('\n', p);
    std::string l = txt.substr(p, e == std::string::npos ? std::string::npos : e - p);
    p = e == std::string::npos ? txt.size() : e + 1;
    if (l.rfind("PTL ", 0) == 0) { lines.push_back(l.substr(4)); continue; }
    if (l.rfind("PT ", 0) != 0) continue;
    Phase x; char name[64]; unsigned e0, e1, e2;
    if (sscanf(l.c_str(), "PT %63s pos=%ld,%ld,%ld steps=%ld,%ld,%ld rate=%lf,%lf,%lf en=%u,%u,%u extra=%lf", name, &x.pos[0],
               &x.pos[1], &x.pos[2], &x.steps[0], &x.steps[1], &x.steps[2], &x.rate[0], &x.rate[1], &x.rate[2], &e0, &e1, &e2,
               &x.extra) != 14) return false;
    x.name = name; x.en[0] = (uint8_t)e0; x.en[1] = (uint8_t)e1; x.en[2] = (uint8_t)e2;
    ph.push_back(x);
  }
  return true;
}
static std::string triple(const long v[3]) { char b[64]; snprintf(b, sizeof b, "%ld,%ld,%ld", v[0], v[1], v[2]); return b; }
static std::string tripleF(const double v[3]) { char b[64]; snprintf(b, sizeof b, "%.0f,%.0f,%.0f", v[0], v[1], v[2]); return b; }
static void s_parity() {
  parityScript();
  std::vector<Phase> me = g_trace;
  std::string cmd = std::string(g_self) + "-stepref parity-trace -q";
  FILE *pp = popen(cmd.c_str(), "r");
  std::string txt; char b[512];
  while (pp && fgets(b, sizeof b, pp)) txt += b;
  if (pp) pclose(pp);
  std::vector<Phase> ref; std::vector<std::string> refLines;
  bool parsed = parseTrace(txt, ref, refLines);
  expect(parsed && ref.size() == me.size(), "stepper_firmware ran the same script (" + cmd + ")",
         std::to_string(ref.size()) + " vs " + std::to_string(me.size()) + " phases");
  if (!parsed || ref.size() != me.size()) return;
  auto P = [&](const char *n) { for (size_t i = 0; i < me.size(); i++) if (me[i].name == n) return (int)i; return -1; };
  bool sawRefDev = false, sawMyDev = false, posOk = true, noHash = true;
  for (const std::string &l : refLines) { if (l == "DEV: s") sawRefDev = true; }
  for (const Line &l : g_out) {
    if (l.s == "DEV: m caps=ext1,log,hostto,home,limits,tmc") sawMyDev = true;
    if (!l.s.empty() && l.s[0] == '#') noHash = false;
    long x, y, z; char tail;
    if (l.s.rfind("POS:", 0) == 0 && sscanf(l.s.c_str(), "POS:%ld,%ld,%ld%c", &x, &y, &z, &tail) != 3) posOk = false;
  }
  expect(sawRefDev && sawMyDev, "identity: stepper_firmware 'DEV: s', the Mega 'DEV: m caps=ext1,log,hostto,home,limits,tmc'");
  expect(noHash, "no '#' line from the Mega in the whole run (nothing was asked)");
  expect(posOk, "every POS line is POS:<x>,<y>,<z>");
  long lastPos[3] = { 0, 0, 0 };
  for (const Line &l : g_out) if (l.s.rfind("POS:", 0) == 0) sscanf(l.s.c_str(), "POS:%ld,%ld,%ld", &lastPos[0], &lastPos[1], &lastPos[2]);
  expect(lastPos[0] == st[0].net && lastPos[1] == st[1].net && lastPos[2] == st[2].net, "the last POS line is the step count",
         triple(lastPos) + " vs " + std::to_string(st[0].net) + "," + std::to_string(st[1].net) + "," + std::to_string(st[2].net));
  // Each phase's own motion (its change of position and its step count) must match; the absolute positions after
  // jog-3200-xyz differ by up to a step size, because the reference's loop()-driven steps are a little slower.
  const char *exact[] = { "frame-vector", "frame-sizes", "jog-grid-stop", "dpad-x", "dpad-z", "dpad-y" };
  for (const char *n : exact) {
    int i = P(n);
    long dm[3], dr[3]; bool same = i > 0;
    for (int a = 0; i > 0 && a < 3; a++) {
      dm[a] = me[i].pos[a] - me[i - 1].pos[a]; dr[a] = ref[i].pos[a] - ref[i - 1].pos[a];
      if (dm[a] != dr[a] || me[i].steps[a] != ref[i].steps[a]) same = false;
    }
    expect(same, std::string(n) + ": the same motion on every axis (change of position and steps)",
           same ? triple(dm) : "mega " + triple(dm) + " ref " + triple(dr));
  }
  int fv = P("frame-vector"), fs = P("frame-sizes");
  expect(me[fv].steps[0] == 1600 && me[fv].steps[1] == 800 && me[fv].steps[2] == 400 && me[fv].pos[0] == 1600 &&
         me[fv].pos[1] == 800 && me[fv].pos[2] == -400, "frame-vector: X +1600 (X distance negated), Y +800, Z -400 (Z negated)",
         triple(me[fv].pos));
  long d2[3] = { me[fs].pos[0] - me[fv].pos[0], me[fs].pos[1] - me[fv].pos[1], me[fs].pos[2] - me[fv].pos[2] };
  expect(d2[0] == -70 && d2[1] == -60 && d2[2] == -60, "frame-sizes: step size x distance per axis (-70, -60, -60)", triple(d2));
  for (const char *n : { "frame-vector", "frame-sizes", "jog-grid-stop" }) {
    int i = P(n); bool ok = true;
    for (int a = 0; a < 3; a++) if (ref[i].rate[a] > 0 && fabs(me[i].rate[a] / ref[i].rate[a] - 1) > 0.03) ok = false;
    expect(ok, std::string(n) + ": the same speed per axis (within 3 %, the reference steps from loop())",
           "mega " + tripleF(me[i].rate) + " ref " + tripleF(ref[i].rate));
  }
  int jg = P("jog-grid-stop");
  expect(me[jg].pos[0] % 16 == 0 && me[jg].pos[1] % 16 == 0 && me[jg].pos[0] < me[fs].pos[0] && me[jg].pos[1] < me[fs].pos[1],
         "jog: the stick's X is inverted; each axis stops on a multiple of its step size", triple(me[jg].pos));
  int j3 = P("jog-3200-xyz"); bool r3 = true; long dd = 0;
  for (int a = 0; a < 3; a++) { if (fabs(me[j3].rate[a] / 3200.0 - 1) > 0.01) r3 = false; dd = std::max(dd, labs(me[j3].pos[a] - ref[j3].pos[a])); }
  expect(r3, "jog-3200-xyz: the Mega steps all three axes at 3200 counts/s (within 1 %)", tripleF(me[j3].rate));
  expect(me[j3].pos[0] % 16 == 0 && me[j3].pos[1] % 16 == 0 && me[j3].pos[2] % 16 == 0 && dd <= 32,
         "jog-3200-xyz: stops on the grid, within two step sizes of the reference (whose loop() steps a little slower)",
         "mega " + triple(me[j3].pos) + " ref " + triple(ref[j3].pos) + " ref rate " + tripleF(ref[j3].rate));
  int sf = P("stop-frame"), sp = P("stop-packet"), dm = P("dead-man"), bp = P("bad-packet"), di = P("disable"), enb = P("enable");
  long dsf = labs(me[sf].steps[0] - ref[sf].steps[0]), dsp = labs(me[sp].steps[0] - ref[sp].steps[0]);
  expect(me[sf].steps[0] > 900 && me[sf].steps[0] < 1000 && dsf <= 10, "stop-frame: the zero frame stops a move in flight",
         std::to_string(me[sf].steps[0]) + " vs " + std::to_string(ref[sf].steps[0]) + " steps");
  expect(me[sp].steps[0] > 250 && dsp <= 10, "stop-packet: a mode-0 packet stops a jog",
         std::to_string(me[sp].steps[0]) + " vs " + std::to_string(ref[sp].steps[0]));
  expect(me[dm].extra >= 250 && me[dm].extra <= 255 && ref[dm].extra >= 250 && ref[dm].extra <= 255,
         "dead-man: both stop 250 ms after the last packet", fmt("mega %.1f ms", me[dm].extra) + fmt(", ref %.1f ms", ref[dm].extra));
  expect(me[bp].steps[0] + me[bp].steps[1] + me[bp].steps[2] == 0 && ref[bp].steps[0] + ref[bp].steps[1] + ref[bp].steps[2] == 0,
         "a malformed jog packet (NaN) moves nothing on either");
  expect(me[enb].en[0] + me[enb].en[1] + me[enb].en[2] == 0 && ref[enb].en[0] + ref[enb].en[1] + ref[enb].en[2] == 0,
         "'e': every EN pin low on both");
  expect(me[di].en[0] + me[di].en[1] + me[di].en[2] == 3 && ref[di].en[0] + ref[di].en[1] + ref[di].en[2] == 3,
         "'d': every EN pin high on both");
}

// 2. Caps in the identity reply; nothing '#' until the host speaks it (MEGA_STANDARD §2, §3).
static void s_identity() {
  drv[2].vm = false;                                     // Z's driver absent: its boot event must wait
  boot();
  size_t m0 = g_out.size();
  sendRaw("s"); runMs(5);
  expect(lineAt(findLine("DEV:", m0)) == "DEV: m caps=ext1,log,hostto,home,limits,tmc", "'s' -> DEV: m caps=...", lineAt(findLine("DEV:", m0)));
  runMs(1000);
  expect(findLine("#") == (size_t)-1, "no '#' line before the host sends one (tmc-missing Z is held)");
  size_t m1 = g_out.size();
  sendLine("#NOPE"); runMs(5);
  expect(findLine("#", m1) == (size_t)-1, "an unknown '#' line before ext1 gets no reply (an old host's desynced packet)");
  size_t m2 = g_out.size();
  std::string r = ext("#INFO");
  expect(r.rfind("#OK INFO fw=xyz_stage_mega proto=1 caps=ext1,log,hostto,home,limits,tmc axes=XYZ ", 0) == 0, "#INFO header", r);
  size_t ki = findLine("#OK INFO", m2), kf = findLine("#EVT FAULT tmc-missing Z", m2);
  expect(kf != (size_t)-1 && kf > ki, "the held boot event follows the first reply: #EVT FAULT tmc-missing Z", lineAt(kf));
  expect(countLines("tmc-missing") == 1, "and only once");
  expect(ext("#NOPE") == "#ERR NOPE unknown-command", "once ext1 is open, an unknown command is answered");
  size_t m3 = g_out.size();
  sendRaw("s"); runMs(5);
  expect(lineAt(findLine("DEV:", m3)) == "DEV: m caps=ext1,log,hostto,home,limits,tmc", "identity unchanged after ext1");
}

// 3. Every '#' command and its errors; exactly one reply each (MEGA_STANDARD §3).
static void s_ext_commands() {
  boot();
  struct C { const char *cmd; const char *want; };
  const C cases[] = {
    { "#LOG 2", "#OK LOG level=2" }, { "#LOG 3", "#ERR LOG bad-arg" }, { "#LOG", "#ERR LOG bad-arg" }, { "#log x", "#ERR LOG bad-arg" },
    { "#LOG 1", "#OK LOG level=1" },
    { "#HOSTTIMEOUT 1000", "#OK HOSTTIMEOUT ms=1000" }, { "#HOSTTIMEOUT 100", "#OK HOSTTIMEOUT ms=250" },
    { "#HOSTTIMEOUT 99999", "#OK HOSTTIMEOUT ms=5000" }, { "#HOSTTIMEOUT 0", "#ERR HOSTTIMEOUT bad-arg" },
    { "#HOSTTIMEOUT abc", "#ERR HOSTTIMEOUT bad-arg" }, { "#HOSTTIMEOUT 1000", "#OK HOSTTIMEOUT ms=1000" },
    { "#HB", "#OK HB" },
    { "#AXISCFG X limits=1 home=1", "#OK AXISCFG axis=X limits=1 home=1 stored=1" },
    { "#AXISCFG y home=1", "#OK AXISCFG axis=Y limits=0 home=1 stored=1" },
    { "#AXISCFG Q limits=1", "#ERR AXISCFG bad-arg" }, { "#AXISCFG X", "#ERR AXISCFG bad-arg" },
    { "#AXISCFG X limits=2", "#ERR AXISCFG bad-arg" }, { "#AXISCFG X foo=1", "#ERR AXISCFG bad-arg" },
    { "#HOME Q", "#ERR HOME bad-arg" }, { "#HOME Z", "#ERR HOME no-home-sensor" }, { "#HOME Y", "#ERR HOME no-limits" },
    { "#HOME X", "#ERR HOME not-enabled" }, { "#HOME", "#ERR HOME bad-arg" },
    { "#ZERO Q", "#ERR ZERO bad-arg" }, { "#ZERO X", "#OK ZERO axis=X" },
    { "#STOP", "#OK STOP" }, { "#FOO 1 2", "#ERR FOO unknown-command" },
  };
  for (const C &c : cases) {
    size_t m = g_out.size();
    std::string r = ext(c.cmd);
    expect(r == c.want && countReplies(upperWord(c.cmd), m) == 1, std::string(c.cmd) + " -> " + c.want + " (one reply)", r);
  }
  host.hb = true; host.nextHb = g_simUs + 250000;           // HOSTTIMEOUT is armed from here on
  std::string inf = ext("#INFO");
  bool keys = true;
  for (int a = 0; a < 3; a++)
    for (const char *k : { "tmc", "limits", "home", "ls1_end", "ls2_end", "homed" }) if (kv(inf, axisKey(a, k)) == "?") keys = false;
  expect(keys && kv(inf, "log") == "1" && kv(inf, "hostto_ms") == "1000", "#INFO: every axis key, log=, hostto_ms=", inf);
  expect(kv(inf, "x_limits") == "1" && kv(inf, "x_home") == "1" && kv(inf, "y_home") == "1" && kv(inf, "y_limits") == "0",
         "#INFO reflects AXISCFG");
  size_t m = g_out.size();
  sendLine("#" + std::string(100, 'A')); runMs(5);
  expect(lineAt(findLine("#ERR", m)) == "#ERR LINE too-long", "an overlong '#' line: #ERR LINE too-long");
  enable();
  expect(ext("#AXISCFG X limits=0") == "#ERR AXISCFG busy", "#AXISCFG refused while enabled");
  sendLine(moveFrame(8000, 0, 0, 1600)); runMs(100);
  expect(ext("#ZERO X") == "#ERR ZERO busy", "#ZERO refused while that axis moves");
  expect(ext("#ZERO Y") == "#OK ZERO axis=Y", "#ZERO on an idle axis while another moves");
  expect(ext("#HOME X") == "#ERR HOME busy", "#HOME refused while anything moves");
  uint64_t ts = g_simUs;
  expect(ext("#STOP") == "#OK STOP", "#STOP");
  runMs(50);
  expect(st[0].lastStepUs <= ts + 5000, "#STOP stops the move at once", fmt("last step +%.2f ms", (st[0].lastStepUs - (double)ts) / 1000));
  m = g_out.size();
  ext("#LOG 0");
  sendRaw("d"); runMs(10);
  sendLine(moveFrame(0, 0, 1600, 1600)); runMs(20);
  expect(findLine("#EVT REFUSED Z reason=not-enabled", m) != (size_t)-1, "LOG 0 keeps essential events (REFUSED)");
  expect(findLine("#EVT DBG", m) == (size_t)-1, "LOG 0: no DBG lines");
  m = g_out.size();
  ext("#LOG 2"); enable(); sendLine(moveFrame(160, 0, 0, 1600)); runMs(300);
  expect(findLine("#EVT DBG", m) != (size_t)-1, "LOG 2 adds #EVT DBG lines", lineAt(findLine("#EVT DBG", m)));
}

// 4. Three axes at 3200 counts/s at once, the interlock live on every tick: Y runs into LS2 and stops on it.
static void s_rate3200() {
  presetCfg(CFG_L, CFG_L, CFG_L);
  boot(25, 49.0, 25);
  openExt(); enable();
  host.pkt = Pkt(); host.pkt.speed = 3200; host.pkt.sx = -1; host.pkt.sy = 1; host.pkt.sz = 1;
  host.stream = true; host.nextPkt = g_simUs;
  uint64_t t0 = g_simUs;
  size_t m0 = g_out.size();
  runMs(1500);
  host.pkt.sx = host.pkt.sy = host.pkt.sz = 0; runMs(300); host.stream = false;
  for (int a : { 0, 2 }) {
    double r = rateIn(a, t0 + 100000, t0 + 1400000);
    expect(fabs(r / 3200 - 1) < 0.002, std::string(1, AXL[a]) + ": 3200 counts/s while the other two step", fmt("%.1f counts/s", r));
    uint64_t dev = maxIntervalDev(a, t0 + 100000, t0 + 1400000, 3200);
    expect(dev <= 50, std::string(1, AXL[a]) + ": every step period within one 50 us tick of 312.5 us", std::to_string(dev) + " us");
  }
  double ry = rateIn(1, t0 + 50000, t0 + 450000);
  expect(fabs(ry / 3200 - 1) < 0.002, "Y: 3200 counts/s up to the switch", fmt("%.1f counts/s", ry));
  uint64_t pressUs = 0;
  for (const StepRec &r : st[1].log) if (r.p1) { pressUs = r.us; break; }
  expect(pressUs && st[1].lastStepUs <= pressUs + 250, "Y: no step later than 250 us after LS2 closed (200 us filter + 1 tick)",
         fmt("last step +%.0f us", (double)st[1].lastStepUs - (double)pressUs));
  expect(st[1].x >= 50.0 && st[1].x < 50.002, "Y stopped on LS2's operating point", xs(1));
  expect(lineAt(findLine("#EVT LIMIT Y ls2", m0)).find("end=+1") != std::string::npos, "#EVT LIMIT Y ls2 ... end=+1", lineAt(findLine("#EVT LIMIT Y", m0)));
  expect(countLines("#EVT REFUSED Y reason=limit-ls2", m0) == 1, "the stick still asks Y for +: refused once, not 50 times a second");
  expect(noCrash(), "no hard-stop contact", crashAll());
}

// 5. Limit learning on every axis: first trips teach the end, a guarded end refuses, release chatter teaches nothing.
static void s_limit_learning() {
  presetCfg(CFG_L, CFG_L, CFG_L);
  boot();
  openExt(); enable();
  size_t m0 = g_out.size();
  sendLine(moveFrame(-48000, -48000, -48000, 3200 * sqrt(3.0))); waitIdle(20000);   // all three toward LS1
  for (int a = 0; a < 3; a++) {
    std::string A(1, AXL[a]);
    expect(st[a].x <= 0.0 && st[a].x > -0.002, A + " halts on LS1 at its first trip", xs(a));
    expect(lineAt(findLine("#EVT LIMIT " + A + " ls1", m0)).find("end=-1") != std::string::npos, "#EVT LIMIT " + A + " ls1 ... end=-1",
           lineAt(findLine("#EVT LIMIT " + A + " ls1", m0)));
  }
  std::string inf = ext("#INFO");
  expect(kv(inf, "x_ls1_end") == "-1" && kv(inf, "y_ls1_end") == "-1" && kv(inf, "z_ls1_end") == "-1", "#INFO: ls1_end=-1 on X, Y, Z");
  for (int a = 0; a < 3; a++) {
    std::string A(1, AXL[a]);
    size_t m = g_out.size(); long n0 = moveAxis(a, -1600, 1600); runMs(100);
    expect(lineAt(findLine("#EVT REFUSED", m)) == "#EVT REFUSED " + A + " reason=limit-ls1" && st[a].net == n0,
           A + ": a frame further toward LS1 is refused, nothing moves", lineAt(findLine("#EVT REFUSED", m)));
    m = g_out.size(); moveAxis(a, 1600, 1600); waitIdle(5000);
    expect(fabs(st[a].x - 1.0) < 0.002 && findLine("#EVT LIMIT", m) == (size_t)-1, A + ": off LS1 the full 1 mm; release chatter teaches nothing", xs(a));
    moveAxis(a, -3200, 3200); waitIdle(5000);
    expect(st[a].x <= 0.0 && st[a].x > -0.002, A + ": halts on LS1 again", xs(a));
  }
  m0 = g_out.size();
  sendLine(moveFrame(96000, 96000, 96000, 3200 * sqrt(3.0))); waitIdle(40000);       // all three to LS2
  inf = ext("#INFO");
  for (int a = 0; a < 3; a++) {
    std::string A(1, AXL[a]);
    expect(st[a].x >= 50.0 && st[a].x < 50.002, A + " halts on LS2 at its first trip", xs(a));
    expect(kv(inf, axisKey(a, "ls2_end")) == "+1", "#INFO " + axisKey(a, "ls2_end") + "=+1");
  }
  expect(noCrash(), "no hard-stop contact", crashAll());
}

// 6. Parked on LS1 at power-up, every axis: frames and HOME refused, the jog off it capped at 160 counts/s, the end
// learned on release (not from its chatter), then a fast move back stops on it.
static void s_parked_jogoff() {
  presetCfg(CFG_L | CFG_H, CFG_L | CFG_H, CFG_L | CFG_H);
  boot(-0.1, -0.1, -0.1);
  openExt(); enable();
  std::string inf = ext("#INFO");
  for (int a = 0; a < 3; a++) {
    std::string A(1, AXL[a]);
    expect(kv(inf, axisKey(a, "ls1_end")) == "0", A + ": parked on LS1, end unknown");
    size_t m = g_out.size(); long n0 = moveAxis(a, 1600, 1600); runMs(100);
    expect(lineAt(findLine("#EVT REFUSED", m)) == "#EVT REFUSED " + A + " reason=limit-ls1-end-unknown:jog-off-it" && st[a].net == n0,
           A + ": a frame is refused while parked", lineAt(findLine("#EVT REFUSED", m)));
    expect(ext("#HOME " + A) == "#ERR HOME limit-ls1-end-unknown:jog-off-it", A + ": #HOME refused while parked");
    uint64_t t0 = g_simUs; m = g_out.size();
    jogAxis(a, +1, 3200, 2500);
    expect(vmaxPressed(a, 0, t0, g_simUs) <= 0.1001, A + ": <= 0.1 mm/s while LS1 is pressed", fmt("%.4f mm/s", vmaxPressed(a, 0, t0, g_simUs)));
    expect(st[a].x > 0.3, A + ": off it, then at full jog speed once released", xs(a));
    expect(kv(ext("#INFO"), axisKey(a, "ls1_end")) == "-1" && findLine("#EVT LIMIT", m) == (size_t)-1,
           A + ": end learned -1 on the release; its chatter halted nothing");
    m = g_out.size();
    jogAxis(a, -1, 3200, 2000);
    expect(st[a].x <= 0.0 && st[a].x > -0.002, A + ": a full-speed jog back stops on LS1", xs(a));
    expect(lineAt(findLine("#EVT LIMIT " + A + " ls1", m)).find("end=-1") != std::string::npos, "#EVT LIMIT " + A + " ls1 ... end=-1");
  }
  expect(noCrash(), "no hard-stop contact", crashAll());
}

// 6b. Parked and driven further in: the 0.5 mm (PROVISIONAL) travel budget halts it and learns that end.
static void s_parked_into() {
  presetCfg(CFG_L, CFG_L, CFG_L);
  boot(-0.1, 25, 25);
  openExt(); enable();
  uint64_t t0 = g_simUs; size_t m0 = g_out.size();
  jogAxis(0, -1, 3200, 10000);
  expect(vmaxPressed(0, 0, t0, g_simUs) <= 0.1001, "into LS1 at <= 0.1 mm/s", fmt("%.4f mm/s", vmaxPressed(0, 0, t0, g_simUs)));
  expect(st[0].x < -0.599 && st[0].x > -0.601, "halted 0.5 mm (800 counts) from where it was parked", xs(0));
  size_t k = findLine("#EVT LIMIT X ls1", m0);
  expect(has(lineAt(k), "end=-1") && has(lineAt(k), "learned=travel"), "#EVT LIMIT X ls1 ... end=-1 learned=travel", lineAt(k));
  size_t m1 = g_out.size();
  jogAxis(0, -1, 3200, 300);
  expect(lineAt(findLine("#EVT REFUSED X", m1)) == "#EVT REFUSED X reason=limit-ls1", "a jog further in is refused");
  size_t m2 = g_out.size();
  moveAxis(0, 1600, 1600); runMs(50);
  expect(has(lineAt(findLine("#EVT REFUSED X", m2)), "limit-ls1-end-unknown"), "frames stay refused until it releases",
         lineAt(findLine("#EVT REFUSED X", m2)));
  jogAxis(0, +1, 3200, 8000);
  expect(kv(ext("#INFO"), "x_ls1_end") == "-1" && st[0].x > 0.05, "jogged off; the release confirms -1", xs(0));
  expect(noCrash(), "no hard-stop contact", crashAll());
}

// 6c. Review R-4's other trap, per axis: the switch releases at rest (a nudge, a jog's last step), then re-reads pressed
// for 300 us just after the next move away starts. That trip must teach nothing, or the way back runs into the hard stop.
static void s_release_at_rest() {
  presetCfg(CFG_L, CFG_L, CFG_L);
  for (int a = 0; a < 3; a++) { st[a].forced[0] = 1; st[a].forcedFrom[0] = 0; st[a].forcedUntil[0] = 1000000; }   // parked until 1 s
  boot(0.06, 0.06, 0.06);
  runMs(1500);
  openExt(); enable();
  std::string inf = ext("#INFO");
  for (int a = 0; a < 3; a++) expect(kv(inf, axisKey(a, "ls1_end")) == "0", std::string(1, AXL[a]) + ": released at rest: clear, end unknown");
  for (int a = 0; a < 3; a++) {
    std::string A(1, AXL[a]);
    moveAxis(a, 8000, 800);
    runMs(50);
    st[a].forced[0] = 1; st[a].forcedFrom[0] = g_simUs; st[a].forcedUntil[0] = g_simUs + 300;   // chatter moving away
    waitIdle(15000);
    moveAxis(a, 8000, 800); waitIdle(15000);                // whatever happened, carry on away from LS1
    expect(kv(ext("#INFO"), axisKey(a, "ls1_end")) != "+1", A + ": LS1 not learned as + from the chatter");
    moveAxis(a, -32000, 1600); waitIdle(30000);             // back toward LS1
    expect(st[a].x <= 0.0 && st[a].x > -0.002 && !st[a].lost, A + ": the way back stops on LS1, not the hard stop", xs(a) + "; " + crashTxt(a));
  }
}

// 7. Both switches of one axis pressed: that axis halts, the others carry on, and it is refused afterwards.
static void s_both_pressed() {
  presetCfg(CFG_L, CFG_L | CFG_H, CFG_L);
  boot();
  openExt(); enable();
  long n0[3] = { st[0].net, st[1].net, st[2].net };
  sendLine(moveFrame(8000, 8000, 8000, 3200 * sqrt(3.0)));
  runMs(500);
  uint64_t tf = g_simUs;
  st[1].forced[0] = st[1].forced[1] = 1; st[1].forcedFrom[0] = st[1].forcedFrom[1] = tf;
  st[1].forcedUntil[0] = st[1].forcedUntil[1] = (uint64_t)1e15;
  waitIdle(5000);
  expect(st[1].lastStepUs <= tf + 250, "Y: no step later than 250 us after both read pressed", fmt("+%.0f us", (double)st[1].lastStepUs - (double)tf));
  expect(st[0].net - n0[0] == 8000 && st[2].net - n0[2] == 8000, "X and Z finish their 8000 counts");
  expect(findLine("#EVT LIMIT Y ls1") != (size_t)-1 && findLine("#EVT LIMIT Y ls2") != (size_t)-1, "#EVT LIMIT for both of Y's switches");
  size_t m = g_out.size();
  moveAxis(1, -1600, 1600); runMs(50);
  expect(lineAt(findLine("#EVT REFUSED", m)) == "#EVT REFUSED Y reason=limits-both-tripped:check-wiring", "a frame moving Y is refused",
         lineAt(findLine("#EVT REFUSED", m)));
  m = g_out.size();
  jogAxis(1, -1, 1600, 200);
  expect(lineAt(findLine("#EVT REFUSED", m)) == "#EVT REFUSED Y reason=limits-both-tripped:check-wiring", "a jog of Y is refused");
  expect(ext("#HOME Y") == "#ERR HOME limits-both-tripped:check-wiring", "#HOME Y refused");
}

// 8. Host silent: stop after HOSTTIMEOUT, outputs off 10 s later; only once armed (MEGA_STANDARD §4).
static void s_host_timeout(int variant) {        // 0: host gone, 1: host back within 10 s, 2: never armed
  boot();
  openExt();
  if (variant != 2) expect(ext("#HOSTTIMEOUT 1000") == "#OK HOSTTIMEOUT ms=1000", "#HOSTTIMEOUT 1000");
  enable();
  size_t m0 = g_out.size(); uint64_t tq = g_simUs;          // the last byte from the host is this frame
  sendLine(moveFrame(16000, 16000, 0, 1600));
  runMs(5);
  runMs(variant == 2 ? 3000 : 1500);
  size_t f = findLine("#EVT FAULT host-timeout", m0);
  if (variant == 2) {
    expect(f == (size_t)-1 && st[0].lastStepUs > tq + 2900000, "never armed: no host timeout, the move goes on (old-board behaviour)");
    waitIdle(20000);
    expect(st[0].net == 16000 && findLine("FAULT", m0) == (size_t)-1, "the move completes; no fault");
    return;
  }
  expect(f != (size_t)-1 && g_out[f].us >= tq + 1000000 && g_out[f].us <= tq + 1010000, "#EVT FAULT host-timeout 1 s after the last byte",
         f == (size_t)-1 ? "none" : fmt("+%.1f ms", (g_out[f].us - (double)tq) / 1000));
  uint64_t tf = f == (size_t)-1 ? g_simUs : g_out[f].us;
  expect(st[0].lastStepUs <= tf + 1000 && st[1].lastStepUs <= tf + 1000, "every axis stopped with it");
  if (variant == 1) { runUs(tf + 5000000 - g_simUs); sendLine("#HB"); }
  runUs(tf + 9800000 - g_simUs);
  expect(en(0) == 0 && en(1) == 0 && en(2) == 0, "still holding (EN low) 9.8 s after the fault");
  runUs(tf + 10300000 - g_simUs);
  if (variant == 1) {
    runMs(10000);
    expect(findLine("host-timeout-disabled", m0) == (size_t)-1 && en(0) == 0, "the host spoke within 10 s: no disable, still holding");
    return;
  }
  size_t d = findLine("#EVT FAULT host-timeout-disabled", m0);
  expect(d != (size_t)-1, "#EVT FAULT host-timeout-disabled", lineAt(d));
  expect(en(0) == 1 && en(1) == 1 && en(2) == 1, "outputs off (EN high) 10.3 s after the fault");
  size_t m1 = g_out.size(); long n0 = st[0].net;
  sendLine(moveFrame(160, 0, 0, 1600)); runMs(50);
  expect(lineAt(findLine("#EVT REFUSED", m1)) == "#EVT REFUSED X reason=not-enabled" && st[0].net == n0, "frames refused until 'e'");
  enable(); moveAxis(0, 160, 1600); waitIdle(2000);
  expect(st[0].net == n0 + 160, "after 'e' they move again");
}

// 9. Absent hardware is inert: blank EEPROM (limits=0 home=0), Z's driver missing, X's LS1 reading pressed.
static void s_absent_inert() {
  drv[2].vm = false;
  st[0].forced[0] = 1; st[0].forcedUntil[0] = (uint64_t)1e15;
  boot();
  runMs(300);
  expect(findLine("#") == (size_t)-1, "no '#' output before the host speaks ext1");
  std::string inf = ext("#INFO");
  bool zero = true;
  for (int a = 0; a < 3; a++) if (kv(inf, axisKey(a, "limits")) != "0" || kv(inf, axisKey(a, "home")) != "0") zero = false;
  expect(zero && kv(inf, "x_tmc") == "1" && kv(inf, "y_tmc") == "1" && kv(inf, "z_tmc") == "0", "#INFO: limits=0 home=0 everywhere, z_tmc=0", inf);
  expect(findLine("#EVT FAULT tmc-missing Z") != (size_t)-1, "#EVT FAULT tmc-missing Z");
  enable();
  expect(en(0) == 0 && en(1) == 0 && en(2) == 1, "'e' enables X and Y; Z (tmc=0) is never enabled");
  size_t m = g_out.size(); long nz = st[2].net;
  moveAxis(2, 1600, 1600); runMs(100);
  expect(lineAt(findLine("#EVT REFUSED", m)) == "#EVT REFUSED Z reason=not-enabled" && st[2].net == nz, "a frame moving Z is refused");
  m = g_out.size(); uint64_t t0 = g_simUs; long n0 = st[0].net;
  moveAxis(0, -1600, 3200); waitIdle(3000);
  expect(st[0].net == n0 - 1600 && fabs(rateIn(0, t0, g_simUs) / 3200 - 1) < 0.01, "limits=0: X moves toward its pressed LS1 at full speed",
         fmt("%.0f counts/s", rateIn(0, t0, g_simUs)));
  moveAxis(0, 1600, 3200); waitIdle(3000);
  expect(st[0].net == n0 && findLine("#EVT LIMIT", m) == (size_t)-1, "and back; no interlock, no LIMIT event");
  expect(ext("#HOME X") == "#ERR HOME no-home-sensor", "home=0: #HOME refused no-home-sensor");
}

// 10. Seen-once: a switch with limits=0 that trips while moving arms that axis for the session, never in EEPROM.
static void s_seen_once() {
  if (g_phase == 2) {
    boot();
    expect(kv(ext("#INFO"), "x_limits") == "0", "after a reboot: x_limits=0 again (seen-once is per session)");
    return;
  }
  boot(1.0, 25, 25);
  openExt();
  expect(kv(ext("#INFO"), "x_limits") == "0", "blank EEPROM: x_limits=0");
  enable();
  size_t m0 = g_out.size();
  moveAxis(0, -3200, 1600); waitIdle(5000);
  expect(st[0].x <= 0.0 && st[0].x > -0.002, "X stops on LS1 the first time it trips", xs(0));
  size_t k = findLine("#EVT LIMIT X ls1", m0);
  expect(has(lineAt(k), "end=-1") && has(lineAt(k), "seen=1"), "#EVT LIMIT X ls1 ... end=-1 seen=1", lineAt(k));
  expect(kv(ext("#INFO"), "x_limits") == "1", "#INFO x_limits=1 for the session");
  bool blank = true;
  for (int i = 16; i < 23; i++) if (sim_eeprom()[i] != 0xFF) blank = false;
  expect(blank, "nothing written to EEPROM");
  expect(noCrash(), "no hard-stop contact", crashAll());
  rebootInto(2);
}

// 10b. Seen-once in the R-4 trap: parked on LS1 at power-up with limits=0, jogged off; the release chatter arms the
// interlock but must not teach the wrong end, so the way back still stops on LS1.
static void s_seen_chatter() {
  boot(-0.1, 25, 25);
  openExt(); enable();
  size_t m0 = g_out.size();
  jogAxis(0, +1, 800, 1500);
  expect(findLine("seen=1", m0) != (size_t)-1, "the chatter arms the interlock: seen=1", lineAt(findLine("seen=1", m0)));
  std::string inf = ext("#INFO");
  expect(kv(inf, "x_limits") == "1" && kv(inf, "x_ls1_end") == "-1", "x_limits=1, ls1_end=-1 (from the release)", inf);
  expect(st[0].x > 0.5, "the jog off was not halted", xs(0));
  moveAxis(0, -3200, 1600); waitIdle(5000);
  expect(st[0].x <= 0.0 && st[0].x > -0.002 && noCrash(), "the way back stops on LS1, not the hard stop", xs(0) + "; " + crashTxt(0));
}

// 11. #AXISCFG writes EEPROM and survives a reboot.
static void s_axiscfg_persists() {
  if (g_phase == 2) {
    boot();
    std::string inf = ext("#INFO");
    expect(kv(inf, "x_limits") == "1" && kv(inf, "x_home") == "1" && kv(inf, "y_limits") == "0" && kv(inf, "y_home") == "0" &&
           kv(inf, "z_limits") == "0" && kv(inf, "z_home") == "1", "after a reboot #INFO shows what was stored", inf);
    expect(ext("#HOME X") == "#ERR HOME not-enabled", "the stored config is in force (#HOME X: not-enabled, not no-home-sensor)");
    return;
  }
  boot();
  expect(ext("#AXISCFG X limits=1 home=1") == "#OK AXISCFG axis=X limits=1 home=1 stored=1", "#AXISCFG X limits=1 home=1");
  expect(ext("#AXISCFG Z home=1") == "#OK AXISCFG axis=Z limits=0 home=1 stored=1", "#AXISCFG Z home=1");
  expect(sim_eeprom()[16] == 0x5A && sim_eeprom()[17] == 3 && sim_eeprom()[20] == (uint8_t)~3, "EEPROM: magic, X's byte, its complement");
  enable();
  expect(ext("#AXISCFG Y limits=1") == "#ERR AXISCFG busy", "refused while enabled");
  sendRaw("d"); runMs(10);
  rebootInto(2);
}

// 12. HOME succeeds and repeats to the same physical step, from either side of the flag.
static double homeRun(const std::string &A, size_t &m0) {
  m0 = g_out.size();
  expect(ext("#HOME " + A) == "#OK HOME started", "#HOME " + A + " -> #OK HOME started");
  for (int k = 0; k < 400 && findLine("HOMED " + A, m0) == (size_t)-1 && findLine("HOME FAIL " + A, m0) == (size_t)-1; k++) runMs(500);
  return st[0].x;
}
static void s_home_success() {
  presetCfg(CFG_L | CFG_H, 0, 0);
  boot();
  openExt(); enable();
  size_t m0;
  double x1 = homeRun("X", m0);
  size_t h = findLine("#EVT HOMED X", m0);
  expect(h != (size_t)-1 && has(lineAt(h), " pos=0"), "#EVT HOMED X edge=... pos=0", lineAt(h));
  expect(findLine("phase=seek", m0) < findLine("phase=backoff", m0) && findLine("phase=backoff", m0) < findLine("phase=approach", m0) &&
         findLine("phase=approach", m0) < findLine("phase=edge", m0) && findLine("phase=edge", m0) < h, "seek, backoff, approach, edge, HOMED");
  expect(fabs(x1 - 12.0) <= 1.0 / 1600 + 1e-9, "zeroed on the flag's edge at 12 mm (moving -)", fmt("x=%.5f", x1));
  expect(kv(ext("#INFO"), "x_homed") == "1", "#INFO x_homed=1");
  runMs(200);
  size_t p = (size_t)-1;
  for (size_t k = g_out.size(); k-- > 0;) if (g_out[k].s.rfind("POS:", 0) == 0) { p = k; break; }
  expect(lineAt(p).rfind("POS:0,", 0) == 0, "POS reports X = 0", lineAt(p));
  moveAxis(0, -11200, 3200); waitIdle(10000);              // to 5 mm, below the flag
  double x2 = homeRun("X", m0);
  expect(has(lineAt(findLine("phase=seek", m0)), "after=start-beyond-old-zero"), "from below: the old zero says R is behind");
  expect(fabs(x2 - x1) <= 1.0 / 1600 + 1e-9, "the second HOME lands on the same step", fmt("%.5f", x2) + fmt(" vs %.5f", x1));
  moveAxis(0, 32000, 3200); waitIdle(20000);               // to 32 mm, above it
  double x3 = homeRun("X", m0);
  expect(fabs(x3 - x1) <= 1.0 / 1600 + 1e-9, "and so does a third from above", fmt("%.5f", x3));
  expect(ext("#ZERO X") == "#OK ZERO axis=X" && kv(ext("#INFO"), "x_homed") == "0", "#ZERO clears homed");
  expect(noCrash(), "no hard-stop contact", crashAll());
}

// 13. HOME refusals and failures: no flag between the switches, a stop mid-search.
static void s_home_failure() {
  presetCfg(CFG_L | CFG_H, CFG_H, 0);
  st[0].homeLo = 100; st[0].homeHi = -100;                  // X: the sensor never sees a flag
  boot();
  openExt();
  expect(ext("#HOME X") == "#ERR HOME not-enabled", "#HOME X while disabled: not-enabled");
  enable();
  expect(ext("#HOME Z") == "#ERR HOME no-home-sensor", "#HOME Z (home=0): no-home-sensor");
  expect(ext("#HOME Y") == "#ERR HOME no-limits", "#HOME Y (home=1, limits=0): no-limits");
  moveAxis(0, 1600, 1600); runMs(50);
  expect(ext("#HOME X") == "#ERR HOME busy", "#HOME X while X moves: busy");
  waitIdle(3000);
  size_t m0;
  homeRun("X", m0);
  size_t f = findLine("#EVT HOME FAIL X", m0);
  expect(has(lineAt(f), "reason=no-edge-between-limits"), "#EVT HOME FAIL X reason=no-edge-between-limits", lineAt(f));
  expect(findLine("#EVT LIMIT X ls1", m0) != (size_t)-1 && findLine("#EVT LIMIT X ls2", m0) != (size_t)-1, "it searched to both switches");
  expect(kv(ext("#INFO"), "x_homed") == "0", "x_homed=0");
  st[0].homeLo = 10; st[0].homeHi = 12;
  size_t m1 = g_out.size();
  ext("#HOME X"); runMs(2000);
  uint64_t ts = g_simUs;
  sendLine(ZERO_FRAME); runMs(50);
  expect(has(lineAt(findLine("#EVT HOME FAIL X", m1)), "reason=stop") && st[0].lastStepUs <= ts + 1000, "a stop frame ends HOME: FAIL reason=stop",
         lineAt(findLine("#EVT HOME FAIL X", m1)));
  expect(noCrash(), "no hard-stop contact", crashAll());
}

// 14. The TMC bus: addresses 0-2 detected and configured, 'e'/'d' TOFF, driver faults stop everything.
static void s_tmc_bus() {
  boot();
  runMs(100);
  for (int a = 0; a < 3; a++) {
    std::string A(1, AXL[a]); const Drv &d = drv[a];
    uint32_t irun = (d.ihr >> 8) & 0x1F, ihold = d.ihr & 0x1F;
    expect((d.gconf & 0xC5) == 0xC4 && drvMres(a) == 5 && drvToff(a) == 0 && irun == 18 && ihold == 9 && (d.chop & (1UL << 17)) &&
           (d.chop & (1UL << 28)) && !(d.pwm & (1UL << 18)) && d.gstat == 0,
           A + " (address " + std::to_string(a) + "): UART config, 8 microsteps, TOFF 0, IRUN 18 / IHOLD 9 (600 mA, 0.11 ohm), reset flag cleared",
           fmt("gconf=0x%03X", d.gconf) + fmt(" chop=0x%08X", d.chop) + fmt(" ihr=0x%05X", d.ihr));
  }
  std::string inf = ext("#INFO");
  expect(kv(inf, "x_tmc") == "1" && kv(inf, "y_tmc") == "1" && kv(inf, "z_tmc") == "1", "#INFO x_tmc=y_tmc=z_tmc=1");
  int reads0 = drv[0].reads;
  enable();
  expect(drvToff(0) == 4 && drvToff(1) == 4 && drvToff(2) == 4 && en(0) == 0 && en(1) == 0 && en(2) == 0, "'e': TOFF 4 on all three, EN low");
  expect(drv[0].reads > reads0, "and CHOPCONF read back");
  sendRaw("d"); runMs(20);
  expect(drvToff(0) == 0 && drvToff(1) == 0 && drvToff(2) == 0 && en(0) == 1 && en(1) == 1 && en(2) == 1, "'d': TOFF 0 on all three, EN high");
  enable();
  sendLine(moveFrame(8000, 8000, 8000, 1600)); runMs(300);
  size_t m = g_out.size(); uint64_t t0 = g_simUs;
  drvPowerUp(drv[1]);                                      // Y loses VM and comes back on its defaults
  runMs(400);
  size_t k = findLine("#EVT FAULT driver-reset Y", m);
  expect(k != (size_t)-1 && g_out[k].us < t0 + 120000, "#EVT FAULT driver-reset Y reconfigured, within two polls", lineAt(k));
  expect(en(0) == 1 && en(1) == 1 && en(2) == 1 && st[0].lastStepUs <= g_out[k].us + 100, "every axis stopped, EN high");
  expect(drvMres(1) == 5 && drvToff(1) == 0 && drv[1].gstat == 0, "Y reconfigured: 8 microsteps, TOFF 0");
  enable();
  m = g_out.size();
  drv[0].drvStatus |= 0x2;                                 // X overheats
  runMs(1500);
  expect(countLines("#EVT FAULT overtemp X ot=1 otpw=0", m) == 1 && en(0) == 1 && en(1) == 1, "#EVT FAULT overtemp X, once, outputs off");
  drv[0].drvStatus &= ~0x2UL;
  enable();
  m = g_out.size();
  drv[0].drvStatus |= 0x4;                                 // a short to ground on X's phase A
  runMs(500);
  expect(findLine("#EVT FAULT short X", m) != (size_t)-1 && en(0) == 1, "#EVT FAULT short X", lineAt(findLine("#EVT FAULT short X", m)));
  drv[0].drvStatus &= ~0x4UL;
  enable();
  m = g_out.size();
  drv[2].vm = false;                                       // Z's UART goes silent
  runMs(1000);
  expect(findLine("#EVT FAULT uart-lost Z", m) != (size_t)-1 && en(2) == 1 && en(0) == 1, "#EVT FAULT uart-lost Z after three missed polls");
  expect(s_badCrc == 0, "no datagram with a bad CRC on the bus", std::to_string(s_badCrc));
}

// 14b. A driver switched on after boot is found; one that ignores writes fails its read-back at 'e'.
static void s_tmc_late() {
  drv[2].vm = false;
  drv[0].ignoreWrites = true;
  boot();
  openExt();
  expect(kv(ext("#INFO"), "z_tmc") == "0" && findLine("#EVT FAULT tmc-missing Z") != (size_t)-1, "Z missing at boot");
  size_t m = g_out.size();
  drv[2].vm = true; drvPowerUp(drv[2]);
  runMs(1500);
  expect(findLine("#EVT TMC Z detected", m) != (size_t)-1 && kv(ext("#INFO"), "z_tmc") == "1" && drvMres(2) == 5,
         "VM on later: #EVT TMC Z detected, configured, z_tmc=1");
  m = g_out.size();
  sendRaw("e"); runMs(100);
  expect(has(lineAt(findLine("#EVT FAULT driver-readback-mismatch X", m)), "toff=3 mres=0") && en(0) == 1 && en(1) == 1 && en(2) == 1,
         "X ignores writes: #EVT FAULT driver-readback-mismatch X toff=3 mres=0 at 'e', outputs off",
         lineAt(findLine("#EVT FAULT", m)));
  long n0 = st[1].net;
  sendLine(moveFrame(0, 1600, 0, 1600)); runMs(200);
  expect(st[1].net == n0, "nothing moves after it");
}

// 15. The jog dead-man and the watchdog, as in stepper_firmware; plus the Mega's step-timer check.
static void s_watchdog_deadman() {
  boot();
  enable();
  Pkt p; p.speed = 1600; p.sx = -1; p.sy = 1; p.sz = 1;
  host.pkt = p; host.stream = true; host.nextPkt = g_simUs;
  runMs(300);
  host.stream = false;
  uint64_t lp = s_lastPktUs;
  runMs(600);
  bool ok = true; std::string got;
  for (int a = 0; a < 3; a++) {
    double d = (st[a].lastStepUs - (double)lp) / 1000;
    got += fmt(" %.1f ms", d);
    if (d < 249 || d > 252) ok = false;
  }
  expect(ok, "no packet for 250 ms: every axis stops (the jog dead-man)", got);
  expect(en(0) == 0 && en(1) == 0 && en(2) == 0, "coils keep holding after the dead-man");
  expect(s_wdtResets == 0, "the watchdog is petted while healthy");
  uint64_t tu = g_simUs;
  UCSR0B &= (uint8_t)~_BV(RXEN0);                          // the host UART's receiver dies
  runMs(5);
  expect(en(0) == 1 && en(1) == 1 && en(2) == 1, "host UART receiver off: outputs off at once");
  runMs(1500);
  expect(s_wdtResets == 1 && s_wdtResetUs >= tu + 990000 && s_wdtResetUs <= tu + 1010000, "and the watchdog resets the board ~1 s later",
         fmt("+%.1f ms", (s_wdtResetUs - (double)tu) / 1000));
}
static void s_watchdog_timer() {
  boot();
  enable();
  uint64_t t0 = g_simUs;
  TIMSK1 &= (uint8_t)~_BV(OCIE1A);                         // the step timer stops (a bug, a stray register write)
  runMs(1500);
  expect(s_wdtResets == 1 && s_wdtResetUs <= t0 + 1010000, "the step ISR stopped ticking: no pet, the watchdog resets the board",
         s_wdtResets ? fmt("+%.1f ms", (s_wdtResetUs - (double)t0) / 1000) : "no reset");
}

// 16. Review R-2 on the board (X-19): a frame whose step size x distance passes 32767 counts, and a D-pad step size
// past 32767 (the jog packet admits 1..100000). stepper_firmware and chuck_firmware held both in an `int`, 16 bits on
// the ATmega2560 (build.sh compiles their copies with int16_t): the frame's move wrapped to -31936 and ran the other
// way, into the - hard stop; the D-pad's 40000 became -25536. Every axis must travel the whole distance, forward.
// The three axes move alike, so the chuck's own pin order (its X on 42-44) does not matter. The xyz Mega held both in
// 32 bits from the start: it passes as it is.
static void s_past_int16() {
  boot(2, 2, 2);
  enable();
  long n0[3] = { st[0].net, st[1].net, st[2].net };
  sendLine(frame(16, 16, 16, 3200.0, -2100, 2100, -2100));   // X and Z negated on the wire: +33600 counts on every axis
  runMs(500);
  std::string early;
  bool forward = true;
  for (int a = 0; a < 3; a++) { long d = st[a].net - n0[a]; early += std::string(early.empty() ? "" : ", ") + std::to_string(d); if (d <= 0) forward = false; }
  expect(forward, "frame 16 x 2100 = 33600 counts: every axis starts forward", early);
  waitIdle(40000);
  long d[3] = { st[0].net - n0[0], st[1].net - n0[1], st[2].net - n0[2] };
  expect(d[0] == 33600 && d[1] == 33600 && d[2] == 33600, "the frame moves every axis +33600 counts (21 mm), not the wrapped -31936",
         triple(d));
  long n1[3] = { st[0].net, st[1].net, st[2].net };
  Pkt p; p.speed = 3200; p.xs = p.ys = p.zs = 40000; p.lr = p.ud = p.bump = 1;
  jogFor(p, 20, 30000);                                     // the press, then neutral packets (the dead-man) while it steps
  host.stream = false;
  long e[3] = { st[0].net - n1[0], st[1].net - n1[1], st[2].net - n1[2] };
  expect(e[0] == 40000 && e[1] == 40000 && e[2] == 40000, "a D-pad step of size 40000 moves every axis +40000 counts, not -25536",
         triple(e));
  expect(noCrash(), "no hard-stop contact", crashAll());
}

struct Scenario { const char *name; void (*fn)(); };
static void s_ht0() { s_host_timeout(0); }
static void s_ht1() { s_host_timeout(1); }
static void s_ht2() { s_host_timeout(2); }
static const Scenario SCENARIOS[] = {
  { "parity-frame-protocol", s_parity },
  { "identity-caps", s_identity },
  { "ext-commands", s_ext_commands },
  { "rate-3200-xyz-interlock", s_rate3200 },
  { "limit-learning-xyz", s_limit_learning },
  { "parked-jogoff-xyz", s_parked_jogoff },
  { "parked-driven-into", s_parked_into },
  { "release-at-rest-then-chatter", s_release_at_rest },
  { "both-switches-pressed", s_both_pressed },
  { "host-timeout-disables", s_ht0 },
  { "host-timeout-host-returns", s_ht1 },
  { "host-timeout-unarmed", s_ht2 },
  { "absent-hardware-inert", s_absent_inert },
  { "seen-once-reboot", s_seen_once },
  { "seen-once-chatter", s_seen_chatter },
  { "axiscfg-persists-reboot", s_axiscfg_persists },
  { "home-success-repeat", s_home_success },
  { "home-failure-refusals", s_home_failure },
  { "tmc-bus-faults", s_tmc_bus },
  { "tmc-late-readback", s_tmc_late },
  { "watchdog-deadman", s_watchdog_deadman },
  { "watchdog-step-timer", s_watchdog_timer },
  { "frame-dpad-past-int16", s_past_int16 },
};

int main(int argc, char **argv) {
  g_self = argv[0];
  if (argc < 2 || !strcmp(argv[1], "list")) {
    for (const Scenario &s : SCENARIOS) if (!(STEPREF && !strcmp(s.name, "parity-frame-protocol"))) printf("%s\n", s.name);
    return 0;
  }
  for (int i = 2; i < argc; i++) if (!strcmp(argv[i], "-q")) g_verbose = false;
  if (!strcmp(argv[1], "parity-trace")) { parityScript(); printTrace(); return 0; }
  g_scenario = argv[1];
  if (argc > 4 && !strcmp(argv[2], "--resume")) g_phase = resumeFrom(argv[3], atoi(argv[4]));
  for (const Scenario &s : SCENARIOS) {
    if (strcmp(s.name, argv[1])) continue;
    s.fn();
    printf("SCENARIO %-36s %s  (%d checks, %d failed)\n", s.name, g_failed ? "FAIL" : "PASS", g_checks, g_failed);
    for (const std::string &r : g_report) if (r.rfind("  FAIL", 0) == 0) printf("%s\n", r.c_str());
    return g_failed ? 1 : 0;
  }
  printf("unknown scenario %s\n", argv[1]);
  return 2;
}
