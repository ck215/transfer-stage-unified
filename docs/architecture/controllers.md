# Controllers

`src/controller/` holds the two hardware-transport wrappers models depend on.
Neither is a "controller" in the MVC-C sense of routing UI events — that job
belongs to the view's `_execute_command`/schema dispatch (see
[views.md](views.md)). These are I/O adapters: one for gamepads (via
pygame/SDL), one for the Arduino serial protocol.

## `gamepad.ControllerPoller` (`src/controller/gamepad.py`, 801 lines)

**Cardinality:** one `ControllerPoller` per `BaseProbe` instance
(1 probe : 1 poller, composition — constructed in `BaseProbe.__init__`,
destroyed via `poller.close()`, never shared between probes). A device with
no controller assigned still gets a `ControllerPoller` object; it just never
successfully binds a physical joystick (`self.gamepad = None`).

**Process-wide shared state (not in this module):** pygame/SDL is a single
global C library context for the whole Python process, and it has exactly one
owner — the `input_service` singleton in `controller/input_service.py`, which
`gamepad.py` imports (line 21). `gamepad.py` holds no SDL state of its own.

The three module-level globals this section used to describe
(`_poller_lock`, `_active_poller_count`, `_ensure_pygame_video()`) no longer
exist, and their removal is RC-13:

- `_active_poller_count` never counted pollers. It was incremented on every
  successful *bind* — including re-binds by the same poller — and decremented
  once per `close()`, including for pollers that had never bound anything.
  `close()` called `pygame.quit()` at zero, so closing one device could tear
  SDL down under another poller that was still running (GAMEPAD-2).
- `_ensure_pygame_video()` existed to bring SDL back up after those
  `quit()`s. `root-causes.md` names it as an **anti-fix**: do not reintroduce
  it, and do not add call sites for it.

What `input_service` provides instead:

| Rule | API |
|---|---|
| SDL is initialised once, lazily | `ensure_init()` |
| SDL is torn down only at process exit | `shutdown()`, called from `lifecycle.shutdown()` (`src/lifecycle.py:69-70`) and nowhere else |
| Every SDL call happens under one re-entrant lock | `lock()` — a poll tick holds it for the whole tick, because pygame's joystick API is not thread-safe and a poller may own its own thread |
| Device handles are per owner | `acquire(owner_id, index)` / `release(owner_id)`; releasing one owner never touches another's handle |
| Claims are derived from real acquisitions | `claims()`, `index_for(owner_id)` |
| Enumeration has one formatter | `enumerate()` → `[(index, name)]`; `names()` → `"ID <n>: <name>"` |
| Presence is one locked read | `count()`, `is_index_connected(index)` |

A `ControllerPoller` still keeps the `active_claims` dict it was constructed
with and still writes `"None Detected"` into it on a failed bind or a
disconnect. That dict is the *view-facing label*, not the authoritative
registry — `input_service.claims()` is.

### Wrapper classes (composition, 1 poller : 0..1 wrapper)

`BaseGamepad` and its subclasses `XboxGamepad`, `BluetoothXboxGamepad`,
`LogitechF310Gamepad`, and `T16000MGamepad` (lines 50-227) each wrap one `pygame.joystick.Joystick`
and normalize its raw axis/button/hat layout into one dict shape via
`get_mapped_state()`:

```python
class BaseGamepad:
    def __init__(self, joystick: pygame.joystick.Joystick)
    def get_mapped_state(self) -> dict
    def update_overrides(self) -> None

class XboxGamepad(BaseGamepad):
    def __init__(self, joystick)
    def get_mapped_state(self) -> dict

class BluetoothXboxGamepad(XboxGamepad):
    def __init__(self, joystick)
    def get_mapped_state(self) -> dict

class LogitechF310Gamepad(BaseGamepad):
    def __init__(self, joystick)
    def _is_dinput_mode(self) -> bool
    def get_mapped_state(self) -> dict

class T16000MGamepad(BaseGamepad):
    def update_overrides(self) -> None
    def get_mapped_state(self) -> dict

def get_gamepad_wrapper(joystick: pygame.joystick.Joystick) -> BaseGamepad
```

`get_gamepad_wrapper(joystick)` (module function, line 229) picks the right subclass
by matching `joystick.get_name()`. `self.poller.gamepad` on `BaseProbe` is
one of these wrapper instances (or `None`), not the raw `pygame.joystick.Joystick`.

### `ControllerPoller` method inventory

| Method | Signature | Line | Ownership / side effect |
|---|---|---|---|
| `POLL_INTERVAL` | `int` (class attr) | 254 | 5 ms → ~200 Hz. The comment above it once read "50 times per second"; the owner ruled the **code** right and the comment wrong (D-12, 2026-09-20). Do not "optimise" it to match the manual command rate. |
| `__init__` | `(controllerID, active_claims: dict, process_name: str)` | 256 | Calls `_init_input_state()` then `_initialize_pygame_joystick(controllerID)` unconditionally. |
| `_init_input_state` | `() -> None` | 282 | Installs the latched-input fields (`_state_lock`, `_levels`, `_pending_edges`, `_latch_state`). Split out of `__init__` so a test can assemble a poller without touching hardware. |
| `_is_os_connected` | `() -> bool` | 294 | Platform-branched (linux/win32/darwin) liveness check. Asks `input_service.is_index_connected(self.controller_index)` first. It **does** touch `self.gamepad` on darwin — but only when `input_service.index_for(...)` says that handle is this index's, because during a swap `self.gamepad` is still the *previous* device's wrapper (GAMEPAD-19). |
| `_handle_disconnect` | `() -> None` | 345 | Reports a warning popup, clears `self.gamepad`, writes `"None Detected"` into `active_claims[process_name]`, discards latched levels/edges, calls `self.stop_polling()`. |
| `get_physical_controllers` | `() -> list[str]` | 357 | **Static-ish scan** — delegates to `input_service.names()`, so the index in each `"ID <n>: <name>"` label is the index `acquire()` takes. Backs the "⟳ Rescan controllers" UI button. |
| `set_controller` | `(controllerID) -> bool` | 364 | Rebinds this poller to a different physical controller. Resumes polling **iff this poller was polling before the rebind** — the gate is the prior `is_polling`, captured before `_initialize_pygame_joystick` tears the binding down, and it is deliberately *not* "does a `gui_root` exist" (GAMEPAD-21). Picking a controller does not by itself start driving the hardware. |
| `_resume_polling_if` | `(should_resume) -> None` | 380 | The one resume path, shared by `set_controller` and `change_controller`. Calls `start_polling()` with **no** `gui` argument on purpose: the scheduler choice stays inside `start_polling`. Do not reintroduce a `gui_root` requirement here. |
| `_initialize_pygame_joystick` | `(controllerID) -> bool` | 399 | `stop_polling()`, parses `controllerID` → index, checks for cross-device claim collisions via `active_claims`, then `input_service.acquire(process_name, index)` and wraps the handle. |
| `change_controller` | `(new_controller_id) -> bool` | 481 | Hot-swap to a new controller ID. **No callers in `src`** (grep-verified); `set_controller` is the live duplicate (GAMEPAD-17). Same resume gate, via `_resume_polling_if`. |
| `_next_generation` | `() -> int` | 506 | Retires every outstanding poll chain and returns the new loop identity (GAMEPAD-7). |
| `start_polling` | `(gui=None, log_updater=None, activity_callback=None)` | 512 | Starts polling if a gamepad is bound and it is not already polling. Takes a fresh generation, then drives the loop one of two ways: a `gui_root` that duck-types `after` clocks it (Tk), otherwise the poller starts its own daemon thread. **This is the only place that chooses between the two clocks.** |
| `_poll_forever` | `(generation) -> None` | 542 | The poller's own clock: `_poll_loop` + `sleep(POLL_INTERVAL)` until it is stopped, closed, or its generation goes stale. |
| `stop_polling` | `() -> None` | 548 | Clears `is_polling` **and** advances the loop generation, so a chain scheduled before the call cannot survive a restart that happens before it next runs. |
| `close` | `() -> None` | 557 | Idempotent (`self._closed` guard). Stops polling and releases *this owner's* device handle via `input_service.release(process_name)`. It does **not** touch SDL: there is no refcount and no `pygame.quit()` here (RC-13). |
| `_read_raw` | `() -> dict \| None` | 574 | The wrapper's mapped state, or `None` when no device is bound. Its `except pygame.error` guard is unreachable today — every wrapper only reads its own `prev_*` caches — and is written so a missing pygame module cannot turn some other exception into an `AttributeError` (GAMEPAD-17). |
| `_apply_deadzones` | `(state) -> state` (static) | 599 | Zeroes stick values inside the deadzone and snaps an idle trigger on the way into `_levels`. The threshold values themselves are owner/bench territory (S16, GAMEPAD-14) — do not take them from this document. |
| `_capture_state` | `() -> None` | 608 | Latches one tick: levels into `_levels`, and any **edge** on `EDGE_KEYS` (572) into `_pending_edges`. Called by the poll loop, never by a reader — that is what stops one reader consuming another's edge, and what lets a tap shorter than the read interval survive. |
| `poll_once` | `() -> None` | 629 | One poll tick, exposed for tests. |
| `read_levels` | `() -> dict` | 633 | Current continuous input. **Non-consuming**; any number of readers. |
| `drain_edges` | `() -> dict` | 641 | Pending discrete presses since the last drain, and clears them. **Single consumer.** |
| `get_mapped_state` | `() -> dict` | 651 | Levels plus pending edges, in the shape existing call sites expect. Returns `{}` — not a zeroed dict — when no gamepad is bound *or* `is_polling` is False. It **drains**, so it is a single-consumer read. |
| `flush_neutral` | `() -> None` | 677 | Rewrites the wrapper's axis caches to neutral and clears the latches. **No callers in `src`** as of this commit: the focus-loss path neutralises at the model instead, because the poll loop overwrites those caches from the hardware within one tick (GAMEPAD-8). |
| `_poll_loop` | `(generation=None) -> None` | 695 | One tick of the input loop, under `input_service.lock()`. Re-arms itself only through `gui_root.after` when the root duck-types `after`; otherwise `_poll_forever` owns the cadence. A stale generation retires here instead of re-arming (GAMEPAD-7), and a re-arm that raises (destroyed widget) is treated as a disconnect. |
| `_read_hardware_changes` | `(_log) -> None` | 759 | Logs whatever moved since the last tick and fires `activity_callback`. Caller holds the SDL lock. |

### SDL surface (why there is no `_ensure_pygame_video()` any more)

This section used to explain why three call sites funnelled through an
`_ensure_pygame_video()` helper that re-ran `pygame.init()` after each
`pygame.quit()`. That helper is gone, and so is the reason for it.

The "no video instance" errors it patched were the *aftermath* of a
`pygame.quit()` that should never have happened: `close()` tore down
process-wide SDL whenever its bind refcount hit zero, and
`connect_controller()` called `pygame.quit()` unconditionally to "restart
pygame" for one poller. Re-initialising SDL afterwards made the symptom
survivable and removed the pressure to fix the ownership, which is why
`root-causes.md` lists it in RC-13's anti-fix table. **Adding call sites for
it is the wrong direction.**

Current surface:

- `SDL_VIDEODRIVER=dummy`, `SDL_AUDIODRIVER=dummy`,
  `SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS=1` and `PYGAME_HIDE_SUPPORT_PROMPT`
  are set in **one** place, `controller/input_service.py`, before it imports
  pygame. Four copies of that block used to exist and disagreed with each
  other (MANAGER-18).
- SDL comes up once, in `input_service.ensure_init()`.
- SDL comes down once, in `input_service.shutdown()`, reached only from
  `lifecycle.shutdown()` at process exit.
- Closing a device releases that device's handle and nothing else.

## `serial.serial` (`src/controller/serial.py`, 267 lines; aliased `SerialArduino`, `Serial`)

**Cardinality:** one `serial` instance per probe/temperature model
(`self.serial_comm` / `self.serial_conn`), constructed in the model's
`__init__` from a port string, `None` if the port is `None`/`'None'`/absent.
`'SIM'` is a valid port value meaning "simulator mode" — `self.ser` stays
`None`, every send method's `_verify_serial()` guard short-circuits, and no
real I/O happens. This is what backs headless/CI test runs.

Thread safety: `self._lock` (an `RLock`) guards every `self.ser` read/write.

### Method inventory

| Method | Signature | Line | Purpose / notes |
|---|---|---|---|
| `__init__` | `(port='SIM', baud_rate=500000)` | 23 | Opens the port, then spends up to ~4.5s (1.5s settle + up to 3s polling `"s
"`) verifying the Arduino responds with a `"DEV:"` handshake. |
| `_verify_serial` | `(verbose=False) -> bool` | 102 | Guard used by every send/read method; `verbose` controls whether a popup fires on failure. |
| `read_position` | `() -> tuple[int,int,int] \| None` | 113 | Parses `"POS:x,y,z
"` lines out of a rolling buffer (capped at 1024 bytes). |
| `send_autonomous_command` | `(params: dict) -> None` | 152 | Sends the 12-field **text/ASCII** comma command (`command_code_manual`/`command_code_auton` are two of the twelve fields). |
| `send_manual_mode_command` | `(params: dict) -> None` | 187 | Sends a **binary struct** (`PACKET_FORMAT = '<BBffffffffff'`, 42 bytes) — a completely different wire format from the autonomous command. |
| `enable` | `() -> None` | 240 | Sends the single-char `'e'` command. Firmware sets `toff(4)`. |
| `disable` | `() -> None` | 249 | Sends the single-char `'d'` command. **This is the only thing that actually disables coil current** (firmware sets `toff(0)`). |
| `close` | `() -> None` | 259 | Closes `self.ser` if open. |

### Command-surface summary (cuts across serial.py + firmware)

| Wire command | Sent by | Firmware effect |
|---|---|---|
| `"s\n"` handshake | `serial.__init__`, `app_bootstrap.probe_device_at` | Firmware replies `"DEV: s"` (etc.) — identity only, no state change. |
| 12-field text command (`send_autonomous_command`) | `BaseProbe.send_stop_command`, `send_autonomous_command` | Sets motion targets/speeds; `command_code_manual=0, command_code_auton=0` zeroes them. **Never touches `toff`.** |
| 42-byte binary struct (`send_manual_mode_command`) | `BaseProbe.send_manual_mode_command` (per-tick, manual mode) | Firmware's binary-mode branch; `mode==0` calls `handleAllStop()` — zeroes speeds/mode flags, **also never touches `toff`**. |
| `'e'` | `serial.enable` | Firmware sets `toff(4)` on all three axis UART drivers (TMC2209) — the only thing that actually enables coil current. |
| `'d'` | `serial.disable` | Firmware sets `toff(0)` on all three axis UART drivers — the only thing that actually disables coil current. **This was broken (`toff(2)`, a nonzero/still-enabled value) until the 2026-09-18 fix** — see known-issues. |

The upshot: **"stop moving" and "de-energize the coils" are two independent
firmware actions**, sent by different commands. `BaseProbe._stop_and_disarm()`
correctly calls both (`send_stop_command()` then `serial_comm.disable()`),
which is why the fix had to land at both layers — a model that only zeroes
motion without also sending `'d'` would leave coils holding current
indefinitely.

---
*The `serial.py`/firmware sections were last verified against commit
`8267e21` (2026-09-18). The `gamepad.py` section above was re-verified
line-by-line against `src/controller/gamepad.py` on 2026-09-20 (GAMEPAD-20),
in its post-RC-13 state. Two subjects are deliberately **not** described
here because they are owner-verified at the bench in S16/RC-12: the
per-wrapper axis/button maps (including the T16000M Z and bumper binds and
the D-pad sign convention) and the deadzone values (GAMEPAD-11/12/13/14).*

## Deep-dive addendum (agy, 2026-09-18)

**Additions & Corrections:**
- Expanded UML-level detail for `gamepad.py` wrapper classes and `serial.py` methods with exact line numbers.
- Verified the "Command-surface summary" table against the actual current firmware (`stepper_firmware.ino` and `chuck_firmware.ino` commit 8267e21). The table is 100% correct: `handleAllStop()` zeroes speeds and state but does NOT touch `toff`; only the `'e'` and `'d'` commands toggle driver `toff` to truly enable/disable the TMC2209 coils.

**New Inconsistencies Found:**
- None in the command-surface contract. The firmware genuinely decouples motion-stop from coil-disable.

### Packet Format Struct Layout Trace (Second Pass)
- Python's `PACKET_FORMAT = '<BBffffffffff'` (2x `uint8`, 10x `float` via little-endian packing) results in exactly 42 bytes. 
- Firmware's `ManualControlPacket` utilizes `__attribute__((packed))` to strip padding, ensuring the fields map 1:1 with Python's layout.
- **Mismatch flagged:** There are no byte-level structural misalignments. However, there is an implicit type cast in the firmware: `x_stepSize`, `y_stepSize`, and `z_stepSize` are received as `float` (and packed as `float` by Python) but are immediately assigned to global `int` variables (e.g., `x_step_size = incomingPacket.x_stepSize;`) in `stepper_firmware.ino`.
