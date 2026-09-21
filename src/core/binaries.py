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
