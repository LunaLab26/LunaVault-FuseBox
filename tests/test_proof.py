"""Tests for core/proof.py — the "see a memory come back" demonstration.

pick_shortest is a pure unit test. prove_recovery needs ffmpeg + ffprobe, so it
generates a tiny synthetic clip and is skipped if the binaries are absent (same
guard style as tests/test_extract.py's integration test). Standalone-runnable.
"""

import subprocess
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from core import proof  # noqa: E402


def test_pick_shortest():
    assert proof.pick_shortest([("a", 30.0), ("b", 5.0), ("c", 12.0)]) == "b"
    assert proof.pick_shortest([("a", None), ("b", 8.0)]) == "b"
    assert proof.pick_shortest([]) is None
    print("ok: test_pick_shortest")


def _integration_prove_recovery():
    from core.binaries import get_ffmpeg, no_window
    try:
        ff, fp = get_ffmpeg()
    except Exception:
        ff = fp = None
    if not ff or not Path(ff).exists() or not fp or not Path(fp).exists():
        print("  (skipped integration: ffmpeg not found)")
        return
    kw = no_window()
    with tempfile.TemporaryDirectory() as d:
        clip = Path(d) / "clip.mp4"
        subprocess.run(
            [ff, "-y", "-v", "error",
             "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=1",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
             "-c:v", "libx264", "-c:a", "aac", "-shortest", str(clip)],
            capture_output=True, **kw)
        assert clip.exists(), "could not create the synthetic clip"
        r = proof.prove_recovery(ff, fp, clip, Path(d) / "work", **kw)
        assert not r.error, f"proof errored: {r.error}"
        assert r.matched, f"proof should match a lossless round-trip: {r}"
        assert r.video_match and r.audio_present and r.audio_match
    print("  real ffmpeg proof round-trip: matched OK")


if __name__ == "__main__":
    test_pick_shortest()
    print("running real ffmpeg integration...")
    _integration_prove_recovery()
    print("test_proof: all tests passed")
