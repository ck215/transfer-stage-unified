"""The station's device log: one local SQLite file, verbose, across ALL
devices (owner ruling 2026-10-08: "a separate db maintains a verbose log of
such information across ALL devices which is stored locally ... It can also
be tied into the existing events system"). It replaces the heater-only
per-session CSV of P1; the heater's readings for a TRIAL live in that
trial's store (`model.transfer_map`, `trial_heater`).

    log = DeviceLog(controller)     # <data root>/logs/device_log.sqlite
    log.start()                     # app.launch, after the event log file
    ...
    log.close()                     # after the Controller has closed (<= 2 s)

**What it records.** Two tables, both stamped `t` (Unix seconds):

- `events`: every event the station's event log PUBLISHES (`events.
  subscribe`: info, warnings, errors; a repeat folded into its first line's
  count inside the dedupe window is not a new event), with its severity,
  source, title, message, the one-line text, the exception's repr and the
  publishing thread. `debug()` lines stay in the text log file
  (`logs/station-<stamp>.log`); they are never published.
- `readings`: every open model, sampled `SAMPLE_HZ` times a second on this
  log's own thread, as `(t, model, key, value, num)`: its mode, model mode,
  phase, `is_active` / `is_energized` / `is_estopped` / `is_faulted`, fault,
  each device's status (`device.<class>`), its serial link (`link.<field>`),
  every schema value (`values.<attr>`, the display text) and, for a model
  that keeps one (the heater's `last_reading`), its latest reading
  (`reading.<field>`). `value` is the text, `num` the number in it (or
  NULL). A key is written when it CHANGES, and every key of every model
  again every `KEYFRAME_S` (so any window has a starting value); `open` is
  1 while the model is open and 0 the poll it is gone.

**Never blocking.** Publishers (the event log's subscribers run on the
publisher's thread, a FULL STOP's included) do one append to a bounded
queue under a lock held only for appends and pops; a full queue drops its
OLDEST entry and counts it (`dropped`), and the writer records the count
as a `Device Log Overflow` row when it catches up. Only the writer thread
touches the disk. A writer stuck in the disk holds nothing but itself;
`close()` waits for it at most `CLOSE_BUDGET_S` and abandons it (a daemon).
A model whose `state` raises is skipped for that poll (`failures`).

**Where, and for how long.** `<data root>/logs/device_log.sqlite` (the
data root is `TRANSFER_STAGE_DATA_ROOT`, else `~/transfer-stage-runs`):
local, never on the cloud drive or any network mount (refused with one
warning, the station runs on without it), never part of a store's backup.
Kept `MAX_AGE_S` (14 days) or `MAX_BYTES` (200 MB), whichever comes first:
pruned at start and once a day, oldest first.
"""
import collections
import json
import os
import re
import sqlite3
import threading
import time
from pathlib import Path

from events import events as station_events
from model import store_choice

SOURCE = "Device Log"
FILE_NAME = "device_log.sqlite"
SAMPLE_HZ = 1.0
#: s between the snapshots that write every key, changed or not.
KEYFRAME_S = 60.0
#: Entries the queue holds before it drops its oldest (an entry is one
#: event, or one poll's changed readings).
QUEUE_MAX = 20_000
MAX_AGE_S = 14 * 86400
MAX_BYTES = 200 * 1024 * 1024
PRUNE_EVERY_S = 86400.0
#: s `close()` gives the writer to drain and close the file.
CLOSE_BUDGET_S = 2.0
#: Entries the writer takes per transaction.
BATCH = 500
#: s the idle writer sleeps before looking at the clock (the daily prune).
IDLE_WAIT_S = 5.0
#: Text kept per value; a longer one is cut.
VALUE_MAX = 200
#: The size cap removes this share of the oldest rows per pass.
PRUNE_SHARE = 10
SCHEMA_VERSION = 1
#: A reading's fields that change with every reading by construction (its
#: clocks): never logged as readings.
CLOCK_FIELDS = frozenset({"wall_epoch_s", "monotonic_s", "board_timer_s"})
#: The state flags a snapshot keeps, by name.
STATE_KEYS = ("mode", "model_mode", "phase", "is_active", "is_estopped",
              "is_faulted", "fault", "stop_confirmed")
LINK_KEYS = ("status", "losses", "reconnects", "dropped", "stalls")

_NUMBER = re.compile(r"^\s*([-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?)")
_CREATE = (
    "CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "t REAL NOT NULL, iso TEXT, severity TEXT, source TEXT, title TEXT, "
    "message TEXT, text TEXT, exception TEXT, thread TEXT)",
    "CREATE INDEX IF NOT EXISTS events_t ON events(t)",
    "CREATE TABLE IF NOT EXISTS readings (t REAL NOT NULL, model TEXT NOT NULL, "
    "key TEXT NOT NULL, value TEXT, num REAL)",
    "CREATE INDEX IF NOT EXISTS readings_t ON readings(t)",
    "CREATE INDEX IF NOT EXISTS readings_model_key_t ON readings(model, key, t)",
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)",
)
_EVENT_INSERT = ("INSERT INTO events (t, iso, severity, source, title, message, "
                 "text, exception, thread) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)")
_READING_INSERT = ("INSERT INTO readings (t, model, key, value, num) "
                   "VALUES (?, ?, ?, ?, ?)")


def data_root():
    configured = os.environ.get("TRANSFER_STAGE_DATA_ROOT")
    return (Path(configured).expanduser() if configured
            else Path.home() / "transfer-stage-runs")


def default_path():
    """`<data root>/logs/device_log.sqlite`, beside the text log files."""
    return data_root() / "logs" / FILE_NAME


def logical_size(path_or_db):
    """Bytes the database's live pages take (the file, less its free
    pages): what the size cap counts."""
    db = (path_or_db if isinstance(path_or_db, sqlite3.Connection)
          else sqlite3.connect(str(path_or_db)))
    try:
        pages = db.execute("PRAGMA page_count").fetchone()[0]
        free = db.execute("PRAGMA freelist_count").fetchone()[0]
        size = db.execute("PRAGMA page_size").fetchone()[0]
        return (pages - free) * size
    finally:
        if db is not path_or_db:
            db.close()


def _iso(t):
    return (time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t))
            + f".{int((t % 1) * 1000):03d}")


def _normal(raw):
    """A reading as `(text, number or None)`."""
    if raw is None:
        return "", None
    if isinstance(raw, bool):
        return ("true" if raw else "false"), (1.0 if raw else 0.0)
    if isinstance(raw, (int, float)):
        number = float(raw)
        return repr(raw), (number if number == number else None)
    if isinstance(raw, (dict, list, tuple)):
        try:
            text = json.dumps(raw, default=str, sort_keys=True)
        except (TypeError, ValueError):
            text = str(raw)
    else:
        enum_value = getattr(raw, "value", None)
        text = enum_value if isinstance(enum_value, str) else str(raw)
    text = text[:VALUE_MAX]
    match = _NUMBER.match(text)
    return text, (float(match.group(1)) if match else None)


def snapshot(model):
    """A compact snapshot of one model: `{key: raw}` (module docstring).
    Reads public attributes only; raises what they raise."""
    state = model.state
    snap = {"open": True}
    for key in STATE_KEYS:
        if key in state:
            snap[key] = state[key]
    energized = getattr(model, "is_energized", None)
    if energized is not None:
        snap["is_energized"] = bool(energized)
    for kind, status in (state.get("devices") or {}).items():
        snap[f"device.{kind}"] = status
    link = state.get("link")
    if isinstance(link, dict):
        for key in LINK_KEYS:
            if key in link:
                snap[f"link.{key}"] = link[key]
    for attr, value in (state.get("values") or {}).items():
        snap[f"values.{attr}"] = value
    reading = getattr(model, "last_reading", None)
    if isinstance(reading, dict):
        for field, value in reading.items():
            if field not in CLOCK_FIELDS:
                snap[f"reading.{field}"] = value
    return snap


class DeviceLog:
    """The log's two threads (writer, sampler) and its bounded queue. The
    Controller is read through `models` only."""

    def __init__(self, controller, path=None, *, event_log=None,
                 clock=time.time, sample_hz=SAMPLE_HZ, queue_max=QUEUE_MAX,
                 max_age_s=MAX_AGE_S, max_bytes=MAX_BYTES):
        self._controller = controller
        self.path = Path(path) if path is not None else default_path()
        self._events = event_log if event_log is not None else station_events
        self._clock = clock
        self._sample_hz = float(sample_hz or 0)
        self._queue_max = max(1, int(queue_max))
        self._max_age_s = max_age_s
        self._max_bytes = max_bytes
        self._cond = threading.Condition(threading.Lock())
        self._queue = collections.deque()
        self._busy = False
        self._ready = False
        self._stopping = False
        self._dropped_written = 0
        self._last_prune = None
        self._db = None
        self._writer = None
        self._sampler = None
        self._sampler_stop = threading.Event()
        self._life = threading.Lock()
        self._started = False
        self._closed = False
        self._close_result = True
        #: Per model: its last written readings and its last keyframe time.
        self._last = {}
        self._keyframe_at = {}
        #: Counters, for the bench and the tests.
        self.dropped = 0
        self.failures = 0
        self.write_failures = 0
        self.prunes = 0

    # -- what the caller reads -------------------------------------------------
    @property
    def is_running(self):
        writer = self._writer
        return writer is not None and writer.is_alive() and not self._stopping

    @property
    def queued(self):
        with self._cond:
            return len(self._queue)

    # -- life ---------------------------------------------------------------------
    def start(self):
        """Open the file and start the writer (and the sampler). True when
        it is logging; False (one warning) when it cannot: a remote folder,
        or a file that cannot be opened. Never raises."""
        with self._life:
            if self._started or self._closed:
                return self._started and not self._closed
            reason = store_choice.remote_reason(self.path)
            if reason:
                self._events.warn("Device Log Off", "The device log was not "
                                  f"started: {reason}", source=SOURCE)
                return False
            try:
                self._db = self._open()
            except (OSError, sqlite3.Error) as exc:
                self._events.warn("Device Log Off", "The device log could not "
                                  f"be opened at {self.path} ({exc}); the "
                                  "station runs on without it.", source=SOURCE)
                return False
            self._started = True
            self._writer = threading.Thread(target=self._run, daemon=True,
                                            name="device-log-writer")
            self._writer.start()
            self._events.subscribe(self._on_event)
            if self._sample_hz > 0:
                self._sampler = threading.Thread(target=self._sample_loop,
                                                 daemon=True,
                                                 name="device-log-sampler")
                self._sampler.start()
        station_events.debug("Device Log", f"logging to {self.path}",
                             source=SOURCE)
        return True

    def _open(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fresh = not self.path.exists()
        db = sqlite3.connect(str(self.path), timeout=5.0,
                             check_same_thread=False)
        try:
            if fresh:
                # Before the first table: lets a prune give pages back.
                db.execute("PRAGMA auto_vacuum = INCREMENTAL")
            db.execute("PRAGMA journal_mode = WAL")
            db.execute("PRAGMA synchronous = NORMAL")
            with db:
                for statement in _CREATE:
                    db.execute(statement)
                db.executemany("INSERT OR IGNORE INTO meta (key, value) "
                               "VALUES (?, ?)",
                               [("schema_version", str(SCHEMA_VERSION)),
                                ("created_at", _iso(self._clock()))])
                db.execute(_EVENT_INSERT, self._own_row(
                    "Device Log Opened", f"pid {os.getpid()}, {self.path}"))
        except BaseException:
            db.close()
            raise
        return db

    def close(self, timeout=CLOSE_BUDGET_S):
        """Stop sampling and listening, let the writer drain the queue and
        close the file, waiting at most `timeout` s in all. True when it
        finished in time. Idempotent; never raises."""
        with self._life:
            if self._closed:
                return self._close_result
            self._closed = True
            if not self._started:
                return True
        deadline = time.monotonic() + max(0.0, float(timeout))
        self._events.unsubscribe(self._on_event)
        self._sampler_stop.set()
        self._put(("event", self._own_row(
            "Device Log Closed", f"{self.dropped} entr(y/ies) dropped in all")))
        with self._cond:
            self._stopping = True
            self._cond.notify_all()
        sampler = self._sampler
        if sampler is not None and sampler is not threading.current_thread():
            sampler.join(max(0.0, min(0.2, deadline - time.monotonic())))
        writer = self._writer
        if writer is not None and writer is not threading.current_thread():
            writer.join(max(0.0, deadline - time.monotonic()))
        self._close_result = writer is None or not writer.is_alive()
        if not self._close_result:
            station_events.debug("Device Log Close Abandoned", "the writer "
                                 f"did not finish within {timeout:g} s; "
                                 f"{self.queued} entr(y/ies) not written",
                                 source=SOURCE)
        return self._close_result

    def flush(self, timeout=2.0):
        """Wait until everything queued is written (tests, the bench). True
        when it was."""
        with self._cond:
            return self._cond.wait_for(
                lambda: (self._ready and not self._queue and not self._busy)
                or self._writer is None or not self._writer.is_alive(),
                timeout) and not self._queue

    def _wake(self):
        with self._cond:
            self._cond.notify_all()

    # -- the queue: every producer's one call ----------------------------------------
    def _put(self, item):
        with self._cond:
            if len(self._queue) >= self._queue_max:
                self._queue.popleft()
                self.dropped += 1
            self._queue.append(item)
            self._cond.notify()

    def _own_row(self, title, message):
        t = self._clock()
        return (t, _iso(t), "info", SOURCE, title, message,
                f"[{SOURCE}] {title}: {message}", None,
                threading.current_thread().name)

    # -- producers ------------------------------------------------------------------
    def _on_event(self, event):
        """An EventLog subscriber, on the publisher's thread: one append."""
        try:
            t = self._clock()
            exc = event.exception
            self._put(("event", (
                t, _iso(t), event.severity, event.source, event.title,
                str(event.message), event.text,
                None if exc is None else repr(exc),
                threading.current_thread().name)))
        except Exception:
            self.failures += 1             # never into the publisher

    def _sample_loop(self):
        period = 1.0 / self._sample_hz
        while not self._sampler_stop.wait(period):
            try:
                self.sample_once()
            except Exception as exc:       # never end the log
                self.failures += 1
                station_events.debug("Device Log Sample Failed", repr(exc),
                                     source=SOURCE, every=30.0)

    def sample_once(self):
        """One poll of every open model: the readings that changed (all of
        them at a keyframe) as one queue entry."""
        now = self._clock()
        try:
            models = dict(self._controller.models)
        except Exception as exc:
            self.failures += 1
            station_events.debug("Device Log Sample Failed", repr(exc),
                                 source=SOURCE, every=30.0)
            return
        rows = []
        for name, model in models.items():
            try:
                snap = {key: _normal(raw) for key, raw in snapshot(model).items()}
            except Exception as exc:
                self.failures += 1
                station_events.debug("Device Log Snapshot Failed",
                                     f"{name}: {exc!r}", source=SOURCE,
                                     every=30.0)
                continue
            last = self._last.setdefault(name, {})
            keyframe = now - self._keyframe_at.get(name, -1e18) >= KEYFRAME_S
            for key, (text, number) in snap.items():
                if keyframe or last.get(key) != (text, number):
                    rows.append((now, name, key, text, number))
            last.update(snap)
            if keyframe:
                self._keyframe_at[name] = now
        for name in [n for n in self._last if n not in models]:
            rows.append((now, name, "open", "false", 0.0))
            self._last.pop(name, None)
            self._keyframe_at.pop(name, None)
        if rows:
            self._put(("readings", rows))

    # -- the writer ---------------------------------------------------------------------
    def _run(self):
        db = self._db
        try:
            self._prune(db)
            with self._cond:
                self._ready = True
                self._cond.notify_all()
            while True:
                with self._cond:
                    if not self._queue and not self._stopping:
                        self._cond.wait(IDLE_WAIT_S)
                    batch = [self._queue.popleft()
                             for _ in range(min(BATCH, len(self._queue)))]
                    lost = self.dropped - self._dropped_written
                    self._dropped_written = self.dropped
                    stopping = self._stopping
                    self._busy = bool(batch or lost)
                if lost:
                    batch.append(("event", self._own_row(
                        "Device Log Overflow", f"{lost} entr(y/ies) dropped: "
                        "the queue was full (the oldest go first)")))
                try:
                    if batch:
                        self._write_batch(db, batch)
                    if self._clock() - self._last_prune >= PRUNE_EVERY_S:
                        self._prune(db)
                except sqlite3.Error as exc:
                    self.write_failures += 1
                    station_events.debug("Device Log Write Failed", repr(exc),
                                         source=SOURCE, every=30.0)
                finally:
                    with self._cond:
                        self._busy = False
                        self._cond.notify_all()
                if stopping:
                    with self._cond:
                        if not self._queue:
                            break
        except Exception as exc:
            station_events.debug("Device Log Writer Failed", repr(exc),
                                 source=SOURCE, exception=exc)
        finally:
            try:
                db.close()
            except sqlite3.Error:
                pass
            with self._cond:
                self._cond.notify_all()

    def _write_batch(self, db, batch):
        """One transaction for a batch of queue entries."""
        event_rows, reading_rows = [], []
        for kind, payload in batch:
            if kind == "event":
                event_rows.append(payload)
            else:
                reading_rows.extend(payload)
        with db:
            if event_rows:
                db.executemany(_EVENT_INSERT, event_rows)
            if reading_rows:
                db.executemany(_READING_INSERT, reading_rows)

    def _prune(self, db):
        """Drop what is older than the age cap, then the oldest rows until
        the live pages fit the size cap; give the pages back."""
        now = self._clock()
        cutoff = now - self._max_age_s
        with db:
            removed = db.execute("DELETE FROM events WHERE t < ?",
                                 (cutoff,)).rowcount
            removed += db.execute("DELETE FROM readings WHERE t < ?",
                                  (cutoff,)).rowcount
        for _ in range(64):
            if logical_size(db) <= self._max_bytes:
                break
            with db:
                taken = 0
                for table in ("readings", "events"):
                    n = db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    if n:
                        taken += db.execute(
                            f"DELETE FROM {table} WHERE rowid IN (SELECT rowid "
                            f"FROM {table} ORDER BY t LIMIT ?)",
                            (max(1, n // PRUNE_SHARE),)).rowcount
            removed += taken
            if not taken:
                break
        try:
            db.execute("PRAGMA incremental_vacuum")
        except sqlite3.Error:
            pass
        self._last_prune = now
        self.prunes += 1
        if removed:
            station_events.debug("Device Log Pruned", f"{removed} row(s) older "
                                 f"than {self._max_age_s / 86400:g} days or "
                                 f"past {self._max_bytes / 1e6:g} MB",
                                 source=SOURCE)
        return removed
