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
