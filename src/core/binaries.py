"""core/binaries.py — locate bundled ffmpeg/ffprobe and suppress console windows."""

import os
import subprocess
import sys
from pathlib import Path


def no_window() -> dict:
    """Subprocess kwargs that suppress console windows on Windows."""
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def get_app_dir() -> Path:
    """Project root when running from source, or the exe's folder when frozen."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    # src/core/binaries.py → parents[2] is the project root
    return Path(__file__).resolve().parents[2]


def get_ffmpeg() -> tuple:
    """Return (ffmpeg, ffprobe) paths, preferring the bundled bin/ binaries."""
    base   = get_app_dir()
    suffix = ".exe" if sys.platform == "win32" else ""
    ff = base / "bin" / f"ffmpeg{suffix}"
    fp = base / "bin" / f"ffprobe{suffix}"
    if ff.exists():
        if sys.platform != "win32":
            # Best-effort: a zip doesn't always preserve the executable bit, so
            # this is normally needed. But bin/ffmpeg can also be a symlink to
            # a root-owned system binary (a user's local workaround, or a
            # future packaging choice) — chmod on a path you don't own raises
            # PermissionError, and an unhandled exception here crashes the
            # whole app before any window shows, with no clue why. If it's
            # already executable there's nothing to do; if chmod fails
            # anyway, proceed regardless — the exec attempt below is the real
            # test, and fails loudly (unlike this).
            for path in (ff, fp):
                if path.exists() and not os.access(path, os.X_OK):
                    try:
                        path.chmod(0o755)
                    except OSError:
                        pass
        return str(ff), str(fp)
    return "ffmpeg", "ffprobe"


# ── ffmpeg version safety ─────────────────────────────────────────────────────
# From ffmpeg 7.1 on, the mov muxer strips the in-band VPS/SPS/PPS from `hvc1`
# samples, so a lossless (TS-route) join of HEVC segments that carry different
# parameter sets keeps only the FIRST segment's sets and the later segments
# decode as garbage. 7.0.2 and 7.1.1 are verified fine; the 2026 git builds and
# the 9.0.2 release are not. H.264 (avc1) is unaffected on every build. The
# canary is tests/test_ffmpeg_cmd.py::test_ts_remux_and_concat_round_trip_real_
# mixed_encoder_footage. Raise this ceiling only after that test passes on the
# newer build.
MAX_SAFE_FFMPEG = (7, 1, 1)


def parse_ffmpeg_version(version_text: str):
    """Release version as an (major, minor, patch) tuple from the first line of
    `ffmpeg -version`, or None for git/unknown builds ("N-12345-gabcdef")."""
    import re
    m = re.search(r"ffmpeg version n?(\d+)\.(\d+)(?:\.(\d+))?", version_text or "",
                  re.IGNORECASE)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))


def ffmpeg_join_safety(version_text: str):
    """(ok, message). ok is False for any build not known to keep HEVC parameter
    sets intact in mixed-encoder lossless joins."""
    ver = parse_ffmpeg_version(version_text)
    first = (version_text or "").strip().splitlines()[0] if (version_text or "").strip() else "(no output)"
    limit = ".".join(map(str, MAX_SAFE_FFMPEG))
    if ver is None:
        return False, (f"Unrecognised ffmpeg build ({first}). Git builds are not "
                       f"verified; use a release up to {limit}.")
    if ver > MAX_SAFE_FFMPEG:
        return False, (f"ffmpeg {'.'.join(map(str, ver))} can corrupt lossless "
                       f"joins of mixed HEVC clips; use {limit} or older.")
    return True, f"ffmpeg {'.'.join(map(str, ver))} OK"


def probe_ffmpeg_version(ffmpeg_path: str, timeout: float = 10.0) -> str:
    """Raw `ffmpeg -version` output ('' if it can't be run)."""
    try:
        r = subprocess.run([ffmpeg_path, "-version"], capture_output=True, text=True,
                           timeout=timeout, **no_window())
        return r.stdout or ""
    except (OSError, subprocess.SubprocessError):
        return ""
