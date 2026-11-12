"""Local stand-in for a YouTube round-trip: ingest like YouTube does (default video +
default audio only), transcode to a typical delivery format (H.264 1080p + AAC 128k,
plus VP9 1080p + Opus), then check the delivered files decode clean, keep their
duration, and (for SYNC masters) keep flash/beep sync.

usage: yt_sim.py MASTER.mov OUTDIR [sync]  -> JSON line
"""
import os
FF = os.environ.get("LV_FFMPEG", "ffmpeg")
FP = os.environ.get("LV_FFPROBE", "ffprobe")
import json, subprocess, sys
from pathlib import Path

TOOLS = Path(__file__).parent


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def dur(p):
    r = run([FP, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(p)])
    return float(r.stdout.strip() or 0)


def default_audio_index(p):
    r = run([FP, "-v", "error", "-select_streams", "a", "-show_entries",
             "stream_disposition=default", "-of", "csv=p=0", str(p)])
    flags = [l.strip() for l in r.stdout.splitlines() if l.strip()]
    return next((i for i, f in enumerate(flags) if f.startswith("1")), 0)


def main(master, outdir, sync=False):
    master, outdir = Path(master), Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    a = default_audio_index(master)
    res = {"master": master.name, "master_dur": round(dur(master), 3), "default_audio": a,
           "faststart": None, "renditions": {}}
    head = master.read_bytes()[:4096]
    res["faststart"] = head.find(b"moov") != -1 and (head.find(b"mdat") == -1 or head.find(b"moov") < head.find(b"mdat"))
    for name, vargs, aargs, ext in (
            ("h264_1080p", ["-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p"],
             ["-c:a", "aac", "-b:a", "128k"], "mp4"),
            ("vp9_1080p", ["-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8", "-b:v", "3M",
                           "-pix_fmt", "yuv420p"], ["-c:a", "libopus", "-b:a", "128k"], "webm")):
        out = outdir / f"{master.stem}.{name}.{ext}"
        r = run([FF, "-v", "error", "-y", "-i", str(master), "-map", "0:v:0", "-map", f"0:a:{a}",
                 "-vf", "scale=-2:1080", *vargs, *aargs, "-ac", "2", str(out)])
        rend = {"encode_ok": r.returncode == 0, "encode_err": r.stderr[-300:]}
        if out.exists():
            d = run([FF, "-v", "error", "-i", str(out), "-f", "null", "-"])
            rend["decode_errors"] = len([l for l in d.stderr.splitlines() if l.strip()])
            rend["duration"] = round(dur(out), 3)
            if sync:
                ev = run([sys.executable, str(TOOLS / "av_events.py"), str(out), "0"])
                try:
                    offs = [p["offset_ms"] for p in json.loads(ev.stdout)["pairs"]]
                    rend["sync_worst_ms"] = max(abs(o) for o in offs if o is not None)
                    rend["sync_events"] = len(offs)
                except Exception as e:
                    rend["sync_error"] = str(e)
        res["renditions"][name] = rend
    print(json.dumps(res))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], len(sys.argv) > 3)
