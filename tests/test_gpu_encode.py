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
    """Swap core.gpu_encode.probe_encoder (nvenc/qsv/amf) AND
    probe_vaapi_encoder for `fake_fn`, run `body()`, always restore — avoids
    a pytest-monkeypatch dependency (this suite also runs standalone,
    without pytest installed). Both need faking: _cached_probe() routes
    *_vaapi encoder names to probe_vaapi_encoder instead of probe_encoder
    (VAAPI needs a different ffmpeg binary + command shape — see gpu_encode's
    module docstring), so a test only faking probe_encoder would otherwise
    hit the REAL probe_vaapi_encoder and become dependent on whatever VAAPI
    support (or lack of it) the machine actually running the test happens
    to have."""
    real = ge.probe_encoder
    real_vaapi = ge.probe_vaapi_encoder
    ge.probe_encoder = fake_fn
    ge.probe_vaapi_encoder = lambda enc_name, pix_fmt="nv12", timeout=8.0: fake_fn(
        "ffmpeg", enc_name, pix_fmt, timeout)
    try:
        body()
    finally:
        ge.probe_encoder = real
        ge.probe_vaapi_encoder = real_vaapi


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


def test_hw_video_encoder_args_excludes_vaapi():
    # VAAPI doesn't fit this function's "swap the trailing args" shape (it
    # needs a device + upload filter upstream of the encoder) — callers must
    # go through hw_encode_plan() for it instead.
    assert ge.hw_video_encoder_args("hevc", "vaapi", "yuv420p") == []


def test_vaapi_encoder_args_no_pix_fmt_and_hevc_tag():
    args = ge.vaapi_encoder_args("hevc", quality=20)
    assert args[:2] == ["-c:v", "hevc_vaapi"]
    assert "-qp" in args and args[args.index("-qp") + 1] == "20"
    assert "-tag:v" in args and args[args.index("-tag:v") + 1] == "hvc1"
    assert "-pix_fmt" not in args   # format is set via the upload filter, not here

    h264_args = ge.vaapi_encoder_args("h264", quality=18)
    assert h264_args[:2] == ["-c:v", "h264_vaapi"]
    assert "-tag:v" not in h264_args


def _with_fake_vaapi_env(ff_path, device_path, encoder_works, body):
    """Fake system_vaapi_ffmpeg()/vaapi_render_device()/the probe so
    hw_encode_plan()'s VAAPI branch is deterministic regardless of whatever
    VAAPI support the machine actually running the test has."""
    real_ff_fn = ge.system_vaapi_ffmpeg
    real_dev_fn = ge.vaapi_render_device
    real_probe = ge.probe_vaapi_encoder
    ge.system_vaapi_ffmpeg = lambda: ff_path
    ge.vaapi_render_device = lambda: device_path
    ge.probe_vaapi_encoder = lambda enc_name, pix_fmt="nv12", timeout=8.0: encoder_works
    ge._probe_cache.clear()
    try:
        body()
    finally:
        ge.system_vaapi_ffmpeg = real_ff_fn
        ge.vaapi_render_device = real_dev_fn
        ge.probe_vaapi_encoder = real_probe
        ge._probe_cache.clear()


def test_hw_encode_plan_vaapi_full_shape():
    def body():
        plan = ge.hw_encode_plan("hevc", "vaapi", "yuv420p10le", quality=20)
        assert plan is not None
        assert plan["ffmpeg_bin"] == "/usr/bin/ffmpeg"
        assert plan["global_args"] == ["-vaapi_device", "/dev/dri/renderD128"]
        assert plan["filter_suffix"] == "format=p010le,hwupload"
        assert plan["encoder_args"][:2] == ["-c:v", "hevc_vaapi"]
    _with_fake_vaapi_env("/usr/bin/ffmpeg", "/dev/dri/renderD128", True, body)


def test_hw_encode_plan_vaapi_none_when_no_system_ffmpeg():
    def body():
        assert ge.hw_encode_plan("hevc", "vaapi", "yuv420p", 18) is None
    _with_fake_vaapi_env(None, "/dev/dri/renderD128", True, body)


def test_hw_encode_plan_vaapi_none_when_no_render_device():
    def body():
        assert ge.hw_encode_plan("hevc", "vaapi", "yuv420p", 18) is None
    _with_fake_vaapi_env("/usr/bin/ffmpeg", None, True, body)


def test_hw_encode_plan_vaapi_none_when_probe_fails():
    def body():
        assert ge.hw_encode_plan("hevc", "vaapi", "yuv420p", 18) is None
    _with_fake_vaapi_env("/usr/bin/ffmpeg", "/dev/dri/renderD128", False, body)


def test_hw_encode_plan_nvenc_wraps_hw_video_encoder_args_plainly():
    plan = ge.hw_encode_plan("hevc", "nvenc", "yuv420p10le", 18)
    assert plan is not None
    assert plan["ffmpeg_bin"] is None
    assert plan["global_args"] == []
    assert plan["filter_suffix"] is None
    assert plan["encoder_args"] == ge.hw_video_encoder_args("hevc", "nvenc", "yuv420p10le", 18)


def test_hw_encode_plan_none_vendor_returns_none():
    assert ge.hw_encode_plan("hevc", None, "yuv420p", 18) is None


def test_hw_decode_availability_and_global_args():
    real_ff = ge.system_vaapi_ffmpeg
    real_dev = ge.vaapi_render_device
    try:
        ge.system_vaapi_ffmpeg = lambda: "/usr/bin/ffmpeg"
        ge.vaapi_render_device = lambda: "/dev/dri/renderD128"
        assert ge.hw_decode_available() is True
        # auto-download form (no -hwaccel_output_format vaapi) so software filters still work
        assert ge.vaapi_decode_global_args() == [
            "-hwaccel", "vaapi", "-hwaccel_device", "/dev/dri/renderD128"]

        # No VAAPI-capable system ffmpeg → not available, no args.
        ge.system_vaapi_ffmpeg = lambda: None
        assert ge.hw_decode_available() is False
        assert ge.vaapi_decode_global_args() is None
    finally:
        ge.system_vaapi_ffmpeg = real_ff
        ge.vaapi_render_device = real_dev


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
