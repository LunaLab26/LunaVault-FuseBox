"""dev_panel.py — hidden Developer options (triple-click the logo to reveal).

Sits next to the Legacy toggle in the window's top-right corner, invisible until
the logo is triple-clicked (logo_widget.TripleClickArea). A small "Developer"
button opens an expandable window of experimental switches, grouped by area. Each
option is independent and defaults to its safe value, so a change that causes a
roadblock can be rolled straight back — nothing else depends on it.

The options currently cover the per-clip preview (core.ffmpeg_cmd.build_clip_
sample_cmd via merge_tab._preview_accel) and the Review tab's software-decode
playback (review_tab / review_playback). Each change persists to settings
immediately and emits `changed` so a live view (e.g. the Review tab) can react.
Styled from theme.active_palette() (no literal colours); offscreen-instantiable.
"""

from dataclasses import dataclass

from PySide6.QtCore import Qt, QPoint, Signal
from PySide6.QtWidgets import (
    QWidget, QHBoxLayout, QVBoxLayout, QPushButton, QLabel, QCheckBox, QComboBox,
    QDialog, QFrame, QScrollArea,
)

import theme


@dataclass
class BoolOpt:
    key: str
    title: str
    desc: str


@dataclass
class ChoiceOpt:
    key: str
    title: str
    desc: str
    choices: list      # [(value, label), ...]
    default: object


# Grouped, experimental, each independently switchable. Wired into real code
# paths — see module docstring. Ordered from most-used to least.
SECTIONS = [
    ("Clip preview — generation (Merge tab)", [
        BoolOpt("dev_preview_gpu_encode", "GPU encode preview",
                "Encode the preview proxy with your graphics card's video encoder "
                "(NVENC / QSV / AMF) instead of the CPU. Falls back to the CPU "
                "automatically if no working GPU encoder is found on this machine."),
        BoolOpt("dev_preview_hw_decode", "GPU (hardware) decode preview",
                "Decode the source clip on the GPU (-hwaccel auto) while building the "
                "preview — fastest on very high-resolution footage. If a clip refuses "
                "to preview with this on, untick it."),
        BoolOpt("dev_preview_fast_sample", "Fast sample",
                "A shorter (2s) proxy at libx264's ultrafast preset — a near-instant, "
                "lower-quality preview. Ignored while GPU encode is on."),
        ChoiceOpt("dev_preview_height", "Preview resolution",
                  "Height of the preview proxy. Taller is clearer but takes longer to "
                  "generate and to decode on playback.",
                  [(160, "160p (default)"), (240, "240p"), (360, "360p"),
                   (480, "480p"), (720, "720p")], 160),
    ]),
    ("Clip preview — window", [
        ChoiceOpt("dev_preview_window_size", "Preview window size",
                  "How big the preview popup opens.",
                  [("small", "Small (400×300)"), ("medium", "Medium (640×360)"),
                   ("large", "Large (960×540)")], "medium"),
        ChoiceOpt("dev_preview_aspect_mode", "Video scaling",
                  "How the video fills the window. Fit keeps the aspect ratio "
                  "(letterboxed); Stretch fills and distorts; Crop fills and trims.",
                  [("fit", "Fit (keep aspect)"), ("stretch", "Stretch (fill)"),
                   ("crop", "Crop to fill")], "fit"),
        ChoiceOpt("dev_preview_speed", "Playback speed",
                  "Play the preview slower or faster.",
                  [(0.5, "0.5× (slow)"), (1.0, "1× (normal)"), (2.0, "2× (fast)")], 1.0),
        BoolOpt("dev_preview_loop", "Loop the preview",
                "Restart the sample automatically when it reaches the end."),
    ]),
    ("Review tab — playback", [
        ChoiceOpt("dev_review_frame_poll_ms", "Software playback smoothness",
                  "In software-decode mode the picture refreshes on a timer (a "
                  "slideshow, not true 30fps). Smoother refreshes more often and uses "
                  "more CPU. Applies live to the Review tab.",
                  [(150, "Smoother (150ms)"), (300, "Balanced (300ms)"),
                   (500, "Lighter (500ms)")], 300),
        BoolOpt("dev_review_allow_risky_hw_decode", "Allow GPU decode for 4K 10-bit HEVC",
                "This profile is normally forced to software decode because it has "
                "crashed GPU drivers on some hardware. Turn on to experiment with GPU "
                "decode for it — playback may freeze. Takes effect on the next master "
                "you open in Review."),
    ]),
    ("Review tab — overview filmstrip", [
        ChoiceOpt("dev_review_thumb_count", "Thumbnail count",
                  "How many frame thumbnails run along the overview track. More gives "
                  "a finer map but takes longer to fill.",
                  [(12, "12 (sparse)"), (24, "24 (default)"), (48, "48 (dense)")], 24),
        ChoiceOpt("dev_review_thumb_width", "Thumbnail resolution",
                  "Pixel width of each overview thumbnail. Wider is sharper but slower "
                  "to extract.",
                  [(120, "120px"), (160, "160px (default)"), (240, "240px")], 160),
    ]),
]


class DeveloperOptionsDialog(QDialog):
    """The expandable window of experimental switches. Modeless — leave it open
    beside the app while you experiment."""
    changed = Signal()

    def __init__(self, settings, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._section_labels: list = []
        self._bool_rows: list = []     # (frame, checkbox, desc_label)
        self._choice_rows: list = []   # (frame, title_label, combo, desc_label)
        self.setWindowTitle("Developer options")
        self.setModal(False)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        outer.addWidget(scroll)
        body = QWidget()
        scroll.setWidget(body)

        root = QVBoxLayout(body)
        root.setContentsMargins(18, 16, 18, 16)
        root.setSpacing(10)

        self._intro = QLabel("Experimental switches, grouped by area. Each is independent "
                             "and defaults to its safe value — if one causes a roadblock, "
                             "set it back to roll straight back.")
        self._intro.setWordWrap(True)
        root.addWidget(self._intro)

        for title, opts in SECTIONS:
            root.addSpacing(6)
            head = QLabel(title)
            self._section_labels.append(head)
            root.addWidget(head)
            for opt in opts:
                if isinstance(opt, BoolOpt):
                    root.addWidget(self._bool_row(opt))
                else:
                    root.addWidget(self._choice_row(opt))
        root.addStretch(1)

        self.resize(400, 500)
        ctrl = theme.controller()
        if ctrl is not None:
            ctrl.changed.connect(self._restyle)
        self._restyle()

    # ── rows ───────────────────────────────────────────────────────────────────
    def _bool_row(self, opt: BoolOpt) -> QFrame:
        box, lay = self._card()
        cb = QCheckBox(opt.title)
        cb.setChecked(bool(self._settings.get(opt.key, False)))
        cb.toggled.connect(lambda on, k=opt.key: self._set(k, bool(on)))
        d = QLabel(opt.desc)
        d.setWordWrap(True)
        lay.addWidget(cb)
        lay.addWidget(d)
        self._bool_rows.append((box, cb, d))
        return box

    def _choice_row(self, opt: ChoiceOpt) -> QFrame:
        box, lay = self._card()
        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        title = QLabel(opt.title)
        combo = QComboBox()
        for value, label in opt.choices:
            combo.addItem(label, value)
        current = self._settings.get(opt.key, opt.default)
        idx = next((i for i in range(combo.count()) if combo.itemData(i) == current), 0)
        combo.setCurrentIndex(idx)
        combo.currentIndexChanged.connect(
            lambda _i, c=combo, k=opt.key: self._set(k, c.currentData()))
        top.addWidget(title, 1)
        top.addWidget(combo, 0)
        d = QLabel(opt.desc)
        d.setWordWrap(True)
        lay.addLayout(top)
        lay.addWidget(d)
        self._choice_rows.append((box, title, combo, d))
        return box

    def _card(self):
        box = QFrame()
        box.setObjectName("devOpt")
        lay = QVBoxLayout(box)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(4)
        return box, lay

    def _set(self, key: str, value):
        self._settings.set(key, value)
        self.changed.emit()

    # ── theming ────────────────────────────────────────────────────────────────
    def _restyle(self):
        p = theme.active_palette()
        self.setStyleSheet(f"QDialog {{ background:{p.surface2}; }}")
        self._intro.setStyleSheet(f"background:transparent; color:{p.text_mute}; font-size:12px;")
        for head in self._section_labels:
            head.setStyleSheet(
                f"background:transparent; color:{p.text}; font-size:13px; font-weight:600;")
        card_qss = (f"#devOpt {{ background:{p.surface}; border:1px solid {p.border}; "
                    f"border-radius:{p.radius_sm}px; }}")
        for box, cb, d in self._bool_rows:
            box.setStyleSheet(card_qss)
            cb.setStyleSheet(f"background:transparent; color:{p.text}; font-weight:500;")
            d.setStyleSheet(f"background:transparent; color:{p.text_mute}; font-size:11px;")
        for box, title, _combo, d in self._choice_rows:
            box.setStyleSheet(card_qss)
            title.setStyleSheet(f"background:transparent; color:{p.text}; font-weight:500;")
            d.setStyleSheet(f"background:transparent; color:{p.text_mute}; font-size:11px;")


class DeveloperPanel(QWidget):
    """The corner control: a small button that toggles the options window open.
    `changed` forwards every option change so a host can refresh a live view."""
    changed = Signal()

    def __init__(self, settings):
        super().__init__()
        self._settings = settings
        self._dialog = None
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._btn = QPushButton("⚙ Developer")
        self._btn.setCheckable(True)
        self._btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn.setFixedHeight(22)
        self._btn.setToolTip("Experimental developer options")
        self._btn.clicked.connect(self._toggle_window)
        lay.addWidget(self._btn)

        ctrl = theme.controller()
        if ctrl is not None:
            ctrl.changed.connect(self._restyle)
        self._restyle()

    def _toggle_window(self):
        if self._dialog is None:
            self._dialog = DeveloperOptionsDialog(self._settings, self)
            self._dialog.changed.connect(self.changed.emit)
            self._dialog.finished.connect(lambda *_: self._sync_button())
        if self._dialog.isVisible():
            self._dialog.hide()
        else:
            self._dialog.move(self.mapToGlobal(QPoint(0, self.height() + 4)))
            self._dialog.show()
            self._dialog.raise_()
        self._sync_button()

    def _sync_button(self):
        self._btn.setChecked(bool(self._dialog and self._dialog.isVisible()))
        self._restyle()

    def _restyle(self):
        p = theme.active_palette()
        active = self._btn.isChecked()
        bg, fg, bd = (p.accent, p.on_accent(), p.accent) if active else (p.surface, p.text_dim, p.border)
        self._btn.setStyleSheet(
            f"QPushButton {{ background:{bg}; color:{fg}; border:1px solid {bd}; "
            f"border-radius:6px; padding:2px 9px; font-size:11px; }}"
            f"QPushButton:hover {{ color:{p.accent if not active else fg}; }}")
