# Controllers

`src/controller/` holds the two hardware-transport wrappers models depend on.
Neither is a "controller" in the MVC-C sense of routing UI events — that job
belongs to the view's `_execute_command`/schema dispatch (see
[views.md](views.md)). These are I/O adapters: one for gamepads (via
pygame/SDL), one for the Arduino serial protocol.

## `gamepad.ControllerPoller` (`src/controller/gamepad.py`, 639 lines)

**Cardinality:** one `ControllerPoller` per `BaseProbe` instance
(1 probe : 1 poller, composition — constructed in `BaseProbe.__init__`,
destroyed via `poller.close()`, never shared between probes). A device with
no controller assigned still gets a `ControllerPoller` object; it just never
successfully binds a physical joystick (`self.gamepad = None`).

**Process-wide shared state (not per-instance!):** pygame/SDL itself is a
single global C library context for the whole Python process. Three module-
level globals coordinate multiple `ControllerPoller` instances against that
one shared context:
```python
_poller_lock: threading.Lock              # guards _active_poller_count
_active_poller_count: int                 # how many pollers currently exist
def _ensure_pygame_video() -> None        # pygame.init() if pygame is truthy
```
`_ensure_pygame_video()` is called at module import time, at the start of
`_initialize_pygame_joystick()`, at the start of `get_physical_controllers()`,
and immediately after the `pygame.quit()` in `close()` — see
"Firmware/SDL command surface" below for why this exists as a single
centralized call rather than four separate `pygame.init()` calls (fixed
2026-09-18, see [known-issues.md](known-issues.md)).

### Wrapper classes (composition, 1 poller : 0..1 wrapper)

`BaseGamepad` and its subclasses `XboxGamepad`, `BluetoothXboxGamepad`,
`LogitechF310Gamepad` (lines 44-200ish) each wrap one `pygame.joystick.Joystick`
and normalize its raw axis/button/hat layout into one dict shape via
`get_mapped_state()`:
```python
def get_mapped_state(self) -> dict:
    # {"x_axisStatus": float, "y_axisStatus": float,
    #  "z_axisStatusL": float, "z_axisStatusR": float,
    #  "dpad_LR": int, "dpad_UD": int, "LBumper": int, "RBumper": int}
```
`get_gamepad_wrapper(joystick)` (module function) picks the right subclass
by matching `joystick.get_name()`. `self.poller.gamepad` on `BaseProbe` is
one of these wrapper instances (or `None`), not the raw `pygame.joystick.Joystick`.

### `ControllerPoller` method inventory

| Method | Signature | Ownership / side effect |
|---|---|---|
| `__init__` | `(controllerID, active_claims: dict, process_name: str)` | Calls `_initialize_pygame_joystick(controllerID)` unconditionally — a poller always attempts a connection at construction time. |
| `_is_os_connected` | `() -> bool` | Platform-branched (linux/win32/darwin) liveness check, doesn't touch `self.gamepad`. |
| `_handle_disconnect` | `() -> None` | Reports a warning popup, clears `self.gamepad`, calls `self.stop_polling()`. |
| `get_physical_controllers` | `() -> list[str]` | **Static-ish scan**, not tied to `self`'s own binding — lists every joystick pygame currently sees. Backs the "⟳ Rescan controllers" UI button. |
| `set_controller` | `(controllerID) -> bool` | Rebinds this poller to a different physical controller; restarts polling if it was already running. |
| `connect_controller` | `() -> bool` | **Full pygame teardown+rebuild**: `pygame.quit()` then `_initialize_pygame_joystick(self.controllerID)`. This is the "reconnect" entry point — note it nukes the *entire* SDL context, not just this poller's joystick, since SDL state is process-global (see below). |
| `_initialize_pygame_joystick` | `(controllerID) -> bool` | The actual bind logic: parses `controllerID` → index, checks for cross-device claim collisions via `active_claims` (shared dict, one entry per `process_name`), constructs the `BaseGamepad` wrapper, increments `_active_poller_count`. |
| `close` | `() -> None` | Idempotent (`self._closed` guard). Decrements `_active_poller_count`; **only when it hits 0** does it call `pygame.joystick.quit()` + `pygame.quit()` — i.e. the last poller to close tears down SDL for every other poller too, if any still existed (in practice they shouldn't, since count reached 0). Re-establishes the dummy video driver immediately afterward via `_ensure_pygame_video()` (fixed 2026-09-18). |
| `get_mapped_state` | `() -> dict` | Delegates to `self.gamepad.get_mapped_state()` if bound, else a zeroed dict. |
| `start_polling` / `stop_polling` | `(gui, log_updater, activity_callback)` / `()` | Owns the actual input-polling loop (not shown above — thread or Qt-timer driven depending on caller); `is_polling` is the run flag. |

### Firmware/SDL command surface (why `_ensure_pygame_video()` exists)

Three previously-independent call sites each assumed pygame's video
subsystem (`SDL_VIDEODRIVER=dummy`, set via `os.environ` at module import)
was already up, without guaranteeing it:
1. `_initialize_pygame_joystick()` — reconnect/construct path.
2. `get_physical_controllers()` — the rescan-button path.
3. `close()`'s post-`pygame.quit()` state — nothing re-armed the dummy
   driver until *something* happened to call #1 or #2 again.

Since `pygame.quit()` tears down the video subsystem along with everything
else, and #3 never restored it, any poller construction or rescan that
happened while the SDL context was in this "just torn down" window hit a
"no video instance" error. All three now funnel through one
`_ensure_pygame_video()` helper (`pygame.init()`, cheap/idempotent when
already up) called at construction, at rescan, and immediately after every
`pygame.quit()` — see [known-issues.md](known-issues.md) for the fix history
(this took two rounds: an initial narrower fix only covered #1, the
generalized fix covers all three).

## `serial.serial` (`src/controller/serial.py`, 267 lines; aliased `SerialArduino`, `Serial`)

**Cardinality:** one `serial` instance per probe/temperature model
(`self.serial_comm` / `self.serial_conn`), constructed in the model's
`__init__` from a port string, `None` if the port is `None`/`'None'`/absent.
`'SIM'` is a valid port value meaning "simulator mode" — `self.ser` stays
`None`, every send method's `_verify_serial()` guard short-circuits, and no
real I/O happens. This is what backs headless/CI test runs.

Thread safety: `self._lock` (an `RLock`) guards every `self.ser` read/write.

### Method inventory

| Method | Signature | Purpose / notes |
|---|---|---|
| `__init__` | `(port='SIM', baud_rate=500000)` | Opens the port, then spends up to ~4.5s (1.5s settle + up to 3s polling `"s\n"`) verifying the Arduino responds with a `"DEV:"` handshake before considering the connection "verified" — this is the same handshake shape `app_bootstrap.probe_device_at()` uses for autodetection (see [ownership-and-lifecycle.md](ownership-and-lifecycle.md)). Reports `report_info`/`report_warning` on the outcome — already well-instrumented. |
| `_verify_serial` | `(verbose=False) -> bool` | Guard used by every send/read method; `verbose` controls whether a popup fires on failure. |
| `read_position` | `() -> tuple[int,int,int] \| None` | Parses `"POS:x,y,z\n"` lines out of a rolling buffer (capped at 1024 bytes to prevent unbounded growth if a newline is ever missed). |
| `send_autonomous_command` | `(params: dict) -> None` | Sends the 12-field **text/ASCII** comma command (`command_code_manual`/`command_code_auton` are two of the twelve fields). This is what `send_stop_command()` uses to zero motion targets — **it does not touch the driver enable pin**, see below. |
| `send_manual_mode_command` | `(params: dict) -> None` | Sends a **binary struct** (`PACKET_FORMAT = '<BBffffffffff'`, 42 bytes) — a completely different wire format from the autonomous command, parsed by the firmware's separate binary-mode branch. |
| `enable` / `disable` | `() -> None` (raises `ValueError` if not connected) | Sends the single-char `'e'`/`'d'` commands. **`disable()` is the only thing that can physically cut stepper coil current** — see the firmware fix history in [known-issues.md](known-issues.md). No ACK/confirmation is read back for either — fire-and-forget over serial (flagged as a known gap in `probes.py`'s own comments, "the serial ACK-verification gap tracked separately" — not yet resolved). |
| `close` | `() -> None` | Closes `self.ser` if open. |

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
*Last verified against commit `8267e21` (2026-09-18).*
