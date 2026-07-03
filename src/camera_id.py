"""camera_id.py — identify which camera a clip came from.

Cascade, most reliable first:
  1. container device metadata — make/model (phones), or a meaningful
     handler_name (Insta360 writes 'Ambarella …');
  2. filename family (PXL_, DJI_, GX…);
  3. spec signature as a last resort.
Returns a stable `key` (clips sharing a key group into one camera) and a
human `label` the user can rename. Pure — callers pass values already probed
via probe.StreamInfo (probe._extract_device / .rotation etc.), no ffprobe here.
"""

import re
from pathlib import Path

# Leading filename token → default camera label (device metadata wins over this).
_KNOWN_PREFIXES = {
    "PXL": "Pixel phone",
    "DJI": "DJI",
    "GX": "GoPro",
    "GOPR": "GoPro",
    "GH": "GoPro",
    "HERO": "GoPro",
    "IMG": "Phone",
    "MOV": "Camera",
    "VID": "Camera",
    "LRV": "Camera (proxy)",
}


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (s or "").lower()).strip("_")


def _filename_family(stem: str) -> str:
    m = re.match(r"([A-Za-z]+)", stem or "")
    return m.group(1).upper() if m else ""


def identify_camera(device: str = "", filename: str = "", spec_hint: str = "") -> tuple:
    """Return (camera_key, camera_label). `device` is probe.StreamInfo.device,
    `filename` the clip's file name, `spec_hint` a spec signature for the final
    fallback."""
    device = (device or "").strip()
    if device:
        return f"dev:{_slug(device)}", device
    fam = _filename_family(Path(filename).stem) if filename else ""
    if fam:
        return f"file:{fam}", _KNOWN_PREFIXES.get(fam, f"{fam} camera")
    if spec_hint:
        return f"spec:{_slug(spec_hint)}", spec_hint
    return "unknown", "Unknown camera"
