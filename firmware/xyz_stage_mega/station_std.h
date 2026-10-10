// station_std.h -- the Probe family's standard extension layer, protocol 1 (docs/rebuild/MEGA_STANDARD.md §2-§4).
// Written for the XYZ Mega (firmware/xyz_stage_mega) and meant to be copied byte for byte into each Mega sketch folder
// in phase 2 (MEGA_STANDARD §6, "Retrofit"); a copy is never edited on its own.
//
// What it owns: the identity reply with caps=, the '#' command set INFO, LOG, HOSTTIMEOUT, HB, AXISCFG, STOP (and
// HOME and ZERO when the sketch has the home feature, SOFTLIMIT when it has the soft travel limit), #OK/#ERR/#EVT output
// with the LOG levels, the host timeout and its 10 s disable, and the AXISCFG and SOFTLIMIT blocks in EEPROM. What it
// does not own: motion, the frame protocol, interlocks and drivers. It reaches those through the sk*() hooks the sketch
// defines (declared below).
//
// Before including it the sketch defines:
//   STD_IDENTITY     identity letter, e.g. 'm'             STD_FW          sketch name, e.g. "xyz_stage_mega"
//   STD_NAXES        number of axes, 1..3                  STD_AXES        their letters, e.g. "XYZ"
//   STD_CAP_HOME     1: firmware has HOME and ZERO         STD_CAP_LIMITS  1: firmware has the limit interlock
//   STD_CAP_TMC      1: firmware reads its drivers over UART
//   STD_CAP_SOFT     1: firmware has the soft travel limit (#SOFTLIMIT; MEGA_STANDARD §4, 2026-10-10)
//   STD_EEPROM_ADDR  first byte of the AXISCFG block (1 + 2 x STD_NAXES bytes); with STD_CAP_SOFT the SOFTLIMIT block
//                    follows it (1 + 8 x STD_NAXES bytes)
//
// Rules this layer keeps (MEGA_STANDARD §3): nothing starting with '#' goes out until the host has sent a '#' command
// this firmware knows (std_ext), so a host speaking only the frame protocol sees exactly the old bytes; every '#'
// command gets exactly one #OK or #ERR line; loop() context only, never from an ISR.
#pragma once
#include <Arduino.h>
#include <EEPROM.h>
#include <avr/pgmspace.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <ctype.h>
#include <stdlib.h>

#if !defined(STD_IDENTITY) || !defined(STD_FW) || !defined(STD_NAXES) || !defined(STD_AXES) || \
    !defined(STD_CAP_HOME) || !defined(STD_CAP_LIMITS) || !defined(STD_CAP_TMC) || !defined(STD_CAP_SOFT) || \
    !defined(STD_EEPROM_ADDR)
#error "station_std.h: define STD_IDENTITY, STD_FW, STD_NAXES, STD_AXES, STD_CAP_* and STD_EEPROM_ADDR first"
#endif

// -Wformat checks the host build's formats; avr-gcc cannot check PSTR() formats, so the attribute is host-only.
#ifdef __AVR__
#define STD_PRINTF(f, a)
#else
#define STD_PRINTF(f, a) __attribute__((format(printf, f, a)))
#endif

// ---------------------------------------------------------------- constants
#define STD_PROTO 1
#define STD_LINE_MAX 80                       // one input line (a frame or a '#' command), NUL included
#define STD_HOST_TIMEOUT_MIN_MS 250           // #HOSTTIMEOUT clamp (MEGA_STANDARD §3)
#define STD_HOST_TIMEOUT_MAX_MS 5000
#define STD_HOST_SILENT_OFF_MS 10000UL        // after #EVT FAULT host-timeout: outputs off if still silent this long
#define STD_EXT_GAP_MS 50                     // a '#' line with no byte for this long is dropped (a desynced stream)
#define STD_CFG_MAGIC 0x5A                    // AXISCFG block: magic, one byte per axis, then each byte's complement
#define STD_CFG_LIMITS 0x01
#define STD_CFG_HOME 0x02
#define STD_SOFT_ADDR (STD_EEPROM_ADDR + 1 + 2 * STD_NAXES)   // SOFTLIMIT block: magic, counts per axis (4 bytes, LE),
#define STD_SOFT_MAGIC 0x5D                                    // then each value's complement
#define STD_SOFT_MAX 160000L                  // #SOFTLIMIT's ceiling, counts (100 mm: twice the stage's travel)

#if STD_CAP_HOME
#define STD_CAPS_HOME_ ",home"
#else
#define STD_CAPS_HOME_ ""
#endif
#if STD_CAP_LIMITS
#define STD_CAPS_LIMITS_ ",limits"
#else
#define STD_CAPS_LIMITS_ ""
#endif
#if STD_CAP_TMC
#define STD_CAPS_TMC_ ",tmc"
#else
#define STD_CAPS_TMC_ ""
#endif
#if STD_CAP_SOFT
#define STD_CAPS_SOFT_ ",soft"
#else
#define STD_CAPS_SOFT_ ""
#endif
// A token is listed only when the firmware has the feature (MEGA_STANDARD §2); hardware presence is #INFO's.
#define STD_CAPS "ext1,log,hostto" STD_CAPS_HOME_ STD_CAPS_LIMITS_ STD_CAPS_TMC_ STD_CAPS_SOFT_

// ---------------------------------------------------------------- hooks the sketch defines
bool skBusy();                         // anything moving, homing or stepping (arms the host timeout)
bool skEnabled();                      // the drivers are on
void skHostTimeoutStop();              // host silent while busy: stop every axis, keep the coils holding
void skHostTimeoutDisable();           // still silent STD_HOST_SILENT_OFF_MS later: outputs off, 'e' needed again
void skStop();                         // #STOP: the same effect as the stop jog packet
bool skAxisCfgBusy();                  // #AXISCFG is refused while this is true
void skAxisCfgApply(uint8_t axis);     // the axis's AXISCFG bits changed: re-arm its features
uint8_t skTmc(uint8_t axis);           // 1: a driver answered on this axis
uint8_t skLimitsOn(uint8_t axis);      // 1: interlock armed (AXISCFG limits=1, or seen this session)
int8_t skLsEnd(uint8_t axis, uint8_t sw);   // the end switch sw (0 = LS1, 1 = LS2) guards: -1, 0 (unknown), +1
uint8_t skHomed(uint8_t axis);
PGM_P skHomeCheck(uint8_t axis);       // why #HOME is refused (a PSTR), or nullptr
void skHomeStart(uint8_t axis);        // called right after "#OK HOME started"
PGM_P skZero(uint8_t axis);            // #ZERO: nullptr when done, else why it was refused
void skExtOpened();                    // the host has just spoken ext1: flush events held back until now
void skInfoExtra(char *buf, size_t n); // sketch-specific INFO keys, appended last (" k=v ...")
#if STD_CAP_SOFT
void skSoftApply(uint8_t axis);        // the axis's SOFTLIMIT changed (std_soft, std_softBad): re-arm its limit
uint8_t skSoftRef(uint8_t axis, int32_t &ls1, int32_t &lim);   // 1: LS1 has given the reference (ls1, lim filled)
#endif

// ---------------------------------------------------------------- state
static bool     std_ext = false;            // the host has sent a '#' command this firmware knows
static uint8_t  std_logLevel = 1;           // #LOG: 0 essential events only, 1 every #EVT, 2 also #EVT DBG
static uint16_t std_hostToMs = 0;           // #HOSTTIMEOUT; 0 = never armed: no host timeout (old-board behaviour)
static uint32_t std_lastRxMs = 0;           // last byte from the host, any byte
static bool     std_hostTimedOut = false;   // #EVT FAULT host-timeout stopped motion; any byte clears it
static uint32_t std_hostTimedOutMs = 0;
static uint8_t  std_cfg[STD_NAXES];         // AXISCFG from EEPROM: STD_CFG_LIMITS | STD_CFG_HOME per axis
#if STD_CAP_SOFT
static int32_t  std_soft[STD_NAXES];        // SOFTLIMIT from EEPROM, counts from LS1; 0 = no limit
static uint8_t  std_softBad = 0;            // bit a: axis a's SOFTLIMIT entry is damaged (jogs only until rewritten)
#endif

struct StdLine { char buf[STD_LINE_MAX]; uint8_t len; bool overflow; };
static StdLine  std_line;                   // the one input line being read (a frame or a '#' command)
static char     std_out[160];               // formatting buffer for output lines (loop() only); a HOME FAIL is ~135

// ---------------------------------------------------------------- small helpers
static inline char stdAxisLetter(uint8_t a) { return STD_AXES[a]; }
static inline int8_t stdAxisIndex(const char *s) {          // "X" -> 0 ...; -1 if not one of STD_AXES
  if (!s || !s[0] || s[1]) return -1;
  char c = (char)toupper((unsigned char)s[0]);
  for (uint8_t a = 0; a < STD_NAXES; a++) if (STD_AXES[a] == c) return (int8_t)a;
  return -1;
}
static inline void stdUpper(char *s) { for (; *s; s++) *s = (char)toupper((unsigned char)*s); }
static inline void stdNl() { Serial.print('\r'); Serial.print('\n'); }   // println's line end, as POS and DEV use
static inline bool stdParseLong(const char *s, long &out) {
  if (!s || !*s) return false;
  char *e; long v = strtol(s, &e, 10);
  if (*e) return false;
  out = v; return true;
}

// Host activity: every byte the sketch reads from the host goes through here (MEGA_STANDARD §3: any byte counts).
static inline int stdRead(uint32_t now) {
  int c = Serial.read();
  if (c >= 0) { std_lastRxMs = now; std_hostTimedOut = false; }
  return c;
}

// ---------------------------------------------------------------- input line
static inline void stdLineReset() { std_line.len = 0; std_line.overflow = false; std_line.buf[0] = 0; }
// One byte of a line; true once '\n' ends it. The buffer is then NUL-terminated (a trailing '\r' kept for the caller).
static inline bool stdLineFeed(char c) {
  if (c == '\n') { std_line.buf[std_line.len] = 0; return true; }
  if (std_line.len < STD_LINE_MAX - 1) std_line.buf[std_line.len++] = c; else std_line.overflow = true;
  return false;
}

// ---------------------------------------------------------------- output
// "#OK <CMD>[ <body>]": cmd is the received command word (RAM), body a PSTR format.
static void stdOk(const char *cmd, PGM_P fmt, ...) STD_PRINTF(2, 3);
static void stdOk(const char *cmd, PGM_P fmt, ...) {
  va_list ap; va_start(ap, fmt); vsnprintf_P(std_out, sizeof std_out, fmt, ap); va_end(ap);
  Serial.print(F("#OK ")); Serial.print(cmd);
  if (std_out[0]) { Serial.print(' '); Serial.print(std_out); }
  stdNl();
}
static void stdOkBare(const char *cmd) { Serial.print(F("#OK ")); Serial.print(cmd); stdNl(); }   // no k=v (an empty format trips -Wformat)
static void stdErr(const char *cmd, PGM_P reason) {
  Serial.print(F("#ERR ")); Serial.print(cmd); Serial.print(' ');
  Serial.print((const __FlashStringHelper *)reason); stdNl();
}
// Unsolicited "#EVT <body>". cls 0 = essential (printed at every LOG level), 1 = ordinary (LOG >= 1). Nothing is
// printed before the host has spoken ext1.
static void stdEvt(uint8_t cls, PGM_P fmt, ...) STD_PRINTF(2, 3);
static void stdEvt(uint8_t cls, PGM_P fmt, ...) {
  if (!std_ext || cls > std_logLevel) return;
  va_list ap; va_start(ap, fmt); vsnprintf_P(std_out, sizeof std_out, fmt, ap); va_end(ap);
  Serial.print(F("#EVT ")); Serial.print(std_out); stdNl();
}
// "#EVT DBG <body>" at LOG 2.
static void stdDbg(PGM_P fmt, ...) STD_PRINTF(1, 2);
static void stdDbg(PGM_P fmt, ...) {
  if (!std_ext || std_logLevel < 2) return;
  va_list ap; va_start(ap, fmt); vsnprintf_P(std_out, sizeof std_out, fmt, ap); va_end(ap);
  Serial.print(F("#EVT DBG ")); Serial.print(std_out); stdNl();
}
// A PSTR copied to RAM for a "%s" argument (avr-libc's %S has no host equivalent). Two slots, used round robin.
static const char *stdP(PGM_P s) {
  static char slot[2][44]; static uint8_t k = 0;
  k ^= 1;
  strncpy_P(slot[k], s, sizeof slot[k] - 1); slot[k][sizeof slot[k] - 1] = 0;
  return slot[k];
}
// "+1", "-1" or "0" (MEGA_STANDARD writes ends as -1/0/+1).
static inline const char *stdEnd(int8_t e) { return e > 0 ? "+1" : e < 0 ? "-1" : "0"; }

// The identity reply: "DEV: <letter> caps=<tokens>", println's line end like the old "DEV: s".
static void stdIdentity() {
  Serial.print(F("DEV: ")); Serial.print(STD_IDENTITY); Serial.print(F(" caps=" STD_CAPS)); stdNl();
}

// ---------------------------------------------------------------- AXISCFG in EEPROM
// Layout at STD_EEPROM_ADDR: STD_CFG_MAGIC, cfg[0..n-1], ~cfg[0..n-1]. An axis whose byte and complement disagree, or
// a block without the magic (a new board reads 0xFF), is "absent hardware": limits=0 home=0, every feature inert.
static void stdCfgLoad() {
  bool magic = EEPROM.read(STD_EEPROM_ADDR) == STD_CFG_MAGIC;
  for (uint8_t a = 0; a < STD_NAXES; a++) {
    uint8_t v = EEPROM.read(STD_EEPROM_ADDR + 1 + a), c = EEPROM.read(STD_EEPROM_ADDR + 1 + STD_NAXES + a);
    std_cfg[a] = (magic && (uint8_t)~v == c) ? (uint8_t)(v & (STD_CFG_LIMITS | STD_CFG_HOME)) : 0;
  }
}
static bool stdCfgStore(uint8_t a, uint8_t v) {
  EEPROM.update(STD_EEPROM_ADDR + 1 + a, v);
  EEPROM.update(STD_EEPROM_ADDR + 1 + STD_NAXES + a, (uint8_t)~v);
  EEPROM.update(STD_EEPROM_ADDR, STD_CFG_MAGIC);
  uint8_t keep[STD_NAXES];
  memcpy(keep, std_cfg, sizeof keep);
  stdCfgLoad();                                            // read back, never assume
  bool ok = std_cfg[a] == v;
  if (!ok) memcpy(std_cfg, keep, sizeof keep);
  return ok;
}
static inline bool stdCfgLimits(uint8_t a) { return std_cfg[a] & STD_CFG_LIMITS; }
static inline bool stdCfgHome(uint8_t a) { return std_cfg[a] & STD_CFG_HOME; }

#if STD_CAP_SOFT
// ---------------------------------------------------------------- SOFTLIMIT in EEPROM (MEGA_STANDARD §4)
// Layout at STD_SOFT_ADDR: STD_SOFT_MAGIC, counts[0..n-1] (4 bytes each, little-endian), ~counts[0..n-1]. An erased
// block (every byte 0xFF: never written) is no limit on any axis. Otherwise an entry whose complement disagrees, or out
// of range, or a block without the magic, is DAMAGED, not "no limit": the owner's limit is unknown, so that axis moves
// only by jog until #SOFTLIMIT writes it again (the opposite of AXISCFG's absent-is-inert, on purpose: fail safe).
static uint32_t stdSoftWord(uint16_t at) {
  uint32_t v = 0;
  for (uint8_t i = 0; i < 4; i++) v |= (uint32_t)EEPROM.read(at + i) << (8 * i);
  return v;
}
static void stdSoftLoad() {
  bool erased = true;
  for (uint16_t i = 0; i < 1 + 8 * STD_NAXES; i++) if (EEPROM.read(STD_SOFT_ADDR + i) != 0xFF) { erased = false; break; }
  bool magic = EEPROM.read(STD_SOFT_ADDR) == STD_SOFT_MAGIC;
  std_softBad = 0;
  for (uint8_t a = 0; a < STD_NAXES; a++) {
    uint32_t v = stdSoftWord(STD_SOFT_ADDR + 1 + 4 * a), c = stdSoftWord(STD_SOFT_ADDR + 1 + 4 * (STD_NAXES + a));
    bool good = magic && c == ~v && v <= (uint32_t)STD_SOFT_MAX;
    std_soft[a] = good ? (int32_t)v : 0;
    if (!good && !erased) std_softBad |= (uint8_t)(1u << a);
  }
}
static bool stdSoftStore(uint8_t a, int32_t v) {
  for (uint8_t i = 0; i < 4; i++) {
    EEPROM.update(STD_SOFT_ADDR + 1 + 4 * a + i, (uint8_t)((uint32_t)v >> (8 * i)));
    EEPROM.update(STD_SOFT_ADDR + 1 + 4 * (STD_NAXES + a) + i, (uint8_t)(~(uint32_t)v >> (8 * i)));
  }
  bool fresh = EEPROM.read(STD_SOFT_ADDR) != STD_SOFT_MAGIC;
  EEPROM.update(STD_SOFT_ADDR, STD_SOFT_MAGIC);
  if (fresh)                                               // the first write: the other axes' entries become "none"
    for (uint8_t b = 0; b < STD_NAXES; b++)
      if (b != a && stdSoftWord(STD_SOFT_ADDR + 1 + 4 * (STD_NAXES + b)) != ~stdSoftWord(STD_SOFT_ADDR + 1 + 4 * b))
        for (uint8_t i = 0; i < 4; i++) {
          EEPROM.update(STD_SOFT_ADDR + 1 + 4 * b + i, 0);
          EEPROM.update(STD_SOFT_ADDR + 1 + 4 * (STD_NAXES + b) + i, 0xFF);
        }
  stdSoftLoad();                                           // read back, never assume
  return !(std_softBad & (1u << a)) && std_soft[a] == v;
}
#endif

// ---------------------------------------------------------------- host timeout (MEGA_STANDARD §4)
// Armed by #HOSTTIMEOUT only. No byte for that window while busy: stop every axis, #EVT FAULT host-timeout. Still
// silent STD_HOST_SILENT_OFF_MS after that: outputs off, #EVT FAULT host-timeout-disabled; 'e' is needed again.
static void stdHostService(uint32_t now) {
  if (!std_hostToMs) return;
  if (!std_hostTimedOut && skBusy() && now - std_lastRxMs > std_hostToMs) {
    std_hostTimedOut = true; std_hostTimedOutMs = now;
    stdEvt(0, PSTR("FAULT host-timeout"));
    skHostTimeoutStop();
  }
  if (std_hostTimedOut && skEnabled() && now - std_hostTimedOutMs >= STD_HOST_SILENT_OFF_MS) {
    skHostTimeoutDisable();
    stdEvt(0, PSTR("FAULT host-timeout-disabled silent_ms=%lu"), (unsigned long)(now - std_lastRxMs));
  }
}

// ---------------------------------------------------------------- the '#' commands
static void stdInfo() {
  Serial.print(F("#OK INFO fw=" STD_FW " proto=1 caps=" STD_CAPS " axes=" STD_AXES));
  for (uint8_t a = 0; a < STD_NAXES; a++) {
    char l = (char)tolower((unsigned char)stdAxisLetter(a));
    snprintf_P(std_out, sizeof std_out, PSTR(" %c_tmc=%u %c_limits=%u %c_home=%u %c_ls1_end=%s %c_ls2_end=%s %c_homed=%u"),
               l, skTmc(a), l, skLimitsOn(a), l, stdCfgHome(a) ? 1u : 0u, l, stdEnd(skLsEnd(a, 0)), l,
               stdEnd(skLsEnd(a, 1)), l, skHomed(a));
    Serial.print(std_out);
#if STD_CAP_SOFT
    int32_t ls1, lim;
    uint8_t ref = skSoftRef(a, ls1, lim);
    snprintf_P(std_out, sizeof std_out, PSTR(" %c_soft=%ld %c_soft_ref=%u"), l, (long)std_soft[a], l, ref);
    Serial.print(std_out);
    if (ref && std_soft[a]) { snprintf_P(std_out, sizeof std_out, PSTR(" %c_soft_lim=%ld"), l, (long)lim); Serial.print(std_out); }
    if (std_softBad & (1u << a)) { snprintf_P(std_out, sizeof std_out, PSTR(" %c_soft_damaged=1"), l); Serial.print(std_out); }
#endif
  }
  snprintf_P(std_out, sizeof std_out, PSTR(" log=%u hostto_ms=%u"), std_logLevel, std_hostToMs);
  Serial.print(std_out);
  std_out[0] = 0;
  skInfoExtra(std_out, sizeof std_out);
  Serial.print(std_out);
  stdNl();
}

// #AXISCFG <A> limits=0|1 home=0|1 (either or both). Writes EEPROM; refused while enabled or moving.
static void stdAxisCfg(const char *cmd, char *args[], uint8_t n) {
  int8_t a = n ? stdAxisIndex(args[0]) : -1;
  if (a < 0 || n < 2) { stdErr(cmd, PSTR("bad-arg")); return; }
  uint8_t v = std_cfg[a];
  for (uint8_t i = 1; i < n; i++) {
    char *eq = strchr(args[i], '=');
    if (!eq || (strcmp(eq + 1, "0") && strcmp(eq + 1, "1"))) { stdErr(cmd, PSTR("bad-arg")); return; }
    *eq = 0;
    uint8_t bit = !strcmp_P(args[i], PSTR("LIMITS")) ? STD_CFG_LIMITS : !strcmp_P(args[i], PSTR("HOME")) ? STD_CFG_HOME : 0;
    if (!bit) { stdErr(cmd, PSTR("bad-arg")); return; }
    if (eq[1] == '1') v |= bit; else v &= (uint8_t)~bit;
  }
  if (skAxisCfgBusy()) { stdErr(cmd, PSTR("busy")); return; }
  if (!stdCfgStore((uint8_t)a, v)) { stdErr(cmd, PSTR("eeprom-verify-failed")); return; }
  skAxisCfgApply((uint8_t)a);
  stdOk(cmd, PSTR("axis=%c limits=%u home=%u stored=1"), stdAxisLetter((uint8_t)a), (v & STD_CFG_LIMITS) ? 1u : 0u,
        (v & STD_CFG_HOME) ? 1u : 0u);
}

#if STD_CAP_SOFT
// #SOFTLIMIT <A> [counts]: query, or set the soft travel limit (counts from LS1's reference, 0 = none; MEGA_STANDARD
// §3-§4). Writes EEPROM; refused while enabled or moving, as AXISCFG is.
static void stdSoftLimit(const char *cmd, char *args[], uint8_t n) {
  int8_t a = n ? stdAxisIndex(args[0]) : -1;
  long v = 0;
  if (a < 0 || n > 2 || (n == 2 && (!stdParseLong(args[1], v) || v < 0 || v > STD_SOFT_MAX))) { stdErr(cmd, PSTR("bad-arg")); return; }
  if (n == 2) {
    if (skAxisCfgBusy()) { stdErr(cmd, PSTR("busy")); return; }
    bool ok = stdSoftStore((uint8_t)a, (int32_t)v);
    skSoftApply((uint8_t)a);
    if (!ok) { stdErr(cmd, PSTR("eeprom-verify-failed")); return; }
  }
  int32_t ls1, lim;
  uint8_t ref = skSoftRef((uint8_t)a, ls1, lim);
  char b[96];
  int k = snprintf_P(b, sizeof b, PSTR("axis=%c counts=%ld ref=%u"), stdAxisLetter((uint8_t)a), (long)std_soft[a], ref);
  if (ref) k += snprintf_P(b + k, sizeof b - k, PSTR(" ls1=%ld"), (long)ls1);
  if (ref && std_soft[a]) k += snprintf_P(b + k, sizeof b - k, PSTR(" lim=%ld"), (long)lim);
  if (std_softBad & (1u << a)) k += snprintf_P(b + k, sizeof b - k, PSTR(" damaged=1"));
  if (n == 2) snprintf_P(b + k, sizeof b - k, PSTR(" stored=1"));
  stdOk(cmd, PSTR("%s"), b);
}
#endif

// One complete '#' line (the leading '#' included). Called by the sketch's byte dispatcher.
static void stdExtLine(char *line, bool overflow) {
  if (overflow) { if (std_ext) { Serial.print(F("#ERR LINE too-long")); stdNl(); } return; }
  size_t len = strlen(line);
  if (len && line[len - 1] == '\r') line[--len] = 0;
  char *save, *args[4]; uint8_t n = 0;
  char *cmd = strtok_r(line + 1, " \t", &save);
  if (!cmd) return;
  stdUpper(cmd);
  char *t;
  while (n < 4 && (t = strtok_r(nullptr, " \t", &save))) { stdUpper(t); args[n++] = t; }
  bool known = true;
  if (!strcmp_P(cmd, PSTR("HB"))) {
    std_ext = true; stdOkBare(cmd);
    return;                                                // the 4 Hz heartbeat is not logged at LOG 2
  }
  bool wasExt = std_ext;
  if (!strcmp_P(cmd, PSTR("INFO"))) { std_ext = true; stdInfo(); }
  else if (!strcmp_P(cmd, PSTR("LOG"))) {
    long v;
    std_ext = true;
    if (n != 1 || !stdParseLong(args[0], v) || v < 0 || v > 2) stdErr(cmd, PSTR("bad-arg"));
    else { std_logLevel = (uint8_t)v; stdOk(cmd, PSTR("level=%u"), std_logLevel); }
  }
  else if (!strcmp_P(cmd, PSTR("HOSTTIMEOUT"))) {
    long v;
    std_ext = true;
    if (n != 1 || !stdParseLong(args[0], v) || v <= 0) stdErr(cmd, PSTR("bad-arg"));
    else {
      std_hostToMs = (uint16_t)(v < STD_HOST_TIMEOUT_MIN_MS ? STD_HOST_TIMEOUT_MIN_MS : v > STD_HOST_TIMEOUT_MAX_MS ? STD_HOST_TIMEOUT_MAX_MS : v);
      stdOk(cmd, PSTR("ms=%u"), std_hostToMs);
    }
  }
  else if (!strcmp_P(cmd, PSTR("AXISCFG"))) { std_ext = true; stdAxisCfg(cmd, args, n); }
  else if (!strcmp_P(cmd, PSTR("STOP"))) { std_ext = true; skStop(); stdOkBare(cmd); }
#if STD_CAP_SOFT
  else if (!strcmp_P(cmd, PSTR("SOFTLIMIT"))) { std_ext = true; stdSoftLimit(cmd, args, n); }
#endif
#if STD_CAP_HOME
  else if (!strcmp_P(cmd, PSTR("HOME")) || !strcmp_P(cmd, PSTR("ZERO"))) {
    std_ext = true;
    int8_t a = n == 1 ? stdAxisIndex(args[0]) : -1;
    bool home = cmd[0] == 'H';
    if (a < 0) stdErr(cmd, PSTR("bad-arg"));
    else if (home) {
      PGM_P why = skHomeCheck((uint8_t)a);
      if (why) stdErr(cmd, why);
      else { stdOk(cmd, PSTR("started")); skHomeStart((uint8_t)a); }
    } else {
      PGM_P why = skZero((uint8_t)a);
      if (why) stdErr(cmd, why); else stdOk(cmd, PSTR("axis=%c"), stdAxisLetter((uint8_t)a));
    }
  }
#endif
  else known = false;
  if (!known) {
    // A '#' line nobody knows is answered only on a link that already speaks ext1: on an old host it is a desynced
    // jog packet that happened to start with '#', and the old firmware said nothing to that.
    if (std_ext) stdErr(cmd, PSTR("unknown-command"));
    return;
  }
  if (!wasExt) skExtOpened();
  stdDbg(PSTR("rx #%s%s%s%s%s"), cmd, n ? " " : "", n ? args[0] : "", n > 1 ? " " : "", n > 1 ? args[1] : "");
}

static void stdBegin(uint32_t now) {
  stdCfgLoad();
#if STD_CAP_SOFT
  stdSoftLoad();
#endif
  stdLineReset();
  std_lastRxMs = now;
}
