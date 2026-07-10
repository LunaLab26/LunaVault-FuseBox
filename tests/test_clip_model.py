"""Tests for clip_model.ClipInfo's drift-override resolution.

Standalone, no ffmpeg/Qt dependency.
"""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from clip_model import ClipInfo, detect_clip_splits, scan_folder  # noqa: E402
from probe import StreamInfo  # noqa: E402


def _clip(**kw):
    return ClipInfo(path=Path("clip.mp4"), **kw)


def _split_clip(order_idx, creation_time, duration, wav_path=None, wav_duration=0.0):
    c = ClipInfo(path=Path(f"clip{order_idx}.mp4"),
                stream=StreamInfo(creation_time=creation_time, duration=duration))
    c.order_idx = order_idx
    if wav_path:
        c.wav_path = Path(wav_path)
        c.wav_duration = wav_duration
    return c


def test_effective_drift_ratio_defaults_to_auto_detected():
    c = _clip(sync_drift_ratio=1.0002)
    assert c.drift_override is None
    assert c.effective_drift_ratio() == 1.0002


def test_effective_drift_ratio_prefers_override_when_set():
    c = _clip(sync_drift_ratio=1.0002, drift_override=1.0)   # "Off" in the dialog
    assert c.effective_drift_ratio() == 1.0


def test_effective_drift_ratio_custom_override():
    c = _clip(sync_drift_ratio=1.0002, drift_override=0.9995)
    assert c.effective_drift_ratio() == 0.9995


def test_new_clip_defaults_are_auto_and_unoverridden():
    c = _clip()
    assert c.alignment_mode == "auto"
    assert c.drift_override is None


# ── detect_clip_splits ───────────────────────────────────────────────────────
# Mirrors the real shoot this was found on: clip A's WAV runs ~6m24s past its
# own video, matching clip B's video length almost exactly, while B has no WAV
# and starts right where A ends.

def test_detect_clip_splits_finds_the_real_world_pattern():
    a = _split_clip(0, "2026-07-07T18:32:04.000000Z", 1798.53,
                    wav_path="a_backup.wav", wav_duration=2183.09)
    b = _split_clip(1, "2026-07-07T19:02:04.000000Z", 384.15)   # adjacent, no WAV
    assert detect_clip_splits([a, b]) == [(a, b)]


def test_detect_clip_splits_requires_adjacency():
    a = _split_clip(0, "2026-07-07T18:32:04.000000Z", 1798.53,
                    wav_path="a_backup.wav", wav_duration=2183.09)
    # same combined-duration match, but a big gap before b starts
    b = _split_clip(1, "2026-07-07T19:30:00.000000Z", 384.15)
    assert detect_clip_splits([a, b]) == []


def test_detect_clip_splits_requires_duration_match():
    a = _split_clip(0, "2026-07-07T18:32:04.000000Z", 1798.53,
                    wav_path="a_backup.wav", wav_duration=1800.0)   # only a normal overrun
    b = _split_clip(1, "2026-07-07T19:02:04.000000Z", 384.15)
    assert detect_clip_splits([a, b]) == []


def test_detect_clip_splits_skips_when_b_already_has_a_wav():
    a = _split_clip(0, "2026-07-07T18:32:04.000000Z", 1798.53,
                    wav_path="a_backup.wav", wav_duration=2183.09)
    b = _split_clip(1, "2026-07-07T19:02:04.000000Z", 384.15,
                    wav_path="b_backup.wav", wav_duration=384.5)
    assert detect_clip_splits([a, b]) == []


def test_detect_clip_splits_skips_when_a_has_no_wav():
    a = _split_clip(0, "2026-07-07T18:32:04.000000Z", 1798.53)
    b = _split_clip(1, "2026-07-07T19:02:04.000000Z", 384.15)
    assert detect_clip_splits([a, b]) == []


def test_detect_clip_splits_falls_back_to_filename_ts_without_creation_time():
    a = ClipInfo(path=Path("a.mp4"), stream=StreamInfo(duration=1798.53),
                filename_ts=3600, wav_path=Path("a_backup.wav"), wav_duration=2183.09)
    a.order_idx = 0
    b = ClipInfo(path=Path("b.mp4"), stream=StreamInfo(duration=384.15),
                filename_ts=3600 + 1798)
    b.order_idx = 1
    assert detect_clip_splits([a, b]) == [(a, b)]


# ── effective_status / has_lrv (per-clip video-source override) ─────────────

def test_effective_status_defaults_to_real_status():
    c = _clip(stream=StreamInfo(status="ok"))
    assert c.video_source_override == "auto"
    assert c.effective_status() == "ok"


def test_effective_status_forced_transcode_overrides_ok():
    c = _clip(stream=StreamInfo(status="ok"), video_source_override="transcode")
    assert c.effective_status() == "transcode"


def test_effective_status_lrv_override_forces_transcode_when_lrv_paired():
    c = _clip(stream=StreamInfo(status="ok"), video_source_override="lrv",
             lrv_path=Path("clip.lrv"))
    assert c.effective_status() == "transcode"


def test_effective_status_lrv_override_falls_back_without_a_paired_proxy():
    c = _clip(stream=StreamInfo(status="ok"), video_source_override="lrv")   # no lrv_path
    assert c.effective_status() == "ok"


def test_effective_status_leaves_a_real_mismatch_alone():
    c = _clip(stream=StreamInfo(status="transcode", conflicts=["1280×720"]))
    assert c.effective_status() == "transcode"


def test_has_lrv_reflects_lrv_path():
    c = _clip()
    assert c.has_lrv() is False
    c.lrv_path = Path("clip.lrv")
    assert c.has_lrv() is True


# ── scan_folder: .lrv proxy pairing ──────────────────────────────────────────

def test_scan_folder_pairs_lrv_by_exact_stem():
    with tempfile.TemporaryDirectory() as d:
        folder = Path(d)
        (folder / "VID_20260707_190203_026.mp4").write_bytes(b"")
        (folder / "VID_20260707_190203_026.lrv").write_bytes(b"")
        clips = scan_folder(folder)
        assert len(clips) == 1
        assert clips[0].has_lrv()
        assert clips[0].lrv_path.name == "VID_20260707_190203_026.lrv"


def test_scan_folder_pairs_lrv_cross_brand_by_clip_key():
    with tempfile.TemporaryDirectory() as d:
        folder = Path(d)
        (folder / "VID_20260703_130055_00_004.mp4").write_bytes(b"")
        (folder / "LRV_20260703_130055_01_004.lrv").write_bytes(b"")
        clips = scan_folder(folder)
        assert len(clips) == 1
        assert clips[0].has_lrv()
        assert clips[0].lrv_path.name == "LRV_20260703_130055_01_004.lrv"


def test_scan_folder_no_lrv_leaves_it_unset():
    with tempfile.TemporaryDirectory() as d:
        folder = Path(d)
        (folder / "VID_20260707_190203_026.mp4").write_bytes(b"")
        clips = scan_folder(folder)
        assert len(clips) == 1
        assert not clips[0].has_lrv()


if __name__ == "__main__":
    test_effective_drift_ratio_defaults_to_auto_detected()
    test_effective_drift_ratio_prefers_override_when_set()
    test_effective_drift_ratio_custom_override()
    test_new_clip_defaults_are_auto_and_unoverridden()
    test_detect_clip_splits_finds_the_real_world_pattern()
    test_detect_clip_splits_requires_adjacency()
    test_detect_clip_splits_requires_duration_match()
    test_detect_clip_splits_skips_when_b_already_has_a_wav()
    test_detect_clip_splits_skips_when_a_has_no_wav()
    test_detect_clip_splits_falls_back_to_filename_ts_without_creation_time()
    test_effective_status_defaults_to_real_status()
    test_effective_status_forced_transcode_overrides_ok()
    test_effective_status_lrv_override_forces_transcode_when_lrv_paired()
    test_effective_status_lrv_override_falls_back_without_a_paired_proxy()
    test_effective_status_leaves_a_real_mismatch_alone()
    test_has_lrv_reflects_lrv_path()
    test_scan_folder_pairs_lrv_by_exact_stem()
    test_scan_folder_pairs_lrv_cross_brand_by_clip_key()
    test_scan_folder_no_lrv_leaves_it_unset()
    print("test_clip_model: all tests passed")
