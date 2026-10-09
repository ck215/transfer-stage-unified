// Host-side stand-in for the AVR EEPROM library: 4 KB, erased (0xFF) unless a scenario loads an image (a reboot).
#pragma once
#include <stdint.h>
struct EEPROMClass {
  uint8_t read(int addr);
  void update(int addr, uint8_t v);
  void write(int addr, uint8_t v) { update(addr, v); }
};
extern EEPROMClass EEPROM;
