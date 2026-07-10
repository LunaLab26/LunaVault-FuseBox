"""widgets/scopes_panel.py — colour/dynamic-range panel for the Review tab.

Metadata badges (from probe) + a Histogram/Waveform toggle (Waveform has a
Parade/RGB sub-toggle), painting arrays built by core.scopes. The numbers
only mean what they claim when `set_frame()` was fed a true-bit-depth frame
from ffmpeg extraction — see `set_exact(False)` for the playback-time
approximate case (the v1.4 spike found QVideoFrame.toImage() silently
converts genuine 10-bit frames to 8-bit).
"""

import numpy as np
from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QPainter, QColor, QImage, QPen, QPolygonF
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QButtonGroup

import theme
from core.scopes import histogram_rgb, waveform_parade, waveform_rgb, axis_ticks


def _ndarray_to_qimage(arr: np.ndarray) -> QImage:
    arr = np.ascontiguousarray(arr)
    h, w = arr.shape[0], arr.shape[1]
    img = QImage(arr.data, w, h, arr.strides[0], QImage.Format.Format_RGB888)
    return img.copy()   # detach from the numpy buffer's lifetime


def _parade_to_qimage(parade: dict) -> QImage:
    r, g, b = parade["r"], parade["g"], parade["b"]
    h, w = r.shape

    def _tint(chan, color):
        # sqrt-compressed normalization: one large uniform region (sky, wall,
        # out-of-focus background) shouldn't be able to swamp everything else
        # under a linear scale — see core.scopes._compress_normalize.
        compressed = np.sqrt(chan)
        peak = compressed.max()
        norm = (compressed / peak) if peak > 0 else compressed
        out = np.zeros((h, w, 3), dtype=np.uint8)
        out[..., 0] = (norm * color[0]).astype(np.uint8)
        out[..., 1] = (norm * color[1]).astype(np.uint8)
        out[..., 2] = (norm * color[2]).astype(np.uint8)
        return out

    gap = max(1, w // 40)
    gap_col = np.zeros((h, gap, 3), dtype=np.uint8)
    combined = np.concatenate([
        _tint(r, (224, 104, 90)), gap_col,
        _tint(g, (63, 183, 101)), gap_col,
        _tint(b, (90, 150, 220)),
    ], axis=1)
    return _ndarray_to_qimage(combined)


class _ScopeCanvas(QWidget):
    """Paints whichever scope is current: histogram or waveform image."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(170)   # was 110 — the scopes column was the tightest spot
        self._mode = "wave"          # "hist" | "wave"
        self._hist = None            # dict from histogram_rgb, or None
        self._wave_img = None        # QImage, or None
        ctrl = theme.controller()
        if ctrl is not None:
            ctrl.changed.connect(self.update)

    def set_mode(self, mode: str):
        self._mode = mode
        self.update()

    def set_data(self, hist, wave_img):
        self._hist = hist
        self._wave_img = wave_img
        self.update()

    def paintEvent(self, event):
        pal = theme.active_palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), QColor(pal.input_dk))
        if self._mode == "hist":
            self._paint_hist(p, pal)
        else:
            self._paint_wave(p, pal)
        p.end()

    def _paint_hist(self, p: QPainter, pal):
        h = self._hist
        if not h:
            return
        w, ht = self.width(), self.height()
        top, bot, left, right = 6, ht - 18, 6, w - 6

        for tick in axis_ticks(h["bit_depth"], 5):
            x = left + tick / h["max_value"] * (right - left)
            p.setPen(QPen(QColor(pal.border), 1))
            p.drawLine(int(x), top, int(x), bot)

        def _series(counts, color, alpha):
            n = len(counts)
            if n < 2:
                return
            body = counts[1:-1] if n > 2 else counts
            cap = max(1, int(body.max())) if len(body) else 1
            step = max(1, n // max(1, int(right - left)))
            pts = [QPointF(left, bot)]
            for i in range(0, n, step):
                x = left + i / (n - 1) * (right - left)
                y = bot - min(counts[i], cap) / cap * (bot - top)
                pts.append(QPointF(x, y))
            pts.append(QPointF(right, bot))
            col = QColor(color)
            col.setAlpha(alpha)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(col)
            p.drawPolygon(QPolygonF(pts))

        # True red/green/blue, not palette tokens — an RGB scope has to show
        # the actual channel colours to be readable as one (exempted in
        # tests/test_theme.py alongside about_tab.py's brand colours).
        _series(h["luma"], pal.text, 60)
        _series(h["r"], "#E0685A", 150)
        _series(h["g"], "#3FB765", 140)
        _series(h["b"], "#4A90D9", 160)

        p.setPen(QColor(pal.text_mute))
        p.drawText(QRectF(left, ht - 16, (right - left) / 2, 14),
                  Qt.AlignmentFlag.AlignLeft, f"Shadows {h['clip_low_pct']:.1f}% clipped")
        p.drawText(QRectF(left + (right - left) / 2, ht - 16, (right - left) / 2, 14),
                  Qt.AlignmentFlag.AlignRight, f"Highlights {h['clip_high_pct']:.1f}% clipped")

    def _paint_wave(self, p: QPainter, pal):
        if self._wave_img is None:
            return
        target = self.rect().adjusted(4, 4, -4, -4)
        p.drawImage(target, self._wave_img)


class ScopesPanel(QWidget):
    """Badges + Histogram/Waveform toggle + the scope canvas."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._exact = False
        self._current_frame = None   # (arr, bit_depth) most recently set
        self._badges: list = []

        self._badge_row = QHBoxLayout()
        self._canvas = _ScopeCanvas(self)

        self._mode_hist = QPushButton("Histogram")
        self._mode_wave = QPushButton("Waveform")
        for b in (self._mode_hist, self._mode_wave):
            b.setCheckable(True)
        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        self._mode_group.addButton(self._mode_hist)
        self._mode_group.addButton(self._mode_wave)
        self._mode_wave.setChecked(True)
        self._mode_hist.toggled.connect(self._on_mode_toggled)

        # Parade/RGB is a *style* of Waveform, not a peer of Histogram — a
        # "Waveform style:" label + smaller buttons give it a clearly
        # subordinate visual weight instead of reading as a third main mode.
        self._wave_style_label = QLabel("Waveform style:")
        self._wave_parade = QPushButton("Parade")
        self._wave_rgb = QPushButton("RGB")
        for b in (self._wave_parade, self._wave_rgb):
            b.setCheckable(True)
            b.setObjectName("wave_style_btn")
        self._wave_group = QButtonGroup(self)
        self._wave_group.setExclusive(True)
        self._wave_group.addButton(self._wave_parade)
        self._wave_group.addButton(self._wave_rgb)
        self._wave_parade.setChecked(True)
        self._wave_parade.toggled.connect(self._on_mode_toggled)

        self._approx_label = QLabel("")

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)
        root.addLayout(self._badge_row)
        mode_row = QHBoxLayout()
        mode_row.addWidget(self._mode_hist)
        mode_row.addWidget(self._mode_wave)
        mode_row.addSpacing(10)
        mode_row.addWidget(self._wave_style_label)
        mode_row.addWidget(self._wave_parade)
        mode_row.addWidget(self._wave_rgb)
        mode_row.addStretch()
        mode_row.addWidget(self._approx_label)
        root.addLayout(mode_row)
        root.addWidget(self._canvas, 1)

        self._on_mode_toggled(True)   # sync initial sub-toggle visibility
        self.set_exact(False)
        ctrl = theme.controller()
        if ctrl is not None:
            ctrl.changed.connect(self._restyle)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_badges(self, codec: str, bit_depth: int, color_space: str,
                   subsampling: str, is_hdr: bool):
        for b in self._badges:
            b.deleteLater()
        self._badges.clear()
        labels = [f"{bit_depth}-bit", (codec or "—").upper(), (color_space or "—").upper(),
                 subsampling or "—", "HDR" if is_hdr else "SDR"]
        for text in labels:
            lbl = QLabel(text)
            lbl.setObjectName("scope_badge")
            self._badge_row.insertWidget(self._badge_row.count(), lbl)
            self._badges.append(lbl)
        self._restyle()

    def set_exact(self, exact: bool):
        self._exact = exact
        self._approx_label.setText("Exact" if exact else "Approximate (live)")
        self._restyle()

    def set_frame(self, arr, bit_depth: int):
        """`arr`: (H, W, 3) array already on `bit_depth`'s native scale."""
        self._current_frame = (arr, bit_depth)
        self._recompute()

    def current_mode(self) -> str:
        return "hist" if self._mode_hist.isChecked() else "wave"

    # ── Internal ──────────────────────────────────────────────────────────────

    def _recompute(self):
        if self._current_frame is None:
            return
        arr, bit_depth = self._current_frame
        hist = histogram_rgb(arr, bit_depth=bit_depth)
        if self._wave_parade.isChecked():
            img = _parade_to_qimage(waveform_parade(arr, out_h=150, bit_depth=bit_depth))
        else:
            img = _ndarray_to_qimage(waveform_rgb(arr, out_h=150, bit_depth=bit_depth))
        self._canvas.set_data(hist, img)

    def _on_mode_toggled(self, checked: bool):
        mode = self.current_mode()
        self._wave_style_label.setVisible(mode == "wave")
        self._wave_parade.setVisible(mode == "wave")
        self._wave_rgb.setVisible(mode == "wave")
        self._canvas.set_mode(mode)
        self._recompute()

    def _restyle(self):
        p = theme.active_palette()
        for b in self._badges:
            b.setStyleSheet(
                f"QLabel#scope_badge {{ background:{p.btn_bg}; color:{p.text_dim}; "
                "border-radius:4px; padding:2px 8px; font-size:11px; }")
        self._approx_label.setStyleSheet(
            f"color:{p.text_mute if self._exact else p.warn}; font-size:11px;")
        self._wave_style_label.setStyleSheet(f"color:{p.text_mute}; font-size:11px;")
        # Deliberately smaller/quieter than the main Histogram/Waveform toggle —
        # a sub-choice, not a peer mode.
        self._wave_parade.setStyleSheet(
            f"QPushButton#wave_style_btn {{ background:{p.btn_bg}; color:{p.text_dim}; "
            f"border:1px solid {p.border}; border-radius:4px; padding:2px 8px; font-size:10px; }}"
            f"QPushButton#wave_style_btn:checked {{ background:{p.surface2}; border-color:{p.accent}; color:{p.accent}; }}")
        self._wave_rgb.setStyleSheet(self._wave_parade.styleSheet())
