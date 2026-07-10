"""storage_compare.py — the storage-choice decision aid (task #15).

Every collection is ALWAYS kept as one archival master (the default, and the
workflow the app is built around: dozens of clips in one file that both uploads
to YouTube and archives long-term, with any original recoverable on demand).

Folder storage is an *additional*, opt-in layer: it also writes every memory out
as its own real file plus a self-contained album.html (core/portable.py), so the
collection browses and plays on any device with no app — at the cost of extra
disk space. This module presents the two side by side as an honest decision aid
and lets the user opt in; it never changes the default.

Pure Qt, themed entirely from theme.active_palette() (no literal colours), and
offscreen-instantiable so it can be tested headless.
"""

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QFrame,
)

import theme
from core import collection as collection_mod


def _mini_diagram(kind: str) -> QWidget:
    """A tiny, themed illustration of each storage shape — no external assets.
    `kind` is "master" (one file feeding YouTube + archive) or "folder" (a folder
    of separate clip files + an album page)."""
    p = theme.active_palette()
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    lay.setSpacing(8)
    lay.setAlignment(Qt.AlignCenter)

    def chip(text: str, *, accent: bool = False) -> QLabel:
        lbl = QLabel(text)
        lbl.setAlignment(Qt.AlignCenter)
        bg = p.accent if accent else p.surface
        fg = p.on_accent() if accent else p.text_mute
        lbl.setStyleSheet(
            f"background:{bg}; color:{fg}; border:1px solid {p.border}; "
            f"border-radius:{p.radius_sm}px; padding:6px 10px; font-size:12px;")
        return lbl

    if kind == "master":
        lay.addWidget(chip("🎞  one master", accent=True))
        arrow = QLabel("→")
        arrow.setStyleSheet(f"background:transparent; color:{p.text_mute}; font-size:16px;")
        lay.addWidget(arrow)
        col = QVBoxLayout()
        col.setSpacing(4)
        col.addWidget(chip("▶  YouTube"))
        col.addWidget(chip("🗄  archive"))
        lay.addLayout(col)
    else:  # folder
        folder = QVBoxLayout()
        folder.setSpacing(4)
        row = QHBoxLayout()
        row.setSpacing(4)
        for _ in range(3):
            row.addWidget(chip("▦"))
        folder.addLayout(row)
        folder.addWidget(chip("🌐  album.html"))
        box = QFrame()
        box.setStyleSheet(
            f"background:transparent; border:1px dashed {p.border_hi}; "
            f"border-radius:{p.radius}px;")
        box.setLayout(folder)
        folder.setContentsMargins(8, 8, 8, 8)
        lay.addWidget(box)
    return w


def _option_card(title: str, diagram_kind: str, bullets, *, badge: str = "") -> QFrame:
    """One themed option card: a badge, title, illustration, and trade-off bullets.
    The action button is added by the caller so it can own the wiring."""
    p = theme.active_palette()
    card = QFrame()
    card.setObjectName("storageCard")
    card.setStyleSheet(
        f"#storageCard {{ background:{p.surface2}; border:1px solid {p.border}; "
        f"border-radius:{p.radius}px; }}")
    lay = QVBoxLayout(card)
    lay.setContentsMargins(16, 14, 16, 16)
    lay.setSpacing(10)

    if badge:
        b = QLabel(badge)
        b.setStyleSheet(
            f"background:{p.surface}; color:{p.accent}; border:1px solid {p.border}; "
            f"border-radius:{p.radius_sm}px; padding:2px 8px; font-size:11px; font-weight:bold;")
        b.setAlignment(Qt.AlignLeft)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(b, 0, Qt.AlignLeft)
        row.addStretch(1)
        lay.addLayout(row)

    t = QLabel(title)
    t.setStyleSheet(f"background:transparent; color:{p.text}; font-size:16px; font-weight:500;")
    t.setWordWrap(True)
    lay.addWidget(t)

    lay.addWidget(_mini_diagram(diagram_kind))
    lay.addSpacing(2)

    for text in bullets:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(7)
        dot = QLabel("•")
        dot.setStyleSheet(f"background:transparent; color:{p.accent};")
        dot.setAlignment(Qt.AlignTop)
        line = QLabel(text)
        line.setStyleSheet(f"background:transparent; color:{p.text_mute}; font-size:12px;")
        line.setWordWrap(True)
        row.addWidget(dot, 0, Qt.AlignTop)
        row.addWidget(line, 1)
        lay.addLayout(row)

    lay.addStretch(1)
    return card


class StorageCompareView(QWidget):
    """The two-card comparison. Emits `chose_portable` when the user opts into the
    additional folder-of-files layer. If the collection is already portable, that
    side shows as current and its button is disabled."""
    chose_portable = Signal()
    dismissed = Signal()

    def __init__(self, current_mode: str = collection_mod.STORAGE_COMPACT, parent=None):
        super().__init__(parent)
        p = theme.active_palette()
        already_portable = (current_mode == collection_mod.STORAGE_PORTABLE)

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 22, 24, 20)
        root.setSpacing(6)

        head = QLabel("How should this collection be stored?")
        head.setStyleSheet(f"background:transparent; color:{p.text}; font-size:19px; font-weight:500;")
        root.addWidget(head)
        sub = QLabel("Both keep every memory safe and recoverable — this only changes "
                     "whether the clips also live as separate files on disk.")
        sub.setStyleSheet(f"background:transparent; color:{p.text_mute};")
        sub.setWordWrap(True)
        root.addWidget(sub)
        root.addSpacing(8)

        cards = QHBoxLayout()
        cards.setSpacing(16)

        master_card = _option_card(
            "Just the master file",
            "master",
            ["A single archival master — the file you already have.",
             "Uploads to YouTube and archives long-term, all in one.",
             "Recover any original, exactly as filmed, whenever you want.",
             "The most compact way to keep everything."],
            badge="Current" if not already_portable else "")
        keep_btn = QPushButton("Keep as one master")
        keep_btn.setCursor(Qt.PointingHandCursor)
        keep_btn.clicked.connect(self.dismissed.emit)
        _card_add_button(master_card, keep_btn)
        cards.addWidget(master_card, 1)

        folder_card = _option_card(
            "Also keep separate files",
            "folder",
            ["Also writes every memory as its own real file, plus an album page.",
             "Browses and plays on any device — no app needed.",
             "Great for cloud sync and handing the folder to family.",
             "Uses more disk space (the master is kept too)."],
            badge="Current" if already_portable else "Optional")
        if already_portable:
            done_btn = QPushButton("Already saved as files")
            done_btn.setEnabled(False)
            _card_add_button(folder_card, done_btn)
        else:
            add_btn = _accent(QPushButton("Also save separate files"))
            add_btn.clicked.connect(self.chose_portable.emit)
            _card_add_button(folder_card, add_btn)
        cards.addWidget(folder_card, 1)

        root.addLayout(cards)


def _card_add_button(card: QFrame, btn: QPushButton) -> None:
    """Drop a full-width action button into the bottom of an option card."""
    card.layout().addWidget(btn)


def _accent(btn: QPushButton) -> QPushButton:
    p = theme.active_palette()
    btn.setCursor(Qt.PointingHandCursor)
    btn.setStyleSheet(
        f"QPushButton {{ background:{p.accent}; color:{p.on_accent()}; border:none; "
        f"border-radius:{p.radius}px; padding:8px 16px; font-weight:500; }}"
        f"QPushButton:hover {{ background:{p.accent_hi}; }}")
    return btn


class StorageCompareDialog(QDialog):
    """Modal wrapper around StorageCompareView. `exec()` returns QDialog.Accepted
    if the user opted into the additional folder-of-files layer."""

    def __init__(self, current_mode: str = collection_mod.STORAGE_COMPACT, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Storage options")
        self.setModal(True)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        self.view = StorageCompareView(current_mode, self)
        self.view.chose_portable.connect(self.accept)
        self.view.dismissed.connect(self.reject)
        lay.addWidget(self.view)
