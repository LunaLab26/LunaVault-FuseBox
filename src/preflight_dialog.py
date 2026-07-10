"""preflight_dialog.py — "what will the merge do?" breakdown before starting.

Shows, per clip: the video action, the audio tracks that will be created (with
codec + lossless flag), and the reasoning (missing camera audio, slow-motion
stretch, etc.), plus the total anticipated output size and a time estimate.

When a `Story` is supplied (see show_me.py), a static "big picture" diagram —
the same film-strip/tape-reel visual language as the animated "Show me"
button, frozen at its FINAL frame (everything already landed on the reel/
shelves/vault) rather than played — sits above the numeric per-clip cards, so
the shape of the whole merge (what stream-copies, what converts, where each
audio source ends up) is visible at a glance before reading the details.
"""

from typing import Optional

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QWidget, QFrame,
)

from core.plan_report import MergeReport
from show_me import Story, ShowMeCanvas
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
                story: Optional[Story] = None):
        super().__init__(parent)
        self.setWindowTitle("Pre-flight — what this merge will do")
        self.setMinimumSize(700, 480) if story is not None else self.setMinimumSize(560, 480)
        self._p = theme.active_palette()
        p = self._p
        self._free_bytes = free_bytes
        self._need_bytes = need_bytes
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

        # ── Big-picture diagram: film strips / tape reels, frozen at the END
        # of the "Show me" animation — everything already landed on the reel,
        # its shelves, and the vault, so the whole shape of the merge reads
        # at a glance without waiting through the animation (that's what the
        # separate "✨ Show me" button is for). ──────────────────────────────
        if story is not None:
            diagram_frame = QFrame()
            diagram_frame.setStyleSheet(
                f"QFrame {{ background:{p.surface2}; border:1px solid {p.border}; border-radius:8px; }}")
            dl = QVBoxLayout(diagram_frame)
            dl.setContentsMargins(10, 8, 10, 4)
            dl.setSpacing(4)
            dtitle = QLabel("HOW YOUR CLIPS BECOME THE MASTER")
            dtitle.setStyleSheet(f"color:{p.text_mute}; font-size:10px; font-weight:bold; "
                                 "letter-spacing:1px; border:none;")
            dl.addWidget(dtitle)
            self._diagram = ShowMeCanvas(story)
            self._diagram.setMinimumSize(640, 320)
            self._diagram.set_time(self._diagram.total_duration)   # static final frame, no timer
            dl.addWidget(self._diagram)
            root.addWidget(diagram_frame)

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
        return card

    def _note(self, text: str) -> QLabel:
        p = self._p
        lbl = QLabel(text); lbl.setWordWrap(True)
        lbl.setStyleSheet(f"color:{p.text_dim}; font-size:10.5px; border:none; margin-left:17px;")
        return lbl

    def _on_start(self):
        self.start_requested.emit()
        self.accept()
