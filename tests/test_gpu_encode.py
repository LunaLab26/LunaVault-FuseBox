"""Tests for core/gpu_encode.py — GPU encoder detection + arg building.
Runs under pytest and standalone."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core import gpu_encode as ge


def test_encoder_name_maps_codec_and_vendor():
    assert ge.encoder_name("hevc", "nvenc") == "hevc_nvenc"
    assert ge.encoder_name("h265", "nvenc") == "hevc_nvenc"   # alias
    assert ge.encoder_name("h264", "qsv") == "h264_qsv"
    assert ge.encoder_name("hevc", "amf") == "hevc_amf"


def test_hw_pix_fmt_maps_10bit_to_p010le():
    assert ge.hw_pix_fmt("yuv420p10le") == "p010le"
    assert ge.hw_pix_fmt("yuv420p") == "nv12"
    assert ge.hw_pix_fmt("") == "nv12"


def test_hw_video_encoder_args_nvenc_10bit_hevc():
    args = ge.hw_video_encoder_args("hevc", "nvenc", "yuv420p10le")
    assert args[:2] == ["-c:v", "hevc_nvenc"]
    assert "-pix_fmt" in args and args[args.index("-pix_fmt") + 1] == "p010le"
    assert "-tag:v" in args and args[args.index("-tag:v") + 1] == "hvc1"
    assert "-profile:v" in args and args[args.index("-profile:v") + 1] == "main10"


def test_hw_video_encoder_args_qsv_8bit_h264_no_hevc_tags():
    args = ge.hw_video_encoder_args("h264", "qsv", "yuv420p")
    assert args[:2] == ["-c:v", "h264_qsv"]
    assert "-pix_fmt" in args and args[args.index("-pix_fmt") + 1] == "nv12"
    assert "-tag:v" not in args        # hvc1 tag is HEVC-only
    assert "-profile:v" not in args    # main10 profile only applies to 10-bit


def test_hw_video_encoder_args_amf():
    args = ge.hw_video_encoder_args("hevc", "amf", "yuv420p")
    assert args[:2] == ["-c:v", "hevc_amf"]


def test_hw_video_encoder_args_unknown_vendor_returns_empty():
    assert ge.hw_video_encoder_args("hevc", "not_a_vendor", "yuv420p") == []


def _with_fake_probe(fake_fn, body):
    """Swap core.gpu_encode.probe_encoder for `fake_fn`, run `body()`, always
    restore — avoids a pytest-monkeypatch dependency (this suite also runs
    standalone, without pytest installed)."""
    real = ge.probe_encoder
    ge.probe_encoder = fake_fn
    try:
        body()
    finally:
        ge.probe_encoder = real


def test_detect_best_hw_respects_vendor_order_and_cache():
    calls = []

    def fake_probe(ff, enc_name, pix_fmt="nv12", timeout=8.0):
        calls.append(enc_name)
        return enc_name == "hevc_qsv"   # only QSV "works"

    def body():
        ge._probe_cache.clear()
        vendor = ge.detect_best_hw("ffmpeg", "hevc")
        assert vendor == "qsv"
        assert calls == ["hevc_nvenc", "hevc_qsv"]   # stops at first success — amf unreached

        calls.clear()
        vendor2 = ge.detect_best_hw("ffmpeg", "hevc")
        assert vendor2 == "qsv"
        assert calls == []   # second call served entirely from cache

    _with_fake_probe(fake_probe, body)


def test_detect_best_hw_returns_none_when_nothing_works():
    def body():
        ge._probe_cache.clear()
        assert ge.detect_best_hw("ffmpeg", "hevc") is None
    _with_fake_probe(lambda ff, enc_name, pix_fmt="nv12", timeout=8.0: False, body)


def test_available_hw_vendors_lists_every_working_vendor():
    def body():
        ge._probe_cache.clear()
        assert ge.available_hw_vendors("ffmpeg", "hevc") == ["qsv", "amf"]
    _with_fake_probe(
        lambda ff, enc_name, pix_fmt="nv12", timeout=8.0: enc_name in ("hevc_qsv", "hevc_amf"),
        body)


def _real_encoder_check():
    """Best-effort: if the bundled ffmpeg is present, confirm probing doesn't crash
    and reports a plain list (contents depend on this machine's actual GPU)."""
    from core.binaries import get_ffmpeg
    ff = get_ffmpeg()[0]
    ge._probe_cache.clear()
    vendors = ge.available_hw_vendors(ff, "hevc")
    print(f"  real-machine working GPU encoders: {vendors or '(none)'}")
    assert isinstance(vendors, list)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print("ok:", name)
    _real_encoder_check()
    print("test_gpu_encode: all tests passed")
