// Host-side simulation of the bench diagnostic limit_seek (dev/equipment_test/diagnostics/limit_seek): the whole sketch
// built natively against the stub Teensy layer (include/, stubs.cpp). Its switches are inputs the simulation drives as
// OPEN / GND / HIGH, read through whichever pull the sketch selects (it classifies each with a pull-up and a pull-down).
//
// Fake clock in 5 us steps: the sketch's IntervalTimer at the period it programs, loop() every 100 us, the host sending
// a bare newline every 200 ms (limit_seek stops after 1 s of host silence). Stage model: the carriage moves only on STEP
// pulses while EN is low; LS1 closes to GND at x <= 0 and LS2 at x >= 50 (normally open, to the stage GND), the home
// sensor reads OPEN (the bench's, 2026-10-09); hard stops 0.8 mm past each switch, and an obstruction (`wall`: the
// bench probe on axis 1 meets its fixture before LS2) a step past which is lost.
//
// Usage: seek <scenario> [-q]   (prints the trace, then one SCENARIO line; exit 0 = every check passed)
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>
#include "sim_api.h"

enum { P_STEP = 2, P_DIR = 3, P_EN = 4, P_LS1 = 5, P_LS2 = 6, P_HOME = 7 };
enum { D_OPEN = 1, D_GND = 2 };
static bool g_verbose = true;
static const double SPM = 1600.0;                       // 8 microsteps, 1 mm lead

struct Stage {
  double x = 25.0, wall = 1e9;
  long lost = 0; double lostAt = 0;
  void onStep(int dir) {
    if (g_simPinOut[P_EN] != 0) return;                  // outputs off: no torque
    double nx = x + dir / SPM;
    if (nx > wall + 1e-9 || nx < -0.8 - 1e-9 || nx > 50.8 + 1e-9) { if (!lost) lostAt = x; lost++; }
    else x = nx;
  }
};
static Stage st;

struct Line { uint64_t us; std::string s; };
static std::vector<Line> g_out;
static std::string g_partial;
void sim_on_serial_output(const char *b, size_t n) {
  for (size_t k = 0; k < n; k++) {
    if (b[k] == '\n') {
      g_out.push_back({g_simUs, g_partial});
      if (g_verbose && g_partial.rfind("P ", 0) != 0) printf("%9.4f  < %s\n", g_simUs / 1e6, g_partial.c_str());
      g_partial.clear();
    } else g_partial += b[k];
  }
}
static uint8_t s_prevStep = 0;
void sim_on_pin_write(uint8_t pin, uint8_t level) {
  if (pin == P_STEP) { if (level && !s_prevStep) st.onStep(g_simPinOut[P_DIR] ? 1 : -1); s_prevStep = level; }
}

static bool g_keepAlive = true;
static uint64_t s_nextKeep = 0, s_nextIsr = 0, s_nextLoop = 0;
static void (*s_isr)() = nullptr;
static void sendLine(const std::string &s) {
  if (g_verbose) printf("%9.4f  > %s\n", g_simUs / 1e6, s.c_str());
  sim_serial_input((s + "\n").c_str());
}
static void stepClock() {
  g_simPinDrive[P_LS1] = st.x <= 0.0 ? D_GND : D_OPEN;
  g_simPinDrive[P_LS2] = st.x >= 50.0 ? D_GND : D_OPEN;
  g_simPinDrive[P_HOME] = D_OPEN;
  if (g_keepAlive && g_simUs >= s_nextKeep) { sim_serial_input("\n"); s_nextKeep = g_simUs + 200000; }
  if (g_simIsr != s_isr) { s_isr = g_simIsr; s_nextIsr = g_simUs + (uint64_t)g_simIsrPeriodF; }
  if (s_isr && g_simUs >= s_nextIsr) { s_isr(); s_nextIsr += (uint64_t)(g_simIsrPeriodF + 0.5); }
  if (g_simUs >= s_nextLoop) { loop(); s_nextLoop += 100; }
  g_simUs += 5;
}
static void runMs(double ms) { uint64_t end = g_simUs + (uint64_t)(ms * 1000); while (g_simUs < end) stepClock(); }
static void boot(double x0) {
  st.x = x0;
  for (int p : {P_LS1, P_LS2, P_HOME}) g_simPinDrive[p] = D_OPEN;
  setup();
  runMs(20);
}
// Send one line, run 5 ms, return the first line the sketch printed after it ("" if none).
static std::string cmd(const std::string &c, double ms = 5) {
  size_t mark = g_out.size();
  sendLine(c);
  runMs(ms);
  return mark < g_out.size() ? g_out[mark].s : "";
}
// Run until a sweep ends (HIT, END, STOP or an ERR), at most maxMs; return that line.
static std::string untilEnd(double maxMs, size_t from) {
  for (double t = 0; t < maxMs; t += 50) {
    runMs(50);
    for (size_t k = from; k < g_out.size(); k++) {
      const std::string &s = g_out[k].s;
      if (s.rfind("HIT ", 0) == 0 || s.rfind("END ", 0) == 0 || s.rfind("STOP ", 0) == 0) return s;
    }
  }
  return "";
}
static size_t findLine(const std::string &needle, size_t from = 0) {
  for (size_t k = from; k < g_out.size(); k++) if (g_out[k].s.find(needle) != std::string::npos) return k;
  return (size_t)-1;
}
static const size_t NONE = (size_t)-1;
static std::string lineAt(size_t k) { return k == NONE ? "(none)" : g_out[k].s; }
static double kvd(const std::string &line, const std::string &key) {
  size_t p = line.find(" " + key + "=");
  return p == std::string::npos ? NAN : atof(line.c_str() + p + key.size() + 2);
}

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
static std::string fmt(const char *f, double v) { char b[64]; snprintf(b, sizeof b, f, v); return b; }
static std::string crashTxt() {
  if (!st.lost) return "no contact";
  return std::to_string(st.lost) + " steps lost against the wall or a stop, from x=" + fmt("%.4f", st.lostAt);
}
// The soft-limit EEPROM block (SOFT_EEPROM_ADDR 4, as xyz_stage_axis and stepper_validator): magic 0x5C, um, ~um.
static void presetSoft(uint32_t um, bool damaged = false) {
  uint8_t *e = sim_eeprom();
  e[4] = 0x5C;
  for (int i = 0; i < 4; i++) { e[5 + i] = (uint8_t)(um >> (8 * i)); e[9 + i] = (uint8_t)~(um >> (8 * i)); }
  if (damaged) e[9] ^= 1;
}

// 1. No sweep before MAXTRAVEL is set this boot, and none longer than it: nothing runs 50 mm by default.
static void s_max_travel() {
  boot(25.0);
  expect(has(cmd("SEEK + 10 0.5"), "ERR SEEK no-max-travel"), "SEEK before MAXTRAVEL is refused", lineAt(g_out.size() - 1));
  runMs(500);
  expect(fabs(st.x - 25.0) < 1e-9, "nothing moved", fmt("x=%.4f", st.x));
  expect(has(cmd("MAXTRAVEL 60"), "ERR MAXTRAVEL bad-arg"), "MAXTRAVEL above 50 is refused");
  expect(has(cmd("MAXTRAVEL 0"), "ERR MAXTRAVEL bad-arg"), "MAXTRAVEL 0 is refused");
  expect(cmd("MAXTRAVEL 12") == "OK MAXTRAVEL mm=12.000", "MAXTRAVEL 12", lineAt(g_out.size() - 1));
  expect(has(cmd("SEEK + 20 0.5"), "ERR SEEK over-max-travel max_mm=12.000"), "a SEEK longer than it is refused");
  runMs(500);
  expect(fabs(st.x - 25.0) < 1e-9, "nothing moved", fmt("x=%.4f", st.x));
  size_t m = g_out.size();
  expect(has(cmd("SEEK + 12 1.0"), "SEEK dir=+1 max_mm=12.000"), "SEEK + 12 starts");
  std::string end = untilEnd(20000, m);
  expect(has(end, "END travel") && fabs(st.x - 37.0) < 1e-6, "it runs its 12 mm and ends", end + fmt(" x=%.4f", st.x));
}

// 2. With a soft limit stored: a SEEK onto LS1 references it, a SEEK past the limit is shortened onto it, and at the
// limit a SEEK further out is refused. The wall is 2 mm past the 28 mm limit.
static void s_soft_shortened() {
  st.wall = 30.0;
  presetSoft(28000);
  boot(25.0);
  expect(cmd("SOFTLIMIT") == "OK SOFTLIMIT mm=28.000 ref=0", "SOFTLIMIT read from EEPROM", lineAt(g_out.size() - 1));
  cmd("MAXTRAVEL 50");
  size_t m = g_out.size();
  cmd("SEEK - 30 1.0");                                   // unreferenced: a slow, bounded sweep is allowed, as a jog is
  std::string hit = untilEnd(40000, m);
  size_t r = findLine("EVT SOFTLIMIT referenced", m);
  double ls1 = kvd(lineAt(r), "ls1_mm"), lim = kvd(lineAt(r), "limit_mm");
  expect(has(hit, "HIT ls1 OPEN->GND") && st.x <= 0.0 && st.x > -0.01, "the sweep stops on LS1", hit + fmt(" x=%.4f", st.x));
  expect(r != NONE && fabs(ls1 + 25.0) < 0.01 && fabs(lim - ls1 - 28.0) < 1e-4, "EVT SOFTLIMIT referenced: the limit 28 mm from LS1",
         lineAt(r));
  const double limX = 25.0 + lim;
  m = g_out.size();
  cmd("SEEK + 1 0.5");                                    // off LS1: a sweep stops when the switch releases
  std::string rel = untilEnd(3000, m);
  expect(has(rel, "HIT ls1 GND->OPEN") && findLine("EVT SOFTLIMIT referenced", m) == NONE,
         "leaving LS1 stops on its release, which references nothing", rel);
  m = g_out.size();
  cmd("SEEK + 50 1.0");
  std::string end = untilEnd(60000, m);
  std::string start = lineAt(findLine("SEEK dir=", m));
  size_t c = findLine("EVT SOFTLIMIT clamped cmd=SEEK", m);
  expect(c != NONE && fabs(kvd(lineAt(c), "limit_mm") - lim) < 1e-4, "EVT SOFTLIMIT clamped cmd=SEEK", lineAt(c));
  expect(has(start, "SEEK dir=+1") && kvd(start, "max_mm") < 28.0 && kvd(start, "max_mm") > 27.9, "the sweep is shortened onto the limit",
         start);
  expect(fabs(st.x - limX) <= 0.5 / SPM && st.lost == 0, "it stops on the limit, short of the wall", fmt("x=%.5f; ", st.x) + crashTxt());
  expect(findLine("EVT SOFTLIMIT reached", m) != NONE, "EVT SOFTLIMIT reached", end);
  expect(has(cmd("SEEK + 1 0.5"), "ERR SEEK soft-limit"), "at the limit, a SEEK further out is refused", lineAt(g_out.size() - 1));
  m = g_out.size();
  const double before = st.x;
  cmd("SEEK - 2 1.0");
  expect(has(untilEnd(5000, m), "END travel") && fabs(st.x - (before - 2.0)) < 1e-6, "toward LS1 it sweeps as asked", fmt("x=%.4f", st.x));
  expect(st.lost == 0, "never reaches the wall", crashTxt());
}

// 4. A damaged block is not "no limit": every SEEK is refused until SOFTLIMIT writes it again.
static void s_soft_damaged() {
  presetSoft(28000, true);
  boot(25.0);
  cmd("MAXTRAVEL 50");
  expect(cmd("SOFTLIMIT") == "OK SOFTLIMIT mm=0.000 ref=0 damaged=1", "a damaged block reads damaged=1", lineAt(g_out.size() - 1));
  expect(has(cmd("SEEK - 5 0.5"), "ERR SEEK soft-limit-eeprom-damaged:set-SOFTLIMIT"), "every SEEK is refused");
  expect(cmd("SOFTLIMIT 28") == "OK SOFTLIMIT mm=28.000 ref=0 stored=1", "SOFTLIMIT 28 rewrites it");
  expect(has(cmd("SEEK - 1 0.5"), "SEEK dir=-1"), "then a SEEK runs");
}

// 3. The reference is lost with the step count (OFF while moving); with LS1's end known, a SEEK away from it is then
// refused and one toward it allowed.
static void s_soft_lost() {
  st.wall = 30.0;
  presetSoft(28000);
  boot(25.0);
  cmd("MAXTRAVEL 50");
  size_t m = g_out.size();
  cmd("SEEK - 30 1.0"); untilEnd(40000, m);
  m = g_out.size();
  cmd("SEEK + 1 0.5"); untilEnd(3000, m);                 // off LS1 (the sweep stops on the release)
  m = g_out.size();
  cmd("SEEK + 10 1.0"); runMs(2000);
  cmd("OFF");
  expect(findLine("EVT SOFTLIMIT lost reason=off", m) != NONE, "OFF while moving: EVT SOFTLIMIT lost reason=off",
         lineAt(findLine("EVT SOFTLIMIT", m)));
  expect(has(cmd("SOFTLIMIT"), " ref=0"), "SOFTLIMIT ref=0");
  expect(has(cmd("SEEK + 1 0.5"), "ERR SEEK soft-limit-unreferenced:touch-ls1"), "a SEEK away from LS1 is refused");
  m = g_out.size();
  cmd("SEEK - 10 1.0");
  std::string hit = untilEnd(15000, m);
  expect(has(hit, "HIT ls1") && findLine("EVT SOFTLIMIT referenced", m) != NONE, "toward LS1 it runs, and references it again", hit);
  expect(st.lost == 0, "never reaches the wall", crashTxt());
}

struct Scenario { const char *name; void (*fn)(); };
static const Scenario SCENARIOS[] = {
  {"seek-max-travel-mandatory", s_max_travel},
  {"seek-soft-limit-shortened", s_soft_shortened},
  {"seek-soft-limit-lost", s_soft_lost},
  {"seek-soft-limit-damaged", s_soft_damaged},
};

int main(int argc, char **argv) {
  if (argc < 2 || !strcmp(argv[1], "list")) { for (const Scenario &s : SCENARIOS) printf("%s\n", s.name); return 0; }
  if (argc > 2 && !strcmp(argv[2], "-q")) g_verbose = false;
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
