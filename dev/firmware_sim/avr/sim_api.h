// The seam between the stubbed AVR core (stubs.cpp) and the Mega simulation (sim_mega.cpp).
#pragma once
#include <stddef.h>
#include <stdint.h>
extern uint64_t g_simUs;                          // fake clock, microseconds
uint8_t sim_pin_out(uint8_t pin);                 // the level the sketch drives on pin (its PORT bit)
uint8_t sim_pin_port(uint8_t pin);                // the port index (SIM_PA..) and bit of a digital pin
uint8_t sim_pin_bit(uint8_t pin);
void sim_set_pin_in(uint8_t pin, uint8_t level);  // the level the sketch reads on pin (its PIN bit)
void sim_serial_input(const uint8_t *b, size_t n);   // host bytes for Serial (USB)
size_t sim_serial_pending();                      // host bytes the sketch has not read yet
void sim_uart_rx(uint8_t uart, uint8_t c);        // a byte for Serial1..3 to read
void (*sim_isr(const char *name))();              // a registered ISR, or nullptr
uint8_t *sim_eeprom();                            // the 4 KB EEPROM image
extern bool g_simWdtArmed;                        // wdt_enable() .. wdt_disable()
extern uint64_t g_simWdtPetUs;                    // last wdt_reset()
extern uint32_t g_simWdtTimeoutUs;
// implemented by the simulation
void sim_on_serial_output(const char *b, size_t n);
void sim_on_port_write(uint8_t port, uint8_t oldv, uint8_t newv);
void sim_on_uart_tx(uint8_t uart, uint8_t c);
void setup();
void loop();
