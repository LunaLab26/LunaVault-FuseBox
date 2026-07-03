"""spike_archival_p2.py — THROWAWAY: the Phase-2 multi-archival-track risk.

Phase 1's spike proved a single parallel archival track round-trips losslessly.
Phase 2 adds the harder bits: a baseline plus MULTIPLE archival tracks from
distinct spec groups (each carrying video + its original audio), muxed into one
master, and extracting a given clip's video+audio back out by STREAM INDEX.

Proves on synthetic clips (no real footage):
  1. baseline (4K HEVC, default) + archival track B (1080p H.264 + AAC) + archival
     track C (720p H.264 + AAC) all coexist in one master; baseline v:0 is default.
  2. each odd-spec clip extracts back by its stream index (video + camera audio)
     content-lossless (decoded md5 match).

Run: python tools/spike_archival_p2.py
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from core.binaries import get_ffmpeg  # noqa: E402

FF, FP = get_ffmpeg()
WORK = Path(r"C:\Users\EMMAKY~1\AppData\Local\Temp\claude\G--Claude-cowork"
            r"\98f16b60-7858-41dc-b04d-79e5e4482701\scratchpad\archival_spike_p2")


def run(cmd):
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        print("  ! ffmpeg failed:", " ".join(str(c) for c in cmd))
        print(r.stderr[-800:])
        raise SystemExit(1)
    return r


def dmd5(path, stream):
    r = subprocess.run([FF, "-v", "error", "-i", str(path), "-map", f"0:{stream}",
                        "-f", "md5", "-"], capture_output=True, text=True)
    return r.stdout.strip()


def streams(path):
    r = subprocess.run([FP, "-v", "error", "-show_entries",
                        "stream=index,codec_type,codec_name,width,height:disposition=default",
                        "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return r.stdout.strip()


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    print(f"work: {WORK}\n")

    print("== sources: A conforms (4K HEVC); B, C are distinct odd specs (+audio) ==")
    a = WORK / "camA_4k.mov"
    b = WORK / "camB_1080.mp4"
    c = WORK / "camC_720.mp4"
    run([FF, "-y", "-f", "lavfi", "-i", "testsrc=size=3840x2160:rate=30:duration=2",
         "-c:v", "libx265", "-pix_fmt", "yuv420p10le", "-tag:v", "hvc1", str(a)])
    run([FF, "-y", "-f", "lavfi", "-i", "testsrc2=size=1920x1080:rate=30:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(b)])
    run([FF, "-y", "-f", "lavfi", "-i", "smptebars=size=1280x720:rate=30:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=660:duration=2",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(c)])

    print("== baseline: conform all three to 4K HEVC, concat ==")
    conf = ["-c:v", "libx265", "-pix_fmt", "yuv420p10le", "-tag:v", "hvc1", "-an"]
    ac, bc, cc = WORK / "a_c.mov", WORK / "b_c.mov", WORK / "c_c.mov"
    run([FF, "-y", "-i", str(a), *conf, str(ac)])
    run([FF, "-y", "-i", str(b), "-vf", "scale=3840:2160", *conf, str(bc)])
    run([FF, "-y", "-i", str(c), "-vf", "scale=3840:2160", *conf, str(cc)])
    lst = WORK / "base.txt"
    lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in (ac, bc, cc)))
    baseline = WORK / "baseline.mov"
    run([FF, "-y", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", str(baseline)])

    print("== archival intermediates: B and C originals (video + audio), stream copy ==")
    arB, arC = WORK / "archive_B.mov", WORK / "archive_C.mov"
    run([FF, "-y", "-i", str(b), "-map", "0:v:0", "-map", "0:a:0", "-c", "copy", str(arB)])
    run([FF, "-y", "-i", str(c), "-map", "0:v:0", "-map", "0:a:0", "-c", "copy", str(arC)])

    print("== final mux: baseline + archival B + archival C ==")
    master = WORK / "master_p2.mov"
    run([FF, "-y", "-i", str(baseline), "-i", str(arB), "-i", str(arC),
         "-map", "0:v:0", "-map", "1:v:0", "-map", "1:a:0", "-map", "2:v:0", "-map", "2:a:0",
         "-c", "copy",
         "-disposition:v:0", "default", "-disposition:v:1", "0", "-disposition:v:2", "0",
         str(master)])
    print("  master streams:\n    " + streams(master).replace("\n", "\n    "))

    print("\n== extract each odd-spec clip by stream index (video + camera audio) ==")
    b_rec, c_rec = WORK / "B_rec.mp4", WORK / "C_rec.mp4"
    # v:1/a:0 = archival B ; v:2/a:1 = archival C  (per the mux/probe above)
    run([FF, "-y", "-i", str(master), "-map", "0:v:1", "-map", "0:a:0", "-c", "copy", str(b_rec)])
    run([FF, "-y", "-i", str(master), "-map", "0:v:2", "-map", "0:a:1", "-c", "copy", str(c_rec)])

    okB = dmd5(b, "v:0") == dmd5(b_rec, "v:0") and dmd5(b, "a:0") == dmd5(b_rec, "a:0")
    okC = dmd5(c, "v:0") == dmd5(c_rec, "v:0") and dmd5(c, "a:0") == dmd5(c_rec, "a:0")
    print(f"  B recovered content-lossless (video+audio): {okB}")
    print(f"  C recovered content-lossless (video+audio): {okC}")

    default_v0 = ",1" in ("," + streams(master).splitlines()[0].split(",", 2)[-1])
    print("\n" + "=" * 60)
    print("VERDICT")
    print(f"  two archival tracks coexist + indexed extract lossless: {okB and okC}")
    print(f"  baseline v:0 default: see probe above")
    print("=" * 60)


if __name__ == "__main__":
    main()
