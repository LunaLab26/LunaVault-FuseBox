"""Regression test for the Merge tab's per-clip preview dialog.

The preview popup played nothing (black window) because it passed a raw Windows
path to QMediaPlayer.setSource, which parses "C:\\…" as a URL whose *scheme* is
"c" — so the media never loads. It must use a proper local-file URL. Offscreen,
standalone.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from PySide6.QtWidgets import QApplication  # noqa: E402
app = QApplication.instance() or QApplication([])

import theme  # noqa: E402
from settings import Settings  # noqa: E402
theme.init_controller(app, Settings())

from merge_tab import _ClipPreviewDialog  # noqa: E402


def test_preview_uses_local_file_url():
    dlg = _ClipPreviewDialog(r"C:\videos\preview_abc.mp4", "Clip 1")
    try:
        src = dlg._player.source()
        assert src.isLocalFile(), "preview must load via a file:// URL, not a raw path"
        assert src.scheme() == "file", f"expected file scheme, got {src.scheme()!r}"
        # the drive-letter path must survive intact (not swallowed as a scheme)
        assert src.toLocalFile().lower().endswith("preview_abc.mp4")
    finally:
        dlg._player.stop()
        dlg.close()
    print("ok: test_preview_uses_local_file_url")


def test_forward_slash_path_also_local():
    dlg = _ClipPreviewDialog("/tmp/preview_xyz.mp4", "Clip 2")
    try:
        assert dlg._player.source().isLocalFile()
    finally:
        dlg._player.stop()
        dlg.close()
    print("ok: test_forward_slash_path_also_local")


if __name__ == "__main__":
    test_preview_uses_local_file_url()
    test_forward_slash_path_also_local()
    print("test_clip_preview: all tests passed")
