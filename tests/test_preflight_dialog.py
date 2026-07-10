"""Tests for preflight_dialog.py's embedded Story diagram (Task 92-3).

Offscreen Qt; the diagram is ShowMeCanvas frozen at its final frame (see
show_me.py) — these tests confirm it embeds correctly and stays optional/
backward-compatible, not the canvas's own rendering (covered by test_show_me.py).
"""

import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from PySide6.QtWidgets import QApplication  # noqa: E402
app = QApplication.instance() or QApplication([])

import theme  # noqa: E402
from settings import Settings  # noqa: E402
theme.init_controller(app, Settings())

from show_me import build_story  # noqa: E402
from core.plan_report import MergeReport, ClipReport, TrackPlan  # noqa: E402
from preflight_dialog import PreflightDialog  # noqa: E402


def _story_clip(stem="VID_001", status="ok", wav=True):
    return SimpleNamespace(
        stem=stem, camera_label="Luna Ultra", status=status,
        stream=SimpleNamespace(conflicts=[] if status == "ok" else ["29.95fps"]),
        has_wav=lambda w=wav: w,
    )


def _story():
    clips = [_story_clip(), _story_clip("VID_002", status="transcode", wav=False)]
    return build_story(clips, archival=True, per_clip_archival=True, optimize_baseline=False,
                       compat_baseline=False, audio_tracks=["camera", "wav"],
                       output_name="test.mov")


def _empty_report():
    return MergeReport(clips=[], total_bytes=0, best_secs=0, worst_secs=0,
                       n_transcode=0, n_slowmo=0, n_no_camera=0)


def _report_with_clips():
    return MergeReport(
        clips=[
            ClipReport(name="VID_001", duration=60.0, has_wav=True, video_included=True,
                      video_action="Stream copy", is_slowmo=False, slowmo_factor=1.0,
                      audio=[TrackPlan(label="Camera mic", out_codec="AAC", lossless=False,
                                       role="primary", est_bytes=1000)],
                      notes=[], est_bytes=50_000_000, best_secs=2.0, worst_secs=5.0),
            ClipReport(name="VID_002", duration=30.0, has_wav=False, video_included=True,
                      video_action="Transcode → 4K HEVC", is_slowmo=False, slowmo_factor=1.0,
                      audio=[], notes=["no camera audio"], est_bytes=80_000_000,
                      best_secs=10.0, worst_secs=30.0),
        ],
        total_bytes=130_000_000, best_secs=12.0, worst_secs=35.0,
        n_transcode=1, n_slowmo=0, n_no_camera=1)


def test_no_story_means_no_diagram_and_the_original_minimum_size():
    dlg = PreflightDialog(_empty_report())
    assert not hasattr(dlg, "_diagram")
    assert (dlg.minimumSize().width(), dlg.minimumSize().height()) == (560, 480)
    print("ok: test_no_story_means_no_diagram_and_the_original_minimum_size")


def test_story_embeds_a_diagram_frozen_at_its_final_frame():
    dlg = PreflightDialog(_report_with_clips(), free_bytes=500_000_000_000,
                          need_bytes=260_000_000, story=_story())
    assert hasattr(dlg, "_diagram")
    assert dlg._diagram._t == dlg._diagram.total_duration
    # room was made for the wider embedded canvas vs. the story=None case
    assert dlg.minimumSize().width() >= 700
    print("ok: test_story_embeds_a_diagram_frozen_at_its_final_frame")


def test_diagram_renders_without_crashing_and_paints_real_content():
    from PySide6.QtGui import QImage
    import numpy as np
    dlg = PreflightDialog(_report_with_clips(), story=_story())
    dlg._diagram.resize(900, 400)
    img = QImage(dlg._diagram.size(), QImage.Format_ARGB32)
    dlg._diagram.render(img)
    ptr = img.constBits()
    arr = np.frombuffer(bytes(ptr), dtype=np.uint8).reshape(
        img.height(), img.bytesPerLine())[:, : img.width() * 4]
    assert arr.astype(float).std() > 1.0   # not a flat/blank image
    print("ok: test_diagram_renders_without_crashing_and_paints_real_content")


if __name__ == "__main__":
    test_no_story_means_no_diagram_and_the_original_minimum_size()
    test_story_embeds_a_diagram_frozen_at_its_final_frame()
    test_diagram_renders_without_crashing_and_paints_real_content()
    print("test_preflight_dialog: all tests passed")
