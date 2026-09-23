"""The result channel from a command to whatever asked for it (RC-8 item 1).

Before this, a command that refused returned `None` and a command that
succeeded returned `None`, so every view reported "executed" either way
(ERRORS-1, ROTATOR-3, WEB-12, REDPERCENT-7, TEMP-10). A refusal the operator
never sees is not a check.

Four outcomes, one type:

    Ok                 - it ran; `value` carries whatever it produced
    Refused(reason)    - a precondition said no; nothing was attempted
    Failed(exception)  - it was attempted and raised
    NeedsConfirmation  - it must not proceed unattended; ask, then re-issue

`bool(result)` is True only for `Ok`, so the many call sites that already
wrote `if model.execute_command(...)` keep their meaning. Testing the
outcome explicitly — `result.refused`, `result.failed` — is preferred in new
code, because `if not result` cannot tell a refusal from a crash and I-8.1
turns on that distinction.
"""


class CommandResult:
    """Base of the four-member result family. Never constructed directly."""

    status = None
    #: Operator-facing sentence. Empty for `Ok`.
    reason = ""
    #: What the command produced, when it produced anything.
    value = None
    #: The exception, for `Failed` only.
    exception = None

    @property
    def ok(self):
        return self.status == "ok"

    @property
    def refused(self):
        return self.status == "refused"

    @property
    def failed(self):
        return self.status == "failed"

    @property
    def needs_confirmation(self):
        return self.status == "needs_confirmation"

    def __bool__(self):
        return self.status == "ok"

    def __repr__(self):
        body = f" {self.reason!r}" if self.reason else ""
        return f"<{type(self).__name__}{body}>"


class Ok(CommandResult):
    status = "ok"

    def __init__(self, value=None):
        self.value = value


class Refused(CommandResult):
    """A precondition said no. **Nothing was attempted**, so there is nothing
    to undo — which is what separates this from `Failed` for the operator."""

    status = "refused"

    def __init__(self, reason):
        self.reason = reason


class Failed(CommandResult):
    """It was attempted and raised. The hardware may be in a partial state."""

    status = "failed"

    def __init__(self, exception, reason=""):
        self.exception = exception
        self.reason = reason or f"{type(exception).__name__}: {exception}"


class NeedsConfirmation(CommandResult):
    """Returned by a command that must not proceed unattended (S10 item 3).

    Each view implements one generic dialog for this, which replaces the
    `confirm_rotation_callback` the views used to *inject into the model* —
    a callback the Web client never supplied, so the ±30° tubing check simply
    did not exist there. A refusal the operator never sees is not a check.

    S11 folded this into the `CommandResult` family. It kept its constructor
    and its attribute names, so the S10 dialog code in all three views and
    the tests that pin it did not have to move.
    """

    status = "needs_confirmation"

    def __init__(self, prompt, command, inputs=None):
        self.prompt = prompt
        self.command = command
        self.inputs = inputs or {}
        self.reason = prompt

    def __repr__(self):
        return f"NeedsConfirmation({self.prompt!r}, command={self.command!r})"


def as_result(value):
    """Adapt a command's raw return into the family.

    Commands written before S11 return `None` for success and, in a few
    places, `False` for refusal. Both readings are preserved here so the
    stage did not have to rewrite every command body at once:

      * an existing `CommandResult` passes through,
      * `False` becomes a `Refused` with a generic reason,
      * anything else — including `None` — becomes `Ok` carrying it.

    A command that wants to *say why* returns `Refused` itself.
    """
    if isinstance(value, CommandResult):
        return value
    if value is False:
        return Refused("The command reported a refusal without a reason.")
    return Ok(value)
