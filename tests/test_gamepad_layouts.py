"""The LAYOUTS table: four pads, every platform branch, one reader.

Ported from `tests/hardware/test_gamepad.py`, which asserted the same values
against five classes. The classes are gone; the numbers are not. Nothing here
imports pygame or touches a device: a layout is read out of this object's own
caches, which is exactly why it can be tested with no hardware.

GAMEPAD-11, -12 and -14 are open bench questions; GAMEPAD-13 (the D-pad
sign) was settled by the owner on the stage 2026-10-13. These tests pin *today's* values so
the rebuild cannot drift while they are open; they are not evidence that the
values are right. See the comment block above LAYOUTS.
"""
import contextlib
from unittest.mock import patch

import pytest

from devices.gamepad import LAYOUTS, NEUTRAL, Gamepad, GamepadHub


@contextlib.contextmanager
def platform_as(name):
    """Run the body as if on `name`, whatever the host actually is."""
    with patch("devices.gamepad.sys.platform", name):
        yield


class StubJoystick:
    """The four calls a layout lookup makes. No SDL, no device."""

    def __init__(self, name, numaxes=6, numbuttons=10, numhats=1,
                 guid="030000005e04"):
        self._name = name
        self._numaxes, self._numbuttons, self._numhats = numaxes, numbuttons, numhats
        self._guid = guid

    def get_name(self):
        return self._name

    def get_numaxes(self):
        return self._numaxes

    def get_numbuttons(self):
        return self._numbuttons

    def get_numhats(self):
        return self._numhats

    def get_guid(self):
        return self._guid


def layout_by_id(layout_id):
    for layout in LAYOUTS:
        if layout["id"] == layout_id:
            return layout
    raise AssertionError(f"no layout row {layout_id!r}")


def bound(layout_id, *, mode="default", platform="darwin", numaxes=6,
          numbuttons=10, numhats=1, axes=None, buttons=None, hats=None):
    """A Gamepad with a layout resolved and its caches primed, no hardware."""
    pad = Gamepad("TestProbe", hub=GamepadHub())
    layout = layout_by_id(layout_id)
    with platform_as(platform):
        spec = Gamepad._spec_for(layout, mode)
    pad._spec = spec
    pad._override = layout["override"]
    pad._layout_id = layout_id
    pad._prime_caches(StubJoystick("stub", numaxes, numbuttons, numhats), spec)
    pad._axis_values.update(axes or {})
    pad._button_values.update(buttons or {})
    pad._hat_values.update(hats or {})
    return pad


# ----------------------------------------------------------------------
# Xbox, wired
# ----------------------------------------------------------------------

def test_xbox_layout_off_linux_reads_axes_zero_three_four_five():
    pad = bound("xbox", platform="darwin",
                axes={0: 0.8, 3: -0.5, 4: -0.5},
                buttons={4: 1}, hats={0: (1, -1)})
    state = pad._read_layout()
    assert state["axis_x"] == 0.8
    assert state["axis_y"] == -0.5      # axis 3 is present, so axis 3
    assert state["trigger_left"] == -0.5     # LT is axis 4 off Linux
    assert state["bumper_left"] == 1
    assert state["hat_x"] == -1          # negated: GAMEPAD-13
    assert state["hat_y"] == 1


def test_xbox_layout_on_linux_moves_the_trigger_and_stick_axes():
    pad = bound("xbox", platform="linux",
                axes={0: 0.8, 4: -0.5, 2: 0.3, 5: 0.9}, buttons={5: 1})
    state = pad._read_layout()
    assert state["axis_x"] == 0.8
    assert state["axis_y"] == -0.5      # Linux xpad: right stick Y is 4
    assert state["trigger_left"] == 0.3      # LT is axis 2 on Linux
    assert state["trigger_right"] == 0.9
    assert state["bumper_right"] == 1


def test_xbox_layout_falls_back_to_axis_one_when_the_pad_has_no_axis_three():
    pad = bound("xbox", platform="darwin", numaxes=2, axes={0: 0.4, 1: -0.2})
    assert pad._read_layout()["axis_y"] == -0.2


def test_xbox_triggers_idle_at_minus_one_before_anything_is_read():
    for platform, idle in (("darwin", (4, 5)), ("linux", (2, 5))):
        pad = bound("xbox", platform=platform)
        for index in idle:
            assert pad._axis_values[index] == -1.0
        state = pad._read_layout()
        assert state["trigger_left"] == -1.0
        assert state["trigger_right"] == -1.0


# ----------------------------------------------------------------------
# Xbox, Bluetooth
# ----------------------------------------------------------------------

def test_bluetooth_xbox_layout_on_linux_uses_the_generic_hid_mapping():
    pad = bound("xbox_bluetooth", platform="linux",
                axes={0: 0.5, 3: -0.2, 5: 0.8, 4: -1.0},
                buttons={6: 1}, hats={0: (-1, 1)})
    state = pad._read_layout()
    assert state["axis_x"] == 0.5
    assert state["axis_y"] == -0.2
    assert state["trigger_left"] == 0.8      # LT is axis 5 on BT Linux
    assert state["trigger_right"] == -1.0     # RT is axis 4
    assert state["bumper_left"] == 1              # bumpers are buttons 6 and 7
    assert state["hat_x"] == 1           # negated: GAMEPAD-13
    assert state["hat_y"] == -1


def test_bluetooth_xbox_layout_off_linux_is_the_wired_xbox_layout():
    wired = layout_by_id("xbox")["binds"]["default"]["default"]
    bluetooth = layout_by_id("xbox_bluetooth")["binds"]["default"]["default"]
    assert bluetooth is wired


def test_bluetooth_xbox_idles_three_trigger_axes_on_linux():
    pad = bound("xbox_bluetooth", platform="linux")
    assert [pad._axis_values[i] for i in (2, 4, 5)] == [-1.0, -1.0, -1.0]


# ----------------------------------------------------------------------
# Logitech F310, both switch positions
# ----------------------------------------------------------------------

def test_f310_dinput_is_detected_from_the_axis_and_button_counts():
    assert Gamepad._is_dinput(StubJoystick("Logitech Dual Action", 4, 10)) is True
    assert Gamepad._is_dinput(StubJoystick("Logitech Gamepad F310", 6, 10)) is False


def test_f310_dinput_reads_the_triggers_as_digital_buttons():
    pad = bound("logitech_f310", mode="dinput", numaxes=4,
                axes={0: 0.4, 3: -0.6}, buttons={6: 1, 7: 0, 4: 1})
    state = pad._read_layout()
    assert state["axis_x"] == 0.4
    assert state["axis_y"] == -0.6      # axis 3 exists on a 4-axis pad
    assert state["trigger_left"] == 1.0      # button 6 pressed
    assert state["trigger_right"] == -1.0     # button 7 idle
    assert state["bumper_left"] == 1


def test_f310_dinput_falls_back_to_axis_one_on_a_two_axis_pad():
    pad = bound("logitech_f310", mode="dinput", numaxes=2,
                axes={0: 0.4, 1: -0.6})
    assert pad._read_layout()["axis_y"] == -0.6


def test_f310_xinput_is_the_xbox_layout_on_both_platform_branches():
    f310 = layout_by_id("logitech_f310")["binds"]["xinput"]
    xbox = layout_by_id("xbox")["binds"]["default"]
    assert f310["linux"] is xbox["linux"] and f310["default"] is xbox["default"]

    pad = bound("logitech_f310", mode="xinput", axes={0: 0.7})
    assert pad._read_layout()["axis_x"] == 0.7


# ----------------------------------------------------------------------
# Thrustmaster T16000M
# ----------------------------------------------------------------------

def test_t16000m_button_two_drives_z_down():
    pad = bound("t16000m", numaxes=11, numbuttons=16, buttons={2: 1})
    state = pad._read_layout()
    # The override writes the buttons into virtual axes 9 and 10 first.
    assert pad._axis_values[9] == 1 and pad._axis_values[10] == 0
    assert state["trigger_right"] == 1.0
    assert state["trigger_left"] == -1.0


def test_t16000m_button_three_drives_z_up():
    pad = bound("t16000m", numaxes=11, numbuttons=16, buttons={3: 1})
    state = pad._read_layout()
    assert pad._axis_values[10] == 1 and pad._axis_values[9] == 0
    assert state["trigger_left"] == 1.0
    assert state["trigger_right"] == -1.0


def test_t16000m_bumpers_read_buttons_four_and_five_today():
    """GAMEPAD-12, pinned UNVERIFIED.

    main read the bumpers from buttons 7 and 9. The refactor reads 4 and 5 --
    the 7/9 fallbacks can never be reached, because a 16-button T16000M has
    buttons 4 and 5. Carried over unchanged; settle it at the bench.
    """
    pad = bound("t16000m", numaxes=11, numbuttons=16, buttons={4: 1, 5: 0, 7: 0, 9: 1})
    state = pad._read_layout()
    assert state["bumper_left"] == 1
    assert state["bumper_right"] == 0


def test_t16000m_sticks_are_axes_zero_and_one():
    pad = bound("t16000m", numaxes=11, numbuttons=16, axes={0: -0.75, 1: 0.25})
    state = pad._read_layout()
    assert state["axis_x"] == -0.75
    assert state["axis_y"] == 0.25


# ----------------------------------------------------------------------
# The factory
# ----------------------------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("Xbox Series X Controller", "xbox"),
    ("Controller (XBOX 360 For Windows)", "xbox"),
    ("Logitech Gamepad F310", "logitech_f310"),
    ("Logitech Dual Action", "logitech_f310"),
    ("Thrustmaster T.16000M", "t16000m"),
    ("T.16000M", "t16000m"),
    ("Xbox Wireless Controller", "xbox_bluetooth"),
])
def test_every_pad_in_service_finds_its_layout(name, expected):
    pad = Gamepad("TestProbe", hub=GamepadHub())
    with platform_as("darwin"):
        assert pad._layout_for(StubJoystick(name))["id"] == expected


def test_a_linux_bluetooth_bus_guid_selects_the_bluetooth_row():
    pad = Gamepad("TestProbe", hub=GamepadHub())
    stub = StubJoystick("Some Pad", guid="05000000abcd")
    with platform_as("linux"):
        assert pad._layout_for(stub)["id"] == "xbox_bluetooth"
    with platform_as("darwin"):
        with pytest.raises(ValueError):
            pad._layout_for(stub)


def test_an_unsupported_pad_is_refused_rather_than_given_an_xbox_layout():
    pad = Gamepad("TestProbe", hub=GamepadHub())
    with pytest.raises(ValueError, match="Unsupported joystick detected"):
        pad._layout_for(StubJoystick("Generic HID Device", guid="00"))


# ----------------------------------------------------------------------
# GAMEPAD-13 / GAMEPAD-14, pinned
# ----------------------------------------------------------------------

#: Every Xbox bind table: (layout row, mode, platform). GAMEPAD-13, owner
#: verified on the stage 2026-10-13: with an Xbox pad both D-pad axes stepped
#: the wrong way; the T.16000M stepped the right way.
XBOX_TABLES = [
    ("xbox", "default", "linux"),
    ("xbox", "default", "darwin"),
    ("xbox", "default", "win32"),
    ("xbox_bluetooth", "default", "linux"),
    ("xbox_bluetooth", "default", "darwin"),
    ("xbox_bluetooth", "default", "win32"),
]


@pytest.mark.parametrize("layout_id,mode,platform", XBOX_TABLES,
                         ids=["-".join(t) for t in XBOX_TABLES])
@pytest.mark.parametrize("hat,expected", [
    ((1, 0), (-1, 0)),      # D-pad right
    ((-1, 0), (1, 0)),      # D-pad left
    ((0, 1), (0, -1)),      # D-pad up
    ((0, -1), (0, 1)),      # D-pad down
    ((1, 1), (-1, -1)),
])
def test_an_xbox_dpad_is_negated_on_both_axes(layout_id, mode, platform, hat,
                                              expected):
    """GAMEPAD-13, owner verified 2026-10-13: Xbox X and Y are inverted."""
    pad = bound(layout_id, mode=mode, platform=platform, hats={0: hat})
    state = pad._read_layout()
    assert (state["hat_x"], state["hat_y"]) == expected


@pytest.mark.parametrize("platform", ["linux", "darwin", "win32"])
@pytest.mark.parametrize("hat", [(1, 0), (-1, 0), (0, 1), (0, -1), (1, -1)])
def test_the_t16000m_dpad_is_passed_through_unchanged(platform, hat):
    """GAMEPAD-13, owner verified 2026-10-13: the T.16000M was already right."""
    pad = bound("t16000m", platform=platform, numaxes=11, numbuttons=16,
                hats={0: hat})
    state = pad._read_layout()
    assert (state["hat_x"], state["hat_y"]) == hat


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_the_f310_in_xinput_mode_follows_the_xbox_tables(platform):
    """The F310's XInput switch position is the Xbox layout (it uses the
    Xbox tables), so its D-pad is negated too. UNVERIFIED at the bench: the
    owner verified an Xbox pad, not an F310."""
    pad = bound("logitech_f310", mode="xinput", platform=platform,
                hats={0: (1, -1)})
    state = pad._read_layout()
    assert (state["hat_x"], state["hat_y"]) == (-1, 1)


def test_the_f310_dinput_dpad_is_passed_through_unchanged():
    """Not an Xbox table; not verified at the bench, so not touched."""
    pad = bound("logitech_f310", mode="dinput", hats={0: (1, -1)})
    state = pad._read_layout()
    assert (state["hat_x"], state["hat_y"]) == (1, -1)


@pytest.mark.parametrize("layout_id,mode,platform", XBOX_TABLES + [
    ("t16000m", "default", "linux"), ("logitech_f310", "dinput", "linux")])
def test_a_centred_dpad_reads_integer_zero(layout_id, mode, platform):
    """Negating must not turn neutral into anything but int 0: the neutral
    reference compares by equality and the jog packet casts it."""
    pad = bound(layout_id, mode=mode, platform=platform, numaxes=11,
                numbuttons=16, hats={0: (0, 0)})
    state = pad._read_layout()
    assert (state["hat_x"], state["hat_y"]) == (0, 0)
    assert type(state["hat_x"]) is int and type(state["hat_y"]) is int


@pytest.mark.parametrize("platform", ["linux", "darwin"])
def test_a_held_xbox_dpad_latches_one_negated_edge_per_press(platform):
    """The sign flip happens before edge latching: one edge per press, with
    the corrected sign, and none while the D-pad is held."""
    pad = bound("xbox", platform=platform, hats={0: (1, 1)})
    pad._capture_state()
    assert pad.drain_edges() == {"hat_x": -1, "hat_y": -1}
    for _ in range(10):                      # held: no stream of steps
        pad._capture_state()
    assert pad.drain_edges() == {}
    assert pad.levels["hat_x"] == 0 and pad.levels["hat_y"] == 0
    pad._hat_values[0] = (0, 0)              # released, pressed again
    pad._capture_state()
    pad._hat_values[0] = (1, 0)
    pad._capture_state()
    assert pad.drain_edges() == {"hat_x": -1}


def test_the_deadzone_is_applied_exactly_once_on_the_way_out():
    """GAMEPAD-14: 0.1 on the raw cache *and* 0.12 on the mapped state was two
    applications with two different numbers. The raw cache is untouched now.
    """
    pad = bound("xbox", axes={0: 0.11, 3: -0.11, 4: -0.95, 5: -0.85})
    raw = pad._read_layout()
    assert raw["axis_x"] == 0.11, "the raw reading was deadzoned on the way in"

    state = Gamepad._apply_deadzones(dict(raw))
    assert state["axis_x"] == 0.0
    assert state["axis_y"] == 0.0
    assert state["trigger_left"] == -1.0     # snapped, it was below -0.9
    assert state["trigger_right"] == -0.85    # left alone

    over = Gamepad._apply_deadzones({"axis_x": 0.13, "axis_y": -0.13,
                                     "trigger_left": -1.0, "trigger_right": 0.5})
    assert over["axis_x"] == 0.13
    assert over["axis_y"] == -0.13
    assert over["trigger_right"] == 0.5


def test_applying_the_deadzone_twice_would_not_change_the_answer():
    """The property that makes "exactly once" checkable: it is idempotent, so
    a second application anywhere in the chain would be silent. It is called
    in one place, `_capture_state`, and this is the guard on that."""
    once = Gamepad._apply_deadzones({"axis_x": 0.11, "axis_y": 0.5,
                                     "trigger_left": -0.95, "trigger_right": 0.2})
    twice = Gamepad._apply_deadzones(dict(once))
    assert once == twice


# ----------------------------------------------------------------------
# flush_neutral, the axis-number half (GAMEPAD-8)
# ----------------------------------------------------------------------

def test_flush_neutral_never_writes_minus_one_into_a_stick_axis():
    """The Linux Xbox case that made the old flush dangerous.

    `flush_neutral` treated axes (2, 4, 5) as triggers idling at -1.0 whatever
    the platform. On Linux an Xbox pad's axis 4 is the right-stick **Y**, so
    the safety flush wrote a full-speed Y jog into the state it was clearing.
    """
    pad = bound("xbox", platform="linux", axes={4: 0.7, 0: 0.7})
    pad._last_raw = pad._read_layout()
    assert pad._last_raw["axis_y"] == 0.7

    pad.flush_neutral()

    assert pad._axis_values[4] == 0.7, "the flush rewrote a raw axis cache"
    assert pad.levels["axis_y"] == 0.0
    assert pad.levels["axis_x"] == 0.0
    assert pad.levels == dict(NEUTRAL, **{k: 0 for k in Gamepad.EDGE_KEYS})


def test_flush_neutral_holds_while_the_stick_is_still_held():
    """The other half: a held stick used to come back within one poll tick,
    because the loop read the hardware again and refilled the cache."""
    pad = bound("xbox", axes={0: 0.9})
    pad._capture_state()
    assert pad.levels["axis_x"] == 0.9

    pad.flush_neutral()
    for _ in range(10):                       # ten poll ticks, stick still held
        pad._capture_state()
    assert pad.levels["axis_x"] == 0.0, "a held stick came back after the flush"


def test_flush_neutral_releases_as_soon_as_the_pad_actually_moves():
    pad = bound("xbox", axes={0: 0.9})
    pad._capture_state()
    pad.flush_neutral()
    pad._capture_state()
    assert pad.levels["axis_x"] == 0.0

    pad._axis_values[0] = 0.4                 # the operator moved it
    pad._capture_state()
    assert pad.levels["axis_x"] == 0.4


def test_flush_neutral_latches_no_edges_while_it_holds():
    pad = bound("xbox", buttons={4: 1})
    pad._capture_state()
    assert pad.drain_edges().get("bumper_left") == 1

    pad.flush_neutral()
    for _ in range(5):
        pad._capture_state()
    assert pad.drain_edges() == {}
