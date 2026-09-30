"""Tk renderer tests. No window is ever opened.

The harness is the one the old suite settled on (`tests/conftest.py`): the
whole of `tkinter` is a stand-in, so importing the view cannot start a Tcl
interpreter. That file's stand-in is deliberately thin, so the widget classes
here are richer ones patched onto `views.tk`'s module globals — every
widget the view builds goes through them, and every option it sets is
readable afterwards.

Two things it does NOT stub: the schema and `Panel`. `DemoPanel` is a real
`panel.Panel`, so `run()` really validates inputs, really raises
`Refused` and `NeedsConfirm`, and really builds a `Result`. A view test that
passes against a mocked command channel proves very little.
"""
import ast
import base64
import os
import re
import tempfile

import pytest

import schema as sch
from events import Event
from panel import Panel
from param import Param
from result import Refused, NeedsConfirm, Result
from views import base as view_base
from views import theme
from views import tk as tkmod

pytestmark = pytest.mark.tk


# ---------------------------------------------------------------------------
# A Tk stand-in with real bookkeeping
# ---------------------------------------------------------------------------

class Scheduler:
    """Every `after` callback the view registers, so a test can run them."""

    def __init__(self):
        self.pending = {}
        self.cancelled = []
        self._next = 1

    def add(self, fn):
        token = f"after#{self._next}"
        self._next += 1
        self.pending[token] = fn
        return token

    def cancel(self, token):
        self.cancelled.append(token)
        self.pending.pop(token, None)

    def pump(self):
        """Run one round. A callback that reschedules itself does not loop."""
        due, self.pending = list(self.pending.items()), {}
        for _token, fn in due:
            fn()
        return len(due)


class Focus:
    current = None


class FakeTcl:
    def call(self, *args):
        if args[:2] == ("tk", "windowingsystem"):
            return "x11"
        raise RuntimeError(f"unstubbed tcl call {args}")


class FakeVar:
    def __init__(self, master=None, value=None):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class FakeWidget:
    def __init__(self, master=None, **options):
        self.master = master
        self.options = dict(options)
        self.bindings = {}
        self.children = []
        self.is_destroyed = False
        self.grid_info = None
        self.is_packed = False
        self.column_weights = {}
        self.tk = FakeTcl()
        self.scheduler = getattr(master, "scheduler", None) or SCHEDULER
        if isinstance(master, FakeWidget):
            master.children.append(self)

    # -- options
    def configure(self, **options):
        self.options.update(options)
    config = configure

    def cget(self, option):
        return self.options.get(option)

    def __getitem__(self, option):
        return self.options.get(option)

    # -- geometry
    def pack(self, **kwargs):
        self.grid_info = self.grid_info or {}
        self.is_packed = True
        self.pack_options = kwargs          # the last pack: a key's pads
        PACK_ORDER.append((self, kwargs))

    def grid(self, **kwargs):
        self.grid_info = kwargs

    def grid_configure(self, **kwargs):
        self.grid_info = dict(self.grid_info or {}, **kwargs)

    def grid_remove(self):
        self.grid_info = None

    def grid_forget(self):
        self.grid_info = None

    def pack_forget(self):
        self.is_packed = False

    def grid_columnconfigure(self, column, **options):
        # Tk merges a column's options (a later minsize keeps the weight).
        self.column_weights.setdefault(column, {}).update(options)

    def yview(self, *_args):
        return None

    def set(self, *_args):
        """A scrollbar's `set`; the Text's `yscrollcommand` is bound to it."""
        return None

    # -- events
    def bind(self, sequence, callback=None, add=None):
        self.bindings[sequence] = callback

    def bind_all(self, sequence, callback=None, add=None):
        ALL_BINDINGS[sequence] = callback

    def unbind_all(self, sequence):
        ALL_BINDINGS.pop(sequence, None)

    def fire(self, sequence, event=None):
        callback = self.bindings.get(sequence)
        assert callback is not None, f"nothing bound to {sequence}"
        return callback(event if event is not None else FakeEvent())

    # -- lifecycle
    def destroy(self):
        self.is_destroyed = True

    def winfo_exists(self):
        return not self.is_destroyed

    def focus_get(self):
        return Focus.current

    def focus_set(self):
        Focus.current = self

    def focus_force(self):
        Focus.current = self

    def grab_set(self):
        GRABS.append(self)

    def grab_release(self):
        pass

    def register(self, fn):
        return fn

    def wait_window(self, _window=None):
        return None

    def after(self, _ms, fn=None):
        return self.scheduler.add(fn)

    def after_cancel(self, token):
        self.scheduler.cancel(token)

    # -- screen geometry, for the region picker
    def winfo_vrootwidth(self):
        return 2560

    def winfo_vrootheight(self):
        return 1440

    def winfo_vrootx(self):
        return 0

    def winfo_vrooty(self):
        return 0

    def winfo_screenwidth(self):
        return 1920

    def winfo_screenheight(self):
        return 1080


class FakeEvent:
    def __init__(self, x=0, y=0, x_root=0, y_root=0, widget=None, delta=0,
                 num=0, width=0, height=0):
        self.x, self.y = x, y
        self.x_root, self.y_root = x_root, y_root
        self.widget = widget
        self.delta, self.num = delta, num       # the wheel, in both dialects
        self.width, self.height = width, height


class FakeText(FakeWidget):
    def __init__(self, master=None, **options):
        super().__init__(master, **options)
        self.body = ""
        self.tags = {}
        self.written_tags = []

    def insert(self, _index, text, *tags):
        self.body += text
        self.written_tags.extend(tags)

    def delete(self, *_args):
        self.body = ""

    def see(self, _index):
        pass

    def tag_configure(self, name, **options):
        self.tags[name] = options

    def tag_remove(self, name, *_indices):
        self.removed_tags = getattr(self, "removed_tags", []) + [name]


class FakeCanvas(FakeWidget):
    def __init__(self, master=None, **options):
        super().__init__(master, **options)
        self.items = []
        self.windows = {}
        self.scrolled = []

    # -- the scroll area
    def create_window(self, _position, window=None, **options):
        handle = f"window#{len(self.windows) + 1}"
        self.windows[handle] = dict(options, window=window)
        return handle

    def itemconfigure(self, handle, **options):
        self.windows.setdefault(handle, {}).update(options)

    def bbox(self, _what):
        return (0, 0, 100, 400)

    def yview(self, *_args):
        return None

    def yview_scroll(self, amount, what):
        self.scrolled.append((amount, what))

    def yview_moveto(self, fraction):
        self.moved_to = fraction

    def create_line(self, *points, **options):
        self.items.append(("line", points, options))
        return len(self.items)

    def create_text(self, x, y, **options):
        self.items.append(("text", (x, y), options))
        return len(self.items)

    def create_rectangle(self, *coords, **options):
        self.items.append(("rect", coords, options))
        return len(self.items)

    def create_oval(self, *coords, **options):
        self.items.append(("oval", coords, options))
        return len(self.items)

    def create_arc(self, *coords, **options):
        self.items.append(("arc", coords, options))
        return len(self.items)

    def create_image(self, x, y, **options):
        self.items.append(("image", (x, y), options))
        return len(self.items)

    def tag_raise(self, _item, _above=None):
        pass

    def coords(self, _item, *values):
        self.items.append(("coords", values, {}))

    def canvasx(self, value):
        return value

    def canvasy(self, value):
        return value

    def delete(self, _what):
        self.items = []


class FakeMenu(FakeWidget):
    def __init__(self, master=None, **options):
        super().__init__(master, **options)
        self.entries = []
        self.cascades = []

    def add_checkbutton(self, label=None, variable=None, command=None, **kwargs):
        self.entries.append({"label": label, "variable": variable,
                             "command": command})

    def add_command(self, label=None, command=None, **kwargs):
        self.entries.append({"label": label, "variable": None, "command": command})

    def add_cascade(self, label=None, menu=None, **kwargs):
        self.cascades.append({"label": label, "menu": menu})


class FakeRoot(FakeWidget):
    def __init__(self, *args, **options):
        self.scheduler = SCHEDULER
        super().__init__(None, **options)
        self.protocols = {}
        self.commands = {}
        self.did_mainloop = 0
        self.did_quit = 0
        self.titles = []
        self.attrs = []
        self.lifted = 0

    def title(self, *args):
        self.titles.extend(args)

    def lift(self, *_args):
        self.lifted += 1

    def deiconify(self):
        pass

    def geometry(self, *_args):
        pass

    def protocol(self, name, fn):
        self.protocols[name] = fn

    def createcommand(self, name, fn):
        self.commands[name] = fn

    def mainloop(self):
        self.did_mainloop += 1

    def quit(self):
        self.did_quit += 1

    def overrideredirect(self, _flag):
        pass

    def attributes(self, *args):
        self.attrs.append(args)


class FakePhotoImage:
    instances = []

    def __init__(self, data=None, **kwargs):
        self.data = data
        FakePhotoImage.instances.append(self)


class FakeDialogs:
    """`filedialog` and `messagebox`, recording what the view asked for."""

    def __init__(self):
        self.save_path = ""
        self.open_path = ""
        self.confirm_answer = True
        self.errors = []
        self.asked = []
        self.words = []         # a confirmation's title and answers (L14)

    # filedialog
    def asksaveasfilename(self, **kwargs):
        self.asked.append(("save", kwargs))
        return self.save_path

    def askopenfilename(self, **kwargs):
        self.asked.append(("open", kwargs))
        return self.open_path

    # messagebox
    def showerror(self, title, message, **kwargs):
        self.errors.append((title, message))

    def askyesno(self, title, prompt, **kwargs):
        self.asked.append(("confirm", prompt))
        return self.confirm_answer


class FakeTkModule:
    StringVar = FakeVar
    BooleanVar = FakeVar
    Frame = FakeWidget
    Label = FakeWidget
    Entry = FakeWidget
    Scrollbar = FakeWidget
    Canvas = FakeCanvas
    Text = FakeText
    Menu = FakeMenu
    Toplevel = FakeRoot
    Tk = FakeRoot
    PhotoImage = FakePhotoImage


class FakeStyle:
    """`ttk.Style`, recording what the view configured."""

    def __init__(self, master=None):
        STYLE.clear()

    def theme_use(self, _name):
        pass

    def configure(self, name, **options):
        STYLE.setdefault(name, {}).update(options)

    def map(self, name, **options):
        STYLE.setdefault(name + ":map", {}).update(options)


class FakeScale(FakeWidget):
    """`ttk.Scale`: like Tk's, `set` runs the widget's command."""

    def __init__(self, master=None, **options):
        super().__init__(master, **options)
        self.value = options.get("from_", 0)
        self.states = []

    def set(self, value):
        self.value = value
        command = self.options.get("command")
        if command is not None:
            command(str(value))

    def get(self):
        return self.value

    def state(self, spec=None):
        if spec is not None:
            self.states.append(list(spec))
        return self.states[-1] if self.states else []


class FakeTtkModule:
    Frame = FakeWidget
    Combobox = FakeWidget
    Checkbutton = FakeWidget
    Scrollbar = FakeWidget
    Scale = FakeScale
    Style = FakeStyle


SCHEDULER = Scheduler()
#: `bind_all` is application-wide in Tk, so the stand-in keeps one table for
#: the whole "application" — which is what lets a test see that a closed
#: panel has let go of the wheel.
ALL_BINDINGS = {}
#: Every `pack`, in order. Pack order IS allocation order in Tk, which is the
#: whole of why the FULL STOP bar can or cannot be pushed off the window.
PACK_ORDER = []
#: Every `grab_set`: a grab is what makes a window modal.
GRABS = []
#: What `ttk.Style` was configured with, by style name.
STYLE = {}


# ---------------------------------------------------------------------------
# A real Panel with every element type on it
# ---------------------------------------------------------------------------

def _internal(command):
    """`schema.py` has no builder for `internal`; it is a hand-written dict."""
    return {"type": "internal", "command": command, "writable": False,
            "role": "neutral"}


class DemoPanel(Panel):
    NAME = "Demo"
    PARAMS = {
        "speed": Param("speed", "float", default=1.0, minimum=0.0, maximum=10.0,
                       decimals=2, label="Speed"),
        "steps": Param("steps", "int", default=5, minimum=0, maximum=100,
                       label="Steps"),
        "note": Param("note", "text", default="hi", label="Note"),
    }
    #: A readout the schema types but the Param table does not carry — the
    #: shape `Setup.scan_progress` has, and the one where an int can still
    #: arrive at a view as "7.0".
    DONE_COUNT = Param("done_count", "int", label="Done")

    def __init__(self):
        super().__init__()
        self.done_count = 7.0
        self.is_running = False
        self.is_latched = False
        self.fault_reason = ""      # an empty coloured readout
        self.region = None
        self.source = None
        self.mode = "idle"
        self.commands = []
        self.saved_path = None
        self.loaded_path = None
        self.samples = []
        self.png = b"\x89PNG\r\n\x1a\n-demo"
        self.lines = ["first", "second"]
        self.side = ["gamepad on", "X+ 1.0"]
        self.side_reads = 0         # the detached stream's polls (G4)
        self.is_armed = False       # a checkbox (G3)
        self.target = None          # a dropdown live only while armed
        self.stop_confirmed = None  # `Model.stop_confirmed` (round 7, L1)
        self.fault = ""             # `Model.fault` / `is_faulted` (O4)
        self.idle_remaining = None  # `Probe.idle_remaining` (Tier N)
        self.extended = 0           # `extend_idle` runs

    @property
    def state(self):
        state = super().state
        state["stop_confirmed"] = self.stop_confirmed
        state["is_faulted"] = bool(self.fault)
        state["fault"] = self.fault
        state["idle_remaining"] = self.idle_remaining
        state["idle_warn_seconds"] = 60.0
        return state

    @property
    def mode_name(self):
        return self.mode

    @property
    def schema(self):
        return sch.schema(
            sch.section(
                "Readouts",
                sch.readonly("Speed now:", "speed", param=self.PARAMS["speed"]),
                sch.readonly("Done:", "done_count", param=self.DONE_COUNT),
                sch.readonly("Fault:", "fault_reason", role="info"),
                sch.indicator("Latched", "is_latched"),
                sch.plot("Series", "series", x_label="n", y_label="red"),
                sch.image("Figure", "figure"),
                sch.log_stream("Log", "log_lines"),
            ),
            sch.section(
                "Controls",
                sch.entry("Speed", "speed", self.PARAMS["speed"],
                          disabled_when=["running"]),
                sch.entry("Steps", "steps", self.PARAMS["steps"]),
                sch.entry("Note", "note", self.PARAMS["note"]),
                sch.button("Go", "go", inputs=["speed"], role="danger"),
                sch.button("Refuse", "refuse_me"),
                sch.button("Ask", "ask_me"),
                sch.toggle("Run", "is_running", "set_running", "RUNNING",
                           "STOPPED", on_args=[True], off_args=[False],
                           on_role="danger", off_role="neutral"),
                sch.dropdown("Source", "source", "set_source", "source_options"),
                sch.checkbox("Armed", "is_armed", "set_armed",
                             tooltip="Arm the demo"),
                sch.dropdown("Target", "target", "set_target", "source_options",
                             enabled_by="is_armed"),
                sch.log_stream("Side Log:", "side_lines", detached=True),
                sch.region_select("Pick area", "set_region", model_attr="region"),
                sch.file_save("Save", "save_run"),
                sch.file_open("Load", "load_run"),
                _internal("hidden"),
                _internal("extend_idle"),
            ),
        )

    # -- commands
    def go(self):
        self.commands.append(("go", self.speed))
        return "went"

    def refuse_me(self):
        raise Refused("the bench is busy")

    def ask_me(self, confirmed=False):
        if not confirmed:
            raise NeedsConfirm("Really?", "ask_me")
        self.commands.append(("ask_me", True))
        return "confirmed"

    def set_running(self, on):
        self.is_running = bool(on)
        self.mode = "running" if on else "idle"
        return self.is_running

    def set_source(self, name):
        self.source = name
        return name

    def source_options(self):
        return ["alpha", "beta"]

    def side_lines(self):
        self.side_reads += 1
        return list(self.side)

    def set_armed(self, flag):
        self.is_armed = bool(flag)
        return self.is_armed

    def set_target(self, name):
        self.target = name
        return name

    def set_region(self, x, y, width, height):
        self.region = {"left": x, "top": y, "width": width, "height": height}
        return self.region

    def save_run(self):
        handle, path = tempfile.mkstemp(suffix=".csv")
        with os.fdopen(handle, "w") as stream:
            stream.write("n,red\n0,1\n")
        self.saved_path = path
        return path

    def load_run(self, path):
        self.loaded_path = path
        return path

    def hidden(self):
        return "hidden"

    def extend_idle(self):
        """`Probe.extend_idle`: the idle clock starts again (Tier N)."""
        self.extended += 1
        self.idle_remaining = 300.0
        return self.idle_remaining

    # -- data commands
    def series(self):
        return {"x": list(range(len(self.samples))), "y": list(self.samples)}

    def figure(self):
        return self.png

    def log_lines(self):
        return list(self.lines)


class FakeController:
    """The Controller surface a view is allowed to touch, and nothing else."""

    def __init__(self, **panels):
        self.panels = dict(panels)
        self.closed = {}
        self.calls = []
        self.removed = []
        self.reopened = []
        self.focus_calls = []
        self.estop_calls = 0
        self.clear_calls = 0
        #: The models latched, by name (O17): the stop state is derived per
        #: model, as `Controller._stop_state` does - one latched model is a
        #: partial stop, never "every model".
        self.latched = []
        #: `Controller.state()["energized"]` (Tier N, O6).
        self.energized = []
        self.clear_needs_confirm = False
        self.is_closed = False
        self._subscribers = []
        #: A stop state to serve instead of the one `latched` implies
        #: (a partial stop, a model that did not confirm): round 7, L1.
        self.stop = None

    @property
    def is_estopped(self):
        """Any model latched (`Controller.is_estopped`)."""
        return bool(self.latched)

    @is_estopped.setter
    def is_estopped(self, value):
        """A test's global stop: every open model latched, or none."""
        self.latched = list(self.panels) if value else []

    @property
    def stop_state(self):
        """`Controller.stop_state` from the per-model latches, unless a test
        set `stop`. `every` only when every open model is latched."""
        if self.stop is not None:
            return dict(self.stop)
        latched = [name for name in self.panels if name in self.latched]
        return {"latched": latched, "unconfirmed": [],
                "every": bool(latched) and len(latched) == len(self.panels)}

    # -- what a view reads
    def schema(self, name):
        return self.panels[name].schema

    def state(self, name=None):
        if name is None:
            # `Controller.state()`: the station at once (Tier N, O4, O6).
            return {"models": {n: p.state for n, p in self.panels.items()},
                    "is_estopped": self.is_estopped,
                    "energized": [n for n in self.panels if n in self.energized],
                    "stop": self.stop_state, "closed": list(self.closed)}
        return self.panels[name].state

    def run(self, name, command, inputs=None, args=()):
        self.calls.append((name, command, dict(inputs or {}), tuple(args)))
        panel = self.panels.get(name)
        if panel is None:
            return Result(Result.REFUSED, reason=f"{name} is not open")
        return panel.run(command, inputs, args)

    def options(self, name, command):
        return self.panels[name].options(command)

    @property
    def model_names(self):
        return list(self.panels)

    @property
    def closed_names(self):
        return list(self.closed)

    # -- what a view does
    def remove(self, name):
        self.removed.append(name)
        panel = self.panels.pop(name, None)
        if panel is None:
            return False
        self.closed[name] = panel
        self._notify("removed", name)
        return True

    def reopen(self, name):
        self.reopened.append(name)
        panel = self.closed.pop(name, None)
        if panel is None:
            raise ValueError(name)
        self.panels[name] = panel
        self._notify("added", name)
        return panel

    def estop_all(self):
        self.estop_calls += 1
        self.latched = list(self.panels)
        if self.stop is not None:
            self.stop = dict(self.stop, latched=list(self.panels), every=True)
        return {name: True for name in self.panels}

    def clear_estop_all(self, confirmed=False):
        self.clear_calls += 1
        if self.clear_needs_confirm and not confirmed:
            return Result(Result.CONFIRM, reason="Release the latch?",
                          command="clear_estop_all")
        self.latched = []
        self.stop = None
        return Result(Result.OK)

    def set_input_focus(self, is_focused):
        self.focus_calls.append(is_focused)

    def close(self):
        self.is_closed = True

    def subscribe(self, fn):
        self._subscribers.append(fn)

    def unsubscribe(self, fn):
        if fn in self._subscribers:
            self._subscribers.remove(fn)

    def _notify(self, change, name):
        for fn in list(self._subscribers):
            fn(change, name)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def tk_harness(monkeypatch):
    """Point the view's toolkit names at the stand-ins, for this test only."""
    global SCHEDULER
    SCHEDULER = Scheduler()
    Focus.current = None
    FakePhotoImage.instances = []
    ALL_BINDINGS.clear()
    del PACK_ORDER[:]
    del GRABS[:]
    STYLE.clear()
    dialogs = FakeDialogs()
    monkeypatch.setattr(tkmod, "tk", FakeTkModule)
    monkeypatch.setattr(tkmod, "ttk", FakeTtkModule)
    monkeypatch.setattr(tkmod, "filedialog", dialogs)
    monkeypatch.setattr(tkmod, "messagebox", dialogs, raising=False)
    # The confirmation window is driven by its own tests below; everywhere
    # else a question is answered by `dialogs.confirm_answer`.
    def confirm(_master, prompt, **words):
        dialogs.words.append(words)
        return dialogs.askyesno(words.get("title", "Confirm"), prompt)

    monkeypatch.setattr(tkmod, "_confirm", confirm, raising=False)
    monkeypatch.delenv("STATION_NO_MOTION", raising=False)
    # Where the operator last dragged each log window, for the session (I1).
    monkeypatch.setattr(tkmod, "_LOG_POSITIONS", {}, raising=False)
    return dialogs


@pytest.fixture
def panel():
    return DemoPanel()


@pytest.fixture
def controller(panel):
    return FakeController(Demo=panel)


@pytest.fixture
def view(controller):
    built = tkmod.TkPanelView(FakeWidget(), controller, "Demo")
    yield built
    built.close()


def element_of(view, kind, text=None):
    for element in view._elements:
        if element["type"] == kind and (text is None or element.get("text") == text):
            return element
    raise AssertionError(f"no {kind} element {text!r}")


def widget_of(view, element):
    return view._widgets[id(element)]["widget"]


def click(view, element):
    widget_of(view, element).fire("<Button-1>")


def refusal(view):
    """The refusal the panel is showing, wherever it is drawn."""
    return getattr(view, "_notice_text", None) or view._status.cget("text") or ""


def last_call(controller, command):
    """The most recent call of one command. `_run` ends with a refresh, so the
    plot/image/log data commands are always the last entries."""
    for call in reversed(controller.calls):
        if call[1] == command:
            return call
    raise AssertionError(f"{command} was never called")


# ---------------------------------------------------------------------------
# rendering
# ---------------------------------------------------------------------------

def test_every_element_type_has_a_renderer():
    """`PanelView.__init__` refuses to build a view missing one; this says
    which one, instead of failing at the first schema that uses it."""
    missing = [t for t in sorted(sch.ELEMENT_TYPES)
               if not callable(getattr(tkmod.TkPanelView, f"_make_{t}", None))]
    assert not missing


def test_a_view_missing_a_renderer_cannot_be_built(controller):
    class Partial(tkmod.TkPanelView):
        _make_plot = None

    with pytest.raises(TypeError) as raised:
        Partial(FakeWidget(), controller, "Demo")
    assert "plot" in str(raised.value)


def test_every_element_type_builds(view, panel):
    kinds = {element["type"] for element in view._elements}
    assert kinds == sch.ELEMENT_TYPES - {"internal"} | {"internal"}
    for element in view._elements:
        if element["type"] == "internal":
            assert id(element) not in view._widgets, "internal renders nothing"
        else:
            assert widget_of(view, element) is not None


def test_readonly_shows_the_formatted_value(view, panel):
    panel.speed = 2.5
    view._refresh()
    element = element_of(view, "readonly")
    assert view._widgets[id(element)]["var"].get() == "2.50"


def test_indicator_and_toggle_take_their_colours_from_the_theme(view, panel):
    """Signature (2026-09-27): a toggle is a latching key. OFF it is a CAP-
    faced key (`theme.toggle_colors` now gives CAP, where the Bench sheet
    drew it on the ground) standing on its lip, its lamp slot hollow; ON it
    is down (the lip folded) in its on colour with the slot lit."""
    toggle = element_of(view, "toggle")
    panel.is_running = True
    view._refresh()
    widget = widget_of(view, toggle)
    entry = view._widgets[id(toggle)]
    expected = theme.toggle_colors(toggle, True)
    assert widget.cget("background") == expected["background"]
    assert widget.cget("foreground") == expected["foreground"]
    assert entry["ring"].is_down and entry["lamp_state"] == "lit"
    # Sentence case, as the Web view renders it: "RUNNING" is the schema's
    # word, "Running" is how every frontend shows it.
    assert widget.cget("text") == "Running"
    # A danger toggle is the theme's danger colour, not a literal red.
    assert expected["background"] == theme.colors("danger")[0]

    panel.is_running = False
    view._refresh()
    off = theme.toggle_colors(toggle, False)
    assert widget.cget("background") == off["background"] == theme.CAP
    assert widget.cget("text") == "Stopped"
    assert not entry["ring"].is_down and entry["lamp_state"] == "off"


def test_a_command_is_outlined_by_a_frame_so_aqua_draws_it(view, panel):
    """Aqua does not draw a Label's highlight ring, so an OFF toggle (its
    role outlined on the panel) rendered as bare text. The outline is a
    one-pixel frame around the label inside a one-pixel ring; keyboard
    focus turns BOTH ink - a 2 px ink ring, not a 1 px trace one (F25).
    Signature: the outline (the KEY_RIM rim) stands on a lip frame, which
    sits in a seat in the ground, inside the ring."""
    toggle = element_of(view, "toggle")
    panel.is_running = False
    view._refresh()
    entry = view._widgets[id(toggle)]
    outline, ring = entry["outline"], entry["ring"]
    assert widget_of(view, toggle).master is outline
    assert outline.master is ring.lip
    assert ring.lip.master is ring.seat and ring.seat.master is ring.outer
    assert ring.lip.cget("background") == theme.KEY_LIP
    resting = outline.cget("background")
    assert resting == entry["border"]
    assert resting not in (tkmod._page(), theme.TRACE), "an outline you can see"
    assert ring.outer.cget("background") == tkmod._page()
    widget_of(view, toggle).fire("<FocusIn>")
    assert outline.cget("background") == theme.TEXT
    assert ring.outer.cget("background") == theme.TEXT
    assert ring.outer.cget("padx") + outline.cget("padx") == 2
    widget_of(view, toggle).fire("<FocusOut>")
    assert outline.cget("background") == resting
    assert ring.outer.cget("background") == tkmod._page()


def test_a_danger_command_renders_neutral_like_qt_and_web(view):
    """One red (DS-9): only the stop object carries the signal colour. A
    danger command ("Go" here, Red Percent's "Stop run" in the app) was
    outlined in signal in Tk alone; it renders neutral, as in Qt and Web."""
    go = element_of(view, "button", "Go")
    entry = view._widgets[id(go)]
    # Signature: a non-`go` command is a CAP key with the KEY_RIM rim and
    # the KEY_LIP lip (it was outlined in the input border on its ground).
    assert widget_of(view, go).cget("background") == theme.CAP
    assert entry["outline"].cget("background") == theme.KEY_RIM
    assert entry["ring"].lip.cget("background") == theme.KEY_LIP
    assert theme.SIGNAL not in (widget_of(view, go).cget("background"),
                                entry["outline"].cget("background"))


def test_a_command_has_hover_and_disabled_states(view, panel):
    """Signature: under the pointer a CAP key's rim turns ink (its face
    keeps its tone); it used to step the face to the other ground."""
    go = element_of(view, "button", "Go")
    widget = widget_of(view, go)
    rim = view._widgets[id(go)]["outline"]
    resting = rim.cget("background")
    widget.fire("<Enter>")
    assert rim.cget("background") == theme.TEXT != resting
    widget.fire("<Leave>")
    assert rim.cget("background") == resting
    speed = element_of(view, "entry", "Speed")
    panel.mode = "running"
    view._refresh()
    assert widget_of(view, speed).cget("state") == "disabled"


def test_labels_are_sentence_case_without_a_colon(view):
    """The Web view's `sentenceCase`: the schema's "Speed now:" reads "Speed
    now", section titles come down to sentence case."""
    # Updated for E: the caption sits OVER its value in the cell, not in a
    # column beside it.
    caption = view._widgets[id(element_of(view, "readonly", "Speed now:"))]["caption"]
    assert caption.cget("text") == "Speed now"
    assert tkmod._label("Coordinate Frame") == "Coordinate frame"
    assert tkmod._label("X Position:") == "X position"
    assert tkmod._label("Target X Dist:") == "Target X dist"
    assert tkmod._label("FULL STOP") == "Full stop"
    assert tkmod._label("AUTONOMOUS MODE (Click to Stop)") == \
        "Autonomous mode (Click to Stop)"
    assert tkmod._sentence("DC Probe") == "DC Probe"


def test_plot_draws_a_polyline_without_matplotlib(view, panel):
    panel.samples = [1.0, 4.0, 2.0]
    view._refresh()
    canvas = widget_of(view, element_of(view, "plot"))
    lines = [item for item in canvas.items if item[0] == "line"]
    assert len(lines) == 1
    assert len(lines[0][1]) == 6      # three (x, y) pairs


def test_empty_plot_says_why(view, panel):
    panel.samples = []
    view._refresh()
    canvas = widget_of(view, element_of(view, "plot"))
    assert [item for item in canvas.items if item[0] == "text"]


def test_image_is_a_photoimage_from_base64_png(view, panel):
    view._refresh()
    assert FakePhotoImage.instances
    assert FakePhotoImage.instances[-1].data == base64.b64encode(panel.png).decode()
    # The reference is kept, or Tk drops the image the moment Python does.
    assert view._widgets[id(element_of(view, "image"))]["photo"] is not None


def test_an_image_is_not_re_rendered_on_every_tick(view, controller):
    """A model renders an image; a 100 ms tick must not ask ten times a
    second. A series is cheap and is not cached."""
    before = len([c for c in controller.calls if c[1] == "figure"])
    for _ in range(5):
        view._refresh()
    after = len([c for c in controller.calls if c[1] == "figure"])
    assert after == before
    assert len([c for c in controller.calls if c[1] == "series"]) >= 5


def test_log_stream_shows_the_model_lines(view, panel):
    view._refresh()
    text = widget_of(view, element_of(view, "log_stream"))
    assert text.body == "first\nsecond"


def test_dropdown_options_come_from_the_options_command(view):
    element = element_of(view, "dropdown")
    assert widget_of(view, element).cget("values") == ["alpha", "beta"]


def test_dropdown_selection_runs_the_command(view, panel):
    element = element_of(view, "dropdown")
    view._widgets[id(element)]["var"].set("beta")
    widget_of(view, element).fire("<<ComboboxSelected>>")
    assert panel.source == "beta"


def test_an_indicator_is_a_lamp_and_never_repeats_its_own_label(view, panel):
    """A lamp carries the state, not a second copy of the caption: the bench
    build showed `Fault   Fault` and said nothing about the fault. It is a
    round light: lit, filled (signal for a danger lamp); unlit, a ring."""
    element = element_of(view, "indicator")
    widget = widget_of(view, element)
    panel.is_latched = True
    view._refresh()
    ovals = [item for item in widget.items if item[0] == "oval"]
    assert ovals and ovals[-1][2]["fill"] == theme.SIGNAL     # on_role danger
    assert not [item for item in widget.items if item[0] == "text"]

    panel.is_latched = False
    view._refresh()
    ovals = [item for item in widget.items if item[0] == "oval"]
    assert ovals[-1][2]["fill"] != theme.SIGNAL
    # ON and OFF differ by more than fill: an unlit lamp is still a ring.
    assert ovals[-1][2]["outline"] == theme.MUTED


def test_an_empty_readout_says_so_instead_of_drawing_a_bare_stripe(view, panel):
    """An empty `info` readout rendered as a thin blue bar, which reads as a
    broken widget. Nothing to show is shown as nothing to show."""
    element = element_of(view, "readonly", "Fault:")
    panel.fault_reason = ""
    view._refresh()
    widget = widget_of(view, element)
    assert view._widgets[id(element)]["var"].get() == tkmod.EMPTY_READOUT
    assert widget.cget("background") == tkmod._page(), "not a coloured stripe"
    assert widget.cget("foreground") == theme.MUTED
    # Updated for E (status by exception): "--" is a quiet value, so a tier-1
    # status says nothing at all - its cell takes no place.
    assert view._widgets[id(element)]["is_quiet"] is True

    panel.fault_reason = "over temperature"
    view._refresh()
    assert view._widgets[id(element)]["var"].get() == "over temperature"
    assert widget.cget("text") == "over temperature"
    assert view._widgets[id(element)]["is_quiet"] is False
    # Updated for E: words are ink; trace is for a CHANGING number only.
    assert widget.cget("foreground") == theme.TEXT
    assert widget.cget("background") == tkmod._page()


def test_a_readout_and_an_entry_are_cells_with_their_caption_over_them(view):
    """Rewritten for the Bench sheet (E): a section's controls are cells -
    the caption over the control - flowing in one strip, not a caption
    column beside a value column."""
    for text, kind in (("Speed now:", "readonly"), ("Speed", "entry")):
        entry = view._widgets[id(element_of(view, kind, text))]
        box, strip = entry["box"], entry["strip"]
        assert box.master is strip and box in view._flows[id(strip)]["items"]
        assert entry["caption"].master is box
        assert entry["caption"].cget("foreground") == theme.MUTED


def test_a_long_readout_is_elided_and_its_whole_text_is_the_tooltip(
        view, panel, monkeypatch):
    """Nothing clips silently: a value too long for its cell ends in an
    ellipsis and the full text is on hover."""
    element = element_of(view, "readonly", "Fault:")
    widget = widget_of(view, element)
    monkeypatch.setattr(tkmod, "_text_width", lambda _font, text: 10 * len(text))
    # Updated for E: a sheet readout's room is the width of its flow.
    view._flow_strip(view._widgets[id(element)]["strip"], 300)
    panel.fault_reason = "identifying /dev/cu.debug-console (2 of 2)..."
    view._refresh()
    shown = widget.cget("text")
    assert shown.endswith(tkmod.ELLIPSIS) and len(shown) * 10 <= 300
    assert view._widgets[id(element)]["tooltip"].text == panel.fault_reason

    panel.fault_reason = "short"
    view._refresh()
    assert widget.cget("text") == "short"
    assert view._widgets[id(element)]["tooltip"].text == ""


def test_a_readout_is_not_drawn_like_a_box_to_type_in(view):
    """RC-6's cousin: an operator who cannot tell a readout from an entry
    tries to type into it. The readout is a number on the panel with no box;
    the entry is a dark field with a border."""
    readout = widget_of(view, element_of(view, "readonly", "Speed now:"))
    entry = widget_of(view, element_of(view, "entry", "Speed"))
    assert readout.cget("relief") == "flat"
    assert not readout.cget("highlightthickness")
    assert readout.cget("background") == tkmod._page()
    ring = view._widgets[id(element_of(view, "entry", "Speed"))]["ring"]
    # Updated for E: no box - a muted underline under a panel-toned well.
    assert ring.line.cget("background") == theme.INPUT_BORDER
    assert ring.inner.cget("background") == tkmod._page()
    assert entry.master is ring.inner
    assert entry.cget("background") == theme.WELL
    assert entry.cget("justify") == "right"
    # numbers first: the readout is one step larger than the entry's text
    assert abs(readout.cget("font")[1]) > abs(entry.cget("font")[1])


# ---------------------------------------------------------------------------
# a panel scrolls; the window does not grow
# ---------------------------------------------------------------------------

def test_the_controls_live_on_a_scrolling_canvas(view):
    """A panel taller than the window must scroll. When it grew the window
    instead, it pushed the global FULL STOP bar off the bottom of the
    screen."""
    assert view._body.master is view._canvas
    assert view._canvas.windows[view._body_window]["window"] is view._body
    assert view._scrollbar.cget("command") == view._canvas.yview
    assert view._canvas.cget("yscrollcommand") == view._scrollbar.set


def test_the_scrollregion_follows_the_controls(view):
    view._on_body_resized()
    assert view._canvas.cget("scrollregion") == view._canvas.bbox("all")


def test_the_body_is_kept_as_wide_as_the_viewport(view):
    """A canvas window is sized to its content, so without this a table stops
    at its widest row instead of reaching the window's edge."""
    view._on_canvas_resized(FakeEvent(width=800))
    assert view._canvas.windows[view._body_window]["width"] == 800


def test_the_wheel_scrolls_the_panel_under_the_pointer(view):
    view.frame.fire("<Enter>")
    assert set(tkmod.TkPanelView.WHEEL_EVENTS) <= set(ALL_BINDINGS)

    ALL_BINDINGS["<MouseWheel>"](FakeEvent(delta=120))     # Aqua / Win32
    assert view._canvas.scrolled == [(-1, "units")]
    ALL_BINDINGS["<Button-5>"](FakeEvent(num=5))           # X11
    assert view._canvas.scrolled[-1] == (1, "units")

    view.frame.fire("<Leave>")
    assert ALL_BINDINGS == {}, "two open panels must not scroll each other"


def test_a_closed_panel_lets_go_of_the_wheel(controller):
    """`bind_all` is application-wide: a destroyed panel still holding it
    would take wheel events to a dead widget."""
    built = tkmod.TkPanelView(FakeWidget(), controller, "Demo")
    built.frame.fire("<Enter>")
    assert ALL_BINDINGS
    built.close()
    assert ALL_BINDINGS == {}


# ---------------------------------------------------------------------------
# section layout: the row form is a table, the column form is a stack
# ---------------------------------------------------------------------------

class RowPanel(Panel):
    """Setup's shape: one row section per model, then a column section.

    `layout="row"` is the schema's hint (Addendum 2) and this is the panel
    that proves the renderer honours it.
    """

    NAME = "Rows"
    PARAMS = {"count": Param("count", "int", default=2, minimum=0, maximum=9,
                             label="Count")}

    def __init__(self):
        super().__init__()
        self.stepper_port = "SIM"
        self.stepper_gamepad = "None"
        self.stepper_found = ""
        self.dc_port = "Off"
        self.dc_gamepad = "None"
        self.dc_found = ""
        self.heater_port = "Off"
        self.heater_found = ""

    @property
    def schema(self):
        return sch.schema(
            sch.section(
                "Stepper Probe",
                sch.dropdown("Port", "stepper_port", "set_stepper_port",
                             "port_options"),
                sch.dropdown("Gamepad", "stepper_gamepad", "set_stepper_port",
                             "port_options"),
                sch.readonly("Detected:", "stepper_found"),
                layout="row"),
            sch.section(
                "DC Probe",
                sch.dropdown("Port", "dc_port", "set_dc_port", "port_options"),
                sch.dropdown("Gamepad", "dc_gamepad", "set_dc_port",
                             "port_options"),
                sch.readonly("Detected:", "dc_found"),
                layout="row"),
            # A heater takes no gamepad, so this row is one control shorter —
            # the case that decides whether the status column is a column.
            sch.section(
                "Temperature",
                sch.dropdown("Port", "heater_port", "set_heater_port",
                             "port_options"),
                sch.readonly("Detected:", "heater_found"),
                layout="row"),
            sch.section(
                "Launch",
                sch.entry("Count", "count", self.PARAMS["count"]),
                sch.button("Launch", "launch", role="go")),
        )

    def set_stepper_port(self, name):
        self.stepper_port = name
        return name

    def set_dc_port(self, name):
        self.dc_port = name
        return name

    def set_heater_port(self, name):
        self.heater_port = name
        return name

    def port_options(self):
        return ["Off", "SIM", "COM3"]

    def launch(self):
        return "launched"


@pytest.fixture
def row_view():
    built = tkmod.TkPanelView(FakeWidget(), FakeController(Rows=RowPanel()), "Rows")
    yield built
    built.close()


def cell_widget(view, element):
    """What a control is gridded by: an entry or a dropdown sits in its
    focus ring, and the ring is the cell."""
    entry = view._widgets[id(element)]
    return entry.get("cell") or entry["widget"]


def _cell(view, element):
    return cell_widget(view, element).grid_info


def _right_edge(view, element):
    info = _cell(view, element)
    return info["column"] + info.get("columnspan", 1)


#: `RowPanel._elements`, by name: three table rows then a column section.
STEPPER_PORT, STEPPER_PAD, STEPPER_FOUND = 0, 1, 2
DC_PORT, DC_PAD, DC_FOUND = 3, 4, 5
HEATER_PORT, HEATER_FOUND = 6, 7
COUNT, LAUNCH = 8, 9


def test_a_row_section_puts_its_elements_on_one_grid_row(row_view):
    cells = [_cell(row_view, row_view._elements[i])
             for i in (STEPPER_PORT, STEPPER_PAD, STEPPER_FOUND)]
    assert len({cell["row"] for cell in cells}) == 1
    columns = [cell["column"] for cell in cells]
    assert columns == sorted(columns) and len(set(columns)) == 3


def test_every_row_section_shares_one_grid_so_its_columns_line_up(row_view):
    """Six sibling frames each with their own grid is six rows that do not
    line up. One grid, one row per section, is a table."""
    port, found = row_view._elements[STEPPER_PORT], row_view._elements[STEPPER_FOUND]
    dc_port, dc_found = row_view._elements[DC_PORT], row_view._elements[DC_FOUND]
    assert cell_widget(row_view, port).master is cell_widget(row_view, dc_port).master
    assert _cell(row_view, dc_port)["column"] == _cell(row_view, port)["column"]
    assert _cell(row_view, dc_found)["column"] == _cell(row_view, found)["column"]
    # the next row, with the first row's refusal line between them (F10)
    assert _cell(row_view, dc_port)["row"] == _cell(row_view, port)["row"] + 2


def test_a_shorter_row_still_ends_its_status_in_the_status_column(row_view):
    """The heater row carries no gamepad dropdown. Its status goes in the
    Status column all the same, and its Gamepad cell is left empty."""
    heater_found = row_view._elements[HEATER_FOUND]
    assert _cell(row_view, heater_found)["column"] == _cell(
        row_view, row_view._elements[STEPPER_FOUND])["column"]


def test_a_table_says_each_caption_once_in_a_header_row(row_view):
    """Captions shared by the rows ("Port", "Gamepad", "Detected") are a
    header, once; the rows carry values only."""
    table = cell_widget(row_view, row_view._elements[STEPPER_PORT]).master
    texts = [child.cget("text") for child in table.children]
    for caption in ("Port", "Gamepad", "Detected"):
        assert texts.count(caption) == 1
    header = {child.cget("text"): child for child in table.children
              if child.cget("text") in ("Port", "Gamepad", "Detected")}
    first_row = _cell(row_view, row_view._elements[STEPPER_PORT])["row"]
    for caption, element in (("Port", STEPPER_PORT), ("Gamepad", STEPPER_PAD),
                             ("Detected", STEPPER_FOUND)):
        assert header[caption].grid_info["column"] == _cell(
            row_view, row_view._elements[element])["column"]
        assert header[caption].grid_info["row"] < first_row


def test_a_row_section_captions_its_row_in_the_first_column(row_view):
    captions = {label.cget("text"): label for label in row_view._section_titles}
    assert captions["Stepper Probe"].grid_info["column"] == 0
    assert captions["DC Probe"].grid_info["column"] == 0
    assert (captions["DC Probe"].grid_info["row"]
            == captions["Stepper Probe"].grid_info["row"] + 2)   # + refusal line


def test_the_status_of_a_row_takes_the_slack(row_view):
    """The status column absorbs the window's spare width, so a long status
    has room; it reads left to right, like the text it is."""
    found = row_view._elements[STEPPER_FOUND]
    widget = widget_of(row_view, found)
    assert _cell(row_view, found)["sticky"] == "ew"
    assert widget.cget("anchor") == "w"
    weights = {column: options.get("weight", 0)
               for column, options in widget.master.column_weights.items()}
    status = weights[_cell(row_view, found)["column"]]
    assert status >= 1 and all(status >= 50 * weight for column, weight in weights.items()
                               if column != _cell(row_view, found)["column"]), \
        "the status column takes nearly all of the slack"


def test_a_row_of_its_own_captions_is_a_bar_across_the_table():
    """A row section whose captions no other row shares (Setup's "Scan",
    "Selected") keeps them inline, in a bar spanning the table."""
    class BarPanel(RowPanel):
        @property
        def schema(self):
            base = RowPanel.schema.fget(self)
            bar = sch.section("Devices", sch.button("Refresh", "launch"),
                              sch.readonly("Scan:", "stepper_found"), layout="row")
            return sch.schema(bar, *base["sections"])

    built = tkmod.TkPanelView(FakeWidget(), FakeController(Rows=BarPanel()), "Rows")
    scan = element_of(built, "readonly", "Scan:")
    bar = widget_of(built, scan).master
    assert "Scan" in [child.cget("text") for child in bar.children]
    assert bar.grid_info["column"] == 1 and bar.grid_info["columnspan"] == 3
    built.close()


def test_a_column_section_flows_its_cells_in_one_strip(row_view):
    """Rewritten for E: a column section is a flow of cells - the entry's
    cell and the command share the section's strip, left to right."""
    entry, button = row_view._elements[COUNT], row_view._elements[LAUNCH]
    strip = row_view._widgets[id(entry)]["strip"]
    items = row_view._flows[id(strip)]["items"]
    assert row_view._widgets[id(entry)]["box"] in items
    assert row_view._widgets[id(button)]["ring"].outer in items


def test_consecutive_commands_share_one_line(view):
    """A section's run of commands is one line of buttons, not a stack of
    full-width bars (the Stepper Probe's System control)."""
    lines = {view._widgets[id(element_of(view, "button", text))]["ring"].outer.master
             for text in ("Go", "Refuse", "Ask")}
    assert len(lines) == 1


def test_cells_wrap_onto_more_lines_as_the_room_narrows(view):
    """Rewritten for E: the width decides how many LINES a section's flow of
    cells takes (the sheet decides the columns of entries)."""
    strip = view._widgets[id(element_of(view, "button", "Go"))]["ring"].outer.master
    flow = view._flows[id(strip)]
    for item in flow["items"]:
        item.winfo_reqwidth = lambda: 100
    view._flow_strip(strip, 2000)
    assert set(flow["layout"][0]) == {0}
    view._flow_strip(strip, 250)
    assert max(flow["layout"][0]) == (len(flow["items"]) - 1) // 2   # two a line


def test_a_column_section_ends_the_table(row_view):
    """A schema that goes row, row, column renders in that order, which it
    cannot do if the column section joins the table frame."""
    assert (widget_of(row_view, row_view._elements[COUNT]).master
            is not widget_of(row_view, row_view._elements[STEPPER_PORT]).master)


def test_dropdowns_in_a_table_share_one_width(row_view):
    widths = {widget_of(row_view, e).cget("width") for e in row_view._elements
              if e["type"] == "dropdown"}
    assert widths == {tkmod.DROPDOWN_WIDTH}


def test_a_dropdown_rereads_its_options_when_opened(row_view):
    """The ⟳ glyph beside every dropdown is gone; opening the list does it."""
    element = row_view._elements[STEPPER_PORT]
    post = widget_of(row_view, element).cget("postcommand")
    assert callable(post)
    post()
    assert widget_of(row_view, element).cget("values") == ["Off", "SIM", "COM3"]


# ---------------------------------------------------------------------------
# running commands
# ---------------------------------------------------------------------------

def test_every_entry_travels_with_a_command(view, panel, controller):
    """D-5: the typed value goes with the command, not one edit behind."""
    speed = element_of(view, "entry", "Speed")
    view._widgets[id(speed)]["var"].set("7.5")
    click(view, element_of(view, "button", "Go"))
    assert panel.commands == [("go", 7.5)]
    name, _command, inputs, _args = last_call(controller, "go")
    assert name == "Demo"
    assert inputs["speed"] == "7.5"


def _var(view, text):
    return view._widgets[id(element_of(view, "entry", text))]["var"]


def test_mod6_a_declared_input_travels_even_when_clean(view, panel, controller):
    """(a) "Go" declares speed: it goes with the command untouched."""
    view._refresh()
    click(view, element_of(view, "button", "Go"))
    _name, _command, inputs, _args = last_call(controller, "go")
    assert "speed" in inputs


def test_mod6_an_undeclared_clean_entry_does_not_travel(view, panel, controller):
    """(b) CON-8: Steps and Note are not Go's inputs and nobody typed in them."""
    view._refresh()
    click(view, element_of(view, "button", "Go"))
    _name, _command, inputs, _args = last_call(controller, "go")
    assert set(inputs) == {"speed"}


def test_mod6_an_undeclared_dirty_entry_travels(view, panel, controller):
    """(c) What the operator just typed is never lost: an edited box goes
    with any command, declared or not."""
    view._refresh()
    _var(view, "Note").set("typed a moment ago")
    click(view, element_of(view, "button", "Go"))
    _name, _command, inputs, _args = last_call(controller, "go")
    assert inputs.get("note") == "typed a moment ago"
    assert "steps" not in inputs


def test_mod6_a_focused_box_nobody_edited_does_not_travel(view, panel, controller):
    """Focus alone is not an edit: a box the operator clicked into and left
    unchanged may hold a value one refresh behind (refresh skips a focused
    box), and sending it would put that stale value back."""
    view._refresh()
    steps = element_of(view, "entry", "Steps")
    Focus.current = view._widgets[id(steps)]["widget"]
    try:
        click(view, element_of(view, "button", "Go"))
    finally:
        Focus.current = None
    _name, _command, inputs, _args = last_call(controller, "go")
    assert "steps" not in inputs


def test_refused_reaches_the_panel_and_not_a_popup(view, tk_harness):
    click(view, element_of(view, "button", "Refuse"))
    assert "the bench is busy" in refusal(view)
    assert tk_harness.errors == [], "a refusal is not a popup"


def test_a_later_success_clears_the_refusal(view):
    click(view, element_of(view, "button", "Refuse"))
    assert refusal(view)
    notice = view._notice
    click(view, element_of(view, "button", "Go"))
    assert refusal(view) == ""
    assert view._notice is None and notice.is_destroyed


def test_needs_confirm_asks_once_and_re_runs(view, panel, tk_harness):
    tk_harness.confirm_answer = True
    click(view, element_of(view, "button", "Ask"))
    assert panel.commands == [("ask_me", True)]
    assert ("confirm", "Really?") in tk_harness.asked


def test_declining_a_confirm_does_not_run_it(view, panel, tk_harness):
    tk_harness.confirm_answer = False
    click(view, element_of(view, "button", "Ask"))
    assert panel.commands == []


def test_toggle_sends_the_state_it_is_switching_to(view, panel, controller):
    click(view, element_of(view, "toggle"))
    assert panel.is_running is True
    assert last_call(controller, "set_running")[3] == (True,)
    click(view, element_of(view, "toggle"))
    assert panel.is_running is False
    assert last_call(controller, "set_running")[3] == (False,)


def test_entry_commit_validates_through_the_model(view, panel):
    speed = element_of(view, "entry", "Speed")
    view._widgets[id(speed)]["var"].set("99")     # above the declared maximum
    widget_of(view, speed).fire("<Return>")
    assert panel.speed != 99
    assert "at most 10" in refusal(view)

    view._widgets[id(speed)]["var"].set("3")
    widget_of(view, speed).fire("<Return>")
    assert panel.speed == 3.0


def test_keystroke_validation_follows_the_declared_type(view):
    speed = element_of(view, "entry", "Speed")
    note = element_of(view, "entry", "Note")
    assert view._validate_entry("", speed) is True        # a cleared box stays numeric
    assert view._validate_entry("-", speed) is True
    assert view._validate_entry("1.5", speed) is True
    assert view._validate_entry("1.5e", speed) is False
    assert view._validate_entry("abc", speed) is False
    assert view._validate_entry("inf", speed) is False
    # bounds do not block a keystroke: "50" has to pass through "5"
    assert view._validate_entry("0.5", speed) is True
    # a text field never gets a numeric validator at all
    assert widget_of(view, note).cget("validate") is None


def test_an_int_entry_refuses_a_decimal_point(view):
    """Addendum 2: an int accepts no decimal point — not even as a partial
    entry, because there is no integer a "." is on the way to."""
    steps = element_of(view, "entry", "Steps")
    for text in ("", "-", "+", "5", "-5", "42"):
        assert view._validate_entry(text, steps) is True, text
    for text in (".", "5.", "5.0", "-.", "0.5", "1e3", "abc"):
        assert view._validate_entry(text, steps) is False, text


def test_a_float_entry_still_takes_the_partial_forms(view):
    """The int rule must not cost the float fields their pass-throughs."""
    speed = element_of(view, "entry", "Speed")
    for text in ("", ".", "-.", "1.5", "0.5"):
        assert view._validate_entry(text, speed) is True, text


def test_refresh_never_writes_decimals_into_an_int_entry(view, panel):
    steps = element_of(view, "entry", "Steps")
    panel.steps = 5
    view._refresh()
    assert view._widgets[id(steps)]["var"].get() == "5"
    # `Param.format` is the first line of defence and renders a declared int
    # as one; the view is the backstop for a value that arrives as "5.000".
    render = panel.PARAMS["steps"].format
    assert render(5.0) == "5"
    assert view._display_text(steps, "5.000") == "5"
    assert view._display_text(element_of(view, "entry", "Speed"), "5.000") == "5.000"


def test_an_int_readout_with_no_param_behind_it_still_shows_an_int(view, panel):
    """`Panel._text_for` hands over `str(raw)` when the Param table has no
    entry for the attribute, so "7.0" reaches the view. It does not reach the
    operator."""
    panel.done_count = 7.0
    view._refresh()
    element = element_of(view, "readonly", "Done:")
    assert view._widgets[id(element)]["var"].get() == "7"


def test_out_of_range_text_is_flagged_without_blocking_typing(view):
    speed = element_of(view, "entry", "Speed")
    view._widgets[id(speed)]["var"].set("99")
    view._bounds_hint(speed)
    # Updated for E: a warning is ink, never a colour - the underline turns ink.
    assert view._widgets[id(speed)]["ring"].line.cget("background") == theme.TEXT
    view._widgets[id(speed)]["var"].set("5")
    view._bounds_hint(speed)
    assert view._widgets[id(speed)]["ring"].line.cget("background") == theme.INPUT_BORDER


# ---------------------------------------------------------------------------
# refresh, dirt and gating
# ---------------------------------------------------------------------------

def test_refresh_does_not_stomp_a_field_being_typed_into(view, panel):
    speed = element_of(view, "entry", "Speed")
    widget, var = widget_of(view, speed), view._widgets[id(speed)]["var"]
    Focus.current = widget
    var.set("4.4")
    view._refresh()
    assert var.get() == "4.4"

    Focus.current = None
    view._refresh()                    # still dirty: differs from last refresh
    assert var.get() == "4.4"


def test_refresh_updates_a_clean_field(view, panel):
    speed = element_of(view, "entry", "Speed")
    var = view._widgets[id(speed)]["var"]
    panel.speed = 6.0
    view._refresh()
    assert var.get() == "6.00"
    assert view._entry_is_dirty(speed) is False


def test_gating_disables_entries_and_commands(view, panel):
    """Entries are gated too, which neither desktop view did."""
    speed = element_of(view, "entry", "Speed")
    panel.set_running(True)
    view._refresh()
    assert widget_of(view, speed).cget("state") == "disabled"
    assert view._widgets[id(speed)]["is_enabled"] is False

    panel.set_running(False)
    view._refresh()
    assert widget_of(view, speed).cget("state") == "normal"


def test_a_disabled_control_does_not_run_when_clicked(view, panel, controller):
    go = element_of(view, "button", "Go")
    view._set_enabled(go, False)
    click(view, go)
    assert panel.commands == []
    assert not any(call[1] == "go" for call in controller.calls)


def test_stale_state_greys_the_panel_title(view):
    view._set_stale(True)
    assert view._title.cget("foreground") == theme.MUTED
    view._set_stale(False)
    assert view._title.cget("foreground") == theme.TEXT


def test_close_cancels_its_after_loop(controller):
    built = tkmod.TkPanelView(FakeWidget(), controller, "Demo")
    token = built._after_id
    assert token in SCHEDULER.pending
    built.close()
    assert token in SCHEDULER.cancelled
    assert built.frame.is_destroyed


def test_a_refresh_tick_for_a_removed_model_stops_quietly(controller):
    built = tkmod.TkPanelView(FakeWidget(), controller, "Demo")
    controller.panels.clear()          # the model went away mid-tick
    built._on_refresh_tick()
    assert built._after_id is None     # no further ticks scheduled
    built.close()


# ---------------------------------------------------------------------------
# composites: region, save, open
# ---------------------------------------------------------------------------

def _drag(picker, start, end):
    picker._on_canvas_press(FakeEvent(x=start[0], y=start[1],
                                      x_root=start[0], y_root=start[1]))
    picker._on_canvas_drag(FakeEvent(x=end[0], y=end[1],
                                     x_root=end[0], y_root=end[1]))
    picker._on_canvas_release(FakeEvent(x=end[0], y=end[1],
                                        x_root=end[0], y_root=end[1]))


def test_region_picker_returns_screen_coordinates():
    picker = tkmod._RegionPicker(FakeWidget())
    picker._build()
    _drag(picker, (100, 200), (400, 500))
    assert picker.region == (100, 200, 300, 300)
    assert picker.top.is_destroyed


def test_region_picker_normalises_a_backwards_drag():
    picker = tkmod._RegionPicker(FakeWidget())
    picker._build()
    _drag(picker, (400, 500), (100, 200))
    assert picker.region == (100, 200, 300, 300)


def test_a_drag_under_ten_pixels_is_reported_not_captured():
    picker = tkmod._RegionPicker(FakeWidget())
    picker._build()
    _drag(picker, (100, 100), (104, 103))
    assert picker.region is None
    assert "10" in picker.reason


def test_escape_cancels_the_region_picker():
    picker = tkmod._RegionPicker(FakeWidget())
    picker._build()
    picker.top.fire("<Escape>")
    assert picker.region is None
    assert "cancel" in picker.reason.lower()
    assert picker.top.is_destroyed


def test_region_select_runs_the_command_with_four_args(view, panel, monkeypatch):
    class Picked:
        reason = ""

        def __init__(self, _master):
            pass

        def pick(self, screenshot=None):
            return (10, 20, 30, 40)

    monkeypatch.setattr(tkmod, "_RegionPicker", Picked)
    click(view, element_of(view, "region_select"))
    assert panel.region == {"left": 10, "top": 20, "width": 30, "height": 40}


def test_a_cancelled_region_shows_the_reason_and_runs_nothing(view, panel,
                                                              monkeypatch):
    class Cancelled:
        def __init__(self, _master):
            self.reason = "Region selection cancelled."

        def pick(self, screenshot=None):
            return None

    monkeypatch.setattr(tkmod, "_RegionPicker", Cancelled)
    click(view, element_of(view, "region_select"))
    assert panel.region is None
    assert "cancel" in refusal(view).lower()


def test_the_captured_region_is_drawn_not_announced(view, panel, tk_harness):
    panel.region = {"left": 1, "top": 2, "width": 3, "height": 4}
    view._refresh()
    element = element_of(view, "region_select")
    assert view._widgets[id(element)]["var"].get() == sch.format_region(panel.region)
    assert tk_harness.errors == []


# -- the picker draws on a screenshot (bench 2026-09-27, bare X11) ---------
#
# "A white view covered the whole display during selection": on an X11
# session with no compositor `-alpha` is accepted and never honoured, so the
# 0.3-alpha overlay was an opaque sheet of `theme.BACKGROUND`. With a
# picture of the desktop the overlay is opaque on purpose and shows it.

def _desktop_png(width=800, height=450):
    """A synthetic two-colour desktop: red on the left, blue on the right."""
    import io
    from PIL import Image
    image = Image.new("RGB", (width, height), (0, 0, 255))
    image.paste((255, 0, 0), (0, 0, width // 2, height))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _screenshot(**bounds):
    shot = {"image": _desktop_png(), "left": 0, "top": 0,
            "width": 3200, "height": 1800}
    shot.update(bounds)
    return shot


def _items(picker, kind):
    return [item for item in picker.canvas.items if item[0] == kind]


def _alpha_requests(picker):
    return [args for args in picker.top.attrs if args and args[0] == "-alpha"]


from views import picking


def _under_pointer(bounds, desktop, overlay, pointer):
    """The capture pixel drawn under a pointer at logical desktop `pointer`,
    by the placement: overlay pixel -> picture pixel -> capture units."""
    scale, x, y = picking.picture_placement(bounds, desktop, overlay)
    return ((pointer[0] - overlay[0] - x) / scale,
            (pointer[1] - overlay[1] - y) / scale)


def _capture_pixel(bounds, desktop, pointer):
    """What the pointer names, in the screenshot's own pixels."""
    ratio = bounds[2] / desktop[2]
    return (pointer[0] * ratio - bounds[0], pointer[1] * ratio - bounds[1])


def test_placement_ratio_one_overlay_equal_to_the_desktop_is_unscaled_at_the_origin():
    desktop = (0, 0, 1920, 1080)
    assert picking.picture_placement(desktop, desktop, desktop) == (1.0, 0.0, 0.0)
    assert picking.drawn_size(desktop, 1.0) == (1920, 1080)


def test_placement_an_overlay_shrunk_by_a_panel_offsets_the_picture_not_squashes_it():
    """The bench (2026-09-28, Linux, Qt): the window manager kept the overlay
    out of a 40-px top panel. The picture stays desktop-sized and moves up
    40 px, so the desktop still lines up under the pointer."""
    bounds = desktop = (0, 0, 1920, 1080)
    overlay = (0, 40, 1920, 1040)
    scale, x, y = picking.picture_placement(bounds, desktop, overlay)
    assert (scale, x, y) == (1.0, 0.0, -40.0)
    assert picking.drawn_size(bounds, scale) == (1920, 1080)     # NOT 1920x1040
    for pointer in [(0, 40), (960, 540), (1919, 1079), (300, 700)]:
        assert _under_pointer(bounds, desktop, overlay, pointer) == \
            _capture_pixel(bounds, desktop, pointer)
    # A panel on the left is the same rule, sideways.
    assert picking.picture_placement(bounds, desktop, (48, 0, 1872, 1080)) == \
        (1.0, -48.0, 0.0)


def test_placement_ratio_two_halves_the_picture():
    """A Retina Mac: the capture is twice the logical desktop."""
    bounds = (0, 0, 5120, 2880)
    desktop = overlay = (0, 0, 2560, 1440)
    scale, x, y = picking.picture_placement(bounds, desktop, overlay)
    assert (scale, x, y) == (0.5, 0.0, 0.0)
    assert picking.drawn_size(bounds, scale) == (2560, 1440)
    for pointer in [(0, 0), (1280, 720), (2559, 1439)]:
        assert _under_pointer(bounds, desktop, overlay, pointer) == \
            _capture_pixel(bounds, desktop, pointer)


def test_placement_a_second_monitor_at_negative_x():
    bounds = desktop = (-1920, 0, 3840, 1080)
    # The overlay over the whole virtual desktop: the picture at its origin.
    assert picking.picture_placement(bounds, desktop, desktop) == (1.0, 0.0, 0.0)
    # The overlay kept to the primary monitor: the left monitor's half hangs
    # off the overlay to the left, and the primary's half is under it.
    overlay = (0, 0, 1920, 1080)
    assert picking.picture_placement(bounds, desktop, overlay) == (1.0, -1920.0, 0.0)
    for pointer in [(0, 0), (100, 500), (1919, 1079)]:
        assert _under_pointer(bounds, desktop, overlay, pointer) == \
            _capture_pixel(bounds, desktop, pointer)


def test_placement_takes_the_screenshot_dict_and_never_divides_by_zero():
    shot = {"image": b"x", "left": 0, "top": 0, "width": 3200, "height": 1800}
    assert picking.picture_placement(shot, (0, 0, 1600, 900), (0, 0, 1600, 900)) == \
        (0.5, 0.0, 0.0)
    # An unmeasured desktop is taken to be the bounds: unscaled, never stretched.
    assert picking.picture_placement(shot, (0, 0, 0, 0), (0, 0, 0, 0)) == (1.0, 0.0, 0.0)
    assert picking.picture_placement(None, None, None) == (1.0, 0.0, 0.0)
    assert picking.drawn_size({}, 1.0) == (1, 1)
    assert picking.drawn_size({"width": "bad", "height": None}, 1.0) == (1, 1)
    assert "ratio=2" in picking.placement_line(shot, (0, 0, 1600, 900), (0, 40, 1600, 860))


class _PlacedTop(FakeRoot):
    """The overlay as the window manager actually placed it."""
    box = (0, 40, 2560, 1400)

    def winfo_rootx(self):
        return self.box[0]

    def winfo_rooty(self):
        return self.box[1]

    def winfo_width(self):
        return self.box[2]

    def winfo_height(self):
        return self.box[3]


def test_an_overlay_the_window_manager_moved_moves_the_picture_and_keeps_its_size(monkeypatch):
    """Tk's half of the bench fix: on `<Configure>` the picture is re-anchored
    where the desktop's origin now falls, and it is never resized to the
    overlay."""
    monkeypatch.setattr(tkmod.tk, "Toplevel", _PlacedTop)
    picker = tkmod._RegionPicker(FakeWidget())
    picker._build(screenshot=_screenshot(width=2560, height=1440))
    import io
    from PIL import Image
    drawn = Image.open(io.BytesIO(base64.b64decode(picker.photo.data)))
    assert drawn.size == (2560, 1440)
    assert _items(picker, "image")[0][1] == (0.0, 0.0)
    picker.top.fire("<Configure>")
    assert ("coords", (0.0, -40.0), {}) in picker.canvas.items
    assert Image.open(io.BytesIO(base64.b64decode(picker.photo.data))).size == (2560, 1440)
    # A second Configure at the same place draws nothing new.
    moves = len(picker.canvas.items)
    picker.top.fire("<Configure>")
    assert len(picker.canvas.items) == moves


def test_the_picture_is_drawn_at_the_logical_desktop_size_not_the_overlays():
    """A capture 1.25x the logical virtual root (FakeWidget's 2560x1440) is
    drawn at 2560x1440; a downscaled capture (the old 1600-px picture) is
    brought back up to the desktop it was taken of."""
    picker = tkmod._RegionPicker(FakeWidget())
    picker._build(screenshot=_screenshot(image=_desktop_png(1600, 900),
                                         width=2560, height=1440))
    import io
    from PIL import Image
    drawn = Image.open(io.BytesIO(base64.b64decode(picker.photo.data)))
    assert drawn.size == (2560, 1440)


def test_with_a_screenshot_the_picker_shows_it_and_requests_no_alpha():
    picker = tkmod._RegionPicker(FakeWidget())
    picker._build(screenshot=_screenshot())
    assert _alpha_requests(picker) == []
    images = _items(picker, "image")
    assert len(images) == 1
    assert images[0][1] == (0, 0) and images[0][2]["image"] is picker.photo
    # The picture is the overlay's size (FakeWidget's virtual desktop), and
    # it is the desktop - dimmed, not replaced: the left half still reads red.
    import io
    from PIL import Image
    drawn = Image.open(io.BytesIO(base64.b64decode(picker.photo.data)))
    assert drawn.size == (2560, 1440)
    red, green, blue = drawn.convert("RGB").getpixel((10, 10))
    assert red > 150 and red > blue + 100 and red > green + 100
    red, green, blue = drawn.convert("RGB").getpixel((2550, 10))
    assert blue > 150 and blue > red + 100


def test_over_a_picture_the_instruction_line_is_drawn_on_the_canvas():
    picker = tkmod._RegionPicker(FakeWidget())
    picker._build(screenshot=_screenshot())
    texts = [item[2].get("text") for item in _items(picker, "text")]
    assert "Drag a box around the area to watch. Esc cancels." in texts
    (x, y), = [item[1] for item in _items(picker, "text")]
    assert x == 2560 // 2          # top centre of the overlay


def test_without_a_screenshot_the_alpha_overlay_says_it_could_not_picture():
    picker = tkmod._RegionPicker(FakeWidget())
    picker._build(screenshot=None)
    assert _alpha_requests(picker) == [("-alpha", 0.3)]
    assert _items(picker, "image") == []
    texts = " ".join(str(item[2].get("text")) for item in _items(picker, "text"))
    assert "Drag a box around the area to watch. Esc cancels." in texts
    assert "(the screen could not be pictured)" in texts


def test_a_screenshot_that_will_not_decode_falls_back_to_the_alpha_overlay():
    picker = tkmod._RegionPicker(FakeWidget())
    picker._build(screenshot=_screenshot(image=b"not a png"))
    assert _alpha_requests(picker) == [("-alpha", 0.3)]
    assert _items(picker, "image") == []
    assert picker.photo is None


def test_over_a_picture_the_drag_still_reports_screen_coordinates():
    picker = tkmod._RegionPicker(FakeWidget())
    picker._build(screenshot=_screenshot())
    _drag(picker, (400, 500), (100, 200))
    assert picker.region == (100, 200, 300, 300)
    assert picker.top.is_destroyed


def test_pick_hands_the_screenshot_to_the_overlay(monkeypatch):
    built = []
    picker = tkmod._RegionPicker(FakeWidget())
    original = picker._build
    monkeypatch.setattr(picker, "_build",
                        lambda screenshot=None: (built.append(screenshot),
                                                 original(screenshot=screenshot)))
    shot = _screenshot()
    picker.pick(screenshot=shot)
    assert built == [shot]


class ShotPanel(DemoPanel):
    """A region picker whose element declares the desktop picture, as Red
    Percent's does (`data_command="screen_image"`)."""

    def __init__(self, shot):
        super().__init__()
        self.shot = shot
        self.shots_taken = 0

    @property
    def schema(self):
        return sch.schema(sch.section(
            "Controls",
            sch.region_select("Pick area", "set_region", model_attr="region",
                              data_command="screen_image")))

    @property
    def screen_image(self):
        self.shots_taken += 1
        return self.shot


def _region_view(shot, monkeypatch):
    seen = []

    class Recording:
        reason = ""

        def __init__(self, _master):
            seen.append(("built", shot_panel.shots_taken))

        def pick(self, screenshot=None):
            seen.append(("picked", screenshot))
            return (10, 20, 30, 40)

    shot_panel = ShotPanel(shot)
    monkeypatch.setattr(tkmod, "_RegionPicker", Recording)
    built = tkmod.TkPanelView(FakeWidget(), FakeController(Demo=shot_panel), "Demo")
    return built, shot_panel, seen


def test_the_region_click_takes_the_picture_before_the_overlay_opens(monkeypatch):
    shot = _screenshot()
    built, shot_panel, seen = _region_view(shot, monkeypatch)
    try:
        click(built, element_of(built, "region_select"))
        assert seen == [("built", 1), ("picked", shot)]
        assert shot_panel.region == {"left": 10, "top": 20, "width": 30, "height": 40}
    finally:
        built.close()


def test_with_no_picture_the_region_click_still_opens_the_picker(monkeypatch):
    built, shot_panel, seen = _region_view(None, monkeypatch)
    try:
        click(built, element_of(built, "region_select"))
        assert seen[-1] == ("picked", None)
        assert shot_panel.region == {"left": 10, "top": 20, "width": 30, "height": 40}
    finally:
        built.close()


def test_a_region_select_with_no_data_command_takes_no_picture(view, panel,
                                                               controller,
                                                               monkeypatch):
    shots = []

    class Recording:
        reason = ""

        def __init__(self, _master):
            pass

        def pick(self, screenshot=None):
            shots.append(screenshot)
            return (1, 2, 30, 40)

    monkeypatch.setattr(tkmod, "_RegionPicker", Recording)
    before = len(controller.calls)
    click(view, element_of(view, "region_select"))
    assert shots == [None]
    assert [c[1] for c in controller.calls[before:]][0] == "set_region"


_REAL_PICKER = r'''
import base64, io, json, os, sys
sys.path.insert(0, os.path.join(sys.argv[1], "src"))
import tkinter as tk
from PIL import Image
import views.tk as tkv
try:
    root = tk.Tk()
except Exception as exc:        # no display
    print(json.dumps({"skip": repr(exc)})); sys.exit(0)
root.withdraw()
requested = []
original = tk.Toplevel.attributes
def recording(self, *args):
    requested.append([str(a) for a in args])
    return original(self, *args)
tk.Toplevel.attributes = recording
image = Image.new("RGB", (800, 450), (0, 0, 255))
image.paste((255, 0, 0), (0, 0, 400, 450))
buffer = io.BytesIO(); image.save(buffer, format="PNG")
shot = {"image": buffer.getvalue(), "left": 0, "top": 0, "width": 800, "height": 450}
picker = tkv._RegionPicker(root)
picker._build(screenshot=shot)
root.update_idletasks()
kinds = [picker.canvas.type(item) for item in picker.canvas.find_all()]
alpha = [args for args in requested if args and args[0] == "-alpha"]
picker._finish(); root.update(); root.destroy()
print(json.dumps({"kinds": kinds, "alpha": alpha}))
'''


@pytest.mark.window
def test_a_real_picker_over_a_screenshot_holds_an_image_and_no_alpha():
    """P3: the real toolkit, in a child process - the picture is a canvas
    image item and `-alpha` is never requested of the overlay."""
    import json
    import subprocess
    import sys
    tree = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(
        tkmod.__file__))))
    done = subprocess.run([sys.executable, "-c", _REAL_PICKER, tree,
                           "-ApplePersistenceIgnoreState", "YES"],
                          capture_output=True, text=True, timeout=120)
    lines = [line for line in done.stdout.splitlines() if line.startswith("{")]
    assert done.returncode == 0 and lines, done.stderr[-2000:]
    result = json.loads(lines[-1])
    if "skip" in result:
        pytest.skip(f"no display for a real Tk picker: {result['skip']}")
    assert "image" in result["kinds"], result
    assert result["alpha"] == [], result


def test_file_save_copies_the_written_file_to_the_destination(view, panel,
                                                              tk_harness, tmp_path):
    destination = tmp_path / "copy.csv"
    tk_harness.save_path = str(destination)
    click(view, element_of(view, "file_save"))
    assert panel.saved_path is not None
    assert destination.read_text() == "n,red\n0,1\n"
    os.unlink(panel.saved_path)


def test_cancelling_the_save_dialog_runs_nothing(view, panel, tk_harness):
    tk_harness.save_path = ""
    click(view, element_of(view, "file_save"))
    assert panel.saved_path is None


def test_file_open_passes_the_chosen_path(view, panel, tk_harness):
    tk_harness.open_path = "/tmp/run.csv"
    click(view, element_of(view, "file_open"))
    assert panel.loaded_path == "/tmp/run.csv"


# ---------------------------------------------------------------------------
# styling
# ---------------------------------------------------------------------------

def _executable_source(path):
    """The module's code with its prose removed.

    Docstrings and comments quote the old hardcoded `('Arial', 10, 'bold')` at
    length — the same trap `legacy/src/` sets, where a grep hit for a defect is prose
    about its removal. Only what runs is scanned.
    """
    source = open(path).read()
    lines = source.splitlines()
    for node in ast.walk(ast.parse(source)):
        body = getattr(node, "body", None)
        for child in body if isinstance(body, list) else []:
            if (isinstance(child, ast.Expr)
                    and isinstance(child.value, ast.Constant)
                    and isinstance(child.value.value, str)):
                for index in range(child.lineno - 1, child.end_lineno):
                    lines[index] = ""
    return "\n".join(re.sub(r"\s#.*$", "", line) for line in lines
                     if not line.strip().startswith("#"))


def test_no_colour_or_font_literal_in_the_module():
    """Every colour and font comes from `views.theme`."""
    code = _executable_source(tkmod.__file__)
    assert not re.findall(r"#[0-9a-fA-F]{6}\b", code)
    named = r"""['"](?:black|white|red|green|darkred|darkgreen|darkblue|"""\
            r"""darkorange|gray\d*|lightgreen|Arial|Helvetica|Courier)['"]"""
    assert not re.findall(named, code), "a colour or font is named here"
    # No bare font tuple: ('Arial', 10, 'bold') and friends.
    assert not re.findall(r"\(\s*['\"][A-Za-z ]+['\"]\s*,\s*\d+", code)


def test_the_theme_is_the_only_palette(view):
    """Change the theme and the widgets change with it."""
    for element in view._elements:
        widget = view._widgets.get(id(element), {}).get("widget")
        background = widget.cget("background") if widget is not None else None
        if background is not None:
            assert (background in {theme.BACKGROUND, theme.SURFACE,
                                   theme.DISABLED[0]}
                    or background in {colour for colour, _ in theme.ROLES.values()})


# ---------------------------------------------------------------------------
# the dashboard
# ---------------------------------------------------------------------------

@pytest.fixture
def setup_panel():
    return DemoPanel()


@pytest.fixture
def dashboard(controller, setup_panel):
    built = tkmod.TkDashboard(controller, setup_panel)
    yield built
    if not built._closing:
        built.close()


def test_open_shows_the_setup_panel_first(dashboard, setup_panel):
    dashboard.open()
    assert list(dashboard._panels)[0] == dashboard.SETUP_TAB
    setup_view = dashboard._panels[dashboard.SETUP_TAB]
    assert setup_view._panel is setup_panel
    assert isinstance(setup_view, tkmod.TkPanelView)
    assert dashboard.root.did_mainloop == 1


def test_open_gives_every_already_open_model_a_tab(dashboard):
    dashboard.open()
    assert "Demo" in dashboard._panels


def test_a_model_added_later_gets_a_tab(dashboard, controller, panel):
    dashboard.open()
    controller.remove("Demo")
    SCHEDULER.pump()
    assert "Demo" not in dashboard._panels
    controller.reopen("Demo")
    SCHEDULER.pump()
    assert "Demo" in dashboard._panels


def test_closing_a_model_from_the_rail_removes_it(dashboard, controller):
    """Updated for E: a model is a line in the rail, not a tab; the middle
    button on its line is the tab-close gesture, and it destructs."""
    dashboard.open()
    _ring, label = dashboard._rail_items["Demo"]
    label.fire(tkmod._close_tab_button(label))
    assert controller.removed == ["Demo"]
    SCHEDULER.pump()
    assert "Demo" not in dashboard._panels
    assert "Demo" not in dashboard._rail_items


def test_a_close_gesture_on_a_page_closes_nothing(dashboard, controller,
                                                  monkeypatch):
    """Updated for E: the notebook holds two pages, Setup and the sheet;
    neither is a model, so a close click on either closes nothing."""
    dashboard.open()
    for index in (0, 1):
        monkeypatch.setattr(dashboard.notebook, "index", lambda _spec, i=index: i)
        dashboard.notebook._on_middle_press(FakeEvent(x=5, y=5))
    assert controller.removed == []


def test_the_setup_tab_cannot_be_closed(dashboard, controller):
    dashboard.open()
    dashboard._on_tab_close(0)          # index 0 is Setup
    assert controller.removed == []


# ---------------------------------------------------------------------------
# Setup minimises when the system launches, and comes back
# ---------------------------------------------------------------------------

def _launch_a_model(dashboard, controller):
    """The event `Dashboard` collapses Setup on: a model added to a running
    controller. `open()` adds the already-open ones directly, which is not a
    launch."""
    controller.remove("Demo")
    SCHEDULER.pump()
    controller.reopen("Demo")
    SCHEDULER.pump()


def _setup_tab_is_shown(dashboard):
    """Asked of the notebook's own bookkeeping, not of `tabs()`.

    A hidden ttk tab stays *managed* — real `Notebook.tabs()` still lists it,
    which is exactly what lets `add` put it back in place, and is why the
    collapse hides rather than forgets. The stand-in models the same two
    sets, so this reads the same answer the real widget would give.
    """
    frame = dashboard._frames[dashboard.SETUP_TAB]
    return (frame in dashboard.notebook._tabs
            and frame not in dashboard.notebook._hidden)


def test_the_setup_tab_minimises_when_the_first_model_launches(dashboard,
                                                               controller):
    dashboard.open()
    assert _setup_tab_is_shown(dashboard)
    _launch_a_model(dashboard, controller)
    assert dashboard._is_setup_collapsed is True
    assert not _setup_tab_is_shown(dashboard)
    # Minimised, not destroyed: the panel and its widgets are still there.
    assert dashboard.SETUP_TAB in dashboard._panels


def test_the_rail_always_offers_setup_filled_while_it_is_shown(dashboard,
                                                               controller):
    """Updated for E: Setup sits at the foot of the rail on every page; it
    is ink-filled while its page is shown and outlined while minimised."""
    dashboard.open()
    assert dashboard._setup_press.frame.is_packed is True
    assert dashboard._setup_press.is_active is True
    _launch_a_model(dashboard, controller)
    assert dashboard._setup_press.frame.is_packed is True
    assert dashboard._setup_press.is_active is False

    dashboard._setup_button.fire("<Button-1>")
    assert dashboard._is_setup_collapsed is False
    assert _setup_tab_is_shown(dashboard)
    assert dashboard._setup_press.is_active is True


def test_the_menu_bar_brings_setup_back(dashboard, controller):
    dashboard.open()
    _launch_a_model(dashboard, controller)
    entries = [e for e in dashboard._setup_menu.entries if e["label"] == "Show Setup"]
    assert entries, "no way back to Setup on the menu bar"
    entries[0]["command"]()
    assert _setup_tab_is_shown(dashboard)


def test_a_restored_setup_panel_still_runs_its_commands(dashboard, controller,
                                                        setup_panel):
    """Re-usable, not just re-showable: Refresh and Launch have to work after
    the collapse, which they cannot if the panel was torn down."""
    dashboard.open()
    _launch_a_model(dashboard, controller)
    assert dashboard.restore_setup() is True
    view = dashboard._panels[dashboard.SETUP_TAB]
    click(view, element_of(view, "button", "Go"))
    assert setup_panel.commands == [("go", setup_panel.speed)]


def test_setup_can_be_minimised_and_restored_more_than_once(dashboard,
                                                            controller):
    dashboard.open()
    _launch_a_model(dashboard, controller)
    for _ in range(2):
        assert dashboard.restore_setup() is True
        assert _setup_tab_is_shown(dashboard)
        dashboard._collapse_setup()               # as a fresh launch would
        assert not _setup_tab_is_shown(dashboard)


def test_restoring_setup_twice_does_not_add_a_second_tab(dashboard, controller):
    dashboard.open()
    _launch_a_model(dashboard, controller)
    dashboard.restore_setup()
    before = list(dashboard.notebook._tabs)
    dashboard.restore_setup()
    assert list(dashboard.notebook._tabs) == before


def test_the_setup_tab_is_not_collapsed_twice(dashboard, controller):
    """The second model to launch must not hide something else: `_collapse_setup`
    is called once by the base and is idempotent in any case."""
    dashboard.open()
    _launch_a_model(dashboard, controller)
    hidden = set(dashboard.notebook._hidden)
    dashboard._collapse_setup()
    assert set(dashboard.notebook._hidden) == hidden


def test_the_models_menu_lists_closed_models_to_reopen(dashboard, controller):
    dashboard.open()
    controller.remove("Demo")
    SCHEDULER.pump()
    entries = {entry["label"]: entry for entry in _menu_entries(dashboard)}
    assert "Demo" in entries
    assert entries["Demo"]["variable"].get() is False
    entries["Demo"]["variable"].set(True)
    entries["Demo"]["command"]()
    assert controller.reopened == ["Demo"]


def _menu_entries(dashboard):
    for name, variable in dashboard._menu_vars.items():
        yield {"label": name, "variable": variable,
               "command": lambda n=name: dashboard._on_model_toggled(n)}


def test_unticking_a_model_closes_it(dashboard, controller):
    dashboard.open()
    dashboard._menu_vars["Demo"].set(False)
    dashboard._on_model_toggled("Demo")
    assert controller.removed == ["Demo"]


def test_the_stop_and_the_event_tray_cannot_be_pushed_off_the_window(
        controller, setup_panel, monkeypatch):
    """SAFETY. Pack order is allocation order: the notebook is the one widget
    that expands, so everything it must never push off the window is packed
    before it. A FULL STOP the operator cannot reach is not a stop.
    Updated for E: the stop is A's disc at the top of the left rail."""
    monkeypatch.setattr(tkmod.ClosableNotebook, "pack",
                        lambda self, **kwargs: PACK_ORDER.append((self, kwargs)),
                        raising=False)
    built = tkmod.TkDashboard(controller, setup_panel)
    packed = [widget for widget, _ in PACK_ORDER]
    notebook = packed.index(built.notebook)
    # The rail - the stop's home - is docked left before the main column.
    rail = packed.index(built._rail)
    assert PACK_ORDER[rail][1]["side"] == "left"
    assert rail < packed.index(built._main) < notebook
    # In the rail the disc is packed before the model list, which gives way.
    # Signature: the disc sits on the nameplate, which is the rail's first
    # child and is packed before the list.
    assert built._stop_button.master is built._plate
    assert built._plate.master.master is built._rail
    assert packed.index(built._stop_button) < packed.index(built._model_list)
    assert packed.index(built._plate_edge) < packed.index(built._model_list)
    # The tray is docked at the bottom of the main column before the notebook.
    assert packed.index(built._tray) < notebook
    assert PACK_ORDER[packed.index(built._tray)][1]["side"] == "bottom"
    built.close()


def test_the_stop_button_label_follows_the_controller(dashboard, controller):
    """The Web view's mushroom, in Tk: `Stop`, then `Clear` once latched; the
    key stays signal red (it is never dimmed).
    Signature (2026-09-27): the old red ring that thickened is gone. Idle,
    the collar is ink with a KEY_RIM edge around a SURFACE socket band;
    latched, the socket floods SKIRT and the collar turns SIGNAL."""
    def faces():
        return [item[2]["text"] for item in dashboard._stop_button.items
                if item[0] == "text"]

    def part(tag):
        return [item[2] for item in dashboard._stop_button.items
                if tag in (item[2].get("tags") or ())][-1]

    dashboard.open()
    dashboard._sync_stop_button()
    assert faces() == ["Stop"]
    assert part("face")["fill"] == theme.SIGNAL
    assert part("collar")["fill"] == theme.STOP["collar_fill"] == theme.TEXT
    assert part("collar")["outline"] == theme.STOP["collar_edge"]
    assert part("socket")["fill"] == theme.STOP["socket"] == theme.SURFACE
    assert dashboard.chord_text == "Stop: Ctrl+."
    assert dashboard._stop.tooltip.text.startswith(tkmod.STOP_HINT)

    controller.is_estopped = True
    dashboard._sync_stop_button()
    assert faces() == ["Clear"]
    assert part("face")["fill"] == theme.SIGNAL
    assert part("collar")["fill"] == theme.STOP["collar_latched"] == theme.SIGNAL
    assert part("socket")["fill"] == theme.STOP["socket_latched"] == theme.SKIRT
    assert dashboard._stop.tooltip.text == tkmod.CLEAR_HINT


def _stop_parts(canvas, tag):
    return [item for item in canvas.items if tag in (item[2].get("tags") or ())]


def test_signature_the_stop_idle_stands_on_its_skirt(controller, setup_panel):
    """Signature: idle, the key stands `lift` px above centre with its SKIRT
    oval showing `skirt` px below the face, and the face reads "Stop" with
    no glyph."""
    disc = tkmod._Mushroom(FakeWidget(), lambda: None, background=theme.CAP)
    disc.set_latched(False)
    centre, _radius, _socket, key, face_y = disc.geometry()
    assert face_y == centre - tkmod._design_px(theme.STOP["lift"])
    (face,) = _stop_parts(disc.canvas, "face")
    (skirt,) = _stop_parts(disc.canvas, "skirt")
    assert skirt[2]["fill"] == theme.STOP["skirt_fill"] == theme.SKIRT
    assert skirt[1][1] - face[1][1] == tkmod._design_px(theme.STOP["skirt"])
    assert face[1][2] - face[1][0] == pytest.approx(2 * key)
    assert not _stop_parts(disc.canvas, "release")
    assert not _stop_parts(disc.canvas, "crescent")


def test_signature_the_stop_latched_drops_floods_and_shows_the_release(
        controller, setup_panel):
    """Signature: latched, the key is down `drop_latched` px with no skirt,
    a SKIRT crescent across the top of its face, and the release glyph over
    "Clear"; the socket floods SKIRT, the collar turns SIGNAL."""
    disc = tkmod._Mushroom(FakeWidget(), lambda: None, background=theme.CAP)
    disc.set_latched(False)
    idle_y = disc.geometry()[4]
    disc.set_latched(True)
    SCHEDULER.pending.clear()           # the pulse is not under test here
    centre, _radius, _socket, key, face_y = disc.geometry()
    assert face_y - idle_y == tkmod._design_px(theme.STOP["drop_latched"])
    assert not _stop_parts(disc.canvas, "skirt")
    (crescent,) = _stop_parts(disc.canvas, "crescent")
    (face,) = _stop_parts(disc.canvas, "face")
    assert crescent[2]["fill"] == theme.SKIRT
    assert face[1][1] > crescent[1][1] and face[1][3] == crescent[1][3]
    release = _stop_parts(disc.canvas, "release")
    assert release and all(item[0] == "line" and item[2]["fill"] == theme.WHITE
                           for item in release)
    (legend,) = _stop_parts(disc.canvas, "legend")
    assert legend[2]["text"] == "Clear"
    assert max(p for item in release for p in item[1][1::2]) < legend[1][1]
    (collar,) = _stop_parts(disc.canvas, "collar")
    (socket,) = _stop_parts(disc.canvas, "socket")
    assert collar[2]["fill"] == theme.SIGNAL and socket[2]["fill"] == theme.SKIRT


def test_the_stop_pulses_once_on_the_edge_and_not_while_latched(dashboard,
                                                                controller):
    dashboard.open()
    dashboard._sync_stop_button()
    assert not dashboard._stop._pulse_ids
    controller.is_estopped = True
    dashboard._sync_stop_button()
    assert len(dashboard._stop._pulse_ids) == len(tkmod.PULSE_FRAMES)
    pulses = list(dashboard._stop._pulse_ids)
    dashboard._sync_stop_button()           # still latched: no second breath
    assert dashboard._stop._pulse_ids == pulses
    controller.is_estopped = False
    dashboard._sync_stop_button()
    assert not dashboard._stop._pulse_ids


def test_the_stop_answers_the_keyboard(dashboard, controller):
    dashboard.open()
    dashboard._stop_button.fire("<Return>")
    assert controller.estop_calls == 1


def test_a_models_own_stop_is_a_small_switch(controller, monkeypatch):
    """Rewritten for E: a Safety section's stop toggle (`is_estopped`) is a
    small switch (tier 3), not a second disc; red only while latched."""
    class Safe(DemoPanel):
        def __init__(self):
            super().__init__()
            self.is_estopped = False

        @property
        def schema(self):
            return sch.schema(sch.section("Safety", sch.toggle(
                "FULL STOP", "is_estopped", "set_running", "LATCHED",
                "FULL STOP", on_role="danger", off_role="danger")))

    safe = Safe()
    own = FakeController(Safe=safe)
    built = tkmod.TkPanelView(FakeWidget(), own, "Safe")
    element = built._elements[0]
    entry = built._widgets[id(element)]
    switch = entry["switch"]
    assert "mushroom" not in entry
    # Signature: the track is a sunk rectangle with a KEY_RIM edge and the
    # knob a key cap (a face on its lip); they were ovals.
    def part(tag):
        return [item[2] for item in switch.canvas.items
                if tag in (item[2].get("tags") or ())][-1]

    fills = {item[2].get("fill") for item in switch.canvas.items}
    assert theme.SIGNAL not in fills, "off: no red"
    assert part("track")["outline"] == theme.SWITCH["off_edge"] == theme.KEY_RIM
    assert part("knob")["fill"] == theme.SWITCH["knob_off"]
    assert part("knob-lip")["fill"] == theme.SWITCH["knob_lip"]
    # Updated for O16 (PM8-8): the switch's words are the view's, one word
    # per thing, whatever the schema's state words ("FULL STOP"/"LATCHED").
    assert entry["words"].cget("text") == "Stop this model"
    safe.is_estopped = True
    built._refresh()
    assert part("track")["fill"] == theme.SWITCH["on_fill"]
    assert part("knob")["fill"] == theme.SWITCH["knob_on"]
    assert part("knob-lip")["fill"] == theme.SWITCH["knob_lip_on"]
    assert part("knob")["outline"] == theme.SKIRT
    assert entry["words"].cget("text") == "Stopped"
    switch.canvas.fire("<Button-1>")
    assert [call[1] for call in own.calls if call[1] == "set_running"], "a press runs it"
    built.close()


def test_the_stop_button_toggles_the_global_estop(dashboard, controller,
                                                  tk_harness):
    dashboard.open()
    dashboard._stop_button.fire("<Button-1>")
    assert controller.estop_calls == 1
    assert controller.is_estopped is True

    dashboard._stop_button.fire("<Button-1>")
    assert controller.clear_calls == 1
    assert controller.is_estopped is False


def test_clearing_the_latch_asks_first(dashboard, controller, tk_harness):
    dashboard.open()
    controller.is_estopped = True
    controller.clear_needs_confirm = True
    tk_harness.confirm_answer = True
    dashboard._stop_button.fire("<Button-1>")
    assert controller.clear_calls == 2       # the ask, then the confirmed run
    assert controller.is_estopped is False


def test_events_reach_the_log_panel_coloured_by_severity(dashboard):
    """Two steps of ink and never fainter than muted: the newest line is ink,
    older info lines muted, warnings trace. An error line is INK behind a
    signal mark and the word "Error" (F14): signal text is 3.21:1 on the
    log, and severity was colour alone."""
    dashboard.open()
    for severity in ("info", "warning", "error"):
        dashboard._show_event(_event(severity, needs_ack=False))
    # Updated for E (status by exception): info is not drawn in the tray.
    # Updated for L2: the history is redrawn whole (a line can leave it), so
    # the LAST drawing is the one that counts: only the newest is `latest`.
    assert dashboard._event_text.written_tags[-4:] == [
        ("mark-warning",), ("warning",),
        ("mark-error",), ("error", "latest")]
    tags = dashboard._event_text.tags
    for severity in ("info", "warning", "error"):
        assert tags[severity]["foreground"] == theme.SEVERITY_INK[severity]
        assert tags[f"mark-{severity}"]["foreground"] == theme.SEVERITY_MARK[severity]
    assert tags["error"]["foreground"] == theme.TEXT
    assert tags["latest"]["foreground"] == theme.TEXT
    body = dashboard._event_text.body
    # Updated for L11: the severity word, the title in sentence case, then
    # the message; no bracketed source.
    # Updated for ARCH-3: the line after the severity word is
    # `views.base.event_line` ("Title: message"), as Qt words it.
    assert "Error: Error-event: something happened" in body and "Warning: " in body
    assert "[Demo]" not in body
    assert "Info" not in body
    # A warning's mark is a hollow square, an error's a solid one.
    assert "\u25a1 " in body and "\u25a0 " in body
    assert dashboard._latest_text.cget("text").startswith("Error")
    # and it is created after `info`, before `warning`/`error` - priority.
    assert list(tags).index("info") < list(tags).index("latest") \
        < list(tags).index("warning")


def test_only_a_needs_ack_event_reaches_the_alert_band(dashboard, tk_harness):
    dashboard.open()
    dashboard._on_event(_event("warning", needs_ack=False))
    dashboard._on_event(_event("error", needs_ack=True))
    SCHEDULER.pump()
    assert [event.title for event in dashboard._alerts] == ["error-event"]
    assert tk_harness.errors == [], "never an application-modal showerror"


def test_an_event_is_marshalled_onto_the_tk_thread(dashboard):
    dashboard.open()
    before = dashboard._event_text.body
    # Updated for E: a warning (info is not drawn in the tray).
    dashboard._on_event(_event("warning", needs_ack=False))
    assert dashboard._event_text.body == before, "drawn straight off the thread"
    SCHEDULER.pump()
    assert dashboard._event_text.body != before


def test_no_popup_once_close_has_begun(dashboard, tk_harness):
    """A modal raised from inside a close path blocks the exit."""
    dashboard.open()
    dashboard.close()
    dashboard._on_event(_event("error", needs_ack=True))
    SCHEDULER.pump()
    assert tk_harness.errors == []
    assert getattr(dashboard, "_alerts", []) == []


def test_close_unsubscribes_and_closes_the_controller(dashboard, controller):
    dashboard.open()
    dashboard.close()
    assert controller.is_closed is True
    assert controller._subscribers == []
    assert dashboard.root.is_destroyed


def test_close_is_idempotent(dashboard, controller):
    dashboard.open()
    dashboard.close()
    dashboard.close()
    assert dashboard.root.did_quit == 1


def test_the_window_close_button_closes_the_app(dashboard, controller):
    dashboard.open()
    dashboard.root.protocols["WM_DELETE_WINDOW"]()
    assert controller.is_closed is True


def test_the_os_quit_command_goes_through_the_close_path(dashboard, controller):
    """G5 keeps this one hook, because the toolkit forces it: Tk on Aqua puts
    a Quit item (and its OS-owned shortcut) in the application menu of every
    Tk app, and with no `::tk::mac::Quit` it calls `Tcl_Exit`, which ends the
    process past `close()` AND past Python's atexit - no stop, no port
    closed (proved on this Mac, Tk 9.0.4: a Quit Apple event to a Tk app
    without the hook exited 0 with neither `mainloop` returning nor atexit
    running; with it, the hook ran and atexit ran). The view adds no
    shortcut or menu item of its own for it; on X11 and Win32 the command
    is never called."""
    dashboard.open()
    dashboard.root.commands["::tk::mac::Quit"]()
    assert controller.is_closed is True


def test_focus_gates_input_and_never_stops(dashboard, controller):
    dashboard.open()
    Focus.current = None
    dashboard.root.fire("<FocusOut>", FakeEvent(widget=dashboard.root))
    assert controller.focus_calls == [False]
    assert controller.estop_calls == 0, "D-4: gate input, never stop"

    Focus.current = dashboard.root
    dashboard.root.fire("<FocusIn>", FakeEvent(widget=dashboard.root))
    assert controller.focus_calls == [False, True]


def test_a_child_dialog_taking_focus_is_not_focus_loss(dashboard, controller):
    dashboard.open()
    Focus.current = dashboard.root
    dashboard._on_window_focus(FakeEvent(widget=dashboard.root))
    assert controller.focus_calls == [True]
    dialog = FakeWidget(dashboard.root)
    Focus.current = dialog          # still inside this application
    dashboard.root.fire("<FocusOut>", FakeEvent(widget=dashboard.root))
    assert controller.focus_calls == [True], "a dialog is not focus loss"


def test_focus_events_from_a_child_widget_are_ignored(dashboard, controller):
    dashboard.open()
    child = FakeWidget(dashboard.root)
    dashboard.root.fire("<FocusOut>", FakeEvent(widget=child))
    assert controller.focus_calls == []


def test_the_dashboard_tick_stops_after_close(dashboard):
    dashboard.open()
    token = dashboard._after_id
    dashboard.close()
    assert token in SCHEDULER.cancelled
    assert dashboard._after_id is None


def _event(severity, needs_ack):
    return Event(1, severity, "Demo", f"{severity}-event", "something happened",
                 None, needs_ack, 0.0)


def test_closing_the_last_model_brings_setup_back(dashboard, controller):
    """An empty notebook is a dead end: with no model open, the next step is
    Setup, so Setup comes back instead of a blank page."""
    dashboard.open()
    _launch_a_model(dashboard, controller)
    assert not _setup_tab_is_shown(dashboard)
    controller.remove("Demo")
    SCHEDULER.pump()
    assert _setup_tab_is_shown(dashboard)


def test_on_aqua_a_point_is_a_pixel(monkeypatch):
    """Tk on Aqua assumes 96 dpi, so 12 pt came out 16 px tall — a third
    larger than every native control. There the theme's points go to Tk as
    pixels (a negative size); elsewhere they stay points."""
    monkeypatch.setattr(tkmod, "_PIXEL_FONTS", True)
    family, size, weight = tkmod._font(tkmod.BASE)
    assert size == -theme.FONT_SIZE
    assert abs(tkmod._font(tkmod.STEP_1)[1]) > theme.FONT_SIZE
    monkeypatch.setattr(tkmod, "_PIXEL_FONTS", False)
    assert tkmod._font(tkmod.BASE)[1] == theme.FONT_SIZE


def test_the_type_scale_steps_by_the_operate_ratio():
    assert 1.125 <= tkmod.RATIO <= 1.2
    sizes = [abs(tkmod._font(step)[1]) for step in
             (tkmod.SMALL, tkmod.BASE, tkmod.STEP_1, tkmod.STEP_2)]
    assert sizes == sorted(sizes) and len(set(sizes)) == 4


# ---------------------------------------------------------------------------
# Tier F (UI audit 2026-09-24): the stop path first
# ---------------------------------------------------------------------------

def _helvetica(font, text):
    """The audit's measurement stood in for Tk's: Helvetica is about 0.55 em
    a glyph, 0.62 em in bold (it gives the 88 px the auditor measured for a
    bold "Clear" at 28 px). A negative size is pixels, a positive points."""
    size = abs(font[1]) * (4 / 3 if font[1] > 0 else 1)
    return round(size * (0.62 if font[2] == "bold" else 0.55) * len(text))


@pytest.mark.parametrize("pixel_fonts", [True, False])
def test_clear_fits_both_stop_discs_at_every_font_size(monkeypatch, pixel_fonts):
    """F7 / AUD-2. The disc was a fixed 64 px (34 px for a model's own), so
    "Clear" ran off both from about 18 pt and the canvas cut the main face
    at 28 pt. The disc is now sized from its face font; the check is the
    auditor's own: `measure("Clear") <= inner - 4`, inner = the disc less
    its ring, and the canvas holds the disc at the top of its pulse."""
    monkeypatch.setattr(tkmod, "_text_width", _helvetica)
    monkeypatch.setattr(tkmod, "_PIXEL_FONTS", pixel_fonts)
    original = theme.FONT_SIZE
    try:
        for points in range(8, 29):
            theme.set_font_size(points)
            # Updated for E: one disc (A's, in the rail), normal and narrow;
            # a model's own stop is a switch, not a second disc.
            for narrow, floor in ((False, tkmod.STOP_DIAMETER),
                                  (True, tkmod.STOP_DIAMETER_NARROW)):
                disc = tkmod._Mushroom(FakeWidget(), lambda: None,
                                       background=theme.SURFACE, narrow=narrow)
                inner = disc.diameter - 2 * disc.ring_width(disc.diameter)
                clear = _helvetica(disc.face_font, "Clear")
                assert clear <= inner - 4, (points, narrow, disc.diameter)
                assert disc.diameter >= floor
                assert disc.size >= disc.diameter * max(tkmod.PULSE_FRAMES)
                assert disc.canvas.cget("width") == disc.size
    finally:
        theme.set_font_size(original)


def test_the_stop_disc_keeps_its_size_at_the_default_font(monkeypatch):
    """Updated for E: at 12 pt the disc is A's 136 px object (theme.STOP),
    or the few pixels more its face needs - never smaller, never a bench
    button."""
    monkeypatch.setattr(tkmod, "_text_width", _helvetica)
    monkeypatch.setattr(tkmod, "_PIXEL_FONTS", True)
    monkeypatch.setattr(theme, "FONT_SIZE", 12)
    disc = tkmod._Mushroom(FakeWidget(), lambda: None, background=theme.SURFACE)
    assert tkmod.STOP_DIAMETER == theme.STOP["diameter"]
    assert tkmod.STOP_DIAMETER <= disc.diameter <= tkmod.STOP_DIAMETER * 1.1


def test_a_fault_is_a_dialog_beside_the_stop_never_a_modal(dashboard, tk_harness):
    """F1 / HC-2 / UXPM-1. `messagebox.showerror` was application-modal:
    five queued errors were five dialogs and the stop took no click while
    one was up. Updated (rb-restart R6, owner 2026-09-28): the alert band is
    gone; the errors queue behind ONE modeless dialog, nothing grabs, and
    the stop still works."""
    dashboard.open()
    for index in range(3):
        dashboard._on_event(Event(index, "error", "Rotator", f"Fault {index}",
                                  "move failed", None, True, 0.0))
    SCHEDULER.pump()
    assert tk_harness.errors == [] and GRABS == []
    assert len(dashboard._alerts) == 3, "stacked, not overwritten"
    dialog = dashboard._ack_dialog
    assert dialog.title_label.cget("text") == "Fault 0"
    assert dialog.count.cget("text") == "2 more waiting"

    dashboard._stop_button.fire("<Button-1>")          # the stop still works
    assert tk_harness.errors == [] and dashboard.controller.estop_calls == 1
    assert not dialog.top.is_destroyed, "the stop does not answer it"

    for index in range(3):
        assert dashboard._ack_dialog.title_label.cget("text") == f"Fault {index}"
        dashboard._ack_dialog.key.widget.fire("<Button-1>")
    assert dashboard._alerts == [] and dashboard._ack_dialog is None


def test_r6_the_alert_band_is_gone(dashboard):
    """rb-restart R6: no band beside the dialog; the dialog is the
    acknowledgement and the log keeps the history."""
    dashboard.open()
    dashboard._show_popup(_event("error", needs_ack=True))
    assert not hasattr(dashboard, "_band")
    assert not hasattr(dashboard, "_band_text")
    assert not hasattr(tkmod, "BAND_LINES")
    assert dashboard.is_alert_shown and dashboard._ack_dialog is not None


def test_the_clear_confirmation_defaults_to_no():
    """F17 / AUD-6 / UXPM-15. `askyesno` defaulted to Yes: Return released
    the latch. Focus starts on No; Return, Escape and the close button all
    answer No; only a press on Yes answers Yes."""
    master = FakeRoot()
    for gesture in ("<Return>", "<Escape>", "close"):
        dialog = tkmod._ConfirmDialog(master, "Release the latch?")
        dialog._build()
        assert Focus.current is dialog.no.widget, "focus starts on No"
        if gesture == "close":
            dialog.top.protocols["WM_DELETE_WINDOW"]()
        else:
            dialog.top.fire(gesture)
        assert dialog.answer is False, gesture
        assert dialog.top.is_destroyed
    dialog = tkmod._ConfirmDialog(master, "Release the latch?")
    dialog._build()
    dialog.yes.widget.fire("<Button-1>")
    assert dialog.answer is True


def test_the_confirmation_takes_no_grab_so_the_stop_stays_live():
    """F1: a question may be open while the operator needs the stop."""
    dialog = tkmod._ConfirmDialog(FakeRoot(), "Release the latch?")
    assert dialog.ask() is False          # the stand-in's wait returns at once
    assert GRABS == []


def test_the_panel_and_the_dashboard_ask_through_the_one_dialog(dashboard,
                                                               monkeypatch):
    asked = []
    monkeypatch.setattr(tkmod._ConfirmDialog, "ask",
                        lambda self: asked.append(self.prompt) or False)
    monkeypatch.setattr(tkmod, "_confirm",
                        lambda master, prompt, **words: tkmod._ConfirmDialog(
                            master, prompt, **words).ask())
    dashboard.open()
    dashboard.controller.is_estopped = True
    dashboard.controller.clear_needs_confirm = True
    dashboard._stop_button.fire("<Button-1>")
    assert asked == ["Release the latch?"]
    assert dashboard.controller.is_estopped is True, "declined: still latched"


def test_a_global_key_stops_every_model_and_never_clears(dashboard, controller):
    """F9. The stop from anywhere in the window, whatever holds focus. It
    only ever stops: clearing stays a press on the disc and a question."""
    dashboard.open()
    handler = ALL_BINDINGS["<Control-period>"]
    handler(FakeEvent())
    assert controller.estop_calls == 1 and controller.is_estopped
    handler(FakeEvent())
    assert controller.clear_calls == 0, "the shortcut never clears the latch"
    assert dashboard._stop.face == tkmod.CLEAR_FACE


@pytest.fixture(params=["x11", "win32", "aqua"])
def any_platform(request, monkeypatch):
    """The dashboard as each windowing system would build it (G5)."""
    monkeypatch.setattr(tkmod, "_PIXEL_FONTS", False)    # restored after
    monkeypatch.setattr(tkmod, "_windowing_system", lambda _w: request.param)
    return request.param


def test_the_stop_shortcut_is_written_where_the_operator_looks(any_platform,
                                                              controller,
                                                              setup_panel):
    """G5: one chord on every platform, and the copy names it and only it."""
    dashboard = tkmod.TkDashboard(controller, setup_panel)
    dashboard.open()
    hint = dashboard.chord_text
    assert tkmod.STOP_KEY_NAME == "Ctrl+."
    assert "Ctrl+." in hint and "Ctrl+." in dashboard._stop.tooltip.text
    for text in (hint, dashboard._stop.tooltip.text):
        assert "\u2318" not in text and "Cmd" not in text, any_platform
    dashboard.close()


def test_ctrl_period_presses_the_stop_on_every_platform(any_platform, controller,
                                                       setup_panel):
    dashboard = tkmod.TkDashboard(controller, setup_panel)
    dashboard.open()
    ALL_BINDINGS["<Control-period>"](FakeEvent())
    assert controller.estop_calls == 1 and controller.is_estopped
    dashboard.close()


def test_no_view_binding_uses_a_mac_only_modifier(any_platform, controller,
                                                  setup_panel):
    """G5 (owner ruling 2026-09-25): no Command / Meta / \u2318 chord, on a
    Mac or anywhere, and no mac-only command beyond the forced Quit hook."""
    dashboard = tkmod.TkDashboard(controller, setup_panel)
    dashboard.open()
    sequences = list(ALL_BINDINGS)
    widgets = [dashboard.root]
    while widgets:
        widget = widgets.pop()
        sequences += list(widget.bindings)
        widgets += widget.children
    assert "<Control-period>" in sequences
    for sequence in sequences:
        assert not re.search(r"Command|Meta|Mod1|Option", sequence), sequence
    assert set(dashboard.root.commands) <= {"::tk::mac::Quit"}
    dashboard.close()


def test_no_mac_only_chord_or_glyph_in_the_module():
    code = _executable_source(tkmod.__file__)
    for needle in ("Command-", "Meta-", "\u2318", "\\u2318", "Cmd"):
        assert needle not in code, needle


def test_space_and_return_both_press_the_stop(dashboard, controller):
    dashboard.open()
    dashboard._stop_button.fire("<space>")
    assert controller.estop_calls == 1
    dashboard._stop_button.fire("<Return>")
    assert controller.clear_calls == 1


def test_the_stop_focus_ring_is_ink_two_pixels_latched_or_not(dashboard,
                                                               controller):
    """A trace ring is what "latched" looks like; focus is STOP_FOCUS."""
    dashboard.open()
    for latched in (False, True):
        controller.is_estopped = latched
        dashboard._sync_stop_button()
        dashboard._stop_button.fire("<FocusIn>")
        rings = [item[2] for item in dashboard._stop_button.items
                 if item[0] == "oval" and not item[2].get("fill")]
        assert rings and rings[0]["outline"] == theme.STOP_FOCUS
        assert rings[0]["width"] == 2
        dashboard._stop_button.fire("<FocusOut>")


class LostPanel(DemoPanel):
    """A model whose serial link has gone: `Model.state` says so in
    `devices`, and nothing else changes - the loop keeps ticking."""

    def __init__(self):
        super().__init__()
        self.link = "verified"

    @property
    def state(self):
        state = dict(super().state)
        state["devices"] = {"SerialPort": self.link, "Gamepad": "bound"}
        return state


def test_a_lost_serial_port_no_longer_looks_live(tk_harness):
    """F3 / HC-1: the grouping rule turns signal, the values are muted, the
    title and a line under it say what was lost."""
    panel = LostPanel()
    panel.speed = 2.5
    view = tkmod.TkPanelView(FakeWidget(), FakeController(Probe=panel), "Probe")
    readout = widget_of(view, element_of(view, "readonly", "Speed now:"))
    # Updated for E: a readout at rest is ink (trace only while it changes);
    # the entry's head rule is 2 px of ink.
    assert readout.cget("foreground") == theme.TEXT
    assert view._rule.cget("background") == theme.RULE_STRONG

    panel.link = "lost"
    view._refresh()
    assert view.lost_devices == ("SerialPort",)
    assert view._rule.cget("background") == theme.SIGNAL
    assert readout.cget("foreground") == theme.MUTED
    assert "connection lost" in view._title.cget("text")
    assert "serial port" in view._health.cget("text")
    assert view._health.is_packed

    panel.link = "verified"
    view._refresh()
    assert view._rule.cget("background") == theme.RULE_STRONG
    assert readout.cget("foreground") == theme.TEXT
    assert view._title.cget("text") == "Probe"
    view.close()


def test_the_stop_bar_names_the_model_and_the_device_it_lost(controller,
                                                             setup_panel):
    panel = LostPanel()
    built = tkmod.TkDashboard(FakeController(**{"Stepper Probe": panel}), setup_panel)
    built.open()
    panel.link = "lost"
    built._panels["Stepper Probe"]._refresh()
    built._on_refresh_tick()
    line = built._station_line.cget("text")
    assert "Stepper Probe" in line and "serial port" in line
    assert any(item[2].get("fill") == theme.SIGNAL
               for item in built._station_mark.items)
    panel.link = "verified"
    built._panels["Stepper Probe"]._refresh()
    built._on_refresh_tick()
    assert built._station_line.cget("text") == ""
    built.close()


class LinkPanel(DemoPanel):
    """A model that owns a serial port and publishes `state["link"]` (the
    rb-link contract): its status, its counters, whether it is stalled."""

    def __init__(self):
        super().__init__()
        self.link = {"status": "verified", "losses": 0, "reconnects": 0,
                     "dropped": 0, "stalls": 0, "stalled": False,
                     "last_loss": None}
        self.position_age = None
        self.pad = "bound"

    @property
    def state(self):
        state = dict(super().state)
        status = self.link["status"]
        state["devices"] = {"SerialPort": status, "Gamepad": self.pad}
        state["link"] = dict(self.link)
        if self.position_age is not None:
            state["values"] = dict(state["values"], position_age=self.position_age)
        return state


def _mark_fills(canvas):
    return {item[2].get("fill") for item in canvas.items if item[0] == "line"}


def test_v1_a_reconnecting_link_turns_the_entry_signal_and_holds_its_modes(
        tk_harness):
    """F3 / HC-1 (rb-link-views V1): `link.status` "reconnecting" - which
    `devices` reports as "reconnecting", not "lost", so the old device rule
    missed it - turns the head rule signal, mutes the numbers, holds the mode
    toggle and says in words when the link went and that it is coming
    back. "lost" says it is not."""
    panel = LinkPanel()
    panel.speed = 2.5
    view = tkmod.TkPanelView(FakeWidget(), FakeController(Probe=panel), "Probe")
    readout = widget_of(view, element_of(view, "readonly", "Speed now:"))
    run = element_of(view, "toggle", "Run")
    assert view._rule.cget("background") == theme.RULE_STRONG
    assert view._widgets[id(run)]["is_enabled"] is True
    assert not view._health.is_packed

    panel.link.update(status="reconnecting", losses=1, last_loss="12:41:07")
    view._refresh()
    assert view._rule.cget("background") == theme.SIGNAL
    assert view._health.cget("text") == "Link lost 12:41:07, reconnecting…"
    assert view._health.is_packed and view._health_row.is_packed
    assert theme.SIGNAL in _mark_fills(view._health_mark)
    assert readout.cget("foreground") == theme.MUTED
    assert "connection lost" in view._title.cget("text")
    assert view._widgets[id(run)]["is_enabled"] is False
    assert view._gate_reason(run) == "Link lost: wait for it to reconnect"

    panel.link.update(status="lost")
    view._refresh()
    assert view._health.cget("text") == "Link lost 12:41:07; not reconnecting"
    assert view._rule.cget("background") == theme.SIGNAL
    assert view._widgets[id(run)]["is_enabled"] is False

    panel.link.update(status="verified", reconnects=1)
    view._refresh()
    assert view._rule.cget("background") == theme.RULE_STRONG
    assert readout.cget("foreground") == theme.TEXT
    assert view._title.cget("text") == "Probe"
    assert not view._health.is_packed
    assert view._widgets[id(run)]["is_enabled"] is True
    view.close()


def test_v1_a_stalled_link_is_the_attention_tier_and_holds_nothing(tk_harness):
    """A link that is up but brings no position: one warning-tier line, the
    glyph in ink, the rule left ink, the modes left live."""
    panel = LinkPanel()
    view = tkmod.TkPanelView(FakeWidget(), FakeController(Probe=panel), "Probe")
    run = element_of(view, "toggle", "Run")
    panel.link.update(stalled=True, stalls=2)
    panel.position_age = 7.2
    view._refresh()
    assert view._health.cget("text") == "No position for 7 s; link up, check the board"
    assert view._health.is_packed
    assert view._rule.cget("background") == theme.RULE_STRONG
    assert _mark_fills(view._health_mark) == {theme.SEVERITY_MARK["warning"]}
    assert view._widgets[id(run)]["is_enabled"] is True
    view.close()


class DiagnosedLinkPanel(LinkPanel):
    """A linked model with a Diagnostics section of its own (tier 3)."""

    @property
    def schema(self):
        schema = dict(super().schema)
        schema["sections"] = list(schema["sections"]) + [sch.section(
            "Diagnostics", sch.readonly("Done:", "done_count", param=self.DONE_COUNT),
            tier=3, disclosure="Diagnostics")]
        return schema


def test_v2_the_link_counters_are_a_readout_in_diagnostics(tk_harness):
    """rb-link-views V2: `losses / reconnects / dropped / stalls` and the
    last loss, one compact readonly line in the model's own Diagnostics
    (tier 3), filled from `state.link` on every refresh."""
    panel = DiagnosedLinkPanel()
    view = tkmod.TkPanelView(FakeWidget(), FakeController(Probe=panel), "Probe")
    row = element_of(view, "readonly", view_base.LINK_CAPTION)
    assert row["model_attr"] == view_base.LINK_ATTR
    assert view._widgets[id(row)]["var"].get() == "0 / 0 / 0 / 0; last loss never"
    panel.link.update(losses=1, reconnects=1, dropped=5, stalls=2,
                      last_loss="12:41:07")
    view._refresh()
    assert view._widgets[id(row)]["var"].get() == "1 / 1 / 5 / 2; last loss 12:41:07"
    # In the tier-3 strip, with the section's own readouts.
    def ancestors(widget):
        while widget is not None:
            yield widget
            widget = getattr(widget, "master", None)
    assert view._tiers[3] in list(ancestors(widget_of(view, row)))
    view.close()


def test_v1_the_rail_line_of_a_down_link_shows_its_tier(controller, setup_panel):
    """The rail's per-model line takes the entry's tier: the warning glyph
    in signal while the link is down, in ink while it is stalled, nothing
    when it is well; the station line under the stop says it in words."""
    panel = LinkPanel()
    built = tkmod.TkDashboard(FakeController(**{"Stepper Probe": panel}), setup_panel)
    built.open()
    mark = built._rail_marks["Stepper Probe"][0]

    def tick():
        built._panels["Stepper Probe"]._refresh()
        built._on_refresh_tick()

    panel.link.update(status="reconnecting", last_loss="12:41:07")
    tick()
    assert "Link lost 12:41:07, reconnecting" in built._station_line.cget("text")
    assert _mark_fills(mark) == {theme.SIGNAL}
    assert built._rail_marks["Stepper Probe"][1].text.startswith("Link lost")

    panel.link.update(status="verified", stalled=True)
    tick()
    assert _mark_fills(mark) == {theme.SEVERITY_MARK["warning"]}
    assert "No position" in built._station_line.cget("text")

    panel.link.update(stalled=False)
    tick()
    assert _mark_fills(mark) == set()
    assert built._station_line.cget("text") == ""
    built.close()


# ---------------------------------------------------------------------------
# Tier F: refusals, severity, long names, repaint cost, focus, tokens
# ---------------------------------------------------------------------------

def test_a_refusal_is_shown_at_the_control_that_caused_it(view):
    """F10: the row under the pressed control, wrapped in ink on a tinted
    band - not one line at the foot of the panel cut at 60 %."""
    refuse = element_of(view, "button", "Refuse")
    click(view, refuse)
    notice = view._notice
    assert notice is not None and notice.cget("text") == "the bench is busy"
    container, row, column, span = view._widgets[id(refuse)]["slot"]
    strip = view._widgets[id(refuse)]["ring"].outer.master
    assert notice.master is container is strip.master
    # Updated for E: the section's notice row, under all its cells.
    assert notice.grid_info["row"] == row > strip.grid_info["row"]
    assert notice.cget("wraplength") > 0 and notice.cget("justify") == "left"
    assert notice.cget("foreground") == theme.TEXT, "ink, not trace"


def test_an_entry_refusal_sits_under_the_entry(view):
    speed = element_of(view, "entry", "Speed")
    view._widgets[id(speed)]["var"].set("99")
    widget_of(view, speed).fire("<Return>")
    # Updated for E: under the section the entry's cell flows in.
    strip = view._widgets[id(speed)]["strip"]
    assert view._notice.master is strip.master
    assert view._notice.grid_info["row"] > strip.grid_info["row"]


def test_a_refusal_below_the_fold_is_scrolled_into_view(view):
    """The body is 1000 px tall and the viewport shows its top 400: a
    refusal at 700 px is scrolled to, just enough to show it whole."""
    refuse = element_of(view, "button", "Refuse")
    container = view._widgets[id(refuse)]["slot"][0]
    container.winfo_y = lambda: 690
    view._tiers[1].winfo_y = lambda: 0     # E: sections sit in a tier frame
    view._body.winfo_height = lambda: 1000
    view._canvas.yview = lambda *args: (0.0, 0.4)
    original = FakeWidget.__init__

    def with_geometry(self, master=None, **options):
        original(self, master, **options)
        self.winfo_y = lambda: 10
        self.winfo_reqheight = lambda: 40
        self.update_idletasks = lambda: None
    FakeTkModule.Label = type("GeometryLabel", (FakeWidget,), {"__init__": with_geometry})
    try:
        click(view, refuse)
    finally:
        FakeTkModule.Label = FakeWidget
    moved = view._canvas.moved_to
    assert 0.0 < moved <= 1.0
    assert moved * 1000 + 400 >= 700 + 40, "the whole line is in view"


def test_a_failed_command_clears_an_old_refusal(view, panel):
    click(view, element_of(view, "button", "Refuse"))
    assert refusal(view)
    panel.go = lambda: (_ for _ in ()).throw(RuntimeError("port gone"))
    click(view, element_of(view, "button", "Go"))
    assert refusal(view) == "", "the failure is on the alert band"


def test_a_fault_reason_is_ink_with_a_signal_mark(controller):
    """F14 / AUD-5: "Fault reason" in signal was 2.73:1 on the panel."""
    class Faulted(DemoPanel):
        def __init__(self):
            super().__init__()
            self.fault = ""

        @property
        def schema(self):
            return sch.schema(sch.section("Safety", sch.readonly(
                "Fault reason:", "fault", role="danger")))

    panel = Faulted()
    built = tkmod.TkPanelView(FakeWidget(), FakeController(F=panel), "F")
    element = built._elements[0]
    entry = built._widgets[id(element)]
    assert entry["mark"] is not None and entry["mark"].items == []
    panel.fault = "Move failed: stage did not answer"
    built._refresh()
    assert entry["widget"].cget("foreground") == theme.TEXT
    assert [item[2]["fill"] for item in entry["mark"].items] == [theme.SIGNAL]
    panel.fault = ""
    built._refresh()
    assert entry["mark"].items == []
    built.close()


def test_long_port_names_are_cut_in_the_middle_and_map_back(row_view):
    """F15 / HC-6: the tail is what tells two ports apart; the box shows a
    middle-elided name, the command gets the whole one, the tooltip has it."""
    names = ["/dev/cu.usbmodem1234567890123", "/dev/cu.usbmodem1234567890456"]
    row_view._panel = None
    element = row_view._elements[STEPPER_PORT]
    entry = row_view._widgets[id(element)]
    row_view._options = lambda _command: names
    entry["var"].set("")
    row_view._refresh_options(element)
    labels = entry["widget"].cget("values")
    assert all(tkmod.ELLIPSIS in label for label in labels)
    assert labels[0][-5:] == "90123" and labels[1][-5:] == "90456"
    assert len(set(labels)) == 2
    assert all(len(label) <= tkmod.DROPDOWN_WIDTH for label in labels)

    entry["var"].set(labels[1])
    ran = []
    row_view._run = lambda el, args=(): ran.append(args)
    row_view._on_dropdown_selected(element)
    assert ran == [(names[1],)]

    row_view._set_text(element, names[0])
    assert entry["var"].get() == labels[0]
    assert entry["tooltip"].text == names[0]


def test_a_long_run_id_is_cut_in_the_middle(view, panel, monkeypatch):
    element = element_of(view, "readonly", "Fault:")
    widget = widget_of(view, element)
    monkeypatch.setattr(tkmod, "_text_width", lambda _font, text: 10 * len(text))
    # Updated for E: a sheet readout's room is the width of its flow.
    view._flow_strip(view._widgets[id(element)]["strip"], 220)
    panel.fault_reason = "RUN-2026-09-24-WSe2-hBN-sample-3-cut-0017"
    view._refresh()
    shown = widget.cget("text")
    assert tkmod.ELLIPSIS in shown and not shown.endswith(tkmod.ELLIPSIS)
    assert shown.endswith("0017") and len(shown) * 10 <= 220


def test_the_stop_discs_are_not_rebuilt_while_nothing_changes(dashboard,
                                                              controller):
    """F21 / AUD-9: the disc was deleted and redrawn 24 times a second."""
    dashboard.open()
    dashboard._sync_stop_button()
    before = dashboard._stop.draws
    for _ in range(25):
        dashboard._on_refresh_tick()
    assert dashboard._stop.draws == before
    controller.is_estopped = True
    dashboard._sync_stop_button()
    assert dashboard._stop.draws == before + 1


def test_an_unchanged_toggle_is_not_repainted(view, panel, monkeypatch):
    painted = []
    original = tkmod.TkPanelView._paint_command
    monkeypatch.setattr(tkmod.TkPanelView, "_paint_command",
                        lambda self, el: (painted.append(el), original(self, el)))
    for _ in range(10):
        view._refresh()
    assert painted == []
    panel.is_running = True
    view._refresh()
    assert len(painted) == 1


def test_an_unchanged_plot_is_not_redrawn(view, panel):
    panel.samples = [1, 2, 3]
    view._refresh()
    canvas = widget_of(view, element_of(view, "plot"))
    canvas.items.append(("marker", (), {}))
    view._refresh()
    assert ("marker", (), {}) in canvas.items, "same series: not redrawn"
    panel.samples = [1, 2, 3, 4]
    view._refresh()
    assert ("marker", (), {}) not in canvas.items


def test_the_minimised_setup_panel_stops_ticking(dashboard, controller):
    """F21 / AUD-9: hidden, it re-read its schema ten times a second."""
    dashboard.open()
    setup = dashboard._panels[dashboard.SETUP_TAB]
    assert setup.is_ticking
    _launch_a_model(dashboard, controller)
    assert dashboard._is_setup_collapsed and not setup.is_ticking
    SCHEDULER.pump()
    assert not setup.is_ticking, "nothing rescheduled it"
    dashboard.restore_setup()
    assert setup.is_ticking


def test_a_reopened_model_is_brought_forward(dashboard, controller):
    """AUD-13: the Models-menu reopen changed nothing on screen."""
    dashboard.open()
    controller.remove("Demo")
    SCHEDULER.pump()
    dashboard.notebook.select(dashboard._frames[dashboard.SETUP_TAB])
    # Updated for K4: a model added by a launch lands on the overview, so the
    # reopen is driven through the Models menu, which is what AUD-13 was;
    # brought forward = the sheet is shown, on that model's own page.
    dashboard._menu_vars["Demo"].set(True)
    dashboard._on_model_toggled("Demo")
    SCHEDULER.pump()
    assert dashboard.notebook.select() == str(dashboard._sheet_page) or \
        dashboard.notebook._selected is dashboard._sheet_page
    assert dashboard._opened == "Demo"


def test_k4_a_model_added_by_a_launch_lands_on_the_overview(dashboard, controller):
    dashboard.open()
    _launch_a_model(dashboard, controller)
    assert dashboard._shown_page == dashboard.SHEET_TAB
    assert dashboard._opened is None


def test_no_motion_disables_the_pulse(dashboard, controller, monkeypatch):
    """AUD-14: `STATION_NO_MOTION=1`; the face and ring still change."""
    monkeypatch.setenv("STATION_NO_MOTION", "1")
    dashboard.open()
    dashboard._sync_stop_button()
    controller.is_estopped = True
    dashboard._sync_stop_button()
    assert dashboard._stop._pulse_ids == []
    assert dashboard._stop.face == tkmod.CLEAR_FACE


def test_every_focus_mark_is_two_pixels_of_ink(dashboard):
    """F25 / AUD-10: tabs showed nothing (focus colour = the strip), entries
    and commands 1 px of trace."""
    dashboard.open()
    tab = STYLE["TNotebook.Tab"]
    assert tab["focuscolor"] == theme.TEXT and tab["focusthickness"] == 2
    view = dashboard._panels["Demo"]
    speed = element_of(view, "entry", "Speed")
    ring = view._widgets[id(speed)]["ring"]
    widget_of(view, speed).fire("<FocusIn>")
    assert ring.outer.cget("background") == ring.inner.cget("background") == theme.TEXT
    assert ring.outer.cget("padx") + ring.inner.cget("padx") == 2
    source = view._widgets[id(element_of(view, "dropdown"))]["ring"]
    widget_of(view, element_of(view, "dropdown")).fire("<FocusIn>")
    assert source.outer.cget("background") == theme.TEXT
    dashboard._setup_button.fire("<FocusIn>")
    assert dashboard._setup_press.ring.outer.cget("background") == theme.TEXT


def _contrast(a, b):
    def luminance(colour):
        channels = [int(colour[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        channels = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
                    for c in channels]
        return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]
    high, low = sorted((luminance(a), luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def test_input_and_command_borders_are_three_to_one_on_the_panel(view):
    """F25 / AUD-11: they measured 1.88:1."""
    speed = view._widgets[id(element_of(view, "entry", "Speed"))]
    go = view._widgets[id(element_of(view, "button", "Go"))]
    # Updated for E: an input's edge is its underline (theme.INPUT_BORDER);
    # a command's is its outline. Both 3:1 on the panel AND on the sheet.
    assert speed["ring"].line.cget("background") == theme.INPUT_BORDER
    for border in (speed["ring"].line.cget("background"),
                   go["outline"].cget("background")):
        assert _contrast(border, theme.SURFACE) >= 3.0
        assert _contrast(border, theme.BACKGROUND) >= 3.0


def test_targets_are_at_least_24_px_at_every_font_size(monkeypatch):
    """F25 / AUD-12, WCAG 2.5.8: a command is its line, its padding and its
    two ring pixels on each side."""
    monkeypatch.setattr(tkmod, "_PIXEL_FONTS", True)
    original = theme.FONT_SIZE
    try:
        for points in range(8, 29):
            theme.set_font_size(points)
            height = tkmod._line_px() + 2 * tkmod._target_pady() + 2 * tkmod.FOCUS_PX
            assert height >= 24, points
            assert tkmod._lamp_px() >= tkmod.LAMP_PX
        assert tkmod._lamp_px() > tkmod.LAMP_PX, "a lamp grows with 28 pt text"
    finally:
        theme.set_font_size(original)


def test_the_type_scale_is_the_theme_scale():
    """F23: the four steps are `theme.size(step)`, not a private ratio."""
    for step in (tkmod.SMALL, tkmod.BASE, tkmod.STEP_1, tkmod.STEP_2):
        assert abs(tkmod._font(step)[1]) == theme.size(step)


def test_no_private_mixer_and_no_spacing_sums():
    """F23 / DS-7, DS-12: one `theme.mix`, and a distance is a SPACE step,
    never `GAP + 2` or `PAD * 2`."""
    code = _executable_source(tkmod.__file__)
    assert "def _mix" not in code and "_mix(" not in code
    sums = re.findall(r"\b(?:PAD|GAP|INSET)\s*[-+*/]\s*\w|\w\s*[-+*/]\s*(?:PAD|GAP|INSET)\b",
                      code)
    assert not sums, sums


# ---------------------------------------------------------------------------
# G3: the Launch checkbox, and the dropdowns it gates
# ---------------------------------------------------------------------------

def tick(view, element):
    """What a click on a ttk.Checkbutton does: Tk flips the variable, then
    calls the command."""
    entry = view._widgets[id(element)]
    entry["var"].set(not entry["var"].get())
    return entry["widget"].cget("command")()


def test_a_checkbox_renders_a_ttk_checkbutton_on_a_boolean(view):
    box = element_of(view, "checkbox", "Armed")
    entry = view._widgets[id(box)]
    widget = entry["widget"]
    assert isinstance(widget, FakeWidget)
    assert widget.cget("variable") is entry["var"]
    assert callable(widget.cget("command"))
    assert entry["tooltip"].text == "Arm the demo"


def test_clicking_a_checkbox_sends_the_new_value(view, panel, controller):
    box = element_of(view, "checkbox", "Armed")
    tick(view, box)
    assert last_call(controller, "set_armed")[3] == (True,)
    assert panel.is_armed is True
    tick(view, box)
    assert last_call(controller, "set_armed")[3] == (False,)
    assert panel.is_armed is False


def test_a_checkbox_sends_the_model_s_opposite_even_when_drawn_behind(view, panel,
                                                                    controller):
    """The box is one refresh behind the model: the command still flips
    what the MODEL holds, and the refresh puts the box right."""
    box = element_of(view, "checkbox", "Armed")
    panel.is_armed = True                 # the model moved; no refresh yet
    tick(view, box)                       # the widget thinks it went on
    assert last_call(controller, "set_armed")[3] == (False,)
    assert view._widgets[id(box)]["var"].get() is False


def test_refresh_sets_a_checkbox_from_state_never_from_the_widget(view, panel):
    box = element_of(view, "checkbox", "Armed")
    var = view._widgets[id(box)]["var"]
    panel.is_armed = True
    view._refresh()
    assert var.get() is True
    var.set(False)                        # the widget alone says otherwise
    view._refresh()
    assert var.get() is True
    panel.is_armed = False
    view._refresh()
    assert var.get() is False


def test_a_greyed_checkbox_is_disabled_and_does_not_run(view, panel, controller):
    box = element_of(view, "checkbox", "Armed")
    widget = widget_of(view, box)
    view._set_enabled(box, False)
    assert widget.cget("state") == "disabled"
    calls = len(controller.calls)
    tick(view, box)
    assert not any(call[1] == "set_armed" for call in controller.calls[calls:])
    assert view._widgets[id(box)]["var"].get() is False, "put back from the model"
    view._set_enabled(box, True)
    assert widget.cget("state") == "normal"


def test_a_gated_dropdown_is_disabled_until_its_box_is_ticked(view, panel):
    target = element_of(view, "dropdown", "Target")
    source = element_of(view, "dropdown", "Source")
    combo = widget_of(view, target)
    assert combo.cget("state") == "disabled", "a disabled combobox cannot open"
    assert widget_of(view, source).cget("state") == "readonly"
    tick(view, element_of(view, "checkbox", "Armed"))
    assert combo.cget("state") == "readonly"
    tick(view, element_of(view, "checkbox", "Armed"))
    assert combo.cget("state") == "disabled"


def test_a_greyed_dropdown_selection_runs_nothing(view, panel, controller):
    target = element_of(view, "dropdown", "Target")
    view._widgets[id(target)]["var"].set("alpha")
    widget_of(view, target).fire("<<ComboboxSelected>>")
    assert not any(call[1] == "set_target" for call in controller.calls)
    assert panel.target is None


def test_the_checkbox_ring_shows_keyboard_focus(view):
    box = element_of(view, "checkbox", "Armed")
    ring = view._widgets[id(box)]["ring"]
    widget_of(view, box).fire("<FocusIn>")
    assert ring.outer.cget("background") == theme.TEXT
    widget_of(view, box).fire("<FocusOut>")
    assert ring.outer.cget("background") == tkmod._page()


@pytest.fixture
def setup_view():
    """The real Setup panel (G3's rows), never scanned: no `start()`."""
    from controller.controller import Controller
    from controller.setup import Setup
    setup = Setup(Controller())
    built = tkmod.TkPanelView(FakeWidget(), FakeController(), "Setup", panel=setup)
    yield built, setup
    built.close()


def test_the_setup_table_puts_the_launch_box_first_in_every_model_row(setup_view):
    view, setup = setup_view
    boxes = [e for e in view._elements if e["type"] == "checkbox"]
    ports = [e for e in view._elements
             if e["type"] == "dropdown" and e["text"] == "Port"]
    assert boxes and len(boxes) == len(ports) == len(setup._rows)
    assert view._table_columns["Launch"] == 1, "the first column after the name"
    for box, port in zip(boxes, ports):
        box_cell, port_cell = _cell(view, box), _cell(view, port)
        assert box_cell["row"] == port_cell["row"]
        assert box_cell["column"] == 1 and port_cell["column"] > 1
        assert box_cell["sticky"] == "w", "narrow: it never stretches"
    table = cell_widget(view, boxes[0]).master
    # One "Launch" caption over the boxes (the Launch bar's own row name is
    # the other "Launch", in column 0 at the bottom of the table).
    header = [c for c in table.children if c.cget("text") == "Launch"
              and (c.grid_info or {}).get("column") == 1]
    assert len(header) == 1, "the caption is said once, in the header"
    assert header[0].grid_info["row"] < _cell(view, boxes[0])["row"]
    assert table.column_weights.get(1, {}).get("weight") in (None, 0)


def test_the_setup_row_dropdowns_follow_the_launch_box(setup_view):
    view, setup = setup_view
    key = next(iter(setup._rows))
    box = next(e for e in view._elements if e.get("model_attr") == f"{key}_enabled")
    port = next(e for e in view._elements if e.get("model_attr") == f"{key}_port")
    assert widget_of(view, port).cget("state") == "disabled"
    tick(view, box)
    assert getattr(setup, f"{key}_enabled") is True
    assert widget_of(view, port).cget("state") == "readonly"


# ---------------------------------------------------------------------------
# G4: a detached log stream is a button that opens its own window
# ---------------------------------------------------------------------------

def side_log(view):
    return element_of(view, "log_stream", "Side Log:")


def test_a_detached_log_stream_renders_a_button_and_no_feed(view):
    element = side_log(view)
    entry = view._widgets[id(element)]
    button = entry["widget"]
    assert not isinstance(button, FakeText)
    assert button.cget("text") == "Side log\u2026"
    assert entry.get("window") is None and entry.get("feed") is None
    # the attached stream is still a feed on the card
    assert isinstance(widget_of(view, element_of(view, "log_stream", "Log")), FakeText)


def test_a_closed_log_window_is_never_polled(view, panel):
    assert view._wants_data(side_log(view)) is False
    assert view._wants_data(element_of(view, "log_stream", "Log")) is True
    before = panel.side_reads
    for _ in range(3):
        view._refresh()
    assert panel.side_reads == before == 0


def test_the_log_button_opens_one_window_showing_the_source_lines(view, panel):
    element = side_log(view)
    click(view, element)
    entry = view._widgets[id(element)]
    window, feed = entry["window"], entry["feed"]
    assert isinstance(window, FakeRoot) and not window.is_destroyed
    assert isinstance(feed, FakeText) and feed.master is not None
    # Updated for L12: "<model> <feed>", sentence case, no dash.
    assert window.titles == ["Demo side log"]
    assert feed.body == "gamepad on\nX+ 1.0"
    assert view._wants_data(element) is True
    panel.side.append("Y- 2.0")
    view._refresh()
    assert feed.body.endswith("Y- 2.0")


def test_the_log_window_is_not_modal_and_never_over_the_stop(view):
    click(view, side_log(view))
    window = view._widgets[id(side_log(view))]["window"]
    assert GRABS == [], "no grab: the stop takes a click while it is open"
    assert not any("-topmost" in args for args in window.attrs)


def test_a_second_press_raises_the_same_window(view):
    element = side_log(view)
    click(view, element)
    first = view._widgets[id(element)]["window"]
    click(view, element)
    assert view._widgets[id(element)]["window"] is first
    assert first.lifted >= 1


def test_escape_and_the_close_button_close_the_log_window(view, panel):
    element = side_log(view)
    for gesture in ("<Escape>", "close"):
        click(view, element)
        window = view._widgets[id(element)]["window"]
        if gesture == "close":
            window.protocols["WM_DELETE_WINDOW"]()
        else:
            window.fire(gesture)
        assert window.is_destroyed, gesture
        assert view._widgets[id(element)]["window"] is None
        assert view._wants_data(element) is False
        assert Focus.current is widget_of(view, element), "focus back to the button"
    reads = panel.side_reads
    view._refresh()
    assert panel.side_reads == reads, "closed again: not polled"


def test_closing_the_panel_destroys_the_log_window(controller):
    built = tkmod.TkPanelView(FakeWidget(), controller, "Demo")
    click(built, side_log(built))
    window = built._widgets[id(side_log(built))]["window"]
    built.close()
    assert window.is_destroyed


def test_the_stop_stays_live_while_the_log_window_is_open(dashboard, controller):
    dashboard.open()
    view = dashboard._panels["Demo"]
    click(view, side_log(view))
    ALL_BINDINGS["<Control-period>"](FakeEvent())
    assert controller.estop_calls == 1


# ---------------------------------------------------------------------------
# G5: the tab-close button is the same physical button everywhere
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("system, version, expected", [
    ("x11", 9.0, "<ButtonPress-2>"),
    ("win32", 9.0, "<ButtonPress-2>"),
    ("aqua", 9.0, "<ButtonPress-2>"),       # Tk 8.7+ numbers Aqua like X11
    ("x11", 8.6, "<ButtonPress-2>"),
    ("win32", 8.6, "<ButtonPress-2>"),
    ("aqua", 8.6, "<ButtonPress-3>"),       # Tk 8.6 on Aqua: 3 is the middle
])
def test_the_middle_button_closes_a_tab_on_every_platform(monkeypatch, system,
                                                          version, expected):
    """The operator's gesture is the middle button on every OS. The branch
    is the toolkit's button numbering, not a platform-specific gesture: the
    old one bound button 3 - the RIGHT button - on X11 and Win32."""
    monkeypatch.setattr(tkmod, "_windowing_system", lambda _w: system)
    monkeypatch.setattr(tkmod, "_tk_version", lambda: version)
    assert tkmod._close_tab_button(FakeWidget()) == expected


# ---------------------------------------------------------------------------
# I1: the Gamepad log window never covers the panel that owns it
# ---------------------------------------------------------------------------

#: A Mac title bar: the station's client area starts this far below its frame.
TITLE_BAR = 28


class Station:
    """The station window as `wm` reports it: a frame at (x, y), a client
    area `width` x `height` under a title bar, on one screen."""

    def __init__(self, x, y, width, height, screen):
        self.x, self.y, self.width, self.height = x, y, width, height
        self.screen = screen

    def winfo_x(self):
        return self.x

    def winfo_y(self):
        return self.y

    def winfo_rootx(self):
        return self.x

    def winfo_rooty(self):
        return self.y + TITLE_BAR

    def winfo_width(self):
        return self.width

    def winfo_height(self):
        return self.height

    def winfo_screenwidth(self):
        return self.screen[0]

    def winfo_screenheight(self):
        return self.screen[1]

    def wm_maxsize(self):
        return self.screen

    def update_idletasks(self):
        pass


class LogWindow(FakeRoot):
    """A Toplevel whose feed asks for `natural` pixels, recording every
    geometry it is given."""

    def __init__(self, natural=(430, 260), position=None):
        super().__init__()
        self.natural = natural
        self.geometries = []
        self.position = position

    def geometry(self, *args):
        if args:
            self.geometries.append(args[0])
            return ""
        if self.position is None:
            return "1x1+0+0"
        return "{}x{}+{}+{}".format(*self.natural, *self.position)

    def update_idletasks(self):
        pass

    def winfo_reqwidth(self):
        return self.natural[0]

    def winfo_reqheight(self):
        return self.natural[1]

    def winfo_x(self):
        return self.position[0]

    def winfo_y(self):
        return self.position[1]

    def winfo_width(self):
        return self.natural[0]

    def winfo_height(self):
        return self.natural[1]


def _outer(geometry):
    """'WxH+X+Y' -> the window's outer rect (x, y, w, h), title bar included."""
    match = re.fullmatch(r"(\d+)x(\d+)\+(-?\d+)\+(-?\d+)", geometry)
    assert match, geometry
    width, height, x, y = (int(part) for part in match.groups())
    return (x, y, width, height + TITLE_BAR)


def _intersects(a, b):
    return (a[0] < b[0] + b[2] and b[0] < a[0] + a[2]
            and a[1] < b[1] + b[3] and b[1] < a[1] + a[3])


def _inside(rect, screen):
    return (rect[0] >= 0 and rect[1] >= 0 and rect[0] + rect[2] <= screen[0]
            and rect[1] + rect[3] <= screen[1])


def _station_view(view, station):
    """Point `view` at `station`: the page is the notebook (tabs and page),
    the tray is the event log between it and the stop bar."""
    top = station.winfo_rooty()
    page = (station.x + 8, top + 40, station.width - 16, station.height - 280)
    tray = (station.x + 8, top + station.height - 230, station.width - 16, 110)
    view.frame.winfo_toplevel = lambda: station
    view.log_window_bounds = lambda: {"page": page, "free": tray}
    return page, tray


@pytest.mark.parametrize("screen", [(1800, 1169), (2560, 1440), (1400, 900)])
def test_the_log_window_never_covers_the_notebook_page(view, screen):
    """UXPM5-1: the window opened at the station's top right, over the
    probe's Stop disc, Fault lamp and Step. Outside the station when the
    screen has room; over the event tray when it has none; never the page."""
    station = Station(60, 60, 1400, 900, screen)
    page, _tray = _station_view(view, station)
    window = LogWindow()
    view._place_log_window(window)
    rect = _outer(window.geometries[-1])
    assert not _intersects(rect, page), (rect, page)
    assert _inside(rect, screen), (rect, screen)


def test_with_no_room_outside_the_log_window_sits_on_the_tray(view):
    station = Station(60, 60, 1400, 900, (1400, 900))
    page, tray = _station_view(view, station)
    window = LogWindow()
    view._place_log_window(window)
    rect = _outer(window.geometries[-1])
    assert _intersects(rect, tray) and not _intersects(rect, page)


def test_the_log_window_opens_beside_the_station_when_there_is_room(view):
    station = Station(60, 60, 1400, 900, (2560, 1440))
    _station_view(view, station)
    window = LogWindow()
    view._place_log_window(window)
    x, y, width, height = _outer(window.geometries[-1])
    assert x >= station.x + station.width, "to the right of the station window"
    assert (width, height - TITLE_BAR) == window.natural, "the feed's own size"


def test_the_log_window_is_sized_to_its_feed_not_a_constant(view):
    station = Station(60, 60, 1400, 900, (2560, 1440))
    _station_view(view, station)
    sizes = set()
    for natural in ((430, 260), (760, 400)):
        window = LogWindow(natural)
        view._place_log_window(window)
        sizes.add(_outer(window.geometries[-1])[2])
    assert sizes == {430, 760}


def test_the_log_window_returns_where_the_operator_dragged_it(view):
    station = Station(60, 60, 1400, 900, (2560, 1440))
    _station_view(view, station)
    element = side_log(view)
    click(view, element)
    entry = view._widgets[id(element)]
    entry["window"].__class__ = LogWindow
    entry["window"].natural, entry["window"].position = (430, 260), (1700, 900)
    entry["window"].geometries = []
    view._close_log_window(element)
    window = LogWindow()
    view._place_log_window(window, element)
    assert window.geometries[-1].endswith("+1700+900")


# ---------------------------------------------------------------------------
# I7: the menu bar survives the log window taking focus
# ---------------------------------------------------------------------------

def test_the_log_window_carries_the_station_menubar(dashboard):
    """UXPM5-6: on Aqua a Toplevel with no menu shows the system defaults,
    so Models and Setup left the menu bar while the log had focus. The log
    window wears the SAME menubar object as the station window."""
    dashboard.open()
    view = dashboard._panels["Demo"]
    click(view, side_log(view))
    window = view._widgets[id(side_log(view))]["window"]
    menubar = dashboard.root.cget("menu")
    assert menubar is not None
    assert window.cget("menu") is menubar


def test_a_rebuilt_menubar_reaches_an_open_log_window(dashboard):
    """The Models menu is rebuilt when a model opens or closes; an open log
    window must not keep the stale one."""
    dashboard.open()
    view = dashboard._panels["Demo"]
    click(view, side_log(view))
    window = view._widgets[id(side_log(view))]["window"]
    before = dashboard.root.cget("menu")
    dashboard._build_menu_bar()
    assert dashboard.root.cget("menu") is not before
    assert window.cget("menu") is dashboard.root.cget("menu")


# ---------------------------------------------------------------------------
# I2: nothing clips at 28 pt - measured in a real Tk build
# ---------------------------------------------------------------------------
#
# The stand-in above cannot see geometry, so these build the real station
# (real Controller, real Setup, a SIM Stepper Probe) in a child process with
# the real tkinter, in a transparent window, and measure what Tk allotted.

_REAL_BUILD = r'''
import json, os, sys
sys.path.insert(0, os.path.join(sys.argv[1], "src"))
POINTS, WIDTH, HEIGHT = int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
from views import base as view_base
from views import theme
theme.set_font_size(POINTS)
from controller.controller import Controller
from controller.setup import Setup, MODEL_TYPES, _key_for
import views.tk as tkv
ctl = Controller(); setup = Setup(ctl)
try:
    dash = tkv.TkDashboard(ctl, setup)
except Exception as exc:        # no display: nothing to measure
    print(json.dumps({"skip": repr(exc)})); sys.exit(0)
root = dash.root
# Mapped but fully transparent: a withdrawn root is never given its real
# size, so its table is laid out at its request and measures nothing.
root.attributes("-alpha", 0.0)
dash._add_setup_panel()
for name in list(MODEL_TYPES):
    key = _key_for(name)
    setup.run(f"set_{key}_enabled", args=(True,))
    setup.run(f"set_{key}_port", args=("SIM",))
root.geometry(f"{WIDTH}x{HEIGHT}+0+0")
setup_view = dash._panels["Setup"]
for _ in range(3):
    setup_view._refresh(); root.update_idletasks(); root.update()

def rect(w):
    return (w.winfo_rootx(), w.winfo_rooty(), w.winfo_width(), w.winfo_height())

def inside(a, b):
    return (a[0] >= b[0] and a[1] >= b[1] and a[0] + a[2] <= b[0] + b[2]
            and a[1] + a[3] <= b[1] + b[3])

out = {"clipped": [], "status": [], "launch": None}
for element in setup_view._elements:
    attr = str(element.get("model_attr", ""))
    if element.get("type") == "readonly" and attr.endswith("_status") and attr != "scan_status":
        entry = setup_view._entry_for(element)
        full, shown = str(entry["var"].get() or ""), entry["widget"].cget("text")
        out["checked"] = out.get("checked", 0) + bool(full)
        if full and shown != full:
            out["status"].append([full, shown])
launch = next(e for e in setup_view._elements
              if e.get("command") == "launch" and e.get("text") == "Launch")
button = setup_view._entry_for(launch)["ring"].outer
seen = rect(button)
view_rect = rect(setup_view.frame)
in_body = str(button).startswith(str(setup_view._body))
out["launch"] = {"rect": seen, "panel": view_rect,
                 "visible": inside(seen, view_rect) and (
                     not in_body or inside(seen, rect(setup_view._canvas)))}

el = next(e for e in setup_view._elements if e.get("command") == "launch"
          and e.get("text") == "Launch")
setup_view._run(el)
for name in ctl.model_names:
    dash._add_panel(name)
for _ in range(3):
    root.update_idletasks(); root.update()

def whole(view, tiers, key):
    """Every command of `view` in `tiers` is its full width inside the sheet."""
    area = rect(view._canvas)
    for element in view._elements:
        entry = view._entry_for(element)
        ring = entry.get("ring")
        if ring is None or element.get("type") not in ("button", "toggle", "log_stream"):
            continue
        if (entry.get("tier") or 1) not in tiers:
            continue
        box = ring.outer
        got, want = rect(box), box.winfo_reqwidth()
        if got[2] < want or got[0] < area[0] or got[0] + got[2] > area[0] + area[2]:
            out[key].append([entry["widget"].cget("text"), got, want, area])

# E: every tier open, the probe's commands - tier 3's "Gamepad log..." too.
dash.show_model("Stepper Probe")
probe = dash._panels["Stepper Probe"]
probe.set_disclosure(2, True); probe.set_disclosure(3, True)
for _ in range(3):
    root.update_idletasks(); root.update()
whole(probe, (1, 2, 3), "clipped")
# E: Red Percent's details open - the stop still whole in the window, and
# its tier-1 controls whole on the sheet.
out["red"] = []
dash.show_model("Red Percent")
red = dash._panels["Red Percent"]
red.set_disclosure(2, True)
for _ in range(3):
    root.update_idletasks(); root.update()
whole(red, (1,), "red")
window = (root.winfo_rootx(), root.winfo_rooty(), root.winfo_width(), root.winfo_height())
disc = rect(dash._stop_button)
out["stop"] = {"disc": disc, "window": window, "whole": inside(disc, window)
               and disc[2] >= dash._stop.size and disc[3] >= dash._stop.size}
dash.close()
print(json.dumps(out))
'''


def _real_build(points, width, height):
    import json
    import subprocess
    import sys
    tree = os.path.dirname(os.path.dirname(os.path.abspath(tkmod.__file__)))
    tree = os.path.dirname(tree)
    # After a crashed Python, AppKit opens a modal "reopen windows?" alert
    # at the next launch and Tk waits on it forever; this argument (read by
    # NSUserDefaults on a Mac, ignored elsewhere) skips it.
    done = subprocess.run([sys.executable, "-c", _REAL_BUILD, tree, str(points),
                           str(width), str(height),
                           "-ApplePersistenceIgnoreState", "YES"],
                          capture_output=True, text=True, timeout=120)
    lines = [line for line in done.stdout.splitlines() if line.startswith("{")]
    assert done.returncode == 0 and lines, done.stderr[-2000:]
    result = json.loads(lines[-1])
    if "skip" in result:
        pytest.skip(f"no display for a real Tk build: {result['skip']}")
    return result


@pytest.mark.parametrize("points, width, height", [
    (12, 1400, 900), (12, 900, 900), (28, 1400, 900), (28, 900, 900)])
def test_every_probe_command_is_whole_inside_its_panel(points, width, height):
    """UXPM5-2 / H2 IMP-3: the action row did not wrap, so "Gamepad log…"
    showed 47 of 109 px at 12 pt and was off the panel at 28 pt."""
    clipped = _real_build(points, width, height)["clipped"]
    assert clipped == [], clipped


@pytest.mark.parametrize("points, width, height", [(12, 900, 900), (28, 900, 900),
                                                   (28, 1400, 900)])
def test_the_stop_is_whole_with_red_percents_details_open(points, width, height):
    """E: Red Percent's Details well is the tallest thing on the sheet; with
    it open, at 900x900 and at 28 pt, the stop disc is still whole in the
    window (it is in the rail, which never scrolls) and no tier-1 control of
    Red Percent is cut."""
    result = _real_build(points, width, height)
    assert result["stop"]["whole"], result["stop"]
    assert result["red"] == [], result["red"]


def test_setup_at_28_pt_keeps_its_status_words_and_its_launch_row():
    """UXPM5-5: every Status word elided to "…" and the Launch row was below
    the scroll viewport at 28 pt."""
    result = _real_build(28, 1400, 900)
    assert result.get("checked", 0) >= 3, "the ticked rows carry a status"
    assert result["status"] == [], result["status"]
    assert result["launch"]["visible"], result["launch"]


def test_the_commit_row_is_pinned_outside_the_scroll_area():
    """The panel's last row, a bar holding a `go` command (Setup's Launch),
    is pinned under the scrolling body so no font size scrolls it away."""
    class CommitPanel(RowPanel):
        @property
        def schema(self):
            base = RowPanel.schema.fget(self)
            rows = [section for section in base["sections"]
                    if section.get("layout") == "row"]
            commit = sch.section("Launch", sch.button("Go", "launch", role="go"),
                                 layout="row")
            return sch.schema(*rows, commit)

    built = tkmod.TkPanelView(FakeWidget(), FakeController(Rows=CommitPanel()), "Rows")
    go = built._widgets[id(element_of(built, "button", "Go"))]["ring"].outer

    def ancestors(widget):
        while widget is not None:
            yield widget
            widget = widget.master

    assert built._body not in list(ancestors(go)), "not on the scrolling canvas"
    assert built._pinned in list(ancestors(go)) and built._pinned.is_packed
    port = cell_widget(built, built._elements[STEPPER_PORT])
    assert built._body in list(ancestors(port)), "the rows still scroll"
    built.close()


# ---------------------------------------------------------------------------
# E (2026-09-25): the Bench sheet, tiered
# ---------------------------------------------------------------------------

class TieredPanel(Panel):
    """A model's shape on the Bench sheet: X/Y/Z readings, a speed with a
    slider and a Step that carries it in tier 1; a step size in tier 2
    ("Configure"); diagnostics in tier 3 with a plot; a quiet status."""
    NAME = "Tiered"
    PARAMS = {
        "speed": Param("speed", "int", default=400, minimum=1, maximum=1000,
                       label="Speed"),
        "step_size": Param("step_size", "int", default=5, minimum=1, maximum=50,
                           label="Step size"),
    }

    def __init__(self):
        super().__init__()
        self.x, self.y, self.z = 10, -3, 0
        self.link = "Connected"
        self.age = 0.1
        self.steps = []
        self.series_reads = 0

    @property
    def mode_name(self):
        return "idle"

    @property
    def schema(self):
        return sch.schema(
            sch.section("Position",
                        sch.readonly("X:", "x", rail=True, unit="steps"),
                        sch.readonly("Y:", "y", rail=True, unit="steps"),
                        sch.readonly("Z:", "z", rail=True, unit="steps")),
            sch.section("Speeds",
                        sch.entry("Speed:", "speed", self.PARAMS["speed"],
                                  slider=(1, 1000)),
                        sch.readonly("Link:", "link"),
                        sch.button("Step", "step", inputs=("speed", "step_size"),
                                   role="go")),
            sch.section("Configuration",
                        sch.entry("Step size:", "step_size", self.PARAMS["step_size"]),
                        tier=2, disclosure="Configure"),
            sch.section("Diagnostics",
                        sch.readonly("Position age (s):", "age"),
                        sch.readonly("Temperature:", "age", unit="\u00b0C"),
                        sch.plot("Trend", "series"),
                        tier=3, disclosure="Diagnostics"),
        )

    def step(self):
        self.steps.append((self.speed, self.step_size))
        return "stepped"

    def series(self):
        self.series_reads += 1
        return {"x": [0, 1], "y": [1.0, 2.0]}


@pytest.fixture
def tiered():
    tkmod._DISCLOSED.clear()
    panel = TieredPanel()
    built = tkmod.TkPanelView(FakeWidget(), FakeController(Tiered=panel), "Tiered")
    yield built, panel
    built.close()
    tkmod._DISCLOSED.clear()


def _mapped(view, frame):
    """Packed and not forgotten since: what the stand-in knows of mapping."""
    return frame.is_packed


def test_tier_two_is_not_mapped_until_its_disclosure_is_pressed(tiered):
    view, _panel = tiered
    well, opener = view._well, view._disclosures[2]
    assert not _mapped(view, well), "tier 2 is one press away"
    # Signature: the chevron is a glyph on the disclosure key, not a
    # character in the words; the key is up, the glyph points right.
    assert opener.widget.cget("text") == "Configure"
    assert not opener.is_open
    size = element_of(view, "entry", "Step size:")
    assert view._widgets[id(size)]["tier"] == 2, "built all the same, in the well"
    opener.widget.fire("<Button-1>")
    assert _mapped(view, well) and view.is_disclosed(2)
    assert opener.widget.cget("text") == "Configure" and opener.is_open
    opener.widget.fire("<Return>")
    assert not _mapped(view, well) and not view.is_disclosed(2)


def test_the_open_tiers_are_remembered_per_model_for_the_session(tiered):
    view, panel = tiered
    view.set_disclosure(2, True)
    view.set_disclosure(3, True)
    view.close()
    again = tkmod.TkPanelView(FakeWidget(), FakeController(Tiered=panel), "Tiered")
    assert _mapped(again, again._well) and _mapped(again, again._diagnostics)
    other = tkmod.TkPanelView(FakeWidget(), FakeController(Other=TieredPanel()),
                              "Other")
    assert not _mapped(other, other._well), "per model, not for every model"
    again.close()
    other.close()


def test_tier_three_sits_inside_tier_two_in_a_deep_pocket(tiered):
    """Signature: tier 3 is a DEEP pocket sunk in the tray, under a 1 px
    top line (it was a strip with a 2 px muted rule down its left)."""
    view, _panel = tiered
    holder = view._diagnostics
    assert holder.master is view._well
    assert view._disclosures[3].frame.master is view._well
    line, pocket = holder.children[0], holder.children[1]
    assert line.cget("background") == tkmod.TRAY_LINE and line.cget("height") == 1
    assert pocket.cget("background") == theme.DEEP
    assert view._tiers[3].master is pocket
    view.set_disclosure(3, True)
    assert not view.is_disclosed(3), "tier 3 shows only inside an open tier 2"
    view.set_disclosure(2, True)
    assert view.is_disclosed(3) and _mapped(view, holder)


def test_a_closed_tier_is_not_polled(tiered):
    view, panel = tiered
    before = panel.series_reads
    view._refresh()
    assert panel.series_reads == before, "the trend is on demand"
    view.set_disclosure(2, True)
    view.set_disclosure(3, True)
    assert panel.series_reads > before


def test_every_entry_travels_whatever_tier_is_shown(tiered):
    """A tier is where a control is drawn, never whether its value travels."""
    view, panel = tiered
    click(view, element_of(view, "button", "Step"))
    assert panel.steps == [(400, 5)]


def test_the_slider_sits_beside_its_entry_never_instead(tiered):
    view, _panel = tiered
    entry = view._widgets[id(element_of(view, "entry", "Speed:"))]
    scale, field = entry["scale"], entry["widget"]
    assert isinstance(scale, FakeScale)
    track = scale.master.master                       # the slider's focus ring
    assert track.master is entry["ring"].outer.master, "one line: slider, then entry"
    line = track.master
    assert line.children.index(track) < line.children.index(entry["ring"].outer)
    assert (scale.cget("from_"), scale.cget("to")) == (1, 1000)


def test_the_slider_and_the_entry_write_each_other(tiered):
    view, panel = tiered
    element = element_of(view, "entry", "Speed:")
    entry = view._widgets[id(element)]
    scale, var = entry["scale"], entry["var"]
    view._refresh()
    assert scale.value == 400, "a refresh moves the slider"
    scale.cget("command")("612.4")                   # the operator drags
    assert var.get() == "612", "an int field gets a whole number"
    var.set("250")                                    # the operator types
    entry["widget"].fire("<KeyRelease>")
    assert scale.value == 250
    var.set("five")
    entry["widget"].fire("<KeyRelease>")
    assert scale.value == 250, "not a number: the slider stays"
    var.set("5000")
    view._sync_slider(element)
    assert scale.value == 1000, "clamped to the slider's travel"


def test_the_command_carries_the_entry_the_slider_wrote(tiered):
    view, panel = tiered
    entry = view._widgets[id(element_of(view, "entry", "Speed:"))]
    entry["scale"].cget("command")("730")
    click(view, element_of(view, "button", "Step"))
    assert panel.steps == [(730, 5)]


def test_releasing_the_slider_commits_through_the_controller(tiered):
    view, panel = tiered
    entry = view._widgets[id(element_of(view, "entry", "Speed:"))]
    entry["scale"].cget("command")("820")
    entry["scale"].fire("<ButtonRelease-1>")
    assert panel.speed == 820


def test_a_disabled_slider_is_muted_with_its_entry(tiered):
    tiered_view, _panel = tiered
    element = element_of(tiered_view, "entry", "Speed:")
    tiered_view._set_enabled(element, False)
    assert tiered_view._widgets[id(element)]["scale"].states[-1] == ["disabled"]
    tiered_view._set_enabled(element, True)
    assert tiered_view._widgets[id(element)]["scale"].states[-1] == ["!disabled"]


def test_quiet_values_are_not_drawn_in_tier_one(tiered):
    """Status by exception: "Connected" in tier 1 takes no place; anything
    else is drawn; a tier-3 readout is drawn whatever it says."""
    view, panel = tiered
    link = view._widgets[id(element_of(view, "readonly", "Link:"))]
    flow = view._flows[id(link["strip"])]
    view._refresh()
    assert id(link["box"]) in flow["hidden"]
    for quiet in ("Idle", "No", "None", "--", "Not recording", ""):
        panel.link = quiet
        view._refresh()
        assert id(link["box"]) in flow["hidden"], quiet
    panel.link = "Lost"
    view._refresh()
    assert id(link["box"]) not in flow["hidden"]
    age = view._widgets[id(element_of(view, "readonly", "Position age (s):"))]
    panel.age = "None"
    view._refresh()
    assert not age.get("is_quiet")


def test_a_reading_is_ink_at_rest_and_trace_only_while_it_changes(tiered,
                                                                   monkeypatch):
    view, panel = tiered
    x = widget_of(view, element_of(view, "readonly", "X:"))
    assert x.cget("foreground") == theme.TEXT
    clock = [100.0]
    monkeypatch.setattr(tkmod.time, "monotonic", lambda: clock[0])
    panel.x = 11
    view._refresh()
    assert x.cget("foreground") == theme.TRACE
    clock[0] += tkmod.CHANGING_S + 0.1
    view._refresh()
    assert x.cget("foreground") == theme.TEXT


def test_axis_readings_say_their_caption_once_with_the_letters_inline(tiered):
    view, _panel = tiered
    x = view._widgets[id(element_of(view, "readonly", "X:"))]
    assert x["caption"] is None, "no caption per axis"
    # The unit the axes share is said once, in the caption (core d3f343a).
    assert "Position, steps" in [label.cget("text") for label in view._section_titles]
    words = [child.cget("text") for child in x["widget"].master.children]
    assert words[0] == "X" and "steps" not in words


def test_a_readouts_unit_sits_small_and_muted_after_its_value(tiered):
    view, _panel = tiered
    entry = view._widgets[id(element_of(view, "readonly", "Temperature:"))]
    line = entry["widget"].master.children
    unit = line[line.index(entry["widget"]) + 1]
    assert unit.cget("text") == "\u00b0C" and unit.cget("foreground") == theme.MUTED
    assert unit.cget("font") == tkmod._caption_font()
    age = view._widgets[id(element_of(view, "readonly", "Position age (s):"))]
    assert age["caption"].cget("text") == "Position age", "the unit leaves the caption"


def test_the_opened_models_axes_are_focal_and_a_closed_ones_compact(controller):
    panel = TieredPanel()
    sheet = type("Sheet", (), {"canvas": FakeCanvas()})()
    built = tkmod.TkPanelView(FakeWidget(), FakeController(T=panel), "T", sheet=sheet)
    x = widget_of(built, element_of(built, "readonly", "X:"))
    assert x.cget("font") == tkmod._reading_font("compact")
    built.set_prominence(True)
    assert x.cget("font") == tkmod._reading_font("focal")
    assert abs(tkmod._reading_font("focal")[1]) > abs(tkmod._reading_font("compact")[1])
    built.close()


def test_an_unconfirmed_stop_is_marked_at_its_own_entry(dashboard, controller,
                                                        monkeypatch):
    """E: a model whose stop did not confirm gets a signal head rule and the
    words "Stop not confirmed. Treat as live." at its entry, for as long as
    the latch it describes."""
    dashboard.open()
    # Updated for L1: the mark is the model's own `stop_confirmed`, read on
    # the entry's refresh, not the result of the press the view happened to
    # see.
    controller.panels["Demo"].stop_confirmed = False
    dashboard._stop_button.fire("<Button-1>")
    view = dashboard._panels["Demo"]
    view._refresh()
    assert view._rule.cget("background") == theme.SIGNAL
    assert view._mark_row.is_packed
    assert view._mark_text.cget("text") == "Stop not confirmed. Treat as live."
    controller.is_estopped = False
    controller.panels["Demo"].stop_confirmed = None
    dashboard._sync_stop_button()
    view._refresh()
    assert view._rule.cget("background") == theme.RULE_STRONG
    assert not view._mark_row.is_packed


def test_latched_the_rail_and_the_sheet_say_so(dashboard, controller):
    dashboard.open()
    dashboard._sync_stop_button()
    assert not dashboard._latched_row.is_packed and not dashboard._headline.is_packed
    controller.is_estopped = True
    dashboard._sync_stop_button()
    assert dashboard._latched_line.cget("text") == "Stopped: every model latched"
    assert dashboard._latched_row.is_packed and dashboard._headline.is_packed


def test_the_rail_lists_every_model_and_a_press_leads_the_sheet_with_it(
        tk_harness, setup_panel):
    controller = FakeController(**{"Stepper Probe": DemoPanel(), "Rotator": DemoPanel(),
                                   "Red Percent": DemoPanel()})
    built = tkmod.TkDashboard(controller, setup_panel)
    built.open()
    assert list(built._rail_items) == ["Stepper Probe", "Rotator", "Red Percent"]
    built._rail_items["Red Percent"][1].fire("<Button-1>")
    assert built._opened == "Red Percent"
    assert built._is_setup_collapsed, "Setup gives way to the sheet"
    first = built._sheet_rows[0]
    assert built._panels["Red Percent"].frame.grid_info["in_"] is first
    assert built._panels["Red Percent"]._is_opened
    assert not built._panels["Rotator"]._is_opened
    label = built._rail_items["Red Percent"][1]
    assert label.cget("background") == theme.SURFACE, "the opened one is lit"  # Signature
    assert built._rail_items["Rotator"][1].cget("background") == tkmod.RAIL_FACE  # Signature
    built.close()


def test_the_sheet_stacks_entries_in_one_column_under_1000_px(tk_harness,
                                                             setup_panel):
    names = ["A", "B", "C", "D", "E", "F"]
    controller = FakeController(**{name: DemoPanel() for name in names})
    built = tkmod.TkDashboard(controller, setup_panel)
    built.open()
    built.root.winfo_width = lambda: 1400
    built._lay_out_sheet(force=True)
    # Updated for K4: the sheet opens on the overview - no model leads it;
    # every model in rows of three.
    assert built._opened is None
    assert len({built._panels[n].frame.grid_info["in_"] for n in names[0:3]}) == 1
    assert len(built._sheet_rows) == 2, "3, then 3"
    built.root.winfo_width = lambda: 900
    built._lay_out_sheet(force=True)
    assert len(built._sheet_rows) == 6, "one column"
    built.close()


def test_the_rail_says_simulation_while_every_link_is_simulated(dashboard,
                                                                 controller):
    dashboard.open()
    view = dashboard._panels["Demo"]
    view._last_state = {"devices": {"SerialPort": "simulated", "Gamepad": "bound"}}
    dashboard._on_refresh_tick()
    assert dashboard._sim_line.cget("text") == "Simulation, no hardware attached"
    view._last_state = {"devices": {"SerialPort": "verified"}}
    dashboard._on_refresh_tick()
    assert dashboard._sim_line.cget("text") == ""


def test_mod5_the_rail_counts_the_hardware_links_the_model_declares(dashboard,
                                                                    controller):
    """MOD-5 / CON-6: a verified link of a class the view has never heard of
    (PiezoLink) is hardware, because the model's state says so."""
    dashboard.open()
    view = dashboard._panels["Demo"]
    view._last_state = {"devices": {"SerialPort": "simulated", "PiezoLink": "verified"},
                        "hardware_devices": ["SerialPort", "PiezoLink"]}
    dashboard._on_refresh_tick()
    assert dashboard._sim_line.cget("text") == ""
    view._last_state = {"devices": {"SerialPort": "simulated", "PiezoLink": "simulated"},
                        "hardware_devices": ["SerialPort", "PiezoLink"]}
    dashboard._on_refresh_tick()
    assert dashboard._sim_line.cget("text") == "Simulation, no hardware attached"


def test_mod5_an_empty_hardware_list_is_no_links_not_the_old_class_names(dashboard,
                                                                         controller):
    dashboard.open()
    view = dashboard._panels["Demo"]
    view._last_state = {"devices": {"SerialPort": "simulated"}, "hardware_devices": []}
    dashboard._on_refresh_tick()
    assert dashboard._sim_line.cget("text") == ""


def test_mod5_a_state_without_the_list_falls_back_to_the_class_names(dashboard,
                                                                     controller):
    """Until every model publishes `hardware_devices`, a state without it
    reads as before: SerialPort and SMC100 are the links."""
    dashboard.open()
    view = dashboard._panels["Demo"]
    view._last_state = {"devices": {"SerialPort": "simulated", "PiezoLink": "verified"}}
    dashboard._on_refresh_tick()
    assert dashboard._sim_line.cget("text") == "Simulation, no hardware attached"


def test_quit_asks_first_and_then_closes(dashboard, controller, tk_harness):
    dashboard.open()
    tk_harness.confirm_answer = False
    dashboard._quit_press.widget.fire("<Button-1>")
    assert not controller.is_closed
    tk_harness.confirm_answer = True
    dashboard._quit_press.widget.fire("<Button-1>")
    assert controller.is_closed


def test_no_colour_is_made_in_the_view():
    """E: every colour is a theme member; the view mixes none of its own
    (the old view derived its input border and notice tint from the dark
    tokens)."""
    code = _executable_source(tkmod.__file__)
    assert "mix(" not in code
    for name in re.findall(r"theme\.([A-Z_]+)\b", code):
        assert hasattr(theme, name), name


# ---------------------------------------------------------------------------
# K (2026-09-26): the disclosure where it opens (K3); Overview and the device
# page (K4)
# ---------------------------------------------------------------------------

def _ancestors(widget):
    while widget is not None:
        yield widget
        widget = widget.master


def test_k3_the_tier_two_disclosure_is_at_the_foot_of_the_body_not_the_head(tiered):
    """K3: the press and what it reveals were a screen apart - the
    disclosure sat in the head, the well under the whole body. It is the
    last thing in the tier-1 body, left-aligned, and the well follows it
    with no gap; it is reached after the tier-1 controls."""
    view, _panel = tiered
    opener = view._disclosures[2]
    assert view._head not in list(_ancestors(opener.frame)), "not in the head"
    assert opener.frame.master is view._body
    order = view._body.children
    tier_one = view._tiers[1]
    assert order.index(tier_one) < order.index(opener.frame) < order.index(view._well), \
        "tier 1, then the disclosure, then its well (and the focus order with it)"
    packed = [kwargs for widget, kwargs in PACK_ORDER if widget is opener.frame]
    assert packed and packed[-1].get("anchor") == "w", "left-aligned"
    view.set_disclosure(2, True)
    packed = [kwargs for widget, kwargs in PACK_ORDER if widget is view._well]
    assert packed[-1].get("after") is opener.frame, "directly beneath its press"
    pady = packed[-1].get("pady") or (0, 0)
    assert pady[0] == 0, "no gap between the press and the well"


def test_k3_the_disclosure_says_the_schemas_words(tiered):
    view, panel = tiered
    words = next(section["disclosure"] for section in panel.schema["sections"]
                 if section.get("tier") == 2)
    # Signature: the words alone; the chevron is the key's glyph.
    assert view._disclosures[2].widget.cget("text") == words


def _two_page_dashboard(setup_panel, *names):
    tkmod._DISCLOSED.clear()
    controller = FakeController(**{name: TieredPanel() for name in names})
    built = tkmod.TkDashboard(controller, setup_panel)
    built.open()
    # A launch: the models arrive on a running controller (as Setup's Launch).
    for name in names:
        controller.remove(name)
    SCHEDULER.pump()
    for name in names:
        controller.reopen(name)
    SCHEDULER.pump()
    return built, controller


def _shown(built):
    """The models whose entries are on the sheet now."""
    rows = set(built._sheet_rows)
    return [name for name, view in built._panels.items()
            if name != built.SETUP_TAB and (view.frame.grid_info or {}).get("in_") in rows]


K_NAMES = ("Stepper Probe", "DC Probe", "Rotator", "Red Percent")


def test_k4_the_rails_first_item_is_overview_and_it_is_current_at_launch(
        setup_panel):
    built, _controller = _two_page_dashboard(setup_panel, *K_NAMES)
    ring, label = built._overview_item
    live = [child for child in built._model_list.children if not child.is_destroyed]
    assert live[0] is ring.outer, "Overview first, above the models"
    assert label.cget("text") == "Overview"
    assert built._opened is None, "the overview is the page shown"
    assert built._is_setup_collapsed
    assert label.cget("background") == theme.SURFACE, "and the rail says so"  # Signature
    for name in K_NAMES:
        assert built._rail_items[name][1].cget("background") == tkmod.RAIL_FACE  # Signature
    tkmod._DISCLOSED.clear()
    built.close()


def test_k4_the_overview_shows_every_model_with_no_well_and_no_disclosure(
        setup_panel):
    tkmod._DISCLOSED[("Rotator", 2)] = True      # opened earlier this session
    tkmod._DISCLOSED[("Rotator", 3)] = True
    controller = FakeController(**{name: TieredPanel() for name in K_NAMES})
    built = tkmod.TkDashboard(controller, setup_panel)
    built.open()
    built.show_overview()
    assert sorted(_shown(built)) == sorted(K_NAMES)
    for name in K_NAMES:
        view = built._panels[name]
        # Updated for L5: on the sheet the well sits in a scroller of its
        # own; that holder is what is shown or not.
        assert not view._well_holder.is_packed, f"{name}: no well on the overview"
        assert not view._disclosures[2].frame.is_packed, f"{name}: no disclosure"
        assert not view.is_disclosed(2)
        assert view._open_label.is_packed
        assert view._open_label.cget("text") == "\u25b8 Open"
        assert view._open_tip.text == f"Open {name}"
        assert view._head.cget("takefocus") == 1, "the head is the press"
    rotator = controller.panels["Rotator"]
    before = rotator.series_reads
    built._panels["Rotator"]._refresh()
    assert rotator.series_reads == before, "what is not shown is not polled"
    assert tkmod._DISCLOSED[("Rotator", 2)], "the memory is untouched"
    tkmod._DISCLOSED.clear()
    built.close()


@pytest.mark.parametrize("how", ["rail", "head", "title", "open", "return", "space"])
def test_k4_a_press_shows_that_model_alone_with_its_disclosures(setup_panel, how):
    built, _controller = _two_page_dashboard(setup_panel, *K_NAMES)
    view = built._panels["Rotator"]
    press = {"rail": (built._rail_items["Rotator"][1], "<Button-1>"),
             "head": (view._head, "<Button-1>"),
             "title": (view._title, "<Button-1>"),
             "open": (view._open_label, "<Button-1>"),
             "return": (view._head, "<Return>"),
             "space": (view._head, "<space>")}[how]
    press[0].fire(press[1])
    assert built._opened == "Rotator"
    assert _shown(built) == ["Rotator"], "one model alone"
    assert len(built._sheet_rows) == 1
    assert view._is_opened, "focal"
    assert view._disclosures[2].frame.is_packed
    assert not view._open_label.is_packed, "no Open on its own page"
    assert view._head.cget("takefocus") == 0
    assert built._rail_items["Rotator"][1].cget("background") == theme.SURFACE  # Signature
    assert built._overview_item[1].cget("background") == tkmod.RAIL_FACE  # Signature
    tkmod._DISCLOSED.clear()
    built.close()


def test_k4_the_head_wears_the_focus_ring_while_keyboard_focused(setup_panel):
    built, _controller = _two_page_dashboard(setup_panel, *K_NAMES)
    view = built._panels["DC Probe"]
    view._head.fire("<FocusIn>")
    assert view._head_ring.outer.cget("background") == tkmod.FOCUS_INK
    view._head.fire("<FocusOut>")
    assert view._head_ring.outer.cget("background") != tkmod.FOCUS_INK
    tkmod._DISCLOSED.clear()
    built.close()


def test_k4_pressing_overview_returns(setup_panel):
    built, _controller = _two_page_dashboard(setup_panel, *K_NAMES)
    built.show_model("DC Probe")
    built._overview_item[1].fire("<Button-1>")
    assert built._opened is None
    assert sorted(_shown(built)) == sorted(K_NAMES)
    assert not any(built._panels[n]._is_opened for n in K_NAMES), "all compact"
    built.show_model("DC Probe")
    built._overview_item[1].fire("<Return>")
    assert built._opened is None
    tkmod._DISCLOSED.clear()
    built.close()


def test_k4_closing_the_shown_device_returns_to_the_overview(setup_panel):
    built, controller = _two_page_dashboard(setup_panel, *K_NAMES)
    built.show_model("Red Percent")
    label = built._rail_items["Red Percent"][1]
    label.fire(tkmod._close_tab_button(label))
    SCHEDULER.pump()
    assert "Red Percent" in controller.removed
    assert built._opened is None
    assert sorted(_shown(built)) == sorted(set(K_NAMES) - {"Red Percent"})
    assert built._overview_item[1].cget("background") == theme.SURFACE  # Signature
    tkmod._DISCLOSED.clear()
    built.close()


def test_k4_the_open_tiers_survive_overview_device_overview_device(setup_panel):
    built, controller = _two_page_dashboard(setup_panel, *K_NAMES)
    built.show_model("Stepper Probe")
    probe = built._panels["Stepper Probe"]
    probe.set_disclosure(2, True)
    probe.set_disclosure(3, True)
    # Updated for L5: the well's holder (its own scroller) is what is packed.
    assert probe._well_holder.is_packed and probe.is_disclosed(3)
    built.show_overview()
    assert not probe._well_holder.is_packed and not probe.is_disclosed(2)
    built.show_model("Stepper Probe")
    assert probe._well_holder.is_packed and probe._diagnostics.is_packed
    assert probe.is_disclosed(3)
    assert probe._disclosures[2].is_open    # Signature: the key is down
    tkmod._DISCLOSED.clear()
    built.close()


def test_k4_the_stop_and_the_latched_headline_are_the_same_on_both_pages(
        setup_panel):
    built, controller = _two_page_dashboard(setup_panel, *K_NAMES)
    controller.is_estopped = True
    built._sync_stop_button()
    assert built._headline.is_packed and built._stop_button.is_packed
    built.show_model("Rotator")
    assert built._headline.is_packed and built._stop_button.is_packed
    assert built.chord_text == "Stop: Ctrl+."
    tkmod._DISCLOSED.clear()
    built.close()


@pytest.mark.parametrize("points, width, height", [(12, 1056, 900), (12, 1400, 900)])
def test_k4_the_overview_settles_where_a_flow_wraps_at_its_columns_width(
        points, width, height):
    """K4: on the overview, Temperature Controller at 1056 px and Red Percent
    at 1400 px wrap at exactly their column's width; the grid then moved the
    column's odd pixel and the flow unwrapped, and the window never settled
    (a real build hung). `_real_build` lays the overview out and returns."""
    result = _real_build(points, width, height)
    assert result["stop"]["whole"], result["stop"]


# ---------------------------------------------------------------------------
# Tier L (audit round 7)
# ---------------------------------------------------------------------------

L_NAMES = ("Stepper Probe", "DC Probe", "Rotator")


def _stop_dashboard(setup_panel):
    controller = FakeController(**{name: DemoPanel() for name in L_NAMES})
    built = tkmod.TkDashboard(controller, setup_panel)
    built.open()
    return built, controller


def _faces(built):
    return [item[2]["text"] for item in built._stop_button.items if item[0] == "text"]


def test_l1_one_models_own_stop_leaves_the_disc_a_working_stop(tk_harness,
                                                                setup_panel):
    """S1, IMP7-1/2: one model latched from its own switch is a partial
    stop. The disc still reads Stop and a press stops the rest; there is no
    "every model" headline; the rail names the one that is stopped."""
    built, controller = _stop_dashboard(setup_panel)
    controller.is_estopped = True
    controller.stop = {"latched": ["Rotator"], "unconfirmed": [], "every": False}
    built._sync_stop_button()
    assert _faces(built) == ["Stop"]
    assert not built._headline.is_packed
    assert built._latched_row.is_packed
    assert built._latched_line.cget("text") == "Stopped: Rotator"
    assert built._stop.tooltip.text.startswith(tkmod.STOP_HINT)
    built._stop_button.fire("<Button-1>")
    assert controller.estop_calls == 1 and controller.clear_calls == 0
    built.close()


def test_l1_the_chord_stops_during_a_partial_stop(tk_harness, setup_panel):
    """Ctrl+. always stops and never clears: a partial latch is not "already
    stopped"."""
    built, controller = _stop_dashboard(setup_panel)
    controller.is_estopped = True
    controller.stop = {"latched": ["Rotator"], "unconfirmed": [], "every": False}
    ALL_BINDINGS["<Control-period>"](FakeEvent())
    assert controller.estop_calls == 1 and controller.clear_calls == 0
    ALL_BINDINGS["<Control-period>"](FakeEvent())     # every model latched now
    assert controller.clear_calls == 0, "the chord never clears"
    built.close()


def test_l1_a_global_stop_one_model_did_not_confirm(tk_harness, setup_panel):
    """TK7-1: the loudest words must not contradict "treat as live"."""
    built, controller = _stop_dashboard(setup_panel)
    controller.is_estopped = True
    controller.stop = {"latched": list(L_NAMES), "unconfirmed": ["Rotator"],
                       "every": True}
    built._sync_stop_button()
    assert _faces(built) == ["Clear"]
    headline, subline = built._headline_lines
    assert built._headline.is_packed
    assert headline.cget("text") == "Stopped. Rotator did not confirm."
    assert subline.cget("text") == ("Treat it as live until you have checked it "
                                    "by hand.")
    assert built._latched_line.cget("text") == "Stopped: Rotator did not confirm"
    assert built._stop.tooltip.text == tkmod.CLEAR_HINT
    built.close()


def test_l1_every_model_stopped_and_confirmed(tk_harness, setup_panel):
    built, controller = _stop_dashboard(setup_panel)
    controller.is_estopped = True
    built._sync_stop_button()
    headline, subline = built._headline_lines
    assert headline.cget("text") == "Every model is stopped."
    assert subline.cget("text") == tkmod.STOPPED_NEXT
    assert built._latched_line.cget("text") == "Stopped: every model latched"
    built.close()


def test_l1_the_words_follow_the_state_as_it_changes(tk_harness, setup_panel):
    """F21 kept: nothing is redrawn while the state holds still, but a change
    in WHICH models are latched is a change."""
    built, controller = _stop_dashboard(setup_panel)
    controller.is_estopped = True
    controller.stop = {"latched": ["Rotator"], "unconfirmed": [], "every": False}
    built._sync_stop_button()
    draws = built._stop.draws
    built._sync_stop_button()
    assert built._stop.draws == draws
    controller.stop = {"latched": ["DC Probe", "Rotator"], "unconfirmed": [],
                       "every": False}
    built._sync_stop_button()
    assert built._latched_line.cget("text") == "Stopped: DC Probe and Rotator"
    controller.stop = None
    controller.is_estopped = False
    built._sync_stop_button()
    assert not built._latched_row.is_packed and not built._headline.is_packed
    built.close()


def test_l1_stop_ctrl_period_is_shown_in_every_state(tk_harness, setup_panel):
    built, controller = _stop_dashboard(setup_panel)
    states = [None, {"latched": ["Rotator"], "unconfirmed": [], "every": False},
              {"latched": list(L_NAMES), "unconfirmed": ["Rotator"], "every": True},
              {"latched": list(L_NAMES), "unconfirmed": [], "every": True}]
    for stop in states:
        controller.stop = stop
        controller.is_estopped = stop is not None
        built._sync_stop_button()
        assert built._stop_hint.is_packed
        assert built.chord_text == "Stop: Ctrl+."
    built.close()


def test_l1_the_rail_marks_each_latched_and_unconfirmed_model(tk_harness,
                                                              setup_panel):
    """A latched model: an ink square and "stopped"; one that did not
    confirm: "did not confirm". Never colour alone.
    Signature: the unconfirmed model's mark is the warning glyph in SIGNAL
    (rule 6) where a signal square was, and its lamp slot turns SIGNAL
    (rule 4); a model that is merely latched keeps its lamp hidden."""
    built, controller = _stop_dashboard(setup_panel)
    controller.is_estopped = True
    controller.stop = {"latched": ["DC Probe", "Rotator"], "unconfirmed": ["Rotator"],
                       "every": False}
    built._sync_stop_button()

    def squares(name):
        canvas, _tip = built._rail_marks[name]
        return [item[2]["fill"] for item in canvas.items if item[0] == "rect"]

    assert squares("Stepper Probe") == []
    assert squares("DC Probe") == [theme.TEXT]
    assert squares("Rotator") == []
    glyph = built._rail_marks["Rotator"][0].items
    assert glyph and {item[2]["fill"] for item in glyph} == {theme.SIGNAL}
    assert {tuple(item[2]["tags"]) for item in glyph} == {("warning",)}
    assert built._rail_lamp_state("Rotator") == "unconfirmed"
    assert built._rail_lamp_state("DC Probe") == "hidden"
    assert built._rail_marks["DC Probe"][1].text == "Stopped"
    assert built._rail_marks["Rotator"][1].text == "Did not confirm the stop"
    assert built._rail_marks["Stepper Probe"][1].text == ""
    mark = built._rail_marks["Rotator"][0]
    packed = [kw for widget, kw in PACK_ORDER if widget is mark][-1]
    assert packed["side"] == "left", "before the name"
    built.show_model("Rotator")
    assert mark.cget("background") == built._rail_items["Rotator"][1].cget("background")
    controller.stop = None
    controller.is_estopped = False
    built._sync_stop_button()
    assert squares("Rotator") == [] and squares("DC Probe") == []
    built.close()


def test_l1_the_entry_mark_comes_from_the_models_stop_confirmed(tk_harness,
                                                                 setup_panel):
    """The per-entry "Stop not confirmed. Treat as live." is driven by the
    model's own `stop_confirmed`, not by which press the view saw: a stop
    from the gamepad or a model's own switch is marked too."""
    built, controller = _stop_dashboard(setup_panel)
    view = built._panels["Rotator"]
    controller.panels["Rotator"].stop_confirmed = False
    view._refresh()
    assert view._mark_row.is_packed
    assert view._rule.cget("background") == theme.SIGNAL
    controller.panels["Rotator"].stop_confirmed = None
    view._refresh()
    assert not view._mark_row.is_packed
    assert not built._panels["DC Probe"]._mark_row.is_packed
    built.close()


def _stop_not_confirmed(needs_ack=True):
    return Event(7, "error", "Controller", "Stop Not Confirmed",
                 "Rotator did not confirm the stop within 2 s. Every model is "
                 "latched; treat it as live until you have checked by hand.",
                 None, needs_ack, 0.0)


def test_l2_the_stop_not_confirmed_line_goes_when_the_latch_opens(tk_harness,
                                                                  setup_panel):
    """TK7-2: after Clear the band and the tray no longer say, in the present
    tense, that a model did not confirm."""
    built, controller = _stop_dashboard(setup_panel)
    controller.is_estopped = True
    built._sync_stop_button()
    built._on_event(_event("error", needs_ack=True))
    built._on_event(_stop_not_confirmed())
    SCHEDULER.pump()
    # Updated (R6): the dialog, not a band, says it.
    assert built._ack_dialog.title_label.cget("text") == "Error-event"
    assert [e.title for e in built._alerts] == ["error-event", "Stop Not Confirmed"]
    assert "did not confirm" in built._latest_text.cget("text")
    controller.is_estopped = False
    built._sync_stop_button()
    assert [event.title for event in built._alerts] == ["error-event"]
    assert "did not confirm" not in built._latest_text.cget("text")
    assert "did not confirm" not in built._event_text.body
    assert "Error-event" in built._latest_text.cget("text"), "the one before it"
    built.close()


def test_l11_band_and_tray_lines_are_sentences_without_a_source(tk_harness,
                                                                 setup_panel):
    """TK7-11: "Error: [Controller] Stop Not Confirmed: ..." was a log line."""
    built, controller = _stop_dashboard(setup_panel)
    built._on_event(_stop_not_confirmed())
    built._on_event(Event(8, "warning", "DC Probe", "Power Down Not Supported",
                          "The DC board has no coil kill.", None, False, 0.0))
    SCHEDULER.pump()
    # Updated (R6): the band is gone; the dialog says the title in sentence
    # case and the message, never the source.
    dialog = built._ack_dialog
    assert dialog.title_label.cget("text") == "Stop not confirmed"
    assert dialog.body.cget("text").startswith("Rotator did not confirm")
    assert "[" not in dialog.body.cget("text")
    assert built._latest_text.cget("text") == (
        "Warning: Power down not supported: The DC board has no coil kill.")
    # One mark in both places, drawn, not a text glyph. Signature: the
    # folded line is led by the warning glyph (rule 6) - ink for a
    # warning, where it was a hollow square.
    marks = built._latest_mark.items
    assert marks and {item[0] for item in marks} == {"line"}
    assert {item[2]["fill"] for item in marks} == {theme.SEVERITY_MARK["warning"]}
    built.close()


def test_l14_the_clear_and_quit_questions_are_titled_with_verb_answers(
        tk_harness, setup_panel):
    built, controller = _stop_dashboard(setup_panel)
    controller.is_estopped = True
    controller.clear_needs_confirm = True
    tk_harness.confirm_answer = False
    built._stop_button.fire("<Button-1>")
    assert tk_harness.words[-1] == {"title": "Clear the stop",
                                    "yes_text": "Clear the stop",
                                    "no_text": "Keep it stopped"}
    assert controller.is_estopped, "declined: still stopped"
    built._quit_press.widget.fire("<Button-1>")
    assert tk_harness.words[-1] == {"title": "Quit", "yes_text": "Quit",
                                    "no_text": "Stay"}
    assert not built._closing, "declined: still running"
    built.close()


def test_l14_the_dialog_wears_its_title_and_its_answers(tk_harness):
    root = FakeRoot()
    dialog = tkmod._ConfirmDialog(root, "Quit the station?", **tkmod.QUIT_DIALOG)
    dialog._build()
    assert dialog.top.titles == ["Quit"]
    assert dialog.yes.widget.cget("text") == "Quit"
    assert dialog.no.widget.cget("text") == "Stay"
    assert Focus.current is dialog.no.widget, "the default keeps things as they are"


def test_l2_the_acknowledgement_stays_required_while_latched(tk_harness,
                                                             setup_panel):
    built, controller = _stop_dashboard(setup_panel)
    controller.is_estopped = True
    built._sync_stop_button()
    built._on_event(_stop_not_confirmed())
    SCHEDULER.pump()
    built._sync_stop_button()
    assert [event.title for event in built._alerts] == ["Stop Not Confirmed"]
    assert built._ack_dialog is not None
    built.close()


def test_l12_the_log_window_says_when_it_is_empty_and_has_a_close(view, panel):
    """TK7-15: a blank box with no way out but Escape."""
    panel.side = []
    element = side_log(view)
    click(view, element)
    entry = view._widgets[id(element)]
    assert entry["feed"].body == "No side input yet."
    assert entry["feed"].cget("foreground") == theme.MUTED
    panel.side = ["gamepad on"]
    view._refresh()
    assert entry["feed"].body == "gamepad on"
    assert entry["feed"].cget("foreground") == theme.TEXT
    closer = entry["closer"]
    assert closer.widget.cget("text") == "Close"
    window = entry["window"]
    closer.widget.fire("<Button-1>")
    assert window.is_destroyed and entry["window"] is None


def test_l12_the_gamepad_log_is_titled_and_empty_as_the_brief_says(view):
    element = {"type": "log_stream", "text": "Gamepad Log:", "detached": True}
    view.name = "Stepper Probe"
    assert view._log_title(element) == "Stepper Probe gamepad log"
    assert view._log_empty_text(element) == "No gamepad input yet."


def test_l13_close_this_model_is_at_the_foot_of_the_well_and_asks(tk_harness,
                                                                  setup_panel):
    built, controller = _stop_dashboard(setup_panel)
    view = built._panels["Rotator"]
    press = view._close_press
    assert press.widget.cget("text") == "Close this model\u2026"
    assert press.ghost, "the quietest control the entry has"
    assert press.frame.master.master is view._well, "in the well's foot"
    tk_harness.confirm_answer = False
    press.widget.fire("<Button-1>")
    assert tk_harness.words[-1]["title"] == "Close Rotator"
    assert tk_harness.words[-1]["no_text"] == "Keep it open"
    assert controller.removed == []
    tk_harness.confirm_answer = True
    press.widget.fire("<Button-1>")
    assert controller.removed == ["Rotator"]
    built.close()


def test_l15_an_empty_plot_is_one_caption_line_with_the_schemas_words(view, panel):
    element = element_of(view, "plot")
    canvas = widget_of(view, element)
    panel.samples = []
    view._refresh()
    assert canvas.cget("height") == view._empty_plot_px()
    assert canvas.cget("height") < 30
    texts = [item[2]["text"] for item in canvas.items if item[0] == "text"]
    assert texts == [element["empty"]] == ["No data yet."]
    panel.samples = [1.0, 2.0, 3.0]
    view._refresh()
    assert canvas.cget("height") == tkmod._design_px(view.PLOT_PX)
    assert [item for item in canvas.items if item[0] == "line"]


def test_l15_an_image_before_its_figure_is_one_caption_line(panel):
    panel.png = b""
    built = tkmod.TkPanelView(FakeWidget(), FakeController(Demo=panel), "Demo")
    widget = widget_of(built, element_of(built, "image"))
    assert widget.cget("pady") == 0 and widget.cget("text") == "No figure yet."
    built.close()


class DeepPanel(TieredPanel):
    """Tier 3 with two sections, one of them titled as its disclosure."""

    @property
    def schema(self):
        base = super().schema
        base["sections"].append(sch.section(
            "Safety", sch.readonly("Link:", "link"), tier=3, disclosure="Diagnostics"))
        return base


def test_l18_a_section_title_equal_to_its_disclosure_is_not_repeated():
    tkmod._DISCLOSED.clear()
    built = tkmod.TkPanelView(FakeWidget(), FakeController(D=DeepPanel()), "D")
    titles = [label.cget("text") for label in built._section_titles]
    assert "Diagnostics" not in titles, "TK7-17"
    assert "Safety" in titles, "a title that says something new stays"
    built.close()


def test_l22_an_empty_reading_is_muted_regular_at_reading_size_with_no_unit(
        tiered):
    view, panel = tiered
    element = element_of(view, "readonly", "X:")
    entry = view._widgets[id(element)]
    widget = entry["widget"]
    reading = entry["font"]
    panel.x = None
    view._refresh()
    assert widget.cget("text") == "--"
    assert widget.cget("foreground") == theme.MUTED
    font = widget.cget("font")
    assert font[1] == reading[1], "reading size: a position never shrinks away"
    assert font[2] == "normal" and font[0] == tkmod._TEXT_FAMILY, "not two bars"
    panel.x = 12
    view._refresh()
    assert widget.cget("font") == reading
    temp = view._widgets[id(element_of(view, "readonly", "Temperature:"))]
    assert temp["unit_label"].is_packed
    panel.age = None
    view._refresh()
    assert not temp["unit_label"].is_packed, "no unit without a number"
    panel.age = 0.2
    view._refresh()
    assert temp["unit_label"].is_packed


class GatedPanel(Panel):
    """Commands behind gates: a `go` Start run that needs a region, a Home
    that needs manual mode, and a speed slider."""
    NAME = "Gated"
    PARAMS = {"speed": Param("speed", "int", default=400, minimum=1, maximum=1000,
                             label="Speed")}

    def __init__(self):
        super().__init__()
        self.mode = "no_region"
        self.committed = []

    @property
    def mode_name(self):
        return self.mode

    @property
    def schema(self):
        return sch.schema(sch.section(
            "Run",
            sch.entry("Speed:", "speed", self.PARAMS["speed"], slider=(1, 1000)),
            sch.button("Start run", "start", role="go",
                       disabled_when=["no_region", "latched", "running"]),
            sch.button("Home", "home", enabled_when=["manual"])))

    def start(self):
        return "started"

    def home(self):
        return "homed"


@pytest.fixture
def gated():
    panel = GatedPanel()
    built = tkmod.TkPanelView(FakeWidget(), FakeController(Gated=panel), "Gated")
    yield built, panel
    built.close()


def test_l3_a_disabled_command_says_why_and_a_go_command_says_it_under_its_row(
        gated):
    """TK7-4: Start run greyed from launch with no reason anywhere."""
    view, panel = gated
    start = view._widgets[id(element_of(view, "button", "Start run"))]
    home = view._widgets[id(element_of(view, "button", "Home"))]
    assert start["gate_tip"].text == "Set a capture region first"
    assert home["gate_tip"].text == "Not in manual mode"
    why = list(view._why_labels.values())
    assert len(why) == 1 and why[0].cget("text") == "Set a capture region first"
    assert why[0].cget("foreground") == theme.MUTED
    assert why[0].grid_info["row"] == view.WHY_ROW
    panel.mode = "latched"
    view._refresh()
    assert start["gate_tip"].text == "Stopped: clear the stop first"
    assert why[0].cget("text") == "Stopped: clear the stop first"
    panel.mode = "manual"
    view._refresh()
    assert start["gate_tip"].text == "" and home["gate_tip"].text == ""
    assert why[0].grid_info is None, "an enabled command says nothing"


def test_l3_the_reason_shows_on_hover_through_the_commands_own_handlers(gated):
    view, _panel = gated
    start = view._widgets[id(element_of(view, "button", "Start run"))]
    start["widget"].fire("<Enter>")
    assert start["gate_tip"]._after_id is not None, "the hover text is due"
    assert start["is_hovered"], "the command's own hover still runs"
    start["widget"].fire("<Leave>")
    assert start["gate_tip"]._after_id is None


class _Key:
    def __init__(self, keysym):
        self.keysym = keysym


def test_l6_slider_keys_step_one_percent_and_home_end_do_nothing(gated):
    """TK7-5: End committed the maximum speed in one key; every arrow moved
    one unit of a 1..1000 travel."""
    view, panel = gated
    element = element_of(view, "entry", "Speed:")
    entry = view._widgets[id(element)]
    scale = entry["scale"]
    scale.set(400)
    assert scale.fire("<Right>") == "break"
    assert scale.value == 410
    assert entry["var"].get() == "410", "the slider writes the entry"
    scale.fire("<Left>")
    scale.fire("<Down>")
    assert scale.value == 390
    scale.fire("<Prior>")
    assert scale.value == 490, "Page Up: 10 %"
    scale.fire("<Next>")
    assert scale.value == 390
    assert scale.fire("<End>") == "break" and scale.fire("<Home>") == "break"
    assert scale.value == 390, "Home and End are inert"
    scale.set(995)
    scale.fire("<Right>")
    assert scale.value == 1000, "never past the end"


def test_l6_a_slider_commits_on_a_moving_keys_release_only(gated):
    view, panel = gated
    element = element_of(view, "entry", "Speed:")
    scale = view._widgets[id(element)]["scale"]
    calls = view.controller.calls
    scale.set(400)
    scale.fire("<Right>")
    before = len([c for c in calls if c[1] == "_commit"])
    scale.fire("<KeyRelease>", _Key("Tab"))
    assert len([c for c in calls if c[1] == "_commit"]) == before, "Tab commits nothing"
    scale.fire("<KeyRelease>", _Key("Right"))
    commits = [c for c in calls if c[1] == "_commit"]
    assert len(commits) == before + 1 and commits[-1][2] == {"speed": "410"}
    assert panel.speed == 410


def test_l6_a_disabled_slider_does_not_move(gated):
    view, panel = gated
    element = element_of(view, "entry", "Speed:")
    entry = view._widgets[id(element)]
    entry["scale"].set(400)
    entry["is_enabled"] = False
    entry["scale"].fire("<Right>")
    assert entry["scale"].value == 400


def test_l4_commands_are_36_px_and_fields_24_px_with_their_rings(gated):
    """TK7-6 floors: a command at least 36 px tall with its ring, an entry
    or a dropdown at least 24 (44 is an owner call)."""
    view, _panel = gated
    line = tkmod._line_px()
    command = view._widgets[id(element_of(view, "button", "Start run"))]["widget"]
    ring = 2 * tkmod.FOCUS_PX
    # Signature: a command is a key; its lip is part of its height.
    lip = tkmod._lip_px("key")
    assert line + 2 * command.cget("pady") + ring + lip >= tkmod.COMMAND_PX
    field = tkmod._line_px() + 2 * tkmod._field_pady() + ring + tkmod.UNDERLINE_PX
    assert field >= tkmod.MIN_TARGET_PX
    press = tkmod._Press(FakeWidget(), "Quit", lambda: None, theme.SURFACE)
    assert line + 2 * press.widget.cget("pady") + ring + lip >= tkmod.COMMAND_PX


def test_l4_a_press_anywhere_in_an_entrys_well_focuses_it(gated):
    view, _panel = gated
    entry = view._widgets[id(element_of(view, "entry", "Speed:"))]
    ring, widget = entry["ring"], entry["widget"]
    for part in (ring.outer, ring.inner, ring.line):
        Focus.current = None
        part.fire("<Button-1>")
        assert Focus.current is widget


class TickPanel(Panel):
    """Setup's shape: table rows named by model, a Launch tick per row."""
    NAME = "Ticks"

    def __init__(self):
        super().__init__()
        self.a_on = False
        self.b_on = False

    @property
    def schema(self):
        return sch.schema(
            sch.section("Stepper Probe", sch.checkbox("Launch", "a_on", "set_a"),
                        layout="row"),
            sch.section("Rotator", sch.checkbox("Launch", "b_on", "set_b"),
                        layout="row"))

    def set_a(self, flag):
        self.a_on = bool(flag)

    def set_b(self, flag):
        self.b_on = bool(flag)


def test_l4_a_table_rows_name_and_its_tick_are_one_target():
    panel = TickPanel()
    built = tkmod.TkPanelView(FakeWidget(), FakeController(Ticks=panel), "Ticks")
    names = {label.cget("text"): label for label in built._section_titles}
    names["Rotator"].fire("<Button-1>")
    assert panel.b_on is True and panel.a_on is False
    assert names["Rotator"].cget("cursor") == "hand2"
    names["Rotator"].fire("<Button-1>")
    assert panel.b_on is False
    built.close()


def test_l4_the_slider_trough_is_a_full_target_tall(tk_harness, setup_panel):
    """The trough image is as tall as a target; the thumb stays 18 px."""
    heights = []

    class Image(FakePhotoImage):
        def __init__(self, width=0, height=0, **kwargs):
            super().__init__(**kwargs)
            heights.append(height)

        def put(self, *_args, **_kwargs):
            pass

    class Style(FakeStyle):
        def element_create(self, *args, **kwargs):
            pass

        def layout(self, *args, **kwargs):
            pass

    tkmod.tk.PhotoImage = Image
    tkmod.ttk.Style = Style
    try:
        built = tkmod.TkDashboard(FakeController(), setup_panel)
    finally:
        tkmod.tk.PhotoImage = FakePhotoImage
        tkmod.ttk.Style = FakeStyle
    assert max(heights) >= tkmod.MIN_TARGET_PX
    built.close()


def test_l20_tab_order_models_before_setup_and_the_sheet_before_the_tray(
        tk_harness, setup_panel, monkeypatch):
    """TK7-7: Tk traverses siblings in stacking order."""
    lifted, configured = [], {}
    monkeypatch.setattr(FakeWidget, "lift", lambda self, *a: lifted.append(self),
                        raising=False)
    monkeypatch.setattr(tkmod.ClosableNotebook, "configure",
                        lambda self, **kw: configured.update(kw), raising=False)
    built = tkmod.TkDashboard(FakeController(), setup_panel)
    rail = built._rail.children
    foot = built._setup_press.frame.master
    assert rail.index(built._model_list) < rail.index(foot)
    assert configured.get("takefocus") == 0, "the hidden tab strip takes no focus"
    # Updated (R6): no band; the tray comes after the sheet.
    assert built._tray in lifted, "sheet, then the tray"
    built.close()


def test_l20_a_short_row_keeps_the_pages_columns(tk_harness, setup_panel):
    """TK7-8: five models in rows of three: the second row's two entries are
    as wide as the first row's three."""
    names = ["A", "B", "C", "D", "E"]
    controller = FakeController(**{name: DemoPanel() for name in names})
    built = tkmod.TkDashboard(controller, setup_panel)
    built.open()
    built.root.winfo_width = lambda: 1400
    built._lay_out_sheet(force=True)
    first, second = built._sheet_rows
    for row in (first, second):
        entries = {c: o for c, o in row.column_weights.items() if o.get("uniform")}
        assert sorted(entries) == [0, 2, 4]
        assert row.column_weights[1]["minsize"] == row.column_weights[3]["minsize"]
    built.close()


def test_l20_captions_in_a_line_share_its_top_and_commands_its_foot(gated):
    """TK7-9: the cells sat on their bottoms, so a taller control pushed its
    neighbours' captions down."""
    view, _panel = gated
    speed = view._widgets[id(element_of(view, "entry", "Speed:"))]["box"]
    start = view._widgets[id(element_of(view, "button", "Start run"))]["ring"].outer
    anchors = {}
    for widget, kwargs in PACK_ORDER:
        if widget in (speed, start) and kwargs.get("in_") is not None:
            anchors[widget] = kwargs.get("anchor")
    assert anchors[speed] == "nw" and anchors[start] == "sw"


def test_l5_on_the_device_page_only_the_well_scrolls(setup_panel):
    """TK7-3: with the well open the opened model's X/Y/Z and speeds
    scrolled off the top. The device page is now the sheet's height; the
    head and tier 1 stay; the well is a scroller of its own."""
    built, controller = _two_page_dashboard(setup_panel, *K_NAMES)
    probe = built._panels["Stepper Probe"]
    assert probe._well.master is probe._well_canvas, "the well is on its own canvas"
    assert probe._tiers[1].master is probe._body, "tier 1 is not"
    assert probe._well_canvas.cget("height") == 1, "it asks for no height"
    built._sheet._on_canvas_resized(FakeEvent(width=1100, height=800))
    assert not built._sheet.fit and built._sheet.fitted == 0, "the overview scrolls"
    built.show_model("Stepper Probe")
    probe.set_disclosure(2, True)
    assert built._sheet.fit
    assert built._sheet.canvas.windows[built._sheet.window]["height"] >= 800
    packed = [kw for widget, kw in PACK_ORDER if widget is probe._well_holder][-1]
    assert packed["fill"] == "both" and packed["expand"], "the well takes the rest"
    row = built._sheet_rows[0]
    assert [kw for widget, kw in PACK_ORDER if widget is row][-1]["expand"]
    assert probe.frame.grid_info["sticky"] == "nsew"
    # The wheel over the well scrolls the well, never the page.
    handler = ALL_BINDINGS.get("<MouseWheel>")
    built._sheet._on_pointer_enter()
    ALL_BINDINGS["<MouseWheel>"](FakeEvent(delta=-1, widget=probe._well))
    assert probe._well_canvas.scrolled == [(1, "units")]
    assert built._sheet.canvas.scrolled == []
    # Back on the overview, the page scrolls as it always has.
    built.show_overview()
    assert not built._sheet.fit
    assert built._sheet.canvas.windows[built._sheet.window]["height"] == 0
    del handler
    tkmod._DISCLOSED.clear()
    built.close()


def test_l5_the_well_is_embedded_only_once_it_is_first_shown(setup_panel):
    """A canvas window inside a canvas that was never mapped sent Tk 9's
    geometry into a loop that never went idle (the real build hung at the
    overview). The well joins its canvas when it is first shown."""
    built, controller = _two_page_dashboard(setup_panel, *K_NAMES)
    probe = built._panels["Stepper Probe"]
    assert probe._well_canvas.windows == {}
    built.show_model("Stepper Probe")
    probe.set_disclosure(2, True)
    assert [w["window"] for w in probe._well_canvas.windows.values()] == [probe._well]
    built.show_overview()
    built.show_model("Stepper Probe")
    assert len(probe._well_canvas.windows) == 1, "once"
    tkmod._DISCLOSED.clear()
    built.close()


def test_l22_the_window_is_titled_as_the_rail_names_the_station(dashboard):
    """TK7-18: the window said "Transfer Station", the rail "Transfer stage"."""
    assert dashboard.root.titles == [tkmod.STATION_TITLE] == ["Transfer stage"]


# ---------------------------------------------------------------------------
# Tier N and O (audit round 8, 2026-09-26)
# ---------------------------------------------------------------------------

class ModePanel(DemoPanel):
    """A probe's go half: the two mode toggles, Step (greyed in manual
    mode), and the internal `extend_idle` the countdown's Extend runs."""
    NAME = "Stepper Probe"

    def __init__(self):
        super().__init__()
        self.is_auto = False
        self.is_manual = False

    @property
    def schema(self):
        return sch.schema(sch.section(
            "System Control",
            sch.toggle("Autonomous:", "is_auto", "set_mode",
                       "Autonomous mode (press to stop)", "Enter Autonomous Mode",
                       on_args=["autonomous"], off_args=["disabled"],
                       disabled_when=("latched",)),
            sch.toggle("Manual / Gamepad:", "is_manual", "set_mode",
                       "Manual mode (press to stop)", "Enter Manual Mode",
                       on_args=["manual"], off_args=["disabled"],
                       disabled_when=("latched",)),
            sch.button("Step", "step", role="go", disabled_when=("manual", "latched")),
            sch.button("Hold", "hold", enabled_when=("autonomous",)),
            _internal("extend_idle")))

    def hold(self):
        return "held"

    def set_mode(self, target):
        self.commands.append(("set_mode", target))
        self.mode = target
        return target

    def step(self):
        self.commands.append(("step",))
        return "stepped"


def _mode_view(name="Stepper Probe"):
    panel = ModePanel()
    controller = FakeController(**{name: panel})
    return tkmod.TkPanelView(FakeWidget(), controller, name), panel, controller


FAULT = ("The disable did not reach the board, so the motors may still be "
         "powered. Treat it as live.")


def test_o17_the_disc_defers_to_the_dashboards_toggle_estop_all(tk_harness,
                                                                 setup_panel,
                                                                 monkeypatch):
    """ARCH-5: Tk kept its own copy of the disc's rule. The press is the
    base's `Dashboard.toggle_estop_all`, as in Qt."""
    pressed = []
    monkeypatch.setattr(tkmod.TkDashboard, "toggle_estop_all",
                        lambda self: pressed.append(True) or {})
    built, controller = _stop_dashboard(setup_panel)
    built._stop_button.fire("<Button-1>")
    assert pressed == [True]
    assert controller.estop_calls == 0 and controller.clear_calls == 0
    built.close()


def test_o17_the_clear_through_the_base_still_asks_in_the_clear_words(
        tk_harness, setup_panel):
    built, controller = _stop_dashboard(setup_panel)
    controller.is_estopped = True
    controller.clear_needs_confirm = True
    built._stop_button.fire("<Button-1>")
    assert tk_harness.words[-1] == tkmod.CLEAR_DIALOG
    assert controller.clear_calls == 2 and not controller.is_estopped
    built.close()


class _NoStopState(FakeController):
    """A controller that serves no stop state: the old fallback read
    `is_estopped` (ANY model latched) as "every model is stopped"."""
    stop_state = None


def test_o17_no_fallback_reads_one_latch_as_every_model(tk_harness, setup_panel):
    controller = _NoStopState(**{name: DemoPanel() for name in L_NAMES})
    built = tkmod.TkDashboard(controller, setup_panel)
    built.open()
    controller.latched = ["Rotator"]
    built._sync_stop_button()
    assert _faces(built) == ["Stop"], "never Clear from a guess"
    assert not built._headline.is_packed
    built.close()


def test_o17_the_stand_in_derives_the_stop_per_model(tk_harness, setup_panel):
    """The test file's FakeController lost the any-latched rule: one latched
    model is a partial stop here too, so an L1 regression in Tk shows."""
    built, controller = _stop_dashboard(setup_panel)
    controller.latched = ["Rotator"]
    assert controller.stop_state == {"latched": ["Rotator"], "unconfirmed": [],
                                     "every": False}
    built._sync_stop_button()
    assert _faces(built) == ["Stop"]
    assert built._latched_line.cget("text") == "Stopped: Rotator"
    built._stop_button.fire("<Button-1>")
    assert controller.estop_calls == 1 and controller.clear_calls == 0
    assert _faces(built) == ["Clear"]
    built.close()


def test_o4_a_faulted_model_is_marked_like_an_unconfirmed_stop(tk_harness,
                                                              setup_panel):
    """IMP8-2: a probe whose disable failed looked like a safe one."""
    built, controller = _stop_dashboard(setup_panel)
    view = built._panels["DC Probe"]
    controller.panels["DC Probe"].fault = FAULT
    view._refresh()
    assert view._mark_row.is_packed
    assert view._mark_text.cget("text") == "Disable failed. Treat as live."
    assert view._mark_reason.cget("text") == FAULT
    assert view._rule.cget("background") == theme.SIGNAL
    assert not built._panels["Rotator"]._mark_row.is_packed
    controller.panels["DC Probe"].fault = ""
    view._refresh()
    assert not view._mark_row.is_packed
    assert view._rule.cget("background") == theme.RULE_STRONG
    built.close()


def test_o4_an_unconfirmed_stop_keeps_its_own_words_over_a_fault(tk_harness,
                                                                setup_panel):
    built, controller = _stop_dashboard(setup_panel)
    view = built._panels["DC Probe"]
    controller.panels["DC Probe"].fault = FAULT
    controller.panels["DC Probe"].stop_confirmed = False
    view._refresh()
    assert view._mark_text.cget("text") == "Stop not confirmed. Treat as live."
    assert view._mark_reason.cget("text") == FAULT, "the reason still shows"
    built.close()


def test_o4_a_faulted_probes_mode_toggles_are_greyed_with_the_reason(tk_harness):
    view, panel, _controller = _mode_view()
    toggles = [element_of(view, "toggle", "Autonomous:"),
               element_of(view, "toggle", "Manual / Gamepad:")]
    entries = [view._widgets[id(element)] for element in toggles]
    assert all(entry["is_enabled"] for entry in entries)
    panel.fault = FAULT
    view._refresh()
    for entry in entries:
        assert not entry["is_enabled"]
        assert entry["gate_tip"].text == "Faulted: clear the fault first"
    widget_of(view, toggles[0]).fire("<Button-1>")
    assert panel.commands == [], "a greyed toggle sends nothing"
    step = view._widgets[id(element_of(view, "button", "Step"))]
    assert step["is_enabled"], "only the mode toggles are held"
    panel.fault = ""
    view._refresh()
    assert all(entry["is_enabled"] for entry in entries)
    view.close()


def test_o4_a_faulted_model_wears_the_signal_mark_in_the_rail(tk_harness,
                                                             setup_panel):
    built, controller = _stop_dashboard(setup_panel)
    controller.panels["DC Probe"].fault = FAULT
    built._on_refresh_tick()
    canvas, tip = built._rail_marks["DC Probe"]
    # Signature: the warning glyph in SIGNAL (was a signal square with a
    # "!"), and the lamp slot in SIGNAL.
    assert {i[2]["fill"] for i in canvas.items} == {theme.SIGNAL}
    assert {tuple(i[2]["tags"]) for i in canvas.items} == {("warning",)}
    assert built._rail_lamp_state("DC Probe") == "unconfirmed"
    assert tip.text == "Disable failed"
    controller.panels["DC Probe"].fault = ""
    built._on_refresh_tick()
    canvas, tip = built._rail_marks["DC Probe"]
    assert canvas.items == [] and tip.text == ""
    built.close()


def _idle_texts(built):
    return [line["text"].cget("text") for line in built._idle_lines.values()]


def test_n2_the_countdown_line_names_the_model_and_the_seconds_from_state(
        tk_harness, setup_panel):
    """Tier N: inside the warning window, one line per probe under the disc:
    "Stepper Probe powers down in 42 s." with Extend; outside it, nothing."""
    built, controller = _stop_dashboard(setup_panel)
    probe = controller.panels["Stepper Probe"]
    probe.idle_remaining = 120.0
    built._on_refresh_tick()
    assert _idle_texts(built) == [] and not built._idle_box.is_packed
    probe.idle_remaining = 42.0
    built._on_refresh_tick()
    assert _idle_texts(built) == ["Stepper Probe powers down in 42 s."]
    assert built._idle_box.is_packed
    line = built._idle_lines["Stepper Probe"]
    assert line["extend"].widget.cget("text") == "Extend"
    assert line["tooltip"].text == "Extend Stepper Probe"
    probe.idle_remaining = 41.0
    built._on_refresh_tick()
    assert _idle_texts(built) == ["Stepper Probe powers down in 41 s."], \
        "the number is the state's, every tick"
    probe.idle_remaining = None
    built._on_refresh_tick()
    assert _idle_texts(built) == [] and not built._idle_box.is_packed
    built.close()


def test_n2_extend_runs_extend_idle_on_that_model_and_the_line_goes(
        tk_harness, setup_panel):
    built, controller = _stop_dashboard(setup_panel)
    probe = controller.panels["DC Probe"]
    probe.idle_remaining = 12.0
    built._on_refresh_tick()
    built._idle_lines["DC Probe"]["extend"].widget.fire("<Button-1>")
    assert last_call(controller, "extend_idle")[0] == "DC Probe"
    assert probe.extended == 1
    assert _idle_texts(built) == [], "state says 300 s: the line goes at once"
    built.close()


def test_n2_two_probes_give_two_lines_in_station_order(tk_harness, setup_panel):
    built, controller = _stop_dashboard(setup_panel)
    controller.panels["DC Probe"].idle_remaining = 20.0
    controller.panels["Stepper Probe"].idle_remaining = 55.0
    built._on_refresh_tick()
    assert _idle_texts(built) == ["Stepper Probe powers down in 55 s.",
                                  "DC Probe powers down in 20 s."]
    built.close()


def test_n2_the_line_never_covers_the_disc_and_is_not_a_modal(tk_harness,
                                                             setup_panel):
    built, controller = _stop_dashboard(setup_panel)
    Focus.current = built._stop_button
    toplevels = len([w for w, _kw in PACK_ORDER if isinstance(w, FakeRoot)])
    controller.panels["Stepper Probe"].idle_remaining = 30.0
    built._on_refresh_tick()
    assert built._idle_box.master is built._rail, "a line in the rail"
    packed = [kw for widget, kw in PACK_ORDER if widget is built._idle_box][-1]
    assert packed.get("before") is built._model_list
    order = [w for w, _kw in PACK_ORDER]
    assert order.index(built._stop_button) < order.index(built._idle_box)
    assert GRABS == [] and Focus.current is built._stop_button, "never steals focus"
    assert len([w for w, _kw in PACK_ORDER if isinstance(w, FakeRoot)]) == toplevels
    built.close()


def test_n4_the_quit_question_names_the_energized_models(tk_harness, setup_panel):
    built, controller = _stop_dashboard(setup_panel)
    tk_harness.confirm_answer = False
    controller.energized = ["Stepper Probe", "Rotator"]
    built._quit_press.widget.fire("<Button-1>")
    assert tk_harness.asked[-1] == ("confirm", (
        "Quit the station? Stepper Probe and Rotator are energized; quitting "
        "stops and disconnects them."))
    controller.energized = ["Rotator"]
    built._quit_press.widget.fire("<Button-1>")
    assert tk_harness.asked[-1] == ("confirm", (
        "Quit the station? Rotator is energized; quitting stops and "
        "disconnects it."))
    controller.energized = []
    built._quit_press.widget.fire("<Button-1>")
    assert tk_harness.asked[-1] == ("confirm", built.QUIT_PROMPT)
    assert not built._closing
    built.close()


def _idle_soon():
    return Event(9, "warning", "Stepper Probe", "Idle Timeout Soon",
                 "Stepper Probe powers its motors down in 60 s unless it moves "
                 "or you extend.", None, False, 0.0)


def test_o13_the_idle_warning_is_history_not_the_trays_live_line(tk_harness,
                                                                setup_panel):
    """PM8-4: the tray's line kept "in 39 s" frozen beside the live
    countdown, and after Extend it still promised a power-down."""
    built, controller = _stop_dashboard(setup_panel)
    built._on_event(Event(8, "warning", "DC Probe", "Power Down Not Supported",
                          "The DC board has no coil kill.", None, False, 0.0))
    built._on_event(_idle_soon())
    SCHEDULER.pump()
    assert "powers its motors down in 60 s" in built._event_text.body, "history"
    assert built._latest_text.cget("text") == (
        "Warning: Power down not supported: The DC board has no coil kill.")
    built.close()


def test_o3_step_in_manual_mode_says_in_manual_mode(tk_harness):
    """IMP8-1: the reason was inverted - "Not in manual mode" while the probe
    WAS in manual mode. The words are `views.base.gate_reason`'s."""
    view, panel, _controller = _mode_view()
    panel.mode = "manual"
    view._refresh()
    step = view._widgets[id(element_of(view, "button", "Step"))]
    assert not step["is_enabled"]
    assert step["gate_tip"].text == "In manual mode"
    assert [label.cget("text") for label in view._why_labels.values()] == [
        "In manual mode"], "a go command says it under its row"
    view.close()


def test_o3_a_command_missing_its_mode_names_the_mode_it_needs(tk_harness):
    """The other direction: Hold needs autonomous mode; in manual mode it says
    what it needs, not the mode it is in."""
    view, panel, _controller = _mode_view()
    panel.mode = "manual"
    view._refresh()
    hold = view._widgets[id(element_of(view, "button", "Hold"))]
    assert hold["gate_tip"].text == "Not in autonomous mode"
    panel.mode = "autonomous"
    view._refresh()
    assert hold["is_enabled"] and hold["gate_tip"].text == ""
    view.close()


def test_o3_the_view_keeps_no_gate_table_of_its_own(tk_harness):
    """One table, in `views.base` (three copies disagreed)."""
    assert not hasattr(tkmod, "GATE_WORDS")
    assert not hasattr(tkmod, "_gate_word")


def test_o6_an_energized_model_wears_an_ink_ring_after_its_stop_mark(tk_harness,
                                                                     setup_panel):
    """PM8-2 / IMP8-4: nothing said which models hold hardware. A ring in
    ink (not red) after the stop mark; the words are the line's tooltip."""
    built, controller = _stop_dashboard(setup_panel)
    controller.energized = ["Stepper Probe"]
    built._on_refresh_tick()
    ring = built._energy_marks["Stepper Probe"]
    ovals = [item[2] for item in ring.items if item[0] == "oval"]
    assert len(ovals) == 1
    assert ovals[0]["outline"] == theme.TEXT and ovals[0]["fill"] == ""
    assert built._rail_marks["Stepper Probe"][1].text == "Energized"
    assert built._energy_marks["DC Probe"].items == []
    order = [widget for widget, _kw in PACK_ORDER]
    assert order.index(built._rail_marks["Stepper Probe"][0]) < order.index(ring)
    built.show_model("Stepper Probe")
    assert ring.cget("background") == \
        built._rail_items["Stepper Probe"][1].cget("background")
    controller.energized = []
    built._on_refresh_tick()
    assert ring.items == [] and built._rail_marks["Stepper Probe"][1].text == ""
    built.close()


def test_o6_a_stopped_mark_and_the_energized_ring_share_the_tooltip(tk_harness,
                                                                    setup_panel):
    built, controller = _stop_dashboard(setup_panel)
    controller.stop = {"latched": ["DC Probe"], "unconfirmed": ["DC Probe"],
                       "every": False}
    controller.energized = ["DC Probe"]
    built._sync_stop_button()
    built._on_refresh_tick()
    assert built._rail_marks["DC Probe"][1].text == "Did not confirm the stop; energized"
    built.close()


def test_o9_the_window_close_asks_the_quit_question(tk_harness, setup_panel):
    """PM8-6 / ARCH-6: the window's close button quit without asking."""
    built, controller = _stop_dashboard(setup_panel)
    controller.energized = ["Rotator"]
    tk_harness.confirm_answer = False
    built.root.protocols["WM_DELETE_WINDOW"]()
    assert tk_harness.asked[-1] == ("confirm", (
        "Quit the station? Rotator is energized; quitting stops and "
        "disconnects it."))
    assert tk_harness.words[-1] == tkmod.QUIT_DIALOG
    assert not built._closing and not controller.is_closed, "declined: still running"
    tk_harness.confirm_answer = True
    built.root.protocols["WM_DELETE_WINDOW"]()
    assert controller.is_closed
    built.close()


def test_o9_the_os_quit_hook_is_left_as_it_is(tk_harness, setup_panel):
    """P3 is the owner's call: the forced `::tk::mac::Quit` still takes the
    close path without a question (an unanswered question there can end in
    Tcl_Exit)."""
    built, controller = _stop_dashboard(setup_panel)
    tk_harness.confirm_answer = False
    built.root.commands["::tk::mac::Quit"]()
    assert controller.is_closed and tk_harness.asked == []


def test_o9_the_rails_middle_click_asks_before_closing_a_model(tk_harness,
                                                              setup_panel):
    """PM8-5: the middle button on a rail line closed the model on one
    press; "Close this model..." asked. Both ask the same question now."""
    built, controller = _stop_dashboard(setup_panel)
    tk_harness.confirm_answer = False
    label = built._rail_items["Rotator"][1]
    label.fire(tkmod._close_tab_button(label))
    assert controller.removed == []
    assert tk_harness.asked[-1][1].startswith("Close Rotator?")
    assert tk_harness.words[-1] == {"title": "Close Rotator",
                                    "yes_text": "Close Rotator",
                                    "no_text": "Keep it open"}
    tk_harness.confirm_answer = True
    label.fire(tkmod._close_tab_button(label))
    assert controller.removed == ["Rotator"]
    built.close()


def test_o9_unticking_a_model_in_the_models_menu_asks_too(tk_harness, setup_panel):
    built, controller = _stop_dashboard(setup_panel)
    tk_harness.confirm_answer = False
    variable = built._menu_vars["Rotator"]
    variable.set(False)
    built._on_model_toggled("Rotator")
    assert controller.removed == [] and variable.get() is True, "declined: ticked"
    tk_harness.confirm_answer = True
    variable.set(False)
    built._on_model_toggled("Rotator")
    assert controller.removed == ["Rotator"]
    built.close()


class _SafetyPanel(DemoPanel):
    """A model's Safety section as `Model._safety_section` builds it."""

    def __init__(self):
        super().__init__()
        self.is_estopped = False

    @property
    def schema(self):
        return sch.schema(sch.section("Safety", sch.toggle(
            "Stop", "is_estopped", "set_running", "Stopped", "Stop",
            on_role="danger", off_role="danger", tooltip="Stop the Safe",
            tooltip_on="The Safe is stopped. Press to clear the stop.")))


def test_o16_the_per_model_switch_says_stop_this_model_and_stopped(tk_harness):
    """PM8-8: "Stop" named five things. The disc is "Stop"/"Clear"; the
    model's own switch is "Stop this model"/"Stopped"."""
    safe = _SafetyPanel()
    built = tkmod.TkPanelView(FakeWidget(), FakeController(Safe=safe), "Safe")
    entry = built._widgets[id(built._elements[0])]
    assert entry["words"].cget("text") == "Stop this model"
    safe.is_estopped = True
    built._refresh()
    assert entry["words"].cget("text") == "Stopped"
    built.close()


def test_o16_the_rail_marks_differ_in_shape_not_colour_alone(tk_harness,
                                                            setup_panel):
    """A11Y-6: a square for stopped, a ring for energized, and for an
    unconfirmed stop (Signature, rule 6) the warning glyph - a triangle of
    lines, where a bigger filled square with "!" was."""
    built, controller = _stop_dashboard(setup_panel)
    controller.stop = {"latched": ["DC Probe", "Rotator"], "unconfirmed": ["Rotator"],
                       "every": False}
    controller.energized = ["Stepper Probe"]
    built._sync_stop_button()
    built._on_refresh_tick()

    def shapes(name):
        canvas = built._rail_marks[name][0]
        return sorted(item[0] for item in canvas.items)

    assert shapes("DC Probe") == ["rect"]
    assert set(shapes("Rotator")) == {"line"}
    assert [i[0] for i in built._energy_marks["Stepper Probe"].items] == ["oval"]
    built.close()


def test_o16_a_log_window_opens_with_its_feed_focused_in_a_ring(view, panel):
    """The log window took focus with no visible ring."""
    element = side_log(view)
    click(view, element)
    entry = view._widgets[id(element)]
    assert Focus.current is entry["feed"]
    assert entry["feed_ring"].is_focused
    assert entry["feed_ring"].outer.cget("background") == tkmod.FOCUS_INK
    entry["feed"].fire("<FocusOut>")
    assert not entry["feed_ring"].is_focused
    entry["feed"].fire("<FocusIn>")
    assert entry["feed_ring"].is_focused
    entry["feed"].fire("<FocusOut>")
    Focus.current = None
    click(view, element)                 # pressed again: raised, focused again
    assert Focus.current is entry["feed"] and entry["feed_ring"].is_focused


# ---------------------------------------------------------------------------
# Signature (owner ruling 2026-09-27): the key family, the lamp slot, the
# flag window, the nameplate, the glyphs
# ---------------------------------------------------------------------------

def _pads(ring):
    """(drop, lip) as packed: the lip frame's top pad in its seat and the
    rim's bottom pad on the lip."""
    return ring.lip.pack_options["pady"][0], ring.inner.pack_options["pady"][1]


def test_signature_the_key_pads_fold_the_lip_and_keep_the_height():
    """Up, a key's face stands on its whole lip; down (pressed or latched)
    the lip folds to `KEY_LIP_PX["pressed"]` and the face drops by what the
    lip lost, so drop + lip - the key's height - never changes."""
    lip, folded = tkmod._lip_px("key"), tkmod._lip_px("pressed")
    assert tkmod._key_pads("key", False) == (0, lip) == (0, 4)
    assert tkmod._key_pads("key", True) == (lip - folded, folded) == (3, 1)
    for kind in theme.KEY_LIP_PX:
        up, down = tkmod._key_pads(kind, False), tkmod._key_pads(kind, True)
        assert sum(up) == sum(down), kind


def test_signature_a_command_key_up_pressed_latched_and_disabled(view, panel):
    """The one helper's key in each state: up (lip 4, no drop), pressed by
    the pointer (folded until the release), latched (a toggle that is on:
    down in its on face), disabled (up, the silhouette in ghost tones - EDGE
    rim, EDGE_SOFT lip, DISABLED legend, the ground for a face)."""
    ask = element_of(view, "button", "Ask")
    entry = view._widgets[id(ask)]
    ring = entry["ring"]
    assert _pads(ring) == tkmod._key_pads("key", False)
    assert ring.lip.cget("background") == theme.KEY_LIP
    entry["widget"].fire("<Button-1>")
    assert ring.is_down and _pads(ring) == tkmod._key_pads("key", True)
    entry["widget"].fire("<ButtonRelease-1>")
    assert not ring.is_down and _pads(ring) == tkmod._key_pads("key", False)

    toggle = element_of(view, "toggle")
    toggle_ring = view._widgets[id(toggle)]["ring"]
    panel.is_running = True
    view._refresh()
    assert _pads(toggle_ring) == tkmod._key_pads("key", True), "latched is down"
    panel.is_running = False
    view._refresh()
    assert _pads(toggle_ring) == tkmod._key_pads("key", False)

    view._set_enabled(ask, False)
    assert _pads(ring) == tkmod._key_pads("key", False), "disabled keys stay up"
    assert entry["outline"].cget("background") == theme.EDGE
    assert ring.lip.cget("background") == theme.EDGE_SOFT
    assert entry["widget"].cget("foreground") == theme.DISABLED[1]
    assert entry["widget"].cget("background") == entry["ground"]
    entry["widget"].fire("<Button-1>")
    assert not ring.is_down, "a disabled key does not press"


def test_signature_the_go_key_is_ink_on_its_go_lip(controller):
    """`go` is the ink key with a CAP legend on the GO_LIP."""
    class Going(DemoPanel):
        @property
        def schema(self):
            return sch.schema(sch.section("Go", sch.button("Home", "go", role="go")))

    built = tkmod.TkPanelView(FakeWidget(), FakeController(Going=Going()), "Going")
    home = built._elements[0]
    entry = built._widgets[id(home)]
    assert entry["widget"].cget("background") == theme.TEXT
    assert entry["widget"].cget("foreground") == theme.CAP
    assert entry["ring"].lip.cget("background") == theme.GO_LIP
    built.close()


def test_signature_the_lamp_slot_is_hollow_off_lit_on_and_ghost_disabled(view,
                                                                          panel):
    """Rule 5: the latching key's lamp slot - hollow off (SURFACE, KEY_RIM
    edge), lit CAP in the ink key when on, EDGE-edged on its ground when
    disabled. Never trace."""
    toggle = element_of(view, "toggle")
    entry = view._widgets[id(toggle)]
    slot = entry["lamp_slot"]

    def lamp():
        (item,) = [i for i in slot.items if i[0] == "rect"]
        return item[2]["fill"], item[2]["outline"]

    panel.is_running = False
    view._refresh()
    assert lamp() == (theme.LAMP["off"], theme.LAMP["edge"])
    panel.is_running = True
    view._refresh()
    assert lamp() == (theme.CAP, theme.CAP)
    view._set_enabled(toggle, False)
    assert lamp()[1] == theme.EDGE
    assert theme.TRACE not in {c for i in slot.items for c in (i[2]["fill"],
                                                               i[2]["outline"])}
    assert tkmod._lamp_colours("hidden", theme.RAIL) is None
    assert tkmod._lamp_colours("unconfirmed", theme.RAIL)[0] == theme.SIGNAL


def test_signature_the_shown_page_lights_its_rail_lamp(setup_panel):
    """The rail: the shown page is a sunk SURFACE pad with its lamp lit ink;
    the other lines keep their lamp's space, hidden."""
    built, _controller = _stop_dashboard(setup_panel)
    built.show_model("DC Probe")
    assert built._rail_lamp_state("DC Probe") == "on"
    assert built._rail_lamp_state("Rotator") == "hidden"
    lit = built._rail_lamps["DC Probe"].items
    assert [i[2]["fill"] for i in lit] == [theme.LAMP["on"]]
    assert built._rail_lamps["Rotator"].items == []
    assert built._rail_lamps["Rotator"].cget("width") == tkmod._lamp_size(True)[0]
    built.close()


def test_signature_the_flag_window_is_drawn_once_per_episode(tiered):
    """Rule 4: an unconfirmed (or faulted) model's entry carries the tripped
    flag - an ink frame, a SIGNAL flag, an ink hatch - drawn when the
    episode begins, not again while it lasts, and gone with it."""
    view, _panel = tiered
    assert view._flag_draws == 0 and view._mark.items == []
    view.set_hazard(True)
    assert view._flag_draws == 1
    tags = {tuple(i[2].get("tags") or ()) for i in view._mark.items}
    assert {("frame",), ("flag",), ("hatch",)} <= tags
    frame = [i for i in view._mark.items if i[2].get("tags") == ("frame",)][0]
    flag = [i for i in view._mark.items if i[2].get("tags") == ("flag",)][0]
    hatch = [i for i in view._mark.items if i[2].get("tags") == ("hatch",)]
    assert frame[2]["fill"] == theme.FLAG["frame"]
    assert flag[2]["fill"] == theme.FLAG["fill"] == theme.SIGNAL
    assert hatch and all(i[2]["fill"] == theme.FLAG["hatch"] for i in hatch)
    x0, y0, x1, y1 = flag[1]
    for item in hatch:                  # the hatch is cut to the window
        xs, ys = item[1][0::2], item[1][1::2]
        assert all(x0 - 0.01 <= x <= x1 + 0.01 for x in xs)
        assert all(y0 - 0.01 <= y <= y1 + 0.01 for y in ys)
    view.set_hazard(True, "disable failed")          # same episode
    assert view._flag_draws == 1
    view.set_hazard(False)
    assert view._mark.items == [] and not view._mark_row.is_packed
    view.set_hazard(True)
    assert view._flag_draws == 2, "a new episode trips it again"


def test_signature_the_nameplate_holds_the_title_the_stop_and_the_chord(dashboard):
    """The nameplate: one CAP plate in a 1 px KEY_RIM edge on the RAIL; the
    stop sits on it; the chord is "Stop:" then two keycaps (CAP, KEY_RIM,
    the 2 px kbd lip) joined by "+", and reads "Stop: Ctrl+."."""
    assert dashboard._rail.cget("background") == theme.RAIL
    assert dashboard._plate_edge.cget("background") == theme.KEY_RIM
    assert dashboard._plate_edge.cget("padx") == dashboard._plate_edge.cget("pady") == 1
    assert dashboard._plate.cget("background") == theme.CAP
    assert dashboard._stop_button.master is dashboard._plate
    assert dashboard._stop_button.cget("background") == theme.CAP
    assert dashboard.chord_text == "Stop: Ctrl+."
    caps = [part for part in dashboard._chord_parts
            if part.master is not dashboard._stop_hint]
    assert [cap.cget("text") for cap in caps] == ["Ctrl", "."]
    for cap in caps:
        rim, lip = cap.master, cap.master.master
        assert rim.cget("background") == theme.KEY_RIM
        assert lip.cget("background") == theme.KEY_LIP
        assert rim.pack_options["pady"] == (0, tkmod._lip_px("kbd"))
    assert dashboard._rail_width(False) == tkmod.RAIL_PX, "the plate fits 248 px"


def test_signature_the_latched_line_is_led_by_the_warning_glyph(dashboard,
                                                                controller):
    """"Stopped: every model latched" under the chord, on the plate, led by
    the warning glyph in SIGNAL (it was a signal square)."""
    dashboard.open()
    controller.is_estopped = True
    dashboard._sync_stop_button()
    assert dashboard._latched_row.is_packed
    assert dashboard._latched_row.master is dashboard._plate
    marks = dashboard._latched_mark.items
    assert marks and {i[0] for i in marks} == {"line"}
    assert {i[2]["fill"] for i in marks} == {theme.SIGNAL}


def test_signature_glyphs_come_from_the_theme_table():
    """Rule 6: every glyph is `theme.ICONS`, read, never redrawn here: each
    parses into strokes on the 20 px grid, and the disclosure turned 90 deg
    points down."""
    for name in theme.ICON_NAMES:
        strokes = tkmod._glyph_strokes(name)
        assert strokes, name
        assert all(0 <= v <= 20 for line in strokes for p in line for v in p), name
    (chevron,) = tkmod._glyph_strokes("disclosure")
    assert chevron == [(8.0, 5.25), (12.75, 10.0), (8.0, 14.75)]
    canvas = FakeCanvas()
    tkmod._draw_glyph(canvas, "disclosure", 0, 0, 20, theme.TEXT, turn=90)
    points = canvas.items[0][1]
    assert points[1] < points[3] and points[5] < points[3], "the tip is lowest"


def test_signature_a_key_carries_its_glyph_from_tk_8_7(monkeypatch, controller):
    """A key's glyph is a PhotoImage of `theme.icon_svg` in the legend's
    colour, from Tk 8.7 (its SVG photo format); below, none - no PNG
    pipeline, the legend alone."""
    class Homing(DemoPanel):
        @property
        def schema(self):
            return sch.schema(sch.section("Go", sch.button("Home", "home", role="go"),
                                          sch.button("Ask", "ask_me")))

    tkmod._ICON_IMAGES.clear()
    built = tkmod.TkPanelView(FakeWidget(), FakeController(Homing=Homing()), "Homing")
    home, ask = built._elements
    widget = built._widgets[id(home)]["widget"]
    image = widget.cget("image")
    assert isinstance(image, FakePhotoImage) and widget.cget("compound") == "left"
    assert image.data == theme.icon_svg("home", tkmod._design_px(tkmod.GLYPH_PX),
                                        theme.CAP)
    assert built._widgets[id(ask)]["widget"].cget("image") is None, "one per key, only some"
    built.close()
    monkeypatch.setattr(tkmod.tk, "TkVersion", 8.6, raising=False)
    tkmod._ICON_IMAGES.clear()
    assert tkmod._icon_image("home", theme.TEXT) is None
    tkmod._ICON_IMAGES.clear()


def test_signature_the_disclosure_is_a_small_key_that_sinks_open(tiered):
    """The disclosure: a 24 px key of the family (CAP face on its 3 px lip,
    KEY_RIM rim) holding the disclosure glyph, then the schema's words.
    Open, the key sinks (a SURFACE face, the lip folded) and the glyph
    turns down."""
    view, _panel = tiered
    opener = view._disclosures[2]

    def face():
        return [i for i in opener.key.items if i[2].get("tags") == ("face",)][0]

    assert opener.key.cget("width") == tkmod._design_px(theme.SPACE[7])
    assert face()[2]["fill"] == theme.CAP and face()[2]["outline"] == theme.KEY_RIM
    closed_top = face()[1][1]
    opener.widget.fire("<Button-1>")
    assert face()[2]["fill"] == theme.SURFACE
    drop, _lip = tkmod._key_pads("small", True)
    assert face()[1][1] == closed_top + drop


def test_signature_fields_are_sunk_windows_deep_in_the_tray(tiered):
    """A field is a sunk window: SURFACE on the sheet, DEEP inside the tray,
    with the MUTED floor lip; the tray and the pocket carry a 1 px top line."""
    view, _panel = tiered
    speed = view._widgets[id(element_of(view, "entry", "Speed:"))]
    size = view._widgets[id(element_of(view, "entry", "Step size:"))]
    assert speed["widget"].cget("background") == theme.SURFACE
    assert size["widget"].cget("background") == theme.DEEP
    assert speed["ring"].line.cget("background") == theme.MUTED
    assert tkmod._field_ground(theme.DEEP) == theme.SURFACE


def test_signature_a_select_is_a_key(view):
    """A dropdown is a select: a neutral key (KEY_RIM rim, KEY_LIP lip),
    ghost-toned while disabled."""
    source = element_of(view, "dropdown", "Source")
    entry = view._widgets[id(source)]
    ring = entry["ring"]
    assert ring.lip.cget("background") == theme.KEY_LIP
    assert ring.inner.cget("background") == theme.KEY_RIM
    view._set_enabled(source, False)
    assert ring.lip.cget("background") == theme.EDGE_SOFT
    assert ring.inner.cget("background") == theme.EDGE


def test_signature_the_view_names_no_colour_or_font_of_its_own():
    """Every colour and font in the Tk view comes from `theme`: no hex
    literal, no named colour, no named font family in `views/tk.py`."""
    import inspect
    tree = ast.parse(inspect.getsource(tkmod))
    named = {"white", "black", "red", "green", "blue", "gray", "grey", "yellow",
             "orange", "darkgreen", "helvetica", "arial", "courier", "times",
             "menlo", "monaco", "figtree", "rubik", "archivo"}
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value.strip().lower()
            if re.fullmatch(r"#[0-9a-f]{3,12}", value) or value in named:
                found.append((node.lineno, node.value))
    assert found == []


def test_signature_the_slider_is_a_fader_cap(dashboard, monkeypatch):
    """The slider's thumb is a fader cap of the key family - CAP face,
    KEY_RIM rim, KEY_LIP lip, an ink index line (`theme.FADER`) - and a
    ghost cap while disabled (BACKGROUND, EDGE, EDGE_SOFT), over a sunk
    groove in the field ground with a KEY_RIM edge. It was a round ink
    thumb on a flat track."""
    class Image:
        def __init__(self, width=0, height=0, **_kw):
            self.size, self.colours = (width, height), []

        def put(self, colour, to=None):
            self.colours.append(colour)

    class Style:
        def __init__(self):
            self.elements = {}

        def element_create(self, name, _kind, image, *states, **_kw):
            self.elements[name] = (image, dict((s[0], s[1]) for s in states))

        def layout(self, *_args):
            pass

    monkeypatch.setattr(tkmod.tk, "PhotoImage", Image)
    style = Style()
    dashboard._style_slider(style)
    live, states = style.elements["Station.Scale.slider"]
    ghost = states["disabled"]
    assert live.size == tuple(tkmod._design_px(v) for v in theme.FADER["cap"])
    assert {theme.CAP, theme.KEY_RIM, theme.KEY_LIP, theme.TEXT} <= set(live.colours)
    assert {theme.BACKGROUND, theme.EDGE, theme.EDGE_SOFT} <= set(ghost.colours)
    trough = style.elements["Station.Scale.trough"][0]
    assert trough.colours == [theme.KEY_RIM, theme.SURFACE]
    well = style.elements["StationWell.Scale.trough"][0]
    assert well.colours == [theme.KEY_RIM, theme.DEEP]


# ---------------------------------------------------------------------------
# One dashboard (owner ruling 2026-09-28): a hosted model is drawn on its
# host's page (`Model.HOST`, `state()["models"][name]["host"]`)
# ---------------------------------------------------------------------------

class MapPanel(DemoPanel):
    NAME = "Map"


class HostedPanel(DemoPanel):
    NAME = "Hosted"
    HOST = "Map"

    @property
    def schema(self):
        """Demo's sections as tier 1, plus a tier-2 section with its own
        disclosure words (Red Percent's "Red Percent details")."""
        built = super().schema
        built["sections"].append(sch.section(
            "Details", sch.entry("Note", "note", self.PARAMS["note"]),
            tier=2, disclosure="Hosted details"))
        return built


class HostController(FakeController):
    """`Controller.state()` publishes `host` per model: the host's NAME
    while the host is open, else None, never itself (as the real one)."""

    def state(self, name=None):
        station = super().state(name)
        if name is not None:
            return station
        for model, state in station["models"].items():
            host = getattr(self.panels[model], "HOST", None)
            state["host"] = host if host in self.panels and host != model else None
        return station


@pytest.fixture
def pack_chain(monkeypatch):
    """Tk's packing order per master, `after=`/`before=` honoured, and the
    stacking order `lift(above)` sets (the focus order): the stand-in's own
    `pack` only records that a widget was packed."""
    plain_pack = FakeWidget.pack

    def pack(self, **kwargs):
        plain_pack(self, **kwargs)
        master = kwargs.get("in_") or self.master
        chain = master.__dict__.setdefault("packed", [])
        anchor = kwargs.get("after") or kwargs.get("before")
        if self in chain and anchor is None:
            return
        if self in chain:
            chain.remove(self)
        if anchor is None:
            chain.append(self)
            return
        assert anchor in chain, "packed after a widget that is not packed there"
        index = chain.index(anchor) + (1 if kwargs.get("after") is not None else 0)
        chain.insert(index, self)

    def pack_forget(self):
        self.is_packed = False
        chain = getattr(self.master, "packed", None)
        if chain and self in chain:
            chain.remove(self)

    def lift(self, above=None):
        siblings = getattr(self.master, "children", None)
        if not siblings or self not in siblings:
            return
        siblings.remove(self)
        if above is not None and above in siblings:
            siblings.insert(siblings.index(above) + 1, self)
        else:
            siblings.append(self)

    monkeypatch.setattr(FakeWidget, "pack", pack)
    monkeypatch.setattr(FakeWidget, "pack_forget", pack_forget)
    monkeypatch.setattr(FakeWidget, "lift", lift, raising=False)
    monkeypatch.setattr(FakeWidget, "tkraise", lift, raising=False)


def _on_screen(widget):
    """Every widget on screen under `widget`, in the order the eye meets
    them: packed children in packing order, then gridded ones."""
    out = [widget]
    packed = list(getattr(widget, "packed", ()))
    gridded = [child for child in widget.children
               if child not in packed and child.grid_info and not child.is_destroyed]
    for child in packed + gridded:
        if not child.is_destroyed:
            out.extend(_on_screen(child))
    return out


def _is_inside(widget, ancestor):
    node = widget
    while node is not None:
        if node is ancestor:
            return True
        node = getattr(node, "master", None)
    return False


@pytest.fixture
def host_pair():
    return MapPanel(), HostedPanel()


@pytest.fixture
def host_dashboard(host_pair, setup_panel, pack_chain):
    host, hosted = host_pair
    controller = HostController(Map=host, Hosted=hosted)
    built = tkmod.TkDashboard(controller, setup_panel)
    built.open()
    yield built
    if not built._closing:
        built.close()


def test_host_a_hosted_model_has_no_page_of_its_own(host_dashboard):
    """The page list has one link: the host's. The overview has one entry."""
    assert list(host_dashboard._rail_items) == ["Map"]
    host_dashboard.show_overview()
    placed = [name for name, view in host_dashboard._panels.items()
              if name != host_dashboard.SETUP_TAB and view.frame.grid_info]
    assert placed == ["Map"]


def test_host_page_holds_the_hosted_group_in_the_contract_order(host_dashboard):
    """M's tier 1, the group (H's name, then H's tier 1), M's tier-2
    disclosure, H's tier-2 disclosure with H's own words."""
    host_dashboard.show_model("Map")
    host = host_dashboard._panels["Map"]
    hosted = host_dashboard._panels["Hosted"]
    shown = _on_screen(host.frame)
    marks = [host._tiers[1], hosted._title, hosted._tiers[1],
             host._disclosures[2].frame, hosted._disclosures[2].frame]
    assert all(mark in shown for mark in marks), "part of the page is not shown"
    assert [shown.index(mark) for mark in marks] == sorted(
        shown.index(mark) for mark in marks)
    assert hosted._title.cget("text") == "Hosted"
    assert hosted._disclosures[2].text == "Hosted details"


def test_host_a_hosted_group_follows_the_hosts_tier_one_in_the_tab_order(
        host_dashboard):
    """Tk traverses siblings in stacking order: the group's slot sits just
    above the host's tier 1, before the host's own disclosure."""
    host_dashboard.show_model("Map")
    host = host_dashboard._panels["Map"]
    hosted = host_dashboard._panels["Hosted"]
    body = host._body.children
    slot = next(child for child in body if _is_inside(hosted.frame, child))
    assert (body.index(host._tiers[1]) < body.index(slot)
            < body.index(host._disclosures[2].frame))


def test_host_the_group_heading_is_the_name_one_step_down_and_the_mode(
        host_dashboard, host_pair):
    host_dashboard.show_model("Map")
    host = host_dashboard._panels["Map"]
    hosted = host_dashboard._panels["Hosted"]
    assert host._title.cget("font") == tkmod._font(tkmod.STEP_2, bold=True)
    assert hosted._title.cget("font") == tkmod._font(tkmod.STEP_1, bold=True)
    word = hosted._state_word
    assert word.cget("text") == "Idle"
    host_pair[1].mode = "no_region"
    hosted._refresh()
    assert word.cget("text") == "No region"
    assert word.cget("foreground") == theme.MUTED
    host_pair[1].mode = "running"
    hosted._refresh()
    assert word.cget("text") == "Running"
    assert host._state_word is None, "the host's own head is unchanged"


def test_host_a_command_in_the_group_reaches_the_hosted_model(host_dashboard,
                                                            host_pair):
    host_dashboard.show_model("Map")
    host = host_dashboard._panels["Map"]
    hosted = host_dashboard._panels["Hosted"]
    go = next(e for e in hosted._elements if e.get("command") == "go")
    assert _is_inside(widget_of(hosted, go), host.frame)
    click(hosted, go)
    controller = host_dashboard.controller
    assert [call[:2] for call in controller.calls if call[1] == "go"] == [("Hosted", "go")]
    assert host_pair[1].commands == [("go", 1.0)]
    assert host_pair[0].commands == []


def test_host_a_refusal_in_the_group_lands_at_its_control(host_dashboard):
    host_dashboard.show_model("Map")
    hosted = host_dashboard._panels["Hosted"]
    host = host_dashboard._panels["Map"]
    refuse = next(e for e in hosted._elements if e.get("command") == "refuse_me")
    click(hosted, refuse)
    assert hosted._notice is not None
    assert hosted._notice.cget("text") == "the bench is busy"
    assert _is_inside(hosted._notice, host.frame)
    assert host._notice is None


def test_host_closing_the_host_gives_the_hosted_model_its_page_back(
        host_dashboard):
    controller = host_dashboard.controller
    host_dashboard.show_model("Map")
    assert _is_inside(host_dashboard._panels["Hosted"].frame,
                      host_dashboard._panels["Map"].frame)
    controller.remove("Map")
    SCHEDULER.pump()
    assert "Map" not in host_dashboard._panels
    hosted = host_dashboard._panels["Hosted"]
    assert not hosted.frame.is_destroyed
    assert list(host_dashboard._rail_items) == ["Hosted"]
    host_dashboard.show_overview()
    assert hosted.frame.grid_info, "the hosted model is an overview entry again"
    assert host_dashboard.show_model("Hosted")
    assert host_dashboard._opened == "Hosted"
    go = next(e for e in hosted._elements if e.get("command") == "go")
    click(hosted, go)
    assert [c[:2] for c in controller.calls if c[1] == "go"] == [("Hosted", "go")]


def test_host_launched_after_the_hosted_model_takes_it_onto_its_page(
        host_dashboard):
    controller = host_dashboard.controller
    controller.remove("Map")
    SCHEDULER.pump()
    assert list(host_dashboard._rail_items) == ["Hosted"]
    controller.reopen("Map")
    SCHEDULER.pump()
    assert list(host_dashboard._rail_items) == ["Map"]
    host_dashboard.show_model("Map")
    hosted = host_dashboard._panels["Hosted"]
    assert _is_inside(hosted._title, host_dashboard._panels["Map"].frame)


def test_host_closing_the_hosted_model_leaves_the_host_untouched(host_dashboard):
    controller = host_dashboard.controller
    host_dashboard.show_model("Map")
    host = host_dashboard._panels["Map"]
    hosted = host_dashboard._panels["Hosted"]
    opener = hosted._disclosures[2].frame
    assert hosted._title in _on_screen(host.frame) and opener in _on_screen(host.frame)
    controller.remove("Hosted")
    SCHEDULER.pump()
    assert "Hosted" not in host_dashboard._panels
    shown = _on_screen(host.frame)
    assert hosted._title not in shown and opener not in shown
    assert host._tiers[1] in shown and host._disclosures[2].frame in shown
    assert host_dashboard._panels["Map"] is host
    assert list(host_dashboard._rail_items) == ["Map"]


def test_host_navigating_to_the_hosted_model_lands_on_the_hosts_page(
        host_dashboard, monkeypatch):
    scrolled = []
    monkeypatch.setattr(host_dashboard._sheet, "scroll_into_view", scrolled.append)
    host_dashboard.show_overview()
    assert host_dashboard.show_model("Hosted")
    assert host_dashboard._opened == "Map"
    assert scrolled == [host_dashboard._panels["Hosted"].frame]


def test_host_the_hosted_stop_state_is_folded_into_the_hosts_link(
        host_dashboard):
    controller = host_dashboard.controller
    controller.stop = {"latched": ["Hosted"], "unconfirmed": [], "every": False}
    host_dashboard._sync_stop_button()
    canvas, tooltip = host_dashboard._rail_marks["Map"]
    assert [item[0] for item in canvas.items] == ["rect"]
    assert tooltip.text == tkmod.RAIL_MARK_WORDS["latched"]
    controller.stop = {"latched": ["Hosted"], "unconfirmed": ["Hosted"],
                       "every": False}
    host_dashboard._sync_stop_button()
    canvas, tooltip = host_dashboard._rail_marks["Map"]
    assert tooltip.text == tkmod.RAIL_MARK_WORDS["unconfirmed"]
    assert host_dashboard._rail_lamp_state("Map") == "unconfirmed"


def test_host_the_hosted_stop_state_is_folded_into_the_hosts_overview_entry(
        host_dashboard, host_pair):
    host_dashboard.show_overview()
    host = host_dashboard._panels["Map"]
    host_pair[1].stop_confirmed = False
    host._refresh()
    assert host._is_unconfirmed, "the host's entry marks the hosted model's stop"
    host_pair[1].stop_confirmed = None
    host._refresh()
    assert not host._is_unconfirmed


def test_host_the_real_pair_in_sim_is_one_dashboard(setup_panel, pack_chain,
                                                    tmp_path, monkeypatch):
    """Red Percent on the Transfer Map page, through the real Controller."""
    monkeypatch.setenv("STATION_MAP_DB", str(tmp_path / "map.sqlite"))
    from controller.controller import Controller
    from model.red_monitor import RedMonitor
    from model.transfer_map import TransferMap
    controller = Controller()
    red_model = RedMonitor(sim=True)
    red_model.output_root = tmp_path / "runs"
    controller.add("Red Percent", red_model, {})
    controller.add("Transfer Map", TransferMap(sim=True), {})
    runs = []
    real_run = controller.run

    def run(name, command, inputs=None, args=()):
        runs.append((name, command))
        return real_run(name, command, inputs, args)

    monkeypatch.setattr(controller, "run", run)
    built = tkmod.TkDashboard(controller, setup_panel)
    try:
        built.open()
        assert list(built._rail_items) == ["Transfer Map"]
        built.show_model("Red Percent")
        assert built._opened == "Transfer Map"
        page = built._panels["Transfer Map"]
        red = built._panels["Red Percent"]
        shown = _on_screen(page.frame)
        marks = [page._tiers[1], red._title, red._tiers[1],
                 page._disclosures[2].frame, red._disclosures[2].frame]
        assert [shown.index(m) for m in marks] == sorted(shown.index(m) for m in marks)
        assert page._disclosures[2].text == "Configure Transfer Map"
        assert red._disclosures[2].text == "Red Percent details"
        start = next(e for e in red._elements if e.get("command") == "start_run")
        assert _is_inside(widget_of(red, start), page.frame)
        # Gated by Red Percent's own mode (no region yet), not the page's.
        red._refresh()
        assert red._gate_reason(start) == "Set a capture region first"
        controller.run("Red Percent", "set_region", args=(0, 0, 10, 10))
        red._refresh()
        click(red, start)
        # (asked, then confirmed: both against Red Percent's name)
        assert {r[0] for r in runs if r[1] == "start_run"} == {"Red Percent"}
        assert red_model.is_running
        controller.run("Red Percent", "end_run")
        controller.remove("Transfer Map")
        SCHEDULER.pump()
        assert list(built._rail_items) == ["Red Percent"]
        assert not built._panels["Red Percent"].frame.is_destroyed
    finally:
        if not built._closing:
            built.close()
        controller.close()


def test_host_the_wheel_over_the_hosted_well_scrolls_that_well(host_dashboard):
    host_dashboard.show_model("Map")
    host = host_dashboard._panels["Map"]
    hosted = host_dashboard._panels["Hosted"]
    hosted.set_disclosure(2, True)
    try:
        assert hosted.well_canvas is not None
        assert _is_inside(hosted._well, host.frame)
        assert _is_inside(hosted._well_holder, host._tail_slot)
        assert host_dashboard._scroll_device_well(1, hosted._tiers[2])
        assert hosted.well_canvas.scrolled == [(1, "units")]
        assert host.well_canvas is None or host.well_canvas.scrolled == []
    finally:
        hosted.set_disclosure(2, False)


# ---------------------------------------------------------------------------
# rb-ack (A3): the acknowledgement is a dialog like the latch-release one
# ---------------------------------------------------------------------------

def _attention(index, title="Idle Timeout", message="Stepper Probe was idle "
               "for 300 s, so it was powered down.", severity="warning"):
    return Event(index, severity, "Stepper Probe", title, message, None, True,
                 0.0)


def _ack_texts(dialog):
    return dialog.title_label.cget("text"), dialog.body.cget("text")


def test_ack_an_acknowledged_event_opens_a_titled_dialog_with_one_key(
        dashboard, tk_harness):
    """The event's title is the dialog's title (and its heading), its
    message the body, in the confirm dialog's type; one key, Understood,
    holds the focus; nothing grabs."""
    dashboard.open()
    dashboard._on_event(_attention(1))
    SCHEDULER.pump()
    dialog = dashboard._ack_dialog
    assert dialog is not None and not dialog.top.is_destroyed
    assert dialog.top.titles == ["Idle timeout"]
    assert _ack_texts(dialog) == (
        "Idle timeout",
        "Stepper Probe was idle for 300 s, so it was powered down.")
    assert dialog.body.cget("font") == tkmod._font()
    assert dialog.body.cget("wraplength") == 420
    assert dialog.key.widget.cget("text") == "Understood"
    presses = [w for w in _all_widgets(dialog.top)
               if w.cget("takefocus") == 1]
    assert presses == [dialog.key.widget], "one key, and only one"
    assert Focus.current is dialog.key.widget, "Understood is the default"
    assert GRABS == [], "an application-modal grab takes the stop away"
    assert tk_harness.errors == []


def _all_widgets(widget):
    out = []
    for child in getattr(widget, "children", []):
        out.append(child)
        out.extend(_all_widgets(child))
    return out


def test_ack_return_escape_and_the_close_button_all_acknowledge(dashboard):
    dashboard.open()
    for index, gesture in enumerate(("<Return>", "<Escape>", "close")):
        dashboard._on_event(_attention(index, title=f"Fault {index}"))
        SCHEDULER.pump()
        dialog = dashboard._ack_dialog
        if gesture == "close":
            dialog.top.protocols["WM_DELETE_WINDOW"]()
        else:
            dialog.top.fire(gesture)
        assert dialog.top.is_destroyed, gesture
        assert dashboard._alerts == [] and dashboard._ack_dialog is None, gesture


def test_ack_a_second_alert_queues_behind_the_first(dashboard):
    """Queue, not stack: the second waits for the first's Understood."""
    dashboard.open()
    dashboard._on_event(_attention(1))
    dashboard._on_event(_attention(2, title="Rotator Unreachable",
                                   message="The stage stopped answering."))
    SCHEDULER.pump()
    first = dashboard._ack_dialog
    assert _ack_texts(first)[0] == "Idle timeout"
    assert first.count.cget("text") == "1 more waiting"
    assert [e.title for e in dashboard._alerts] == ["Idle Timeout",
                                                   "Rotator Unreachable"]
    first.key.widget.fire("<Button-1>")
    assert first.top.is_destroyed
    second = dashboard._ack_dialog
    assert second is not first and not second.top.is_destroyed
    assert _ack_texts(second) == ("Rotator unreachable",
                                  "The stage stopped answering.")
    assert second.count.cget("text") == ""
    second.key.widget.fire("<Button-1>")
    assert dashboard._ack_dialog is None and dashboard._alerts == []
    assert Focus.current is dashboard._stop_button, "focus goes back to the stop"


def test_ack_a_repeat_of_the_open_title_counts_and_does_not_reopen(dashboard):
    dashboard.open()
    dashboard._on_event(_attention(1))
    SCHEDULER.pump()
    dialog = dashboard._ack_dialog
    dashboard._on_event(_attention(2, message="Stepper Probe was idle for "
                                   "301 s, so it was powered down."))
    SCHEDULER.pump()
    assert dashboard._ack_dialog is dialog and not dialog.top.is_destroyed
    assert len(dashboard._alerts) == 2, "both are kept (HC-2)"
    assert dialog.body.cget("text") == ("Stepper Probe was idle for 301 s, so "
                                        "it was powered down. (x2)")
    dialog.key.widget.fire("<Button-1>")
    assert dashboard._ack_dialog is None and dashboard._alerts == []


def test_ack_the_stop_still_fires_while_the_dialog_is_open(dashboard,
                                                          controller):
    dashboard.open()
    dashboard._on_event(_attention(1))
    SCHEDULER.pump()
    dialog = dashboard._ack_dialog
    ALL_BINDINGS["<Control-period>"](FakeEvent())
    assert controller.estop_calls == 1 and controller.is_estopped
    assert not dialog.top.is_destroyed, "the stop does not answer for the operator"
    assert GRABS == []


def test_ack_the_acknowledgement_is_logged_at_debug(dashboard, monkeypatch):
    said = []
    monkeypatch.setattr(tkmod.events, "debug",
                        lambda title, message, **kw: said.append((title, message)))
    dashboard.open()
    dashboard._on_event(_attention(1))
    SCHEDULER.pump()
    dashboard._ack_dialog.key.widget.fire("<Button-1>")
    assert ("Alert Acknowledged", "warning/Idle Timeout") in said, said


def test_ack_close_takes_the_dialog_down(dashboard):
    dashboard.open()
    dashboard._on_event(_attention(1))
    SCHEDULER.pump()
    dialog = dashboard._ack_dialog
    dashboard.close()
    assert dialog.top.is_destroyed


# ---------------------------------------------------------------------------
# rb-restart R1/R6: an action on an acknowledged notice
# ---------------------------------------------------------------------------

class ActionSetup(Panel):
    """A Setup stand-in with the two update commands the prompts name."""
    NAME = "Setup"

    def __init__(self):
        super().__init__()
        self.restarts, self.applies = [], []
        self.refuse = ""

    @property
    def schema(self):
        return sch.schema(sch.section(
            "Update",
            sch.button("Update now", "apply_update", role="go"),
            sch.button("Restart", "restart_station"),
            layout="row"))

    def apply_update(self, confirmed=False):
        if self.refuse:
            raise Refused(self.refuse)
        if not confirmed:
            raise NeedsConfirm("Update the station now?", "apply_update")
        self.applies.append(confirmed)
        return True

    def restart_station(self, confirmed=False):
        if not confirmed:
            raise NeedsConfirm("Restart the station now?", "restart_station")
        self.restarts.append(confirmed)
        return True


RESTART_ACTION = {"label": "Restart now", "name": "__setup__",
                  "command": "restart_station", "args": [True]}
UPDATE_ACTION = {"label": "Update now", "name": "__setup__",
                 "command": "apply_update", "args": []}


def _prompt(index, action, title="Restart Needed",
            message="Updated to def5678. Restart the station to run it."):
    return Event(index, "warning", "Setup", title, message, None, True, 0.0,
                 action)


@pytest.fixture
def action_board(controller):
    setup = ActionSetup()
    built = tkmod.TkDashboard(controller, setup)
    built.open()
    yield built, setup
    if not built._closing:
        built.close()


def test_restart_an_action_shows_two_keys_and_return_runs_it(action_board,
                                                             tk_harness):
    board, setup = action_board
    board._on_event(_prompt(1, RESTART_ACTION))
    SCHEDULER.pump()
    dialog = board._ack_dialog
    assert dialog.key.widget.cget("text") == "Restart now"
    assert dialog.later.widget.cget("text") == "Later"
    presses = [w for w in _all_widgets(dialog.top) if w.cget("takefocus") == 1]
    assert sorted(w.cget("text") for w in presses) == ["Later", "Restart now"]
    assert Focus.current is dialog.key.widget, "the action is the default"
    dialog.top.fire("<Return>")
    assert dialog.top.is_destroyed and board._ack_dialog is None
    # The dialog was the question: confirmed, and nothing asked again.
    assert setup.restarts == [True]
    assert tk_harness.asked == []


def test_restart_escape_and_the_close_button_are_later(action_board):
    board, setup = action_board
    for index, gesture in enumerate(("<Escape>", "close", "click")):
        board._on_event(_prompt(index, RESTART_ACTION, message=f"Updated {index}."))
        SCHEDULER.pump()
        dialog = board._ack_dialog
        if gesture == "close":
            dialog.top.protocols["WM_DELETE_WINDOW"]()
        elif gesture == "click":
            dialog.later.widget.fire("<Button-1>")
        else:
            dialog.top.fire(gesture)
        assert dialog.top.is_destroyed and board._alerts == [], gesture
    assert setup.restarts == []


def test_restart_the_action_key_click_runs_it_too(action_board):
    board, setup = action_board
    board._on_event(_prompt(1, RESTART_ACTION))
    SCHEDULER.pump()
    board._ack_dialog.key.widget.fire("<Button-1>")
    assert setup.restarts == [True]


def test_restart_an_action_that_asks_asks_as_a_button_press_does(action_board,
                                                                tk_harness):
    """Update now from the Update Ready dialog is the panel's own press:
    its confirmation is asked, and a No runs nothing."""
    board, setup = action_board
    tk_harness.confirm_answer = False
    board._on_event(_prompt(1, UPDATE_ACTION, title="Update Ready",
                            message="2 new commits are ready: x."))
    SCHEDULER.pump()
    board._ack_dialog.key.widget.fire("<Button-1>")
    assert tk_harness.asked == [("confirm", "Update the station now?")]
    assert setup.applies == []
    tk_harness.confirm_answer = True
    board._on_event(_prompt(2, UPDATE_ACTION, title="Update Ready",
                            message="3 new commits are ready: y."))
    SCHEDULER.pump()
    board._ack_dialog.key.widget.fire("<Button-1>")
    assert setup.applies == [True]


def test_restart_a_refused_action_shows_on_its_panel(action_board, monkeypatch):
    board, setup = action_board
    setup.refuse = "Close every model first."
    shown = []
    view = board._panels[board.SETUP_TAB]
    monkeypatch.setattr(view, "_show_refused", lambda reason, *a: shown.append(reason))
    board._on_event(_prompt(1, UPDATE_ACTION, title="Update Ready"))
    SCHEDULER.pump()
    board._ack_dialog.key.widget.fire("<Button-1>")
    assert "Close every model first." in shown


def test_restart_the_dialog_stays_over_the_window_without_a_grab(
        action_board, monkeypatch):
    """R6: transient for the main window and lifted on every show."""
    board, _ = action_board
    transient = []
    monkeypatch.setattr(FakeWidget, "winfo_toplevel", lambda self: self,
                        raising=False)
    monkeypatch.setattr(FakeRoot, "transient",
                        lambda self, master=None: transient.append(master),
                        raising=False)
    board._on_event(_prompt(1, RESTART_ACTION))
    SCHEDULER.pump()
    dialog = board._ack_dialog
    assert transient == [board.root] and dialog.top.lifted >= 1
    before = dialog.top.lifted
    board._on_event(_prompt(2, RESTART_ACTION, message="Updated again."))
    SCHEDULER.pump()
    assert board._ack_dialog is dialog and dialog.top.lifted > before
    assert GRABS == []


def test_restart_a_notice_without_an_action_keeps_one_key(action_board):
    board, _ = action_board
    board._on_event(_attention(1))
    SCHEDULER.pump()
    dialog = board._ack_dialog
    assert dialog.later is None and dialog.key.widget.cget("text") == "Understood"


def test_restart_the_base_runs_an_action_without_a_drawn_panel(tk_harness):
    """`Dashboard.run_action` with no PanelView for the name: the same call,
    the Dashboard's own confirmation, a refusal as a tray warning."""
    from views.base import Dashboard
    setup = ActionSetup()

    class Bare(Dashboard):
        asked = []

        def _confirm(self, prompt):
            self.asked.append(prompt)
            return True

    board = Bare(FakeController(), setup)
    assert board.run_action(UPDATE_ACTION).is_ok
    assert board.asked == ["Update the station now?"] and setup.applies == [True]
    assert board.run_action(RESTART_ACTION).is_ok and setup.restarts == [True]
    assert board.run_action(None) is None
