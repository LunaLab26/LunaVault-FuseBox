"""tools/check_ffmpeg.py — refuse to build with an ffmpeg that corrupts HEVC joins.

Usage: python tools/check_ffmpeg.py bin/ffmpeg[.exe]
Exit 0 if the binary runs and is a known-safe version, 1 otherwise.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from core.binaries import ffmpeg_join_safety, probe_ffmpeg_version  # noqa: E402


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    out = probe_ffmpeg_version(sys.argv[1])
    if not out:
        print(f"  ERROR: {sys.argv[1]} did not run (corrupt or truncated download?)")
        return 1
    ok, msg = ffmpeg_join_safety(out)
    print(f"  {'OK:' if ok else 'ERROR:'} {msg}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
