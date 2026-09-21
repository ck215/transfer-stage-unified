"""Typed motion parameters (RC-6).

Every numeric field a view can edit is declared here: its type, its bounds,
how many decimals it renders with, and the value to fall back to when the
stored one cannot be parsed. Two things follow from having the declaration in
one place.

**Views stop guessing.** Both Tk and PySide decided whether a field was
numeric by calling `float()` on its *current value* and seeing whether it
raised. A field that happened to hold `""` — which is what an operator leaves
behind after clearing a box — was therefore classified as text, and silently
lost its validator for the rest of the session. The schema carries the type
now, so numeric-ness is a property of the parameter rather than an accident
of what is in the box.

**The fallback belongs to the class, not the call site.** `_num(self.
full_speed, 400)` hardcoded *BaseProbe's* 400 at every use. A `DCProbe`
declares 120, so an unparseable value sent the firmware more than three times
the speed that probe is configured for, with nothing on screen saying a
substitution had happened.

`coerce` is the lenient path, for building a frame from whatever is stored.
`parse` is the strict one, for accepting operator input: it refuses rather
than substitutes, and names the field it refused. A heater setpoint is the
case that makes the distinction matter — see RC-6 and `strtok`.
"""
import math


class Param:
    """One declared parameter. Immutable."""

    __slots__ = ("name", "label", "type", "default", "minimum", "maximum",
                 "decimals", "unit")

    def __init__(self, name, type="float", default=0, minimum=None,
                 maximum=None, decimals=3, unit="", label=None):
        if type not in ("int", "float", "text"):
            raise ValueError(f"{name}: unknown param type {type!r}")
        self.name = name
        self.label = label or name
        self.type = type
        self.default = default
        self.minimum = minimum
        self.maximum = maximum
        self.decimals = 0 if type == "int" else decimals
        self.unit = unit

    @property
    def is_numeric(self):
        return self.type in ("int", "float")

    def coerce(self, raw):
        """Lenient: fall back to this parameter's own default. Never raises.

        For building a frame out of whatever is currently stored. The result
        is always a usable number of the declared type.
        """
        if self.type == "text":
            return "" if raw is None else str(raw)
        try:
            value = float(raw)
            if math.isnan(value) or math.isinf(value):
                value = float(self.default)
        except (TypeError, ValueError):
            value = float(self.default)
        return self._clamp(value)

    def parse(self, raw):
        """Strict: `(ok, value_or_reason)`. Refuses rather than substitutes.

        For accepting operator input. A value that cannot be read is an error
        to report, not a number to invent — the whole RC-6 thesis in one
        method.
        """
        if self.type == "text":
            return True, "" if raw is None else str(raw)

        text = "" if raw is None else str(raw).strip()
        if not text:
            return False, f"{self.label} is empty"
        try:
            value = float(text)
        except (TypeError, ValueError):
            return False, f"{self.label} is not a number: {text!r}"
        if math.isnan(value) or math.isinf(value):
            return False, f"{self.label} is not a finite number: {text!r}"
        if self.minimum is not None and value < self.minimum:
            return False, f"{self.label} must be at least {self.minimum}"
        if self.maximum is not None and value > self.maximum:
            return False, f"{self.label} must be at most {self.maximum}"
        return True, int(value) if self.type == "int" else value

    def format(self, value):
        """Render for display, at this parameter's declared precision."""
        if self.type == "text":
            return "" if value is None else str(value)
        number = self.coerce(value)
        if self.type == "int":
            return str(int(number))
        return f"{number:.{self.decimals}f}"

    def to_schema(self):
        """The subset a renderer needs. Kept flat so all three views read it."""
        return {
            "param": self.name,
            "value_type": self.type,
            "min": self.minimum,
            "max": self.maximum,
            "decimals": self.decimals,
            "unit": self.unit,
        }

    def _clamp(self, value):
        if self.minimum is not None and value < self.minimum:
            value = self.minimum
        if self.maximum is not None and value > self.maximum:
            value = self.maximum
        return int(value) if self.type == "int" else value

    def __repr__(self):
        return f"Param({self.name!r}, {self.type!r}, default={self.default!r})"
