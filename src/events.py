"""The one event log. Models and the Controller publish; views subscribe.

Popup policy lives here and nowhere else. An `error()` asks for an
acknowledged popup by default: failed commands, faults and unconfirmed stops.
A `warn()` is a tray line unless its title is in `ATTENTION`, the short list
of warnings the operator must not miss (a timeout that ended manual mode, a
link that stayed lost past its boot grace, a heater-off that was never sent);
those sites pass `ack=True`. `info()` is always a log line. Repeats of the same event
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
                 "needs_ack", "count", "first_seen", "last_seen", "action")

    def __init__(self, id, severity, source, title, message, exception,
                 needs_ack, now, action=None):
        self.id, self.severity, self.source = id, severity, source
        self.title, self.message, self.exception = title, message, exception
        self.needs_ack, self.count = needs_ack, 1
        self.first_seen = self.last_seen = now
        #: rb-restart R1: the acknowledgement dialog's second key, or None:
        #: {"label", "name", "command", "args"}. `name` is a model's name or
        #: `SETUP_PANEL`; the view runs it the way a press of a button on
        #: that panel runs, so refusals and confirmations show as usual.
        self.action = action

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
                "text": self.text, "last_seen": self.last_seen,
                "action": dict(self.action, args=list(self.action["args"]))
                if self.action else None}

    def __repr__(self):
        return f"<Event {self.id} {self.severity} {self.text!r}>"


#: Titles the views key on (round 8, ARCH-4). Models raise them by name so a
#: wording change cannot silently detach a view.
STOP_NOT_CONFIRMED = "Stop Not Confirmed"
IDLE_TIMEOUT_SOON = "Idle Timeout Soon"
IDLE_TIMEOUT = "Idle Timeout"
BROWSER_SILENT = "Browser Silent"
BROWSER_GONE = "Browser Gone - FULL STOP"
TEMPERATURE_DISCONNECTED = "Temperature Disconnected"
ROTATOR_UNREACHABLE = "Rotator Unreachable"
HEATER_OFF_NOT_SENT = "Heater Off Not Sent"
#: rb-restart R2: Setup's two update prompts. Each carries an action.
UPDATE_READY = "Update Ready"
RESTART_NEEDED = "Restart Needed"
#: Owner 2026-09-28 ("Flash should be unattended, as with the prev. script.
#: It can send a popup first"): the startup firmware check's one question;
#: its action, Flash now, runs the flash.
FIRMWARE_OUT_OF_DATE = "Firmware Out of Date"
#: Owner 2026-09-30 (L4): a model's serial link was lost (acknowledged; the
#: port stops the model and reconnects by itself), and came back (info).
LINK_LOST = "Connection Lost"
LINK_RESTORED = "Connection Restored"
#: L6: a probe's position snapped to (0,0,0) while enabled (warning only).
BOARD_RESET_SUSPECTED = "Board Reset Suspected"

#: The `name` an action (and the Web's `/api/run`) gives the Setup panel.
SETUP_PANEL = "__setup__"

#: The warnings that ask for an acknowledgement (rb-ack, owner 2026-09-28:
#: "more attention grabbing, similar to the popup for the latch release").
#: Each site raises its title with `warn(..., ack=True)`; a test holds the
#: sites and this set to each other. Deliberately NOT here: the idle
#: countdown (`IDLE_TIMEOUT_SOON`, a tray line with its Extend - a modal
#: every period would nag), `BROWSER_SILENT` (whoever could answer a modal
#: is not at the page), and every error (errors ask by default).
ATTENTION = frozenset({IDLE_TIMEOUT, TEMPERATURE_DISCONNECTED,
                       ROTATOR_UNREACHABLE, HEATER_OFF_NOT_SENT,
                       UPDATE_READY, RESTART_NEEDED, FIRMWARE_OUT_OF_DATE,
                       LINK_LOST, BOARD_RESET_SUSPECTED})


def _action(action, needs_ack):
    """`(label, name, command[, args])` -> the dict an Event carries, or None.
    An action is a key on the acknowledgement dialog, so it needs one."""
    if action is None:
        return None
    if not needs_ack:
        raise ValueError("an action rides only on a notice that asks for "
                         "acknowledgement (ack=True)")
    items = tuple(action)
    if len(items) not in (3, 4):
        raise ValueError("an action is (label, name, command) or "
                         "(label, name, command, args)")
    label, name, command = (str(item) for item in items[:3])
    args = list(items[3]) if len(items) == 4 else []
    return {"label": label, "name": name, "command": command, "args": args}


class EventLog:
    DEDUPE_SECONDS = 5.0

    def __init__(self, max_events=500, clock=time.time):
        self._lock = threading.Lock()
        self._events, self._recent, self._subscribers = [], {}, []
        self._max_events, self._clock, self._next_id = max_events, clock, 1
        self._debug_seen, self._file, self._file_lock = {}, None, threading.Lock()

    def error(self, title, message, *, source="", exception=None, ack=True,
              action=None):
        return self._publish("error", source, title, message, exception, ack,
                             _action(action, ack))

    def warn(self, title, message, *, source="", exception=None, ack=False,
             action=None):
        """A tray line; `ack=True` only for a title in `ATTENTION`. `action`
        (rb-restart R1) puts a second key on its dialog."""
        return self._publish("warning", source, title, message, exception, ack,
                             _action(action, ack))

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

    def flush_file(self):
        """Push what the log file holds to disk, keeping it open (a restart
        replaces the process without running any exit path)."""
        with self._file_lock:
            if self._file:
                try:
                    self._file.flush()
                    os.fsync(self._file.fileno())
                except (OSError, ValueError):
                    pass

    def close_file(self):
        with self._file_lock:
            if self._file:
                self._file.close()
            self._file = None

    def _write_file(self, severity, source, text, exception):
        with self._file_lock:
            if not self._file:
                return
            # L7: the date too, so a log read days later says which day.
            now = time.time()
            stamp = (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now))
                     + f".{int((now % 1) * 1000):03d}")
            line = (f"{stamp} {severity.upper():7} {threading.current_thread().name:24.24} "
                    f"[{source}] {text}\n")
            if exception is not None:
                line += "".join(traceback.format_exception(
                    type(exception), exception, exception.__traceback__))
            try:
                self._file.write(line)
            except (OSError, ValueError):
                pass

    def _publish(self, severity, source, title, message, exception, needs_ack,
                 action=None):
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
                              exception, needs_ack, now, action)
                self._next_id += 1
                self._events.append(event)
                del self._events[:-self._max_events]
                self._recent[key] = event
                is_new = True
            subscribers = list(self._subscribers)
        self._write_file(severity, source, f"{title}: {message}", exception)
        if is_new:  # a repeat updates the count; it neither re-prints nor re-notifies
            # The log file is the record and the tray the operator's view
            # (lead's ruling 2026-09-28): only errors reach the terminal,
            # unless STATION_ECHO_EVENTS=1 asks for every event there too.
            if severity == "error" or os.environ.get("STATION_ECHO_EVENTS") == "1":
                print(event.text, file=sys.stderr if severity == "error" else sys.stdout)
            for fn in subscribers:
                try:
                    fn(event)
                except Exception as exc:  # a broken view must not break a model
                    print(f"[EventLog] subscriber failed: {exc}", file=sys.stderr)
        return event

    def forget(self, title):
        """End the dedupe episode for every recent event titled `title`, so
        the next one is a NEW event that re-notifies (round 7, Web CCR 2):
        stop, clear, stop again inside the window used to fold the second
        "Stop Not Confirmed" into the first one's count, and the page's
        event feed never saw it. The Model calls this when its latch clears."""
        with self._lock:
            for key in [k for k in self._recent if k[2] == title]:
                del self._recent[key]

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
for _name in ("STOP_NOT_CONFIRMED", "IDLE_TIMEOUT_SOON", "IDLE_TIMEOUT", "BROWSER_SILENT",
              "BROWSER_GONE", "TEMPERATURE_DISCONNECTED", "ROTATOR_UNREACHABLE",
              "HEATER_OFF_NOT_SENT", "UPDATE_READY", "RESTART_NEEDED",
              "FIRMWARE_OUT_OF_DATE", "LINK_LOST", "LINK_RESTORED",
              "BOARD_RESET_SUSPECTED",
              "SETUP_PANEL", "ATTENTION"):
    setattr(events, _name, globals()[_name])
