"""Schema v2: one element contract, three renderers (RC-7).

A model describes its controls once and Tk, PySide and the Web client each
render that description. That was already the intent; what v2 adds is the
information the renderers were previously *inferring*, each in its own way.

**What the views had to guess, and now do not.**

* **Numeric-ness.** Both desktop views called `float()` on a field's current
  value and treated a raised exception as "this is text". An empty box was
  therefore text, and lost its validator. `value_type` is declared now.
* **Writability.** Nothing marked a control read-only, so the Web
  `/api/set_attr` route would accept a write to anything the schema
  mentioned — including the mode flags, which arms hardware (STEPPER-11,
  DC-11). `writable` defaults to **False**; a control has to ask.
* **Colour.** Elements carried `bg`/`fg` hex strings, which only Tk could
  honour, so the same control looked different in each frontend for no
  stated reason. `role` names the *meaning* — "danger", "go", "neutral" —
  and each renderer maps it to its own palette.
* **Availability.** Whether a control should be greyed out during an
  autonomous run was hardcoded per view, where it was hardcoded at all.
  `disabled_when` / `enabled_when` name modes.

**Commands carry their inputs (D-5).** A command element may declare
`inputs`: the parameters whose current widget values travel *with* the
command and are validated as a set before it runs. Without this a view sends
a command and the model reads whatever it happens to hold, which is one edit
behind whatever was just typed. Tk hid that by forcing focus away before
every command — a named anti-fix, because it only ever worked in Tk.
What a view sends (MOD-6, 2026-09-27): the command's declared `inputs`,
edited or not, plus every writable entry the operator has edited and not
committed. A clean entry the command does not declare stays home, so a
stale or bad box elsewhere cannot refuse an unrelated command.

Every builder here returns a plain dict. The schema stays serialisable, which
is what lets the Web client receive the same description the desktop views
render from.
"""

#: Element types a renderer must implement. A type outside this set is a
#: schema bug, caught by the conformance test rather than by a silent
#: fall-through in one view and a crash in another.
ELEMENT_TYPES = frozenset({
    "readonly", "entry", "button", "toggle", "checkbox", "dropdown",
    "region_select", "file_save", "file_open", "plot", "image", "indicator",
    "log_stream", "internal",
})

#: Semantic roles. Renderers map these to their own palettes.
ROLES = frozenset({"neutral", "go", "danger", "warning", "info"})


TIERS = (1, 2, 3)


def section(title, *elements, layout="column", tier=1, disclosure=None,
            phases=None, hosted_tier=None):
    """`layout="row"` asks the renderer to place the elements side by side
    on one line (a table row: label, dropdown, dropdown, status).

    `tier` is the section's prominence (owner ruling 2026-09-25): 1 is
    always drawn; 2 sits behind one disclosure per model (its text is
    `disclosure`, default "Configure"/"Details" from `theme.TIER_LABELS`);
    3 sits behind a second disclosure ("Diagnostics") inside tier 2. Every
    renderer honours tiers the same way; the Panel ignores them (a tier is
    where a control is drawn, never whether it may run).

    `phases` (owner ruling 2026-10-07, the interactive procedure): the
    procedure steps during which the section is DRAWN at all. Absent means
    always. A section hidden by phase takes its elements with it; the Panel
    refuses their commands (`shown_elements`), so a hidden control is not a
    reachable one. The same key on one element is set with `phased()`.
    """
    if tier not in TIERS:
        raise ValueError(f"section {title!r}: tier must be one of {TIERS}, not {tier!r}")
    if disclosure is not None and tier == 1:
        raise ValueError(f"section {title!r}: a tier-1 section has no disclosure")
    built = {"title": title, "layout": layout, "tier": tier,
             "elements": [e for e in elements if e is not None]}
    if disclosure:
        built["disclosure"] = disclosure
    if phases is not None:
        _phase_list(phases, f"section {title!r}")
        built["phases"] = list(phases)
    if hosted_tier is not None:
        # The tier this section takes when its model is drawn on its HOST's
        # page (owner ruling 2026-10-07: the analysis's live group leaves
        # the trial page's tier 1; the procedure strip replaces it). On the
        # model's own page `tier` still applies.
        if hosted_tier not in TIERS:
            raise ValueError(f"section {title!r}: hosted_tier must be one of {TIERS}")
        built["hosted_tier"] = hosted_tier
    return built


def phased(element, *phases):
    """Mark one built element as drawn only during `phases` (a procedure
    step name each). `phased(button(...), "live", "marked")`. A `stop=True`
    button is never phased: the stop is reachable in every step (the model
    contract test enforces it)."""
    if element.get("stop"):
        raise ValueError(f"{element.get('text')!r}: a stop control is never hidden by phase")
    _phase_list(phases, f"element {element.get('text')!r}")
    element["phases"] = list(phases)
    return element


def _phase_list(phases, where):
    if isinstance(phases, str) or not phases:
        raise ValueError(f"{where}: phases must be a non-empty sequence of step names")
    for p in phases:
        if not isinstance(p, str) or not p:
            raise ValueError(f"{where}: a phase is a non-empty string, not {p!r}")


def schema(*sections):
    return {"version": 2, "sections": list(sections)}


def readonly(text, model_attr, *, param=None, format=None, role="neutral",
             rail=False, unit=None, secondary=False):
    """A value the operator reads and cannot write. `rail=True` marks one of
    the few numbers the operator watches constantly: the opened model's
    focal readings. `unit` is drawn small beside the number (a `param`
    carries its own unit; this is for readouts without one). `secondary=True`
    draws the readout small, under the control declared just before it (the
    steps/s under a percent speed dial, owner ruling 2026-10-07)."""
    element = {
        "type": "readonly", "text": text, "model_attr": model_attr,
        "writable": False, "role": role,
    }
    if rail:
        element["rail"] = True
    if secondary:
        element["secondary"] = True
    if param is not None:
        element.update(param.to_schema())
    if format is not None:
        element["format"] = format
    if unit is not None:
        element["unit"] = unit
    return element


def entry(text, model_attr, param, *, enabled_when=None, disabled_when=None,
          slider=None):
    """An editable field, typed and bounded by its `param` declaration.

    `slider=(low, high)` asks every renderer to draw a range control BESIDE
    the entry (never instead of it: the entry keeps the precision, the
    slider is the common adjustment - owner ruling 2026-09-25). The range is
    the slider's travel only; the Param's own bounds still validate what is
    typed, so the wire is unchanged.
    """
    element = {
        "type": "entry", "text": text, "model_attr": model_attr,
        "writable": True, "role": "neutral",
    }
    element.update(param.to_schema())
    if slider is not None:
        low, high = slider
        if not (high > low):
            raise ValueError(f"entry {model_attr!r}: slider range {slider!r} is empty")
        element["slider"] = [low, high]
    return _gate(element, enabled_when, disabled_when)


def button(text, command, *, inputs=(), args=(), role="neutral", confirm=None,
           enabled_when=None, disabled_when=None, stop=False):
    """A command. `inputs` names the parameters whose values travel with it;
    `args` are fixed positional arguments the button always passes (two
    buttons can share one command: `move_by` with args (1,) and (-1,)).
    `stop=True` marks a command that takes hardware DOWN (halt, end a run):
    the Panel never refuses it over unrelated entry text (CON-4), whatever
    the command is called."""
    element = {
        "type": "button", "text": text, "command": command,
        "inputs": list(inputs), "args": list(args), "writable": False,
        "role": role,
    }
    if confirm:
        element["confirm"] = confirm
    if stop:
        element["stop"] = True
    return _gate(element, enabled_when, disabled_when)


def toggle(text, model_attr, command, true_text, false_text, *,
           on_args=(), off_args=(), on_role="go", off_role="neutral",
           enabled_when=None, disabled_when=None, tooltip=None,
           tooltip_on=None):
    """A two-state control. Never writable: the attribute is derived state and
    the command is the only way to change it.

    `on_args` / `off_args` are passed to `command` when switching ON / OFF, so
    one command (`set_mode`) serves several toggles. `on_role` / `off_role`
    say what each state MEANS; `theme.toggle_colors` turns that into colour in
    every view. A latched estop is `on_role="danger"` - not green because it
    happens to be "on".

    `tooltip` / `tooltip_on` are the control's accessible name and hover
    text while off / on, for when `text` alone would not say which control
    it is (every model's stop is labelled "Stop"; the tooltip names the
    model, F20).
    """
    element = {
        "type": "toggle", "text": text, "model_attr": model_attr,
        "command": command, "true_text": true_text, "false_text": false_text,
        "on_args": list(on_args), "off_args": list(off_args),
        "on_role": on_role, "off_role": off_role,
        "writable": False, "role": off_role,
    }
    if tooltip:
        element["tooltip"] = tooltip
    if tooltip_on:
        element["tooltip_on"] = tooltip_on
    return _gate(element, enabled_when, disabled_when)


def checkbox(text, model_attr, command, *, tooltip=None,
             enabled_when=None, disabled_when=None):
    """A tick box: one boolean the operator sets directly (G3).

    Unlike `toggle`, which is a command button whose face names a mode, a
    checkbox IS the value: ticked or not. `command` receives one argument,
    the new boolean, and `model_attr` is the boolean it reads back. Setup's
    per-row "Launch" box is the first one; its dropdowns are gated on it
    through `enabled_by`, so an unticked row is greyed out the way `main`'s
    device checkboxes greyed out their dropdowns.
    """
    element = {
        "type": "checkbox", "text": text, "model_attr": model_attr,
        "command": command, "writable": False, "role": "neutral",
    }
    if tooltip:
        element["tooltip"] = tooltip
    return _gate(element, enabled_when, disabled_when)


def dropdown(text, model_attr, command, options_command, *,
             enabled_when=None, disabled_when=None, enabled_by=None,
             enabled_by_reason=None):
    """A selection. `command` is **required**.

    `enabled_by` names a boolean `model_attr` (a checkbox's) that must be
    true for the control to be live; the one gating rule in `is_enabled`
    reads it, so the three views and the Panel agree. `enabled_by_reason`
    is the short sentence a view shows on the greyed control and the Panel
    refuses with ("Choose a sample first"); without it, the Setup row's
    "Tick Launch" words.

    A dropdown with `model_attr` and no `command` reached
    `getattr(self.model, None)` in PySide and raised `TypeError` (PYSIDE-7).
    Making the argument mandatory here is why that cannot recur: there is no
    way to express the broken shape.
    """
    if not command:
        raise ValueError(f"dropdown {model_attr!r} needs a command")
    element = {
        "type": "dropdown", "text": text, "model_attr": model_attr,
        "command": command, "options_command": options_command,
        "writable": False, "role": "neutral",
    }
    if enabled_by_reason:
        if not enabled_by:
            raise ValueError(f"dropdown {model_attr!r}: enabled_by_reason needs enabled_by")
        element["enabled_by_reason"] = str(enabled_by_reason)
    return _gate(element, enabled_when, disabled_when, enabled_by)


# -- composites: one contract, three renderers (S10 item 2) ----------------

def region_select(text, command, *, model_attr=None, data_command=None,
                  role="neutral"):
    """Pick a rectangular region of the screen. A view without its own
    overlay (the browser) draws on the image `data_command` supplies."""
    element = {
        "type": "region_select", "text": text, "command": command,
        "model_attr": model_attr, "writable": False, "role": role,
    }
    if data_command is not None:
        element["data_command"] = data_command
    return element


def file_save(text, command, *, extensions=("csv",), role="neutral"):
    """Choose a destination and save. The view supplies the file dialog."""
    return {
        "type": "file_save", "text": text, "command": command,
        "extensions": list(extensions), "writable": False, "role": role,
    }


def file_open(text, command, *, extensions=("csv",), role="neutral",
              placeholder=None):
    """Choose an existing file; its path is passed to `command`.
    `placeholder` is the path box's hint ("Path to a saved microscope
    image"); a view without it says its own default."""
    element = {
        "type": "file_open", "text": text, "command": command,
        "extensions": list(extensions), "writable": False, "role": role,
    }
    if placeholder:
        element["placeholder"] = str(placeholder)
    return element


def image(text, data_command, *, role="neutral", empty="No image yet.",
          model_attr=None):
    """A picture the model supplies as PNG bytes through `data_command`;
    empty bytes mean "nothing to show" and a view draws `empty` as one
    caption line instead of a full-size pane (L15).

    `model_attr` makes it a still (a preview): the attribute is a key that
    changes exactly when the picture does, and a view fetches the picture
    when the key changes instead of on every poll."""
    element = {
        "type": "image", "text": text, "data_command": data_command,
        "empty": str(empty), "writable": False, "role": role,
    }
    if model_attr:
        element["model_attr"] = model_attr
    return element


def indicator(text, model_attr, *, on_role="danger", off_role="neutral"):
    """A read-only lamp for a boolean: latched, faulted, connected."""
    return {
        "type": "indicator", "text": text, "model_attr": model_attr,
        "on_role": on_role, "off_role": off_role, "writable": False,
        "role": off_role,
    }


def plot(text, data_command, *, x_label="", y_label="", role="neutral",
         empty="No data yet."):
    """A live series the model supplies through `data_command`. `empty` is
    the one sentence a view draws while the series has no points (L22: the
    heater's plot used to borrow Red Percent's words in one view)."""
    return {
        "type": "plot", "text": text, "data_command": data_command,
        "x_label": x_label, "y_label": y_label, "empty": str(empty),
        "writable": False, "role": role,
    }


def log_stream(text, source_command, *, role="neutral", detached=False):
    """A scrolling text feed.

    `detached` (G4): the feed is not drawn in the panel. The renderer draws
    a button in its place that opens ONE non-modal window holding the feed;
    the window never covers the stop, Escape closes it, reopening raises it,
    and the feed's source command is polled only while it is open
    (`PanelView._wants_data`).
    """
    return {
        "type": "log_stream", "text": text, "source_command": source_command,
        "writable": False, "role": role, "detached": bool(detached),
    }


def _gate(element, enabled_when, disabled_when, enabled_by=None):
    if enabled_when:
        element["enabled_when"] = list(enabled_when)
    if disabled_when:
        element["disabled_when"] = list(disabled_when)
    if enabled_by:
        element["enabled_by"] = enabled_by
    return element


# -- what renderers ask, instead of deciding for themselves ---------------

def is_enabled(element, mode_name, values=None):
    """Should this control accept interaction in the model's current mode?

    One implementation, consulted by all three views, so "greyed out during a
    run" cannot mean three different things. `values` is the panel's current
    `state["values"]`; an element with `enabled_by` is live only while the
    named value is true. A caller without values skips that rule.
    """
    by = element.get("enabled_by")
    if by and values is not None and not values.get(by):
        return False
    disabled = element.get("disabled_when")
    if disabled and mode_name in disabled:
        return False
    enabled = element.get("enabled_when")
    if enabled and mode_name not in enabled:
        return False
    return True


def is_shown(item, phase):
    """Should this section or element be DRAWN during `phase`?

    Distinct from `is_enabled`: a disabled control is greyed where it stands,
    a hidden one is not there. An item without `phases` is always shown, so
    a model that declares no procedure (`PHASES == ()`, `phase == ""`)
    never hides anything. One implementation for the renderer and the
    Panel's allow-list, so "not on screen" and "not runnable" cannot drift.
    """
    wanted = item.get("phases")
    return not wanted or phase in wanted


def shown_elements(schema_dict, phase):
    """Every element drawn during `phase`, flattened, order preserved. A
    section hidden by phase hides every element in it."""
    for section_dict in schema_dict.get("sections", []):
        if not is_shown(section_dict, phase):
            continue
        for element in section_dict.get("elements", []):
            if is_shown(element, phase):
                yield element


def current_text(model, element):
    """The element's current value as display text — **absent stays absent**.

    `str(getattr(model, attr))` turns an unset `None` into the four-character
    string `"None"`, and the two desktop dropdown renderers then prepended
    that to their option list as a selectable entry. An operator saw a probe
    named "None" selected, and choosing it called `set_stepper_model("None")`,
    which matches nothing and returns silently. The Web client was the only
    one to get this right, with an empty placeholder — so the three renderers
    disagreed about what "nothing is selected" looks like, which is the RC-7
    shape schema v2 exists to remove.

    Returning "" for an absent value lets every renderer's existing
    `if current_val` guard do the right thing without repeating this rule.
    """
    raw = getattr(model, element.get("model_attr"), None)
    return "" if raw is None else str(raw)


def format_region(value):
    """A captured region as the operator reads it. One wording, three views.

    `region_select` has always declared `model_attr`, and no renderer showed
    it. PySide confirmed a capture with a modal `QMessageBox` instead —
    known-issues #9's fix for "the drag gave zero on-screen confirmation" —
    and that modal, raised over an always-on-top frameless overlay, is
    PYSIDE-12's deadlock and one of the two dialogs that hung the test suite.
    A value the schema already carries does not need a dialog to announce it;
    it needs a renderer that draws it.
    """
    if not value:
        return "not set"
    try:
        return (f"{value['width']}x{value['height']} at "
                f"({value['left']}, {value['top']})")
    except (KeyError, TypeError):
        return str(value)


def elements(schema_dict):
    """Every element in the schema, flattened. Order preserved."""
    for section_dict in schema_dict.get("sections", []):
        for element in section_dict.get("elements", []):
            yield element
