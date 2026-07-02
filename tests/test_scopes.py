"""Tests for core.scopes — histogram, waveform-parade and RGB-waveform arrays."""

import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.scopes import (
    rescale_to_bit_depth, axis_ticks, histogram_rgb, waveform_channel,
    waveform_parade, waveform_rgb,
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
    test_waveform_rgb_is_an_rgb_image_and_white_where_channels_agree()
    print("test_scopes: all tests passed")
