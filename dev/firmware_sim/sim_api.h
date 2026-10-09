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
extern uint16_t g_simDrvMicrosteps;
void sim_serial_input(const char *s);                  // queue host bytes for Serial.read()
void sim_on_serial_output(const char *b, size_t n);    // sketch output (sim.cpp)
void sim_on_pin_write(uint8_t pin, uint8_t level);     // sketch pin writes (sim.cpp)
void setup();
void loop();
