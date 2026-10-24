// Host-side stand-in for TMCStepper 0.7.3's TMC2208Stepper/TMC2209Stepper: the setters the Mega sketches call keep
// shadow registers with the library's defaults() and bit layout (TMC2208_bitfields.h) and send each whole register
// through the virtual write(reg, value), as the library does. xyz_stage_mega overrides write() to queue datagrams on
// its bus; stepper_firmware and chuck_firmware do not, and their writes land on a driver model here: each instance's
// CHOPCONF as the driver holds it (g_simTmcChop, by construction order: the chuck's xUART, yUART, zUART are 0, 1, 2),
// which read(0x6C) returns. Reads go through read(); test_connection() is the library's (0 = a DRV_STATUS reply that is
// neither all zeros nor all ones). microsteps() both ways is the library's (TMCStepper.cpp): a full step is spelled 0,
// any other value is ignored by the setter, and the getter decodes the CHOPCONF it reads from the driver.
// Fault hooks for the simulation (bit i = instance i): g_simTmcDropWrites, a driver that ignores writes;
// g_simTmcNoReply, a driver that never answers a read (the library then returns 0 and sets CRCerror).
#pragma once
#include <Arduino.h>
extern uint32_t g_simTmcChop[8];          // defined in sim_mega.cpp: the driver side of each instance
extern uint32_t g_simTmcChopWrites[8];    // CHOPCONF writes each driver took
extern uint8_t g_simTmcDropWrites, g_simTmcNoReply;
extern int g_simTmcCount;

class TMC2208Stepper {
 public:
  TMC2208Stepper(Stream *serial, float rsense, uint8_t addr)
      : Rsense(rsense), slave_address(addr), HWSerial(serial), simIdx(g_simTmcCount < 7 ? g_simTmcCount++ : 7) {
    g_simTmcChop[simIdx] = 0x10000053UL;   // the driver's power-on CHOPCONF (mres 0: 256 microsteps)
  }
  virtual ~TMC2208Stepper() {}
  void begin() { pdn_disable(true); mstep_reg_select(true); }
  // GCONF (0x00)
  void I_scale_analog(bool b) { gconf(0, b); }
  void en_spreadCycle(bool b) { gconf(2, b); }
  void pdn_disable(bool b) { gconf(6, b); }
  void mstep_reg_select(bool b) { gconf(7, b); }
  // CHOPCONF (0x6C): toff 0-3, vsense 17, mres 24-27, intpol 28
  void toff(uint8_t b) { chop = (chop & ~0xFUL) | (b & 0xFUL); write(0x6C, chop); }
  void vsense(bool b) { chop = b ? chop | (1UL << 17) : chop & ~(1UL << 17); write(0x6C, chop); }
  void mres(uint8_t m) { chop = (chop & ~(0xFUL << 24)) | ((uint32_t)(m & 0xF) << 24); write(0x6C, chop); }
  void intpol(bool b) { chop = b ? chop | (1UL << 28) : chop & ~(1UL << 28); write(0x6C, chop); }
  void microsteps(uint16_t ms) {                   // TMCStepper::microsteps(uint16_t): 0 is a full step, 1 does nothing
    switch (ms) {
      case 256: mres(0); break; case 128: mres(1); break; case 64: mres(2); break; case 32: mres(3); break;
      case 16: mres(4); break;  case 8: mres(5); break;   case 4: mres(6); break;  case 2: mres(7); break;
      case 0: mres(8); break;   default: break;
    }
  }
  uint16_t microsteps() {                          // TMCStepper::microsteps(): mres() read back from the driver
    switch ((read(0x6C) >> 24) & 0xF) {
      case 0: return 256; case 1: return 128; case 2: return 64; case 3: return 32;
      case 4: return 16;  case 5: return 8;   case 6: return 4;  case 7: return 2; case 8: return 0;
    }
    return 0;
  }
  // IHOLD_IRUN (0x10): ihold 0-4, irun 8-12, iholddelay 16-19
  void irun(uint8_t b) { ihr = (ihr & ~(0x1FUL << 8)) | ((uint32_t)(b & 0x1F) << 8); write(0x10, ihr); }
  void ihold(uint8_t b) { ihr = (ihr & ~0x1FUL) | (b & 0x1FUL); write(0x10, ihr); }
  void rms_current(uint16_t mA) {                  // TMCStepper::rms_current, verbatim arithmetic
    uint8_t CS = 32.0 * 1.41421 * mA / 1000.0 * (Rsense + 0.02) / 0.325 - 1;
    if (CS < 16) { vsense(true); CS = 32.0 * 1.41421 * mA / 1000.0 * (Rsense + 0.02) / 0.180 - 1; }
    else vsense(false);
    if (CS > 31) CS = 31;
    irun(CS);
    ihold(CS * holdMultiplier);
  }
  void rms_current(uint16_t mA, float mult) { holdMultiplier = mult; rms_current(mA); }
  // PWMCONF (0x70): pwm_autoscale 18
  void pwm_autoscale(bool b) { pwm = b ? pwm | (1UL << 18) : pwm & ~(1UL << 18); write(0x70, pwm); }
  void TPWMTHRS(uint32_t v) { write(0x13, v); }
  void GSTAT(uint8_t) { write(0x01, 0b111); }
  uint32_t DRV_STATUS() { return read(0x6F); }
  uint8_t test_connection() { uint32_t d = DRV_STATUS(); return d == 0xFFFFFFFFUL ? 1 : d == 0 ? 2 : 0; }
  float Rsense;
  bool CRCerror = false;                           // the library's: the last read failed its CRC (or got no reply)
 protected:
  virtual void write(uint8_t reg, uint32_t v) {    // the driver model: it takes a CHOPCONF write unless it ignores writes
    if (reg == 0x6C && !(g_simTmcDropWrites & (1u << simIdx))) { g_simTmcChop[simIdx] = v; g_simTmcChopWrites[simIdx]++; }
  }
  virtual uint32_t read(uint8_t reg) {
    CRCerror = false;
    if (g_simTmcNoReply & (1u << simIdx)) { CRCerror = true; return 0; }   // TMC2208Stepper::read: no reply -> 0
    if (reg == 0x6C) return g_simTmcChop[simIdx];
    return 0x80100000UL;                           // a healthy standstill DRV_STATUS (stst, cs_actual 16)
  }
  const uint8_t slave_address;
  Stream *HWSerial;
  const int simIdx;
 private:
  void gconf(uint8_t bit, bool b) { gc = b ? gc | (1UL << bit) : gc & ~(1UL << bit); write(0x00, gc); }
  uint32_t gc = 0x101;                 // defaults(): i_scale_analog, multistep_filt
  uint32_t chop = 0x10000053UL;        // defaults()
  uint32_t ihr = 1UL << 16;            // defaults(): iholddelay 1
  uint32_t pwm = 0xC10D0024UL;         // defaults()
  float holdMultiplier = 0.5f;
};
class TMC2209Stepper : public TMC2208Stepper {
 public:
  TMC2209Stepper(Stream *serial, float rsense, uint8_t addr) : TMC2208Stepper(serial, rsense, addr) {}
};
