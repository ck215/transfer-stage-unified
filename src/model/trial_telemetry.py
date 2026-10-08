"""Per-trial telemetry: every stream the station publishes or exposes, as
`(t, stream, value)` rows on ONE clock (REC-2; audit capture.md section 7).

    telemetry = TrialTelemetry(controller)          # clock=time.monotonic
    telemetry.start(trial_id)
    ...
    rows = telemetry.stop()     # [(t, "stepper_probe.z", -1520.0), ...], sorted by t

The default clock is `time.monotonic`, the clock of the screen recording's
`frames.csv` (`devices.screen_recorder`) and of a probe's `position_time`,
so the footage, the stage and the red percent line up without a conversion.

**What is recorded.** Three kinds of source, all stamped with `clock()` at
the moment the row is taken:

- Pushed: Red Percent's per-row publication (`subscribe`, on its run
  thread) as `<red>.row_red`, one row per row it logs; and every NEW
  EventLog line (`events.subscribe`) as `events.<severity>`, its text as the
  value. (The EventLog folds a repeat inside its dedupe window into the
  first line's count; a repeat is not a new line.)
- Polled at `poll_hz`, from every model the Controller holds, found by what
  it exposes (never by class, as the Transfer Map and Sample DB find their
  peers): a stage (`position`, `position_time`, `velocity`) gives x, y, z,
  vx, vy, vz, position_time, mode, full_speed, man_full_speed; a pad holder
  (`gamepad`, `gamepad_name`) gives gamepad_connected, gamepad_held; a
  rotator (`position_deg`) gives angle, motion; a heater (`heating_to`) gives
  temperature, setpoint, heating_to, heating (1.0 while a setpoint has been
  sent; the board reports no output duty, so there is none to record); Red
  Percent (`current_red`) gives red. A polled stream gets a row when its
  value changes, so a constant reading is one row, not one per poll.
- `telemetry.trial`: the trial id, at start.

Stream names are `<model>.<field>`, the model being its Controller name in
lower case with every other character an underscore (`Stepper Probe` ->
`stepper_probe`). Values are floats (bools as 1.0/0.0, numbers inside the
heater's text like "23.40 °C") or strings; a reading of None is "".

**It never raises into a model loop and never holds a model lock.** The
pushed hooks run on Red Percent's run thread and the publisher's thread:
each takes this object's own lock for one append and swallows anything that
goes wrong, counting it. Polling reads public attributes only, on this
object's own thread; a reader that raises is counted (`failures`,
`failure_counts`) and skipped for that poll. (A model's own property may
take that model's lock for the length of a copy - the Rotator's `position`
does; `Heater.history`, which copies its ring under a lock, is not read.)
`stop()` waits `STOP_TIMEOUT` for the poll thread, then abandons it
(`stop_timeouts`); a row from an abandoned poll never lands.
"""
import re
import threading
import time

from devices.gamepad import NEUTRAL
from events import events

SOURCE = "Trial Telemetry"
_NUMBER = re.compile(r"^\s*([-+]?\d+(?:\.\d+)?)")


def _slug(name):
    """`Stepper Probe` -> `stepper_probe`."""
    return re.sub(r"[^0-9a-z]+", "_", str(name).lower()).strip("_") or "model"


def _has(model, name):
    """Does `model` expose `name`? Without reading it: a property that
    raises or blocks is found here, then failed safely by its reader."""
    if any(name in vars(klass) for klass in type(model).__mro__):
        return True
    try:
        return name in vars(model)
    except TypeError:
        return False


def _value(raw):
    """A row's value: a float when the reading is a number (or a bool, or a
    numeric string), else its text. None is ""."""
    if raw is None:
        return ""
    if isinstance(raw, bool):
        return 1.0 if raw else 0.0
    if isinstance(raw, (int, float)):
        return float(raw)
    enum_value = getattr(raw, "value", None)
    if isinstance(enum_value, str):
        return enum_value
    text = str(raw)
    try:
        return float(text)
    except ValueError:
        return text


def _reading(text):
    """The number inside a reading worded with its unit ("23.40 °C" ->
    23.4); the text itself when there is none ("Disconnected", "")."""
    if text is None:
        return ""
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return float(text)
    match = _NUMBER.match(str(text))
    return float(match.group(1)) if match else str(text)


# -- readers: model -> [(field, raw)]; one failure skips one reader --------------

def _read_stage(model):
    x, y, z = model.position
    vx, vy, vz = model.velocity
    return [("x", x), ("y", y), ("z", z), ("vx", vx), ("vy", vy), ("vz", vz),
            ("position_time", model.position_time)]


def _read_mode(model):
    return [("mode", model.mode_name)]


def _read_speeds(model):
    return [(name, getattr(model, name)) for name in ("full_speed", "man_full_speed")
            if _has(model, name)]


def _read_gamepad(model):
    pad = model.gamepad
    if pad is None:
        return [("gamepad_connected", False), ("gamepad_held", False)]
    levels = pad.levels or {}
    held = any(abs(float(levels.get(key, rest)) - float(rest)) > 0.05
               for key, rest in NEUTRAL.items())
    return [("gamepad_connected", bool(pad.is_bound)), ("gamepad_held", held)]


def _read_tilt(model):
    pairs = [("angle", model.position_deg)]
    if _has(model, "motion_state"):
        pairs.append(("motion", model.motion_state))
    return pairs


def _read_heater(model):
    pairs = [("temperature", _reading(model.temperature)),
             ("heating_to", _reading(model.heating_to))]
    if _has(model, "setpoint"):
        pairs.append(("setpoint", model.setpoint))
    pairs.append(("heating", bool(model.is_active)))
    return pairs


def _read_red(model):
    return [("red", model.current_red)]


def _readers(model):
    """[(reader name, reader)] for what `model` exposes; [] for a model with
    nothing this records (the Transfer Map, the Sample DB)."""
    found = []
    if all(_has(model, a) for a in ("position", "position_time", "velocity")):
        found.append(("position", _read_stage))
        if _has(model, "mode_name"):
            found.append(("mode", _read_mode))
        found.append(("speeds", _read_speeds))
    if _has(model, "gamepad") and _has(model, "gamepad_name"):
        found.append(("gamepad", _read_gamepad))
    if _has(model, "position_deg"):
        found.append(("tilt", _read_tilt))
    if _has(model, "heating_to") and _has(model, "temperature"):
        found.append(("heater", _read_heater))
    if _has(model, "current_red"):
        found.append(("red", _read_red))
    return found


class TrialTelemetry:
    """`start(trial_id)`, `rows()`, `stop() -> rows`. One trial at a time;
    start again after a stop for the next."""

    #: What `stop()` gives the poll thread to finish its poll.
    STOP_TIMEOUT = 2.0

    def __init__(self, controller, clock=time.monotonic, *, poll_hz=20):
        poll_hz = float(poll_hz)
        if not poll_hz > 0:
            raise ValueError(f"poll_hz must be positive, not {poll_hz}")
        self._controller = controller
        self._clock = clock
        self._period = 1.0 / poll_hz
        self._lock = threading.Lock()
        self._generation = 0
        self._recording = False
        self._rows = []
        self._last = {}
        self._known = {}                   # id(model) -> (model, slug, readers)
        self._hooks = {}                   # id(model) -> (model, hook)
        self._thread = None
        self._stop_event = threading.Event()
        self.trial_id = None
        self.failure_counts = {}
        self.stop_timeouts = 0

    # -- what the caller reads ----------------------------------------------------------
    @property
    def is_recording(self):
        return self._recording

    @property
    def failures(self):
        with self._lock:
            return sum(self.failure_counts.values())

    def rows(self):
        """A snapshot of the rows so far (the last trial's after `stop`),
        sorted by t."""
        with self._lock:
            rows = list(self._rows)
        return sorted(rows, key=lambda row: row[0])

    # -- start / stop ---------------------------------------------------------------------
    def start(self, trial_id):
        """Begin recording `trial_id`. RuntimeError if one is recording."""
        with self._lock:
            if self._recording:
                raise RuntimeError(f"telemetry is recording trial {self.trial_id}; "
                                   "stop it first")
            self._generation += 1
            generation = self._generation
            self._recording = True
            self._rows, self._last, self._known = [], {}, {}
            self.failure_counts = {}
            self.trial_id = trial_id
        self._record(generation, self._clock(), "telemetry.trial", str(trial_id),
                     dedupe=False)
        events.subscribe(self._on_event)
        self._stop_event = threading.Event()
        self._thread = threading.Thread(target=self._loop,
                                        args=(generation, self._stop_event),
                                        name="trial-telemetry", daemon=True)
        self._thread.start()
        events.debug("Telemetry", f"trial {trial_id}: polling at "
                     f"{1 / self._period:g} Hz", source=SOURCE)

    def stop(self):
        """End the recording; return its rows, sorted by t. Bounded by
        STOP_TIMEOUT. Idempotent: again, it returns the same rows."""
        with self._lock:
            if not self._recording:
                rows = list(self._rows)
                return sorted(rows, key=lambda row: row[0])
        events.unsubscribe(self._on_event)
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(self.STOP_TIMEOUT)
            if thread.is_alive():
                self.stop_timeouts += 1
                events.debug("Telemetry Poll Hung", f"a read did not return within "
                             f"{self.STOP_TIMEOUT:g} s; the poll thread is "
                             "abandoned", source=SOURCE)
        with self._lock:
            self._recording = False
            self._generation += 1          # a late row from an abandoned poll is dropped
            rows = list(self._rows)
            failures = sum(self.failure_counts.values())
            hooks, self._hooks = list(self._hooks.values()), {}
        for model, hook in hooks:
            self._unhook(model, hook)
        events.debug("Telemetry", f"trial {self.trial_id}: {len(rows)} row(s), "
                     f"{failures} failed read(s)", source=SOURCE)
        return sorted(rows, key=lambda row: row[0])

    # -- rows ---------------------------------------------------------------------------
    def _record(self, generation, t, stream, value, dedupe=True):
        with self._lock:
            if generation != self._generation or not self._recording:
                return
            if dedupe:
                if stream in self._last and self._last[stream] == value:
                    return
                self._last[stream] = value
            self._rows.append((t, stream, value))

    def _count_failure(self, key, exc):
        with self._lock:
            self.failure_counts[key] = self.failure_counts.get(key, 0) + 1
        events.debug("Telemetry Read Failed", f"{key}: {exc!r}", source=SOURCE,
                     every=5.0)

    # -- pushed: the EventLog and Red Percent, on THEIR threads -------------------------
    def _on_event(self, event):
        try:
            t = self._clock()
            self._record(self._generation, t, f"events.{event.severity}",
                         str(event.text), dedupe=False)
        except Exception as exc:          # never into the publisher
            self._count_failure("events", exc)

    def _red_row_hook(self, slug, generation):
        stream = f"{slug}.row_red"

        def on_row(*args):
            """Red Percent's `fn(t_s, red, positions)`, on its run thread."""
            try:
                t = self._clock()
                self._record(generation, t, stream, _value(args[1]), dedupe=False)
            except Exception as exc:      # never into the run loop
                self._count_failure(stream, exc)
        return on_row

    # -- polled: every model the Controller holds, on this thread -----------------------
    def _loop(self, generation, stop):
        next_tick = time.monotonic()
        while not stop.is_set():
            try:
                self._poll(generation)
            except Exception as exc:      # never end the trial's record
                self._count_failure("poll", exc)
            next_tick += self._period
            now = time.monotonic()
            if next_tick < now:           # a slow poll: skip, never burst
                next_tick = now
            if stop.wait(max(0.0, next_tick - now)):
                break

    def _poll(self, generation):
        models = self._controller.models
        for name, model in models.items():
            entry = self._known.get(id(model))
            if entry is None or entry[0] is not model:
                entry = (model, _slug(name), _readers(model))
                self._known[id(model)] = entry
                self._hook_red(model, entry[1], generation)
            _model, slug, readers = entry
            for reader_name, reader in readers:
                if generation != self._generation:
                    return
                try:
                    t = self._clock()
                    pairs = reader(model)
                except Exception as exc:
                    self._count_failure(f"{slug}.{reader_name}", exc)
                    continue
                for field, raw in pairs:
                    self._record(generation, t, f"{slug}.{field}", _value(raw))

    def _hook_red(self, model, slug, generation):
        """Subscribe to Red Percent's rows the first time it is seen."""
        if not (_has(model, "current_red") and callable(getattr(model, "subscribe", None))):
            return
        hook = self._red_row_hook(slug, generation)
        with self._lock:
            if generation != self._generation or id(model) in self._hooks:
                return
            self._hooks[id(model)] = (model, hook)
        try:
            model.subscribe(hook)
        except Exception as exc:
            with self._lock:
                self._hooks.pop(id(model), None)
            self._count_failure(f"{slug}.subscribe", exc)
            return
        with self._lock:                   # a stop that ran meanwhile missed it
            stale = generation != self._generation
        if stale:
            self._unhook(model, hook)

    @staticmethod
    def _unhook(model, hook):
        try:
            model.unsubscribe(hook)
        except Exception as exc:
            events.debug("Unsubscribe Failed", repr(exc), source=SOURCE,
                         exception=exc)
