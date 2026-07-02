"""core/scopes.py — histogram and RGB waveform arrays for the Review tab's
colour/dynamic-range panel.

Every function takes an (H, W, 3) array already expressed on the target bit
depth's native scale (0..255 for 8-bit, 0..1023 for 10-bit — `rescale_to_bit_depth`
gets there from a wide-range source such as ffmpeg's 16-bit `rgb48le`) and
returns small arrays ready to colourize and paint. No Qt, no file I/O.

The v1.4 playback spike confirmed `QVideoFrame.toImage()` silently converts
genuine 10-bit frames to 8-bit RGB — so anything claiming to show true
dynamic range must be built from an ffmpeg-extracted `rgb48le` frame, not a
live-playback QImage. These functions don't care which; they just need the
right `bit_depth` and a matching-scale array.
"""

import numpy as np

BIT_DEPTH_MAX = {8: 255, 10: 1023, 12: 4095, 16: 65535}


def rescale_to_bit_depth(arr, bit_depth: int, src_max: int = 65535) -> np.ndarray:
    """Rescale a wide-range array (e.g. 16-bit rgb48le) onto 0..(2**bit_depth-1)."""
    target_max = BIT_DEPTH_MAX.get(bit_depth, 255)
    arr = np.asarray(arr, dtype=np.float64)
    scaled = arr / float(src_max) * target_max
    return np.clip(np.round(scaled), 0, target_max).astype(np.int32)


def axis_ticks(bit_depth: int, n: int = 5) -> list:
    """`n` evenly spaced tick values from 0 to the bit depth's max, inclusive."""
    top = BIT_DEPTH_MAX.get(bit_depth, 255)
    if n < 2:
        return [0, top]
    return [round(i * top / (n - 1)) for i in range(n)]


def histogram_rgb(arr, bit_depth: int = 8) -> dict:
    """Per-channel + luma bin counts, plus shadow/highlight clip percentages.

    `arr` is (H, W, 3) on the native `bit_depth` scale.
    """
    arr = np.asarray(arr)
    top = BIT_DEPTH_MAX.get(bit_depth, 255)
    nbins = top + 1
    r = arr[..., 0].astype(np.int64).ravel()
    g = arr[..., 1].astype(np.int64).ravel()
    b = arr[..., 2].astype(np.int64).ravel()
    luma = np.clip(np.round(0.2126 * r + 0.7152 * g + 0.0722 * b).astype(np.int64), 0, top)

    def _count(x):
        return np.bincount(np.clip(x, 0, top), minlength=nbins).astype(np.int64)

    total = luma.size
    band = max(0, round(top * 0.004))   # ~2 of 512 — a thin "clipped" band at each end
    clip_low  = float((luma <= band).sum()) / total * 100.0 if total else 0.0
    clip_high = float((luma >= top - band).sum()) / total * 100.0 if total else 0.0

    return {
        "r": _count(r), "g": _count(g), "b": _count(b), "luma": _count(luma),
        "clip_low_pct": clip_low, "clip_high_pct": clip_high,
        "bit_depth": bit_depth, "max_value": top,
    }


def waveform_channel(channel_2d, out_h: int, bit_depth: int = 8) -> np.ndarray:
    """2D histogram (out_h, W): each column's distribution of one channel's
    values across the frame's rows, binned into `out_h` rows (row 0 = brightest,
    row out_h-1 = darkest — matching how a waveform scope is drawn top-down)."""
    channel_2d = np.asarray(channel_2d)
    if channel_2d.ndim != 2 or channel_2d.size == 0:
        return np.zeros((max(1, out_h), max(1, channel_2d.shape[-1] if channel_2d.ndim else 1)),
                        dtype=np.float64)
    H, W = channel_2d.shape
    top = BIT_DEPTH_MAX.get(bit_depth, 255)
    row = np.clip(channel_2d.astype(np.float64) / top * (out_h - 1), 0, out_h - 1)
    row = (out_h - 1 - np.round(row)).astype(np.int64)   # invert: brightest → row 0
    col = np.broadcast_to(np.arange(W), (H, W))
    out = np.zeros((out_h, W), dtype=np.float64)
    np.add.at(out, (row.ravel(), col.ravel()), 1.0)
    return out


def waveform_parade(arr, out_h: int = 256, bit_depth: int = 8) -> dict:
    """Per-channel column-wise intensity histograms — the data for a Resolve-
    style RGB parade (three scopes side by side), one per channel."""
    arr = np.asarray(arr)
    return {
        "r": waveform_channel(arr[..., 0], out_h, bit_depth),
        "g": waveform_channel(arr[..., 1], out_h, bit_depth),
        "b": waveform_channel(arr[..., 2], out_h, bit_depth),
        "bit_depth": bit_depth, "max_value": BIT_DEPTH_MAX.get(bit_depth, 255),
    }


def _compress_normalize(counts: np.ndarray) -> np.ndarray:
    """sqrt-compressed 0..1 normalization: a real frame often has one large
    uniform region (sky, wall, out-of-focus background) whose bin count
    would otherwise dwarf everything else under a linear scale, making the
    rest of the waveform invisible. sqrt keeps the dominant bin brightest
    while still showing the rest of the spread."""
    compressed = np.sqrt(counts)
    peak = compressed.max()
    return (compressed / peak) if peak > 0 else compressed


def waveform_rgb(arr, out_h: int = 256, bit_depth: int = 8) -> np.ndarray:
    """Single (out_h, W, 3) uint8 image: the three channel waveforms overlaid
    additively, so channel agreement reads white/grey and disagreement reads
    as colour — the combined-RGB waveform mode."""
    ch = waveform_parade(arr, out_h, bit_depth)
    out = np.zeros((out_h, ch["r"].shape[1], 3), dtype=np.float64)
    out[..., 0] = _compress_normalize(ch["r"])
    out[..., 1] = _compress_normalize(ch["g"])
    out[..., 2] = _compress_normalize(ch["b"])
    return np.clip(out * 255.0, 0, 255).astype(np.uint8)
