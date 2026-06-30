"""Tests for the pure encoder selection logic (core.encoders.recommend/args)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.encoders import recommend, encoder_args


def test_quality_mode_always_cpu():
    avail = {"hevc_nvenc", "h264_nvenc"}
    assert recommend("hevc", avail, prefer_hw=False) == "libx265"
    assert recommend("h264", avail, prefer_hw=False) == "libx264"


def test_prefers_hardware_when_available():
    assert recommend("hevc", {"hevc_nvenc"}, prefer_hw=True) == "hevc_nvenc"
    assert recommend("h264", {"h264_qsv"}, prefer_hw=True) == "h264_qsv"


def test_falls_back_to_cpu_without_hardware():
    assert recommend("hevc", set(), prefer_hw=True) == "libx265"
    assert recommend("h264", {"hevc_nvenc"}, prefer_hw=True) == "libx264"


def test_preference_order():
    avail = {"hevc_amf", "hevc_qsv"}   # qsv ranks above amf
    assert recommend("hevc", avail, prefer_hw=True) == "hevc_qsv"


def test_encoder_args_shapes():
    assert encoder_args("libx265", 18)[:2] == ["-crf", "18"]
    assert "-cq" in encoder_args("hevc_nvenc", 20)
    assert "-global_quality" in encoder_args("h264_qsv", 23)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"\n{len(fns)} encoder tests passed.")
