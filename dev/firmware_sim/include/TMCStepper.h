// TMC2209 stand-in: a healthy driver at version 0x21 that remembers what it is told.
#pragma once
#include <Arduino.h>
extern uint16_t g_simDrvMicrosteps;   // what the firmware last wrote (0 = full step), read by the stage model
class TMC2209Stepper {
 public:
  TMC2209Stepper(SimHwSerial *, float, uint8_t) {}
  void begin() {}
  uint32_t DRV_STATUS() { return (16UL << 16) | (1UL << 31); }   // cs_actual 16, standstill flag, no faults
  uint8_t GSTAT() { return 0; }
  void GSTAT(uint8_t) { ifcnt++; }
  uint8_t version() { return 0x21; }
  uint16_t microsteps() { return g_simDrvMicrosteps; }
  void microsteps(uint16_t m) { g_simDrvMicrosteps = m; ifcnt++; }
  void toff(uint8_t) { ifcnt++; }
  void pdn_disable(bool) { ifcnt++; }
  void mstep_reg_select(bool) { ifcnt++; }
  void I_scale_analog(bool) { ifcnt++; }
  void TPWMTHRS(uint32_t) { ifcnt++; }
  void TCOOLTHRS(uint32_t) { ifcnt++; }
  void SGTHRS(uint8_t) { ifcnt++; }
  void rms_current(uint16_t, float) { ifcnt++; }
  void en_spreadCycle(bool) { ifcnt++; }
  void pwm_autoscale(bool) { ifcnt++; }
  uint16_t SG_RESULT() { return 200; }
  uint32_t TSTEP() { return 1000; }
  uint8_t tbl() { return tblv; }
  void tbl(uint8_t v) { tblv = v; ifcnt++; }
  uint8_t IFCNT() { return ifcnt; }
 private:
  uint8_t tblv = 2, ifcnt = 0;
};
