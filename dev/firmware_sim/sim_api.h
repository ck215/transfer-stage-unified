// The seam between the stubbed Teensy layer (stubs.cpp) and the simulation (sim.cpp).
#pragma once
#include <stddef.h>
#include <stdint.h>
extern uint64_t g_simUs;                 // fake clock, microseconds
extern uint8_t g_simPinIn[64];           // levels the sketch reads (set by the stage model every ISR tick)
extern uint8_t g_simPinOut[64];          // levels the sketch last wrote
extern bool g_simDtr;                    // USB DTR: (bool)Serial
extern void (*g_simIsr)();               // the step ISR registered with IntervalTimer
extern unsigned g_simIsrPeriodUs;
extern double g_simIsrPeriodF;          // the same period as the sketch gave it (limit_seek's is a float)
extern uint8_t g_simPinDrive[64];       // 0: the sketch reads g_simPinIn; 1 open (its pull decides), 2 GND, 3 +5 V
extern uint16_t g_simDrvMicrosteps;
void sim_serial_input(const char *s);                  // queue host bytes for Serial.read()
void sim_on_serial_output(const char *b, size_t n);    // sketch output (sim.cpp)
void sim_on_pin_write(uint8_t pin, uint8_t level);     // sketch pin writes (sim.cpp)
uint8_t *sim_eeprom();                                 // the 4 KB EEPROM image (erased 0xFF until written)
void setup();
void loop();
