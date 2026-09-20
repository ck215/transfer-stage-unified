"""The one owner of SDL/pygame (RC-13 item 1).

Before this, pygame ownership was spread across every `ControllerPoller`:

- `connect_controller()` called `pygame.quit()` — a **process-wide** teardown
  — to "restart pygame" for one poller, which killed every *other* live
  poller's joystick handle at the same time;
- `close()` decremented a module-level `_active_poller_count` and called
  `pygame.quit()` when it hit zero, so closing one device could tear SDL down
  under another that was still running;
- `_ensure_pygame_video()` existed to re-init SDL after those `quit()` calls,
  and its own docstring described it as a recurring patch. It is the
  **anti-fix** `root-causes.md` names: it makes the symptom survivable and so
  removes the pressure to fix the ownership. Adding call sites for it was the
  wrong direction; this module removes the need for it.

The rules here are the fix:

1. SDL is initialised **once**, lazily, and never torn down except at
   process exit — which is `lifecycle.shutdown()`'s job, installed in S2.
2. Every SDL call happens under one re-entrant lock, because pygame's
   joystick API is not thread-safe and each poller has its own thread.
3. Device handles are acquired and released **by owner id**. Releasing one
   owner never touches another's handle, and the claims registry is derived
   from real acquisitions rather than kept in a parallel dict that can drift.
"""

import threading

try:
    import pygame
except ImportError:  # pragma: no cover - pygame is expected in practice
    pygame = None


class InputService:
    """Module-level singleton; use the `input_service` instance below."""

    def __init__(self):
        # Re-entrant: enumerate() is called from inside acquire().
        self._lock = threading.RLock()
        self._initialised = False
        self._handles = {}   # owner_id -> (index, joystick)

    # -- lifecycle -----------------------------------------------------

    def ensure_init(self):
        """Bring SDL up once. Cheap and idempotent afterwards."""
        if pygame is None:
            return False
        with self._lock:
            if not self._initialised:
                pygame.init()
                self._initialised = True
            if not pygame.joystick.get_init():
                try:
                    pygame.joystick.init()
                except Exception as e:
                    # Under SDL_VIDEODRIVER=dummy this can raise for video
                    # reasons that are not real failures in headless use.
                    if not any(k in str(e).lower()
                               for k in ("video", "display", "no available")):
                        raise
            return True

    def shutdown(self):
        """Tear SDL down. **Process exit only** — see `lifecycle.shutdown`.

        Never call this when a device closes. That was the old behaviour and
        it killed every other live poller's joystick.
        """
        if pygame is None:
            return
        with self._lock:
            self._handles.clear()
            try:
                pygame.joystick.quit()
                pygame.quit()
            except Exception:
                pass
            self._initialised = False

    # -- enumeration ---------------------------------------------------

    def enumerate(self):
        """[(index, name)] for every attached controller."""
        if not self.ensure_init():
            return []
        with self._lock:
            try:
                pygame.event.pump()
                found = []
                for i in range(pygame.joystick.get_count()):
                    try:
                        found.append((i, pygame.joystick.Joystick(i).get_name()))
                    except Exception:
                        pass
                return found
            except Exception:
                return []

    def is_index_connected(self, index):
        if index is None:
            return False
        return any(i == index for i, _ in self.enumerate())

    # -- per-owner device handles --------------------------------------

    def acquire(self, owner_id, index):
        """Claim controller `index` for `owner_id`. Returns the joystick or None.

        Refuses a controller another owner already holds, so the claim
        registry cannot disagree with reality.
        """
        if index is None or not self.ensure_init():
            return None
        with self._lock:
            for other, (held, _) in self._handles.items():
                if other != owner_id and held == index:
                    return None
            existing = self._handles.get(owner_id)
            if existing and existing[0] == index:
                return existing[1]
            self.release(owner_id)
            try:
                joystick = pygame.joystick.Joystick(index)
                joystick.init()
            except Exception:
                return None
            self._handles[owner_id] = (index, joystick)
            return joystick

    def release(self, owner_id):
        """Drop one owner's handle. Never touches SDL itself, or anyone else."""
        with self._lock:
            entry = self._handles.pop(owner_id, None)
        if entry is not None:
            try:
                entry[1].quit()
            except Exception:
                pass

    def claims(self):
        """owner_id -> controller index, derived from actual acquisitions."""
        with self._lock:
            return {owner: index for owner, (index, _) in self._handles.items()}

    def index_for(self, owner_id):
        with self._lock:
            entry = self._handles.get(owner_id)
            return entry[0] if entry else None

    def lock(self):
        """The SDL lock, for a block of joystick reads that must not interleave.

        pygame's joystick API is not thread-safe, and each poller now runs its
        own thread (RC-13 item 2), so a poll tick holds this for the whole
        read rather than per call.
        """
        return self._lock

    def pump(self):
        """Service the SDL event queue under the lock."""
        if not self._initialised or pygame is None:
            return
        with self._lock:
            try:
                pygame.event.pump()
            except Exception:
                pass


input_service = InputService()
