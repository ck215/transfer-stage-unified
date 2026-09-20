"""Process-level shutdown (RC-1 item 3).

Before this module there were **zero** `atexit`, `signal` or `aboutToQuit`
handlers anywhere in `src` (MANAGER-2/3, SERIAL-15, VIEW-TKINTER-8). Ctrl-C
in the terminal, a SIGTERM from a supervisor, or a Dock "Quit" all ended the
process with hardware still enabled and serial ports still open — the models
were simply never told.

Two rules make this safe:

1. **One accessor, never a captured reference.** Handlers resolve the manager
   through `current_manager()` at the moment they fire. A launcher that
   captured its manager in a closure would keep stopping a stale one after a
   Web re-setup replaced it.
2. **Shut down exactly once.** A SIGTERM during `atexit`, or a Qt quit
   followed by interpreter exit, would otherwise tear down twice, and the
   second pass runs against half-closed transports.

This merges into `AppContext` in S12 (RC-9); it is deliberately small so
that merge is a move, not a rewrite.
"""

import atexit
import signal
import threading

_manager = None
_lock = threading.Lock()
_installed = False
_shutdown_done = False


def set_current_manager(manager):
    """Record the live manager. The last one set is the one handlers use."""
    global _manager
    with _lock:
        _manager = manager
    return manager


def current_manager():
    with _lock:
        return _manager


def shutdown(reason="exit"):
    """Stop and tear down everything the current manager owns. Runs once."""
    global _shutdown_done
    with _lock:
        if _shutdown_done:
            return
        _shutdown_done = True
        manager = _manager
    if manager is None:
        return
    print(f"[lifecycle] Shutting down ({reason})...")
    try:
        manager.shutdown_all()
    except Exception as e:  # never let a teardown failure mask the exit
        print(f"[lifecycle] shutdown_all failed: {e}")


def install_exit_hooks():
    """Install `atexit` and signal handlers. Idempotent."""
    global _installed
    with _lock:
        if _installed:
            return
        _installed = True

    atexit.register(shutdown, "atexit")

    def _handler(signum, _frame):
        shutdown(f"signal {signum}")
        # Restore the default action and re-raise, so the process still dies
        # with the right status instead of the signal being swallowed.
        try:
            signal.signal(signum, signal.SIG_DFL)
            signal.raise_signal(signum)
        except Exception:
            raise SystemExit(128 + signum)

    for name in ("SIGINT", "SIGTERM", "SIGHUP"):
        sig = getattr(signal, name, None)
        if sig is None:
            continue  # SIGHUP does not exist on Windows
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError):
            # Not on the main thread, or the platform refuses this signal.
            pass


def _reset_for_tests():
    """Clear module state. Used by the test fixture, not by the app."""
    global _manager, _installed, _shutdown_done
    with _lock:
        _manager, _installed, _shutdown_done = None, False, False
