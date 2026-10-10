// Host-side stand-in for <avr/pgmspace.h>: flash strings are ordinary strings, so -Wformat checks the *_P formats.
#pragma once
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define PROGMEM
typedef const char *PGM_P;
#define PSTR(s) (s)
#define pgm_read_byte(p) (*(const uint8_t *)(p))
#define pgm_read_word(p) (*(const uint16_t *)(p))
#define strcmp_P strcmp
#define strncmp_P strncmp
#define strncpy_P strncpy
#define strlen_P strlen
#define memcpy_P memcpy
#define snprintf_P snprintf
#define vsnprintf_P vsnprintf
