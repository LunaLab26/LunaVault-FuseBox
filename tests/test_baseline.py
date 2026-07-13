"""Tests for core/baseline.py — spec enumeration + baseline recommendation.
Runs under pytest and standalone."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.baseline import ClipSpec, enumerate_specs, recommend_baseline


def _luna(dur):   return ClipSpec("hevc", 3840, 2160, "29.97", "yuv420p10le", 10, "bt709", dur)
def _insta(dur):  return ClipSpec("h264", 3840, 2160, "29.97", "yuv420p", 8, "bt709", dur)
def _pixel(dur):  return ClipSpec("hevc", 3840, 2160, "30", "yuv420p", 8, "bt709", dur)
def _lowres(dur): return ClipSpec("hevc", 1280, 960, "30", "yuv420p", 8, "bt709", dur)


def test_enumerate_groups_and_tallies():
    specs = [_luna(45), _luna(45), _insta(20), _insta(30), _pixel(10), _lowres(12)]
    groups = enumerate_specs(specs)
    assert len(groups) == 4          # luna, insta, pixel(30fps), lowres
    luna = next(g for g in groups if g.bit_depth == 10)
    assert luna.clip_count == 2 and abs(luna.total_duration - 90) < 1e-6


def test_recommend_prefers_10bit_hero_at_majority_resolution():
    # 4K is the majority resolution; among 4K groups the 10-bit HEVC (Luna) wins.
    specs = [_luna(45), _luna(45), _insta(20), _insta(30), _pixel(10), _lowres(12)]
    rec = recommend_baseline(enumerate_specs(specs))
    assert (rec.width, rec.height) == (3840, 2160)
    assert rec.bit_depth == 10 and rec.codec.lower() == "hevc"


def test_recommend_does_not_upscale_to_a_minority_hi_res():
    # One tiny 8K clip must NOT drag the baseline up to 8K (would upscale the bulk).
    specs = [_insta(60), _insta(60), _insta(60),
             ClipSpec("hevc", 7680, 4320, "30", "yuv420p", 8, "bt709", 3.0)]
    rec = recommend_baseline(enumerate_specs(specs))
    assert (rec.width, rec.height) == (3840, 2160)   # 4K majority, not 8K


def test_recommend_none_for_empty():
    assert recommend_baseline([]) is None


def test_enumerate_specs_carries_color_primaries_and_transfer_through_to_the_group():
    # Real bug: color_primaries/color_transfer used to be dropped entirely
    # between ClipSpec and SpecGroup (only color_space survived), which is
    # part of why a BT.2020/HLG clip's group ended up with no way to build a
    # correct -color_primaries/-color_trc pair (see test_ffmpeg_cmd.py's
    # test_encoder_args_bt2020_uses_distinct_primaries_and_trc_not_the_matrix_value).
    hdr_clip = ClipSpec("hevc", 3840, 2160, "30", "yuv420p10le", 10, "bt2020nc", 5.0,
                        color_transfer="arib-std-b67", color_primaries="bt2020")
    groups = enumerate_specs([hdr_clip])
    assert len(groups) == 1
    g = groups[0]
    assert g.color_space == "bt2020nc"
    assert g.color_primaries == "bt2020"
    assert g.color_transfer == "arib-std-b67"


def _real_folder_check():
    folder = Path(r"G:\Claude cowork\20260703 - multicam video archive test")
    if not folder.exists():
        print("  (skipped real-folder check: folder not present)")
        return
    from core.binaries import get_ffmpeg
    from probe import probe, pix_fmt_info
    fp = get_ffmpeg()[1]
    specs = []
    for mp4 in folder.glob("*.mp4"):
        st = probe(fp, str(mp4))
        specs.append(ClipSpec(st.codec, st.width, st.height, st.fps_str, st.pix_fmt,
                              pix_fmt_info(st.pix_fmt)[0], st.color_space, st.duration))
    rec = recommend_baseline(enumerate_specs(specs))
    print(f"  real-folder recommended baseline: {rec.label()}")
    assert (rec.width, rec.height) == (3840, 2160) and rec.bit_depth == 10


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print("ok:", name)
    _real_folder_check()
    print("test_baseline: all tests passed")
