"""whatsapp_tab.py — WhatsApp clip export tab UI and logic."""

import os
import tempfile
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QScrollArea,
    QLabel, QPushButton, QLineEdit, QFileDialog,
    QProgressBar, QFrame, QSizePolicy,
    QMessageBox, QCheckBox,
)

from ffmpeg_runner import WhatsAppWorker, FramePreviewWorker, get_ffmpeg, get_app_dir
from thread_utils import settle
from grade_manager import Grade, scan_luts, migrate_grade_key
from probe import probe as probe_file
from settings import Settings
from widgets.timeline import TrimTimeline as TimelineWidget, secs_to_tc as _secs_to_tc
import log_manager
import theme


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

        self._setup_ui()
        self._load_grades()
        self._restyle()
        ctrl = theme.controller()
        if ctrl is not None:
            ctrl.changed.connect(self._restyle)

    # ── UI construction ───────────────────────────────────────────────────────

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(14, 14, 14, 14)

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
