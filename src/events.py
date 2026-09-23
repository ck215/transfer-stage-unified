"""The one event log. Models and the Controller publish; views subscribe.

Popup policy lives here and nowhere else: only `error()` may ask for an
acknowledged popup, and it is for failed commands, faults and unconfirmed
stops. `warn()` and `info()` go to the log panel. Repeats of the same event
inside `DEDUPE_SECONDS` collapse into one entry with a count, so a fault in a
60 Hz loop is one line, not a flood.

`debug()` is the diagnostic channel: it never reaches a view or the terminal.
It goes only to the log FILE, where every event of every severity is also
written with a timestamp, the thread name and the full traceback of any
exception. Log generously there: mode transitions, port open/close and
handshake results, stop-path latencies, every refusal, reconnects. `every=`
rate-limits a call site so a loop can report without flooding.
"""
import os
import sys
import threading
import time
import traceback


class Event:
    __slots__ = ("id", "severity", "source", "title", "message", "exception",
                 "needs_ack", "count", "first_seen", "last_seen")

    def __init__(self, id, severity, source, title, message, exception,
                 needs_ack, now):
        self.id, self.severity, self.source = id, severity, source
        self.title, self.message, self.exception = title, message, exception
        self.needs_ack, self.count = needs_ack, 1
        self.first_seen = self.last_seen = now

    @property
    def key(self):
        return (self.severity, self.source, self.title, self.message)

    @property
    def text(self):
        """One wording for all three views: `[Source] Title: message (xN)`."""
        head = f"[{self.source}] " if self.source else ""
        tail = f" (x{self.count})" if self.count > 1 else ""
        return f"{head}{self.title}: {self.message}{tail}"

    def to_dict(self):
        return {"id": self.id, "severity": self.severity, "source": self.source,
                "title": self.title, "message": self.message,
                "needs_ack": self.needs_ack, "count": self.count,
                "text": self.text, "last_seen": self.last_seen}

    def __repr__(self):
        return f"<Event {self.id} {self.severity} {self.text!r}>"


class EventLog:
    DEDUPE_SECONDS = 5.0

    def __init__(self, max_events=500, clock=time.time):
        self._lock = threading.Lock()
        self._events, self._recent, self._subscribers = [], {}, []
        self._max_events, self._clock, self._next_id = max_events, clock, 1
        self._debug_seen, self._file, self._file_lock = {}, None, threading.Lock()

    def error(self, title, message, *, source="", exception=None, ack=True):
        return self._publish("error", source, title, message, exception, ack)

    def warn(self, title, message, *, source="", exception=None):
        return self._publish("warning", source, title, message, exception, False)

    def info(self, title, message, *, source=""):
        return self._publish("info", source, title, message, None, False)

    def debug(self, title, message, *, source="", exception=None, every=0.0):
        """File only. `every` = at most one line per that many seconds for this
        (source, title), with the number suppressed since the last one."""
        now = self._clock()
        with self._lock:
            key = (source, title)
            last, skipped = self._debug_seen.get(key, (None, 0))
            if every and last is not None and now - last < every:
                self._debug_seen[key] = (last, skipped + 1)
                return
            self._debug_seen[key] = (now, 0)
        tail = f" (+{skipped} suppressed)" if skipped else ""
        self._write_file("debug", source, f"{title}: {message}{tail}", exception)

    def open_file(self, directory=None):
        """Start the log file. Called once by app.launch(). Returns its path."""
        directory = directory or os.path.join(
            os.environ.get("TRANSFER_STAGE_DATA_ROOT")
            or os.path.join(os.path.expanduser("~"), "transfer-stage-runs"), "logs")
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, time.strftime("station-%Y%m%d-%H%M%S.log"))
        with self._file_lock:
            self._file = open(path, "a", buffering=1, encoding="utf-8")
        self._write_file("info", "EventLog", f"log opened: {path}", None)
        return path

    def close_file(self):
        with self._file_lock:
            if self._file:
                self._file.close()
            self._file = None

    def _write_file(self, severity, source, text, exception):
        with self._file_lock:
            if not self._file:
                return
            stamp = time.strftime("%H:%M:%S") + f".{int((time.time() % 1) * 1000):03d}"
            line = (f"{stamp} {severity.upper():7} {threading.current_thread().name:24.24} "
                    f"[{source}] {text}\n")
            if exception is not None:
                line += "".join(traceback.format_exception(
                    type(exception), exception, exception.__traceback__))
            try:
                self._file.write(line)
            except (OSError, ValueError):
                pass

    def _publish(self, severity, source, title, message, exception, needs_ack):
        now = self._clock()
        with self._lock:
            self._prune_recent(now)
            key = (severity, source, title, message)
            event = self._recent.get(key)
            if event is not None:
                event.count += 1
                event.last_seen = now
                is_new = False
            else:
                event = Event(self._next_id, severity, source, title, message,
                              exception, needs_ack, now)
                self._next_id += 1
                self._events.append(event)
                del self._events[:-self._max_events]
                self._recent[key] = event
                is_new = True
            subscribers = list(self._subscribers)
        self._write_file(severity, source, f"{title}: {message}", exception)
        if is_new:  # a repeat updates the count; it neither re-prints nor re-notifies
            print(event.text, file=sys.stderr if severity == "error" else sys.stdout)
            for fn in subscribers:
                try:
                    fn(event)
                except Exception as exc:  # a broken view must not break a model
                    print(f"[EventLog] subscriber failed: {exc}", file=sys.stderr)
        return event

    def _prune_recent(self, now):
        for key in [k for k, e in self._recent.items()
                    if now - e.last_seen > self.DEDUPE_SECONDS]:
            del self._recent[key]

    def subscribe(self, fn):
        with self._lock:
            if fn not in self._subscribers:
                self._subscribers.append(fn)

    def unsubscribe(self, fn):
        with self._lock:
            if fn in self._subscribers:
                self._subscribers.remove(fn)

    def since(self, event_id):
        """Non-destructive: every reader sees every event."""
        with self._lock:
            return [e for e in self._events if e.id > event_id]

    @property
    def latest_id(self):
        with self._lock:
            return self._next_id - 1

    def clear(self):
        with self._lock:
            self._events.clear()
            self._recent.clear()

    def hook_exceptions(self):
        """Route uncaught exceptions on any thread into the log. Idempotent."""
        if getattr(self, "_hooked", False):
            return
        self._hooked = True

        def _sys(exc_type, exc, tb):
            if issubclass(exc_type, KeyboardInterrupt):
                return sys.__excepthook__(exc_type, exc, tb)
            self.error("Unhandled Exception", f"{exc_type.__name__}: {exc}",
                       source="app", exception=exc)

        def _thread(args):
            if issubclass(args.exc_type, SystemExit):
                return
            name = args.thread.name if args.thread else "thread"
            self.error("Thread Crashed", f"{name}: {args.exc_type.__name__}: "
                       f"{args.exc_value}", source="app", exception=args.exc_value)

        sys.excepthook = _sys
        threading.excepthook = _thread


events = EventLog()
