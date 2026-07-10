"""audio_sync_dialog.py — per-clip Advanced sync analysis dialog.

Runs core.sync_advanced.analyze_sync in a background thread for the selected
clip, shows the GCC-PHAT window lags, constant offset, drift and confidence,
and lets the user apply a manual ±ms nudge, override which end the alignment
anchors on (Auto/start/end), override or disable the mix track's drift
correction, and reassign which WAV file this clip pairs with (from here
rather than needing to close the dialog and double-click the table cell).
Accepting writes the results back onto the ClipInfo so the merge reuses them
(and they appear in the Log).
"""

from typing import Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QDoubleSpinBox, QComboBox, QFrame, QMessageBox,
)

from ffmpeg_runner import get_ffmpeg
from probe import probe_duration
from core.sync_advanced import analyze_sync
from thread_utils import settle
import theme

_ALIGNMENT_ITEMS = [
    ("auto", "Auto (recommended)"),
    ("start", "Align to clip start"),
    ("end", "Align to clip end"),
]
_DRIFT_AUTO, _DRIFT_OFF, _DRIFT_CUSTOM = "auto", "off", "custom"


class _AnalyzeThread(QThread):
    done  = Signal(object)
    error = Signal(str)

    def __init__(self, clip):
        super().__init__()
        self._clip = clip

    def run(self):
        try:
            ff, fp = get_ffmpeg()
            wav_dur = probe_duration(fp, str(self._clip.wav_path))
            res = analyze_sync(ff, str(self._clip.path), str(self._clip.wav_path),
                               self._clip.duration, wav_dur,
                               anchor_mode=self._clip.alignment_mode)
            res._wav_dur = wav_dur
            self.done.emit(res)
        except Exception as e:
            self.error.emit(str(e))


class _BatchThread(QThread):
    one_done = Signal(int, object)   # clip index in list, SyncResult
    progress = Signal(int, int)      # done, total
    finished_all = Signal()

    def __init__(self, clips):
        super().__init__()
        self._clips = clips
        self._cancel = False

    def cancel(self):
        self._cancel = True

    def run(self):
        ff, fp = get_ffmpeg()
        targets = [(i, c) for i, c in enumerate(self._clips) if c.has_wav()]
        total = len(targets)
        for n, (i, clip) in enumerate(targets, 1):
            if self._cancel:
                break
            wav_dur = probe_duration(fp, str(clip.wav_path))
            res = analyze_sync(ff, str(clip.path), str(clip.wav_path), clip.duration, wav_dur,
                               anchor_mode=clip.alignment_mode)
            clip.wav_offset             = res.constant_offset + clip.manual_nudge_ms / 1000.0
            clip.sync_drift_ratio       = res.drift_ratio
            clip.sync_confidence_ms     = res.confidence_ms
            clip.sync_polarity_inverted = res.polarity_inverted
            clip.sync_windows           = res.n_windows
            clip.sync_lags_ms           = res.window_lags_ms
            clip.sync_done              = True
            self.one_done.emit(i, res)
            self.progress.emit(n, total)
        self.finished_all.emit()


class BatchSyncDialog(QDialog):
    """Run sync analysis on every clip that has a WAV, updating as it goes."""
    clip_analyzed = Signal()   # emitted after each clip so the table can refresh

    def __init__(self, clips, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Analyse all clips")
        self.setMinimumWidth(380)
        p = theme.active_palette()
        root = QVBoxLayout(self)
        root.setSpacing(10); root.setContentsMargins(18, 16, 18, 16)
        n_wav = sum(1 for c in clips if c.has_wav())
        self._status = QLabel(f"Analysing {n_wav} clip(s) with a WAV backup…")
        self._status.setStyleSheet(f"color:{p.accent}; font-weight:bold;")
        root.addWidget(self._status)
        self._detail = QLabel("")
        self._detail.setStyleSheet(f"color:{p.text}; font-size:12px;")
        root.addWidget(self._detail)
        btn_row = QHBoxLayout(); btn_row.addStretch()
        self._close = QPushButton("Cancel")
        self._close.clicked.connect(self._on_close)
        btn_row.addWidget(self._close)
        root.addLayout(btn_row)

        self._thread = _BatchThread(clips)
        self._thread.one_done.connect(lambda i, r: self.clip_analyzed.emit())
        self._thread.progress.connect(self._on_progress)
        self._thread.finished_all.connect(self._on_finished)
        if n_wav == 0:
            self._status.setText("No clips have a WAV backup to analyse.")
            self._close.setText("Close")
        else:
            self._thread.start()

    def _on_progress(self, done, total):
        self._detail.setText(f"{done} / {total} clips analysed")

    def _on_finished(self):
        self._status.setText("Analysis complete")
        self._close.setText("Close")

    def _on_close(self):
        if self._thread.isRunning():
            self._thread.cancel()
            self._thread.wait(2000)
        self.accept()

    def done(self, r):
        # Covers every close path (Cancel, X button, Esc): the thread must be
        # settled before the dialog — and its reference — can be destroyed.
        if self._thread.isRunning():
            self._thread.cancel()
        settle(self._thread)
        super().done(r)


class AdvancedSyncDialog(QDialog):
    def __init__(self, clip, parent=None, on_reassign_wav=None):
        super().__init__(parent)
        self._clip = clip
        self._res = None
        self._thread: Optional[_AnalyzeThread] = None
        # Opens the SAME WAV-swap dialog the table's double-click uses — lets
        # a wrong pairing be fixed without leaving this dialog first. None in
        # any context that can't supply one (e.g. no source folder known).
        self._on_reassign_wav = on_reassign_wav
        self.setWindowTitle(f"Advanced sync — {clip.stem}")
        self.setMinimumWidth(460)

        p = theme.active_palette()
        root = QVBoxLayout(self)
        root.setSpacing(10)
        root.setContentsMargins(18, 16, 18, 16)

        self._status = QLabel("Analysing mic alignment…")
        self._status.setStyleSheet(f"color:{p.accent}; font-weight:bold;")
        root.addWidget(self._status)

        self._body = QLabel("")
        self._body.setWordWrap(True)
        self._body.setTextFormat(Qt.TextFormat.RichText)
        self._body.setStyleSheet(f"color:{p.text}; font-size:12px;")
        root.addWidget(self._body)

        line = QFrame(); line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet(f"border:none; border-top:1px solid {p.border};")
        root.addWidget(line)

        # ── Alignment mode — re-runs analysis on change, since it changes
        # what the whole GCC-PHAT search is anchored against. ──────────────
        align_row = QHBoxLayout()
        align_row.addWidget(QLabel("Alignment:"))
        self._align_combo = QComboBox()
        for value, label in _ALIGNMENT_ITEMS:
            self._align_combo.addItem(label, value)
        self._align_combo.setCurrentIndex(
            max(0, self._align_combo.findData(clip.alignment_mode)))
        self._align_combo.setToolTip(
            "Which end of the recordings the sync search assumes lines up before\n"
            "fine-tuning. \"Auto\" end-aligns normally, and for a WAV that runs far\n"
            "longer than its clip (a mic left running past this take), rescues itself\n"
            "with a coarse whole-clip match instead of guessing wrong. Override to\n"
            "\"start\" if you know the mic and camera were started together, or\n"
            "\"end\" if you know they were stopped together, regardless of any\n"
            "duration difference.")
        self._align_combo.currentIndexChanged.connect(self._on_alignment_changed)
        align_row.addWidget(self._align_combo, 1)
        root.addLayout(align_row)

        # ── Drift correction (mix track only) ───────────────────────────────
        drift_row = QHBoxLayout()
        drift_row.addWidget(QLabel("Drift correction (mix):"))
        self._drift_combo = QComboBox()
        self._drift_combo.addItem("Auto (recommended)", _DRIFT_AUTO)
        self._drift_combo.addItem("Off — no tempo correction", _DRIFT_OFF)
        self._drift_combo.addItem("Custom (ms/min)", _DRIFT_CUSTOM)
        self._drift_combo.setToolTip(
            "How the derived MIX track's WAV side tracks any clock-rate difference\n"
            "between the camera and the WAV recorder over the clip's duration — the\n"
            "lossless WAV backup track is NEVER resampled either way, only the mix.\n"
            "\"Auto\" uses what analysis measures. \"Off\" leaves the mix's WAV side\n"
            "untouched even if drift was detected. \"Custom\" lets you dial in your\n"
            "own rate if you trust your own ear over the measurement.")
        self._drift_combo.currentIndexChanged.connect(self._on_drift_mode_changed)
        drift_row.addWidget(self._drift_combo)
        self._drift_custom = QDoubleSpinBox()
        self._drift_custom.setRange(-500.0, 500.0)
        self._drift_custom.setDecimals(2)
        self._drift_custom.setSuffix(" ms/min")
        self._drift_custom.setSingleStep(0.5)
        drift_row.addWidget(self._drift_custom)
        root.addLayout(drift_row)
        if clip.drift_override is None:
            self._drift_combo.setCurrentIndex(self._drift_combo.findData(_DRIFT_AUTO))
        elif abs(clip.drift_override - 1.0) < 1e-9:
            self._drift_combo.setCurrentIndex(self._drift_combo.findData(_DRIFT_OFF))
        else:
            self._drift_combo.setCurrentIndex(self._drift_combo.findData(_DRIFT_CUSTOM))
            self._drift_custom.setValue((clip.drift_override - 1.0) * 60_000.0)
        self._on_drift_mode_changed()   # sync the spinbox's enabled state

        nudge_row = QHBoxLayout()
        nudge_row.addWidget(QLabel("Manual nudge (ms):"))
        self._nudge = QDoubleSpinBox()
        self._nudge.setRange(-500.0, 500.0)
        self._nudge.setDecimals(1)
        self._nudge.setSingleStep(1.0)
        self._nudge.setValue(clip.manual_nudge_ms)
        nudge_row.addWidget(self._nudge)
        nudge_row.addStretch()
        root.addLayout(nudge_row)

        reassign_row = QHBoxLayout()
        self._reassign_btn = QPushButton("Reassign WAV file…")
        self._reassign_btn.setEnabled(self._on_reassign_wav is not None)
        self._reassign_btn.clicked.connect(self._on_click_reassign)
        reassign_row.addWidget(self._reassign_btn)
        reassign_row.addStretch()
        root.addLayout(reassign_row)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self._cancel = QPushButton("Cancel")
        self._cancel.clicked.connect(self.reject)
        self._apply = QPushButton("Apply")
        self._apply.setEnabled(False)
        self._apply.clicked.connect(self._on_apply)
        btn_row.addWidget(self._cancel)
        btn_row.addWidget(self._apply)
        root.addLayout(btn_row)

        self._start_analysis()

    # ── Analysis lifecycle ───────────────────────────────────────────────────

    def _start_analysis(self):
        settle(self._thread, 10000)
        self._res = None
        self._apply.setEnabled(False)
        self._status.setText("Analysing mic alignment…")
        self._status.setStyleSheet(f"color:{theme.active_palette().accent}; font-weight:bold;")
        self._body.setText("")
        self._thread = _AnalyzeThread(self._clip)
        self._thread.done.connect(self._on_done)
        self._thread.error.connect(self._on_error)
        self._thread.start()

    def _on_alignment_changed(self):
        self._clip.alignment_mode = self._align_combo.currentData()
        self._start_analysis()

    def _on_drift_mode_changed(self):
        self._drift_custom.setEnabled(self._drift_combo.currentData() == _DRIFT_CUSTOM)

    def _on_click_reassign(self):
        if self._on_reassign_wav is None:
            return
        had_wav = self._clip.has_wav()
        prev_path = self._clip.wav_path
        self._on_reassign_wav(self._clip)
        if not self._clip.has_wav():
            QMessageBox.information(self, "No WAV backup",
                                    "This clip no longer has a paired WAV backup — "
                                    "closing, since there's nothing left to sync.")
            self.reject()
            return
        if self._clip.wav_path != prev_path or not had_wav:
            self.setWindowTitle(f"Advanced sync — {self._clip.stem}")
            self._start_analysis()

    def _on_error(self, msg: str):
        self._status.setText("Analysis failed")
        self._body.setText(msg[:300])

    def _on_done(self, res):
        self._res = res
        p = theme.active_palette()
        if not res.ok:
            self._status.setText("Limited result")
            self._body.setText(res.note or "Could not analyse; using end-alignment.")
            self._apply.setEnabled(True)
            return
        self._status.setText("Analysis complete")
        conf = res.confidence_ms
        conf_word = "high" if conf < 2 else ("fair" if conf < 8 else "low")
        lags = ", ".join(f"{x:.1f}" for x in res.window_lags_ms)
        drift_mode = self._drift_combo.currentData()
        if drift_mode == _DRIFT_OFF:
            drift_line = (f"<b>Drift</b> (mix track only): measured {res.drift_ms_per_min():+.1f} ms/min "
                          f"— <span style='color:{p.warn}'>overridden to OFF</span>")
        elif drift_mode == _DRIFT_CUSTOM:
            drift_line = (f"<b>Drift</b> (mix track only): measured {res.drift_ms_per_min():+.1f} ms/min "
                          f"— <span style='color:{p.warn}'>overridden to {self._drift_custom.value():+.1f} ms/min</span>")
        else:
            drift_line = (f"<b>Drift</b> (mix track only): {res.drift_ms_per_min():+.1f} ms/min "
                          f"&nbsp;<span style='color:{p.text_dim}'>(×{res.drift_ratio:.7f})</span>")
        rows = [
            f"<b>Constant offset</b> (lossless track): {res.constant_offset*1000:+.1f} ms",
            drift_line,
            f"<b>Confidence</b>: {conf_word} &nbsp;±{conf:.2f} ms over {res.n_windows} windows",
            f"<b>Polarity</b>: {'inverted — will be flipped' if res.polarity_inverted else 'in phase'}",
            f"<span style='color:{p.text_dim}'>Window lags (ms): {lags}</span>",
        ]
        if res.note:
            rows.append(f"<span style='color:{p.text_dim}'>{res.note}</span>")
        if conf_word == "low":
            rows.append(f"<span style='color:{p.warn}'>⚠ Low agreement — the mics may be too "
                        "dissimilar to mix cleanly. Prefer keeping them on separate tracks.</span>")
        self._body.setText("<br>".join(rows))
        self._apply.setEnabled(True)

    def done(self, r):
        # _AnalyzeThread has no cancel — analysis is bounded (a few seconds);
        # settle it before the dialog reference can be destroyed.
        settle(self._thread, 10000)
        super().done(r)

    def _on_apply(self):
        res = self._res
        c = self._clip
        c.manual_nudge_ms = self._nudge.value()
        c.alignment_mode = self._align_combo.currentData()
        drift_mode = self._drift_combo.currentData()
        if drift_mode == _DRIFT_AUTO:
            c.drift_override = None
        elif drift_mode == _DRIFT_OFF:
            c.drift_override = 1.0
        else:
            c.drift_override = 1.0 + (self._drift_custom.value() / 60_000.0)
        if res is not None and res.ok:
            c.wav_offset             = res.constant_offset + c.manual_nudge_ms / 1000.0
            c.sync_drift_ratio       = res.drift_ratio
            c.sync_confidence_ms     = res.confidence_ms
            c.sync_polarity_inverted = res.polarity_inverted
            c.sync_windows           = res.n_windows
            c.sync_lags_ms           = res.window_lags_ms
            c.sync_done              = True
        self.accept()
