"""Tests for core.scopes — histogram, waveform-parade and RGB-waveform arrays."""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.scopes import (
    rescale_to_bit_depth, axis_ticks, histogram_rgb, waveform_channel,
    waveform_parade, waveform_rgb, _downsample_for_scope, _MAX_SCOPE_PIXELS,
)


def test_rescale_to_bit_depth_maps_full_range():
    arr16 = np.array([0, 65535], dtype=np.float64)
    out = rescale_to_bit_depth(arr16, bit_depth=10)
    assert out[0] == 0
    assert out[1] == 1023


def test_axis_ticks_span_the_bit_depth():
    ticks8 = axis_ticks(8, n=5)
    assert ticks8[0] == 0 and ticks8[-1] == 255
    ticks10 = axis_ticks(10, n=5)
    assert ticks10[0] == 0 and ticks10[-1] == 1023
    assert len(ticks10) == 5


def test_histogram_rgb_counts_sum_to_pixel_count():
    h, w = 10, 20
    arr = np.random.RandomState(1).randint(0, 256, size=(h, w, 3)).astype(np.uint8)
    hist = histogram_rgb(arr, bit_depth=8)
    assert hist["r"].sum() == h * w
    assert hist["luma"].sum() == h * w
    assert len(hist["r"]) == 256
    assert hist["bit_depth"] == 8
    assert hist["max_value"] == 255


def test_histogram_rgb_flags_full_black_as_clipped_shadows():
    arr = np.zeros((8, 8, 3), dtype=np.uint8)
    hist = histogram_rgb(arr, bit_depth=8)
    assert hist["clip_low_pct"] > 99.0
    assert hist["clip_high_pct"] < 1.0


def test_histogram_rgb_flags_full_white_as_clipped_highlights():
    arr = np.full((8, 8, 3), 1023, dtype=np.int32)
    hist = histogram_rgb(arr, bit_depth=10)
    assert hist["clip_high_pct"] > 99.0
    assert hist["clip_low_pct"] < 1.0
    assert len(hist["luma"]) == 1024


def test_waveform_channel_places_bright_column_near_row_zero():
    # one bright column (all max value), one dark column (all zero)
    channel = np.zeros((16, 2), dtype=np.float64)
    channel[:, 0] = 255   # column 0: fully bright
    channel[:, 1] = 0     # column 1: fully dark
    out = waveform_channel(channel, out_h=64, bit_depth=8)
    bright_col_peak_row = out[:, 0].argmax()
    dark_col_peak_row = out[:, 1].argmax()
    assert bright_col_peak_row < 4          # near the top (row 0 = brightest)
    assert dark_col_peak_row > 59           # near the bottom (darkest)
    assert out[:, 0].sum() == 16            # every row's sample accounted for


def test_waveform_parade_returns_three_channels_same_shape():
    arr = np.random.RandomState(2).randint(0, 256, size=(12, 30, 3)).astype(np.uint8)
    parade = waveform_parade(arr, out_h=64, bit_depth=8)
    assert parade["r"].shape == parade["g"].shape == parade["b"].shape == (64, 30)
    assert parade["bit_depth"] == 8


def test_downsample_for_scope_leaves_small_arrays_untouched():
    arr = np.zeros((10, 20, 3), dtype=np.uint8)
    out = _downsample_for_scope(arr)
    assert out is arr   # untouched — no striding applied, no copy


def test_downsample_for_scope_caps_a_4k_frame():
    # A real 3840x2160 frame — this exact size + full-precision processing
    # is what exhausted memory on modest hardware before this fix.
    arr = np.zeros((2160, 3840, 3), dtype=np.uint8)
    out = _downsample_for_scope(arr)
    assert out.shape[0] * out.shape[1] <= _MAX_SCOPE_PIXELS
    assert out.shape[0] * out.shape[1] > _MAX_SCOPE_PIXELS * 0.5   # not wastefully over-cropped
    # Plain step-slicing must stay a view (free) rather than a copy.
    assert out.base is not None


def test_histogram_rgb_handles_a_4k_frame_without_blowing_up():
    # Exercises the exact crash scenario from the field: a full-resolution
    # frame run through histogram_rgb repeatedly (simulating playback).
    rng = np.random.RandomState(3)
    arr = rng.randint(0, 256, size=(2160, 3840, 3)).astype(np.uint8)
    for _ in range(5):
        hist = histogram_rgb(arr, bit_depth=8)
    # Counts sum to the DOWNSAMPLED pixel count, not the original 8.3M.
    assert hist["r"].sum() <= _MAX_SCOPE_PIXELS
    assert hist["r"].sum() > 0
    assert hist["r"].dtype == np.int64   # the small (256-bin) result array — fine to be wide
    assert len(hist["r"]) == 256


def test_rescale_to_bit_depth_handles_a_4k_frame_and_uses_a_narrow_dtype():
    arr16 = np.full((2160, 3840, 3), 65535, dtype=np.uint16)
    out = rescale_to_bit_depth(arr16, bit_depth=10)
    assert out.dtype == np.uint16
    assert out.max() == 1023
    assert out.shape[0] * out.shape[1] <= _MAX_SCOPE_PIXELS


def test_waveform_parade_handles_a_4k_frame_without_blowing_up():
    arr = np.zeros((2160, 3840, 3), dtype=np.uint8)
    parade = waveform_parade(arr, out_h=64, bit_depth=8)
    # Waveform width tracks the downsampled column count, not the original 3840.
    assert parade["r"].shape[1] < 3840
    assert parade["r"].shape[0] == 64


def test_waveform_rgb_is_an_rgb_image_and_white_where_channels_agree():
    h, w = 20, 4
    arr = np.zeros((h, w, 3), dtype=np.uint8)
    arr[:, 0, :] = 200   # column 0: neutral grey (R=G=B agree)
    arr[:, 1, 0] = 200   # column 1: red only
    out = waveform_rgb(arr, out_h=64, bit_depth=8)
    assert out.shape == (64, w, 3)
    assert out.dtype == np.uint8
    # column 0 (agreement) should have a row where all three channels are high together
    col0_max_per_row = out[:, 0, :].min(axis=1)   # min across channels = "agreement" floor
    assert col0_max_per_row.max() > 0


if __name__ == "__main__":
    test_rescale_to_bit_depth_maps_full_range()
    test_axis_ticks_span_the_bit_depth()
    test_histogram_rgb_counts_sum_to_pixel_count()
    test_histogram_rgb_flags_full_black_as_clipped_shadows()
    test_histogram_rgb_flags_full_white_as_clipped_highlights()
    test_waveform_channel_places_bright_column_near_row_zero()
    test_waveform_parade_returns_three_channels_same_shape()
    test_downsample_for_scope_leaves_small_arrays_untouched()
    test_downsample_for_scope_caps_a_4k_frame()
    test_histogram_rgb_handles_a_4k_frame_without_blowing_up()
    test_rescale_to_bit_depth_handles_a_4k_frame_and_uses_a_narrow_dtype()
    test_waveform_parade_handles_a_4k_frame_without_blowing_up()
    test_waveform_rgb_is_an_rgb_image_and_white_where_channels_agree()
    print("test_scopes: all tests passed")
