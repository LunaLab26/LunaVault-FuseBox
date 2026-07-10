"""Tests for review_playback.py's pure decode-risk detection.
Runs under pytest and standalone."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from review_playback import is_risky_hw_decode_profile
from probe import StreamInfo


def _stream(codec="hevc", width=3840, height=2160, pix_fmt="yuv420p10le"):
    return StreamInfo(codec=codec, width=width, height=height, pix_fmt=pix_fmt)


def test_4k_10bit_hevc_is_risky():
    # The exact confirmed-dangerous profile: real testing found this combination
    # causes QtPlaybackEngine to consume 14+ GB of memory and hang the system
    # hard enough to drop an active remote-desktop session.
    assert is_risky_hw_decode_profile(_stream()) is True


def test_h265_alias_also_flagged():
    assert is_risky_hw_decode_profile(_stream(codec="h265")) is True


def test_8bit_4k_hevc_not_risky():
    assert is_risky_hw_decode_profile(_stream(pix_fmt="yuv420p")) is False


def test_1080p_10bit_hevc_not_risky():
    assert is_risky_hw_decode_profile(_stream(width=1920, height=1080)) is False


def test_4k_10bit_h264_not_risky():
    assert is_risky_hw_decode_profile(_stream(codec="h264")) is False


def test_none_stream_info_not_risky():
    assert is_risky_hw_decode_profile(None) is False


def test_8k_10bit_hevc_still_risky():
    # Above the 4K floor, not just exactly at it.
    assert is_risky_hw_decode_profile(_stream(width=7680, height=4320)) is True


def test_hybrid_engine_honours_frame_poll_ms():
    """The Developer panel's 'software playback smoothness' knob sets, and can
    live-update, the HybridPlaybackEngine's picture refresh interval."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])   # noqa: F841
    from review_playback import HybridPlaybackEngine, make_engine

    eng = HybridPlaybackEngine(frame_poll_ms=150)
    assert eng._frame_timer.interval() == 150
    eng.set_frame_poll_ms(500)
    assert eng._frame_timer.interval() == 500, "must live-update the timer"

    made = make_engine(use_software=True, frame_poll_ms=250)
    assert isinstance(made, HybridPlaybackEngine)
    assert made._frame_timer.interval() == 250, "make_engine must pass the interval through"


def _run_all():
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("test_review_playback: all tests passed")


if __name__ == "__main__":
    _run_all()
