"""Tests for core.eta — conservative byte-weighted ETA estimation."""

import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.eta import ConservativeEta, format_hms, format_completion


class _FakeClock:
    def __init__(self, start=0.0):
        self.t = start

    def __call__(self):
        return self.t

    def advance(self, secs):
        self.t += secs


def test_no_estimate_before_enough_progress():
    clk = _FakeClock()
    e = ConservativeEta(clock=clk)
    r = e.estimate(0, 1000)
    assert r["eta_secs"] is None and r["total_secs"] is None
    assert r["pct"] == 0.0


def test_steady_rate_matches_simple_extrapolation():
    # Constant 100 bytes/sec, 1000 total: at 300 bytes (30%) after 3s, the
    # average-rate formula (elapsed*(1-frac)/frac) predicts 7s remaining —
    # and since the rate never varies, the "slowest recent rate" estimate
    # agrees, so the conservative max() doesn't inflate it further.
    clk = _FakeClock()
    e = ConservativeEta(clock=clk)
    e.estimate(0, 1000)
    for produced in (100, 200, 300):
        clk.advance(1.0)
        r = e.estimate(produced, 1000)
    assert abs(r["pct"] - 30.0) < 0.01
    assert abs(r["eta_secs"] - 7.0) < 0.5


def test_conservative_estimate_beats_optimistic_average_when_rate_slows():
    # Fast start (front-loaded cheap work), then a slowdown (the transcode-
    # after-copies pattern this module exists to correct for). The average-
    # rate formula alone would still be dragged optimistic by the fast
    # early samples; the conservative max() must pick the slower-rate
    # extrapolation instead once the slowdown is the RECENT behaviour.
    clk = _FakeClock()
    e = ConservativeEta(clock=clk)
    e.estimate(0, 10000)
    # Fast phase: 1000 bytes/sec for 5 seconds -> 5000 bytes.
    produced = 0
    for _ in range(5):
        clk.advance(1.0)
        produced += 1000
        r = e.estimate(produced, 10000)
    fast_only_eta = r["eta_secs"]
    # Slow phase: 100 bytes/sec for 5 more seconds -> +500 bytes (5500 total).
    produced = 5000
    for _ in range(5):
        clk.advance(1.0)
        produced += 100
        r = e.estimate(produced, 10000)
    # The average-rate-only figure at this point would extrapolate from the
    # blended (still fairly fast) average rate; the conservative estimate
    # must be at least as long as continuing at the newly-observed slow rate
    # (100 B/s, 4500 bytes remaining -> 45s).
    assert r["eta_secs"] >= 44.0
    assert r["eta_secs"] >= fast_only_eta


def test_pct_clamped_and_monotonic_with_produced_bytes():
    clk = _FakeClock()
    e = ConservativeEta(clock=clk)
    clk.advance(1.0)
    r1 = e.estimate(500, 1000)
    clk.advance(1.0)
    r2 = e.estimate(999, 1000)
    clk.advance(1.0)
    r3 = e.estimate(1000, 1000)   # fully produced -> pct clamped under 100
    assert r1["pct"] < r2["pct"] < r3["pct"]
    assert r3["pct"] < 100.0   # clamped (never claims "done" via this path)


def test_zero_expected_total_does_not_crash():
    clk = _FakeClock()
    e = ConservativeEta(clock=clk)
    r = e.estimate(0, 0)
    assert r["pct"] == 0.0


def test_format_hms_hours_minutes_seconds():
    assert format_hms(3661) == "1h01m01s"
    assert format_hms(75) == "1m15s"
    assert format_hms(9) == "9s"
    assert format_hms(0) == "0s"
    assert format_hms(None) == "—"
    assert format_hms(-5) == "—"


def test_format_completion_uses_provided_now():
    now = datetime(2026, 7, 12, 19, 15, 0)   # a Sunday
    out = format_completion(3600, now=now)
    assert out == "20:15, Sunday 12 July 2026"
    assert format_completion(None, now=now) == "—"


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"\n{len(fns)} eta tests passed.")
