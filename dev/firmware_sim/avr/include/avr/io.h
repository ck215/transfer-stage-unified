// Host-side stand-in for <avr/io.h> on the ATmega2560: just what the Mega sketches use. The port registers are
// objects, so the simulation sees every write (STEP, DIR and EN edges) and supplies every read (switch and sensor
// levels); everything else is a plain variable.
#pragma once
#include <stdint.h>
#define _BV(b) (1u << (b))
enum { SIM_PA, SIM_PB, SIM_PC, SIM_PD, SIM_PE, SIM_PF, SIM_PG, SIM_PH, SIM_PJ, SIM_PK, SIM_PL, SIM_NPORTS };
struct SimReg8 {
  uint8_t v;
  uint8_t kind;                  // 0 PIN (read: pin levels), 1 PORT (write: output levels / pull-ups), 2 DDR
  uint8_t port;
  operator uint8_t() const { return v; }
  SimReg8 &operator=(uint8_t x);  // stubs.cpp: a PORT write goes to the simulation
  SimReg8 &operator|=(uint8_t x) { return *this = (uint8_t)(v | x); }
  SimReg8 &operator&=(uint8_t x) { return *this = (uint8_t)(v & x); }
  SimReg8 &operator^=(uint8_t x) { return *this = (uint8_t)(v ^ x); }
};
extern SimReg8 g_simPinReg[SIM_NPORTS], g_simPortReg[SIM_NPORTS], g_simDdrReg[SIM_NPORTS];
#define PINA g_simPinReg[SIM_PA]
#define PINB g_simPinReg[SIM_PB]
#define PINC g_simPinReg[SIM_PC]
#define PIND g_simPinReg[SIM_PD]
#define PINE g_simPinReg[SIM_PE]
#define PINF g_simPinReg[SIM_PF]
#define PING g_simPinReg[SIM_PG]
#define PINH g_simPinReg[SIM_PH]
#define PINJ g_simPinReg[SIM_PJ]
#define PINK g_simPinReg[SIM_PK]
#define PINL g_simPinReg[SIM_PL]
#define PORTA g_simPortReg[SIM_PA]
#define PORTB g_simPortReg[SIM_PB]
#define PORTC g_simPortReg[SIM_PC]
#define PORTD g_simPortReg[SIM_PD]
#define PORTE g_simPortReg[SIM_PE]
#define PORTF g_simPortReg[SIM_PF]
#define PORTG g_simPortReg[SIM_PG]
#define PORTH g_simPortReg[SIM_PH]
#define PORTJ g_simPortReg[SIM_PJ]
#define PORTK g_simPortReg[SIM_PK]
#define PORTL g_simPortReg[SIM_PL]
extern uint8_t SREG, MCUSR, UCSR0B, TCCR1A, TCCR1B, TIMSK1;
extern uint16_t OCR1A, TCNT1;
#define CS10 0
#define CS11 1
#define CS12 2
#define WGM12 3
#define OCIE1A 1
#define TXEN0 3
#define RXEN0 4
#define RXCIE0 7
