"""merge_tab.py — Merge clips tab UI and logic (v1.3)."""

from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QPixmap, QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QLineEdit, QFileDialog,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QAbstractItemView, QProgressBar, QFrame,
    QComboBox, QMessageBox, QCheckBox, QScrollArea,
)

from clip_model import ClipInfo, scan_folder, unpaired_wavs, check_dst_warning
from ffmpeg_runner import MergeWorker, get_ffmpeg
from thread_utils import settle
from probe import probe, probe_duration
from settings import Settings
from core.ffmpeg_cmd import OutputPlan, OutputTrack
import log_manager
import theme

CUSTOM_AUDIO_LABEL = "Custom…"

# status → (palette attribute name, label)
STATUS_COLORS = {
    "ok":        ("ok",        "Stream copy"),
    "transcode": ("warn",      "Will transcode"),
    "hdr":       ("danger",    "Review — HDR"),
    "error":     ("danger",    "Probe error"),
    "unknown":   ("text_mute", "…"),
}

COL_ORDER  = 0
COL_NAME   = 1
COL_CAM    = 2
COL_DUR    = 3
COL_WAV    = 4
COL_OFFSET = 5
COL_DRIFT  = 6
COL_STATUS = 7
COL_UP     = 8
COL_DOWN   = 9
N_COLS     = 10


def _fmt_offset(clip) -> str:
    if not clip.has_wav():
        return "—"
    if not clip.sync_done:
        return "·"          # pending analysis
    return f"{clip.wav_offset*1000:+.0f} ms"


def _fmt_drift(clip) -> str:
    if not clip.has_wav():
        return "—"
    if not clip.sync_done:
        return "·"
    return f"{(clip.sync_drift_ratio-1)*60000:+.0f} ms/min"

# Primary (lossless) audio track. The combined mix is a separate opt-in track.
TRACK_OPTIONS = [
    ("Camera audio (Bluetooth mic)",    "camera"),
    ("WAV backup (on-board mic)",       "wav"),
]


def _fmt_dur(secs: float) -> str:
    if secs <= 0:
        return "—"
    h, r = divmod(int(secs), 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _make_status_badge(status: str, conflicts: list) -> QLabel:
    pal = theme.active_palette()
    attr, label = STATUS_COLORS.get(status, ("text_mute", status))
    color = getattr(pal, attr)
    text = label + ("  " + " · ".join(conflicts) if conflicts else "")
    lbl  = QLabel(text)
    lbl.setStyleSheet(
        f"background:{color}; color:{pal.bg}; border-radius:4px; padding:2px 6px; font-size:11px;"
    )
    lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
    return lbl


class ProbeThread(QThread):
    clip_probed = Signal(int, object)

    def __init__(self, clips: list, ffprobe_bin: str):
        super().__init__()
        self._clips   = clips
        self._ffprobe = ffprobe_bin
        self._stopped = False

    def stop(self):
        self._stopped = True

    def run(self):
        for i, clip in enumerate(self._clips):
            if self._stopped:
                return
            info = probe(self._ffprobe, str(clip.path))
            clip.stream = info
            if clip.has_wav() and clip.wav_duration <= 0:
                clip.wav_duration = probe_duration(self._ffprobe, str(clip.wav_path))
            self.clip_probed.emit(i, info)


class MergeTab(QWidget):
    merge_complete = Signal(str)

    def __init__(self, settings: Settings):
        super().__init__()
        self._settings      = settings
        self._clips: list[ClipInfo] = []
        self._worker: Optional[MergeWorker] = None
        self._probe_thread: Optional[ProbeThread] = None
        # Output track plan state
        self._custom_tracks = None     # list[OutputTrack] when a custom order is set
        self._include_video = True
        self._suppress_combo = False
        self._setup_ui()
        self._restyle()
        ctrl = theme.controller()
        if ctrl is not None:
            ctrl.changed.connect(self._restyle)

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(14, 14, 14, 14)
        self._sections: list[QFrame] = []
        self._section_titles: list[QLabel] = []

        # Empty state — shown until a folder with clips is loaded
        self._empty_state = self._build_empty_state()
        root.addWidget(self._empty_state)

        # Everything else lives in a scrollable content container, grouped into
        # sections — scrolling means the page never clips at high DPI or on short
        # screens (e.g. the Steam Deck's 800px height).
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        root.addWidget(self._scroll, 1)
        self._content = QWidget()
        self._scroll.setWidget(self._content)
        c = QVBoxLayout(self._content)
        c.setContentsMargins(0, 0, 8, 0)   # room for the scrollbar
        c.setSpacing(10)

        # ── SOURCE ──────────────────────────────────────────────────────────────
        src_frame, src_box = self._section("SOURCE")
        folder_row = QHBoxLayout()
        self._folder_edit = QLineEdit()
        self._folder_edit.setPlaceholderText("Select folder containing MP4 clips and WAV backups…")
        self._folder_edit.setReadOnly(True)
        folder_btn = QPushButton("Browse…")
        folder_btn.setFixedWidth(90)
        folder_btn.clicked.connect(self._browse_folder)
        folder_row.addWidget(self._folder_edit, 1)
        folder_row.addWidget(folder_btn)
        src_box.addLayout(folder_row)
        c.addWidget(src_frame)

        # ── Banners ───────────────────────────────────────────────────────────
        self._dst_banner = QLabel(
            "⚠  Daylight saving detected — consecutive clips are ~1 hour apart by filename. "
            "Verify the clip order below is correct."
        )
        self._dst_banner.setWordWrap(True)
        self._dst_banner.hide()
        c.addWidget(self._dst_banner)

        self._unmatched_banner = QLabel()
        self._unmatched_banner.setWordWrap(True)
        self._unmatched_banner.hide()
        c.addWidget(self._unmatched_banner)

        # ── Resolution mismatch panel ─────────────────────────────────────────
        self._res_banner = QWidget()
        self._res_banner.hide()
        res_layout = QVBoxLayout(self._res_banner)
        res_layout.setContentsMargins(10, 8, 10, 8)
        res_layout.setSpacing(6)
        self._res_label = QLabel()
        self._res_label.setWordWrap(True)
        res_layout.addWidget(self._res_label)
        res_btn_row = QHBoxLayout()
        res_btn_row.setSpacing(6)
        self._res_buttons: list[QPushButton] = []
        for label, key in [
            ("Downscale to baseline", "downscale"),
            ("Upscale all to largest", "upscale"),
            ("Separate file", "separate"),
            ("Drop minority clips", "drop"),
        ]:
            btn = QPushButton(label)
            btn.setProperty("res_key", key)
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, b=btn: self._on_res_btn(b))
            self._res_buttons.append(btn)
            res_btn_row.addWidget(btn)
        res_btn_row.addStretch()
        res_layout.addLayout(res_btn_row)
        c.addWidget(self._res_banner)

        # ── CLIPS ───────────────────────────────────────────────────────────────
        self._show_sync_check = QCheckBox("Show sync details")
        self._show_sync_check.setToolTip("Show the WAV Offset and Drift columns.")
        self._show_sync_check.toggled.connect(self._on_show_sync_toggled)
        clips_frame, clips_box = self._section("CLIPS", right=self._show_sync_check)
        self._clips_title = self._section_titles[-1]

        self._table = QTableWidget(0, N_COLS)
        self._table.setHorizontalHeaderLabels(
            ["#", "Clip", "Camera", "Duration", "WAV", "WAV Offset", "Drift", "Status", "↑", "↓"]
        )
        self._table.horizontalHeader().setSectionResizeMode(COL_NAME,   QHeaderView.ResizeMode.Stretch)
        self._table.horizontalHeader().setSectionResizeMode(COL_STATUS, QHeaderView.ResizeMode.Stretch)
        for col in (COL_ORDER, COL_DUR, COL_WAV, COL_OFFSET, COL_DRIFT, COL_UP, COL_DOWN):
            self._table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self._table.horizontalHeader().setSectionResizeMode(COL_CAM, QHeaderView.ResizeMode.Interactive)
        self._table.setColumnWidth(COL_CAM, 140)
        self._table.verticalHeader().setVisible(False)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setMinimumHeight(200)
        # Sync columns hidden by default (revealed via "Show sync details")
        self._table.setColumnHidden(COL_OFFSET, True)
        self._table.setColumnHidden(COL_DRIFT, True)
        clips_box.addWidget(self._table, 1)

        clips_sub = QHBoxLayout()
        self._square_label = QLabel("Square clips:")
        self._square_combo = QComboBox()
        self._square_combo.addItems(["Crop to fill 16:9", "Add black bars"])
        self._square_label.hide()
        self._square_combo.hide()
        reset_btn = QPushButton("Reset order")
        reset_btn.clicked.connect(self._reset_order)
        clips_sub.addWidget(self._square_label)
        clips_sub.addWidget(self._square_combo)
        clips_sub.addStretch()
        clips_sub.addWidget(reset_btn)
        clips_box.addLayout(clips_sub)
        c.addWidget(clips_frame, 1)

        # ── AUDIO OPTIONS (collapsible) ─────────────────────────────────────────
        c.addWidget(self._build_audio_section())

        # ── OUTPUT ──────────────────────────────────────────────────────────────
        out_frame, out_box = self._section("OUTPUT")
        out_grid = QGridLayout()
        out_grid.setColumnStretch(1, 1)
        self._out_name = QLineEdit()
        self._out_name.setPlaceholderText("output_master.mov")
        self._adv_out_btn = QPushButton("Advanced…")
        self._adv_out_btn.setFixedWidth(104)
        self._adv_out_btn.setToolTip("Choose which video / audio tracks the master file contains.")
        self._adv_out_btn.clicked.connect(self._open_output_advanced)
        out_grid.addWidget(QLabel("Output filename:"), 0, 0)
        out_grid.addWidget(self._out_name, 0, 1)
        out_grid.addWidget(self._adv_out_btn, 0, 2)
        self._out_dir = QLineEdit()
        self._out_dir.setPlaceholderText("Output folder…")
        self._out_dir.setReadOnly(True)
        out_dir_btn = QPushButton("Browse…")
        out_dir_btn.setFixedWidth(90)
        out_dir_btn.clicked.connect(self._browse_out_dir)
        out_grid.addWidget(QLabel("Output folder:"), 1, 0)
        out_grid.addWidget(self._out_dir, 1, 1)
        out_grid.addWidget(out_dir_btn, 1, 2)
        out_box.addLayout(out_grid)
        c.addWidget(out_frame)

        # ── Transcode estimate label ──────────────────────────────────────────
        self._estimate_label = QLabel()
        self._estimate_label.hide()
        c.addWidget(self._estimate_label)

        # ── Progress ──────────────────────────────────────────────────────────
        self._progress_frame = QFrame()
        self._progress_frame.setFrameShape(QFrame.Shape.StyledPanel)
        self._progress_frame.hide()
        prog_layout = QVBoxLayout(self._progress_frame)
        prog_layout.setSpacing(8)

        self._stage_row = QHBoxLayout()
        self._stage_labels: list[QLabel] = []
        prog_layout.addLayout(self._stage_row)

        pbar_row = QHBoxLayout()
        self._pbar = QProgressBar()
        self._pbar.setRange(0, 100)
        self._stats_label = QLabel("—")
        self._stats_label.setFixedWidth(420)
        pbar_row.addWidget(self._pbar, 1)
        pbar_row.addWidget(self._stats_label)
        prog_layout.addLayout(pbar_row)

        thumb_row = QHBoxLayout()
        self._thumb_label = QLabel("Rendering…")
        self._thumb_label.setFixedSize(240, 135)
        self._thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        thumb_row.addWidget(self._thumb_label)
        thumb_row.addStretch()
        prog_layout.addLayout(thumb_row)

        c.addWidget(self._progress_frame)

        # ── Action bar ──────────────────────────────────────────────────────────
        btn_row = QHBoxLayout()

        self._preview_check = QCheckBox("Live preview")
        self._preview_check.setChecked(True)
        self._preview_check.setToolTip("Show a frame-by-frame preview while rendering.\n"
                                       "Disable to prevent terminal windows obscuring the UI.")
        self._preview_check.stateChanged.connect(
            lambda: self._thumb_label.setVisible(self._preview_check.isChecked())
        )
        btn_row.addWidget(self._preview_check)
        btn_row.addStretch()

        self._preflight_btn = QPushButton("Pre-flight…")
        self._preflight_btn.setFixedHeight(36)
        self._preflight_btn.setEnabled(False)
        self._preflight_btn.setToolTip("Preview exactly what work will be done before merging.")
        self._preflight_btn.clicked.connect(self._open_preflight)

        self._start_btn = QPushButton("▶  Start merge")
        self._start_btn.setEnabled(False)
        self._start_btn.setFixedHeight(36)
        self._start_btn.clicked.connect(self._start_merge)
        self._style_start_btn()

        self._cancel_btn = QPushButton("Cancel")
        self._cancel_btn.setFixedHeight(36)
        self._cancel_btn.hide()
        self._cancel_btn.clicked.connect(self._cancel_merge)

        btn_row.addWidget(self._cancel_btn)
        btn_row.addWidget(self._preflight_btn)
        btn_row.addWidget(self._start_btn)
        c.addLayout(btn_row)

        # Restore settings
        self._out_dir.setText(self._settings.get("last_merge_output_dir", ""))
        self._out_name.setText(self._settings.get("last_merge_output_name", ""))
        saved_order = self._settings.get("last_merge_track_order", "camera")
        for i, (_, key) in enumerate(TRACK_OPTIONS):
            if key == saved_order:
                self._track_combo.setCurrentIndex(i)
                break

        self._set_loaded(False)        # start on the empty state
        self._update_audio_summary()

    # ── Layout helpers ────────────────────────────────────────────────────────

    def _section(self, title: str, right: Optional[QWidget] = None):
        """A titled, bordered group card. Returns (frame, content_layout)."""
        frame = QFrame()
        frame.setObjectName("section")
        v = QVBoxLayout(frame)
        v.setContentsMargins(12, 9, 12, 11)
        v.setSpacing(8)
        hdr = QHBoxLayout()
        lbl = QLabel(title)
        lbl.setObjectName("sectionTitle")
        hdr.addWidget(lbl)
        if right is not None:
            hdr.addStretch()
            hdr.addWidget(right)
        v.addLayout(hdr)
        body = QVBoxLayout()
        body.setSpacing(7)
        v.addLayout(body)
        self._sections.append(frame)
        self._section_titles.append(lbl)
        return frame, body

    def _build_empty_state(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.setSpacing(12)
        v.addStretch()
        self._empty_icon = QLabel()
        icon_path = Path(__file__).parent / "assets" / "lunavault.png"
        if icon_path.exists():
            self._empty_icon.setPixmap(QPixmap(str(icon_path)).scaled(
                72, 72, Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))
        self._empty_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self._empty_icon)
        self._empty_title = QLabel("Select a folder of clips to begin")
        self._empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self._empty_title)
        self._empty_sub = QLabel(
            "FuseBox pairs each video with its WAV backup, orders them by time, "
            "and merges them into one lossless master.")
        self._empty_sub.setWordWrap(True)
        self._empty_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._empty_sub.setMaximumWidth(420)
        v.addWidget(self._empty_sub, 0, Qt.AlignmentFlag.AlignCenter)
        self._empty_btn = QPushButton("Choose source folder…")
        self._empty_btn.setFixedHeight(38)
        self._empty_btn.clicked.connect(self._browse_folder)
        v.addWidget(self._empty_btn, 0, Qt.AlignmentFlag.AlignCenter)
        v.addStretch()
        return w

    def _build_audio_section(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("section")
        self._sections.append(frame)
        v = QVBoxLayout(frame)
        v.setContentsMargins(12, 9, 12, 11)
        v.setSpacing(8)

        header = QWidget()
        header.setCursor(Qt.CursorShape.PointingHandCursor)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(0, 0, 0, 0)
        self._audio_title = QLabel("▸  AUDIO OPTIONS")
        self._audio_title.setObjectName("sectionTitle")
        self._section_titles.append(self._audio_title)
        self._audio_summary = QLabel("")
        hl.addWidget(self._audio_title)
        hl.addStretch()
        hl.addWidget(self._audio_summary)
        header.mousePressEvent = lambda e: self._toggle_audio_section()
        v.addWidget(header)

        self._audio_body = QWidget()
        bl = QVBoxLayout(self._audio_body)
        bl.setContentsMargins(0, 4, 0, 0)
        bl.setSpacing(8)

        prow = QHBoxLayout()
        prow.addWidget(QLabel("Primary audio:"))
        self._track_combo = QComboBox()
        for label, _ in TRACK_OPTIONS:
            self._track_combo.addItem(label)
        self._track_combo.addItem(CUSTOM_AUDIO_LABEL)
        self._track_combo.setToolTip(
            "The default audio track in the master file. Both lossless mics are\n"
            "always kept; this only sets which one plays by default.")
        self._track_combo.currentIndexChanged.connect(self._on_track_changed)
        self._track_combo.currentIndexChanged.connect(lambda _: self._update_audio_summary())
        self._mixed_note = QLabel()
        self._mixed_note.hide()
        self._mixed_warn = QLabel()
        self._mixed_warn.hide()
        prow.addWidget(self._track_combo)
        prow.addWidget(self._mixed_note)
        prow.addStretch()
        bl.addLayout(prow)

        bl.addWidget(self._build_mix_panel())
        self._mix_check.toggled.connect(lambda _: self._update_audio_summary())

        v.addWidget(self._audio_body)
        self._audio_body.setVisible(False)
        self._audio_collapsed = True
        return frame

    def _toggle_audio_section(self):
        self._audio_collapsed = not self._audio_collapsed
        self._audio_body.setVisible(not self._audio_collapsed)
        self._update_audio_summary()

    def _update_audio_summary(self):
        chevron = "▸" if getattr(self, "_audio_collapsed", True) else "▾"
        self._audio_title.setText(f"{chevron}  AUDIO OPTIONS")
        if getattr(self, "_audio_collapsed", True):
            primary = self._track_combo.currentText().strip()
            mix = "mix on" if self._mix_check.isChecked() else "mix off"
            self._audio_summary.setText(f"Primary: {primary}  ·  {mix}")
        else:
            self._audio_summary.setText("")

    def _on_show_sync_toggled(self, on: bool):
        self._table.setColumnHidden(COL_OFFSET, not on)
        self._table.setColumnHidden(COL_DRIFT, not on)

    def _set_loaded(self, loaded: bool):
        self._scroll.setVisible(loaded)
        self._empty_state.setVisible(not loaded)

    # ── Combined mix panel ──────────────────────────────────────────────────────

    def _build_mix_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("mixPanel")
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(6)

        top = QHBoxLayout()
        self._mix_check = QCheckBox("Add combined mix track")
        self._mix_check.setToolTip("Add one extra track that carries both mics together.\n"
                                   "The lossless camera and WAV tracks are always kept.")
        self._mix_check.toggled.connect(self._on_mix_toggled)
        top.addWidget(self._mix_check)
        self._mix_cap = QLabel("— blends both mics into one extra track")
        top.addWidget(self._mix_cap)
        top.addStretch()
        lay.addLayout(top)

        self._mix_subrow = QWidget()
        sub = QVBoxLayout(self._mix_subrow)
        sub.setContentsMargins(22, 0, 0, 0)
        sub.setSpacing(6)

        kind_row = QHBoxLayout()
        self._mix_kind_combo = QComboBox()
        self._mix_kind_combo.addItem("L/R split (camera left · WAV right)", "lr")
        self._mix_kind_combo.addItem("50/50 blend (both summed)", "5050")
        self._mix_kind_combo.setToolTip(
            "L/R split keeps the mics on separate channels — no echo on headphones.\n"
            "50/50 blend sums them; plays anywhere but two open mics can sound hollow.")
        self._mix_kind_combo.currentIndexChanged.connect(self._on_mix_kind_changed)
        kind_row.addWidget(self._mix_kind_combo)
        self._mix_kind_cap = QLabel()
        kind_row.addWidget(self._mix_kind_cap)
        kind_row.addStretch()
        sub.addLayout(kind_row)

        checks_row = QHBoxLayout()
        self._mix_levels_check = QCheckBox("Match channel levels")
        self._mix_levels_check.setToolTip("Balance loudness so one mic isn't far louder than the other.")
        self._mix_default_check = QCheckBox("Make this the default track")
        self._mix_default_check.setToolTip("Otherwise the mix is added after the lossless mics.")
        checks_row.addWidget(self._mix_levels_check)
        checks_row.addSpacing(16)
        checks_row.addWidget(self._mix_default_check)
        checks_row.addStretch()
        sub.addLayout(checks_row)

        btn_row = QHBoxLayout()
        self._adv_sync_btn = QPushButton("Advanced sync…")
        self._adv_sync_btn.setToolTip("Analyse the selected clip's mic alignment and drift.")
        self._adv_sync_btn.clicked.connect(self._open_advanced_sync)
        self._batch_sync_btn = QPushButton("Analyse all clips")
        self._batch_sync_btn.setToolTip("Run sync analysis on every clip with a WAV and fill the\n"
                                        "WAV Offset and Drift columns automatically.")
        self._batch_sync_btn.clicked.connect(self._batch_sync)
        self._play_sample_btn = QPushButton("▶  Play 10s sample")
        self._play_sample_btn.setToolTip("Render and audition a 10-second sample of the mix for the selected clip.")
        self._play_sample_btn.clicked.connect(self._play_mix_sample)
        btn_row.addWidget(self._adv_sync_btn)
        btn_row.addWidget(self._batch_sync_btn)
        btn_row.addWidget(self._play_sample_btn)
        btn_row.addStretch()
        sub.addLayout(btn_row)

        lay.addWidget(self._mix_subrow)
        self._mix_subrow.setVisible(False)

        panel.setStyleSheet(
            "QFrame#mixPanel { background:transparent; border:1px solid palette(mid); border-radius:8px; }"
        )
        self._update_mix_captions()
        return panel

    def _on_mix_toggled(self, on: bool):
        self._mix_subrow.setVisible(on)

    def _on_mix_kind_changed(self, _idx: int):
        self._update_mix_captions()

    def _mix_kind(self) -> str:
        return self._mix_kind_combo.currentData()

    def _update_mix_captions(self):
        p = theme.active_palette()
        self._mix_cap.setStyleSheet(f"color:{p.text_dim}; font-size:11px;")
        self._mix_kind_cap.setStyleSheet(f"color:{p.text_dim}; font-size:11px;")
        if self._mix_kind() == "lr":
            self._mix_kind_cap.setText("L: camera (BT) · R: WAV — no echo on headphones")
        else:
            self._mix_kind_cap.setText("summed to mono — plays anywhere; may sound hollow")

    def _selected_clip(self) -> Optional[ClipInfo]:
        row = self._table.currentRow()
        if row < 0:
            return None
        item = self._table.item(row, COL_NAME)
        if not item:
            return None
        idx = item.data(Qt.ItemDataRole.UserRole)
        if idx is None or idx >= len(self._clips):
            return None
        return self._clips[idx]

    def _open_advanced_sync(self):
        clip = self._selected_clip()
        if clip is None:
            QMessageBox.information(self, "Select a clip",
                                    "Select a clip row first to analyse its audio sync.")
            return
        if not clip.has_wav():
            QMessageBox.information(self, "No WAV backup",
                                    "This clip has no paired WAV to sync.")
            return
        from audio_sync_dialog import AdvancedSyncDialog
        dlg = AdvancedSyncDialog(clip, self)
        dlg.exec()
        self._refresh_sync_cells()

    def _batch_sync(self):
        if not self._clips:
            QMessageBox.information(self, "No clips", "Load a source folder first.")
            return
        from audio_sync_dialog import BatchSyncDialog
        dlg = BatchSyncDialog(self._clips, self)
        dlg.clip_analyzed.connect(self._refresh_sync_cells)
        dlg.exec()
        self._refresh_sync_cells()

    def _play_mix_sample(self):
        clip = self._selected_clip()
        if clip is None or not clip.has_wav():
            QMessageBox.information(self, "Select a clip with WAV",
                                    "Select a clip that has a paired WAV backup.")
            return
        from audio_sample_player import play_mix_sample
        play_mix_sample(clip, self._mix_kind(), self._mix_levels_check.isChecked(), self)

    # ── Theming ─────────────────────────────────────────────────────────────────

    def _style_start_btn(self):
        p = theme.active_palette()
        self._start_btn.setStyleSheet(
            f"QPushButton {{ background:{p.accent}; color:{p.on_accent()}; border-radius:6px; "
            "font-weight:bold; padding:0 18px; }"
            f"QPushButton:disabled {{ background:{p.btn_bg}; color:{p.text_dim}; }}"
        )

    def _restyle(self):
        p = theme.active_palette()
        self._style_start_btn()
        self._update_mix_captions()
        # Section cards + titles
        for f in getattr(self, "_sections", []):
            f.setStyleSheet(
                f"QFrame#section {{ background:{p.surface}; border:1px solid {p.border_dk}; "
                "border-radius:8px; }")
        for t in getattr(self, "_section_titles", []):
            t.setStyleSheet(f"color:{p.accent}; font-size:10px; font-weight:bold; letter-spacing:1px;")
        if hasattr(self, "_audio_summary"):
            self._audio_summary.setStyleSheet(f"color:{p.text_dim}; font-size:11px;")
        # Empty state
        if hasattr(self, "_empty_title"):
            self._empty_title.setStyleSheet(f"color:{p.text}; font-size:16px; font-weight:500;")
            self._empty_sub.setStyleSheet(f"color:{p.text_mute}; font-size:12px;")
            self._empty_btn.setStyleSheet(
                f"QPushButton {{ background:{p.accent}; color:{p.on_accent()}; border-radius:6px; "
                "font-weight:bold; padding:0 22px; }"
                f"QPushButton:hover {{ background:{p.accent_hi}; }}")
        # Banners
        self._dst_banner.setStyleSheet(
            f"background:{p.banner_warn_bg}; color:{p.text}; border-radius:4px; padding:6px 10px;")
        self._unmatched_banner.setStyleSheet(
            f"background:{p.banner_info_bg}; color:{p.text}; border-radius:4px; padding:6px 10px;")
        self._res_label.setStyleSheet(f"color:{p.text}; font-size:12px;")
        for btn in getattr(self, "_res_buttons", []):
            btn.setStyleSheet(
                "QPushButton { font-size:11px; padding:4px 10px; border-radius:4px; "
                f"border:1px solid {p.border}; color:{p.text_dim}; background:{p.input_dk}; }}"
                f"QPushButton:checked {{ border-color:{p.accent}; color:{p.accent}; background:{p.surface2}; }}")
        self._res_banner.setStyleSheet(
            f"background:{p.surface2}; border:1px solid {p.border}; border-radius:8px;")
        self._estimate_label.setStyleSheet(f"color:{p.text_dim}; font-size:11px;")
        self._thumb_label.setStyleSheet(
            f"background:{p.input_dk}; border-radius:4px; color:{p.text_mute};")
        # Status badges + row colours are cell widgets — rebuild the table to recolour them.
        if self._clips:
            self._populate_table()

    # ── Track combo ───────────────────────────────────────────────────────────

    def _on_track_changed(self, idx: int):
        if self._suppress_combo:
            return
        if self._track_combo.itemText(idx) == CUSTOM_AUDIO_LABEL:
            # Opening the custom arrangement; revert if the user cancels.
            if not self._open_audio_arrange():
                self._suppress_combo = True
                self._track_combo.setCurrentIndex(0)
                self._suppress_combo = False
        else:
            self._custom_tracks = None   # back to a simple preset

    def _current_track_order(self) -> str:
        idx = self._track_combo.currentIndex()
        if 0 <= idx < len(TRACK_OPTIONS):
            return TRACK_OPTIONS[idx][1]
        return "camera"

    # ── Output track plan ───────────────────────────────────────────────────────

    def _effective_plan(self) -> OutputPlan:
        """Current OutputPlan from either the custom arrangement or the presets."""
        if self._custom_tracks is not None:
            tracks = [OutputTrack(t.kind, t.enabled) for t in self._custom_tracks]
        else:
            base = OutputPlan.preset(self._current_track_order(),
                                     self._mix_check.isChecked(), self._mix_kind(),
                                     self._mix_default_check.isChecked(),
                                     self._mix_levels_check.isChecked())
            tracks = base.tracks
        return OutputPlan(include_video=self._include_video, tracks=tracks,
                          mix_kind=self._mix_kind(),
                          mix_match_levels=self._mix_levels_check.isChecked())

    def _representative_clip(self) -> Optional[ClipInfo]:
        return self._selected_clip() or (self._clips[0] if self._clips else None)

    def _mark_custom(self):
        self._suppress_combo = True
        self._track_combo.setCurrentIndex(self._track_combo.count() - 1)  # "Custom…"
        self._suppress_combo = False

    def _open_audio_arrange(self) -> bool:
        clip = self._representative_clip()
        plan = self._effective_plan()
        from audio_track_dialogs import AudioArrangeDialog
        dlg = AudioArrangeDialog(plan, clip, self)
        if dlg.exec():
            self._custom_tracks = plan.tracks
            self._sync_mix_check_from_custom()
            return True
        return False

    def _sync_mix_check_from_custom(self):
        if self._custom_tracks is None:
            return
        has_mix = any(t.kind == "mix" and t.enabled for t in self._custom_tracks)
        self._mix_check.setChecked(has_mix)

    def _open_output_advanced(self):
        clip = self._representative_clip()
        plan = self._effective_plan()
        from audio_track_dialogs import OutputAdvancedDialog
        dlg = OutputAdvancedDialog(plan, clip, self)
        if dlg.exec():
            self._include_video = plan.include_video
            self._custom_tracks = plan.tracks
            self._sync_mix_check_from_custom()
            self._mark_custom()

    def _update_primary_labels(self):
        """Camera audio is the on-board mic when no clips have a WAV backup."""
        any_wav = any(c.has_wav() for c in self._clips)
        cam_label = ("Camera audio (Bluetooth mic)" if any_wav
                     else "Camera audio (on-board mic)")
        self._suppress_combo = True
        self._track_combo.setItemText(0, cam_label)
        self._suppress_combo = False

    # ── Resolution mismatch ───────────────────────────────────────────────────

    def _on_res_btn(self, clicked_btn: QPushButton):
        for b in self._res_buttons:
            b.setChecked(b is clicked_btn)

    def _res_mode(self) -> str:
        for b in self._res_buttons:
            if b.isChecked():
                return b.property("res_key")
        return "downscale"

    def _check_resolution_mismatch(self):
        resolutions = set()
        for clip in self._clips:
            if clip.stream and clip.stream.width and clip.stream.height:
                resolutions.add((clip.stream.width, clip.stream.height))
        if len(resolutions) > 1:
            res_list = ", ".join(f"{w}×{h}" for w, h in sorted(resolutions, reverse=True))
            self._res_label.setText(
                f"Resolution mismatch detected — clips contain: {res_list}. "
                "How would you like to handle minority-resolution clips?"
            )
            self._res_banner.show()
            if not any(b.isChecked() for b in self._res_buttons):
                self._res_buttons[0].setChecked(True)
        else:
            self._res_banner.hide()

    # ── Transcode estimate ────────────────────────────────────────────────────

    def _update_estimate(self):
        """Show a rough best/worst-case transcode time estimate."""
        if not self._clips:
            self._estimate_label.hide()
            return
        total_secs = sum(c.duration for c in self._clips if c.duration > 0)
        if total_secs <= 0:
            self._estimate_label.hide()
            return
        # Rough: GPU ~4× realtime, CPU ~0.5× realtime
        best_min  = max(1, int(total_secs / 4 / 60))
        worst_min = max(1, int(total_secs / 0.5 / 60))
        self._estimate_label.setText(
            f"Estimated transcode time:  "
            f"Best ~{best_min} min (GPU)  ·  Worst ~{worst_min} min (CPU only)"
        )
        self._estimate_label.show()

    # ── Folder scanning ───────────────────────────────────────────────────────

    def _browse_folder(self):
        start = self._settings.get("last_merge_source", "") or str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "Select source folder", start)
        if not folder:
            return
        self._settings.set("last_merge_source", folder)
        self._folder_edit.setText(folder)
        self._load_folder(Path(folder))

    def _load_folder(self, folder: Path):
        self._clips = scan_folder(folder)
        if not self._clips:
            self._table.setRowCount(0)
            self._start_btn.setEnabled(False)
            self._preflight_btn.setEnabled(False)
            self._set_loaded(False)
            QMessageBox.information(
                self, "No clips found",
                "No MP4 clips were found in that folder. Choose a folder that "
                "contains your camera clips (and their WAV backups).")
            return

        self._set_loaded(True)
        self._clips_title.setText(f"CLIPS  ·  {len(self._clips)} found")

        orphans = unpaired_wavs(folder, self._clips)
        if orphans:
            names = ", ".join(w.name for w in orphans[:4])
            extra = f" (+{len(orphans)-4} more)" if len(orphans) > 4 else ""
            self._unmatched_banner.setText(f"ℹ  Unmatched WAV files (not used): {names}{extra}")
            self._unmatched_banner.show()
        else:
            self._unmatched_banner.hide()

        self._dst_banner.setVisible(check_dst_warning(self._clips))
        self._update_primary_labels()
        self._populate_table()
        self._start_probe()

    def _start_probe(self):
        if self._probe_thread and self._probe_thread.isRunning():
            return
        settle(self._probe_thread)
        _, fp = get_ffmpeg()
        self._probe_thread = ProbeThread(self._clips, fp)
        self._probe_thread.clip_probed.connect(self._on_clip_probed)
        self._probe_thread.finished.connect(self._on_probe_done)
        self._probe_thread.start()

    def _on_clip_probed(self, idx: int, info):
        if idx >= len(self._clips):
            return
        clip = self._clips[idx]
        for row in range(self._table.rowCount()):
            item = self._table.item(row, COL_NAME)
            if item and item.data(Qt.ItemDataRole.UserRole) == idx:
                self._update_row(row, clip)
                break

    def _on_probe_done(self):
        self._start_btn.setEnabled(bool(self._clips))
        self._preflight_btn.setEnabled(bool(self._clips))
        has_square = any(
            c.stream and c.stream.width == c.stream.height for c in self._clips
        )
        self._square_label.setVisible(has_square)
        self._square_combo.setVisible(has_square)
        self._check_resolution_mismatch()
        self._update_estimate()
        self._refresh_sync_cells()   # slow-mo offset/drift hints now that WAV durs are known

    def _open_preflight(self):
        if not self._clips:
            return
        import shutil
        from core.plan_report import analyze_merge
        from preflight_dialog import PreflightDialog
        report = analyze_merge(self._clips, self._effective_plan())
        free = None
        out_dir = self._out_dir.text().strip()
        if out_dir:
            try:
                free = shutil.disk_usage(out_dir).free
            except Exception:
                free = None
        dlg = PreflightDialog(report, self, free_bytes=free,
                              need_bytes=self._estimated_need_bytes())
        dlg.start_requested.connect(self._start_merge)
        dlg.exec()

    # ── Table ─────────────────────────────────────────────────────────────────

    def _populate_table(self):
        self._table.setRowCount(0)
        for clip in sorted(self._clips, key=lambda c: c.order_idx):
            self._add_row(clip, self._table.rowCount())

    def _add_row(self, clip: ClipInfo, row: int):
        self._table.insertRow(row)

        p = theme.active_palette()
        order_item = QTableWidgetItem(str(clip.order_idx + 1))
        order_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        if clip.manually_moved:
            order_item.setForeground(QColor(p.accent))
            order_item.setFont(QFont("", -1, QFont.Weight.Bold))
        self._table.setItem(row, COL_ORDER, order_item)

        name_item = QTableWidgetItem(clip.stem)
        name_item.setData(Qt.ItemDataRole.UserRole, self._clips.index(clip))
        self._table.setItem(row, COL_NAME, name_item)

        cam = f"{clip.stream.width}×{clip.stream.height} · {clip.stream.codec}" if clip.stream else ""
        self._table.setItem(row, COL_CAM, QTableWidgetItem(cam))

        dur_item = QTableWidgetItem(_fmt_dur(clip.duration))
        dur_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.setItem(row, COL_DUR, dur_item)

        wav_item = QTableWidgetItem("✓" if clip.has_wav() else "—")
        wav_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        if not clip.has_wav():
            wav_item.setForeground(QColor(p.text_dim))
        self._table.setItem(row, COL_WAV, wav_item)

        off_item = QTableWidgetItem(_fmt_offset(clip))
        off_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.setItem(row, COL_OFFSET, off_item)

        drift_item = QTableWidgetItem(_fmt_drift(clip))
        drift_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        self._table.setItem(row, COL_DRIFT, drift_item)

        self._update_status_cell(row, clip)

        icon_style = (
            f"QPushButton {{ background:{p.btn_bg}; color:{p.text}; border:1px solid {p.border}; "
            "border-radius:4px; padding:0px; font-size:14px; }"
            f"QPushButton:hover {{ border-color:{p.accent}; color:{p.accent}; }}")
        for col, delta in ((COL_UP, -1), (COL_DOWN, +1)):
            sym = "↑" if delta == -1 else "↓"
            btn = QPushButton(sym)
            btn.setFixedSize(28, 28)
            btn.setStyleSheet(icon_style)            # padding:0 so the arrow isn't clipped
            btn.clicked.connect(lambda _, r=row, d=delta: self._move_row(r, d))
            self._table.setCellWidget(row, col, btn)

    def _update_row(self, row: int, clip: ClipInfo):
        if clip.stream:
            cam = f"{clip.stream.width}×{clip.stream.height} · {clip.stream.codec}"
            self._table.item(row, COL_CAM).setText(cam)
            dur = self._table.item(row, COL_DUR)
            if dur:
                dur.setText(_fmt_dur(clip.duration))
        off = self._table.item(row, COL_OFFSET)
        if off:
            off.setText(_fmt_offset(clip))
        drift = self._table.item(row, COL_DRIFT)
        if drift:
            drift.setText(_fmt_drift(clip))
        self._update_status_cell(row, clip)

    def _refresh_sync_cells(self):
        """Update the WAV Offset / Drift columns for all rows after analysis."""
        for row in range(self._table.rowCount()):
            item = self._table.item(row, COL_NAME)
            if not item:
                continue
            idx = item.data(Qt.ItemDataRole.UserRole)
            if idx is None or idx >= len(self._clips):
                continue
            clip = self._clips[idx]
            off = self._table.item(row, COL_OFFSET)
            if off:
                off.setText(_fmt_offset(clip))
            drift = self._table.item(row, COL_DRIFT)
            if drift:
                drift.setText(_fmt_drift(clip))

    def _update_status_cell(self, row: int, clip: ClipInfo):
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(4, 2, 4, 2)
        lay.addWidget(_make_status_badge(clip.status, clip.conflicts))
        self._table.setCellWidget(row, COL_STATUS, w)

    def _move_row(self, row: int, delta: int):
        new_row = row + delta
        if new_row < 0 or new_row >= self._table.rowCount():
            return
        item_a = self._table.item(row,     COL_NAME)
        item_b = self._table.item(new_row, COL_NAME)
        if not item_a or not item_b:
            return
        clip_a = self._clips[item_a.data(Qt.ItemDataRole.UserRole)]
        clip_b = self._clips[item_b.data(Qt.ItemDataRole.UserRole)]
        clip_a.order_idx, clip_b.order_idx = clip_b.order_idx, clip_a.order_idx
        clip_a.manually_moved = True
        clip_b.manually_moved = True
        self._populate_table()
        self._table.selectRow(new_row)
        self._dst_banner.setVisible(check_dst_warning(self._clips))

    def _reset_order(self):
        for clip in self._clips:
            clip.manually_moved = False
        self._clips.sort(key=lambda c: (
            c.filename_ts if c.filename_ts is not None else 99999999, c.name
        ))
        for i, c in enumerate(self._clips):
            c.order_idx = i
        self._populate_table()
        self._dst_banner.setVisible(check_dst_warning(self._clips))

    # ── Output ────────────────────────────────────────────────────────────────

    def _browse_out_dir(self):
        start = self._out_dir.text() or str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "Select output folder", start)
        if folder:
            self._out_dir.setText(folder)
            self._settings.set("last_merge_output_dir", folder)

    # ── Merge ─────────────────────────────────────────────────────────────────

    def _estimated_need_bytes(self) -> int:
        """Peak space needed on the output drive: temp clips + final ≈ 2× output."""
        try:
            from core.plan_report import analyze_merge
            return int(analyze_merge(self._clips, self._effective_plan()).total_bytes * 2.2)
        except Exception:
            return 0

    def _check_disk_space(self, out_dir: str) -> bool:
        import shutil
        try:
            free = shutil.disk_usage(out_dir).free
        except Exception:
            return True
        need = self._estimated_need_bytes()
        if need and free < need:
            reply = QMessageBox.warning(
                self, "Low disk space",
                f"This merge needs roughly {need/1024**3:.1f} GB free on the output drive "
                f"(temporary files + final master), but only {free/1024**3:.1f} GB is available.\n\n"
                "Continue anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            return reply == QMessageBox.StandardButton.Yes
        return True

    def _start_merge(self):
        if not self._clips:
            return

        out_name = self._out_name.text().strip() or "output_master.mov"
        if not out_name.lower().endswith(".mov"):
            out_name += ".mov"
        out_dir = self._out_dir.text().strip()
        if not out_dir:
            QMessageBox.warning(self, "No output folder", "Please select an output folder.")
            return

        track_order = self._current_track_order()
        square_mode = "crop" if self._square_combo.currentIndex() == 0 else "pad"
        output      = Path(out_dir) / out_name

        # Confirm before overwriting an existing master.
        if output.exists():
            reply = QMessageBox.question(
                self, "Overwrite file?",
                f"{out_name} already exists in this folder.\n\nReplace it?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        # Warn if the output drive looks short on space (temp + final ≈ 2× output).
        if not self._check_disk_space(out_dir):
            return

        self._settings.set("last_merge_output_dir", out_dir)
        self._settings.set("last_merge_output_name", out_name)
        self._settings.set("last_merge_track_order", track_order)

        # Build stage pills
        for i in reversed(range(self._stage_row.count())):
            w = self._stage_row.itemAt(i).widget()
            if w:
                w.deleteLater()
        self._stage_labels.clear()

        p = theme.active_palette()
        pill_idle = (f"background:{p.btn_bg}; color:{p.text}; border-radius:4px; "
                     "padding:3px 7px; font-size:11px;")
        for clip in sorted(self._clips, key=lambda c: c.order_idx):
            lbl = QLabel(clip.stem[:20])
            lbl.setStyleSheet(pill_idle)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._stage_labels.append(lbl)
            self._stage_row.addWidget(lbl)
        merge_lbl = QLabel("Merge")
        merge_lbl.setStyleSheet(pill_idle)
        merge_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._stage_labels.append(merge_lbl)
        self._stage_row.addWidget(merge_lbl)
        self._stage_row.addStretch()

        self._progress_frame.show()
        self._pbar.setValue(0)
        self._start_btn.hide()
        self._cancel_btn.show()

        self._worker = MergeWorker(
            clips          = self._clips,
            output_path    = output,
            plan           = self._effective_plan(),
            square_mode    = square_mode,
            title          = output.stem,
            enable_preview = self._preview_check.isChecked(),
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.thumbnail.connect(self._on_thumbnail)
        self._worker.finished.connect(self._on_finished)
        self._worker.start()

    def _cancel_merge(self):
        if self._worker:
            self._worker.cancel()

    def _on_progress(self, data: dict):
        idx  = data.get("stage_idx", 1) - 1
        pct  = data.get("pct", 0)
        size = data.get("size", 0)
        self._pbar.setValue(int(pct))
        p = theme.active_palette()
        for i, lbl in enumerate(self._stage_labels):
            if i < idx:
                col = p.ok
            elif i == idx:
                col = p.accent
            else:
                col = p.btn_bg
            lbl.setStyleSheet(f"background:{col}; color:{p.text}; border-radius:4px; "
                              f"padding:3px 7px; font-size:11px;")
        rate    = data.get("rate_bps", 0) or 0
        eta     = data.get("eta_secs", 0) or 0
        elapsed = data.get("elapsed_secs", 0) or 0

        def _mmss(secs):
            m, s = divmod(int(secs), 60)
            return f"{m}:{s:02d}"

        parts = [f"{size/1024**3:.2f} GB", f"{pct:.0f}%"]
        if rate > 0:
            parts.append(f"{rate/1024/1024:.0f} MB/s")
        parts.append(f"Elapsed {_mmss(elapsed)}")
        if eta > 1:
            parts.append(f"ETA {_mmss(eta)}")
        self._stats_label.setText("  ·  ".join(parts))

    def _on_thumbnail(self, path: str):
        px = QPixmap(path)
        if not px.isNull():
            self._thumb_label.setPixmap(
                px.scaled(240, 135, Qt.AspectRatioMode.KeepAspectRatio,
                          Qt.TransformationMode.SmoothTransformation)
            )

    def _on_finished(self, success: bool, message: str):
        self._cancel_btn.hide()
        self._start_btn.show()
        # The worker emits `finished` from inside run(), so the OS thread may
        # not have exited yet — wait it out BEFORE dropping the last reference,
        # or the GC can destroy a live QThread and abort the whole process.
        worker, self._worker = self._worker, None
        settle(worker)
        out = Path(self._out_dir.text()) / self._out_name.text()
        try:
            plan = self._effective_plan()
            log_manager.log_merge(
                source_folder = self._folder_edit.text(),
                output        = str(out),
                clips         = sorted(self._clips, key=lambda c: c.order_idx),
                track_order   = self._current_track_order(),
                success       = success,
                message       = message,
                mix           = {
                    "tracks":       [t.kind for t in plan.tracks if t.enabled],
                    "include_video": plan.include_video,
                    "mix_enabled":  any(t.kind == "mix" and t.enabled for t in plan.tracks),
                    "kind":         plan.mix_kind,
                    "match_levels": plan.mix_match_levels,
                },
                plan          = plan,
            )
        except Exception:
            pass
        if success:
            self._pbar.setValue(100)
            p = theme.active_palette()
            for lbl in self._stage_labels:
                lbl.setStyleSheet(f"background:{p.ok}; color:white; border-radius:4px; "
                                  "padding:3px 7px; font-size:11px;")
            self.merge_complete.emit(str(out))
            QMessageBox.information(self, "Done", f"Merge complete!\n\n{message}\n{out}")
        else:
            QMessageBox.warning(self, "Failed", f"Merge failed:\n{message}")

    def shutdown(self):
        """Wait out all worker threads (called from MainWindow.closeEvent)."""
        if self._worker:
            self._worker.cancel()
        if self._probe_thread:
            self._probe_thread.stop()
        settle(self._worker, 10000)
        settle(self._probe_thread)
        self._worker = None
        self._probe_thread = None

    def set_output_path_hint(self, path: str):
        p = Path(path)
        self._out_dir.setText(str(p.parent))
        self._out_name.setText(p.name)
