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
    monkeypatch.setattr(tm_module, "_install_root", lambda: install)
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
