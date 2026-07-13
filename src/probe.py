"""probe.py — ffprobe wrapper, stream inspection, Luna Ultra conformance checking."""

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Optional


def _no_window() -> dict:
    if sys.platform == "win32":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


# Format-level provenance tags worth capturing/restoring when present — GPS/
# location and device identity, however the camera happened to spell them.
# Deliberately raw key names (not derived/renamed) so restoring them later is
# a verbatim -metadata replay, not a guess at which convention (com.android.*
# vs com.apple.quicktime.* vs plain) a given camera used — adaptive to
# whatever tags actually show up, not a hardcoded set of expected cameras.
# Shared between probe.py (capture) and core/verify.py (comparison) so they
# never drift apart.
KEY_METADATA_TAGS = (
    "location", "location-eng", "com.apple.quicktime.location.ISO6709",
    "creation_time", "com.apple.quicktime.creationdate",
    "com.android.capture.fps", "com.android.manufacturer", "com.android.model",
    "make", "model", "com.apple.quicktime.make", "com.apple.quicktime.model",
)


# ── Baseline spec (the conform target) ─────────────────────────────────────────
# Historically hardcoded to the Luna Ultra 4K/HEVC/10-bit standard; now the
# DEFAULT, overridable by the user's chosen baseline (see BaselineSpec below).
TARGET = {
    "codec":       "hevc",
    "width":       3840,
    "height":      2160,
    "fps":         "30000/1001",
    "pix_fmt":     "yuv420p10le",
    "color_space": "bt709",
}
TARGET_FPS_FLOAT = 30000 / 1001   # ≈ 29.97


@dataclass
class BaselineSpec:
    """The spec every clip conforms to (or stream-copies if it already matches).
    Defaults to the app's original 4K/HEVC/10-bit target; the merge overrides it
    with the user's chosen baseline and re-runs `apply_conformance`."""
    codec: str = "hevc"
    width: int = 3840
    height: int = 2160
    fps_float: float = TARGET_FPS_FLOAT
    pix_fmt: str = "yuv420p10le"
    color_space: str = "bt709"


DEFAULT_BASELINE = BaselineSpec()

_CODEC_ALIASES = {"hevc": {"hevc", "h265"}, "h265": {"hevc", "h265"},
                  "h264": {"h264", "avc"}, "avc": {"h264", "avc"}}


def _codec_matches(clip_codec: str, target_codec: str) -> bool:
    c, t = (clip_codec or "").lower(), (target_codec or "").lower()
    return c == t or c in _CODEC_ALIASES.get(t, {t})

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
    color_primaries: str = ""
    audio_codec: str = ""
    audio_sample_rate: int = 0
    audio_channels: int = 0
    audio_bit_rate: int = 0
    conflicts: list = field(default_factory=list)
    is_hdr: bool = False
    status: str = "unknown"    # "ok" | "transcode" | "hdr" | "error"
    error: str = ""
    # Multicam-overhaul fields
    creation_time: str = ""    # ISO-8601 UTC from container metadata (reliable cross-camera order)
    rotation: int = 0          # display rotation in degrees, normalised 0..359
    device: str = ""           # best-effort device name from metadata (make/model or handler)
    is_vfr: bool = False       # variable frame rate (r_frame_rate != avg_frame_rate)
    format_tags: dict = field(default_factory=dict)   # raw KEY_METADATA_TAGS present on this
                                                        # file (GPS/location, device make/model,
                                                        # capture fps) — restored verbatim on
                                                        # recovery by core.extract.recover_metadata_args


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
    try:
        if "/" in s:
            num, den = s.split("/", 1)
            return float(num) / float(den) if float(den) else 0.0
        return float(s)
    except (TypeError, ValueError, AttributeError):
        return 0.0


# Standard frame rates a normalised fps snaps to (so 29.997 → "30", not an ugly
# rational, and clips at ~30 group together in spec_signature).
_COMMON_FPS = [23.976, 24, 25, 29.97, 30, 48, 50, 59.94, 60, 100, 120, 240]


def _normalize_fps(f: float) -> str:
    for c in _COMMON_FPS:
        if abs(f - c) < 0.02:
            return f"{c:g}"
    return f"{round(f, 2):g}" if f > 0 else "0"


def _nominal_fps(cap_fps: float, r_float: float, avg_float: float, is_vfr: bool) -> float:
    """Pick a STABLE nominal frame rate for a clip.

    `avg_frame_rate` is total_frames/duration — the true average, but it drifts a
    few hundredths per clip (the same 29.97 camera can report 29.92, 29.95, 29.97
    across clips), which wrongly splits identical-spec clips into different
    baselines and forces needless transcodes. `r_frame_rate` is the clean nominal
    (e.g. 30000/1001) and is byte-identical across clips from one camera — but it
    grossly misreads VFR (a Pixel 30fps VFR clip reports r=120). So:
      • a camera-declared capture fps wins when present (most authoritative);
      • otherwise use r_frame_rate for CFR clips (stable nominal);
      • fall back to avg only for genuinely-VFR clips, whose r is the misread one.
    """
    if cap_fps > 0:
        return cap_fps
    if not is_vfr and r_float > 0:
        return r_float
    if avg_float > 0:
        return avg_float
    return r_float


def _extract_rotation(stream: dict) -> int:
    """Display rotation in degrees, normalised to 0..359. Reads the modern
    Display-Matrix side data first, then the legacy `rotate` tag."""
    for sd in stream.get("side_data_list", []) or []:
        if "rotation" in sd:
            try:
                return int(round(float(sd["rotation"]))) % 360
            except (TypeError, ValueError):
                pass
    rot = (stream.get("tags", {}) or {}).get("rotate")
    try:
        return int(round(float(rot))) % 360 if rot is not None else 0
    except (TypeError, ValueError):
        return 0


_GENERIC_HANDLERS = {"videohandler", "videohandle", "core media video",
                     "gpac iso video handler", "soundhandler", "soundhandle", ""}


def _extract_device(fmt_tags: dict, video_tags: dict) -> str:
    """Best-effort camera/device name from container metadata: an explicit
    make/model (phones) first, else a meaningful handler_name (e.g. Insta360's
    'Ambarella AVC' → 'Ambarella'), else '' (generic → caller falls back)."""
    for model_key, make_key in (("com.android.model", "com.android.manufacturer"),
                                ("com.apple.quicktime.model", "com.apple.quicktime.make"),
                                ("model", "make")):
        model = fmt_tags.get(model_key)
        if model:
            make = fmt_tags.get(make_key) or ""
            if make and make.lower() not in model.lower():
                return f"{make} {model}".strip()
            return model
    # Handler strings can carry a leading length-byte / control char and a codec
    # suffix — e.g. "\x10INS.AVC" (Insta360 X4) or "Ambarella AVC" (Go3s).
    handler = "".join(ch for ch in (video_tags.get("handler_name", "") or "") if ch.isprintable()).strip()
    if handler.lower() not in _GENERIC_HANDLERS:
        tok = handler.split()[0]                                   # "Ambarella AVC" → "Ambarella"
        tok = re.sub(r"\.(avc|aac|hevc|hvc1|h264|h265)$", "", tok, flags=re.I)  # "INS.AVC" → "INS"
        return tok
    return ""


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
    video_stream = None
    for s in streams:
        ctype = s.get("codec_type", "")
        if ctype == "video" and not info.codec:
            video_stream = s
            info.codec         = s.get("codec_name", "")
            info.width         = s.get("width", 0)
            info.height        = s.get("height", 0)
            info.pix_fmt       = s.get("pix_fmt", "")
            info.color_space   = s.get("color_space", "")
            info.color_transfer = s.get("color_transfer", "")
            info.color_primaries = s.get("color_primaries", "")
            # r_frame_rate is the clean, stable nominal rate; avg_frame_rate is the
            # measured average and drifts slightly per clip. Detect VFR from a large
            # r-vs-avg gap, then pick the nominal rate (see _nominal_fps) — using r
            # for CFR keeps same-camera clips on one baseline instead of splitting
            # them by hundredths-of-an-fps measurement noise.
            r_float = _rational_to_float(s.get("r_frame_rate", "0/1"))
            avg_float = _rational_to_float(s.get("avg_frame_rate", "0/1"))
            cap_fps = _rational_to_float((s.get("tags", {}) or {}).get("com.android.capture.fps", "0"))
            info.is_vfr = bool(r_float > 0 and avg_float > 0 and abs(r_float - avg_float) > 0.5)
            info.fps_float = _nominal_fps(cap_fps, r_float, avg_float, info.is_vfr)
            info.fps_str = _normalize_fps(info.fps_float)

        elif ctype == "audio" and not info.audio_codec:
            info.audio_codec       = s.get("codec_name", "")
            try:
                info.audio_sample_rate = int(s.get("sample_rate", 0) or 0)
                info.audio_channels    = int(s.get("channels", 0) or 0)
                info.audio_bit_rate    = int(s.get("bit_rate", 0) or 0)
            except (ValueError, TypeError):
                pass

    fmt_tags = fmt.get("tags", {}) or {}
    vs_tags = (video_stream or {}).get("tags", {}) or {}
    info.creation_time = fmt_tags.get("creation_time") or vs_tags.get("creation_time") or ""
    info.rotation = _extract_rotation(video_stream or {})
    info.device = _extract_device(fmt_tags, vs_tags)
    info.format_tags = {k: v for k, v in fmt_tags.items() if k in KEY_METADATA_TAGS}

    # HDR is special and baseline-independent — flag it and stop here.
    if info.color_transfer and info.color_transfer.lower() in HDR_TRANSFERS:
        info.is_hdr = True
        info.status = "hdr"
        info.conflicts = [f"HDR ({info.color_transfer})"]
        return info

    apply_conformance(info, DEFAULT_BASELINE)
    return info


def apply_conformance(info: StreamInfo, baseline: "BaselineSpec" = DEFAULT_BASELINE) -> StreamInfo:
    """Classify a probed clip against `baseline`, setting `info.status`
    ("ok" = matches → stream-copy; "transcode" = needs conforming) and
    `info.conflicts` (human labels for what differs). Skips clips already flagged
    error/hdr. Re-runnable: the merge calls this again once the user picks a
    baseline, so status/conflicts update without re-probing."""
    if info.status in ("error", "hdr"):
        return info
    conflicts = []
    if not _codec_matches(info.codec, baseline.codec):
        conflicts.append(info.codec or "unknown-codec")
    if info.width != baseline.width or info.height != baseline.height:
        conflicts.append(f"{info.width}×{info.height}")
    # Compare the true rate (fps_float); a VFR clip always needs conforming
    # (CFR-normalising) even if its average matches, since VFR drifts A/V sync
    # on a concatenated timeline.
    if info.fps_float <= 0:
        conflicts.append("unknown-fps")
    elif abs(info.fps_float - baseline.fps_float) > 0.01 or info.is_vfr:
        fps_display = (f"{info.fps_float:.0f}fps" if abs(info.fps_float - round(info.fps_float)) < 0.01
                       else f"{info.fps_float:.2f}fps")
        if info.is_vfr:
            fps_display += " VFR"
        conflicts.append(fps_display)
    if info.pix_fmt != baseline.pix_fmt:
        conflicts.append(PIX_FMT_LABELS.get(info.pix_fmt, info.pix_fmt or "unknown-fmt"))
    if (info.color_space and baseline.color_space
            and info.color_space.lower() not in (baseline.color_space.lower(), "unknown", "")):
        conflicts.append(info.color_space)
    # A rotated clip (270°/180°/90°) can numerically match the baseline's own
    # codec/resolution/fps/pix_fmt while still needing its picture corrected —
    # and a plain stream-copy into a shared concat track does NOT reliably
    # carry a rotation Display Matrix that differs from earlier clips in the
    # same track (ffmpeg's concat demuxer effectively inherits the first
    # segment's side-data for the whole resulting stream). Confirmed as a real
    # bug this way: a real recovered clip lost its orientation entirely.
    # Force conforming so a rotated clip always gets a real (correctly
    # oriented) encode instead of a copy that can silently drop the tag.
    if (getattr(info, "rotation", 0) or 0) % 360 != 0:
        conflicts.append(f"rotated {info.rotation}°")
    info.conflicts = conflicts
    info.status = "ok" if not conflicts else "transcode"
    return info


def probe_duration(ffprobe_bin: str, path: str) -> float:
    try:
        raw = _run_ffprobe(ffprobe_bin, path)
        return float(raw.get("format", {}).get("duration", 0) or 0)
    except Exception:
        return 0.0


def probe_concat_segment(ffprobe_bin: str, path: str, audio_index: Optional[int]) -> tuple:
    """(container_duration, audio_stream_duration) for one per-clip temp file —
    the measured truth the concat demuxer will act on. `container_duration` is
    how far the concat advances the NEXT segment's timestamps; the audio
    duration is the `audio_index`-th audio stream's own length (the WAV/ALAC
    slot when that's what's asked for). Either value is 0.0 when unavailable
    (callers treat 0/None as "fall back to the modelled video offsets")."""
    try:
        raw = _run_ffprobe(ffprobe_bin, path)
    except Exception:
        return 0.0, 0.0
    file_dur = 0.0
    try:
        file_dur = float(raw.get("format", {}).get("duration", 0) or 0)
    except (TypeError, ValueError):
        pass
    audio_dur = 0.0
    if audio_index is not None:
        audio_streams = [s for s in raw.get("streams", [])
                         if s.get("codec_type") == "audio"]
        if 0 <= audio_index < len(audio_streams):
            try:
                audio_dur = float(audio_streams[audio_index].get("duration", 0) or 0)
            except (TypeError, ValueError):
                pass
    return file_dur, audio_dur


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


# ── Multi-track video (Extract tab's manual-recovery mode) ────────────────────
# A foreign master (produced by a different tool) can carry more than one video
# stream — e.g. its own archival-track-style embedding — with no manifest to say
# which one is the "real" continuous track. Mirrors AudioTrackInfo/
# probe_audio_tracks above so the Extract tab can offer a video-stream picker.

@dataclass
class VideoTrackInfo:
    """One video stream's identity. `video_index` is 0-based among VIDEO
    streams only (matches ffmpeg's `-map 0:v:N`)."""
    video_index: int
    codec: str = ""
    width: int = 0
    height: int = 0
    fps: str = ""          # r_frame_rate string, e.g. "30000/1001"
    rotation: int = 0      # best-effort: the stream's own "rotate" tag, if any
                           # (0 if absent — a display-matrix rotation needs a
                           # separate side-data query, not worth it just for
                           # this picker's reference display)


def parse_video_tracks(raw: dict) -> list:
    """Pure: turn an ffprobe -show_streams JSON dict into a VideoTrackInfo list."""
    out = []
    video_i = 0
    for s in raw.get("streams", []):
        if s.get("codec_type") != "video":
            continue
        tags = s.get("tags", {}) or {}
        try:
            rotation = int(tags.get("rotate", 0) or 0)
        except (TypeError, ValueError):
            rotation = 0
        out.append(VideoTrackInfo(
            video_index=video_i,
            codec=s.get("codec_name", "") or "",
            width=int(s.get("width", 0) or 0),
            height=int(s.get("height", 0) or 0),
            fps=s.get("r_frame_rate", "") or "",
            rotation=rotation,
        ))
        video_i += 1
    return out


def probe_video_tracks(ffprobe_bin: str, path: str) -> list:
    try:
        raw = _run_ffprobe(ffprobe_bin, path)
    except Exception:
        return []
    return parse_video_tracks(raw)


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
