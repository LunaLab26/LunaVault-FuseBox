"""core/gpu_encode.py — optional GPU-accelerated transcode (NVENC/QSV/AMF/VAAPI).

Encoder *availability* in `ffmpeg -encoders` only means the codec was compiled
in — it says nothing about whether this machine actually has a working GPU/
driver for it (an AMD-only box still lists `hevc_nvenc`; it just fails to open
at encode time). So detection here always does a real, tiny (2-frame, null-
output) encode and checks the exit code, rather than trusting the encoder list.
Results are cached per (ffmpeg path, codec) for the life of the process, since
a probe costs a few hundred ms and a merge shouldn't pay it once per clip.

AMF is AMD's proprietary encoder API tied to AMD's *Windows* driver stack —
it has no Linux equivalent. On Linux, AMD (and Intel, as an alternative to
QSV) GPUs expose hardware encode through VAAPI instead, which needs its own
handling: unlike NVENC/QSV/AMF (which ffmpeg can feed plain software frames
directly), VAAPI needs an initialized `-vaapi_device`, an explicit
`hwupload` filter step, and — critically — an ffmpeg binary actually
compiled with VAAPI support. This app's bundled Linux static build
(johnvansickle.com) deliberately ships with no hardware-acceleration
libraries at all, so VAAPI detection/encoding always goes through a
separate, real system ffmpeg (see system_vaapi_ffmpeg()) instead of the
bundled one — confirmed directly: the bundled build's `-hwaccels` lists
only vdpau, while SteamOS's own /usr/bin/ffmpeg successfully VAAPI-encodes
on the same machine's AMD GPU.
"""

import glob
import shutil
import subprocess

from core.binaries import no_window

# Priority order when "auto": most broadly capable / best quality-per-watt on
# typical consumer hardware first. Vendors not present on the machine simply
# fail their probe and are skipped. VAAPI is last — it's the generic/open
# Linux path, tried only once NVENC/QSV (and AMF, which never works on Linux
# but costs almost nothing to still probe) have failed.
VENDOR_ORDER = ["nvenc", "qsv", "amf", "vaapi"]

_ENCODER_NAMES = {
    "nvenc": {"h264": "h264_nvenc", "hevc": "hevc_nvenc"},
    "qsv":   {"h264": "h264_qsv",   "hevc": "hevc_qsv"},
    "amf":   {"h264": "h264_amf",   "hevc": "hevc_amf"},
    "vaapi": {"h264": "h264_vaapi", "hevc": "hevc_vaapi"},
}

_probe_cache: dict = {}   # (ff, encoder_name) -> bool
_system_vaapi_ffmpeg_cache: dict = {}   # lazy singleton — see system_vaapi_ffmpeg()
_vaapi_device_cache: dict = {}          # lazy singleton — see vaapi_render_device()


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


def system_vaapi_ffmpeg() -> "str | None":
    """A real ffmpeg with VAAPI compiled in, or None if none is found — the
    bundled static build never qualifies (see module docstring). Cached for
    the life of the process; `-hwaccels` output is what actually distinguishes
    a VAAPI-capable build from one without, cheaper than a real probe encode."""
    if "path" not in _system_vaapi_ffmpeg_cache:
        _system_vaapi_ffmpeg_cache["path"] = _find_system_vaapi_ffmpeg()
    return _system_vaapi_ffmpeg_cache["path"]


def _find_system_vaapi_ffmpeg() -> "str | None":
    seen = set()
    for candidate in (shutil.which("ffmpeg"), "/usr/bin/ffmpeg"):
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            r = subprocess.run([candidate, "-hide_banner", "-hwaccels"],
                               capture_output=True, text=True, timeout=5, **no_window())
            if "vaapi" in r.stdout:
                return candidate
        except Exception:
            continue
    return None


def vaapi_render_device() -> "str | None":
    """First /dev/dri/renderD* node found, or None if this machine has none."""
    if "path" not in _vaapi_device_cache:
        nodes = sorted(glob.glob("/dev/dri/renderD*"))
        _vaapi_device_cache["path"] = nodes[0] if nodes else None
    return _vaapi_device_cache["path"]


def hw_decode_available() -> bool:
    """True when hardware (VAAPI) video DECODE can be attempted on this machine
    — i.e. a real VAAPI-capable system ffmpeg and a render node both exist.
    Gated on the same two prerequisites as VAAPI encode (see the module
    docstring: the bundled static ffmpeg carries no VAAPI at all), so a machine
    where VAAPI encode probes as working can decode too. Used to decide whether
    to OFFER the hardware-decode pipeline option, not to force it on."""
    return system_vaapi_ffmpeg() is not None and vaapi_render_device() is not None


def vaapi_decode_global_args() -> "list | None":
    """The pre-input args that turn on VAAPI hardware decode: `-hwaccel vaapi
    -hwaccel_device <node>`. This is the AUTO-DOWNLOAD form (no
    `-hwaccel_output_format vaapi`) on purpose — decoded frames land back in
    system memory, so every downstream software filter (scale/pad/blur) and
    either encoder still works unchanged; a zero-copy GPU-surface form would
    break software filters and has no such general-purpose command shape.
    Returns None when hardware decode isn't available."""
    device = vaapi_render_device()
    if not hw_decode_available() or device is None:
        return None
    return ["-hwaccel", "vaapi", "-hwaccel_device", device]


def probe_vaapi_encoder(enc_name: str, pix_fmt: str = "nv12", timeout: float = 8.0) -> bool:
    """VAAPI equivalent of probe_encoder() — same real-encode-to-null-output
    check, but against system_vaapi_ffmpeg() (not the bundled binary) and
    with the device + hwupload filter VAAPI actually requires; a plain
    sw-frame attempt (the nvenc/qsv/amf probe shape) fails for VAAPI even
    when it works, so it can't share that function's command."""
    ff = system_vaapi_ffmpeg()
    device = vaapi_render_device()
    if not ff or not device:
        return False
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error",
           "-vaapi_device", device,
           "-f", "lavfi", "-i", "testsrc=size=320x240:rate=30:duration=0.1",
           "-vf", f"format={pix_fmt},hwupload",
           "-frames:v", "2", "-c:v", enc_name,
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
        if enc_name.endswith("_vaapi"):
            _probe_cache[key] = probe_vaapi_encoder(enc_name)
        else:
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
    Returns [] for an unrecognised vendor (caller falls back to sw).

    NVENC/QSV/AMF only — these accept plain software frames directly, so a
    trailing `-c:v ... -pix_fmt <hwfmt>` is all a caller needs to add to an
    otherwise-normal command. VAAPI needs a real hw device + an upload filter
    upstream of the encoder, which doesn't fit this shape — see
    hw_encode_plan(), the one entry point that handles every vendor
    (including VAAPI) and returns everything a caller actually needs."""
    if vendor == "vaapi":
        return []
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


def vaapi_encoder_args(codec: str, quality: int = 18) -> list:
    """VAAPI's own trailing encoder args — no -pix_fmt here (the hwupload
    filter step in hw_encode_plan() already puts the frame in the right
    hardware surface format; the encoder receives an opaque vaapi frame
    reference, not nv12/p010le, so passing -pix_fmt here would be wrong)."""
    codec_key = "hevc" if codec.lower() in ("hevc", "h265") else "h264"
    enc = _ENCODER_NAMES["vaapi"][codec_key]
    args = ["-c:v", enc, "-qp", str(quality)]
    if codec_key == "hevc":
        args += ["-tag:v", "hvc1"]
    return args


def hw_encode_plan(codec: str, vendor: "str | None", pix_fmt: str, quality: int = 18) -> "dict | None":
    """The single entry point a caller needs for ANY hw vendor, VAAPI
    included — unlike hw_video_encoder_args()/hw_pix_fmt(), which only cover
    NVENC/QSV/AMF's simpler "swap the trailing encoder args" shape, this
    also reports the ffmpeg BINARY to actually run the command with (None
    means "the caller's own bundled ffmpeg is fine"; VAAPI needs
    system_vaapi_ffmpeg() instead), any extra GLOBAL args the caller must
    insert before its first -i (VAAPI's -vaapi_device), and a filter step
    the caller must append as the LAST step of its video filter chain
    (VAAPI's format=...,hwupload). Returns None if `vendor` is falsy or its
    encoder isn't actually usable on this machine (caller falls back to sw)."""
    if not vendor:
        return None
    if vendor == "vaapi":
        ff = system_vaapi_ffmpeg()
        device = vaapi_render_device()
        if not ff or not device or not _cached_probe(ff, encoder_name(codec, "vaapi")):
            return None
        return {
            "ffmpeg_bin": ff,
            "global_args": ["-vaapi_device", device],
            "filter_suffix": f"format={hw_pix_fmt(pix_fmt)},hwupload",
            "encoder_args": vaapi_encoder_args(codec, quality),
        }
    args = hw_video_encoder_args(codec, vendor, pix_fmt, quality)
    if not args:
        return None
    return {"ffmpeg_bin": None, "global_args": [], "filter_suffix": None, "encoder_args": args}
