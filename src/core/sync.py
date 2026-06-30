"""core/sync.py — WAV ↔ camera audio synchronisation analysis (UI-agnostic).

The starting estimate is end-alignment (mp4_dur - wav_dur). We then sample a few
short audio windows from both sources, cross-correlate each at a low sample-rate,
take the median lag, and clamp the result to ±SYNC_MAX_S of the end-aligned
position.

Phase 0 note: this preserves the v1.2 algorithm verbatim, but with the
correlation core (`best_lag`) lifted out as a pure function so it can be unit
tested. Phase 2 replaces `best_lag` with GCC-PHAT and adds start+end drift
estimation — applied only to the derived mix track, never the lossless WAV.
"""

import array as _array
import subprocess
from typing import Optional

from .binaries import no_window

# ── Tunables ──────────────────────────────────────────────────────────────────
SYNC_SR    = 2000   # analysis sample rate (Hz) — enough for transients
SYNC_WIN   = 3.0    # duration of each audio window (seconds)
SYNC_WINS  = 5      # number of windows to sample
SYNC_MAX_S = 0.10   # maximum shift from end-alignment (seconds)
SYNC_STEP  = 3      # sub-sample factor inside cross-correlation (speed up)


def best_lag(ca: list, wa: list, max_lag_sub: int, step: int,
             min_samples: int) -> Optional[int]:
    """Cross-correlate two equal-rate signals; return lag in FULL-rate samples.

    Pure and dependency-free so it can be unit tested with synthetic signals.
    `max_lag_sub` is the search range in *sub-sampled* steps; the returned lag is
    scaled back by `step`. Returns None if the overlap is shorter than
    `min_samples`.
    """
    min_len = min(len(ca), len(wa))
    if min_len < min_samples:
        return None
    ca_s = ca[:min_len:step]
    wa_s = wa[:min_len:step]
    cap  = min(len(ca_s), len(wa_s))
    best_v, best_l = float("-inf"), 0
    for lag in range(-max_lag_sub, max_lag_sub + 1):
        if lag >= 0:
            cs = ca_s[lag:cap]
            ws = wa_s[:cap - lag]
        else:
            cs = ca_s[:cap + lag]
            ws = wa_s[-lag:cap]
        n = min(len(cs), len(ws))
        if n == 0:
            continue
        c = sum(cs[i] * ws[i] for i in range(n))
        if c > best_v:
            best_v = c
            best_l = lag
    return best_l * step   # back to full-rate samples


def _extract(ff: str, src: str, t_start: float, sr: int, win: float) -> Optional[list]:
    """Decode a mono, high-passed PCM window from `src` starting at `t_start`."""
    t_start = max(0.0, t_start)
    cmd = [ff, "-hide_banner", "-loglevel", "error",
           "-ss", f"{t_start:.3f}", "-i", src,
           "-t", f"{win:.1f}", "-vn",
           "-af", "highpass=f=200",
           "-ar", str(sr), "-ac", "1", "-f", "s16le", "pipe:1"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=15, **no_window())
    except Exception:
        return None
    if r.returncode != 0 or len(r.stdout) < 2:
        return None
    n = len(r.stdout) // 2
    return list(_array.array("h", r.stdout[:n * 2]))


def refine_wav_sync(ff: str, clip_path: str, wav_path: str,
                    clip_dur: float, wav_dur: float) -> float:
    """Return a delta (seconds) to ADD to the raw end-aligned offset.

    Positive delta → shift WAV later; negative → shift WAV earlier. Clamped to
    ±SYNC_MAX_S so we never stray far from end-alignment. Returns 0.0 on any
    failure (falls back to pure end-alignment).
    """
    sr        = SYNC_SR
    win       = SYNC_WIN
    max_lag   = int(SYNC_MAX_S * sr)                              # full-rate samples
    max_lag_sub = max(1, (max_lag + SYNC_STEP - 1) // SYNC_STEP)  # sub-sampled
    min_samples = sr                                             # need ≥ 1 second

    overlap = min(clip_dur, wav_dur)
    if overlap < win + 1.0:
        return 0.0

    spacing = max(0.0, (overlap - win) / max(SYNC_WINS, 1))
    lags: list[float] = []

    for i in range(SYNC_WINS):
        offset_from_end = win + i * spacing
        ct = clip_dur - offset_from_end
        wt = wav_dur  - offset_from_end
        if ct < 0 or wt < 0:
            break
        ca = _extract(ff, clip_path, ct, sr, win)
        wa = _extract(ff, wav_path,  wt, sr, win)
        if ca and wa:
            lg = best_lag(ca, wa, max_lag_sub, SYNC_STEP, min_samples)
            if lg is not None:
                lags.append(lg / sr)

    if not lags:
        return 0.0

    lags.sort()
    median = lags[len(lags) // 2]
    return max(-SYNC_MAX_S, min(SYNC_MAX_S, median))
