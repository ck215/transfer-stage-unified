// Host-side simulation of one axis: the whole sketch (station xyz_stage_axis.ino, or the bench stepper_validator.ino
// with -DSIM_VALIDATOR) built natively against the real AccelStepper source and stub Teensy/TMC/EEPROM layers.
//
// Fake clock: the step ISR runs every 25 us, loop() every 100 us, a host model sends lines (JOGV/JOG streams every
// 100 ms like the station's gamepad, heartbeats every 500 ms). Stage model: the carriage moves only by STEP pulses
// while EN is low; LS1 operates at 0 mm and releases above +DT, LS2 operates at 50 mm and releases below 50-DT;
// hard stops OVT beyond each operating point (a step into a hard stop is lost and counted as a crash, with the
// speed at contact); contact bounce after each snap (a release re-reads pressed for 300 us by default: the R-4
// chatter); a home flag between 10 and 12 mm; forced-level windows for wiring faults.
//
// Usage: sim <scenario>   (prints the trace, then one SCENARIO line; exit 0 = every check passed)
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
#include <vector>
#include "sim_api.h"

#ifdef SIM_VALIDATOR
static const bool VALIDATOR = true;
#else
static const bool VALIDATOR = false;
#endif
enum { P_STEP = 2, P_DIR = 3, P_EN = 4, P_LS1 = 5, P_LS2 = 6, P_HOME = 7 };
static bool g_verbose = true;

// ------------------------------------------------------------------ stage model
struct StepRec { uint64_t us; double x; int dir; bool p0, p1; };
struct Stage {
  double x = 25.0;
  double op1 = 0.0, op2 = 50.0, dt = 0.05, ovt = 0.8;
  bool nc = false;                       // false: NO contacts (pressed reads LOW); true: NC (pressed reads HIGH)
  bool pressed[2] = {false, false};
  bool snapped[2] = {false, false}, snapRelease[2] = {false, false};
  uint64_t snapUs[2] = {0, 0};
  uint32_t relB0 = 300, relB1 = 600;     // after a release snap the contact reads pressed again in [relB0, relB1) us
  uint32_t preB0 = 300, preB1 = 450;     // after a press snap it reads released in [preB0, preB1) us (under the filter)
  int forced[2] = {-1, -1};              // forced pressed state in [forcedFrom, forcedUntil)
  uint64_t forcedFrom[2] = {0, 0}, forcedUntil[2] = {0, 0};
  double homeLo = 10.0, homeHi = 12.0;
  long lostSteps = 0; double crashVmax = 0; uint64_t crashFirstUs = 0;
  uint64_t lastStepUs = 0; int lastDir = 0;
  std::vector<StepRec> log;
  double spm() const { return 200.0 * (g_simDrvMicrosteps ? g_simDrvMicrosteps : 1) / 1.0; }
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
    bool p = pressed[i];
    if (snapped[i]) {
      uint64_t d = now - snapUs[i];
      if (snapRelease[i] ? (d >= relB0 && d < relB1) : (d >= preB0 && d < preB1)) p = !p;
    }
    return p;
  }
  uint8_t level(int i, uint64_t now) const { bool p = contactPressed(i, now); return nc ? (p ? 1 : 0) : (p ? 0 : 1); }
  uint8_t homeLevel() const { return (x >= homeLo && x <= homeHi) ? 1 : 0; }
  void onStep(int dir, uint64_t now) {
    if (g_simPinOut[P_EN] != 0) return;                       // outputs off: no torque, the carriage does not move
    double nx = x + dir / spm();
    double v = (lastStepUs && dir == lastDir && now > lastStepUs) ? (1.0 / spm()) / ((now - lastStepUs) * 1e-6) : 0.0;
    if (nx < op1 - ovt - 1e-9 || nx > op2 + ovt + 1e-9) {     // against a hard stop: the step is lost
      lostSteps++;
      if (!crashFirstUs) crashFirstUs = now;
      if (v > crashVmax) crashVmax = v;
    } else x = nx;
    lastStepUs = now; lastDir = dir;
    snapCheck(now);
    log.push_back({now, x, dir, pressed[0], pressed[1]});
  }
};
static Stage st;

// Highest cruise speed (mm/s) over steps in [t0, t1) taken while switch i was pressed (consecutive same-direction steps).
static double vmaxPressed(int i, uint64_t t0, uint64_t t1) {
  double vm = 0;
  for (size_t k = 1; k < st.log.size(); k++) {
    const StepRec &a = st.log[k - 1], &b = st.log[k];
    if (b.us < t0 || b.us >= t1 || a.dir != b.dir) continue;
    if (!(i == 0 ? a.p0 && b.p0 : a.p1 && b.p1)) continue;
    double v = (1.0 / st.spm()) / ((b.us - a.us) * 1e-6);
    if (v > vm) vm = v;
  }
  return vm;
}

// ------------------------------------------------------------------ serial capture and pin hooks
struct Line { uint64_t us; std::string s; };
static std::vector<Line> g_out;
static std::string g_partial;
static uint8_t s_prevStep = 0;
void sim_on_serial_output(const char *b, size_t n) {
  for (size_t k = 0; k < n; k++) {
    if (b[k] == '\n') {
      g_out.push_back({g_simUs, g_partial});
      if (g_verbose && g_partial.rfind("OK HB", 0) != 0 && g_partial.rfind("OK PING", 0) != 0 &&
          g_partial.rfind("OK JOG", 0) != 0 && g_partial.rfind("OK STATUS", 0) != 0)
        printf("%9.4f  < %s\n", g_simUs / 1e6, g_partial.c_str());
      g_partial.clear();
    } else g_partial += b[k];
  }
}
void sim_on_pin_write(uint8_t pin, uint8_t level) {
  if (pin == P_STEP) { if (level && !s_prevStep) st.onStep(g_simPinOut[P_DIR] ? 1 : -1, g_simUs); s_prevStep = level; }
}

// ------------------------------------------------------------------ host model and the clock
struct Host {
  bool hb = true; uint64_t nextHb = 0;
  int stick = 0; double stickMm = 0; uint64_t stickUntil = 0, nextStick = 0;
};
static Host host;
static void sendLine(const std::string &s, bool echo = true) {
  if (g_verbose && echo) printf("%9.4f  > %s\n", g_simUs / 1e6, s.c_str());
  sim_serial_input((s + "\n").c_str());
}
static std::string jogLine(int dir, double mm) {
  char b[64];
  if (VALIDATOR) snprintf(b, sizeof b, "JOG %d", dir);
  else snprintf(b, sizeof b, "JOGV %.4f", dir * mm);
  return b;
}
static void hostPoll() {
  uint64_t now = g_simUs;
  if (host.hb && now >= host.nextHb) { sendLine(VALIDATOR ? "PING" : "HB", false); host.nextHb = now + 500000; }
  if (host.stick) {
    if (now >= host.stickUntil) { sendLine(VALIDATOR ? "JOG 0" : "JOGV 0"); host.stick = 0; }
    else if (now >= host.nextStick) { sendLine(jogLine(host.stick, host.stickMm), false); host.nextStick = now + 100000; }
  }
}
uint8_t sim_probe_level(int i);                          // appended to the sketch by build.sh
struct Filtered { uint64_t us; int sw; uint8_t level; };
static std::vector<Filtered> g_filtered;                 // every change of the ISR's filtered LS1/LS2 level
static uint8_t s_prevFiltered[2] = {2, 2};
static void tick() {
  g_simPinIn[P_LS1] = st.level(0, g_simUs);
  g_simPinIn[P_LS2] = st.level(1, g_simUs);
  g_simPinIn[P_HOME] = st.homeLevel();
  if (g_simIsr) g_simIsr();
  for (int i = 0; i < 2; i++) {
    uint8_t l = sim_probe_level(i);
    if (s_prevFiltered[i] != 2 && l != s_prevFiltered[i]) g_filtered.push_back({g_simUs, i, l});
    s_prevFiltered[i] = l;
  }
  g_simUs += 25;
}
static int filteredChanges(int sw, uint64_t t0, uint64_t t1) {
  int n = 0;
  for (const Filtered &f : g_filtered) if (f.sw == sw && f.us >= t0 && f.us < t1) n++;
  return n;
}
static void runUs(uint64_t us) {
  uint64_t end = g_simUs + us;
  while (g_simUs < end) {
    hostPoll();
    tick();
    if (g_simUs % 100 == 0) loop();
  }
}
static void runMs(double ms) { runUs((uint64_t)(ms * 1000)); }
static void boot(double x0) {
  st.x = x0; st.init();
  g_simPinIn[P_LS1] = st.level(0, 0); g_simPinIn[P_LS2] = st.level(1, 0); g_simPinIn[P_HOME] = st.homeLevel();
  setup();
  if (!g_simIsr || g_simIsrPeriodUs != 25) { printf("setup() did not start a 25 us step ISR\n"); exit(2); }
  runMs(20);
  if (!VALIDATOR) { sendLine("LOG 2"); runMs(3); }      // the station's setting: EVT DBG lines in the trace
}
static std::string upperWord(const std::string &c) {
  std::string w = c.substr(0, c.find(' '));
  for (auto &ch : w) ch = (char)toupper((unsigned char)ch);
  return w;
}
// Send one command, run 3 ms, return its OK/ERR reply.
static std::string cmd(const std::string &c, double waitMs = 3) {
  size_t mark = g_out.size();
  sendLine(c);
  runMs(waitMs);
  std::string w = upperWord(c);
  for (size_t k = mark; k < g_out.size(); k++) {
    const std::string &s = g_out[k].s;
    if (s.rfind("OK " + w, 0) == 0 || s.rfind("ERR " + w, 0) == 0) return s;
  }
  return "";
}
static std::string status(const std::string &key) {
  std::string r = cmd("STATUS");
  size_t p = r.find(" " + key + "=");
  if (p == std::string::npos) return "?";
  p += key.size() + 2;
  return r.substr(p, r.find(' ', p) - p);
}
// Hold the stick for ms, then release it and let the jog ramp down.
static void jog(int dir, double mm, double ms) {
  if (VALIDATOR) cmd("SPEED " + std::to_string(mm));
  host.stick = dir; host.stickMm = mm; host.stickUntil = g_simUs + (uint64_t)(ms * 1000); host.nextStick = g_simUs;
  runMs(ms + 400);
}
static void waitIdle(double maxMs);
static std::string probeJog(int dir) {
  std::string r = cmd(jogLine(dir, 0.5));
  cmd(VALIDATOR ? "JOG 0" : "JOGV 0");
  waitIdle(3000);
  return r;
}
static void waitIdle(double maxMs) {
  uint64_t end = g_simUs + (uint64_t)(maxMs * 1000);
  runMs(50);
  while (g_simUs < end && g_simUs - st.lastStepUs < 300000) runMs(50);
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
static std::string fmt(const char *f, double v) { char b[64]; snprintf(b, sizeof b, f, v); return b; }
static std::string crashTxt() {
  if (!st.lostSteps) return "no hard-stop contact";
  char b[120];
  snprintf(b, sizeof b, "hard stop hit at t=%.3f s, %ld steps lost (%.3f mm), contact speed %.3f mm/s",
           st.crashFirstUs / 1e6, st.lostSteps, st.lostSteps / st.spm(), st.crashVmax);
  return b;
}
static std::string limitErr() { return VALIDATOR ? "ERR JOG " : "ERR JOGV "; }

// ------------------------------------------------------------------ scenarios
// 1. R-4: parked on LS1 at power-up, jog off it with release chatter, then come back toward LS1.
static void s_r4_jogoff_chatter(bool backByMove) {
  boot(-0.1);
  expect(status("ls1") == "1" && status("ls1_end") == "0", "boot: LS1 pressed, end unknown");
  expect(has(cmd("ENABLE"), "OK ENABLE"), "ENABLE");
  uint64_t t0 = g_simUs; size_t m0 = g_out.size();
  jog(+1, 0.5, 2500);                                     // the safe way off (LS1 is the - end)
  expect(status("ls1") == "0", "jog + left LS1", "x=" + fmt("%.4f", st.x));
  expect(status("ls1_end") == "-1", "LS1 end learned as -1 (on release)", "ls1_end=" + status("ls1_end"));
  expect(filteredChanges(0, t0, g_simUs) >= 3, "the filter saw LS1 release, re-trip (chatter), release",
         std::to_string(filteredChanges(0, t0, g_simUs)) + " filtered changes");
  expect(findLine("LIMIT ls1", m0) == (size_t)-1, "release chatter did not halt the jog-off");
  expect(vmaxPressed(0, t0, g_simUs) <= 0.1001, "speed while LS1 pressed and its end unknown <= 0.1 mm/s",
         fmt("%.4f mm/s", vmaxPressed(0, t0, g_simUs)));
  size_t m1 = g_out.size();
  if (backByMove) { cmd("SPEED 0.5"); expect(has(cmd("MOVE -2"), "OK MOVE"), "MOVE -2 accepted (LS1 clear)"); waitIdle(8000); }
  else jog(-1, 0.5, 5000);
  expect(st.lostSteps == 0, "never reaches the - hard stop", crashTxt());
  expect(st.x <= 0.0 && st.x > -0.01, "halted at LS1's operating point", "x=" + fmt("%.4f", st.x));
  expect(findLine("LIMIT ls1 tripped", m1) != (size_t)-1 && has(g_out[findLine("LIMIT ls1 tripped", m1)].s, "end=-1"),
         "EVT LIMIT ls1 ... end=-1 on the way back");
  std::string r = probeJog(-1);
  expect(has(r, limitErr() + "limit-ls1"), "a jog further toward - is refused", r);
  expect(has(cmd("MOVE -1"), "ERR MOVE limit-ls1"), "MOVE -1 refused at LS1");
  r = probeJog(1);
  expect(has(r, VALIDATOR ? "OK JOG" : "OK JOGV"), "the safe way out (+) is allowed", r);
}

// 2. Normal first trip: from mid-travel to each end, learn on the trip, release chatter ignored afterwards.
static void s_first_trip() {
  double start = VALIDATOR ? 10.0 : 25.0, far = VALIDATOR ? 15.0 : 30.0;
  boot(start);
  cmd("ENABLE"); cmd("SPEED 2.5");
  size_t m0 = g_out.size();
  cmd("MOVE -" + std::to_string(far)); waitIdle(20000);
  expect(st.lostSteps == 0, "no hard-stop contact", crashTxt());
  expect(st.x <= 0.0 && st.x > -0.01, "halted at LS1 on the first trip", "x=" + fmt("%.4f", st.x));
  expect(status("ls1_end") == "-1", "LS1 learned as the - end", "ls1_end=" + status("ls1_end"));
  expect(countLines("LIMIT ls1 tripped", m0) == 1, "one EVT LIMIT ls1");
  expect(has(cmd("MOVE -1"), "ERR MOVE limit-ls1"), "MOVE -1 refused");
  size_t m1 = g_out.size();
  expect(has(cmd("MOVE 1"), "OK MOVE"), "MOVE +1 accepted (moves off LS1)"); waitIdle(5000);
  expect(findLine("LIMIT", m1) == (size_t)-1 && fabs(st.x - 1.0) < 0.01, "release chatter ignored: full 1 mm, no halt",
         "x=" + fmt("%.4f", st.x));
  expect(status("ls1_end") == "-1", "LS1 end unchanged by the release");
  cmd("MOVE -2"); waitIdle(5000);
  expect(st.x <= 0.0 && st.x > -0.01, "halts at LS1 again", "x=" + fmt("%.4f", st.x));
  for (int k = 0; k < 5 && status("ls2") != "1"; k++) { cmd(VALIDATOR ? "MOVE 15" : "MOVE 60"); waitIdle(30000); }
  expect(st.x >= 50.0 && st.x < 50.01, "halted at LS2 on its first trip", "x=" + fmt("%.4f", st.x));
  expect(status("ls2_end") == "1", "LS2 learned as the + end", "ls2_end=" + status("ls2_end"));
  expect(st.lostSteps == 0, "no hard-stop contact overall", crashTxt());
}

// 2b. TEST LIMITS and (station) HOME from mid-travel still work.
static void s_regress_tests() {
  boot(25.0);
  cmd("ENABLE");
  size_t m0 = g_out.size();
  expect(has(cmd("TEST LIMITS"), "OK TEST LIMITS started"), "TEST LIMITS starts");
  for (int k = 0; k < 400 && findLine("RESULT LIMITS", m0) == (size_t)-1; k++) runMs(500);
  size_t r = findLine("RESULT LIMITS", m0);
  expect(r != (size_t)-1 && has(g_out[r].s, "PASS"), "TEST LIMITS PASS", r == (size_t)-1 ? "no result" : g_out[r].s);
  expect(st.lostSteps == 0, "no hard-stop contact", crashTxt());
  if (!VALIDATOR) {
    size_t m1 = g_out.size();
    expect(has(cmd("HOME"), "OK HOME started"), "HOME starts");
    for (int k = 0; k < 400 && findLine("HOMED", m1) == (size_t)-1 && findLine("HOME FAIL", m1) == (size_t)-1; k++) runMs(500);
    expect(findLine("HOMED", m1) != (size_t)-1, "HOMED");
    expect(st.lostSteps == 0, "no hard-stop contact during HOME", crashTxt());
  }
}

// 3a. Both switches pressed at boot: nothing moves.
static void s_both_boot() {
  st.forced[0] = st.forced[1] = 1; st.forcedUntil[0] = st.forcedUntil[1] = (uint64_t)1e15;
  boot(25.0);
  cmd("ENABLE");
  expect(has(cmd("MOVE 1"), "ERR MOVE limits-both-tripped"), "MOVE refused");
  expect(has(cmd("MOVE -1"), "ERR MOVE limits-both-tripped"), "MOVE - refused");
  std::string r = probeJog(1);
  expect(has(r, "limits-both-tripped"), "jog refused", r);
  expect(has(cmd("TEST REVS 1"), "ERR TEST limit-tripped"), "TEST refused");
  if (!VALIDATOR) expect(has(cmd("HOME"), "ERR HOME limits-both-tripped"), "HOME refused");
  runMs(500);
  expect(st.log.empty(), "no step taken");
}

// 3b. Both switches read pressed mid-move (a short to GND): halt within the filter time.
static void s_both_midmove() {
  boot(25.0);
  cmd("ENABLE"); cmd("SPEED 2.5");
  cmd("MOVE 10");
  runMs(1000);
  uint64_t tf = g_simUs;
  st.forced[0] = st.forced[1] = 1; st.forcedFrom[0] = st.forcedFrom[1] = tf; st.forcedUntil[0] = st.forcedUntil[1] = (uint64_t)1e15;
  runMs(1000);
  expect(st.lastStepUs <= tf + 225, "no step later than 225 us after both read pressed",
         "last step at +" + std::to_string((long long)st.lastStepUs - (long long)tf) + " us");
  expect(findLine("LIMIT ls1 tripped") != (size_t)-1 && findLine("LIMIT ls2 tripped") != (size_t)-1, "EVT LIMIT for both");
  std::string r = probeJog(-1);
  expect(has(r, "limits-both-tripped"), "jog refused afterwards", r);
}

// 4. Parked on LS1 at power-up, driven further INTO it.
static void s_parked_into() {
  boot(-0.1);
  cmd("ENABLE");
  std::string mv = cmd("MOVE -1");
  expect(has(mv, "ERR MOVE limit-ls1-end-unknown"), "MOVE - refused while parked", mv);
  expect(has(cmd("MOVE 1"), "ERR MOVE limit-ls1-end-unknown"), "MOVE + refused while parked");
  expect(has(cmd("REVS 1"), "ERR REVS limit-ls1-end-unknown"), "REVS refused while parked");
  expect(has(cmd("TEST REVS 1"), "ERR TEST limit-tripped"), "TEST refused while parked");
  if (!VALIDATOR) {
    expect(has(cmd("MOVETO 5"), "ERR MOVETO limit-ls1-end-unknown"), "MOVETO refused while parked");
    expect(has(cmd("HOME"), "ERR HOME limit-ls1-end-unknown"), "HOME refused while parked");
    std::string r = cmd("JOGV -0.5"); cmd("JOGV 0");
    expect(has(r, "OK JOGV mm_s=-0.1000 clamped=1"), "JOGV capped at 0.1 mm/s while parked", r);
  }
  uint64_t t0 = g_simUs; size_t m0 = g_out.size();
  jog(-1, 0.5, 10000);                                    // INTO the switch: the operator holds the stick 10 s
  expect(st.lostSteps == 0, "never reaches the - hard stop", crashTxt());
  expect(vmaxPressed(0, t0, g_simUs) <= 0.1001, "speed into it <= 0.1 mm/s", fmt("%.4f mm/s", vmaxPressed(0, t0, g_simUs)));
  expect(st.x < -0.59 && st.x > -0.61, "halted 0.5 mm from where it was parked", "x=" + fmt("%.4f", st.x));
  size_t k = findLine("LIMIT ls1 tripped", m0);
  expect(k != (size_t)-1 && has(g_out[k].s, "end=-1") && has(g_out[k].s, "learned=travel"),
         "EVT LIMIT ls1 tripped ... end=-1 learned=travel", k == (size_t)-1 ? "none" : g_out[k].s);
  std::string r = probeJog(-1);
  expect(has(r, limitErr() + "limit-ls1"), "once learned, a jog further in is refused", r);
  expect(has(cmd("MOVE 1"), "ERR MOVE limit-ls1-end-unknown"), "MOVE still refused until LS1 releases");
  uint64_t t1 = g_simUs;
  jog(+1, 0.5, 8000);                                     // now off it: 0.65 mm at the 0.1 mm/s cap
  expect(status("ls1") == "0" && status("ls1_end") == "-1", "jogged off; LS1 end confirmed -1 on release",
         "x=" + fmt("%.4f", st.x));
  expect(vmaxPressed(0, t1, g_simUs) <= 0.1001, "speed off it <= 0.1 mm/s while pressed");
  cmd("SPEED 2.5"); cmd("MOVE -2"); waitIdle(5000);
  expect(st.x <= 0.0 && st.x > -0.01, "a fast MOVE back halts at LS1", "x=" + fmt("%.4f", st.x));
  expect(has(cmd("MOVE -1"), "ERR MOVE limit-ls1"), "and MOVE - is refused there");
  expect(st.lostSteps == 0, "no hard-stop contact overall", crashTxt());
}

// 4b. Parked deep in the overtravel (0.1 mm from the hard stop): the release lies 0.75 mm away, beyond the 0.5 mm
// budget. Shows what the provisional budget does when it is smaller than the overtravel: a wrong learn, but only a
// slow stall and then the axis is confined.
static void s_parked_deep() {
  boot(-0.7);
  cmd("ENABLE");
  uint64_t t0 = g_simUs; size_t m0 = g_out.size();
  jog(+1, 0.5, 8000);
  size_t k = findLine("LIMIT ls1 tripped", m0);
  expect(k != (size_t)-1 && has(g_out[k].s, "end=+1") && has(g_out[k].s, "learned=travel"),
         "budget spent moving + without a release: learned +1 (wrong here; budget < overtravel)",
         k == (size_t)-1 ? "none" : g_out[k].s);
  size_t m1 = g_out.size();
  jog(-1, 0.5, 15000);
  expect(st.crashVmax <= 0.1001, "hard-stop contact only at <= 0.1 mm/s", crashTxt());
  expect(st.lostSteps <= (long)(0.5 * st.spm()) + 1, "at most 0.5 mm of steps against the hard stop", crashTxt());
  k = findLine("LIMIT ls1 tripped", m1);
  expect(k != (size_t)-1 && has(g_out[k].s, "pressed_both_ways=1"), "EVT LIMIT ... pressed_both_ways=1",
         k == (size_t)-1 ? "none" : g_out[k].s);
  std::string a = probeJog(-1);
  std::string b = probeJog(1);
  expect(has(a, "limit-ls1") && has(b, "limit-ls1"), "confined: jogs both ways refused", a + " | " + b);
  (void)t0;
}

// 4c. Overtravel smaller than the budget (hard stop 0.3 mm past LS1): an into-jog touches the hard stop slowly.
static void s_parked_short_overtravel() {
  st.ovt = 0.3;
  boot(-0.1);
  cmd("ENABLE");
  size_t m0 = g_out.size();
  jog(-1, 0.5, 10000);
  expect(st.crashVmax <= 0.1001, "hard-stop contact only at <= 0.1 mm/s", crashTxt());
  size_t k = findLine("LIMIT ls1 tripped", m0);
  expect(k != (size_t)-1 && has(g_out[k].s, "end=-1"), "halted after the budget, learned -1",
         k == (size_t)-1 ? "none" : g_out[k].s);
  jog(+1, 0.5, 8000);
  expect(status("ls1") == "0" && status("ls1_end") == "-1", "jogs off, end confirmed -1");
}

// 5. Host silent after a host-timeout stop: outputs off ~10 s later; ENABLE needed again.
static void s_host_timeout(bool hostReturns) {
  boot(25.0);
  cmd("ENABLE");
  if (!VALIDATOR) cmd("HOSTTIMEOUT 1000");
  cmd("SPEED 0.5"); cmd("MOVE 10");
  host.hb = false;                                        // the host goes silent (port stays open)
  size_t m0 = g_out.size();
  runMs(VALIDATOR ? 3000 : 1500);
  size_t f = findLine("FAULT host-timeout", m0);
  expect(f != (size_t)-1, "EVT FAULT host-timeout");
  uint64_t tf = f == (size_t)-1 ? g_simUs : g_out[f].us;
  if (hostReturns) { runUs(tf + 5000000 - g_simUs); cmd(VALIDATOR ? "PING" : "HB"); }
  runUs(tf + 9800000 - g_simUs);
  expect(g_simPinOut[P_EN] == 0, "still energized (holding) 9.8 s after the fault");
  runUs(tf + 10300000 - g_simUs);
  size_t d = findLine("FAULT host-timeout-disabled", m0);
  if (hostReturns) {
    runMs(10000);
    expect(findLine("FAULT host-timeout-disabled", m0) == (size_t)-1 && g_simPinOut[P_EN] == 0,
           "host spoke within 10 s: stays energized, no disable");
    return;
  }
  expect(d != (size_t)-1, "EVT FAULT host-timeout-disabled",
         d == (size_t)-1 ? "none" : g_out[d].s + " at +" + fmt("%.3f s", (g_out[d].us - tf) / 1e6));
  expect(g_simPinOut[P_EN] == 1, "outputs off (EN high) 10.3 s after the fault");
  host.hb = true;
  expect(has(cmd("MOVE 1"), "ERR MOVE not-enabled"), "MOVE refused until ENABLE");
  expect(has(cmd("ENABLE"), "OK ENABLE enabled=1"), "ENABLE again");
  expect(has(cmd("MOVE 1"), "OK MOVE"), "MOVE accepted after ENABLE");
}

// 5c. DTR drop is unchanged: outputs off at once.
static void s_dtr_drop() {
  boot(25.0);
  cmd("ENABLE"); cmd("MOVE 10");
  runMs(500);
  g_simDtr = false;
  runMs(5);
  expect(g_simPinOut[P_EN] == 1, "DTR drop: outputs off within 5 ms");
  runMs(15000);
  expect(findLine("host-timeout") == (size_t)-1, "no host-timeout line after a DTR drop");
}

// 6. The switch releases at rest (hand nudge or the jog's last step), then re-reads pressed for 300 us just after the
// next move away starts: must not teach the wrong end.
static void s_release_at_rest() {
  st.forced[0] = 1; st.forcedFrom[0] = 0; st.forcedUntil[0] = 2000000;   // parked on LS1 until it releases at rest at 2 s
  boot(0.06);
  runMs(2500);
  expect(status("ls1") == "0" && status("ls1_end") == "0", "released at rest: clear, end unknown");
  cmd("ENABLE"); cmd("SPEED 0.5");
  cmd("MOVE 5");
  runMs(50);
  st.forced[0] = 1; st.forcedFrom[0] = g_simUs; st.forcedUntil[0] = g_simUs + 300;   // chatter while moving away
  waitIdle(15000);
  cmd("MOVE 5"); waitIdle(15000);                         // whatever happened, carry on away from LS1
  expect(status("ls1_end") != "1", "LS1 not learned as + from the chatter", "ls1_end=" + status("ls1_end"));
  cmd("SPEED 0.5"); cmd("MOVE -15"); waitIdle(40000);     // back toward LS1 (validator: within its 15 mm clamp)
  expect(st.lostSteps == 0, "the move back never reaches the - hard stop", crashTxt());
  expect(st.x <= 0.0 && st.x > -0.01, "halted at LS1", "x=" + fmt("%.4f", st.x));
  if (status("ls1_end") != "-1") {                        // stopped without learning: jog off it to learn
    jog(+1, 0.5, 3000);
    expect(status("ls1_end") == "-1", "jogging off LS1 teaches -1", "ls1_end=" + status("ls1_end"));
  }
  expect(st.lostSteps == 0, "no hard-stop contact overall", crashTxt());
}

// 4d. ZERO and MICROSTEPS while parked keep the parked travel window on the same carriage positions.
static void s_parked_rebase() {
  boot(-0.1);
  cmd("ENABLE");
  jog(-1, 0.5, 3000);                                     // 0.3 mm into it (capped at 0.1 mm/s)
  expect(st.x < -0.35 && st.x > -0.45, "jogged ~0.3 mm into it", "x=" + fmt("%.4f", st.x));
  expect(has(cmd("ZERO"), "OK ZERO"), "ZERO while parked");
  expect(has(cmd("MICROSTEPS 16"), "OK MICROSTEPS microsteps=16"), "MICROSTEPS 16 while parked");
  uint64_t t0 = g_simUs; size_t m0 = g_out.size();
  jog(-1, 0.5, 10000);
  expect(st.lostSteps == 0, "never reaches the - hard stop", crashTxt());
  expect(st.x < -0.59 && st.x > -0.61, "still halted 0.5 mm from where it was parked (budget not reset)",
         "x=" + fmt("%.4f", st.x));
  expect(vmaxPressed(0, t0, g_simUs) <= 0.1001, "still <= 0.1 mm/s at 16 microsteps", fmt("%.4f mm/s", vmaxPressed(0, t0, g_simUs)));
  size_t k = findLine("learned=travel", m0);
  expect(k != (size_t)-1, "EVT LIMIT ... learned=travel");
}

// 7. The station's vector Step (co-arrival). Each axis is its own board, so one board runs the X leg, then the Y leg,
// of one short diagonal Step; each leg's steps are kept relative to its command time and start position, and the two
// are laid side by side as the stage would run them. MOVE <mm> <mm_s> scales the speed only: every axis ramps at the
// board's ACCEL, so the shorter leg finishes first and the path bows. MOVE <mm> <mm_s> <mm_s2> with a_i = |d_i|/|d| x
// ACCEL scales the whole trapezoid. What is left is AccelStepper's own ramp start (its first step goes at once and its
// c0 is 0.676 sqrt(2/a)): a time lead of a few c0 per leg, larger on the gentler leg, that does not grow with the move
// (0.6 x 0.2 mm: 421 -> 41 ms apart and 96 -> 8 um off the line at 2.5 mm/s; 128 -> 23 ms and 10 -> 3 um at 0.5 mm/s).
struct Leg { std::vector<std::pair<double, double>> pts; double mm = 0; std::string reply; };
static Leg runLeg(const std::string &c) {
  size_t k0 = st.log.size(); uint64_t t0 = g_simUs; double x0 = st.x;
  Leg leg; leg.reply = cmd(c);
  waitIdle(10000);
  for (size_t k = k0; k < st.log.size(); k++) leg.pts.push_back({(st.log[k].us - t0) / 1e6, st.log[k].x - x0});
  leg.mm = st.x - x0;
  return leg;
}
static double legEnd(const Leg &l) { return l.pts.empty() ? 0 : l.pts.back().first; }
static double legAt(const Leg &l, double t) {          // position at t: the last step taken at or before it
  double x = 0;
  for (const auto &p : l.pts) { if (p.first > t) break; x = p.second; }
  return x;
}
// Largest distance (mm) from the straight line of the Step (dx, dy) over every step of either leg.
static double pathError(const Leg &a, const Leg &b, double dx, double dy) {
  double len = sqrt(dx * dx + dy * dy), worst = 0;
  std::vector<double> ts;
  for (const auto &p : a.pts) ts.push_back(p.first);
  for (const auto &p : b.pts) ts.push_back(p.first);
  for (double t : ts) worst = fmax(worst, fabs(legAt(a, t) * dy - legAt(b, t) * dx) / len);
  return worst;
}
// One diagonal Step (dx, dy) at vector speed v, board ACCEL a: the old pair, then the new pair.
static void coarrival(double dx, double dy, double v, double a) {
  const double len = sqrt(dx * dx + dy * dy), kx = dx / len, ky = dy / len;
  char b[96];
  std::string at = fmt(" (%.1f mm/s)", v);
  snprintf(b, sizeof b, "MOVE %.4f %.4f", dx, kx * v); Leg ox = runLeg(b);
  snprintf(b, sizeof b, "MOVE %.4f %.4f", dy, ky * v); Leg oy = runLeg(b);
  snprintf(b, sizeof b, "MOVE %.4f %.4f %.4f", dx, kx * v, kx * a); Leg nx = runLeg(b);
  snprintf(b, sizeof b, "MOVE %.4f %.4f %.4f", dy, ky * v, ky * a); Leg ny = runLeg(b);
  double oldGap = fabs(legEnd(ox) - legEnd(oy)), oldErr = pathError(ox, oy, dx, dy);
  double gap = fabs(legEnd(nx) - legEnd(ny)), err = pathError(nx, ny, dx, dy);
  double c0 = 0.676 * sqrt(2.0 / (ky * a * st.spm()));   // AccelStepper's first interval on the gentler leg, s
  expect(oldGap > 0.1, "old MOVE: the legs finish apart" + at, fmt("%.1f ms", oldGap * 1e3));
  expect(gap <= 2 * c0, "new MOVE: the legs finish within two c0 of the gentler leg (AccelStepper's ramp start)" + at,
         fmt("%.1f ms apart", gap * 1e3) + fmt(" (old %.1f ms", oldGap * 1e3) + fmt(", c0 %.1f ms)", c0 * 1e3));
  expect(err < 0.5 * oldErr, "new MOVE: the path keeps closer to the line" + at,
         fmt("%.2f um off it", err * 1e3) + fmt(" (old %.2f um)", oldErr * 1e3));
  expect(has(nx.reply, "OK MOVE") && has(nx.reply, fmt(" accel_mm_s2=%.4f accel_clamped=0", kx * a)),
         "OK MOVE echoes the move's ACCEL" + at, nx.reply);
  expect(fabs(nx.mm - dx) < 1e-6 && fabs(ny.mm - dy) < 1e-6, "both legs travel their whole distance" + at,
         fmt("x %.5f", nx.mm) + fmt(" y %.5f", ny.mm));
}
static void s_move_accel_coarrival() {
  boot(25.0);
  cmd("ENABLE");
  char b[96];
  Leg ref = runLeg("MOVE 0.3 2.5");                       // a two-argument MOVE before any per-move ACCEL
  coarrival(0.6, 0.2, 2.5, 2.5);                          // short, full speed: a triangle at the boot ACCEL
  coarrival(0.6, 0.2, 0.5, 2.5);                          // the same at the station's default speed: a trapezoid
  // The per-move ACCEL is that move's only: the board's comes back once it has stopped, by arrival or by STOP.
  Leg again = runLeg("MOVE 0.3 2.5");
  expect(fabs(legEnd(again) - legEnd(ref)) < 1e-3, "after it, a two-argument MOVE ramps at ACCEL again",
         fmt("%.2f ms", legEnd(again) * 1e3) + fmt(" vs %.2f ms", legEnd(ref) * 1e3));
  expect(again.reply.find("accel") == std::string::npos, "and its reply is the two-argument one", again.reply);
  cmd("MOVE 5 0.5 0.25"); runMs(500);
  expect(has(cmd("STOP"), "OK STOP"), "STOP during a per-move ACCEL move"); waitIdle(10000);
  again = runLeg("MOVE 0.3 2.5");
  expect(fabs(legEnd(again) - legEnd(ref)) < 1e-3, "after a STOP, too", fmt("%.2f ms", legEnd(again) * 1e3));
  expect(has(cmd("INFO"), " accel_mm_s2=2.5000 "), "INFO: the board's ACCEL is unchanged");
  // Bounds and refusals, as for the speed argument.
  std::string r = cmd("MOVE 0.01 0.5 100"); waitIdle(3000);
  expect(has(r, "accel_mm_s2=25.0000 accel_clamped=1"), "clamped to MAX_ACCEL_MM", r);
  snprintf(b, sizeof b, "MOVETO %.4f 0.5 0.01", atof(status("pos_mm").c_str()) + 0.2);
  r = cmd(b); waitIdle(10000);
  expect(has(r, "OK MOVETO") && has(r, "accel_mm_s2=0.2500 accel_clamped=1"), "MOVETO takes it too, clamped to MIN_ACCEL_MM", r);
  for (const char *bad : {"MOVE 1 0.5 0", "MOVE 1 0.5 -2", "MOVE 1 0.5 fast", "MOVETO 3 0.5 nan"}) {
    size_t k0 = st.log.size();
    r = cmd(bad); runMs(200);
    std::string word = upperWord(bad);
    expect(r == "ERR " + word + " bad-accel" && st.log.size() == k0, std::string(bad) + ": refused, nothing moves", r);
  }
  expect(st.lostSteps == 0, "no hard-stop contact", crashTxt());
}

struct Scenario { const char *name; void (*fn)(); bool stationOnly; };
static void s1() { s_r4_jogoff_chatter(false); }
static void s1b() { s_r4_jogoff_chatter(true); }
static void s5() { s_host_timeout(false); }
static void s5b() { s_host_timeout(true); }
static const Scenario SCENARIOS[] = {
  {"r4-jogoff-chatter-then-jog-back", s1, false},
  {"r4-jogoff-chatter-then-move-back", s1b, false},
  {"first-trip-learns", s_first_trip, false},
  {"regress-test-limits-home", s_regress_tests, false},
  {"both-tripped-at-boot", s_both_boot, false},
  {"both-tripped-mid-move", s_both_midmove, false},
  {"parked-driven-into", s_parked_into, false},
  {"parked-deep-budget-short", s_parked_deep, false},
  {"parked-overtravel-short", s_parked_short_overtravel, false},
  {"parked-zero-microsteps-keep-window", s_parked_rebase, false},
  {"host-timeout-disables", s5, false},
  {"host-timeout-host-returns", s5b, false},
  {"dtr-drop-unchanged", s_dtr_drop, false},
  {"release-at-rest-then-chatter", s_release_at_rest, false},
  {"move-accel-coarrival", s_move_accel_coarrival, true},
};

int main(int argc, char **argv) {
  if (argc < 2 || !strcmp(argv[1], "list")) {
    for (const Scenario &s : SCENARIOS) if (!(s.stationOnly && VALIDATOR)) printf("%s\n", s.name);
    return 0;
  }
  if (argc > 2 && !strcmp(argv[2], "-q")) g_verbose = false;
  st.nc = VALIDATOR;                                      // the validator boots LIMITS NC; the station boots NO
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
