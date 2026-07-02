"""core/audio_peaks.py — min/max peak pyramids for scrubbable waveform lanes.

An audio lane needs to draw a waveform at any zoom level, instantly, without
decoding PCM on every paint. The fix is a peak pyramid: level 0 holds a
(min, max) pair for small time bins across the whole track (~50ms each);
each coarser level halves the bin count by combining neighbouring pairs.
Picking a view just means choosing the finest level with roughly one bin
per pixel and slicing it — no per-frame decode.

PCM only exists transiently while a pyramid is being built (streamed in via
ffmpeg, downsampled to a low rate first); the pyramid itself is tiny — a
~50-minute track at the default bin size is well under 1 MB.
"""

from dataclasses import dataclass
from typing import Iterable

import numpy as np

DEFAULT_RATE = 8000        # Hz — extraction sample rate (peaks don't need hi-fi)
DEFAULT_BIN_SECS = 0.05    # ~50ms per level-0 bin


def build_pcm_extract_cmd(ff: str, path: str, track_idx: int,
                          rate: int = DEFAULT_RATE, mono: bool = True) -> list:
    """ffmpeg command streaming raw f32le PCM for one audio track to stdout.

    `track_idx` is 0-based among AUDIO streams only (ffmpeg's `-map 0:a:N`),
    matching `probe.parse_audio_tracks`'s `audio_index`.
    """
    cmd = [ff, "-v", "quiet", "-i", str(path), "-map", f"0:a:{track_idx}",
           "-ar", str(rate)]
    if mono:
        cmd += ["-ac", "1"]
    cmd += ["-f", "f32le", "pipe:1"]
    return cmd


@dataclass
class PeakPyramid:
    """Levels of (min, max) pairs, finest first. levels[0] has the most bins."""
    levels: list      # list[np.ndarray], each shape (N_i, 2) float32
    bin_secs: list     # seconds per bin at each level, same length as levels
    duration: float    # total seconds represented

    def level_for_width(self, t0: float, t1: float, px_width: int) -> int:
        """Index of the coarsest level that still has >= 1 bin per pixel over [t0,t1)."""
        if px_width <= 0 or not self.levels:
            return 0
        span = max(1e-6, t1 - t0)
        target_bin_secs = span / px_width
        best = 0
        for i, bs in enumerate(self.bin_secs):
            if bs <= target_bin_secs:
                best = i
            else:
                break
        return best

    def peaks_for_view(self, t0: float, t1: float, px_width: int) -> np.ndarray:
        """(min, max) pairs covering [t0, t1), at a level suited to px_width."""
        if not self.levels:
            return np.zeros((0, 2), dtype=np.float32)
        lvl = self.level_for_width(t0, t1, px_width)
        arr = self.levels[lvl]
        bs = self.bin_secs[lvl]
        if bs <= 0 or arr.shape[0] == 0:
            return arr
        i0 = max(0, int(t0 / bs))
        i1 = min(arr.shape[0], int(np.ceil(t1 / bs)) + 1)
        return arr[i0:i1]


def peaks_for_view(pyramid: PeakPyramid, t0: float, t1: float, px_width: int) -> np.ndarray:
    return pyramid.peaks_for_view(t0, t1, px_width)


def _levelize(level0: np.ndarray, bin0_secs: float, min_bins: int = 8) -> tuple:
    """Build successive half-resolution levels by pairwise min/max reduction."""
    levels = [level0]
    bin_secs = [bin0_secs]
    cur, cur_bs = level0, bin0_secs
    while cur.shape[0] >= min_bins * 2:
        n_pairs = cur.shape[0] // 2
        pairs = cur[: n_pairs * 2].reshape(n_pairs, 2, 2)
        mins = pairs[:, :, 0].min(axis=1)
        maxs = pairs[:, :, 1].max(axis=1)
        cur = np.stack([mins, maxs], axis=1).astype(np.float32)
        cur_bs *= 2
        levels.append(cur)
        bin_secs.append(cur_bs)
    return levels, bin_secs


def build_pyramid(samples, sample_rate: int, bin_secs: float = DEFAULT_BIN_SECS) -> PeakPyramid:
    """Build a PeakPyramid from mono float samples at `sample_rate`."""
    samples = np.asarray(samples, dtype=np.float32).reshape(-1)
    n = samples.shape[0]
    duration = n / float(sample_rate) if sample_rate else 0.0
    bin_len = max(1, int(round(bin_secs * sample_rate))) if sample_rate else 1
    n_bins = max(0, n // bin_len)
    if n_bins == 0:
        return PeakPyramid(levels=[np.zeros((0, 2), dtype=np.float32)],
                           bin_secs=[bin_secs], duration=duration)
    trimmed = samples[: n_bins * bin_len].reshape(n_bins, bin_len)
    level0 = np.stack([trimmed.min(axis=1), trimmed.max(axis=1)], axis=1).astype(np.float32)
    levels, secs_per = _levelize(level0, bin_secs)
    return PeakPyramid(levels=levels, bin_secs=secs_per, duration=duration)


def pyramid_from_stream(chunk_iter: Iterable[bytes], sample_rate: int = DEFAULT_RATE,
                        bin_secs: float = DEFAULT_BIN_SECS) -> PeakPyramid:
    """Fold streamed raw f32le mono PCM bytes into a PeakPyramid.

    Accepts any iterable of byte chunks (real ffmpeg stdout or synthetic test
    data), so no decode step is required to test this. The concatenated
    samples are held only for the duration of this call — only the returned
    (tiny) pyramid is kept.
    """
    buf = bytearray()
    for chunk in chunk_iter:
        buf.extend(chunk)
    usable = len(buf) - (len(buf) % 4)
    samples = np.frombuffer(bytes(buf[:usable]), dtype=np.float32)
    return build_pyramid(samples, sample_rate, bin_secs)
