"""Regression tests for the 2026-09-24 overnight fixes: the effective-encoder
decision shared by label + command, the software preset, the pre-merge time
estimate, and the wrapping progress-badge row."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core import gpu_encode as ge
from core import ffmpeg_cmd as fc
from core.eta import estimate_merge_seconds, describe_duration


def _with_vendor(vendor, fn):
    saved = (ge.detect_best_hw, ge.hw_encode_plan)
    ge.detect_best_hw = lambda ff, codec="hevc": vendor
    ge.hw_encode_plan = lambda codec, v, pix, q=18: {"encoder_args": ["-c:v", f"{codec}_{v}"]}
    try:
        return fn()
    finally:
        ge.detect_best_hw, ge.hw_encode_plan = saved


def test_vaapi_hevc_main10_is_reported_as_software():
    c = fc.ConformSpec(codec="hevc", pix_fmt="yuv420p10le", hw_encoder="auto")
    assert _with_vendor("vaapi", lambda: fc.effective_conform_encoder(c, "ff")) == "software"


def test_vaapi_8bit_and_nvenc_10bit_stay_on_gpu():
    c8 = fc.ConformSpec(codec="hevc", pix_fmt="yuv420p", hw_encoder="auto")
    assert _with_vendor("vaapi", lambda: fc.effective_conform_encoder(c8, "ff")) == "vaapi"
    c10 = fc.ConformSpec(codec="hevc", pix_fmt="yuv420p10le", hw_encoder="auto")
    assert _with_vendor("nvenc", lambda: fc.effective_conform_encoder(c10, "ff")) == "nvenc"


def test_hw_off_is_software():
    c = fc.ConformSpec(hw_encoder="off")
    assert fc.effective_conform_encoder(c, "ff") == "software"


def test_software_args_use_fast_preset_and_lower_crf():
    c = fc.ConformSpec(codec="hevc", pix_fmt="yuv420p10le", hw_encoder="off", quality=26)
    args = fc._video_encoder_args(c, None)
    assert args[args.index("-preset") + 1] == fc.SW_CONFORM_PRESET == "veryfast"
    assert args[args.index("-crf") + 1] == "23"


def test_estimate_ignores_copied_clips_and_scales_with_resolution():
    copied_only = estimate_merge_seconds([(60, False, 0)], 3840, 2160, 30, "hevc", "software")
    assert copied_only == 0
    uhd = estimate_merge_seconds([(60, True, 0)], 3840, 2160, 30, "hevc", "software")
    hd = estimate_merge_seconds([(60, True, 0)], 1920, 1080, 30, "hevc", "software")
    gpu = estimate_merge_seconds([(60, True, 0)], 3840, 2160, 30, "hevc", "vaapi")
    assert 300 <= uhd <= 420          # 1800 frames at ~5 fps
    assert abs(uhd / hd - 4) < 0.01
    assert gpu < uhd / 5


def test_describe_duration():
    assert describe_duration(20) == "under a minute"
    assert describe_duration(600) == "about 10 minutes"
    assert describe_duration(60) == "about 1 minute"
    assert describe_duration(2 * 3600 + 9 * 60) == "about 2 h 10 min"


def test_flow_layout_wraps_instead_of_widening():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel, QWidget
    from widgets.flow_layout import FlowLayout
    app = QApplication.instance() or QApplication([])
    host = QWidget()
    lay = FlowLayout(host)
    for i in range(60):
        lay.addWidget(QLabel(f"VID_20260919_1244{i:02d}_089"))
    one_label = QLabel("VID_20260919_124400_089").sizeHint().width()
    assert lay.minimumSize().width() <= one_label + 2
    assert lay.heightForWidth(600) > lay.heightForWidth(4000)
    host.deleteLater()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print("ok", name)
