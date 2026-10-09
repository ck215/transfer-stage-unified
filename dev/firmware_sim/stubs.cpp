// Stubbed Teensy layer: fake clock, pins, USB serial, EEPROM, IntervalTimer. No interrupts: the simulation calls the
// step ISR every 25 us of fake time and loop() between ticks, so __disable_irq/__enable_irq have nothing to do.
#include <Arduino.h>
#include <EEPROM.h>
#include <TMCStepper.h>
#include "sim_api.h"
uint64_t g_simUs = 0;
uint8_t g_simPinIn[64], g_simPinOut[64];
bool g_simDtr = true;
void (*g_simIsr)() = nullptr;
unsigned g_simIsrPeriodUs = 0;
uint16_t g_simDrvMicrosteps = 8;
SimUsbSerial Serial;
SimHwSerial Serial1;
SimEEPROM EEPROM;
static char s_in[1 << 16];
static size_t s_head = 0, s_tail = 0;
static uint8_t s_eeprom[4096];
static bool s_eepromInit = false;

uint32_t millis() { return (uint32_t)(g_simUs / 1000); }
uint32_t micros() { return (uint32_t)g_simUs; }
void delay(uint32_t) {}
void delayMicroseconds(uint32_t) {}
void pinMode(uint8_t, uint8_t) {}
void digitalWrite(uint8_t p, uint8_t l) { g_simPinOut[p] = l; sim_on_pin_write(p, l); }
uint8_t digitalRead(uint8_t p) { return g_simPinIn[p]; }
uint8_t digitalReadFast(uint8_t p) { return g_simPinIn[p]; }
void __disable_irq() {}
void __enable_irq() {}
void yield() {}
int SimUsbSerial::available() { return (int)(s_tail - s_head); }
int SimUsbSerial::read() { return s_head < s_tail ? (unsigned char)s_in[s_head++] : -1; }
int SimUsbSerial::availableForWrite() { return 4096; }
size_t SimUsbSerial::write(const uint8_t *b, size_t n) { sim_on_serial_output((const char *)b, n); return n; }
size_t SimUsbSerial::print(const char *s) { size_t n = strlen(s); sim_on_serial_output(s, n); return n; }
size_t SimUsbSerial::print(char c) { sim_on_serial_output(&c, 1); return 1; }
SimUsbSerial::operator bool() const { return g_simDtr; }
bool IntervalTimer::begin(void (*f)(), unsigned us) { g_simIsr = f; g_simIsrPeriodUs = us; return true; }
uint8_t SimEEPROM::read(int a) { if (!s_eepromInit) { memset(s_eeprom, 0xFF, sizeof s_eeprom); s_eepromInit = true; } return s_eeprom[a]; }
void SimEEPROM::update(int a, uint8_t v) { read(a); s_eeprom[a] = v; }
void sim_serial_input(const char *s) {
  if (s_head == s_tail) s_head = s_tail = 0;
  size_t n = strlen(s);
  if (s_tail + n > sizeof s_in) { fprintf(stderr, "serial input overflow\n"); exit(3); }
  memcpy(s_in + s_tail, s, n); s_tail += n;
}
