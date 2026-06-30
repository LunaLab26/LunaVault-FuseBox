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
from core.sync_advanced import gcc_phat_lag, fit_offset_drift  # noqa: E402


def _noise(n, seed=0):
    rng = np.random.default_rng(seed)
    return rng.standard_normal(n)


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


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"\n{len(fns)} sync_advanced tests passed.")
