"""Tests for the pure helpers in crash_log (no Qt required)."""

import sys
import tempfile
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from crash_log import format_entry, tag_qt_message, trim_to_tail


def test_format_entry_stamps_and_strips():
    now = datetime(2026, 7, 2, 12, 30, 45)
    out = format_entry("QT WARNING", "something happened\n\n", now=now)
    assert out == "[2026-07-02 12:30:45] QT WARNING\nsomething happened\n"


def test_tag_qt_message_flags_thread_lifetime():
    assert tag_qt_message("QThread: Destroyed while thread is still running") \
        == "[THREAD-LIFETIME] QThread: Destroyed while thread is still running"
    assert tag_qt_message("QWindowsWindow: SetGeometry failed") \
        == "QWindowsWindow: SetGeometry failed"


def test_trim_to_tail_keeps_small_files():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "crash.log"
        p.write_bytes(b"line one\nline two\n")
        trim_to_tail(p, max_bytes=1024)
        assert p.read_bytes() == b"line one\nline two\n"


def test_trim_to_tail_trims_large_files_on_line_boundary():
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "crash.log"
        lines = [f"entry {i:05d}\n".encode() for i in range(500)]
        p.write_bytes(b"".join(lines))
        total = p.stat().st_size
        trim_to_tail(p, max_bytes=total // 2)
        data = p.read_bytes()
        assert len(data) <= total // 4 + 16
        assert data.startswith(b"entry ")          # starts on a whole line
        assert data.endswith(b"entry 00499\n")     # tail preserved


def test_trim_to_tail_missing_file_is_noop():
    trim_to_tail(Path(tempfile.gettempdir()) / "does_not_exist_crash.log")


if __name__ == "__main__":
    test_format_entry_stamps_and_strips()
    test_tag_qt_message_flags_thread_lifetime()
    test_trim_to_tail_keeps_small_files()
    test_trim_to_tail_trims_large_files_on_line_boundary()
    test_trim_to_tail_missing_file_is_noop()
    print("test_crash_log: all tests passed")
