"""log_tab.py — Export log viewer tab."""

import os
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QTextEdit, QSplitter, QMessageBox,
)
from PySide6.QtGui import QColor, QFont

import log_manager


# Column indices
COL_TIME   = 0
COL_TYPE   = 1
COL_OUTPUT = 2
COL_DUR    = 3
COL_GRADE  = 4
COL_SIZE   = 5
COL_STATUS = 6

HEADERS = ["Time", "Type", "Output", "Duration", "Grade", "Size (MB)", "Status"]


def _fmt_dur(secs: float) -> str:
    if secs <= 0:
        return "—"
    h, r = divmod(int(secs), 3600)
    m, s = divmod(r, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


class LogTab(QWidget):
    def __init__(self):
        super().__init__()
        self._entries: list = []
        self._setup_ui()
        self.refresh()

    def _setup_ui(self):
        root = QVBoxLayout(self)
        root.setSpacing(8)
        root.setContentsMargins(14, 14, 14, 14)

        # Header row
        hdr = QHBoxLayout()
        title = QLabel("Export Log")
        font = QFont()
        font.setPointSize(13)
        font.setBold(True)
        title.setFont(font)
        hdr.addWidget(title)
        hdr.addStretch()

        refresh_btn = QPushButton("⟳  Refresh")
        refresh_btn.setFixedWidth(100)
        refresh_btn.clicked.connect(self.refresh)
        hdr.addWidget(refresh_btn)

        clear_btn = QPushButton("Clear log")
        clear_btn.setFixedWidth(100)
        clear_btn.clicked.connect(self._clear_log)
        hdr.addWidget(clear_btn)
        root.addLayout(hdr)

        # Splitter: table on top, detail panel below
        splitter = QSplitter(Qt.Orientation.Vertical)

        # Table
        self._table = QTableWidget(0, len(HEADERS))
        self._table.setHorizontalHeaderLabels(HEADERS)
        self._table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setVisible(False)
        self._table.horizontalHeader().setSectionResizeMode(COL_OUTPUT, QHeaderView.ResizeMode.Stretch)
        for col in (COL_TIME, COL_TYPE, COL_DUR, COL_GRADE, COL_SIZE, COL_STATUS):
            self._table.horizontalHeader().setSectionResizeMode(
                col, QHeaderView.ResizeMode.ResizeToContents
            )
        self._table.selectionModel().selectionChanged.connect(self._on_selection)
        splitter.addWidget(self._table)

        # Detail panel
        detail_frame = QWidget()
        detail_layout = QVBoxLayout(detail_frame)
        detail_layout.setContentsMargins(0, 4, 0, 0)
        detail_lbl = QLabel("Details")
        detail_lbl.setStyleSheet("font-weight:bold; color:#aaa;")
        detail_layout.addWidget(detail_lbl)
        self._detail = QTextEdit()
        self._detail.setReadOnly(True)
        self._detail.setMaximumHeight(180)
        self._detail.setStyleSheet("font-family: monospace; font-size: 11px;")
        detail_layout.addWidget(self._detail)
        splitter.addWidget(detail_frame)

        splitter.setSizes([400, 180])
        root.addWidget(splitter, 1)

        # Status bar
        self._status_lbl = QLabel()
        self._status_lbl.setStyleSheet("color:#888; font-size:11px;")
        root.addWidget(self._status_lbl)

    # ── Data loading ──────────────────────────────────────────────────────────

    def refresh(self):
        """Reload log entries from disk."""
        self._entries = log_manager.load_log()
        self._populate_table()

    def _populate_table(self):
        self._table.setRowCount(0)
        for entry in self._entries:
            row = self._table.rowCount()
            self._table.insertRow(row)

            t = entry.get("type", "?")
            ts = entry.get("timestamp", "")[:16].replace("T", " ")
            output = entry.get("output", "")
            success = entry.get("success", False)

            if t == "whatsapp":
                dur_s = entry.get("duration", "—")
                grade = entry.get("grade", "None")
            else:
                dur_s = _fmt_dur(entry.get("total_duration_secs", 0))
                grade = entry.get("track_order", "—")

            size_mb = entry.get("file_size_mb", 0)
            status_text = "✓  OK" if success else "✗  Failed"

            items = [
                ts,
                t.capitalize(),
                Path(output).name if output else "—",
                str(dur_s),
                grade,
                f"{size_mb:.1f}" if size_mb else "—",
                status_text,
            ]

            for col, text in enumerate(items):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
                self._table.setItem(row, col, item)

            # Colour the status cell
            status_item = self._table.item(row, COL_STATUS)
            if success:
                status_item.setForeground(QColor("#2ecc71"))
            else:
                status_item.setForeground(QColor("#e74c3c"))

        n = len(self._entries)
        ok  = sum(1 for e in self._entries if e.get("success"))
        self._status_lbl.setText(f"{n} entries  ·  {ok} succeeded  ·  {n - ok} failed")

    def _on_selection(self):
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            self._detail.clear()
            return
        idx = rows[0].row()
        if idx >= len(self._entries):
            return
        entry = self._entries[idx]
        self._render_detail(entry)

    def _render_detail(self, entry: dict):
        lines = []
        t = entry.get("type", "?")
        lines.append(f"{'─'*60}")
        lines.append(f"  {t.upper()} EXPORT — {entry.get('timestamp','')}")
        lines.append(f"{'─'*60}")
        lines.append(f"  Output  : {entry.get('output','—')}")
        lines.append(f"  Source  : {entry.get('source', entry.get('source_folder','—'))}")
        lines.append(f"  Size    : {entry.get('file_size_mb', 0):.2f} MB")
        lines.append(f"  Status  : {'OK' if entry.get('success') else 'FAILED — ' + entry.get('message','')}")

        if t == "whatsapp":
            lines.append(f"  Start   : {entry.get('start','—')}")
            lines.append(f"  Duration: {entry.get('duration','—')}")
            lines.append(f"  Grade   : {entry.get('grade','None')}")

        elif t == "merge":
            lines.append(f"  Folder  : {entry.get('source_folder','—')}")
            lines.append(f"  Clips   : {entry.get('clip_count',0)}    Total: {_fmt_dur(entry.get('total_duration_secs',0))}")
            mix = entry.get("mix", {}) or {}
            if mix.get("mix_enabled") or mix.get("enabled"):
                lines.append(f"  Mix     : on  ·  {mix.get('kind','lr')}"
                             + ("  ·  level-matched" if mix.get('match_levels') else ""))
            if mix.get("include_video") is False:
                lines.append("  Video   : excluded from output")
            lines.append("")
            lines.append("  Per-clip arrangement and sync:")
            clips = entry.get("clips", [])
            if not clips:
                lines.append("    —")
            for c in clips:
                name = c.get("name", "?")
                arr  = c.get("arrangement") or {}
                lines.append("")
                head = f"  • {name}"
                if arr.get("video"):
                    head += f"   [video: {arr['video']}]"
                lines.append(head)

                tracks = arr.get("tracks") or []
                for tk in tracks:
                    role = "  (default)" if tk.get("role") == "primary" else ""
                    loss = "lossless" if tk.get("lossless") else "lossy"
                    lines.append(f"      - {tk.get('label','?')}  [{tk.get('codec','')} · {loss}]{role}")

                for note in (arr.get("decisions") or []):
                    lines.append(f"        → {note}")

                # Sync numbers (constant offset on lossless; drift on the mix only)
                if c.get("has_wav") and not arr.get("is_slowmo"):
                    off = c.get("audio_offset_ms")
                    drift = c.get("drift_ms_per_min")
                    conf = c.get("confidence_ms")
                    pol = c.get("polarity_inverted")
                    bits = []
                    if off is not None:   bits.append(f"offset {off:+.1f} ms")
                    if drift is not None: bits.append(f"drift {drift:+.1f} ms/min")
                    if conf is not None:  bits.append(f"±{conf:.1f} ms")
                    if pol:               bits.append("polarity flipped")
                    if bits:
                        lines.append(f"        sync: {'  ·  '.join(bits)}")
                elif arr.get("is_slowmo"):
                    f = arr.get("slowmo_factor")
                    lines.append(f"        sync: slow-motion {f:.1f}× — WAV stretched, pitch-corrected"
                                 if f else "        sync: slow-motion — WAV stretched")

        self._detail.setText("\n".join(lines))

    # ── Clear log ─────────────────────────────────────────────────────────────

    def _clear_log(self):
        reply = QMessageBox.question(
            self, "Clear log",
            "Delete all log entries?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if reply == QMessageBox.StandardButton.Yes:
            p = log_manager._log_path()
            if p.exists():
                p.unlink()
            self.refresh()
