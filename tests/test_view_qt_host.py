"""The Qt view's hosted-model rules (Model.HOST, owner ruling 2026-09-28:
Red Percent and the Transfer Map are one dashboard), toolkit-free.

`Controller.state()` publishes `host` per model: the host's NAME while the
host is launched, else None. These are the pure rules the dashboard draws
by; the widgets that apply them are `test_view_qt_host_widgets.py`
(`qt`-marked, run by the lead).
"""
import re

import pytest

from views import qt


def _states(**hosts):
    return {name: {"host": host, "mode": "idle"} for name, host in hosts.items()}


def test_a_model_whose_host_is_launched_is_hosted_by_it():
    states = _states(Map=None, Red="Map", Probe=None)
    assert qt.hosted_pairs(["Map", "Red", "Probe"], states) == {"Red": "Map"}


def test_a_model_is_its_own_page_when_its_host_is_not_launched():
    # The state names a host that is not open (a stale read, a close in
    # flight): the view does not hide the model behind a page that is gone.
    states = _states(Red="Map", Probe=None)
    assert qt.hosted_pairs(["Red", "Probe"], states) == {}
    # And a state with no `host` at all (an older Controller) hosts nothing.
    assert qt.hosted_pairs(["Map", "Red"], {"Map": {}, "Red": {}}) == {}


def test_a_model_never_hosts_itself():
    assert qt.hosted_pairs(["Loop"], _states(Loop="Loop")) == {}


def test_the_page_list_has_one_link_for_the_host_and_its_hosted_model():
    names = ["Map", "Red", "Probe"]
    hosted = qt.hosted_pairs(names, _states(Map=None, Red="Map", Probe=None))
    assert qt.page_names(names, hosted) == ["Map", "Probe"]
    # Closing the host gives the hosted model its link back.
    names = ["Red", "Probe"]
    hosted = qt.hosted_pairs(names, _states(Red=None, Probe=None))
    assert qt.page_names(names, hosted) == ["Red", "Probe"]


@pytest.mark.parametrize("marks, worst", [
    ([None, None], None),
    ([None, "stopped"], "stopped"),
    (["stopped", "faulted"], "faulted"),
    (["stopped", "unconfirmed"], "unconfirmed"),
    (["unconfirmed", "faulted", "stopped"], "unconfirmed"),
])
def test_the_hosts_link_shows_the_worse_of_the_two_stop_marks(marks, worst):
    """The hosted model's stop state is folded into its host's link: the
    view's own ranking (`rail_mark`): a stop that did not confirm outranks a
    fault, a fault a plain latch, a latch a live model."""
    assert qt.worst_mark(marks) == worst


def test_the_fold_ranks_exactly_the_marks_rail_mark_can_return():
    # Updated (rb-link-views V1): rail_mark also returns the entry's link
    # tier - "lost" (a link lost or reconnecting) and "attention" (stalled,
    # or a held input gate) - so the fold must rank those too.
    stop = {"latched": ["a", "b", "c"], "unconfirmed": ["a"]}
    tiers = {"e": "error", "f": "warning"}
    kinds = {qt.rail_mark(n, stop, {"b"}, tiers)
             for n in ("a", "b", "c", "d", "e", "f")}
    assert kinds - {None} == set(qt.RAIL_MARK_RANK)


@pytest.mark.parametrize("mode, word", [
    ("idle", "Idle"), ("running", "Running"), ("latched", "Latched"),
    ("no_region", "No region"), (None, ""), ("", ""),
])
def test_the_group_heading_says_the_hosted_models_mode(mode, word):
    assert qt.state_word({"mode": mode}) == word


def test_the_group_heading_is_drawn_from_the_existing_head_style():
    """The group's heading is the entry name's rule one step down (the
    closed entry's size) and the state word a caption: no new selector, so
    no colour or size the theme does not hold."""
    source = open(qt.__file__, encoding="utf-8").read()
    body = source.split("def _build_host_group", 1)[1].split("\n    def ", 1)[0]
    assert 'setObjectName("entryName")' in body
    assert '"opened", "false"' in body
    assert 'setObjectName("caption")' in body
    assert not re.search(r"#[0-9a-fA-F]{3,8}\b|\d+px|\d+pt", body)
