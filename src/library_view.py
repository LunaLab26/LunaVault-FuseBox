"""library_view.py — the everyday hub: Home → Collection → Memory.

The walking skeleton of the product spine (BUILD_PLAN.md Phase 2). Home reads the
catalog and shows a shelf of collections; opening one shows its album (from the
folder's own manifest + thumbs); opening a memory shows play / save-the-original.
Reads only the light local index, so it renders instantly and offline.

Navigation is signal-driven and lives in `LibraryView` (a QStackedWidget), which
is added to the main window alongside the classic tabs. Actions (play / save /
share) emit signals; the real wiring is Phase 3. Offscreen-instantiable.
"""

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPixmap, QCursor
from PySide6.QtWidgets import (
    QWidget, QLabel, QPushButton, QScrollArea, QGridLayout, QVBoxLayout,
    QHBoxLayout, QFrame, QStackedWidget, QSizePolicy, QMenu, QInputDialog,
    QMessageBox, QDialog,
)

import theme
from settings import _settings_path
from core import catalog as catalog_mod
from core import collection as collection_mod
from core import manifest as manifest_mod

_FIDELITY_WORDS = {
    "byte-exact": "byte for byte",
    "decode-lossless": "exactly as filmed",
    "transcoded": "high-quality copy",
}


def app_dir() -> Path:
    return _settings_path().parent


def _thumb_pixmap(path: Path, w: int, h: int) -> Optional[QPixmap]:
    try:
        if path and Path(path).exists():
            pm = QPixmap(str(path))
            if not pm.isNull():
                return pm.scaled(w, h, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
    except Exception:
        pass
    return None


def _style_accent(b: QPushButton) -> QPushButton:
    """(Re)apply friendly filled-accent styling to a button, from the CURRENT
    palette — safe to call again on a theme change to repaint it."""
    p = theme.active_palette()
    b.setCursor(Qt.PointingHandCursor)
    b.setStyleSheet(
        f"QPushButton {{ background:{p.accent}; color:{p.on_accent()}; border:none; "
        f"border-radius:{p.radius}px; padding:8px 18px; font-weight:500; }}"
        f"QPushButton:hover {{ background:{p.accent_hi}; }}")
    return b


def _accent_button(text: str) -> QPushButton:
    """A friendly filled-accent button for primary actions on the new screens."""
    return _style_accent(QPushButton(text))


class _Tile(QFrame):
    """A clickable memory/collection card: cover image, title, subtitle, badge.

    With `show_menu`, a "⋯" button in the title row emits `menu_requested` so the
    shelf can offer rename / reorder / remove without stealing the card's click."""
    clicked = Signal()
    menu_requested = Signal()

    def __init__(self, title: str, subtitle: str, cover: Optional[Path],
                 badge: str = "", show_menu: bool = False):
        super().__init__()
        p = theme.active_palette()
        self.setObjectName("memoryTile")
        self.setCursor(Qt.PointingHandCursor)
        # A real card: soft surface, hairline border that warms to the accent on
        # hover, rounded corners and breathing room (friendly-mode only screens).
        self.setStyleSheet(
            f"#memoryTile {{ background:{p.surface2}; border:1px solid {p.border}; border-radius:12px; }}"
            f"#memoryTile:hover {{ border-color:{p.accent}; }}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(10, 10, 10, 12)
        lay.setSpacing(7)

        img = QLabel()
        img.setFixedHeight(132)
        img.setAlignment(Qt.AlignCenter)
        img.setStyleSheet(f"background:{p.input_dk}; border-radius:8px; "
                          f"color:{p.text_mute}; font-size:22px;")
        pm = _thumb_pixmap(cover, 240, 132) if cover else None
        if pm is not None:
            img.setPixmap(pm)
        else:
            img.setText("▶")
        lay.addWidget(img)

        # The text labels must be transparent — otherwise they inherit the global
        # QWidget window background (darker than this card) and paint ugly dark
        # boxes over the surface, which reads as harsh contrast and hurts
        # readability. Transparent lets them sit cleanly on the card.
        t = QLabel(title)
        t.setStyleSheet(f"background:transparent; font-weight:500; color:{p.text};")
        t.setWordWrap(True)
        if show_menu:
            trow = QHBoxLayout()
            trow.setContentsMargins(0, 0, 0, 0)
            trow.setSpacing(4)
            trow.addWidget(t, 1)
            menu_btn = QPushButton("⋮")
            menu_btn.setCursor(Qt.PointingHandCursor)
            menu_btn.setFixedSize(30, 26)
            menu_btn.setToolTip("Rename, reorder or remove")
            # Always visible (a clear, always-on affordance in a subtle chip), and
            # warms to the accent on hover so it's obviously interactive.
            menu_btn.setStyleSheet(
                f"QPushButton {{ background:{p.surface}; border:1px solid {p.border}; "
                f"color:{p.text}; font-size:19px; font-weight:bold; padding:0; "
                f"border-radius:{p.radius_sm}px; }}"
                f"QPushButton:hover {{ background:{p.accent}; border-color:{p.accent}; "
                f"color:{p.on_accent()}; }}")
            menu_btn.clicked.connect(self.menu_requested.emit)
            trow.addWidget(menu_btn, 0, Qt.AlignTop)
            lay.addLayout(trow)
        else:
            lay.addWidget(t)
        if subtitle:
            s = QLabel(subtitle)
            s.setStyleSheet(f"background:transparent; color:{p.text_mute}; font-size:12px;")
            lay.addWidget(s)
        if badge:
            b = QLabel("✓ " + badge)
            b.setStyleSheet(f"background:transparent; color:{p.ok}; font-size:11px;")
            lay.addWidget(b)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(e)


def _grid_scroll() -> tuple:
    scroll = QScrollArea()
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.NoFrame)
    inner = QWidget()
    grid = QGridLayout(inner)
    grid.setContentsMargins(2, 2, 6, 6)
    grid.setSpacing(18)
    scroll.setWidget(inner)
    return scroll, grid


class HomeView(QWidget):
    """The shelf of collections, read from the catalog cache."""
    open_collection = Signal(str)   # folder path
    add_memories = Signal()

    _COLS = 3

    def __init__(self):
        super().__init__()
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(18)
        header = QHBoxLayout()
        header.setSpacing(10)
        title = QLabel("Your memories")
        title.setStyleSheet("font-size:24px; font-weight:500;")
        header.addWidget(title)
        self._sub = QLabel("")
        self._sub.setStyleSheet(f"background:transparent; color:{theme.active_palette().text_mute}; padding-top:6px;")
        header.addWidget(self._sub)
        header.addStretch(1)
        self._add_btn = _accent_button("＋  Add memories")
        self._add_btn.clicked.connect(self.add_memories.emit)
        header.addWidget(self._add_btn)
        root.addLayout(header)

        self._scroll, self._grid = _grid_scroll()
        root.addWidget(self._scroll, 1)
        self.refresh()

    def _restyle(self):
        """Repaint from the current palette after a theme change and rebuild the
        tiles (each tile bakes in palette colours at build time)."""
        p = theme.active_palette()
        self._sub.setStyleSheet(f"background:transparent; color:{p.text_mute}; padding-top:6px;")
        _style_accent(self._add_btn)
        self.refresh()

    def refresh(self):
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        cat = catalog_mod.load(catalog_mod.catalog_path(app_dir()))
        cat.refresh_statuses()
        n = len(cat.collections)
        self._sub.setText(f"{n} collection{'s' if n != 1 else ''}, all kept" if n else "")
        if not n:
            empty = QLabel("No memories kept yet. Add some to get started.")
            empty.setStyleSheet(f"color:{theme.active_palette().text_mute}; padding:24px;")
            self._grid.addWidget(empty, 0, 0)
            return
        covers = catalog_mod.covers_dir(app_dir())
        for idx, e in enumerate(cat.collections):
            cover = covers / e.cached.cover if e.cached.cover else None
            sub = f"{e.cached.date} · {e.cached.memory_count} memories"
            if e.status != catalog_mod.STATUS_AVAILABLE:
                sub += " · offline"
            tile = _Tile(e.cached.name or Path(e.path).name, sub, cover,
                         badge="kept", show_menu=True)
            tile.clicked.connect(lambda p=e.path: self.open_collection.emit(p))
            tile.menu_requested.connect(
                lambda entry=e, i=idx, total=n: self._tile_menu(entry, i, total))
            self._grid.addWidget(tile, idx // self._COLS, idx % self._COLS)

    # ── per-collection controls (rename / reorder / remove) ────────────────────
    def _tile_menu(self, entry, idx: int, total: int):
        menu = QMenu(self)
        act_rename = menu.addAction("Rename…")
        act_left = menu.addAction("Move left")
        act_right = menu.addAction("Move right")
        act_left.setEnabled(idx > 0)
        act_right.setEnabled(idx < total - 1)
        menu.addSeparator()
        act_remove = menu.addAction("Remove…")
        chosen = menu.exec(QCursor.pos())
        if chosen is None:
            return
        if chosen is act_rename:
            self._rename(entry)
        elif chosen is act_left:
            catalog_mod.reorder(app_dir(), entry.id, -1)
            self.refresh()
        elif chosen is act_right:
            catalog_mod.reorder(app_dir(), entry.id, +1)
            self.refresh()
        elif chosen is act_remove:
            self._remove(entry)

    def _label(self, entry) -> str:
        return entry.cached.name or Path(entry.path).name

    def _rename(self, entry):
        name, ok = QInputDialog.getText(
            self, "Rename collection", "Name:", text=self._label(entry))
        if ok and name.strip():
            catalog_mod.rename_collection(app_dir(), entry.id, name.strip())
            self.refresh()

    def _remove(self, entry):
        name = self._label(entry)
        box = QMessageBox(self)
        box.setWindowTitle("Remove collection")
        box.setIcon(QMessageBox.Question)
        box.setText(f"Remove “{name}” from your library?")
        box.setInformativeText(
            "“Remove from library” forgets it here but leaves every file on disk "
            "(re-add the folder any time to bring it back).\n\n"
            "“Delete files…” permanently erases the whole collection folder — the "
            "master, the originals, everything — and can’t be undone.")
        b_forget = box.addButton("Remove from library", QMessageBox.AcceptRole)
        b_delete = box.addButton("Delete files…", QMessageBox.DestructiveRole)
        box.addButton("Cancel", QMessageBox.RejectRole)
        box.setDefaultButton(b_forget)
        box.exec()
        clicked = box.clickedButton()
        if clicked is b_forget:
            catalog_mod.remove_from_library(app_dir(), entry.id)
            self.refresh()
        elif clicked is b_delete:
            self._confirm_delete(entry)

    def _confirm_delete(self, entry):
        warn = QMessageBox(self)
        warn.setWindowTitle("Delete files permanently")
        warn.setIcon(QMessageBox.Warning)
        warn.setText("Permanently delete this collection’s folder?")
        warn.setInformativeText(
            f"{entry.path}\n\nEverything in this folder will be erased, including "
            "your original clips. This cannot be undone.")
        b_del = warn.addButton("Delete permanently", QMessageBox.DestructiveRole)
        b_cancel = warn.addButton("Cancel", QMessageBox.RejectRole)
        warn.setDefaultButton(b_cancel)
        warn.exec()
        if warn.clickedButton() is b_del:
            catalog_mod.delete_collection_folder(app_dir(), entry.id)
            self.refresh()


class CollectionView(QWidget):
    """One collection's album — memories read from the folder's own manifest."""
    open_memory = Signal(str, int)          # folder, clip index
    make_portable_requested = Signal(str)   # folder
    back = Signal()

    _COLS = 3

    def __init__(self):
        super().__init__()
        self._folder = None
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(16)
        header = QHBoxLayout()
        header.setSpacing(10)
        back = QPushButton("‹  Back")
        back.clicked.connect(self.back.emit)
        header.addWidget(back)
        self._title = QLabel("")
        self._title.setStyleSheet("font-size:20px; font-weight:500;")
        header.addWidget(self._title)
        header.addStretch(1)
        self._verified = QLabel("")
        self._verified.setStyleSheet(f"color:{theme.active_palette().ok}; padding-top:4px;")
        header.addWidget(self._verified)
        self._storage_mode = collection_mod.STORAGE_COMPACT
        self._portable_btn = QPushButton("Storage options…")
        self._portable_btn.setToolTip("Choose how this collection is stored on disk — keep it "
                                      "as one master, or also save every memory as a separate file.")
        self._portable_btn.clicked.connect(self._open_storage_options)
        header.addWidget(self._portable_btn)
        root.addLayout(header)

        self._scroll, self._grid = _grid_scroll()
        root.addWidget(self._scroll, 1)

    def _restyle(self):
        """Repaint palette-coloured bits and rebuild the album tiles on a theme change."""
        p = theme.active_palette()
        self._verified.setStyleSheet(f"background:transparent; color:{p.ok}; padding-top:4px;")
        if self._folder:
            self.load(self._folder)

    def _open_storage_options(self):
        """Show the storage decision aid; if the user opts into the additional
        folder-of-files layer, run it through the existing portable pipeline."""
        from storage_compare import StorageCompareDialog
        dlg = StorageCompareDialog(self._storage_mode, self)
        if dlg.exec() == QDialog.Accepted and self._folder:
            self.make_portable_requested.emit(self._folder)

    def load(self, folder: str):
        self._folder = folder
        while self._grid.count():
            item = self._grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        col = collection_mod.read_collection(folder)
        self._title.setText(col.name if col else Path(folder).name)
        if col:
            self._verified.setText(f"✓ {col.verified.passed}/{col.verified.total} kept and verified")
        # The button opens the storage decision aid, which reflects the current
        # mode (and offers the opt-in folder layer only when not already portable).
        self._storage_mode = col.storage_mode if col else collection_mod.STORAGE_COMPACT
        self._portable_btn.setVisible(bool(col))
        man = _read_folder_manifest(folder)
        thumbs = Path(folder) / "thumbs"
        clips = getattr(man, "clips", []) if man else []
        for i, clip in enumerate(clips):
            name = Path(clip.source_filename).stem if clip.source_filename else f"Memory {i+1}"
            badge = _FIDELITY_WORDS.get(clip.recovery_fidelity, "")
            cover = thumbs / f"{i+1:03d}.jpg"
            tile = _Tile(name, "", cover if cover.exists() else None, badge=badge)
            tile.clicked.connect(lambda f=folder, idx=i: self.open_memory.emit(f, idx))
            self._grid.addWidget(tile, i // self._COLS, i % self._COLS)


class MemoryView(QWidget):
    """A single memory — play / save the original / share (actions are Phase 3)."""
    back = Signal()
    play_requested = Signal(str, int)
    save_original_requested = Signal(str, int)
    share_requested = Signal(str, int)

    def __init__(self):
        super().__init__()
        self._folder = None
        self._index = -1
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(16)
        header = QHBoxLayout()
        header.setSpacing(10)
        back = QPushButton("‹  Back")
        back.clicked.connect(self.back.emit)
        header.addWidget(back)
        header.addStretch(1)
        root.addLayout(header)

        p = theme.active_palette()
        self._img = QLabel()
        self._img.setFixedHeight(320)
        self._img.setAlignment(Qt.AlignCenter)
        self._img.setStyleSheet(f"background:{p.input_dk}; border:1px solid {p.border}; "
                                f"border-radius:12px; color:{p.text_mute}; font-size:28px;")
        self._img.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        root.addWidget(self._img)

        self._title = QLabel("")
        self._title.setStyleSheet("font-size:18px; font-weight:500;")
        root.addWidget(self._title)
        self._meta = QLabel("")
        self._meta.setStyleSheet(f"background:transparent; color:{p.text_mute};")
        root.addWidget(self._meta)

        btns = QHBoxLayout()
        btns.setSpacing(10)
        play = self._play_btn = _accent_button("▶  Play")
        save = QPushButton("⤓  Save the original")
        share = QPushButton("↗  Share")
        play.clicked.connect(lambda: self.play_requested.emit(self._folder, self._index))
        save.clicked.connect(lambda: self.save_original_requested.emit(self._folder, self._index))
        share.clicked.connect(lambda: self.share_requested.emit(self._folder, self._index))
        for b in (play, save, share):
            btns.addWidget(b)
        btns.addStretch(1)
        root.addSpacing(4)
        root.addLayout(btns)
        root.addStretch(1)

    def _restyle(self):
        """Repaint palette-coloured bits after a theme change."""
        p = theme.active_palette()
        self._img.setStyleSheet(f"background:{p.input_dk}; border:1px solid {p.border}; "
                                f"border-radius:12px; color:{p.text_mute}; font-size:28px;")
        self._meta.setStyleSheet(f"background:transparent; color:{p.text_mute};")
        _style_accent(self._play_btn)
        if self._folder is not None and self._index >= 0:
            self.load(self._folder, self._index)

    def load(self, folder: str, index: int):
        self._folder, self._index = folder, index
        man = _read_folder_manifest(folder)
        clips = getattr(man, "clips", []) if man else []
        if not (0 <= index < len(clips)):
            return
        clip = clips[index]
        name = Path(clip.source_filename).stem if clip.source_filename else f"Memory {index+1}"
        self._title.setText(name)
        fid = _FIDELITY_WORDS.get(clip.recovery_fidelity, "")
        recorded = (clip.creation_time or "")[:10]
        self._meta.setText(" · ".join(x for x in (recorded, f"recovers {fid}" if fid else "") if x))
        cover = Path(folder) / "thumbs" / f"{index+1:03d}.jpg"
        pm = _thumb_pixmap(cover, 520, 300)
        if pm is not None:
            self._img.setPixmap(pm)
        else:
            self._img.setText("▶")


def _read_folder_manifest(folder):
    """Read the sidecar manifest.json in a collection folder (the master's)."""
    try:
        for p in Path(folder).glob("*.manifest.json"):
            return manifest_mod.from_json(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


class LibraryView(QStackedWidget):
    """The hub: hosts Home / Collection / Memory and wires navigation between them.
    `add_memories` bubbles up so the host can route to the Add flow (Phase 4)."""
    add_memories = Signal()
    make_portable_requested = Signal(str)   # bubbled from the collection view

    def __init__(self, settings=None):
        super().__init__()
        self._settings = settings
        self.home = HomeView()
        self.collection = CollectionView()
        self.memory = MemoryView()
        for w in (self.home, self.collection, self.memory):
            self.addWidget(w)

        self.home.open_collection.connect(self._open_collection)
        self.home.add_memories.connect(self.add_memories.emit)
        self.collection.back.connect(lambda: self.setCurrentWidget(self.home))
        self.collection.open_memory.connect(self._open_memory)
        self.collection.make_portable_requested.connect(self.make_portable_requested.emit)
        self.memory.back.connect(lambda: self.setCurrentWidget(self.collection))
        self.setCurrentWidget(self.home)

        # Repaint on theme change — the tiles and several labels bake palette
        # colours in at build time, so without this the shelf keeps the palette it
        # was last built with (e.g. white light-mode cards left over on a dark bg).
        ctrl = theme.controller()
        if ctrl is not None:
            ctrl.changed.connect(self._restyle)

    def _restyle(self):
        self.home._restyle()
        self.collection._restyle()
        self.memory._restyle()

    def _open_collection(self, folder: str):
        self.collection.load(folder)
        self.setCurrentWidget(self.collection)

    def _open_memory(self, folder: str, index: int):
        self.memory.load(folder, index)
        self.setCurrentWidget(self.memory)

    def refresh(self):
        self.home.refresh()
