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
        """The name `enabled_when` / `disabled_when` are matched against."""
        return ""

    @property
    def state(self):
        """One snapshot for views: every schema `model_attr` as display text,
        plus the mode. Subclasses extend it; they never replace it."""
        values = {}
        for element in sch.elements(self.schema):
            attr = element.get("model_attr")
            if attr:
                values[attr] = self._text_for(element)
        return {"name": self.NAME, "mode": self.mode_name, "values": values}

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
            events.error("Command Failed", f"{command} failed: {exc}",
                         source=source, exception=exc)
            return Result(Result.FAILED, reason=f"{command} failed: {exc}",
                          exception=exc)

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
        if not any(sch.is_enabled(e, self.mode_name) for e in candidates):
            element = candidates[0]
            raise Refused(f"{element.get('text', command)} is not available "
                          f"while {self.mode_name or 'in this state'}")

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
            if not sch.is_enabled(writable[name], self.mode_name):
                if self._same_value(getattr(self, name, None), value):
                    continue  # unchanged value of a gated field: not an edit
                raise Refused(f"{param.label or name} cannot be changed "
                              f"while {self.mode_name}")
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
        as `current_red`) is declared for its type and unit only; never seed it."""
        return {name: p.default for name, p in self.PARAMS.items()
                if not isinstance(getattr(type(self), name, None), property)}

    def _param(self, name):
        return self.PARAMS[name]
