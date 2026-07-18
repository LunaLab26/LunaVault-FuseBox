"""settings.py — load/save persistent app preferences to settings.json beside the executable."""

import json
import sys
from pathlib import Path


def _settings_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent / "settings.json"
    return Path(__file__).resolve().parent.parent / "settings.json"


DEFAULTS = {
    "last_merge_source": "",
    "last_merge_output_dir": "",
    "last_merge_output_name": "",
    "last_merge_track_order": "camera",   # "camera" | "wav" | "mixed"
    "last_wa_source": "",
    "last_wa_output_dir": "",
    "last_wa_grade": "",
    "last_wa_start": "00:00:00",
    "last_wa_duration": "00:01:00",
    "window_geometry": None,
    "theme_mode": "system",
    "last_review_source": "",
    "review_software_decode": False,
    "auto_save_log_on_failure": True,
    "camera_labels": {},   # {camera_id: user-given label}, remembered across folder loads
    "extract_output_format": "native",   # "native" | "mov" | "mp4" — Extract tab recovery container
    # Merge transcode pipeline (decode + encode method), chosen in Pre-flight.
    # "recommended" auto-picks the benchmarked-best pipeline for this machine
    # (hardware encode when a GPU encoder is available, software decode — the
    # fastest wall-clock hybrid); when off, the two method choices below apply.
    "merge_pipeline_recommended": True,
    "merge_decode_method": "software",    # "software" (CPU) | "hardware" (GPU/VAAPI)
    "merge_encode_method": "hardware",    # "software" (CPU) | "hardware" (GPU/VAAPI)
    "ui_mode": "friendly",   # "friendly" (Memories/Add + classic tabs) | "legacy" (pre-overhaul
                             # tab set only) — switched via a hidden toggle (triple-click the logo)
    # Developer options — experimental, off by default, exposed only via the hidden
    # Developer panel (triple-click the logo, next to the Legacy toggle). Each is an
    # independent switch so a change that causes trouble can be rolled back alone.
    # These currently tune how the per-clip preview sample is generated.
    "dev_preview_gpu_encode": False,   # encode the preview with a detected GPU encoder (NVENC/QSV/AMF)
    "dev_preview_hw_decode": False,    # decode the source with GPU hardware acceleration (-hwaccel auto)
    "dev_preview_fast_sample": False,  # shorter, ultrafast-preset sample for a near-instant preview
    "dev_preview_height": 160,         # preview proxy resolution (scale height): 160|240|360|480|720
    "dev_preview_window_size": "medium",  # preview popup size: small|medium|large
    "dev_preview_aspect_mode": "fit",     # preview video scaling: fit|stretch|crop
    "dev_preview_loop": True,             # loop the preview sample
    "dev_preview_speed": 1.0,             # preview playback speed: 0.5 | 1.0 | 2.0
    # Review-tab playback experiments
    "dev_review_frame_poll_ms": 300,   # software-decode picture refresh interval: 150 | 300 | 500
    "dev_review_allow_risky_hw_decode": False,  # let the GPU decode 4K 10-bit HEVC (normally forced to software)
    "dev_review_thumb_count": 24,      # overview filmstrip tile count: 12 | 24 | 48
    "dev_review_thumb_width": 160,     # overview filmstrip tile width px: 120 | 160 | 240
}


class Settings:
    def __init__(self):
        self._path = _settings_path()
        self._data: dict = dict(DEFAULTS)
        self._load()

    def _load(self):
        try:
            if self._path.exists():
                with open(self._path, encoding="utf-8") as f:
                    saved = json.load(f)
                # Merge everything on disk, not just keys already in DEFAULTS —
                # a saved key that predates a DEFAULTS entry (or was added by a
                # newer app version) must not be silently discarded on load.
                self._data.update(saved)
        except Exception:
            pass

    def save(self):
        try:
            with open(self._path, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
        except Exception:
            pass

    def get(self, key: str, default=None):
        return self._data.get(key, default)

    def set(self, key: str, value):
        self._data[key] = value
        self.save()

    def __getitem__(self, key):
        return self._data[key]

    def __setitem__(self, key, value):
        self.set(key, value)
