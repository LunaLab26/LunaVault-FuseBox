"""probe.py — ffprobe wrapper, stream inspection, Luna Ultra conformance checking."""

import json
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Optional


def _no_window() -> dict:
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


# ── Luna Ultra target standard ────────────────────────────────────────────────
TARGET = {
    "codec":       "hevc",
    "width":       3840,
    "height":      2160,
    "fps":         "30000/1001",
    "pix_fmt":     "yuv420p10le",
    "color_space": "bt709",
}
TARGET_FPS_FLOAT = 30000 / 1001   # ≈ 29.97

HDR_TRANSFERS = {"smpte2084", "arib-std-b67", "smpte428"}   # PQ / HLG / DCI

# Human-readable labels for pixel format conflicts
PIX_FMT_LABELS = {
    "yuvj420p":  "8-bit (full range)",
    "yuv420p":   "8-bit",
    "yuv422p":   "8-bit 4:2:2",
    "yuv444p":   "8-bit 4:4:4",
    "yuv420p10": "10-bit (wrong range)",
    "nv12":      "8-bit NV12",
    "p010le":    "10-bit P010",
}


@dataclass
class StreamInfo:
    """All probed metadata for one video file."""
    path: str = ""
    duration: float = 0.0
    codec: str = ""
    width: int = 0
    height: int = 0
    fps_str: str = ""
    fps_float: float = 0.0
    pix_fmt: str = ""
    color_space: str = ""
    color_transfer: str = ""
    audio_codec: str = ""
    audio_sample_rate: int = 0
    audio_channels: int = 0
    audio_bit_rate: int = 0
    conflicts: list = field(default_factory=list)
    is_hdr: bool = False
    status: str = "unknown"    # "ok" | "transcode" | "hdr" | "error"
    error: str = ""


def _run_ffprobe(ffprobe_bin: str, path: str) -> dict:
    cmd = [
        ffprobe_bin,
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise RuntimeError(f"ffprobe failed: {r.stderr.strip()}")
    return json.loads(r.stdout)


def _rational_to_float(s: str) -> float:
    if "/" in s:
        num, den = s.split("/", 1)
        return float(num) / float(den) if float(den) else 0.0
    return float(s)


def probe(ffprobe_bin: str, path: str) -> StreamInfo:
    info = StreamInfo(path=str(path))
    try:
        raw = _run_ffprobe(ffprobe_bin, path)
    except Exception as e:
        info.status = "error"
        info.error = str(e)
        info.conflicts = [str(e)[:120]]   # surface the error in the status badge
        return info

    fmt = raw.get("format", {})
    try:
        info.duration = float(fmt.get("duration", 0) or 0)
    except ValueError:
        info.duration = 0.0

    streams = raw.get("streams", [])
    for s in streams:
        ctype = s.get("codec_type", "")
        if ctype == "video" and not info.codec:
            info.codec         = s.get("codec_name", "")
            info.width         = s.get("width", 0)
            info.height        = s.get("height", 0)
            info.pix_fmt       = s.get("pix_fmt", "")
            info.color_space   = s.get("color_space", "")
            info.color_transfer = s.get("color_transfer", "")
            fps_raw = s.get("r_frame_rate") or s.get("avg_frame_rate", "0/1")
            try:
                info.fps_float = _rational_to_float(fps_raw)
                info.fps_str = "30000/1001" if abs(info.fps_float - TARGET_FPS_FLOAT) < 0.01 else fps_raw
            except Exception:
                info.fps_str = fps_raw

        elif ctype == "audio" and not info.audio_codec:
            info.audio_codec       = s.get("codec_name", "")
            try:
                info.audio_sample_rate = int(s.get("sample_rate", 0) or 0)
                info.audio_channels    = int(s.get("channels", 0) or 0)
                info.audio_bit_rate    = int(s.get("bit_rate", 0) or 0)
            except (ValueError, TypeError):
                pass

    # ── Conformance check ─────────────────────────────────────────────────────
    conflicts = []

    if info.color_transfer and info.color_transfer.lower() in HDR_TRANSFERS:
        info.is_hdr = True
        info.status = "hdr"
        info.conflicts = [f"HDR ({info.color_transfer})"]
        return info

    if info.codec.lower() not in ("hevc", "h265"):
        conflicts.append(info.codec or "unknown-codec")

    if info.width != TARGET["width"] or info.height != TARGET["height"]:
        conflicts.append(f"{info.width}×{info.height}")

    if info.fps_str != TARGET["fps"]:
        if info.fps_float > 0:
            # Show as clean integer if close to a whole number
            fps_display = f"{info.fps_float:.0f}fps" if abs(info.fps_float - round(info.fps_float)) < 0.01 else f"{info.fps_float:.2f}fps"
            conflicts.append(fps_display)
        else:
            conflicts.append("unknown-fps")

    if info.pix_fmt != TARGET["pix_fmt"]:
        # Use friendly label if known, otherwise show raw name
        label = PIX_FMT_LABELS.get(info.pix_fmt, info.pix_fmt or "unknown-fmt")
        conflicts.append(label)

    if info.color_space and info.color_space.lower() not in ("bt709", "unknown", ""):
        conflicts.append(info.color_space)

    info.conflicts = conflicts
    info.status = "ok" if not conflicts else "transcode"
    return info


def probe_duration(ffprobe_bin: str, path: str) -> float:
    try:
        raw = _run_ffprobe(ffprobe_bin, path)
        return float(raw.get("format", {}).get("duration", 0) or 0)
    except Exception:
        return 0.0
