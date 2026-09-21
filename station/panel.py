"""Anything a view can render: a Param table, a schema, a state snapshot, run().

Base of `Model` and `Setup`. There is no other way for a view to reach a
model than `schema`, `state` and `run` (through the Controller), which is why
the three views cannot drift apart.
"""
from station import schema as sch
from station.events import events
from station.result import Result, Refused, NeedsConfirm


class Panel:
    NAME = "Panel"
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
            return Result(Result.OK, value=getattr(self, command)(*args))
        except Refused as refusal:
            events.info("Refused", refusal.reason, source=source)
            return Result(Result.REFUSED, reason=refusal.reason)
        except NeedsConfirm as ask:
            return Result(Result.CONFIRM, reason=ask.prompt, command=ask.command,
                          inputs=ask.inputs, args=ask.args)
        except Exception as exc:
            events.error("Command Failed", f"{command} failed: {exc}",
                         source=source, exception=exc)
            return Result(Result.FAILED, reason=f"{command} failed: {exc}",
                          exception=exc)

    def set_value(self, attr, value):
        """Commit one writable entry outside a command. Same validation."""
        return self.run("_commit", inputs={attr: value})

    def _commit(self):
        return None

    def options(self, command):
        """Choices for a dropdown. `command` must be a declared options_command."""
        declared = {e.get("options_command") for e in sch.elements(self.schema)}
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
        element = next((e for e in matches
                        if wanted and wanted in (e.get("on_args"), e.get("off_args"))),
                       matches[0])
        if not sch.is_enabled(element, self.mode_name):
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
            if not sch.is_enabled(writable[name], self.mode_name):
                if param.format(getattr(self, name, None)) == str(raw):
                    continue  # unchanged value of a gated field: not an edit
                raise Refused(f"{param.label or name} cannot be changed "
                              f"while {self.mode_name}")
            ok, value = param.parse(raw)
            if not ok:
                raise Refused(value)
            parsed[name] = value
        for name, value in parsed.items():
            setattr(self, name, value)

    def _defaults(self):
        return {name: p.default for name, p in self.PARAMS.items()}

    def _param(self, name):
        return self.PARAMS[name]
