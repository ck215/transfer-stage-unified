// Host-side stand-in for <avr/interrupt.h>. ISR(vec) defines a function and registers it by name; the simulation
// calls TIMER1_COMPA_vect at the period Timer1 is programmed for. cli()/sei() keep SREG's I bit (nothing preempts
// anything on the host: the simulation runs ISRs and loop() one after the other).
#pragma once
#include <avr/io.h>
void cli();
void sei();
struct SimIsrReg { SimIsrReg(const char *name, void (*fn)()); };
#define ISR(vec) static void sim_isr_##vec(); static SimIsrReg sim_isr_reg_##vec(#vec, sim_isr_##vec); static void sim_isr_##vec()
