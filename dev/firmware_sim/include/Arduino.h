// Host-side stand-in for the Teensy core: just what xyz_stage_axis.ino, stepper_validator.ino, limit_seek.ino and
// AccelStepper use.
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <stdio.h>
#include <ctype.h>
typedef bool boolean;
typedef uint8_t byte;
#define HIGH 1
#define LOW 0
#define INPUT 0
#define OUTPUT 1
#define INPUT_PULLUP 2
#define INPUT_PULLDOWN 3
#define SERIAL_8N1 0
#define SERIAL_8N1_HALF_DUPLEX 1
uint32_t millis();
uint32_t micros();
void delay(uint32_t ms);
void delayMicroseconds(uint32_t us);
void pinMode(uint8_t pin, uint8_t mode);
void digitalWrite(uint8_t pin, uint8_t level);
uint8_t digitalRead(uint8_t pin);
uint8_t digitalReadFast(uint8_t pin);
void digitalWriteFast(uint8_t pin, uint8_t level);
void __disable_irq();
void __enable_irq();
void noInterrupts();
void interrupts();
void yield();
#define constrain(amt, lo, hi) ((amt) < (lo) ? (lo) : ((amt) > (hi) ? (hi) : (amt)))
#ifndef max
#define max(a, b) ((a) > (b) ? (a) : (b))
#endif
#ifndef min
#define min(a, b) ((a) < (b) ? (a) : (b))
#endif
class SimUsbSerial {
 public:
  void begin(unsigned long) {}
  int available();
  int read();
  int availableForWrite();
  size_t write(const uint8_t *b, size_t n);
  size_t print(const char *s);
  size_t print(char c);
  size_t println(const char *s);
  int printf(const char *fmt, ...) __attribute__((format(printf, 2, 3)));
  explicit operator bool() const;
};
extern SimUsbSerial Serial;
class SimHwSerial { public: void begin(unsigned long, int) {} };
extern SimHwSerial Serial1;
class IntervalTimer {           // any period type, as Teensy's (limit_seek passes a float)
 public:
  template <typename T> bool begin(void (*f)(), T us) { return beginUs(f, (double)us); }
  void end();
  bool beginUs(void (*f)(), double us);
};
