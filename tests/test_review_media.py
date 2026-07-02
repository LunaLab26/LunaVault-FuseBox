"""Tests for core.review_media — ffmpeg command builders for the Review tab."""

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.review_media import (
    build_frame_extract_cmd, build_snapshot_cmd, snapshot_filename,
    mix_cache_key, build_review_mix_cmd, _split_seek,
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
    print("test_review_media: all tests passed")


if __name__ == "__main__":
    _run_all()
