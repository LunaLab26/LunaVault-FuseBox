"""Tests for GCC-PHAT delay estimation and drift fitting (core.sync_advanced).

Synthetic signals with a known delay must come back out; the line fit must
recover a known offset + drift. These lock the contract before the engine is
wired to real media.
"""

import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402
import core.sync_advanced as sync_advanced  # noqa: E402
from core.sync_advanced import (  # noqa: E402
    gcc_phat_lag, fit_offset_drift, rms_envelope, envelope_offset, analyze_sync,
)


def _noise(n, seed=0):
    rng = np.random.default_rng(seed)
    return rng.standard_normal(n)


ENVELOPE_Z_GOOD = 4.0   # mirrors core.sync_advanced.ENVELOPE_Z_THRESHOLD


def test_gcc_phat_zero_delay():
    sig = _noise(4000)
    tau, peak = gcc_phat_lag(sig, sig, fs=8000, max_tau=0.05)
    assert abs(tau) < 1e-6
    assert peak > 0          # in-phase → positive peak


def test_gcc_phat_recovers_positive_delay():
    fs = 8000
    base = _noise(8000, seed=1)
    delay = 40               # samples → 5 ms at 8 kHz
    sig = np.concatenate([np.zeros(delay), base[:-delay]])   # sig delayed vs base
    tau, _ = gcc_phat_lag(sig, base, fs=fs, max_tau=0.05)
    assert abs(tau - delay / fs) < 1e-6


def test_gcc_phat_recovers_negative_delay():
    fs = 8000
    base = _noise(8000, seed=2)
    adv = 24
    sig = np.concatenate([base[adv:], np.zeros(adv)])        # sig advanced vs base
    tau, _ = gcc_phat_lag(sig, base, fs=fs, max_tau=0.05)
    assert abs(tau - (-adv / fs)) < 1e-6


def test_gcc_phat_detects_inverted_polarity():
    base = _noise(4000, seed=3)
    tau, peak = gcc_phat_lag(-base, base, fs=8000, max_tau=0.05)
    assert abs(tau) < 1e-6
    assert peak < 0          # inverted → negative peak


def test_gcc_phat_robust_to_level_and_offset():
    # Different gain + DC offset shouldn't move the delay estimate.
    fs = 8000
    base = _noise(8000, seed=4)
    delay = 16
    sig = np.concatenate([np.zeros(delay), base[:-delay]]) * 0.3 + 5.0
    tau, _ = gcc_phat_lag(sig, base, fs=fs, max_tau=0.05)
    assert abs(tau - delay / fs) < 1e-6


def test_fit_offset_drift_recovers_line():
    times = [0.0, 10.0, 20.0, 30.0]
    offset_true, drift_true = 0.18, 0.0001        # 0.18s offset, +0.1 ms/s
    lags = [offset_true + drift_true * t for t in times]
    offset, drift, resid = fit_offset_drift(times, lags)
    assert abs(offset - offset_true) < 1e-9
    assert abs(drift - drift_true) < 1e-12
    assert resid < 1e-9


def test_fit_offset_drift_single_point():
    offset, drift, resid = fit_offset_drift([5.0], [0.2])
    assert offset == 0.2 and drift == 0.0 and resid == 0.0


# ── Coarse envelope alignment (the large clip/WAV duration-mismatch rescue) ──

def test_rms_envelope_reflects_loud_vs_quiet_blocks():
    quiet = np.full(1000, 0.01)
    loud = np.full(1000, 2.0)
    pcm = np.concatenate([quiet, loud, quiet])
    env = rms_envelope(pcm, bin_n=1000)
    assert env.shape == (3,)
    assert env[0] < 0.02 and env[2] < 0.02
    assert env[1] > 1.9


def test_rms_envelope_drops_a_trailing_partial_bin():
    pcm = np.ones(2500)
    env = rms_envelope(pcm, bin_n=1000)
    assert env.shape == (2,)   # the trailing 500 samples don't fill a bin


def test_envelope_offset_recovers_a_known_shift():
    # env_b is a long, noisy loudness contour; env_a is an exact slice of it
    # starting at bin 40 — envelope_offset must find that offset with a high
    # z-score (matches what was measured directly against real audio: a
    # genuine match scores z≈10, see the module docstring).
    rng = np.random.default_rng(7)
    env_b = rng.uniform(0.05, 0.5, size=200)
    env_a = env_b[40:40 + 30].copy()
    offset, z = envelope_offset(env_a, env_b)
    assert offset == 40
    assert z > ENVELOPE_Z_GOOD


def test_envelope_offset_low_confidence_for_unrelated_signals():
    rng = np.random.default_rng(11)
    env_a = rng.uniform(0.05, 0.5, size=30)
    env_b = rng.uniform(0.05, 0.5, size=200)   # independent noise, no embedded match
    _, z = envelope_offset(env_a, env_b)
    assert z < ENVELOPE_Z_GOOD


def test_envelope_offset_a_longer_than_b_is_a_safe_no_match():
    offset, z = envelope_offset(np.ones(50), np.ones(10))
    assert offset == 0 and z == 0.0


# ── anchor_mode override (manual align-to-start/end vs the automatic rescue) ─
#
# analyze_sync needs real ffmpeg extraction to run its fine per-window pass no
# matter which anchor it started from, so these monkeypatch _extract (returns
# fixed fake PCM — content doesn't matter, only that a window "succeeds") and
# _coarse_preroll (records whether it was called) to isolate just the anchor-
# selection branching, without needing real media.

class _Patched:
    """Swap a module attribute for the block's duration, then restore it —
    this project's tests run as plain scripts (no pytest), so no monkeypatch
    fixture is available."""
    def __init__(self, module, name, value):
        self._module, self._name, self._value = module, name, value

    def __enter__(self):
        self._orig = getattr(self._module, self._name)
        setattr(self._module, self._name, self._value)
        return self

    def __exit__(self, *exc):
        setattr(self._module, self._name, self._orig)


def _fake_extract(ff, src, t_start, sr, win, timeout=20.0):
    return [0] * int(sr * win)   # silence — enough samples for a window to "succeed"


def test_anchor_mode_start_skips_coarse_rescue_and_forces_zero_preroll():
    calls = []
    with _Patched(sync_advanced, "_extract", _fake_extract), \
         _Patched(sync_advanced, "_coarse_preroll",
                  lambda *a, **k: calls.append(1) or (999.0, 99.0)):
        res = analyze_sync("ff", "clip.mp4", "wav.wav",
                           clip_dur=100.0, wav_dur=500.0, anchor_mode="start")
    assert calls == []   # the coarse rescue must never run under an explicit override
    assert res.ok
    # preroll=0 means each window's wav-side time equals its clip-side time —
    # confirm via the first window's recorded centre falling inside clip_dur,
    # not off in the 500s-long wav as end-alignment would have placed it.
    assert all(t < 100.0 for t in res.window_times_s)


def test_anchor_mode_end_skips_coarse_rescue_even_for_a_large_mismatch():
    calls = []
    with _Patched(sync_advanced, "_extract", _fake_extract), \
         _Patched(sync_advanced, "_coarse_preroll",
                  lambda *a, **k: calls.append(1) or (999.0, 99.0)):
        res = analyze_sync("ff", "clip.mp4", "wav.wav",
                           clip_dur=100.0, wav_dur=500.0, anchor_mode="end")
    assert calls == []
    assert res.ok
    assert "manual override" in res.note


def test_anchor_mode_auto_still_runs_the_coarse_rescue_for_a_large_mismatch():
    calls = []
    with _Patched(sync_advanced, "_extract", _fake_extract), \
         _Patched(sync_advanced, "_coarse_preroll",
                  lambda *a, **k: calls.append(1) or (999.0, 99.0)):
        analyze_sync("ff", "clip.mp4", "wav.wav",
                     clip_dur=100.0, wav_dur=500.0, anchor_mode="auto")
    assert calls == [1]   # the default path is unaffected by adding the override


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"\n{len(fns)} sync_advanced tests passed.")
