"""UX audit 2026-10-08 (docs/ux-audit-2026-10-08.md): the proposals.

- #10: a warning whose condition has cleared leaves the tray (a store chosen
  after "Store Not Chosen"): the event that ends it `resolves` it.
- #11: a prompt's title that names the current pick ("New flake on S-001 ·
  C1") is the model's now, not the one the page fetched at the launch.

Headless Chrome through the harness in test_view_web_server.py (skipped
where node or puppeteer is absent).
"""
import pytest

import schema as sch
from controller.controller import Controller
from events import events
from param import Param
from views.web.server import WebView

from test_view_web_client import NODE, _node_value
from test_view_web_procedure import FakeProc, _READY
from test_view_web_server import FakeSetup, _browse, needs_browser


# ------------------------------------------------------------- #10 the tray
def test_an_info_that_resolves_a_warning_says_which():
    seen = []
    events.subscribe(seen.append)
    try:
        events.warn(events.SAMPLE_STORE_NOT_CHOSEN, "pick one", source="Sample DB")
        done = events.info("Sample Store", "Samples go to x", source="Sample DB",
                           resolves=events.SAMPLE_STORE_NOT_CHOSEN)
        # The condition back again is a new warning, not a fold into the old.
        again = events.warn(events.SAMPLE_STORE_NOT_CHOSEN, "pick one",
                            source="Sample DB")
    finally:
        events.unsubscribe(seen.append)
        events.clear()
    assert done.to_dict()["resolves"] == "Sample Store Not Chosen"
    assert [e.title for e in seen].count("Sample Store Not Chosen") == 2
    assert again.count == 1


def test_the_transfer_maps_store_choice_resolves_its_warning(tmp_path, monkeypatch):
    from controller import user_config
    from model import transfer_map as tm_module
    from model.transfer_map import TransferMap
    monkeypatch.delenv("STATION_MAP_DB", raising=False)
    monkeypatch.setenv("STATION_CONFIG", str(tmp_path / "choices" / "station.json"))
    user_config.forget()
    monkeypatch.setattr(TransferMap, "choices", user_config)
    install = tmp_path / "install"
    (install / "src").mkdir(parents=True)
    from model import store_choice
    monkeypatch.setattr(store_choice, "install_root", lambda: install)
    seen = []
    events.subscribe(seen.append)
    try:
        model = TransferMap()
        model.open()
        result = model.run("new_store", {"store_dir": str(tmp_path / "d"),
                                         "store_name": "t"})
        model.close()
    finally:
        events.unsubscribe(seen.append)
        events.clear()
        user_config.forget()
    assert result.is_ok, result
    [warned] = [e for e in seen if e.title == events.TRIAL_STORE_NOT_CHOSEN]
    [ended] = [e for e in seen if e.resolves == events.TRIAL_STORE_NOT_CHOSEN]
    assert ended.source == warned.source == "Transfer Map"


class StoreProc(FakeProc):
    """Warns that no store is chosen, then chooses one, as the maps do."""

    @property
    def schema(self):
        base = super().schema
        base["sections"].append(sch.section(
            "Store", sch.button("Warn", "warn_store"),
            sch.button("Choose", "choose_store")))
        return base

    def warn_store(self):
        events.warn(events.SAMPLE_STORE_NOT_CHOSEN, "Choose a store.",
                    source=self.NAME)

    def choose_store(self):
        events.info("Sample Store", "Samples go to /x.", source=self.NAME,
                    resolves=events.SAMPLE_STORE_NOT_CHOSEN)


@pytest.fixture
def store_station():
    controller = Controller()
    model = StoreProc()
    controller.add("Fake Proc", model, {"kind": "Fake Proc"})
    view = WebView(controller, FakeSetup(), port=0, open_browser=False)
    assert view.open(), "the server did not bind an ephemeral port"
    try:
        yield view, model
    finally:
        view.close()
        events.clear()


@needs_browser
def test_the_tray_drops_a_store_warning_once_a_store_is_chosen(store_station, tmp_path):
    view, _model = store_station
    out = _browse(view, _READY + r"""
      const run = (c) => api('/api/run', { name: 'Fake Proc', command: c, inputs: {}, args: [] });
      const tray = () => page.evaluate(() => document.querySelector('#tray-latest .tray-text').textContent);
      await run('warn_store');
      await when(async () => (await tray()).toLowerCase().indexOf('store not chosen') !== -1);
      const warned = await tray();
      await run('choose_store');
      await sleep(1800);
      return { warned, after: await tray() };
    """, tmp_path)
    assert "store not chosen" in out["warned"].lower(), out
    assert "store not chosen" not in out["after"].lower(), out


# ------------------------------------------------- #11 a title that tracks
class TitledProc(FakeProc):
    """A prompt whose title names the current pick, like the Sample DB's
    "New flake on S-001 · C1"."""
    PARAMS = {"tip_name": Param("tip_name", "text", default="", label="Name")}

    @property
    def schema(self):
        base = super().schema
        for section in base["sections"]:
            if section["title"] == "New tip":
                section["title"] = f"New tip on {self.chip or '?'}"
        return base

    @property
    def state(self):
        snapshot = super().state
        snapshot["section_titles"] = [s["title"] for s in self.schema["sections"]]
        return snapshot


def test_the_sample_dbs_state_carries_its_titles_of_now(tmp_path):
    from model.sample_map import SampleMap
    model = SampleMap(db_path=tmp_path / "s" / "sample_map.sqlite")
    model.open()
    try:
        model.run("begin_new_sample")
        model.run("set_new_material", None, ("hBN",))
        assert model.run("create_sample", {"new_sample_id": "S-001"}).is_ok
        model.run("begin_new_chip")
        assert model.run("create_chip", {"new_chip_id": "C1"}).is_ok
        model.run("begin_new_flake")
        titles = model.state["section_titles"]
    finally:
        model.close()
    assert titles == [s["title"] for s in model.schema["sections"]]
    assert "New flake on S-001 · C1" in titles


@pytest.fixture
def titled_station():
    controller = Controller()
    model = TitledProc()
    controller.add("Fake Proc", model, {"kind": "Fake Proc"})
    view = WebView(controller, FakeSetup(), port=0, open_browser=False)
    assert view.open(), "the server did not bind an ephemeral port"
    try:
        yield view, model
    finally:
        view.close()


@needs_browser
def test_a_prompts_title_names_the_pick_made_after_the_page_loaded(titled_station, tmp_path):
    view, _model = titled_station
    out = _browse(view, _READY + r"""
      await page.evaluate(() => window.station.showPage('Fake Proc'));
      await sleep(400);
      await api('/api/run', { name: 'Fake Proc', command: 'pick_chip', inputs: {}, args: ['A2'] });
      await api('/api/run', { name: 'Fake Proc', command: 'go', inputs: {}, args: ['new_tip'] });
      await until(() => Boolean(document.querySelector('.card-dialog')));
      await sleep(1200);
      return page.evaluate(() => {
        const box = document.querySelector('.card-dialog .dialog-box');
        const head = box.querySelector('.section-title');
        return { title: head ? head.textContent : '', label: box.getAttribute('aria-label') };
      });
    """, tmp_path)
    assert "A2" in out["title"] and "?" not in out["title"], out
    assert "A2" in (out["label"] or ""), out


# ----------------------------------- #15 one sentence once; #16 prompt steps
class EchoProc(FakeProc):
    """A "Next step" readout that says what the strip says, like the
    Transfer Map's."""

    @property
    def schema(self):
        base = super().schema
        base["sections"].insert(0, sch.section(
            "Trial", sch.readonly("Next step", "next_words", role="info")))
        return base

    @property
    def state(self):
        snapshot = super().state
        snapshot["values"]["next_words"] = self.step_text
        return snapshot


@pytest.fixture
def echo_station():
    controller = Controller()
    model = EchoProc()
    controller.add("Fake Proc", model, {"kind": "Fake Proc"})
    view = WebView(controller, FakeSetup(), port=0, open_browser=False)
    assert view.open(), "the server did not bind an ephemeral port"
    try:
        yield view, model
    finally:
        view.close()


_SEEN = r"""() => {
  const card = Array.from(document.querySelectorAll('#cards .card'))
    .find((c) => c.querySelector('.proc-steps'));
  const shown = (n) => Boolean(n && n.getClientRects().length);
  return {
    sentences: Array.from(card.querySelectorAll('*')).filter((n) => shown(n)
      && n.children.length === 0 && n.textContent.trim() === 'Set a region.').length,
    steps: Array.from(card.querySelectorAll('.proc-step')).filter(shown)
      .map((n) => n.dataset.step),
  };
}"""


@needs_browser
def test_the_next_step_sentence_is_said_once_and_prompts_are_not_steps(echo_station, tmp_path):
    view, _model = echo_station
    out = _browse(view, _READY + r"""
      await page.evaluate(() => window.station.showPage('Fake Proc'));
      await sleep(600);
      const before = await page.evaluate(%s);
      await api('/api/run', { name: 'Fake Proc', command: 'go', inputs: {}, args: ['new_tip'] });
      await until(() => Boolean(document.querySelector('.card-dialog')));
      await sleep(600);
      const asking = await page.evaluate(%s);
      return { before, asking };
    """ % (_SEEN, _SEEN), tmp_path)
    assert out["before"]["sentences"] == 1, out  # 2 before the fix
    # The strip lists the procedure; a prompt shows only while it is asked.
    assert out["before"]["steps"] == ["setup", "live"], out
    assert "new_tip" in out["asking"]["steps"], out


# --------------------------------------------- #17 folder choices that differ
@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_folder_choices_show_the_tail_that_tells_them_apart():
    """The store prompt's Choose folder… list read "/home/tran…op@lab.test",
    "/home/tran…runs/stores", "/home/tran…-stage-user": the head they all
    share, then a cut. A long path shows its last segments after "…/"; the
    whole path is the option's title (loadOptions)."""
    shown = [_node_value(f"optionText({path!r}, undefined)") for path in (
        "/home/transfer-stage-user/transfer-stage-runs/stores/op@lab.test",
        "/home/transfer-stage-user/transfer-stage-runs/stores",
        "/home/transfer-stage-user",
        "/home/transfer-stage-user/transfer-stage-runs/stores/ialbinog@uci.edu")]
    assert shown == ["…/stores/op@lab.test", "…/transfer-stage-runs/stores",
                     "/home/tran…-stage-user", "…/stores/ialbinog@uci.edu"], shown
    assert len(set(shown)) == len(shown)
    # A port stays as F15 settled it.
    assert _node_value("optionText('/dev/cu.usbmodem1234567890123', undefined)") \
        == "/dev/cu.us…34567890123"
    assert _node_value("optionText('C:\\\\Users\\\\lab\\\\Documents\\\\stores\\\\a@b.c', undefined)") \
        == "…\\stores\\a@b.c"
