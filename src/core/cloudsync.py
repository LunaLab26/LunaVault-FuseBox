"""core/cloudsync.py — provider-agnostic detection of cloud-sync folders.

FuseBox never talks to a cloud API; it treats cloud storage as "just a folder"
(see PRODUCT_DIRECTION.md). This module answers one question — "does this folder
live in a cloud-sync location?" — so the app can pick the context-aware storage
default (compact when local, keep clip files when cloud-backed) and explain it.

Two signals, both best-effort:
  1. A known sync-client folder name somewhere in the path (Dropbox, OneDrive,
     Jottacloud, Google Drive, iCloud Drive, …) — provider-agnostic by design.
  2. On Windows, the cloud "placeholder" file attributes (offline / recall on
     open) that Files-On-Demand clients set. Guarded to Windows; a no-op elsewhere.
"""

import os
from pathlib import Path
from typing import Optional

# folder-name fragment (lowercased) -> provider label
_PROVIDER_MARKERS = {
    "jottacloud": "jottacloud",
    "dropbox": "dropbox",
    "onedrive": "onedrive",
    "google drive": "googledrive",
    "googledrive": "googledrive",
    "icloerdrive": "icloud",     # tolerate odd spellings
    "icloud drive": "icloud",
    "icloud~": "icloud",
    "icloud": "icloud",
    "pcloud": "pcloud",
    "box sync": "box",
    "mega": "mega",
    "nextcloud": "nextcloud",
    "sync.com": "sync",
}

# Windows file attributes set by Files-On-Demand / online-only placeholders.
_FILE_ATTRIBUTE_OFFLINE = 0x00001000
_FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x00040000
_FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x00400000
_CLOUD_ATTRS = (_FILE_ATTRIBUTE_OFFLINE
                | _FILE_ATTRIBUTE_RECALL_ON_OPEN
                | _FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS)


def detect_provider(path) -> Optional[str]:
    """A provider label if any component of `path` names a known sync client,
    else None. Case-insensitive, substring match (handles 'OneDrive - Personal',
    'iCloudDrive', etc.)."""
    for part in Path(path).parts:
        low = part.lower()
        for marker, label in _PROVIDER_MARKERS.items():
            if marker in low:
                return label
    return None


def has_placeholder_attributes(path) -> bool:
    """True if `path` carries Windows cloud-placeholder attributes (online-only).
    Always False off Windows or when unreadable."""
    try:
        attrs = getattr(os.stat(path), "st_file_attributes", 0)
        return bool(attrs & _CLOUD_ATTRS)
    except Exception:
        return False


def is_cloud_backed(path) -> bool:
    """Best-effort: does this folder live in a cloud-sync location?"""
    if detect_provider(path) is not None:
        return True
    p = Path(path)
    # Check the folder itself and a couple of parents for placeholder attributes —
    # the collection folder is usually a normal dir even when its contents sync.
    for cand in (p, *list(p.parents)[:2]):
        if has_placeholder_attributes(cand):
            return True
    return False
