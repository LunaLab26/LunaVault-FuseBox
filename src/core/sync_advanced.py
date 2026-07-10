"""core/sync_advanced.py — GCC-PHAT time-delay estimation + clock-drift fitting.

This is the Phase 2 upgrade over the plain cross-correlation in sync.py. GCC-PHAT
(Generalized Cross-Correlation with Phase Transform) whitens both signals before
correlating, so it gives a sharp, level/timbre-robust delay estimate between two
*different* microphones — exactly our Bluetooth-vs-onboard case.

By estimating the lag in several windows spread across the overlap and fitting a
line lag(t) = offset + drift·t, we separate a constant offset (used for the
lossless WAV track) from clock drift (applied only to the derived mix track).

Pure NumPy DSP (`gcc_phat_lag`, `fit_offset_drift`, `rms_envelope`,
`envelope_offset`) is unit-tested with synthetic signals; `analyze_sync` adds
the ffmpeg extraction on top.

Every per-window GCC-PHAT search below is anchored on an assumption of
END-ALIGNMENT (`preroll = wav_dur - clip_dur`: the two recordings are
assumed to finish at roughly the same real-world moment, so any excess WAV
duration must be un-recorded lead-in at its START) and only searches
±MAX_TAU=0.5s around that anchor per window. That's the right, cheap
assumption for ordinary pre/post-roll (a camera and a separate WAV/wireless
mic recorder rarely start or stop within the same video frame — confirmed
directly against a real 8-clip shoot: 7 of 8 clips had a 0.3-0.4s WAV/video
duration difference, well inside this tolerance). It breaks down completely
when the WAV recorder was left running across a much longer span than its
video (the 8th clip in that same shoot: a WAV backup 385 SECONDS longer
than its video, from a mic that was recording well before the camera
started) — end-alignment then points nowhere near the true overlap, every
fine window searches the wrong region, and the resulting "constant offset"
embeds genuinely wrong, unsynced audio into the lossless WAV baseline track
for that clip's entire duration. `envelope_offset` is the fix: a coarse,
second-resolution RMS-envelope cross-correlation over the WHOLE clip/WAV
pair — far more forgiving of clock drift and per-sample noise than
sample-level GCC-PHAT, cheap even for a 30+ minute clip — used ONLY when
the raw duration mismatch is large enough that end-alignment can't be
trusted, to find a much better anchor for the existing fine per-window
pass to refine.
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

# A clip/WAV duration difference beyond this is not ordinary pre/post-roll —
# real shoots show ~0.3-0.4s; this leaves a wide margin before distrusting
# end-alignment, so it won't fire on a merely slightly-longer WAV.
LARGE_MISMATCH_S    = 5.0
ENVELOPE_BIN_SECS   = 1.0    # coarse alignment resolution — cheap, drift-tolerant
# How many standard deviations the best envelope-correlation peak must clear
# the noise floor by before it's trusted as a real match rather than a
# spurious peak in a large search space (measured directly against a real
# true match: z≈10.5; a scrambled/unrelated pair sits near 0).
ENVELOPE_Z_THRESHOLD = 4.0


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


def rms_envelope(pcm, bin_n: int):
    """Per-bin RMS of `pcm` (1-D samples), `bin_n` samples per bin — e.g.
    `bin_n = int(ENVELOPE_BIN_SECS * sample_rate)` for a 1-second-resolution
    loudness contour. Trailing samples that don't fill a whole bin are
    dropped. The coarse, second-scale counterpart to gcc_phat_lag's
    sample-accurate delay estimate — see the module docstring for why a
    large clip/WAV duration mismatch needs this instead."""
    import numpy as np

    pcm = np.asarray(pcm, dtype=np.float64)
    bin_n = max(1, int(bin_n))
    n_bins = pcm.size // bin_n
    if n_bins == 0:
        return np.zeros(0, dtype=np.float64)
    trimmed = pcm[: n_bins * bin_n].reshape(n_bins, bin_n)
    return np.sqrt(np.mean(trimmed * trimmed, axis=1))


def envelope_offset(env_a, env_b):
    """Where in `env_b` does `env_a` best align? Cross-correlates two RMS
    envelopes (from `rms_envelope`, same bin size) and returns
    `(offset_bins, z_score)`: `offset_bins` is the bin index into `env_b`
    that `env_a`'s bin 0 best lines up with, and `z_score` is how many
    standard deviations that best peak clears the correlation's own mean —
    the confidence gate callers use to decide whether to trust it (measured
    directly: a genuine match scores z≈10, unrelated audio sits near 0).
    `env_a` must be no longer than `env_b`."""
    import numpy as np

    a = np.asarray(env_a, dtype=np.float64)
    b = np.asarray(env_b, dtype=np.float64)
    if a.size == 0 or b.size < a.size:
        return 0, 0.0
    a_n = (a - a.mean()) / (a.std() + 1e-9)
    b_n = (b - b.mean()) / (b.std() + 1e-9)
    corr = np.correlate(b_n, a_n, mode="valid")   # corr[k] = alignment with a placed at offset k
    if corr.size == 0:
        return 0, 0.0
    best = int(np.argmax(corr))
    z = float((corr[best] - corr.mean()) / (corr.std() + 1e-9))
    return best, z


# ── ffmpeg extraction + orchestration ─────────────────────────────────────────

def _extract(ff: str, src: str, t_start: float, sr: int, win: float,
            timeout: float = 20.0) -> Optional[list]:
    t_start = max(0.0, t_start)
    cmd = [ff, "-hide_banner", "-loglevel", "error",
           "-ss", f"{t_start:.3f}", "-i", src,
           "-t", f"{win:.2f}", "-vn",
           "-af", "highpass=f=120",
           "-ar", str(sr), "-ac", "1", "-f", "s16le", "pipe:1"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=timeout, **no_window())
    except Exception:
        return None
    if r.returncode != 0 or len(r.stdout) < 2:
        return None
    n = len(r.stdout) // 2
    return list(_array.array("h", r.stdout[:n * 2]))


def _extract_envelope(ff: str, src: str, dur: float, sr: int, bin_secs: float):
    """The whole-file counterpart to `_extract`: one big PCM pull (from t=0
    for `dur` seconds) reduced straight to an RMS envelope — cheap even for
    a 30+ minute clip since it never leaves this as raw per-sample data.

    `_extract`'s default 20s timeout is sized for the short ~4s per-window
    pulls elsewhere in this module — nowhere near enough to demux 30+
    minutes of audio out of a large 4K/HEVC source (confirmed directly: a
    real 30-minute, 11.7GB clip's audio extraction silently timed out and
    returned None under the default). Scaled to the actual pull size, with
    a floor for short clips and a ceiling so a genuinely stuck ffmpeg
    process can't hang the merge indefinitely."""
    timeout = max(60.0, min(600.0, dur * 1.0))
    pcm = _extract(ff, src, 0.0, sr, dur, timeout=timeout)
    if not pcm:
        return None
    return rms_envelope(pcm, int(bin_secs * sr))


def _coarse_preroll(ff: str, clip_path: str, wav_path: str,
                    clip_dur: float, wav_dur: float):
    """Envelope-based rescue for when end-alignment can't be trusted (see the
    module docstring). Returns (preroll_seconds, z_score) or None if either
    extraction failed. Positive preroll = seconds of un-recorded lead-in at
    the WAV's start, same sign convention `analyze_sync` already uses."""
    env_clip = _extract_envelope(ff, clip_path, clip_dur, ANALYSIS_SR, ENVELOPE_BIN_SECS)
    env_wav  = _extract_envelope(ff, wav_path,  wav_dur,  ANALYSIS_SR, ENVELOPE_BIN_SECS)
    if env_clip is None or env_wav is None or env_clip.size == 0 or env_wav.size == 0:
        return None
    # envelope_offset needs the shorter sequence as `a`; whichever recording
    # is longer holds the whole clip within it somewhere.
    if env_clip.size <= env_wav.size:
        offset_bins, z = envelope_offset(env_clip, env_wav)
        preroll = offset_bins * ENVELOPE_BIN_SECS
    else:
        offset_bins, z = envelope_offset(env_wav, env_clip)
        preroll = -offset_bins * ENVELOPE_BIN_SECS
    return preroll, z


def analyze_sync(ff: str, clip_path: str, wav_path: str,
                 clip_dur: float, wav_dur: float, anchor_mode: str = "auto") -> SyncResult:
    """Full per-pair analysis: end-align (or, for a large duration mismatch,
    a coarse envelope rescue — see module docstring) + GCC-PHAT windows +
    drift fit.

    `constant_offset` is what the lossless WAV track uses (one fixed shift, never
    resampled). `drift_ratio` is applied ONLY to the derived mix track so its WAV
    side tracks the camera clock across the whole clip.

    `anchor_mode` — the user's own override, from the Advanced sync dialog,
    of what "the two recordings line up here" should mean:
      - "auto" (default): end-alignment, or the coarse envelope rescue above
        when the duration mismatch is large — see LARGE_MISMATCH_S.
      - "start": force preroll=0 — assume the WAV and clip begin together,
        skipping end-alignment (and the coarse rescue) entirely. Right when
        the user knows a WAV recorder was started in sync with the camera
        but kept running well past it (the mismatch that "auto"'s coarse
        rescue exists to catch, made explicit instead of inferred).
      - "end": force literal end-alignment even for a large mismatch,
        bypassing the coarse rescue safety net — the user's own call that
        the recordings really do finish together (e.g. both stopped for a
        take break at the same moment).
    """
    res = SyncResult()
    res.end_offset = clip_dur - wav_dur
    res.constant_offset = res.end_offset

    # preroll: seconds of un-recorded WAV lead-in before the clip's own t=0 —
    # end-alignment's implicit assumption is that ALL of the duration
    # difference is exactly this (nothing at the tail). Everything below is
    # expressed in terms of this one anchor so the coarse-rescue path and the
    # normal path share the same window-placement math.
    preroll = wav_dur - clip_dur
    if anchor_mode == "start":
        preroll = 0.0
        if abs(res.end_offset) > LARGE_MISMATCH_S:
            res.note = "aligned to clip start (manual override)"
    elif anchor_mode == "end":
        if abs(res.end_offset) > LARGE_MISMATCH_S:
            res.note = "aligned to clip end (manual override) — the coarse-mismatch rescue was skipped"
    elif abs(res.end_offset) > LARGE_MISMATCH_S:   # anchor_mode == "auto"
        coarse = _coarse_preroll(ff, clip_path, wav_path, clip_dur, wav_dur)
        if coarse is not None and coarse[1] >= ENVELOPE_Z_THRESHOLD:
            preroll, z = coarse
            res.note = (f"large {abs(res.end_offset):.1f}s clip/WAV duration mismatch — "
                       f"end-alignment would have been wrong; used a coarse envelope "
                       f"match instead (confidence z={z:.1f})")
        else:
            z = coarse[1] if coarse is not None else 0.0
            res.note = (f"large {abs(res.end_offset):.1f}s clip/WAV duration mismatch and "
                       f"no confident coarse match (z={z:.1f}) — falling back to "
                       f"end-alignment, but this clip's sync should be checked by ear")

    overlap_start = max(0.0, -preroll)
    overlap_end = min(clip_dur, wav_dur - preroll)
    overlap = overlap_end - overlap_start
    if overlap < WINDOW_SECS + 2 * EDGE_SKIP:
        res.note = (res.note + "; " if res.note else "") + "overlap too short for analysis; using end-alignment"
        return res

    usable = overlap - WINDOW_SECS - 2 * EDGE_SKIP
    step = usable / max(1, N_WINDOWS - 1)

    lags: list[float] = []
    times: list[float] = []
    peaks: list[float] = []

    for i in range(N_WINDOWS):
        ct = overlap_start + EDGE_SKIP + i * step
        wt = ct + preroll
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
        res.note = (res.note + "; " if res.note else "") + "no usable windows; using end-alignment"
        return res

    offset0, drift, resid_std = fit_offset_drift(times, lags)

    # Constant offset for the lossless track: the anchor (end-alignment, or
    # the coarse rescue above) plus the median window lag (robust to
    # outliers), clamped so we never wander far from a trusted starting point.
    sorted_lags = sorted(lags)
    median_lag = sorted_lags[len(sorted_lags) // 2]
    res.constant_offset = -preroll + median_lag
    res.drift_ratio = 1.0 + drift            # WAV side resample factor for the mix
    res.confidence_ms = resid_std * 1000.0
    res.polarity_inverted = (sum(peaks) < 0)
    res.n_windows = len(lags)
    res.window_lags_ms = [round(x * 1000.0, 2) for x in lags]
    res.window_times_s = [round(x, 2) for x in times]
    res.ok = True
    return res
