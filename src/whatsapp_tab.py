"""whatsapp_tab.py — "Extract and Share" tab: the original WhatsApp-clip Share
half (trim + colour-grade + export a short clip), plus a new Extract half
(recover original camera clips losslessly from an archival master, driven by
its manifest — see core/extract.py and extract_workers.py). A segmented
toggle at the top switches between the two — they operate on different kinds
of source file and don't share state."""

import os
import tempfile
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPixmap, QColor, QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QScrollArea,
    QLabel, QPushButton, QLineEdit, QFileDialog,
    QProgressBar, QFrame, QSizePolicy,
    QMessageBox, QCheckBox, QTreeWidget, QTreeWidgetItem, QButtonGroup, QAbstractItemView,
)

from ffmpeg_runner import WhatsAppWorker, FramePreviewWorker, get_ffmpeg, get_app_dir
from thread_utils import settle
from grade_manager import Grade, scan_luts, migrate_grade_key
from probe import probe as probe_file
from settings import Settings
from widgets.timeline import TrimTimeline as TimelineWidget, secs_to_tc as _secs_to_tc
from core.manifest import Manifest, ClipEntry
from extract_workers import ManifestLoadWorker, ExtractWorker
import log_manager
import theme


# ── Extract clip-tree columns ─────────────────────────────────────────────────
EX_COL_NAME = 0
EX_COL_CAMERA = 1
EX_COL_SPEC = 2
EX_COL_RECOVERY = 3
EX_N_COLS = 4


# ── Timecode helpers ──────────────────────────────────────────────────────────

_FPS_DEFAULT = 30000 / 1001   # ≈ 29.97


def _tc_to_secs(tc: str, fps: float = _FPS_DEFAULT) -> float:
    """Parse HH:MM:SS:FF, HH:MM:SS, or HH:MM:SS.mmm."""
    parts = tc.strip().split(":")
    try:
        if len(parts) == 4:
            h, m, s, f = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
            return h * 3600 + m * 60 + s + f / max(1.0, fps)
        if len(parts) == 3:
            h, m = int(parts[0]), int(parts[1])
            return h * 3600 + m * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except (ValueError, IndexError):
        return 0.0


def _secs_to_ffmpeg(secs: float) -> str:
    """Format seconds as HH:MM:SS.mmm for ffmpeg -ss / -t."""
    secs = max(0.0, secs)
    h, r = divmod(int(secs), 3600)
    m, sec = divmod(r, 60)
    ms = round((secs - int(secs)) * 1000)
    return f"{h:02d}:{m:02d}:{sec:02d}.{ms:03d}"


def _estimate_size_mb(duration_secs: float, has_grade: bool) -> float:
    """Rough estimate: H.264 CRF 26 1280×720 ≈ 1.2–1.8 MB/s."""
    rate = 1.5 if not has_grade else 1.4
    return duration_secs * rate


# ── Grade button ──────────────────────────────────────────────────────────────

class GradeButton(QPushButton):
    def __init__(self, grade: Optional[Grade], label: str):
        super().__init__(label)
        self.grade = grade
        self.setCheckable(True)
        self._update_style(False)

    def _update_style(self, selected: bool):
        p = theme.active_palette()
        if selected:
            self.setStyleSheet(
                f"QPushButton {{ border:2px solid {p.accent}; border-radius:6px; "
                f"background:{p.accent}; color:{p.on_accent()}; padding:4px 8px; font-size:11px; }}"
            )
        else:
            self.setStyleSheet(
                f"QPushButton {{ border:2px solid {p.border}; border-radius:6px; "
                f"background:transparent; color:{p.text}; padding:4px 8px; font-size:11px; }}"
                f"QPushButton:hover {{ border-color:{p.accent}; }}"
            )

    def setSelected(self, v: bool):
        self.setChecked(v)
        self._update_style(v)


# ── Main tab ──────────────────────────────────────────────────────────────────

class WhatsAppTab(QWidget):
    def __init__(self, settings: Settings):
        super().__init__()
        self._settings = settings
        self._grades: list[Grade] = []
        self._selected_grade: Optional[Grade] = None
        self._grade_buttons: list[GradeButton] = []
        self._worker: Optional[WhatsAppWorker] = None
        self._preview_worker: Optional[FramePreviewWorker] = None
        self._before_worker: Optional[FramePreviewWorker] = None   # kept alive — prevents GC crash
        self._retired_workers: list[FramePreviewWorker] = []       # superseded but still running
        self._before_path: str = ""
        self._after_path:  str = ""
        self._showing_after = True
        self._updating_fields = False

        # Source metadata (probed on file selection)
        self._source_fps: float = _FPS_DEFAULT
        self._source_dur: float = 0.0

        # Debounce timer for preview requests while scrubbing
        self._preview_timer = QTimer(self)
        self._preview_timer.setSingleShot(True)
        self._preview_timer.setInterval(350)
        self._preview_timer.timeout.connect(self._request_preview)

        # ── Extract state ────────────────────────────────────────────────────
        self._extract_master_path: str = ""
        self._extract_manifest: Optional[Manifest] = None
        self._manifest_load_worker: Optional[ManifestLoadWorker] = None
        self._extract_worker: Optional[ExtractWorker] = None
        self._extract_items: dict = {}   # ClipEntry -> QTreeWidgetItem (for progress updates)

        self.setAcceptDrops(True)
        self._setup_ui()
        self._load_grades()
        self._restyle()
        ctrl = theme.controller()
        if ctrl is not None:
            ctrl.changed.connect(self._restyle)

    # ── Drag-and-drop (Extract panel only — Share uses its own Browse flow) ───

    def dragEnterEvent(self, event):
        if self._extract_panel.isVisible() and event.mimeData().hasUrls():
            for u in event.mimeData().urls():
                if u.toLocalFile().lower().endswith((".mov", ".mp4")):
                    event.acceptProposedAction()
                    return

    def dropEvent(self, event):
        if not self._extract_panel.isVisible():
            return
        for u in event.mimeData().urls():
            path = u.toLocalFile()
            if path.lower().endswith((".mov", ".mp4")):
                self._load_extract_master(path)
                event.acceptProposedAction()
                return

    # ── UI construction ───────────────────────────────────────────────────────

    def _setup_ui(self):
        outer = QVBoxLayout(self)
        outer.setSpacing(10)
        outer.setContentsMargins(14, 14, 14, 14)

        mode_row = QHBoxLayout()
        self._mode_share_btn = QPushButton("Share a clip")
        self._mode_extract_btn = QPushButton("Extract originals")
        for b in (self._mode_share_btn, self._mode_extract_btn):
            b.setCheckable(True)
        self._mode_group = QButtonGroup(self)
        self._mode_group.setExclusive(True)
        self._mode_group.addButton(self._mode_share_btn)
        self._mode_group.addButton(self._mode_extract_btn)
        self._mode_share_btn.setChecked(True)
        self._mode_share_btn.toggled.connect(self._on_mode_toggled)
        mode_row.addWidget(self._mode_share_btn)
        mode_row.addWidget(self._mode_extract_btn)
        mode_row.addStretch()
        outer.addLayout(mode_row)

        self._share_panel = QWidget()
        root = QVBoxLayout(self._share_panel)
        root.setSpacing(10)
        root.setContentsMargins(0, 0, 0, 0)

        # ── Source file ───────────────────────────────────────────────────────
        src_row = QHBoxLayout()
        self._src_edit = QLineEdit()
        self._src_edit.setPlaceholderText("Select source video file…")
        self._src_edit.setReadOnly(True)
        src_btn = QPushButton("Browse…")
        src_btn.setFixedWidth(90)
        src_btn.clicked.connect(self._browse_source)
        src_row.addWidget(QLabel("Source:"))
        src_row.addWidget(self._src_edit, 1)
        src_row.addWidget(src_btn)
        root.addLayout(src_row)

        # ── Clip range (HH:MM:SS:FF) ──────────────────────────────────────────
        range_row = QHBoxLayout()
        range_row.addWidget(QLabel("Start:"))
        self._start_edit = QLineEdit("00:00:00:00")
        self._start_edit.setFixedWidth(110)
        self._start_edit.editingFinished.connect(self._on_start_changed)
        range_row.addWidget(self._start_edit)

        range_row.addSpacing(12)
        range_row.addWidget(QLabel("Duration:"))
        self._dur_edit = QLineEdit("00:01:00:00")
        self._dur_edit.setFixedWidth(110)
        self._dur_edit.editingFinished.connect(self._on_dur_changed)
        range_row.addWidget(self._dur_edit)

        range_row.addSpacing(12)
        range_row.addWidget(QLabel("End:"))
        self._end_edit = QLineEdit("00:01:00:00")
        self._end_edit.setFixedWidth(110)
        self._end_edit.editingFinished.connect(self._on_end_changed)
        range_row.addWidget(self._end_edit)

        range_row.addStretch()
        root.addLayout(range_row)

        # ── Main content: preview + grades ────────────────────────────────────
        content_split = QHBoxLayout()
        content_split.setSpacing(12)

        # Preview pane
        preview_pane = QVBoxLayout()
        preview_pane.setSpacing(6)

        # Before/after toggle row
        ph_row = QHBoxLayout()
        self._before_after_btn = QPushButton("Before / After")
        self._before_after_btn.setCheckable(True)
        self._before_after_btn.setFixedWidth(110)
        self._before_after_btn.clicked.connect(self._toggle_before_after)
        self._before_after_btn.setToolTip("Toggle between graded and original preview")
        ph_row.addWidget(self._before_after_btn)
        ph_row.addStretch()
        preview_pane.addLayout(ph_row)

        # Preview image
        self._preview_label = QLabel("Select a source file to preview")
        self._preview_label.setFixedSize(426, 240)
        self._preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_label.setWordWrap(True)
        preview_pane.addWidget(self._preview_label)

        # Before/after caption
        self._ba_caption = QLabel()
        self._ba_caption.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview_pane.addWidget(self._ba_caption)

        # Timeline scrubber
        self._timeline = TimelineWidget(self)
        self._timeline.setFixedWidth(426)
        self._timeline.position_changed.connect(self._on_scrubber_moved)
        self._timeline.in_changed.connect(self._on_in_marker_moved)
        self._timeline.out_changed.connect(self._on_out_marker_moved)
        preview_pane.addWidget(self._timeline)

        # Timeline labels row: In | position | Out
        tl_labels = QHBoxLayout()
        self._tl_in_lbl  = QLabel("In: —")
        self._tl_pos_lbl = QLabel("—")
        self._tl_out_lbl = QLabel("Out: —")
        self._tl_pos_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._tl_out_lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        tl_labels.addWidget(self._tl_in_lbl)
        tl_labels.addWidget(self._tl_pos_lbl, 1)
        tl_labels.addWidget(self._tl_out_lbl)
        preview_pane.addLayout(tl_labels)

        preview_pane.addStretch()
        content_split.addLayout(preview_pane)

        # Grade selection grid
        grade_pane = QVBoxLayout()
        grade_pane.addWidget(QLabel("Colour grade:"))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setFixedWidth(260)

        self._grade_container = QWidget()
        self._grade_grid = QVBoxLayout(self._grade_container)
        self._grade_grid.setSpacing(4)
        scroll.setWidget(self._grade_container)
        grade_pane.addWidget(scroll, 1)
        content_split.addLayout(grade_pane)

        root.addLayout(content_split, 1)

        # ── Output ────────────────────────────────────────────────────────────
        out_grid = QGridLayout()
        out_grid.setColumnStretch(1, 1)

        self._out_name = QLineEdit()
        self._out_name.setPlaceholderText("whatsapp_clip.mp4")
        self._out_name.textChanged.connect(self._update_size_estimate)
        out_grid.addWidget(QLabel("Output filename:"), 0, 0)
        out_grid.addWidget(self._out_name, 0, 1)

        self._out_dir = QLineEdit()
        self._out_dir.setPlaceholderText("Output folder…")
        self._out_dir.setReadOnly(True)
        out_dir_btn = QPushButton("Browse…")
        out_dir_btn.setFixedWidth(90)
        out_dir_btn.clicked.connect(self._browse_out_dir)
        out_grid.addWidget(QLabel("Output folder:"), 1, 0)
        out_grid.addWidget(self._out_dir, 1, 1)
        out_grid.addWidget(out_dir_btn, 1, 2)

        self._size_label = QLabel()
        out_grid.addWidget(self._size_label, 2, 1)

        root.addLayout(out_grid)

        # ── Progress section ──────────────────────────────────────────────────
        self._progress_frame = QFrame()
        self._progress_frame.setFrameShape(QFrame.Shape.StyledPanel)
        self._progress_frame.hide()
        prog_layout = QVBoxLayout(self._progress_frame)

        pbar_row = QHBoxLayout()
        self._pbar = QProgressBar()
        self._pbar.setRange(0, 100)
        self._pbar.setTextVisible(True)
        self._stats_label = QLabel("—")
        self._stats_label.setFixedWidth(180)
        pbar_row.addWidget(self._pbar, 1)
        pbar_row.addWidget(self._stats_label)
        prog_layout.addLayout(pbar_row)

        thumb_row = QHBoxLayout()
        self._thumb_label = QLabel()
        self._thumb_label.setFixedSize(240, 135)
        self._thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._thumb_label.setText("Rendering…")
        thumb_row.addWidget(self._thumb_label)
        thumb_row.addStretch()
        prog_layout.addLayout(thumb_row)

        root.addWidget(self._progress_frame)

        # ── Action buttons ────────────────────────────────────────────────────
        btn_row = QHBoxLayout()

        self._preview_check = QCheckBox("Live preview")
        self._preview_check.setChecked(True)
        self._preview_check.setToolTip(
            "Show a frame-by-frame preview while rendering.\n"
            "Disable to prevent terminal windows obscuring the UI."
        )
        self._preview_check.stateChanged.connect(
            lambda: self._thumb_label.setVisible(self._preview_check.isChecked())
        )
        btn_row.addWidget(self._preview_check)
        btn_row.addStretch()

        self._export_btn = QPushButton("▶  Export clip")
        self._export_btn.setFixedHeight(36)
        self._export_btn.setEnabled(False)
        self._export_btn.clicked.connect(self._start_export)
        self._style_export_btn()

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setFixedHeight(36)
        self._cancel_btn.hide()
        self._cancel_btn.clicked.connect(self._cancel_export)

        btn_row.addWidget(self._cancel_btn)
        btn_row.addWidget(self._export_btn)
        root.addLayout(btn_row)

        # Restore settings
        saved_src = self._settings.get("last_wa_source", "")
        if saved_src and Path(saved_src).exists():
            self._src_edit.setText(saved_src)
            self._export_btn.setEnabled(True)
            QTimer.singleShot(0, lambda: self._probe_source(saved_src))
        self._out_dir.setText(self._settings.get("last_wa_output_dir", ""))
        self._start_edit.setText(self._settings.get("last_wa_start", "00:00:00:00"))
        self._dur_edit.setText(self._settings.get("last_wa_duration", "00:01:00:00"))
        self._sync_end_from_start_dur()
        self._update_size_estimate()

        outer.addWidget(self._share_panel, 1)

        self._extract_panel = self._build_extract_panel()
        outer.addWidget(self._extract_panel, 1)
        self._extract_panel.hide()

    def _on_mode_toggled(self, share_checked: bool):
        self._share_panel.setVisible(share_checked)
        self._extract_panel.setVisible(not share_checked)

    # ── Extract panel construction ───────────────────────────────────────────

    def _build_extract_panel(self) -> QWidget:
        panel = QWidget()
        lay = QVBoxLayout(panel)
        lay.setSpacing(10)
        lay.setContentsMargins(0, 0, 0, 0)

        src_row = QHBoxLayout()
        self._ex_master_edit = QLineEdit()
        self._ex_master_edit.setPlaceholderText("Select or drop an archival master (.mov/.mp4)…")
        self._ex_master_edit.setReadOnly(True)
        ex_browse_btn = QPushButton("Browse…")
        ex_browse_btn.setFixedWidth(90)
        ex_browse_btn.clicked.connect(self._browse_extract_master)
        src_row.addWidget(QLabel("Master:"))
        src_row.addWidget(self._ex_master_edit, 1)
        src_row.addWidget(ex_browse_btn)
        lay.addLayout(src_row)

        self._ex_status_label = QLabel(
            "Drop a master here, or Browse, to see what can be recovered from it.")
        self._ex_status_label.setWordWrap(True)
        lay.addWidget(self._ex_status_label)

        sel_row = QHBoxLayout()
        sel_row.addWidget(QLabel("Recoverable clips:"))
        sel_row.addStretch()
        select_all_btn = QPushButton("Select all")
        select_all_btn.clicked.connect(lambda: self._set_all_extract_checks(True))
        select_none_btn = QPushButton("Select none")
        select_none_btn.clicked.connect(lambda: self._set_all_extract_checks(False))
        sel_row.addWidget(select_all_btn)
        sel_row.addWidget(select_none_btn)
        lay.addLayout(sel_row)

        self._ex_tree = QTreeWidget()
        self._ex_tree.setColumnCount(EX_N_COLS)
        self._ex_tree.setHeaderLabels(["Clip", "Camera", "Spec", "Recovers as"])
        self._ex_tree.header().setSectionResizeMode(EX_COL_NAME, self._ex_tree.header().ResizeMode.Stretch)
        self._ex_tree.setAlternatingRowColors(True)
        self._ex_tree.setMinimumHeight(200)
        lay.addWidget(self._ex_tree, 1)

        out_row = QHBoxLayout()
        self._ex_out_dir = QLineEdit()
        self._ex_out_dir.setPlaceholderText("Output folder for recovered clips…")
        self._ex_out_dir.setReadOnly(True)
        ex_out_btn = QPushButton("Browse…")
        ex_out_btn.setFixedWidth(90)
        ex_out_btn.clicked.connect(self._browse_extract_out_dir)
        out_row.addWidget(QLabel("Output folder:"))
        out_row.addWidget(self._ex_out_dir, 1)
        out_row.addWidget(ex_out_btn)
        lay.addLayout(out_row)

        self._ex_progress_frame = QFrame()
        self._ex_progress_frame.setFrameShape(QFrame.Shape.StyledPanel)
        self._ex_progress_frame.hide()
        ex_prog_lay = QVBoxLayout(self._ex_progress_frame)
        self._ex_pbar = QProgressBar()
        self._ex_pbar.setRange(0, 100)
        self._ex_pbar.setTextVisible(True)
        ex_prog_lay.addWidget(self._ex_pbar)
        self._ex_progress_label = QLabel("")
        ex_prog_lay.addWidget(self._ex_progress_label)
        lay.addWidget(self._ex_progress_frame)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._ex_cancel_btn = QPushButton("Cancel")
        self._ex_cancel_btn.setFixedHeight(36)
        self._ex_cancel_btn.hide()
        self._ex_cancel_btn.clicked.connect(self._cancel_extract)
        self._ex_extract_btn = QPushButton("▶  Extract selected")
        self._ex_extract_btn.setFixedHeight(36)
        self._ex_extract_btn.setEnabled(False)
        self._ex_extract_btn.clicked.connect(self._start_extract)
        btn_row.addWidget(self._ex_cancel_btn)
        btn_row.addWidget(self._ex_extract_btn)
        lay.addLayout(btn_row)

        return panel

    # ── Extract logic ─────────────────────────────────────────────────────────

    def _browse_extract_master(self):
        start = self._settings.get("last_extract_source", "") or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self, "Select archival master", str(Path(start).parent) if start else "",
            "Video files (*.mov *.mp4);;All files (*)")
        if path:
            self._load_extract_master(path)

    def _load_extract_master(self, path: str):
        self._settings.set("last_extract_source", path)
        self._ex_master_edit.setText(path)
        self._extract_master_path = path
        self._extract_manifest = None
        self._ex_tree.clear()
        self._extract_items = {}
        self._ex_extract_btn.setEnabled(False)
        self._ex_status_label.setText("Reading manifest…")

        if self._manifest_load_worker is not None:
            settle(self._manifest_load_worker, 5000)
        _, fp = get_ffmpeg()
        w = ManifestLoadWorker(fp, path)
        w.manifest_ready.connect(self._on_extract_manifest_ready)
        self._manifest_load_worker = w
        w.start()

    def _on_extract_manifest_ready(self, manifest):
        self._extract_manifest = manifest
        if manifest is None or not manifest.clips:
            self._ex_status_label.setText(
                "No manifest found in this file — it wasn't produced with \"Archival "
                "master\" enabled, or its sidecar .manifest.json is missing. Nothing to "
                "recover.")
            return
        self._ex_status_label.setText(
            f"{len(manifest.clips)} original clip(s) recorded in this master's manifest.")
        self._populate_extract_tree(manifest)
        self._ex_extract_btn.setEnabled(True)

    def _populate_extract_tree(self, manifest: Manifest):
        p = theme.active_palette()
        self._ex_tree.clear()
        self._extract_items = {}
        groups: dict = {}
        for idx, entry in enumerate(manifest.clips):
            groups.setdefault(entry.camera_label or "Unknown camera", []).append((idx, entry))

        for camera, members in groups.items():
            group_item = QTreeWidgetItem(self._ex_tree)
            group_item.setText(EX_COL_NAME, f"{camera}  ({len(members)} clip{'s' if len(members) != 1 else ''})")
            font = QFont("", -1, QFont.Weight.Bold)
            for col in range(EX_N_COLS):
                group_item.setFont(col, font)
                group_item.setForeground(col, QColor(p.accent))
            for idx, entry in members:
                item = QTreeWidgetItem(group_item)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(EX_COL_NAME, Qt.CheckState.Checked)
                item.setText(EX_COL_NAME, entry.source_filename)
                item.setText(EX_COL_CAMERA, camera)
                spec = f"{(entry.codec or '?').upper()} {entry.width}x{entry.height} {entry.bit_depth}-bit"
                if entry.rotation:
                    spec += f", {entry.rotation}°"
                item.setText(EX_COL_SPEC, spec)
                recovers = entry.source_filename
                if entry.has_wav:
                    recovers += f" + {Path(entry.source_filename).stem}.wav"
                if entry.conform_status != "ok" and entry.archival_track is not None:
                    recovers += "" if entry.in_track_start == 0.0 else "  (near-exact)"
                item.setText(EX_COL_RECOVERY, recovers)
                self._extract_items[idx] = item
            group_item.setExpanded(True)

    def _set_all_extract_checks(self, checked: bool):
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for item in self._extract_items.values():
            item.setCheckState(EX_COL_NAME, state)

    def _browse_extract_out_dir(self):
        start = self._ex_out_dir.text() or str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "Select output folder", start)
        if folder:
            self._ex_out_dir.setText(folder)
            self._settings.set("last_extract_output_dir", folder)

    def _start_extract(self):
        if self._extract_manifest is None or not self._extract_master_path:
            return
        out_dir = self._ex_out_dir.text().strip()
        if not out_dir:
            QMessageBox.information(self, "Choose an output folder",
                                    "Pick a folder to save the recovered clips into.")
            return
        selected = [self._extract_manifest.clips[idx] for idx, item in self._extract_items.items()
                   if item.checkState(EX_COL_NAME) == Qt.CheckState.Checked]
        if not selected:
            QMessageBox.information(self, "Nothing selected",
                                    "Tick at least one clip to extract.")
            return

        self._ex_progress_frame.show()
        self._ex_pbar.setValue(0)
        self._ex_extract_btn.hide()
        self._ex_cancel_btn.show()

        ff, _ = get_ffmpeg()
        w = ExtractWorker(ff, self._extract_master_path, self._extract_manifest,
                          selected, Path(out_dir))
        w.progress.connect(self._on_extract_progress)
        w.clip_done.connect(self._on_extract_clip_done)
        w.clip_error.connect(self._on_extract_clip_error)
        w.finished_all.connect(self._on_extract_finished)
        self._extract_worker = w
        w.start()

    def _cancel_extract(self):
        if self._extract_worker is not None:
            self._extract_worker.cancel()

    def _on_extract_progress(self, done: int, total: int, name: str):
        pct = int(done / total * 100) if total else 0
        self._ex_pbar.setValue(pct)
        self._ex_progress_label.setText(f"{done}/{total}" + (f" — {name}" if name else ""))

    def _on_extract_clip_done(self, name: str, paths: list):
        self._ex_status_label.setText(f"Recovered {name} → " + ", ".join(p.name for p in paths))

    def _on_extract_clip_error(self, name: str, msg: str):
        self._ex_status_label.setText(f"Failed to recover {name}: {msg}")

    def _on_extract_finished(self, ok: bool):
        self._ex_progress_frame.hide()
        self._ex_cancel_btn.hide()
        self._ex_extract_btn.show()
        worker, self._extract_worker = self._extract_worker, None
        settle(worker, 10000)
        if ok:
            self._ex_status_label.setText("Extraction complete.")
        else:
            self._ex_status_label.setText("Extraction cancelled.")

    # ── Theming ─────────────────────────────────────────────────────────────────

    def _style_export_btn(self):
        p = theme.active_palette()
        self._export_btn.setStyleSheet(
            f"QPushButton {{ background:{p.accent}; color:{p.on_accent()}; border-radius:6px; "
            "font-weight:bold; padding:0 18px; }"
            f"QPushButton:disabled {{ background:{p.disabled_bg}; color:{p.disabled_fg}; }}"
        )

    def _restyle(self):
        p = theme.active_palette()
        self._style_export_btn()
        self._tl_in_lbl.setStyleSheet(f"color:{p.ok}; font-size:10px;")
        self._tl_pos_lbl.setStyleSheet(f"color:{p.text_mute}; font-size:10px;")
        self._tl_out_lbl.setStyleSheet(f"color:{p.danger}; font-size:10px;")
        self._ba_caption.setStyleSheet(f"color:{p.text_mute}; font-size:11px;")
        self._size_label.setStyleSheet(f"color:{p.text_mute}; font-size:11px;")
        self._preview_label.setStyleSheet(
            f"background:{p.input_dk}; border-radius:6px; color:{p.text_mute};")
        self._thumb_label.setStyleSheet(
            f"background:{p.input_dk}; border-radius:4px; color:{p.text_mute};")
        # Re-apply grade button styles for the new palette
        for b in self._grade_buttons:
            b.setSelected(b.grade is self._selected_grade if b.grade else self._selected_grade is None)
        self._timeline.update()

        mode_style = (
            f"QPushButton {{ padding:6px 14px; border:1px solid {p.border}; color:{p.text_dim}; "
            f"background:{p.input_dk}; }}"
            f"QPushButton:checked {{ border-color:{p.accent}; color:{p.accent}; background:{p.surface2}; }}")
        self._mode_share_btn.setStyleSheet(mode_style)
        self._mode_extract_btn.setStyleSheet(mode_style)
        self._ex_status_label.setStyleSheet(f"color:{p.text_mute}; font-size:11px;")

    # ── Grade loading ─────────────────────────────────────────────────────────

    def _load_grades(self):
        self._grades = scan_luts()
        while self._grade_grid.count():
            item = self._grade_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._grade_buttons.clear()

        none_btn = GradeButton(None, "None (original)")
        none_btn.clicked.connect(lambda: self._select_grade(None, none_btn))
        self._grade_buttons.append(none_btn)
        self._grade_grid.addWidget(none_btn)

        p = theme.active_palette()
        current_group = ""
        for grade in self._grades:
            if grade.group != current_group:
                current_group = grade.group
                group_lbl = QLabel(current_group)
                group_lbl.setStyleSheet(
                    f"color:{p.text_mute}; font-size:10px; font-weight:bold; margin-top:6px;"
                )
                self._grade_grid.addWidget(group_lbl)
            btn = GradeButton(grade, grade.display_name)
            btn.clicked.connect(lambda checked, g=grade, b=btn: self._select_grade(g, b))
            self._grade_buttons.append(btn)
            self._grade_grid.addWidget(btn)

        self._grade_grid.addStretch()

        saved_key = migrate_grade_key(self._settings.get("last_wa_grade", ""))
        if saved_key:
            for btn in self._grade_buttons:
                if btn.grade and btn.grade.key == saved_key:
                    self._select_grade(btn.grade, btn)
                    break
            else:
                self._select_grade_btn_none()
        else:
            self._select_grade_btn_none()

    def _select_grade_btn_none(self):
        if self._grade_buttons:
            self._select_grade(None, self._grade_buttons[0])

    def _select_grade(self, grade: Optional[Grade], btn: GradeButton):
        self._selected_grade = grade
        for b in self._grade_buttons:
            b.setSelected(b is btn)
        self._settings.set("last_wa_grade", grade.key if grade else "")
        self._update_size_estimate()
        if self._src_edit.text():
            self._preview_timer.start()

    # ── Source file ───────────────────────────────────────────────────────────

    def _browse_source(self):
        start = self._settings.get("last_wa_source", "") or str(Path.home())
        if Path(start).is_file():
            start = str(Path(start).parent)
        path, _ = QFileDialog.getOpenFileName(
            self, "Select source video", start,
            "Video files (*.mov *.mp4 *.mkv *.m4v *.avi);;All files (*.*)"
        )
        if path:
            self._src_edit.setText(path)
            self._settings.set("last_wa_source", path)
            self._export_btn.setEnabled(True)
            self._probe_source(path)

    def shutdown(self):
        """Wait out all worker threads (called from MainWindow.closeEvent)."""
        if self._worker:
            self._worker.cancel()
        settle(self._worker, 10000)
        settle(self._preview_worker)
        settle(self._before_worker)
        for w in self._retired_workers:
            settle(w)
        self._worker = None
        self._preview_worker = None
        self._before_worker = None
        self._retired_workers.clear()

        if self._extract_worker:
            self._extract_worker.cancel()
        settle(self._extract_worker, 10000)
        settle(self._manifest_load_worker, 5000)
        self._extract_worker = None
        self._manifest_load_worker = None

    def set_source(self, path: str):
        """Called from main window when a merge job completes."""
        if path and Path(path).exists():
            self._src_edit.setText(path)
            self._settings.set("last_wa_source", path)
            self._export_btn.setEnabled(True)
            self._probe_source(path)

    def _probe_source(self, path: str):
        """Probe fps and duration from source; update timeline."""
        try:
            _, fp = get_ffmpeg()
            info = probe_file(fp, path)
            fps = info.fps_float if info.fps_float > 0 else _FPS_DEFAULT
            dur = info.duration
        except Exception:
            fps = _FPS_DEFAULT
            dur = 0.0

        self._source_fps = fps
        self._source_dur = dur

        # Update timeline range
        self._timeline.set_duration(dur)

        # Restore / clamp existing in/out to new duration
        start_s = _tc_to_secs(self._start_edit.text(), fps)
        dur_s   = _tc_to_secs(self._dur_edit.text(),   fps)
        start_s = min(start_s, max(0.0, dur - 0.1))
        end_s   = min(start_s + dur_s, dur)
        dur_s   = end_s - start_s

        self._updating_fields = True
        self._start_edit.setText(_secs_to_tc(start_s, fps))
        self._dur_edit.setText(_secs_to_tc(dur_s, fps))
        self._end_edit.setText(_secs_to_tc(end_s, fps))
        self._updating_fields = False

        self._timeline.set_in(start_s)
        self._timeline.set_out(end_s)
        self._timeline.set_position(start_s)
        self._update_tl_labels(start_s)
        self._update_size_estimate()

        # Request preview at start
        self._preview_timer.start()

    # ── Clip range editing ────────────────────────────────────────────────────

    def _on_start_changed(self):
        if self._updating_fields:
            return
        start_s = _tc_to_secs(self._start_edit.text(), self._source_fps)
        dur_s   = _tc_to_secs(self._dur_edit.text(),   self._source_fps)
        end_s   = start_s + dur_s
        self._updating_fields = True
        self._end_edit.setText(_secs_to_tc(end_s, self._source_fps))
        self._updating_fields = False
        self._timeline.set_in(start_s)
        self._timeline.set_out(end_s)
        self._update_size_estimate()
        self._settings.set("last_wa_start", self._start_edit.text())

    def _on_dur_changed(self):
        if self._updating_fields:
            return
        start_s = _tc_to_secs(self._start_edit.text(), self._source_fps)
        dur_s   = _tc_to_secs(self._dur_edit.text(),   self._source_fps)
        end_s   = start_s + dur_s
        self._updating_fields = True
        self._end_edit.setText(_secs_to_tc(end_s, self._source_fps))
        self._updating_fields = False
        self._timeline.set_out(end_s)
        self._update_size_estimate()
        self._settings.set("last_wa_duration", self._dur_edit.text())

    def _on_end_changed(self):
        if self._updating_fields:
            return
        start_s = _tc_to_secs(self._start_edit.text(), self._source_fps)
        end_s   = _tc_to_secs(self._end_edit.text(),   self._source_fps)
        dur_s   = max(0.0, end_s - start_s)
        self._updating_fields = True
        self._dur_edit.setText(_secs_to_tc(dur_s, self._source_fps))
        self._updating_fields = False
        self._timeline.set_out(end_s)
        self._update_size_estimate()

    def _sync_end_from_start_dur(self):
        fps     = self._source_fps
        start_s = _tc_to_secs(self._start_edit.text(), fps)
        dur_s   = _tc_to_secs(self._dur_edit.text(),   fps)
        self._updating_fields = True
        self._end_edit.setText(_secs_to_tc(start_s + dur_s, fps))
        self._updating_fields = False

    def _update_size_estimate(self):
        dur_s = _tc_to_secs(self._dur_edit.text(), self._source_fps)
        est   = _estimate_size_mb(dur_s, self._selected_grade is not None)
        self._size_label.setText(
            f"Estimated file size: ~{est:.0f} MB  ·  H.264 1280×720 CRF 26 AAC 128k"
        )

    # ── Timeline signal handlers ──────────────────────────────────────────────

    def _on_scrubber_moved(self, secs: float):
        """Scrubber dragged → update position label and debounce preview."""
        self._update_tl_labels(secs)
        self._preview_timer.start()

    def _on_in_marker_moved(self, secs: float):
        """In marker dragged → update Start and Duration fields."""
        if self._updating_fields:
            return
        fps = self._source_fps
        end_s = _tc_to_secs(self._end_edit.text(), fps)
        dur_s = max(0.0, end_s - secs)
        self._updating_fields = True
        self._start_edit.setText(_secs_to_tc(secs, fps))
        self._dur_edit.setText(_secs_to_tc(dur_s, fps))
        self._updating_fields = False
        self._tl_in_lbl.setText(f"In: {_secs_to_tc(secs, fps)}")
        self._update_size_estimate()
        self._settings.set("last_wa_start", self._start_edit.text())

    def _on_out_marker_moved(self, secs: float):
        """Out marker dragged → update End and Duration fields."""
        if self._updating_fields:
            return
        fps = self._source_fps
        start_s = _tc_to_secs(self._start_edit.text(), fps)
        dur_s   = max(0.0, secs - start_s)
        self._updating_fields = True
        self._end_edit.setText(_secs_to_tc(secs, fps))
        self._dur_edit.setText(_secs_to_tc(dur_s, fps))
        self._updating_fields = False
        self._tl_out_lbl.setText(f"Out: {_secs_to_tc(secs, fps)}")
        self._update_size_estimate()

    def _update_tl_labels(self, pos_secs: float):
        fps = self._source_fps
        self._tl_pos_lbl.setText(_secs_to_tc(pos_secs, fps))
        in_s  = _tc_to_secs(self._start_edit.text(), fps)
        out_s = _tc_to_secs(self._end_edit.text(),   fps)
        self._tl_in_lbl.setText(f"In: {_secs_to_tc(in_s, fps)}")
        self._tl_out_lbl.setText(f"Out: {_secs_to_tc(out_s, fps)}")

    # ── Frame preview ─────────────────────────────────────────────────────────

    def _request_preview(self):
        src = self._src_edit.text()
        if not src:
            return

        # Use scrubber position; fall back to in-point if no duration known
        pos_s = self._timeline._pos if self._source_dur > 0 else \
                _tc_to_secs(self._start_edit.text(), self._source_fps)
        tc = _secs_to_ffmpeg(pos_s)

        self._retire(self._preview_worker, ("done", "error"))
        self._preview_worker = None
        self._retire(self._before_worker, ("done",))
        self._before_worker = None

        self._preview_label.setText("Loading preview…")

        app_dir  = get_app_dir()
        temp_dir = app_dir / "_temp"
        temp_dir.mkdir(exist_ok=True)

        before_path = str(temp_dir / "preview_before.jpg")
        after_path  = str(temp_dir / "preview_after.jpg")
        self._before_path = before_path
        self._after_path  = after_path

        self._preview_worker = FramePreviewWorker(
            source   = src,
            timecode = tc,
            grade    = self._selected_grade,
            out_path = after_path,
        )
        self._preview_worker.done.connect(self._on_after_preview)
        self._preview_worker.error.connect(self._on_preview_error)
        self._preview_worker.start()

        self._before_worker = FramePreviewWorker(
            source   = src,
            timecode = tc,
            grade    = None,
            out_path = before_path,
        )
        self._before_worker.done.connect(lambda p: setattr(self, '_before_path', p))
        self._before_worker.start()

    def _retire(self, worker, signals: tuple = ()):
        """Park a superseded worker so its QThread isn't GC'd while running."""
        if worker is None:
            return
        for name in signals:
            try:
                getattr(worker, name).disconnect()
            except (RuntimeError, TypeError):
                pass
        if worker.isRunning():
            self._retired_workers.append(worker)
            worker.finished.connect(self._prune_retired)

    def _prune_retired(self):
        self._retired_workers = [w for w in self._retired_workers if w.isRunning()]

    def _on_after_preview(self, path: str):
        self._after_path = path
        if self._showing_after:
            self._show_preview(path, graded=True)

    def _on_preview_error(self, msg: str):
        self._preview_label.setText(f"Preview failed\n{msg[:100]}")

    def _toggle_before_after(self):
        self._showing_after = not self._before_after_btn.isChecked()
        if self._showing_after and self._after_path and Path(self._after_path).exists():
            self._show_preview(self._after_path, graded=True)
        elif not self._showing_after and self._before_path and Path(self._before_path).exists():
            self._show_preview(self._before_path, graded=False)

    def _show_preview(self, path: str, graded: bool):
        px = QPixmap(path)
        if not px.isNull():
            self._preview_label.setPixmap(
                px.scaled(426, 240, Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
            )
        grade_name = self._selected_grade.display_name if self._selected_grade else "None"
        self._ba_caption.setText(
            f"{'After: ' + grade_name if graded else 'Before (original)'}"
        )

    # ── Output folder ─────────────────────────────────────────────────────────

    def _browse_out_dir(self):
        start = self._out_dir.text() or str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "Select output folder", start)
        if folder:
            self._out_dir.setText(folder)
            self._settings.set("last_wa_output_dir", folder)

    # ── Export ────────────────────────────────────────────────────────────────

    def _start_export(self):
        src = self._src_edit.text()
        if not src or not Path(src).exists():
            QMessageBox.warning(self, "No source", "Please select a source video file.")
            return

        out_name = self._out_name.text().strip() or "whatsapp_clip.mp4"
        if not out_name.lower().endswith(".mp4"):
            out_name += ".mp4"
        out_dir = self._out_dir.text().strip()
        if not out_dir:
            QMessageBox.warning(self, "No output folder", "Please select an output folder.")
            return

        output = Path(out_dir) / out_name

        # Convert HH:MM:SS:FF → HH:MM:SS.mmm for ffmpeg
        fps     = self._source_fps
        start_s = _tc_to_secs(self._start_edit.text(), fps)
        dur_s   = _tc_to_secs(self._dur_edit.text(),   fps)
        start_str = _secs_to_ffmpeg(start_s)
        dur_str   = _secs_to_ffmpeg(dur_s)

        self._progress_frame.show()
        self._pbar.setValue(0)
        self._export_btn.hide()
        self._cancel_btn.show()

        self._worker = WhatsAppWorker(
            source         = src,
            start          = start_str,
            duration       = dur_str,
            output         = output,
            grade          = self._selected_grade,
            enable_preview = self._preview_check.isChecked(),
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.thumbnail.connect(self._on_thumbnail)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _cancel_export(self):
        if self._worker:
            self._worker.cancel()

    def _on_progress(self, data: dict):
        pct  = data.get("pct", 0)
        size = data.get("size", 0)
        self._pbar.setValue(int(pct))
        size_mb = size / 1024 / 1024
        self._stats_label.setText(f"{size_mb:.1f} MB  ·  {pct:.1f}%")

    def _on_thumbnail(self, path: str):
        px = QPixmap(path)
        if not px.isNull():
            self._thumb_label.setPixmap(
                px.scaled(240, 135, Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
            )

    def _on_finished(self, success: bool, message: str):
        self._cancel_btn.hide()
        self._export_btn.show()
        # Wait the worker thread out BEFORE dropping the last reference —
        # destroying a live QThread aborts the whole process.
        worker, self._worker = self._worker, None
        settle(worker)
        out_path = Path(self._out_dir.text()) / (self._out_name.text() or "whatsapp_clip.mp4")
        # Log the result
        try:
            log_manager.log_whatsapp(
                source       = self._src_edit.text(),
                output       = str(out_path),
                start_str    = self._start_edit.text(),
                duration_str = self._dur_edit.text(),
                grade        = self._selected_grade.display_name if self._selected_grade else None,
                success      = success,
                message      = message,
            )
        except Exception:
            pass

        if success:
            self._pbar.setValue(100)
            QMessageBox.information(self, "Done", f"Export complete!\n\n{message}\n{out_path}")
        else:
            QMessageBox.warning(self, "Failed", f"Export failed:\n{message}")
