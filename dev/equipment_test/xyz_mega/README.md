# XYZ Mega wiring

`wiring.html` is the pin-by-pin wiring sheet for the XYZ stage on one Arduino
Mega 2560 with three BIGTREETECH TMC2209 V1.2 drivers on a shared UART bus.
Open it in any browser; it needs no server and no network.

The pin map is `docs/rebuild/MEGA_STANDARD.md` section 5 and nothing else.
If the sheet and that section ever disagree, the standard wins; fix the sheet.

It covers: every Mega pin used, each driver's J1/J2 pins and address straps
(X = 0, Y = 1, Z = 2), the shared PDN_UART junction (TX2 through 1 kohm, RX2
direct), the 12 V supply with a 100 uF capacitor at each VM, the common
ground, each axis's DB9 (limit switches, home photo-interrupter, coils), the
pin 4 vs pin 5 limit-return caveat, and the pre-power checklist.

Set the address straps before power. With them right, `#INFO` shows
`x_tmc=y_tmc=z_tmc=1`.
