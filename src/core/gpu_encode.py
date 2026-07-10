"""core/gpu_encode.py — optional GPU-accelerated transcode (NVENC/QSV/AMF).

Encoder *availability* in `ffmpeg -encoders` only means the codec was compiled
in — it says nothing about whether this machine actually has a working GPU/
driver for it (an AMD-only box still lists `hevc_nvenc`; it just fails to open
at encode time). So detection here always does a real, tiny (2-frame, null-
output) encode and checks the exit code, rather than trusting the encoder list.
Results are cached per (ffmpeg path, codec) for the life of the process, since
a probe costs a few hundred ms and a merge shouldn't pay it once per clip.
"""

import subprocess

from core.binaries import no_window

# Priority order when "auto": most broadly capable / best quality-per-watt on
# typical consumer hardware first. Vendors not present on the machine simply
# fail their probe and are skipped.
VENDOR_ORDER = ["nvenc", "qsv", "amf"]

_ENCODER_NAMES = {
    "nvenc": {"h264": "h264_nvenc", "hevc": "hevc_nvenc"},
    "qsv":   {"h264": "h264_qsv",   "hevc": "hevc_qsv"},
    "amf":   {"h264": "h264_amf",   "hevc": "hevc_amf"},
}

_probe_cache: dict = {}   # (ff, encoder_name) -> bool


def encoder_name(codec: str, vendor: str) -> str:
    """The ffmpeg encoder name for a codec ("h264"/"hevc") + vendor."""
    codec_key = "hevc" if codec.lower() in ("hevc", "h265") else "h264"
    return _ENCODER_NAMES[vendor][codec_key]


def hw_pix_fmt(pix_fmt: str) -> str:
    """Map a software conform pix_fmt to the hardware surface format: 10-bit
    formats need p010le, everything else nv12 (what NVENC/QSV/AMF all accept)."""
    return "p010le" if "10le" in (pix_fmt or "") or "p010" in (pix_fmt or "") else "nv12"


def probe_encoder(ff: str, enc_name: str, pix_fmt: str = "nv12", timeout: float = 8.0) -> bool:
    """Real availability check: try a tiny 2-frame encode to null output.
    Returns True only if ffmpeg actually exits 0 — a listed-but-nonfunctional
    encoder (no matching GPU/driver) returns False here."""
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error",
           "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=0.1",
           "-frames:v", "2", "-c:v", enc_name, "-pix_fmt", pix_fmt,
           "-f", "null", "-"]
    try:
        proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                              timeout=timeout, **no_window())
        return proc.returncode == 0
    except Exception:
        return False


def _cached_probe(ff: str, enc_name: str) -> bool:
    key = (ff, enc_name)
    if key not in _probe_cache:
        _probe_cache[key] = probe_encoder(ff, enc_name)
    return _probe_cache[key]


def detect_best_hw(ff: str, codec: str = "hevc") -> "str | None":
    """First VENDOR_ORDER entry whose encoder for `codec` actually works on
    this machine, or None if no GPU encoder is usable."""
    for vendor in VENDOR_ORDER:
        if _cached_probe(ff, encoder_name(codec, vendor)):
            return vendor
    return None


def available_hw_vendors(ff: str, codec: str = "hevc") -> list:
    """Every vendor (not just the best) whose encoder works — for UI display."""
    return [v for v in VENDOR_ORDER if _cached_probe(ff, encoder_name(codec, v))]


def hw_video_encoder_args(codec: str, vendor: str, pix_fmt: str, quality: int = 18) -> list:
    """Vendor-specific encoder args, quality-matched to the software crf value
    (`quality`, defaulting to 18 to match this app's original baseline).
    Returns [] for an unrecognised vendor (caller falls back to sw)."""
    codec_key = "hevc" if codec.lower() in ("hevc", "h265") else "h264"
    enc = _ENCODER_NAMES.get(vendor, {}).get(codec_key)
    if not enc:
        return []
    hwfmt = hw_pix_fmt(pix_fmt)
    q = str(quality)
    args = ["-c:v", enc]
    if vendor == "nvenc":
        args += ["-preset", "p6", "-tune", "hq", "-rc", "vbr", "-cq", q, "-b:v", "0"]
    elif vendor == "qsv":
        args += ["-preset", "veryslow", "-global_quality", q]
    elif vendor == "amf":
        args += ["-quality", "quality", "-rc", "cqp", "-qp_i", q, "-qp_p", str(quality + 2)]
    args += ["-pix_fmt", hwfmt]
    if codec_key == "hevc":
        args += ["-tag:v", "hvc1"]
        if hwfmt == "p010le":
            args += ["-profile:v", "main10"]
    return args
