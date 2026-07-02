"""core/spectrogram.py — STFT spectrogram + a magma-style colour lookup table.

Pure NumPy: window, rFFT, magnitude in dB, normalized to [0, 1]. `MAGMA_LUT`
is a small precomputed 256x3 table (no external colour-map dependency) used
to turn the normalized spectrogram into an RGB image. Computed only for the
Review tab's currently-visible time window, not the whole track.
"""

import numpy as np

DEFAULT_N_FFT = 1024
DEFAULT_HOP = 256
DB_FLOOR = -90.0


def spectrogram(pcm, rate: int, n_fft: int = DEFAULT_N_FFT,
                hop: int = DEFAULT_HOP, db_floor: float = DB_FLOOR) -> np.ndarray:
    """(n_frames, n_fft//2+1) magnitude spectrogram in dB, normalized to [0, 1].

    Frame 0 is the earliest; column order matches time, matching how a
    spectrogram is conventionally read left-to-right.
    """
    pcm = np.asarray(pcm, dtype=np.float64).reshape(-1)
    if pcm.shape[0] < n_fft:
        pcm = np.pad(pcm, (0, n_fft - pcm.shape[0]))
    n = pcm.shape[0]
    window = np.hanning(n_fft)
    n_frames = max(1, 1 + (n - n_fft) // hop)
    frames = np.empty((n_frames, n_fft), dtype=np.float64)
    for i in range(n_frames):
        start = i * hop
        seg = pcm[start:start + n_fft]
        if seg.shape[0] < n_fft:
            seg = np.pad(seg, (0, n_fft - seg.shape[0]))
        frames[i] = seg * window

    mag = np.abs(np.fft.rfft(frames, axis=1))
    mag[mag < 1e-12] = 1e-12
    db = 20.0 * np.log10(mag)
    db = db - (db.max() if db.size else 0.0)   # normalize peak to 0 dB
    db = np.clip(db, db_floor, 0.0)
    return ((db - db_floor) / (-db_floor)).astype(np.float32)


def _build_magma_lut() -> np.ndarray:
    """256x3 uint8 approximation of matplotlib's 'magma' colormap, interpolated
    from a handful of anchor colours — no external plotting dependency."""
    anchors = np.array([
        [0,   0,   4],
        [20,  11,  53],
        [66,  10,  104],
        [114, 22,  110],
        [159, 42,  99],
        [203, 71,  82],
        [237, 105, 57],
        [251, 155, 6],
        [252, 210, 66],
        [252, 253, 191],
    ], dtype=np.float64)
    xs = np.linspace(0, 255, anchors.shape[0])
    idx = np.arange(256)
    out = np.empty((256, 3), dtype=np.uint8)
    for c in range(3):
        out[:, c] = np.clip(np.interp(idx, xs, anchors[:, c]), 0, 255).astype(np.uint8)
    return out


MAGMA_LUT = _build_magma_lut()


def to_rgb(spec_norm, lut: np.ndarray = MAGMA_LUT) -> np.ndarray:
    """Map a [0,1]-normalized spectrogram to an (n_frames, n_bins, 3) uint8 image."""
    idx = np.clip(np.round(np.asarray(spec_norm) * 255.0), 0, 255).astype(np.int32)
    return lut[idx]
