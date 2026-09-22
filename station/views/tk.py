"""The Tk renderer: widgets only.

`station.views.base` holds every decision that is not a widget — what a
command does with its inputs, when a control is greyed out, what a refusal
looks like as a *state*, which events become a popup. This module supplies
the Tk widgets that state is drawn with, and nothing else. There is no model
import, no `setattr` on a model, and no model-specific subclass: Red Percent,
the rotator and Setup are schemas, not classes.

Three things this replaces are worth naming, because each was a defect.

* **`DynamicView` read the model directly** (`getattr(self.model, attr)`,
  `self.model.execute_command(...)`, a `RedPercentView` subclass that knew
  what "stop monitoring" meant). The Controller is the only backend object
  here now, reached through `PanelView._call`.
* **Colour and font were spelled out at 40-odd call sites** — `'darkgreen'`,
  `('Arial', 10, 'bold')`, `bg='black'` — so the same control looked
  different in each frontend. Every colour and every font in this file comes
  from `station.views.theme`.
* **Closing a tab called `notebook.forget()`** and left the model running with
  its coils energized and no way to reach it (VIEW-TKINTER-1). A tab close is
  `Controller.remove`, which estops and destructs; the Models menu reopens
  from the remembered config.

Tk-specific hazards this file is deliberate about:

* `tk.Button` ignores `bg`/`fg` on macOS Aqua, so a white-on-red button
  renders white-on-white. Buttons are `tk.Label`s styled as buttons, which is
  what the old view settled on for the same reason.
* Every `after` callback is cancelled on close. The old view registered five
  `after` loops per panel and cancelled none, which is how a popup loop ended
  up rescheduling itself onto a destroyed widget (VIEW-TKINTER-2).
* `<FocusOut>` fires both when the operator leaves the application and when
  *this* application opens a dialog. `focus_get()` tells them apart; treating
  a child dialog as focus loss is the defect behind VIEW-TKINTER-9. Focus
  gates input and never stops anything (D-4).
"""
import base64
import shutil
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from station import schema as sch
from station.events import events
from station.views import theme
from station.views.base import Dashboard, PanelView

SOURCE = "TkView"

#: Spacing, in pixels. `theme` carries a palette and a font scale but no
#: spacing scale, so every pad in this file comes from one of these three
#: names rather than from a number at the call site — one place to change,
#: and no "8 here, 6 there" drift. A `theme.PAD`/`GAP`/`INSET` request is in
#: the handoff; when it lands these become aliases.
PAD, GAP, INSET = 8, 4, 2

#: Widths, in characters, for the controls that sit in a table column. A
#: fixed width is what makes six rows of dropdowns line up as columns instead
#: of stepping in and out with the length of a port name.
CAPTION_WIDTH, DROPDOWN_WIDTH, ENTRY_WIDTH, READOUT_WIDTH = 20, 16, 10, 14

#: Lines the dashboard event log shows before it scrolls. It is a footer, not
#: a panel: eight lines of log was taking vertical space from the controls.
EVENT_LOG_LINES = 5

#: Width of an indicator lamp, in characters. An indicator is a *lamp*: the
#: caption already says what it is, so the lamp says only whether it is on.
LAMP_WIDTH = 2

#: What a readout with nothing in it shows. An empty coloured label renders as
#: a bare stripe of colour, which reads as a broken widget rather than as "no
#: value yet" — it is what "Position age (s):" looked like at the bench.
EMPTY_READOUT = "--"


def _tcl_error():
    """`tk.TclError`, or `Exception` when Tk is a stand-in.

    The test harness replaces `tkinter` with a mock whose `TclError` is a mock
    *instance*, and `except <instance>` raises `TypeError` at the moment the
    handler is needed — turning a recoverable bad tab index into a crash
    inside an event handler.
    """
    candidate = getattr(tk, "TclError", None)
    if isinstance(candidate, type) and issubclass(candidate, BaseException):
        return candidate
    return Exception


_TCL_ERROR = _tcl_error()


def _windowing_system(widget):
    """"aqua", "win32" or "x11". Unknown answers are treated as x11."""
    try:
        return str(widget.tk.call("tk", "windowingsystem"))
    except Exception:
        return "x11"


def _close_tab_button(widget):
    """The event sequence that closes a tab, per platform (VIEW-TKINTER-18).

    The old view hardcoded `<ButtonPress-2>`, which is the *middle* button on
    X11 and Win32 and the *right* button on Aqua — so on a Mac a right-click
    aimed at the tab menu closed the tab instead. The button number is
    resolved from the windowing system now rather than assumed. Which
    physical button that is on a Mac is listed under UNVERIFIED: it needs a
    Mac to confirm.
    """
    return ("<ButtonPress-2>" if _windowing_system(widget) == "aqua"
            else "<ButtonPress-3>")


class ClosableNotebook(ttk.Notebook):
    """A notebook whose tabs can be dragged to reorder and clicked to close.

    Pure widget: it resolves a click to a tab index and hands that to
    `on_close_tab`. What closing *means* belongs to the dashboard, because it
    means destructing a model.

    `on_close_tab` is actually assigned now. `DraggableClosableNotebook`
    declared the same attribute, never wrote it, and so always fell through to
    `forget()` — a one-way removal ttk offers no way back from
    (VIEW-TKINTER-1).
    """

    def __init__(self, master=None, on_close_tab=None, **kwargs):
        super().__init__(master, **kwargs)
        self.on_close_tab = on_close_tab
        self._dragging = None
        self.bind("<ButtonPress-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind(_close_tab_button(self), self._on_middle_press)

    # -- close -------------------------------------------------------------
    def close_tab(self, index):
        if self.on_close_tab is not None:
            self.on_close_tab(index)
        else:
            try:
                self.forget(index)
            except _TCL_ERROR:
                pass

    def _on_middle_press(self, event):
        index = self._tab_at(event)
        if index is not None:
            self.close_tab(index)

    # -- drag to reorder ---------------------------------------------------
    def _on_press(self, event):
        self._dragging = self._tab_at(event)

    def _on_drag(self, event):
        if self._dragging is None:
            return
        target = self._tab_at(event)
        if target is None or target == self._dragging:
            return
        try:
            self.insert(target, self.tabs()[self._dragging])
        except (_TCL_ERROR, IndexError):
            return
        self._dragging = target

    def _on_release(self, _event):
        self._dragging = None

    def _tab_at(self, event):
        try:
            return self.index(f"@{event.x},{event.y}")
        except (_TCL_ERROR, ValueError):
            return None


class _RegionPicker:
    """A borderless, semi-transparent overlay the operator drags a box on.

    Tk had no picker at all — `_select_region` asked for "x,y,width,height" as
    *text* in a modal prompt, which is why REDPERCENT-18 is still open. This
    is the same drag interaction the Qt view gets, in Tk idiom: a topmost
    `Toplevel` sized to the virtual desktop, a rubber band on a Canvas,
    Escape to cancel, and screen coordinates out.

    A drag smaller than `MINIMUM_DRAG` is reported rather than returned: a
    stray click used to capture a 1x1 region, and a 1x1 focus area reads 100%
    red forever.
    """

    MINIMUM_DRAG = 10      # px; below this a drag is a misclick, not a region

    def __init__(self, master):
        self.master = master
        self.region = None
        self.reason = ""
        self._origin = None
        self._press = (0, 0)
        self._band = None
        self.top = None
        self.canvas = None

    def pick(self):
        """Blocks until the operator drags or cancels. -> (x, y, w, h) | None."""
        self._build()
        try:
            self.master.wait_window(self.top)
        except Exception as exc:
            events.debug("Region Wait Failed", str(exc), source=SOURCE,
                         exception=exc)
        events.debug("Region Picked", f"region={self.region} reason={self.reason!r}",
                     source=SOURCE)
        return self.region

    def _build(self):
        self.top = tk.Toplevel(self.master)
        self.top.overrideredirect(True)
        for attribute, value in (("-alpha", 0.3), ("-topmost", True)):
            try:
                self.top.attributes(attribute, value)
            except Exception as exc:
                events.debug("Overlay Attribute Refused",
                             f"{attribute}={value}: {exc}", source=SOURCE,
                             exception=exc)
        self.top.geometry(self._virtual_desktop())
        self.top.configure(background=theme.BACKGROUND)
        self.canvas = tk.Canvas(self.top, highlightthickness=0, cursor="crosshair",
                                background=theme.BACKGROUND)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<ButtonPress-1>", self._on_canvas_press)
        self.canvas.bind("<B1-Motion>", self._on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_canvas_release)
        self.top.bind("<Escape>", self._on_escape_press)
        try:
            self.top.focus_force()
            self.canvas.grab_set()
        except Exception as exc:
            events.debug("Overlay Grab Failed", str(exc), source=SOURCE,
                         exception=exc)

    def _virtual_desktop(self):
        """The whole virtual desktop, so a region on a second monitor is
        reachable. Falls back to the primary screen."""
        widget = self.top
        width = self._measure(widget, "winfo_vrootwidth", "winfo_screenwidth")
        height = self._measure(widget, "winfo_vrootheight", "winfo_screenheight")
        left = self._measure(widget, "winfo_vrootx", None)
        top = self._measure(widget, "winfo_vrooty", None)
        return f"{width}x{height}+{left}+{top}"

    @staticmethod
    def _measure(widget, name, fallback_name):
        for candidate in (name, fallback_name):
            if candidate is None:
                continue
            try:
                value = int(getattr(widget, candidate)())
            except Exception:
                continue
            if value:
                return value
        return 0

    # -- drag --------------------------------------------------------------
    def _on_canvas_press(self, event):
        self._origin = (event.x_root, event.y_root)
        self._press = (event.x, event.y)      # widget coords, for the band
        try:
            self._band = self.canvas.create_rectangle(
                event.x, event.y, event.x, event.y,
                outline=theme.colors("info")[0], width=2)
        except Exception:
            self._band = None

    def _on_canvas_drag(self, event):
        if self._origin is None or self._band is None:
            return
        start_x, start_y = self._press
        try:
            self.canvas.coords(self._band, start_x, start_y, event.x, event.y)
        except Exception:
            pass

    def _on_canvas_release(self, event):
        if self._origin is None:
            return self._finish()
        left, top = self._origin
        width, height = abs(event.x_root - left), abs(event.y_root - top)
        if width < self.MINIMUM_DRAG or height < self.MINIMUM_DRAG:
            self.reason = (f"Region ignored: drag at least {self.MINIMUM_DRAG} "
                           f"px in both directions (got {width}x{height}).")
        else:
            self.region = (min(left, event.x_root), min(top, event.y_root),
                           width, height)
        return self._finish()

    def _on_escape_press(self, _event=None):
        self.reason = "Region selection cancelled."
        return self._finish()

    def _finish(self):
        try:
            self.canvas.grab_release()
        except Exception:
            pass
        try:
            self.top.destroy()
        except Exception:
            pass
        return "break"


class TkPanelView(PanelView):
    """One panel of controls, rendered from a schema.

    `PanelView.__init__` refuses to construct unless every element type in
    `schema.ELEMENT_TYPES` has a `_make_<type>` here, so a schema element this
    renderer cannot draw is a construction-time failure rather than a silent
    blank in one frontend.
    """

    #: How often the dropdown option lists are re-read. Options can be
    #: expensive (a port scan), so they do not ride the 100 ms refresh.
    OPTIONS_REFRESH_MS = 2000
    #: How often an `image` element's data command may run. Producing one is
    #: a figure render, which must not happen ten times a second.
    IMAGE_REFRESH_MS = 1000

    def __init__(self, master, controller, name, panel=None):
        super().__init__(controller, name, panel)
        self._widgets = {}          # id(element) -> {widget, var, ...}
        self._grid = {}             # id(container) -> the grid cursor
        self._table = None          # the frame the current run of row sections shares
        self._section_titles = []
        self._after_id = None
        self._options_due = 0
        self._is_stale = None
        self._slow_commands = set()     # data commands worth caching
        self._cached_results = {}       # command -> (monotonic, Result)

        self.frame = tk.Frame(master, background=theme.BACKGROUND)
        # A titled panel: the model's name, a rule under it, then the
        # controls. The title used to be one padded label with nothing
        # separating it from the first section's own bold heading, so a tab
        # opened on two headings that looked alike.
        self._title = tk.Label(self.frame, text=name, font=theme.font(1.3, bold=True),
                               anchor="w", background=theme.BACKGROUND,
                               foreground=theme.TEXT)
        self._title.pack(fill="x", padx=PAD, pady=(PAD, INSET))
        self._rule = tk.Frame(self.frame, height=1, background=theme.SURFACE)
        self._rule.pack(fill="x", padx=PAD, pady=(0, GAP))
        self._body = tk.Frame(self.frame, background=theme.BACKGROUND)
        self._body.pack(expand=True, fill="both")
        self._status = tk.Label(self.frame, text="", anchor="w",
                                font=theme.font(0.9),
                                background=theme.BACKGROUND, foreground=theme.MUTED)
        self._status.pack(fill="x", side="bottom", padx=PAD, pady=(0, GAP))

        self._build()
        self._schedule_refresh()
        events.debug("Panel Built", f"{name}: {len(self._elements)} elements",
                     source=SOURCE)

    def _call(self, command, inputs=None, args=()):
        """`PanelView._refresh` re-reads every plot, image and log source on
        every 100 ms tick. That is right for a series and wrong for an image,
        which a model renders. Image sources are cached here for
        `IMAGE_REFRESH_MS`; nothing else is cached, and a command carrying
        inputs or args never is.
        """
        if command not in self._slow_commands or inputs or args:
            return super()._call(command, inputs, args)
        now = time.monotonic()
        stamped = self._cached_results.get(command)
        if stamped is not None and (now - stamped[0]) * 1000 < self.IMAGE_REFRESH_MS:
            return stamped[1]
        result = super()._call(command, inputs, args)
        self._cached_results[command] = (now, result)
        return result

    # -- the poll tick -----------------------------------------------------
    def _schedule_refresh(self):
        try:
            self._after_id = self.frame.after(self.REFRESH_MS, self._on_refresh_tick)
        except Exception as exc:
            events.debug("Refresh Not Scheduled", str(exc), source=SOURCE,
                         exception=exc)

    def _on_refresh_tick(self):
        self._after_id = None
        try:
            self._refresh()
        except KeyError:
            # The model was removed between the tick and now. The dashboard
            # closes this panel; stop ticking rather than log every 100 ms.
            events.debug("Panel Gone", f"{self.name} is no longer open",
                         source=SOURCE)
            return
        except Exception as exc:
            events.debug("Refresh Failed", f"{self.name}: {exc}", source=SOURCE,
                         exception=exc, every=1.0)
        self._schedule_refresh()

    def close(self):
        if self._after_id is not None:
            try:
                self.frame.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        super().close()
        self._widgets.clear()
        try:
            self.frame.destroy()
        except Exception as exc:
            events.debug("Panel Destroy Failed", str(exc), source=SOURCE,
                         exception=exc)
        events.debug("Panel Closed", self.name, source=SOURCE)

    # -- layout helpers ----------------------------------------------------
    def _make_section(self, title, layout="column"):
        """A section of the panel. `layout` is the schema's hint (Addendum 2).

        `column` is the stacked form: a heading, then one element per grid
        row. `row` is the table form: a caption, then every element of the
        section side by side on ONE line — which is what turns the Setup
        panel from six stacked cards into one table with a row per model.
        """
        if layout == "row":
            return self._make_row_section(title)
        # A column section ends the table: the next row section starts a new
        # one, so a schema that interleaves the two still renders in order.
        self._table = None
        container = tk.Frame(self._body, background=theme.BACKGROUND)
        container.pack(fill="x", padx=PAD, pady=(GAP, 0))
        label = tk.Label(container, text=title, font=theme.font(1.0, bold=True),
                         anchor="w", background=theme.BACKGROUND,
                         foreground=theme.TEXT)
        label.grid(row=0, column=0, columnspan=4, sticky="w", pady=(GAP, INSET))
        self._section_titles.append(label)
        self._grid[id(container)] = {"layout": "column", "row": 1,
                                    "column": 0, "width": 0, "ends": {}}
        return container

    def _make_row_section(self, title):
        """One table row: a caption in column 0, the elements after it.

        Every row section in a run shares ONE grid, because columns only line
        up inside a single grid — six sibling frames each with their own grid
        is exactly the "everything in one vertical tab" the owner rejected,
        rotated ninety degrees. The caption carries a fixed width so the
        first control column starts at the same x on every row.
        """
        if self._table is None:
            self._table = tk.Frame(self._body, background=theme.BACKGROUND)
            self._table.pack(fill="x", padx=PAD, pady=GAP)
            self._grid[id(self._table)] = {"layout": "row", "row": -1,
                                           "column": 0, "width": 0, "ends": {}}
        state = self._grid[id(self._table)]
        state["row"] += 1
        state["column"] = 0
        caption = tk.Label(self._table, text=title, font=theme.font(bold=True),
                           anchor="w", width=CAPTION_WIDTH,
                           background=theme.BACKGROUND, foreground=theme.TEXT)
        caption.grid(row=state["row"], column=0, sticky="w",
                     padx=(0, GAP), pady=INSET)
        state["column"] = 1
        self._section_titles.append(caption)
        return self._table

    # A renderer asks for cells rather than computing a row and a column, so
    # the same renderer draws both layouts and neither can drift from the
    # other. `_next_cell` hands out the next slot on the current line;
    # `_end_line` ends that line, which in a row section is deliberately
    # nothing at all.
    def _next_cell(self, container, span=1):
        state = self._cursor(container)
        row, column = state["row"], state["column"]
        state["column"] += span
        state["width"] = max(state["width"], state["column"])
        return row, column

    def _place(self, container, widget, row, column, **options):
        """Grid one widget, and remember it as the rightmost cell of its line.

        `_align_table` needs to know where each table row ends, and the row
        that ends earliest is the one that would otherwise put its status
        under the next row's port: the models with no gamepad dropdown are
        two columns shorter than the ones with.
        """
        widget.grid(row=row, column=column, **options)
        state = self._cursor(container)
        if state["layout"] == "row":
            state["ends"][row] = (widget, column,
                                  options.get("columnspan") or 1)
        return widget

    def _build(self):
        super()._build()
        self._align_table()

    def _align_table(self):
        """Square off the right-hand column of every table.

        The last cell of each row is stretched to the width of the widest
        row and right-aligned, so the status column is a column even when
        the rows above it carry one control more.
        """
        for state in self._grid.values():
            if state["layout"] != "row":
                continue
            width = state["width"]
            for row, (widget, column, span) in state["ends"].items():
                if column + span >= width:
                    continue
                try:
                    widget.grid_configure(columnspan=width - column, sticky="e")
                except Exception as exc:
                    events.debug("Row Not Squared", f"row {row}: {exc}",
                                 source=SOURCE, exception=exc, every=5.0)

    def _end_line(self, container):
        state = self._cursor(container)
        if state["layout"] == "row":
            return
        state["row"] += 1
        state["column"] = 0

    def _cursor(self, container):
        """The grid cursor for one container, created on first use.

        `width` is the widest row so far and `ends` is the last widget on
        each line; `_align_table` reads both.
        """
        return self._grid.setdefault(id(container), {
            "layout": "column", "row": 0, "column": 0, "width": 0, "ends": {}})

    def _stretch_column(self, container, column):
        """Let one column absorb the slack, so what follows it is pushed to
        the right edge instead of trailing the widest cell above it."""
        try:
            container.grid_columnconfigure(column, weight=1)
        except Exception as exc:
            events.debug("Column Weight Refused", str(exc), source=SOURCE,
                         exception=exc, every=5.0)

    def _register(self, element, **widgets):
        entry = self._widgets.setdefault(id(element), {})
        entry.update(widgets)
        entry.setdefault("is_enabled", True)
        return entry

    def _entry_for(self, element):
        return self._widgets.get(id(element), {})

    def _caption(self, container, text, **options):
        """The label in front of a control. Muted on purpose: the *value* is
        the thing to read, and a panel of equally bright label/value pairs
        gives the eye nothing to land on."""
        row, column = self._next_cell(container)
        label = tk.Label(container, text=text, font=theme.font(), anchor="w",
                         background=theme.BACKGROUND, foreground=theme.MUTED,
                         **options)
        self._place(container, label, row, column, sticky="w",
                    padx=(0, INSET), pady=INSET)
        return label

    def _button_label(self, container, element, on_click):
        """A `tk.Label` styled as a button: `tk.Button` ignores bg/fg on Aqua."""
        background, foreground = self._role_colors(element.get("role"))
        widget = tk.Label(container, text=element.get("text", ""),
                          font=theme.font(bold=True), background=background,
                          foreground=foreground, relief="raised", pady=5,
                          cursor="hand2")

        def _on_widget_click(_event=None, element=element):
            if not self._entry_for(element).get("is_enabled", True):
                return "break"
            on_click(element)
            return "break"

        widget.bind("<Button-1>", _on_widget_click)
        return widget

    def _role_colors(self, role):
        """The schema names the meaning; the theme owns the palette."""
        return theme.colors(role or "neutral")

    # -- element renderers -------------------------------------------------
    def _make_readonly(self, container, element):
        """A readout, drawn so it cannot be mistaken for a box to type in:
        raised off the panel onto the surface colour, right-aligned to a
        fixed width, and never given the sunken border an entry carries."""
        self._caption(container, element.get("text", ""))
        var = tk.StringVar(value="")
        # A role on a readout means something (a connection state, a fault
        # reason), so it is coloured; a neutral one sits on the surface.
        role = element.get("role") or "neutral"
        background, foreground = self._role_colors(role)
        if role == "neutral":
            background, foreground = theme.SURFACE, theme.TEXT
        row, column = self._next_cell(container)
        value = tk.Label(container, textvariable=var, font=theme.font(bold=True),
                         anchor="e", width=READOUT_WIDTH, relief="flat",
                         padx=GAP, background=background, foreground=foreground)
        self._place(container, value, row, column, sticky="e",
                    padx=INSET, pady=INSET)
        self._stretch_column(container, column)
        self._register(element, widget=value, var=var)
        self._end_line(container)

    def _make_entry(self, container, element):
        self._caption(container, element.get("text", ""))
        var = tk.StringVar(value="")
        row, column = self._next_cell(container)
        widget = tk.Entry(container, textvariable=var, font=theme.font(),
                          width=ENTRY_WIDTH, justify="right", relief="sunken",
                          borderwidth=1, background=theme.SURFACE,
                          foreground=theme.TEXT, insertbackground=theme.TEXT)
        if element.get("value_type") in ("int", "float"):
            self._attach_validator(widget, element)
        self._place(container, widget, row, column, sticky="w",
                    padx=INSET, pady=INSET)
        widget.bind("<Return>", lambda _e, el=element: self._on_entry_commit(el))
        widget.bind("<FocusOut>", lambda _e, el=element: self._on_entry_commit(el))
        unit = element.get("unit")
        if unit:
            self._caption(container, unit)
        self._register(element, widget=widget, var=var, last_text="")
        self._end_line(container)

    def _attach_validator(self, widget, element):
        try:
            command = (self.frame.register(
                lambda text, el=element: self._validate_entry(text, el)), "%P")
            widget.configure(validate="key", validatecommand=command)
        except Exception as exc:
            events.debug("Validator Not Attached",
                         f"{element.get('model_attr')}: {exc}", source=SOURCE,
                         exception=exc)

    def _validate_entry(self, text, element):
        """Keystroke validation from the declared type — never from `float()`
        on whatever the box currently holds.

        Guessing numeric-ness from the current contents is RC-6's defect: a
        box the operator had cleared came back as text and lost its validator
        for the rest of the session. Bounds are deliberately NOT enforced per
        keystroke (typing "50" passes through "5", which may be below a
        minimum of 10); `_bounds_hint` colours the field instead, and the
        model refuses out-of-range values as a set when the command runs.
        """
        if element.get("value_type") == "int":
            # An int field takes no decimal point at all — not as a partial
            # entry either, because there is no integer a "." is on the way
            # to. The float pass-throughs below would have let "." stand in
            # the box until the commit refused it (Addendum 2).
            if text in ("", "-", "+"):
                return True
            body = text[1:] if text[0] in "+-" else text
            return body.isdigit()
        if text in ("", "-", "+", ".", "-.", "+."):
            return True
        try:
            number = float(text)
        except ValueError:
            return False
        return number == number and number not in (float("inf"), float("-inf"))

    def _bounds_hint(self, element):
        """Colour an entry whose current text is outside min/max."""
        entry = self._entry_for(element)
        widget, var = entry.get("widget"), entry.get("var")
        if widget is None or var is None:
            return
        low, high = element.get("min"), element.get("max")
        foreground = theme.TEXT
        try:
            number = float(var.get())
        except (TypeError, ValueError):
            number = None
        if number is not None and ((low is not None and number < low)
                                   or (high is not None and number > high)):
            foreground = theme.colors("warning")[0]
        try:
            widget.configure(foreground=foreground)
        except Exception:
            pass

    def _on_entry_commit(self, element):
        """Return / focus-out commits the typed value through the Controller.

        The old view did `setattr(self.model, attr, text)` from the widget
        callback, which wrote unvalidated text straight onto the model. A
        commit is a `_commit` command now: `Panel._apply_inputs` validates it
        and refuses by name. Only this field is sent, so a half-typed value in
        another box cannot refuse this edit; every writable field travels with
        a *command* through `_gather_inputs` regardless (D-5).
        """
        attr = element.get("model_attr")
        result = self._call("_commit", {attr: self._read_entry(element)})
        if result.is_refused:
            self._show_refused(result.reason)
        elif result.is_ok:
            self._show_refused("")
        return result

    def _make_button(self, container, element):
        row, column = self._next_cell(container, span=2)
        widget = self._button_label(container, element, lambda el: self._run(el))
        self._place(container, widget, row, column, columnspan=2,
                    sticky="ew", padx=INSET, pady=GAP)
        self._register(element, widget=widget)
        self._end_line(container)

    def _make_toggle(self, container, element):
        row, column = self._next_cell(container, span=2)
        widget = self._button_label(container, element,
                                    lambda el: self._run_toggle(el))
        self._place(container, widget, row, column, columnspan=2,
                    sticky="ew", padx=INSET, pady=GAP)
        self._register(element, widget=widget)
        self._end_line(container)

    def _make_dropdown(self, container, element):
        self._caption(container, element.get("text", ""))
        var = tk.StringVar(value="")
        row, column = self._next_cell(container)
        # No `font=` here: a ttk widget is styled through `ttk.Style`, not
        # per-widget options, and passing one is a TclError on some builds.
        # The ttk theme pass is listed under UNVERIFIED. `width` is in
        # characters and is fixed, so a column of dropdowns is a column.
        widget = ttk.Combobox(container, textvariable=var, state="readonly",
                              width=DROPDOWN_WIDTH)
        self._place(container, widget, row, column, sticky="w",
                    padx=INSET, pady=INSET)
        widget.bind("<<ComboboxSelected>>",
                    lambda _e, el=element: self._on_dropdown_selected(el))
        row, column = self._next_cell(container)
        refresh = tk.Label(container, text="⟳", font=theme.font(bold=True),
                           background=theme.SURFACE, foreground=theme.TEXT,
                           cursor="hand2")
        self._place(container, refresh, row, column, padx=INSET, pady=INSET)
        refresh.bind("<Button-1>",
                     lambda _e, el=element: self._refresh_options(el))
        self._register(element, widget=widget, var=var, options=[])
        self._refresh_options(element)
        self._end_line(container)

    def _on_dropdown_selected(self, element):
        entry = self._entry_for(element)
        if not entry.get("is_enabled", True):
            return None
        var = entry.get("var")
        return self._run(element, args=(var.get() if var else "",))

    def _refresh_options(self, element):
        """Re-read a dropdown's choices. Never mid-refresh by default: an
        options command can be a port scan."""
        command = element.get("options_command")
        entry = self._entry_for(element)
        widget, var = entry.get("widget"), entry.get("var")
        if not command or widget is None:
            return
        try:
            options = [str(o) for o in self._options(command)]
        except Exception as exc:
            events.debug("Options Failed", f"{command}: {exc}", source=SOURCE,
                         exception=exc, every=5.0)
            return
        current = var.get() if var is not None else ""
        if current and current not in options:
            options = [current] + options
        entry["options"] = options
        try:
            widget.configure(values=options)
        except Exception:
            pass

    def _make_region_select(self, container, element):
        row, column = self._next_cell(container, span=2)
        widget = self._button_label(container, element, self._on_region_clicked)
        self._place(container, widget, row, column, columnspan=2,
                    sticky="ew", padx=INSET, pady=GAP)
        self._end_line(container)
        var = tk.StringVar(value=sch.format_region(None))
        row, column = self._next_cell(container, span=3)
        shown = tk.Label(container, textvariable=var, font=theme.font(0.9),
                         anchor="w", background=theme.BACKGROUND,
                         foreground=theme.MUTED)
        self._place(container, shown, row, column, columnspan=3,
                    sticky="w", padx=INSET)
        # The captured region is *drawn*, not announced: PySide's confirming
        # modal over an always-on-top overlay is PYSIDE-12's deadlock.
        self._register(element, widget=widget, var=var)
        self._end_line(container)

    def _on_region_clicked(self, element):
        picker = _RegionPicker(self.frame)
        region = picker.pick()
        if region is None:
            self._show_refused(picker.reason)
            return None
        self._show_refused("")
        return self._run(element, args=region)

    def _make_file_save(self, container, element):
        row, column = self._next_cell(container, span=2)
        widget = self._button_label(container, element, self._on_save_clicked)
        self._place(container, widget, row, column, columnspan=2,
                    sticky="ew", padx=INSET, pady=GAP)
        self._register(element, widget=widget)
        self._end_line(container)

    def _on_save_clicked(self, element):
        """Ask for a destination, let the model write, then copy it there.

        The model owns the file it produces and returns its path; the view
        owns the dialog. That split is what lets the same `file_save` element
        render in the Web client, which has no file dialog at all.
        """
        extensions = element.get("extensions") or ["csv"]
        destination = filedialog.asksaveasfilename(
            title=element.get("text", "Save"),
            defaultextension="." + extensions[0],
            filetypes=[(e.upper(), "*." + e) for e in extensions])
        if not destination:
            return None
        result = self._run(element)
        if not result.is_ok or not result.value:
            return result
        try:
            if str(result.value) != str(destination):
                shutil.copyfile(str(result.value), str(destination))
        except OSError as exc:
            events.error("Save Failed", f"could not copy {result.value} to "
                         f"{destination}: {exc}", source=SOURCE, exception=exc)
        return result

    def _make_file_open(self, container, element):
        row, column = self._next_cell(container, span=2)
        widget = self._button_label(container, element, self._on_open_clicked)
        self._place(container, widget, row, column, columnspan=2,
                    sticky="ew", padx=INSET, pady=GAP)
        self._register(element, widget=widget)
        self._end_line(container)

    def _on_open_clicked(self, element):
        extensions = element.get("extensions") or ["csv"]
        path = filedialog.askopenfilename(
            title=element.get("text", "Open"),
            filetypes=[(e.upper(), "*." + e) for e in extensions])
        if not path:
            return None
        return self._run(element, args=(path,))

    def _make_plot(self, container, element):
        """A Canvas polyline. No matplotlib: the model publishes the series
        and each renderer draws it (D-6)."""
        if element.get("text"):
            self._caption(container, element["text"])
            self._end_line(container)
        row, column = self._next_cell(container, span=3)
        canvas = tk.Canvas(container, height=160, width=360,
                           background=theme.SURFACE, highlightthickness=0)
        self._place(container, canvas, row, column, columnspan=3,
                    sticky="ew", padx=INSET, pady=GAP)
        self._register(element, widget=canvas)
        self._end_line(container)

    def _redraw_plot(self, element, series):
        canvas = self._entry_for(element).get("widget")
        if canvas is None:
            return
        try:
            canvas.delete("all")
        except Exception:
            return
        values = [v for v in list((series or {}).get("y") or [])
                  if isinstance(v, (int, float))]
        width, height = 360, 160
        if len(values) < 2:
            # An empty plot that says why, rather than a blank rectangle
            # (REDPERCENT-17).
            text = "no data yet" if not values else "one sample so far"
            try:
                canvas.create_text(width / 2, height / 2, text=text,
                                   fill=theme.MUTED, font=theme.font(0.9))
            except Exception:
                pass
            return
        low, high = min(values), max(values)
        span = (high - low) or 1.0
        step = width / max(len(values) - 1, 1)
        points = []
        for index, value in enumerate(values):
            points.append(index * step)
            points.append(height - ((value - low) / span) * (height - 10) - 5)
        try:
            canvas.create_line(*points, fill=theme.colors("danger")[0], width=2)
        except Exception as exc:
            events.debug("Plot Draw Failed", str(exc), source=SOURCE,
                         exception=exc, every=5.0)

    def _make_image(self, container, element):
        if element.get("text"):
            self._caption(container, element["text"])
            self._end_line(container)
        row, column = self._next_cell(container, span=3)
        widget = tk.Label(container, background=theme.SURFACE,
                          foreground=theme.MUTED, text="")
        self._place(container, widget, row, column, columnspan=3,
                    sticky="w", padx=INSET, pady=GAP)
        self._slow_commands.add(element.get("data_command"))
        self._register(element, widget=widget, photo=None, data=None)
        self._end_line(container)

    def _show_image(self, element, data):
        """PNG bytes (or base64 text) -> PhotoImage. Tk reads PNG natively."""
        entry = self._entry_for(element)
        widget = entry.get("widget")
        if widget is None or not data or entry.get("data") == data:
            return
        encoded = (base64.b64encode(data).decode("ascii")
                   if isinstance(data, (bytes, bytearray)) else str(data))
        try:
            photo = tk.PhotoImage(data=encoded)
            widget.configure(image=photo, text="")
        except Exception as exc:
            events.debug("Image Failed", str(exc), source=SOURCE, exception=exc,
                         every=5.0)
            return
        # Tk drops an image the moment Python does: keep the reference.
        entry["photo"], entry["data"] = photo, data

    def _make_indicator(self, container, element):
        """A lamp, not a second copy of the label.

        The label went on the lamp as well as in front of it, so a fault
        indicator read "Fault   Fault" and said nothing about the fault. The
        caption names it once; the lamp carries only the state, as fill
        (`theme.toggle_colors`) against a ring of the same role, so ON and OFF
        differ by shape as well as by colour.
        """
        self._caption(container, element.get("text", ""))
        row, column = self._next_cell(container)
        widget = tk.Label(container, text="", width=LAMP_WIDTH, relief="flat",
                          highlightthickness=2, pady=INSET)
        self._place(container, widget, row, column, sticky="w",
                    padx=INSET, pady=INSET)
        self._register(element, widget=widget)
        self._end_line(container)

    def _make_log_stream(self, container, element):
        if element.get("text"):
            self._caption(container, element["text"])
            self._end_line(container)
        row, column = self._next_cell(container, span=3)
        widget = tk.Text(container, height=EVENT_LOG_LINES + 1, width=48,
                         state="disabled", relief="flat", font=theme.font(0.9),
                         background=theme.SURFACE, foreground=theme.TEXT,
                         wrap="word")
        self._place(container, widget, row, column, columnspan=3,
                    sticky="ew", padx=INSET, pady=GAP)
        self._register(element, widget=widget, last_text=None)
        self._end_line(container)

    def _refresh_log(self, element, lines):
        entry = self._entry_for(element)
        widget = entry.get("widget")
        if widget is None:
            return
        text = "\n".join(str(line) for line in list(lines or [])[-40:])
        if entry.get("last_text") == text:
            return
        entry["last_text"] = text
        try:
            widget.configure(state="normal")
            widget.delete("1.0", "end")
            widget.insert("1.0", text)
            widget.configure(state="disabled")
            widget.see("end")
        except Exception as exc:
            events.debug("Log Draw Failed", str(exc), source=SOURCE,
                         exception=exc, every=5.0)

    def _make_internal(self, container, element):
        """Renders nothing. The element exists so its command is in the
        schema-derived allow-list."""
        return None

    # -- what base.PanelView drives ---------------------------------------
    def _read_entry(self, element):
        var = self._entry_for(element).get("var")
        return var.get() if var is not None else ""

    def _entry_is_dirty(self, element):
        """True while the operator owns the box: it holds focus, or its text
        differs from what the last refresh put there.

        Without this the 100 ms tick overwrites the field between keystrokes
        and it cannot be typed into at all.
        """
        entry = self._entry_for(element)
        widget, var = entry.get("widget"), entry.get("var")
        if widget is None or var is None:
            return False
        try:
            if widget.focus_get() is widget:
                return True
        except Exception:
            pass
        return var.get() != entry.get("last_text", "")

    def _display_text(self, element, text):
        """What an int field shows: an integer, with no decimal point.

        `Param.format` already renders a declared `int` as `str(int(number))`,
        so a model whose Param table matches its schema arrives here correct.
        This is the backstop for the values that reach a view without a Param
        behind them — `Setup.scan_progress` is one, a plain attribute the
        schema types and `Panel._text_for` hands over as `str(raw)` — because
        an int box showing "5.000" while its validator refuses "." is a
        contradiction the operator has to look at (Addendum 2).
        """
        if element["type"] == "readonly" and not str(text).strip():
            # A readout with no value says so. It never says it with an empty
            # coloured label, which is a stripe of colour with no meaning.
            return EMPTY_READOUT
        if element.get("value_type") != "int" or not text:
            return text
        try:
            number = float(text)
        except (TypeError, ValueError):
            return text
        return str(int(number))

    def _style_readout(self, element, text):
        """A role colours a readout that HAS a value. With nothing to show it
        goes back to the surface colour, so an empty "info" readout is not a
        blue bar with nothing in it."""
        entry = self._entry_for(element)
        widget = entry.get("widget")
        role = element.get("role") or "neutral"
        if widget is None:
            return
        if role == "neutral" or text == EMPTY_READOUT:
            background, foreground = theme.SURFACE, (
                theme.MUTED if text == EMPTY_READOUT else theme.TEXT)
        else:
            background, foreground = self._role_colors(role)
        if entry.get("colors") == (background, foreground):
            return              # nothing to redraw ten times a second
        entry["colors"] = (background, foreground)
        try:
            widget.configure(background=background, foreground=foreground)
        except Exception:
            pass

    def _set_text(self, element, text):
        entry = self._entry_for(element)
        var = entry.get("var")
        if var is None:
            return
        text = "" if text is None else str(text)
        text = self._display_text(element, text)
        if element["type"] == "readonly":
            self._style_readout(element, text)
        if element["type"] == "dropdown":
            options = entry.get("options") or []
            if text and text not in options:
                self._refresh_options(element)
        if var.get() != text:
            var.set(text)
        entry["last_text"] = text
        if element["type"] == "entry":
            self._bounds_hint(element)

    def _set_on(self, element, is_on):
        entry = self._entry_for(element)
        widget = entry.get("widget")
        if widget is None:
            return
        colors = theme.toggle_colors(element, is_on)
        options = {"background": colors["background"],
                   "foreground": colors["foreground"],
                   "highlightbackground": colors["border"],
                   "highlightcolor": colors["border"]}
        if element["type"] == "toggle":
            options["text"] = element["true_text"] if is_on else element["false_text"]
        elif element["type"] == "indicator":
            # Never text: the caption in front of the lamp already names it,
            # and a lamp that repeats its own label ("Fault   Fault") is the
            # one thing it must not say.
            options["text"] = ""
        entry["is_on"] = is_on
        try:
            widget.configure(**options)
        except Exception as exc:
            events.debug("Toggle Draw Failed", str(exc), source=SOURCE,
                         exception=exc, every=5.0)

    def _set_data(self, element, data):
        kind = element["type"]
        if kind == "plot":
            self._redraw_plot(element, data)
        elif kind == "log_stream":
            self._refresh_log(element, data)
        elif kind == "image":
            self._show_image(element, data)

    def _set_enabled(self, element, is_enabled):
        """Gate entries too, which neither desktop view did.

        The flag is authoritative rather than the widget's `state` option: a
        `tk.Label` styled as a button has no usable `state`, so the old view's
        `cget("state") == "disabled"` guard was reading an option it had
        failed to set (VIEW-TKINTER-14).
        """
        entry = self._entry_for(element)
        if not entry:
            return
        was_enabled = entry.get("is_enabled", True)
        entry["is_enabled"] = bool(is_enabled)
        widget = entry.get("widget")
        if widget is None:
            return
        if element["type"] in ("entry", "dropdown"):
            live = "normal" if element["type"] == "entry" else "readonly"
            try:
                widget.configure(state=live if is_enabled else "disabled")
            except Exception:
                pass
        elif element["type"] in ("button", "toggle", "region_select",
                                 "file_save", "file_open"):
            try:
                widget.configure(cursor="hand2" if is_enabled else "X_cursor")
                if not is_enabled:
                    widget.configure(background=theme.DISABLED[0],
                                     foreground=theme.DISABLED[1])
                elif element["type"] != "toggle":
                    background, foreground = self._role_colors(element.get("role"))
                    widget.configure(background=background, foreground=foreground)
            except Exception:
                pass
        if was_enabled != bool(is_enabled):
            events.debug("Gate Changed",
                         f"{self.name}/{element.get('text') or element.get('command')}"
                         f" -> {'enabled' if is_enabled else 'disabled'}",
                         source=SOURCE)

    def _set_stale(self, is_stale):
        """Grey the panel title when the model's state has stopped updating."""
        if is_stale == self._is_stale:
            return
        self._is_stale = is_stale
        foreground = theme.MUTED if is_stale else theme.TEXT
        try:
            self._title.configure(
                foreground=foreground,
                text=f"{self.name} (stale)" if is_stale else self.name)
            for label in self._section_titles:
                label.configure(foreground=foreground)
        except Exception:
            pass
        events.debug("Staleness Changed", f"{self.name} stale={is_stale}",
                     source=SOURCE)

    def _confirm(self, prompt):
        return bool(messagebox.askyesno("Confirm", prompt, parent=self.frame))

    def _show_refused(self, reason):
        """Non-modal. A refusal is information, not an incident: the desktop
        views used to drop it entirely."""
        try:
            self._status.configure(
                text=reason or "",
                foreground=theme.colors("warning")[0] if reason else theme.MUTED)
        except Exception:
            pass

    def _apply_theme(self):
        for widget in (self.frame, self._body):
            try:
                widget.configure(background=theme.BACKGROUND)
            except Exception:
                pass

    def _refresh(self):
        super()._refresh()
        self._options_due -= self.REFRESH_MS
        if self._options_due <= 0:
            self._options_due = self.OPTIONS_REFRESH_MS
            for element in self._elements:
                if element["type"] == "dropdown":
                    self._refresh_options(element)


class TkDashboard(Dashboard):
    """The window: a tab per model, the Setup panel, the global FULL STOP,
    the event log and the acknowledged popup.

    Everything that decides *policy* — which events pop up, what closing a
    model means, what the stop toggle does — is in `Dashboard`. This class
    owns the widgets and the marshalling onto the Tk thread.
    """

    REFRESH_MS = 200
    SETUP_TAB = "Setup"

    def __init__(self, controller, setup):
        super().__init__(controller, setup)
        self._panels = {}       # name -> TkPanelView
        self._frames = {}       # name -> the tab frame
        self._menu_vars = {}    # name -> BooleanVar in the Models menu
        self._after_id = None
        self._is_focused = None
        self._is_setup_collapsed = False
        self._setup_menu = None      # the "Show Setup" menu, once built

        self.root = tk.Tk()
        self.root.title("Transfer Station")
        self.root.geometry("1100x850")
        self.root.configure(background=theme.BACKGROUND)

        # Packed before the notebook, so the strip sits above the tab bar
        # rather than under it: pack order is allocation order.
        self._build_toolbar()

        self.notebook = ClosableNotebook(self.root, on_close_tab=self._on_tab_close)
        self.notebook.pack(fill="both", expand=True)

        self._build_event_panel()
        self._build_stop_button()
        self._build_menu_bar()

        # D-4: gate input on focus, never stop. Bound on the root, filtered to
        # the root's own events so a child widget's focus traffic is not a
        # window focus change.
        self.root.bind("<FocusIn>", self._on_window_focus)
        self.root.bind("<FocusOut>", self._on_window_focus)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._hook_macos_quit()

    # -- construction ------------------------------------------------------
    def _build_toolbar(self):
        """The strip above the tabs. Empty while Setup has a tab of its own;
        it carries the way back once Setup minimises.

        The frame is packed now and stays packed even while empty, because a
        frame packed after the notebook lands *below* it.
        """
        self._toolbar = tk.Frame(self.root, background=theme.BACKGROUND)
        self._toolbar.pack(side="top", fill="x")
        background, foreground = theme.colors("info")
        self._setup_button = tk.Label(
            self._toolbar, text="Setup", font=theme.font(0.9, bold=True),
            background=background, foreground=foreground, relief="raised",
            padx=PAD, pady=INSET, cursor="hand2")
        self._setup_button.bind("<Button-1>", self._on_setup_clicked)

    def _build_stop_button(self):
        """Bottom-docked on purpose: directly above the tab bar it was an easy
        accidental-click target when reaching for a tab.

        It is the largest control in the window, because it is the one the
        operator has to find without looking for it.
        """
        background, foreground = theme.colors("danger")
        self._stop_button = tk.Label(
            self.root, text="FULL STOP", font=theme.font(1.5, bold=True),
            background=background, foreground=foreground, relief="raised",
            borderwidth=3, pady=PAD + GAP, cursor="hand2")
        self._stop_button.bind("<Button-1>", self._on_stop_clicked)
        self._stop_button.pack(side="bottom", fill="x", padx=PAD, pady=(GAP, PAD))

    def _build_event_panel(self):
        """A footer, sized in lines and scrolled — not an expanding panel.

        `height` is in text lines and `expand` is False, so the log cannot
        take space from the controls as it fills: eight lines of log on an
        expanding panel was the first thing the owner saw grow.
        """
        frame = tk.Frame(self.root, background=theme.BACKGROUND)
        frame.pack(side="bottom", fill="x", padx=PAD, pady=(0, INSET))
        caption = tk.Label(frame, text="Events", font=theme.font(0.9, bold=True),
                           anchor="w", background=theme.BACKGROUND,
                           foreground=theme.MUTED)
        caption.pack(fill="x")
        body = tk.Frame(frame, background=theme.BACKGROUND)
        body.pack(fill="x")
        scrollbar = tk.Scrollbar(body)
        scrollbar.pack(side="right", fill="y")
        self._event_text = tk.Text(body, height=EVENT_LOG_LINES, state="disabled",
                                   wrap="word", relief="flat",
                                   font=theme.font(0.9),
                                   background=theme.SURFACE, foreground=theme.TEXT,
                                   yscrollcommand=scrollbar.set)
        self._event_text.pack(side="left", fill="x", expand=True)
        try:
            scrollbar.configure(command=self._event_text.yview)
        except Exception as exc:
            events.debug("Event Scrollbar Not Wired", str(exc), source=SOURCE,
                         exception=exc)
        for severity, role in theme.SEVERITY_ROLE.items():
            try:
                self._event_text.tag_configure(
                    severity, foreground=theme.colors(role)[0])
            except Exception:
                pass

    def _build_menu_bar(self):
        """The Models menu — tick to open, untick to close — and the way back
        to Setup.

        A closed model is still listed, which is the reopen path Tk never had:
        a forgotten tab was gone for the session (VIEW-TKINTER-1). The Setup
        entry is the same idea for the wizard, which minimises once the first
        model launches.
        """
        try:
            menubar = tk.Menu(self.root)
            menu = tk.Menu(menubar, tearoff=0)
            setup_menu = tk.Menu(menubar, tearoff=0)
        except Exception as exc:
            events.debug("Menu Not Built", str(exc), source=SOURCE, exception=exc)
            return
        self._menu_vars = {}
        open_names = list(self.controller.model_names)
        for name in open_names + [n for n in self.controller.closed_names
                                  if n not in open_names]:
            variable = tk.BooleanVar(value=name in open_names)
            self._menu_vars[name] = variable
            menu.add_checkbutton(label=name, variable=variable,
                                 command=lambda n=name: self._on_model_toggled(n))
        menubar.add_cascade(label="Models", menu=menu)
        setup_menu.add_command(label="Show Setup", command=self.restore_setup)
        menubar.add_cascade(label=self.SETUP_TAB, menu=setup_menu)
        self._setup_menu = setup_menu
        try:
            self.root.configure(menu=menubar)
        except Exception as exc:
            events.debug("Menubar Not Attached", str(exc), source=SOURCE,
                         exception=exc)

    def _hook_macos_quit(self):
        """Cmd-Q. Without it the app exits past every teardown path
        (VIEW-TKINTER-8)."""
        try:
            self.root.createcommand("::tk::mac::Quit", self.close)
        except Exception as exc:
            events.debug("macOS Quit Not Hooked", str(exc), source=SOURCE,
                         exception=exc)

    # -- lifecycle ---------------------------------------------------------
    def open(self):
        """Show Setup first, then run. Returns when the window closes.

        Setup is a panel like any other — `TkPanelView` over the Setup schema —
        which is why there is one wizard for three views instead of three
        wizards that disagree.
        """
        events.debug("View Opening", "Tk dashboard", source=SOURCE)
        self._add_setup_panel()
        super().open()               # subscribe BEFORE anything can publish
        for name in self.controller.model_names:
            self._add_panel(name)
        self._schedule_refresh()
        events.info("Dashboard Open", "Tk dashboard ready", source=SOURCE)
        self.root.mainloop()
        if not self._closing:
            # Not an error: `Controller._hook_exit` (atexit + SIGINT/TERM/HUP)
            # owns the teardown of last resort. Worth a line in the log file
            # when the loop ends by some path other than close().
            events.debug("Loop Ended", "the Tk main loop returned without a "
                         "close path", source=SOURCE)
        return None

    def _add_setup_panel(self):
        frame = ttk.Frame(self.notebook)
        view = TkPanelView(frame, self.controller, self.SETUP_TAB, panel=self.setup)
        view.frame.pack(fill="both", expand=True)
        self.notebook.add(frame, text=self.SETUP_TAB)
        self._panels[self.SETUP_TAB] = view
        self._frames[self.SETUP_TAB] = frame

    def close(self):
        """Unsubscribe first, then tear the widgets down.

        `Dashboard.close` sets `_closing` and unsubscribes before anything
        else, so nothing opened from inside this path can raise a modal that
        blocks the exit.
        """
        if self._closing:
            return
        events.debug("View Closing", "Tk dashboard", source=SOURCE)
        if self._after_id is not None:
            try:
                self.root.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        super().close()
        for name in list(self._panels):
            self._destroy_panel(name)
        try:
            self.root.quit()
            self.root.destroy()
        except Exception as exc:
            events.debug("Root Destroy Failed", str(exc), source=SOURCE,
                         exception=exc)
        events.debug("View Closed", "Tk dashboard", source=SOURCE)

    # -- the refresh tick --------------------------------------------------
    def _schedule_refresh(self):
        try:
            self._after_id = self.root.after(self.REFRESH_MS, self._on_refresh_tick)
        except Exception as exc:
            events.debug("Dashboard Tick Not Scheduled", str(exc), source=SOURCE,
                         exception=exc)

    def _on_refresh_tick(self):
        self._after_id = None
        if self._closing:
            return
        self._sync_stop_button()
        self._schedule_refresh()

    def _sync_stop_button(self):
        """Label and colour follow `Controller.is_estopped`, so the control
        says what it will do rather than what it did."""
        is_estopped = bool(self.controller.is_estopped)
        text = "CLEAR FULL STOP" if is_estopped else "FULL STOP"
        background, foreground = theme.colors("warning" if is_estopped else "danger")
        try:
            if self._stop_button.cget("text") != text:
                events.debug("Stop Button Changed", f"{text}", source=SOURCE)
            self._stop_button.configure(text=text, background=background,
                                        foreground=foreground)
        except Exception as exc:
            events.debug("Stop Button Draw Failed", str(exc), source=SOURCE,
                         exception=exc, every=5.0)

    def _on_stop_clicked(self, _event=None):
        return self.toggle_estop_all()

    # -- panels ------------------------------------------------------------
    def _add_panel(self, name):
        if name in self._panels:
            return
        frame = ttk.Frame(self.notebook)
        view = TkPanelView(frame, self.controller, name)
        view.frame.pack(fill="both", expand=True)
        self.notebook.add(frame, text=name)
        self._panels[name] = view
        self._frames[name] = frame
        self._build_menu_bar()
        events.debug("Tab Opened", name, source=SOURCE)

    def _remove_panel(self, name):
        if name not in self._panels:
            return
        self._destroy_panel(name)
        self._build_menu_bar()
        events.debug("Tab Closed", name, source=SOURCE)

    # -- the Setup tab, minimised and brought back -------------------------
    def _collapse_setup(self):
        """`Dashboard` calls this when the first model launches: the wizard
        has done its job and the models want the window.

        The tab is *hidden*, never destroyed — the panel view keeps its
        widgets, its state and its refresh tick, so Refresh and Launch work
        the moment it comes back. Destroying it and rebuilding on demand
        would be a second construction path for a panel that already exists,
        and VIEW-TKINTER-1 is what a one-way removal costs.
        """
        frame = self._frames.get(self.SETUP_TAB)
        if frame is None or self._is_setup_collapsed:
            return
        for step in (lambda: self.notebook.hide(frame),
                     lambda: self.notebook.forget(frame)):
            try:
                step()
                break
            except Exception as exc:
                events.debug("Setup Not Hidden", str(exc), source=SOURCE,
                             exception=exc)
        else:
            return
        self._is_setup_collapsed = True
        self._show_setup_button(True)
        self._build_menu_bar()
        events.info("Setup Minimised", "Setup is on the toolbar and the menu "
                    "bar; reopen it to re-scan or relaunch", source=SOURCE)

    def restore_setup(self):
        """Bring the Setup tab back and select it. -> bool.

        Reopenable as often as the operator likes, and idempotent: asking for
        Setup while it is already showing selects it rather than adding a
        second tab.
        """
        frame = self._frames.get(self.SETUP_TAB)
        if frame is None:
            return False
        try:
            self.notebook.add(frame, text=self.SETUP_TAB)
        except Exception as exc:
            events.debug("Setup Not Restored", str(exc), source=SOURCE,
                         exception=exc)
            return False
        try:
            self.notebook.select(frame)
        except Exception as exc:
            events.debug("Setup Not Selected", str(exc), source=SOURCE,
                         exception=exc)
        if self._is_setup_collapsed:
            events.debug("Setup Restored", "the Setup tab is back", source=SOURCE)
        self._is_setup_collapsed = False
        self._show_setup_button(False)
        self._build_menu_bar()
        return True

    def _show_setup_button(self, is_shown):
        """The toolbar button exists only while Setup has no tab, so the
        window never offers two ways to the same visible panel."""
        try:
            if is_shown:
                self._setup_button.pack(side="left", padx=PAD, pady=INSET)
            else:
                self._setup_button.pack_forget()
        except Exception as exc:
            events.debug("Setup Button Not Drawn", str(exc), source=SOURCE,
                         exception=exc)

    def _on_setup_clicked(self, _event=None):
        return self.restore_setup()

    def _destroy_panel(self, name):
        view = self._panels.pop(name, None)
        frame = self._frames.pop(name, None)
        if view is not None:
            try:
                view.close()
            except Exception as exc:
                events.debug("Panel Close Failed", f"{name}: {exc}", source=SOURCE,
                             exception=exc)
        if frame is not None:
            for step in (lambda: self.notebook.forget(frame), frame.destroy):
                try:
                    step()
                except Exception:
                    pass

    def _on_tab_close(self, index):
        """A tab close destructs the model (`Controller.remove`): estop, close,
        drop. The Models menu reopens it from the remembered config."""
        name = self._name_at(index)
        if name is None or name == self.SETUP_TAB:
            return
        events.debug("Tab Close Requested", name, source=SOURCE)
        self.close_model(name)

    def _name_at(self, index):
        try:
            tab_id = self.notebook.tabs()[index]
        except (IndexError, TypeError, _TCL_ERROR):
            return None
        for name, frame in self._frames.items():
            if str(frame) == str(tab_id):
                return name
        return None

    def _on_model_toggled(self, name):
        variable = self._menu_vars.get(name)
        wants_open = bool(variable.get()) if variable is not None else True
        events.debug("Model Menu Toggled", f"{name} -> "
                     f"{'open' if wants_open else 'closed'}", source=SOURCE)
        if wants_open:
            self.open_model(name)
        else:
            self.close_model(name)

    # -- events ------------------------------------------------------------
    def _marshal(self, fn):
        """Any thread -> the Tk thread. Tk is not thread-safe and an event can
        be published from a model's worker."""
        try:
            self.root.after(0, fn)
        except Exception as exc:
            events.debug("Marshal Failed", str(exc), source=SOURCE, exception=exc)

    def _show_event(self, event):
        severity = event.severity if event.severity in theme.SEVERITY_ROLE else "info"
        try:
            self._event_text.configure(state="normal")
            self._event_text.insert("end", event.text + "\n", severity)
            self._event_text.see("end")
            self._event_text.configure(state="disabled")
        except Exception as exc:
            events.debug("Event Draw Failed", str(exc), source=SOURCE,
                         exception=exc, every=5.0)

    def _show_popup(self, event):
        """The one acknowledged modal. `Dashboard._on_event` has already
        decided this event earned it, and that the window is not closing."""
        if self._closing:
            return
        events.debug("Popup Shown", f"{event.severity}/{event.title}", source=SOURCE)
        try:
            messagebox.showerror(event.title, event.text, parent=self.root)
        except Exception as exc:
            events.debug("Popup Failed", str(exc), source=SOURCE, exception=exc)

    def _confirm(self, prompt):
        return bool(messagebox.askyesno("Confirm", prompt, parent=self.root))

    # -- focus -------------------------------------------------------------
    def _on_window_focus(self, event=None):
        """D-4: gate gamepad input while unfocused; never stop.

        `focus_get()` returns the widget holding focus *within this
        application*, so a non-None answer means a child dialog took it and
        focus is still ours. Treating that as focus loss is VIEW-TKINTER-9.
        """
        if event is not None and getattr(event, "widget", self.root) is not self.root:
            return
        try:
            is_focused = self.root.focus_get() is not None
        except Exception:
            is_focused = False      # the window is going away: gate closed
        if is_focused == self._is_focused:
            return
        self._is_focused = is_focused
        events.debug("Focus Gate Changed",
                     f"input {'enabled' if is_focused else 'gated'}", source=SOURCE)
        self._on_focus_change(is_focused)
