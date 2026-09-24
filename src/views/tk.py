"""The Tk renderer: widgets only.

`views.base` holds every decision that is not a widget — what a
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
  from `views.theme`.
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
import math
import os
import re
import shutil
import time
import tkinter as tk
from tkinter import ttk, filedialog
from tkinter import font as tkfont

import schema as sch
from events import events
from views import theme
from views.base import Dashboard, PanelView

SOURCE = "TkView"

#: Spacing comes from the theme: PAD around a panel or a section, GAP
#: between a label and its control and between rows, INSET for the left
#: indent of a section's body under its title. Any other distance is a step
#: of `theme.SPACE` - never a sum of these three (DS-7).
PAD, GAP, INSET = theme.PAD, theme.GAP, theme.INSET
SPACE = theme.SPACE

#: The type scale (Operate mode): one family and the theme's ratio, one
#: `theme.size(step)` per step. SMALL is a table header or the event log,
#: BASE is a caption, an entry or a button, STEP_1 is a section title and a
#: readout (numbers first: the value is one step louder than its label),
#: STEP_2 is a panel's name.
RATIO = theme.TYPE_RATIO
SMALL, BASE, STEP_1, STEP_2 = -1, 0, 1, 2

#: Widths. In a column section every entry, dropdown and readout fills ONE
#: value column at least `VALUE_PX` wide, so a readout and the entry under it
#: are the same width and end at the same right edge; the caption column
#: takes the slack. `FIELD_WIDTH` (characters) is only an entry's *request*,
#: deliberately below `VALUE_PX`, so the column decides and not the font.
#: `DROPDOWN_WIDTH` is a table's dropdown, wide enough for a macOS port name.
VALUE_PX, FIELD_WIDTH, DROPDOWN_WIDTH = 168, 8, 22

#: A panel lays its column sections out in as many columns as give each at
#: least `COLUMN_PX` (a caption and a `VALUE_PX` value without crowding), up
#: to `MAX_COLUMNS`: one on a narrow window, two at the default 1100 px, three
#: on a wide one, where two would put a caption half a screen from its value.
COLUMN_PX, MAX_COLUMNS = 400, 3

#: Lines the dashboard event log shows before it scrolls. It is a footer, not
#: a panel: eight lines of log was taking vertical space from the controls.
EVENT_LOG_LINES = 5

#: The smallest an indicator lamp gets, in pixels. It grows with the text
#: beside it (`_lamp_px`): a 16 px dot beside 37 px text read as a speck.
LAMP_PX = 16

#: What a readout with nothing in it shows. An empty coloured label renders as
#: a bare stripe of colour, which reads as a broken widget rather than as "no
#: value yet" — it is what "Position age (s):" looked like at the bench.
EMPTY_READOUT = "--"

#: The end of a value too long for its cell. The whole value is the cell's
#: tooltip, so nothing is ever cut off silently.
ELLIPSIS = "…"

#: The stop object. The dashboard's disc and the per-model disc in a Safety
#: section are the same object in two sizes, as in the Web view. These are
#: FLOORS: each disc is sized from its face font (`_Mushroom.diameter_for`),
#: so "Clear" fits at every `--font-size` (F7). The pulse is one breath
#: (up 7 %, back) when the latch closes, about 400 ms, and none at all with
#: `STATION_NO_MOTION=1` in the environment.
STOP_DIAMETER, MINI_STOP_DIAMETER = 64, 34
PULSE_FRAMES, PULSE_FRAME_MS = (1.03, 1.06, 1.07, 1.05, 1.025, 1.0), 65
NO_MOTION_ENV = "STATION_NO_MOTION"

#: Copy the view owns: the stop's face, and what the bar beside it says the
#: press will do. The Web view's words, so an action keeps its name in every
#: frontend.
STOP_FACE, CLEAR_FACE = "Stop", "Clear"
STOP_HINT, CLEAR_HINT = "Stop every model", "Clear the stop on every model"

#: The stop's keyboard shortcut, from anywhere in the window (F9). It only
#: ever STOPS: clearing the latch stays a deliberate press on the disc and a
#: confirmation. Command-period is the Mac's own "stop"; Control-period is
#: the same chord elsewhere, and is bound on the Mac too.
STOP_KEYS = ("<Control-period>",)
STOP_KEYS_AQUA = ("<Command-period>",)
STOP_KEY_NAME, STOP_KEY_NAME_AQUA = "Ctrl+.", "\u2318."

#: Keyboard focus is a 2 px ring in ink on every focusable control (F25,
#: WCAG 2.4.13); the stop's ring is `theme.STOP_FOCUS`, never the trace ring
#: that means "latched".
FOCUS_PX = 2
FOCUS_INK = theme.TEXT

#: The boundary of anything typed into or pressed, at 3:1 on the panel
#: (WCAG 1.4.11). `theme.RULE_STRONG` measures 1.98:1 on the panel, so this
#: is derived here with the theme's one `mix()` until the theme carries an
#: input-border token (CORE CHANGE REQUEST in the handoff).
INPUT_BORDER = theme.mix(theme.SURFACE, theme.TEXT, 0.40)

#: Every control a pointer presses is at least this tall, ring included
#: (WCAG 2.5.8), at every font size.
MIN_TARGET_PX = 24

#: A refusal sits on a band tinted toward the warning colour, in ink: a
#: refusal is something to read, and trace text is a live number (HC-16).
NOTICE_BG = theme.mix(theme.SURFACE, theme.TRACE, 0.12)

#: The word beside each severity's colour, so severity is never colour alone.
SEVERITY_WORD = {"error": "Error", "warning": "Warning", "info": "Info"}

#: Unacknowledged errors the band lists by name before it summarises.
BAND_LINES = 3

#: True once the dashboard has found itself on Aqua. Tk there assumes 96 dpi
#: (`tk scaling` 1.33), so a 12 pt font is drawn 16 px tall: a third larger
#: than every native Mac control around it, which is the "everything is
#: oversized" the owner saw. A Mac point IS a logical pixel, so on Aqua the
#: theme's points are handed to Tk as pixels (a negative size).
_PIXEL_FONTS = False


def _font(step=BASE, bold=False):
    """One step of the type scale, `theme.size(step)`."""
    size = theme.size(step)
    return (theme.FONT_FAMILY, -size if _PIXEL_FONTS else size,
            "bold" if bold else "normal")


def _page():
    """A notebook page's surface: the panel colour, as the Web view's cards."""
    return theme.SURFACE


def _motion_reduced():
    """`STATION_NO_MOTION=1`: the latch changes face and ring, but nothing
    moves (AUD-14). Read on every pulse, so a test can set it."""
    return os.environ.get(NO_MOTION_ENV, "").strip() not in ("", "0")


_SHOUTED = re.compile(r"^[A-Z]{4,}[:.,]?$")
_CAPITALISED = re.compile(r"^[A-Z][a-z]+$")


def _sentence(text):
    """The Web view's `sentence`: a SHOUTED word of four letters or more comes
    down ("FULL STOP" -> "Full stop"); capitals that carry meaning ("X",
    "DC", "ID") stay. The schema is the model's vocabulary and is not edited;
    this is presentation."""
    words = [word.lower() if _SHOUTED.match(word) else word
             for word in str("" if text is None else text).split(" ")]
    joined = " ".join(words)
    return joined[:1].upper() + joined[1:]


def _label(text):
    """The Web view's `sentenceCase`, for a caption, heading or button: the
    interior Capitalised words come down too ("Coordinate Frame" ->
    "Coordinate frame") and the trailing colon goes - the value sits beside
    its label on a panel, not after it in a form."""
    words = re.sub(r"\s*:\s*$", "", _sentence(text)).split(" ")
    return " ".join(word.lower() if index and _CAPITALISED.match(word) else word
                    for index, word in enumerate(words))


_MEASURES = {}


def _measure(font):
    """A `tkfont.Font` for `font`, or None when Tk cannot say (no display, or
    the test stand-in)."""
    try:
        measure = _MEASURES.get(font)
        if measure is None:
            measure = _MEASURES[font] = tkfont.Font(font=font)
        return measure
    except Exception:
        return None


def _text_width(font, text):
    """Pixels `text` takes in `font`, or None when Tk cannot say."""
    try:
        width = _measure(font).measure(text)
    except Exception:
        return None
    return width if isinstance(width, int) else None


def _width_px(font, text):
    """`_text_width`, or an estimate from the size when Tk cannot measure:
    a Helvetica glyph is about 0.55 em wide, 0.62 em in bold."""
    width = _text_width(font, text)
    if width is not None:
        return width
    em = abs(font[1]) * (4 / 3 if font[1] > 0 else 1)     # points -> px
    return math.ceil(em * (0.62 if font[2] == "bold" else 0.55) * len(text))


def _line_px(step=BASE, bold=False):
    """The line height of one type step in pixels, measured when Tk can."""
    font = _font(step, bold)
    try:
        height = _measure(font).metrics("linespace")
        if isinstance(height, int) and height > 0:
            return height
    except Exception:
        pass
    em = abs(font[1]) * (4 / 3 if font[1] > 0 else 1)
    return math.ceil(em * 1.25)


def _target_pady(step=BASE):
    """Vertical padding that makes a pressable `MIN_TARGET_PX` tall with its
    two-pixel ring, at this font size - never less than GAP."""
    return max(GAP, math.ceil((MIN_TARGET_PX - _line_px(step) - 2 * FOCUS_PX) / 2))


def _lamp_px():
    """A lamp scales with the caption beside it, from a 16 px floor."""
    return max(LAMP_PX, round(0.8 * _line_px()))


def _elide(font, text, room, middle=False):
    """`text`, or as much of it as fits in `room` px with an ellipsis: at the
    end for prose, in the MIDDLE for an identifier (`middle=True`), whose
    tail is the part that tells two apart ("/dev/cu.usbm…14201")."""
    full = _text_width(font, text)
    if full is None or room is None or room <= 1 or full <= room:
        return text
    low, high = 0, len(text)
    while low < high:
        keep = (low + high + 1) // 2
        width = _text_width(font, _cut(text, keep, middle))
        if width is not None and width <= room:
            low = keep
        else:
            high = keep - 1
    return _cut(text, low, middle)


def _cut(text, keep, middle):
    """`text` shortened to `keep` characters plus an ellipsis."""
    if keep >= len(text):
        return text
    if not middle:
        return text[:keep].rstrip() + ELLIPSIS
    tail = math.ceil(keep * 0.6)
    head = keep - tail
    return text[:head].rstrip() + ELLIPSIS + (text[-tail:] if tail else "")


def _elide_middle(text, limit):
    """`text` in at most `limit` characters, the ellipsis in the middle."""
    text = str(text)
    if limit < 5 or len(text) <= limit:
        return text
    return _cut(text, limit - 1, True)


def _is_identifier(text):
    """One unbroken token (a Run ID, a port): elide it in the middle."""
    text = str(text).strip()
    return bool(text) and " " not in text


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
                outline=theme.TRACE, width=2)
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


class _Tooltip:
    """The whole of a value that its cell had to shorten.

    Shown after a short hover, gone on leave or on a click; says nothing when
    the value fitted (`text` is then empty). `above=True` opens it above the
    pointer, for a control at the bottom of the window (the stop), so the
    tip never sits over the control it describes. `bind=False` leaves the
    widget's own Enter/Leave handlers alone; the owner calls `enter`/`leave`.
    """

    DELAY_MS = 450

    def __init__(self, widget, above=False, bind=True):
        self.widget = widget
        self.text = ""
        self.above = above
        self._after_id = None
        self._top = None
        if not bind:
            return
        for sequence, handler in (("<Enter>", self._on_enter),
                                  ("<Leave>", self._on_leave),
                                  ("<ButtonPress>", self._on_leave)):
            try:
                widget.bind(sequence, handler, add="+")
            except Exception:
                pass

    def _on_enter(self, _event=None):
        if not self.text or self._after_id is not None:
            return
        try:
            self._after_id = self.widget.after(self.DELAY_MS, self._show)
        except Exception:
            self._after_id = None

    def _show(self):
        self._after_id = None
        if not self.text or self._top is not None:
            return
        try:
            top = tk.Toplevel(self.widget)
            top.overrideredirect(True)
            label = tk.Label(top, text=self.text, font=_font(SMALL), justify="left",
                             background=theme.BACKGROUND, foreground=theme.TEXT,
                             padx=PAD, pady=GAP, highlightthickness=1,
                             highlightbackground=theme.RULE, wraplength=480)
            label.pack()
            x = self.widget.winfo_pointerx() + SPACE[4]
            y = self.widget.winfo_pointery() + SPACE[5]
            if self.above:
                top.update_idletasks()
                y = self.widget.winfo_pointery() - SPACE[4] - top.winfo_reqheight()
            top.geometry(f"+{x}+{y}")
            self._top = top
        except Exception as exc:
            events.debug("Tooltip Failed", str(exc), source=SOURCE,
                         exception=exc, every=5.0)

    def _on_leave(self, _event=None):
        if self._after_id is not None:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        if self._top is not None:
            try:
                self._top.destroy()
            except Exception:
                pass
            self._top = None

    enter, leave = _on_enter, _on_leave
    close = _on_leave


class _Ring:
    """The two-pixel focus ring every focusable control wears (F25).

    Aqua draws no highlight ring on a Label and a one-pixel one elsewhere, so
    the ring is two frames: an outer pixel the colour of whatever it sits on,
    and an inner pixel that is the control's resting border (3:1 on the
    panel). Focused, both turn ink - a 2 px ring - and nothing moves,
    because the ring's pixels are always there.
    """

    def __init__(self, parent, background, border=INPUT_BORDER):
        self.background, self.border = background, border
        self.outer = tk.Frame(parent, background=background,
                              padx=FOCUS_PX - 1, pady=FOCUS_PX - 1)
        self.inner = tk.Frame(self.outer, background=border, padx=1, pady=1)
        self.inner.pack(fill="both", expand=True)
        self.is_focused = False

    def paint(self, is_focused=None, border=None):
        if is_focused is not None:
            self.is_focused = is_focused
        if border is not None:
            self.border = border
        try:
            self.outer.configure(background=FOCUS_INK if self.is_focused
                                 else self.background)
            self.inner.configure(background=FOCUS_INK if self.is_focused
                                 else self.border)
        except Exception:
            pass


class _Press:
    """A chrome command for the window itself - Setup, Acknowledge, a
    confirmation's Yes and No: a Label drawn as a button (`tk.Button`
    ignores its colours on Aqua), in the ring, with hover, focus and
    Return/Space. `ghost` draws it without a fill, as the Web rail's
    Setup."""

    def __init__(self, parent, text, on_press, background, ghost=False):
        self.on_press = on_press
        self.background = background
        self.ghost = ghost
        self.ring = _Ring(parent, background)
        self.fill = background if ghost else theme.colors("neutral")[0]
        self.ink = theme.MUTED if ghost else theme.TEXT
        self.widget = tk.Label(self.ring.inner, text=text, font=_font(),
                               background=self.fill, foreground=self.ink,
                               relief="flat", padx=SPACE[4], pady=_target_pady(),
                               cursor="hand2", takefocus=1, highlightthickness=0)
        self.widget.pack(fill="both", expand=True)
        self.is_hovered = False
        for sequence in ("<Button-1>", "<Return>", "<space>"):
            self.widget.bind(sequence, self._on_press)
        self.widget.bind("<Enter>", lambda _e: self._hover(True))
        self.widget.bind("<Leave>", lambda _e: self._hover(False))
        self.widget.bind("<FocusIn>", lambda _e: self.ring.paint(True))
        self.widget.bind("<FocusOut>", lambda _e: self.ring.paint(False))

    @property
    def frame(self):
        return self.ring.outer

    def _on_press(self, _event=None):
        self.on_press()
        return "break"

    def _hover(self, is_hovered):
        self.is_hovered = is_hovered
        try:
            self.widget.configure(
                background=theme.mix(self.fill, theme.TEXT, 0.08) if is_hovered
                else self.fill,
                foreground=theme.TEXT if is_hovered else self.ink)
        except Exception:
            pass


class _ConfirmDialog:
    """A yes/no question that never takes the stop away.

    `messagebox.askyesno` is application-modal and answered Yes to Return,
    so the latch-clear confirmation both blocked the stop while it was up and
    released the latch on a reflex keystroke (F1, F17). This window takes no
    grab - the stop disc and its shortcut work while it is open - and its
    default is No: focus starts on No, Return answers No unless the operator
    has moved to Yes, Escape and the window's close button answer No.
    """

    #: One question at a time: a second one while the first is open (a
    #: stop press re-entering the clear path) is declined, not stacked.
    is_open = False

    def __init__(self, master, prompt, yes_text="Yes", no_text="No"):
        self.master = master
        self.prompt = str(prompt or "")
        self.yes_text, self.no_text = yes_text, no_text
        self.answer = False
        self.top = self.yes = self.no = None

    def ask(self):
        if _ConfirmDialog.is_open:
            events.debug("Confirm Declined", "another question is open",
                         source=SOURCE)
            return False
        _ConfirmDialog.is_open = True
        try:
            self._build()
            try:
                self.master.wait_window(self.top)
            except Exception as exc:
                events.debug("Confirm Wait Failed", str(exc), source=SOURCE,
                             exception=exc)
        finally:
            _ConfirmDialog.is_open = False
        return self.answer

    def _build(self):
        top = self.top = tk.Toplevel(self.master)
        for call in (lambda: top.title("Confirm"),
                     lambda: top.transient(self.master.winfo_toplevel()),
                     lambda: top.resizable(False, False)):
            try:
                call()
            except Exception:
                pass
        top.configure(background=theme.SURFACE)
        tk.Label(top, text=self.prompt, font=_font(), justify="left", anchor="w",
                 wraplength=420, background=theme.SURFACE, foreground=theme.TEXT,
                 padx=SPACE[5], pady=SPACE[5]).pack(fill="x")
        row = tk.Frame(top, background=theme.SURFACE)
        row.pack(fill="x", padx=SPACE[5], pady=(0, SPACE[5]))
        self.no = _Press(row, self.no_text, lambda: self._answer(False),
                         theme.SURFACE)
        self.no.frame.pack(side="right")
        self.yes = _Press(row, self.yes_text, lambda: self._answer(True),
                          theme.SURFACE)
        self.yes.frame.pack(side="right", padx=(0, SPACE[3]))
        top.bind("<Return>", lambda _e: self._answer(False))
        top.bind("<Escape>", lambda _e: self._answer(False))
        try:
            top.protocol("WM_DELETE_WINDOW", lambda: self._answer(False))
        except Exception:
            pass
        self._centre()
        try:
            self.no.widget.focus_set()
        except Exception:
            pass

    def _centre(self):
        try:
            self.top.update_idletasks()
            owner = self.master.winfo_toplevel()
            x = owner.winfo_rootx() + (owner.winfo_width() - self.top.winfo_reqwidth()) // 2
            y = owner.winfo_rooty() + (owner.winfo_height() - self.top.winfo_reqheight()) // 3
            self.top.geometry(f"+{max(0, x)}+{max(0, y)}")
        except Exception:
            pass

    def _answer(self, value):
        self.answer = bool(value)
        try:
            self.top.destroy()
        except Exception:
            pass
        return "break"


def _confirm(master, prompt):
    """The one confirmation both the dashboard and a panel ask. -> bool"""
    return _ConfirmDialog(master, prompt).ask()


class _Mushroom:
    """The stop object: a round signal-red disc with a darker ring and a
    highlight sunk into its top, reading `Stop`, or `Clear` once latched.

    The same object as the Web view's mushroom. It is never dimmed; latched,
    its ring turns the trace colour and it breathes ONCE, on the edge, not
    for as long as the latch stays closed. A Canvas, because a round control
    is the one shape Tk's widgets do not have, and because `tk.Button`
    ignores its colours on Aqua anyway.

    Its diameter comes from its face font (F7): a fixed 64 px disc cut
    "Clear" off at 28 pt. It is redrawn only when something it shows changes
    (F21): the dashboard syncs it five times a second and it was rebuilding
    every canvas item each time.
    """

    def __init__(self, master, on_press, background, face_step=STEP_1,
                 floor=STOP_DIAMETER):
        self.face_step = face_step
        self.diameter = self.diameter_for(face_step, floor)
        self.on_press = on_press
        self.background = background
        self.face = STOP_FACE
        self.is_latched = None           # unknown until the first sync
        self.scale = 1.0
        self.is_hovered = self.is_focused = False
        self._pulse_ids = []
        self.draws = 0
        # Room around the disc for the pulse (7 %) and the focus ring
        # (SPACE[1] out, FOCUS_PX wide) on every side.
        self.size = int(self.diameter * 1.08) + 2 * (SPACE[1] + FOCUS_PX + 1)
        self.canvas = tk.Canvas(master, width=self.size, height=self.size,
                                background=background, highlightthickness=0,
                                takefocus=1, cursor="hand2")
        self.tooltip = _Tooltip(self.canvas, above=True, bind=False)
        bindings = (("<Button-1>", self._on_press), ("<Return>", self._on_press),
                    ("<KP_Enter>", self._on_press), ("<space>", self._on_press),
                    ("<Enter>", lambda _e: self._set_flag("is_hovered", True)),
                    ("<Leave>", lambda _e: self._set_flag("is_hovered", False)),
                    ("<FocusIn>", lambda _e: self._set_flag("is_focused", True)),
                    ("<FocusOut>", lambda _e: self._set_flag("is_focused", False)))
        for sequence, handler in bindings:
            self.canvas.bind(sequence, handler)
        self.draw()

    # -- size --------------------------------------------------------------
    @staticmethod
    def ring_width(diameter):
        return max(3, round(diameter * 0.07))

    @classmethod
    def diameter_for(cls, face_step, floor):
        """The smallest disc, from `floor` up, whose face - both words, with
        their line height - sits inside the highlight arc with room to
        spare."""
        font = _font(face_step, bold=True)
        half_width = max(_width_px(font, CLEAR_FACE), _width_px(font, STOP_FACE)) / 2
        half_height = _line_px(face_step, bold=True) * 0.4
        corner = math.hypot(half_width, half_height) + SPACE[0]
        diameter = int(floor)
        while corner > diameter / 2 - cls.ring_width(diameter) - SPACE[0]:
            diameter += 1
        return diameter

    # -- input -------------------------------------------------------------
    def _on_press(self, _event=None):
        self.tooltip.leave()
        try:
            self.canvas.focus_set()
        except Exception:
            pass
        self.on_press()
        return "break"

    def _set_flag(self, name, value):
        """Hover and keyboard focus: the ring lightens under the pointer and
        a focus ring in ink shows where Return / Space will land."""
        if name == "is_hovered":
            (self.tooltip.enter if value else self.tooltip.leave)()
        if getattr(self, name) == value:
            return
        setattr(self, name, value)
        self.draw()

    # -- state -------------------------------------------------------------
    def set_latched(self, is_latched):
        """Face and ring follow the latch; the pulse answers its closing.
        Nothing is drawn when nothing changed."""
        is_latched = bool(is_latched)
        was = self.is_latched
        if was == is_latched:
            return
        self.is_latched = is_latched
        self.face = CLEAR_FACE if is_latched else STOP_FACE
        if is_latched and was is False:
            self.pulse()
        elif not is_latched:
            self.cancel()
            self.scale = 1.0
        self.draw()

    def pulse(self):
        self.cancel()
        if _motion_reduced():
            return
        for index, scale in enumerate(PULSE_FRAMES):
            try:
                self._pulse_ids.append(self.canvas.after(
                    PULSE_FRAME_MS * (index + 1),
                    lambda s=scale: self._pulse_frame(s)))
            except Exception:
                break

    def _pulse_frame(self, scale):
        if scale == self.scale:
            return
        self.scale = scale
        self.draw()

    def cancel(self):
        for token in self._pulse_ids:
            try:
                self.canvas.after_cancel(token)
            except Exception:
                pass
        self._pulse_ids = []

    # -- drawing -----------------------------------------------------------
    def draw(self):
        canvas = self.canvas
        try:
            canvas.delete("all")
        except Exception:
            return
        self.draws += 1
        centre = self.size / 2
        radius = self.diameter / 2 * self.scale
        ring = self.ring_width(self.diameter)
        if self.is_latched:
            ring_colour = theme.TRACE
        elif self.is_hovered:
            ring_colour = theme.mix(theme.SIGNAL, theme.TEXT, 0.25)
        else:
            ring_colour = theme.mix(theme.SIGNAL, theme.BACKGROUND, 0.45)
        try:
            if self.is_focused:
                reach = radius + SPACE[1]
                canvas.create_oval(centre - reach, centre - reach,
                                   centre + reach, centre + reach,
                                   outline=theme.STOP_FOCUS, width=FOCUS_PX)
            canvas.create_oval(centre - radius + ring / 2, centre - radius + ring / 2,
                               centre + radius - ring / 2, centre + radius - ring / 2,
                               fill=theme.SIGNAL, outline=ring_colour, width=ring)
            inset = ring + SPACE[0]
            canvas.create_arc(centre - radius + inset, centre - radius + inset,
                              centre + radius - inset, centre + radius - inset,
                              start=35, extent=110, style="arc", width=1,
                              outline=theme.mix(theme.SIGNAL, theme.TEXT, 0.35))
            canvas.create_text(centre, centre, text=self.face,
                               fill=theme.colors("danger")[1],
                               font=_font(self.face_step, bold=True))
        except Exception as exc:
            events.debug("Stop Draw Failed", str(exc), source=SOURCE,
                         exception=exc, every=5.0)


#: What a device class is called in a sentence to the operator.
DEVICE_WORDS = {"SerialPort": "serial port", "Gamepad": "gamepad",
                "SMC100": "SMC100 controller", "Screen": "screen capture"}


def _device_list(names):
    words = [DEVICE_WORDS.get(name, name) for name in names]
    return ", ".join(words) or "a device"


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
    #: The wheel, per platform: `<MouseWheel>` on Aqua and Win32, buttons 4
    #: and 5 on X11.
    WHEEL_EVENTS = ("<MouseWheel>", "<Button-4>", "<Button-5>")

    def __init__(self, master, controller, name, panel=None):
        super().__init__(controller, name, panel)
        self._widgets = {}          # id(element) -> {widget, var, ...}
        self._grid = {}             # id(container) -> the grid cursor
        self._table = None          # the frame the current run of row sections shares
        self._section_run = None            # the current run of column sections
        self._runs = []             # every run: its holder, two columns, its sections
        self._row_kinds = []        # per schema section: "table" | "bar" | None
        self._table_columns = {}    # a table's shared caption -> its grid column
        self._section_index = 0
        self._layout_columns = None
        self._is_scrollbar_shown = True
        self._section_titles = []
        self._after_id = None
        self._options_due = 0
        self._is_stale = None
        self._slow_commands = set()     # data commands worth caching
        self._cached_results = {}       # command -> (monotonic, Result)
        self._acting = None             # the element whose command is running
        self._notice = None             # the refusal line, at its control
        self._notice_text = ""
        self._last_state = {}
        self.lost_devices = ()          # device names whose link is lost
        self._is_paused = False

        self.frame = tk.Frame(master, background=_page())
        # A titled panel: the model's name, a rule under it, then the
        # controls. The title used to be one padded label with nothing
        # separating it from the first section's own bold heading, so a tab
        # opened on two headings that looked alike.
        self._title = tk.Label(self.frame, text=name, font=_font(STEP_2, bold=True),
                               anchor="w", background=_page(),
                               foreground=theme.TEXT)
        self._title.pack(fill="x", padx=INSET, pady=(INSET, GAP))
        # The rule is the panel's grouping mark: a signal rule is a panel
        # whose device link is lost (F3).
        self._rule = tk.Frame(self.frame, height=1, background=theme.RULE)
        self._rule.pack(fill="x", padx=INSET, pady=(0, GAP))
        # What is wrong with the panel as a whole (a lost link), in ink under
        # the rule; packed only while there is something to say.
        self._health = tk.Label(self.frame, text="", anchor="w", justify="left",
                                font=_font(), wraplength=640,
                                background=_page(), foreground=theme.TEXT)
        # A refusal with no control to sit under (a region picker's reason
        # lands at its control; this is the fallback). It wraps: a 300-
        # character refusal lost its recovery clause to the window edge.
        self._status = tk.Label(self.frame, text="", anchor="w", justify="left",
                                font=_font(), wraplength=640,
                                background=_page(), foreground=theme.TEXT)
        self._status.pack(fill="x", side="bottom", padx=INSET, pady=(0, GAP))
        self._build_scroll_area()

        self._build()
        self._schedule_refresh()
        events.debug("Panel Built", f"{name}: {len(self._elements)} elements",
                     source=SOURCE)

    # -- the scroll area ---------------------------------------------------
    def _build_scroll_area(self):
        """The panel's controls live on a scrolling canvas.

        A panel taller than the window used to make the *window* taller, and
        the window is not allowed to grow: the global stop lives at the
        bottom of it, and a stop control pushed off the bottom of the
        screen is the whole safety contract lost to a layout. A tall panel
        scrolls; the stop bar does not move. The scrollbar shows only while
        there is something to scroll to.
        """
        area = tk.Frame(self.frame, background=_page())
        area.pack(side="top", fill="both", expand=True)
        self._scrollbar = ttk.Scrollbar(area, orient="vertical")
        self._scrollbar.pack(side="right", fill="y")
        self._canvas = tk.Canvas(area, background=_page(),
                                 highlightthickness=0,
                                 yscrollcommand=self._scrollbar.set)
        self._canvas.pack(side="left", fill="both", expand=True)
        self._body = tk.Frame(self._canvas, background=_page())
        try:
            self._scrollbar.configure(command=self._canvas.yview)
            self._body_window = self._canvas.create_window(
                (0, 0), window=self._body, anchor="nw")
        except Exception as exc:
            self._body_window = None
            events.debug("Scroll Area Not Wired", str(exc), source=SOURCE,
                         exception=exc)
        self._body.bind("<Configure>", self._on_body_resized)
        self._canvas.bind("<Configure>", self._on_canvas_resized)
        # The wheel is bound application-wide only while the pointer is over
        # this panel, so two open panels never scroll each other.
        self.frame.bind("<Enter>", self._on_pointer_enter)
        self.frame.bind("<Leave>", self._on_pointer_leave)

    def _on_body_resized(self, _event=None):
        """The scrollable extent is whatever the controls actually occupy."""
        try:
            self._canvas.configure(scrollregion=self._canvas.bbox("all"))
        except Exception as exc:
            events.debug("Scrollregion Not Set", str(exc), source=SOURCE,
                         exception=exc, every=5.0)
        self._sync_scrollbar()

    def _on_canvas_resized(self, event=None):
        """Keep the body as wide as the viewport: a canvas window is sized to
        its content otherwise, and a table would stop at its widest row
        instead of reaching the window's edge. The width also decides one
        column of sections or two."""
        width = getattr(event, "width", 0)
        if self._body_window is None or not width:
            return
        try:
            self._canvas.itemconfigure(self._body_window, width=width)
        except Exception as exc:
            events.debug("Body Width Not Set", str(exc), source=SOURCE,
                         exception=exc, every=5.0)
        self._reflow(width)
        self._sync_scrollbar()
        self._wrap_to(width)

    def _wrap_to(self, width):
        """Long lines wrap to the panel instead of running off its edge."""
        room = max(SPACE[6] * 8, width - 2 * SPACE[6])
        for label in (self._status, self._health, self._notice):
            if label is None:
                continue
            try:
                label.configure(wraplength=room)
            except Exception:
                pass

    def _sync_scrollbar(self):
        """A scrollbar only when the controls are taller than the viewport; a
        bare trough beside a panel that fits is chrome with nothing to do."""
        try:
            content = self._body.winfo_reqheight()
            viewport = self._canvas.winfo_height()
        except Exception:
            return
        if not (isinstance(content, int) and isinstance(viewport, int)) or viewport <= 1:
            return
        needed = content > viewport
        if needed == self._is_scrollbar_shown:
            return
        self._is_scrollbar_shown = needed
        try:
            if needed:
                self._scrollbar.pack(side="right", fill="y", before=self._canvas)
            else:
                self._scrollbar.pack_forget()
                self._canvas.yview_moveto(0)
        except Exception as exc:
            events.debug("Scrollbar Not Synced", str(exc), source=SOURCE,
                         exception=exc, every=5.0)

    def _on_pointer_enter(self, _event=None):
        for sequence in self.WHEEL_EVENTS:
            try:
                self.frame.bind_all(sequence, self._on_wheel)
            except Exception as exc:
                events.debug("Wheel Not Bound", f"{sequence}: {exc}",
                             source=SOURCE, exception=exc)

    def _on_pointer_leave(self, _event=None):
        for sequence in self.WHEEL_EVENTS:
            try:
                self.frame.unbind_all(sequence)
            except Exception:
                pass

    def _on_wheel(self, event):
        """One notch, in whichever dialect the platform speaks: `delta` on
        Aqua and Win32, Button-4/5 on X11. Only the sign is used — a Windows
        notch is 120 and an Aqua one is 1."""
        number, delta = getattr(event, "num", 0), getattr(event, "delta", 0)
        if number == 4:
            step = -1
        elif number == 5:
            step = 1
        elif delta:
            step = -1 if delta > 0 else 1
        else:
            return None
        try:
            self._canvas.yview_scroll(step, "units")
        except Exception as exc:
            events.debug("Wheel Scroll Failed", str(exc), source=SOURCE,
                         exception=exc, every=5.0)
        return "break"

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
        if self._is_paused:
            return
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

    def pause(self):
        """Stop ticking while hidden (F21): the minimised Setup panel was
        re-reading its schema and state ten times a second for nobody."""
        self._is_paused = True
        if self._after_id is not None:
            try:
                self.frame.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def resume(self):
        """Tick again, starting with an immediate refresh."""
        if not self._is_paused:
            return
        self._is_paused = False
        self._on_refresh_tick()

    @property
    def is_ticking(self):
        return self._after_id is not None

    def close(self):
        if self._after_id is not None:
            try:
                self.frame.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None
        # The wheel binding is application-wide while the pointer is here; a
        # closed panel must not be left holding it.
        self._on_pointer_leave()
        super().close()
        self._widgets.clear()
        try:
            self.frame.destroy()
        except Exception as exc:
            events.debug("Panel Destroy Failed", str(exc), source=SOURCE,
                         exception=exc)
        events.debug("Panel Closed", self.name, source=SOURCE)

    # -- layout: planning a table --------------------------------------------
    #: Element types drawn with a caption in front of them. A button names
    #: itself; these name the value beside them.
    CAPTIONED = ("readonly", "entry", "dropdown", "indicator")
    #: Element types that are commands, and share one line of buttons.
    COMMANDS = ("button", "toggle", "file_save", "file_open", "region_select")

    def _build(self):
        self._plan_tables()
        super()._build()
        self._reflow()

    def _plan_tables(self):
        """Decide, before anything is drawn, which row sections are the rows
        of a table and what its columns are.

        A caption that two or more row sections share ("Port", "Gamepad",
        "Status") is a column: it is said ONCE, in a header row, and every
        row puts its control for it in that column — so a row with no
        gamepad leaves the Gamepad cell empty and its status still lands in
        the Status column. A row section whose captions are its own
        ("Scan", "Selected") is not a row of that table: it is a bar across
        the table, and keeps its caption inline. Setup is six rows of the
        first kind between a bar of each kind.
        """
        sections = list(self._schema().get("sections") or [])
        captions = []
        for section in sections:
            if section.get("layout") != "row":
                captions.append(None)
                continue
            captions.append([_label(element.get("text", ""))
                             for element in section.get("elements") or []
                             if element.get("type") in self.CAPTIONED])
        counts = {}
        for names in captions:
            for name in set(names or ()):
                counts[name] = counts.get(name, 0) + 1
        shared = {name for name, count in counts.items() if count > 1}
        self._row_kinds, order = [], []
        for names in captions:
            if names is None:
                self._row_kinds.append(None)
            elif names and set(names) <= shared:
                self._row_kinds.append("table")
                order += [name for name in names if name not in order]
            else:
                self._row_kinds.append("bar")
        self._table_columns = {name: index + 1 for index, name in enumerate(order)}

    # -- layout: sections ----------------------------------------------------
    def _make_section(self, title, layout="column"):
        """A section of the panel. `layout` is the schema's hint (Addendum 2).

        `column` is the stacked form: a heading, then one caption and one
        value per line, the value column the same width for every entry,
        dropdown and readout. `row` is the table form (`_make_row_section`).
        Consecutive column sections are a *run*, laid out in one or two
        columns by `_reflow` according to the panel's width.
        """
        index = self._section_index
        self._section_index += 1
        if layout == "row":
            kinds = self._row_kinds
            return self._make_row_section(
                title, kinds[index] if index < len(kinds) and kinds[index] else "bar")
        # A column section ends the table: the next row section starts a new
        # one, so a schema that interleaves the two still renders in order.
        self._table = None
        if self._section_run is None:
            holder = tk.Frame(self._body, background=_page())
            holder.pack(fill="x", padx=INSET, pady=(0, GAP))
            self._section_run = {"holder": holder, "sections": [],
                         "columns": tuple(tk.Frame(holder, background=_page())
                                         for _ in range(MAX_COLUMNS))}
            self._runs.append(self._section_run)
        container = tk.Frame(self._body, background=_page())
        label = tk.Label(container, text=_label(title), font=_font(STEP_1, bold=True),
                         anchor="w", background=_page(), foreground=theme.TEXT)
        label.grid(row=0, column=0, columnspan=3, sticky="w", pady=(INSET, GAP))
        self._section_titles.append(label)
        try:
            # The caption column takes the slack, so values end at the
            # section's right edge; the value column is one width for all.
            container.grid_columnconfigure(0, weight=1)
            container.grid_columnconfigure(1, minsize=VALUE_PX)
        except Exception as exc:
            events.debug("Section Columns Refused", str(exc), source=SOURCE,
                         exception=exc, every=5.0)
        self._grid[id(container)] = {"layout": "column", "row": 1, "strip": None}
        self._section_run["sections"].append(container)
        return container

    def _make_row_section(self, title, kind):
        """One line of the table: the row's name in column 0, then its cells.

        Every row section in a run shares ONE grid, because columns only line
        up inside a single grid. A `table` row puts each control under its
        caption's header; a `bar` row (its captions are its own) spans the
        table with its captions inline. A rule sets the header off from the
        rows, and the rows off from a bar that follows them.
        """
        self._section_run = None
        if self._table is None:
            self._table = tk.Frame(self._body, background=_page())
            self._table.pack(fill="x", padx=INSET, pady=(INSET, GAP))
            width = len(self._table_columns)
            self._grid[id(self._table)] = {
                "layout": "row", "row": -1, "kind": None, "bar": None,
                "has_header": False, "width": width, "extra": width + 1}
            if width:
                # The last shared column (the status) takes the slack, so a
                # long status has room instead of being cut off.
                self._stretch_column(self._table, width)
        state = self._grid[id(self._table)]
        span = max(1, state["width"])
        if state["kind"] is not None:
            state["row"] += 1           # the previous row's refusal line
        if kind == "table" and not state["has_header"]:
            state["row"] += 1
            for caption, column in self._table_columns.items():
                tk.Label(self._table, text=caption, font=_font(SMALL), anchor="w",
                         background=_page(), foreground=theme.MUTED
                         ).grid(row=state["row"], column=column, sticky="w",
                                padx=(0, SPACE[5]), pady=(GAP, 0))
            state["row"] += 1
            self._hairline(self._table, state["row"], span + 1)
            state["has_header"] = True
        elif kind == "bar" and state["kind"] == "table":
            state["row"] += 1
            self._hairline(self._table, state["row"], span + 1)
        state["row"] += 1
        state["kind"] = kind
        state["extra"] = state["width"] + 1
        caption = tk.Label(self._table, text=_sentence(title), font=_font(bold=True),
                           anchor="w", background=_page(), foreground=theme.TEXT)
        caption.grid(row=state["row"], column=0, sticky="w",
                     padx=(0, SPACE[5]), pady=GAP)
        self._section_titles.append(caption)
        state["bar"] = None
        if kind == "bar":
            bar = tk.Frame(self._table, background=_page())
            bar.grid(row=state["row"], column=1, columnspan=span, sticky="ew",
                     pady=GAP)
            state["bar"] = bar
        return self._table

    def _hairline(self, container, row, span):
        tk.Frame(container, height=1, background=theme.RULE).grid(
            row=row, column=0, columnspan=span, sticky="ew", pady=(GAP, GAP))

    def _reflow(self, width=None):
        """One column of sections, or more once the panel is wide enough.

        Columns are filled shortest-first in schema order, so a tall
        section (Configuration) sits beside two short ones instead of leaving
        a hole under its neighbour. Sections are packed `in_` a column frame:
        Tk cannot re-parent a widget, but it can manage one inside any
        descendant of its parent.
        """
        if width is None:
            try:
                width = self._canvas.winfo_width()
            except Exception:
                width = 0
        columns = (max(1, min(MAX_COLUMNS, width // COLUMN_PX))
                   if isinstance(width, int) else 1)
        if columns == self._layout_columns:
            return
        self._layout_columns = columns
        for run in self._runs:
            self._lay_out_run(run, columns)

    def _lay_out_run(self, run, columns):
        holder, frames = run["holder"], run["columns"]
        columns = max(1, min(columns, len(run["sections"]) or 1))
        try:
            for index, frame in enumerate(frames):
                is_used = index < columns
                holder.grid_columnconfigure(index, weight=1 if is_used else 0,
                                            uniform="run" if is_used else "")
                if is_used:
                    frame.grid(row=0, column=index, sticky="new",
                               padx=(SPACE[6] if index else 0, 0))
                else:
                    frame.grid_remove()
        except Exception as exc:
            events.debug("Run Not Laid Out", str(exc), source=SOURCE,
                         exception=exc, every=5.0)
        heights = [0] * columns
        for section in run["sections"]:
            target = heights.index(min(heights))
            try:
                section.pack_forget()
                section.pack(in_=frames[target], fill="x", anchor="n")
            except Exception as exc:
                events.debug("Section Not Placed", str(exc), source=SOURCE,
                             exception=exc, every=5.0)
            heights[target] += self._height_of(section)

    def _height_of(self, section):
        try:
            section.update_idletasks()
            height = section.winfo_reqheight()
        except Exception:
            height = None
        if isinstance(height, int) and height > 1:
            return height
        return self._grid.get(id(section), {}).get("row") or 1

    # -- layout: cells ---------------------------------------------------------
    def _cursor(self, container):
        return self._grid.setdefault(id(container), {
            "layout": "column", "row": 1, "strip": None})

    def _stretch_column(self, container, column):
        """Let one column absorb the slack."""
        try:
            container.grid_columnconfigure(column, weight=1)
        except Exception as exc:
            events.debug("Column Weight Refused", str(exc), source=SOURCE,
                         exception=exc, every=5.0)

    def _field(self, container, element):
        """Where one captioned control goes. -> (parent, place)

        `place(widget, fill)` positions the control; `fill` says what it is:
        "value" a readout (always takes its cell's width, so it can elide),
        "field" an entry or dropdown (the value column's width in a section,
        its own width in a table), "mark" a lamp or a stop disc (its own size,
        at the value column's right edge).
        """
        state = self._cursor(container)
        text = _label(element.get("text", ""))
        self._register(element, slot=self._notice_slot(container))
        if state["layout"] == "column":
            row = state["row"]
            state["row"] += 2           # the row under it is its refusal line
            state["last_row"] = row
            state["strip"] = None
            tk.Label(container, text=text, font=_font(), anchor="w",
                     background=_page(), foreground=theme.MUTED
                     ).grid(row=row, column=0, sticky="w", padx=(0, PAD), pady=GAP)

            def place(widget, fill):
                widget.grid(row=row, column=1, pady=GAP,
                            sticky="e" if fill == "mark" else "ew")
                return widget
            return container, place
        bar = state.get("bar")
        if bar is not None:
            tk.Label(bar, text=text, font=_font(), anchor="w", background=_page(),
                     foreground=theme.MUTED).pack(side="left", padx=(0, SPACE[3]))

            def place(widget, fill):
                widget.pack(side="left", fill="x" if fill == "value" else None,
                            expand=fill == "value", padx=(0, SPACE[5]))
                return widget
            return bar, place
        column = self._table_columns.get(text)
        if column is None:
            column = state["extra"]
            state["extra"] += 1

        def place(widget, fill):
            widget.grid(row=state["row"], column=column, pady=GAP,
                        padx=(0, SPACE[5]), sticky="ew" if fill == "value" else "w")
            return widget
        return container, place

    def _command_slot(self, container, element=None):
        """Where one command goes. -> (parent, place)

        In a section, consecutive commands share ONE line — "Enter autonomous
        mode", "Enter manual mode", "Step" — instead of a stack of full-width
        bars; the line ends at the next control that is not a command. The
        row under the line is where a refusal of any of them is shown.
        """
        state = self._cursor(container)
        if state["layout"] == "column":
            strip = state.get("strip")
            if strip is None:
                strip = tk.Frame(container, background=_page())
                strip.grid(row=state["row"], column=0, columnspan=3, sticky="w",
                           pady=GAP)
                state["strip_slot"] = (container, state["row"] + 1, 0, 3)
                state["row"] += 2
                state["strip"] = strip
            if element is not None:
                self._register(element, slot=state["strip_slot"])
            return strip, lambda widget: widget.pack(side="left", padx=(0, PAD))
        if element is not None:
            self._register(element, slot=self._notice_slot(container))
        bar = state.get("bar")
        if bar is not None:
            return bar, lambda widget: widget.pack(side="left", padx=(0, PAD))
        column = state["extra"]
        state["extra"] += 1
        return container, lambda widget: widget.grid(
            row=state["row"], column=column, sticky="w", padx=(0, PAD), pady=GAP)

    def _wide_slot(self, container, caption=None):
        """Where a plot, a picture or a log goes: the full width of its
        section, under its caption. -> (parent, place)"""
        state = self._cursor(container)
        if state["layout"] == "column":
            state["strip"] = None
            if caption:
                tk.Label(container, text=_label(caption), font=_font(), anchor="w",
                         background=_page(), foreground=theme.MUTED
                         ).grid(row=state["row"], column=0, columnspan=3,
                                sticky="w", pady=(GAP, 0))
                state["row"] += 1
            row = state["row"]
            state["row"] += 1
            return container, lambda widget, sticky="ew": widget.grid(
                row=row, column=0, columnspan=3, sticky=sticky, pady=GAP)
        parent, place = self._command_slot(container)
        return parent, lambda widget, sticky=None: place(widget)

    def _notice_slot(self, container):
        """(container, row, column, span): the grid cell under a control
        where its refusal is shown. In a column section it is the row under
        the control's own; in a table, the row under the table row."""
        state = self._cursor(container)
        if state["layout"] == "column":
            return (container, state["row"] + 1, 0, 3)
        return (container, state["row"] + 1, 1, max(1, state.get("width") or 1))

    def _register(self, element, **widgets):
        entry = self._widgets.setdefault(id(element), {})
        entry.update(widgets)
        entry.setdefault("is_enabled", True)
        return entry

    def _entry_for(self, element):
        return self._widgets.get(id(element), {})

    # -- commands: a label drawn as a button -------------------------------------
    def _button_label(self, parent, element, on_click, text=None):
        """A `tk.Label` styled as a button: `tk.Button` ignores bg/fg on Aqua.
        -> the frame to place.

        It sits in a `_Ring`: Aqua does not draw a Label's highlight ring, so
        an outlined command (an OFF toggle) rendered as bare text. It has
        every state a control needs: hover (a step lighter), keyboard focus
        (a 2 px ink ring; Return and Space press it), disabled (the theme's
        disabled pair, and a click does nothing). It is at least
        `MIN_TARGET_PX` tall at every font size.
        """
        ring = _Ring(parent, _page())
        widget = tk.Label(ring.inner, text=_label(element.get("text", "") if text is None
                                                 else text),
                          font=_font(), relief="flat", padx=SPACE[4],
                          pady=_target_pady(), cursor="hand2", takefocus=1,
                          highlightthickness=0)
        widget.pack(fill="both", expand=True)
        self._register(element, widget=widget, outline=ring.inner, ring=ring)

        def _on_widget_click(_event=None, element=element):
            if not self._entry_for(element).get("is_enabled", True):
                return "break"
            on_click(element)
            return "break"

        def _on_flag(name, value, element=element):
            self._entry_for(element)[name] = value
            self._paint_command(element)

        widget.bind("<Button-1>", _on_widget_click)
        widget.bind("<Return>", _on_widget_click)
        widget.bind("<space>", _on_widget_click)
        widget.bind("<Enter>", lambda _e: _on_flag("is_hovered", True))
        widget.bind("<Leave>", lambda _e: _on_flag("is_hovered", False))
        widget.bind("<FocusIn>", lambda _e: _on_flag("is_focused", True))
        widget.bind("<FocusOut>", lambda _e: _on_flag("is_focused", False))
        self._paint_command(element)
        return ring.outer

    @staticmethod
    def _command_colors(role):
        """(background, foreground, border) of a command, from its role.

        Only the stop object carries the signal colour. A danger command (a
        model's own "Stop run") renders neutral, as it does in Qt and on the
        Web (DS-9): one red. A warning one is outlined in the trace colour.
        Every border is 3:1 on the panel."""
        if role == "warning":
            return _page(), theme.TRACE, theme.TRACE
        background, foreground = theme.colors(
            "neutral" if role in (None, "danger") else role)
        return background, foreground, INPUT_BORDER

    def _paint_command(self, element):
        """Configure only what changed: an unchanged command costs a tuple
        compare, not four Tk calls (F21)."""
        entry = self._entry_for(element)
        widget = entry.get("widget")
        if widget is None:
            return
        if not entry.get("is_enabled", True):
            background, foreground = theme.DISABLED
            border = theme.RULE
        elif element["type"] == "toggle":
            colors = theme.toggle_colors(element, bool(entry.get("is_on")))
            background, foreground, border = (colors["background"],
                                              colors["foreground"], colors["border"])
            if not entry.get("is_on") and border != theme.SIGNAL:
                # An OFF toggle is its role outlined on the panel; a quiet
                # role's own fill is too close to the panel to be an outline.
                border = INPUT_BORDER
        else:
            background, foreground, border = self._command_colors(element.get("role"))
        if entry.get("is_hovered") and entry.get("is_enabled", True):
            background = theme.mix(background, theme.TEXT, 0.08)
        is_focused = bool(entry.get("is_focused"))
        cursor = "hand2" if entry.get("is_enabled", True) else "arrow"
        key = (background, foreground, border, is_focused, cursor)
        entry["border"] = border
        if entry.get("paint") == key:
            return
        entry["paint"] = key
        try:
            widget.configure(background=background, foreground=foreground,
                             cursor=cursor)
        except Exception:
            pass
        ring = entry.get("ring")
        if ring is not None:
            ring.paint(is_focused, border)

    def _role_colors(self, role):
        """The schema names the meaning; the theme owns the palette."""
        return theme.colors(role or "neutral")

    # -- element renderers -------------------------------------------------
    def _make_readonly(self, container, element):
        """A readout: a number in the trace colour, one step larger than its
        caption, on the panel itself - no box, so it is never mistaken for a
        field to type into - right-aligned at the same edge as the entries.

        It takes its cell's width and never asks for more: a value longer
        than the cell ("identifying /dev/cu.debug-console (2 of 2)...") is
        cut at a word with an ellipsis and the whole of it is the tooltip.
        In a table's status column and in a bar it reads left to right like
        the text it is; in a section it is a number and aligns right.
        """
        parent, place = self._field(container, element)
        state = self._cursor(container)
        is_text = state["layout"] == "row"
        var = tk.StringVar(value="")
        font = _font(STEP_1)
        value = tk.Label(parent, text="", font=font, width=1,
                         anchor="w" if is_text else "e", relief="flat",
                         background=_page(), foreground=theme.TRACE)
        place(value, "value")
        tooltip = _Tooltip(value)
        mark = None
        if (element.get("role") or "neutral") == "danger" and not is_text:
            # A fault reason is ink with a signal mark beside it: signal
            # text is 2.73:1 on the panel, and this is the one readout the
            # operator must read during a fault (F14, AUD-5).
            size = _lamp_px()
            mark = tk.Canvas(parent, width=size, height=size, background=_page(),
                             highlightthickness=0)
            mark.grid(row=state["last_row"], column=2, sticky="w",
                      padx=(SPACE[1], 0))
        self._register(element, widget=value, var=var, font=font, tooltip=tooltip,
                       shown=None, mark=mark)
        value.bind("<Configure>", lambda _e, el=element: self._fit_readout(el),
                   add="+")

    def _fit_readout(self, element):
        """Show as much of the value as the cell holds; the rest is the
        tooltip. With no measure of the cell, show it all."""
        entry = self._entry_for(element)
        widget, var = entry.get("widget"), entry.get("var")
        if widget is None or var is None:
            return
        text = str(var.get() or "")
        try:
            room = widget.winfo_width() - 2 * int(widget.cget("padx") or 0) - 2
        except Exception:
            room = None
        shown = _elide(entry.get("font"), text, room if isinstance(room, int) else None,
                       middle=_is_identifier(text))
        tooltip = entry.get("tooltip")
        if tooltip is not None:
            tooltip.text = text if shown != text else ""
        if entry.get("shown") == shown:
            return
        entry["shown"] = shown
        try:
            widget.configure(text=shown)
        except Exception:
            pass

    def _make_entry(self, container, element):
        parent, place = self._field(container, element)
        var = tk.StringVar(value="")
        # The well's border is 3:1 on the panel and focus is a 2 px ink
        # ring around it (F25): a 1 px trace ring was the only focus mark.
        ring = _Ring(parent, _page())
        widget = tk.Entry(ring.inner, textvariable=var, font=_font(),
                          width=FIELD_WIDTH, justify="right", relief="flat",
                          borderwidth=0, highlightthickness=0,
                          background=theme.WELL, foreground=theme.TEXT,
                          insertbackground=theme.TEXT,
                          disabledbackground=_page(),
                          disabledforeground=theme.DISABLED[1])
        widget.pack(fill="both", expand=True, ipady=max(0, _target_pady() - GAP))
        if element.get("value_type") in ("int", "float"):
            self._attach_validator(widget, element)
        place(ring.outer, "field")
        widget.bind("<Return>", lambda _e, el=element: self._on_entry_commit(el))
        widget.bind("<FocusIn>", lambda _e, r=ring: r.paint(True))
        widget.bind("<FocusOut>", lambda _e, el=element, r=ring:
                    (r.paint(False), self._on_entry_commit(el)))
        unit = element.get("unit")
        if unit and self._cursor(container)["layout"] == "column":
            tk.Label(parent, text=unit, font=_font(SMALL), anchor="w",
                     background=_page(), foreground=theme.MUTED
                     ).grid(row=self._cursor(container)["last_row"], column=2,
                            sticky="w", padx=(GAP, 0))
        elif unit:
            tk.Label(parent, text=unit, font=_font(SMALL), background=_page(),
                     foreground=theme.MUTED).pack(side="left", padx=(0, PAD))
        self._register(element, widget=widget, var=var, last_text="",
                       cell=ring.outer, ring=ring)

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
        previous, self._acting = self._acting, element
        try:
            if result.is_refused:
                self._show_refused(result.reason)
            elif result.is_ok:
                self._show_refused("")
        finally:
            self._acting = previous
        return result

    def _on_dropdown_selected(self, element):
        entry = self._entry_for(element)
        if not entry.get("is_enabled", True):
            return None
        var = entry.get("var")
        shown = var.get() if var else ""
        # The box shows a middle-elided label; the command gets the name.
        full = (entry.get("labels") or {}).get(shown, shown)
        return self._run(element, args=(full,))

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
        shown = var.get() if var is not None else ""
        current = (entry.get("labels") or {}).get(shown, shown)
        if current and current not in options:
            options = [current] + options
        entry["options"] = options
        # Long names are cut in the MIDDLE so the part that tells two ports
        # or fifty gamepads apart stays in view (F15); a label that would
        # collide with another keeps its whole name.
        limit = entry.get("chars") or DROPDOWN_WIDTH
        labels, seen = {}, set()
        for option in options:
            label = _elide_middle(option, limit)
            if label in seen:
                label = option
            seen.add(label)
            labels[label] = option
        entry["labels"] = labels
        entry["label_of"] = {full: label for label, full in labels.items()}
        values = list(labels)
        if entry.get("values") == values:
            return
        entry["values"] = values
        try:
            widget.configure(values=values)
        except Exception:
            pass

    def _on_region_clicked(self, element):
        picker = _RegionPicker(self.frame)
        region = picker.pick()
        if region is None:
            previous, self._acting = self._acting, element
            try:
                self._show_refused(picker.reason)
            finally:
                self._acting = previous
            return None
        self._show_refused("")
        return self._run(element, args=region)

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

    def _on_open_clicked(self, element):
        extensions = element.get("extensions") or ["csv"]
        path = filedialog.askopenfilename(
            title=element.get("text", "Open"),
            filetypes=[(e.upper(), "*." + e) for e in extensions])
        if not path:
            return None
        return self._run(element, args=(path,))

    def _redraw_plot(self, element, series):
        entry = self._entry_for(element)
        canvas = entry.get("widget")
        if canvas is None:
            return
        values = [v for v in list((series or {}).get("y") or [])
                  if isinstance(v, (int, float))]
        width, height = 360, 160
        try:
            # The canvas fills its section; draw to the size it was given.
            measured = canvas.winfo_width(), canvas.winfo_height()
            if all(isinstance(v, int) and v > 1 for v in measured):
                width, height = measured
        except Exception:
            pass
        # The same series at the same size is the same picture: it was
        # deleted and redrawn ten times a second regardless (F21).
        key = (tuple(values), width, height)
        if entry.get("plot_key") == key:
            return
        entry["plot_key"] = key
        try:
            canvas.delete("all")
        except Exception:
            return
        if len(values) < 2:
            # An empty plot that says why, rather than a blank rectangle
            # (REDPERCENT-17).
            text = "no data yet" if not values else "one sample so far"
            try:
                canvas.create_text(width / 2, height / 2, text=text,
                                   fill=theme.MUTED, font=_font(SMALL))
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
            canvas.create_line(*points, fill=theme.TRACE, width=2)
        except Exception as exc:
            events.debug("Plot Draw Failed", str(exc), source=SOURCE,
                         exception=exc, every=5.0)

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

    def _make_button(self, container, element):
        parent, place = self._command_slot(container, element)
        place(self._button_label(parent, element, lambda el: self._run(el)))

    def _make_toggle(self, container, element):
        """A two-state command. A model's own stop (`is_estopped`) is not a
        button at all: it is the stop object, the same disc as the
        dashboard's, bench-sized, reading `Stop` / `Clear`."""
        if element.get("model_attr") == "is_estopped":
            parent, place = self._field(container, element)
            disc = _Mushroom(parent, lambda el=element: self._on_mushroom_pressed(el),
                             background=_page(), face_step=SMALL,
                             floor=MINI_STOP_DIAMETER)
            place(disc.canvas, "mark")
            self._register(element, widget=disc.canvas, mushroom=disc)
            return
        parent, place = self._command_slot(container, element)
        place(self._button_label(parent, element, lambda el: self._run_toggle(el),
                                 text=element.get("false_text", "")))

    def _on_mushroom_pressed(self, element):
        if not self._entry_for(element).get("is_enabled", True):
            return None
        return self._run_toggle(element)

    def _make_dropdown(self, container, element):
        """A dropdown whose choices are re-read when it is opened
        (`postcommand`) and every `OPTIONS_REFRESH_MS` — the ⟳ glyph beside
        every dropdown did what opening it now does."""
        parent, place = self._field(container, element)
        var = tk.StringVar(value="")
        is_table = self._cursor(container)["layout"] == "row"
        options = dict(textvariable=var, state="readonly",
                       width=DROPDOWN_WIDTH if is_table else FIELD_WIDTH,
                       postcommand=lambda el=element: self._refresh_options(el))
        ring = _Ring(parent, _page(), border=_page())
        try:
            widget = ttk.Combobox(ring.inner, font=_font(), **options)
        except Exception:
            # ttk takes `font` on a Combobox on current builds; an older one
            # refuses it, and the style's font is then used.
            widget = ttk.Combobox(ring.inner, **options)
        widget.pack(fill="both", expand=True)
        place(ring.outer, "field")
        widget.bind("<<ComboboxSelected>>",
                    lambda _e, el=element: self._on_dropdown_selected(el))
        widget.bind("<FocusIn>", lambda _e, r=ring: r.paint(True))
        widget.bind("<FocusOut>", lambda _e, r=ring: r.paint(False))
        # How many characters a name may keep: a table's box is sized in
        # characters; a section's fills the value column.
        chars = (DROPDOWN_WIDTH - 1 if is_table else
                 max(FIELD_WIDTH, (VALUE_PX - SPACE[6]) // max(1, _width_px(_font(), "0"))))
        self._register(element, widget=widget, var=var, options=[],
                       tooltip=_Tooltip(widget), cell=ring.outer, ring=ring,
                       chars=chars, labels={}, label_of={})
        self._refresh_options(element)

    def _make_region_select(self, container, element):
        parent, place = self._command_slot(container, element)
        place(self._button_label(parent, element, self._on_region_clicked))
        var = tk.StringVar(value=sch.format_region(None))
        shown = tk.Label(parent, textvariable=var, font=_font(SMALL), anchor="w",
                         background=_page(), foreground=theme.MUTED)
        place(shown)
        # The captured region is *drawn*, not announced: PySide's confirming
        # modal over an always-on-top overlay is PYSIDE-12's deadlock.
        self._register(element, var=var)
        # A line of commands never continues past the region it reports.
        self._cursor(container)["strip"] = None

    def _make_file_save(self, container, element):
        parent, place = self._command_slot(container, element)
        place(self._button_label(parent, element, self._on_save_clicked))

    def _make_file_open(self, container, element):
        parent, place = self._command_slot(container, element)
        place(self._button_label(parent, element, self._on_open_clicked))

    def _make_plot(self, container, element):
        """A Canvas polyline in the trace colour. No matplotlib: the model
        publishes the series and each renderer draws it (D-6)."""
        parent, place = self._wide_slot(container, element.get("text"))
        canvas = tk.Canvas(parent, height=160, width=360,
                           background=theme.BACKGROUND, highlightthickness=1,
                           highlightbackground=theme.RULE)
        place(canvas)
        self._register(element, widget=canvas)

    def _make_image(self, container, element):
        parent, place = self._wide_slot(container, element.get("text"))
        widget = tk.Label(parent, background=theme.BACKGROUND,
                          foreground=theme.MUTED, text="No figure yet",
                          font=_font(SMALL), padx=PAD, pady=PAD)
        place(widget, "w")
        self._slow_commands.add(element.get("data_command"))
        self._register(element, widget=widget, photo=None, data=None)

    def _make_indicator(self, container, element):
        """A lamp, not a second copy of the label.

        The label went on the lamp as well as in front of it, so a fault
        indicator read "Fault   Fault" and said nothing about the fault. The
        caption names it once; the lamp carries only the state: lit, it is
        filled with the trace colour (the signal colour for a danger lamp -
        a latch, a fault); unlit, it is an empty ring.
        """
        parent, place = self._field(container, element)
        size = _lamp_px()
        widget = tk.Canvas(parent, width=size, height=size,
                           background=_page(), highlightthickness=0)
        place(widget, "mark")
        self._register(element, widget=widget, lamp=None, size=size)

    @staticmethod
    def _lamp_colors(element, is_on):
        """(fill, ring) of a lamp."""
        if not is_on:
            return _page(), theme.MUTED
        lit = theme.SIGNAL if element.get("on_role") == "danger" else theme.TRACE
        return lit, lit

    def _make_log_stream(self, container, element):
        parent, place = self._wide_slot(container, element.get("text"))
        widget = tk.Text(parent, height=EVENT_LOG_LINES + 1, width=48,
                         state="disabled", relief="flat", font=_font(SMALL),
                         background=theme.BACKGROUND, foreground=theme.TEXT,
                         highlightthickness=1, highlightbackground=theme.RULE,
                         padx=GAP, pady=GAP, wrap="word")
        place(widget)
        self._register(element, widget=widget, last_text=None)

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
        if element["type"] == "readonly" and str(text).strip().lower() in ("true", "false"):
            # A boolean reads as the operator would say it, as in the Web and
            # Qt views ("Yes" / "No"), not as a programming literal.
            return "Yes" if str(text).strip().lower() == "true" else "No"
        if element.get("value_type") != "int" or not text:
            return text
        try:
            number = float(text)
        except (TypeError, ValueError):
            return text
        return str(int(number))

    def _style_readout(self, element, text):
        """A readout's value is the trace colour - the colour a live number
        is drawn in. A danger readout (a fault reason) is ink with a signal
        mark beside it (F14). Nothing to show is `--` in muted ink; a panel
        whose readings are stale or whose link is lost mutes every value,
        because a frozen number drawn in trace reads as live (F3)."""
        entry = self._entry_for(element)
        widget = entry.get("widget")
        if widget is None:
            return
        is_danger = (element.get("role") or "neutral") == "danger"
        if text == EMPTY_READOUT or self._is_quiet():
            foreground = theme.MUTED
        elif is_danger:
            foreground = theme.TEXT
        else:
            foreground = theme.TRACE
        marked = is_danger and text not in ("", EMPTY_READOUT)
        if entry.get("colors") != foreground:
            entry["colors"] = foreground
            try:
                widget.configure(background=_page(), foreground=foreground)
            except Exception:
                pass
        mark = entry.get("mark")
        if mark is not None and entry.get("marked") != marked:
            entry["marked"] = marked
            try:
                mark.delete("all")
                if marked:
                    size = int(mark.cget("width") or LAMP_PX)
                    mark.create_oval(2, 2, size - 2, size - 2, fill=theme.SIGNAL,
                                     outline=theme.SIGNAL)
            except Exception:
                pass

    def _is_quiet(self):
        """True while the panel's values cannot be trusted as live."""
        return bool(self._is_stale) or bool(self.lost_devices)

    def _set_text(self, element, text):
        entry = self._entry_for(element)
        var = entry.get("var")
        if var is None:
            return
        text = "" if text is None else str(text)
        text = self._display_text(element, text)
        if element["type"] == "readonly":
            entry["shown_text"] = text
            self._style_readout(element, text)
        if element["type"] == "dropdown":
            options = entry.get("options") or []
            if text and text not in options:
                self._refresh_options(element)
            shown = (entry.get("label_of") or {}).get(text) or _elide_middle(
                text, entry.get("chars") or DROPDOWN_WIDTH)
            tooltip = entry.get("tooltip")
            if tooltip is not None:
                # The whole name, whenever the box shows less of it.
                tooltip.text = text if shown != text else ""
            if var.get() != shown:
                var.set(shown)
            entry["last_text"] = text
            return
        if var.get() != text:
            var.set(text)
        entry["last_text"] = text
        if element["type"] == "readonly":
            self._fit_readout(element)
        if element["type"] == "entry":
            self._bounds_hint(element)

    def _set_on(self, element, is_on):
        entry = self._entry_for(element)
        widget = entry.get("widget")
        if widget is None:
            return
        was_on = entry.get("is_on")
        entry["is_on"] = is_on
        if entry.get("mushroom") is not None:
            entry["mushroom"].set_latched(is_on)
            return
        if element["type"] == "indicator":
            # Never text: the caption in front of the lamp already names it,
            # and a lamp that repeats its own label ("Fault   Fault") is the
            # one thing it must not say.
            lamp = self._lamp_colors(element, is_on)
            if entry.get("lamp") == lamp:
                return
            entry["lamp"] = lamp
            size = entry.get("size") or LAMP_PX
            try:
                widget.delete("all")
                widget.create_oval(2, 2, size - 2, size - 2, fill=lamp[0],
                                   outline=lamp[1], width=1.5)
            except Exception as exc:
                events.debug("Lamp Draw Failed", str(exc), source=SOURCE,
                             exception=exc, every=5.0)
            return
        text = _label(element["true_text"] if is_on else element["false_text"])
        try:
            if widget.cget("text") != text:
                widget.configure(text=text)
        except Exception as exc:
            events.debug("Toggle Draw Failed", str(exc), source=SOURCE,
                         exception=exc, every=5.0)
        if was_on != is_on:
            self._paint_command(element)

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
        failed to set (VIEW-TKINTER-14). A stop disc is gated like any
        command but never drawn dimmed.
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
        elif element["type"] in self.COMMANDS and entry.get("mushroom") is None:
            if was_enabled != bool(is_enabled) or "painted" not in entry:
                entry["painted"] = True
                self._paint_command(element)
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
        self._paint_health()
        events.debug("Staleness Changed", f"{self.name} stale={is_stale}",
                      source=SOURCE)

    def _set_lost(self, lost):
        """A device whose link is lost (F3, HC-1): the model says so in
        `state.devices` and nothing else on the panel did - the toggle stayed
        lit and the numbers stayed trace. Now the grouping rule turns signal,
        the title and a line under it say what was lost, and every value is
        muted."""
        lost = tuple(lost)
        if lost == self.lost_devices:
            return
        self.lost_devices = lost
        self._paint_health()
        events.debug("Link State Changed", f"{self.name} lost={list(lost)}",
                     source=SOURCE)

    def _paint_health(self):
        lost, is_stale = self.lost_devices, bool(self._is_stale)
        if lost:
            title = f"{self.name} (connection lost)"
        elif is_stale:
            title = f"{self.name} (stale)"
        else:
            title = self.name
        heading = theme.MUTED if is_stale and not lost else theme.TEXT
        try:
            self._title.configure(foreground=heading, text=title)
            for label in self._section_titles:
                label.configure(foreground=heading)
            self._rule.configure(background=theme.SIGNAL if lost else theme.RULE,
                                 height=FOCUS_PX if lost else 1)
        except Exception:
            pass
        try:
            if lost:
                self._health.configure(text=(
                    f"Connection lost: {_device_list(lost)}. Its readings are "
                    "frozen. Press Stop, check the cable, then relaunch from "
                    "Setup."))
                self._health.pack(fill="x", padx=INSET, pady=(0, GAP),
                                  after=self._rule)
            else:
                self._health.configure(text="")
                self._health.pack_forget()
        except Exception:
            pass
        for element in self._elements:
            if element["type"] == "readonly":
                entry = self._entry_for(element)
                entry["colors"] = None
                self._style_readout(element, entry.get("shown_text", ""))

    def _confirm(self, prompt):
        return _confirm(self.frame, prompt)

    def _show_refused(self, reason):
        """Non-modal, and AT the control that caused it (F10): the row
        under it, wrapped, scrolled into view, gone on the next success.
        With no control to point at it is the line at the foot of the
        panel. A refusal used to land in one place for every control and
        was cut off at about 60 % of its length."""
        self._clear_notice()
        self._notice_text = reason or ""
        if not reason:
            try:
                self._status.configure(text="")
            except Exception:
                pass
            return
        slot = self._entry_for(self._acting).get("slot") if self._acting else None
        if slot is None:
            try:
                self._status.configure(text=reason, foreground=theme.TEXT,
                                       background=NOTICE_BG)
            except Exception:
                pass
            return
        container, row, column, span = slot
        try:
            notice = tk.Label(container, text=reason, font=_font(), anchor="w",
                              justify="left", wraplength=self._notice_room(container),
                              background=NOTICE_BG, foreground=theme.TEXT,
                              padx=SPACE[3], pady=SPACE[1])
            notice.grid(row=row, column=column, columnspan=span, sticky="ew",
                        pady=(0, GAP))
        except Exception as exc:
            events.debug("Refusal Not Placed", str(exc), source=SOURCE,
                         exception=exc, every=5.0)
            return
        self._notice = notice
        self._scroll_into_view(notice)

    def _clear_notice(self):
        if self._notice is not None:
            try:
                self._notice.destroy()
            except Exception:
                pass
            self._notice = None
        try:
            self._status.configure(background=_page())
        except Exception:
            pass

    def _notice_room(self, container):
        """Wrap width for a refusal: its container's width when Tk knows it,
        else the panel's."""
        for widget in (container, self._canvas):
            try:
                width = widget.winfo_width()
            except Exception:
                continue
            if isinstance(width, int) and width > SPACE[6] * 8:
                return width - 2 * SPACE[3]
        return 640

    def _scroll_into_view(self, widget):
        """Scroll the panel just enough that `widget` is fully visible."""
        try:
            widget.update_idletasks()
            top, node = 0, widget
            while node is not None and node is not self._body:
                top += node.winfo_y()
                node = node.master
            bottom = top + widget.winfo_reqheight()
            total = self._body.winfo_height()
            first, last = self._canvas.yview()
        except Exception:
            return
        numbers = (top, bottom, total, first, last)
        if not all(isinstance(v, (int, float)) for v in numbers) or total <= 1:
            return
        view_top, view_bottom = first * total, last * total
        if top >= view_top and bottom <= view_bottom:
            return
        if top < view_top:
            target = top - SPACE[4]
        else:
            target = bottom + SPACE[4] - (view_bottom - view_top)
        try:
            self._canvas.yview_moveto(max(0.0, min(1.0, target / total)))
        except Exception:
            pass

    def _run(self, element, args=()):
        """`PanelView._run`, remembering which control is acting so its
        refusal can be drawn at it. A failed command is already on the
        alert band; the refusal line from an earlier attempt is cleared
        rather than left to describe something else."""
        previous, self._acting = self._acting, element
        try:
            result = super()._run(element, args)
        finally:
            self._acting = previous
        if result is not None and getattr(result, "is_failed", False):
            self._show_refused("")
        return result

    def _state(self):
        state = super()._state()
        self._last_state = state if isinstance(state, dict) else {}
        return state

    def _apply_theme(self):
        for widget in (self.frame, self._body, self._canvas):
            try:
                widget.configure(background=_page())
            except Exception:
                pass

    def _refresh(self):
        super()._refresh()
        devices = self._last_state.get("devices") or {}
        self._set_lost([name for name, status in sorted(devices.items())
                        if str(status) == "lost"])
        self._options_due -= self.REFRESH_MS
        if self._options_due <= 0:
            self._options_due = self.OPTIONS_REFRESH_MS
            for element in self._elements:
                if element["type"] == "dropdown":
                    self._refresh_options(element)


class TkDashboard(Dashboard):
    """The window: a tab per model, the Setup panel, the global stop,
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
        self._alerts = []            # unacknowledged needs_ack events
        self._station_text = None

        self.root = tk.Tk()
        self.root.title("Transfer Station")
        self.root.geometry("1100x850")
        self.root.configure(background=theme.BACKGROUND)
        try:
            self.root.minsize(720, 520)
        except Exception:
            pass
        self._configure_styles()

        # Pack order is allocation order, and the notebook is the one widget
        # here that expands: everything it must never push off the window is
        # packed BEFORE it. The stop went off the bottom of the screen the
        # first time a tall panel opened, which is a stop control the
        # operator cannot reach — the worst defect this view can have. The
        # toolbar takes its strip at the top, the stop bar and the event log
        # take theirs at the bottom, and the notebook gets what is left.
        # The alert band is built with them and packed directly above the
        # stop bar only while an error waits: an error never covers, dims or
        # blocks the stop (F1).
        self._build_toolbar()
        self._build_stop_button()
        self._build_alert_band()
        self._build_event_panel()

        self.notebook = ClosableNotebook(self.root, on_close_tab=self._on_tab_close)
        self.notebook.pack(side="top", fill="both", expand=True,
                           padx=PAD, pady=(GAP, 0))
        try:
            # Control-Tab moves between tabs, and the tab strip takes focus.
            self.notebook.enable_traversal()
        except Exception:
            pass
        self._bind_stop_keys()

        self._build_menu_bar()

        # D-4: gate input on focus, never stop. Bound on the root, filtered to
        # the root's own events so a child widget's focus traffic is not a
        # window focus change.
        self.root.bind("<FocusIn>", self._on_window_focus)
        self.root.bind("<FocusOut>", self._on_window_focus)
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._hook_macos_quit()

    # -- construction ------------------------------------------------------
    def _configure_styles(self):
        """Fonts in the Mac's own points, and ttk drawn from the theme.

        Aqua's ttk ignores colours (a blue-arrowed white combobox and a
        centred silver tab strip on a dark panel), so ttk runs the `clam`
        theme here, coloured from the same six tokens, on every platform:
        tabs left-aligned on the type scale, dark fields with a hairline
        border and a trace focus ring, quiet scrollbars.
        """
        global _PIXEL_FONTS
        _PIXEL_FONTS = _windowing_system(self.root) == "aqua"
        base, surface, text = theme.BACKGROUND, _page(), theme.TEXT
        muted, rule = theme.MUTED, theme.RULE
        control = theme.colors("neutral")[0]
        arrow = max(12, theme.size(BASE))
        try:
            style = ttk.Style(self.root)
            style.theme_use("clam")
        except Exception as exc:
            events.debug("ttk Theme Not Set", str(exc), source=SOURCE, exception=exc)
            return
        settings = [
            (".", dict(background=base, foreground=text, font=_font(),
                       bordercolor=rule, lightcolor=base, darkcolor=base,
                       troughcolor=base, focuscolor=FOCUS_INK,
                       fieldbackground=base, selectbackground=control,
                       selectforeground=text, insertcolor=text, arrowcolor=muted)),
            ("TFrame", dict(background=surface)),
            ("TNotebook", dict(background=base, borderwidth=0, tabmargins=(0, 0, 0, 0),
                               tabposition="nw", lightcolor=base, darkcolor=base,
                               bordercolor=base)),
            # A focused tab is ringed in ink, 2 px; it resolved to the strip's
            # own colour, so a focused tab showed nothing (AUD-10).
            ("TNotebook.Tab", dict(background=base, foreground=muted, font=_font(),
                                   padding=(SPACE[5], _target_pady() + FOCUS_PX),
                                   borderwidth=0, bordercolor=base, lightcolor=base,
                                   darkcolor=base, focuscolor=FOCUS_INK,
                                   focusthickness=FOCUS_PX)),
            ("TCombobox", dict(fieldbackground=base, background=control,
                               foreground=text, arrowcolor=muted,
                               bordercolor=INPUT_BORDER, lightcolor=base,
                               darkcolor=base, padding=(SPACE[2], _target_pady() - SPACE[0]),
                               arrowsize=arrow, focuscolor=FOCUS_INK)),
            ("Vertical.TScrollbar", dict(background=control, troughcolor=surface,
                                         bordercolor=surface, lightcolor=control,
                                         darkcolor=control, arrowcolor=muted,
                                         gripcount=0, arrowsize=arrow)),
        ]
        maps = [
            ("TNotebook.Tab", dict(
                background=[("selected", surface), ("active", theme.mix(base, surface, 0.5))],
                foreground=[("selected", text), ("active", text)],
                lightcolor=[("selected", surface)])),
            ("TCombobox", dict(
                fieldbackground=[("disabled", surface), ("readonly", base)],
                foreground=[("disabled", theme.DISABLED[1]), ("readonly", text)],
                bordercolor=[("focus", FOCUS_INK), ("hover", muted)],
                arrowcolor=[("disabled", theme.DISABLED[1]), ("hover", text)],
                background=[("active", theme.mix(control, text, 0.08))],
                selectbackground=[("readonly", base)],
                selectforeground=[("readonly", text)])),
            ("Vertical.TScrollbar", dict(
                background=[("active", theme.mix(control, text, 0.12))])),
        ]
        for name, options in settings:
            try:
                style.configure(name, **options)
            except Exception as exc:
                events.debug("ttk Style Refused", f"{name}: {exc}", source=SOURCE,
                             exception=exc)
        for name, options in maps:
            try:
                style.map(name, **options)
            except Exception as exc:
                events.debug("ttk Style Map Refused", f"{name}: {exc}",
                             source=SOURCE, exception=exc)
        # A combobox's open list is a plain Tk listbox, styled by option.
        for option, value in (("*TCombobox*Listbox.background", base),
                              ("*TCombobox*Listbox.foreground", text),
                              ("*TCombobox*Listbox.selectBackground", control),
                              ("*TCombobox*Listbox.selectForeground", text),
                              ("*TCombobox*Listbox.font", _font())):
            try:
                self.root.option_add(option, value)
            except Exception:
                pass

    def _build_toolbar(self):
        """The strip above the tabs. Empty while Setup has a tab of its own;
        it carries the way back once Setup minimises.

        The frame is packed now and stays packed even while empty, because a
        frame packed after the notebook lands *below* it. The button is
        chrome, not an instrument control: a ghost, as the Web rail's Setup.
        """
        self._toolbar = tk.Frame(self.root, background=theme.BACKGROUND)
        self._toolbar.pack(side="top", fill="x")
        self._setup_press = _Press(self._toolbar, "Setup", self._on_setup_clicked,
                                   theme.BACKGROUND, ghost=True)
        self._setup_button = self._setup_press.widget

    def _build_stop_button(self):
        """The stop object, docked at the bottom of the window.

        The same object as the Web view's: a round signal-red disc reading
        `Stop`, `Clear` once latched, that breathes once when the latch
        closes. Bottom-docked on purpose — directly above the tab bar it was
        an easy accidental-click target when reaching for a tab — and packed
        before the notebook, so no panel can push it off the window. The
        line beside it says what a press will do, to every model.
        """
        self._stop_bar = tk.Frame(self.root, background=theme.BACKGROUND)
        self._stop_bar.pack(side="bottom", fill="x")
        tk.Frame(self._stop_bar, height=1, background=theme.RULE
                 ).pack(side="top", fill="x")
        self._stop = _Mushroom(self._stop_bar, self._on_stop_clicked,
                               background=theme.BACKGROUND, face_step=STEP_1,
                               floor=STOP_DIAMETER)
        self._stop_button = self._stop.canvas
        self._stop_button.pack(side="right", padx=(PAD, INSET), pady=GAP)
        self._stop_key = (STOP_KEY_NAME_AQUA if _windowing_system(self.root) == "aqua"
                          else STOP_KEY_NAME)
        self._stop_hint = tk.Label(self._stop_bar, text=self._hint(False), font=_font(),
                                   background=theme.BACKGROUND,
                                   foreground=theme.MUTED)
        self._stop_hint.pack(side="right", padx=(INSET, 0))
        self._stop.tooltip.text = self._hint(False)
        # The station line: which model lost which link (F3). Left of the
        # hint, ink, with a signal lamp; empty while every link is up.
        size = _lamp_px()
        self._station_mark = tk.Canvas(self._stop_bar, width=size, height=size,
                                       background=theme.BACKGROUND,
                                       highlightthickness=0)
        self._station_line = tk.Label(self._stop_bar, text="", font=_font(),
                                      anchor="w", justify="left",
                                      background=theme.BACKGROUND,
                                      foreground=theme.TEXT)
        self._station_mark.pack(side="left", padx=(INSET, SPACE[2]))
        self._station_line.pack(side="left", fill="x", expand=True)

    def _hint(self, is_latched):
        """What a press will do - and, unlatched, the key that does it from
        anywhere in the window (F9)."""
        return CLEAR_HINT if is_latched else f"{STOP_HINT} ({self._stop_key})"

    def _bind_stop_keys(self):
        """The stop from anywhere, focus wherever it is: an entry, a tab, a
        confirmation, the region picker. It only ever stops."""
        sequences = STOP_KEYS + (STOP_KEYS_AQUA if self._stop_key == STOP_KEY_NAME_AQUA
                                 else ())
        for sequence in sequences:
            try:
                self.root.bind_all(sequence, self._on_stop_key)
            except Exception as exc:
                events.debug("Stop Key Not Bound", f"{sequence}: {exc}",
                             source=SOURCE, exception=exc)

    def _on_stop_key(self, _event=None):
        events.debug("Stop Key", "the stop shortcut was pressed", source=SOURCE)
        if not self.controller.is_estopped:
            self.controller.estop_all()
        self._sync_stop_button()
        return "break"

    def _build_alert_band(self):
        """Errors that need acknowledging, listed by source, beside the stop
        and never over it (F1, HC-2). It replaces `messagebox.showerror`,
        which was application-modal: five queued errors made five dialogs,
        and the stop could not take a click while one was up."""
        self._band = tk.Frame(self.root, background=theme.BACKGROUND)
        size = _lamp_px()
        self._band_mark = tk.Canvas(self._band, width=size, height=size,
                                    background=theme.BACKGROUND, highlightthickness=0)
        self._band_mark.pack(side="left", anchor="n", padx=(INSET, SPACE[2]),
                             pady=SPACE[3])
        self._band_ack = _Press(self._band, "Acknowledge", self._acknowledge,
                                theme.BACKGROUND)
        self._band_ack.frame.pack(side="right", anchor="n", padx=(SPACE[3], INSET),
                                  pady=SPACE[1])
        self._band_text = tk.Label(self._band, text="", font=_font(), anchor="w",
                                   justify="left", wraplength=720,
                                   background=theme.BACKGROUND,
                                   foreground=theme.TEXT)
        self._band_text.pack(side="left", fill="x", expand=True, pady=SPACE[1])
        self._band.bind("<Configure>", self._on_band_resized)
        try:
            self._band_mark.create_oval(2, 2, size - 2, size - 2,
                                        fill=theme.SIGNAL, outline=theme.SIGNAL)
        except Exception:
            pass

    def _on_band_resized(self, event=None):
        width = getattr(event, "width", 0)
        if isinstance(width, int) and width > SPACE[6] * 10:
            try:
                self._band_text.configure(wraplength=width - SPACE[6] * 8)
            except Exception:
                pass

    @property
    def is_alert_shown(self):
        return bool(self._alerts)

    def _render_alerts(self):
        alerts = self._alerts
        if not alerts:
            try:
                self._band.pack_forget()
            except Exception:
                pass
            return
        word = SEVERITY_WORD["error"]
        if len(alerts) == 1:
            text = f"{word}: {alerts[0].text}"
        else:
            shown = [f"{word}: {event.text}" for event in alerts[-BAND_LINES:]]
            more = len(alerts) - len(shown)
            text = "\n".join([f"{len(alerts)} errors need acknowledgement."]
                             + (["..."] if more else []) + shown)
        try:
            self._band_text.configure(text=text)
            self._band.pack(side="bottom", fill="x", after=self._stop_bar)
        except Exception as exc:
            events.debug("Alert Band Failed", str(exc), source=SOURCE,
                         exception=exc)

    def _acknowledge(self):
        """One press clears every listed error; focus goes to the stop."""
        count, self._alerts = len(self._alerts), []
        events.debug("Alerts Acknowledged", f"{count} acknowledged", source=SOURCE)
        try:
            had_focus = self.root.focus_get() is self._band_ack.widget
        except Exception:
            had_focus = False
        self._render_alerts()
        if had_focus:
            try:
                self._stop_button.focus_set()
            except Exception:
                pass

    def _build_event_panel(self):
        """A footer, sized in lines and scrolled — not an expanding panel.

        `height` is in text lines and `expand` is False, so the log cannot
        take space from the controls as it fills. Two steps of ink and no
        more: the latest line in ink, the ones before it muted (never
        fainter than the muted token), warnings in the trace colour; an
        error is ink behind a signal mark and the word "Error".
        """
        frame = tk.Frame(self.root, background=theme.BACKGROUND)
        frame.pack(side="bottom", fill="x", padx=INSET, pady=(GAP, GAP))
        caption = tk.Label(frame, text="Events", font=_font(SMALL),
                           anchor="w", background=theme.BACKGROUND,
                           foreground=theme.MUTED)
        caption.pack(fill="x")
        body = tk.Frame(frame, background=theme.BACKGROUND)
        body.pack(fill="x")
        scrollbar = ttk.Scrollbar(body, orient="vertical")
        scrollbar.pack(side="right", fill="y")
        self._event_text = tk.Text(body, height=EVENT_LOG_LINES, state="disabled",
                                   wrap="word", relief="flat",
                                   font=_font(SMALL), padx=GAP, pady=GAP,
                                   highlightthickness=1,
                                   highlightbackground=theme.RULE,
                                   highlightcolor=theme.RULE,
                                   background=theme.BACKGROUND,
                                   foreground=theme.MUTED,
                                   yscrollcommand=scrollbar.set)
        self._event_text.pack(side="left", fill="x", expand=True)
        try:
            scrollbar.configure(command=self._event_text.yview)
        except Exception as exc:
            events.debug("Event Scrollbar Not Wired", str(exc), source=SOURCE,
                         exception=exc)
        # Creation order is tag priority: `latest` outranks `info` and is
        # outranked by `warning` and `error`, so the newest line is ink
        # unless its severity says otherwise. A line's text is its
        # severity's INK (an error is ink: signal text is 3.21:1 here); its
        # colour is the MARK before it, beside a severity word (F14).
        tags = [(severity, theme.SEVERITY_INK[severity]) for severity in ("info",)]
        tags += [("latest", theme.TEXT)]
        tags += [(severity, theme.SEVERITY_INK[severity])
                 for severity in ("warning", "error")]
        tags += [(f"mark-{severity}", theme.SEVERITY_MARK[severity])
                 for severity in ("info", "warning", "error")]
        for tag, foreground in tags:
            try:
                self._event_text.tag_configure(tag, foreground=foreground)
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
        self._stop.cancel()
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
        self._sync_station_line()
        self._schedule_refresh()

    def _sync_stop_button(self):
        """Face and ring follow `Controller.is_estopped`, so the control says
        what it will do rather than what it did; the disc breathes once on
        the edge where the latch closes. Nothing is redrawn or reconfigured
        while the latch holds still (F21)."""
        is_estopped = bool(self.controller.is_estopped)
        if self._stop.is_latched == is_estopped:
            return
        events.debug("Stop Button Changed",
                     CLEAR_FACE if is_estopped else STOP_FACE, source=SOURCE)
        self._stop.set_latched(is_estopped)
        hint = self._hint(is_estopped)
        self._stop.tooltip.text = hint
        try:
            self._stop_hint.configure(text=hint)
        except Exception as exc:
            events.debug("Stop Button Draw Failed", str(exc), source=SOURCE,
                         exception=exc, every=5.0)

    def _sync_station_line(self):
        """Name every model whose link is lost, and the device, beside the
        stop - the one line the operator's eye passes on the way to it."""
        lost = [(name, view.lost_devices) for name, view in self._panels.items()
                if getattr(view, "lost_devices", ())]
        text = "; ".join(f"{name}: {_device_list(devices)} connection lost"
                         for name, devices in lost)
        if text == self._station_text:
            return
        self._station_text = text
        size = _lamp_px()
        try:
            self._station_line.configure(text=text)
            self._station_mark.delete("all")
            if text:
                self._station_mark.create_oval(2, 2, size - 2, size - 2,
                                               fill=theme.SIGNAL, outline=theme.SIGNAL)
        except Exception as exc:
            events.debug("Station Line Failed", str(exc), source=SOURCE,
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
        try:
            # A model opened or reopened is brought forward: the Models menu
            # reopen used to change nothing on screen (AUD-13).
            self.notebook.select(frame)
        except Exception as exc:
            events.debug("Tab Not Selected", str(exc), source=SOURCE,
                         exception=exc)
        self._build_menu_bar()
        events.debug("Tab Opened", name, source=SOURCE)

    def _remove_panel(self, name):
        if name not in self._panels:
            return
        self._destroy_panel(name)
        self._build_menu_bar()
        events.debug("Tab Closed", name, source=SOURCE)
        if self._is_setup_collapsed and not any(
                other != self.SETUP_TAB for other in self._panels):
            # An empty notebook is a dead end. The next step from "no model
            # is open" is Setup, so Setup comes back rather than a blank page.
            self.restore_setup()

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
        view = self._panels.get(self.SETUP_TAB)
        if view is not None:
            view.pause()             # hidden: no tick until it is back (F21)
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
        view = self._panels.get(self.SETUP_TAB)
        if view is not None:
            view.resume()
        self._show_setup_button(False)
        self._build_menu_bar()
        return True

    def _show_setup_button(self, is_shown):
        """The toolbar button exists only while Setup has no tab, so the
        window never offers two ways to the same visible panel."""
        try:
            if is_shown:
                self._setup_press.frame.pack(side="left", padx=PAD, pady=GAP)
            else:
                self._setup_press.frame.pack_forget()
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
        """One line per event: a mark in the severity's colour, the severity
        word, then the text in the severity's ink (F14, UXPM-10)."""
        severity = event.severity if event.severity in theme.SEVERITY_ROLE else "info"
        try:
            self._event_text.configure(state="normal")
            self._event_text.tag_remove("latest", "1.0", "end")
            self._event_text.insert("end", "\u25cf ", (f"mark-{severity}",))
            self._event_text.insert("end", f"{SEVERITY_WORD[severity]}  {event.text}\n",
                                    (severity, "latest"))
            self._event_text.see("end")
            self._event_text.configure(state="disabled")
        except Exception as exc:
            events.debug("Event Draw Failed", str(exc), source=SOURCE,
                         exception=exc, every=5.0)

    def _show_popup(self, event):
        """An event that needs acknowledging joins the alert band. Not a
        modal: `Dashboard._on_event` decided it earned acknowledgement, not
        that it may take the stop away."""
        if self._closing:
            return
        events.debug("Alert Shown", f"{event.severity}/{event.title}", source=SOURCE)
        self._alerts.append(event)
        del self._alerts[:-50]
        self._render_alerts()

    def _confirm(self, prompt):
        return _confirm(self.root, prompt)

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
