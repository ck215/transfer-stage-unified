"""A board's ordinary text is not a garbled packet.

`Probe._read_position` used to count every line that was not a whole POS line
as dropped, so a boot banner or a firmware log line raised a false "Check the
cable" warning and its words were lost. The XYZ Stage's axis link already draws
the line (`teensy_axis._dispatch`): printable text is logged ("Board Says"),
only garble is dropped. All three probes share the one read path.
"""
import pytest

from events import events
from model.probe import ChuckPositioner, DCProbe, StepperProbe
from test_probe import Collected, make_probe

PROBES = [StepperProbe, DCProbe, ChuckPositioner]


@pytest.fixture(params=PROBES, ids=lambda cls: cls.__name__)
def any_probe(request):
    probe, port, gamepad = make_probe(request.param)
    yield probe
    probe._stop_threads()


@pytest.fixture(autouse=True)
def debug_lines(monkeypatch):
    """`events.debug` is file-only, so subscribers never see it: record it."""
    calls = []

    def record(title, message, *, source="", **_):
        calls.append((title, message, source))

    monkeypatch.setattr(events, "debug", record)
    return calls


def _says(calls):
    return [c for c in calls if c[0] == "Board Says"]


def _warnings(seen):
    return [e for e in seen.of("warning") if e.title == "Packets Dropped"]


@pytest.mark.transport
def test_a_banner_line_is_logged_not_dropped_and_does_not_warn(any_probe, debug_lines):
    any_probe.port.lines = ["Teensy 4.1 boot v2.3", "POS:1,2,3"]
    with Collected() as seen:
        assert any_probe._read_position() == (1, 2, 3)
    assert any_probe.dropped == 0
    assert _warnings(seen) == []
    assert _says(debug_lines) == [("Board Says", "Teensy 4.1 boot v2.3",
                                   any_probe.NAME)]


@pytest.mark.transport
def test_board_text_is_capped_at_200_characters(any_probe, debug_lines):
    any_probe.port.lines = ["x" * 500]
    with Collected() as seen:
        any_probe._read_position()
    assert [c[1] for c in _says(debug_lines)] == ["x" * 200]
    assert any_probe.dropped == 0


@pytest.mark.transport
@pytest.mark.parametrize("line", ["POS:1,2", "POS:bad,,", "POS:1,2,3,4",
                                  "POS:1,2,x"])
def test_a_malformed_position_line_is_still_dropped_and_warns(any_probe, debug_lines, line):
    any_probe.port.lines = [line]
    with Collected() as seen:
        assert any_probe._read_position() is None
    assert any_probe.dropped == 1
    assert len(_warnings(seen)) == 1
    assert _says(debug_lines) == []


@pytest.mark.transport
@pytest.mark.parametrize("line", ["boot\x00ok", "POS:1,2,3\x00", "ab\xffcd",
                                  b"ab\xffcd", "tab\there"])
def test_a_line_with_unprintable_bytes_is_still_dropped(any_probe, debug_lines, line):
    any_probe.port.lines = [line]
    with Collected() as seen:
        any_probe._read_position()
    assert any_probe.dropped == 1
    assert _says(debug_lines) == []
    assert len(_warnings(seen)) == 1


@pytest.mark.transport
def test_the_handshake_answer_is_neither_said_nor_dropped(any_probe,
                                                           debug_lines):
    any_probe.port.lines = ["DEV: stepper"]
    with Collected() as seen:
        any_probe._read_position()
    assert any_probe.dropped == 0
    assert _says(debug_lines) == []
