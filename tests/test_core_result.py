"""`result` — the channel that replaced "return False and hope".

Ports the intent of `tests/edge_cases/test_edge_mvc_error_router.py` and the
`CommandResult` / `NeedsConfirmation` half of `tests/core/test_system_manager.py`:
a refusal must be a value the caller cannot mistake for success, and a
confirmation must carry enough to re-run the command it interrupted.
"""
import pytest

from result import NeedsConfirm, Refused, Result, _plain

pytestmark = pytest.mark.errors


# -- Refused ---------------------------------------------------------------

def test_refused_carries_its_reason_as_an_attribute_and_as_str():
    refusal = Refused("the lid is open")
    assert refusal.reason == "the lid is open"
    assert str(refusal) == "the lid is open"


def test_refused_is_an_exception_so_it_cannot_be_dropped_on_the_floor():
    assert issubclass(Refused, Exception)
    with pytest.raises(Refused):
        raise Refused("no")


# -- NeedsConfirm ----------------------------------------------------------

def test_needs_confirm_carries_the_prompt_command_inputs_and_args():
    ask = NeedsConfirm("Really?", "go", inputs={"speed": "3"}, args=["north", 2])
    assert (ask.prompt, ask.command) == ("Really?", "go")
    assert ask.inputs == {"speed": "3"}
    assert ask.rerun_args == ("north", 2), "rerun_args must be a tuple, ready for (*rerun_args, True)"


def test_needs_confirm_defaults_are_empty_not_none():
    ask = NeedsConfirm("Really?", "go")
    assert ask.inputs == {} and ask.rerun_args == ()


def test_str_of_a_needs_confirm_is_the_prompt():
    """`Refused` gets this right; `NeedsConfirm` does not.

    `Model.clear_estop` raises `NeedsConfirm(prompt, "clear_estop")` with no
    args, so today `str()` of the only confirmation the core itself raises is
    the empty string. Anything that logs a caught exception by interpolation —
    and `Panel.run`, `Controller._estop_concurrently` and `EventLog.debug` all
    do for other exception types — would print nothing.
    """
    assert str(NeedsConfirm("Really?", "go", args=["north", 2])) == "Really?"
    assert str(NeedsConfirm("Release the latch?", "clear_estop")) == \
        "Release the latch?"


# -- Result ----------------------------------------------------------------

def test_the_four_statuses_are_mutually_exclusive():
    for status, flag in ((Result.OK, "is_ok"), (Result.REFUSED, "is_refused"),
                         (Result.FAILED, "is_failed"), (Result.CONFIRM, "needs_confirm")):
        result = Result(status)
        flags = {name: getattr(result, name) for name in
                 ("is_ok", "is_refused", "is_failed", "needs_confirm")}
        assert flags[flag] is True
        assert sum(flags.values()) == 1, f"{status} answered to {flags}"


def test_only_ok_is_truthy():
    assert bool(Result(Result.OK))
    assert not bool(Result(Result.REFUSED))
    assert not bool(Result(Result.FAILED))
    assert not bool(Result(Result.CONFIRM)), (
        "a needs_confirm result that reads as success is the old "
        "'returned None and the caller carried on' defect")


def test_a_refusal_keeps_the_operator_facing_reason():
    result = Result(Result.REFUSED, reason="Speed must be at least 0")
    assert result.reason == "Speed must be at least 0"
    assert "refused" in repr(result) and "Speed" in repr(result)


def test_ok_carries_the_commands_return_value():
    assert Result(Result.OK, value=[1, 2]).value == [1, 2]


def test_args_are_normalised_to_a_tuple():
    assert Result(Result.CONFIRM, args=["a"]).args == ("a",)


def test_inputs_default_to_an_empty_dict_not_none():
    assert Result(Result.OK).inputs == {}


def test_to_dict_is_json_safe_and_drops_an_unserialisable_value():
    class Opaque:
        pass

    payload = Result(Result.OK, value=Opaque(), command="go").to_dict()
    assert payload == {"status": "ok", "reason": "", "command": "go", "value": None,
                       "inputs": {}, "args": []}


@pytest.mark.parametrize("value", ["s", 1, 1.5, True, [1], {"a": 1}, None])
def test_plain_passes_everything_the_web_client_can_receive(value):
    assert _plain(value) == value


def test_to_dict_keeps_a_refusal_reason_for_the_web_client():
    payload = Result(Result.REFUSED, reason="nope").to_dict()
    assert payload["status"] == "refused" and payload["reason"] == "nope"
