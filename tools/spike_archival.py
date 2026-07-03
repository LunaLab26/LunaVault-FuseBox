"""spike_archival.py — THROWAWAY proof-of-concept for the archival master idea.

NOT shipped. Proves, on synthetic clips (no real footage needed), that:
  1. A master can carry a normal baseline video track (track 1, default) PLUS a
     parallel archival video+audio track holding an odd-spec original, stream-copied.
  2. ffprobe sees the extra track and track 1 is the default.
  3. The archived original can be extracted back out via stream copy and is
     CONTENT-lossless (decoded-frame md5s identical), even though the container
     bytes differ.
  4. Two same-spec originals concat-copied onto ONE track can be re-cut back into
     the individual originals at their boundaries, content-lossless — the
     "one track per camera/spec" layout the user chose.

Run:  python tools/spike_archival.py
Outputs go to the scratchpad, not the repo. Findings are printed; copy the verdict
into DEVELOPMENT.md as the Phase-2 decision gate.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from core.binaries import get_ffmpeg  # noqa: E402

FF, FP = get_ffmpeg()

WORK = Path(r"C:\Users\EMMAKY~1\AppData\Local\Temp\claude\G--Claude-cowork"
            r"\98f16b60-7858-41dc-b04d-79e5e4482701\scratchpad\archival_spike")


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        print("  ! ffmpeg failed:", " ".join(str(c) for c in cmd))
        print(r.stderr[-800:])
        raise SystemExit(1)
    return r


def decoded_md5(path, stream):
    """md5 of the DECODED stream (video or audio) — content identity, not file bytes.
    `stream` is like 'v:0' or 'a:0'."""
    r = subprocess.run(
        [FF, "-v", "error", "-i", str(path), "-map", f"0:{stream}",
         "-f", "md5", "-"],
        capture_output=True, text=True)
    return r.stdout.strip()


def probe_streams(path):
    r = subprocess.run(
        [FP, "-v", "error", "-show_entries",
         "stream=index,codec_type,codec_name,width,height:disposition=default",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True)
    return r.stdout.strip()


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    print(f"ffmpeg: {FF}\nwork:   {WORK}\n")

    # ── 1. Synthetic source clips of DIFFERING specs ──────────────────────────
    print("== generating synthetic source clips ==")
    a_4k = WORK / "camA_4k_hevc.mov"        # conforms to the 4K baseline
    b_1080 = WORK / "camB_1080_h264.mp4"    # odd-spec original #1
    b2_1080 = WORK / "camB2_1080_h264.mp4"  # odd-spec original #2 (same spec as b)
    run([FF, "-y", "-f", "lavfi", "-i", "testsrc=size=3840x2160:rate=30:duration=3",
         "-c:v", "libx265", "-pix_fmt", "yuv420p10le", "-tag:v", "hvc1", str(a_4k)])
    for src, label in ((b_1080, "testsrc2"), (b2_1080, "smptebars")):
        run([FF, "-y",
             "-f", "lavfi", "-i", f"{label}=size=1920x1080:rate=30:duration=3",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
             "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(src)])
    print(f"  camA 4K HEVC : {probe_streams(a_4k)}")
    print(f"  camB 1080 H264: {probe_streams(b_1080)}\n")

    # ── 2. Baseline: conform both to a uniform 4K HEVC track, concat-copy ─────
    print("== building baseline track (conform + concat) ==")
    a_conf = WORK / "a_conf.mov"
    b_conf = WORK / "b_conf.mov"
    conform = ["-c:v", "libx265", "-pix_fmt", "yuv420p10le", "-tag:v", "hvc1", "-an"]
    run([FF, "-y", "-i", str(a_4k), *conform, str(a_conf)])
    run([FF, "-y", "-i", str(b_1080), "-vf", "scale=3840:2160", *conform, str(b_conf)])
    concat_list = WORK / "concat.txt"
    concat_list.write_text(f"file '{a_conf.as_posix()}'\nfile '{b_conf.as_posix()}'\n")
    baseline = WORK / "baseline.mov"
    run([FF, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list),
         "-c", "copy", str(baseline)])
    print(f"  baseline: {probe_streams(baseline)}\n")

    # ── 3. Master = baseline track 1 + parallel archival original (stream copy) ─
    print("== muxing archival master (baseline + parallel original track) ==")
    master = WORK / "master_archival.mov"
    run([FF, "-y", "-i", str(baseline), "-i", str(b_1080),
         "-map", "0:v:0", "-map", "1:v:0", "-map", "1:a:0", "-c", "copy",
         "-disposition:v:0", "default", "-disposition:v:1", "0",
         str(master)])
    print(f"  master streams:\n    " + probe_streams(master).replace("\n", "\n    "))

    # ── 4. Extract the archived original back, prove content-lossless ─────────
    print("\n== extracting archived original back (stream copy) ==")
    recovered = WORK / "camB_recovered.mp4"
    run([FF, "-y", "-i", str(master), "-map", "0:v:1", "-map", "0:a:0",
         "-c", "copy", str(recovered)])
    v_src, v_rec = decoded_md5(b_1080, "v:0"), decoded_md5(recovered, "v:0")
    a_src, a_rec = decoded_md5(b_1080, "a:0"), decoded_md5(recovered, "a:0")
    print(f"  video md5 src={v_src}  rec={v_rec}  {'OK' if v_src == v_rec else 'MISMATCH'}")
    print(f"  audio md5 src={a_src}  rec={a_rec}  {'OK' if a_src == a_rec else 'MISMATCH'}")
    lossless_1 = (v_src == v_rec and a_src == a_rec)

    # ── 5. Per-spec concat of two originals, then re-cut clip 2 back out ──────
    print("\n== per-spec concat + boundary re-cut (one track per camera/spec) ==")
    cam_list = WORK / "camB_concat.txt"
    cam_list.write_text(f"file '{b_1080.as_posix()}'\nfile '{b2_1080.as_posix()}'\n")
    cam_track = WORK / "camB_archive.mov"
    run([FF, "-y", "-f", "concat", "-safe", "0", "-i", str(cam_list),
         "-c", "copy", str(cam_track)])
    # clip 2 begins at 3.0s (clip 1 is 3s). Re-cut it back out at that boundary.
    clip2 = WORK / "camB2_recut.mp4"
    run([FF, "-y", "-ss", "3.0", "-i", str(cam_track), "-map", "0:v:0", "-map", "0:a:0",
         "-c", "copy", str(clip2)])
    v2_src, v2_rec = decoded_md5(b2_1080, "v:0"), decoded_md5(clip2, "v:0")
    print(f"  clip2 video md5 src={v2_src}  recut={v2_rec}  "
          f"{'OK' if v2_src == v2_rec else 'MISMATCH'}")
    lossless_2 = (v2_src == v2_rec)

    # ── Verdict ───────────────────────────────────────────────────────────────
    print("\n" + "=" * 66)
    print("VERDICT")
    print(f"  parallel archival track round-trip content-lossless : {lossless_1}")
    print(f"  per-spec concat + boundary re-cut content-lossless   : {lossless_2}")
    print(f"  master carries 2 video tracks, track1 default        : see probe above")
    print("=" * 66)


if __name__ == "__main__":
    main()
