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

# (bit_depth, subsampling label) for pix_fmts the Review tab's scopes panel
# badges know how to describe precisely; anything else falls back to sniffing
# a bit-depth digit off the format name.
_PIX_FMT_INFO = {
    "yuv420p":     (8, "4:2:0"),
    "yuvj420p":    (8, "4:2:0"),
    "yuv422p":     (8, "4:2:2"),
    "yuv444p":     (8, "4:4:4"),
    "yuv420p10le": (10, "4:2:0"),
    "yuv422p10le": (10, "4:2:2"),
    "yuv444p10le": (10, "4:4:4"),
    "yuv420p12le": (12, "4:2:0"),
    "p010le":      (10, "4:2:0"),
    "nv12":        (8, "4:2:0"),
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
        "-show_chapters",
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


def pix_fmt_info(pix_fmt: str) -> tuple:
    """(bit_depth, subsampling_label) for a pix_fmt, for the Review tab's
    colour/dynamic-range badges. Falls back to sniffing a bit-depth digit off
    the format name for anything not in the known-format table."""
    if pix_fmt in _PIX_FMT_INFO:
        return _PIX_FMT_INFO[pix_fmt]
    import re
    m = re.search(r"(\d+)(le|be)?$", pix_fmt or "")
    depth = int(m.group(1)) if m and int(m.group(1)) in (8, 9, 10, 12, 14, 16) else 8
    return depth, (pix_fmt or "?")


# ── Multi-track audio (Review tab) ─────────────────────────────────────────────
# `probe()` above keeps only the first audio stream (merge/WhatsApp tabs only
# ever act on one at a time); the Review tab needs every audio track a master
# carries. The v1.4 playback spike found Qt exposes no usable per-track
# metadata (title/language all empty), so these labels come from ffprobe.

@dataclass
class AudioTrackInfo:
    """One audio stream's identity. `audio_index` is 0-based among AUDIO
    streams only (matches ffmpeg's `-map 0:a:N`)."""
    audio_index: int
    codec: str = ""
    channels: int = 0
    sample_rate: int = 0
    bit_depth: int = 0
    title: str = ""
    language: str = ""


def parse_audio_tracks(raw: dict) -> list:
    """Pure: turn an ffprobe -show_streams JSON dict into an AudioTrackInfo list."""
    out = []
    audio_i = 0
    for s in raw.get("streams", []):
        if s.get("codec_type") != "audio":
            continue
        tags = s.get("tags", {}) or {}
        try:
            bit_depth = int(s.get("bits_per_raw_sample") or 0)
        except (TypeError, ValueError):
            bit_depth = 0
        out.append(AudioTrackInfo(
            audio_index=audio_i,
            codec=s.get("codec_name", "") or "",
            channels=int(s.get("channels", 0) or 0),
            sample_rate=int(s.get("sample_rate", 0) or 0),
            bit_depth=bit_depth,
            title=tags.get("title", "") or tags.get("handler_name", "") or "",
            language=tags.get("language", "") or "",
        ))
        audio_i += 1
    return out


def probe_audio_tracks(ffprobe_bin: str, path: str) -> list:
    try:
        raw = _run_ffprobe(ffprobe_bin, path)
    except Exception:
        return []
    return parse_audio_tracks(raw)


# ── Chapters (Review tab prev/next transport) ──────────────────────────────────
# Masters carry per-clip chapters written at merge time (ffmpeg_runner.py's
# FFMETADATA chapters file) — the Review tab's prev/next buttons jump to these
# instead of arbitrary time skips.

@dataclass
class ChapterInfo:
    start: float = 0.0
    end: float = 0.0
    title: str = ""


def parse_chapters(raw: dict) -> list:
    """Pure: turn an ffprobe -show_chapters JSON dict into a ChapterInfo list."""
    out = []
    for c in raw.get("chapters", []):
        try:
            start = float(c.get("start_time", 0) or 0)
            end = float(c.get("end_time", 0) or 0)
        except (TypeError, ValueError):
            start, end = 0.0, 0.0
        title = (c.get("tags", {}) or {}).get("title", "") or ""
        out.append(ChapterInfo(start=start, end=end, title=title))
    return out


def probe_chapters(ffprobe_bin: str, path: str) -> list:
    try:
        raw = _run_ffprobe(ffprobe_bin, path)
    except Exception:
        return []
    return parse_chapters(raw)
