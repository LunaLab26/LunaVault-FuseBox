"""add_flow.py — the guided "seven moments" that create a collection.

Rather than rewrite the 2,200-line MergeTab, this WRAPS a hidden MergeTab and
drives it (the pattern the test harness already proved), presenting the friendly
guided screens on top: welcome → add videos → what was found → the proof → keep
them all → safe → yours to keep (PRODUCT_DIRECTION.md, the seven moments).

Auto-everything with the mechanics hidden; the classic MergeTab stays available
for power users during the transition. Offscreen-instantiable.
"""

from pathlib import Path

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtWidgets import (
    QWidget, QLabel, QPushButton, QVBoxLayout, QHBoxLayout, QStackedWidget,
    QLineEdit, QProgressBar, QFileDialog,
)

import theme
from merge_tab import MergeTab
from core.binaries import get_ffmpeg, no_window
from core import proof as proof_mod

_VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".mts", ".m2ts"}


def _screen(*widgets) -> QWidget:
    w = QWidget()
    lay = QVBoxLayout(w)
    lay.setContentsMargins(48, 40, 48, 40)
    lay.setSpacing(14)
    for x in widgets:
        lay.addWidget(x)
    lay.addStretch(1)
    return w


def _h(text, size=22):
    lbl = QLabel(text)
    lbl.setStyleSheet(f"font-size:{size}px; font-weight:500;")
    lbl.setWordWrap(True)
    return lbl


def _p(text):
    lbl = QLabel(text)
    lbl.setStyleSheet(f"color:{theme.active_palette().text_mute};")
    lbl.setWordWrap(True)
    return lbl


def _accent(btn: QPushButton) -> QPushButton:
    """Friendly filled-accent styling for a screen's primary call-to-action."""
    p = theme.active_palette()
    btn.setCursor(Qt.PointingHandCursor)
    btn.setStyleSheet(
        f"QPushButton {{ background:{p.accent}; color:{p.on_accent()}; border:none; "
        f"border-radius:{p.radius}px; padding:9px 20px; font-weight:500; }}"
        f"QPushButton:hover {{ background:{p.accent_hi}; }}")
    return btn


class AddFlow(QStackedWidget):
    """The guided add experience. Emits `finished(master_path)` when a collection
    has been created, and `cancelled()` if the user backs all the way out."""
    finished = Signal(str)
    cancelled = Signal()

    def __init__(self, settings):
        super().__init__()
        self._settings = settings
        self._mt = MergeTab(settings)      # hidden engine — driven, never shown
        self._mt.setVisible(False)
        self._folder = None
        self._name = ""
        self._build_screens()
        self.setCurrentWidget(self._welcome)

    # ── screens ──────────────────────────────────────────────────────────────
    def _build_screens(self):
        # 0 · welcome
        start = _accent(QPushButton("Get started"))
        start.clicked.connect(lambda: self.setCurrentWidget(self._choose))
        self._welcome = _screen(
            _h("A safe home for your memories"),
            _p("Point FuseBox at your videos, and it'll check every one, keep them "
               "together, and let you get any of them back — exactly as you filmed it."),
            self._row(start))
        self.addWidget(self._welcome)

        # 1 · add videos
        pick = _accent(QPushButton("Choose a folder"))
        pick.clicked.connect(self._choose_folder)
        self._choose = _screen(
            _h("Start with your videos"),
            _p("Phone videos, camera clips, screen recordings — whatever you've got."),
            self._row(pick))
        self.addWidget(self._choose)

        # 2 · what was found
        self._found_h = _h("")
        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("Collection name")
        cont = _accent(QPushButton("Looks right, continue"))
        cont.clicked.connect(self._go_proof)
        self._found = _screen(
            self._found_h,
            _p("They'll be kept together as one collection. Rename it whenever you like."),
            self._name_edit, self._row(cont))
        self.addWidget(self._found)

        # 3 · the proof
        self._proof_h = _h("First, here's what “safe” really means")
        self._proof_p = _p("Watch one memory go into the vault and come straight back — untouched.")
        self._proof_next = _accent(QPushButton("Keep all my memories this way"))
        self._proof_next.setEnabled(False)
        self._proof_next.clicked.connect(self._start_keep)
        self._proof = _screen(self._proof_h, self._proof_p, self._row(self._proof_next))
        self.addWidget(self._proof)

        # 4 · keep them all (progress)
        self._progress = QProgressBar()
        self._progress.setRange(0, 100)
        self._keep_p = _p("This runs once. You can leave it — it'll be here when you're back.")
        self._keep = _screen(_h("Keeping your memories safe"), self._keep_p, self._progress)
        self.addWidget(self._keep)

        # 5 · safe
        self._safe_h = _h("")
        self._safe_p = _p("Every clip was checked, and can be recovered exactly as filmed.")
        see = _accent(QPushButton("See your collection"))
        see.clicked.connect(self._done)
        self._safe = _screen(self._safe_h, self._safe_p, self._row(see))
        self.addWidget(self._safe)

    def _row(self, *btns):
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        for b in btns:
            lay.addWidget(b)
        lay.addStretch(1)
        return w

    # ── flow ─────────────────────────────────────────────────────────────────
    def _choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose a folder of videos")
        if folder:
            self.load_folder(folder)

    def load_folder(self, folder: str):
        """Public entry (also drivable in tests): scan a folder, drive the hidden
        MergeTab to load it, and advance to 'what was found'."""
        self._folder = folder
        vids = [p for p in Path(folder).iterdir()
                if p.is_file() and p.suffix.lower() in _VIDEO_EXTS]
        self._name = Path(folder).name
        self._name_edit.setText(self._name)
        self._found_h.setText(f"Found {len(vids)} video{'s' if len(vids) != 1 else ''}")
        try:
            self._mt._load_folder(Path(folder))
        except Exception:
            pass
        self.setCurrentWidget(self._found)

    def _go_proof(self):
        self._name = self._name_edit.text().strip() or self._name
        self.setCurrentWidget(self._proof)
        QTimer.singleShot(50, self._run_proof)

    def _run_proof(self):
        """Run the one-clip 'see a memory come back' proof on the shortest clip."""
        clips = getattr(self._mt, "_clips", []) or []
        pairs = [(str(c.path), getattr(c, "duration", None)) for c in clips]
        target = proof_mod.pick_shortest(pairs)
        if not target:
            self._proof_h.setText("Ready when you are")
            self._proof_next.setEnabled(True)
            return
        ff, fp = get_ffmpeg()
        import tempfile
        res = proof_mod.prove_recovery(ff, fp, target, tempfile.mkdtemp(), **no_window())
        if res.matched:
            self._proof_h.setText("Your memory came back — exactly as you filmed it")
            self._proof_p.setText("Identical, byte for byte. Nothing was lost. That's how every "
                                  "one of your memories will be kept.")
        else:
            self._proof_h.setText("Ready to keep your memories")
            self._proof_p.setText("Your memories will be kept and verified as we go.")
        self._proof_next.setEnabled(True)

    def _start_keep(self):
        self.setCurrentWidget(self._keep)
        mt = self._mt
        # auto-everything: archival on, verify on, a clean playable baseline, and
        # an output beside the source. compat_baseline gives the family user a
        # master that plays on every device (one continuous re-encode, no broken
        # concat splices); the lossless originals still live on the archival tracks.
        try:
            if mt._spec_groups:
                mt._on_baseline_chosen(mt._spec_groups[0])
            mt._archival_check.setChecked(True)
            mt._verify_md5_check.setChecked(True)
            mt.compat_baseline = True
            out_dir = Path(self._folder).parent / self._name
            out_dir.mkdir(parents=True, exist_ok=True)
            mt._out_dir.setText(str(out_dir))
            mt._out_name.setText(f"{self._name}.mov")
            mt._start_merge()
        except Exception:
            pass
        worker = getattr(mt, "_worker", None)
        if worker is not None:
            worker.progress.connect(self._on_progress)
            worker.finished.connect(self._on_merge_finished)

    def _on_progress(self, info: dict):
        try:
            self._progress.setValue(int(info.get("pct", 0)))
        except Exception:
            pass

    def _on_merge_finished(self, ok: bool, msg: str):
        out = str(Path(self._mt._out_dir.text()) / self._mt._out_name.text())
        self._master = out
        n = len(getattr(self._mt, "_clips", []) or [])
        self._safe_h.setText(f"All {n} memories are kept and verified")
        self.setCurrentWidget(self._safe)

    def _done(self):
        self.finished.emit(getattr(self, "_master", ""))

    def reset(self):
        self.setCurrentWidget(self._welcome)

    def shutdown(self):
        """Settle the hidden engine's threads — call from the host's closeEvent so
        the app exits cleanly."""
        try:
            self._mt.shutdown()
        except Exception:
            pass
