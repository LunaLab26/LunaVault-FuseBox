"""Smoke test for add_flow.py — the guided seven-moment Add flow.

Constructs the flow offscreen and drives it through welcome → choose → found,
asserting screens exist and navigate. The proof and merge steps need real ffmpeg
+ footage (covered by the engine's own tests), so they're not run here.
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from PySide6.QtWidgets import QApplication  # noqa: E402
app = QApplication.instance() or QApplication([])

from settings import Settings  # noqa: E402
from add_flow import AddFlow  # noqa: E402


def test_construct_and_navigate_early_screens():
    flow = AddFlow(Settings())
    assert flow.currentWidget() is flow._welcome, "should open on welcome"
    # it holds a hidden engine
    assert flow._mt is not None and flow._mt.isVisible() is False

    # welcome → choose
    flow.setCurrentWidget(flow._choose)
    assert flow.currentWidget() is flow._choose

    # a folder with a couple of (empty) video-named files → 'what was found'
    with tempfile.TemporaryDirectory() as d:
        (Path(d) / "VID_01.mp4").write_bytes(b"")
        (Path(d) / "VID_02.mov").write_bytes(b"")
        (Path(d) / "notes.txt").write_bytes(b"")   # ignored — not a video ext
        flow.load_folder(d)
        assert flow.currentWidget() is flow._found
        assert "Found 2 videos" in flow._found_h.text()
        assert flow._name_edit.text() == Path(d).name
    flow.shutdown()
    print("ok: test_construct_and_navigate_early_screens")


def test_finished_signal_emits_master_path():
    flow = AddFlow(Settings())
    got = []
    flow.finished.connect(lambda p: got.append(p))
    flow._master = "G:/Memories/Pool day/Pool day.mov"
    flow._done()
    assert got == ["G:/Memories/Pool day/Pool day.mov"]
    flow.shutdown()
    print("ok: test_finished_signal_emits_master_path")


if __name__ == "__main__":
    test_construct_and_navigate_early_screens()
    test_finished_signal_emits_master_path()
    print("test_add_flow: all tests passed")
