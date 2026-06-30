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
                for k, v in saved.items():
                    if k in self._data:
                        self._data[k] = v
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
