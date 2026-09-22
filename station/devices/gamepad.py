"""The gamepad: one SDL owner (`GamepadHub`) and one poll loop per `Gamepad`.

Two objects, and the split is the fix for RC-13.

`GamepadHub` owns SDL/pygame for the whole process. Before the rebuild every
poller talked to SDL directly: `connect_controller()` called `pygame.quit()` --
a *process-wide* teardown -- to "restart pygame" for one device, and `close()`
decremented a module-level poller count and called `pygame.quit()` at zero, so
closing one device tore SDL down under another that was still running. A helper
called `_ensure_pygame_video()` existed to resurrect SDL afterwards, and its own
docstring described it as a recurring patch: it is the anti-fix
`root-causes.md` names, because it made the symptom survivable and so removed
the pressure to fix the ownership. There is nothing left to resurrect here. SDL
comes up once, lazily; it comes down only at process exit; and device handles
are held **by owner id**, so releasing one owner never touches another's.

`Gamepad` is one probe's pad: it binds by name, runs exactly one poll thread,
latches edges at poll time, and hands a snapshot to whoever reads it. It is a
`Device`, so `is_open` answers the question the old `_is_os_connected()` did --
is the bound pad still physically there -- and `status` is the word a model
puts in its state.

The word "controller" means the `Controller` and nothing else. A joystick is a
gamepad. The only survivors are SDL device-name substrings (a Windows Xbox pad
reports itself as "Controller") and the standard input keys, which are on the
wire to the firmware and cannot be renamed here.

pygame is imported lazily and guarded: this module imports, and every layout
maps, with no pygame installed and no hardware attached.
"""
import collections
import os
import re
import sys
import threading
import time

from station.devices.device import Device
from station.events import events

# SDL configuration belongs to SDL's one owner and must be set **before**
# pygame is imported. Four copies of this block existed -- the old gamepad
# module, both desktop launchers, and the string of Python the web adapter
# handed to a subprocess -- and they did not agree: the subprocess set the
# video driver but not `SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS`, so the pads it
# enumerated were the ones an unfocused window could not have read anyway
# (MANAGER-18). This is the only copy now.
os.environ.setdefault("SDL_JOYSTICK_ALLOW_BACKGROUND_EVENTS", "1")
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

#: Bound by `_load_pygame()` the first time the hub opens. Left None so the
#: module imports on a machine without pygame, and so a test can substitute a
#: fake joystick layer by patching this one name.
pygame = None


def _load_pygame():
    """Import pygame once, on demand. Returns the module or None."""
    global pygame
    if pygame is None:
        try:
            import pygame as _pygame
        except ImportError:
            return None
        pygame = _pygame
    return pygame


def _label_for(index, name):
    """The one operator-facing formatter: `"ID 0: Xbox Series X Controller"`.

    The index in the label is the SDL index `claim()` takes, so a name that
    came from the hub resolves back to a handle without per-caller parsing
    conventions.
    """
    return f"ID {index}: {name}"


_INDEX_PATTERN = re.compile(r"(?:Joy|ID)?\s*(\d+)", re.IGNORECASE)

#: Substrings and exact labels that mean "no pad". The Web select's empty
#: placeholder is one of them (GAMEPAD-6): it used to fall through to "no
#: numeric id" and unbind silently, with nothing said.
_NO_PAD_TOKENS = ("none", "virtual")
_NO_PAD_EXACT = ("", "n/a")


def _is_no_pad(name):
    if name is None:
        return True
    if isinstance(name, int):
        return False
    text = str(name).strip().lower()
    return (text in _NO_PAD_EXACT
            or any(token in text for token in _NO_PAD_TOKENS))


def _index_from(name):
    """SDL index out of an int, `"ID 12: ..."`, `"Joy 3"`, or None."""
    if isinstance(name, int):
        return name
    match = _INDEX_PATTERN.search(str(name))
    if match:
        return int(match.group(1))
    try:
        return int(str(name)[3:4])
    except (ValueError, IndexError):
        return None


class GamepadHub:
    """Process-wide owner of SDL and of every joystick claim.

    Module-level singleton: use `hub` below. A second instance is legal and is
    what tests use, because every claim it holds is its own.
    """

    SOURCE = "gamepad-hub"

    def __init__(self):
        # Re-entrant: _enumerate() runs inside claim().
        self._lock = threading.RLock()
        self._is_open = False
        self._handles = {}   # owner_id -> (index, joystick)

    # -- lifecycle -----------------------------------------------------

    def open(self):
        """Bring SDL up once. Cheap and idempotent afterwards."""
        module = _load_pygame()
        if module is None:
            events.debug("SDL unavailable", "pygame is not installed; no pad can bind",
                         source=self.SOURCE, every=60.0)
            return False
        with self._lock:
            if not self._is_open:
                module.init()
                self._is_open = True
                events.debug("SDL up", "pygame initialised by the hub", source=self.SOURCE)
            if not module.joystick.get_init():
                try:
                    module.joystick.init()
                except Exception as exc:
                    # Under SDL_VIDEODRIVER=dummy this raises for video reasons
                    # that are not real failures in headless use.
                    if not any(word in str(exc).lower()
                               for word in ("video", "display", "no available")):
                        raise
                    events.debug("SDL joystick init", f"headless video complaint ignored: {exc}",
                                 source=self.SOURCE, exception=exc)
            return True

    def close(self):
        """Tear SDL down and release every claim. **Process exit only.**

        Never call this when one device closes. That was the old behaviour and
        it killed every other live poller's joystick.
        """
        module = pygame
        with self._lock:
            owners = list(self._handles)
            for owner in owners:
                self.release(owner)
            if module is not None:
                try:
                    module.joystick.quit()
                    module.quit()
                except Exception as exc:
                    events.debug("SDL teardown", f"ignored on the way out: {exc}",
                                 source=self.SOURCE, exception=exc)
            self._is_open = False
        events.debug("SDL down", f"released {len(owners)} claim(s) at process exit",
                     source=self.SOURCE)

    @property
    def is_open(self):
        """Is SDL up? A caller with its own OS-level presence check needs this
        to know whether an SDL answer is available at all."""
        return self._is_open

    # -- enumeration ---------------------------------------------------

    def _enumerate(self):
        """[(index, name)] for every attached pad. Constructs one Joystick per
        device, so it is too expensive for a poll tick -- `count` is what the
        tick reads."""
        if not self.open():
            return []
        with self._lock:
            try:
                pygame.event.pump()
                found = []
                for index in range(pygame.joystick.get_count()):
                    try:
                        found.append((index, pygame.joystick.Joystick(index).get_name()))
                    except Exception:
                        pass
                return found
            except Exception as exc:
                events.debug("Enumeration failed", str(exc), source=self.SOURCE,
                             exception=exc, every=5.0)
                return []

    @property
    def names(self):
        """Attached pads as the operator-facing `"ID 0: <name>"` strings."""
        return [_label_for(index, name) for index, name in self._enumerate()]

    @property
    def count(self):
        """How many pads SDL currently sees. Locked, and cheap: one SDL read."""
        if pygame is None or not self._is_open:
            return 0
        with self._lock:
            try:
                return pygame.joystick.get_count()
            except Exception:
                return 0

    def is_connected(self, index):
        """Is `index` still attached? The cheap presence check.

        NOTE (deviation from design.json, flagged in the handoff): the design
        lists this as a property, but it answers about one index and so has to
        take one. The name is unchanged.
        """
        if index is None:
            return False
        return 0 <= index < self.count

    # -- per-owner device handles --------------------------------------

    def claim(self, owner_id, index):
        """Claim pad `index` for `owner_id`. Returns the joystick or None.

        Refuses a pad another owner already holds, so the claim registry
        cannot disagree with reality.
        """
        if index is None or not self.open():
            return None
        with self._lock:
            for other, (held, _) in self._handles.items():
                if other != owner_id and held == index:
                    events.debug("Claim refused",
                                 f"{owner_id} wanted index {index}; {other} holds it",
                                 source=self.SOURCE)
                    return None
            existing = self._handles.get(owner_id)
            if existing and existing[0] == index:
                return existing[1]
            self.release(owner_id)
            try:
                joystick = pygame.joystick.Joystick(index)
                joystick.init()
            except Exception as exc:
                events.debug("Claim failed", f"{owner_id} index {index}: {exc}",
                             source=self.SOURCE, exception=exc)
                return None
            self._handles[owner_id] = (index, joystick)
            events.debug("Claim granted", f"{owner_id} holds index {index}",
                         source=self.SOURCE)
            return joystick

    def release(self, owner_id):
        """Drop one owner's handle. Never touches SDL, or anyone else."""
        with self._lock:
            entry = self._handles.pop(owner_id, None)
        if entry is not None:
            try:
                entry[1].quit()
            except Exception:
                pass
            events.debug("Claim released", f"{owner_id} let go of index {entry[0]}",
                         source=self.SOURCE)

    @property
    def claims(self):
        """owner_id -> index, derived from actual acquisitions."""
        with self._lock:
            return {owner: index for owner, (index, _) in self._handles.items()}

    def index_for(self, owner_id):
        with self._lock:
            entry = self._handles.get(owner_id)
            return entry[0] if entry else None

    def lock(self):
        """The SDL lock, for a block of joystick reads that must not interleave.

        pygame's joystick API is not thread-safe and each `Gamepad` runs its
        own thread, so a poll tick holds this for the whole read rather than
        per call.
        """
        return self._lock

    def pump(self):
        """Service the SDL event queue under the lock."""
        if not self._is_open or pygame is None:
            return
        with self._lock:
            try:
                pygame.event.pump()
            except Exception:
                pass


#: The process-wide hub. `Gamepad(owner_id)` uses it unless handed another.
hub = GamepadHub()


# ======================================================================
# Layout table
# ======================================================================
#
# The five layout classes (BaseGamepad, XboxGamepad, BluetoothXboxGamepad,
# LogitechF310Gamepad, T16000MGamepad) are rows here, read by one
# `_read_layout`. All four pads stay in service. Every index and every
# platform branch below is today's value, moved and not touched.
#
# UNVERIFIED AT THE BENCH. Four findings are open questions about these
# numbers. Each needs the physical pad in hand, so the rebuild ports what is
# here today and marks it. This table is the single place to settle them.
#
#   GAMEPAD-11 -- name matching. main accepted an exact-name whitelist
#     ("Xbox Series X Controller", "T.16000M", "Thrustmaster T.16000M",
#     "Logitech Gamepad F310", Windows names starting "Controller") and raised
#     ValueError otherwise; on Linux an Xbox pad with an unrecognised bus GUID
#     also raised. What is here is the looser substring match the refactor
#     introduced, so a DualShock ("Wireless Controller") silently gets the
#     Bluetooth-Xbox row and a foreign pad gets the Xbox row. UNVERIFIED:
#     which names the bench actually reports on each OS.
#
#   GAMEPAD-12 -- T16000M Z direction and bumpers. main bound
#     [X, Y, Z+(R), Z-(L), LBumper, RBumper] per *name*: "T.16000M" -> [0, 1,
#     9, 10, 7, 9] and "Thrustmaster T.16000M" -> [0, 1, 10, 9, 7, 9], i.e.
#     the two names disagree about which button is Z up, and bumpers came from
#     buttons 7 and 9. One row covers both names here, with z_left = virtual
#     axis 10 and z_right = 9 (the Windows-name variant) and bumpers from
#     buttons 4/5 (the 7/9 fallbacks are never reached: a 16-button T16000M
#     has buttons 4 and 5). UNVERIFIED: which way the throttle buttons drive Z
#     on the stage.
#
#   GAMEPAD-13 -- D-pad sign. main's `get_hat_edge` returned `(-out_x, -out_y)`
#     and the firmware moves `x_axis.move(dpad_LR * x_step_size)`. The
#     refactor passes the hat through unnegated, closed on a mock unit test
#     rather than on the stage. UNVERIFIED: which way D-pad right moves the
#     stage. If it must be negated, negate it once, here, in `_read_layout`.
#
#   GAMEPAD-14 -- deadzone. main applied one deadzone of 0.1 in the poll loop
#     (its T16000M-specific 0.03 was a function-local assignment that never
#     did anything). The refactor applied 0.1 to the raw axis cache *and* 0.12
#     to x/y in the mapped state, so the effective stick deadzone was 0.12 and
#     the log/activity threshold disagreed with the value sent to hardware.
#     Both values are carried over unchanged, but the deadzone that reaches
#     the hardware is now applied **exactly once**, in `_apply_deadzones`:
#     `DEADZONE` (0.12, x/y) and the trigger snap (-0.9). `LOG_DEADZONE` (0.1)
#     is only the "did this axis move" threshold for the pad log; it never
#     touches a value on its way to the firmware. UNVERIFIED: the number the
#     bench wants. One constant, one call site, when it is settled.
#
# A spec's sources: an int is a direct lookup; a tuple of ints is "the first
# of these present in the cache" (main's `4 if 4 in prev_axis_states else 1`);
# ("button", n) reads a digital trigger as -1.0/+1.0; ("axis2x", n) reads a
# virtual axis holding a 0/1 button value and remaps it to [-1, 1].

#: What a pad with no live mapping reads as. Was `BaseGamepad.get_mapped_state`.
NEUTRAL = {
    "x_axisStatus": 0.0,
    "y_axisStatus": 0.0,
    "z_axisStatusL": -1.0,
    "z_axisStatusR": -1.0,
    "dpad_LR": 0,
    "dpad_UD": 0,
    "LBumper": 0,
    "RBumper": 0,
}

_XBOX_LINUX = {
    "x": 0, "y": (4, 1), "z_left": 2, "z_right": 5,
    "hat": 0, "bumper_left": 4, "bumper_right": 5, "idle_axes": (2, 5),
}
_XBOX_DEFAULT = {
    "x": 0, "y": (3, 1), "z_left": 4, "z_right": 5,
    "hat": 0, "bumper_left": 4, "bumper_right": 5, "idle_axes": (4, 5),
}
_XBOX_BLUETOOTH_LINUX = {
    "x": 0, "y": 3, "z_left": 5, "z_right": 4,
    "hat": 0, "bumper_left": 6, "bumper_right": 7, "idle_axes": (2, 4, 5),
}
_F310_DINPUT = {
    "x": 0, "y": (3, 1), "z_left": ("button", 6), "z_right": ("button", 7),
    "hat": 0, "bumper_left": 4, "bumper_right": 5, "idle_axes": (),
}
_T16000M = {
    "x": 0, "y": 1, "z_left": ("axis2x", 10), "z_right": ("axis2x", 9),
    "hat": 0, "bumper_left": (4, 7), "bumper_right": (5, 9), "idle_axes": (),
}


def _override_t16000m(axes, buttons):
    """T16000M: buttons 2 and 3 are read as virtual axes 9 and 10.

    Was `T16000MGamepad.update_overrides`, which asked the joystick directly
    mid-tick. It reads the button cache the same tick filled instead: same
    values, one fewer SDL call, and it works with no hardware attached.
    """
    axes[9] = buttons.get(2, 0)
    axes[10] = buttons.get(3, 0)


def _is_linux_bluetooth_guid(joystick):
    """main's Linux bus-GUID test for a Bluetooth Xbox pad (GAMEPAD-11)."""
    if not sys.platform.startswith("linux"):
        return False
    try:
        guid = joystick.get_guid()
    except Exception:
        return False
    return bool(guid) and len(guid) > 2 and guid[1:2] == "5"


LAYOUTS = (
    {
        "id": "t16000m",
        "match": ("t.16000m", "thrustmaster"),
        "guid_match": None,
        "mode": None,
        "override": _override_t16000m,
        "binds": {"default": {"default": _T16000M}},
    },
    {
        "id": "logitech_f310",
        "match": ("f310", "dual action"),
        "guid_match": None,
        "mode": "dinput",          # resolved by Gamepad._is_dinput
        "override": None,
        "binds": {
            "dinput": {"default": _F310_DINPUT},
            "xinput": {"linux": _XBOX_LINUX, "default": _XBOX_DEFAULT},
        },
    },
    {
        "id": "xbox_bluetooth",
        "match": ("wireless",),
        "guid_match": _is_linux_bluetooth_guid,
        "mode": None,
        "override": None,
        "binds": {"default": {"linux": _XBOX_BLUETOOTH_LINUX,
                              "default": _XBOX_DEFAULT}},
    },
    {
        "id": "xbox",
        # "controller" is an SDL device-name substring -- a Windows Xbox pad
        # reports itself as "Controller" -- not a name in this codebase.
        "match": ("xbox", "controller", "x-box"),
        "guid_match": None,
        "mode": None,
        "override": None,
        "binds": {"default": {"linux": _XBOX_LINUX, "default": _XBOX_DEFAULT}},
    },
)


def _source_value(cache, source, default):
    """Resolve one spec source against a cache. See the LAYOUTS comment."""
    if isinstance(source, tuple):
        for index in source:
            if index in cache:
                return cache[index]
        return default
    return cache.get(source, default)


class Gamepad(Device):
    """One probe's pad: binds by name, polls itself, latches edges.

    `open()` starts the poll thread; `close()` stops it and releases the claim.
    `is_open` is the presence question the old `_is_os_connected()` answered.
    """

    # Poll interval in milliseconds.
    #
    # NOTE (ruled D-12 on 2026-09-20): the comment here used to read "Poll 50
    # times per second (1000ms / 20ms = 50Hz)" above a value of 5, i.e. 200 Hz
    # -- four times the documented rate. The owner ruled the *code* was right
    # and the comment wrong: 5 ms stands. The pad therefore samples at 4x the
    # manual command rate, which is deliberate: it catches button edges
    # shorter than one jog tick (the 50 Hz jog cadence is the model's).
    POLL_INTERVAL = 5          # ms -> ~200 Hz  (D-12: ruled, do not change)

    #: Discrete inputs. Latched at poll time, drained by exactly one consumer.
    EDGE_KEYS = ("dpad_LR", "dpad_UD", "LBumper", "RBumper")

    DEADZONE = 0.12            # x/y, applied once, on the way out (GAMEPAD-14)
    TRIGGER_SNAP = -0.9        # below this a trigger reads fully idle
    LOG_DEADZONE = 0.1         # pad-log threshold only; never reaches hardware

    RATE_REPORT_SECONDS = 5.0  # observed poll rate, to the log file only
    LOG_LINES = 200

    #: How often the darwin presence check may re-enumerate. Enumeration
    #: constructs a Joystick per device and the tick runs at 200 Hz.
    DARWIN_RESCAN_SECONDS = 0.5

    def __init__(self, owner_id, hub=hub):
        self._owner_id = str(owner_id)
        self._hub = hub
        self._source = f"gamepad:{self._owner_id}"

        self._lock = threading.RLock()

        # Bound device.
        self._spec = None          # the resolved layout spec, None = unbound
        self._layout_id = None
        self._joystick = None
        self._index = None
        self._device_name = None
        self._label = None
        self._override = None
        self._is_lost = False

        # Raw caches, one entry per physical axis/button/hat, filled at bind.
        self._axis_values = {}
        self._button_values = {}
        self._hat_values = {}

        # Latched input (RC-13 item 2). Edges are detected *here*, at poll
        # time, and accumulate until drained. They used to be detected inside
        # the reader, so whichever caller read first swallowed the edge for
        # everyone else and a tap shorter than the gap between reads was never
        # seen at all.
        self._levels = {}
        self._pending_edges = {}
        self._latch = {}
        self._last_raw = None

        # flush_neutral holds a neutral snapshot until the pad physically
        # moves (GAMEPAD-8). It never writes into the raw axis cache.
        self._is_neutralised = False
        self._neutral_reference = None

        # Poll loop.
        self._is_polling = False
        self._wants_poll = False
        self._generation = 0
        self._thread = None
        self._is_closed = False
        self._tick_count = 0
        self._rate_mark = 0.0
        self._darwin_scan_mark = 0.0
        self._darwin_scan = {}

        self._gate = threading.Event()
        self._gate.set()
        self._log = collections.deque(maxlen=self.LOG_LINES)

    def __repr__(self):
        return f"<Gamepad {self._owner_id} {self.status} {self._label or '-'}>"

    # -- device lifecycle ----------------------------------------------

    def open(self):
        """Start polling. Idempotent; safe before anything is bound."""
        self._is_closed = False
        self._wants_poll = True
        self._hub.open()
        started = self._start_poll_loop()
        events.debug("Gamepad open",
                     f"{self._owner_id}: poll loop "
                     f"{'started' if started else 'already running or pending a bind'}",
                     source=self._source)
        return self._is_polling

    def close(self):
        """Stop the poll loop and release the claim. Idempotent."""
        if self._is_closed:
            return
        self._is_closed = True
        self._stop_poll_loop("close")
        self._release_binding("close")
        self._wants_poll = False
        events.debug("Gamepad closed", f"{self._owner_id}: claim released",
                     source=self._source)

    @property
    def is_open(self):
        """Is the bound pad still physically present? Was `_is_os_connected`."""
        if self._is_closed or self._spec is None:
            return False
        return self._is_present(self._index, expected_name=self._device_name)

    @property
    def status(self):
        if self._is_closed:
            return "closed"
        if self._spec is None:
            return "lost" if self._is_lost else "unbound"
        return "bound" if self.is_open else "lost"

    # -- binding -------------------------------------------------------

    @property
    def options(self):
        """`["None", *unclaimed pad labels]`, re-read from SDL every time.

        Live refresh, a real "None" entry, and claim filtering were all
        regressions against main (GAMEPAD-5, VIEW-TKINTER-10, PYSIDE-20): the
        list was built once, contained no "None", and showed pads another
        probe already held. Reading it is the refresh -- a view that polls
        this sees a hot-plugged pad appear and an unplugged one vanish.
        """
        taken = {index for owner, index in self._hub.claims.items()
                 if owner != self._owner_id}
        return ["None"] + [_label_for(index, name)
                           for index, name in self._hub._enumerate()
                           if index not in taken]

    @property
    def is_bound(self):
        return self._spec is not None

    def bind(self, name):
        """Bind this pad to `name` (a label from `options`, an int, or None).

        Returns True only when a pad is actually bound. **False is the signal
        the probe acts on**: a swap that fails has to stop the probe and
        revert the selection rather than leave manual mode running against a
        pad that is not there (GAMEPAD-3, GAMEPAD-4).

        Rebinding the pad that is already bound is a real rebind, not a no-op:
        after a disconnect that is exactly how the operator reconnects, and
        the old code handed back the stale handle (GAMEPAD-5).
        """
        if self._is_closed:
            # A closed device does not quietly take a claim it will never poll.
            events.debug("Gamepad bind refused",
                         f"{self._owner_id}: closed, so {name!r} was not bound",
                         source=self._source)
            return False
        was_polling = self._is_polling or self._wants_poll
        events.debug("Gamepad bind requested",
                     f"{self._owner_id}: {self._label!r} -> {name!r} "
                     f"(polling={self._is_polling})", source=self._source)
        self._stop_poll_loop("rebind")
        is_bound = self._open_joystick(name)
        if is_bound and was_polling:
            self._start_poll_loop()
        events.debug("Gamepad bind result",
                     f"{self._owner_id}: {'bound ' + str(self._label) if is_bound else 'not bound'}"
                     f", polling={self._is_polling}", source=self._source)
        return is_bound

    def _open_joystick(self, name):
        """Claim `name` and resolve its layout. Was `_initialize_pygame_joystick`."""
        self._release_binding("rebind")

        if _is_no_pad(name):
            events.debug("Gamepad unbound", f"{self._owner_id}: selection is {name!r}",
                         source=self._source)
            return False

        index = _index_from(name)
        if index is None:
            events.warn("Gamepad Not Bound", f"{self._owner_id}: cannot read a device "
                        f"index out of {name!r}", source=self._source)
            return False

        if not self._hub.open():
            events.warn("Gamepad Not Bound",
                        f"{self._owner_id}: SDL is unavailable, so {name!r} cannot bind",
                        source=self._source)
            return False

        # Presence is asked about the index being bound, by re-enumerating --
        # never by interrogating the handle we still hold (GAMEPAD-19). During
        # a swap that handle belongs to the *previous* pad, so an unplugged
        # old pad used to report the new one as absent and the bind failed
        # with "Device not physically present at OS level".
        if not self._is_present(index, rescan=True):
            events.warn("Gamepad Not Bound",
                        f"{self._owner_id}: no device at index {index} ({name!r})",
                        source=self._source)
            return False

        joystick = self._hub.claim(self._owner_id, index)
        if joystick is None:
            holder = next((owner for owner, held in self._hub.claims.items()
                           if held == index and owner != self._owner_id), None)
            if holder is not None:
                events.warn("Gamepad Claim Conflict",
                            f"{self._owner_id}: index {index} is already claimed by {holder}",
                            source=self._source)
            else:
                events.warn("Gamepad Not Bound",
                            f"{self._owner_id}: index {index} could not be opened",
                            source=self._source)
            return False

        try:
            device_name = joystick.get_name()
            layout = self._layout_for(joystick)
            mode = self._resolve_mode(layout, joystick)
            spec = self._spec_for(layout, mode)
        except ValueError as exc:
            events.warn("Unsupported Gamepad", str(exc), source=self._source, exception=exc)
            self._hub.release(self._owner_id)
            return False
        except Exception as exc:
            events.warn("Gamepad Not Bound",
                        f"{self._owner_id}: could not read {name!r}: {exc}",
                        source=self._source, exception=exc)
            self._hub.release(self._owner_id)
            return False

        with self._lock:
            self._joystick = joystick
            self._index = index
            self._device_name = device_name
            self._label = _label_for(index, device_name)
            self._spec = spec
            self._layout_id = layout["id"]
            self._override = layout["override"]
            self._is_lost = False
            self._prime_caches(joystick, spec)
            self._latch.clear()
            self._pending_edges.clear()
            self._levels = {}
            self._last_raw = None
            self._is_neutralised = False

        self._note(f"Bound {self._label} as layout {layout['id']}"
                   + (f" ({mode})" if mode != "default" else ""))
        events.info("Gamepad Bound", f"{self._owner_id} -> {self._label} "
                    f"[{layout['id']}{'' if mode == 'default' else '/' + mode}]",
                    source=self._source)
        return True

    def _prime_caches(self, joystick, spec):
        """One cache entry per physical control, plus the idle triggers.

        The mapping asks "is axis 4 present?" to tell a right-stick Y from a
        trigger, so the caches must have exactly the axes the device has.
        """
        self._axis_values = {}
        self._button_values = {}
        self._hat_values = {}
        for index in range(joystick.get_numaxes()):
            self._axis_values[index] = 0.0
        for index in range(joystick.get_numbuttons()):
            self._button_values[index] = 0
        for index in range(joystick.get_numhats()):
            self._hat_values[index] = (0, 0)
        for index in spec["idle_axes"]:
            self._axis_values[index] = -1.0

    def _layout_for(self, joystick):
        """The LAYOUTS row for this device. Was `get_gamepad_wrapper`."""
        try:
            name = joystick.get_name()
        except Exception:
            name = ""
        lowered = (name or "").lower()
        for layout in LAYOUTS:
            if any(token in lowered for token in layout["match"]):
                return layout
            guid_match = layout["guid_match"]
            if guid_match is not None and guid_match(joystick):
                return layout
        raise ValueError(f"Unsupported joystick detected: '{name}'. "
                         "Add axis binds to LAYOUTS in gamepad.py!")

    def _resolve_mode(self, layout, joystick):
        if layout["mode"] == "dinput":
            return "dinput" if self._is_dinput(joystick) else "xinput"
        return "default"

    @staticmethod
    def _spec_for(layout, mode):
        by_platform = layout["binds"][mode]
        if sys.platform.startswith("linux") and "linux" in by_platform:
            return by_platform["linux"]
        return by_platform["default"]

    @staticmethod
    def _is_dinput(joystick):
        """Logitech F310 switch position. Was `_is_dinput_mode`."""
        try:
            return joystick.get_numaxes() <= 4 and joystick.get_numbuttons() >= 8
        except Exception:
            return False

    def _release_binding(self, reason):
        """Drop the handle, the claim and every cached reading."""
        had_binding = self._spec is not None
        self._hub.release(self._owner_id)
        with self._lock:
            self._spec = None
            self._layout_id = None
            self._override = None
            self._joystick = None
            self._index = None
            self._device_name = None
            self._axis_values = {}
            self._button_values = {}
            self._hat_values = {}
            self._levels = {}
            self._pending_edges.clear()
            self._latch.clear()
            self._last_raw = None
            self._is_neutralised = False
            self._neutral_reference = None
        if had_binding:
            events.debug("Gamepad unbound", f"{self._owner_id}: {reason}",
                         source=self._source)

    # -- presence ------------------------------------------------------

    def _is_present(self, index, expected_name=None, rescan=False):
        """Is there a pad at `index`, and is it still the one we bound?"""
        if index is None:
            return False
        if self._hub.is_open and not self._hub.is_connected(index):
            return False

        if sys.platform.startswith("linux"):
            return True
        if sys.platform == "win32":
            # main had joyGetPosEx here and answered True either way; the
            # index check above is the signal that fires.
            return True
        if sys.platform == "darwin":
            # macOS has no joyGetPosEx equivalent, and the old darwin branch
            # asked `self.gamepad.joystick.get_name()` -- the handle we
            # happen to hold, which during a swap is the *previous* device
            # (GAMEPAD-19). Re-enumerate instead: that answers about the
            # index actually in question. Rate-limited because enumeration
            # constructs a Joystick per device and this runs in a 200 Hz tick.
            found = self._darwin_devices(rescan)
            if found is None:
                return True
            if index not in found:
                return False
            if expected_name is not None and found[index] != expected_name:
                return False
            return True
        return True

    def _darwin_devices(self, rescan):
        """{index: name} for macOS presence, cached for DARWIN_RESCAN_SECONDS."""
        now = time.monotonic()
        if rescan or now - self._darwin_scan_mark >= self.DARWIN_RESCAN_SECONDS:
            self._darwin_scan = dict(self._hub._enumerate())
            self._darwin_scan_mark = now
        return self._darwin_scan

    def _handle_disconnect(self, reason="device gone"):
        """The pad went away. Stop, release, and say so exactly once."""
        label = self._label or "gamepad"
        events.warn("Gamepad Disconnected",
                    f"{self._owner_id}: {label} is gone ({reason})", source=self._source)
        self._note(f"Disconnected: {label} ({reason})")
        self._stop_poll_loop("disconnect")
        self._release_binding(f"disconnect: {reason}")
        self._is_lost = True

    # -- the poll loop -------------------------------------------------

    def _next_generation(self):
        """Retire every outstanding poll loop and return the new identity.

        A swap is stop + start, and `is_polling` used to be the only thing a
        loop checked before carrying on. By the time the old loop's sleep
        expired the flag was True again, so it ran alongside its replacement:
        N swaps left N+1 loops pumping SDL against one shared cache
        (GAMEPAD-7). Each loop now carries the generation it started with and
        retires as soon as that is no longer current.
        """
        with self._lock:
            self._generation += 1
            return self._generation

    def _start_poll_loop(self):
        if self._is_closed or self._is_polling or self._spec is None:
            return False
        generation = self._next_generation()
        self._is_polling = True
        self._tick_count = 0
        self._rate_mark = time.monotonic()
        thread = threading.Thread(target=self._poll_loop, args=(generation,),
                                  daemon=True, name=f"gamepad-{self._owner_id}")
        self._thread = thread
        thread.start()
        events.debug("Poll loop started",
                     f"{self._owner_id}: generation {generation} at "
                     f"{1000.0 / self.POLL_INTERVAL:.0f} Hz nominal", source=self._source)
        return True

    def _stop_poll_loop(self, reason="stop"):
        was_polling = self._is_polling
        self._is_polling = False
        # Unconditional: a loop started before this call must not survive a
        # restart that happens before it next wakes (GAMEPAD-7).
        generation = self._next_generation()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=1.0)
            if not thread.is_alive():
                self._thread = None
        if was_polling:
            # Nothing is refreshing the snapshot any more, so it must not keep
            # answering. A stopped loop used to leave the last reading in
            # place, and a stale full-deflection reading is a jog command.
            with self._lock:
                self._levels = {}
            events.debug("Poll loop stopped",
                         f"{self._owner_id}: {reason}, generation now {generation}",
                         source=self._source)

    def _poll_loop(self, generation):
        """The poll thread. One `Gamepad`, one live loop, ever.

        The pad owns its own clock. It used to reschedule itself through a Tk
        widget's `after()` and stop when there was none, which is why the Web
        frontend had no manual mode at all: no Tk root, no polling, so
        entering manual mode energized the coils and then did nothing else
        (GAMEPAD-1, GAMEPAD-21).
        """
        while (self._is_polling and not self._is_closed
                and self._generation == generation):
            self._poll_tick(generation)
            time.sleep(self.POLL_INTERVAL / 1000.0)

    def poll_once(self):
        """Run exactly one tick. The unit of the loop, exposed for tests and
        for a caller that drives the pad itself."""
        self._poll_tick(None)

    def _poll_tick(self, generation=None):
        """One tick: read hardware, latch a snapshot. Never raises."""
        if generation is None:
            generation = self._generation
        elif generation != self._generation:
            return False          # a stale loop retires here

        if self._spec is None:
            return False

        if not self.is_open:
            self._handle_disconnect("not present at the OS level")
            return False

        try:
            # One tick reads many joystick values and every Gamepad has its
            # own thread. pygame's joystick API is not thread-safe, so the
            # whole tick is taken under the hub's lock rather than leaving two
            # threads to interleave inside it.
            with self._hub.lock():
                module = pygame
                if module is not None:
                    module.event.pump()
                    module.event.get()
                joystick = self._joystick
                if joystick is None:
                    self._handle_disconnect("handle went away")
                    return False
                self._read_hardware(joystick)
        except Exception as exc:
            events.warn("Gamepad Polling Error",
                        f"{self._owner_id}: {exc}", source=self._source, exception=exc)
            self._handle_disconnect(f"poll failed: {exc}")
            return False

        self._capture_state()
        self._report_rate()
        return True

    def _read_hardware(self, joystick):
        """Refresh the raw caches and log anything that moved. Holds the lock.

        Was `_read_hardware_changes`. Raw readings are stored **undeadzoned**:
        the deadzone that reaches the firmware is applied once, on the way
        out, in `_apply_deadzones` (GAMEPAD-14). `LOG_DEADZONE` still decides
        what counts as movement worth a log line, exactly as before, but it no
        longer edits the value.
        """
        for index in range(joystick.get_numaxes()):
            value = joystick.get_axis(index)
            shown = 0.0 if abs(value) < self.LOG_DEADZONE else value
            previous = self._axis_values.get(index, 0.0)
            previous_shown = 0.0 if abs(previous) < self.LOG_DEADZONE else previous
            if round(shown, 2) != round(previous_shown, 2):
                self._note(f"Axis {index} changed: {shown:.2f}")
            self._axis_values[index] = value

        for index in range(joystick.get_numbuttons()):
            value = joystick.get_button(index)
            if value != self._button_values.get(index, 0):
                self._note(f"Button {index} {'pressed' if value else 'released'}")
                self._button_values[index] = value

        for index in range(joystick.get_numhats()):
            value = joystick.get_hat(index)
            if value != self._hat_values.get(index, (0, 0)):
                self._note(f"Hat {index} (DPad) changed: {value}")
                self._hat_values[index] = value

    def _report_rate(self):
        """Observed poll rate, to the log file only, once every few seconds."""
        self._tick_count += 1
        now = time.monotonic()
        elapsed = now - self._rate_mark
        if elapsed < self.RATE_REPORT_SECONDS:
            return
        rate = self._tick_count / elapsed if elapsed else 0.0
        events.debug("Gamepad poll rate",
                     f"{self._owner_id}: {rate:.0f} Hz observed over {elapsed:.1f}s "
                     f"({self._tick_count} ticks, {1000.0 / self.POLL_INTERVAL:.0f} Hz nominal)",
                     source=self._source, every=self.RATE_REPORT_SECONDS)
        self._tick_count = 0
        self._rate_mark = now

    # -- the snapshot --------------------------------------------------

    def _read_layout(self):
        """Today's mapped state for the bound layout. One reader, no classes.

        Was `BaseGamepad.get_mapped_state` and the four subclass overrides,
        plus `update_overrides`.
        """
        spec = self._spec
        if spec is None:
            return None
        axes, buttons, hats = self._axis_values, self._button_values, self._hat_values
        if self._override is not None:
            self._override(axes, buttons)
        hat = hats.get(spec["hat"], (0, 0))
        return {
            "x_axisStatus": _source_value(axes, spec["x"], 0.0),
            "y_axisStatus": _source_value(axes, spec["y"], 0.0),
            "z_axisStatusL": self._trigger_value(spec["z_left"], axes, buttons),
            "z_axisStatusR": self._trigger_value(spec["z_right"], axes, buttons),
            # D-pad sign is passed through unnegated; see GAMEPAD-13 above.
            "dpad_LR": hat[0],
            "dpad_UD": hat[1],
            "LBumper": _source_value(buttons, spec["bumper_left"], 0),
            "RBumper": _source_value(buttons, spec["bumper_right"], 0),
        }

    @staticmethod
    def _trigger_value(source, axes, buttons):
        if isinstance(source, tuple) and source and isinstance(source[0], str):
            kind, index = source
            if kind == "button":
                return 1.0 if buttons.get(index, 0) else -1.0
            if kind == "axis2x":
                return axes.get(index, 0.0) * 2.0 - 1.0
            raise ValueError(f"unknown trigger source {source!r}")
        return _source_value(axes, source, -1.0)

    def _read_raw(self):
        """Mapped state, or None if the device is gone.

        `_read_layout` only reads this object's own caches, so the guard below
        is unreachable today (GAMEPAD-17). It is kept for a future layout that
        touches hardware directly, and scoped so the guard itself cannot
        crash: `except pygame.error` used to evaluate `None.error` the moment
        anything else in the try block raised when pygame had failed to
        import, replacing that exception with an unrelated AttributeError.
        """
        if self._spec is None:
            return None
        try:
            return self._read_layout()
        except (pygame.error if pygame else ()) as exc:
            events.warn("Gamepad Disconnected",
                        f"{self._owner_id}: hardware error during poll: {exc}",
                        source=self._source, exception=exc)
            self._handle_disconnect(f"hardware error: {exc}")
            return None

    @staticmethod
    def _apply_deadzones(state):
        """The one place a deadzone is applied (GAMEPAD-14)."""
        for key in ("x_axisStatus", "y_axisStatus"):
            if abs(state.get(key, 0.0)) < Gamepad.DEADZONE:
                state[key] = 0.0
        for key in ("z_axisStatusL", "z_axisStatusR"):
            if state.get(key, 0.0) < Gamepad.TRIGGER_SNAP:
                state[key] = -1.0
        return state

    def _capture_state(self):
        """Latch one tick of input. Called by the loop, never by a reader.

        Edges are detected here, at poll time, and accumulate until drained.
        They used to be detected inside the reader, so the first reader
        consumed the edge for every other reader and a tap shorter than the
        gap between reads was never seen at all.
        """
        raw = self._read_raw()
        if raw is None:
            return
        with self._lock:
            if self._is_neutralised:
                if raw == self._neutral_reference:
                    # Still exactly where it was when focus went away: hold
                    # the neutral snapshot and latch no edges (GAMEPAD-8).
                    self._last_raw = raw
                    return
                self._is_neutralised = False
                self._neutral_reference = None
                events.debug("Gamepad re-armed",
                             f"{self._owner_id}: pad moved after a neutral flush",
                             source=self._source, every=1.0)
            self._last_raw = raw
            levels = self._apply_deadzones(dict(raw))
            for key in self.EDGE_KEYS:
                current = raw.get(key, 0)
                if current != 0 and current != self._latch.get(key, 0):
                    self._pending_edges[key] = current
                self._latch[key] = current
                levels[key] = 0          # levels never carry edges
            self._levels = levels

    @property
    def levels(self):
        """Continuous input (sticks, triggers). Non-consuming, thread-safe.

        Any number of readers on any number of threads may read this; it
        changes nothing. Edges live in `drain_edges()` precisely so a
        background reader cannot swallow a D-pad tap the jog loop needed
        (GAMEPAD-15).
        """
        with self._lock:
            if self._spec is None:
                return {}
            return dict(self._levels)

    def drain_edges(self):
        """Discrete presses since the last drain. Single consumer.

        Returns them and clears them, so exactly one caller acts on each
        press. That caller is the model's jog loop.
        """
        with self._lock:
            edges, self._pending_edges = self._pending_edges, {}
            return edges

    def flush_neutral(self):
        """Neutralise the snapshot until the pad physically moves again.

        The old version rewrote the raw axis cache, which could not work: the
        poll loop -- four times faster than the jog pump -- read the held
        stick straight back in and refilled it before the next send. Worse, it
        treated axes (2, 4, 5) as triggers idling at -1.0 regardless of
        platform, and on Linux an Xbox pad's axis 4 is the right-stick **Y**:
        the "safety" flush wrote a full-speed Y jog into the state it was
        supposed to be clearing (GAMEPAD-8, VIEW-TKINTER-9).

        Neutral is expressed in *mapped* keys, so no axis number is guessed at
        and nothing can write -1.0 into a stick. It holds until the pad reads
        differently from the moment of the flush.
        """
        with self._lock:
            self._neutral_reference = self._last_raw if self._last_raw is not None \
                else self._read_layout()
            self._is_neutralised = True
            self._levels = dict(NEUTRAL)
            self._latch.clear()
            self._pending_edges.clear()
        events.debug("Gamepad flushed neutral",
                     f"{self._owner_id}: snapshot held neutral until the pad moves",
                     source=self._source)

    # -- the input gate ------------------------------------------------

    @property
    def is_gate_open(self):
        return self._gate.is_set()

    def set_gate(self, is_open):
        """Open or close the manual input gate (D-4). Was `set_input_gate`.

        The Controller forwards window focus here. **A child dialog
        deactivating the main window is not focus loss** -- that distinction
        is the actual defect behind GAMEPAD-8 / PYSIDE-14 / VIEW-TKINTER-9,
        and each view makes it before calling. Gating the *send* is what
        actually holds an axis; this flag is what the jog loop reads.
        """
        was_open = self._gate.is_set()
        if is_open:
            self._gate.set()
        else:
            self._gate.clear()
        if was_open != bool(is_open):
            events.debug("Gamepad gate",
                         f"{self._owner_id}: {'open' if is_open else 'closed'} "
                         f"(was {'open' if was_open else 'closed'})", source=self._source)
            if not is_open:
                self.flush_neutral()

    # -- the pad log ---------------------------------------------------

    @property
    def log(self):
        """The buffered pad log, oldest first. The `log_stream` source.

        It used to live on the probe and be shown by a Tk/Qt window the Web
        client could not build; it is a schema element now, so all three
        frontends read the same lines.
        """
        with self._lock:
            return list(self._log)

    def _note(self, message):
        with self._lock:
            self._log.append(str(message))
        # Inside the poll loop: rate-limited, file only.
        events.debug("Gamepad activity", f"{self._owner_id}: {message}",
                     source=self._source, every=1.0)
