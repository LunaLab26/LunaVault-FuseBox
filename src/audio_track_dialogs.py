"""audio_track_dialogs.py — Custom audio arrangement + Advanced output dialogs.

Both edit a shared core.ffmpeg_cmd.OutputPlan:
  • AudioArrangeDialog (from the "Custom…" primary-audio choice) — pick which
    audio sources to include and their order, with source details.
  • OutputAdvancedDialog (from the output "Advanced" button) — toggle the video
    and each audio track, with output codec / lossy-lossless details, in the same
    order chosen above.
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QCheckBox, QFrame,
)

from ffmpeg_runner import get_ffmpeg
from core.ffmpeg_cmd import OutputTrack, OutputPlan
from core.track_info import audio_tracks, video_meta, fmt_duration, fmt_bitrate, fmt_size
import theme


def _meta_line(p, parts) -> QLabel:
    lbl = QLabel("  ·  ".join(x for x in parts if x))
    lbl.setStyleSheet(f"color:{p.text_dim}; font-size:11px;")
    return lbl


class AudioArrangeDialog(QDialog):
    """Choose which audio sources to include and in what order."""

    def __init__(self, plan: OutputPlan, clip, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Custom audio tracks")
        self.setMinimumWidth(480)
        self._plan = plan
        self._p = theme.active_palette()
        _, fp = get_ffmpeg()
        self._meta = audio_tracks(clip, fp) if clip is not None else {}
        # Working copy of the track order (only kinds that exist for this clip
        # are shown, but we keep unknown kinds too).
        self._tracks = [OutputTrack(t.kind, t.enabled) for t in plan.tracks]

        self._root = QVBoxLayout(self)
        self._root.setSpacing(8)
        self._root.setContentsMargins(18, 16, 18, 16)
        intro = QLabel("Tick the audio tracks to include and order them. The first "
                       "ticked track becomes the default in the file.")
        intro.setWordWrap(True)
        intro.setStyleSheet(f"color:{self._p.text}; font-size:12px;")
        self._root.addWidget(intro)

        self._rows_box = QVBoxLayout()
        self._rows_box.setSpacing(6)
        self._root.addLayout(self._rows_box)
        self._render_rows()

        btns = QHBoxLayout(); btns.addStretch()
        cancel = QPushButton("Cancel"); cancel.clicked.connect(self.reject)
        ok = QPushButton("Apply"); ok.clicked.connect(self._apply)
        btns.addWidget(cancel); btns.addWidget(ok)
        self._root.addLayout(btns)

    def _render_rows(self):
        while self._rows_box.count():
            item = self._rows_box.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        p = self._p
        for i, t in enumerate(self._tracks):
            m = self._meta.get(t.kind)
            row = QFrame()
            row.setStyleSheet(f"QFrame {{ border:1px solid {p.border}; border-radius:6px; }}")
            h = QHBoxLayout(row); h.setContentsMargins(10, 8, 8, 8); h.setSpacing(8)

            chk = QCheckBox()
            chk.setChecked(t.enabled and (m.available if m else True))
            chk.setEnabled(bool(m and m.available))
            chk.toggled.connect(lambda v, idx=i: self._set_enabled(idx, v))
            h.addWidget(chk)

            col = QVBoxLayout(); col.setSpacing(1)
            label = m.label if m else t.kind
            name = QLabel(label)
            name.setStyleSheet(f"color:{p.text}; font-size:12px; font-weight:500; border:none;")
            col.addWidget(name)
            if m and m.available:
                col.addWidget(_meta_line(p, [m.src_codec, fmt_duration(m.duration),
                                             fmt_bitrate(m.bitrate), fmt_size(m.filesize),
                                             f"{m.channels}ch" if m.channels else ""]))
            else:
                col.addWidget(_meta_line(p, ["not available for this clip"]))
            h.addLayout(col, 1)

            icon_style = (
                f"QPushButton {{ background:{p.surface2}; color:{p.text}; border:1px solid {p.border}; "
                "border-radius:4px; padding:0px; font-size:14px; }"
                f"QPushButton:hover {{ border-color:{p.accent}; color:{p.accent}; }}"
                f"QPushButton:disabled {{ color:{p.text_dim}; border-color:{p.border_dk}; }}")
            up = QPushButton("↑"); up.setFixedSize(26, 26); up.setStyleSheet(icon_style)
            up.clicked.connect(lambda _, idx=i: self._move(idx, -1))
            dn = QPushButton("↓"); dn.setFixedSize(26, 26); dn.setStyleSheet(icon_style)
            dn.clicked.connect(lambda _, idx=i: self._move(idx, +1))
            up.setEnabled(i > 0); dn.setEnabled(i < len(self._tracks) - 1)
            h.addWidget(up); h.addWidget(dn)
            self._rows_box.addWidget(row)

    def _set_enabled(self, idx, v):
        self._tracks[idx].enabled = v

    def _move(self, idx, delta):
        j = idx + delta
        if 0 <= j < len(self._tracks):
            self._tracks[idx], self._tracks[j] = self._tracks[j], self._tracks[idx]
            self._render_rows()

    def _apply(self):
        self._plan.tracks = self._tracks
        self.accept()


class OutputAdvancedDialog(QDialog):
    """Toggle the video and each audio track in the final output, with details."""

    def __init__(self, plan: OutputPlan, clip, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Advanced output — tracks")
        self.setMinimumWidth(500)
        self._plan = plan
        self._p = theme.active_palette()
        _, fp = get_ffmpeg()
        self._ameta = audio_tracks(clip, fp) if clip is not None else {}
        self._vmeta = video_meta(clip) if clip is not None else None

        p = self._p
        root = QVBoxLayout(self); root.setSpacing(8); root.setContentsMargins(18, 16, 18, 16)
        intro = QLabel("Choose which tracks the master file will contain. At least "
                       "one track must stay enabled.")
        intro.setWordWrap(True); intro.setStyleSheet(f"color:{p.text}; font-size:12px;")
        root.addWidget(intro)

        # Video row
        self._video_chk = QCheckBox()
        self._video_chk.setChecked(plan.include_video)
        vrow = self._make_row(self._video_chk, self._vmeta.label if self._vmeta else "Video",
                              [self._vmeta.out_codec, fmt_duration(self._vmeta.duration),
                               "lossless" if self._vmeta.lossless else "lossy",
                               fmt_size(self._vmeta.filesize)] if self._vmeta else [],
                              accent=True)
        root.addWidget(vrow)

        # Audio rows in plan order
        self._audio_chks = []
        for t in plan.tracks:
            m = self._ameta.get(t.kind)
            chk = QCheckBox()
            avail = bool(m and m.available)
            chk.setChecked(t.enabled and avail)
            chk.setEnabled(avail)
            self._audio_chks.append((t, chk))
            details = ([m.out_codec, fmt_duration(m.duration),
                        "lossless" if m.lossless else "lossy",
                        fmt_bitrate(m.bitrate)] if avail else ["not available for this clip"])
            root.addWidget(self._make_row(chk, m.label if m else t.kind, details))

        btns = QHBoxLayout(); btns.addStretch()
        cancel = QPushButton("Cancel"); cancel.clicked.connect(self.reject)
        ok = QPushButton("Apply"); ok.clicked.connect(self._apply)
        btns.addWidget(cancel); btns.addWidget(ok)
        root.addLayout(btns)

    def _make_row(self, chk, title, details, accent=False):
        p = self._p
        row = QFrame()
        border = p.accent if accent else p.border
        row.setStyleSheet(f"QFrame {{ border:1px solid {border}; border-radius:6px; }}")
        h = QHBoxLayout(row); h.setContentsMargins(10, 8, 10, 8); h.setSpacing(8)
        h.addWidget(chk)
        col = QVBoxLayout(); col.setSpacing(1)
        name = QLabel(title)
        name.setStyleSheet(f"color:{p.text}; font-size:12px; font-weight:500; border:none;")
        col.addWidget(name)
        col.addWidget(_meta_line(p, details))
        h.addLayout(col, 1)
        return row

    def _apply(self):
        if not self._video_chk.isChecked() and not any(c.isChecked() for _, c in self._audio_chks):
            from PySide6.QtWidgets import QMessageBox
            QMessageBox.information(self, "Keep one track",
                                    "Enable at least one track (video or audio).")
            return
        self._plan.include_video = self._video_chk.isChecked()
        for t, chk in self._audio_chks:
            t.enabled = chk.isChecked()
        self.accept()
