"""widgets/audio_lanes.py — per-track audio lanes for the Review tab.

One lane per audio track: a tickbox to include it in the mix, a peak
waveform or spectrogram tile (whichever the global Waveform|Spectral
toggle picks — one mode for every lane, so comparing tracks means
comparing the same kind of view), and a shared playhead line. A
plain-language "Playing: ..." readout sits in the header.
"""

from typing import Optional

from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QPainter, QColor, QPen
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QButtonGroup, QCheckBox,
)

import theme


class _LaneCanvas(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(48)
        self._mode = "wave"     # "wave" | "spec"
        self._peaks = None      # (N,2) min/max pairs, or None
        self._spec_img = None   # QImage, or None
        self._playhead_frac: Optional[float] = None
        ctrl = theme.controller()
        if ctrl is not None:
            ctrl.changed.connect(self.update)

    def set_mode(self, mode: str):
        self._mode = mode
        self.update()

    def set_peaks(self, peaks):
        self._peaks = peaks
        self.update()

    def set_spectrogram(self, img):
        self._spec_img = img
        self.update()

    def set_playhead(self, frac: Optional[float]):
        self._playhead_frac = frac
        self.update()

    def paintEvent(self, event):
        pal = theme.active_palette()
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(pal.input_dk))
        w, h = self.width(), self.height()

        if self._mode == "wave" and self._peaks is not None and len(self._peaks):
            mid = h / 2.0
            n = len(self._peaks)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(pal.accent))
            bar_w = max(1.0, w / n)
            for i in range(n):
                x = i / n * w
                mn, mx = float(self._peaks[i][0]), float(self._peaks[i][1])
                y0 = mid - mx * (h / 2 - 4)
                y1 = mid - mn * (h / 2 - 4)
                p.drawRect(QRectF(x, y0, bar_w, max(1.0, y1 - y0)))
        elif self._mode == "spec" and self._spec_img is not None:
            p.drawImage(self.rect(), self._spec_img)

        if self._playhead_frac is not None and 0.0 <= self._playhead_frac <= 1.0:
            x = self._playhead_frac * w
            p.setPen(QPen(QColor(pal.gold), 2))
            p.drawLine(int(x), 0, int(x), h)
        p.end()


class _AudioLane(QWidget):
    """One track: tickbox + label + canvas."""
    toggled = Signal(int, bool)   # audio_index, checked

    def __init__(self, audio_index: int, label: str, sublabel: str, parent=None):
        super().__init__(parent)
        self.audio_index = audio_index
        self.setFixedHeight(54)

        self._checkbox = QCheckBox()
        self._checkbox.setChecked(True)
        self._checkbox.toggled.connect(lambda c: self.toggled.emit(self.audio_index, c))

        self._label = QLabel(label)
        self._sublabel = QLabel(sublabel)
        info = QVBoxLayout()
        info.setSpacing(0)
        info.addWidget(self._label)
        info.addWidget(self._sublabel)

        self._canvas = _LaneCanvas(self)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        lay.addWidget(self._checkbox, 0, Qt.AlignmentFlag.AlignVCenter)
        info_widget = QWidget()
        info_widget.setLayout(info)
        info_widget.setFixedWidth(140)
        lay.addWidget(info_widget)
        lay.addWidget(self._canvas, 1)

        self._restyle()
        ctrl = theme.controller()
        if ctrl is not None:
            ctrl.changed.connect(self._restyle)

    def is_checked(self) -> bool:
        return self._checkbox.isChecked()

    def set_checked(self, checked: bool):
        self._checkbox.blockSignals(True)
        self._checkbox.setChecked(checked)
        self._checkbox.blockSignals(False)

    def set_mode(self, mode: str):
        self._canvas.set_mode(mode)

    def set_peaks(self, peaks):
        self._canvas.set_peaks(peaks)

    def set_spectrogram(self, img):
        self._canvas.set_spectrogram(img)

    def set_playhead(self, frac):
        self._canvas.set_playhead(frac)

    def _restyle(self):
        p = theme.active_palette()
        self._label.setStyleSheet(f"color:{p.text}; font-size:12px;")
        self._sublabel.setStyleSheet(f"color:{p.text_mute}; font-size:11px;")


class AudioLaneStack(QWidget):
    """Stack of per-track audio lanes + the global Waveform|Spectral toggle
    + a plain-language 'Playing: ...' readout."""
    track_toggled = Signal(int, bool)   # audio_index, checked
    mode_changed  = Signal(str)         # "wave" | "spec"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lanes: dict = {}

        self._mode_wave = QPushButton("Waveform")
        self._mode_spec = QPushButton("Spectral")
        for b in (self._mode_wave, self._mode_spec):
            b.setCheckable(True)
        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        self._mode_group.addButton(self._mode_wave)
        self._mode_group.addButton(self._mode_spec)
        self._mode_wave.setChecked(True)
        self._mode_wave.toggled.connect(self._on_mode_toggled)

        self._title = QLabel("Audio tracks")
        self._readout = QLabel("")

        header = QHBoxLayout()
        header.addWidget(self._title)
        header.addWidget(self._mode_wave)
        header.addWidget(self._mode_spec)
        header.addStretch()
        header.addWidget(self._readout)

        self._lane_layout = QVBoxLayout()
        self._lane_layout.setSpacing(4)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.addLayout(header)
        root.addLayout(self._lane_layout)

        self._restyle()
        ctrl = theme.controller()
        if ctrl is not None:
            ctrl.changed.connect(self._restyle)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_tracks(self, tracks: list):
        """`tracks`: list of (audio_index, label, sublabel) — rebuilds the stack."""
        while self._lane_layout.count():
            item = self._lane_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._lanes.clear()
        mode = self.current_mode()
        for idx, label, sublabel in tracks:
            lane = _AudioLane(idx, label, sublabel)
            lane.toggled.connect(self.track_toggled.emit)
            lane.set_mode(mode)
            self._lane_layout.addWidget(lane)
            self._lanes[idx] = lane

    def set_peaks(self, audio_index: int, peaks):
        lane = self._lanes.get(audio_index)
        if lane is not None:
            lane.set_peaks(peaks)

    def set_spectrogram(self, audio_index: int, img):
        lane = self._lanes.get(audio_index)
        if lane is not None:
            lane.set_spectrogram(img)

    def set_playhead(self, frac):
        for lane in self._lanes.values():
            lane.set_playhead(frac)

    def set_checked_tracks(self, indices):
        indices = set(indices)
        for idx, lane in self._lanes.items():
            lane.set_checked(idx in indices)

    def ticked_tracks(self) -> list:
        return [idx for idx, lane in self._lanes.items() if lane.is_checked()]

    def set_readout(self, text: str):
        self._readout.setText(text)

    def current_mode(self) -> str:
        return "wave" if self._mode_wave.isChecked() else "spec"

    # ── Internal ──────────────────────────────────────────────────────────────

    def _on_mode_toggled(self, checked: bool):
        mode = self.current_mode()
        for lane in self._lanes.values():
            lane.set_mode(mode)
        self.mode_changed.emit(mode)

    def _restyle(self):
        p = theme.active_palette()
        self._title.setStyleSheet(f"color:{p.text_mute}; font-size:11px; letter-spacing:0.5px;")
        self._readout.setStyleSheet(f"color:{p.text_mute}; font-size:11px;")
