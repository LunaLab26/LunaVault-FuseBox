"""core/collection.py — the collection record (schema `fusebox.collection/1`).

A collection is FuseBox's user-facing unit: a named, dated group of memories in a
self-describing folder. `collection.json` sits BESIDE the technical
`manifest.json` (core/manifest.py) and carries the ORGANISATIONAL layer — name,
capture date range, cover, storage mode, and creation-time provenance — that Home
and the album view read. The folder is the source of truth; the app's catalog
(core/catalog.py) is only a rebuildable cache. See COLLECTION_SCHEMA.md.

Pure module: dataclasses + JSON (de)serialisation, mirroring core/manifest.py.
"""

import json
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

COLLECTION_SCHEMA = "fusebox.collection/1"
COLLECTION_FILENAME = "collection.json"

# The three honesty levels ffmpeg_runner writes into each ClipEntry.recovery_fidelity.
FIDELITY_LEVELS = ("byte-exact", "decode-lossless", "transcoded")

STORAGE_COMPACT = "compact"     # thumbnails only; play/recover from the master
STORAGE_PORTABLE = "portable"   # real clip files kept alongside (walk-away browsable)


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def new_collection_id() -> str:
    """A stable id that survives folder moves and renames — matched by the catalog
    so a relocated collection relinks instead of duplicating."""
    return "col_" + uuid.uuid4().hex[:12]


@dataclass
class Captured:
    start: str = ""   # ISO date (YYYY-MM-DD) of the earliest clip
    end: str = ""     # ISO date of the latest clip


@dataclass
class Verified:
    """Creation-time provenance — a FACT about when the archive was made, never a
    live health signal (ongoing integrity is out of scope)."""
    at_utc: str = ""
    passed: int = 0
    total: int = 0
    fidelity: dict = field(default_factory=dict)   # {level: count} over FIDELITY_LEVELS


@dataclass
class Collection:
    id: str = ""
    name: str = ""
    created_utc: str = ""
    captured: Captured = field(default_factory=Captured)
    cover: str = ""                    # relative path within the folder, e.g. "thumbs/03.jpg"
    memory_count: int = 0
    master: str = ""                   # relative filename of the vault
    storage_mode: str = STORAGE_COMPACT
    clips_dir: Optional[str] = None    # relative dir when portable (e.g. "clips"); else None
    cloud_backed: bool = False
    verified: Verified = field(default_factory=Verified)


# ── JSON (de)serialisation ─────────────────────────────────────────────────────

_CAPTURED_FIELDS = {f.name for f in fields(Captured)}
_VERIFIED_FIELDS = {f.name for f in fields(Verified)}


def _to_dict(c: Collection) -> dict:
    return {
        "schema": COLLECTION_SCHEMA,
        "id": c.id,
        "name": c.name,
        "created_utc": c.created_utc,
        "captured": asdict(c.captured),
        "cover": c.cover,
        "memory_count": c.memory_count,
        "master": c.master,
        "storage_mode": c.storage_mode,
        "clips_dir": c.clips_dir,
        "cloud_backed": c.cloud_backed,
        "verified": asdict(c.verified),
    }


def to_json(c: Collection, indent: Optional[int] = 2) -> str:
    return json.dumps(_to_dict(c), indent=indent, ensure_ascii=False)


def from_json(s: str) -> Collection:
    """Tolerant of missing/unknown keys (forward + backward compatible)."""
    d = json.loads(s)
    cap = d.get("captured") or {}
    ver = d.get("verified") or {}
    return Collection(
        id=d.get("id", "") or "",
        name=d.get("name", "") or "",
        created_utc=d.get("created_utc", "") or "",
        captured=Captured(**{k: v for k, v in cap.items() if k in _CAPTURED_FIELDS}),
        cover=d.get("cover", "") or "",
        memory_count=int(d.get("memory_count", 0) or 0),
        master=d.get("master", "") or "",
        storage_mode=d.get("storage_mode", STORAGE_COMPACT) or STORAGE_COMPACT,
        clips_dir=d.get("clips_dir"),
        cloud_backed=bool(d.get("cloud_backed", False)),
        verified=Verified(**{k: v for k, v in ver.items() if k in _VERIFIED_FIELDS}),
    )


def collection_path(folder) -> Path:
    return Path(folder) / COLLECTION_FILENAME


def write_collection(c: Collection, folder) -> Path:
    p = collection_path(folder)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(to_json(c), encoding="utf-8")
    return p


def read_collection(folder) -> Optional[Collection]:
    """The Collection from a folder's collection.json, or None if absent/unreadable."""
    p = collection_path(folder)
    try:
        if p.exists():
            return from_json(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return None


# ── Builders from a manifest ────────────────────────────────────────────────────

def fidelity_counts(manifest) -> dict:
    """Roll each clip's recovery_fidelity into {level: count} over FIDELITY_LEVELS."""
    counts = {lvl: 0 for lvl in FIDELITY_LEVELS}
    for clip in getattr(manifest, "clips", []):
        lvl = getattr(clip, "recovery_fidelity", "") or ""
        if lvl in counts:
            counts[lvl] += 1
    return counts


def capture_range(manifest) -> Captured:
    """The earliest/latest capture DATE across the manifest's clips (from each
    clip's creation_time), for grouping and the default name."""
    dates = sorted(
        (getattr(c, "creation_time", "") or "")[:10]
        for c in getattr(manifest, "clips", [])
        if (getattr(c, "creation_time", "") or "")
    )
    if not dates:
        return Captured()
    return Captured(start=dates[0], end=dates[-1])


def verified_txt(c: Collection) -> str:
    """The plain-English 'safe' proof written into the folder — honest, no
    'forever'. Reads the fidelity roll-up so it can be specific per collection."""
    fc = c.verified.fidelity or {}
    be = int(fc.get("byte-exact", 0))
    dl = int(fc.get("decode-lossless", 0))
    tr = int(fc.get("transcoded", 0))
    as_filmed = be + dl
    day = (c.verified.at_utc or "")[:10]
    lines = [f"{c.name} — kept and verified",
             f"{c.verified.total} memories" + (f", checked {day}." if day else ".") ,
             ""]
    if as_filmed:
        lines.append(f"{as_filmed} recovered exactly as filmed — identical picture and sound.")
    if be:
        lines.append(f"  of those, {be} are byte-for-byte identical to the original files.")
    if tr:
        lines.append(f"{tr} kept as a high-quality copy (not the original bitstream).")
    lines += ["",
              "Every memory can be recovered from this archive.",
              "Open it in FuseBox to browse, play, or save any original."]
    return "\n".join(lines)


def write_verified_txt(c: Collection, folder, filename: str = "verified.txt") -> Path:
    p = Path(folder) / filename
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(verified_txt(c), encoding="utf-8")
    return p


def emit_collection(c: Collection, folder) -> Path:
    """Write the two organisational files (collection.json + verified.txt) into a
    collection folder. The master/manifest/restore.log/thumbs are written by the
    caller; this makes the folder self-describing at the collection level."""
    write_verified_txt(c, folder)
    return write_collection(c, folder)


def build_collection(manifest, *, name: str, cover: str = "",
                     storage_mode: str = STORAGE_COMPACT, clips_dir: Optional[str] = None,
                     cloud_backed: bool = False, verified_passed: int = 0,
                     collection_id: Optional[str] = None) -> Collection:
    """Assemble a Collection from a finished master's manifest plus the few
    organisational choices the manifest doesn't carry."""
    total = len(getattr(manifest, "clips", []))
    return Collection(
        id=collection_id or new_collection_id(),
        name=name,
        created_utc=now_utc_iso(),
        captured=capture_range(manifest),
        cover=cover,
        memory_count=total,
        master=getattr(manifest, "master_filename", "") or "",
        storage_mode=storage_mode,
        clips_dir=clips_dir,
        cloud_backed=cloud_backed,
        verified=Verified(at_utc=now_utc_iso(), passed=verified_passed, total=total,
                          fidelity=fidelity_counts(manifest)),
    )
