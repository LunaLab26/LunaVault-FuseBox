"""SYNC_W: clips + WAV backups cut from one shared 'scene' recording, with the WAV
starting early / late / ending early, so WAV-track sync can be measured per join."""
import os
FF = os.environ.get("LV_FFMPEG", "ffmpeg")
FP = os.environ.get("LV_FFPROBE", "ffprobe")
import subprocess, sys
from pathlib import Path
import numpy as np

OUT = Path(sys.argv[1]); OUT.mkdir(parents=True, exist_ok=True)
SR = 48000
DUR = 4.5
EV = [(1.0, 1.1), (3.0, 3.1)]
# name, wav_start (rel. to video start), wav_end
CASES = [
    ("VID_20260921_110001_001", -0.40, DUR + 0.20),   # WAV rolls first (needs trim)
    ("VID_20260921_110101_002", +0.30, DUR + 0.20),   # WAV starts late (needs delay)
    ("VID_20260921_110201_003", -0.20, DUR - 0.50),   # WAV stops early
    ("VID_20260921_110301_004", 0.00, DUR),           # exact
]
rng = np.random.default_rng(7)


# one deterministic scene buffer from -1 s to DUR+1 s, shared by camera and WAV
T0, T1 = -1.0, DUR + 1.0
N = int(round((T1 - T0) * SR))
tt = T0 + np.arange(N) / SR
for name, ws, we in CASES:
    noise = rng.standard_normal(N) * (0.05 + 0.04 * np.sin(2 * np.pi * 0.9 * tt) ** 2)
    beep = np.zeros(N)
    for a, b in EV:
        m = (tt >= a) & (tt < b)
        beep[m] = 0.7 * np.sin(2 * np.pi * 1000 * tt[m])
    sig = (noise + beep).astype(np.float32)

    def seg(a, b):
        i0 = int(round((a - T0) * SR)); i1 = int(round((b - T0) * SR))
        return np.stack([sig[i0:i1], sig[i0:i1]], axis=1)

    cam = seg(0.0, DUR)
    wav = seg(ws, we)
    cam_raw = OUT / f"{name}.cam.f32"; cam.tofile(cam_raw)
    wav_path = OUT / f"{name}_backup.wav"
    subprocess.run([FF, "-v", "error", "-y", "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", "-",
                    "-c:a", "pcm_s24le", str(wav_path)], input=wav.astype(np.float32).tobytes(), check=True)
    vf = (f"testsrc2=s=1920x1080:r=30000/1001:d={DUR},eq=brightness=-0.3,"
          f"drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:enable='between(t,1,1.1)+between(t,3,3.1)'")
    subprocess.run([FF, "-v", "error", "-y", "-f", "lavfi", "-i", vf,
                    "-f", "f32le", "-ar", str(SR), "-ac", "2", "-i", str(cam_raw),
                    "-c:v", "libx264", "-preset", "veryfast", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k", "-shortest", str(OUT / f"{name}.mp4")], check=True)
    cam_raw.unlink()
    print(name, ws, we)
