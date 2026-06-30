"""core/sync_advanced.py — GCC-PHAT time-delay estimation + clock-drift fitting.

This is the Phase 2 upgrade over the plain cross-correlation in sync.py. GCC-PHAT
(Generalized Cross-Correlation with Phase Transform) whitens both signals before
correlating, so it gives a sharp, level/timbre-robust delay estimate between two
*different* microphones — exactly our Bluetooth-vs-onboard case.

By estimating the lag in several windows spread across the overlap and fitting a
line lag(t) = offset + drift·t, we separate a constant offset (used for the
lossless WAV track) from clock drift (applied only to the derived mix track).

Pure NumPy DSP (`gcc_phat_lag`, `fit_offset_drift`) is unit-tested with synthetic
signals; `analyze_sync` adds the ffmpeg extraction on top.
"""

import array as _array
import subprocess
from dataclasses import dataclass, field
from typing import Optional

from .binaries import no_window

# ── Tunables ──────────────────────────────────────────────────────────────────
ANALYSIS_SR   = 8000    # Hz — higher than the legacy 2000 for finer timing
WINDOW_SECS   = 4.0     # length of each analysis window
N_WINDOWS     = 6       # windows spread across the overlap (≥2 needed for drift)
MAX_TAU       = 0.50    # max |lag| to consider per window (seconds)
EDGE_SKIP     = 0.5     # don't sample within this many secs of either end


@dataclass
class SyncResult:
    """Outcome of analysing one camera-clip ↔ WAV pair."""
    end_offset: float        = 0.0   # mp4_dur - wav_dur (raw end-alignment)
    constant_offset: float   = 0.0   # best constant offset → LOSSLESS WAV track
    drift_ratio: float       = 1.0   # WAV resample factor → MIX track only
    confidence_ms: float     = 0.0   # std-dev of per-window lag residuals
    polarity_inverted: bool  = False
    n_windows: int           = 0
    window_lags_ms: list     = field(default_factory=list)
    window_times_s: list     = field(default_factory=list)
    ok: bool                 = False
    note: str                = ""

    def drift_ms_per_min(self) -> float:
        return (self.drift_ratio - 1.0) * 60_000.0


# ── Pure DSP (unit-tested) ────────────────────────────────────────────────────

def gcc_phat_lag(sig, ref, fs: float = 1.0, max_tau: Optional[float] = None):
    """Return (tau_seconds, peak_value) — delay of `sig` relative to `ref`.

    Positive tau → `sig` arrives later than `ref`. `peak_value` carries the sign
    of the correlation peak (negative ⇒ likely inverted polarity).
    """
    import numpy as np

    sig = np.asarray(sig, dtype=np.float64)
    ref = np.asarray(ref, dtype=np.float64)
    if sig.size == 0 or ref.size == 0:
        return 0.0, 0.0
    sig = sig - sig.mean()
    ref = ref - ref.mean()

    n = sig.size + ref.size
    SIG = np.fft.rfft(sig, n=n)
    REF = np.fft.rfft(ref, n=n)
    R = SIG * np.conj(REF)
    denom = np.abs(R)
    denom[denom < 1e-12] = 1e-12
    R /= denom                          # phase transform
    cc = np.fft.irfft(R, n=n)

    max_shift = n // 2
    if max_tau is not None:
        max_shift = min(int(fs * max_tau), max_shift)
    cc = np.concatenate((cc[-max_shift:], cc[:max_shift + 1]))

    idx = int(np.argmax(np.abs(cc)))
    shift = idx - max_shift
    peak = float(cc[idx])
    return shift / float(fs), peak


def fit_offset_drift(times, lags):
    """Least-squares fit lag = offset + drift·t.

    Returns (offset_at_t0, drift_slope, residual_std). `drift_slope` is seconds
    of lag gained per second of runtime (the clock-rate error). With a single
    point, drift is 0 and offset is that point.
    """
    import numpy as np

    t = np.asarray(times, dtype=np.float64)
    y = np.asarray(lags, dtype=np.float64)
    if t.size == 0:
        return 0.0, 0.0, 0.0
    if t.size == 1:
        return float(y[0]), 0.0, 0.0
    drift, offset = np.polyfit(t, y, 1)        # y = drift*t + offset
    resid = y - (drift * t + offset)
    return float(offset), float(drift), float(resid.std())


# ── ffmpeg extraction + orchestration ─────────────────────────────────────────

def _extract(ff: str, src: str, t_start: float, sr: int, win: float) -> Optional[list]:
    t_start = max(0.0, t_start)
    cmd = [ff, "-hide_banner", "-loglevel", "error",
           "-ss", f"{t_start:.3f}", "-i", src,
           "-t", f"{win:.2f}", "-vn",
           "-af", "highpass=f=120",
           "-ar", str(sr), "-ac", "1", "-f", "s16le", "pipe:1"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=20, **no_window())
    except Exception:
        return None
    if r.returncode != 0 or len(r.stdout) < 2:
        return None
    n = len(r.stdout) // 2
    return list(_array.array("h", r.stdout[:n * 2]))


def analyze_sync(ff: str, clip_path: str, wav_path: str,
                 clip_dur: float, wav_dur: float) -> SyncResult:
    """Full per-pair analysis: end-align + GCC-PHAT windows + drift fit.

    `constant_offset` is what the lossless WAV track uses (one fixed shift, never
    resampled). `drift_ratio` is applied ONLY to the derived mix track so its WAV
    side tracks the camera clock across the whole clip.
    """
    res = SyncResult()
    res.end_offset = clip_dur - wav_dur
    res.constant_offset = res.end_offset

    overlap = min(clip_dur, wav_dur)
    if overlap < WINDOW_SECS + 2 * EDGE_SKIP:
        res.note = "overlap too short for analysis; using end-alignment"
        return res

    usable = overlap - WINDOW_SECS - 2 * EDGE_SKIP
    step = usable / max(1, N_WINDOWS - 1)

    lags: list[float] = []
    times: list[float] = []
    peaks: list[float] = []

    for i in range(N_WINDOWS):
        # window start measured from the END of each source (end-aligned frame)
        from_end = WINDOW_SECS + EDGE_SKIP + (usable - i * step)
        ct = clip_dur - from_end
        wt = wav_dur  - from_end
        if ct < 0 or wt < 0:
            continue
        ca = _extract(ff, clip_path, ct, ANALYSIS_SR, WINDOW_SECS)
        wa = _extract(ff, wav_path,  wt, ANALYSIS_SR, WINDOW_SECS)
        if not ca or not wa:
            continue
        tau, peak = gcc_phat_lag(ca, wa, fs=ANALYSIS_SR, max_tau=MAX_TAU)
        lags.append(tau)
        # time of this window's centre, measured from clip start
        times.append(ct + WINDOW_SECS / 2.0)
        peaks.append(peak)

    if not lags:
        res.note = "no usable windows; using end-alignment"
        return res

    offset0, drift, resid_std = fit_offset_drift(times, lags)

    # Constant offset for the lossless track: end-alignment plus the median
    # window lag (robust to outliers), clamped so we never wander far.
    sorted_lags = sorted(lags)
    median_lag = sorted_lags[len(sorted_lags) // 2]
    res.constant_offset = res.end_offset + median_lag
    res.drift_ratio = 1.0 + drift            # WAV side resample factor for the mix
    res.confidence_ms = resid_std * 1000.0
    res.polarity_inverted = (sum(peaks) < 0)
    res.n_windows = len(lags)
    res.window_lags_ms = [round(x * 1000.0, 2) for x in lags]
    res.window_times_s = [round(x, 2) for x in times]
    res.ok = True
    return res
