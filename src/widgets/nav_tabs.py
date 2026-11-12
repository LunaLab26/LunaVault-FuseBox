"""NavTabs — the app's page switcher: a grouped header over a QStackedWidget.

Replaces the flat seven-tab QTabWidget bar. Pages are added to one of three
groups so the everyday path stays up front and the power tools step back:

  primary  — big nav buttons on the left (Memories, Add memories)
  tools    — a smaller, labelled group after a divider (Merge, Review, Recover)
  menu     — no button; reached from the "⋯" overflow menu (Activity log, About)

Keeps the subset of the QTabWidget API the rest of the app (and its tests)
uses: addTab / removeTab / count / widget / indexOf / tabText / currentIndex /
currentWidget / setCurrentIndex / setCurrentWidget / currentChanged.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QMenu, QPushButton,
                               QStackedWidget, QToolButton, QVBoxLayout, QWidget)

import theme


class NavTabs(QWidget):
    currentChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pages: list = []          # [(widget, text, group)]
        self._buttons: dict = {}        # widget -> QPushButton (primary/tools only)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._header = QFrame()
        self._header.setObjectName("NavHeader")
        hl = QHBoxLayout(self._header)
        hl.setContentsMargins(16, 8, 12, 8)
        hl.setSpacing(6)
        self._primary_row = QHBoxLayout()
        self._primary_row.setSpacing(4)
        hl.addLayout(self._primary_row)
        self._divider = QFrame()
        self._divider.setFixedSize(1, 22)
        hl.addSpacing(10)
        hl.addWidget(self._divider)
        hl.addSpacing(10)
        self._tools_label = QLabel("Tools")
        hl.addWidget(self._tools_label)
        self._tools_row = QHBoxLayout()
        self._tools_row.setSpacing(2)
        hl.addLayout(self._tools_row)
        hl.addStretch(1)
        self._corner_slot = QHBoxLayout()
        self._corner_slot.setSpacing(8)
        hl.addLayout(self._corner_slot)

        self._more = QToolButton()
        self._more.setText("•••")   # "⋯" (U+22EF) renders tiny in Segoe UI; bullets read on every font
        self._more.setToolTip("Activity log, About FuseBox")
        self._more.setCursor(Qt.CursorShape.PointingHandCursor)
        self._more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self._menu = QMenu(self._more)
        self._more.setMenu(self._menu)
        self._corner_slot.addWidget(self._more)

        self._stack = QStackedWidget()
        self._stack.currentChanged.connect(self._on_stack_changed)
        outer.addWidget(self._header)
        outer.addWidget(self._stack, 1)

        ctrl = theme.controller()
        if ctrl is not None:
            ctrl.changed.connect(self._restyle)
        self._restyle()

    # ── QTabWidget-compatible API ─────────────────────────────────────────────
    def addTab(self, widget, text: str, group: str = "primary") -> int:
        self._pages.append((widget, text, group))
        self._stack.addWidget(widget)
        if group in ("primary", "tools"):
            b = QPushButton(text)
            b.setCheckable(True)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setProperty("navGroup", group)
            b.clicked.connect(lambda _=False, w=widget: self.setCurrentWidget(w))
            (self._primary_row if group == "primary" else self._tools_row).addWidget(b)
            self._buttons[widget] = b
        self._rebuild_menu()
        self._sync()
        return len(self._pages) - 1

    def removeTab(self, index: int):
        if not 0 <= index < len(self._pages):
            return
        widget, _text, _group = self._pages.pop(index)
        self._stack.removeWidget(widget)
        b = self._buttons.pop(widget, None)
        if b is not None:
            b.setParent(None)
            b.deleteLater()
        self._rebuild_menu()
        self._sync()

    def count(self) -> int:
        return len(self._pages)

    def widget(self, index: int):
        return self._pages[index][0] if 0 <= index < len(self._pages) else None

    def indexOf(self, widget) -> int:
        for i, (w, _t, _g) in enumerate(self._pages):
            if w is widget:
                return i
        return -1

    def tabText(self, index: int) -> str:
        return self._pages[index][1] if 0 <= index < len(self._pages) else ""

    def currentWidget(self):
        return self._stack.currentWidget()

    def currentIndex(self) -> int:
        return self.indexOf(self._stack.currentWidget())

    def setCurrentIndex(self, index: int):
        w = self.widget(index)
        if w is not None:
            self._stack.setCurrentWidget(w)

    def setCurrentWidget(self, widget):
        if self.indexOf(widget) >= 0:
            self._stack.setCurrentWidget(widget)

    def setDocumentMode(self, _on: bool):   # QTabWidget compatibility; no-op
        pass

    def addCornerWidget(self, widget):
        """Right-hand header area, before the ⋯ menu."""
        # A plain QWidget container would otherwise take the app-wide
        # `QWidget { background:<page bg> }` rule and show as a lighter box on
        # the header. Scoped by object name so its children keep their styles.
        if not widget.objectName():
            widget.setObjectName("NavCorner")
        widget.setStyleSheet(widget.styleSheet() +
                             f"QWidget#{widget.objectName()} {{ background:transparent; }}")
        self._corner_slot.insertWidget(self._corner_slot.count() - 1, widget)

    def setMenuExtras(self, build):
        """`build(menu)` appends extra items (e.g. Appearance) after the pages."""
        self._menu_extras = build
        self._rebuild_menu()

    # ── internals ─────────────────────────────────────────────────────────────
    def _on_stack_changed(self, _i: int):
        self._sync()
        if not self.signalsBlocked():
            self.currentChanged.emit(self.currentIndex())

    def _sync(self):
        cur = self._stack.currentWidget()
        for w, b in self._buttons.items():
            b.setChecked(w is cur)
        has_tools = any(g == "tools" for _w, _t, g in self._pages)
        has_primary = any(g == "primary" for _w, _t, g in self._pages)
        self._tools_label.setVisible(has_tools)
        self._divider.setVisible(has_tools and has_primary)

    def _rebuild_menu(self):
        self._menu.clear()
        for w, text, group in self._pages:
            if group == "menu":
                act = self._menu.addAction(text)
                act.triggered.connect(lambda _=False, ww=w: self.setCurrentWidget(ww))
        build = getattr(self, "_menu_extras", None)
        if build:
            self._menu.addSeparator()
            build(self._menu)
        self._more.setVisible(not self._menu.isEmpty())

    def _restyle(self):
        p = theme.active_palette()
        self._header.setStyleSheet(
            f"QFrame#NavHeader {{ background:{p.panel}; border-bottom:1px solid {p.border_dk}; }}"
            # primary: rounded pills, bold when active
            f"QPushButton[navGroup='primary'] {{ background:transparent; color:{p.text_mute}; "
            f"border:none; border-radius:16px; padding:6px 16px; font-size:15px; }}"
            f"QPushButton[navGroup='primary']:hover {{ color:{p.text}; background:{p.hover_bg}; }}"
            f"QPushButton[navGroup='primary']:checked {{ color:{p.on_accent()}; background:{p.accent}; "
            f"font-weight:600; }}"
            # tools: quieter text links with an underline when active
            f"QPushButton[navGroup='tools'] {{ background:transparent; color:{p.text_dim}; "
            f"border:none; border-bottom:2px solid transparent; padding:5px 10px; font-size:13px; }}"
            f"QPushButton[navGroup='tools']:hover {{ color:{p.text}; }}"
            f"QPushButton[navGroup='tools']:checked {{ color:{p.text}; border-bottom:2px solid {p.accent}; }}"
            f"QToolButton {{ background:transparent; color:{p.text_mute}; border:1px solid {p.border}; "
            f"border-radius:14px; padding:2px 10px; font-size:10px; min-height:22px; }}"
            f"QToolButton:hover {{ color:{p.text}; border-color:{p.accent}; }}"
            "QToolButton::menu-indicator { image: none; width:0; }")
        self._divider.setStyleSheet(f"background:{p.border};")
        self._tools_label.setStyleSheet(
            f"color:{p.text_dim}; font-size:11px; letter-spacing:1px; text-transform:uppercase;")
