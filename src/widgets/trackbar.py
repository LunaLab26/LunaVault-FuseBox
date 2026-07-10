"""widgets/trackbar.py — the Review tab's full-duration overview + viewport.

A TimelineBase subclass showing a condensed waveform across the WHOLE
master, with a draggable/resizable viewport window (drag an edge to zoom,
drag inside it to scroll) and the playhead — which stays positioned by the
FULL duration, independent of the viewport, exactly like TimelineBase's
scrubber already works.
"""

from typing import Optional

from PySide6.QtCore import Qt, QRectF, Signal
from PySide6.QtGui import QPainter, QColor, QPen, QImage

from widgets.timeline import TimelineBase
from widgets.audio_lanes import LANE_LABEL_MARGIN

_EDGE_TOL = 10     # px tolerance for grabbing a viewport edge
_SCRUB_TOL = 8     # px tolerance for grabbing the playhead directly, even inside the viewport
_MIN_SPAN = 0.5    # seconds — viewport can't collapse smaller than this
_HEIGHT = 96       # taller than the base timeline to fit a thumbnail row + ruler strip

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

    _TRK_Y = 54   # pushed down from the base class's 28 to leave room for the thumbnail row above
    _THUMB_Y = 2
    _THUMB_H = 38

    def __init__(self, parent=None):
        super().__init__(parent)
        self._envelope = None       # sequence of 0..1 amplitude values across the full duration
        self._view_t0 = 0.0
        self._view_t1 = 0.0
        self._drag_offset = 0.0     # body-drag: click_secs - view_t0, captured at press time
        self._thumbnails: list = []   # QImage or None, one per evenly-spaced filmstrip slot
        self.setMouseTracking(True)   # so hover sets the cursor even with no button down
        self.setFixedHeight(_HEIGHT)  # room for the thumbnail row + timestamp ruler

    # The Overview sits directly above the Audio tracks section (see
    # review_tab.py) and is meant to read as one continuous timeline with
    # it — the video track below lines up with the waveform lanes above, not
    # start flush at the widget's own left edge while the lanes start ~180px
    # in. Overriding these two (everything else in TimelineBase/this class
    # paints relative to them) shifts the whole track right by the audio
    # lanes' own label-column width.
    def _track_x(self) -> int:
        return self._PAD + LANE_LABEL_MARGIN

    def _track_w(self) -> int:
        return max(1, self.width() - self._track_x() - self._PAD)

    def set_duration(self, dur: float):
        super().set_duration(dur)
        if self._view_t1 <= self._view_t0 or self._view_t1 > self._duration:
            self._view_t0, self._view_t1 = 0.0, self._duration
        # NB: do NOT clear the filmstrip here. The default (GPU) engine reports
        # duration ASYNCHRONOUSLY — this runs after _start_thumbnail_strip has
        # already reserved the slots and the worker has begun filling them, so
        # clearing would silently drop every arriving tile. A new master is
        # cleared authoritatively by load_master via set_thumbnail_count(0).

    def set_thumbnail_count(self, n: int):
        """Reserve `n` evenly-spaced filmstrip slots (call once duration is
        known); fill them one at a time via `set_thumbnail`."""
        self._thumbnails = [None] * max(0, n)
        self.update()

    def set_thumbnail(self, index: int, image):
        if 0 <= index < len(self._thumbnails):
            self._thumbnails[index] = image
            self.update()

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
        self._paint_label(p, pal)
        self._paint_thumbnails(p, pal)
        self._paint_envelope(p, pal)
        self._paint_ruler(p, pal)
        self._paint_viewport(p, pal)

    def _paint_label(self, p: QPainter, pal):
        """"Video" in the left margin, at the same x as an audio lane's own
        name label — the visual cue that this row and the ones below are the
        same timeline, not an unrelated gap."""
        f = p.font()
        f.setPixelSize(12)
        p.setFont(f)
        p.setPen(QColor(pal.text))
        p.drawText(QRectF(self._PAD, self._THUMB_Y, LANE_LABEL_MARGIN - self._PAD, self._THUMB_H),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, "Video")

    def _paint_thumbnails(self, p: QPainter, pal):
        """The filmstrip row: each reserved slot painted edge-to-edge across
        the track width — a coarse 'what's roughly where' map, not meant to
        line up frame-exactly with any particular second."""
        tx, tw = self._track_x(), self._track_w()
        p.fillRect(QRectF(tx, self._THUMB_Y, tw, self._THUMB_H), QColor(pal.input_dk))
        n = len(self._thumbnails)
        if n == 0:
            return
        cell_w = tw / n
        for i, img in enumerate(self._thumbnails):
            if img is None or (hasattr(img, "isNull") and img.isNull()):
                continue
            target = QRectF(tx + i * cell_w, self._THUMB_Y, cell_w, self._THUMB_H)
            p.drawImage(target, img, QRectF(img.rect()))
        p.setPen(QPen(QColor(pal.border), 1))
        p.drawRect(QRectF(tx, self._THUMB_Y, tw, self._THUMB_H))

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
        top = self._THUMB_Y + self._THUMB_H   # flush under the thumbnail row, no overlap
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
        # The playhead always wins if the click is right on it — even inside a
        # zoomed viewport — so scrubbing the actual position is never blocked
        # by the viewport-drag targets sharing the same area.
        if abs(px - self._secs_to_x(self._pos)) <= _SCRUB_TOL:
            return "pos"
        # Viewport-edge/body dragging only applies once the viewport is a real
        # sub-range. At full duration (the default right after loading a
        # master) x0/x1 sit at the track's own edges, so "anywhere inside"
        # would otherwise swallow the ENTIRE track and make scrubbing
        # impossible except in the last few pixels at each end.
        if self._duration > 0 and (self._view_t1 - self._view_t0) < self._duration - 1e-6:
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
