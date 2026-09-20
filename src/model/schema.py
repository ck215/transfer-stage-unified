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

Every builder here returns a plain dict. The schema stays serialisable, which
is what lets the Web client receive the same description the desktop views
render from.
"""

#: Element types a renderer must implement. A type outside this set is a
#: schema bug, caught by the conformance test rather than by a silent
#: fall-through in one view and a crash in another.
ELEMENT_TYPES = frozenset({
    "readonly", "entry", "button", "toggle", "dropdown",
    "region_select", "file_save", "plot", "log_stream", "internal",
})

#: Semantic roles. Renderers map these to their own palettes.
ROLES = frozenset({"neutral", "go", "danger", "warning", "info"})


def section(title, *elements):
    return {"title": title, "elements": [e for e in elements if e is not None]}


def schema(*sections):
    return {"version": 2, "sections": list(sections)}


def readonly(text, model_attr, *, param=None, format=None, role="neutral"):
    """A value the operator reads and cannot write."""
    element = {
        "type": "readonly", "text": text, "model_attr": model_attr,
        "writable": False, "role": role,
    }
    if param is not None:
        element.update(param.to_schema())
    if format is not None:
        element["format"] = format
    return element


def entry(text, model_attr, param, *, enabled_when=None, disabled_when=None):
    """An editable field, typed and bounded by its `param` declaration."""
    element = {
        "type": "entry", "text": text, "model_attr": model_attr,
        "writable": True, "role": "neutral",
    }
    element.update(param.to_schema())
    return _gate(element, enabled_when, disabled_when)


def button(text, command, *, inputs=(), role="neutral", confirm=None,
           enabled_when=None, disabled_when=None):
    """A command. `inputs` names the parameters whose values travel with it."""
    element = {
        "type": "button", "text": text, "command": command,
        "inputs": list(inputs), "writable": False, "role": role,
    }
    if confirm:
        element["confirm"] = confirm
    return _gate(element, enabled_when, disabled_when)


def toggle(text, model_attr, command, true_text, false_text, *,
           role="neutral", enabled_when=None, disabled_when=None):
    """A two-state control.

    `writable` is False and stays False: the *attribute* is a derived view of
    the model's state, and the only way to change it is the command. This is
    invariant I-3.4 expressed in the schema — a toggle that published itself
    as writable is how `/api/set_attr` came to be able to arm manual mode.
    """
    element = {
        "type": "toggle", "text": text, "model_attr": model_attr,
        "command": command, "true_text": true_text, "false_text": false_text,
        "writable": False, "role": role,
    }
    return _gate(element, enabled_when, disabled_when)


def dropdown(text, model_attr, command, options_command, *,
             enabled_when=None, disabled_when=None):
    """A selection. `command` is **required**.

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
    return _gate(element, enabled_when, disabled_when)


# -- composites: one contract, three renderers (S10 item 2) ----------------

def region_select(text, command, *, model_attr=None, role="neutral"):
    """Pick a rectangular region of the screen or image."""
    return {
        "type": "region_select", "text": text, "command": command,
        "model_attr": model_attr, "writable": False, "role": role,
    }


def file_save(text, command, *, extensions=("csv",), role="neutral"):
    """Choose a destination and save. The view supplies the file dialog."""
    return {
        "type": "file_save", "text": text, "command": command,
        "extensions": list(extensions), "writable": False, "role": role,
    }


def plot(text, data_command, *, x_label="", y_label="", role="neutral"):
    """A live series the model supplies through `data_command`."""
    return {
        "type": "plot", "text": text, "data_command": data_command,
        "x_label": x_label, "y_label": y_label,
        "writable": False, "role": role,
    }


def log_stream(text, source_command, *, role="neutral"):
    """A scrolling text feed."""
    return {
        "type": "log_stream", "text": text, "source_command": source_command,
        "writable": False, "role": role,
    }


def _gate(element, enabled_when, disabled_when):
    if enabled_when:
        element["enabled_when"] = list(enabled_when)
    if disabled_when:
        element["disabled_when"] = list(disabled_when)
    return element


# -- what renderers ask, instead of deciding for themselves ---------------

def is_enabled(element, mode_name):
    """Should this control accept interaction in the model's current mode?

    One implementation, consulted by all three views, so "greyed out during a
    run" cannot mean three different things.
    """
    disabled = element.get("disabled_when")
    if disabled and mode_name in disabled:
        return False
    enabled = element.get("enabled_when")
    if enabled and mode_name not in enabled:
        return False
    return True


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


class NeedsConfirmation:
    """Returned by a command that must not proceed unattended (S10 item 3).

    Each view implements one generic dialog for this, which replaces the
    `confirm_rotation_callback` the views used to *inject into the model* —
    a callback the Web client never supplied, so the ±30° tubing check simply
    did not exist there. A refusal the operator never sees is not a check.
    """

    def __init__(self, prompt, command, inputs=None):
        self.prompt = prompt
        self.command = command
        self.inputs = inputs or {}

    def __repr__(self):
        return f"NeedsConfirmation({self.prompt!r}, command={self.command!r})"
