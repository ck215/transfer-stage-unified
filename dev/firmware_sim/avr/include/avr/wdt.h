// Host-side stand-in for <avr/wdt.h>: the simulation counts a watchdog reset when wdt_reset() stops coming.
#pragma once
#include <stdint.h>
#define WDTO_15MS 0
#define WDTO_30MS 1
#define WDTO_60MS 2
#define WDTO_120MS 3
#define WDTO_250MS 4
#define WDTO_500MS 5
#define WDTO_1S 6
#define WDTO_2S 7
void wdt_enable(uint8_t timeout);
void wdt_disable();
void wdt_reset();
