"""preflight_dialog.py — "what will the merge do?" breakdown before starting.

Shows, per clip: the video action, the audio tracks that will be created (with
codec + lossless flag), and the reasoning (missing camera audio, slow-motion
stretch, etc.), plus the total anticipated output size and a time estimate.

Also offers optional pre-flight DIAGNOSTIC checks (core/diagnostics.py,
diagnostics_workers.py) — container structure, packet timestamps, stream-copy
compatibility, and decode-error scans — run on demand against the real clip
files, with results attached to each clip's own card. Informational only:
findings never block "Start merge" (the same way the disk-space warning is a
heads-up, not a hard stop) — it's the user's call what to do about a flagged
clip (re-encode anyway, swap in an LRV proxy, drop it, etc.).
"""

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QWidget, QFrame, QCheckBox, QProgressBar, QComboBox,
)

from core.plan_report import MergeReport
from core.binaries import get_ffmpeg
from core import diagnostics as diag
from diagnostics_workers import DiagnosticsWorker
import theme


def _fmt_size(b: int) -> str:
    if b >= 1024**3:
        return f"{b/1024**3:.2f} GB"
    if b >= 1024**2:
        return f"{b/1024**2:.0f} MB"
    return f"{b/1024:.0f} KB"


def _fmt_time(secs: float) -> str:
    secs = max(0, int(secs))
    h, r = divmod(secs, 3600)
    m, s = divmod(r, 60)
    if h:
        return f"{h}h {m}m"
    if m:
        return f"{m}m {s}s"
    return f"{s}s"


class PreflightDialog(QDialog):
    start_requested = Signal()

    def __init__(self, report: MergeReport, parent=None, free_bytes=None, need_bytes=0,
                clips: Optional[list] = None, settings=None, gpu_available: bool = False):
        super().__init__(parent)
        self.setWindowTitle("Pre-flight — what this merge will do")
        self.setMinimumSize(560, 480)
        self._p = theme.active_palette()
        p = self._p
        self._free_bytes = free_bytes
        self._need_bytes = need_bytes
        self._clips = clips or []          # real ClipInfo objects, same order as report.clips
        self._settings = settings          # persist pipeline choices here (None => hide the section)
        self._gpu_available = bool(gpu_available)
        self._diag_worker: Optional[DiagnosticsWorker] = None
        self._clip_diag_labels: dict = {}   # 0-based clip index -> QLabel
        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 14)
        root.setSpacing(10)

        # ── Summary band ─────────────────────────────────────────────────────
        summ = QFrame()
        summ.setStyleSheet(f"QFrame {{ background:{p.surface2}; border:1px solid {p.border}; border-radius:8px; }}")
        sl = QHBoxLayout(summ); sl.setContentsMargins(14, 10, 14, 10)
        for label, value in [
            ("Clips", str(len(report.clips))),
            ("Est. output size", _fmt_size(report.total_bytes)),
            ("Est. time", f"{_fmt_time(report.best_secs)} – {_fmt_time(report.worst_secs)}"),
        ]:
            col = QVBoxLayout(); col.setSpacing(1)
            v = QLabel(value); v.setStyleSheet(f"color:{p.text}; font-size:16px; font-weight:500;")
            k = QLabel(label); k.setStyleSheet(f"color:{p.text_mute}; font-size:11px;")
            col.addWidget(v); col.addWidget(k); sl.addLayout(col)
        sl.addStretch()
        flags = []
        if report.n_transcode:
            flags.append(f"{report.n_transcode} transcode")
        if report.n_slowmo:
            flags.append(f"{report.n_slowmo} slow-mo")
        if report.n_no_camera:
            flags.append(f"{report.n_no_camera} no camera audio")
        if flags:
            fl = QLabel("  ·  ".join(flags))
            fl.setStyleSheet(f"color:{p.accent}; font-size:11px;")
            sl.addWidget(fl, 0, Qt.AlignmentFlag.AlignVCenter)
        root.addWidget(summ)

        # Disk-space line (temp clips + final ≈ 2× output need the output drive).
        if free_bytes is not None:
            low = need_bytes and free_bytes < need_bytes
            disk = QLabel(
                f"Output drive: {_fmt_size(free_bytes)} free"
                + (f"  ·  needs ~{_fmt_size(need_bytes)} (temp + final)" if need_bytes else "")
                + ("   ⚠ may not fit" if low else "")
            )
            disk.setStyleSheet(f"color:{p.danger if low else p.text_dim}; font-size:11px;")
            root.addWidget(disk)

        # ── Processing pipeline (decode + encode method) ─────────────────────
        if self._settings is not None:
            root.addWidget(self._build_pipeline_section())

        # ── Diagnostics ──────────────────────────────────────────────────────
        if self._clips:
            root.addWidget(self._build_diagnostics_section())

        # ── Per-clip list ────────────────────────────────────────────────────
        scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget(); col = QVBoxLayout(body); col.setSpacing(8); col.setContentsMargins(0, 0, 0, 0)
        for i, cr in enumerate(report.clips, 1):
            col.addWidget(self._clip_card(i, cr))
        col.addStretch()
        scroll.setWidget(body)
        root.addWidget(scroll, 1)

        est_note = QLabel("Sizes and times are estimates; actual results depend on content and hardware.")
        est_note.setStyleSheet(f"color:{p.text_dim}; font-size:11px; font-style:italic;")
        root.addWidget(est_note)

        # ── Buttons ──────────────────────────────────────────────────────────
        btns = QHBoxLayout(); btns.addStretch()
        close = QPushButton("Close"); close.clicked.connect(self.reject)
        start = QPushButton("▶  Start merge")
        start.setStyleSheet(f"QPushButton {{ background:{p.accent}; color:{p.on_accent()}; "
                            "border-radius:6px; font-weight:bold; padding:6px 18px; }")
        start.clicked.connect(self._on_start)
        btns.addWidget(close); btns.addWidget(start)
        root.addLayout(btns)

    # ── Processing-pipeline section ───────────────────────────────────────────

    def _build_pipeline_section(self) -> QFrame:
        p = self._p
        s = self._settings
        frame = QFrame()
        frame.setStyleSheet(f"QFrame {{ background:{p.surface2}; border:1px solid {p.border}; border-radius:8px; }}")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(6)

        title = QLabel("PROCESSING PIPELINE")
        title.setStyleSheet(f"color:{p.text_mute}; font-size:10px; font-weight:bold; "
                            "letter-spacing:1px; border:none;")
        lay.addWidget(title)

        self._pipe_recommended = QCheckBox("Use recommended settings (automatic)")
        self._pipe_recommended.setChecked(bool(s.get("merge_pipeline_recommended", True)))
        self._pipe_recommended.setStyleSheet(f"color:{p.text}; border:none;")
        self._pipe_recommended.setToolTip(
            "Let the app choose the best video decode + encode combination for this machine. "
            "Untick to pick your own.")
        self._pipe_recommended.toggled.connect(self._on_pipe_recommended_toggled)
        lay.addWidget(self._pipe_recommended)

        # Custom decode/encode method pickers (enabled only when not recommended).
        self._pipe_custom = QWidget()
        cl = QVBoxLayout(self._pipe_custom)
        cl.setContentsMargins(18, 2, 0, 2)
        cl.setSpacing(6)
        self._pipe_decode = self._method_combo(
            "Video decode", s.get("merge_decode_method", "software"), cl, "decode")
        self._pipe_encode = self._method_combo(
            "Video encode", s.get("merge_encode_method", "hardware"), cl, "encode")
        lay.addWidget(self._pipe_custom)

        self._pipe_reco_label = QLabel("")
        self._pipe_reco_label.setWordWrap(True)
        self._pipe_reco_label.setStyleSheet(f"color:{p.text_dim}; font-size:10.5px; border:none;")
        lay.addWidget(self._pipe_reco_label)

        self._refresh_pipeline_ui()
        return frame

    def _method_combo(self, label_text: str, current: str, parent_layout, which: str) -> QComboBox:
        p = self._p
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        lbl = QLabel(label_text)
        lbl.setStyleSheet(f"color:{p.text}; font-size:11.5px; border:none;")
        combo = QComboBox()
        combo.addItem("Software (CPU)", "software")
        combo.addItem("Hardware (GPU)", "hardware")
        if not self._gpu_available:
            # No GPU encoder → hardware isn't selectable; keep it visible but disabled
            # so the user sees the option exists and why it's greyed out.
            combo.model().item(1).setEnabled(False)
            combo.model().item(1).setToolTip("No GPU encoder detected on this machine")
        want_hw = current == "hardware" and self._gpu_available
        combo.setCurrentIndex(1 if want_hw else 0)
        combo.currentIndexChanged.connect(
            lambda _i, c=combo, w=which: self._on_method_changed(w, c.currentData()))
        row.addWidget(lbl)
        row.addStretch()
        row.addWidget(combo)
        parent_layout.addLayout(row)
        return combo

    def _on_pipe_recommended_toggled(self, on: bool):
        self._settings.set("merge_pipeline_recommended", bool(on))
        self._refresh_pipeline_ui()

    def _on_method_changed(self, which: str, value):
        key = "merge_decode_method" if which == "decode" else "merge_encode_method"
        self._settings.set(key, value)
        self._refresh_pipeline_ui()

    def _refresh_pipeline_ui(self):
        recommended = self._pipe_recommended.isChecked()
        self._pipe_custom.setEnabled(not recommended)
        self._pipe_reco_label.setText(self._pipeline_summary_text(recommended))

    def _pipeline_summary_text(self, recommended: bool) -> str:
        if recommended:
            if self._gpu_available:
                return ("→ GPU (hardware) encode + CPU (software) decode — the fastest combination "
                        "measured on this class of hardware. Full hardware decode frees the CPU but "
                        "runs slightly slower overall.")
            return "→ CPU encode + CPU decode — no GPU encoder was detected on this machine."
        dec = self._settings.get("merge_decode_method", "software")
        enc = self._settings.get("merge_encode_method", "hardware")
        if not self._gpu_available and (dec == "hardware" or enc == "hardware"):
            return "No GPU encoder detected — hardware selections will fall back to the CPU."
        return ("Hardware = your GPU's dedicated video engine (VAAPI); software = the CPU "
                "(libx264/libx265). Both codecs work either way.")

    # ── Diagnostics section ───────────────────────────────────────────────────

    def _build_diagnostics_section(self) -> QFrame:
        p = self._p
        frame = QFrame()
        frame.setStyleSheet(f"QFrame {{ background:{p.surface2}; border:1px solid {p.border}; border-radius:8px; }}")
        lay = QVBoxLayout(frame)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(6)

        title = QLabel("DIAGNOSTICS")
        title.setStyleSheet(f"color:{p.text_mute}; font-size:10px; font-weight:bold; "
                            "letter-spacing:1px; border:none;")
        lay.addWidget(title)

        self._diag_checks: dict = {}
        for info in diag.CHECKS:
            row = QHBoxLayout(); row.setSpacing(8)
            chk = QCheckBox(info.label)
            chk.setChecked(info.default_on)
            cost_txt = {"fast": "fast", "medium": "~10-30s/clip", "slow": "can take minutes/clip"}[info.cost_hint]
            chk.setToolTip(f"{info.description}\n\nCost: {cost_txt}")
            self._diag_checks[info.check_id] = chk
            row.addWidget(chk)
            cost = QLabel(f"({cost_txt})")
            cost.setStyleSheet(f"color:{p.text_dim}; font-size:10.5px; border:none;")
            row.addWidget(cost)
            row.addStretch()
            lay.addLayout(row)

        run_row = QHBoxLayout()
        self._diag_status_label = QLabel("Informational only — findings never block starting the merge.")
        self._diag_status_label.setStyleSheet(f"color:{p.text_dim}; font-size:10.5px; border:none;")
        run_row.addWidget(self._diag_status_label, 1)
        self._diag_cancel_btn = QPushButton("Cancel")
        self._diag_cancel_btn.clicked.connect(self._cancel_diagnostics)
        self._diag_cancel_btn.hide()
        run_row.addWidget(self._diag_cancel_btn)
        self._diag_run_btn = QPushButton("Run diagnostics")
        self._diag_run_btn.clicked.connect(self._run_diagnostics)
        run_row.addWidget(self._diag_run_btn)
        lay.addLayout(run_row)

        self._diag_pbar = QProgressBar()
        self._diag_pbar.setRange(0, 100)
        self._diag_pbar.hide()
        lay.addWidget(self._diag_pbar)

        return frame

    def _run_diagnostics(self):
        check_ids = [cid for cid, chk in self._diag_checks.items() if chk.isChecked()]
        if not check_ids or not self._clips:
            return
        ff, fp = get_ffmpeg()
        self._diag_run_btn.hide()
        self._diag_cancel_btn.show()
        self._diag_pbar.setValue(0)
        self._diag_pbar.show()
        self._diag_status_label.setText("Starting…")
        for chk in self._diag_checks.values():
            chk.setEnabled(False)

        self._diag_worker = DiagnosticsWorker(ff, fp, self._clips, check_ids, self)
        self._diag_worker.progress.connect(self._on_diag_progress)
        self._diag_worker.result_ready.connect(self._on_diag_result)
        self._diag_worker.finished_all.connect(self._on_diag_finished)
        self._diag_worker.start()

    def _cancel_diagnostics(self):
        if self._diag_worker is not None:
            self._diag_worker.cancel()

    def _on_diag_progress(self, done: int, total: int, name: str):
        pct = int(done / total * 100) if total else 0
        self._diag_pbar.setValue(pct)
        self._diag_status_label.setText(
            f"Checking {done}/{total}" + (f" — {name}" if name else ""))

    def _on_diag_result(self, idx: int, result):
        label = self._clip_diag_labels.get(idx)
        if label is None:
            return
        p = self._p
        colors = {"clean": p.ok, "warning": p.warn, "problem": p.danger, "error": p.text_dim}
        symbols = {"clean": "✓", "warning": "⚠", "problem": "✗", "error": "?"}
        color = colors.get(result.verdict, p.text_dim)
        symbol = symbols.get(result.verdict, "?")
        existing = label.property("_diag_lines") or []
        existing.append((color, f"{symbol} {result.label}: {result.detail}"))
        label.setProperty("_diag_lines", existing)
        label.setText("\n".join(f"<span style='color:{c};'>{t}</span>" for c, t in existing))
        label.show()

    def _on_diag_finished(self, ok: bool):
        from thread_utils import settle
        worker, self._diag_worker = self._diag_worker, None
        if worker is not None:
            settle(worker, 10000)
        self._diag_cancel_btn.hide()
        self._diag_run_btn.show()
        self._diag_pbar.hide()
        for chk in self._diag_checks.values():
            chk.setEnabled(True)
        self._diag_status_label.setText(
            "Informational only — findings never block starting the merge."
            if ok else "Diagnostics cancelled.")

    # ── Per-clip cards ─────────────────────────────────────────────────────────

    def _clip_card(self, idx: int, cr) -> QFrame:
        p = self._p
        card = QFrame()
        card.setStyleSheet(f"QFrame {{ background:{p.surface}; border:1px solid {p.border}; border-radius:8px; }}")
        lay = QVBoxLayout(card); lay.setContentsMargins(12, 9, 12, 10); lay.setSpacing(5)

        head = QHBoxLayout()
        title = QLabel(f"{idx}.  {cr.name}")
        title.setStyleSheet(f"color:{p.text}; font-size:12.5px; font-weight:500; border:none;")
        head.addWidget(title)
        head.addStretch()
        va_color = p.warn if cr.video_action.startswith("Transcode") else p.ok
        if cr.video_action == "Excluded":
            va_color = p.text_dim
        vid = QLabel(f"video: {cr.video_action}")
        vid.setStyleSheet(f"color:{va_color}; font-size:11px; border:none;")
        head.addWidget(vid)
        sz = QLabel(f"~{_fmt_size(cr.est_bytes)}")
        sz.setStyleSheet(f"color:{p.text_mute}; font-size:11px; border:none; margin-left:10px;")
        head.addWidget(sz)
        lay.addLayout(head)

        for t in cr.audio:
            row = QHBoxLayout(); row.setSpacing(8)
            dot = QLabel("●"); dot.setStyleSheet(f"color:{p.accent if t.role=='primary' else p.text_dim}; font-size:9px; border:none;")
            row.addWidget(dot)
            name = QLabel(t.label + ("  (default)" if t.role == "primary" else ""))
            name.setStyleSheet(f"color:{p.text}; font-size:11.5px; border:none;")
            row.addWidget(name)
            badge = QLabel(t.out_codec + ("  · lossless" if t.lossless else ""))
            badge.setStyleSheet(f"color:{p.ok if t.lossless else p.text_mute}; font-size:10.5px; border:none;")
            row.addWidget(badge)
            row.addStretch()
            lay.addLayout(row)
        if not cr.audio:
            lay.addWidget(self._note("(no audio tracks)"))

        for n in cr.notes:
            lay.addWidget(self._note("→ " + n))

        if idx - 1 < len(self._clips):
            diag_label = QLabel("")
            diag_label.setWordWrap(True)
            diag_label.setStyleSheet("font-size:10.5px; border:none; margin-left:2px;")
            diag_label.setTextFormat(Qt.TextFormat.RichText)
            diag_label.hide()
            lay.addWidget(diag_label)
            self._clip_diag_labels[idx - 1] = diag_label

        return card

    def _note(self, text: str) -> QLabel:
        p = self._p
        lbl = QLabel(text); lbl.setWordWrap(True)
        lbl.setStyleSheet(f"color:{p.text_dim}; font-size:10.5px; border:none; margin-left:17px;")
        return lbl

    def _on_start(self):
        self.start_requested.emit()
        self.accept()
