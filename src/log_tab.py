"""log_tab.py — Export log viewer tab."""

import os
from pathlib import Path
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QTableWidget, QTableWidgetItem, QHeaderView, QAbstractItemView,
    QTextEdit, QSplitter, QMessageBox, QCheckBox, QFileDialog,
)
from PySide6.QtGui import QColor, QFont

import log_manager
from settings import Settings
import theme


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
    def __init__(self, settings: Settings):
        super().__init__()
        self._settings = settings
        self._entries: list = []
        self._setup_ui()
        self._restyle()
        self.refresh()
        ctrl = theme.controller()
        if ctrl is not None:
            ctrl.changed.connect(self._restyle)

    def _restyle(self):
        p = theme.active_palette()
        self._detail_lbl.setStyleSheet(f"font-weight:bold; color:{p.text_mute};")
        self._status_lbl.setStyleSheet(f"color:{p.text_mute}; font-size:11px;")
        # Status-cell colours are set when the table is populated — refresh them.
        if self._entries:
            self._populate_table()

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

        self._autosave_check = QCheckBox("Auto-save .txt on failure")
        self._autosave_check.setToolTip(
            "When a merge or export fails, automatically save a timestamped .txt copy of "
            "its log entry (to the app's failure_logs folder) — so a diagnostic survives "
            "even if you never open this tab.")
        self._autosave_check.setChecked(self._settings.get("auto_save_log_on_failure", True))
        self._autosave_check.toggled.connect(
            lambda on: self._settings.set("auto_save_log_on_failure", on))
        hdr.addWidget(self._autosave_check)

        export_btn = QPushButton("Export…")
        export_btn.setFixedWidth(90)
        export_btn.setToolTip("Save the selected entry (or the whole log, if none is selected) "
                              "as a .txt file.")
        export_btn.clicked.connect(self._export_log)
        hdr.addWidget(export_btn)

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
        self._detail_lbl = detail_lbl
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
            p = theme.active_palette()
            status_item = self._table.item(row, COL_STATUS)
            status_item.setForeground(QColor(p.ok if success else p.danger))

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
        self._detail.setText(log_manager.render_entry_text(entry))

    # ── Export ────────────────────────────────────────────────────────────────

    def _export_log(self):
        rows = self._table.selectionModel().selectedRows()
        if rows and rows[0].row() < len(self._entries):
            entry = self._entries[rows[0].row()]
            text = log_manager.render_entry_text(entry)
            ts = entry.get("timestamp", "").replace(":", "").replace("-", "")
            default_name = f"{entry.get('type','export')}_log_{ts or 'entry'}.txt"
        else:
            text = "\n\n".join(log_manager.render_entry_text(e) for e in self._entries) or "(log is empty)"
            default_name = "lunavault_export_log.txt"

        path, _ = QFileDialog.getSaveFileName(self, "Export log", default_name, "Text files (*.txt)")
        if not path:
            return
        try:
            Path(path).write_text(text, encoding="utf-8")
        except Exception as e:
            QMessageBox.warning(self, "Export failed", f"Could not save the log:\n{e}")

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
