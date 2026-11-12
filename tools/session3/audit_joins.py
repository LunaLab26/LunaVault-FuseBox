"""Per-join A/V audit for a merged master, works on REAL footage.

For each clip in the manifest, locate that clip's own camera audio inside the master's
audio tracks by GCC-PHAT and report the offset from where the VIDEO says the clip starts
(manifest concat_start). 0 ms = audio sits exactly under its picture.

usage: audit_joins.py MASTER.mov SOURCE_DIR   -> JSON on stdout
"""
import os
FF = os.environ.get("LV_FFMPEG", "ffmpeg")
FP = os.environ.get("LV_FFPROBE", "ffprobe")
import json, subprocess, sys
from pathlib import Path
import numpy as np

SR = 8000


def pcm(path, t0, dur, amap):
    r = subprocess.run([FF, "-v", "error", "-ss", f"{max(0, t0):.4f}", "-t", f"{dur:.4f}", "-i", str(path),
                        "-map", amap, "-ac", "1", "-ar", str(SR), "-f", "f32le", "-"], capture_output=True)
    return np.frombuffer(r.stdout, dtype=np.float32)


def gcc(sig, ref, max_tau):
    n = len(sig) + len(ref)
    S = np.fft.rfft(sig, n); R = np.fft.rfft(ref, n)
    X = S * np.conj(R); X /= np.abs(X) + 1e-12
    cc = np.fft.irfft(X, n)
    m = int(max_tau * SR)
    cc = np.concatenate((cc[-m:], cc[:m + 1]))
    k = int(np.argmax(np.abs(cc)))
    pk = float(np.abs(cc[k]) / (np.abs(cc).mean() + 1e-12))
    return (k - m) / SR, pk


def main(master, src_dir):
    master = Path(master); src_dir = Path(src_dir)
    man = json.loads(Path(str(master).replace(".mov", ".manifest.json")).read_text())
    tracks = man.get("baseline_audio_tracks", {})
    out = []
    r = subprocess.run([FP, "-v", "error", "-select_streams", "v:0", "-show_entries",
                        "stream=start_time", "-of", "csv=p=0", str(master)], capture_output=True, text=True)
    try:
        v_start = float(r.stdout.strip().split(",")[0])
    except Exception:
        v_start = 0.0
    full = {}
    for kind, idx in tracks.items():
        r = subprocess.run([FF, "-v", "error", "-i", str(master), "-map", f"0:a:{idx}", "-ac", "1",
                            "-ar", str(SR), "-f", "f32le", "-"], capture_output=True)
        full[kind] = np.frombuffer(r.stdout, dtype=np.float32)
    for c in man["clips"]:
        start = c.get("concat_start")
        if start is not None:
            start += v_start
        src = src_dir / c["source_filename"]
        dur = float(c.get("duration") or 0)
        if start is None or not src.exists() or dur < 1.2:
            out.append({"clip": c["source_filename"], "skip": True}); continue
        a0 = min(0.4, dur * 0.2)
        L = min(3.0, dur - a0 - 0.1)
        r = subprocess.run([FF, "-v", "error", "-i", str(src), "-map", "0:a:0?", "-ac", "1",
                            "-ar", str(SR), "-f", "f32le", "-"], capture_output=True)
        ref = np.frombuffer(r.stdout, dtype=np.float32)[int(a0 * SR):int((a0 + L) * SR)]
        row = {"clip": c["source_filename"], "concat_start": round(start, 4), "dur": dur}
        if ref.size < SR * 0.5:
            row["note"] = "source has no audio"; out.append(row); continue
        for kind, idx in tracks.items():
            pad = 0.6
            t0 = max(0.0, start + a0 - pad)
            i0 = int(round(t0 * SR)); i1 = int(round((start + a0 + L + pad) * SR))
            sig = full[kind][i0:i1]
            if sig.size < ref.size:
                row[kind] = None; continue
            expect = start + a0 - t0            # where ref should sit inside sig
            lag, pk = gcc(sig, ref, max_tau=2 * pad)
            row[kind] = {"offset_ms": round((lag - expect) * 1000, 1), "peak": round(pk, 1)}
        out.append(row)
    print(json.dumps({"master": str(master), "joins": out}))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
