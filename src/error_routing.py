"""The event bus every subsystem reports through (RC-8 items 3 and 4).

What this replaced. `ErrorRouter` was a class with three process-wide
callback slots and a `_last_messages` dict, deduplicating on the **message
text alone** for 5 s from the first send, with no lock, and running that
dedup *before* checking whether any callback was installed — so the fallback
`print` was silently rate-limited too (ERRORS-8, ERRORS-10). Installing a
view replaced the previous view's callbacks outright, which is why the
process-global state showed up in the test quarantine.

What it is now. One `EventBus` instance, held module-level but constructible
fresh (which is what lets a test isolate itself), with:

  * a monotonic `id` per event, so the Web client can ask `since=<id>`
    instead of destructively popping a buffer that a second browser tab
    would then never see (ERRORS-2, WEB-17);
  * a rate limit keyed on `(severity, source, title)` rather than the
    message text, which **collapses repeats into one event carrying a
    `count`** instead of dropping them. I-8.2 wants a fault that persists
    for 60 s to produce one event and a visible state, not twelve popups,
    and a dropped repeat would make the log lie about how long it lasted;
  * many subscribers rather than one callback trio, so a log panel and a
    modal handler coexist and installing a second view does not silence the
    first;
  * a lock around every mutation, with subscriber callbacks invoked
    **outside** it — a subscriber that publishes (an exception handler that
    itself fails is the realistic case) would otherwise deadlock.

Severity is one of `info`, `warning`, `error`. Only `error` may carry
`requires_ack`, and only that combination is allowed to raise a modal: RC-8
records "Temperature Send" raising an info popup on every send (TEMP-12) as
the shape to make unrepresentable.
"""

import threading
import time
import traceback

INFO = "info"
WARNING = "warning"
ERROR = "error"

SEVERITIES = (INFO, WARNING, ERROR)

#: Seconds during which a repeat of the same `(severity, source, title)` is
#: folded into the existing event instead of producing a new one. Errors hold
#: for a full minute because I-8.2 is stated in minutes; info is noisier and
#: less costly to repeat.
RATE_LIMIT_WINDOW = {INFO: 30.0, WARNING: 60.0, ERROR: 60.0}

#: How many events the bus keeps for `since()`. The Web client polls roughly
#: once a second, so this is minutes of history even under a fault storm.
MAX_EVENTS = 500


class Event:
    """One thing that happened, at one moment, from one place."""

    __slots__ = ("id", "severity", "source", "title", "message",
                 "exception", "requires_ack", "first_seen", "last_seen",
                 "count")

    def __init__(self, id, severity, source, title, message,
                 exception=None, requires_ack=False, now=None):
        now = time.time() if now is None else now
        self.id = id
        self.severity = severity
        self.source = source
        self.title = title
        self.message = message
        self.exception = exception
        self.requires_ack = requires_ack
        self.first_seen = now
        self.last_seen = now
        self.count = 1

    @property
    def key(self):
        return (self.severity, self.source, self.title)

    def to_dict(self):
        """The wire form. `type` is spelled the way the Web client already
        reads it, so `app.js` did not need a parallel vocabulary."""
        return {
            "id": self.id,
            "type": self.severity,
            "severity": self.severity,
            "source": self.source,
            "title": self.title,
            "message": self.message,
            "exception": (f"{type(self.exception).__name__}: {self.exception}"
                          if self.exception is not None else None),
            "requires_ack": self.requires_ack,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "count": self.count,
        }

    def __repr__(self):
        tail = f" x{self.count}" if self.count > 1 else ""
        return (f"<Event #{self.id} {self.severity} "
                f"{self.source}/{self.title}{tail}>")


class EventBus:
    def __init__(self, max_events=MAX_EVENTS, clock=time.time):
        self._lock = threading.RLock()
        self._clock = clock
        self._max_events = max_events
        self._events = []
        self._subscribers = []
        self._next_id = 1
        self._recent = {}

    # -- publishing ----------------------------------------------------

    def publish(self, severity, source, title, message,
                exception=None, requires_ack=False):
        """Record an event and notify subscribers.

        Returns the `Event` — the new one, or the existing one whose `count`
        this call incremented. A repeat returns the original rather than
        `None` so a caller can still see its id and that it was folded.
        """
        if severity not in SEVERITIES:
            raise ValueError(
                f"severity must be one of {SEVERITIES!r}, got {severity!r}")
        if requires_ack and severity != ERROR:
            raise ValueError(
                "only an 'error' may set requires_ack; a modal for anything "
                "quieter is TEMP-12, the shape RC-8 set out to remove")

        now = self._clock()
        key = (severity, str(source), str(title))

        with self._lock:
            existing = self._recent.get(key)
            window = RATE_LIMIT_WINDOW.get(severity, 60.0)
            if existing is not None and now - existing.last_seen < window:
                existing.last_seen = now
                existing.count += 1
                return existing

            event = Event(self._next_id, severity, str(source), str(title),
                          str(message), exception, requires_ack, now=now)
            self._next_id += 1
            self._events.append(event)
            if len(self._events) > self._max_events:
                del self._events[:len(self._events) - self._max_events]
            self._recent[key] = event
            self._prune_recent(now)
            subscribers = list(self._subscribers)

        # Outside the lock, on purpose: a subscriber that publishes (a
        # failing exception handler) would otherwise re-enter and, from
        # another thread, deadlock.
        for fn in subscribers:
            try:
                fn(event)
            except Exception:
                # A broken subscriber must not take down the reporter. This
                # is the one place in the codebase allowed to swallow, and
                # it still prints.
                print(f"[EventBus] subscriber {fn!r} raised:")
                traceback.print_exc()

        if not subscribers:
            print(f"[{severity.upper()}] {source}/{title}: {message}")
            if exception is not None:
                traceback.print_exception(
                    type(exception), exception, exception.__traceback__)

        return event

    def _prune_recent(self, now):
        longest = max(RATE_LIMIT_WINDOW.values())
        stale = [k for k, e in self._recent.items()
                 if now - e.last_seen > longest]
        for k in stale:
            del self._recent[k]

    # -- subscribing ---------------------------------------------------

    def subscribe(self, fn):
        """Register `fn(event)`. Returns `fn`, so it can be unsubscribed."""
        with self._lock:
            if fn not in self._subscribers:
                self._subscribers.append(fn)
        return fn

    def unsubscribe(self, fn):
        with self._lock:
            if fn in self._subscribers:
                self._subscribers.remove(fn)

    @property
    def subscriber_count(self):
        with self._lock:
            return len(self._subscribers)

    # -- reading -------------------------------------------------------

    def since(self, event_id=0):
        """Every event with an id greater than `event_id`, oldest first.

        Non-destructive, which is the point: two browser tabs each track
        their own cursor and both see everything. `pop_errors` could not do
        that — whichever tab polled first consumed the error.
        """
        with self._lock:
            return [e for e in self._events if e.id > event_id]

    def latest_id(self):
        with self._lock:
            return self._events[-1].id if self._events else 0

    def snapshot(self):
        with self._lock:
            return list(self._events)

    def clear(self):
        """Drop all history, subscribers and rate-limit state."""
        with self._lock:
            self._events.clear()
            self._subscribers.clear()
            self._recent.clear()
            self._next_id = 1


#: The process bus. Module-level because there is one per process, but an
#: instance rather than class state so a test can build its own.
bus = EventBus()


def reset_bus():
    """Return the process bus to a clean state. For tests and relaunches."""
    bus.clear()


class ErrorRouter:
    """The reporting facade the models and controllers already call.

    Kept deliberately: roughly forty call sites across the models say
    `ErrorRouter.report_error(...)`, and rewriting them to touch the bus
    directly would have been a large diff that bought nothing. What changed
    is underneath — these publish to `bus` instead of invoking one global
    callback, and the text-keyed 5 s dedup is gone.

    `set_callbacks` is **not** here any more. It was the process-global slot
    RC-8 names; subscribing is the replacement, and it does not silence
    whoever subscribed first.
    """

    @staticmethod
    def report_error(title, message, exception=None,
                     source="app", requires_ack=False):
        return bus.publish(ERROR, source, title, message,
                           exception=exception, requires_ack=requires_ack)

    @staticmethod
    def report_warning(title, message, exception=None, source="app"):
        return bus.publish(WARNING, source, title, message,
                           exception=exception)

    @staticmethod
    def report_info(title, message, source="app"):
        return bus.publish(INFO, source, title, message)

    @staticmethod
    def subscribe(fn):
        return bus.subscribe(fn)

    @staticmethod
    def unsubscribe(fn):
        bus.unsubscribe(fn)

    @staticmethod
    def since(event_id=0):
        return bus.since(event_id)


def install_exception_hooks(target=None, tk_root=None):
    """Route every unhandled exception to the bus (RC-8 item 4).

    One function for all three launchers, where each view used to install its
    own `sys.excepthook` and nothing else. `threading.excepthook` was missing
    everywhere, so an exception in a poller thread printed to stderr and the
    operator never learned the loop had died (ERRORS-4, PYSIDE-15); Tk also
    missed `report_callback_exception`, which is where every exception raised
    inside a widget callback goes.

    `tk_root` is bound to the **process-lifetime root**, never the dashboard:
    the Tk popup loop used to die with the dashboard window (ERRORS-5,
    VIEW-TKINTER-2, MANAGER-17).
    """
    import sys

    target = bus if target is None else target

    def report(exc_type, exc_value, exc_tb, where):
        try:
            traceback.print_exception(exc_type, exc_value, exc_tb)
        except Exception:
            print(f"Unhandled exception: {exc_value!r}")
        try:
            target.publish(
                ERROR, where, "Unhandled Exception",
                f"An unexpected error occurred:\n\n{exc_value}",
                exception=exc_value, requires_ack=True)
        except Exception:
            # The bus itself failing must not mask the original traceback,
            # which has already been printed above.
            traceback.print_exc()

    sys.excepthook = lambda t, v, tb: report(t, v, tb, "main-thread")

    def thread_hook(args):
        if args.exc_type is SystemExit:
            return
        name = getattr(args.thread, "name", "thread")
        report(args.exc_type, args.exc_value, args.exc_traceback,
               f"thread:{name}")

    threading.excepthook = thread_hook

    if tk_root is not None:
        def tk_hook(exc_type, exc_value, exc_tb):
            report(exc_type, exc_value, exc_tb, "tk-callback")
        # Bound on the class as well as the instance: Toplevel widgets
        # dispatch through their own object, and only the class binding
        # catches a callback raised inside a dialog.
        tk_root.report_callback_exception = tk_hook
        try:
            type(tk_root).report_callback_exception = staticmethod(tk_hook)
        except (AttributeError, TypeError):
            # A mocked root in the Tk test harness; the instance binding
            # above is what those tests read anyway.
            pass

    return target
