"""Tests for core.spectrogram — STFT magnitude + magma colour mapping."""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.spectrogram import spectrogram, to_rgb, MAGMA_LUT, DEFAULT_N_FFT


def test_spectrogram_output_shape():
    rate = 8000
    dur = 1.0
    pcm = np.zeros(int(rate * dur), dtype=np.float32)
    spec = spectrogram(pcm, rate, n_fft=1024, hop=256)
    assert spec.shape[1] == 1024 // 2 + 1
    assert spec.shape[0] >= 1
    assert spec.dtype == np.float32


def test_spectrogram_values_bounded_zero_to_one():
    rate = 8000
    t = np.arange(rate) / rate
    pcm = (np.sin(2 * np.pi * 440 * t) * 0.8).astype(np.float32)
    spec = spectrogram(pcm, rate)
    assert spec.min() >= 0.0
    assert spec.max() <= 1.0 + 1e-6


def test_spectrogram_pure_tone_peaks_at_expected_bin():
    rate = 8000
    n_fft = 1024
    freq = 1000.0   # Hz
    t = np.arange(rate * 2) / rate
    pcm = np.sin(2 * np.pi * freq * t).astype(np.float32)
    spec = spectrogram(pcm, rate, n_fft=n_fft, hop=256)
    mid_frame = spec[spec.shape[0] // 2]
    peak_bin = int(mid_frame.argmax())
    expected_bin = int(round(freq / (rate / n_fft)))
    assert abs(peak_bin - expected_bin) <= 2   # within a couple of FFT bins


def test_spectrogram_short_input_is_padded_not_crashed():
    rate = 8000
    pcm = np.array([0.1, -0.1, 0.2], dtype=np.float32)   # far shorter than n_fft
    spec = spectrogram(pcm, rate, n_fft=DEFAULT_N_FFT)
    assert spec.shape[0] >= 1
    assert spec.shape[1] == DEFAULT_N_FFT // 2 + 1


def test_magma_lut_shape_and_dtype():
    assert MAGMA_LUT.shape == (256, 3)
    assert MAGMA_LUT.dtype == np.uint8
    # dark end near black, bright end near pale yellow (magma's anchors)
    assert MAGMA_LUT[0].sum() < 20
    assert MAGMA_LUT[-1][0] > 200 and MAGMA_LUT[-1][1] > 200


def test_to_rgb_maps_normalized_spectrogram_to_image():
    # 5 time frames, 10 frequency bins (bin 0 = 0Hz/DC ... bin 9 = highest);
    # the high-frequency half is bright, the low-frequency half is dark.
    spec = np.zeros((5, 10), dtype=np.float32)
    spec[:, 5:] = 1.0
    img = to_rgb(spec)
    assert img.shape == (10, 5, 3)   # (freq_bins, time_frames, 3) — display orientation
    assert img.dtype == np.uint8
    assert tuple(img[0, 0]) == tuple(MAGMA_LUT[255])    # top row = highest freq = bright
    assert tuple(img[-1, 0]) == tuple(MAGMA_LUT[0])     # bottom row = 0Hz/DC = dark


if __name__ == "__main__":
    test_spectrogram_output_shape()
    test_spectrogram_values_bounded_zero_to_one()
    test_spectrogram_pure_tone_peaks_at_expected_bin()
    test_spectrogram_short_input_is_padded_not_crashed()
    test_magma_lut_shape_and_dtype()
    test_to_rgb_maps_normalized_spectrogram_to_image()
    print("test_spectrogram: all tests passed")
