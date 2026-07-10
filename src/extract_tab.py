"""extract_tab.py — "Extract and Recover" tab: the original WhatsApp-clip
Share half (trim + colour-grade + export a short clip), plus an Extract half
(recover original camera clips from a master). Extract prefers manifest-
driven recovery (bit-exact/near-exact, aware of archival tracks — see
core/extract.py and extract_workers.py) but falls back to chapter-based
recovery when a master has no manifest (an older master, or one merged
without "Archival master" ticked) — every master this app produces titles
its chapters with the original clip's filename, so camera grouping and
filenames still recover correctly even without a manifest; a third-party
chapter-marked MOV this app never produced just gets generic titles/one
camera bucket. A segmented toggle at the top switches between Share and
Extract — they operate on different kinds of source file and don't share
state."""

import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer, QThread, Signal
from PySide6.QtGui import QPixmap, QColor, QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QScrollArea,
    QLabel, QPushButton, QLineEdit, QFileDialog, QDialog,
    QProgressBar, QFrame, QSizePolicy, QComboBox,
    QMessageBox, QCheckBox, QTreeWidget, QTreeWidgetItem, QAbstractItemView,
)

from ffmpeg_runner import WhatsAppWorker, FramePreviewWorker, get_ffmpeg, get_app_dir
from thread_utils import settle
from grade_manager import Grade, scan_luts, migrate_grade_key
from probe import probe as probe_file
from settings import Settings
from widgets.timeline import TrimTimeline as TimelineWidget, secs_to_tc as _secs_to_tc
from core.manifest import Manifest, ClipEntry
from core.extract import (generic_recovered_filename, build_recovery_plan, build_preview_sample_cmd,
                          recovered_filenames, is_mp4_compatible_audio, build_generic_recovery_plans,
                          GenericRecoveryPlan)
from camera_id import identify_camera
from extract_workers import ManifestLoadWorker, ExtractWorker, GenericExtractWorker
from merge_tab import _ClipPreviewDialog
import log_manager
import theme


# ── Extract clip-tree columns ─────────────────────────────────────────────────
EX_COL_NAME = 0
EX_COL_PREVIEW = 1
EX_COL_DURATION = 2
EX_COL_CAMERA = 3
EX_COL_SPEC = 4
EX_COL_RECOVERY = 5
EX_COL_EDIT = 6      # manual-mode only: per-row "edit boundaries/name" button
EX_COL_REMOVE = 7    # manual-mode only: per-row "remove this segment" button
EX_N_COLS = 8

# Manual-mode audio-role choices for the Extract tab's per-track combo —
# values match GenericRecoveryPlan's audio_stream/wav_stream assignment.
_AUDIO_ROLE_IGNORE = "ignore"
_AUDIO_ROLE_CAMERA = "camera"
_AUDIO_ROLE_WAV = "wav"


def _fmt_extract_dur(secs: float) -> str:
    if secs <= 0:
        return "—"
    h, r = divmod(int(secs), 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _fmt_extract_fps(fps_str: str) -> str:
    if not fps_str:
        return ""
    try:
        if "/" in fps_str:
            num, den = fps_str.split("/")
            val = float(num) / float(den)
        else:
            val = float(fps_str)
    except (ValueError, ZeroDivisionError):
        return ""
    return f"{round(val)}fps" if abs(val - round(val)) < 0.02 else f"{val:.2f}fps"


class _ExtractSampleThread(QThread):
    """Extracts a short 160p proxy sample for the Extract tab's per-clip
    preview button, from a clip still embedded in the master (see
    core.extract.build_preview_sample_cmd). Same pattern as the Merge tab's
    _ClipSampleThread, just built from a pre-made command since the source
    here is a seek+map into the master rather than a standalone file."""
    done = Signal(int, str, str)   # row_idx, out_path, error ("" if none)

    def __init__(self, cmd: list, row_idx: int, out_path):
        super().__init__()
        self._cmd, self._row_idx, self._out_path = cmd, row_idx, out_path

    def run(self):
        kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
        try:
            r = subprocess.run(self._cmd, capture_output=True, text=True, timeout=30, **kwargs)
            err = "" if r.returncode == 0 and Path(self._out_path).exists() else (r.stderr or "sample extraction failed")
        except Exception as e:
            err = str(e)
        self.done.emit(self._row_idx, str(self._out_path), err)


class _CreateFolderDialog(QDialog):
    """Suggests a recovered-clips output folder (name derived from the
    master's own filename, location defaulting to the master's own
    directory) and lets the user tweak either before it's actually created."""

    def __init__(self, suggested_name: str, suggested_parent: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Create output folder")
        self._parent_dir = suggested_parent

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Folder name:"))
        self._name_edit = QLineEdit(suggested_name)
        lay.addWidget(self._name_edit)

        lay.addWidget(QLabel("Location:"))
        loc_row = QHBoxLayout()
        self._loc_edit = QLineEdit(suggested_parent)
        self._loc_edit.setReadOnly(True)
        loc_browse = QPushButton("Browse…")
        loc_browse.clicked.connect(self._browse_location)
        loc_row.addWidget(self._loc_edit, 1)
        loc_row.addWidget(loc_browse)
        lay.addLayout(loc_row)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        create = QPushButton("Create")
        create.setDefault(True)
        create.clicked.connect(self.accept)
        btn_row.addWidget(cancel)
        btn_row.addWidget(create)
        lay.addLayout(btn_row)

    def _browse_location(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose a location", self._parent_dir)
        if folder:
            self._parent_dir = folder
            self._loc_edit.setText(folder)

    def full_path(self) -> Optional[Path]:
        name = self._name_edit.text().strip()
        if not name:
            return None
        return Path(self._parent_dir) / name


class _ManualClipDialog(QDialog):
    """Add or edit a manually-defined clip boundary in the Extract tab's
    no-manifest fallback — the only way to recover a clip from a foreign
    master with wrong/missing chapter markers, or none at all. Start/
    duration use the same HH:MM:SS(.mmm) text format as the Share panel's
    trim fields (`_tc_to_secs`/`_secs_to_ffmpeg`)."""

    def __init__(self, name: str = "", start: float = 0.0, duration: float = 0.0, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Clip boundaries")
        self.setMinimumWidth(340)

        lay = QVBoxLayout(self)
        grid = QGridLayout()
        grid.addWidget(QLabel("Name:"), 0, 0)
        self._name_edit = QLineEdit(name)
        grid.addWidget(self._name_edit, 0, 1)
        grid.addWidget(QLabel("Start (H:MM:SS):"), 1, 0)
        self._start_edit = QLineEdit(_secs_to_ffmpeg(start)[:-4])   # drop the .mmm — seconds precision is enough
        grid.addWidget(self._start_edit, 1, 1)
        grid.addWidget(QLabel("Duration (H:MM:SS):"), 2, 0)
        self._dur_edit = QLineEdit(_secs_to_ffmpeg(duration)[:-4] if duration else "")
        grid.addWidget(self._dur_edit, 2, 1)
        lay.addLayout(grid)

        self._error_label = QLabel("")
        self._error_label.setWordWrap(True)
        self._error_label.hide()
        lay.addWidget(self._error_label)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Apply")
        ok.setDefault(True)
        ok.clicked.connect(self._try_accept)
        btn_row.addWidget(cancel)
        btn_row.addWidget(ok)
        lay.addLayout(btn_row)

    def _try_accept(self):
        if not self._name_edit.text().strip():
            self._show_error("Enter a name for this clip.")
            return
        if _tc_to_secs(self._dur_edit.text()) <= 0:
            self._show_error("Enter a duration greater than 0 (H:MM:SS, e.g. 0:01:30).")
            return
        self.accept()

    def _show_error(self, text: str):
        p = theme.active_palette()
        self._error_label.setText(text)
        self._error_label.setStyleSheet(f"color:{p.danger};")
        self._error_label.show()

    def values(self) -> tuple:
        """(name, start_seconds, duration_seconds)."""
        return (self._name_edit.text().strip(),
                _tc_to_secs(self._start_edit.text()),
                _tc_to_secs(self._dur_edit.text()))


# ── Verification report parsing (core.verify.write_verify_log's format) ───────

_VERIFY_RESULT_RE = re.compile(r"Result:\s*(\d+)\s*/\s*(\d+)\s*clips verified")
_VERIFY_FAIL_RE = re.compile(r"^FAIL\s+(.+)$", re.MULTILINE)


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

class ExtractTab(QWidget):
    open_share_requested = Signal()   # "Share a clip" now lives in the Review tab

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
        self._extract_generic_plans: Optional[list] = None   # no-manifest fallback (chapter-based)
        self._manifest_load_worker: Optional[ManifestLoadWorker] = None
        self._extract_worker = None   # ExtractWorker | GenericExtractWorker
        self._extract_items: dict = {}   # ClipEntry -> QTreeWidgetItem (for progress updates)
        self._extract_preview_threads: list = []   # keep _ExtractSampleThread instances alive while running
        self._extract_preview_dialogs: list = []   # keep open _ClipPreviewDialog instances alive
        self._extract_preview_cache: dict = {}     # row idx -> generated sample path

        # ── Manual controls (foreign masters with no manifest, or "ignore
        # manifest" opted in for one that IS present) ───────────────────────
        self._extract_audio_tracks: list = []      # list[AudioTrackInfo], probed at load time
        self._extract_video_tracks: list = []      # list[VideoTrackInfo], probed at load time
        self._extract_chapters: list = []          # list[ChapterInfo], probed at load time — kept
                                                    # around so toggling "ignore manifest" can build
                                                    # the generic path without a second probe
        self._ex_audio_role_combos: list = []      # one per detected audio track
        self._ex_video_track_combo = None          # only shown when >1 video stream
        self._ex_rotation_combo = None

        self.setAcceptDrops(True)
        self._setup_ui()
        self._load_grades()
        self._restyle()
        ctrl = theme.controller()
        if ctrl is not None:
            ctrl.changed.connect(self._restyle)

    # ── Drag-and-drop (Extract panel only — Share uses its own Browse flow) ───

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            for u in event.mimeData().urls():
                if u.toLocalFile().lower().endswith((".mov", ".mp4")):
                    event.acceptProposedAction()
                    return

    def dropEvent(self, event):
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

        # "Share a clip" now lives in the Review tab (its widget is built here,
        # unchanged, then embedded there by main.py via `share_panel()`) — this
        # tab is Extract-only, with a shortcut back for anyone used to the old
        # combined layout.
        shortcut_row = QHBoxLayout()
        shortcut_row.addWidget(QLabel("Extract and recover original camera clips from a master."))
        shortcut_row.addStretch()
        share_shortcut_btn = QPushButton("Share a clip →")
        share_shortcut_btn.setToolTip("Trim + colour-grade + export a short clip — now in the Review tab.")
        share_shortcut_btn.clicked.connect(self.open_share_requested.emit)
        shortcut_row.addWidget(share_shortcut_btn)
        outer.addLayout(shortcut_row)

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

        # self._share_panel is NOT added to this tab's own layout — main.py
        # embeds it into the Review tab via share_panel() below.

        self._extract_panel = self._build_extract_panel()
        outer.addWidget(self._extract_panel, 1)

    def share_panel(self) -> QWidget:
        """The Share-a-clip widget, built (and still fully owned/updated) by
        this class — returned so main.py can embed it in the Review tab."""
        return self._share_panel

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

        self._ex_verify_banner = QLabel()
        self._ex_verify_banner.setWordWrap(True)
        self._ex_verify_banner.hide()
        lay.addWidget(self._ex_verify_banner)

        # Escape hatch for a manifest that's present but wrong/incomplete (a
        # hand-edited master, one from an older/buggy version, or one this app
        # merged but whose manifest got corrupted/truncated in transit) — lets
        # the user fall back to the same manual controls a no-manifest master
        # gets, using the SAME probed chapters/audio/video tracks (no re-probe
        # needed, see _apply_extract_mode). Only shown once a manifest has
        # actually been found; a manifest-less master already starts in manual
        # mode with nothing to opt out of.
        self._ex_ignore_manifest_check = QCheckBox("Ignore manifest — use manual controls instead")
        self._ex_ignore_manifest_check.hide()
        self._ex_ignore_manifest_check.toggled.connect(self._on_ignore_manifest_toggled)
        lay.addWidget(self._ex_ignore_manifest_check)

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
        self._ex_tree.setHeaderLabels(["Clip", "", "Duration", "Camera", "Spec", "Recovers as", "", ""])
        self._ex_tree.header().setSectionResizeMode(EX_COL_NAME, self._ex_tree.header().ResizeMode.Stretch)
        for col in (EX_COL_PREVIEW, EX_COL_EDIT, EX_COL_REMOVE):
            self._ex_tree.header().setSectionResizeMode(col, self._ex_tree.header().ResizeMode.ResizeToContents)
        self._ex_tree.setAlternatingRowColors(True)
        self._ex_tree.setMinimumHeight(200)
        lay.addWidget(self._ex_tree, 1)

        # Manual controls — only shown for a foreign (no-manifest) master, built
        # fresh per master load (_rebuild_manual_controls) since the audio/video
        # track counts vary. Lets the user assign audio-track roles (camera/WAV/
        # ignore), pick a video stream, override rotation, and hand-add/edit/
        # remove clip boundaries when chapters are missing or wrong — none of
        # which a manifest-less master can determine on its own.
        self._ex_manual_frame = QFrame()
        self._ex_manual_frame.setFrameShape(QFrame.Shape.StyledPanel)
        self._ex_manual_layout = QVBoxLayout(self._ex_manual_frame)
        self._ex_manual_frame.hide()
        lay.addWidget(self._ex_manual_frame)

        format_row = QHBoxLayout()
        format_row.addWidget(QLabel("Recover video as:"))
        self._ex_format_combo = QComboBox()
        self._ex_format_combo.addItem("Native (keep each clip's own format)", "native")
        self._ex_format_combo.addItem("MOV", "mov")
        self._ex_format_combo.addItem("MP4 (splits out incompatible audio, e.g. ALAC/PCM → WAV)", "mp4")
        saved_format = self._settings.get("extract_output_format", "native")
        fmt_idx = self._ex_format_combo.findData(saved_format)
        self._ex_format_combo.setCurrentIndex(fmt_idx if fmt_idx >= 0 else 0)
        self._ex_format_combo.currentIndexChanged.connect(self._on_extract_format_changed)
        format_row.addWidget(self._ex_format_combo, 1)
        lay.addLayout(format_row)

        out_row = QHBoxLayout()
        self._ex_out_dir = QLineEdit()
        self._ex_out_dir.setPlaceholderText("Output folder for recovered clips…")
        self._ex_out_dir.setReadOnly(True)
        ex_out_btn = QPushButton("Browse…")
        ex_out_btn.setFixedWidth(90)
        ex_out_btn.clicked.connect(self._browse_extract_out_dir)
        ex_create_btn = QPushButton("Create folder…")
        ex_create_btn.setFixedWidth(110)
        ex_create_btn.clicked.connect(self._create_extract_out_dir)
        out_row.addWidget(QLabel("Output folder:"))
        out_row.addWidget(self._ex_out_dir, 1)
        out_row.addWidget(ex_create_btn)
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

    def _current_extract_container(self) -> str:
        return self._ex_format_combo.currentData() or "native"

    def _on_extract_format_changed(self, _idx: int):
        self._settings.set("extract_output_format", self._ex_format_combo.currentData())
        # Keep the "Recovers as" preview and filenames in the tree accurate if the
        # user changes format after a master's already loaded (re-check state is
        # preserved since we rebuild from the same underlying manifest/plans).
        checked_before = {idx for idx, item in self._extract_items.items()
                          if item.checkState(EX_COL_NAME) == Qt.CheckState.Checked}
        # _extract_generic_plans is None exactly when manifest-driven recovery is
        # active (see _apply_extract_mode) — checked first so an "ignore manifest"
        # override stays in effect even though self._extract_manifest is still set.
        if self._extract_generic_plans is not None:
            self._populate_extract_tree_generic(self._extract_generic_plans)
        elif self._extract_manifest is not None and self._extract_manifest.clips:
            self._populate_extract_tree(self._extract_manifest)
        else:
            return
        for idx, item in self._extract_items.items():
            item.setCheckState(EX_COL_NAME, Qt.CheckState.Checked if idx in checked_before else Qt.CheckState.Unchecked)

    def _browse_extract_master(self):
        start = self._settings.get("last_extract_source", "") or str(Path.home())
        path, _ = QFileDialog.getOpenFileName(
            self, "Select archival master", str(Path(start).parent) if start else "",
            "Video files (*.mov *.mp4);;All files (*)")
        if path:
            self._load_extract_master(path)

    def _update_verify_banner(self, master_path: str):
        """A master merged with 'Verify MD5 recovery' leaves a sibling
        <stem>.verify.log — surface its result right here, before the user
        even picks what to recover, instead of leaving them to wonder whether
        what they archived is really what they'll get back."""
        report = Path(master_path).parent / (Path(master_path).stem + ".verify.log")
        if not report.exists():
            self._ex_verify_banner.hide()
            return
        try:
            text = report.read_text(encoding="utf-8")
        except OSError:
            self._ex_verify_banner.hide()
            return
        m = _VERIFY_RESULT_RE.search(text)
        if not m:
            self._ex_verify_banner.hide()
            return
        passed, total = int(m.group(1)), int(m.group(2))
        p = theme.active_palette()
        if total > 0 and passed == total:
            self._ex_verify_banner.setText(
                f"✓ Verified when created — all {total} clip{'s' if total != 1 else ''} "
                "confirmed byte-identical to their originals.")
            self._ex_verify_banner.setStyleSheet(
                f"background:{p.ok}22; border:1px solid {p.ok}; color:{p.text}; "
                "border-radius:4px; padding:6px 10px;")
        else:
            failed = _VERIFY_FAIL_RE.findall(text)
            names = ", ".join(failed) if failed else "some clips"
            self._ex_verify_banner.setText(
                f"⚠ {total - passed} of {total} clips did not verify when this master was "
                f"created ({names}). See the verification report:\n{report}")
            self._ex_verify_banner.setStyleSheet(
                f"background:{p.banner_warn_bg}; color:{p.text}; border-radius:4px; padding:6px 10px;")
        self._ex_verify_banner.show()

    def _load_extract_master(self, path: str):
        self._settings.set("last_extract_source", path)
        self._ex_master_edit.setText(path)
        self._extract_master_path = path
        self._extract_manifest = None
        self._extract_generic_plans = None
        self._extract_audio_tracks = []
        self._extract_video_tracks = []
        self._extract_chapters = []
        self._ex_tree.clear()
        self._extract_items = {}
        self._ex_extract_btn.setEnabled(False)
        self._ex_manual_frame.hide()
        self._ex_ignore_manifest_check.hide()
        self._ex_ignore_manifest_check.blockSignals(True)
        self._ex_ignore_manifest_check.setChecked(False)   # a new master always starts trusting its own manifest
        self._ex_ignore_manifest_check.blockSignals(False)
        self._ex_status_label.setText("Reading manifest…")
        self._update_verify_banner(path)

        if self._manifest_load_worker is not None:
            settle(self._manifest_load_worker, 5000)
        _, fp = get_ffmpeg()
        w = ManifestLoadWorker(fp, path)
        w.manifest_ready.connect(self._on_extract_manifest_ready)
        self._manifest_load_worker = w
        w.start()

    def _on_extract_manifest_ready(self, manifest, chapters: list, audio_tracks: list, video_tracks: list):
        self._extract_manifest = manifest
        self._extract_audio_tracks = audio_tracks
        self._extract_video_tracks = video_tracks
        self._extract_chapters = chapters
        has_manifest = manifest is not None and bool(manifest.clips)
        self._ex_ignore_manifest_check.setVisible(has_manifest)
        self._apply_extract_mode()

    def _on_ignore_manifest_toggled(self, _checked: bool):
        self._apply_extract_mode()

    def _apply_extract_mode(self):
        """Pick manifest-driven vs. manual/generic recovery from what was
        probed at load time (_on_extract_manifest_ready) plus the "ignore
        manifest" checkbox — re-run whenever either changes, with no second
        probe needed since chapters/audio/video tracks are always read
        upfront regardless of whether a manifest exists (ManifestLoadWorker).
        """
        manifest = self._extract_manifest
        has_manifest = manifest is not None and bool(manifest.clips)
        ignore_manifest = has_manifest and self._ex_ignore_manifest_check.isChecked()

        if has_manifest and not ignore_manifest:
            self._extract_generic_plans = None
            self._ex_manual_frame.hide()
            self._ex_status_label.setText(
                f"{len(manifest.clips)} original clip(s) recorded in this master's manifest.")
            self._populate_extract_tree(manifest)
            self._ex_extract_btn.setEnabled(True)
            return

        # No manifest (or the user opted to ignore one that IS present — e.g. it's
        # wrong/incomplete for a hand-edited or corrupted master). Either way this
        # master either predates "Archival master", never had it ticked, was
        # produced by a different tool entirely, or is a chapter-marked MOV this
        # app never produced. This app always titles a chapter with the original
        # clip's filename, so camera grouping/filenames still recover correctly
        # for its own masters; a foreign master's generic/untitled chapters (or NO
        # chapters at all) fall to the manual controls below — audio-track roles,
        # video-stream choice, rotation override, and hand-added/edited clip
        # boundaries, since nothing here can be assumed.
        chapters = self._extract_chapters
        audio_indices = [t.audio_index for t in self._extract_audio_tracks]
        ignored_note = " (manifest ignored — using manual controls instead)" if ignore_manifest else ""
        if chapters:
            self._extract_generic_plans = build_generic_recovery_plans(chapters, audio_indices)
            self._ex_status_label.setText(
                f"{'Manifest ignored' if ignore_manifest else 'No manifest found'} — falling back "
                f"to {len(chapters)} chapter-marked segment{'s' if len(chapters) != 1 else ''}. "
                "Camera grouping and filenames are guessed from the chapter titles, and recovery "
                "trims the master's own baseline track rather than an archival original. Check "
                "the manual controls below — audio-track roles, rotation, and clip boundaries can "
                "all be corrected there.")
        else:
            self._extract_generic_plans = []
            self._ex_status_label.setText(
                f"No chapter markers found in this file{ignored_note}. Use the manual controls "
                "below to assign audio-track roles and add clip boundaries by hand.")
        self._rebuild_manual_controls()
        self._ex_manual_frame.show()
        self._populate_extract_tree_generic(self._extract_generic_plans)
        self._ex_extract_btn.setEnabled(bool(self._extract_generic_plans))

    def _resolve_camera_label(self, camera_id: str, fallback_label: str) -> str:
        """Prefer the CURRENT persisted name for this camera (Merge tab's
        Settings-backed camera_labels map, task 61) over whatever label this
        master's recovery data happened to record — the same physical camera
        recognised in a fresh Merge-tab folder load is recognised here too."""
        saved = self._settings.get("camera_labels", {})
        return (camera_id and saved.get(camera_id)) or fallback_label or "Unknown camera"

    def _populate_extract_tree_generic(self, plans: list):
        p = theme.active_palette()
        self._ex_tree.clear()
        self._extract_items = {}
        groups: dict = {}
        for idx, plan in enumerate(plans):
            camera = self._resolve_camera_label(plan.camera_id, plan.camera_label)
            groups.setdefault(camera, []).append((idx, plan))

        for camera, members in groups.items():
            group_item = QTreeWidgetItem(self._ex_tree)
            group_item.setText(EX_COL_NAME, f"{camera}  ({len(members)} clip{'s' if len(members) != 1 else ''})")
            font = QFont("", -1, QFont.Weight.Bold)
            for col in range(EX_N_COLS):
                group_item.setFont(col, font)
                group_item.setForeground(col, QColor(p.accent))
            for idx, plan in members:
                item = QTreeWidgetItem(group_item)
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(EX_COL_NAME, Qt.CheckState.Checked)
                item.setText(EX_COL_NAME, plan.title)
                item.setText(EX_COL_DURATION, _fmt_extract_dur(plan.duration))
                item.setText(EX_COL_CAMERA, camera)
                spec = "— (guessed from chapters)" if plan.wav_stream is None and plan.rotation is None else ""
                spec_bits = []
                if plan.wav_stream is not None:
                    spec_bits.append(f"WAV: track {plan.wav_stream}")
                if plan.rotation is not None:
                    spec_bits.append(f"rotation: {plan.rotation}°")
                item.setText(EX_COL_SPEC, " · ".join(spec_bits) or spec)
                recovers = generic_recovered_filename(plan, self._current_extract_container())
                if plan.wav_stream is not None:
                    recovers += f" + {Path(recovers).stem}.wav"
                item.setText(EX_COL_RECOVERY, recovers)
                self._add_extract_preview_button(item, idx, plan)
                self._add_generic_edit_remove_buttons(item, idx)
                self._extract_items[idx] = item
            group_item.setExpanded(True)

    def _add_generic_edit_remove_buttons(self, item: QTreeWidgetItem, row_idx: int):
        """✎/🗑 buttons on a manual-mode row — the only path to correct a
        wrongly-guessed chapter boundary or drop one entirely; also how a
        hand-added clip (no chapters at all) gets edited after the fact."""
        p = theme.active_palette()
        style = (
            f"QPushButton {{ background:{p.btn_bg}; color:{p.text}; border:1px solid {p.border}; "
            "border-radius:4px; padding:0px; font-size:12px; }"
            f"QPushButton:hover {{ border-color:{p.accent}; color:{p.accent}; }}")
        edit_btn = QPushButton("✎")
        edit_btn.setFixedSize(22, 22)
        edit_btn.setStyleSheet(style)
        edit_btn.setToolTip("Edit this clip's name/start/duration")
        edit_btn.clicked.connect(lambda _, ri=row_idx: self._edit_generic_plan(ri))
        self._ex_tree.setItemWidget(item, EX_COL_EDIT, edit_btn)

        remove_btn = QPushButton("🗑")
        remove_btn.setFixedSize(22, 22)
        remove_btn.setStyleSheet(style)
        remove_btn.setToolTip("Remove this clip from the recovery list")
        remove_btn.clicked.connect(lambda _, ri=row_idx: self._remove_generic_plan(ri))
        self._ex_tree.setItemWidget(item, EX_COL_REMOVE, remove_btn)

    # ── Manual controls (foreign masters with no manifest) ────────────────────

    def _rebuild_manual_controls(self):
        """Rebuild the manual-controls section from the currently probed
        audio/video tracks — called once per master load. Only relevant while
        the generic (no-manifest) path is active; a manifest-driven master
        already knows every role/boundary precisely and never shows this."""
        while self._ex_manual_layout.count():
            item = self._ex_manual_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._ex_audio_role_combos = []
        self._ex_video_track_combo = None
        self._ex_rotation_combo = None

        title = QLabel("Manual controls — no manifest found in this master")
        title.setStyleSheet("font-weight:bold;")
        self._ex_manual_layout.addWidget(title)

        # Reflect whatever role/stream/rotation the plans ALREADY carry (e.g.
        # re-showing this after edits, or the plain camera-track-0 default
        # build_generic_recovery_plans used) rather than always resetting to
        # a fresh default.
        cur_cam = cur_wav = None
        cur_vs = 0
        cur_rot = None
        if self._extract_generic_plans:
            cur_cam = self._extract_generic_plans[0].audio_stream
            cur_wav = self._extract_generic_plans[0].wav_stream
            cur_vs = self._extract_generic_plans[0].video_stream
            cur_rot = self._extract_generic_plans[0].rotation
        elif self._extract_audio_tracks:
            cur_cam = self._extract_audio_tracks[0].audio_index

        if self._extract_audio_tracks:
            audio_box = QWidget()
            audio_lay = QVBoxLayout(audio_box)
            audio_lay.setContentsMargins(0, 4, 0, 4)
            audio_lay.addWidget(QLabel("Audio tracks — assign each one's role:"))
            for t in self._extract_audio_tracks:
                row = QHBoxLayout()
                desc = f"Track {t.audio_index}: {(t.codec or '?').upper()}"
                if t.channels:
                    desc += f", {t.channels}ch"
                if t.sample_rate:
                    desc += f", {t.sample_rate}Hz"
                row.addWidget(QLabel(desc))
                combo = QComboBox()
                combo.addItem("Ignore", _AUDIO_ROLE_IGNORE)
                combo.addItem("Camera audio", _AUDIO_ROLE_CAMERA)
                combo.addItem("WAV backup", _AUDIO_ROLE_WAV)
                if t.audio_index == cur_cam:
                    combo.setCurrentIndex(combo.findData(_AUDIO_ROLE_CAMERA))
                elif t.audio_index == cur_wav:
                    combo.setCurrentIndex(combo.findData(_AUDIO_ROLE_WAV))
                combo.currentIndexChanged.connect(self._on_audio_roles_changed)
                row.addWidget(combo, 1)
                self._ex_audio_role_combos.append(combo)
                audio_lay.addLayout(row)
            self._ex_manual_layout.addWidget(audio_box)

        if len(self._extract_video_tracks) > 1:
            video_box = QWidget()
            video_lay = QHBoxLayout(video_box)
            video_lay.setContentsMargins(0, 4, 0, 4)
            video_lay.addWidget(QLabel("Video track:"))
            combo = QComboBox()
            for t in self._extract_video_tracks:
                combo.addItem(f"Track {t.video_index}: {(t.codec or '?').upper()} {t.width}x{t.height}",
                             t.video_index)
            idx = combo.findData(cur_vs)
            combo.setCurrentIndex(idx if idx >= 0 else 0)
            combo.currentIndexChanged.connect(self._on_video_track_changed)
            video_lay.addWidget(combo, 1)
            self._ex_video_track_combo = combo
            self._ex_manual_layout.addWidget(video_box)

        rot_box = QWidget()
        rot_lay = QHBoxLayout(rot_box)
        rot_lay.setContentsMargins(0, 4, 0, 4)
        rot_lay.addWidget(QLabel("Rotation override:"))
        rot_combo = QComboBox()
        rot_combo.addItem("Auto (unchanged)", "auto")
        for deg in (0, 90, 180, 270):
            rot_combo.addItem(f"{deg}°", deg)
        if cur_rot is None:
            rot_combo.setCurrentIndex(0)
        else:
            idx = rot_combo.findData(cur_rot)
            rot_combo.setCurrentIndex(idx if idx >= 0 else 0)
        rot_combo.currentIndexChanged.connect(self._on_rotation_override_changed)
        rot_lay.addWidget(rot_combo, 1)
        self._ex_rotation_combo = rot_combo
        self._ex_manual_layout.addWidget(rot_box)

        add_row = QHBoxLayout()
        add_row.setContentsMargins(0, 4, 0, 0)
        add_row.addStretch()
        add_btn = QPushButton("+ Add clip…")
        add_btn.clicked.connect(self._add_generic_plan)
        add_row.addWidget(add_btn)
        add_box = QWidget()
        add_box.setLayout(add_row)
        self._ex_manual_layout.addWidget(add_box)

    def _current_audio_role_assignment(self) -> tuple:
        """(camera_audio_index, wav_audio_index) from the manual role combos —
        the last track marked with a given role wins if more than one is
        (mis)assigned the same role."""
        cam_idx = wav_idx = None
        for t, combo in zip(self._extract_audio_tracks, self._ex_audio_role_combos):
            role = combo.currentData()
            if role == _AUDIO_ROLE_CAMERA:
                cam_idx = t.audio_index
            elif role == _AUDIO_ROLE_WAV:
                wav_idx = t.audio_index
        return cam_idx, wav_idx

    def _current_rotation_override(self):
        if self._ex_rotation_combo is None:
            return None
        val = self._ex_rotation_combo.currentData()
        return None if val in (None, "auto") else val

    def _refresh_generic_tree_preserving_checks(self):
        checked_before = {idx for idx, item in self._extract_items.items()
                          if item.checkState(EX_COL_NAME) == Qt.CheckState.Checked}
        self._populate_extract_tree_generic(self._extract_generic_plans or [])
        for idx, item in self._extract_items.items():
            item.setCheckState(EX_COL_NAME,
                              Qt.CheckState.Checked if idx in checked_before else Qt.CheckState.Unchecked)

    def _on_audio_roles_changed(self, _idx=None):
        cam_idx, wav_idx = self._current_audio_role_assignment()
        for plan in (self._extract_generic_plans or []):
            plan.audio_stream = cam_idx
            plan.wav_stream = wav_idx
        self._refresh_generic_tree_preserving_checks()

    def _on_video_track_changed(self, _idx=None):
        if self._ex_video_track_combo is None:
            return
        vs = self._ex_video_track_combo.currentData()
        for plan in (self._extract_generic_plans or []):
            plan.video_stream = vs
        self._refresh_generic_tree_preserving_checks()

    def _on_rotation_override_changed(self, _idx=None):
        rotation = self._current_rotation_override()
        for plan in (self._extract_generic_plans or []):
            plan.rotation = rotation
        self._refresh_generic_tree_preserving_checks()

    def _add_generic_plan(self):
        dlg = _ManualClipDialog(parent=self)
        if not dlg.exec():
            return
        name, start, duration = dlg.values()
        self._commit_generic_plan(None, name, start, duration)

    def _edit_generic_plan(self, idx: int):
        if not self._extract_generic_plans or idx >= len(self._extract_generic_plans):
            return
        plan = self._extract_generic_plans[idx]
        dlg = _ManualClipDialog(plan.title, plan.start, plan.duration, parent=self)
        if not dlg.exec():
            return
        name, start, duration = dlg.values()
        self._commit_generic_plan(idx, name, start, duration)

    def _remove_generic_plan(self, idx: int):
        if not self._extract_generic_plans or idx >= len(self._extract_generic_plans):
            return
        del self._extract_generic_plans[idx]
        self._refresh_generic_tree_preserving_checks()
        self._ex_extract_btn.setEnabled(bool(self._extract_generic_plans))
        if not self._extract_generic_plans:
            self._ex_status_label.setText(
                "No clip segments defined — use \"+ Add clip…\" to add one.")

    def _commit_generic_plan(self, idx: Optional[int], name: str, start: float, duration: float):
        """Add (idx=None) or update (idx=existing position) a manually-defined
        GenericRecoveryPlan from the "+ Add clip…"/✎ Edit dialog, carrying the
        current audio-role/video-stream/rotation manual settings."""
        cam_idx, wav_idx = self._current_audio_role_assignment()
        video_stream = self._ex_video_track_combo.currentData() if self._ex_video_track_combo else 0
        rotation = self._current_rotation_override()
        key, label = identify_camera("", name)
        if self._extract_generic_plans is None:
            self._extract_generic_plans = []
        if idx is None:
            self._extract_generic_plans.append(GenericRecoveryPlan(
                title=name, index=len(self._extract_generic_plans), start=start, duration=duration,
                camera_id=key, camera_label=label, video_stream=video_stream or 0,
                audio_stream=cam_idx, wav_stream=wav_idx, rotation=rotation,
            ))
        else:
            plan = self._extract_generic_plans[idx]
            plan.title, plan.start, plan.duration = name, start, duration
            plan.camera_id, plan.camera_label = key, label
        self._ex_status_label.setText(
            f"{len(self._extract_generic_plans)} clip segment"
            f"{'s' if len(self._extract_generic_plans) != 1 else ''} — manually adjusted.")
        self._ex_extract_btn.setEnabled(True)
        self._refresh_generic_tree_preserving_checks()

    def _populate_extract_tree(self, manifest: Manifest):
        p = theme.active_palette()
        self._ex_tree.clear()
        self._extract_items = {}
        groups: dict = {}
        for idx, entry in enumerate(manifest.clips):
            camera = self._resolve_camera_label(entry.camera_id, entry.camera_label)
            groups.setdefault(camera, []).append((idx, entry))

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
                item.setText(EX_COL_DURATION, _fmt_extract_dur(entry.duration))
                item.setText(EX_COL_CAMERA, camera)
                spec_bits = [f"{(entry.codec or '?').upper()}", f"{entry.width}x{entry.height}"]
                fps_disp = _fmt_extract_fps(entry.fps)
                if fps_disp:
                    spec_bits.append(fps_disp)
                spec_bits.append(f"{entry.bit_depth}-bit")
                if entry.color_space:
                    spec_bits.append(entry.color_space.upper())
                spec = " · ".join(spec_bits)
                if entry.rotation:
                    spec += f", {entry.rotation}°"
                if entry.is_vfr:
                    spec += " (VFR)"
                item.setText(EX_COL_SPEC, spec)
                container = self._current_extract_container()
                video_name, wav_name = recovered_filenames(entry, container)
                recovers = video_name
                split_camera_audio = (container == "mp4" and entry.has_camera_audio
                                      and not is_mp4_compatible_audio(entry.original_audio_codec))
                if split_camera_audio:
                    recovers += f" + {Path(entry.source_filename).stem} (camera audio).wav"
                if wav_name:
                    recovers += f" + {wav_name}"
                if entry.wav_archival_stream is not None:
                    recovers += f" + {Path(entry.source_filename).stem} (WAV - preserved original).wav"
                if entry.lrv_video_archival_track is not None:
                    recovers += f" + {Path(entry.source_filename).stem} (LRV proxy).mov"
                if entry.conform_status != "ok" and entry.archival_track is not None:
                    recovers += "" if entry.in_track_start == 0.0 else "  (near-exact)"
                item.setText(EX_COL_RECOVERY, recovers)
                plan = build_recovery_plan(manifest, entry)
                self._add_extract_preview_button(item, idx, plan)
                self._extract_items[idx] = item
            group_item.setExpanded(True)

    def _add_extract_preview_button(self, item: QTreeWidgetItem, row_idx: int, plan):
        """A ▶ button next to the clip name — same low-res midpoint preview as
        the Merge tab's (task 63), but the source clip is still embedded in
        the master here, so the sample is seeked/mapped straight out of it
        (core.extract.build_preview_sample_cmd) rather than read from a
        standalone file."""
        p = theme.active_palette()
        btn = QPushButton("▶")
        btn.setFixedSize(24, 24)
        btn.setStyleSheet(
            f"QPushButton {{ background:{p.btn_bg}; color:{p.text}; border:1px solid {p.border}; "
            "border-radius:4px; padding:0px; font-size:14px; }"
            f"QPushButton:hover {{ border-color:{p.accent}; color:{p.accent}; }}")
        btn.setToolTip("Play a quick low-res preview, starting from the middle of the clip")
        clip_dur = getattr(plan, "video_duration", None)
        if clip_dur is None:
            clip_dur = getattr(plan, "duration", 0.0) if plan else 0.0
        btn.setEnabled(bool(plan) and clip_dur > 0)
        btn.clicked.connect(lambda _, ri=row_idx, pl=plan, b=btn: self._on_extract_preview_clicked(ri, pl, b))
        self._ex_tree.setItemWidget(item, EX_COL_PREVIEW, btn)

    def _on_extract_preview_clicked(self, row_idx: int, plan, btn: QPushButton):
        cached = self._extract_preview_cache.get(row_idx)
        if cached and Path(cached).exists():
            self._show_extract_preview_dialog(cached)
            return
        if plan is None:
            return
        clip_start = getattr(plan, "video_start", None)
        if clip_start is None:
            clip_start = getattr(plan, "start", 0.0)
        clip_dur = getattr(plan, "video_duration", None)
        if clip_dur is None:
            clip_dur = getattr(plan, "duration", 0.0)
        if clip_dur <= 0:
            return
        btn.setEnabled(False)
        btn.setText("…")
        mid_ts = clip_start + clip_dur / 2.0                  # "middle of the clip"
        sample_dur = min(5.0, max(0.5, clip_dur - clip_dur / 2.0))
        ff, _ = get_ffmpeg()
        out_dir = get_app_dir() / "_temp" / "extract_previews"
        out_dir.mkdir(parents=True, exist_ok=True)
        cache_key = f"{self._extract_master_path}:{row_idx}"
        out_path = out_dir / f"preview_{abs(hash(cache_key))}.mp4"
        cmd = build_preview_sample_cmd(ff, self._extract_master_path, plan, mid_ts, sample_dur, str(out_path))
        thread = _ExtractSampleThread(cmd, row_idx, out_path)
        thread.done.connect(lambda ri, path, err, b=btn: self._on_extract_sample_done(ri, b, path, err))
        thread.finished.connect(lambda t=thread: t in self._extract_preview_threads and self._extract_preview_threads.remove(t))
        self._extract_preview_threads.append(thread)
        thread.start()

    def _on_extract_sample_done(self, row_idx: int, btn: QPushButton, path: str, err: str):
        btn.setEnabled(True)
        btn.setText("▶")
        if err:
            QMessageBox.warning(self, "Preview failed", f"Couldn't generate a preview:\n{err}")
            return
        self._extract_preview_cache[row_idx] = path
        self._show_extract_preview_dialog(path)

    def _show_extract_preview_dialog(self, sample_path: str):
        dlg = _ClipPreviewDialog(sample_path, "Clip preview", self)
        dlg.finished.connect(lambda _, d=dlg: d in self._extract_preview_dialogs and self._extract_preview_dialogs.remove(d))
        self._extract_preview_dialogs.append(dlg)
        dlg.show()

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

    def _create_extract_out_dir(self):
        master = Path(self._extract_master_path) if self._extract_master_path else None
        suggested_name = f"{master.stem} - recovered clips" if master else "Recovered clips"
        suggested_parent = str(master.parent) if master else (self._ex_out_dir.text() or str(Path.home()))
        dlg = _CreateFolderDialog(suggested_name, suggested_parent, self)
        if not dlg.exec():
            return
        full_path = dlg.full_path()
        if full_path is None:
            return
        try:
            full_path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            QMessageBox.warning(self, "Couldn't create folder", str(e))
            return
        self._ex_out_dir.setText(str(full_path))
        self._settings.set("last_extract_output_dir", str(full_path))

    def _start_extract(self):
        # _apply_extract_mode is the single source of truth for which mode is
        # active: it sets _extract_generic_plans to None for manifest-driven
        # recovery, or a (possibly empty) list otherwise — including when a
        # manifest exists but "ignore manifest" is checked. Re-deriving that
        # decision here from the manifest/checkbox directly would drift from
        # what the tree is actually showing.
        is_generic_mode = self._extract_generic_plans is not None
        has_manifest = not is_generic_mode and self._extract_manifest is not None and self._extract_manifest.clips
        has_generic = is_generic_mode and bool(self._extract_generic_plans)
        if (not has_manifest and not has_generic) or not self._extract_master_path:
            return
        out_dir = self._ex_out_dir.text().strip()
        if not out_dir:
            QMessageBox.information(self, "Choose an output folder",
                                    "Pick a folder to save the recovered clips into.")
            return
        checked_idx = [idx for idx, item in self._extract_items.items()
                      if item.checkState(EX_COL_NAME) == Qt.CheckState.Checked]
        if not checked_idx:
            QMessageBox.information(self, "Nothing selected",
                                    "Tick at least one clip to extract.")
            return

        self._ex_progress_frame.show()
        self._ex_pbar.setValue(0)
        self._ex_extract_btn.hide()
        self._ex_cancel_btn.show()

        ff, _ = get_ffmpeg()
        container = self._ex_format_combo.currentData()
        if has_manifest:
            selected = [self._extract_manifest.clips[idx] for idx in checked_idx]
            w = ExtractWorker(ff, self._extract_master_path, self._extract_manifest,
                              selected, Path(out_dir), container=container)
        else:
            selected = [self._extract_generic_plans[idx] for idx in checked_idx]
            w = GenericExtractWorker(ff, self._extract_master_path, selected, Path(out_dir),
                                     container=container)
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

        for dlg in list(self._extract_preview_dialogs):
            dlg.close()
        for thread in list(self._extract_preview_threads):
            settle(thread)

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
