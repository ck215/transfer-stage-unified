// Stubbed Arduino AVR core for a Mega 2560 on the host: fake clock, port registers wired to the simulation, the four
// UARTs as byte queues, EEPROM, the watchdog, and an ISR registry. Nothing preempts anything: the simulation calls the
// timer ISR at its period and loop() between ticks.
#include <Arduino.h>
#include <EEPROM.h>
#include <avr/wdt.h>
#include <deque>
#include <map>
#include <string>
#include "sim_api.h"

uint64_t g_simUs = 0;
SimReg8 g_simPinReg[SIM_NPORTS], g_simPortReg[SIM_NPORTS], g_simDdrReg[SIM_NPORTS];
uint8_t SREG = 0, MCUSR = 0, UCSR0B = 0, TCCR1A = 0, TCCR1B = 0, TIMSK1 = 0;
uint16_t OCR1A = 0, TCNT1 = 0;
bool g_simWdtArmed = false;
uint64_t g_simWdtPetUs = 0;
uint32_t g_simWdtTimeoutUs = 0;
HardwareSerial Serial(0), Serial1(1), Serial2(2), Serial3(3);
EEPROMClass EEPROM;

// digital pin -> (port, bit): variants/mega/pins_arduino.h, digital_pin_to_port_PGM / digital_pin_to_bit_mask_PGM
static const uint8_t PIN_MAP[70][2] = {
  {SIM_PE,0}, {SIM_PE,1}, {SIM_PE,4}, {SIM_PE,5}, {SIM_PG,5}, {SIM_PE,3}, {SIM_PH,3}, {SIM_PH,4}, {SIM_PH,5}, {SIM_PH,6},
  {SIM_PB,4}, {SIM_PB,5}, {SIM_PB,6}, {SIM_PB,7}, {SIM_PJ,1}, {SIM_PJ,0}, {SIM_PH,1}, {SIM_PH,0}, {SIM_PD,3}, {SIM_PD,2},
  {SIM_PD,1}, {SIM_PD,0}, {SIM_PA,0}, {SIM_PA,1}, {SIM_PA,2}, {SIM_PA,3}, {SIM_PA,4}, {SIM_PA,5}, {SIM_PA,6}, {SIM_PA,7},
  {SIM_PC,7}, {SIM_PC,6}, {SIM_PC,5}, {SIM_PC,4}, {SIM_PC,3}, {SIM_PC,2}, {SIM_PC,1}, {SIM_PC,0}, {SIM_PD,7}, {SIM_PG,2},
  {SIM_PG,1}, {SIM_PG,0}, {SIM_PL,7}, {SIM_PL,6}, {SIM_PL,5}, {SIM_PL,4}, {SIM_PL,3}, {SIM_PL,2}, {SIM_PL,1}, {SIM_PL,0},
  {SIM_PB,3}, {SIM_PB,2}, {SIM_PB,1}, {SIM_PB,0}, {SIM_PF,0}, {SIM_PF,1}, {SIM_PF,2}, {SIM_PF,3}, {SIM_PF,4}, {SIM_PF,5},
  {SIM_PF,6}, {SIM_PF,7}, {SIM_PK,0}, {SIM_PK,1}, {SIM_PK,2}, {SIM_PK,3}, {SIM_PK,4}, {SIM_PK,5}, {SIM_PK,6}, {SIM_PK,7},
};
static struct RegInit {
  RegInit() {
    for (uint8_t p = 0; p < SIM_NPORTS; p++) {
      g_simPinReg[p] = SimReg8{0, 0, p}; g_simPortReg[p] = SimReg8{0, 1, p}; g_simDdrReg[p] = SimReg8{0, 2, p};
    }
  }
} s_regInit;

SimReg8 &SimReg8::operator=(uint8_t x) {
  uint8_t old = v;
  v = x;
  if (kind == 1 && old != x) sim_on_port_write(port, old, x);
  return *this;
}
uint8_t digitalPinToPort(uint8_t pin) { return pin < 70 ? (uint8_t)(PIN_MAP[pin][0] + 1) : NOT_A_PORT; }
uint8_t digitalPinToBitMask(uint8_t pin) { return pin < 70 ? (uint8_t)_BV(PIN_MAP[pin][1]) : 0; }
SimReg8 *portOutputRegister(uint8_t port) { return port ? &g_simPortReg[port - 1] : nullptr; }
SimReg8 *portInputRegister(uint8_t port) { return port ? &g_simPinReg[port - 1] : nullptr; }
SimReg8 *portModeRegister(uint8_t port) { return port ? &g_simDdrReg[port - 1] : nullptr; }
uint8_t sim_pin_port(uint8_t pin) { return PIN_MAP[pin][0]; }
uint8_t sim_pin_bit(uint8_t pin) { return PIN_MAP[pin][1]; }
uint8_t sim_pin_out(uint8_t pin) { return (g_simPortReg[PIN_MAP[pin][0]].v >> PIN_MAP[pin][1]) & 1; }
void sim_set_pin_in(uint8_t pin, uint8_t level) {
  SimReg8 &r = g_simPinReg[PIN_MAP[pin][0]];
  uint8_t m = (uint8_t)_BV(PIN_MAP[pin][1]);
  r.v = level ? (uint8_t)(r.v | m) : (uint8_t)(r.v & ~m);
}

uint32_t millis() { return (uint32_t)(g_simUs / 1000); }
uint32_t micros() { return (uint32_t)g_simUs; }
void delay(uint32_t) {}
void delayMicroseconds(uint32_t) {}
void yield() {}
void pinMode(uint8_t pin, uint8_t mode) {       // the core's pinMode: DDR bit, and PORT bit = pull-up for INPUT_PULLUP
  if (pin >= 70) return;
  uint8_t p = PIN_MAP[pin][0], m = (uint8_t)_BV(PIN_MAP[pin][1]);
  uint8_t s = SREG; cli();
  if (mode == OUTPUT) g_simDdrReg[p] |= m;
  else { g_simDdrReg[p] &= (uint8_t)~m; if (mode == INPUT_PULLUP) g_simPortReg[p] |= m; else g_simPortReg[p] &= (uint8_t)~m; }
  SREG = s;
}
void digitalWrite(uint8_t pin, uint8_t level) {
  if (pin >= 70) return;
  uint8_t p = PIN_MAP[pin][0], m = (uint8_t)_BV(PIN_MAP[pin][1]);
  uint8_t s = SREG; cli();
  if (level) g_simPortReg[p] |= m; else g_simPortReg[p] &= (uint8_t)~m;
  SREG = s;
}
int digitalRead(uint8_t pin) { return pin < 70 ? (g_simPinReg[PIN_MAP[pin][0]].v >> PIN_MAP[pin][1]) & 1 : 0; }
void cli() { SREG &= (uint8_t)~0x80; }
void sei() { SREG |= 0x80; }

// ---- ISRs by name
static std::map<std::string, void (*)()> &isrs() { static std::map<std::string, void (*)()> m; return m; }
SimIsrReg::SimIsrReg(const char *name, void (*fn)()) { isrs()[name] = fn; }
void (*sim_isr(const char *name))() { auto it = isrs().find(name); return it == isrs().end() ? nullptr : it->second; }

// ---- watchdog
void wdt_enable(uint8_t t) {
  static const uint32_t MS[] = {15, 30, 60, 120, 250, 500, 1000, 2000};
  g_simWdtArmed = true; g_simWdtTimeoutUs = MS[t & 7] * 1000u; g_simWdtPetUs = g_simUs;
}
void wdt_disable() { g_simWdtArmed = false; }
void wdt_reset() { g_simWdtPetUs = g_simUs; }

// ---- EEPROM
static uint8_t s_eeprom[4096];
static bool s_eepromInit = false;
uint8_t *sim_eeprom() { if (!s_eepromInit) { memset(s_eeprom, 0xFF, sizeof s_eeprom); s_eepromInit = true; } return s_eeprom; }
uint8_t EEPROMClass::read(int a) { return (a >= 0 && a < 4096) ? sim_eeprom()[a] : 0xFF; }
void EEPROMClass::update(int a, uint8_t v) { if (a >= 0 && a < 4096) sim_eeprom()[a] = v; }

// ---- UARTs
static std::deque<uint8_t> s_rx[4];
void sim_serial_input(const uint8_t *b, size_t n) { for (size_t i = 0; i < n; i++) s_rx[0].push_back(b[i]); }
size_t sim_serial_pending() { return s_rx[0].size(); }
void sim_uart_rx(uint8_t uart, uint8_t c) { if (uart < 4) s_rx[uart].push_back(c); }
void HardwareSerial::begin(unsigned long) { if (id_ == 0) UCSR0B = (uint8_t)(_BV(RXEN0) | _BV(TXEN0) | _BV(RXCIE0)); }
int HardwareSerial::available() { return (int)s_rx[id_].size(); }
int HardwareSerial::read() {
  if (s_rx[id_].empty()) return -1;
  uint8_t c = s_rx[id_].front(); s_rx[id_].pop_front(); return c;
}
int HardwareSerial::peek() { return s_rx[id_].empty() ? -1 : s_rx[id_].front(); }
int HardwareSerial::availableForWrite() { return 63; }   // never full: SERIAL_TX_BUFFER_SIZE - 1, as the core reports empty
size_t HardwareSerial::write(uint8_t c) {
  if (id_ == 0) { char ch = (char)c; sim_on_serial_output(&ch, 1); }
  else sim_on_uart_tx(id_, c);
  return 1;
}
size_t HardwareSerial::readBytes(char *b, size_t n) {
  size_t k = 0;
  while (k < n && !s_rx[id_].empty()) { b[k++] = (char)s_rx[id_].front(); s_rx[id_].pop_front(); }
  return k;
}
String HardwareSerial::readStringUntil(char t) {
  std::string s;
  while (!s_rx[id_].empty()) { char c = (char)s_rx[id_].front(); s_rx[id_].pop_front(); if (c == t) break; s += c; }
  return String(s);
}
size_t HardwareSerial::printf_(const char *fmt, ...) {
  char b[48]; va_list ap; va_start(ap, fmt); vsnprintf(b, sizeof b, fmt, ap); va_end(ap);
  return print(b);
}
