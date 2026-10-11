# The Mega standard: frame protocol plus capability-gated features (2026-10-09)

Owner rulings, 2026-10-09:
- An XYZ stage on **one Mega 2560**, all three axes.
- It speaks the **Stepper Probe's frame protocol**, plus the XYZ features.
  These features "should become standard but just disabled if detected as
  not present in hardware".
- It is **its own device in the Probe family**: `XYZ Stage (Mega)`, beside
  the Teensy `XYZ Stage`, which stays as it is.
- The three TMC2209s sit on **one UART bus, at addresses 0-2**.
- Limit switches and home sensors are marked present by **EEPROM config,
  plus seen-once**. Drivers are auto-detected.

This file is the contract the firmware and the station build against. Where
code and this file disagree, say so in the handoff; do not silently pick.

## 1. The frame protocol stays byte for byte

Everything `firmware/stepper_firmware/stepper_firmware.ino` speaks today is
unchanged, in meaning and in bytes:

| Item | Bytes |
|---|---|
| Jog packet | `0xAA` + the 41-byte rest of `ManualControlPacket` |
| Enable | `e` |
| Disable | `d` |
| Identity query | `s` |
| Autonomous frame | 12 comma fields + `\n` |
| Position | the `POS:x,y,z` line every 100 ms |

The runaway guards stay as they are:
- the 250 ms jog dead-man;
- the 1 s hardware watchdog;
- `d` drives the EN pins high.

The station never sends a new byte to a board that did not advertise a
capability. `tests/test_wire_golden.py` stays 77.

## 2. Capabilities in the identity reply

A standard board answers `s` with:

```
DEV: <letter> caps=<token>[,<token>...]
```

- **Tokens:**
  - `ext1`: the extension channel below, protocol 1;
  - `log`: LOG levels;
  - `hostto`: HOSTTIMEOUT and HB;
  - `home`: HOME and ZERO per axis;
  - `limits`: limit interlock and events;
  - `tmc`: driver UART status;
  - `soft`: the soft travel limit, `#SOFTLIMIT` (added 2026-10-10, X-14; §4).
- **What may be listed:** a token is listed only when the firmware has the
  feature. Whether the hardware for it is present is reported per axis by
  `#INFO`, not here.
- **Old boards** answer `DEV: s` with no `caps=`, and get none of this.
- **Setup** already maps the first word to the model, so `DEV: m caps=...`
  names `XYZ Stage (Mega)`.
- **Letters:** `m` is the XYZ Mega's. Taken: s, d, c, t, x.

## 3. The extension channel: `#` lines

Only for a board whose caps include `ext1`.

- **Host to board:** one line, `#<CMD> [args]\n`. An old board would
  misread a `d`, `e`, `s` or a digit inside such a line, which is why it is
  never sent to one.
- **Board to host:** every extension line starts with `#`.
  - `#OK <CMD> k=v ...` and `#ERR <CMD> <reason>`: exactly one per command.
  - `#EVT <KIND> ...`: unsolicited.
  - POS lines and the identity reply are untouched.
- **Units:** counts and counts/s, the frame protocol's units. The XYZ stage
  is 1600 counts/mm (1 mm lead, 200 full steps, 8 microsteps), the same
  0.625 µm/count as the Stepper Probe.

| Command | Reply / effect |
|---|---|
| `#INFO` | `#OK INFO fw=<sketch> proto=1 caps=... axes=XYZ`, then per axis `x_tmc=0/1 x_limits=0/1 x_home=0/1` (and y_, z_), `x_ls1_end=-1/0/+1 x_ls2_end=...`, `x_homed=0/1`, `log=N hostto_ms=N` |
| `#AXISCFG <A> limits=0/1 home=0/1` | Writes EEPROM. Refused while enabled or moving (`#ERR AXISCFG busy`). Replies `#OK AXISCFG axis=A limits=.. home=.. stored=1` |
| `#LOG <0/1/2>` | `#OK LOG level=N`. Default 1. Level 2 adds `#EVT DBG ...` |
| `#HOSTTIMEOUT <ms>` | 250..5000. `#OK HOSTTIMEOUT ms=N`. Arms the host timeout; until it is sent, the board behaves as an old board (no host timeout outside the jog dead-man) |
| `#HB` | `#OK HB` (any received byte also counts as host activity) |
| `#HOME <A>` | Needs the drives enabled, autonomous idle and `<A>_home=1`. Replies `#OK HOME started` at once. Sequence as the Teensy axis firmware (PROTOCOL.md "HOME"): `#EVT HOME A phase=...`, then `#EVT HOMED A edge=<counts> pos=0` or `#EVT HOME FAIL A reason=...` |
| `#ZERO <A>` | Sets that axis's position to 0. Refused while it moves. `#OK ZERO axis=A`. Clears `homed` |
| `#STOP` | Same effect as the stop jog packet (mode 0). `#OK STOP` |
| `#SOFTLIMIT <A> [counts]` | Cap `soft` only. No count: a query. `0..160000` (100 mm): writes EEPROM, 0 = no limit. Refused while enabled or moving (`#ERR SOFTLIMIT busy`), as AXISCFG is. Replies `#OK SOFTLIMIT axis=A counts=N ref=0/1`, then ` ls1=<counts>` when referenced, ` lim=<counts>` when referenced with a limit set, ` damaged=1`, and ` stored=1` after a write. `#INFO` adds per axis `x_soft=N x_soft_ref=0/1`, and `x_soft_lim=`, `x_soft_damaged=1` as the reply does |

**Errors** use the Teensy firmware's reason words where the meaning is the
same: `busy`, `bad-arg`, `not-enabled`, `no-home-sensor`,
`limit-lsN-end-unknown:jog-off-it`, `limit-lsN-pressed-both-ways:check-switch`,
`soft-limit`, `soft-limit-unreferenced:touch-ls1`, `soft-limit-eeprom-damaged:set-SOFTLIMIT`.

## 4. Safety: the XYZ features, per axis, when present

The same rules as `firmware/xyz_stage_axis/PROTOCOL.md` "Safety behaviour",
in counts:

- **Limit interlock in interrupt context.** A tripped switch stops that
  axis's pulses within one step tick (after a 200 µs filter). Each switch's
  end is learned on release while moving (it guards -dir), or on a trip from
  clear while moving (it guards dir). A re-trip after reading pressed with
  its end unknown halts and teaches nothing. Both switches of one axis
  pressed at once halts that axis.
- **Parked switch** (pressed, end unknown): only a jog moves that axis,
  capped at 160 counts/s (0.1 mm/s). After 800 counts (0.5 mm, PROVISIONAL)
  with the switch still pressed, the board halts and learns that end, and
  emits `#EVT LIMIT A lsN learned=travel`. Out both ways:
  `pressed_both_ways=1`. Autonomous frames and `#HOME` that would move that
  axis are refused or not run. A refused frame emits
  `#EVT REFUSED A reason=...`, because the frame protocol has no reply.
- **Limit events:** `#EVT LIMIT A lsN pos=<counts> end=<+1/-1>`.
- **Host silent:** once `#HOSTTIMEOUT` is armed:
  - no byte for that window while moving stops all axes and emits
    `#EVT FAULT host-timeout`;
  - still silent 10 s later, it drives EN high and emits
    `#EVT FAULT host-timeout-disabled`;
  - `e` is needed again.
- **Driver faults** read over the TMC bus (reset, short, over-temperature,
  UART lost) stop all axes, drive EN high and emit `#EVT FAULT <what> A`.
- **A feature whose hardware is absent is inert:**
  - `limits=0`: no interlock and no events for that axis. The old
    behaviour, and the station says so.
  - `home=0`: `#HOME` refused with `no-home-sensor`.
  - `tmc=0`: no driver diagnostics for that axis, `#EVT FAULT tmc-missing A`
    at boot, and that axis is never enabled.
- **Seen-once:** a switch with `limits=0` that trips while moving marks its
  axis's limits present for the session and emits
  `#EVT LIMIT A lsN seen=1`. It is never written to EEPROM.
- **Soft travel limit** (cap `soft`, 2026-10-10, X-14), for an axis whose
  carriage cannot reach one of its switches (a probe that meets its fixture
  first). `#SOFTLIMIT A <counts>` is the most it may travel from LS1. It is
  stored in EEPROM, and is none by default.
  - **Reference:** the count at which LS1 tripped while the axis moved toward
    the end it guards (that end known, the interlock armed), taken in the step
    ISR: `#EVT SOFTLIMIT A referenced ls1=<counts> lim=<counts>`. The limit
    lies `counts` from it, away from that end. Each such trip refreshes it.
    `#ZERO` and HOME move it with the origin.
  - **Referenced:** no step past the limit, whatever started the motion (frame,
    jog, D-pad step, HOME search): it stops on it,
    `#EVT SOFTLIMIT A stopped pos=<counts> lim=<counts>`. A start further out
    from the limit is refused `soft-limit`. HOME takes it as an end.
  - **Not referenced** (since boot, `#AXISCFG A`, or a loss), with a limit set:
    only a jog moves that axis, or a frame or D-pad step toward LS1 once its end
    is known. Everything else is `soft-limit-unreferenced:touch-ls1`, `#HOME`
    included. A soft limit needs LS1: with `limits=0` the reference never comes.
  - **Lost** by homed's rule for the step count (a disable while the axis
    turned, a driver fault on it) and on `#AXISCFG A`:
    `#EVT SOFTLIMIT A lost reason=<why>`.
  - **A damaged EEPROM entry is not "none"** (an erased block is):
    `x_soft_damaged=1`, jogs only (`soft-limit-eeprom-damaged:set-SOFTLIMIT`)
    until `#SOFTLIMIT` writes it again. This is the opposite of AXISCFG's
    absent-is-inert, on purpose: the owner's limit is unknown, not absent.
  - **EEPROM:** the block follows AXISCFG's, `1 + 8 x axes` bytes: `0x5D`, a
    4-byte little-endian count per axis, then each count's complement.

## 5. The XYZ Mega's hardware map

| What | Mega pin(s) |
|---|---|
| Step / Dir / EN | X 32/33/34, Y 22/23/24, Z 42/43/44 (the Stepper Probe's map). EN is active-low |
| TMC2209 bus | **Serial2**. TX2 (16) through 1 kΩ to the shared PDN_UART junction; RX2 (17) direct to the junction |
| Driver addresses | Set by each driver's MS1/MS2 to VIO or GND: X = 0 (MS1 GND, MS2 GND), Y = 1 (MS1 VIO, MS2 GND), Z = 2 (MS1 GND, MS2 VIO). VIO = 5 V from the Mega |
| Limit switches (external interrupts) | X LS1 2, LS2 3; Y LS1 18, LS2 19; Z LS1 20, LS2 21 (the Mega's six INT pins; Serial1 and I2C are not used). Internal pull-ups; contacts NO by default (LIMITS NC/NO as on the Teensy) |
| Home photo-interrupters (pin-change interrupts) | X A8, Y A9, Z A10 (PCINT16-18) |
| Host | USB Serial at 500000 baud (the Probe family's `BAUD_RATE`) |

Run current, clamp, microsteps (8) and HOME speeds are the Teensy axis
firmware's values and stay the owner's bench values. The firmware reads them
from one constants block.

## 6. The station side

- **`Probe` base:** parses `caps=` from the identity. Only with `ext1`, it:
  - sends `#INFO`, `#LOG 2` and `#HOSTTIMEOUT 1000` on link-up, then `#HB`
    every 250 ms;
  - routes `#` lines: `#EVT` is logged at debug, and LIMIT/FAULT/HOME are
    acted on as the XYZ Stage acts on them;
  - offers the standard features in the schema: per-axis Zero and Home, and
    homed and limit state.
- **Feature visibility:** each feature is shown only when caps list it, and
  it is disabled with a reason when `#INFO` says that axis's hardware is
  absent ("no home sensor on Y").
- **Soft limit (cap `soft`):** the page shows each axis's soft limit (from
  `#INFO`) and sets it with `#SOFTLIMIT` while the stage is disabled. It acts
  on `#EVT SOFTLIMIT ... stopped` as on a LIMIT (a Step in flight is stopped on
  every axis), and refuses a Step that would cross a referenced limit before
  sending it.
- **Old boards:** no caps means the station is exactly today's: same wire,
  same schema.
- **`XyzStageMega`:** a Probe-family model, `NAME = "XYZ Stage (Mega)"`,
  `IDENTITY = "m"`. It speaks the Stepper Probe's frame code and units (µm
  views over counts, step size times distance as the board moves it). Its
  own Setup row, flashing entry (`xyz_stage_mega`, mega) and tutorial.
- **Retrofit (phase 2, after the XYZ Mega is on the bench):** the Stepper
  Probe, DC and Chuck firmwares gain the same `#` layer.
  - The layer is a byte-identical copy of the shared header in each sketch
    folder; a test pins the copies equal.
  - With no limit or home hardware configured, those features are inert.

## 7. Bench checks (owner)

- Address straps: `#INFO` shows x_tmc=y_tmc=z_tmc=1.
- `#AXISCFG` persists across power cycles and uploads.
- Each limit switch trips its own axis only.
- Parked jog-off on each switch, then the 0.5 mm budget.
- HOME per axis, and its repeatability.
- The 10 s host-silent disable.
- With no `#` command sent, frames, jog packets, `e`/`d` and the POS stream
  behave exactly as on the Stepper Probe.
- Soft limit, on an axis that needs one: measure the travel from LS1 to the
  obstruction with none set, set it with a margin; a frame, the stick and a
  D-pad step each stop on it; it survives a power cycle and needs LS1 again
  after one.
