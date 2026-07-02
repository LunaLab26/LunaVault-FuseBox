"""widgets/jog_wheel.py — a rotary shuttle control for frame-accurate scrubbing.

Drag around the wheel; the drag angle accumulates and emits frame_delta(n)
each time it crosses a per-frame threshold, so a slow drag steps one frame
at a time and a fast drag jumps several — a mouse analogue of a hardware
jog wheel.
"""

import math
from typing import Optional

from PySide6.QtCore import Qt, QPointF, Signal
from PySide6.QtGui import QPainter, QColor, QPen
from PySide6.QtWidgets import QWidget

import theme

_DEGREES_PER_FRAME = 12.0


class JogWheel(QWidget):
    frame_delta = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(34, 34)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._dragging = False
        self._last_angle: Optional[float] = None
        self._accum_deg = 0.0
        self._knob_angle = 0.0   # decorative — where the notch currently points

        ctrl = theme.controller()
        if ctrl is not None:
            ctrl.changed.connect(self.update)

    def _angle_at(self, pos: QPointF) -> float:
        c = QPointF(self.width() / 2, self.height() / 2)
        return math.degrees(math.atan2(pos.y() - c.y(), pos.x() - c.x()))

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self._last_angle = self._angle_at(event.position())

    def mouseMoveEvent(self, event):
        if not self._dragging or self._last_angle is None:
            return
        angle = self._angle_at(event.position())
        delta = angle - self._last_angle
        while delta > 180:
            delta -= 360
        while delta < -180:
            delta += 360
        self._last_angle = angle
        self._accum_deg += delta
        self._knob_angle = (self._knob_angle + delta) % 360

        n = int(self._accum_deg // _DEGREES_PER_FRAME)
        if n != 0:
            self._accum_deg -= n * _DEGREES_PER_FRAME
            self.frame_delta.emit(n)
        self.update()

    def mouseReleaseEvent(self, event):
        self._dragging = False
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._last_angle = None
        self._accum_deg = 0.0

    def paintEvent(self, event):
        pal = theme.active_palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = min(self.width(), self.height()) / 2 - 2
        c = QPointF(self.width() / 2, self.height() / 2)

        p.setBrush(QColor(pal.surface2))
        p.setPen(QPen(QColor(pal.border_hi), 2))
        p.drawEllipse(c, r, r)

        rad = math.radians(self._knob_angle)
        nx = c.x() + math.cos(rad) * (r - 6)
        ny = c.y() + math.sin(rad) * (r - 6)
        p.setBrush(QColor(pal.accent))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(nx, ny), 2.5, 2.5)
        p.end()
