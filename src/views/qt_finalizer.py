"""The data finalizer's window (Qt), opened by the Transfer Map's
"Finalize data..." button.

Temporary by the owner's word (2026-10-06): it lives in the default view for
now and is standardised later. It walks the trials one sample at a time with
each trial's video and pictures beside a form for the AFM and optical
estimates (AFM, optical, both, or neither), and shows the transfer map so the
outcome moves as the entries fill in.

It reaches the station only through `controller.run` on the Transfer Map's
`finalize_*` commands; the rules (the order, what a typed number may be)
are `model/finalize.py`'s, never repeated here.
"""
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFormLayout, QGridLayout, QGroupBox,
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QSlider, QTabWidget,
    QVBoxLayout, QWidget)

try:                                    # the player is optional: a station
    from PySide6.QtMultimedia import QMediaPlayer     # without a multimedia
    from PySide6.QtMultimediaWidgets import QVideoWidget    # backend still works
except ImportError:                     # pragma: no cover - platform dependent
    QMediaPlayer = QVideoWidget = None

import events

SOURCE = "Finalizer"

#: (column, label) of each box, by group. The uncertainty sits beside its value.
AFM_FIELDS = (
    ("width_um", "width_sigma_um", "Channel width (um)"),
    ("thickness_nm", "thickness_sigma_nm", "Sample thickness (nm)"),
    ("channel_height_nm", "channel_height_sigma_nm", "Channel height (nm)"),
    ("trench_depth_nm", "trench_depth_sigma_nm", "Trench depth (nm)"),
)
OPTICAL_FIELDS = (("width_optical_um", "width_optical_sigma_um",
                   "Channel width (um)"),)
IDENTITY_FIELDS = (("sample_id", "Sample"), ("chip_id", "Chip"),
                   ("flake_id", "Flake"), ("cut_id", "Cut"), ("note", "Note"))
RATES = (("0.25x", 0.25), ("0.5x", 0.5), ("1x", 1.0), ("2x", 2.0))
STILLS = (("first_frame", "First frame"), ("mark_frame", "Mark frame"),
          ("before_full", "Whole screen"))


def _text(value):
    return "" if value is None else f"{value:g}" if isinstance(value, float) else str(value)


class FinalizerWindow(QDialog):
    """One window, not modal: the station keeps running behind it."""

    def __init__(self, controller, model_name, parent=None):
        super().__init__(parent)
        self.controller, self.model_name = controller, model_name
        self.setWindowTitle("Finalize data")
        self.setWindowFlag(Qt.WindowType.WindowMaximizeButtonHint, True)
        self.resize(1280, 820)
        self._queue, self._index, self._media, self._player = [], -1, None, None
        self._fields = {}                    # column -> QLineEdit
        self._loaded = {}                    # column -> the text as loaded
        self._frames = []
        self._build()
        QShortcut(QKeySequence("Ctrl+Return"), self, activated=self.save_and_next)
        self.reload()

    # -- the station ---------------------------------------------------------
    def _run(self, command, *args):
        result = self.controller.run(self.model_name, command, None, tuple(args))
        if not result.is_ok:
            self._say(result.reason or "That did not complete.", bad=True)
            return None
        return result.value

    def _say(self, text, bad=False):
        self.status.setStyleSheet("color: #b3261e;" if bad else "")
        self.status.setText(text)

    # -- layout --------------------------------------------------------------
    def _build(self):
        root = QVBoxLayout(self)
        bar = QHBoxLayout()
        self.title = QLabel()
        self.title.setStyleSheet("font-size: 16px; font-weight: 600;")
        bar.addWidget(self.title, 1)
        self.only_missing = QCheckBox("Only trials with something unfilled")
        self.only_missing.setChecked(True)
        self.only_missing.toggled.connect(lambda _on: self.reload(keep=True))
        bar.addWidget(self.only_missing)
        for text, slot in (("Previous", self.previous), ("Next", self.next),
                           ("Next sample", self.next_sample)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            bar.addWidget(button)
        root.addLayout(bar)

        body = QHBoxLayout()
        root.addLayout(body, 1)
        body.addLayout(self._media_column(), 5)
        body.addLayout(self._form_column(), 3)
        body.addLayout(self._outcome_column(), 4)

        self.status = QLabel()
        self.status.setWordWrap(True)
        root.addWidget(self.status)

    def _media_column(self):
        column = QVBoxLayout()
        self.video_box = QWidget()
        box = QVBoxLayout(self.video_box)
        box.setContentsMargins(0, 0, 0, 0)
        if QMediaPlayer is not None:
            self.video = QVideoWidget()
            self.video.setMinimumSize(480, 300)
            self._player = QMediaPlayer(self)
            self._player.setVideoOutput(self.video)
            self._player.errorOccurred.connect(self._player_error)
            self._player.positionChanged.connect(self._player_position)
            self._player.durationChanged.connect(
                lambda ms: self.scrub.setRange(0, max(ms, 0)))
            box.addWidget(self.video, 1)
        else:
            self.video = None
        self.frame_label = QLabel("")
        self.frame_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.frame_label.setMinimumSize(480, 300)
        self.frame_label.hide()
        box.addWidget(self.frame_label, 1)
        controls = QHBoxLayout()
        self.play = QPushButton("Play")
        self.play.clicked.connect(self.toggle_play)
        self.scrub = QSlider(Qt.Orientation.Horizontal)
        self.scrub.sliderMoved.connect(self._scrub_to)
        self.rate = QComboBox()
        for label, _value in RATES:
            self.rate.addItem(label)
        self.rate.setCurrentIndex(2)
        self.rate.currentIndexChanged.connect(self._set_rate)
        self.external = QPushButton("Open in video player")
        self.external.clicked.connect(self._open_external)
        for widget in (self.play, self.scrub, self.rate, self.external):
            controls.addWidget(widget, 1 if widget is self.scrub else 0)
        box.addLayout(controls)
        self.video_note = QLabel("")
        self.video_note.setWordWrap(True)
        box.addWidget(self.video_note)
        column.addWidget(self.video_box, 3)

        self.stills = QTabWidget()
        self.still_labels = {}
        for key, label in STILLS:
            holder = QLabel("")
            holder.setAlignment(Qt.AlignmentFlag.AlignCenter)
            holder.setMinimumHeight(200)
            self.still_labels[key] = holder
            self.stills.addTab(holder, label)
        column.addWidget(self.stills, 2)
        return column

    def _form_column(self):
        column = QVBoxLayout()
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        column.addWidget(self.summary)

        identity = QGroupBox("Identity")
        form = QFormLayout(identity)
        for name, label in IDENTITY_FIELDS:
            self._fields[name] = edit = QLineEdit()
            form.addRow(label, edit)
        column.addWidget(identity)

        for title, rows in (("AFM", AFM_FIELDS), ("Optical (by eye or reticle)",
                                                  OPTICAL_FIELDS)):
            group = QGroupBox(title)
            grid = QGridLayout(group)
            grid.addWidget(QLabel(""), 0, 0)
            grid.addWidget(QLabel("Value"), 0, 1)
            grid.addWidget(QLabel("Uncertainty"), 0, 2)
            for r, (value, sigma, label) in enumerate(rows, start=1):
                grid.addWidget(QLabel(label), r, 0)
                for c, name in enumerate((value, sigma), start=1):
                    self._fields[name] = edit = QLineEdit()
                    edit.setPlaceholderText("unfilled" if c == 1 else "")
                    grid.addWidget(edit, r, c)
            if title.startswith("Optical"):
                grid.addWidget(QLabel("Method"), len(rows) + 1, 0)
                self.method = QComboBox()
                self.method.addItems(["", "estimate", "reticle", "capture_px",
                                      "vendor_tool"])
                grid.addWidget(self.method, len(rows) + 1, 1, 1, 2)
            column.addWidget(group)

        buttons = QHBoxLayout()
        save = QPushButton("Save")
        save.clicked.connect(self.save)
        both = QPushButton("Save && next  (Ctrl+Enter)")
        both.clicked.connect(self.save_and_next)
        skip = QPushButton("Skip (neither)")
        skip.clicked.connect(self.next)
        for button in (save, both, skip):
            buttons.addWidget(button)
        column.addLayout(buttons)
        column.addStretch(1)
        return column

    def _outcome_column(self):
        column = QVBoxLayout()
        column.addWidget(QLabel("Outcome so far"))
        self.figure_type = QComboBox()
        options = self.controller.options(self.model_name, "figure_type_options")
        self.figure_type.addItems([str(o) for o in options])
        current = self.controller.state(self.model_name)["values"].get("figure_type")
        if current in options:
            self.figure_type.setCurrentText(current)
        self.figure_type.currentTextChanged.connect(self._set_figure_type)
        column.addWidget(self.figure_type)
        self.figure = QLabel("")
        self.figure.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.figure.setMinimumSize(420, 320)
        column.addWidget(self.figure, 1)
        return column

    # -- the walk ------------------------------------------------------------
    def reload(self, keep=False):
        """Fetch the queue again (a save changes what is unfilled). `keep`
        stays on the trial being shown when it is still listed, else on the
        place it held."""
        shown = self._queue[self._index]["id"] if 0 <= self._index < len(self._queue) else None
        place = max(self._index, 0)
        queue = self._run("finalize_queue", self.only_missing.isChecked())
        if queue is None:
            return
        self._queue = queue
        ids = [t["id"] for t in queue]
        if keep and shown in ids:
            self._index = ids.index(shown)
        else:
            self._index = min(place, len(queue) - 1) if queue else -1
        self._show()

    def previous(self):
        if self._index > 0:
            self._index -= 1
            self._show()

    def next(self):
        if self._index + 1 < len(self._queue):
            self._index += 1
            self._show()
        else:
            self._say("That was the last trial in the list.")

    def next_sample(self):
        """Jump to the first trial of the next sample."""
        if self._index < 0:
            return
        sample = self._queue[self._index]["sample_id"]
        for i in range(self._index + 1, len(self._queue)):
            if self._queue[i]["sample_id"] != sample:
                self._index = i
                self._show()
                return
        self._say("That was the last sample.")

    def _show(self):
        if not 0 <= self._index < len(self._queue):
            self.title.setText("Nothing to finalize" if not self._queue else "")
            self.summary.setText("Every trial in the list has its properties "
                                 "filled, or none has been recorded.")
            self._clear_media()
            self._load_form({})
            self._refresh_figure()
            return
        entry = self._queue[self._index]
        self.title.setText(
            f"Sample {entry['sample_id'] or '(none)'}  -  chip {entry['chip_id'] or '?'}, "
            f"flake {entry['flake_id'] or '?'}, cut {entry['cut_id'] or '?'}  -  "
            f"trial {entry['id']}  ({entry['sample_position']} of "
            f"{entry['sample_count']} in this sample)")
        media = self._run("finalize_media", entry["id"])
        if media is None:
            return
        self._media = media
        row = media["row"]
        self.summary.setText(
            f"Tip {row.get('tip_id') or '?'}   tilt {_text(row.get('tilt_deg'))} deg   "
            f"speed {_text(row.get('speed_steps_s'))} steps/s   "
            f"status {row.get('status')}"
            + ("   INVALID" if row.get("invalid") else "")
            + ("\nUnfilled: " + ", ".join(entry["missing_labels"]) if entry["missing"] else
               "\nEvery property has a value."))
        self._load_form(row)
        self._load_media(media)
        self.controller.set_value(self.model_name, "trial_pick", entry["id"])
        self._refresh_figure()
        self._say("")

    def _load_form(self, row):
        self._loaded = {}
        for name, edit in self._fields.items():
            text = _text(row.get(name))
            edit.setText(text)
            self._loaded[name] = text
        method = row.get("width_optical_method") or ""
        self.method.setCurrentText(method)
        self._loaded["width_optical_method"] = method

    # -- media ---------------------------------------------------------------
    def _clear_media(self):
        if self._player is not None:
            self._player.stop()
            self._player.setSource(QUrl())
        for holder in self.still_labels.values():
            holder.setPixmap(QPixmap())
            holder.setText("")
        self.frame_label.hide()
        self.video_note.setText("")

    def _load_media(self, media):
        self._clear_media()
        for key, _label in STILLS:
            holder, png = self.still_labels[key], media[key]
            pixmap = QPixmap()
            if png and pixmap.loadFromData(png):
                holder.setPixmap(pixmap.scaledToWidth(
                    520, Qt.TransformationMode.SmoothTransformation))
            else:
                holder.setText("No picture was taken.")
        kind = media["video_kind"]
        self.external.setEnabled(kind != "none")
        self.play.setEnabled(kind != "none")
        self.scrub.setEnabled(kind != "none")
        self._frames = media["frame_paths"]
        if kind == "file" and self._player is not None:
            self.video.show()
            self.frame_label.hide()
            self._player.setSource(QUrl.fromLocalFile(media["video_path"]))
            self._set_rate()
            self._player.pause()
            self.video_note.setText(f"{media['frames']} frames. Drag the bar to "
                                    "find the moment the tip touched down.")
        elif kind == "frames":
            if self.video is not None:
                self.video.hide()
            self.frame_label.show()
            self.scrub.setRange(0, max(len(self._frames) - 1, 0))
            self._scrub_to(0)
            self.video_note.setText(f"{len(self._frames)} JPEG frames (no encoder "
                                    "was installed when this was recorded).")
        else:
            if self.video is not None:
                self.video.hide()
            self.frame_label.hide()
            self.video_note.setText("This trial has no video.")

    def toggle_play(self):
        if self._player is None or self._media is None or self._media["video_kind"] != "file":
            return
        state = self._player.playbackState()
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._player.pause()
            self.play.setText("Play")
        else:
            self._player.play()
            self.play.setText("Pause")

    def _set_rate(self, _index=None):
        if self._player is not None:
            self._player.setPlaybackRate(RATES[self.rate.currentIndex()][1])

    def _scrub_to(self, value):
        if self._media and self._media["video_kind"] == "frames":
            if 0 <= value < len(self._frames):
                pixmap = QPixmap(self._frames[value])
                self.frame_label.setPixmap(pixmap.scaledToWidth(
                    520, Qt.TransformationMode.SmoothTransformation))
        elif self._player is not None:
            self._player.setPosition(value)

    def _player_position(self, ms):
        if not self.scrub.isSliderDown():
            self.scrub.setValue(ms)

    def _player_error(self, _error, message):
        events.debug("Video Player", message, source=SOURCE)
        self.video_note.setText("This window cannot play that video here ("
                                f"{message}). Use Open in video player.")

    def _open_external(self):
        if self._media and self._media["video_path"]:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._media["video_path"]))

    # -- the outcome ---------------------------------------------------------
    def _set_figure_type(self, label):
        if label:
            self.controller.run(self.model_name, "set_figure_type", None, (label,))
            self._refresh_figure()

    def _refresh_figure(self):
        png = self._run("figure")
        pixmap = QPixmap()
        if png and pixmap.loadFromData(bytes(png)):
            self.figure.setPixmap(pixmap.scaledToWidth(
                max(self.figure.width(), 420), Qt.TransformationMode.SmoothTransformation))
        else:
            self.figure.setPixmap(QPixmap())
            self.figure.setText("Nothing to plot yet.")

    # -- saving --------------------------------------------------------------
    def _typed(self):
        """What the form changed or newly holds: a text box only when it
        differs from what was loaded; a number box when it has text (a blank
        one is "leave it", which the model drops)."""
        out = {}
        for name, edit in self._fields.items():
            text = edit.text().strip()
            if text != self._loaded.get(name, ""):
                out[name] = text
        method = self.method.currentText()
        if method != self._loaded.get("width_optical_method", ""):
            out["width_optical_method"] = method
        return out

    def save(self):
        if self._media is None:
            return False
        saved = self._run("finalize_save", self._media["id"], self._typed())
        if saved is None:
            return False
        self._say(f"Trial {saved['id']} saved." if saved else "")
        self.reload(keep=True)
        return True

    def save_and_next(self):
        entry_id = self._queue[self._index]["id"] if 0 <= self._index < len(self._queue) else None
        if not self.save():
            return
        ids = [t["id"] for t in self._queue]
        if entry_id in ids:                  # still listed: step past it
            self.next()
        else:                                # filled in: the list closed over it
            self._show()

    def closeEvent(self, event):
        if self._player is not None:
            self._player.stop()
        super().closeEvent(event)
