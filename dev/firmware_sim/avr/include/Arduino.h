// Host-side stand-in for the Arduino AVR core on a Mega 2560: what xyz_stage_mega.ino, stepper_firmware.ino and
// AccelStepper use. Pins map to port bits through the core's own table (stubs.cpp, from variants/mega/pins_arduino.h).
#pragma once
#include <stdint.h>
#include <stddef.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
#include <stdio.h>
#include <ctype.h>
#include <string>
#include <avr/io.h>
#include <avr/interrupt.h>
#include <avr/pgmspace.h>
typedef bool boolean;
typedef uint8_t byte;
#define HIGH 1
#define LOW 0
#define INPUT 0
#define OUTPUT 1
#define INPUT_PULLUP 2
#define A8 62
#define A9 63
#define A10 64
#define NOT_A_PORT 0
#define constrain(amt, lo, hi) ((amt) < (lo) ? (lo) : ((amt) > (hi) ? (hi) : (amt)))
#ifndef max
#define max(a, b) ((a) > (b) ? (a) : (b))
#endif
#ifndef min
#define min(a, b) ((a) < (b) ? (a) : (b))
#endif
uint32_t millis();
uint32_t micros();
void delay(uint32_t ms);
void delayMicroseconds(uint32_t us);
void yield();
void pinMode(uint8_t pin, uint8_t mode);
void digitalWrite(uint8_t pin, uint8_t level);
int digitalRead(uint8_t pin);
uint8_t digitalPinToPort(uint8_t pin);          // the port's index + 1 (NOT_A_PORT = 0), as the core's table
uint8_t digitalPinToBitMask(uint8_t pin);
SimReg8 *portOutputRegister(uint8_t port);
SimReg8 *portInputRegister(uint8_t port);
SimReg8 *portModeRegister(uint8_t port);

class __FlashStringHelper;
#define F(s) ((const __FlashStringHelper *)(s))

class String {
 public:
  String() {}
  String(const char *c) : s(c ? c : "") {}
  String(const std::string &x) : s(x) {}
  unsigned int length() const { return (unsigned int)s.size(); }
  char operator[](unsigned int i) const { return i < s.size() ? s[i] : 0; }
  String substring(unsigned int a, unsigned int b) const {
    if (a > s.size()) a = (unsigned int)s.size();
    if (b > s.size()) b = (unsigned int)s.size();
    return a < b ? String(s.substr(a, b - a)) : String();
  }
  void trim() {
    size_t a = 0, b = s.size();
    while (a < b && isspace((unsigned char)s[a])) a++;
    while (b > a && isspace((unsigned char)s[b - 1])) b--;
    s = s.substr(a, b - a);
  }
  long toInt() const { return atol(s.c_str()); }
  float toFloat() const { return (float)atof(s.c_str()); }
  const char *c_str() const { return s.c_str(); }
  String &operator+=(char c) { s += c; return *this; }
 private:
  std::string s;
};

class Stream {
 public:
  virtual ~Stream() {}
  virtual int available() = 0;
  virtual int read() = 0;
  virtual int peek() = 0;
  virtual size_t write(uint8_t c) = 0;
};

// One hardware UART. id 0 is USB Serial (the host); 1-3 are Serial1-3 (the drivers' buses). The simulation queues
// bytes in and takes bytes out (sim_api.h); on the host nothing waits: a read of an empty buffer returns -1 at once.
class HardwareSerial : public Stream {
 public:
  explicit HardwareSerial(uint8_t id) : id_(id) {}
  void begin(unsigned long baud);
  void begin(unsigned long baud, int) { begin(baud); }
  void setTimeout(unsigned long) {}
  int available() override;
  int read() override;
  int peek() override;
  int availableForWrite();
  void flush() {}
  size_t write(uint8_t c) override;
  size_t write(const uint8_t *b, size_t n) { for (size_t i = 0; i < n; i++) write(b[i]); return n; }
  size_t readBytes(char *b, size_t n);         // no timeout on the host: what is there, up to n
  String readStringUntil(char terminator);     // up to the terminator or the end of what is there
  size_t print(const char *s) { return write((const uint8_t *)s, strlen(s)); }
  size_t print(const __FlashStringHelper *s) { return print((const char *)s); }
  size_t print(const String &s) { return print(s.c_str()); }
  size_t print(char c) { return write((uint8_t)c); }
  size_t print(int v) { return printf_("%d", v); }
  size_t print(unsigned int v) { return printf_("%u", v); }
  size_t print(long v) { return printf_("%ld", v); }
  size_t print(unsigned long v) { return printf_("%lu", v); }
  size_t print(double v) { return printf_("%.2f", v); }
  template <typename T> size_t println(T v) { size_t n = print(v); return n + print("\r\n"); }
  size_t println() { return print("\r\n"); }
  explicit operator bool() const { return true; }
 private:
  size_t printf_(const char *fmt, ...) __attribute__((format(printf, 2, 3)));
  uint8_t id_;
};
extern HardwareSerial Serial, Serial1, Serial2, Serial3;
