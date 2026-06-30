"""theme_toggle.py — compact Dark / Light / System segmented control."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget, QHBoxLayout, QPushButton

import theme

_OPTIONS = [
    ("dark",   "Dark"),
    ("light",  "Light"),
    ("system", "Auto"),
]


class ThemeToggle(QWidget):
    def __init__(self, controller):
        super().__init__()
        self._ctrl = controller
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._buttons: dict[str, QPushButton] = {}
        for mode, label in _OPTIONS:
            b = QPushButton(label)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFixedHeight(22)
            b.setToolTip(f"{label} theme")
            b.clicked.connect(lambda _=False, m=mode: self._select(m))
            self._buttons[mode] = b
            lay.addWidget(b)
        controller.changed.connect(self._restyle)
        self._restyle()

    def _select(self, mode: str):
        self._ctrl.set_mode(mode)   # triggers changed → _restyle

    def _restyle(self):
        p = theme.active_palette()
        active = self._ctrl.mode
        n = len(_OPTIONS)
        for i, (mode, _label) in enumerate(_OPTIONS):
            b = self._buttons[mode]
            b.setChecked(mode == active)
            # rounded only on the outer edges of the segmented control
            left  = "6px" if i == 0 else "0"
            right = "6px" if i == n - 1 else "0"
            if mode == active:
                bg, fg, bd = p.accent, p.on_accent(), p.accent
            else:
                bg, fg, bd = p.surface, p.text_dim, p.border
            b.setStyleSheet(
                f"QPushButton {{ background:{bg}; color:{fg}; border:1px solid {bd}; "
                f"border-top-left-radius:{left}; border-bottom-left-radius:{left}; "
                f"border-top-right-radius:{right}; border-bottom-right-radius:{right}; "
                "padding:2px 9px; font-size:11px; }"
                f"QPushButton:hover {{ color:{p.accent if mode != active else fg}; }}"
            )
