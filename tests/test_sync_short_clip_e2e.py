"""Short clips (< ~5 s of camera/WAV overlap) must still be measured, not
end-aligned. Real 2 s Luna Ultra clips were ~0.3 s out before this."""
import subprocess as sp
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from core.binaries import get_ffmpeg  # noqa: E402
from core.sync_advanced import analyze_sync  # noqa: E402
from probe import probe_duration  # noqa: E402

SR = 48000


def _write(ff, path, sig, video_dur=None):
    raw = Path(str(path) + ".f32")
    np.stack([sig, sig], axis=1).astype(np.float32).tofile(raw)
    if video_dur is None:
        cmd = [ff, "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", str(raw),
               "-c:a", "pcm_s24le", str(path)]
    else:
        cmd = [ff, "-v", "error", "-y", "-f", "lavfi", "-i", f"color=c=gray:s=320x180:r=30:d={video_dur}",
               "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", str(raw),
               "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(path)]
    sp.run(cmd, check=True)
    raw.unlink()


@pytest.mark.parametrize("wav_start,wav_end", [(-0.42, 2.9), (0.3, 2.5), (-0.2, 1.9)])
def test_short_clip_offset_is_measured(wav_start, wav_end):
    ff, fp = get_ffmpeg()
    if not Path(ff).exists():
        pytest.skip("no ffmpeg")
    rng = np.random.default_rng(3)
    t0, t1 = -1.0, 4.0
    scene = (rng.standard_normal(int((t1 - t0) * SR)) * 0.1).astype(np.float32)
    seg = lambda a, b: scene[int((a - t0) * SR):int((b - t0) * SR)]
    with tempfile.TemporaryDirectory() as d:
        v, w = Path(d) / "c.mp4", Path(d) / "c.wav"
        _write(ff, v, seg(0.0, 2.2), video_dur=2.2)
        _write(ff, w, seg(wav_start, wav_end))
        r = analyze_sync(ff, str(v), str(w), probe_duration(fp, str(v)), probe_duration(fp, str(w)))
    assert r.ok and r.n_windows == 1
    assert abs(r.constant_offset - wav_start) < 0.005, r
