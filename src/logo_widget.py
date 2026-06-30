"""logo_widget.py — theme-aware LunaVault logo (swaps light/dark SVG)."""

from pathlib import Path

from PySide6.QtCore import Qt
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
