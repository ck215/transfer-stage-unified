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
        PACK_ORDER.append((self, kwargs))

    def grid(self, **kwargs):
        self.grid_info = kwargs

    def grid_configure(self, **kwargs):
        self.grid_info = dict(self.grid_info or {}, **kwargs)

    def grid_remove(self):
        self.grid_info = None

    def pack_forget(self):
        self.is_packed = False

    def grid_columnconfigure(self, column, **options):
        self.column_weights[column] = options

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
        pass

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

    def title(self, *_args):
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

    def attributes(self, *_args):
        pass


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


class FakeTtkModule:
    Frame = FakeWidget
    Combobox = FakeWidget
    Scrollbar = FakeWidget


SCHEDULER = Scheduler()
#: `bind_all` is application-wide in Tk, so the stand-in keeps one table for
#: the whole "application" — which is what lets a test see that a closed
#: panel has let go of the wheel.
ALL_BINDINGS = {}
#: Every `pack`, in order. Pack order IS allocation order in Tk, which is the
#: whole of why the FULL STOP bar can or cannot be pushed off the window.
PACK_ORDER = []


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
                sch.region_select("Pick area", "set_region", model_attr="region"),
                sch.file_save("Save", "save_run"),
                sch.file_open("Load", "load_run"),
                _internal("hidden"),
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
        self.is_estopped = False
        self.clear_needs_confirm = False
        self.is_closed = False
        self._subscribers = []

    # -- what a view reads
    def schema(self, name):
        return self.panels[name].schema

    def state(self, name=None):
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
        self.is_estopped = True
        return {name: True for name in self.panels}

    def clear_estop_all(self, confirmed=False):
        self.clear_calls += 1
        if self.clear_needs_confirm and not confirmed:
            return Result(Result.CONFIRM, reason="Release the latch?",
                          command="clear_estop_all")
        self.is_estopped = False
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
    dialogs = FakeDialogs()
    monkeypatch.setattr(tkmod, "tk", FakeTkModule)
    monkeypatch.setattr(tkmod, "ttk", FakeTtkModule)
    monkeypatch.setattr(tkmod, "filedialog", dialogs)
    monkeypatch.setattr(tkmod, "messagebox", dialogs)
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
    toggle = element_of(view, "toggle")
    panel.is_running = True
    view._refresh()
    widget = widget_of(view, toggle)
    expected = theme.toggle_colors(toggle, True)
    assert widget.cget("background") == expected["background"]
    assert widget.cget("foreground") == expected["foreground"]
    # Sentence case, as the Web view renders it: "RUNNING" is the schema's
    # word, "Running" is how every frontend shows it.
    assert widget.cget("text") == "Running"
    # A danger toggle is the theme's danger colour, not a literal red.
    assert expected["background"] == theme.colors("danger")[0]

    panel.is_running = False
    view._refresh()
    off = theme.toggle_colors(toggle, False)
    assert widget.cget("background") == off["background"]
    assert widget.cget("text") == "Stopped"


def test_a_command_is_outlined_by_a_frame_so_aqua_draws_it(view, panel):
    """Aqua does not draw a Label's highlight ring, so an OFF toggle (its
    role outlined on the panel) rendered as bare text. The outline is a
    one-pixel frame around the label; keyboard focus turns it trace."""
    toggle = element_of(view, "toggle")
    panel.is_running = False
    view._refresh()
    entry = view._widgets[id(toggle)]
    outline = entry["outline"]
    assert widget_of(view, toggle).master is outline
    resting = outline.cget("background")
    assert resting == entry["border"]
    assert resting not in (tkmod._page(), theme.TRACE), "an outline you can see"
    widget_of(view, toggle).fire("<FocusIn>")
    assert outline.cget("background") == theme.TRACE
    widget_of(view, toggle).fire("<FocusOut>")
    assert outline.cget("background") == resting


def test_a_danger_command_is_outlined_never_filled(view):
    """One red: only the stop object is filled with the signal colour. A
    danger command ("Go" here, a model's own "Stop" in the app) is outlined
    in it, as the Web view does."""
    go = element_of(view, "button", "Go")
    entry = view._widgets[id(go)]
    assert widget_of(view, go).cget("background") != theme.SIGNAL
    assert entry["outline"].cget("background") == theme.SIGNAL


def test_a_command_has_hover_and_disabled_states(view, panel):
    go = element_of(view, "button", "Go")
    widget = widget_of(view, go)
    resting = widget.cget("background")
    widget.fire("<Enter>")
    assert widget.cget("background") != resting
    widget.fire("<Leave>")
    assert widget.cget("background") == resting
    speed = element_of(view, "entry", "Speed")
    panel.mode = "running"
    view._refresh()
    assert widget_of(view, speed).cget("state") == "disabled"


def test_labels_are_sentence_case_without_a_colon(view):
    """The Web view's `sentenceCase`: the schema's "Speed now:" reads "Speed
    now", section titles come down to sentence case."""
    captions = [child.cget("text") for child in widget_of(
        view, element_of(view, "readonly", "Speed now:")).master.children]
    assert "Speed now" in captions and "Speed now:" not in captions
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

    panel.fault_reason = "over temperature"
    view._refresh()
    assert view._widgets[id(element)]["var"].get() == "over temperature"
    assert widget.cget("text") == "over temperature"
    # A live value is drawn in the trace colour, on the panel.
    assert widget.cget("foreground") == theme.TRACE
    assert widget.cget("background") == tkmod._page()


def test_a_readout_sits_where_an_entry_sits_in_a_column_section(view):
    """One value column per section, the same width for every entry and
    every readout: both fill it, so they end at the same right edge."""
    readout = element_of(view, "readonly", "Speed now:")
    entry = element_of(view, "entry", "Speed")
    assert _cell(view, readout)["column"] == _cell(view, entry)["column"] == 1
    assert _cell(view, readout)["sticky"] == _cell(view, entry)["sticky"] == "ew"
    for element in (readout, entry):
        weights = widget_of(view, element).master.column_weights
        assert weights[1]["minsize"] == tkmod.VALUE_PX
        assert weights[0]["weight"] == 1     # the caption takes the slack


def test_a_long_readout_is_elided_and_its_whole_text_is_the_tooltip(
        view, panel, monkeypatch):
    """Nothing clips silently: a value too long for its cell ends in an
    ellipsis and the full text is on hover."""
    element = element_of(view, "readonly", "Fault:")
    widget = widget_of(view, element)
    monkeypatch.setattr(tkmod, "_text_width", lambda _font, text: 10 * len(text))
    widget.winfo_width = lambda: 100
    widget.options["padx"] = 0
    panel.fault_reason = "identifying /dev/cu.debug-console (2 of 2)..."
    view._refresh()
    shown = widget.cget("text")
    assert shown.endswith(tkmod.ELLIPSIS) and len(shown) * 10 <= 100
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
    assert entry.cget("highlightthickness") == 1
    assert entry.cget("background") == theme.BACKGROUND
    assert readout.cget("anchor") == "e"
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


def _cell(view, element):
    return widget_of(view, element).grid_info


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
    assert widget_of(row_view, port).master is widget_of(row_view, dc_port).master
    assert _cell(row_view, dc_port)["column"] == _cell(row_view, port)["column"]
    assert _cell(row_view, dc_found)["column"] == _cell(row_view, found)["column"]
    assert _cell(row_view, dc_port)["row"] == _cell(row_view, port)["row"] + 1


def test_a_shorter_row_still_ends_its_status_in_the_status_column(row_view):
    """The heater row carries no gamepad dropdown. Its status goes in the
    Status column all the same, and its Gamepad cell is left empty."""
    heater_found = row_view._elements[HEATER_FOUND]
    assert _cell(row_view, heater_found)["column"] == _cell(
        row_view, row_view._elements[STEPPER_FOUND])["column"]


def test_a_table_says_each_caption_once_in_a_header_row(row_view):
    """Captions shared by the rows ("Port", "Gamepad", "Detected") are a
    header, once; the rows carry values only."""
    table = widget_of(row_view, row_view._elements[STEPPER_PORT]).master
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
            == captions["Stepper Probe"].grid_info["row"] + 1)


def test_the_status_of_a_row_takes_the_slack(row_view):
    """The status column absorbs the window's spare width, so a long status
    has room; it reads left to right, like the text it is."""
    found = row_view._elements[STEPPER_FOUND]
    widget = widget_of(row_view, found)
    assert _cell(row_view, found)["sticky"] == "ew"
    assert widget.cget("anchor") == "w"
    assert widget.master.column_weights[_cell(row_view, found)["column"]]["weight"] == 1


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


def test_a_column_section_still_stacks_one_element_per_row(row_view):
    entry, button = row_view._elements[COUNT], row_view._elements[LAUNCH]
    strip = row_view._widgets[id(button)]["outline"].master
    assert strip.grid_info["row"] == _cell(row_view, entry)["row"] + 1
    assert _cell(row_view, entry)["column"] == 1      # column 0 is its caption


def test_consecutive_commands_share_one_line(view):
    """A section's run of commands is one line of buttons, not a stack of
    full-width bars (the Stepper Probe's System control)."""
    lines = {view._widgets[id(element_of(view, "button", text))]["outline"].master
             for text in ("Go", "Refuse", "Ask")}
    assert len(lines) == 1


def test_sections_flow_into_columns_by_width(view):
    """One column narrow, two at the default window, up to three when wide
    - never more columns than the run has sections."""
    def homes():
        placed = {}
        for widget, options in PACK_ORDER:
            if widget in view._runs[0]["sections"]:
                placed[widget] = options.get("in_")
        return set(placed.values())

    view._reflow(500)
    assert view._layout_columns == 1 and len(homes()) == 1
    view._reflow(900)
    assert view._layout_columns == 2 and len(homes()) == 2
    view._reflow(1400)
    assert view._layout_columns == 3
    assert len(homes()) == 2, "two sections: no empty third column"


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


def test_refused_reaches_the_status_line_and_not_a_popup(view, tk_harness):
    click(view, element_of(view, "button", "Refuse"))
    assert "the bench is busy" in view._status.cget("text")
    assert tk_harness.errors == [], "a refusal is not a popup"


def test_a_later_success_clears_the_status_line(view):
    click(view, element_of(view, "button", "Refuse"))
    assert view._status.cget("text")
    click(view, element_of(view, "button", "Go"))
    assert view._status.cget("text") == ""


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
    assert "at most 10" in view._status.cget("text")

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
    assert widget_of(view, speed).cget("foreground") == theme.colors("warning")[0]


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

        def pick(self):
            return (10, 20, 30, 40)

    monkeypatch.setattr(tkmod, "_RegionPicker", Picked)
    click(view, element_of(view, "region_select"))
    assert panel.region == {"left": 10, "top": 20, "width": 30, "height": 40}


def test_a_cancelled_region_shows_the_reason_and_runs_nothing(view, panel,
                                                              monkeypatch):
    class Cancelled:
        def __init__(self, _master):
            self.reason = "Region selection cancelled."

        def pick(self):
            return None

    monkeypatch.setattr(tkmod, "_RegionPicker", Cancelled)
    click(view, element_of(view, "region_select"))
    assert panel.region is None
    assert "cancel" in view._status.cget("text").lower()


def test_the_captured_region_is_drawn_not_announced(view, panel, tk_harness):
    panel.region = {"left": 1, "top": 2, "width": 3, "height": 4}
    view._refresh()
    element = element_of(view, "region_select")
    assert view._widgets[id(element)]["var"].get() == sch.format_region(panel.region)
    assert tk_harness.errors == []


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


def test_closing_a_tab_removes_the_model(dashboard, controller):
    dashboard.open()
    index = list(dashboard.notebook.tabs()).index(str(dashboard._frames["Demo"]))
    dashboard.notebook.close_tab(index)
    assert controller.removed == ["Demo"]
    SCHEDULER.pump()
    assert "Demo" not in dashboard._panels


def test_a_close_click_on_the_tab_bar_closes_that_tab(dashboard, controller,
                                                      monkeypatch):
    dashboard.open()
    monkeypatch.setattr(dashboard.notebook, "index", lambda _spec: 1)
    dashboard.notebook._on_middle_press(FakeEvent(x=5, y=5))
    assert controller.removed == ["Demo"]


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


def test_the_toolbar_offers_setup_only_while_it_is_minimised(dashboard,
                                                             controller):
    dashboard.open()
    assert dashboard._setup_button.is_packed is False
    _launch_a_model(dashboard, controller)
    assert dashboard._setup_button.is_packed is True

    dashboard._setup_button.fire("<Button-1>")
    assert dashboard._is_setup_collapsed is False
    assert _setup_tab_is_shown(dashboard)
    assert dashboard._setup_button.is_packed is False


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


def test_the_stop_bar_and_the_event_log_cannot_be_pushed_off_the_window(
        controller, setup_panel, monkeypatch):
    """SAFETY. Pack order is allocation order: the notebook is the one widget
    that expands, so everything it must never push off the window is packed
    before it. A FULL STOP the operator cannot reach is not a stop."""
    packed = []
    monkeypatch.setattr(tkmod.ClosableNotebook, "pack",
                        lambda self, **kwargs: PACK_ORDER.append((self, kwargs)),
                        raising=False)
    built = tkmod.TkDashboard(controller, setup_panel)
    packed = [widget for widget, _ in PACK_ORDER]
    notebook = packed.index(built.notebook)
    assert packed.index(built._stop_button) < notebook
    assert packed.index(built._event_text.master.master) < notebook
    # nothing docked at an edge is packed after the expanding widget
    docked = [index for index, (_, options) in enumerate(PACK_ORDER)
              if options.get("side") in ("bottom", "top")
              and not options.get("expand")]
    assert max(docked) < notebook
    # The disc lives in the stop bar, and the bar is docked at the bottom.
    assert built._stop_button.master is built._stop_bar
    assert packed.index(built._stop_bar) < notebook
    assert PACK_ORDER[packed.index(built._stop_bar)][1]["side"] == "bottom"
    built.close()


def test_the_stop_button_label_follows_the_controller(dashboard, controller):
    """The Web view's mushroom, in Tk: `Stop`, then `Clear` once latched; the
    disc stays signal red (it is never dimmed) and its ring turns trace."""
    def faces():
        return [item[2]["text"] for item in dashboard._stop_button.items
                if item[0] == "text"]

    def disc():
        return [item[2] for item in dashboard._stop_button.items
                if item[0] == "oval" and item[2].get("fill")][-1]

    dashboard.open()
    dashboard._sync_stop_button()
    assert faces() == ["Stop"]
    assert disc()["fill"] == theme.SIGNAL
    assert dashboard._stop_hint.cget("text") == tkmod.STOP_HINT

    controller.is_estopped = True
    dashboard._sync_stop_button()
    assert faces() == ["Clear"]
    assert disc()["fill"] == theme.SIGNAL
    assert disc()["outline"] == theme.TRACE
    assert dashboard._stop_hint.cget("text") == tkmod.CLEAR_HINT


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


def test_a_models_own_stop_is_the_same_disc(controller, monkeypatch):
    """A Safety section's stop toggle (`is_estopped`) is the stop object,
    bench-sized, not a red bar."""
    class Safe(DemoPanel):
        def __init__(self):
            super().__init__()
            self.is_estopped = False

        @property
        def schema(self):
            return sch.schema(sch.section("Safety", sch.toggle(
                "FULL STOP", "is_estopped", "set_running", "LATCHED",
                "FULL STOP", on_role="danger", off_role="danger")))

    built = tkmod.TkPanelView(FakeWidget(), FakeController(Safe=Safe()), "Safe")
    element = built._elements[0]
    entry = built._widgets[id(element)]
    assert entry["mushroom"].diameter == tkmod.MINI_STOP_DIAMETER
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
    older info lines muted, warnings trace, errors signal. Info used to be
    drawn in the `info` role's *background*, a shade off the log's own
    background, which is why older lines were unreadable."""
    dashboard.open()
    for severity in ("info", "warning", "error"):
        dashboard._show_event(_event(severity, needs_ack=False))
    assert dashboard._event_text.written_tags == [
        ("info", "latest"), ("warning", "latest"), ("error", "latest")]
    tags = dashboard._event_text.tags
    assert tags["info"]["foreground"] == theme.MUTED
    assert tags["latest"]["foreground"] == theme.TEXT
    assert tags["warning"]["foreground"] == theme.TRACE
    assert tags["error"]["foreground"] == theme.SIGNAL
    # `latest` moves: it is taken off the old lines before each new one.
    assert dashboard._event_text.removed_tags.count("latest") == 3
    # and it is created after `info`, before `warning`/`error` - priority.
    assert list(tags).index("info") < list(tags).index("latest") \
        < list(tags).index("warning")


def test_only_a_needs_ack_event_becomes_a_popup(dashboard, tk_harness):
    dashboard.open()
    dashboard._on_event(_event("warning", needs_ack=False))
    dashboard._on_event(_event("error", needs_ack=True))
    SCHEDULER.pump()
    assert [title for title, _ in tk_harness.errors] == ["error-event"]


def test_an_event_is_marshalled_onto_the_tk_thread(dashboard):
    dashboard.open()
    before = dashboard._event_text.body
    dashboard._on_event(_event("info", needs_ack=False))
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


def test_macos_quit_closes_the_app(dashboard, controller):
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
