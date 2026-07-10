"""widgets/video_view.py — the Review tab's zoomable, pannable preview.

ZoomableVideoView paints the latest QImage handed to it via `set_frame()`.
Zoom is Fit / 1:1 / an arbitrary percent; dragging pans when zoomed past
fit. Painting uses FastTransformation while playing (cheap) and
SmoothTransformation with a cached scaled pixmap once paused (sharp,
recomputed only when the frame or zoom actually changes) — a 4K frame at
30fps is too much to smooth-scale every paint.

A snapshot always saves the FULL-RESOLUTION frame via ffmpeg regardless of
the view's zoom (see review_workers.FrameFetchWorker) — this widget's
`flash_snapshot()` is purely the visual acknowledgement.
"""

from typing import Optional

from PySide6.QtCore import Qt, QEvent, QPointF, QRectF, QVariantAnimation, Signal
from PySide6.QtGui import QPainter, QColor, QImage, QPixmap
from PySide6.QtWidgets import QWidget

import theme

_MIN_ZOOM_PCT = 10.0
_MAX_ZOOM_PCT = 800.0
_PREVIEW_ASPECT = 16.0 / 9.0   # the preview area itself is always letterboxed to this


class _SnapshotFlashOverlay(QWidget):
    """A brief white flash that shrinks into the top-right corner — the
    visual acknowledgement that a snapshot was captured."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._opacity = 0.0
        self._rect = QRectF()
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(380)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.valueChanged.connect(self._on_tick)
        self._anim.finished.connect(self.hide)

    def play(self):
        parent = self.parentWidget()
        if parent is not None:
            self.setGeometry(parent.rect())
        self.show()
        self.raise_()
        self._anim.stop()
        self._anim.start()

    def _on_tick(self, t: float):
        self._opacity = (t / 0.15) if t <= 0.15 else max(0.0, 1.0 - (t - 0.15) / 0.85)
        full = QRectF(self.rect())
        thumb_w, thumb_h = full.width() * 0.14, full.height() * 0.14
        thumb = QRectF(full.right() - thumb_w - 14, full.top() + 14, thumb_w, thumb_h)
        ease = t * t   # accelerate into the corner
        self._rect = QRectF(
            full.x() + (thumb.x() - full.x()) * ease,
            full.y() + (thumb.y() - full.y()) * ease,
            full.width() + (thumb.width() - full.width()) * ease,
            full.height() + (thumb.height() - full.height()) * ease,
        )
        self.update()

    def paintEvent(self, event):
        if self._opacity <= 0.0:
            return
        p = QPainter(self)
        p.setOpacity(self._opacity)
        p.fillRect(self._rect, QColor(255, 255, 255))
        p.end()


class ZoomableVideoView(QWidget):
    zoom_changed = Signal(float)   # effective zoom fraction, 1.0 = 100%

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(160, 90)
        self._image: Optional[QImage] = None
        self._zoom_mode = "fit"     # "fit" | "1:1" | "percent"
        self._zoom_pct = 100.0
        self._pan = QPointF(0, 0)
        self._dragging = False
        self._drag_start = QPointF()
        self._pan_start = QPointF()
        self._playing = False
        self._cached_pixmap: Optional[QPixmap] = None
        self._cached_pixmap_key = None

        self._flash = _SnapshotFlashOverlay(self)
        self._flash.hide()

        ctrl = theme.controller()
        if ctrl is not None:
            ctrl.changed.connect(self.update)

    # ── Public API ────────────────────────────────────────────────────────────

    def set_frame(self, image: QImage):
        self._image = image
        self._cached_pixmap = None
        self.update()

    def set_playing(self, playing: bool):
        if playing != self._playing:
            self._cached_pixmap = None
        self._playing = playing
        self.update()

    def set_zoom_fit(self):
        self._zoom_mode = "fit"
        self._pan = QPointF(0, 0)
        self._cached_pixmap = None
        self.update()
        self.zoom_changed.emit(self.effective_zoom())

    def set_zoom_1to1(self):
        self._zoom_mode = "1:1"
        self._cached_pixmap = None
        self.update()
        self.zoom_changed.emit(1.0)

    def set_zoom_percent(self, pct: float):
        self._zoom_mode = "percent"
        self._zoom_pct = max(_MIN_ZOOM_PCT, min(_MAX_ZOOM_PCT, pct))
        self._cached_pixmap = None
        self.update()
        self.zoom_changed.emit(self._zoom_pct / 100.0)

    def effective_zoom(self) -> float:
        if self._image is None or self._image.isNull():
            return 1.0
        aw, ah = self._active_size()
        if self._zoom_mode == "fit":
            iw, ih = self._image.width(), self._image.height()
            if iw <= 0 or ih <= 0 or aw <= 0 or ah <= 0:
                return 1.0
            return min(aw / iw, ah / ih)
        if self._zoom_mode == "1:1":
            return 1.0
        return self._zoom_pct / 100.0

    def flash_snapshot(self):
        self._flash.play()

    def zoom_mode(self) -> str:
        return self._zoom_mode

    # ── Aspect-locked active area ─────────────────────────────────────────────
    #
    # The preview always letterboxes to 16:9 within whatever rect the layout
    # actually gives this widget — matching real footage's own aspect rather
    # than stretching/cropping to fill an arbitrary panel shape. Zoom, pan,
    # and painting all operate relative to this "active rect", not the raw
    # widget rect.

    def _active_size(self) -> tuple:
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return w, h
        if w / h > _PREVIEW_ASPECT:
            return max(1, round(h * _PREVIEW_ASPECT)), h
        return w, max(1, round(w / _PREVIEW_ASPECT))

    def _active_rect(self) -> QRectF:
        aw, ah = self._active_size()
        x = (self.width() - aw) / 2
        y = (self.height() - ah) / 2
        return QRectF(x, y, aw, ah)

    # ── Painting ──────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        pal = theme.active_palette()
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(pal.bg))
        active = self._active_rect()
        if active.width() < self.width() or active.height() < self.height():
            # Dead space outside the 16:9 box reads as "not part of the player",
            # not just more background — a subtly darker letterbox tone.
            p.fillRect(self.rect(), QColor(pal.input_dk))
            p.fillRect(active, QColor(pal.bg))
        if self._image is None or self._image.isNull():
            p.end()
            return

        zoom = self.effective_zoom()
        iw, ih = self._image.width(), self._image.height()
        target_w = max(1, round(iw * zoom))
        target_h = max(1, round(ih * zoom))

        key = (target_w, target_h, self._playing, id(self._image))
        if self._cached_pixmap is None or self._cached_pixmap_key != key:
            mode = (Qt.TransformationMode.FastTransformation if self._playing
                   else Qt.TransformationMode.SmoothTransformation)
            pm = QPixmap.fromImage(self._image)
            self._cached_pixmap = pm.scaled(target_w, target_h,
                                            Qt.AspectRatioMode.IgnoreAspectRatio, mode)
            self._cached_pixmap_key = key

        cx = active.x() + (active.width() - target_w) / 2 + self._pan.x()
        cy = active.y() + (active.height() - target_h) / 2 + self._pan.y()
        p.drawPixmap(int(cx), int(cy), self._cached_pixmap)
        p.end()

    def resizeEvent(self, event):
        self._cached_pixmap = None
        if self._flash is not None:
            self._flash.setGeometry(self.rect())
        super().resizeEvent(event)

    # ── Drag to pan (meaningful once zoomed past fit) ─────────────────────────

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_start = event.position()
            self._pan_start = QPointF(self._pan)

    def mouseMoveEvent(self, event):
        if self._dragging:
            delta = event.position() - self._drag_start
            self._pan = self._pan_start + delta
            self.update()

    def mouseReleaseEvent(self, event):
        self._dragging = False

    # ── Zoom: scroll wheel + touchpad pinch (zoom around the cursor) ───────────

    def _zoom_around(self, pos: QPointF, factor: float):
        """Scale by `factor`, keeping the image point under `pos` fixed — the
        web-browser zoom feel. Switches to explicit percent mode."""
        if self._image is None or self._image.isNull() or factor <= 0:
            return
        old_zoom = self.effective_zoom()
        new_pct = max(_MIN_ZOOM_PCT, min(_MAX_ZOOM_PCT, old_zoom * 100.0 * factor))
        new_zoom = new_pct / 100.0
        if abs(new_zoom - old_zoom) < 1e-6:
            return
        iw, ih = self._image.width(), self._image.height()
        active = self._active_rect()
        cx = active.x() + (active.width() - iw * old_zoom) / 2 + self._pan.x()
        cy = active.y() + (active.height() - ih * old_zoom) / 2 + self._pan.y()
        img_x = (pos.x() - cx) / old_zoom
        img_y = (pos.y() - cy) / old_zoom
        self._zoom_mode = "percent"
        self._zoom_pct = new_pct
        self._pan = QPointF(
            pos.x() - img_x * new_zoom - active.x() - (active.width() - iw * new_zoom) / 2,
            pos.y() - img_y * new_zoom - active.y() - (active.height() - ih * new_zoom) / 2)
        self._cached_pixmap = None
        self.update()
        self.zoom_changed.emit(new_zoom)

    def wheelEvent(self, event):
        if self._image is None or self._image.isNull():
            return
        dy = event.angleDelta().y()
        if dy == 0:
            return
        self._zoom_around(event.position(), 1.0015 ** dy)   # one notch (±120) ≈ ±20%
        event.accept()

    def event(self, e):
        # Touchpad pinch arrives as a native gesture, not a wheel event.
        if (e.type() == QEvent.Type.NativeGesture
                and e.gestureType() == Qt.NativeGestureType.ZoomNativeGesture):
            self._zoom_around(e.position(), 1.0 + e.value())
            return True
        return super().event(e)
