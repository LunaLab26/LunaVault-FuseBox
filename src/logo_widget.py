"""logo_widget.py — theme-aware LunaVault logo (swaps light/dark SVG)."""

import time
from pathlib import Path

from PySide6.QtCore import Qt, QEvent, Signal
from PySide6.QtSvgWidgets import QSvgWidget
from PySide6.QtWidgets import QHBoxLayout, QWidget

import theme

_ASSETS    = Path(__file__).parent / "assets"
_DARK_SVG  = _ASSETS / "lunavault_fusebox_logo_v3a.svg"     # cream text
_LIGHT_SVG = _ASSETS / "lunavault_fusebox_logo_light.svg"   # dark text
_ASPECT    = 370 / 100   # viewBox 370x100


class LogoWidget(QWidget):
    """Logo that re-renders its light/dark SVG when the theme changes."""

    def __init__(self, height: int = 48):
        super().__init__()
        self.setStyleSheet("background:transparent;")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._svg = QSvgWidget()
        self._svg.setFixedSize(int(height * _ASPECT), height)
        self._svg.setStyleSheet("background:transparent;")
        lay.addWidget(self._svg, 0, Qt.AlignmentFlag.AlignVCenter)

        ctrl = theme.controller()
        if ctrl is not None:
            ctrl.changed.connect(self._refresh)
        self._refresh()

    def _refresh(self):
        path = _LIGHT_SVG if theme.active_palette().is_light else _DARK_SVG
        if path.exists():
            self._svg.load(str(path))


def make_logo_widget(height: int = 48) -> QWidget:
    """Backwards-compatible factory used by the About tab (full lockup)."""
    return LogoWidget(height)


_ICON_SVG = _ASSETS / "lunavault_icon.svg"   # owl mark only (145×100), theme-neutral


def make_icon_widget(height: int = 30) -> QWidget:
    """Compact owl-only mark for the window corner (no wordmark — saves width)."""
    container = QWidget()
    container.setStyleSheet("background:transparent;")
    lay = QHBoxLayout(container)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(0)
    if _ICON_SVG.exists():
        svg = QSvgWidget(str(_ICON_SVG))
        svg.setFixedSize(int(height * 145 / 100), height)
        svg.setStyleSheet("background:transparent;")
        lay.addWidget(svg, 0, Qt.AlignmentFlag.AlignVCenter)
    return container


class TripleClickArea(QWidget):
    """Wraps a widget and emits `tripleClicked` on three quick clicks anywhere on
    it — used to reveal a hidden control (the legacy/friendly mode toggle)
    without adding any visible affordance. An event filter is installed on
    `child` ONLY, not its descendants: Qt bubbles an unhandled mouse press up
    from a non-interactive descendant (e.g. the SVG icon here, which has no
    click handling of its own and so ignores the event) to its parent — filtering
    both would double-count the same physical click (confirmed directly: a
    single press on the SVG fired the filter twice, once for the SVG and once
    for the bubbled copy on the container). This wrapper is only meant for
    non-interactive content like an icon; a genuinely interactive child could
    consume the event before it bubbles here."""
    tripleClicked = Signal()

    _WINDOW_S = 0.8   # 3 clicks must land within this span to count as "triple"

    def __init__(self, child: QWidget):
        super().__init__()
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(child)
        self._times: list = []
        child.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonDblClick):
            self._register_click()
        return super().eventFilter(obj, event)

    def _register_click(self):
        now = time.monotonic()
        self._times = [t for t in self._times if now - t < self._WINDOW_S] + [now]
        if len(self._times) >= 3:
            self._times = []
            self.tripleClicked.emit()
