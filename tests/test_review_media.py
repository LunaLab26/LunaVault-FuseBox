"""Tests for core.review_media — ffmpeg command builders for the Review tab."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.review_media import (
    build_frame_extract_cmd, build_snapshot_cmd, snapshot_filename,
    mix_cache_key, build_review_mix_cmd, _split_seek, build_thumbnail_strip_cmd,
    build_proxy_cmd, proxy_cache_path,
)


def test_split_seek_sums_to_requested_timestamp():
    coarse, fine = _split_seek(125.0)
    assert abs((coarse + fine) - 125.0) < 1e-9
    assert coarse >= 0.0
    assert fine >= 0.0


def test_split_seek_handles_timestamps_shorter_than_the_lead_in():
    coarse, fine = _split_seek(0.5)
    assert coarse == 0.0
    assert fine == 0.5


def test_build_frame_extract_cmd_seeks_twice_and_grabs_one_frame():
    cmd = build_frame_extract_cmd("ffmpeg", "master.mov", 90.0, pix_fmt="rgb48le")
    assert cmd.count("-ss") == 2
    assert "-frames:v" in cmd and cmd[cmd.index("-frames:v") + 1] == "1"
    assert "-pix_fmt" in cmd and cmd[cmd.index("-pix_fmt") + 1] == "rgb48le"
    assert cmd[-1] == "pipe:1"
    assert "rawvideo" in cmd


def test_build_frame_extract_cmd_includes_size_when_given():
    cmd = build_frame_extract_cmd("ffmpeg", "master.mov", 10.0, width=3840, height=2160)
    assert "-s" in cmd
    assert cmd[cmd.index("-s") + 1] == "3840x2160"


def test_build_snapshot_cmd_preserves_16bit_and_writes_png():
    cmd = build_snapshot_cmd("ffmpeg", "master.mov", 42.0, "out.png")
    assert cmd[cmd.index("-pix_fmt") + 1] == "rgb48be"
    assert cmd[-1] == "out.png"
    assert "-y" in cmd


def test_snapshot_filename_uses_stem_and_padded_frame_index():
    with tempfile.TemporaryDirectory() as td:
        tmp_dir = Path(td)
        out = snapshot_filename(str(tmp_dir / "20260628 - pool day.mov"), 42)
        assert out.name == "20260628 - pool day_f000042.png"
        assert out.parent == tmp_dir


def test_snapshot_filename_suffixes_on_collision():
    with tempfile.TemporaryDirectory() as td:
        master = Path(td) / "master.mov"
        first = snapshot_filename(str(master), 5)
        first.write_bytes(b"x")
        second = snapshot_filename(str(master), 5)
        assert second != first
        assert second.name == "master_f000005_1.png"


def test_mix_cache_key_is_order_independent():
    assert mix_cache_key([2, 0, 1]) == mix_cache_key([0, 1, 2])
    assert mix_cache_key([0, 0, 1]) == mix_cache_key([0, 1])   # de-duplicated
    assert mix_cache_key([2]) == "2"


def test_build_review_mix_cmd_single_track_still_reencodes_uniformly():
    cmd = build_review_mix_cmd("ffmpeg", "master.mov", [1], "out.m4a")
    assert "-map" in cmd and cmd[cmd.index("-map") + 1] == "0:a:1"
    assert "-c:a" in cmd and cmd[cmd.index("-c:a") + 1] == "aac"
    assert "-filter_complex" not in cmd


def test_build_review_mix_cmd_multi_track_uses_amix():
    cmd = build_review_mix_cmd("ffmpeg", "master.mov", [0, 2], "out.m4a")
    assert "-filter_complex" in cmd
    filt = cmd[cmd.index("-filter_complex") + 1]
    assert "[0:a:0]" in filt and "[0:a:2]" in filt
    assert "amix=inputs=2" in filt
    assert "-map" in cmd and cmd[cmd.index("-map") + 1] == "[mix]"


def test_build_review_mix_cmd_rejects_empty_selection():
    try:
        build_review_mix_cmd("ffmpeg", "master.mov", [], "out.m4a")
    except ValueError:
        return
    raise AssertionError("expected ValueError for an empty track selection")


def test_thumbnail_strip_cmd_skips_to_nearest_keyframe():
    # Measured directly on a real 4K 10-bit HEVC clip: without -skip_frame nokey
    # a single-frame extract took 1.1s-5.2s depending on position (worse later
    # in the file); with it, every position took ~0.5s flat. A thumbnail tile
    # is a rough filmstrip marker, not a precision reading, so trading a
    # possible one-GOP timestamp drift for a ~10x speedup is the right call.
    cmd = build_thumbnail_strip_cmd("ffmpeg", "master.mov", 42.0, "out.jpg", width=160)
    assert "-skip_frame" in cmd and cmd[cmd.index("-skip_frame") + 1] == "nokey"
    assert cmd.index("-skip_frame") < cmd.index("-i")   # must apply to the input, before -i
    assert "-frames:v" in cmd and cmd[cmd.index("-frames:v") + 1] == "1"
    assert cmd[-1] == "out.jpg"


def test_thumbnail_strip_cmd_forces_full_range_for_the_mjpeg_encoder():
    # Real bug, found against actual camera footage: ffmpeg's mjpeg encoder
    # REJECTS standard "tv"/limited-range yuv420p ("Non full-range YUV is
    # non-standard") — which is what virtually every real clip is — failing
    # silently under -v quiet (returncode -22, empty stderr, zero thumbnails
    # ever produced). format=yuvj420p forces full-range before the encode.
    cmd = build_thumbnail_strip_cmd("ffmpeg", "master.mov", 3.0, "out.jpg", width=160)
    vf = cmd[cmd.index("-vf") + 1]
    assert "format=yuvj420p" in vf


def test_build_proxy_cmd_preserves_audio_track_order():
    # -map 0:v:0 then -map 0:a (all audio, in source order) — must match
    # probe.probe_audio_tracks' audio_index numbering so
    # PlaybackEngine.set_audio_single(track_idx) behaves the same on the
    # proxy as on the master.
    cmd = build_proxy_cmd("ffmpeg", "master.mov", "proxy.mp4", height=480)
    assert cmd.count("-map") == 2
    assert cmd[cmd.index("-map") + 1] == "0:v:0"
    assert cmd[cmd.index("-map", cmd.index("-map") + 1) + 1] == "0:a?"
    assert "-c:v" in cmd and cmd[cmd.index("-c:v") + 1] == "libx264"
    assert "-pix_fmt" in cmd and cmd[cmd.index("-pix_fmt") + 1] == "yuv420p"
    assert cmd[-1] == "proxy.mp4"


def test_build_proxy_cmd_scale_never_upsamples():
    cmd = build_proxy_cmd("ffmpeg", "master.mov", "proxy.mp4", height=480)
    vf = cmd[cmd.index("-vf") + 1]
    assert "min(480" in vf and "ih)" in vf


def test_proxy_cache_path_is_deterministic_for_the_same_file():
    with tempfile.TemporaryDirectory() as td:
        master = Path(td) / "master.mov"
        master.write_bytes(b"x" * 1000)
        cache_dir = Path(td) / "cache"
        p1 = proxy_cache_path(cache_dir, str(master))
        p2 = proxy_cache_path(cache_dir, str(master))
        assert p1 == p2
        assert p1.parent == cache_dir
        assert p1.suffix == ".mp4"


def test_proxy_cache_path_changes_when_the_file_changes():
    with tempfile.TemporaryDirectory() as td:
        master = Path(td) / "master.mov"
        cache_dir = Path(td) / "cache"
        master.write_bytes(b"x" * 1000)
        before = proxy_cache_path(cache_dir, str(master))
        master.write_bytes(b"y" * 2000)   # different size -> different signature
        after = proxy_cache_path(cache_dir, str(master))
        assert before != after


def _run_all():
    test_split_seek_sums_to_requested_timestamp()
    test_split_seek_handles_timestamps_shorter_than_the_lead_in()
    test_build_frame_extract_cmd_seeks_twice_and_grabs_one_frame()
    test_build_frame_extract_cmd_includes_size_when_given()
    test_build_snapshot_cmd_preserves_16bit_and_writes_png()
    test_snapshot_filename_uses_stem_and_padded_frame_index()
    test_snapshot_filename_suffixes_on_collision()
    test_mix_cache_key_is_order_independent()
    test_build_review_mix_cmd_single_track_still_reencodes_uniformly()
    test_build_review_mix_cmd_multi_track_uses_amix()
    test_build_review_mix_cmd_rejects_empty_selection()
    test_thumbnail_strip_cmd_skips_to_nearest_keyframe()
    test_thumbnail_strip_cmd_forces_full_range_for_the_mjpeg_encoder()
    test_build_proxy_cmd_preserves_audio_track_order()
    test_build_proxy_cmd_scale_never_upsamples()
    test_proxy_cache_path_is_deterministic_for_the_same_file()
    test_proxy_cache_path_changes_when_the_file_changes()
    print("test_review_media: all tests passed")


if __name__ == "__main__":
    _run_all()
