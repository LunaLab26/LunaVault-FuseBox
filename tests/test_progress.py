"""Tests for ffmpeg -progress parsing in core.progress."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from core.progress import parse_progress


def test_basic_percentage_and_size():
    out = parse_progress({"out_time_us": "5000000", "total_size": "1048576"}, 10.0)
    assert abs(out["pct"] - 50.0) < 1e-6
    assert out["size"] == 1048576
    assert abs(out["current_time"] - 5.0) < 1e-6


def test_caps_at_100():
    out = parse_progress({"out_time_us": "20000000"}, 10.0)
    assert out["pct"] == 100.0


def test_zero_duration_is_safe():
    out = parse_progress({"out_time_us": "5000000"}, 0.0)
    assert out["pct"] == 0.0


def test_garbage_values_dont_crash():
    out = parse_progress({"out_time_us": "N/A", "total_size": ""}, 10.0)
    assert out["pct"] == 0.0
    assert out["size"] == 0


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"\n{len(fns)} progress tests passed.")
