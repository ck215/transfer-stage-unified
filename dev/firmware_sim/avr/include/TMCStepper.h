// Host-side stand-in for TMCStepper 0.7.3's TMC2208Stepper/TMC2209Stepper: the setters the Mega sketches call keep
// shadow registers with the library's defaults() and bit layout (TMC2208_bitfields.h) and send each whole register
// through the virtual write(reg, value), as the library does. xyz_stage_mega overrides write() to queue datagrams on
// its bus; stepper_firmware does not, and its writes are dropped here. Reads go through read(); test_connection() is
// the library's (0 = a DRV_STATUS reply that is neither all zeros nor all ones).
#pragma once
#include <Arduino.h>

class TMC2208Stepper {
 public:
  TMC2208Stepper(Stream *serial, float rsense, uint8_t addr) : Rsense(rsense), slave_address(addr), HWSerial(serial) {}
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
  void microsteps(uint16_t ms) {
    uint8_t m = ms == 256 ? 0 : ms == 128 ? 1 : ms == 64 ? 2 : ms == 32 ? 3 : ms == 16 ? 4 : ms == 8 ? 5 : ms == 4 ? 6 : ms == 2 ? 7 : 8;
    mres(m);
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
 protected:
  virtual void write(uint8_t, uint32_t) {}
  virtual uint32_t read(uint8_t) { return 0x80100000UL; }   // a healthy standstill DRV_STATUS (stst, cs_actual 16)
  const uint8_t slave_address;
  Stream *HWSerial;
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
