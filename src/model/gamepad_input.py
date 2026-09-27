"""Gamepad input, as a mixin any Model with `NEEDS_GAMEPAD = True` takes (MOD-1).

Was probe code. The Gamepad device speaks the channels in
`devices.gamepad.NEUTRAL` (the contract: sticks, triggers, hat and bumper
edges); this mixin owns everything between that device and a model:

* building the Gamepad from a name and binding it on `open()`,
* `gamepad_name`, `gamepad_options`, `gamepad_log`, `set_gamepad` (a failed
  bind reverts the selection and drops the model out of its gamepad mode),
* the pump: every tick drains the edges (in every mode, so a press made
  while idle, gated or latched never fires later), and while
  `_pumps_gamepad` and the gate is open and the latch is clear hands
  `levels` (edges merged in) to `_on_gamepad`; leaving that state sends ONE
  neutral call, `_on_gamepad({}, {})`, never a stream (I-4.2),
* the lost-pad transition and the pump-fault transition,
* `_gamepad_elements()`: the "Gamepad:" dropdown and the tier-3 log stream,
  for the model to compose into its own sections.

A model declares `class X(GamepadInput, Model)`, calls
`self._attach_gamepad(gamepad)` in `__init__` after `super().__init__()`,
and writes:

    GAMEPAD_RATE_HZ            the pump's rate
    _pumps_gamepad             property: True while input should drive it
    _on_gamepad(levels, edges) act on one tick of input ({} = neutral)
    _on_gamepad_lost(reason)   leave the gamepad-driven mode
    _on_gamepad_fault(reason)  the pump raised: hardware state unknown

Operator copy and command names are the probe's, unchanged: the Web client
keys its empty state on `gamepad_log`.
"""
import schema as sch
from devices import gamepad as gamepad_device
from events import events
from result import Refused


class GamepadInput:
    """Mixin: put it before `Model` in the bases."""

    #: Pump rate. The probe's is 50 Hz, the manual jog stream.
    GAMEPAD_RATE_HZ = 50.0

    #: `_on_gamepad_lost` reasons. Only LOST is the pad going away on its
    #: own; the other two are the operator's choice in the dropdown.
    GAMEPAD_LOST = "gamepad lost"
    GAMEPAD_RELEASED = "gamepad released"
    GAMEPAD_BIND_FAILED = "gamepad bind failed"

    def __init__(self, *args, **kwargs):
        self.gamepad = None
        self._gamepad_name = None
        self._was_pumping = False     # the previous tick's pumping state
        super().__init__(*args, **kwargs)

    # -- hooks the model writes --------------------------------------------
    @property
    def _pumps_gamepad(self):
        return False

    def _on_gamepad(self, levels, edges):
        """One tick of input. `levels` carries the edges merged in; `{}` is
        neutral (a pad that could not be read, or the one frame on exit)."""

    def _on_gamepad_lost(self, reason):
        """The pad went away while it was driving the model."""

    def _on_gamepad_fault(self, reason):
        """The pump raised. The hardware state is unknown."""

    # -- building and binding ----------------------------------------------
    def _attach_gamepad(self, gamepad):
        """Build the Gamepad from a name (Setup passes a name or None), or
        take the device a test hands in."""
        self.gamepad = self._build_gamepad(gamepad)
        self._gamepad_name = gamepad if isinstance(gamepad, str) else None

    def _build_gamepad(self, gamepad):
        if gamepad is None or isinstance(gamepad, str):
            hub = getattr(gamepad_device, "hub", None)
            if hub is None:
                return gamepad_device.Gamepad(self.NAME)
            return gamepad_device.Gamepad(self.NAME, hub=hub)
        return gamepad

    def open(self):
        super().open()
        if self._gamepad_name and self._gamepad_name != "None":
            try:
                self.gamepad.bind(self._gamepad_name)
            except Exception as exc:
                events.debug("Gamepad Bind Failed",
                             f"{self._gamepad_name!r}: {exc!r}",
                             source=self.NAME, exception=exc)
                events.warn("Gamepad Bind Failed",
                            f"Could not connect to the gamepad "
                            f"{self._gamepad_name}. Check it is plugged in, "
                            "then choose it again.", source=self.NAME,
                            exception=exc)

    def _start_threads(self):
        super()._start_threads()
        self._spawn("gamepad", self._gamepad_loop)

    # -- what the model and the views read ---------------------------------
    @property
    def _is_gamepad_bound(self):
        return bool(self.gamepad is not None and self.gamepad.is_bound)

    @property
    def _is_gate_open(self):
        return bool(self.gamepad is not None and self.gamepad.is_gate_open)

    @property
    def gamepad_name(self):
        """What the Gamepad says it is bound to, not a mirror this model keeps
        in step by hand."""
        if self.gamepad is None:
            return "None"
        name = getattr(self.gamepad, "name", None)
        if name is None:
            name = self._gamepad_name if self._is_gamepad_bound else None
        return name or "None"

    def gamepad_options(self):
        if self.gamepad is None:
            return ["None"]
        return list(self.gamepad.options)

    def gamepad_log(self):
        if self.gamepad is None:
            return []
        return list(self.gamepad.log)

    def set_gamepad(self, name):
        """Bind a gamepad. A failed bind reverts the selection and stops.

        The selection used to be assigned *before* the bind was attempted, so
        a failed swap left the UI naming a pad that was never bound and the
        mode flipping lazily on some later tick (GAMEPAD-3, GAMEPAD-4).
        """
        previous = self._gamepad_name
        wanted = None if name in (None, "", "None") else str(name)
        bound = False
        try:
            bound = bool(self.gamepad.bind(wanted))
        except Exception as exc:
            events.debug("Gamepad Bind Failed", f"{name!r}: {exc!r}",
                         source=self.NAME, exception=exc)
            events.warn("Gamepad Bind Failed", f"Could not connect to the "
                        f"gamepad {name}. Check it is plugged in, then choose "
                        "it again.", source=self.NAME, exception=exc)
        if wanted is None:
            self._gamepad_name = None
            events.debug("Gamepad", "unbound by operator", source=self.NAME)
            if self._pumps_gamepad:
                self._on_gamepad_lost(self.GAMEPAD_RELEASED)
            return "None"
        if not bound:
            self._gamepad_name = previous
            if self._pumps_gamepad:
                # Driven by a pad that is not bound is exactly the state
                # I-3.2 forbids.
                self._on_gamepad_lost(self.GAMEPAD_BIND_FAILED)
            events.debug("Gamepad", f"bind to {name!r} failed; selection "
                         f"reverted to {previous!r}", source=self.NAME)
            reason = (f"Could not connect to the gamepad {name}. Check it is "
                      "plugged in, then choose it again.")
            events.debug("Refused", reason, source=self.NAME)
            raise Refused(reason)
        self._gamepad_name = wanted
        events.debug("Gamepad", f"bound to {wanted!r}", source=self.NAME)
        return wanted

    def _gamepad_elements(self):
        """(dropdown, log stream): the "Gamepad:" choice and the detached
        tier-3 log, for the model to place in its own sections."""
        return (sch.dropdown("Gamepad:", "gamepad_name", "set_gamepad",
                             "gamepad_options"),
                # G4: behind a button, in its own window, not on the card.
                sch.log_stream("Gamepad Log:", "gamepad_log", detached=True))

    @staticmethod
    def _is_off_neutral(levels):
        """True when any channel in `levels` is away from its neutral value:
        real use, for an idle clock."""
        return any(levels.get(key, rest) not in (None, rest)
                   for key, rest in gamepad_device.NEUTRAL.items())

    # -- input ---------------------------------------------------------------
    def _drain_edges(self):
        """Discrete presses (hat, bumpers) since the last tick, or `{}`.

        The one consumer of `Gamepad.drain_edges()`. A failed drain holds the
        edges at 0 and is logged; the levels read beside it reports a broken
        pad to the operator.
        """
        if self.gamepad is None:
            return {}
        try:
            return dict(self.gamepad.drain_edges() or {})
        except Exception as exc:
            events.debug("Gamepad Drain Failed", repr(exc), source=self.NAME,
                         exception=exc, every=1.0)
            return {}

    def _axis_state(self):
        """The gamepad's mapped levels, or `{}` if they could not be read.

        The read itself is guarded, not just the float cast (REDPERCENT-4): a
        pad unplugged mid-session is the ordinary case. Reported rather than
        swallowed -- the event log folds repeats of one event into a single
        counted entry, so a failure at 50 Hz is one warning, not a storm.
        """
        if self.gamepad is None:
            return {}
        try:
            return dict(self.gamepad.levels or {})
        except Exception as exc:
            events.debug("Gamepad Read Failed", repr(exc), source=self.NAME,
                         exception=exc)
            events.warn("Gamepad Read Failed",
                        "The gamepad could not be read. Every axis is held at "
                        "neutral until it recovers.",
                        source=self.NAME, exception=exc)
            return {}

    # -- the pump ------------------------------------------------------------
    def _gamepad_loop(self):
        """Pump gamepad input to the model at GAMEPAD_RATE_HZ.

        The gate is checked here, in the one place that turns gamepad input
        into motion. `flush_neutral` used to be the answer and could not
        work: the poll loop simply read the physical stick again and refilled
        the cache before the next send. Gating the *send* is what holds the
        axis.
        """
        self._was_pumping = False
        interval = 1.0 / float(self.GAMEPAD_RATE_HZ)
        while not self._threads_stop.wait(interval):
            if not self._gamepad_tick():
                return

    def _gamepad_tick(self):
        """One pump tick: the body of `_gamepad_loop`, driven directly by
        tests. Returns False only when the pump has faulted and must stop.

        Edges travel one per press: the Gamepad parks each press and zeroes
        those keys in `levels`, so the levels alone never carry one. They are
        drained on **every** tick in **every** mode and used only when this
        tick pumps; otherwise they are discarded. Draining only while pumping
        would leave a tap made while idle, gated or latched parked in the
        pad, to fire on the first tick after entering the mode.

        Losing the pad while pumping leaves the mode through the model's own
        transition (STEPPER-5): clearing a flag alone once left the coils
        energized in a mode nothing was driving.
        """
        try:
            edges = self._drain_edges()
            if self._pumps_gamepad and not self._is_gamepad_bound:
                self._on_gamepad_lost(self.GAMEPAD_LOST)
            pumping = self._pumps_gamepad and self._is_gate_open
            if pumping and not self._estop.is_set():
                levels = self._axis_state()
                if levels:
                    # A pad whose levels could not be read is held at
                    # neutral, so it does not step either.
                    for key in gamepad_device.Gamepad.EDGE_KEYS:
                        levels[key] = edges.get(key, 0)
                self._on_gamepad(levels, edges)
            elif self._was_pumping:
                # Neutral on exit (I-4.2): leaving the mode, or having the
                # gate close under it, sends one neutral tick or the last
                # non-zero command stands. One, not a stream -- the gate is
                # not a stop.
                self._on_gamepad({}, {})
            self._was_pumping = pumping
        except Refused as refusal:
            events.debug("Gamepad Refused", refusal.reason, source=self.NAME,
                         every=1.0)
            self._was_pumping = False
        except Exception as exc:
            events.debug("Gamepad Pump Failed", repr(exc), source=self.NAME,
                         exception=exc)
            self._on_gamepad_fault(repr(exc))
            return False
        return True
