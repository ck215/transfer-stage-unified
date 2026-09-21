# Rebuild brief — read this first, all of it

We are rebuilding the backend of a lab-instrument control app as a new package,
`station/`, beside the old `src/`. The old code stays in place as your reference
and is deleted at cutover. **Firmware is not touched and every byte on the wire
must stay identical to what `src/` sends today.**

## The hierarchy (this is the design; do not improvise around it)

    app.main() -> view(controller, setup)
    Setup      constructs Models into the Controller (autodetect: port scan,
               handshake identity byte, gamepad list)
    Model      owns its Devices (SerialPort, Gamepad, SMC100, Screen); opens and
               closes them on demand; reports their status in `state`
    Controller owns the Models; add() constructs, remove() destructs, reopen()
               re-adds from the remembered config; run() is the only way a
               command reaches a model; estop_all() is the per-model estops wired
    View       holds the Controller and NOTHING else from the backend:
               schema(name), state(name), run(name, command, inputs, args)

Import rules (one test enforces them, `tests/station/test_architecture.py`):
- `station/views/**` imports only: station.controller, station.setup (type only),
  station.result, station.schema, station.events, station.views.*. Never a model
  or a device.
- `station/models/**` imports station.model, station.panel, station.param,
  station.schema, station.events, station.result, station.devices.*. Never a view,
  never the Controller.
- Only `devices/serial_port.py` imports pyserial. Only `devices/gamepad.py` imports
  pygame. Only `devices/screen.py` imports mss.

## What already exists and is FROZEN (the lead owns it; do not edit)

`station/result.py`, `events.py`, `param.py`, `schema.py`, `panel.py`, `model.py`,
`controller.py`, `devices/device.py`, `views/theme.py`, `views/base.py`.
Read them before writing a line: they are short and they are the contract.
If you need a change in one, do NOT edit it. Write the request in your handoff
under `## CORE CHANGE REQUESTS` with the exact diff you want, and work around it.

Key mechanics you must use, not re-implement:
- A command returns a value or raises `Refused(reason)` / `NeedsConfirm(prompt,
  command, inputs, args)`. Never return False/None to mean "refused".
- `Model.estop()` is inherited. You write `_halt_hardware()` only: the strongest
  stop the device has, on the PRIORITY lane, returning True when it landed.
- `self._guard("Move")` at the top of every motion/heat command, AND pass
  `abort_if=self._estop.is_set` to the device write so the check happens inside
  the lock.
- `Model.close()` is inherited and ordered. Implement `_start_threads`,
  `_stop_threads`, `disable`, `devices`.
- Every model's schema ends with `self._safety_section()`.
- Logging: `from station.events import events`. `events.info/warn` for the log
  panel. `events.error` ONLY for a fault or an unconfirmed stop (Panel.run already
  raises it for a failed command). **No `print()` anywhere in `station/`.** Nothing
  in a loop may publish per-iteration.
- Styling: only through `station.views.theme`. No colour literal or font size
  anywhere else in `station/views/`.

## Pinned cross-file interfaces (code against these even if the file is a stub)

    SerialPort(port, baud_rate=115200)        # port None or "SIM" -> SimulatedPort
      .open()                 non-blocking: connect + handshake on a worker thread
      .wait_open(timeout) -> bool
      .write(payload: bytes, *, priority=False, abort_if=None) -> bool
            True = written. False = abort_if() was true inside the lock, nothing
            written. Raises TransportError on I/O failure and marks the port LOST.
      .read_line(timeout=None) -> str | None
      .flush(timeout) ; .close() ; .is_open ; .identity -> str | None
      .state -> ConnectionState ; .status -> state.value
      SimulatedPort.writes -> list[bytes]   # every payload, in order (tests use it)

    hub = GamepadHub()                        # module-level singleton in gamepad.py
    Gamepad(owner_id, hub=hub)
      .open() starts the poll thread ; .close() stops it and releases the claim
      .options -> ["None", *unclaimed names] ; .bind(name_or_None) -> bool
      .is_bound ; .levels -> dict (the standard keys x_axisStatus, y_axisStatus,
      z_axisStatusL, z_axisStatusR, dpad_LR, dpad_UD, LBumper, RBumper)
      .drain_edges() -> dict ; .flush_neutral() ; .set_gate(bool) ; .is_gate_open
      .log -> list[str]

    Every Model: __init__(self, port=None, gamepad=None, sim=False); class attrs
      NAME, IDENTITY, NEEDS_PORT, NEEDS_GAMEPAD. `setup.MODEL_TYPES = {cls.NAME: cls}`.
    Probe: .port (SerialPort) .gamepad (Gamepad) .position -> (x, y, z)
      .position_age -> seconds since the last successful position read
      .velocity -> (x, y, z) ; .mode -> ProbeMode ; mode_name -> mode.value
    Controller.set_input_focus() reaches a model's gamepad through `.gamepad`.

## Naming scheme (enforced by a lint test)

- Properties are nouns, no parentheses. Booleans: is_/has_/can_/needs_.
- No `get_` prefix. No `_flag`, `_var`, `_status` suffixes.
- One verb, one meaning: open/close, enable/disable, halt (no latch),
  estop/clear_estop (latched), set_<thing>(value), start_run/end_run.
- Private helpers `_verb_noun`; thread bodies `_<x>_loop`; callbacks `on_<event>`;
  UI handlers `_on_<widget>_<event>`.
- No abbreviations (auton -> auto, ser -> port). "controller" means only the
  Controller; a joystick is a Gamepad.
- Exempt: Qt event handler names, the pyserial interface SimulatedPort imitates,
  SMC100 protocol method names.

## Your file

Your stub lists every public member (the contract, from `docs/rebuild/design.json`),
what old method(s) each came from, and a MUST SATISFY block: fixes from the old
audit ledger that live in the code you are replacing. **Each one is a bug that
returns if you do not port it.** Find the old fix in `src/` (the old code comments
cite the finding IDs), port the behaviour, and port or write a test for it.
`docs/rebuild/design.rules` has one line per old method saying where it went and why.

Trap: `src/` comments quote old broken code at length. A grep hit for a defect
is not evidence the defect exists. Read the line.

## Rules

- Touch ONLY your write set. Everything else belongs to someone working right now.
- Tests go in `tests/station/` in files named as your brief says. Port the
  relevant old tests from `tests/` (adapting names) rather than starting from
  nothing; they encode the fixed bugs. Run:
      python3 -m pytest tests/station -q -p no:cacheprovider -x --timeout 60 2>&1 | tail -15
  (drop --timeout if the plugin is missing). Do not run Qt tests: mark them
  `@pytest.mark.qt` and list them under UNVERIFIED. Never open a real serial
  port, real gamepad or a GUI window in a test.
- Commit in your worktree (one or a few commits, message `rebuild(<file>): ...`,
  ending with the line `Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>`).
  NEVER push. NEVER touch another branch or worktree.
- Hand back `<HANDOFF>` (path in your brief) with these sections:
  `## DONE` (members implemented, anything left NotImplemented and why),
  `## TESTS` (files, count, and the literal last line of pytest output),
  `## MUST-SATISFY` (each finding cluster: ported how, tested by which test name),
  `## UNVERIFIED`, `## CORE CHANGE REQUESTS`, `## NOTES FOR THE LEAD`.
  Report honestly: a `partly` with the reason is a good outcome; a claimed pass
  that did not run is not.
