"""The Qt view draws a hosted model on its host's page (Model.HOST; owner
ruling 2026-09-28: Red Percent and the Transfer Map are one dashboard).
**Every test here is `qt`-marked**: the agent that wrote them did not run
them (a native Qt abort takes the session down); the lead runs the Qt pass.

The contract (`handoff/brief-dashboard-contract.md`): with the host
launched, the hosted model has no link and no Overview entry of its own;
the host's page draws the host's tier 1, the hosted group (its name, its
state word, its tier 1), the host's disclosure, then the hosted model's
disclosure; every control in the group runs against the hosted model's
name; closing the host gives the hosted model its page back.

The station here is the real `Controller` (it publishes `host`), two
`FakeModel`s with their own schemas, then the real pair in SIM.
"""
import pytest

import schema as sch
from controller.controller import Controller
from events import events
from panel import Panel
from views import qt

from test_core_fakes import FakeModel

pytestmark = pytest.mark.qt


class MapModel(FakeModel):
    NAME = "Map"

    @property
    def schema(self):
        return sch.schema(
            sch.section("Trial", sch.readonly("Mode:", "mode"),
                        sch.button("Move", "move", role="go")),
            sch.section("Store", sch.readonly("Note:", "note"), tier=2,
                        disclosure="Configure Map"),
        )


class RedModel(FakeModel):
    """The hosted model: its tier 1 has a command of the SAME name as the
    host's, so a press that reached the wrong model would be seen."""
    NAME = "Red"
    HOST = "Map"

    @property
    def schema(self):
        return sch.schema(
            sch.section("Run", sch.readonly("Mode:", "mode"),
                        sch.button("Move", "move", role="go")),
            sch.section("Region", sch.readonly("Note:", "note"), tier=2,
                        disclosure="Red details"),
            self._safety_section(),
        )


@pytest.fixture
def station():
    made = Controller()
    yield made
    made.close()


@pytest.fixture
def board(qapp, station):
    window = qt.QtDashboard(station, Panel())
    yield window
    if not window._closing:
        window.close()
    events.unsubscribe(window._on_event)


def _launch(board, station, *pairs):
    for name, model in pairs:
        station.add(name, model, {})
        board._add_panel(name)


def _command_widget(panel, command):
    for element in panel._elements:
        if element.get("command") == command and element.get("type") == "button":
            return panel._widget_for(element)
    raise AssertionError(f"{panel.name} draws no {command!r} button")


def _layout_index(layout, target):
    """Where `target` (a widget or a layout) sits in `layout`, or -1."""
    for index in range(layout.count()):
        item = layout.itemAt(index)
        if item.widget() is target or item.layout() is target:
            return index
    return -1


@pytest.fixture
def hosted(board, station):
    red, host = RedModel(), MapModel()
    _launch(board, station, ("Map", host), ("Red", red))
    return host, red


def test_the_page_list_has_one_link_while_the_host_is_launched(board, hosted):
    board._sync_rail()
    assert list(board._rail_items) == ["Map"]
    assert board._entries["Red"].isHidden()
    board._arrange_entries()
    assert board._arrangement == (("Map",),)


def test_the_hosts_page_draws_the_group_between_its_tier_one_and_the_disclosures(
        board, hosted):
    board.open_entry("Map")
    assert board.page == "Map"
    host, red = board._panels["Map"], board._panels["Red"]
    group = board._groups["Red"].widget
    order = [_layout_index(host._layout, host._tier_layouts[1]),
             _layout_index(host._layout, group),
             _layout_index(host._layout, host.tier_block),
             _layout_index(host._layout, red.tier_block)]
    assert -1 not in order and order == sorted(order), order
    assert group.isVisibleTo(board) and red.tier_block.isVisibleTo(board)
    assert board._groups["Red"].title.text() == "Red"
    assert board._groups["Red"].title.objectName() == "entryName"
    assert board._groups["Red"].title.property("opened") == "false"
    assert board._groups["Red"].word.text() == "Idle"
    assert host.tier_button.text() == "Configure Map"
    assert red.tier_button.text() == "Red details"
    # The group is the hosted model's own panel, inside the host's entry.
    assert board._entries["Map"].isAncestorOf(red)


def test_the_overview_shows_the_host_compact_and_no_hosted_group(board, hosted):
    board.show_overview()
    assert not board._groups["Red"].widget.isVisibleTo(board)
    assert board._entries["Red"].isHidden()


def test_a_command_pressed_in_the_group_reaches_the_hosted_models_name(
        board, hosted):
    host_model, red_model = hosted
    board.open_entry("Map")
    button = _command_widget(board._panels["Red"], "move")
    assert board._entries["Map"].isAncestorOf(button)
    button.click()
    assert red_model.moved and not host_model.moved


def test_the_state_word_follows_the_hosted_models_mode(board, hosted):
    _, red_model = hosted
    red_model.mode = "running"
    board._sync_states()
    assert board._groups["Red"].word.text() == "Running"


def test_navigating_to_the_hosted_model_lands_on_the_hosts_page(board, hosted):
    board.open_entry("Red")
    assert board.page == "Map"
    board._sync_rail()
    assert board._rail_items["Map"].isChecked()


def test_the_hosts_link_folds_the_hosted_models_latch(board, hosted):
    _, red_model = hosted
    board._sync_states()
    assert board._rail_items["Map"].stop_mark is None
    red_model.estop()
    board._sync_states()
    assert board._rail_items["Map"].stop_mark == "stopped"


def test_the_hosts_link_and_entry_fold_a_hosted_stop_that_did_not_confirm(
        board, hosted):
    """The worse of the two shows: a stop that did not confirm outranks a
    plain latch (`rail_mark`'s own ranking), on the link and in the entry."""
    host_model, red_model = hosted
    host_model.estop()
    red_model.halt_result = False
    red_model.estop()
    assert red_model.stop_confirmed is False
    board._sync_states()
    assert board._rail_items["Map"].stop_mark == "unconfirmed"
    assert board._entries["Map"].is_unconfirmed


def test_the_group_follows_the_hosts_tier_one_in_the_tab_order(board, hosted):
    board.open_entry("Map")
    board._order_tab()
    host, red = board._panels["Map"], board._panels["Red"]
    first = _command_widget(host, "move")
    group = _command_widget(red, "move")
    chain, widget = [], board._entries["Map"].head
    for _ in range(5000):
        widget = widget.nextInFocusChain()
        if widget is board._entries["Map"].head:
            break
        chain.append(widget)
    assert chain.index(first) < chain.index(group) < chain.index(host.tier_button)
    assert chain.index(host.tier_button) < chain.index(red.tier_button)


def test_closing_the_host_gives_the_hosted_model_its_page_back(
        board, station, hosted):
    _, red_model = hosted
    station.remove("Map")
    board._remove_panel("Map")
    red = board._panels["Red"]
    assert qt.qt_alive(red) and qt.qt_alive(red.tier_block)
    assert board._entries["Red"].isAncestorOf(red)
    assert red.isAncestorOf(red.tier_block)
    assert "Red" not in board._groups
    board._sync_rail()
    assert list(board._rail_items) == ["Red"]
    board._arrange_entries()
    assert not board._entries["Red"].isHidden()
    _command_widget(red, "move").click()
    assert red_model.moved


def test_closing_the_hosted_model_leaves_the_host_untouched(
        board, station, hosted):
    host_model, _ = hosted
    host = board._panels["Map"]
    station.remove("Red")
    board._remove_panel("Red")
    assert "Red" not in board._groups
    assert _layout_index(host._layout, host.tier_block) == host._layout.count() - 1
    board._sync_rail()
    assert list(board._rail_items) == ["Map"]
    _command_widget(host, "move").click()
    assert host_model.moved


def test_a_hosted_model_launched_first_moves_onto_its_host_when_it_arrives(
        board, station):
    _launch(board, station, ("Red", RedModel()))
    board._sync_rail()
    assert list(board._rail_items) == ["Red"] and "Red" not in board._groups
    _launch(board, station, ("Map", MapModel()))
    board._sync_rail()
    assert list(board._rail_items) == ["Map"] and "Red" in board._groups


def test_the_real_pair_in_sim_is_one_dashboard(board, station, tmp_path,
                                              monkeypatch):
    """Red Percent over a fake desktop and the Transfer Map on a private
    database (tests never touch the project database)."""
    monkeypatch.setenv("STATION_MAP_DB", str(tmp_path / "db" / "map.sqlite"))
    from model.red_monitor import RedMonitor
    from model.transfer_map import TransferMap
    from test_red_monitor import desktop_screen
    red = RedMonitor(screen=desktop_screen())
    red.output_root = tmp_path / "runs"
    _launch(board, station, ("Transfer Map", TransferMap()), ("Red Percent", red))
    board._sync_rail()
    assert list(board._rail_items) == ["Transfer Map"]
    board.open_entry("Red Percent")
    assert board.page == "Transfer Map"
    group = board._groups["Red Percent"]
    assert group.title.text() == "Red Percent"
    assert board._panels["Red Percent"].tier_button.text() == "Red Percent details"
    station.remove("Transfer Map")
    board._remove_panel("Transfer Map")
    board._sync_rail()
    assert list(board._rail_items) == ["Red Percent"]
