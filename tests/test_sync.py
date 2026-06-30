"""Tests for the pure cross-correlation core in core.sync.best_lag.

These run with no ffmpeg and no Qt — they feed synthetic signals with a known
offset and assert the algorithm recovers it. This is the safety net for the
Phase 2 GCC-PHAT + drift-correction rewrite: the public contract (offset in →
offset out) must keep holding.
"""

import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.sync import best_lag


def _signal(n: int, seed: int = 0) -> list:
    rnd = random.Random(seed)
    return [rnd.randint(-100, 100) for _ in range(n)]


def test_recovers_positive_offset():
    # wa is `base` advanced by K samples → best_lag should report +K.
    base = _signal(400)
    k = 5
    ca = base
    wa = base[k:]
    assert best_lag(ca, wa, max_lag_sub=20, step=1, min_samples=100) == k


def test_recovers_zero_offset():
    base = _signal(400, seed=1)
    assert best_lag(base, base, max_lag_sub=20, step=1, min_samples=100) == 0


def test_recovers_larger_offset():
    base = _signal(600, seed=2)
    k = 12
    assert best_lag(base, base[k:], max_lag_sub=30, step=1, min_samples=100) == k


def test_too_short_returns_none():
    base = _signal(50, seed=3)
    assert best_lag(base, base, max_lag_sub=10, step=1, min_samples=100) is None


def test_step_subsampling_still_finds_offset():
    # With step>1 the search is coarser but should land within one step of truth.
    base = _signal(900, seed=4)
    k = 9
    lag = best_lag(base, base[k:], max_lag_sub=20, step=3, min_samples=100)
    assert lag is not None
    assert abs(lag - k) <= 3


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"\n{len(fns)} sync tests passed.")
