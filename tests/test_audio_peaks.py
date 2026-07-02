"""Tests for core.audio_peaks — peak pyramid construction and view slicing."""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.audio_peaks import (
    build_pcm_extract_cmd, build_pyramid, pyramid_from_stream, peaks_for_view,
)


def test_build_pcm_extract_cmd_maps_the_right_audio_track():
    cmd = build_pcm_extract_cmd("ffmpeg", "master.mov", track_idx=2, rate=8000)
    assert "ffmpeg" in cmd[0]
    assert "-map" in cmd
    assert cmd[cmd.index("-map") + 1] == "0:a:2"
    assert "-ar" in cmd and cmd[cmd.index("-ar") + 1] == "8000"
    assert cmd[-1] == "pipe:1"
    assert "-ac" in cmd   # mono by default


def test_build_pyramid_basic_shape_and_duration():
    sr = 8000
    samples = np.linspace(-1.0, 1.0, sr * 2, dtype=np.float32)   # 2 seconds
    pyr = build_pyramid(samples, sample_rate=sr, bin_secs=0.05)
    assert abs(pyr.duration - 2.0) < 1e-6
    assert len(pyr.levels) == len(pyr.bin_secs)
    assert pyr.levels[0].shape[1] == 2   # (min, max) columns
    # bin_secs strictly increases (coarser) at each level
    for a, b in zip(pyr.bin_secs, pyr.bin_secs[1:]):
        assert b == a * 2


def test_build_pyramid_captures_known_extremes():
    sr = 8000
    n = sr  # 1 second
    samples = np.zeros(n, dtype=np.float32)
    samples[100] = 0.9
    samples[5000] = -0.7
    pyr = build_pyramid(samples, sample_rate=sr, bin_secs=0.05)
    level0 = pyr.levels[0]
    assert level0[:, 1].max() >= 0.89   # some bin's max captured the peak
    assert level0[:, 0].min() <= -0.69  # some bin's min captured the trough


def test_empty_input_returns_empty_pyramid_not_a_crash():
    pyr = build_pyramid(np.array([], dtype=np.float32), sample_rate=8000)
    assert pyr.levels[0].shape[0] == 0
    assert pyr.duration == 0.0


def test_pyramid_from_stream_matches_direct_build():
    sr = 8000
    samples = (np.sin(np.linspace(0, 40 * np.pi, sr, dtype=np.float32)) * 0.5).astype(np.float32)
    raw = samples.tobytes()
    # split into uneven chunks to exercise the streaming fold
    chunks = [raw[i:i + 777] for i in range(0, len(raw), 777)]
    pyr_stream = pyramid_from_stream(chunks, sample_rate=sr, bin_secs=0.05)
    pyr_direct = build_pyramid(samples, sample_rate=sr, bin_secs=0.05)
    assert pyr_stream.levels[0].shape == pyr_direct.levels[0].shape
    assert np.allclose(pyr_stream.levels[0], pyr_direct.levels[0], atol=1e-5)


def test_peaks_for_view_slices_the_requested_range():
    sr = 8000
    samples = np.linspace(-1.0, 1.0, sr * 10, dtype=np.float32)   # 10 seconds
    pyr = build_pyramid(samples, sample_rate=sr, bin_secs=0.05)
    full = peaks_for_view(pyr, 0.0, 10.0, px_width=800)
    half = peaks_for_view(pyr, 0.0, 5.0, px_width=800)
    assert full.shape[0] > half.shape[0]


def test_level_for_width_picks_coarser_level_for_zoomed_out_view():
    sr = 8000
    samples = np.random.RandomState(0).uniform(-1, 1, sr * 120).astype(np.float32)  # 2 minutes
    pyr = build_pyramid(samples, sample_rate=sr, bin_secs=0.05)
    zoomed_in_level = pyr.level_for_width(0.0, 1.0, px_width=800)     # 1 sec across 800px
    zoomed_out_level = pyr.level_for_width(0.0, 120.0, px_width=800)  # whole track across 800px
    assert zoomed_out_level >= zoomed_in_level


if __name__ == "__main__":
    test_build_pcm_extract_cmd_maps_the_right_audio_track()
    test_build_pyramid_basic_shape_and_duration()
    test_build_pyramid_captures_known_extremes()
    test_empty_input_returns_empty_pyramid_not_a_crash()
    test_pyramid_from_stream_matches_direct_build()
    test_peaks_for_view_slices_the_requested_range()
    test_level_for_width_picks_coarser_level_for_zoomed_out_view()
    print("test_audio_peaks: all tests passed")
