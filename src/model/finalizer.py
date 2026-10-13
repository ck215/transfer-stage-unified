"""The Web data finalizer (C-11, 2026-10-10): the Qt window's walk, as a
model drawn on the Transfer Map's page.

One trial at a time, sample by sample: the trial's stills beside a form for
the AFM and optical estimates (AFM, optical, both, or neither), and the
transfer map's figure so the outcome moves as the entries fill in. It holds
no rules and no store of its own. The order, what a typed number may be and
what each save does are `model/finalize.py`'s and the Transfer Map's
`finalize_queue` / `finalize_media` / `finalize_save`; this model only walks
them and keeps the form, so the Web and the retired Qt window refuse the same
things in the same words.

It finds the Transfer Map the way the other models do, through
`on_model_added` (duck-typed on the finalize commands), and is launched with
it (`HOST`). It owns no device: its stop is nothing to halt.

The trial's video plays in the page when it is one MP4 file under the map's
output root (`video_src`, served with Range by the Web server's `/api/video`);
a folder of JPEG frames, or a file outside the root, is only named, with its
place, to be opened in the lab's player. The stills and the mark frame are
shown either way.
"""
from pathlib import PurePath
from urllib.parse import quote

import schema as sch
from events import events
from param import Param
from result import Refused

from model import finalize
from model.base import Model

#: form attribute -> trial column, in the order the form draws them.
IDENTITY_BOXES = (("f_sample_id", "sample_id", "Sample"), ("f_chip_id", "chip_id", "Chip"),
            ("f_flake_id", "flake_id", "Flake"), ("f_cut_id", "cut_id", "Cut"),
            ("f_note", "note", "Note"))
#: (value attr, uncertainty attr, label, unit, kind)
AFM = (("f_width_um", "f_width_sigma_um", "Channel width", "um"),
       ("f_thickness_nm", "f_thickness_sigma_nm", "Sample thickness", "nm"),
       ("f_channel_height_nm", "f_channel_height_sigma_nm", "Channel height", "nm"),
       ("f_trench_depth_nm", "f_trench_depth_sigma_nm", "Trench depth", "nm"))
OPTICAL = (("f_width_optical_um", "f_width_optical_sigma_um", "Channel width", "um"),)
METHODS = ("estimate", "reticle", "capture_px", "vendor_tool")
#: (min attr, max attr, label, unit): a channel that varies along the cut
#: (owner 2026-10-13), drawn under its value's row.
RANGES = (("f_width_min_um", "f_width_max_um", "Channel width (AFM)", "um"),
          ("f_channel_height_min_nm", "f_channel_height_max_nm", "Channel height (AFM)", "nm"),
          ("f_width_optical_min_um", "f_width_optical_max_um", "Channel width (optical)", "um"))

_COLUMN = {attr: column for attr, column, _label in IDENTITY_BOXES}
for _value, _sigma, _label, _unit in AFM + OPTICAL:
    _COLUMN[_value] = _value[2:]
    _COLUMN[_sigma] = _sigma[2:]
for _low, _high, _label, _unit in RANGES:
    _COLUMN[_low] = _low[2:]
    _COLUMN[_high] = _high[2:]
_COLUMN["f_method"] = "width_optical_method"
#: Every box of the form, in schema order: what Save declares as its inputs.
FORM = tuple(_COLUMN)
STILLS = (("first_frame", "First frame"), ("mark_frame", "Mark frame"),
          ("before_full", "Whole screen"))


def _text(value):
    return "" if value is None else f"{value:g}" if isinstance(value, float) else str(value)


class Finalizer(Model):
    NAME = "Finalize Data"
    HOST = "Transfer Map"
    IDENTITY = None
    NEEDS_PORT = False
    NEEDS_GAMEPAD = False
    STORE = "Transfer Map"
    #: Temporary (owner, Ian, 2026-10-13): walk only this experiment's trials
    #: (`finalize.EXPERIMENT`, the 7° speed x force series); None walks all.
    EXPERIMENT = finalize.EXPERIMENT
    #: The one disclosure the whole form sits behind on the map's page, named
    #: for the experiment while one is set.
    DISCLOSURE = (f"Finalize data ({EXPERIMENT['label']})" if EXPERIMENT
                  else "Finalize data")

    PARAMS = {p.name: p for p in (
        *(Param(attr, "text", default="", label=label)
          for attr, _column, label in IDENTITY_BOXES),
        *(Param(attr, "text", default="", label=f"{label} ({unit})" if i == 0 else "Uncertainty")
          for value, sigma, label, unit in AFM + OPTICAL
          for i, attr in enumerate((value, sigma))),
        *(Param(attr, "text", default="", label=f"{label} {end} ({unit})")
          for low, high, label, unit in RANGES
          for end, attr in (("min", low), ("max", high))),
    )}

    def __init__(self, sim=False):
        super().__init__()
        self._map = None
        self._map_name = ""
        self._only_missing = True
        self._queue = []
        self._index = -1
        self._media = None
        self._loaded = {}            # form attr -> the text as loaded
        self._method = ""
        self._status = ""
        self._revision = 0
        self.sim = sim

    # -- the Transfer Map ----------------------------------------------------
    def on_model_added(self, name, model):
        if model is not self and callable(getattr(model, "finalize_queue", None)):
            self._map, self._map_name = model, name
            self._reload()

    def on_model_removed(self, name, model=None):
        if model is self._map or (self._map is not None and name == self.STORE):
            self._map = None
            self._queue, self._index, self._media = [], -1, None
            self._clear_form()

    def _need_map(self):
        if self._map is None:
            raise Refused("Open the Transfer Map first: the finalizer walks "
                          "its recorded trials.")
        return self._map

    def _expects_heartbeat(self):
        return False

    # -- the form ------------------------------------------------------------
    def _clear_form(self):
        self._loaded = {}
        for attr in _COLUMN:
            if attr == "f_method":
                self._method = ""
            else:
                setattr(self, attr, "")
        self._loaded = {attr: "" for attr in _COLUMN}

    def _load_form(self, row):
        self._loaded = {}
        for attr, column in _COLUMN.items():
            text = _text(row.get(column))
            if attr == "f_method":
                self._method = text
            else:
                setattr(self, attr, text)
            self._loaded[attr] = text

    @property
    def f_method(self):
        return self._method

    def set_method(self, method):
        if method not in ("",) + METHODS:
            raise Refused(f"{method!r} is not a method: use {', '.join(METHODS)}.")
        self._method = method
        self._touch()
        return method

    @property
    def f_method_options(self):
        return list(METHODS)

    def _typed(self):
        """What the form changed or newly holds: a box only when it differs
        from what was loaded (a blank number box is "leave it", which the
        Transfer Map's save drops)."""
        out = {}
        for attr, column in _COLUMN.items():
            text = (self._method if attr == "f_method"
                    else str(getattr(self, attr, "") or "")).strip()
            if text != self._loaded.get(attr, ""):
                out[column] = text
        return out

    # -- the walk ------------------------------------------------------------
    @property
    def only_missing(self):
        return self._only_missing

    def set_only_missing(self, on):
        self._only_missing = bool(on)
        self._reload(keep=True)
        return self._only_missing

    def _reload(self, keep=False):
        """Fetch the queue again (a save changes what is unfilled). `keep`
        stays on the trial being shown when it is still listed, else on the
        place it held."""
        if self._map is None:
            return
        shown = self._queue[self._index]["id"] if 0 <= self._index < len(self._queue) else None
        place = max(self._index, 0)
        queue = self._map.finalize_queue(self._only_missing,
                                         experiment=self.EXPERIMENT)
        self._queue = queue
        ids = [t["id"] for t in queue]
        if keep and shown in ids:
            self._index = ids.index(shown)
        else:
            self._index = min(place, len(queue) - 1) if queue else -1
        self._show()

    def reload(self):
        """Walk the trials again from where the list stands."""
        self._need_map()
        self._reload(keep=True)
        return len(self._queue)

    def _show(self):
        self._revision += 1
        if not 0 <= self._index < len(self._queue):
            self._media = None
            self._clear_form()
            self._touch()
            return
        entry = self._queue[self._index]
        try:
            self._media = self._map.finalize_media(entry["id"])
        except Refused as refusal:
            self._media = None
            self._status = refusal.reason
            self._clear_form()
            self._touch()
            return
        self._load_form(self._media["row"])
        self._status = ""
        self._touch()

    @property
    def _entry(self):
        return self._queue[self._index] if 0 <= self._index < len(self._queue) else None

    def previous(self):
        self._need_map()
        if self._index > 0:
            self._index -= 1
            self._show()
        return self._index

    def next(self):
        self._need_map()
        if self._index + 1 < len(self._queue):
            self._index += 1
            self._show()
        else:
            self._status = "That was the last trial in the list."
            self._touch()
        return self._index

    def next_sample(self):
        """Jump to the first trial of the next sample."""
        self._need_map()
        if self._index < 0:
            return self._index
        sample = self._queue[self._index]["sample_id"]
        for i in range(self._index + 1, len(self._queue)):
            if self._queue[i]["sample_id"] != sample:
                self._index = i
                self._show()
                return i
        self._status = "That was the last sample."
        self._touch()
        return self._index

    # -- saving --------------------------------------------------------------
    def save(self):
        """Write what the form changed to the trial shown: AFM, optical, both
        or neither. All or nothing; the Transfer Map's refusal is passed on
        word for word."""
        mapping = self._need_map()
        if self._media is None:
            raise Refused("There is no trial shown to save.")
        saved = mapping.finalize_save(self._media["id"], self._typed())
        self._status = f"Trial {saved['id']} saved."
        self._reload(keep=True)
        return saved["id"]

    def save_and_next(self):
        self._need_map()
        shown = self._entry["id"] if self._entry else None
        self.save()
        ids = [t["id"] for t in self._queue]
        if shown in ids:                 # still listed: step past it
            self.next()
        else:                            # filled in: the list closed over it
            self._show()
        return shown

    # -- what the page shows -------------------------------------------------
    @property
    def has_trial(self):
        return self._entry is not None

    @property
    def title_text(self):
        entry = self._entry
        if self._map is None:
            return "Open the Transfer Map to finalize its trials"
        if entry is None:
            return "Nothing to finalize"
        return (f"Sample {entry['sample_id'] or '(none)'}  -  chip "
                f"{entry['chip_id'] or '?'}, flake {entry['flake_id'] or '?'}, "
                f"cut {entry['cut_id'] or '?'}  -  trial {entry['id']}  "
                f"({entry['sample_position']} of {entry['sample_count']} in this sample)")

    @property
    def summary_text(self):
        entry, media = self._entry, self._media
        if entry is None or media is None:
            return ("Every trial in the list has its properties filled, or "
                    "none has been recorded.")
        row = media["row"]
        head = (f"Tip {row.get('tip_id') or '?'}   tilt {_text(row.get('tilt_deg'))} deg   "
                f"speed {_text(row.get('speed_steps_s'))} steps/s   "
                f"status {row.get('status')}" + ("   INVALID" if row.get("invalid") else ""))
        tail = ("Unfilled: " + ", ".join(entry["missing_labels"]) if entry["missing"]
                else "Every property has a value.")
        return head + "\n" + tail

    @property
    def video_text(self):
        media = self._media
        if media is None:
            return ""
        kind = media["video_kind"]
        if kind == "file":
            return f"{media['frames']} frames, in {media['video_path']}"
        if kind == "frames":
            return (f"{len(media['frame_paths'])} JPEG frames in {media['video_path']} "
                    "(no encoder was installed when this was recorded)")
        return "This trial has no video."

    @property
    def video_src(self):
        """The server route that plays this trial's MP4, or "" when it has
        none the page can play (no file, a folder of frames, a file outside
        the map's output root). The path is relative to that root; the
        server checks it again."""
        media, mapping = self._media, self._map
        root = getattr(mapping, "output_root", None)
        if not media or media["video_kind"] != "file" or root is None:
            return ""
        try:
            relative = PurePath(media["video_path"]).relative_to(root).as_posix()
        except ValueError:
            return ""
        return f"/api/video?name={quote(self._map_name)}&path={quote(relative)}"

    @property
    def state(self):
        snapshot = super().state
        snapshot["values"]["video_src"] = self.video_src
        return snapshot

    @property
    def status_text(self):
        return self._status

    @property
    def still_key(self):
        entry = self._entry
        return f"{entry['id']}:{self._revision}" if entry else ""

    def _still(self, name):
        return (self._media or {}).get(name) or b""

    @property
    def first_frame_png(self):
        return self._still("first_frame")

    @property
    def mark_frame_png(self):
        return self._still("mark_frame")

    @property
    def before_full_png(self):
        return self._still("before_full")

    @property
    def outcome_key(self):
        mapping = self._map
        return "" if mapping is None else f"{self._revision}:{mapping.figure_type}"

    @property
    def outcome_figure(self):
        mapping = self._map
        if mapping is None:
            return b""
        try:
            return mapping.figure or b""
        except Exception as exc:         # a figure that cannot draw is no picture
            events.debug("Finalizer Figure", repr(exc), source=self.NAME, exception=exc)
            return b""

    @property
    def figure_type(self):
        return "" if self._map is None else self._map.figure_type

    @property
    def figure_type_options(self):
        return [] if self._map is None else self._map.figure_type_options

    def set_figure_type(self, label):
        result = self._need_map().set_figure_type(label)
        self._touch()
        return result

    # -- schema --------------------------------------------------------------
    @property
    def schema(self):
        P = self.PARAMS
        form = [a for a in FORM if a != "f_method"]
        entries = [sch.entry(label, attr, P[attr]) for attr, _c, label in IDENTITY_BOXES]
        grid = []
        ranges = {low[:-3].replace("_min", "") + low[-3:]: (low, high, label, unit)
                  for low, high, label, unit in RANGES}
        for rows in (AFM, OPTICAL):
            for value, sigma, label, unit in rows:
                grid.append(sch.entry(f"{label} ({unit})", value, P[value]))
                grid.append(sch.entry(f"{label} uncertainty", sigma, P[sigma]))
                if value in ranges:
                    low, high, _l, _u = ranges[value]
                    grid.append(sch.entry(f"{label} min ({unit})", low, P[low]))
                    grid.append(sch.entry(f"{label} max ({unit})", high, P[high]))
        return sch.schema(
            sch.section(
                self.DISCLOSURE,
                sch.readonly("Trial", "title_text", role="info"),
                sch.readonly("Summary", "summary_text"),
                sch.checkbox("Only trials with something unfilled", "only_missing",
                             "set_only_missing"),
                sch.button("Previous", "previous"),
                sch.button("Next", "next"),
                sch.button("Next sample", "next_sample"),
                sch.readonly("Status", "status_text"),
                tier=2, disclosure=self.DISCLOSURE,
            ),
            sch.section(
                "Identity", *entries, tier=2, disclosure=self.DISCLOSURE,
            ),
            sch.section(
                "AFM and optical estimates", *grid,
                sch.dropdown("Optical method", "f_method", "set_method",
                             "f_method_options"),
                sch.button("Save", "save", inputs=form, role="go"),
                sch.button("Save and next", "save_and_next", inputs=form),
                tier=2, disclosure=self.DISCLOSURE,
            ),
            sch.section(
                "Pictures",
                *(sch.image(label, f"{name}_png", model_attr="still_key",
                            empty="No picture was taken.")
                  for name, label in STILLS),
                sch.video(sch.readonly("Video", "video_text"), "video_src"),
                tier=2, disclosure=self.DISCLOSURE,
            ),
            sch.section(
                "Outcome so far",
                sch.dropdown("Figure", "figure_type", "set_figure_type",
                             "figure_type_options"),
                sch.image("Outcome", "outcome_figure", model_attr="outcome_key",
                          empty="Nothing to plot yet."),
                tier=2, disclosure=self.DISCLOSURE,
            ),
            self._safety_section())
