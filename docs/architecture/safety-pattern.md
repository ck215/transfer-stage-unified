# The emergency-stop pattern

S8 built this for the probes. The rotator and the heater did not have a
weaker version of it — they had none, and that gap survived three stages
because the invariant's test built a probe. It is written down here so the
fourth subsystem matches the first three, and so conformance can be checked
rather than remembered.

Applies to anything that can energize a coil or move an axis.

## The contract

**1. Latch before any I/O.**

```python
self._estop = threading.Event()      # in __init__
```

`emergency_stop` sets it first, before touching the transport. Every motion
path checks it immediately before its write. The latch is what actually
orders a stop against work already in flight — the hardware write is not,
because it can be queued behind a transaction.

**2. Never block the caller.** `emergency_stop` frequently runs on the UI
thread. Dispatch the hardware write to a daemon worker and join with a bound:

```python
ESTOP_RETURN_BUDGET = 0.08

def emergency_stop(self):
    self._estop.set()
    done = threading.Event()
    def _stop():
        try: self.stop(priority=True)
        finally: done.set()
    threading.Thread(target=_stop, daemon=True,
                     name=f"estop-{self.__class__.__name__}").start()
    if not done.wait(self.ESTOP_RETURN_BUDGET):
        print(f"[{self.__class__.__name__}] FULL STOP: latched; hardware "
              f"stop still in flight after {self.ESTOP_RETURN_BUDGET}s")
```

A wedged transport must not be able to hold the operator's stop button.

**3. The priority write path.** A stop that cannot get the lock is worse than
an unsynchronised one:

```python
PRIORITY_LOCK_TIMEOUT = 0.05

acquired = self._lock.acquire(timeout=self.PRIORITY_LOCK_TIMEOUT)
if not acquired:
    print("PRIORITY: lock busy, forcing the stop through")
try:
    ...write...
finally:
    if acquired:
        self._lock.release()
```

**This is only ever safe for a single idempotent byte or frame** — `ST`, `k`,
a zero-setpoint frame. The controller treats a repeated stop as a stop, so
the worst case is one mangled *stop*. **Never take this path for a motion
command**, where a mangled frame is a move to the wrong place.

**4. Re-check the latch inside the lock.** This is the one that was missed.
`TemperatureSystem.send_settings` tested `_estop` at the top, then spent ~40
lines validating and building the frame, then wrote — so a FULL STOP landing
in that window was overwritten and the heater returned to setpoint silently.
Check at the top if you like, but the check that *counts* is the one
immediately before the write, inside the lock that serialises it.

**5. Cleared only by an explicit operator action.** Nothing else may call
`clear_estop()` — not a reconnect, not a mode change, not a retry (RC-5).

**6. A stop makes derived state unknown.** The stage halts where it is, not
at the commanded target. `RotatorSystem.emergency_stop` drops
`_commanded_target`, so the next relative move refuses rather than computing
from a position that was never reached (ROTATOR-4).

## Where it lives

| File | Constants |
|---|---|
| `src/controller/serial.py` | `PRIORITY_LOCK_TIMEOUT` |
| `src/lib/smc100.py` | `PRIORITY_LOCK_TIMEOUT` |
| `src/model/probes.py` | `ESTOP_RETURN_BUDGET` |
| `src/model/rotator_system.py` | `ESTOP_RETURN_BUDGET` |
| `src/model/temperature_system.py` | both |

The values are duplicated literally at six sites, each re-explaining itself.
That is deliberate for now — a shared constants module would put a safety
threshold one import away from a subsystem that must not fail to have it —
but if a seventh appears, reconsider.

## Testing it

The fakes live in `tests/core/test_transport_truth.py`:

- **`StallingSMC`** — holds a real `threading.Lock()` as `_serial_lock`;
  `stop(priority=True)` returns immediately, `stop()` takes the lock.
- **`StallingHeaterTransport`** — ordinary writes block on a gate; its
  `is_open()` hook lands a FULL STOP *between* the top-of-method check and
  the write, which is how the check-then-act is reproduced.

Four things are worth asserting for any new subsystem:

1. `emergency_stop` returns within ~100 ms against a held lock.
2. The stop still reaches the hardware.
3. A move queued behind the stop does not land afterwards.
4. Only an explicit operator action clears the latch.

Verify each against the pre-fix code — see the `fix-a-finding` skill. For
ROTATOR-8 that check **deadlocked** rather than failing, which is the
strongest possible demonstration of the defect.
