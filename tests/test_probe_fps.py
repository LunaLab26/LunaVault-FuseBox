"""Tests for probe.py's nominal-fps selection.

A folder of clips from ONE camera at ONE nominal rate (e.g. 29.97) must stay on a
single baseline and stream-copy — but ffprobe's avg_frame_rate drifts a few
hundredths per clip (29.92 / 29.95 / 29.97), which used to split them into
separate baselines and force needless transcodes. probe now takes the nominal
rate from the stable r_frame_rate for CFR clips. Pure, standalone.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from probe import (  # noqa: E402
    _nominal_fps, _normalize_fps, StreamInfo, apply_conformance,
    DEFAULT_BASELINE, TARGET_FPS_FLOAT)

R_2997 = 30000 / 1001   # 29.9700… — the clean nominal r_frame_rate


def test_cfr_drift_uses_stable_r_not_avg():
    # same 29.97 source, avg drifts clip-to-clip, r is byte-identical → use r
    for avg in (29.92, 29.95, 29.97, 30.0):
        got = _nominal_fps(0.0, R_2997, avg, is_vfr=False)
        assert abs(got - R_2997) < 1e-6, f"CFR clip (avg={avg}) should take the stable r"


def test_vfr_uses_avg_because_r_is_the_misread_one():
    # Pixel 30fps VFR reports r=120; avg (30) is the real rate
    assert abs(_nominal_fps(0.0, 120.0, 30.0, is_vfr=True) - 30.0) < 1e-6


def test_capture_fps_tag_wins_when_present():
    assert _nominal_fps(30.0, R_2997, 29.9, is_vfr=False) == 30.0


def test_fallback_to_r_when_avg_missing():
    assert abs(_nominal_fps(0.0, R_2997, 0.0, is_vfr=False) - R_2997) < 1e-6


def _clip(avg_fps: float) -> StreamInfo:
    """A StreamInfo as probe() would build it for a 4K/HEVC/10-bit clip whose
    avg_frame_rate came back as `avg_fps` but whose r_frame_rate is the clean
    29.97 nominal (the exact same-camera-drift situation)."""
    info = StreamInfo(codec="hevc", width=3840, height=2160, pix_fmt="yuv420p10le",
                      color_space="bt709")
    info.is_vfr = abs(R_2997 - avg_fps) > 0.5
    info.fps_float = _nominal_fps(0.0, R_2997, avg_fps, info.is_vfr)
    info.fps_str = _normalize_fps(info.fps_float)
    return info


def test_drifted_same_camera_clips_all_stream_copy():
    baseline = DEFAULT_BASELINE   # 4K HEVC 10-bit @ 29.97
    sigs = set()
    for avg in (29.92, 29.95, 29.97):
        info = _clip(avg)
        apply_conformance(info, baseline)
        assert info.status == "ok", f"avg={avg} clip should stream-copy, got {info.conflicts}"
        assert abs(info.fps_float - TARGET_FPS_FLOAT) < 0.01
        sigs.add(info.fps_str)
    assert sigs == {"29.97"}, f"all clips must share one fps signature, got {sigs}"


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("test_probe_fps: all tests passed")
