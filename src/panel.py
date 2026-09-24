"""Anything a view can render: a Param table, a schema, a state snapshot, run().

Base of `Model` and `Setup`. There is no other way for a view to reach a
model than `schema`, `state` and `run` (through the Controller), which is why
the three views cannot drift apart.
"""
import time

import schema as sch
from events import events
from result import Result, Refused, NeedsConfirm


class Panel:
    NAME = "Panel"
    _QUIET = frozenset({"_commit"})   # plus every data/source command, see run()
    PARAMS = {}     # {name: Param}; subclasses: PARAMS = {**Base.PARAMS, ...}

    def __init__(self):
        for name, value in self._defaults().items():
            setattr(self, name, value)

    # -- what a view reads -------------------------------------------------
    @property
    def schema(self):
        """The `schema.schema(...)` dict describing this panel's controls."""
        raise NotImplementedError

    @property
    def mode_name(self):
        """This panel's own mode: what it is doing."""
        return ""

    #: Gate tokens that are not a mode of their own, with the sentence a
    #: refusal gives for them. A view sees only the token (`state["mode"]`).
    GATE_REASONS = {
        "latched": "the stop is set on {name}. Clear the stop first.",
        "no_region": "no capture region is set. Set one first.",
    }

    @property
    def gate_mode(self):
        """The token `enabled_when` / `disabled_when` are matched against, and
        what a view reads as `state["mode"]`. The mode, unless a condition
        that outranks it (the stop latch) is in force."""
        return self.mode_name

    def _is_enabled(self, element):
        """Enabled under the gate token AND the underlying mode, so a token
        that outranks the mode never loosens what the mode alone refused."""
        return (sch.is_enabled(element, self.gate_mode)
                and sch.is_enabled(element, self.mode_name))

    def _gate_reason(self):
        token = self.gate_mode
        if token in self.GATE_REASONS:
            return self.GATE_REASONS[token].format(name=self.NAME)
        return f"{self.NAME} is {token or 'in this state'}."

    @property
    def state(self):
        """One snapshot for views: every schema `model_attr` as display text,
        plus the mode. Subclasses extend it; they never replace it."""
        values = {}
        for element in sch.elements(self.schema):
            attr = element.get("model_attr")
            if attr:
                values[attr] = self._text_for(element)
        return {"name": self.NAME, "mode": self.gate_mode,
                "model_mode": self.mode_name, "values": values}

    def _text_for(self, element):
        raw = getattr(self, element["model_attr"], None)
        param = self.PARAMS.get(element["model_attr"])
        if raw is None:
            return ""
        if isinstance(raw, bool) or param is None:
            return raw if isinstance(raw, bool) else (
                sch.format_region(raw) if element["type"] == "region_select" else str(raw))
        return param.format(raw)

    # -- what a view does --------------------------------------------------
    def run(self, command, inputs=None, args=()):
        """Validate `inputs` as a set, then run a schema-declared command.

        Always returns a `Result`. The command body returns a value or raises
        `Refused` / `NeedsConfirm`; anything else it raises becomes `failed`
        and is the one case that produces an acknowledged popup.
        """
        source = self.NAME
        try:
            self._allows(command, args)
            self._apply_inputs(inputs)
            started = time.monotonic()
            found = getattr(self, command)
            # A data source (`series`, `figure`, `log`) may be a property.
            value = found(*args) if callable(found) else found
            if command not in self._QUIET and not self._is_data_command(command):
                events.debug("Command", f"{command}{tuple(args)} ok in "
                             f"{(time.monotonic() - started) * 1000:.1f} ms "
                             f"inputs={inputs or {}}", source=source)
            return Result(Result.OK, value=value)
        except Refused as refusal:
            events.info("Refused", refusal.reason, source=source)
            return Result(Result.REFUSED, reason=refusal.reason)
        except NeedsConfirm as ask:
            return Result(Result.CONFIRM, reason=ask.prompt, command=ask.command,
                          inputs=ask.inputs, args=ask.rerun_args)
        except Exception as exc:
            # F19: the command name, the exception and any bytes it quotes go
            # to the file log only; the view gets a sentence.
            events.debug("Command Failed", f"{command}{tuple(args)} failed: "
                         f"{exc!r}", source=source, exception=exc)
            reason = (f"{self._label_of(command)} did not complete. Check that "
                      f"the {source} is connected, then try again; the "
                      "details are in the log file.")
            events.error("Command Failed", reason, source=source,
                         exception=exc)
            return Result(Result.FAILED, reason=reason, exception=exc)

    def _label_of(self, command):
        """The operator's name for a command: its control's text."""
        try:
            for element in sch.elements(self.schema):
                if element.get("command") == command and element.get("text"):
                    return str(element["text"]).rstrip(": ")
        except Exception:
            pass
        return "That command"

    def _is_data_command(self, command):
        """Plot, image and log sources are polled every refresh: never logged."""
        return any(command in (e.get("data_command"), e.get("source_command"))
                   for e in sch.elements(self.schema))

    def set_value(self, attr, value):
        """Commit one writable entry outside a command. Same validation."""
        return self.run("_commit", inputs={attr: value})

    def _commit(self):
        return None

    def options(self, command):
        """Choices for a dropdown. `command` must be a declared options_command."""
        declared = {e.get("options_command") for e in sch.elements(self.schema)}
        declared.discard(None)
        if command not in declared:
            raise Refused(f"{command} is not an options source of {self.NAME}")
        found = getattr(self, command)
        return list(found() if callable(found) else found)

    # -- enforcement, written once ----------------------------------------
    def _allows(self, command, args=()):
        """Refuse a command the schema does not declare, or that the current
        mode gates off. This is the allow-list for every view, the Web API
        included, so a view cannot call what the schema does not show."""
        if command == "_commit":
            return
        matches = [e for e in sch.elements(self.schema)
                   if command in (e.get("command"), e.get("data_command"),
                                  e.get("source_command"))]
        if not matches:
            raise Refused(f"{command} is not a command of {self.NAME}")
        wanted = list(args)
        candidates = [e for e in matches
                      if wanted and wanted in (e.get("on_args"), e.get("off_args"))]
        candidates = candidates or matches
        # Two toggles may share a command and its off_args (Autonomous and
        # Manual both leave through set_mode("idle")): the command is allowed
        # if ANY declaring element is enabled in this mode.
        if not any(self._is_enabled(e) for e in candidates):
            element = candidates[0]
            label = str(element.get("text", command)).rstrip(":")
            raise Refused(f"{label} is not available: {self._gate_reason()}")

    def _apply_inputs(self, inputs):
        """All or nothing. Refusal names the field."""
        if not inputs:
            return
        writable = {e["model_attr"]: e for e in sch.elements(self.schema)
                    if e.get("writable") and e.get("model_attr")}
        parsed = {}
        for name, raw in inputs.items():
            param = self.PARAMS.get(name)
            if param is None or name not in writable:
                raise Refused(f"{name} is not an editable field of {self.NAME}")
            ok, value = param.parse(raw)
            if not ok:
                raise Refused(value)
            if not self._is_enabled(writable[name]):
                if self._same_value(getattr(self, name, None), value):
                    continue  # unchanged value of a gated field: not an edit
                raise Refused(f"{param.label or name} cannot be changed in "
                              f"{self.mode_name} mode. Leave {self.mode_name} "
                              "mode to edit it.")
            parsed[name] = value
        for name, value in parsed.items():
            setattr(self, name, value)

    @staticmethod
    def _same_value(current, new):
        """Parsed values, not display text: "5" and 5.000 are the same edit."""
        try:
            return abs(float(current) - float(new)) < 1e-9
        except (TypeError, ValueError):
            return current == new

    def _defaults(self):
        """A Param that is also a read-only property (a derived readout such
        as `current_red`) is declared for its type and unit only; never seed it.

        A property **with a setter** is a stored parameter behind a gate (the
        probe's motion fields) and is seeded like any other. Skipping it left
        the store empty, so the model read `''` while every view showed the
        default, and the first re-send of that default in autonomous mode
        looked like an edit and was refused (Tier F item 1)."""
        seeded = {}
        for name, p in self.PARAMS.items():
            found = getattr(type(self), name, None)
            if isinstance(found, property) and found.fset is None:
                continue
            seeded[name] = p.default
        return seeded

    def _param(self, name):
        return self.PARAMS[name]
