"""GamepadHub and Gamepad: claims, binding, the one poll loop, latched edges.

Every test here runs against a fake SDL layer. Nothing opens a real gamepad,
and the autouse fixture makes that structural: `station.devices.gamepad.pygame`
is replaced for the duration of every test in this file, so even a test that
forgot to ask cannot reach the hardware on a machine with pygame installed.

Ported from `tests/hardware/test_gamepad.py` and
`tests/hardware/test_gamepad17_fallback_poller_starts.py`, which pinned the
same behaviours against `ControllerPoller` + `InputService`.
"""
import contextlib
import threading
import time
from unittest.mock import patch

import pytest

from station.devices import gamepad as gamepad_module
from station.devices.gamepad import Gamepad, GamepadHub, hub as module_hub
from station.events import events


# ----------------------------------------------------------------------
# A fake SDL
# ----------------------------------------------------------------------

class FakeError(Exception):
    """Stands in for pygame.error."""


class FakeJoystick:
    def __init__(self, name="Xbox Series X Controller", numaxes=6, numbuttons=10,
                 numhats=1, guid="030000005e04"):
        self.name = name
        self.axes = {i: 0.0 for i in range(numaxes)}
        self.buttons = {i: 0 for i in range(numbuttons)}
        self.hats = {i: (0, 0) for i in range(numhats)}
        self.guid = guid
        self.is_dead = False
        self.quit_calls = 0
        self.init_calls = 0

    # -- the SDL surface a layout and a poll tick use
    def get_name(self):
        self._check()
        return self.name

    def get_guid(self):
        return self.guid

    def get_numaxes(self):
        self._check()
        return len(self.axes)

    def get_numbuttons(self):
        self._check()
        return len(self.buttons)

    def get_numhats(self):
        self._check()
        return len(self.hats)

    def get_axis(self, index):
        self._check()
        return self.axes[index]

    def get_button(self, index):
        self._check()
        return self.buttons[index]

    def get_hat(self, index):
        self._check()
        return self.hats[index]

    def init(self):
        self._check()
        self.init_calls += 1

    def quit(self):
        self.quit_calls += 1

    def _check(self):
        if self.is_dead:
            raise FakeError("device removed")


class FakeJoystickModule:
    def __init__(self, devices):
        self.devices = list(devices)
        self._is_init = False
        self.quit_calls = 0

    def init(self):
        self._is_init = True

    def get_init(self):
        return self._is_init

    def quit(self):
        self.quit_calls += 1
        self._is_init = False

    def get_count(self):
        return len(self.devices)

    def Joystick(self, index):
        if not 0 <= index < len(self.devices):
            raise FakeError(f"no joystick at index {index}")
        return self.devices[index]


class FakeEventModule:
    def __init__(self):
        self.pumps = 0

    def pump(self):
        self.pumps += 1

    def get(self):
        return []


class FakePygame:
    error = FakeError

    def __init__(self, devices):
        self.joystick = FakeJoystickModule(devices)
        self.event = FakeEventModule()
        self.init_calls = 0
        self.quit_calls = 0

    def init(self):
        self.init_calls += 1

    def quit(self):
        self.quit_calls += 1


@pytest.fixture(autouse=True)
def fake_sdl():
    """No test in this file may see the real pygame."""
    fake = FakePygame([FakeJoystick(name="Xbox Series X Controller"),
                       FakeJoystick(name="Controller (XBOX 360 For Windows)")])
    with patch.object(gamepad_module, "pygame", fake):
        yield fake


@pytest.fixture
def hub():
    """A hub of this test's own, so no claim outlives it."""
    made = GamepadHub()
    yield made
    made.close()


@contextlib.contextmanager
def platform_as(name):
    with patch("station.devices.gamepad.sys.platform", name):
        yield


def make_pad(hub, owner="StepperProbe", bind_to=None, open_it=False):
    pad = Gamepad(owner, hub=hub)
    if bind_to is not None:
        assert pad.bind(bind_to) is True
    if open_it:
        pad.open()
    return pad


def wait_for(predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.005)
    return bool(predicate())


# ----------------------------------------------------------------------
# One SDL owner
# ----------------------------------------------------------------------

def test_the_module_offers_one_hub_for_the_process():
    assert isinstance(module_hub, GamepadHub)


def test_closing_one_gamepad_never_tears_sdl_down(hub, fake_sdl):
    """Closing a device releases that device, and nothing else (RC-13).

    The old code decremented a module-level poller count and called
    `pygame.quit()` when it hit zero, so closing one device tore SDL down
    under another that was still running -- and a poller that had never bound
    anything still decremented. Process-wide teardown belongs at process exit.
    """
    first = make_pad(hub, "StepperProbe", bind_to="ID 0: Xbox Series X Controller")
    second = make_pad(hub, "DCProbe", bind_to=1)
    never_bound = make_pad(hub, "ChuckPositioner")

    never_bound.close()
    first.close()

    assert fake_sdl.quit_calls == 0
    assert fake_sdl.joystick.quit_calls == 0
    assert hub.index_for("StepperProbe") is None
    assert hub.index_for("DCProbe") == 1, "the other probe lost its device"

    second.close()
    assert fake_sdl.quit_calls == 0, "the last close took SDL down"

    first.close()   # idempotent
    assert fake_sdl.quit_calls == 0


def test_the_hub_comes_down_only_when_it_is_asked_to(fake_sdl):
    hub = GamepadHub()
    pad = make_pad(hub, "StepperProbe", bind_to=0)
    hub.close()
    assert fake_sdl.quit_calls == 1
    assert hub.claims == {}, "a claim outlived the hub"
    pad.close()


def test_a_claim_is_released_on_close_so_the_next_launch_is_not_blocked(hub):
    """GAMEPAD-10 / MANAGER-16 / VIEW-TKINTER-6: the claims dict was never
    cleared, so a second launch inherited "StepperProbe holds ID 0" and a
    legitimate bind failed with a claim conflict."""
    first = make_pad(hub, "StepperProbe", bind_to=0)
    assert hub.claims == {"StepperProbe": 0}
    first.close()
    assert hub.claims == {}

    relaunched = make_pad(hub, "DCProbe")
    assert relaunched.bind(0) is True, "a stale claim blocked the next launch"
    relaunched.close()


def test_two_gamepads_cannot_hold_the_same_pad(hub):
    first = make_pad(hub, "StepperProbe", bind_to=0)
    second = make_pad(hub, "DCProbe")

    assert second.bind(0) is False
    assert second.is_bound is False
    assert hub.index_for("StepperProbe") == 0
    assert hub.claims == {"StepperProbe": 0}
    first.close()


def test_the_claim_registry_is_derived_from_real_acquisitions(hub):
    pad = make_pad(hub, "StepperProbe", bind_to=0)
    assert hub.claims == {"StepperProbe": 0}
    pad.bind(1)
    assert hub.claims == {"StepperProbe": 1}, "the registry drifted from reality"
    pad.bind("None")
    assert hub.claims == {}
    pad.close()


def test_the_hub_counts_and_reports_connected_indices(hub, fake_sdl):
    hub.open()
    assert hub.count == 2
    assert hub.is_connected(0) is True
    assert hub.is_connected(2) is False
    assert hub.is_connected(None) is False
    assert hub.names == ["ID 0: Xbox Series X Controller",
                         "ID 1: Controller (XBOX 360 For Windows)"]


# ----------------------------------------------------------------------
# Binding
# ----------------------------------------------------------------------

@pytest.mark.parametrize("selection", [None, "None", "None Detected", "",
                                       "Virtual Controller A", "N/A"])
def test_binding_to_nothing_returns_false(hub, selection):
    """A failed or empty bind must report False: that is the signal the probe
    stops and reverts the selection on (GAMEPAD-3, GAMEPAD-4, GAMEPAD-6)."""
    pad = make_pad(hub, "StepperProbe")
    assert pad.bind(selection) is False
    assert pad.is_bound is False
    pad.close()


def test_binding_to_an_unparsable_name_returns_false(hub):
    pad = make_pad(hub, "StepperProbe")
    assert pad.bind("no digits here") is False
    assert pad.is_bound is False
    pad.close()


def test_binding_to_an_absent_index_returns_false(hub):
    pad = make_pad(hub, "StepperProbe")
    assert pad.bind("ID 7: Ghost Pad") is False
    assert pad.is_bound is False
    pad.close()


def test_binding_to_an_unsupported_pad_returns_false_and_holds_no_claim(hub, fake_sdl):
    fake_sdl.joystick.devices.append(FakeJoystick(name="Generic HID Device", guid="00"))
    pad = make_pad(hub, "StepperProbe")
    assert pad.bind(2) is False
    assert pad.is_bound is False
    assert hub.claims == {}, "an unsupported pad was left claimed"
    pad.close()


def test_binding_to_a_pad_another_probe_holds_returns_false(hub):
    holder = make_pad(hub, "StepperProbe", bind_to=0)
    pad = make_pad(hub, "DCProbe")
    assert pad.bind("ID 0: Xbox Series X Controller") is False
    assert pad.is_bound is False
    holder.close()
    pad.close()


def test_a_failed_swap_leaves_the_gamepad_unbound_not_on_the_old_pad(hub):
    """A swap that fails must not silently keep driving the previous pad."""
    pad = make_pad(hub, "StepperProbe", bind_to=0)
    assert pad.bind("ID 9: Nothing There") is False
    assert pad.is_bound is False
    assert hub.claims == {}
    pad.close()


def test_multi_digit_indices_parse(hub, fake_sdl):
    for index in range(2, 13):
        fake_sdl.joystick.devices.append(FakeJoystick(name=f"Xbox Pad {index}"))
    pad = make_pad(hub, "StepperProbe")
    assert pad.bind("ID 12: Xbox Pad 12") is True
    assert hub.index_for("StepperProbe") == 12
    pad.close()


def test_integer_selections_bind(hub):
    pad = make_pad(hub, "StepperProbe")
    assert pad.bind(1) is True
    assert hub.index_for("StepperProbe") == 1
    pad.close()


def test_rebinding_the_same_pad_after_a_disconnect_reconnects_it(hub, fake_sdl):
    """GAMEPAD-5: re-selecting the same entry was the operator's way back
    after a replug, and it handed back the stale handle instead of a fresh
    one. Selecting the same name is a real rebind."""
    pad = make_pad(hub, "StepperProbe", bind_to=0, open_it=True)
    assert wait_for(lambda: pad.is_bound and pad._is_polling)
    first_handle = pad._joystick

    pad._handle_disconnect("unplugged")
    assert pad.is_bound is False
    assert hub.claims == {}

    replacement = FakeJoystick(name="Xbox Series X Controller")
    fake_sdl.joystick.devices[0] = replacement

    assert pad.bind("ID 0: Xbox Series X Controller") is True
    assert pad.is_bound is True
    assert pad._joystick is replacement and pad._joystick is not first_handle
    assert pad._is_polling, "the rebind did not resume the poll loop"
    pad.close()


# ----------------------------------------------------------------------
# options
# ----------------------------------------------------------------------

def test_options_start_with_none_and_list_the_attached_pads(hub):
    pad = make_pad(hub, "StepperProbe")
    assert pad.options == ["None",
                           "ID 0: Xbox Series X Controller",
                           "ID 1: Controller (XBOX 360 For Windows)"]
    pad.close()


def test_options_hide_a_pad_another_probe_holds_but_keep_our_own(hub):
    holder = make_pad(hub, "DCProbe", bind_to=1)
    pad = make_pad(hub, "StepperProbe", bind_to=0)
    assert pad.options == ["None", "ID 0: Xbox Series X Controller"]
    holder.close()
    pad.close()


def test_options_refresh_live(hub, fake_sdl):
    """VIEW-TKINTER-10 / PYSIDE-20: the list was built once, so a pad plugged
    in after the dashboard opened never appeared and an unplugged one never
    left."""
    pad = make_pad(hub, "StepperProbe")
    assert len(pad.options) == 3

    fake_sdl.joystick.devices.append(FakeJoystick(name="Thrustmaster T.16000M"))
    assert pad.options[-1] == "ID 2: Thrustmaster T.16000M"

    del fake_sdl.joystick.devices[2]
    del fake_sdl.joystick.devices[1]
    assert pad.options == ["None", "ID 0: Xbox Series X Controller"]
    pad.close()


# ----------------------------------------------------------------------
# One poll loop, ever
# ----------------------------------------------------------------------

def test_polling_runs_without_any_event_loop(hub, fake_sdl):
    """This is why the Web frontend had no manual mode: the loop rescheduled
    itself through a Tk widget's `after()` and stopped when there was none, so
    entering manual mode energized the coils and then did nothing else
    (GAMEPAD-1)."""
    fake_sdl.joystick.devices[0].axes[0] = 0.9
    pad = make_pad(hub, "WebProbe", bind_to=0, open_it=True)
    try:
        assert wait_for(lambda: pad.levels.get("x_axisStatus") == 0.9), \
            "input never reached the reader with no event loop"
        assert pad._thread is not None and pad._thread.is_alive()
    finally:
        pad.close()


def test_a_successful_swap_keeps_input_alive(hub, fake_sdl):
    """GAMEPAD-21: the resume was gated on a Tk root that is never assigned,
    so *every* successful swap on *every* frontend stopped input for the life
    of the process while manual mode stayed engaged."""
    fake_sdl.joystick.devices[0].axes[0] = 0.9
    fake_sdl.joystick.devices[1].axes[0] = 0.5
    pad = make_pad(hub, "WebProbe", bind_to=0, open_it=True)
    try:
        assert wait_for(lambda: pad.levels.get("x_axisStatus") == 0.9)

        assert pad.bind(1) is True
        assert pad._is_polling, "a successful swap left the pad stopped"
        assert wait_for(lambda: pad.levels.get("x_axisStatus") == 0.5), \
            "stick deflection stopped reaching the reader after the swap"
    finally:
        pad.close()


def test_opening_before_anything_is_bound_starts_the_loop_at_the_bind(hub, fake_sdl):
    """The model's order: the device opens with the model, and the operator
    picks a pad afterwards. Nothing polls until there is something to poll."""
    fake_sdl.joystick.devices[0].axes[0] = 0.3
    pad = Gamepad("StepperProbe", hub=hub)
    try:
        assert pad.open() is False, "a loop started with no pad bound"
        assert pad._thread is None

        assert pad.bind(0) is True
        assert wait_for(lambda: pad.levels.get("x_axisStatus") == 0.3), \
            "binding after open never started the loop"
    finally:
        pad.close()


def test_a_closed_gamepad_does_not_take_a_claim_it_will_never_poll(hub):
    pad = make_pad(hub, "StepperProbe", bind_to=0, open_it=True)
    pad.close()
    assert pad.bind(0) is False
    assert hub.claims == {}
    assert pad.is_bound is False


def test_levels_carry_exactly_the_standard_keys(hub):
    pad = make_pad(hub, "StepperProbe", bind_to=0)
    pad.poll_once()
    assert set(pad.levels) == {"x_axisStatus", "y_axisStatus", "z_axisStatusL",
                               "z_axisStatusR", "dpad_LR", "dpad_UD",
                               "LBumper", "RBumper"}
    pad.close()


def test_an_unbound_gamepad_reads_empty_and_drains_nothing(hub):
    pad = Gamepad("StepperProbe", hub=hub)
    assert pad.levels == {}
    assert pad.drain_edges() == {}
    assert pad.is_bound is False
    pad.poll_once()          # must not raise
    pad.close()


def test_a_swap_does_not_start_polling_on_a_gamepad_nobody_opened(hub):
    """The other half of the gate: picking a pad from a dropdown is not a
    request to start driving the hardware."""
    pad = make_pad(hub, "StepperProbe", bind_to=0)
    assert pad._is_polling is False
    assert pad.bind(1) is True
    assert pad._is_polling is False, "a swap started polling on its own"
    assert pad._thread is None
    pad.close()


def test_a_restart_leaves_exactly_one_poll_loop(hub):
    """GAMEPAD-7: `is_polling` was the only thing a scheduled tick checked, so
    a restart that happened before it woke revived the old chain instead of
    ending it. N swaps gave N+1 loops against one shared cache."""
    pad = make_pad(hub, "StepperProbe", bind_to=0, open_it=True)
    try:
        first = pad._thread
        assert wait_for(lambda: first.is_alive())

        pad._stop_poll_loop("test restart")
        pad._start_poll_loop()
        second = pad._thread

        assert second is not first, "no new poll loop after the restart"
        assert wait_for(lambda: not first.is_alive()), \
            "the stopped loop is still polling alongside its replacement"
        assert second.is_alive()
    finally:
        pad.close()


def test_every_swap_leaves_exactly_one_poll_loop(hub):
    pad = make_pad(hub, "StepperProbe", bind_to=0, open_it=True)
    try:
        live = []
        for target in (1, 0, 1):
            previous = pad._thread
            assert pad.bind(target) is True
            assert wait_for(lambda: not previous.is_alive()), \
                f"the loop from before the swap to {target} is still running"
            live = [t for t in threading.enumerate()
                    if t.name == "gamepad-StepperProbe" and t.is_alive()]
            assert len(live) == 1, f"{len(live)} poll loops are live instead of 1"
    finally:
        pad.close()


def test_a_stale_tick_retires_instead_of_running(hub):
    pad = make_pad(hub, "StepperProbe", bind_to=0)
    stale = pad._generation
    pad._next_generation()
    assert pad._poll_tick(stale) is False
    pad.close()


def test_poll_once_adopts_the_current_generation(hub, fake_sdl):
    fake_sdl.joystick.devices[0].axes[0] = 0.6
    pad = make_pad(hub, "StepperProbe", bind_to=0)
    pad.poll_once()
    assert pad.levels["x_axisStatus"] == 0.6
    pad.close()


def test_a_stopped_loop_stops_answering_with_a_stale_reading(hub, fake_sdl):
    fake_sdl.joystick.devices[0].axes[0] = 0.9
    pad = make_pad(hub, "StepperProbe", bind_to=0, open_it=True)
    assert wait_for(lambda: pad.levels.get("x_axisStatus") == 0.9)
    pad._stop_poll_loop("test")
    assert pad.levels == {}, "a stale full-deflection reading survived the stop"
    pad.close()


# ----------------------------------------------------------------------
# Latched edges
# ----------------------------------------------------------------------

def _pressing(pad, fake_sdl, buttons):
    for index, value in buttons.items():
        fake_sdl.joystick.devices[0].buttons[index] = value
    pad.poll_once()


def test_a_tap_shorter_than_a_read_interval_is_not_lost(hub, fake_sdl):
    """Edges used to be detected inside the reader, so a press that started
    and ended between two reads was never seen at all."""
    pad = make_pad(hub, "StepperProbe", bind_to=0)
    pad.poll_once()
    _pressing(pad, fake_sdl, {4: 1})      # down
    _pressing(pad, fake_sdl, {4: 0})      # and up, all between reads
    assert pad.drain_edges().get("LBumper") == 1, "the tap was dropped"
    pad.close()


def test_reading_levels_does_not_consume_edges(hub, fake_sdl):
    """GAMEPAD-15: whichever caller read first swallowed the edge for everyone
    else, because the read updated the latch. The Red Percent thread and the
    jog loop were both readers."""
    fake_sdl.joystick.devices[0].axes[0] = 0.8
    fake_sdl.joystick.devices[0].hats[0] = (1, 0)
    pad = make_pad(hub, "StepperProbe", bind_to=0)
    pad.poll_once()

    assert pad.levels["x_axisStatus"] == 0.8
    assert pad.levels["x_axisStatus"] == 0.8
    assert pad.drain_edges().get("dpad_LR") == 1, "a level read ate the edge"
    pad.close()


def test_edges_drain_exactly_once(hub, fake_sdl):
    pad = make_pad(hub, "StepperProbe", bind_to=0)
    _pressing(pad, fake_sdl, {5: 1})
    assert pad.drain_edges().get("RBumper") == 1
    assert pad.drain_edges() == {}
    pad.close()


def test_holding_a_button_produces_one_edge_not_a_stream(hub, fake_sdl):
    pad = make_pad(hub, "StepperProbe", bind_to=0)
    fake_sdl.joystick.devices[0].buttons[4] = 1
    for _ in range(5):
        pad.poll_once()
    assert pad.drain_edges().get("LBumper") == 1
    for _ in range(5):
        pad.poll_once()
    assert pad.drain_edges() == {}, "a held button kept re-firing"
    pad.close()


def test_levels_never_carry_edge_keys(hub, fake_sdl):
    fake_sdl.joystick.devices[0].hats[0] = (1, 0)
    pad = make_pad(hub, "StepperProbe", bind_to=0)
    pad.poll_once()
    assert pad.levels["dpad_LR"] == 0
    pad.close()


def test_levels_are_readable_from_another_thread_without_consuming_edges(hub, fake_sdl):
    """The Red Percent monitor reads levels from its own thread while the jog
    loop drains edges. Neither may disturb the other."""
    fake_sdl.joystick.devices[0].axes[0] = 0.7
    fake_sdl.joystick.devices[0].buttons[4] = 1
    pad = make_pad(hub, "StepperProbe", bind_to=0, open_it=True)
    try:
        assert wait_for(lambda: pad.levels.get("x_axisStatus") == 0.7)

        seen = []
        stop = threading.Event()

        def reader():
            while not stop.is_set():
                seen.append(pad.levels.get("x_axisStatus"))

        thread = threading.Thread(target=reader, name="reader", daemon=True)
        thread.start()
        time.sleep(0.05)
        stop.set()
        thread.join(timeout=2.0)

        assert seen and set(seen) == {0.7}
        assert pad.drain_edges().get("LBumper") == 1, \
            "a background level reader swallowed the edge"
    finally:
        pad.close()


# ----------------------------------------------------------------------
# Presence, disconnect, the gate
# ----------------------------------------------------------------------

def test_the_macos_presence_check_re_enumerates_instead_of_asking_the_old_handle(hub, fake_sdl):
    """GAMEPAD-19: the darwin branch asked `self.gamepad.joystick.get_name()`.

    During a swap `controller_index` already named the device being bound
    while the handle was still the *previous* device's, so the check answered
    about the wrong pad: an unplugged old pad made a perfectly present new one
    fail with "Device not physically present at OS level".
    """
    pad = make_pad(hub, "StepperProbe")
    pad._joystick = fake_sdl.joystick.devices[0]
    fake_sdl.joystick.devices[0].is_dead = True     # the old pad is gone

    with platform_as("darwin"):
        assert pad._is_present(1, rescan=True) is True, \
            "the presence check for pad 1 was answered by pad 0"
    pad.close()


def test_a_macos_swap_succeeds_when_the_previous_pad_is_gone(hub, fake_sdl):
    with platform_as("darwin"):
        pad = make_pad(hub, "StepperProbe", bind_to=0)
        fake_sdl.joystick.devices[0].is_dead = True
        assert pad.bind(1) is True, \
            "the swap failed because the *previous* pad had been unplugged"
        assert pad.is_bound is True
        pad.close()


def test_a_macos_disconnect_is_still_detected(hub, fake_sdl):
    """The fix narrows which device gets asked; it does not stop asking."""
    with platform_as("darwin"):
        pad = make_pad(hub, "StepperProbe", bind_to=0)
        del fake_sdl.joystick.devices[1]
        del fake_sdl.joystick.devices[0]
        pad._darwin_scan_mark = 0.0
        assert pad.is_open is False
        pad.close()


def test_an_index_that_renumbers_onto_another_device_counts_as_a_disconnect(hub, fake_sdl):
    with platform_as("darwin"):
        pad = make_pad(hub, "StepperProbe", bind_to=0)
        fake_sdl.joystick.devices[0] = FakeJoystick(name="Thrustmaster T.16000M")
        pad._darwin_scan_mark = 0.0
        assert pad.is_open is False
        pad.close()


def test_losing_the_pad_stops_the_loop_and_releases_the_claim(hub, fake_sdl):
    """The model reads `is_bound`: losing the pad while MANUAL is what makes
    it halt and disable (STEPPER-5, DC-17, VIEW-TKINTER-4)."""
    pad = make_pad(hub, "StepperProbe", bind_to=0, open_it=True)
    assert wait_for(lambda: pad._is_polling)
    thread = pad._thread

    del fake_sdl.joystick.devices[1]
    del fake_sdl.joystick.devices[0]

    assert wait_for(lambda: pad.is_bound is False), "the loss went unnoticed"
    assert wait_for(lambda: not thread.is_alive()), "the poll loop kept running"
    assert hub.claims == {}, "a lost pad kept its claim"
    assert pad.levels == {}
    assert pad.status == "lost"
    pad.close()


def test_status_words_report_the_binding(hub):
    pad = make_pad(hub, "StepperProbe")
    assert pad.status == "unbound"
    pad.bind(0)
    assert pad.status == "bound"
    pad.close()
    assert pad.status == "closed"


def test_the_gate_opens_and_closes_and_a_close_flushes_neutral(hub, fake_sdl):
    fake_sdl.joystick.devices[0].axes[0] = 0.9
    pad = make_pad(hub, "StepperProbe", bind_to=0)
    pad.poll_once()
    assert pad.is_gate_open is True
    assert pad.levels["x_axisStatus"] == 0.9

    pad.set_gate(False)
    assert pad.is_gate_open is False
    pad.poll_once()
    assert pad.levels["x_axisStatus"] == 0.0, "the gate closed but the stick still read"

    pad.set_gate(True)
    assert pad.is_gate_open is True
    pad.close()


def test_the_pad_log_records_what_moved(hub, fake_sdl):
    pad = make_pad(hub, "StepperProbe", bind_to=0)
    fake_sdl.joystick.devices[0].axes[0] = 0.5
    fake_sdl.joystick.devices[0].buttons[4] = 1
    fake_sdl.joystick.devices[0].hats[0] = (1, 0)
    pad.poll_once()

    text = "\n".join(pad.log)
    assert "Bound ID 0" in text
    assert "Axis 0 changed: 0.50" in text
    assert "Button 4 pressed" in text
    assert "Hat 0 (DPad) changed: (1, 0)" in text
    pad.close()


# ----------------------------------------------------------------------
# No pygame at all
# ----------------------------------------------------------------------

def test_the_module_works_with_no_pygame_installed(hub):
    with patch.object(gamepad_module, "pygame", None), \
            patch.object(gamepad_module, "_load_pygame", lambda: None):
        pad = Gamepad("StepperProbe", hub=hub)
        assert hub.open() is False
        assert pad.options == ["None"]
        assert pad.bind(0) is False
        assert pad.is_bound is False
        assert pad.levels == {}
        pad.close()


def test_a_read_error_does_not_become_an_attribute_error_when_pygame_is_none(hub):
    """GAMEPAD-17: `except pygame.error` evaluated `None.error` the moment
    anything else in the try block raised, replacing the real exception with
    an unrelated AttributeError."""
    pad = make_pad(hub, "StepperProbe", bind_to=0)
    with patch.object(gamepad_module, "pygame", None), \
            patch.object(Gamepad, "_read_layout", side_effect=RuntimeError("layout boom")):
        with pytest.raises(RuntimeError, match="layout boom"):
            pad._read_raw()
    pad.close()


# ----------------------------------------------------------------------
# Diagnostics (Addendum 1)
# ----------------------------------------------------------------------

def test_the_log_file_records_binds_losses_loop_transitions_and_a_poll_rate(hub, fake_sdl, tmp_path):
    """Everything the bench needs to reconstruct a session, file only."""
    path = events.open_file(str(tmp_path))
    try:
        pad = Gamepad("StepperProbe", hub=hub)
        pad.RATE_REPORT_SECONDS = 0.0          # report on the first tick
        assert pad.bind(0) is True
        pad.open()
        assert wait_for(lambda: pad._is_polling)

        rival = Gamepad("DCProbe", hub=hub)
        assert rival.bind(0) is False          # claim conflict

        del fake_sdl.joystick.devices[1]
        del fake_sdl.joystick.devices[0]
        assert wait_for(lambda: pad.is_bound is False)
        pad.close()
        rival.close()
    finally:
        events.close_file()

    text = open(path, encoding="utf-8").read()
    assert "Gamepad bind requested" in text
    assert "Gamepad Bound" in text
    assert "Poll loop started" in text
    assert "Poll loop stopped" in text
    assert "Gamepad Disconnected" in text
    assert "Gamepad unbound" in text
    assert "Claim refused" in text and "Gamepad Claim Conflict" in text
    assert "Hz observed over" in text
    assert "gamepad:StepperProbe" in text


def test_nothing_in_the_poll_loop_reaches_a_view(hub, fake_sdl):
    """`events.debug` is file-only, and nothing in a loop may publish per
    iteration. A subscriber must see no traffic from a quiet poll."""
    seen = []
    events.subscribe(seen.append)
    try:
        pad = make_pad(hub, "StepperProbe", bind_to=0)
        seen.clear()
        for _ in range(50):
            pad.poll_once()
        assert seen == [], f"the poll loop published {len(seen)} event(s) to the views"
        pad.close()
    finally:
        events.unsubscribe(seen.append)
