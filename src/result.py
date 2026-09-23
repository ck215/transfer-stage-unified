"""What a command produced. Built only by `Panel.run`.

A command body returns a value (success) or raises `Refused` /
`NeedsConfirm`. It never returns a status, so a refusal cannot be dropped on
the floor and reported as success.
"""


class Refused(Exception):
    """A precondition said no. Nothing was attempted."""

    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


class NeedsConfirm(Exception):
    """The command must not proceed unattended. The view asks the operator,
    then re-runs `command` with `confirmed=True`."""

    def __init__(self, prompt, command, inputs=None, args=()):
        super().__init__(prompt)
        self.prompt = prompt
        self.command = command
        self.inputs = inputs or {}
        self.rerun_args = tuple(args)   # re-run as command(*rerun_args, True)


class Result:
    OK, REFUSED, FAILED, CONFIRM = "ok", "refused", "failed", "needs_confirm"

    def __init__(self, status, value=None, reason="", command="", inputs=None,
                 exception=None, args=()):
        self.status = status
        self.value = value
        self.reason = reason          # operator-facing sentence; "" for ok
        self.command = command        # for needs_confirm: what to re-run
        self.inputs = inputs or {}
        self.args = tuple(args)       # for needs_confirm: re-run with (*args, True)
        self.exception = exception

    @property
    def is_ok(self):
        return self.status == self.OK

    @property
    def is_refused(self):
        return self.status == self.REFUSED

    @property
    def is_failed(self):
        return self.status == self.FAILED

    @property
    def needs_confirm(self):
        return self.status == self.CONFIRM

    def __bool__(self):
        return self.is_ok

    def __repr__(self):
        return f"<Result {self.status}{' ' + repr(self.reason) if self.reason else ''}>"

    def to_dict(self):
        return {"status": self.status, "reason": self.reason,
                "command": self.command, "value": _plain(self.value),
                "inputs": dict(self.inputs), "args": list(self.args)}


def _plain(value):
    return value if isinstance(value, (str, int, float, bool, list, dict, type(None))) else None
