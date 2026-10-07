"""Measure the TRUE camera-audio vs WAV-backup offset by cross-correlation (GCC-PHAT) on
real clips, and compare with the app's end-alignment assumption (offset = video_dur - wav_dur).

offset convention (same as app): positive = WAV must be DELAYED (starts after video start),
negative = WAV must be TRIMMED at its start (WAV started first)."""
import os
FF = os.environ.get("LV_FFMPEG", "ffmpeg")
FP = os.environ.get("LV_FFPROBE", "ffprobe")
import json, subprocess, sys
from pathlib import Path
import numpy as np

SR = 8000


def dur(p, sel):
    r = subprocess.run([FP, "-v", "error", "-select_streams", sel, "-show_entries",
                        "stream=duration", "-of", "csv=p=0", str(p)], capture_output=True, text=True)
    try:
        return float(r.stdout.split()[0])
    except Exception:
        return None


def pcm(p, t0, t, idx="0:a:0"):
    r = subprocess.run([FF, "-v", "error", "-ss", str(t0), "-t", str(t), "-i", str(p), "-map", idx,
                        "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"], capture_output=True)
    return np.frombuffer(r.stdout, dtype=np.float32)


def gcc(a, b):
    n = len(a) + len(b)
    A = np.fft.rfft(a, n); B = np.fft.rfft(b, n)
    R = A * np.conj(B); R /= np.abs(R) + 1e-12
    cc = np.fft.irfft(R, n)
    k = int(np.argmax(cc))
    if k > n // 2:
        k -= n
    peak = cc.max() / (np.abs(cc).mean() + 1e-12)
    return k / SR, peak


def main(folder):
    out = []
    for v in sorted(Path(folder).glob("VID_*.mp4")):
        w = v.with_name(v.stem + "_backup.wav")
        if not w.exists():
            continue
        vd, wd = dur(v, "v:0"), dur(w, "a:0")
        win = min(20.0, vd)
        cam = pcm(v, 0, win)
        wav = pcm(w, 0, win + 3.0)
        # lag of wav relative to cam: cam(t) == wav(t + L)  ->  L = WAV time of video start
        lag, peak = gcc(wav, cam)
        true_off = -lag
        end_align = vd - wd
        out.append({"clip": v.name, "video_s": round(vd, 3), "wav_s": round(wd, 3),
                    "true_offset_s": round(true_off, 4), "end_align_s": round(end_align, 4),
                    "error_ms": round((end_align - true_off) * 1000, 1), "peak": round(float(peak), 1)})
        print(json.dumps(out[-1]), flush=True)


if __name__ == "__main__":
    main(sys.argv[1])
