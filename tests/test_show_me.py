"""Tests for show_me.py — the 'Show me' merge animation.

The story builder is pure (clips + parameters → what goes where and why); the
canvas takes injectable time so the animation can be scrubbed without a timer.
Offscreen, standalone.
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

from show_me import (  # noqa: E402
    build_story, build_phases, ShowMeCanvas, ShowMeDialog, MAX_CARDS, INTRO_T)


def _clip(stem="VID_001", status="ok", wav=False, camera="Luna Ultra", conflicts=None):
    return SimpleNamespace(
        stem=stem, camera_label=camera,
        status=status,
        stream=SimpleNamespace(conflicts=conflicts or ([] if status == "ok" else ["29.95fps"])),
        has_wav=lambda w=wav: w,
    )


def _story(clips=None, **kw):
    defaults = dict(archival=True, per_clip_archival=True, optimize_baseline=False,
                    compat_baseline=False, audio_tracks=["camera", "wav"],
                    output_name="holiday.mov")
    defaults.update(kw)
    return build_story(clips if clips is not None else
                       [_clip(), _clip("VID_002", status="transcode", wav=True)], **defaults)


def test_story_reflects_conform_decisions():
    s = _story()
    assert s.clips[0].convert is False and s.clips[0].reason == ""
    assert s.clips[1].convert is True and s.clips[1].reason == "29.95fps"
    assert s.clips[1].has_wav is True and s.clips[0].has_wav is False
    assert s.output_name == "holiday.mov"
    print("ok: test_story_reflects_conform_decisions")


def test_optimize_converts_everything_with_its_own_reason():
    s = _story(optimize_baseline=True)
    assert all(c.convert for c in s.clips)
    assert s.clips[0].reason == "optimized for delivery"
    assert s.clips[1].reason == "29.95fps", "a real conflict keeps its own reason"
    print("ok: test_optimize_converts_everything_with_its_own_reason")


def test_archival_modes_drive_vault_slots():
    # per-clip: every clip gets its own vault box
    s = _story(archival=True, per_clip_archival=True)
    assert s.archival == "per-clip" and all(c.vault == "own" for c in s.clips)
    # grouped: only odd-spec clips get (shared) slots — the reel IS the copy for matching ones
    s2 = _story(archival=True, per_clip_archival=False)
    assert s2.archival == "grouped"
    assert s2.clips[0].vault is None and s2.clips[1].vault == "29.95fps"
    # off: nobody
    s3 = _story(archival=False)
    assert s3.archival == "off" and all(c.vault is None for c in s3.clips)
    print("ok: test_archival_modes_drive_vault_slots")


def test_many_clips_fold_into_a_more_card():
    clips = [_clip(f"VID_{i:03d}") for i in range(12)]
    s = _story(clips=clips)
    assert len(s.clips) == MAX_CARDS + 1
    assert s.clips[-1].name == "+4 more" and s.clips[-1].count == 4
    print("ok: test_many_clips_fold_into_a_more_card")


def test_phases_cover_every_clip_and_end_with_outro():
    s = _story(compat_baseline=True)
    phases = build_phases(s)
    # intro + one per clip + compat sweep + outro
    assert len(phases) == 1 + len(s.clips) + 1 + 1
    assert "copied onto the reel exactly" in phases[1].caption
    assert "converted" in phases[2].caption
    assert "ONE smooth take" in phases[-2].caption
    assert phases[-1].t1 > phases[-2].t1, "outro must extend the timeline"
    print("ok: test_phases_cover_every_clip_and_end_with_outro")


def test_canvas_scrubs_without_a_timer():
    s = _story()
    c = ShowMeCanvas(s)
    c.set_time(0.0)
    assert c.flight_progress(0) == 0.0 and c.flight_progress(1) == 0.0
    c.set_time(INTRO_T + 0.75)          # first card mid-flight
    assert 0.0 < c.flight_progress(0) < 1.0
    c.set_time(c.total_duration)
    assert c.flight_progress(0) == 1.0 and c.flight_progress(1) == 1.0
    assert "Done" in c.current_caption()
    print("ok: test_canvas_scrubs_without_a_timer")


def test_canvas_renders_at_any_time_offscreen():
    """Paint the canvas at several scrub points — a paint crash fails the test."""
    from PySide6.QtGui import QImage
    s = _story(compat_baseline=True, optimize_baseline=True)
    c = ShowMeCanvas(s)
    c.resize(900, 560)
    for t in (0.0, INTRO_T + 0.7, c.total_duration * 0.6, c.total_duration):
        c.set_time(t)
        img = QImage(c.size(), QImage.Format_ARGB32)
        c.render(img)
    print("ok: test_canvas_renders_at_any_time_offscreen")


def test_dialog_constructs_and_replays():
    s = _story()
    dlg = ShowMeDialog(s)
    try:
        assert dlg.canvas.total_duration > 0
        dlg.canvas.set_time(dlg.canvas.total_duration)
        dlg.replay()
        assert dlg.canvas._t == 0.0, "replay must rewind to the start"
    finally:
        dlg._timer.stop()
        dlg.close()
    print("ok: test_dialog_constructs_and_replays")


if __name__ == "__main__":
    test_story_reflects_conform_decisions()
    test_optimize_converts_everything_with_its_own_reason()
    test_archival_modes_drive_vault_slots()
    test_many_clips_fold_into_a_more_card()
    test_phases_cover_every_clip_and_end_with_outro()
    test_canvas_scrubs_without_a_timer()
    test_canvas_renders_at_any_time_offscreen()
    test_dialog_constructs_and_replays()
    print("test_show_me: all tests passed")
