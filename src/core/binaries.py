"""core/binaries.py — locate bundled ffmpeg/ffprobe and suppress console windows."""

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
            ff.chmod(0o755)
            fp.chmod(0o755)
        return str(ff), str(fp)
    return "ffmpeg", "ffprobe"
