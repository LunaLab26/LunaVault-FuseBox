"""Tests for extract_tab.py's Extract-tab manual controls (foreign masters
with no manifest): audio-track role assignment, video-stream picker, rotation
override, and hand-add/edit/remove of clip boundaries. Offscreen, standalone
— mirrors test_output_suggest.py's QApplication bootstrap.
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

_tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
import settings as settings_mod  # noqa: E402
settings_mod._settings_path = lambda: Path(_tmp.name)

from PySide6.QtWidgets import QApplication  # noqa: E402
app = QApplication.instance() or QApplication([])

from settings import Settings  # noqa: E402
from extract_tab import (  # noqa: E402
    ExtractTab, _ManualClipDialog, _tc_to_secs, _secs_to_ffmpeg,
    EX_COL_EDIT, EX_COL_REMOVE, EX_COL_SPEC,
)
from core.manifest import Manifest, ClipEntry  # noqa: E402
from probe import AudioTrackInfo, VideoTrackInfo, ChapterInfo  # noqa: E402


def _tab() -> ExtractTab:
    t = ExtractTab(Settings())
    t._extract_master_path = "foreign.mov"
    return t


def test_manifest_path_hides_manual_frame():
    t = _tab()
    m = Manifest(master_filename="master.mov", baseline_audio_tracks={"camera": 0, "wav": 1},
                clips=[ClipEntry(source_filename="A.mp4", duration=10.0, conform_status="ok",
                                 baseline_chapter_index=0, has_camera_audio=True, has_wav=True)])
    t._on_extract_manifest_ready(m, [], [], [])
    assert t._ex_manual_frame.isHidden()
    assert t._ex_extract_btn.isEnabled()
    assert len(t._extract_items) == 1
    assert not t._ex_ignore_manifest_check.isHidden()   # offered since a manifest WAS found


def test_ignore_manifest_checkbox_hidden_when_no_manifest():
    t = _tab()
    t._on_extract_manifest_ready(None, [], [AudioTrackInfo(audio_index=0, codec="aac")], [])
    assert t._ex_ignore_manifest_check.isHidden()   # nothing to opt out of


def test_ignore_manifest_toggle_switches_to_manual_mode_and_back():
    t = _tab()
    m = Manifest(master_filename="master.mov", baseline_audio_tracks={"camera": 0, "wav": 1},
                clips=[ClipEntry(source_filename="A.mp4", duration=10.0, conform_status="ok",
                                 baseline_chapter_index=0, has_camera_audio=True, has_wav=True)])
    chapters = [ChapterInfo(start=0.0, end=10.0, title="A")]
    audio_tracks = [AudioTrackInfo(audio_index=0, codec="aac")]
    t._on_extract_manifest_ready(m, chapters, audio_tracks, [])
    assert t._extract_generic_plans is None            # manifest mode by default
    assert t._ex_manual_frame.isHidden()

    t._ex_ignore_manifest_check.setChecked(True)
    assert t._extract_generic_plans is not None         # switched to the SAME probed chapters
    assert len(t._extract_generic_plans) == 1
    assert not t._ex_manual_frame.isHidden()
    assert len(t._extract_items) == 1                   # tree rebuilt from the generic plan

    t._ex_ignore_manifest_check.setChecked(False)
    assert t._extract_generic_plans is None             # back to manifest mode
    assert t._ex_manual_frame.isHidden()
    assert len(t._extract_items) == 1                   # tree rebuilt from the manifest again


def test_ignore_manifest_toggle_reflects_manual_edits():
    t = _tab()
    m = Manifest(master_filename="master.mov", baseline_audio_tracks={"camera": 0, "wav": 1},
                clips=[ClipEntry(source_filename="A.mp4", duration=10.0, conform_status="ok",
                                 baseline_chapter_index=0, has_camera_audio=True, has_wav=True)])
    t._on_extract_manifest_ready(m, [], [AudioTrackInfo(audio_index=0, codec="aac")], [])
    t._ex_ignore_manifest_check.setChecked(True)
    assert t._extract_generic_plans == []               # no chapters — starts empty, like a true no-manifest master
    t._commit_generic_plan(None, "hand_added_clip", 0.0, 5.0)
    assert len(t._extract_generic_plans) == 1
    assert t._ex_extract_btn.isEnabled()


def test_start_extract_dispatch_matches_active_mode_not_manifest_presence():
    # Regression: _start_extract must respect "ignore manifest" rather than
    # re-deriving mode from self._extract_manifest directly (which stays set
    # either way) — otherwise toggling the checkbox wouldn't actually change
    # which worker/plan set an Extract click uses.
    t = _tab()
    m = Manifest(master_filename="master.mov", baseline_audio_tracks={"camera": 0, "wav": 1},
                clips=[ClipEntry(source_filename="A.mp4", duration=10.0, conform_status="ok",
                                 baseline_chapter_index=0, has_camera_audio=True, has_wav=True)])
    chapters = [ChapterInfo(start=0.0, end=10.0, title="A")]
    t._on_extract_manifest_ready(m, chapters, [AudioTrackInfo(audio_index=0, codec="aac")], [])
    t._ex_ignore_manifest_check.setChecked(True)
    is_generic_mode = t._extract_generic_plans is not None
    assert is_generic_mode is True
    # Mirrors _start_extract's own has_manifest/has_generic derivation.
    has_manifest = not is_generic_mode and t._extract_manifest is not None and t._extract_manifest.clips
    has_generic = is_generic_mode and bool(t._extract_generic_plans)
    assert has_manifest is False and has_generic is True


def test_chapterless_foreign_master_shows_manual_controls_and_starts_empty():
    t = _tab()
    audio_tracks = [AudioTrackInfo(audio_index=0, codec="pcm_s16le", channels=2, sample_rate=48000)]
    t._on_extract_manifest_ready(None, [], audio_tracks, [])
    assert not t._ex_manual_frame.isHidden()
    assert t._extract_generic_plans == []
    assert not t._ex_extract_btn.isEnabled()          # nothing to extract yet
    assert len(t._ex_audio_role_combos) == 1


def test_video_track_combo_only_appears_with_multiple_streams():
    t = _tab()
    audio_tracks = [AudioTrackInfo(audio_index=0, codec="aac")]
    one_video = [VideoTrackInfo(video_index=0, codec="h264", width=1920, height=1080)]
    t._on_extract_manifest_ready(None, [], audio_tracks, one_video)
    assert t._ex_video_track_combo is None

    t2 = _tab()
    two_video = one_video + [VideoTrackInfo(video_index=1, codec="hevc", width=3840, height=2160)]
    t2._on_extract_manifest_ready(None, [], audio_tracks, two_video)
    assert t2._ex_video_track_combo is not None


def test_audio_role_assignment_applies_to_every_plan():
    t = _tab()
    audio_tracks = [AudioTrackInfo(audio_index=0, codec="pcm_s16le"),
                   AudioTrackInfo(audio_index=1, codec="aac")]
    chapters = [ChapterInfo(start=0.0, end=5.0, title="a"), ChapterInfo(start=5.0, end=9.0, title="b")]
    t._on_extract_manifest_ready(None, chapters, audio_tracks, [])
    # Default: track 0 = camera (build_generic_recovery_plans' own default), no WAV role.
    assert all(p.audio_stream == 0 and p.wav_stream is None for p in t._extract_generic_plans)

    # Reassign: track 1 becomes the WAV-backup role.
    combo1 = t._ex_audio_role_combos[1]
    combo1.setCurrentIndex(combo1.findData("wav"))
    assert all(p.wav_stream == 1 for p in t._extract_generic_plans)
    assert all(p.audio_stream == 0 for p in t._extract_generic_plans)   # camera role untouched


def test_rotation_override_applies_to_every_plan():
    t = _tab()
    chapters = [ChapterInfo(start=0.0, end=5.0, title="a")]
    t._on_extract_manifest_ready(None, chapters, [], [])
    assert t._extract_generic_plans[0].rotation is None
    t._ex_rotation_combo.setCurrentIndex(t._ex_rotation_combo.findData(90))
    assert t._extract_generic_plans[0].rotation == 90
    # Back to Auto clears the override rather than leaving a stale rotation.
    t._ex_rotation_combo.setCurrentIndex(0)
    assert t._extract_generic_plans[0].rotation is None


def test_add_edit_remove_generic_plan_round_trip():
    t = _tab()
    t._on_extract_manifest_ready(None, [], [], [])   # chapterless — starts empty
    assert t._extract_generic_plans == []

    t._commit_generic_plan(None, "my_clip", 5.0, 10.0)
    assert len(t._extract_generic_plans) == 1
    assert t._ex_extract_btn.isEnabled()
    plan = t._extract_generic_plans[0]
    assert (plan.title, plan.start, plan.duration) == ("my_clip", 5.0, 10.0)
    edit_widget = t._ex_tree.itemWidget(t._extract_items[0], EX_COL_EDIT)
    remove_widget = t._ex_tree.itemWidget(t._extract_items[0], EX_COL_REMOVE)
    assert edit_widget is not None and remove_widget is not None

    t._commit_generic_plan(0, "renamed_clip", 5.0, 20.0)
    assert len(t._extract_generic_plans) == 1   # edit, not a second add
    assert t._extract_generic_plans[0].title == "renamed_clip"
    assert t._extract_generic_plans[0].duration == 20.0

    t._remove_generic_plan(0)
    assert t._extract_generic_plans == []
    assert not t._ex_extract_btn.isEnabled()


def test_added_plan_carries_current_manual_settings():
    t = _tab()
    audio_tracks = [AudioTrackInfo(audio_index=0, codec="pcm_s16le"),
                   AudioTrackInfo(audio_index=1, codec="aac")]
    two_video = [VideoTrackInfo(video_index=0, codec="h264", width=1920, height=1080),
                VideoTrackInfo(video_index=1, codec="hevc", width=3840, height=2160)]
    t._on_extract_manifest_ready(None, [], audio_tracks, two_video)
    t._ex_audio_role_combos[0].setCurrentIndex(t._ex_audio_role_combos[0].findData("wav"))
    t._ex_audio_role_combos[1].setCurrentIndex(t._ex_audio_role_combos[1].findData("camera"))
    t._ex_video_track_combo.setCurrentIndex(t._ex_video_track_combo.findData(1))
    t._ex_rotation_combo.setCurrentIndex(t._ex_rotation_combo.findData(180))

    t._commit_generic_plan(None, "clip_x", 0.0, 5.0)
    plan = t._extract_generic_plans[0]
    assert plan.audio_stream == 1 and plan.wav_stream == 0
    assert plan.video_stream == 1
    assert plan.rotation == 180


def test_spec_column_reflects_manual_wav_and_rotation():
    t = _tab()
    audio_tracks = [AudioTrackInfo(audio_index=0, codec="aac"), AudioTrackInfo(audio_index=1, codec="pcm_s16le")]
    chapters = [ChapterInfo(start=0.0, end=5.0, title="a")]
    t._on_extract_manifest_ready(None, chapters, audio_tracks, [])
    t._ex_audio_role_combos[1].setCurrentIndex(t._ex_audio_role_combos[1].findData("wav"))
    t._ex_rotation_combo.setCurrentIndex(t._ex_rotation_combo.findData(90))
    spec_text = t._extract_items[0].text(EX_COL_SPEC)
    assert "WAV: track 1" in spec_text
    assert "rotation: 90" in spec_text


def test_tc_to_secs_and_secs_to_ffmpeg_round_trip():
    assert _tc_to_secs("0:01:30") == 90.0
    assert _tc_to_secs("garbage") == 0.0
    assert _secs_to_ffmpeg(90.0) == "00:01:30.000"


def test_manual_clip_dialog_values_parses_fields():
    dlg = _ManualClipDialog(name="clip_a", start=12.0, duration=8.0)
    name, start, duration = dlg.values()
    assert name == "clip_a"
    assert abs(start - 12.0) < 1.0     # seconds-precision text field, sub-second not preserved
    assert abs(duration - 8.0) < 1.0


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("test_extract_manual_mode: all tests passed")
