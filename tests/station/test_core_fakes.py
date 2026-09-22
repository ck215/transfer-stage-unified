"""Shared fakes for the frozen-core test suite. Contains no tests itself.

Every core test builds its Model/Device/View out of these rather than out of a
real subsystem, so a failure here is a failure of `station/` core and not of
whatever a peer agent is writing this hour.
"""
import threading
import time

from station import schema as sch
from station.devices.device import Device
from station.events import events as global_events
from station.model import Model
from station.panel import Panel
from station.param import Param
from station.result import NeedsConfirm, Refused
from station.views.base import Dashboard, PanelView


# -- events ----------------------------------------------------------------

class EventRecorder:
    """Subscribe to an EventLog for the duration of a `with` block.

    Clears the log on entry: the dedupe window is five seconds and the module
    singleton is shared by every test in the session, so without this a second
    test raising the same failure sees no event at all.
    """

    def __init__(self, log=None):
        self.log = global_events if log is None else log
        self.seen = []

    def _on_event(self, event):
        self.seen.append(event)

    def __enter__(self):
        self.log.clear()
        self.log.subscribe(self._on_event)
        return self

    def __exit__(self, *exc_info):
        self.log.unsubscribe(self._on_event)
        return False

    @property
    def severities(self):
        return [e.severity for e in self.seen]

    @property
    def acknowledged(self):
        return [e for e in self.seen if e.needs_ack]

    def titled(self, title):
        return [e for e in self.seen if e.title == title]


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds
        return self.now


# -- devices ---------------------------------------------------------------

class FakeDevice(Device):
    def __init__(self, name="fake", open_error=None, close_error=None,
                 status="verified"):
        self.name = name
        self.open_error, self.close_error = open_error, close_error
        self.open_calls = self.close_calls = 0
        self._is_open = False
        self._status = status

    def open(self):
        self.open_calls += 1
        if self.open_error is not None:
            raise self.open_error
        self._is_open = True

    def close(self):
        self.close_calls += 1
        self._is_open = False
        if self.close_error is not None:
            raise self.close_error

    @property
    def is_open(self):
        return self._is_open

    @property
    def status(self):
        return self._status if self._is_open else "closed"


# -- panels ----------------------------------------------------------------

SPEED = Param("speed", "float", default=100.0, minimum=0.0, maximum=500.0,
              decimals=1, unit="mm/s", label="Speed")
STEPS = Param("steps", "int", default=4, minimum=1, maximum=16, label="Steps")
NOTE = Param("note", "text", default="", label="Note")


class FakePanel(Panel):
    """A Panel with no hardware: the allow-list, inputs and gating, alone."""

    NAME = "FakePanel"
    PARAMS = {"speed": SPEED, "steps": STEPS, "note": NOTE}

    def __init__(self, mode="idle"):
        self.mode = mode
        self.ran = []
        super().__init__()

    @property
    def mode_name(self):
        return self.mode

    @property
    def schema(self):
        return sch.schema(sch.section(
            "Main",
            sch.entry("Speed", "speed", SPEED, disabled_when=("running",)),
            sch.entry("Steps", "steps", STEPS, disabled_when=("running",)),
            sch.entry("Note", "note", NOTE),
            sch.readonly("Mode:", "mode"),
            sch.button("Go", "go", inputs=("speed", "steps"),
                       disabled_when=("running",)),
            sch.button("Always", "always"),
        ))

    def go(self):
        self.ran.append(("go", self.speed, self.steps))
        return "went"

    def always(self):
        self.ran.append(("always",))
        return "ok"

    def secret(self):            # never declared by the schema
        self.ran.append(("secret",))
        return "leaked"


class FakeModel(Model):
    """A Model with every element type the core has to gate, stop and close."""

    NAME = "Fake"
    IDENTITY = "F"
    PARAMS = {"speed": SPEED, "steps": STEPS, "note": NOTE}
    ESTOP_BUDGET = 0.08

    def __init__(self, devices=None, halt_result=True, halt_delay=0.0,
                 halt_error=None, halt_blocks=False):
        self.mode = "idle"
        self.port_name = None
        self._device_list = list(devices or [])
        self.halt_result = halt_result
        self.halt_delay = halt_delay
        self.halt_error = halt_error
        self.halt_blocks = halt_blocks
        self.halt_gate = threading.Event()
        self.halt_latched_at_call = []     # is_estopped as the stop was sent
        self.moved = []
        self.data_reads = 0
        self.start_calls = self.stop_calls = self.disable_calls = 0
        self.enable_calls = 0
        self.start_error = self.stop_error = self.disable_error = None
        self.added_seen, self.removed_seen = [], []
        self.close_order = []
        super().__init__()

    # -- lifecycle ---------------------------------------------------------
    @property
    def devices(self):
        return list(self._device_list)

    def _start_threads(self):
        self.start_calls += 1
        self.close_order.append("_start_threads")
        if self.start_error is not None:
            raise self.start_error

    def _stop_threads(self):
        self.stop_calls += 1
        self.close_order.append("_stop_threads")
        if self.stop_error is not None:
            raise self.stop_error

    def enable(self):
        self.enable_calls += 1

    def disable(self):
        self.disable_calls += 1
        self.close_order.append("disable")
        if self.disable_error is not None:
            raise self.disable_error

    def on_model_added(self, name, model):
        self.added_seen.append(name)

    def on_model_removed(self, name, model):
        self.removed_seen.append(name)

    # -- the stop ----------------------------------------------------------
    def _halt_hardware(self):
        self.halt_latched_at_call.append(self.is_estopped)
        self.close_order.append("halt")
        if self.halt_blocks:
            self.halt_gate.wait(10)
        elif self.halt_delay:
            time.sleep(self.halt_delay)
        if self.halt_error is not None:
            raise self.halt_error
        return self.halt_result

    def release(self):
        self.halt_gate.set()

    # -- state -------------------------------------------------------------
    @property
    def mode_name(self):
        return self.mode

    @property
    def is_active(self):
        return self.mode == "running"

    @property
    def is_auto(self):
        return self.mode == "auto"

    @property
    def is_running(self):
        return self.mode == "running"

    @property
    def schema(self):
        return sch.schema(
            sch.section(
                "Motion",
                sch.entry("Speed", "speed", SPEED, disabled_when=("running",)),
                sch.entry("Steps", "steps", STEPS, disabled_when=("running",)),
                sch.entry("Note", "note", NOTE),
                sch.readonly("Mode:", "mode"),
                sch.button("Move", "move", inputs=("speed", "steps"), role="go",
                           disabled_when=("running",)),
                sch.button("Slow move", "slow_move", role="go"),
                sch.button("Boom", "boom"),
                sch.button("Ask", "ask"),
                sch.button("Nope", "nope"),
                sch.dropdown("Port", "port_name", "set_port", "port_options"),
                sch.plot("Trace", "trace_data"),
                sch.log_stream("Log", "log_lines"),
            ),
            sch.section(
                "Modes",
                sch.toggle("Auto", "is_auto", "set_mode", "AUTO", "MANUAL",
                           on_args=("auto",), off_args=("idle",)),
                sch.toggle("Run", "is_running", "set_mode", "RUNNING", "STOPPED",
                           on_args=("running",), off_args=("idle",),
                           disabled_when=("auto",)),
            ),
            self._safety_section(),
        )

    # -- commands ----------------------------------------------------------
    def move(self):
        self._guard("Move")
        self.moved.append((self.speed, self.steps))
        return "moved"

    def slow_move(self, seconds=0.2):
        """Records its own entry and exit, so a test can see interleaving."""
        self._guard("Move")
        self.moved.append("enter")
        time.sleep(seconds)
        self.moved.append("exit")
        return "moved"

    def boom(self):
        raise RuntimeError("the board said no")

    def ask(self, direction="north", confirmed=False):
        """A command whose re-run signature is `command(*args, True)`."""
        if not confirmed:
            raise NeedsConfirm("Really move?", "ask", inputs={"speed": "12.0"},
                               args=(direction,))
        self.moved.append(("confirmed", direction))
        return "asked"

    def nope(self):
        raise Refused("not while the lid is open")

    def set_mode(self, mode):
        self.mode = mode
        return mode

    def set_port(self, name):
        self.port_name = name
        return name

    def port_options(self):
        return ["COM1", "COM2"]

    def trace_data(self):
        self.data_reads += 1
        return [1, 2, 3]

    def log_lines(self):
        return ["one line"]


class FakeSetupPanel(Panel):
    NAME = "Setup"
    PARAMS = {"note": NOTE}

    @property
    def schema(self):
        return sch.schema(sch.section(
            "Setup", sch.entry("Note", "note", NOTE),
            sch.button("Build", "build")))

    def build(self):
        return "built"


# -- views -----------------------------------------------------------------

class FakePanelView(PanelView):
    """A PanelView with a recording toolkit instead of Tk or Qt."""

    def __init__(self, controller, name, panel=None):
        self.sections, self.built = [], []
        self.texts, self.on_states, self.data, self.enabled = {}, {}, {}, {}
        self.entry_text, self.dirty_entries = {}, set()
        self.refusals, self.prompts = [], []
        self.confirm_answer = True
        self.stale = None
        self.theme_calls = 0
        super().__init__(controller, name, panel)

    @staticmethod
    def key(element):
        return element.get("model_attr") or element.get("text")

    def _make_section(self, title):
        self.sections.append(title)
        return title

    def _read_entry(self, element):
        return self.entry_text.get(element["model_attr"], "")

    def _entry_is_dirty(self, element):
        return element["model_attr"] in self.dirty_entries

    def _set_text(self, element, text):
        self.texts[self.key(element)] = text
        if element["type"] == "entry":
            self.entry_text[element["model_attr"]] = text

    def _set_on(self, element, is_on):
        self.on_states[self.key(element)] = is_on

    def _set_data(self, element, data):
        self.data[self.key(element)] = data

    def _set_enabled(self, element, is_enabled):
        self.enabled[self.key(element)] = is_enabled

    def _set_stale(self, is_stale):
        self.stale = is_stale

    def _confirm(self, prompt):
        self.prompts.append(prompt)
        return self.confirm_answer

    def _show_refused(self, reason):
        self.refusals.append(reason)

    def _apply_theme(self):
        self.theme_calls += 1


def _make_recorder(kind):
    def _make(self, container, element):
        self.built.append((kind, element.get("text")))
    _make.__name__ = f"_make_{kind}"
    return _make


for _element_type in sorted(sch.ELEMENT_TYPES):
    setattr(FakePanelView, f"_make_{_element_type}", _make_recorder(_element_type))


class FakeDashboard(Dashboard):
    def __init__(self, controller, setup=None):
        super().__init__(controller, setup)
        self.shown, self.popups = [], []
        self.added_panels, self.removed_panels = [], []
        self.prompts = []
        self.confirm_answer = True
        self.defer = False
        self.pending = []

    def flush(self):
        pending, self.pending = list(self.pending), []
        for fn in pending:
            fn()

    def _marshal(self, fn):
        if self.defer:
            self.pending.append(fn)
        else:
            fn()

    def _show_event(self, event):
        self.shown.append(event)

    def _show_popup(self, event):
        self.popups.append(event)

    def _confirm(self, prompt):
        self.prompts.append(prompt)
        return self.confirm_answer

    def _add_panel(self, name):
        self.added_panels.append(name)

    def _remove_panel(self, name):
        self.removed_panels.append(name)
