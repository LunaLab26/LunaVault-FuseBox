"""Find flash (video) and beep (audio) onsets in a media file and report A/V offsets.

usage: av_events.py FILE [audio_stream_index]   -> prints JSON
"""
import os
FF = os.environ.get("LV_FFMPEG", "ffmpeg")
FP = os.environ.get("LV_FFPROBE", "ffprobe")
import json, subprocess, sys
import numpy as np



def video_onsets(path):
    r = subprocess.run([FF, "-v", "error", "-i", path, "-map", "0:v:0", "-an",
                        "-vf", "scale=32:18,format=yuv420p,signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=-",
                        "-f", "null", "-"], capture_output=True, text=True)
    t, out, last_bright = None, [], -10.0
    for line in r.stdout.splitlines():
        if line.startswith("frame:"):
            t = float(line.split("pts_time:")[1])
        elif "YAVG=" in line and t is not None:
            y = float(line.split("=")[1])
            if y > 180:
                if t - last_bright > 0.5:
                    out.append(round(t, 4))
                last_bright = t
    return out


def audio_onsets(path, aidx=0, sr=48000):
    r = subprocess.run([FF, "-v", "error", "-i", path, "-map", f"0:a:{aidx}", "-ac", "1",
                        "-ar", str(sr), "-f", "f32le", "-"], capture_output=True)
    a = np.frombuffer(r.stdout, dtype=np.float32)
    if a.size == 0:
        return []
    win = int(sr * 0.002)
    env = np.convolve(np.abs(a), np.ones(win) / win, mode="same")
    hot = env > 0.15
    out, last = [], -10.0
    idx = np.flatnonzero(hot)
    for i in idx:
        t = i / sr
        if t - last > 0.5:
            out.append(round(t, 4))
        last = t
    return out


def main():
    path = sys.argv[1]
    aidx = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    v = video_onsets(path)
    a = audio_onsets(path, aidx)
    pairs = []
    for tv in v:
        near = [ta for ta in a if abs(ta - tv) < 0.5]
        pairs.append({"video": tv, "audio": near[0] if near else None,
                      "offset_ms": round((near[0] - tv) * 1000, 1) if near else None})
    print(json.dumps({"file": path, "video_events": len(v), "audio_events": len(a),
                      "pairs": pairs}))


if __name__ == "__main__":
    main()
