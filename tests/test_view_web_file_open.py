"""The photo prompt carries the instrument and magnification (CHECKPOINT
item 4, 2026-10-10): a `file_open` row that declares `inputs` draws a choice
for each beside its path box and sends them as the args after the path.
Headless Chrome against a real Sample DB; skipped where node or puppeteer is
absent."""
import re
from pathlib import Path

import pytest

import schema as sch
from controller.controller import Controller
from model.sample_map import SampleMap
from panel import Panel
from views.web.server import WebView

from test_view_web_client import APP_JS
from test_view_web_server import _browse, needs_browser

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16


class _Setup(Panel):
    NAME = "Setup"

    @property
    def schema(self):
        return sch.schema(sch.section("Hardware", sch.button("Refresh", "scan")))

    def scan(self):
        return None

    def start(self):
        pass


@pytest.fixture
def station(tmp_path):
    controller = Controller()
    samples = SampleMap(db_path=tmp_path / "sample_map.sqlite")
    controller.add("Sample DB", samples)
    samples.open()
    samples.run("begin_new_sample")
    samples.run("set_new_material", None, ("hBN",))
    assert samples.run("create_sample", {"new_sample_id": "S1"}).is_ok
    view = WebView(controller, _Setup(), port=0, open_browser=False)
    assert view.open(), "the server did not bind an ephemeral port"
    try:
        yield view, samples
    finally:
        view.close()
        samples.close()


def test_the_client_sends_the_prompts_choices_after_the_path():
    body = APP_JS[APP_JS.index("function renderFileOpen"):]
    body = body[:body.index("function renderPlot")]
    assert "fileOpenChoices(panel, element)" in body
    assert len(re.findall(r"choices\.values\(\)", body)) == 2, \
        "both the typed path and the chosen file send the choices"


@needs_browser
def test_the_photo_row_adds_with_the_prompts_instrument_and_magnification(
        station, tmp_path):
    view, samples = station
    photo = tmp_path / "plain.png"
    photo.write_bytes(PNG)
    out = _browse(view, r"""
      await page.evaluate(() => {
        const link = document.querySelector('.model-link[data-model="Sample DB"]');
        if (link) link.click();
      });
      await sleep(1500);
      const info = await page.evaluate((path) => {
        const rows = Array.from(document.querySelectorAll('.row.file-open'))
          .filter((r) => r.querySelector('select[name="image_magnification"]')
            && r.querySelector('button.role-go'));
        const row = rows[0];
        if (!row) return null;
        const pick = (name, value) => {
          const select = row.querySelector('select[name="' + name + '"]');
          return new Promise((done) => {
            const tick = () => {
              if (Array.from(select.options).some((o) => o.value === value)) {
                select.value = value;
                done(Array.from(select.options).map((o) => o.value));
              } else setTimeout(tick, 100);
            };
            tick();
          });
        };
        return Promise.all([pick('image_instrument', 'transfer_stage'),
                            pick('image_magnification', '50x')]).then(() => {
          const input = row.querySelector('.path-input');
          input.value = path;
          row.querySelector('button.file-open-key').click();
          return true;
        });
      }, %s);
      await sleep(1500);
      return { info };
    """ % __import__("json").dumps(str(photo)), tmp_path)
    assert out["info"] is True, out
    [row] = samples._store.images("S1")
    assert (row["instrument"], row["magnification"]) == ("transfer_stage", 50)
