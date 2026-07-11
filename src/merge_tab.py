"""merge_tab.py — Merge clips tab UI and logic (v1.4)."""

import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal, QUrl
from PySide6.QtGui import QColor, QPixmap, QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QPushButton, QLineEdit, QFileDialog,
    QTableWidget, QTableWidgetItem, QHeaderView,
    QTreeWidget, QTreeWidgetItem, QDialog,
    QAbstractItemView, QProgressBar, QFrame,
    QComboBox, QMessageBox, QCheckBox, QScrollArea,
    QRadioButton, QButtonGroup,
)
from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
from PySide6.QtMultimediaWidgets import QVideoWidget

from clip_model import (ClipInfo, scan_folder, unpaired_wavs, check_dst_warning,
                        order_clips_by_time, assign_cameras, group_clips_by_camera,
                        _iso_epoch, detect_clip_splits)
from ffmpeg_runner import MergeWorker, get_ffmpeg, get_app_dir
from thread_utils import settle
from probe import probe, probe_duration, pix_fmt_info, BaselineSpec, apply_conformance
from settings import Settings
from core.ffmpeg_cmd import (OutputPlan, OutputTrack, ConformSpec, DEFAULT_CONFORM, build_clip_sample_cmd,
                             QUALITY_PRESETS, DEFAULT_QUALITY_PRESET, quality_for_preset)
from core.baseline import ClipSpec, enumerate_specs, recommend_baseline
from core.sync_advanced import LARGE_MISMATCH_S
import log_manager
import theme

CUSTOM_AUDIO_LABEL = "Custom…"

_NTSC_RATES = {30000 / 1001: "30000/1001", 24000 / 1001: "24000/1001",
               60000 / 1001: "60000/1001"}


def _fps_to_float(fps_str: str) -> float:
    try:
        return float(fps_str)
    except (TypeError, ValueError):
        return 30000 / 1001


def _fps_to_ffmpeg(fps_str: str) -> str:
    """An ffmpeg fps expression — snaps NTSC rates to their exact fraction."""
    f = _fps_to_float(fps_str)
    for rate, frac in _NTSC_RATES.items():
        if abs(f - rate) < 0.01:
            return frac
    return fps_str or "30"

# status → (palette attribute name, label)
STATUS_COLORS = {
    "ok":        ("ok",        "Stream copy"),
    "transcode": ("warn",      "Will transcode"),
    "hdr":       ("danger",    "Review — HDR"),
    "error":     ("danger",    "Probe error"),
    "unknown":   ("text_mute", "…"),
}

COL_ORDER   = 0
COL_NAME    = 1
COL_PREVIEW = 2
COL_TIME    = 3
COL_CAM     = 4
COL_DUR     = 5
COL_WAV     = 6
COL_WAV_DUR = 7
COL_PRIMARY = 8
COL_OFFSET  = 9
COL_DRIFT   = 10
COL_STATUS  = 11
COL_UP      = 12
COL_DOWN    = 13
N_COLS      = 14

# Primary-track override choices for the per-clip Primary combo (COL_PRIMARY).
# "auto" defers to the global Camera/WAV choice (plus the no-source fallback in
# core.ffmpeg_cmd's _slot_fill) — the other three force that clip's disposition-
# default track to carry a specific source regardless of the global setting.
PRIMARY_OVERRIDE_OPTIONS = [
    ("auto",   "Auto"),
    ("camera", "Camera"),
    ("wav",    "WAV"),
    ("mix",    "Mix"),
]

# Columns whose header can be clicked to view-sort — a display-only reorder that
# never touches clip.order_idx (the actual merge sequence). COL_TIME/COL_DUR sort
# clips within each camera group; COL_CAM sorts the groups themselves.
_SORTABLE_COLS = (COL_TIME, COL_DUR, COL_CAM)


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


def _valid_primary_options(clip) -> list:
    """Which per-clip Primary override choices (COL_PRIMARY) are actually
    usable for this clip — [(key, label), …]. "Auto" is always offered; the
    others only when the underlying source genuinely exists on this clip, so
    picking one can never silently fall back to something else without the
    user seeing why it wasn't offered in the first place."""
    opts = [("auto", "Auto")]
    if clip.has_camera_audio():
        opts.append(("camera", "Camera"))
    if clip.has_wav():
        opts.append(("wav", "WAV"))
    if clip.status == "ok" and clip.has_camera_audio() and clip.has_wav():
        opts.append(("mix", "Mix"))
    return opts

# Primary (lossless) audio track. The combined mix is a separate opt-in track.
TRACK_OPTIONS = [
    ("Camera audio (AAC)",              "camera"),
    ("WAV backup (on-board mic)",       "wav"),
]


def _fmt_dur(secs: float) -> str:
    if secs <= 0:
        return "—"
    h, r = divmod(int(secs), 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def _fmt_timestamp_cell(clip) -> tuple:
    """(display_text, differs_from_filename, tooltip_reason). The clip's
    linear-order timestamp — the file's own creation_time (used for ordering,
    per Phase 1) when available, else the filename-embedded time. Flags when
    the two still disagree after converting to local time (a genuine clock
    mismatch — e.g. the camera's clock was never set — not an ordinary DST
    difference), since that's exactly the kind of silent reordering a user
    should be able to see and understand, not just trust.

    creation_time is UTC (confirmed by its "Z" suffix); converting with
    .astimezone() (no args = the system's local zone) re-applies whatever
    DST offset was in effect, matching the camera's own local-time filename.
    Without that conversion this compared UTC directly against local time —
    confirmed directly against a real shoot: every clip was off by exactly
    the BST (UTC+1) offset, and the "differs" warning below fired on all of
    them even though nothing was actually wrong."""
    ct_time = None
    ct = clip.stream.creation_time if clip.stream else ""
    if ct:
        try:
            from datetime import datetime
            ct_time = (datetime.fromisoformat(ct.replace("Z", "+00:00"))
                      .astimezone().strftime("%H:%M:%S"))
        except (ValueError, TypeError):
            ct_time = None
    fn_time = None
    if clip.filename_ts is not None:
        h, rem = divmod(clip.filename_ts, 3600)
        mnt, s = divmod(rem, 60)
        fn_time = f"{h:02d}:{mnt:02d}:{s:02d}"

    display = ct_time or fn_time or "—"
    differs = bool(ct_time and fn_time and ct_time != fn_time)
    reason = ""
    if differs:
        reason = (f"Filename suggests {fn_time}, but the file's own metadata — used for "
                 f"ordering — says {ct_time} (already converted to local time). Likely a "
                 "genuine clock mismatch on the camera, not a timezone/DST artifact.")
    return display, differs, reason


def _clip_time_sort_key(clip):
    """Sortable value for the Timestamp column: creation_time epoch when
    available (matches the same preference `_fmt_timestamp_cell` displays),
    else the filename-embedded time, else last."""
    epoch = _iso_epoch(clip.stream.creation_time if clip.stream else "")
    if epoch is not None:
        return epoch
    return clip.filename_ts if clip.filename_ts is not None else float("inf")


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


def _make_status_button(clip: ClipInfo) -> QPushButton:
    """Same look as _make_status_badge, but a real button — clicking it opens
    _ClipVideoOptionsDialog (per-clip force-transcode / use-LRV-proxy /
    preserve-LRV-proxy options). Reflects effective_status() (the override-
    aware status), not the raw probed one, so an overridden clip's badge
    genuinely matches what the merge will do."""
    pal = theme.active_palette()
    status = clip.effective_status()
    attr, label = STATUS_COLORS.get(status, ("text_mute", status))
    color = getattr(pal, attr)
    conflicts = clip.conflicts
    if clip.video_source_override == "transcode" and not conflicts:
        label = label + "  (forced)"
    elif clip.video_source_override == "lrv" and clip.has_lrv():
        label = label + "  (LRV)"
    text = label + ("  " + " · ".join(conflicts) if conflicts else "")
    btn = QPushButton(text)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    btn.setStyleSheet(
        f"QPushButton {{ background:{color}; color:{pal.bg}; border:none; border-radius:4px; "
        "padding:2px 6px; font-size:11px; }"
        f"QPushButton:hover {{ background:{color}; border:1px solid {pal.text}; }}")
    btn.setToolTip("Click to change how this clip's video gets into the master")
    return btn


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


class _GpuProbeThread(QThread):
    """One-shot: which GPU encoders (if any) actually work on this machine.
    Runs off the UI thread since each vendor probe spawns a real ffmpeg process."""
    detected = Signal(list)   # list[str] of working vendors, e.g. ["qsv"]

    def __init__(self, ff: str):
        super().__init__()
        self._ff = ff

    def run(self):
        from core.gpu_encode import available_hw_vendors
        self.detected.emit(available_hw_vendors(self._ff, "hevc"))


class _ClipSampleThread(QThread):
    """Extracts a short 160p proxy sample for the per-clip preview button.
    Runs off the UI thread since it's a real ffmpeg subprocess."""
    done = Signal(int, str, str)   # clip_idx, out_path, error ("" if none)

    def __init__(self, ff: str, clip_idx: int, source: str, start_ts: float,
                duration: float, out_path: Path, accel: Optional[dict] = None):
        super().__init__()
        self._ff, self._clip_idx, self._source = ff, clip_idx, source
        self._start_ts, self._duration, self._out_path = start_ts, duration, out_path
        self._accel = accel or {}

    def run(self):
        kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
        cmd = build_clip_sample_cmd(self._ff, self._source, self._start_ts,
                                    self._duration, str(self._out_path), **self._accel)
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30, **kwargs)
            err = "" if r.returncode == 0 and self._out_path.exists() else (r.stderr or "sample extraction failed")
        except Exception as e:
            err = str(e)
        self.done.emit(self._clip_idx, str(self._out_path), err)


_PREVIEW_WINDOW_SIZES = {"small": (400, 300), "medium": (640, 360), "large": (960, 540)}
_PREVIEW_ASPECT_MODES = {
    "fit": Qt.AspectRatioMode.KeepAspectRatio,
    "stretch": Qt.AspectRatioMode.IgnoreAspectRatio,
    "crop": Qt.AspectRatioMode.KeepAspectRatioByExpanding,
}


class _ClipPreviewDialog(QDialog):
    """Small popup that auto-plays a clip's low-res sample file. Window size,
    video scaling (aspect mode), looping and speed are all driven by the hidden
    Developer panel (defaults: 640×360, fit, loop on, 1× speed)."""

    def __init__(self, sample_path: str, title: str, parent=None, *,
                 window_size=(640, 360), aspect_mode="fit", loop=True, speed=1.0):
        super().__init__(parent)
        self.setWindowTitle(f"Preview — {title}")
        self._loop = bool(loop)
        w, h = window_size
        self.resize(int(w), int(h))
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        video = QVideoWidget()
        video.setAspectRatioMode(_PREVIEW_ASPECT_MODES.get(aspect_mode,
                                                           Qt.AspectRatioMode.KeepAspectRatio))
        lay.addWidget(video, 1)
        # A small status line, shown only if playback can't start — otherwise the
        # video fills the window. Prevents a silent "black window, no video".
        self._status_lbl = QLabel("", self)
        self._status_lbl.setAlignment(Qt.AlignCenter)
        self._status_lbl.setWordWrap(True)
        self._status_lbl.setVisible(False)
        lay.addWidget(self._status_lbl)

        self._player = QMediaPlayer(self)
        self._audio_out = QAudioOutput(self)
        self._player.setAudioOutput(self._audio_out)
        self._player.setVideoOutput(video)
        try:
            self._player.setPlaybackRate(float(speed))
        except Exception:
            pass
        # setSource wants a QUrl: a bare Windows path string is parsed as a URL,
        # so "C:\…\preview.mp4" treats "C" as the scheme and never loads (the
        # black-window symptom). fromLocalFile builds a correct file:// URL.
        self._player.setSource(QUrl.fromLocalFile(sample_path))
        self._player.mediaStatusChanged.connect(self._on_status)
        self._player.errorOccurred.connect(self._on_error)
        self._player.play()

    def _on_status(self, status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            if self._loop:
                self._player.setPosition(0)
                self._player.play()
        elif status == QMediaPlayer.MediaStatus.InvalidMedia:
            self._show_message("This preview couldn't be played on this system's media backend.")

    def _on_error(self, _error, error_string):
        self._show_message(error_string or "The preview couldn't be played.")

    def _show_message(self, text: str):
        self._status_lbl.setText(text)
        self._status_lbl.setVisible(True)

    def closeEvent(self, event):
        self._player.stop()
        super().closeEvent(event)


class _WavAssignDialog(QDialog):
    """Manually pair unmatched WAV files with clips — the fallback for
    cross-brand naming conventions `clip_model._pair_wav`'s automatic
    (date, time, clip-number) heuristic couldn't match."""

    def __init__(self, orphans: list, clips: list, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Assign unmatched WAV files")
        self._combos: dict = {}   # wav_path -> QComboBox

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Pick which clip each unmatched WAV file belongs to:"))
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        for row, wav in enumerate(orphans):
            grid.addWidget(QLabel(wav.name), row, 0)
            combo = QComboBox()
            combo.addItem("— unused —", None)
            for clip in clips:
                label = clip.stem + ("  (already has a WAV)" if clip.has_wav() else "")
                combo.addItem(label, clip)
            grid.addWidget(combo, row, 1)
            self._combos[wav] = combo
        lay.addLayout(grid)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Apply")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        btn_row.addWidget(cancel)
        btn_row.addWidget(ok)
        lay.addLayout(btn_row)

    def assignments(self) -> dict:
        """{wav_path: ClipInfo} for every combo the user pointed at a real clip
        (skips ones left at "— unused —")."""
        return {wav: combo.currentData() for wav, combo in self._combos.items()
                if combo.currentData() is not None}


_MISMATCH_RESOLUTIONS = [
    ("auto",       "Trim automatically (recommended)",
     "Detects the real offset and trims the WAV to match — the same automatic "
     "sync analysis used for every clip."),
    ("start",      "Align to clip start",
     "Assumes the WAV and the clip's video begin at the same instant, with no "
     "trimming."),
    ("end",        "Align to clip end",
     "Assumes the WAV and the clip's video finish at the same instant — the "
     "right call when you know the recordings were stopped together."),
    ("disconnect", "Don't use this WAV",
     "Disconnects this WAV entirely — the clip's audio falls back to its "
     "camera track automatically."),
]


class _WavMismatchDialog(QDialog):
    """Shown before committing a WAV reassignment/swap whose duration doesn't
    match the clip it would be paired with (beyond LARGE_MISMATCH_S) — rather
    than silently accepting a probably-wrong pairing, explain the mismatch and
    let the user choose how to resolve it. Every option maps straight onto
    already-tested machinery (ClipInfo.alignment_mode, the existing WAV
    disconnect path, core.ffmpeg_cmd's no-WAV camera fallback) — no new merge
    behaviour is invented here, just a way to pick between what already
    exists, on purpose instead of by accident."""

    def __init__(self, clip_name: str, clip_dur: float, wav_name: str, wav_dur: float,
                parent=None):
        super().__init__(parent)
        p = theme.active_palette()
        self.setWindowTitle(f"This WAV doesn't match {clip_name}")
        self.setMinimumWidth(440)

        lay = QVBoxLayout(self)
        diff = abs(clip_dur - wav_dur)
        longer = "WAV" if wav_dur > clip_dur else "clip"
        explain = QLabel(
            f"“{wav_name}” runs {diff:.1f}s longer/shorter than “{clip_name}” — the {longer} "
            "is the longer of the two. Recovering meaningful sync from a difference this large "
            "isn't automatic; pick how to handle it:")
        explain.setWordWrap(True)
        lay.addWidget(explain)

        self._group = QButtonGroup(self)
        self._radios: dict = {}
        self._cards: list = []
        for key, label, desc in _MISMATCH_RESOLUTIONS:
            recommended = key == "auto"
            card = QFrame()
            card.setObjectName("mismatchCardRecommended" if recommended else "mismatchCard")
            border = f"2px solid {p.accent}" if recommended else f"1px solid {p.border}"
            card.setStyleSheet(
                f"QFrame#{card.objectName()} {{ background:{p.surface2}; "
                f"border:{border}; border-radius:8px; }}")
            card_lay = QVBoxLayout(card)
            card_lay.setContentsMargins(14, 10, 14, 10)
            card_lay.setSpacing(3)
            radio = QRadioButton(label)
            radio.setChecked(recommended)
            radio.setStyleSheet(f"QRadioButton {{ color:{p.text}; font-size:12.5px; font-weight:bold; }}")
            desc_lbl = QLabel(desc)
            desc_lbl.setWordWrap(True)
            desc_lbl.setStyleSheet(f"color:{p.text_dim}; font-size:11px;")
            card_lay.addWidget(radio)
            card_lay.addWidget(desc_lbl)
            self._group.addButton(radio)
            self._radios[key] = radio
            self._cards.append(card)
            lay.addWidget(card)

        lay.addSpacing(6)
        self._preserve_check = QCheckBox("Also preserve this WAV in full, on its own archival track")
        self._preserve_check.setChecked(False)   # opt-in — never doubles a merge's audio footprint by default
        self._preserve_check.setToolTip(
            "Embeds the complete, untouched original WAV as an extra standalone lossless track, "
            "regardless of which option above is used for the actual playback/mix track — recorded "
            "in the manifest so Extract and Recover can retrieve it byte-exact later.")
        lay.addWidget(self._preserve_check)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Apply")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        btn_row.addWidget(cancel)
        btn_row.addWidget(ok)
        lay.addLayout(btn_row)

    def resolution(self) -> str:
        """One of 'auto' / 'start' / 'end' / 'disconnect' — whichever card is checked."""
        return next((key for key, radio in self._radios.items() if radio.isChecked()), "auto")

    def preserve_full(self) -> bool:
        return self._preserve_check.isChecked()


_SPLIT_RESOLUTIONS = [
    ("split",   "Split the WAV and pair each half (recommended)",
     "Trims the WAV at {a_dur} — {a} keeps its own portion, {b} is paired with the remainder."),
    ("leave",   "Leave as-is",
     "{b} keeps using its camera audio; {a}'s WAV stays untouched."),
    ("dismiss", "Don’t ask about this pair again",
     "Dismisses this specific finding for this folder."),
]


class _ClipSplitDialog(QDialog):
    """Shown from the clip-split banner (clip_model.detect_clip_splits) —
    explains the finding and lets the user choose how to resolve it. "split"
    is the only option that changes anything; it maps straight onto the
    existing WAV-reassignment path (MergeTab._resolve_clip_split does a
    plain ffmpeg trim + the SAME wav_path/wav_offset/sync_done reset any
    other WAV reassignment uses), so nothing new is invented in the merge
    pipeline itself."""

    def __init__(self, clip_a: ClipInfo, clip_b: ClipInfo, parent=None):
        super().__init__(parent)
        p = theme.active_palette()
        self.setWindowTitle(f"Clip split detected: {clip_a.stem} → {clip_b.stem}")
        self.setMinimumWidth(460)

        a_dur, b_dur = clip_a.duration, clip_b.duration
        wav_dur = clip_a.wav_duration

        lay = QVBoxLayout(self)
        explain = QLabel(
            f"“{clip_a.wav_path.name if clip_a.wav_path else ''}” runs {_fmt_dur(wav_dur)} — "
            f"about as long as {clip_a.stem} ({_fmt_dur(a_dur)}) plus {clip_b.stem} ({_fmt_dur(b_dur)}) "
            "combined. The two clips are also adjacent with no timestamp gap. This looks like one "
            "continuous audio recording that the camera split into two video files.")
        explain.setWordWrap(True)
        explain.setStyleSheet(f"color:{p.text_mute}; font-size:11.5px;")
        lay.addWidget(explain)

        timeline = QFrame()
        timeline.setStyleSheet(f"QFrame {{ background:{p.surface2}; border:1px solid {p.border}; border-radius:6px; }}")
        t_lay = QVBoxLayout(timeline)
        t_lay.setContentsMargins(10, 8, 10, 8)
        cap = QLabel("TIMELINE")
        cap.setStyleSheet(f"color:{p.text_mute}; font-size:10px;")
        t_lay.addWidget(cap)
        bar_row = QHBoxLayout()
        bar_row.setSpacing(0)
        seg_a = QLabel(f"{clip_a.stem}  ·  {_fmt_dur(a_dur)}")
        seg_a.setAlignment(Qt.AlignmentFlag.AlignCenter)
        seg_a.setStyleSheet(f"background:{p.gold}; color:{p.on_accent()}; font-size:10px; padding:4px 0;")
        seg_b = QLabel(f"{clip_b.stem}  ·  {_fmt_dur(b_dur)}")
        seg_b.setAlignment(Qt.AlignmentFlag.AlignCenter)
        seg_b.setStyleSheet(f"background:{p.accent}; color:{p.on_accent()}; font-size:10px; padding:4px 0;")
        bar_row.addWidget(seg_a, max(1, int(a_dur)))
        bar_row.addWidget(seg_b, max(1, int(b_dur)))
        t_lay.addLayout(bar_row)
        wav_cap = QLabel(f"One continuous WAV  ·  {_fmt_dur(wav_dur)} total")
        wav_cap.setStyleSheet(f"color:{p.text_mute}; font-size:10px; padding-top:4px;")
        t_lay.addWidget(wav_cap)
        lay.addWidget(timeline)
        lay.addSpacing(6)

        self._group = QButtonGroup(self)
        self._radios: dict = {}
        for key, label, desc_tpl in _SPLIT_RESOLUTIONS:
            recommended = key == "split"
            card = QFrame()
            border = f"2px solid {p.accent}" if recommended else f"1px solid {p.border}"
            card.setStyleSheet(f"QFrame {{ background:{p.surface2}; border:{border}; border-radius:8px; }}")
            card_lay = QVBoxLayout(card)
            card_lay.setContentsMargins(14, 10, 14, 10)
            card_lay.setSpacing(3)
            radio = QRadioButton(label)
            radio.setChecked(recommended)
            radio.setStyleSheet(f"QRadioButton {{ color:{p.text}; font-size:12.5px; font-weight:bold; }}")
            desc = QLabel(desc_tpl.format(a=clip_a.stem, b=clip_b.stem, a_dur=_fmt_dur(a_dur)))
            desc.setWordWrap(True)
            desc.setStyleSheet(f"color:{p.text_dim}; font-size:11px;")
            card_lay.addWidget(radio)
            card_lay.addWidget(desc)
            self._group.addButton(radio)
            self._radios[key] = radio
            lay.addWidget(card)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Apply")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        btn_row.addWidget(cancel)
        btn_row.addWidget(ok)
        lay.addLayout(btn_row)

    def resolution(self) -> str:
        return next((key for key, radio in self._radios.items() if radio.isChecked()), "leave")


_VIDEO_SOURCE_OPTIONS = [
    ("auto", "Auto — stream copy (current)",
     "Lossless, no re-encode — this clip's own footage goes straight into the baseline."),
    ("transcode", "Force transcode",
     "Re-encodes this clip to the baseline's target quality instead of copying it. The "
     "untouched 4K original is kept, byte-exact, on its own archival track — recoverable "
     "later via Extract."),
    ("lrv", "Use the LRV proxy instead",
     "Conforms the camera's own low-res proxy into the baseline in place of the original "
     "— much faster to encode. The untouched original is kept, byte-exact, on its own "
     "archival track."),
]


class _ClipVideoOptionsDialog(QDialog):
    """Opened by clicking a clip's Status badge — per-clip control over how
    its VIDEO lands in the master. "Force transcode"/"Use the LRV proxy
    instead" both just set ClipInfo.video_source_override, which
    ClipInfo.effective_status() (read by core.ffmpeg_cmd/ffmpeg_runner.py
    instead of the raw probed status) turns into the SAME conform+archival
    path a genuine spec mismatch already goes through — no new merge
    behaviour invented, just a way to trigger existing machinery on purpose.
    "Also preserve the LRV proxy" is a separate, independent opt-in
    (ClipInfo.preserve_lrv) — it can be combined with any of the three
    choices above."""

    def __init__(self, clip: ClipInfo, parent=None):
        super().__init__(parent)
        p = theme.active_palette()
        self.setWindowTitle(f"Video source for {clip.stem}")
        self.setMinimumWidth(440)

        lay = QVBoxLayout(self)
        explain = QLabel(
            "This clip already matches your baseline spec and stream-copies cleanly. You can "
            "still choose to conform it a different way — useful if you want every clip in the "
            "master to share one consistent encode, or to prioritise a smaller file size over a "
            "byte-exact copy." if clip.status == "ok" else
            "This clip needs conforming to match the baseline. You can still choose which "
            "source to conform FROM.")
        explain.setWordWrap(True)
        explain.setStyleSheet(f"color:{p.text_mute}; font-size:11.5px;")
        lay.addWidget(explain)

        self._group = QButtonGroup(self)
        self._radios: dict = {}
        current = clip.video_source_override if clip.video_source_override != "lrv" or clip.has_lrv() else "auto"
        for key, label, desc in _VIDEO_SOURCE_OPTIONS:
            if key == "lrv" and not clip.has_lrv():
                continue   # nothing to swap in — don't offer a choice that can't work
            selected = key == current
            card = QFrame()
            border = f"2px solid {p.accent}" if selected else f"1px solid {p.border}"
            card.setStyleSheet(f"QFrame {{ background:{p.surface2}; border:{border}; border-radius:8px; }}")
            card_lay = QVBoxLayout(card)
            card_lay.setContentsMargins(14, 10, 14, 10)
            card_lay.setSpacing(3)
            radio = QRadioButton(label)
            radio.setChecked(selected)
            radio.setStyleSheet(f"QRadioButton {{ color:{p.text}; font-size:12.5px; font-weight:bold; }}")
            desc_lbl = QLabel(desc)
            desc_lbl.setWordWrap(True)
            desc_lbl.setStyleSheet(f"color:{p.text_dim}; font-size:11px;")
            card_lay.addWidget(radio)
            card_lay.addWidget(desc_lbl)
            self._group.addButton(radio)
            self._radios[key] = radio
            lay.addWidget(card)

        self._preserve_check = None
        if clip.has_lrv():
            lay.addSpacing(6)
            check = QCheckBox("Also preserve the LRV proxy on its own track")
            check.setChecked(clip.preserve_lrv)
            check.setToolTip(
                "Independent of the choice above — keeps the low-res proxy, stream-copied, as an "
                "extra recoverable backup regardless of which video actually plays.")
            lay.addWidget(check)
            self._preserve_check = check

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("Apply")
        ok.setDefault(True)
        ok.clicked.connect(self.accept)
        btn_row.addWidget(cancel)
        btn_row.addWidget(ok)
        lay.addLayout(btn_row)

    def video_source_override(self) -> str:
        return next((key for key, radio in self._radios.items() if radio.isChecked()), "auto")

    def preserve_lrv(self) -> bool:
        return self._preserve_check.isChecked() if self._preserve_check is not None else False


class _CameraNamingDialog(QDialog):
    """Shown once after a fresh folder finishes probing — lets the user
    confirm or rename each auto-detected camera group up front, rather than
    only discoverable later via double-clicking a group header."""

    def __init__(self, groups: list, parent=None):
        """`groups`: [(camera_id, guessed_label, clip_count), …]."""
        super().__init__(parent)
        self.setWindowTitle("Name your cameras")
        self._edits: dict = {}   # camera_id -> QLineEdit

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel(
            f"Detected {len(groups)} camera{'s' if len(groups) != 1 else ''} in this folder — "
            "confirm or rename each one (you can also rename later by double-clicking its "
            "group header)."))
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        for row, (camera_id, guessed_label, count) in enumerate(groups):
            grid.addWidget(QLabel(f"{count} clip{'s' if count != 1 else ''}"), row, 0)
            edit = QLineEdit(guessed_label)
            edit.selectAll()
            grid.addWidget(edit, row, 1)
            self._edits[camera_id] = edit
        lay.addLayout(grid)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        skip_btn = QPushButton("Skip")
        skip_btn.setToolTip("Keep the auto-detected names — you can still rename a camera "
                            "later by double-clicking its group header.")
        skip_btn.clicked.connect(self.reject)
        ok_btn = QPushButton("OK")
        ok_btn.setDefault(True)
        ok_btn.clicked.connect(self.accept)
        btn_row.addWidget(skip_btn)
        btn_row.addWidget(ok_btn)
        lay.addLayout(btn_row)

    def labels(self) -> dict:
        """{camera_id: new_label} for every field the user left non-empty."""
        return {cid: edit.text().strip() for cid, edit in self._edits.items()
                if edit.text().strip()}


class _CameraGroupTree(QTreeWidget):
    """The clips tree: one top-level item per detected camera, clips nested
    underneath. Dragging a clip onto a different camera group reassigns it
    (emits `clip_reassign_requested` instead of letting Qt reparent the item
    directly, so MergeTab's data model stays the single source of truth and a
    full rebuild — via `clip_reassign_requested` → `_populate_table()` —
    keeps the tree consistent)."""
    clip_reassign_requested = Signal(int, str)   # clip_idx (into MergeTab._clips), target camera_id

    def __init__(self, columns: int, parent=None):
        super().__init__(parent)
        self.setColumnCount(columns)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDropIndicatorShown(True)
        self._drag_source_item: Optional[QTreeWidgetItem] = None

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        self._drag_source_item = self.currentItem()

    def dropEvent(self, event):
        dragged = self._drag_source_item
        if dragged is None or dragged.parent() is None:
            event.ignore()   # only clip (child) items can be dragged; groups can't
            return
        target = self.itemAt(event.position().toPoint())
        if target is None:
            event.ignore()
            return
        group_item = target if target.parent() is None else target.parent()
        clip_idx = dragged.data(COL_NAME, Qt.ItemDataRole.UserRole)
        target_camera_id = group_item.data(COL_NAME, Qt.ItemDataRole.UserRole)
        if clip_idx is not None and target_camera_id is not None:
            self.clip_reassign_requested.emit(clip_idx, target_camera_id)
        event.accept()   # we handle the model change ourselves — no default reparent


class MergeTab(QWidget):
    merge_complete = Signal(str)
    open_in_review = Signal(str)   # user clicked "Review" on the completion dialog

    def __init__(self, settings: Settings):
        super().__init__()
        self.setAcceptDrops(True)   # drag a folder onto the tab to load it
        self._settings      = settings
        self._clips: list[ClipInfo] = []
        self._worker: Optional[MergeWorker] = None
        self._probe_thread: Optional[ProbeThread] = None
        self._gpu_probe_thread: Optional[_GpuProbeThread] = None
        self._gpu_vendors: list = []   # working hw encoder vendors, filled in async
        self._view_sort_col: Optional[int] = None   # display-only sort; None = chronological
        self._view_sort_asc: bool = True
        self._probed_count = 0
        self._pending_camera_naming_prompt = False   # set on a fresh folder load
        self._preview_threads: list = []   # keep _ClipSampleThread instances alive while running
        self._preview_dialogs: list = []   # keep open _ClipPreviewDialog instances alive
        self._preview_cache: dict = {}     # clip path (str) -> generated sample Path
        self._last_verify_summary: str = ""
        # Clip-split detection (a camera hitting its own length limit splits one
        # continuous take into two files, but a separate audio recorder keeps
        # rolling) — see clip_model.detect_clip_splits. Recomputed on every
        # _populate_table call (cheap); dismissed pairs are keyed by (path, path)
        # since ClipInfo isn't hashable, and only last for this session/folder.
        self._clip_split_suggestions: list = []
        self._dismissed_split_pairs: set = set()
        # Output track plan state
        self._custom_tracks = None     # list[OutputTrack] when a custom order is set
        self._include_video = True
        self._suppress_combo = False
        # Archival-defaults auto-selection (see _auto_select_archival_params):
        # True once the user has manually touched one of the three checkboxes
        # for the current folder, so we stop overriding their choice.
        self._archival_user_overridden = False
        self._applying_auto_archival = False
        self._verify_pill: Optional[QLabel] = None
        self._setup_ui()
        self._restyle()
        ctrl = theme.controller()
        if ctrl is not None:
            ctrl.changed.connect(self._restyle)
        self._start_gpu_probe()

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

        self._unmatched_row = QWidget()
        self._unmatched_row.hide()
        unmatched_lay = QHBoxLayout(self._unmatched_row)
        unmatched_lay.setContentsMargins(0, 0, 0, 0)
        self._unmatched_banner = QLabel()
        self._unmatched_banner.setWordWrap(True)
        self._unmatched_assign_btn = QPushButton("Assign…")
        self._unmatched_assign_btn.setToolTip(
            "Manually pair an unmatched WAV file with a clip — useful when clips and their\n"
            "recorder audio use naming conventions the app couldn't automatically match.")
        self._unmatched_assign_btn.clicked.connect(self._open_wav_assign_dialog)
        unmatched_lay.addWidget(self._unmatched_banner, 1)
        unmatched_lay.addWidget(self._unmatched_assign_btn)
        c.addWidget(self._unmatched_row)
        self._source_folder: Optional[Path] = None

        # ── Baseline spec chooser ─────────────────────────────────────────────
        self._res_banner = QWidget()
        self._res_banner.hide()
        res_layout = QVBoxLayout(self._res_banner)
        res_layout.setContentsMargins(10, 8, 10, 8)
        res_layout.setSpacing(6)
        self._res_label = QLabel()
        self._res_label.setWordWrap(True)
        res_layout.addWidget(self._res_label)
        self._res_btn_row = QHBoxLayout()   # baseline-spec buttons, rebuilt after probe
        self._res_btn_row.setSpacing(6)
        res_layout.addLayout(self._res_btn_row)
        fill_row = QHBoxLayout()
        fill_row.setSpacing(6)
        self._fill_label = QLabel("Padding for odd-aspect / vertical clips:")
        self._fill_combo = QComboBox()
        self._fill_combo.addItems(["Black bars", "Blurred fill"])
        fill_row.addWidget(self._fill_label)
        fill_row.addWidget(self._fill_combo)
        fill_row.addStretch()
        res_layout.addLayout(fill_row)
        c.addWidget(self._res_banner)
        self._baseline_buttons: list = []   # (QPushButton, SpecGroup)
        self._spec_groups: list = []
        self._chosen_group = None

        # ── CLIPS ───────────────────────────────────────────────────────────────
        self._show_sync_check = QCheckBox("Show sync details")
        self._show_sync_check.setToolTip("Show the WAV Offset and Drift columns.")
        self._show_sync_check.toggled.connect(self._on_show_sync_toggled)
        clips_frame, clips_box = self._section("CLIPS", right=self._show_sync_check)
        self._clips_title = self._section_titles[-1]

        # Probing (ffprobe per clip) can take a noticeable moment on a large real
        # shoot with zero other feedback beyond the static "N found" title — this
        # bar shows live progress so a slow probe doesn't look identical to a
        # stalled one.
        self._probe_progress_row = QWidget()
        probe_progress_lay = QHBoxLayout(self._probe_progress_row)
        probe_progress_lay.setContentsMargins(0, 0, 0, 0)
        probe_progress_lay.setSpacing(8)
        self._probe_progress_bar = QProgressBar()
        self._probe_progress_bar.setRange(0, 100)
        self._probe_progress_bar.setFixedHeight(14)
        self._probe_progress_label = QLabel("")
        probe_progress_lay.addWidget(self._probe_progress_bar, 1)
        probe_progress_lay.addWidget(self._probe_progress_label)
        self._probe_progress_row.hide()
        clips_box.addWidget(self._probe_progress_row)

        self._table = _CameraGroupTree(N_COLS)
        self._table.setHeaderLabels(
            ["#", "Clip", "", "Timestamp", "Camera", "Duration", "WAV", "WAV Dur", "Primary", "WAV Offset", "Drift",
             "Status", "↑", "↓"]
        )
        # Status stays Stretch to absorb leftover width; every other content column is
        # Interactive so the user can drag-resize it (e.g. widen Clip for a long name) —
        # utility columns (#, preview, ↑/↓, hidden sync details) stay auto-sized, they're not worth dragging.
        self._table.header().setSectionResizeMode(COL_STATUS, QHeaderView.ResizeMode.Stretch)
        for col in (COL_ORDER, COL_PREVIEW, COL_OFFSET, COL_DRIFT, COL_UP, COL_DOWN):
            self._table.header().setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        for col, default_width in (
            (COL_NAME, 220), (COL_TIME, 90), (COL_CAM, 110), (COL_DUR, 70), (COL_WAV, 50),
            (COL_WAV_DUR, 65), (COL_PRIMARY, 110),
        ):
            self._table.header().setSectionResizeMode(col, QHeaderView.ResizeMode.Interactive)
            self._table.setColumnWidth(col, default_width)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.setMinimumHeight(200)
        self._table.itemDoubleClicked.connect(self._on_tree_item_double_clicked)
        self._table.itemChanged.connect(self._on_tree_item_edited)
        self._table.clip_reassign_requested.connect(self._on_clip_reassigned)
        self._table.header().setSectionsClickable(True)
        self._table.header().sectionClicked.connect(self._on_header_clicked)
        self._table.header().setToolTip(
            "Click Timestamp/Duration/Camera to sort the view (click again to reverse).\n"
            "This only changes what you see — the actual merge order is untouched. Click "
            "\"#\" to return to chronological order.")
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

        # ── ARCHIVAL & DELIVERY ──────────────────────────────────────────────────
        c.addWidget(self._build_archival_section())

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

        # Once the user types their own filename or browses their own folder, stop
        # auto-suggesting from the loaded source folder (see _suggest_output_paths).
        # textEdited fires only on real user keystrokes, not programmatic setText.
        self._output_user_set = False
        self._out_name.textEdited.connect(lambda *_: setattr(self, "_output_user_set", True))
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

        # What's happening right now, in plain words — a slow transcode or MD5
        # pass looks identical to a hang without this; the kind badge + step
        # text make the current activity (stream copy / transcode / merge /
        # archive / MD5 verify) visible at a glance, updated on every tick.
        step_row = QHBoxLayout()
        step_row.setSpacing(8)
        self._kind_badge = QLabel("")
        self._kind_badge.setFixedHeight(20)
        self._kind_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._step_label = QLabel("")
        step_row.addWidget(self._kind_badge)
        step_row.addWidget(self._step_label, 1)
        prog_layout.addLayout(step_row)

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

        self._gpu_check = QCheckBox("GPU encode")
        self._gpu_check.setEnabled(False)
        self._gpu_check.setToolTip("Checking for a usable GPU encoder…")
        btn_row.addWidget(self._gpu_check)

        self._show_me_btn = QPushButton("✨ Show me…")
        self._show_me_btn.setFixedHeight(36)
        self._show_me_btn.setEnabled(False)
        self._show_me_btn.setToolTip(
            "Watch a short animation of what this merge will do with YOUR clips and\n"
            "settings — which ones are copied exactly, which get converted and why,\n"
            "and where everything lands inside the finished file.")
        self._show_me_btn.clicked.connect(self._open_show_me)

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
        btn_row.addWidget(self._show_me_btn)
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

    # ── Archival & delivery section ──────────────────────────────────────────────

    def _build_archival_section(self) -> QWidget:
        """Archival master / one-track-per-clip / Optimize baseline for delivery /
        Verify MD5 recovery. The first three form a dependency chain (each needs
        the one above it); all default checked so the relationship is discoverable
        by unchecking one and watching what fades, rather than hidden until earned."""
        frame, box = self._section("ARCHIVAL & DELIVERY")

        self._archival_check = QCheckBox("Archival master")
        self._archival_check.setChecked(True)
        self._archival_check.setToolTip(
            "Also embed each odd-spec camera's ORIGINAL clips on their own lossless\n"
            "video tracks inside the master, so \"Extract and Recover\" can later recover\n"
            "the individual originals. Adds encode time + file size for the extra tracks;\n"
            "the master still plays normally (the baseline track stays the default).")
        self._archival_check.toggled.connect(self._on_archival_checkbox_touched)
        box.addWidget(self._archival_check)

        otpc_row = QHBoxLayout()
        otpc_row.addSpacing(20)
        self._per_clip_archival_check = QCheckBox("One track per clip (bit-exact)")
        # Real default is chosen per folder by _auto_select_archival_params once
        # clips are probed (off for a single uniform spec, on for varied specs);
        # this construction-time value only matters before any folder is loaded.
        self._per_clip_archival_check.setChecked(False)
        self._per_clip_archival_check.setToolTip(
            "Default: same-spec originals share one archival track (fewer tracks, but a\n"
            "clip recovered from a shared track can differ by a frame / a few audio\n"
            "priming samples at its boundary — content-complete, not bit-exact). Tick this\n"
            "to give every odd-spec clip its own track instead — more tracks, but every\n"
            "clip recovers byte-for-byte identical to its camera original.")
        self._per_clip_archival_check.toggled.connect(self._on_archival_checkbox_touched)
        otpc_row.addWidget(self._per_clip_archival_check)
        box.addLayout(otpc_row)

        optimize_row = QHBoxLayout()
        optimize_row.addSpacing(40)
        self._optimize_baseline_check = QCheckBox("Optimize baseline for delivery")
        # See _per_clip_archival_check above — auto-selected per folder.
        self._optimize_baseline_check.setChecked(False)
        self._optimize_baseline_check.setToolTip(
            "Re-encode every clip to one consistent quality target instead of copying\n"
            "already-matching clips as-is. Needs Archival master + One track per clip —\n"
            "once nothing stream-copies into the baseline, every original still needs to\n"
            "be safely preserved on its own track.")
        self._optimize_baseline_check.toggled.connect(self._on_archival_checkbox_touched)
        optimize_row.addWidget(self._optimize_baseline_check)
        box.addLayout(optimize_row)

        info_row = QHBoxLayout()
        info_row.addSpacing(60)
        self._optimize_info_btn = QPushButton()
        self._optimize_info_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._optimize_info_open = False
        self._optimize_info_btn.clicked.connect(self._toggle_optimize_info)
        info_row.addWidget(self._optimize_info_btn)
        info_row.addStretch()
        box.addLayout(info_row)

        info_body_row = QHBoxLayout()
        info_body_row.addSpacing(60)
        self._optimize_info_body = QLabel(
            "Every clip gets re-encoded to your chosen quality target below instead of "
            "copied as-is. This takes longer — turning on GPU encode (in the action bar "
            "below) cuts that difference dramatically and is worth enabling specifically "
            "for this mode. In exchange: every clip displays consistently regardless of "
            "which camera shot it, orientation issues some cameras introduce are "
            "corrected automatically, and your originals stay byte-for-byte recoverable "
            "on their own tracks — provable on demand with \"Verify MD5 recovery\" below."
        )
        self._optimize_info_body.setWordWrap(True)
        self._optimize_info_body.setVisible(False)
        info_body_row.addWidget(self._optimize_info_body, 1)
        box.addLayout(info_body_row)
        self._update_optimize_info_btn()

        quality_row = QHBoxLayout()
        quality_row.addSpacing(60)
        quality_col = QVBoxLayout()
        quality_col.setSpacing(10)
        self._quality_label = QLabel("Quality target:")
        quality_col.addWidget(self._quality_label)
        self._quality_group = QButtonGroup(self)
        self._quality_radios: dict = {}
        self._quality_descs: dict = {}
        self._quality_cards: list = []
        for key, info in QUALITY_PRESETS.items():
            # Each preset is its own bordered, padded card — not a bare row —
            # so a list of similar-height options reads as distinct choices
            # to scan, not a ruled/spreadsheet-like stack (a "cluttered" note
            # this was rebuilt directly in response to).
            recommended = key == DEFAULT_QUALITY_PRESET
            card = QFrame()
            card.setObjectName("qualityCardRecommended" if recommended else "qualityCard")
            card_lay = QVBoxLayout(card)
            card_lay.setContentsMargins(14, 10, 14, 10)
            card_lay.setSpacing(3)
            if recommended:
                badge_row = QHBoxLayout()
                badge_row.setContentsMargins(0, 0, 0, 2)
                badge = QLabel("Recommended")
                badge.setObjectName("qualityBadge")
                badge_row.addWidget(badge)
                badge_row.addStretch()
                card_lay.addLayout(badge_row)
            radio = QRadioButton(info["label"])
            radio.setChecked(recommended)
            desc = QLabel(info["description"])
            desc.setWordWrap(True)
            card_lay.addWidget(radio)
            card_lay.addWidget(desc)
            self._quality_group.addButton(radio)
            self._quality_radios[key] = radio
            self._quality_descs[key] = desc
            self._quality_cards.append(card)
            quality_col.addWidget(card)
        quality_row.addLayout(quality_col, 1)
        box.addLayout(quality_row)

        # A clear gap (not another rule/line) before the next distinct group of
        # options — archival choices above, verification below — so the section
        # reads as a few clusters rather than one long flat list of checkboxes.
        box.addSpacing(14)
        self._verify_md5_check = QCheckBox("Verify MD5 recovery")
        self._verify_md5_check.setChecked(True)
        self._verify_md5_check.setToolTip(
            "After the merge finishes, extract every clip straight back out of the\n"
            "finished master and compare it (video, audio, and WAV backup) against the\n"
            "original file using MD5 checksums of the raw compressed data — the same\n"
            "check \"Extract and Recover\" itself relies on. Writes a human-readable\n"
            "verification report next to the master. Adds time proportional to your\n"
            "footage size (every file gets read and hashed twice).")
        self._verify_md5_check.toggled.connect(self._update_archival_dependency_states)
        box.addWidget(self._verify_md5_check)

        skip_predictable_row = QHBoxLayout()
        skip_predictable_row.addSpacing(20)
        self._skip_predictable_verify_check = QCheckBox("Skip checks predicted to fail (recommended)")
        self._skip_predictable_verify_check.setChecked(True)
        self._skip_predictable_verify_check.setToolTip(
            "Some checks are known before they even run to be unable to pass, purely from\n"
            "how this merge is configured — a transcoded clip with no archival track of its\n"
            "own has nothing byte-exact left to compare against, and a clip's camera audio\n"
            "sitting mid-way in a SHARED archival track (or the baseline) can't be aligned\n"
            "for exact verification even though the footage is intact. Skipping these saves\n"
            "the time they'd otherwise spend extracting and hashing both sides just to\n"
            "confirm what's already certain — the report still lists them, clearly marked\n"
            "as predicted rather than checked. Untick to force every check to actually run\n"
            "regardless, if you want exhaustive verification anyway.")
        skip_predictable_row.addWidget(self._skip_predictable_verify_check)
        box.addLayout(skip_predictable_row)

        box.addSpacing(14)
        self._compat_baseline_check = QCheckBox("Compatible playback master")
        self._compat_baseline_check.setChecked(bool(getattr(self, "compat_baseline", False)))
        self._compat_baseline_check.setToolTip(
            "Rebuild the watchable video as one clean, continuous stream (8-bit H.264)\n"
            "instead of stream-copying the clips together end-to-end. Stream-copying\n"
            "joins independently-encoded segments without re-encoding, which breaks the\n"
            "video's internal frame references at each join — some players show green or\n"
            "garbled frames, freezes, or stutters, and every player copes differently.\n"
            "This option makes a master that plays smoothly everywhere. Your archival\n"
            "originals are unaffected. Adds re-encode time and is not bit-exact for the\n"
            "playable track (the lossless originals still are).")
        self._compat_baseline_check.toggled.connect(
            lambda on: setattr(self, "compat_baseline", bool(on)))
        box.addWidget(self._compat_baseline_check)

        self._update_archival_dependency_states()
        return frame

    def _update_archival_dependency_states(self):
        """Cascading fade: each setting needs the one above it. Disabling (not
        hiding) preserves the checked-state underneath, so re-enabling a
        prerequisite restores exactly what was chosen before, nothing resets."""
        archival_on = self._archival_check.isChecked()
        self._per_clip_archival_check.setEnabled(archival_on)

        otpc_effective = archival_on and self._per_clip_archival_check.isChecked()
        self._optimize_baseline_check.setEnabled(otpc_effective)

        optimize_effective = otpc_effective and self._optimize_baseline_check.isChecked()
        self._quality_label.setEnabled(optimize_effective)
        for key, radio in self._quality_radios.items():
            radio.setEnabled(optimize_effective)
            self._quality_descs[key].setEnabled(optimize_effective)
        for card in getattr(self, "_quality_cards", []):
            card.setEnabled(optimize_effective)

        self._skip_predictable_verify_check.setEnabled(self._verify_md5_check.isChecked())
        self._reconform_clips()

    def _on_archival_checkbox_touched(self, *_args):
        """Any real user click on one of the three archival checkboxes opts this
        folder out of auto-selection — their choice is final until a new folder
        is loaded. Programmatic changes from _auto_select_archival_params itself
        set _applying_auto_archival first, so they don't count as a "touch"."""
        if not self._applying_auto_archival:
            self._archival_user_overridden = True
        self._update_archival_dependency_states()

    def _auto_select_archival_params(self):
        """Pick sensible archival defaults for THIS folder's footage, right after
        probing settles which spec(s) it actually contains.

        One shared spec (a single camera, or several that all happen to shoot
        identically) needs nothing beyond the archival copy itself — every clip
        already stream-copies into the baseline, so there's no odd-spec original
        that per-clip tracks or a delivery re-encode would protect. Varied specs
        bring back the safety net: per-clip bit-exact tracks (every odd-spec
        original individually recoverable) plus an optimized, consistently
        re-encoded delivery baseline so playback doesn't vary by camera.

        Runs once per freshly-loaded folder; stays out of the way the moment the
        user touches a checkbox themselves (see _on_archival_checkbox_touched)."""
        if self._archival_user_overridden or not self._clips:
            return
        varied = len(self._spec_groups) > 1
        self._applying_auto_archival = True
        try:
            self._archival_check.setChecked(True)
            self._per_clip_archival_check.setChecked(varied)
            self._optimize_baseline_check.setChecked(varied)
        finally:
            self._applying_auto_archival = False
        self._update_archival_dependency_states()

    def _toggle_optimize_info(self):
        self._optimize_info_open = not self._optimize_info_open
        self._optimize_info_body.setVisible(self._optimize_info_open)
        self._update_optimize_info_btn()

    def _update_optimize_info_btn(self):
        chevron = "▾" if self._optimize_info_open else "▸"
        self._optimize_info_btn.setText(f"{chevron}  What does this change?")

    def _selected_quality_preset(self) -> str:
        for key, radio in self._quality_radios.items():
            if radio.isChecked():
                return key
        return DEFAULT_QUALITY_PRESET

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
        item = self._table.currentItem()
        if item is None or item.parent() is None:   # nothing selected, or a camera-group header
            return None
        idx = item.data(COL_NAME, Qt.ItemDataRole.UserRole)
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
        dlg = AdvancedSyncDialog(clip, self, on_reassign_wav=self._open_wav_swap_dialog)
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
            # Muted, not accent — keeps accent reserved for interactive/active
            # elements (matches the same fix in review_tab.py's _restyle).
            t.setStyleSheet(f"color:{p.text_mute}; font-size:10px; font-weight:bold; letter-spacing:1px;")
        if hasattr(self, "_audio_summary"):
            self._audio_summary.setStyleSheet(f"color:{p.text_dim}; font-size:11px;")
        if hasattr(self, "_optimize_info_btn"):
            self._optimize_info_btn.setStyleSheet(
                f"QPushButton {{ background:transparent; color:{p.text_mute}; border:none; "
                "font-size:11px; text-align:left; padding:2px 0; }"
                f"QPushButton:hover {{ color:{p.accent}; }}")
            self._optimize_info_body.setStyleSheet(f"color:{p.text_dim}; font-size:11px; padding-top:2px;")
            self._quality_label.setStyleSheet(f"color:{p.text_mute}; font-size:11px; font-weight:bold;")
            for radio in self._quality_radios.values():
                radio.setStyleSheet(f"QRadioButton {{ color:{p.text}; font-size:12.5px; font-weight:bold; }}")
            for desc in self._quality_descs.values():
                desc.setStyleSheet(f"color:{p.text_dim}; font-size:11px;")
            for card in getattr(self, "_quality_cards", []):
                recommended = card.objectName() == "qualityCardRecommended"
                border = f"2px solid {p.accent}" if recommended else f"1px solid {p.border}"
                card.setStyleSheet(
                    f"QFrame#{card.objectName()} {{ background:{p.surface2}; "
                    f"border:{border}; border-radius:8px; }}")
            badge = self.findChild(QLabel, "qualityBadge")
            if badge is not None:
                badge.setStyleSheet(
                    f"QLabel#qualityBadge {{ background:{p.accent}; color:{p.bg}; "
                    "font-size:10px; font-weight:bold; padding:2px 8px; border-radius:8px; }")
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
        self._fill_label.setStyleSheet(f"color:{p.text_dim}; font-size:11px;")
        for btn, _g in getattr(self, "_baseline_buttons", []):
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
        cam_label = ("Camera audio (AAC)" if any_wav
                     else "Camera audio (on-board mic)")
        self._suppress_combo = True
        self._track_combo.setItemText(0, cam_label)
        self._suppress_combo = False

    # ── Baseline spec chooser ─────────────────────────────────────────────────

    def _collect_clip_specs(self) -> list:
        specs = []
        for c in self._clips:
            st = c.stream
            if st and st.width and st.height:
                specs.append(ClipSpec(st.codec, st.width, st.height, st.fps_str, st.pix_fmt,
                                      pix_fmt_info(st.pix_fmt)[0], st.color_space, st.duration))
        return specs

    def _build_baseline_chooser(self):
        """After probing, list the distinct specs as selectable baselines with the
        recommended one pre-picked. Selecting one reclassifies every clip against
        it (matching clips → stream copy, the rest → transcode)."""
        while self._res_btn_row.count():
            it = self._res_btn_row.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        self._baseline_buttons = []
        self._spec_groups = enumerate_specs(self._collect_clip_specs())

        if len(self._spec_groups) <= 1:
            # One spec (or none): baseline is simply that spec — everything
            # stream-copies. Hide the chooser but still set the baseline.
            self._res_banner.hide()
            if self._spec_groups:
                self._on_baseline_chosen(self._spec_groups[0])
            else:
                self._chosen_group = None
            self._auto_select_archival_params()
            return

        rec = recommend_baseline(self._spec_groups)
        self._res_label.setText(
            "Baseline spec — every clip conforms to this; clips that already match are "
            "copied losslessly and the rest are transcoded to it. Odd-spec originals are "
            "preserved on their own archival tracks (with “Archival master” on).")
        for g in self._spec_groups:
            btn = QPushButton(g.label() + ("   ★ recommended" if g is rec else ""))
            btn.setCheckable(True)
            btn.clicked.connect(lambda checked, gg=g: self._on_baseline_chosen(gg))
            self._res_btn_row.addWidget(btn)
            self._baseline_buttons.append((btn, g))
        self._res_btn_row.addStretch()
        self._res_banner.show()
        self._on_baseline_chosen(rec)
        self._restyle()   # style the freshly-created buttons
        self._auto_select_archival_params()

    def _on_baseline_chosen(self, group):
        self._chosen_group = group
        for btn, g in self._baseline_buttons:
            btn.setChecked(g is group)
        self._reconform_clips()

    def _effective_optimize_baseline(self) -> bool:
        """True only once the whole dependency chain is actually satisfied —
        Optimize baseline needs One-track-per-clip, which needs Archival master,
        so every clip has a safe original before nothing stream-copies anymore."""
        return (self._archival_check.isChecked() and self._per_clip_archival_check.isChecked()
                and self._optimize_baseline_check.isChecked())

    def _reconform_clips(self):
        """Recompute every clip's conform status against the chosen baseline; if
        Optimize baseline for delivery is fully active, force every clip to
        transcode regardless of match, so nothing stream-copies into the shared
        baseline (the root cause of task 74's rotation-loss bug — a clip that
        LOOKS like it matches the baseline can still be silently mis-recovered
        if it shares that concatenated track with other clips)."""
        if self._chosen_group is None:
            return
        g = self._chosen_group
        bspec = BaselineSpec(codec=g.codec, width=g.width, height=g.height,
                             fps_float=_fps_to_float(g.fps),
                             pix_fmt=g.pix_fmt, color_space=g.color_space or "bt709")
        force_transcode = self._effective_optimize_baseline()
        for c in self._clips:
            if not c.stream:
                continue
            apply_conformance(c.stream, bspec)
            if force_transcode and c.stream.status == "ok":
                c.stream.status = "transcode"
                c.stream.conflicts = list(c.stream.conflicts) + ["optimized for delivery"]
        self._populate_table()
        self._update_estimate()

    def _current_conform(self) -> ConformSpec:
        g = self._chosen_group
        hw = "auto" if (self._gpu_check.isEnabled() and self._gpu_check.isChecked()) else "off"
        codec = g.codec if g is not None else DEFAULT_CONFORM.codec
        quality = (quality_for_preset(self._selected_quality_preset(), codec)
                  if self._effective_optimize_baseline() else 18)
        if g is None:
            return replace(DEFAULT_CONFORM, hw_encoder=hw, quality=quality)
        fill = "blur" if self._fill_combo.currentIndex() == 1 else "black"
        return ConformSpec(width=g.width, height=g.height, fps=_fps_to_ffmpeg(g.fps),
                           codec=g.codec, pix_fmt=g.pix_fmt,
                           color_space=g.color_space or "bt709", fill=fill,
                           hw_encoder=hw, quality=quality)

    def _start_gpu_probe(self):
        ff, _ = get_ffmpeg()
        self._gpu_probe_thread = _GpuProbeThread(ff)
        self._gpu_probe_thread.detected.connect(self._on_gpu_detected)
        self._gpu_probe_thread.start()

    def _on_gpu_detected(self, vendors: list):
        self._gpu_vendors = vendors
        if vendors:
            self._gpu_check.setEnabled(True)
            self._gpu_check.setChecked(True)
            self._gpu_check.setToolTip(
                f"Detected working GPU encoder: {vendors[0].upper()}. Transcodes non-conforming "
                "clips on the GPU (faster, frees up the CPU) instead of libx264/libx265.\n"
                "If a GPU encode fails partway (driver hiccup, VRAM exhausted, etc.), that one "
                "clip automatically retries in software rather than failing the whole merge.")
        else:
            self._gpu_check.setEnabled(False)
            self._gpu_check.setChecked(False)
            self._gpu_check.setToolTip(
                "No working GPU encoder (NVENC/QSV/AMF) was detected on this machine — "
                "transcoding will use the CPU (libx264/libx265).")

    # ── Transcode estimate ────────────────────────────────────────────────────

    def _selected_clips(self) -> list:
        """Clips ticked for inclusion — unticked ones are excluded from the
        merge (faded in the table) without being removed from the list."""
        return [c for c in self._clips if c.selected]

    def _update_estimate(self):
        """Show a rough best/worst-case transcode time estimate."""
        if not self._clips:
            self._estimate_label.hide()
            return
        total_secs = sum(c.duration for c in self._selected_clips() if c.duration > 0)
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

    # ── Drag-and-drop a folder onto the tab ───────────────────────────────────

    def _dropped_folder(self, event) -> Optional[Path]:
        if not event.mimeData().hasUrls():
            return None
        for u in event.mimeData().urls():
            p = Path(u.toLocalFile())
            if p.is_dir():
                return p
            if p.is_file():           # a file was dropped — use its containing folder
                return p.parent
        return None

    def dragEnterEvent(self, event):
        if self._dropped_folder(event) is not None:
            event.acceptProposedAction()

    def dropEvent(self, event):
        folder = self._dropped_folder(event)
        if folder is not None:
            self._settings.set("last_merge_source", str(folder))
            self._folder_edit.setText(str(folder))
            self._load_folder(folder)
            event.acceptProposedAction()

    def _load_folder(self, folder: Path):
        self._source_folder = folder
        self._view_sort_col = None
        self._table.header().setSortIndicatorShown(False)
        self._archival_user_overridden = False   # fresh folder: auto-select again
        self._clips = scan_folder(folder)
        if not self._clips:
            self._table.clear()
            self._start_btn.setEnabled(False)
            self._preflight_btn.setEnabled(False)
            self._show_me_btn.setEnabled(False)
            self._set_loaded(False)
            QMessageBox.information(
                self, "No clips found",
                "No MP4 clips were found in that folder. Choose a folder that "
                "contains your camera clips (and their WAV backups).")
            return

        self._set_loaded(True)
        self._suggest_output_paths(folder)
        self._clips_title.setText(f"CLIPS  ·  {len(self._clips)} found")
        self._refresh_unmatched_banner()
        self._dst_banner.setVisible(check_dst_warning(self._clips))
        self._pending_camera_naming_prompt = True   # ask once camera groups are known (post-probe)
        self._populate_table()   # show clips immediately, even before probing fills them in
        self._start_probe()      # kick off ffprobe — was previously only triggered from the
                                  # WAV-assign dialog, so a folder with no unmatched WAVs never
                                  # probed at all (empty table, "unknown" camera, no duration/status)

    def _refresh_unmatched_banner(self):
        if not self._source_folder:
            self._unmatched_row.hide()
            return
        orphans = unpaired_wavs(self._source_folder, self._clips)
        if orphans:
            names = ", ".join(w.name for w in orphans[:4])
            extra = f" (+{len(orphans)-4} more)" if len(orphans) > 4 else ""
            self._unmatched_banner.setText(f"ℹ  Unmatched WAV files (not used): {names}{extra}")
            self._unmatched_row.show()
        else:
            self._unmatched_row.hide()

    def _open_wav_assign_dialog(self):
        if not self._source_folder:
            return
        orphans = unpaired_wavs(self._source_folder, self._clips)
        if not orphans:
            return
        dlg = _WavAssignDialog(orphans, sorted(self._clips, key=lambda c: c.order_idx), self)
        if dlg.exec():
            _, fp = get_ffmpeg()
            for wav_path, clip in dlg.assignments().items():
                clip.wav_path = wav_path
                clip.wav_offset = 0.0
                clip.wav_duration = probe_duration(fp, str(wav_path))
                clip.sync_done = False   # re-run sync analysis against the newly paired WAV
            self._refresh_unmatched_banner()
            self._populate_table()
            self._update_estimate()

        self._dst_banner.setVisible(check_dst_warning(self._clips))
        self._update_primary_labels()
        self._populate_table()
        self._start_probe()

    def _start_probe(self):
        if self._probe_thread and self._probe_thread.isRunning():
            return
        settle(self._probe_thread)
        self._probed_count = 0
        self._probe_progress_bar.setValue(0)
        self._probe_progress_label.setText(f"0 / {len(self._clips)}")
        self._probe_progress_row.setVisible(bool(self._clips))
        _, fp = get_ffmpeg()
        self._probe_thread = ProbeThread(self._clips, fp)
        self._probe_thread.clip_probed.connect(self._on_clip_probed)
        self._probe_thread.finished.connect(self._on_probe_done)
        self._probe_thread.start()

    def _on_clip_probed(self, idx: int, info):
        self._probed_count += 1
        total = max(1, len(self._clips))
        self._probe_progress_bar.setValue(int(100 * self._probed_count / total))
        self._probe_progress_label.setText(f"{self._probed_count} / {len(self._clips)}")
        if idx >= len(self._clips):
            return
        clip = self._clips[idx]
        item = self._find_clip_item(idx)
        if item is not None:
            self._update_clip_item(item, clip)

    def _on_probe_done(self):
        self._probe_progress_row.hide()
        self._start_btn.setEnabled(bool(self._clips))
        self._preflight_btn.setEnabled(bool(self._clips))
        self._show_me_btn.setEnabled(bool(self._clips))
        has_square = any(
            c.stream and c.stream.width == c.stream.height for c in self._clips
        )
        self._square_label.setVisible(has_square)
        self._square_combo.setVisible(has_square)
        assign_cameras(self._clips, self._settings.get("camera_labels", {}))  # camera_id/label, remembered names win
        order_clips_by_time(self._clips)      # chronological order now that creation_time is known
        self._build_baseline_chooser()        # (also reclassifies + repopulates the table)
        self._refresh_sync_cells()   # slow-mo offset/drift hints now that WAV durs are known
        self._maybe_prompt_camera_naming()

    def _maybe_prompt_camera_naming(self):
        """Once, right after a fresh folder's clips are probed and grouped by
        camera, offer to confirm/rename each detected camera up front —
        previously only discoverable by double-clicking a group header after
        the fact. Cameras already named in a past session (remembered via
        Settings' camera_labels) are recognized automatically and skipped, so
        the dialog only ever asks about genuinely new cameras."""
        if not self._pending_camera_naming_prompt:
            return
        self._pending_camera_naming_prompt = False
        groups = group_clips_by_camera(self._clips)
        if not groups:
            return
        saved_labels = self._settings.get("camera_labels", {})
        ordered_ids = sorted(groups, key=lambda gid: min(c.order_idx for c in groups[gid]))
        new_ids = [gid for gid in ordered_ids if gid not in saved_labels]
        if not new_ids:
            return   # every detected camera is already known from a previous folder
        rows = [(gid, groups[gid][0].camera_label or gid, len(groups[gid])) for gid in new_ids]
        dlg = _CameraNamingDialog(rows, self)
        if dlg.exec():
            labels = dlg.labels()
            for clip in self._clips:
                if clip.camera_id in labels:
                    clip.camera_label = labels[clip.camera_id]
            for camera_id, label in labels.items():
                self._remember_camera_label(camera_id, label)
            self._populate_table()

    def _remember_camera_label(self, camera_id: str, label: str):
        """Persist a camera_id -> label mapping so future folder loads recognize it."""
        if not camera_id or not label:
            return
        saved = dict(self._settings.get("camera_labels", {}))
        if saved.get(camera_id) == label:
            return
        saved[camera_id] = label
        self._settings.set("camera_labels", saved)

    def _open_show_me(self):
        """The 'Show me' animation — a friendly, moving picture of what THIS
        merge will do: the user's actual clips, judged against their actual
        parameters, flying into the container they'll actually produce."""
        clips = self._selected_clips()
        if not clips:
            return
        from show_me import build_story, ShowMeDialog
        story = build_story(
            clips,
            archival=self._archival_check.isChecked(),
            per_clip_archival=self._per_clip_archival_check.isChecked(),
            optimize_baseline=self._effective_optimize_baseline(),
            compat_baseline=bool(getattr(self, "compat_baseline", False)),
            audio_tracks=[t.kind for t in self._effective_plan().tracks if t.enabled],
            output_name=self._out_name.text().strip() or "master.mov",
        )
        ShowMeDialog(story, self).exec()

    def _open_preflight(self):
        clips = self._selected_clips()
        if not clips:
            return
        import shutil
        from core.plan_report import analyze_merge
        from preflight_dialog import PreflightDialog
        from show_me import build_story
        report = analyze_merge(clips, self._effective_plan())
        story = build_story(
            clips,
            archival=self._archival_check.isChecked(),
            per_clip_archival=self._per_clip_archival_check.isChecked(),
            optimize_baseline=self._effective_optimize_baseline(),
            compat_baseline=bool(getattr(self, "compat_baseline", False)),
            audio_tracks=[t.kind for t in self._effective_plan().tracks if t.enabled],
            output_name=self._out_name.text().strip() or "master.mov",
        )
        free = None
        out_dir = self._out_dir.text().strip()
        if out_dir:
            try:
                free = shutil.disk_usage(out_dir).free
            except Exception:
                free = None
        dlg = PreflightDialog(report, self, free_bytes=free,
                              need_bytes=self._estimated_need_bytes(), story=story)
        dlg.start_requested.connect(self._start_merge)
        dlg.exec()

    # ── Table (grouped by camera) ────────────────────────────────────────────

    def _on_header_clicked(self, col: int):
        """Click Timestamp/Duration/Camera to view-sort; click again to reverse;
        click "#" to return to the default chronological view. Purely a display
        reorder — clip.order_idx (the actual merge sequence) is never touched,
        so this is safe to use after manually reordering clips too."""
        if col == COL_ORDER:
            if self._view_sort_col is None:
                return
            self._view_sort_col = None
            self._table.header().setSortIndicatorShown(False)
            self._populate_table()
            return
        if col not in _SORTABLE_COLS:
            return
        if self._view_sort_col == col:
            self._view_sort_asc = not self._view_sort_asc
        else:
            self._view_sort_col = col
            self._view_sort_asc = True
        self._table.header().setSortIndicatorShown(True)
        self._table.header().setSortIndicator(
            col, Qt.SortOrder.AscendingOrder if self._view_sort_asc else Qt.SortOrder.DescendingOrder)
        self._populate_table()

    def _populate_table(self):
        """Rebuild the tree: one top-level item per camera, clips nested
        underneath. Default order is chronological (groups by their earliest
        clip, clips by order_idx — the actual merge sequence); a view-sort
        (see `_on_header_clicked`) instead orders groups by camera label or
        clips by duration/timestamp, purely for display. Preserves each
        group's expand/collapse state."""
        expanded = {self._table.topLevelItem(i).data(COL_NAME, Qt.ItemDataRole.UserRole): self._table.topLevelItem(i).isExpanded()
                   for i in range(self._table.topLevelItemCount())}
        self._table.blockSignals(True)   # avoid itemChanged firing while we rebuild
        self._table.clear()

        self._clip_split_suggestions = [
            (a, b) for (a, b) in detect_clip_splits(self._clips)
            if (a.path, b.path) not in self._dismissed_split_pairs
        ]

        groups = group_clips_by_camera(self._clips)   # {camera_id: [clips]}, first-seen order
        if self._view_sort_col == COL_CAM:
            ordered_ids = sorted(groups, key=lambda gid: (groups[gid][0].camera_label or gid).lower(),
                                 reverse=not self._view_sort_asc)
        else:
            # Order groups by their earliest clip's chronological position.
            ordered_ids = sorted(groups, key=lambda gid: min(c.order_idx for c in groups[gid]))

        sort_active = self._view_sort_col is not None
        for gid in ordered_ids:
            members = sorted(groups[gid], key=lambda c: c.order_idx)
            if self._view_sort_col == COL_DUR:
                members.sort(key=lambda c: c.duration, reverse=not self._view_sort_asc)
            elif self._view_sort_col == COL_TIME:
                members.sort(key=_clip_time_sort_key, reverse=not self._view_sort_asc)
            label = groups[gid][0].camera_label or gid
            group_item = self._add_camera_group(gid, label, len(members))
            for clip in members:
                self._add_clip_row(group_item, clip, reorder_enabled=not sort_active)
                for pair_a, pair_b in self._clip_split_suggestions:
                    if pair_a is clip:
                        self._add_split_banner_row(group_item, pair_a, pair_b)
            group_item.setExpanded(expanded.get(gid, True))

        self._table.blockSignals(False)

    def _add_camera_group(self, camera_id: str, label: str, count: int) -> QTreeWidgetItem:
        p = theme.active_palette()
        item = QTreeWidgetItem(self._table)
        item.setText(COL_NAME, label)
        item.setData(COL_NAME, Qt.ItemDataRole.UserRole, camera_id)
        item.setText(COL_CAM, f"{count} clip{'s' if count != 1 else ''}")
        item.setToolTip(COL_NAME, "Double-click to rename this camera")
        item.setFlags((item.flags() | Qt.ItemFlag.ItemIsEditable | Qt.ItemFlag.ItemIsDropEnabled)
                      & ~Qt.ItemFlag.ItemIsDragEnabled)
        font = QFont("", -1, QFont.Weight.Bold)
        for col in range(N_COLS):
            item.setFont(col, font)
            item.setForeground(col, QColor(p.accent))
        return item

    def _add_clip_row(self, group_item: QTreeWidgetItem, clip: ClipInfo, reorder_enabled: bool = True):
        p = theme.active_palette()
        item = QTreeWidgetItem(group_item)
        item.setFlags((item.flags() | Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsUserCheckable)
                      & ~(Qt.ItemFlag.ItemIsDropEnabled | Qt.ItemFlag.ItemIsEditable))
        item.setCheckState(COL_NAME, Qt.CheckState.Checked if clip.selected else Qt.CheckState.Unchecked)

        item.setText(COL_ORDER, str(clip.order_idx + 1))
        item.setTextAlignment(COL_ORDER, Qt.AlignmentFlag.AlignCenter)
        if clip.manually_moved:
            item.setForeground(COL_ORDER, QColor(p.accent))
            item.setFont(COL_ORDER, QFont("", -1, QFont.Weight.Bold))

        item.setText(COL_NAME, clip.stem)
        item.setData(COL_NAME, Qt.ItemDataRole.UserRole, self._clips.index(clip))

        icon_style = (
            f"QPushButton {{ background:{p.btn_bg}; color:{p.text}; border:1px solid {p.border}; "
            "border-radius:4px; padding:0px; font-size:14px; }"
            f"QPushButton:hover {{ border-color:{p.accent}; color:{p.accent}; }}")
        preview_btn = QPushButton("▶")
        preview_btn.setFixedSize(24, 24)
        preview_btn.setStyleSheet(icon_style)
        preview_btn.setToolTip("Play a quick low-res preview, starting from the middle of the clip")
        preview_btn.setEnabled(clip.duration > 0)
        preview_btn.clicked.connect(lambda _, c=clip, b=preview_btn: self._on_preview_clicked(c, b))
        self._table.setItemWidget(item, COL_PREVIEW, preview_btn)

        time_text, time_differs, time_reason = _fmt_timestamp_cell(clip)
        item.setText(COL_TIME, time_text)
        item.setTextAlignment(COL_TIME, Qt.AlignmentFlag.AlignCenter)
        if time_differs:
            item.setForeground(COL_TIME, QColor(p.warn))
            item.setToolTip(COL_TIME, time_reason)

        item.setText(COL_DUR, _fmt_dur(clip.duration))
        item.setTextAlignment(COL_DUR, Qt.AlignmentFlag.AlignCenter)

        item.setText(COL_WAV, "✓" if clip.has_wav() else "—")
        item.setTextAlignment(COL_WAV, Qt.AlignmentFlag.AlignCenter)
        if not clip.has_wav():
            item.setForeground(COL_WAV, QColor(p.text_dim))

        item.setText(COL_WAV_DUR, _fmt_dur(clip.wav_duration) if clip.has_wav() else "—")
        item.setTextAlignment(COL_WAV_DUR, Qt.AlignmentFlag.AlignCenter)
        if not clip.has_wav():
            item.setForeground(COL_WAV_DUR, QColor(p.text_dim))

        self._table.setItemWidget(item, COL_PRIMARY, self._build_primary_combo(clip))

        item.setText(COL_OFFSET, _fmt_offset(clip))
        item.setTextAlignment(COL_OFFSET, Qt.AlignmentFlag.AlignCenter)
        item.setText(COL_DRIFT, _fmt_drift(clip))
        item.setTextAlignment(COL_DRIFT, Qt.AlignmentFlag.AlignCenter)

        self._update_status_cell(item, clip)

        for col, delta in ((COL_UP, -1), (COL_DOWN, +1)):
            sym = "↑" if delta == -1 else "↓"
            btn = QPushButton(sym)
            btn.setFixedSize(28, 28)
            btn.setStyleSheet(icon_style)            # padding:0 so the arrow isn't clipped
            btn.setEnabled(reorder_enabled)
            if not reorder_enabled:
                btn.setToolTip('Reordering is disabled while the view is sorted — click the '
                              '"#" column header to return to chronological order first.')
            btn.clicked.connect(lambda _, c=clip, d=delta: self._move_clip(c, d))
            self._table.setItemWidget(item, col, btn)
        self._apply_selected_fade(item, clip)
        return item

    def _add_split_banner_row(self, group_item: QTreeWidgetItem, clip_a: ClipInfo, clip_b: ClipInfo):
        """Inline suggestion row right under clip_a — a camera file-split
        detected by clip_model.detect_clip_splits. Spans the whole row (no
        checkbox, not selectable/draggable) so it reads as a notice rather
        than another clip."""
        p = theme.active_palette()
        item = QTreeWidgetItem(group_item)
        item.setFlags(item.flags() & ~(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsDragEnabled
                                       | Qt.ItemFlag.ItemIsSelectable))
        item.setFirstColumnSpanned(True)

        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(28, 3, 8, 3)
        lay.setSpacing(8)
        icon = QLabel("⚠")
        icon.setStyleSheet(f"color:{p.accent}; font-size:13px;")
        lay.addWidget(icon)
        text = QLabel(f"{clip_a.stem}’s WAV may also cover {clip_b.stem} — "
                      "one continuous recording, split by the camera")
        text.setStyleSheet(f"color:{p.text_mute}; font-size:11px;")
        lay.addWidget(text, 1)
        btn = QPushButton("Review & resolve…")
        btn.setStyleSheet(
            f"QPushButton {{ background:{p.accent}; color:{p.on_accent()}; border:none; "
            "border-radius:5px; padding:3px 12px; font-size:11px; font-weight:bold; }"
            f"QPushButton:hover {{ background:{p.accent_hi}; }}")
        btn.clicked.connect(lambda _, a=clip_a, b=clip_b: self._open_clip_split_dialog(a, b))
        lay.addWidget(btn)
        self._table.setItemWidget(item, COL_ORDER, w)

    def _open_clip_split_dialog(self, clip_a: ClipInfo, clip_b: ClipInfo):
        dlg = _ClipSplitDialog(clip_a, clip_b, self)
        if not dlg.exec():
            return
        resolution = dlg.resolution()
        if resolution == "split":
            self._resolve_clip_split(clip_a, clip_b)
        elif resolution == "dismiss":
            self._dismissed_split_pairs.add((clip_a.path, clip_b.path))
            self._populate_table()
        # "leave" — close with no change; the banner reappears on the next
        # repopulate since nothing about the underlying clips changed.

    def _resolve_clip_split(self, clip_a: ClipInfo, clip_b: ClipInfo):
        """Trim clip_a's WAV at its own video's duration and hand the
        remainder to clip_b as its own WAV file — a physical, on-disk split
        (not a shared-file offset model), so every existing WAV mechanism
        (sync analysis, verify, extract) keeps working on clip_b completely
        unchanged, exactly as if it always had its own separate recording."""
        if clip_a.wav_path is None:
            return
        ff, fp = get_ffmpeg()
        split_point = max(0.0, clip_a.duration)
        out_path = clip_a.wav_path.parent / f"{clip_b.stem}_backup.wav"
        cmd = [ff, "-y", "-v", "error", "-ss", f"{split_point:.3f}",
              "-i", str(clip_a.wav_path), "-c", "copy", str(out_path)]
        kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=120, **kwargs)
            ok = r.returncode == 0 and out_path.exists()
        except Exception:
            ok = False
        if not ok:
            QMessageBox.warning(self, "Couldn't split WAV",
                               f"Failed to create {out_path.name} from {clip_a.wav_path.name}.")
            return
        clip_b.wav_path = out_path
        clip_b.wav_offset = 0.0
        clip_b.sync_done = False
        clip_b.wav_duration = probe_duration(fp, str(out_path))
        self._refresh_unmatched_banner()
        self._populate_table()
        self._update_estimate()

    def _build_primary_combo(self, clip: ClipInfo) -> QComboBox:
        """The per-clip Primary override combo (COL_PRIMARY) — only options
        this clip can actually satisfy are offered (see
        _valid_primary_options); "Auto" defers to the global Camera/WAV
        choice plus core.ffmpeg_cmd's own no-source fallback."""
        combo = QComboBox()
        for key, label in _valid_primary_options(clip):
            combo.addItem(label, key)
        current = clip.primary_override or "auto"
        i = combo.findData(current)
        combo.setCurrentIndex(i if i >= 0 else 0)
        combo.currentIndexChanged.connect(
            lambda _, c=clip, cb=combo: self._on_primary_override_changed(c, cb))
        self._style_primary_combo(combo, current)
        return combo

    def _style_primary_combo(self, combo: QComboBox, value: str):
        """Manually-overridden rows get an accent border + bold text — the
        same "this isn't the default behavior" visual cue used elsewhere
        (e.g. manually_moved rows in COL_ORDER)."""
        p = theme.active_palette()
        overridden = value not in (None, "auto")
        border = p.accent if overridden else p.border
        combo.setStyleSheet(
            f"QComboBox {{ border:1px solid {border}; border-radius:4px; padding:1px 4px; "
            f"color:{p.accent if overridden else p.text}; "
            f"font-weight:{'bold' if overridden else 'normal'}; background:{p.surface2}; }}")

    def _on_primary_override_changed(self, clip: ClipInfo, combo: QComboBox):
        value = combo.currentData()
        clip.primary_override = None if value == "auto" else value
        self._style_primary_combo(combo, value)
        self._update_estimate()

    def _preview_accel(self) -> dict:
        """Read the (experimental) Developer-panel preview options into the kwargs
        build_clip_sample_cmd expects. GPU encode resolves to a concrete vendor
        only if one actually works on this machine (else None → software), so an
        unusable GPU can never wedge the preview."""
        s = self._settings
        accel = {
            "hw_decode": bool(s.get("dev_preview_hw_decode", False)),
            "fast": bool(s.get("dev_preview_fast_sample", False)),
            "height": int(s.get("dev_preview_height", 160) or 160),
        }
        if s.get("dev_preview_gpu_encode", False):
            try:
                from core.gpu_encode import detect_best_hw
                ff, _ = get_ffmpeg()
                accel["gpu_vendor"] = detect_best_hw(ff, "h264")
            except Exception:
                accel["gpu_vendor"] = None
        return accel

    def _accel_sig(self, accel: dict) -> str:
        """A short signature so previews cached under one set of Developer options
        aren't reused after those options change."""
        return (f"g{accel.get('gpu_vendor') or '-'}"
                f"_d{int(accel.get('hw_decode', False))}"
                f"_f{int(accel.get('fast', False))}"
                f"_h{int(accel.get('height', 160))}")

    def _on_preview_clicked(self, clip: ClipInfo, btn: QPushButton):
        accel = self._preview_accel()
        cache_key = f"{clip.path}|{self._accel_sig(accel)}"
        cached = self._preview_cache.get(cache_key)
        if cached and Path(cached).exists():
            self._show_preview_dialog(cached, clip.stem)
            return
        if clip.duration <= 0:
            return
        btn.setEnabled(False)
        btn.setText("…")
        start_ts = clip.duration / 2.0                       # "middle of the clip"
        cap = 2.0 if accel.get("fast") else 5.0
        sample_dur = min(cap, max(0.5, clip.duration - start_ts))
        ff, _ = get_ffmpeg()
        out_dir = get_app_dir() / "_temp" / "merge_previews"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"preview_{abs(hash(cache_key))}.mp4"
        idx = self._clips.index(clip)
        thread = _ClipSampleThread(ff, idx, str(clip.path), start_ts, sample_dur, out_path, accel)
        thread.done.connect(lambda ci, path, err, c=clip, b=btn, k=cache_key:
                            self._on_preview_sample_done(c, b, path, err, k))
        thread.finished.connect(lambda t=thread: t in self._preview_threads and self._preview_threads.remove(t))
        self._preview_threads.append(thread)
        thread.start()

    def _on_preview_sample_done(self, clip: ClipInfo, btn: QPushButton, path: str,
                                err: str, cache_key: str):
        btn.setEnabled(True)
        btn.setText("▶")
        if err:
            QMessageBox.warning(self, "Preview failed",
                                f"Couldn't generate a preview for {clip.stem}:\n{err}")
            return
        self._preview_cache[cache_key] = path
        self._show_preview_dialog(path, clip.stem)

    def _show_preview_dialog(self, sample_path: str, title: str):
        s = self._settings
        size = _PREVIEW_WINDOW_SIZES.get(s.get("dev_preview_window_size", "medium"),
                                         _PREVIEW_WINDOW_SIZES["medium"])
        dlg = _ClipPreviewDialog(
            sample_path, title, self,
            window_size=size,
            aspect_mode=s.get("dev_preview_aspect_mode", "fit"),
            loop=bool(s.get("dev_preview_loop", True)),
            speed=float(s.get("dev_preview_speed", 1.0) or 1.0))
        dlg.finished.connect(lambda _, d=dlg: d in self._preview_dialogs and self._preview_dialogs.remove(d))
        self._preview_dialogs.append(dlg)
        dlg.show()

    def _apply_selected_fade(self, item: QTreeWidgetItem, clip: ClipInfo):
        """Unticked clips fade — the visual cue that they're excluded from the
        merge without being removed from the list (e.g. a bad take you don't
        want to move out of the source folder)."""
        p = theme.active_palette()
        color = QColor(p.text_dim) if not clip.selected else None
        for col in (COL_NAME, COL_TIME, COL_CAM, COL_DUR, COL_WAV, COL_WAV_DUR, COL_STATUS):
            if color is not None and col != COL_TIME:   # keep the timestamp-differs warning colour
                item.setForeground(col, color)
            elif color is None:
                # restore defaults that _add_clip_row would have set
                if col in (COL_WAV, COL_WAV_DUR) and not clip.has_wav():
                    item.setForeground(col, QColor(p.text_dim))
                elif col != COL_TIME or not _fmt_timestamp_cell(clip)[1]:
                    item.setForeground(col, QColor(p.text))

    def _find_clip_item(self, clip_idx: int) -> Optional[QTreeWidgetItem]:
        for gi in range(self._table.topLevelItemCount()):
            group = self._table.topLevelItem(gi)
            for ci in range(group.childCount()):
                child = group.child(ci)
                if child.data(COL_NAME, Qt.ItemDataRole.UserRole) == clip_idx:
                    return child
        return None

    def _all_clip_items(self):
        for gi in range(self._table.topLevelItemCount()):
            group = self._table.topLevelItem(gi)
            for ci in range(group.childCount()):
                yield group.child(ci)

    def _update_clip_item(self, item: QTreeWidgetItem, clip: ClipInfo):
        if clip.stream:
            item.setText(COL_DUR, _fmt_dur(clip.duration))
            preview_btn = self._table.itemWidget(item, COL_PREVIEW)
            if isinstance(preview_btn, QPushButton) and clip.duration > 0:
                preview_btn.setEnabled(True)
        item.setText(COL_OFFSET, _fmt_offset(clip))
        item.setText(COL_DRIFT, _fmt_drift(clip))
        self._update_status_cell(item, clip)

    def _refresh_sync_cells(self):
        """Update the WAV Offset / Drift columns for all clip rows after analysis."""
        for item in self._all_clip_items():
            idx = item.data(COL_NAME, Qt.ItemDataRole.UserRole)
            if idx is None or idx >= len(self._clips):
                continue
            clip = self._clips[idx]
            item.setText(COL_OFFSET, _fmt_offset(clip))
            item.setText(COL_DRIFT, _fmt_drift(clip))

    def _update_status_cell(self, item: QTreeWidgetItem, clip: ClipInfo):
        w = QWidget()
        lay = QHBoxLayout(w)
        lay.setContentsMargins(4, 2, 4, 2)
        btn = _make_status_button(clip)
        btn.clicked.connect(lambda _, c=clip: self._open_clip_video_options_dialog(c))
        lay.addWidget(btn)
        self._table.setItemWidget(item, COL_STATUS, w)

    def _open_clip_video_options_dialog(self, clip: ClipInfo):
        dlg = _ClipVideoOptionsDialog(clip, self)
        if not dlg.exec():
            return
        clip.video_source_override = dlg.video_source_override()
        clip.preserve_lrv = dlg.preserve_lrv()
        self._populate_table()
        self._update_estimate()

    # ── Camera-group editing / reassignment ──────────────────────────────────

    def _on_tree_item_double_clicked(self, item: QTreeWidgetItem, column: int):
        if item.parent() is None and column == COL_NAME:   # a camera-group header
            self._table.editItem(item, COL_NAME)
        elif item.parent() is not None and column == COL_WAV:   # a clip's WAV cell
            idx = item.data(COL_NAME, Qt.ItemDataRole.UserRole)
            if idx is not None and 0 <= idx < len(self._clips):
                self._open_wav_swap_dialog(self._clips[idx])

    def _open_wav_swap_dialog(self, clip: ClipInfo):
        """Double-clicking a clip's WAV cell — swap or clear which WAV file
        it's paired with, from every WAV in the source folder (not just the
        currently-unmatched ones), so a wrongly-auto-paired WAV can be
        corrected too."""
        if not self._source_folder:
            return
        all_wavs = sorted(self._source_folder.glob("*.wav"))
        if not all_wavs:
            return
        dlg = QDialog(self)
        dlg.setWindowTitle(f"WAV for {clip.stem}")
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel(f"Paired audio for “{clip.stem}”:"))
        combo = QComboBox()
        combo.addItem("— none —", None)
        for wav in all_wavs:
            other = next((c for c in self._clips if c is not clip and c.wav_path == wav), None)
            label = wav.name + (f"  (currently: {other.stem})" if other else "")
            combo.addItem(label, wav)
        if clip.wav_path is not None:
            i = combo.findData(clip.wav_path)
            if i >= 0:
                combo.setCurrentIndex(i)
        lay.addWidget(combo)
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(dlg.reject)
        ok = QPushButton("Apply")
        ok.setDefault(True)
        ok.clicked.connect(dlg.accept)
        btn_row.addWidget(cancel)
        btn_row.addWidget(ok)
        lay.addLayout(btn_row)
        if not dlg.exec():
            return
        chosen = combo.currentData()
        old_wav = clip.wav_path

        # A genuine reassignment (not just re-confirming the current pairing)
        # whose duration is way off this clip's own — LARGE_MISMATCH_S, the
        # same threshold core.sync_advanced already uses to decide a mismatch
        # is too big for ordinary sync analysis to explain — gets a chance to
        # resolve it deliberately before anything is actually committed.
        resolution = None
        preserve_full = False
        if chosen is not None and chosen != old_wav:
            _, fp = get_ffmpeg()
            new_dur = probe_duration(fp, str(chosen))
            if new_dur > 0 and clip.duration > 0 and abs(new_dur - clip.duration) > LARGE_MISMATCH_S:
                mismatch_dlg = _WavMismatchDialog(clip.stem, clip.duration, chosen.name, new_dur, self)
                if not mismatch_dlg.exec():
                    return   # cancelled — nothing committed
                resolution = mismatch_dlg.resolution()
                preserve_full = mismatch_dlg.preserve_full()
                if resolution == "disconnect":
                    chosen = None

        # If another clip already had this WAV, free it up — a WAV pairs with
        # at most one clip at a time.
        if chosen is not None:
            for c in self._clips:
                if c is not clip and c.wav_path == chosen:
                    c.wav_path = None
                    c.wav_offset = 0.0
                    c.wav_duration = 0.0
                    c.sync_done = False
        clip.wav_path = chosen
        clip.wav_offset = 0.0
        clip.sync_done = False
        if resolution is not None and resolution != "disconnect":
            clip.alignment_mode = resolution
        if chosen != old_wav:
            # The preserve-in-full flag refers to whichever WAV is actually
            # paired — a changed pairing without a mismatch resolution (i.e.
            # a well-matched swap) resets it rather than silently carrying
            # over a stale opt-in for a now-different file.
            clip.preserve_wav_full = preserve_full
        if chosen is not None:
            _, fp = get_ffmpeg()
            clip.wav_duration = probe_duration(fp, str(chosen))
        else:
            clip.wav_duration = 0.0
        self._refresh_unmatched_banner()
        self._populate_table()
        self._update_estimate()

    def _on_tree_item_edited(self, item: QTreeWidgetItem, column: int):
        if item.parent() is not None:
            if column == COL_NAME:
                self._on_clip_check_toggled(item)
            return
        if column != COL_NAME:
            return   # only camera-group header renames are handled here
        new_label = item.text(COL_NAME).strip()
        camera_id = item.data(COL_NAME, Qt.ItemDataRole.UserRole)
        if not new_label or camera_id is None:
            return
        for clip in self._clips:
            if clip.camera_id == camera_id:
                clip.camera_label = new_label
        self._remember_camera_label(camera_id, new_label)

    def _on_clip_check_toggled(self, item: QTreeWidgetItem):
        idx = item.data(COL_NAME, Qt.ItemDataRole.UserRole)
        if idx is None or idx >= len(self._clips):
            return
        clip = self._clips[idx]
        selected = item.checkState(COL_NAME) == Qt.CheckState.Checked
        if clip.selected == selected:
            return
        clip.selected = selected
        self._apply_selected_fade(item, clip)
        self._update_estimate()

    def _on_clip_reassigned(self, clip_idx: int, target_camera_id: str):
        if clip_idx < 0 or clip_idx >= len(self._clips):
            return
        clip = self._clips[clip_idx]
        if clip.camera_id == target_camera_id:
            return
        target_label = next((c.camera_label for c in self._clips
                             if c.camera_id == target_camera_id), target_camera_id)
        clip.camera_id, clip.camera_label = target_camera_id, target_label
        self._populate_table()

    def _move_clip(self, clip: ClipInfo, delta: int):
        """Swap `clip`'s position with its neighbour in the GLOBAL chronological
        order (order_idx) — grouping is by camera, but the merge timeline is
        always one sequential, cross-camera order."""
        ordered = sorted(self._clips, key=lambda c: c.order_idx)
        i = ordered.index(clip)
        j = i + delta
        if j < 0 or j >= len(ordered):
            return
        other = ordered[j]
        clip.order_idx, other.order_idx = other.order_idx, clip.order_idx
        clip.manually_moved = True
        other.manually_moved = True
        self._populate_table()
        item = self._find_clip_item(self._clips.index(clip))
        if item is not None:
            self._table.setCurrentItem(item)
        self._dst_banner.setVisible(check_dst_warning(self._clips))

    def _reset_order(self):
        for clip in self._clips:
            clip.manually_moved = False
        order_clips_by_time(self._clips)   # creation_time when available, filename fallback
        self._populate_table()
        self._dst_banner.setVisible(check_dst_warning(self._clips))

    # ── Output ────────────────────────────────────────────────────────────────

    def _suggest_output_paths(self, folder: Path):
        """Recommend an output folder + filename from the just-loaded source
        folder — the source folder itself, and a `<folder name>.mov` filename.
        A starting suggestion only: skipped once the user has set their own this
        session, and always overridable via Browse / editing the filename."""
        if getattr(self, "_output_user_set", False):
            return
        try:
            self._out_dir.setText(str(folder))
            self._out_name.setText(f"{Path(folder).name}.mov")
        except Exception:
            pass

    def _browse_out_dir(self):
        start = self._out_dir.text() or str(Path.home())
        folder = QFileDialog.getExistingDirectory(self, "Select output folder", start)
        if folder:
            self._out_dir.setText(folder)
            self._output_user_set = True
            self._settings.set("last_merge_output_dir", folder)

    # ── Merge ─────────────────────────────────────────────────────────────────

    def _estimated_need_bytes(self) -> int:
        """Peak space needed on the output drive: temp clips + final ≈ 2× output."""
        try:
            from core.plan_report import analyze_merge
            return int(analyze_merge(self._selected_clips(), self._effective_plan()).total_bytes * 2.2)
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
        selected_clips = self._selected_clips()
        if not selected_clips:
            if self._clips:
                QMessageBox.information(self, "Nothing selected",
                                        "Every clip is unticked — tick at least one to merge.")
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
        for clip in sorted(selected_clips, key=lambda c: c.order_idx):
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
        # A dedicated pill for the post-merge MD5 pass — its own progress walks
        # 0..100% per clip again, unrelated to the merge stages' numbering, so it
        # must not be confused with (or recolor) the clip/Merge pills above.
        self._verify_pill = None
        if self._verify_md5_check.isChecked():
            verify_lbl = QLabel("MD5 verify")
            verify_lbl.setStyleSheet(pill_idle)
            verify_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._stage_labels.append(verify_lbl)
            self._stage_row.addWidget(verify_lbl)
            self._verify_pill = verify_lbl
        self._stage_row.addStretch()

        self._progress_frame.show()
        self._step_label.setText("Starting…")
        self._kind_badge.setText("")
        self._pbar.setValue(0)
        self._start_btn.hide()
        self._cancel_btn.show()

        self._worker = MergeWorker(
            clips          = selected_clips,
            output_path    = output,
            plan           = self._effective_plan(),
            square_mode    = square_mode,
            title          = output.stem,
            enable_preview = self._preview_check.isChecked(),
            archival       = self._archival_check.isChecked(),
            conform        = self._current_conform(),
            per_clip_archival = self._per_clip_archival_check.isChecked(),
            verify_md5     = self._verify_md5_check.isChecked(),
            skip_predictable_verify = self._skip_predictable_verify_check.isChecked(),
            # Re-encode the baseline into one clean, widely-playable stream. Set by
            # the guided Add flow (family/preservation path wants a master that
            # plays everywhere); the classic tab leaves it off unless a caller opts
            # in. See ffmpeg_runner / task #13.
            compat_baseline = getattr(self, "compat_baseline", False),
        )
        self._last_verify_summary = ""
        self._worker.progress.connect(self._on_progress)
        self._worker.thumbnail.connect(self._on_thumbnail)
        self._worker.finished.connect(self._on_finished)
        self._worker.verification_done.connect(self._on_verification_done)
        self._worker.start()

    def _cancel_merge(self):
        if self._worker:
            self._worker.cancel()

    def _progress_kind(self, stage: str, label: str, p) -> tuple:
        """Short badge text + color for what `stage`/`label` actually describe.
        The visible difference between "copying bytes", "re-encoding", and
        "hashing for MD5" is what keeps a slow pass from looking like a hang."""
        low = label.lower()
        if stage == "verify":
            return "MD5 VERIFY", p.blue
        if "transcod" in low or "re-encoding" in low:
            return "TRANSCODE", p.warn
        if "stream-copying" in low or low.startswith("mux"):
            return "STREAM COPY", p.ok
        if "archiv" in low:
            return "ARCHIVE", p.gold
        if "merging" in low or "baseline" in low:
            return "MERGE", p.accent
        return "WORKING", p.accent

    def _on_progress(self, data: dict):
        stage = data.get("stage", "mux")
        idx   = data.get("stage_idx", 1) - 1
        pct   = data.get("pct", 0)
        size  = data.get("size", 0)
        self._pbar.setValue(int(pct))
        p = theme.active_palette()
        pill_ok    = f"background:{p.ok}; color:white; border-radius:4px; padding:3px 7px; font-size:11px;"
        pill_accent = f"background:{p.accent}; color:{p.text}; border-radius:4px; padding:3px 7px; font-size:11px;"
        pill_idle  = f"background:{p.btn_bg}; color:{p.text}; border-radius:4px; padding:3px 7px; font-size:11px;"
        if stage == "verify":
            # Verify's own idx numbering restarts at 0 across just the clips —
            # not comparable to the merge stages' numbering, so it must not
            # recolor the clip/Merge pills (that would wrongly flash them back
            # to "idle" mid-verify, looking like the merge undid itself). They
            # already finished, by definition, before verify could start.
            clip_pills = self._stage_labels[:-1] if self._verify_pill else self._stage_labels
            for lbl in clip_pills:
                lbl.setStyleSheet(pill_ok)
            if self._verify_pill:
                self._verify_pill.setStyleSheet(pill_accent)
        else:
            for i, lbl in enumerate(self._stage_labels):
                if lbl is self._verify_pill:
                    continue   # untouched until the verify stage actually starts
                if i < idx:
                    lbl.setStyleSheet(pill_ok)
                elif i == idx:
                    lbl.setStyleSheet(pill_accent)
                else:
                    lbl.setStyleSheet(pill_idle)

        # Plain-language status: what kind of work, and which file/step.
        label = data.get("stage_label", "")
        if label:
            self._step_label.setText(label)
        kind, kind_col = self._progress_kind(stage, label, p)
        self._kind_badge.setText(kind)
        self._kind_badge.setStyleSheet(
            f"background:{kind_col}; color:white; border-radius:4px; padding:2px 8px; "
            "font-size:10px; font-weight:600;")

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

    def _on_verification_done(self, all_passed: bool, summary: str, report_path: str):
        self._last_verify_summary = summary
        if not all_passed:
            QMessageBox.warning(
                self, "Verification found a problem",
                f"{summary}\n\nSee the full report:\n{report_path}")

    def _on_finished(self, success: bool, message: str):
        self._cancel_btn.hide()
        self._start_btn.show()
        # The worker emits `finished` from inside run(), so the OS thread may
        # not have exited yet — wait it out BEFORE dropping the last reference,
        # or the GC can destroy a live QThread and abort the whole process.
        worker, self._worker = self._worker, None
        settle(worker)
        out = Path(self._out_dir.text()) / self._out_name.text()
        # `plan`/`mix` are best-effort — a failure building either must not
        # skip the log_merge call altogether (see log_manager.log_merge's own
        # docstring: it's the SECOND line of defence, this is the first).
        try:
            plan = self._effective_plan()
            mix = {
                "tracks":       [t.kind for t in plan.tracks if t.enabled],
                "include_video": plan.include_video,
                "mix_enabled":  any(t.kind == "mix" and t.enabled for t in plan.tracks),
                "kind":         plan.mix_kind,
                "match_levels": plan.mix_match_levels,
            }
        except Exception:
            plan, mix = None, None
        try:
            log_manager.log_merge(
                source_folder = self._folder_edit.text(),
                output        = str(out),
                clips         = sorted(self._clips, key=lambda c: c.order_idx),
                track_order   = self._current_track_order(),
                success       = success,
                message       = message,
                mix           = mix,
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
            box = QMessageBox(self)
            box.setWindowTitle("Done")
            box.setIcon(QMessageBox.Icon.Information)
            verify_line = f"\n\n{self._last_verify_summary}" if self._last_verify_summary else ""
            box.setText(f"Merge complete!\n\n{message}\n{out}{verify_line}")
            ok_btn = box.addButton(QMessageBox.StandardButton.Ok)
            review_btn = box.addButton("Review", QMessageBox.ButtonRole.ActionRole)
            box.setDefaultButton(ok_btn)
            box.exec()
            if box.clickedButton() is review_btn:
                self.open_in_review.emit(str(out))
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
        settle(self._gpu_probe_thread)
        for dlg in list(self._preview_dialogs):
            dlg.close()
        for thread in list(self._preview_threads):
            settle(thread)
        self._worker = None
        self._probe_thread = None
        self._gpu_probe_thread = None

    def set_output_path_hint(self, path: str):
        p = Path(path)
        self._out_dir.setText(str(p.parent))
        self._out_name.setText(p.name)
