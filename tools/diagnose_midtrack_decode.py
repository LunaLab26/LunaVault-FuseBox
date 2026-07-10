"""tools/diagnose_midtrack_decode.py — pin down WHY mid-track clips fail video
verification against a concatenated master (Task 86).

For each clip in a master's manifest, decodes per-frame hashes (framemd5) of:
  • the original clip's head (first --head seconds), and
  • the master, from --lead seconds BEFORE the clip's modelled start,

then classifies the relationship (core.seam_diag): an exact match at a shifted
offset = window rounding; a damaged head with an intact tail = concat-seam
decode damage; no alignment = genuine divergence.

Usage:
    python tools/diagnose_midtrack_decode.py "path/to/master.mov"
        [--source-dir DIR]   originals' folder (default: the master's folder)
        [--head SECONDS]     original head length to compare   (default 12)
        [--lead SECONDS]     widened lead-in before each start  (default 4)

Read-only: decodes to hashes on stdout, writes nothing next to the master.
"""

import argparse
import subprocess
import sys
from pathlib import Path

# Windows consoles default to cp1252, which can't print the report's ≈/·
# characters — never let an encoding kill a diagnostic run.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from core import seam_diag                                    # noqa: E402
from core.binaries import get_ffmpeg, no_window               # noqa: E402
from core.extract import compute_baseline_offsets             # noqa: E402
from core import manifest as manifest_mod                     # noqa: E402


def framemd5(ff: str, path: str, seek: float, duration: float, stream: int = 0) -> list:
    cmd = [ff, "-v", "error"]
    if seek > 0:
        cmd += ["-ss", f"{seek:.3f}"]
    cmd += ["-t", f"{duration:.3f}", "-i", str(path),
            "-map", f"0:v:{stream}", "-f", "framemd5", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800, **no_window())
    if r.returncode != 0:
        raise RuntimeError(f"framemd5 failed on {path}: {(r.stderr or '')[-300:]}")
    return seam_diag.parse_framemd5(r.stdout)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("master")
    ap.add_argument("--source-dir", default=None)
    ap.add_argument("--head", type=float, default=12.0)
    ap.add_argument("--lead", type=float, default=4.0)
    ap.add_argument("--tail", type=float, default=4.0,
                    help="also check each clip's LAST N seconds (0 disables) — a head that "
                         "matches while verification fails points at the window's end")
    ap.add_argument("--only", default=None,
                    help="comma-separated filename substrings; skip clips matching none")
    args = ap.parse_args()
    only = [s.strip().lower() for s in args.only.split(",")] if args.only else None

    master = Path(args.master)
    source_dir = Path(args.source_dir) if args.source_dir else master.parent
    man_path = master.with_suffix("").parent / f"{master.stem}.manifest.json"
    if not man_path.exists():
        sidecars = list(master.parent.glob(f"{master.stem}*.manifest.json"))
        if not sidecars:
            sys.exit(f"no manifest found for {master.name}")
        man_path = sidecars[0]
    man = manifest_mod.from_json(man_path.read_text(encoding="utf-8"))
    offsets = compute_baseline_offsets(man)
    ff, _fp = get_ffmpeg()

    print(f"Seam diagnostic — {master.name}")
    print(f"original head compared: {args.head:.0f}s · master lead-in: {args.lead:.0f}s\n")

    for entry in man.clips:
        stem = Path(entry.source_filename).stem
        src = source_dir / entry.source_filename
        if only and not any(s in entry.source_filename.lower() for s in only):
            continue
        if entry.baseline_chapter_index not in offsets:
            print(f"{stem}: skipped (no baseline chapter)")
            continue
        if not src.exists():
            print(f"{stem}: skipped (original not found at {src})")
            continue
        start, dur = offsets[entry.baseline_chapter_index]
        # fps for frame<->ms conversion + expected offset in frames
        try:
            num, den = (entry.fps.split("/") + ["1"])[:2]
            fps = float(num) / float(den or 1)
        except (ValueError, ZeroDivisionError):
            fps = 29.97
        lead = min(args.lead, start)               # first clip: no room for a lead-in
        expected = int(round(lead * fps))
        try:
            orig = framemd5(ff, str(src), 0.0, args.head)
            mast = framemd5(ff, str(master), start - lead, lead + args.head + 2.0,
                            stream=0)
        except (RuntimeError, subprocess.TimeoutExpired) as e:
            print(f"{stem}: decode error — {e}")
            continue
        v = seam_diag.classify_window(orig, mast, expected)
        print(seam_diag.describe(stem + " [head]", v, fps=fps))

        # Tail check: decode the original's LAST --tail seconds and the master
        # around this clip's modelled END — the decisive test when a head
        # matches but full-window verification still fails (window-length /
        # end-boundary drift shows up here as a shifted or foreign tail).
        if args.tail > 0 and dur > args.tail + 1:
            t_lead = 2.0
            t_start = max(0.0, start + dur - args.tail - t_lead)
            t_expected = int(round((start + dur - args.tail - t_start) * fps))
            try:
                t_orig = framemd5(ff, str(src), max(0.0, dur - args.tail), args.tail + 1.0)
                t_mast = framemd5(ff, str(master), t_start, args.tail + t_lead + 2.0)
            except (RuntimeError, subprocess.TimeoutExpired) as e:
                print(f"{stem} [tail]: decode error — {e}")
                print()
                continue
            tv = seam_diag.classify_window(t_orig, t_mast, t_expected)
            print(seam_diag.describe(stem + " [tail]", tv, fps=fps))
        print()


if __name__ == "__main__":
    main()
