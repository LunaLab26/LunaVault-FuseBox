"""core/encoders.py — detect available video encoders and choose one.

Probes `ffmpeg -encoders` for hardware encoders (NVENC / QSV / AMF / VAAPI) and
picks per a mode:
  - "quality" → always CPU (libx265 / libx264). Best fidelity; the v1.2 default.
  - "fast"    → hardware if present, else CPU.
  - "auto"    → caller decides prefer_hw per job (hardware for the small WhatsApp
                clip, CPU for the 4K archival master).

Selection is a pure function (`recommend`) and is unit-tested. The hardware
rate-control args are provided but the app keeps "quality" (CPU) as the default
until a user validates hardware output on their own GPU — see merge tab settings.
"""

import subprocess
from typing import List, Tuple

from .binaries import no_window

# Preference order per codec, best-first. CPU fallback last.
_HEVC_HW = ["hevc_nvenc", "hevc_qsv", "hevc_amf", "hevc_vaapi"]
_H264_HW = ["h264_nvenc", "h264_qsv", "h264_amf", "h264_vaapi"]
_CPU = {"hevc": "libx265", "h264": "libx264"}


def detect_encoders(ff: str) -> set:
    """Return the set of encoder names ffmpeg reports as available."""
    try:
        r = subprocess.run([ff, "-hide_banner", "-encoders"],
                           capture_output=True, text=True, timeout=15, **no_window())
    except Exception:
        return set()
    names = set()
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 6:   # flag column then name
            names.add(parts[1])
    return names


def recommend(codec: str, available: set, prefer_hw: bool) -> str:
    """Pure choice of encoder name given what's available and whether to prefer HW."""
    codec = "hevc" if codec in ("hevc", "h265") else "h264"
    if prefer_hw:
        order = _HEVC_HW if codec == "hevc" else _H264_HW
        for enc in order:
            if enc in available:
                return enc
    return _CPU[codec]


def encoder_args(name: str, quality: int) -> List[str]:
    """Rate-control args for a chosen encoder (quality ~ CRF/CQ scale 0–51)."""
    if name in ("libx265", "libx264"):
        return ["-crf", str(quality), "-preset", "medium"]
    if name.endswith("_nvenc"):
        return ["-rc", "vbr", "-cq", str(quality), "-preset", "p5"]
    if name.endswith("_qsv"):
        return ["-global_quality", str(quality), "-preset", "medium"]
    if name.endswith("_amf"):
        return ["-rc", "cqp", "-qp_p", str(quality), "-qp_i", str(quality)]
    if name.endswith("_vaapi"):
        return ["-rc_mode", "CQP", "-qp", str(quality)]
    return ["-crf", str(quality)]


def select(ff: str, codec: str, mode: str, prefer_hw_in_auto: bool,
           quality: int) -> Tuple[str, List[str]]:
    """High-level: return (encoder_name, rate_control_args) for a job.

    mode ∈ {"quality","fast","auto"}. "quality" forces CPU.
    """
    if mode == "quality":
        prefer_hw = False
    elif mode == "fast":
        prefer_hw = True
    else:  # auto
        prefer_hw = prefer_hw_in_auto
    available = detect_encoders(ff) if prefer_hw else set()
    name = recommend(codec, available, prefer_hw)
    return name, encoder_args(name, quality)
