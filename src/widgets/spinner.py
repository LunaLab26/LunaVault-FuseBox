"""widgets/spinner.py — a small rotating-arc "loading" indicator.

Sits next to a section title while a background worker is filling that
section in (thumbnails, waveforms, a proxy render, …) — visible proof of
progress so a slow extraction reads as "working" rather than "broken".
Purely cosmetic: callers own starting/stopping it around their own workers'
actual lifetime.
"""

from PySide6.QtCore import Qt, QTimer, QRectF
from PySide6.QtGui import QPainter, QPen, QColor
from PySide6.QtWidgets import QWidget

import theme

_STEP_DEG = 30      # degrees advanced per tick
_INTERVAL_MS = 60   # ms per tick — a full turn every 720ms
_ARC_SPAN = 100      # degrees of visible arc (a partial ring reads as "spinning")


class LoadingSpinner(QWidget):
    def __init__(self, size: int = 16, parent=None):
        super().__init__(parent)
        self._size = size
        self._angle = 0
        self.setFixedSize(size, size)
        self._timer = QTimer(self)
        self._timer.setInterval(_INTERVAL_MS)
        self._timer.timeout.connect(self._tick)
        self.hide()

    def start(self):
        self._angle = 0
        self.show()
        self._timer.start()

    def stop(self):
        self._timer.stop()
        self.hide()

    def is_spinning(self) -> bool:
        return self._timer.isActive()

    def _tick(self):
        self._angle = (self._angle + _STEP_DEG) % 360
        self.update()

    def paintEvent(self, event):
        if not self._timer.isActive():
            return
        pal = theme.active_palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(pal.accent))
        pen.setWidthF(max(1.5, self._size * 0.14))
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        p.setPen(pen)
        r = self._size / 2 - pen.widthF() / 2 - 1
        rect = QRectF(self._size / 2 - r, self._size / 2 - r, r * 2, r * 2)
        # Qt angles: 0 = 3 o'clock, positive = counter-clockwise, in 1/16ths
        # of a degree — a negative start makes it visibly sweep clockwise.
        p.drawArc(rect, -self._angle * 16, _ARC_SPAN * 16)
        p.end()
