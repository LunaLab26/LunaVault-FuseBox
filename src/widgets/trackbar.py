"""widgets/trackbar.py — the Review tab's full-duration overview + viewport.

A TimelineBase subclass showing a condensed waveform across the WHOLE
master, with a draggable/resizable viewport window (drag an edge to zoom,
drag inside it to scroll) and the playhead — which stays positioned by the
FULL duration, independent of the viewport, exactly like TimelineBase's
scrubber already works.
"""

from typing import Optional

from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QPainter, QColor, QPen

from widgets.timeline import TimelineBase

_EDGE_TOL = 10     # px tolerance for grabbing a viewport edge
_MIN_SPAN = 0.5    # seconds — viewport can't collapse smaller than this
_HEIGHT = 72       # taller than the base timeline to fit a ruler strip below the track

# "Nice" ruler intervals (seconds) — pick the smallest that isn't too dense.
_RULER_STEPS = [1, 2, 5, 10, 15, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800]


def _nice_step(raw: float) -> float:
    for s in _RULER_STEPS:
        if s >= raw:
            return float(s)
    return float(_RULER_STEPS[-1])


def _fmt_ruler(secs: float) -> str:
    s = int(round(secs))
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


class OverviewTrackbar(TimelineBase):
    viewport_changed = Signal(float, float)   # t0, t1

    def __init__(self, parent=None):
        super().__init__(parent)
        self._envelope = None       # sequence of 0..1 amplitude values across the full duration
        self._view_t0 = 0.0
        self._view_t1 = 0.0
        self._drag_offset = 0.0     # body-drag: click_secs - view_t0, captured at press time
        self.setMouseTracking(True)   # so hover sets the cursor even with no button down
        self.setFixedHeight(_HEIGHT)  # room for the timestamp ruler under the track

    def set_duration(self, dur: float):
        super().set_duration(dur)
        if self._view_t1 <= self._view_t0 or self._view_t1 > self._duration:
            self._view_t0, self._view_t1 = 0.0, self._duration

    def set_envelope(self, envelope):
        """`envelope`: any indexable sequence of 0..1 amplitude values spanning
        the full duration — painted stretched to the track width, so the
        exact length doesn't matter."""
        self._envelope = envelope
        self.update()

    def set_viewport(self, t0: float, t1: float, emit: bool = False):
        t0 = max(0.0, min(t0, self._duration))
        t1 = max(t0 + _MIN_SPAN, min(t1, self._duration))
        self._view_t0, self._view_t1 = t0, t1
        self.update()
        if emit:
            self.viewport_changed.emit(self._view_t0, self._view_t1)

    def viewport(self) -> tuple:
        return self._view_t0, self._view_t1

    # ── Painting ──────────────────────────────────────────────────────────────

    def _paint_extra(self, p: QPainter, pal):
        self._paint_envelope(p, pal)
        self._paint_ruler(p, pal)
        self._paint_viewport(p, pal)

    def _paint_ruler(self, p: QPainter, pal):
        """Time ticks + HH:MM:SS / M:SS labels across the full duration, in a
        strip below the track — the timeline's sense of *where* you are."""
        if self._duration <= 0:
            return
        tw = self._track_w()
        y = self._TRK_Y + self._TRK_H + 16   # clear of the centred envelope
        target = max(3, min(8, tw // 90))
        step = _nice_step(self._duration / target)
        f = p.font()
        f.setPixelSize(9)
        p.setFont(f)
        t = 0.0
        while t <= self._duration + 1e-6:
            x = self._secs_to_x(t)
            p.setPen(QPen(QColor(pal.border_hi), 1))
            p.drawLine(x, y, x, y + 4)
            p.setPen(QColor(pal.text_mute))
            p.drawText(QRectF(x - 34, y + 5, 68, 12),
                       Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, _fmt_ruler(t))
            t += step

    def _paint_envelope(self, p: QPainter, pal):
        env = self._envelope
        if env is None or len(env) == 0 or self._duration <= 0:
            return
        tx, tw = self._track_x(), self._track_w()
        mid_y = self._TRK_Y + self._TRK_H / 2
        max_h = 16
        n = len(env)
        p.setPen(QPen(QColor(pal.text_dim), 1))
        step = max(1, n // max(1, tw))
        for i in range(0, n, step):
            x = tx + int(i / n * tw)
            amp = max(0.0, min(1.0, float(env[i])))
            h = amp * max_h
            p.drawLine(x, int(mid_y - h), x, int(mid_y + h))

    def _paint_viewport(self, p: QPainter, pal):
        if self._duration <= 0:
            return
        x0 = self._secs_to_x(self._view_t0)
        x1 = self._secs_to_x(self._view_t1)
        top = self._TRK_Y - 20
        bot = self._TRK_Y + self._TRK_H + 6
        fill = QColor(pal.accent)
        fill.setAlpha(40)
        p.setBrush(fill)
        p.setPen(QPen(QColor(pal.accent), 2))
        p.drawRect(x0, top, max(1, x1 - x0), bot - top)

        # Solid grab-handles on each edge — without them the drag-to-zoom
        # affordance is invisible (the whole point of this being discoverable).
        mid_y = (top + bot) / 2
        hh = 8   # half-height of a handle
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(pal.accent))
        for x in (x0, x1):
            p.drawRoundedRect(int(x) - 2, int(mid_y - hh), 4, hh * 2, 2, 2)

    # ── Hit-test / drag ───────────────────────────────────────────────────────

    def _hit_test(self, px: float) -> str:
        x0 = self._secs_to_x(self._view_t0)
        x1 = self._secs_to_x(self._view_t1)
        if abs(px - x0) <= _EDGE_TOL:
            return "viewport-left"
        if abs(px - x1) <= _EDGE_TOL:
            return "viewport-right"
        if x0 < px < x1:
            self._drag_offset = self._x_to_secs(px) - self._view_t0
            return "viewport-body"
        return "pos"

    def mouseMoveEvent(self, event):
        # While dragging, TimelineBase does the work. While merely hovering,
        # reflect what a press would grab so the interaction is discoverable:
        # resize arrows on the edges, a hand over the body.
        if self._drag is None and self._duration > 0:
            tag = self._hit_test(event.position().x())
            if tag in ("viewport-left", "viewport-right"):
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            elif tag == "viewport-body":
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            else:
                self.setCursor(Qt.CursorShape.PointingHandCursor)
        elif self._drag == "viewport-body":
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def _apply_drag_other(self, tag: str, secs: float):
        span = self._view_t1 - self._view_t0
        if tag == "viewport-left":
            self._view_t0 = max(0.0, min(secs, self._view_t1 - _MIN_SPAN))
        elif tag == "viewport-right":
            self._view_t1 = min(self._duration, max(secs, self._view_t0 + _MIN_SPAN))
        elif tag == "viewport-body":
            new_t0 = max(0.0, min(secs - self._drag_offset, self._duration - span))
            self._view_t0, self._view_t1 = new_t0, new_t0 + span
        else:
            return
        self.update()
        self.viewport_changed.emit(self._view_t0, self._view_t1)
