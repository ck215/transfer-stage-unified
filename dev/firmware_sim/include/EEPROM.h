#pragma once
#include <stdint.h>
struct SimEEPROM { uint8_t read(int a); void update(int a, uint8_t v); void write(int a, uint8_t v) { update(a, v); } };
extern SimEEPROM EEPROM;
