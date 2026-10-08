"""UX audit 2026-10-08 (docs/ux-audit-2026-10-08.md): what stayed open.

- #15: the Transfer Map's Tilt and Speed readings beside their entries.
- #17: the store prompt's Folder entry, too narrow for a path.
- Return in a photo-path entry (a `file_open` in a prompt) did nothing.
- #19: phone-width Settings rows and the take-over key.
- 2026-10-07 #5: status dots told apart by colour alone, at low contrast.

Headless Chrome through the harness in test_view_web_server.py (skipped
where node or puppeteer is absent).
"""
import pytest

import schema as sch
from controller.controller import Controller
from param import Param
from views.web.server import WebView

from test_view_web_procedure import FakeProc, _READY, _go
from test_view_web_server import FakeSetup, _browse, needs_browser


# ------------------------------------------ Return in a photo-path entry
class PhotoProc(FakeProc):
    """A prompt with a photo path beside the key that adds the record, like
    the Sample DB's New flake."""
    PARAMS = {"flake_id": Param("flake_id", "text", default="", label="Flake ID")}

    def __init__(self):
        super().__init__()
        self.flake_id = ""
        self.staged = []
        self.flakes = []

    @property
    def schema(self):
        return sch.schema(
            sch.section("Controls", sch.button("Ask", "go", args=("new_tip",))),
            sch.section("New tip",
                        sch.entry("Flake ID", "flake_id", self.PARAMS["flake_id"]),
                        sch.file_open("Add photo…", "stage_photo",
                                      extensions=("png",),
                                      placeholder="Path to a saved microscope image"),
                        sch.button("Add flake", "create_flake", role="go",
                                   inputs=("flake_id",)),
                        sch.button("Cancel", "cancel"),
                        phases=("new_tip",)))

    @property
    def state(self):
        snapshot = super().state
        snapshot["values"]["flake_id"] = self.flake_id
        return snapshot

    def stage_photo(self, path):
        self.staged.append(path)

    def create_flake(self):
        self.flakes.append(self.flake_id)
        self._phase = "setup"


@pytest.fixture
def photo_station():
    controller = Controller()
    model = PhotoProc()
    controller.add("Fake Proc", model, {"kind": "Fake Proc"})
    view = WebView(controller, FakeSetup(), port=0, open_browser=False)
    assert view.open(), "the server did not bind an ephemeral port"
    try:
        yield view, model
    finally:
        view.close()


@needs_browser
def test_return_in_a_prompts_photo_path_presses_its_add_photo_key(photo_station, tmp_path):
    """The prompt's Return handler swallowed the path box's own Return and
    found no key for it, so nothing happened. Return now presses the key
    that takes the path (Add photo…); the prompt stays open for the rest."""
    view, model = photo_station
    out = _browse(view, _READY + r"""
      await page.evaluate(() => window.station.showPage('Fake Proc'));
      await sleep(400);
      %s
      const open = () => page.evaluate(() => Boolean(document.querySelector('.card-dialog')));
      await page.focus('.card-dialog .file-open input.path-input');
      await page.keyboard.type('/tmp/flake_100x.png');
      await page.keyboard.press('Enter');
      await sleep(1500);
      return { open: await open() };
    """ % _go("new_tip"), tmp_path)
    assert model.staged == ["/tmp/flake_100x.png"], "Return in the photo path did nothing"
    assert model.flakes == [], "Return in the photo path added the flake"
    assert out["open"] is True, out


# ------------------------------------------------ #17 the Folder entry's width
LONG_FOLDER = "/home/transfer-stage-user/transfer-stage-runs/stores/op@lab.test"


class FolderProc(FakeProc):
    """The store prompt's shape: a sentence, a Folder, a Name, the keys."""
    PARAMS = {"store_dir": Param("store_dir", "text", default="", label="Folder"),
              "store_name": Param("store_name", "text", default="t", label="Name")}

    def __init__(self):
        super().__init__()
        self.store_dir = LONG_FOLDER
        self.store_name = "transfer_map"

    @property
    def schema(self):
        return sch.schema(
            sch.section("Controls", sch.button("Ask", "go", args=("new_tip",))),
            sch.section("Where to save the store",
                        sch.readonly("Store", "status_words", role="info"),
                        sch.entry("Folder", "store_dir", self.PARAMS["store_dir"]),
                        sch.entry("Name", "store_name", self.PARAMS["store_name"]),
                        sch.button("New store", "add", role="go",
                                   inputs=("store_dir", "store_name")),
                        sch.button("Cancel", "cancel"),
                        phases=("new_tip",)))

    @property
    def state(self):
        snapshot = super().state
        snapshot["values"].update({"store_dir": self.store_dir,
                                   "store_name": self.store_name,
                                   "status_words": "Not chosen."})
        return snapshot


@pytest.fixture
def folder_station():
    controller = Controller()
    model = FolderProc()
    controller.add("Fake Proc", model, {"kind": "Fake Proc"})
    view = WebView(controller, FakeSetup(), port=0, open_browser=False)
    assert view.open(), "the server did not bind an ephemeral port"
    try:
        yield view, model
    finally:
        view.close()


_FOLDER = r"""() => {
  const input = document.querySelector('.card-dialog input[name="store_dir"]');
  const box = input.closest('.dialog-box');
  const style = getComputedStyle(box);
  const inner = box.clientWidth - parseFloat(style.paddingLeft) - parseFloat(style.paddingRight);
  return { input: input.getBoundingClientRect().width, inner,
           fits: input.scrollWidth <= input.clientWidth + 1,
           sideways: document.documentElement.scrollWidth - window.innerWidth,
           value: input.value };
}"""


@needs_browser
def test_a_prompts_folder_entry_runs_the_box_and_shows_a_whole_path(folder_station, tmp_path):
    """The store prompt's Folder showed "/home/transfer-sta" in a 9rem box.
    A text entry in a prompt runs the prompt's width, which is wide enough
    for a store path; at phone width it still fits the screen."""
    view, _model = folder_station
    out = _browse(view, _READY + r"""
      await page.evaluate(() => window.station.showPage('Fake Proc'));
      await sleep(400);
      %s
      await until(() => Boolean(document.querySelector('.card-dialog input[name="store_dir"]')));
      await sleep(600);
      const wide = await page.evaluate(%s);
      await page.setViewport({ width: 390, height: 844 });
      await sleep(800);
      const phone = await page.evaluate(%s);
      return { wide, phone };
    """ % (_go("new_tip"), _FOLDER, _FOLDER), tmp_path)
    wide, phone = out["wide"], out["phone"]
    assert wide["value"] == LONG_FOLDER, out
    assert wide["fits"], out                       # 144 px and cut before
    assert wide["input"] >= wide["inner"] - 2, out
    assert phone["input"] >= phone["inner"] - 2, out
    assert phone["sideways"] <= 0, out
