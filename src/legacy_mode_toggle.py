"""legacy_mode_toggle.py — hidden "User friendly / Legacy mode" switch.

Not part of the normal UI: it lives next to the theme toggle in the window's
top-right corner but stays invisible until the logo is triple-clicked
(logo_widget.TripleClickArea). Lets the current Home/Collection/Memory build be
compared against, or rolled back to, the exact pre-overhaul tab set — without a
second build. Styled identically to theme_toggle.py's segmented control.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QWidget, QHBoxLayout, QPushButton

import theme

_OPTIONS = [
    ("friendly", "User friendly"),
    ("legacy",   "Legacy mode"),
]


class LegacyModeToggle(QWidget):
    mode_changed = Signal(str)   # "friendly" | "legacy" — only fires on an actual change

    def __init__(self, initial: str = "friendly"):
        super().__init__()
        self._mode = initial if initial in ("friendly", "legacy") else "friendly"
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        self._buttons: dict = {}
        for mode, label in _OPTIONS:
            b = QPushButton(label)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setFixedHeight(22)
            b.setToolTip(f"Switch to {label}")
            b.clicked.connect(lambda _=False, m=mode: self._select(m))
            self._buttons[mode] = b
            lay.addWidget(b)
        ctrl = theme.controller()
        if ctrl is not None:
            ctrl.changed.connect(self._restyle)
        self._restyle()

    def mode(self) -> str:
        return self._mode

    def _select(self, mode: str):
        if mode == self._mode:
            self._restyle()   # re-press of the active option — just re-sync the checked state
            return
        self._mode = mode
        self._restyle()
        self.mode_changed.emit(mode)

    def _restyle(self):
        p = theme.active_palette()
        n = len(_OPTIONS)
        for i, (mode, _label) in enumerate(_OPTIONS):
            b = self._buttons[mode]
            b.setChecked(mode == self._mode)
            left  = "6px" if i == 0 else "0"
            right = "6px" if i == n - 1 else "0"
            if mode == self._mode:
                bg, fg, bd = p.accent, p.on_accent(), p.accent
            else:
                bg, fg, bd = p.surface, p.text_dim, p.border
            b.setStyleSheet(
                f"QPushButton {{ background:{bg}; color:{fg}; border:1px solid {bd}; "
                f"border-top-left-radius:{left}; border-bottom-left-radius:{left}; "
                f"border-top-right-radius:{right}; border-bottom-right-radius:{right}; "
                "padding:2px 9px; font-size:11px; }"
                f"QPushButton:hover {{ color:{p.accent if mode != self._mode else fg}; }}"
            )
