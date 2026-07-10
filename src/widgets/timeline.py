"""widgets/timeline.py — shared horizontal-timeline geometry, scrubbing and paint.

`TimelineBase` owns everything a timeline strip needs regardless of what it's
for: padding/track geometry, seconds<->pixel mapping, the scrubber head/line,
and generic press/move/release drag plumbing. Subclasses hook three points to
add their own marks and drag targets:

  - `_paint_extra(p, pal)`   — draw between the track background and the
                                scrubber (e.g. a trim range, a zoom viewport)
  - `_hit_test(px) -> str`   — which drag target is under the pointer
  - `_apply_drag_other(tag, secs)` — handle a non-"pos" drag tag

`TrimTimeline` is today's Extract-tab Share-panel timeline (in/out markers +
scrubber), moved here unchanged in behaviour so `extract_tab.py` only needs to
swap its import. `widgets/trackbar.py`'s `OverviewTrackbar` (Review tab) is the other
`TimelineBase` subclass — a viewport window instead of trim handles.
"""

from typing import Optional

from PySide6.QtCore import Qt, Signal, QPoint
from PySide6.QtGui import QPainter, QColor, QPen, QPolygon
from PySide6.QtWidgets import QWidget

import theme


_FPS_DEFAULT = 30000 / 1001   # ≈ 29.97


def secs_to_tc(secs: float, fps: float = _FPS_DEFAULT) -> str:
    """Format seconds as HH:MM:SS:FF."""
    secs = max(0.0, secs)
    fps_i = max(1, round(fps))
    tot_f = int(secs * fps)
    ff = tot_f % fps_i
    tot_s = tot_f // fps_i
    s = tot_s % 60
    m = (tot_s // 60) % 60
    h = tot_s // 3600
    return f"{h:02d}:{m:02d}:{s:02d}:{ff:02d}"


class TimelineBase(QWidget):
    """Horizontal timeline: track bar + scrubber, with a drag-target hook."""
    position_changed = Signal(float)   # scrubber moved → seconds

    _PAD   = 14   # left/right padding (px)
    _TRK_Y = 28   # y-coordinate of top of track bar
    _TRK_H = 6    # track bar height
    _SCR_R = 5    # scrubber head radius

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedHeight(48)
        self.setMinimumWidth(200)
        self._duration: float = 0.0
        self._pos: float = 0.0
        self._drag: Optional[str] = None   # "pos" | subclass tag | None

    # ── Public setters (don't emit signals) ───────────────────────────────────

    def set_duration(self, dur: float):
        self._duration = max(0.0, dur)
        self._pos = min(self._pos, self._duration)
        self.update()

    def set_position(self, secs: float):
        self._pos = max(0.0, min(secs, self._duration))
        self.update()

    # ── Coordinate helpers ────────────────────────────────────────────────────

    def _track_x(self) -> int:
        return self._PAD

    def _track_w(self) -> int:
        return max(1, self.width() - 2 * self._PAD)

    def _secs_to_x(self, secs: float) -> int:
        if self._duration <= 0:
            return self._track_x()
        return int(self._track_x() + secs / self._duration * self._track_w())

    def _x_to_secs(self, px: float) -> float:
        tw = self._track_w()
        if tw <= 0 or self._duration <= 0:
            return 0.0
        return max(0.0, min(self._duration, (px - self._track_x()) / tw * self._duration))

    # ── Painting ──────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        pal = theme.active_palette()
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        self._paint_track(p, pal)
        self._paint_extra(p, pal)
        self._paint_scrubber(p, pal)

        p.end()

    def _paint_track(self, p: QPainter, pal):
        tx, ty, tw, th = self._track_x(), self._TRK_Y, self._track_w(), self._TRK_H
        p.setBrush(QColor(pal.border))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawRoundedRect(tx, ty, tw, th, 3, 3)

    def _paint_extra(self, p: QPainter, pal):
        """Hook: draw between the track background and the scrubber."""
        pass

    def _paint_scrubber(self, p: QPainter, pal):
        ty, th = self._TRK_Y, self._TRK_H
        sx = self._secs_to_x(self._pos)
        head = QColor(pal.text)
        p.setPen(QPen(head, 1))
        p.drawLine(sx, ty - self._SCR_R * 2 - 2, sx, ty + th)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(head)
        p.drawEllipse(sx - self._SCR_R, ty - self._SCR_R * 2 - 2 - self._SCR_R,
                      self._SCR_R * 2, self._SCR_R * 2)

    def _draw_marker(self, p: QPainter, x: int, tip_y: int, color: QColor,
                      mh: int = 12, mw: int = 7):
        """Draw a downward-pointing triangle with tip at (x, tip_y)."""
        poly = QPolygon([
            QPoint(x,      tip_y),
            QPoint(x - mw, tip_y - mh),
            QPoint(x + mw, tip_y - mh),
        ])
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawPolygon(poly)

    # ── Mouse interaction ─────────────────────────────────────────────────────

    def _hit_test(self, px: float) -> str:
        """Which drag target is under `px`. Base: always the scrubber."""
        return "pos"

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag = self._hit_test(event.position().x())
            self._apply_drag(event.position().x())

    def mouseMoveEvent(self, event):
        if self._drag:
            self._apply_drag(event.position().x())

    def mouseReleaseEvent(self, event):
        self._drag = None

    def _apply_drag(self, px: float):
        secs = self._x_to_secs(px)
        if self._drag == "pos":
            self._pos = secs
            self.update()
            self.position_changed.emit(secs)
        elif self._drag is not None:
            self._apply_drag_other(self._drag, secs)

    def _apply_drag_other(self, tag: str, secs: float):
        """Hook: handle a non-'pos' drag tag returned by `_hit_test`."""
        pass


class TrimTimeline(TimelineBase):
    """`TimelineBase` + draggable in/out trim markers (the Extract-tab Share-panel timeline)."""
    in_changed  = Signal(float)   # in marker moved → seconds
    out_changed = Signal(float)   # out marker moved → seconds

    _MH = 12   # marker triangle height
    _MW = 7    # marker triangle half-width

    def __init__(self, parent=None):
        super().__init__(parent)
        self._in:  float = 0.0
        self._out: float = 0.0

    def set_duration(self, dur: float):
        super().set_duration(dur)
        self._in  = min(self._in,  self._duration)
        self._out = min(self._out, self._duration)
        self.update()

    def set_in(self, secs: float):
        self._in = max(0.0, min(secs, self._duration))
        self.update()

    def set_out(self, secs: float):
        self._out = max(0.0, min(secs, self._duration))
        self.update()

    def _paint_extra(self, p: QPainter, pal):
        ty, th = self._TRK_Y, self._TRK_H
        if self._duration > 0 and self._out > self._in:
            ix = self._secs_to_x(self._in)
            ox = self._secs_to_x(self._out)
            p.setBrush(QColor(pal.accent))
            p.setPen(Qt.PenStyle.NoPen)
            p.drawRect(ix, ty, ox - ix, th)

        self._draw_marker(p, self._secs_to_x(self._in), ty, QColor(pal.ok), self._MH, self._MW)
        self._draw_marker(p, self._secs_to_x(self._out), ty, QColor(pal.danger), self._MH, self._MW)

    def _hit_test(self, px: float) -> str:
        tol = 14
        sx = self._secs_to_x(self._pos)
        ix = self._secs_to_x(self._in)
        ox = self._secs_to_x(self._out)
        if abs(px - sx) <= tol:
            return "pos"
        if abs(px - ix) <= tol:
            return "in"
        if abs(px - ox) <= tol:
            return "out"
        return "pos"   # clicking elsewhere moves scrubber

    def _apply_drag_other(self, tag: str, secs: float):
        if tag == "in":
            self._in = min(secs, self._out)
            self.update()
            self.in_changed.emit(self._in)
        elif tag == "out":
            self._out = max(secs, self._in)
            self.update()
            self.out_changed.emit(self._out)
