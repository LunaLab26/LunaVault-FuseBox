"""core/catalog.py — FuseBox's local registry of known collections (schema
`fusebox.catalog/1`).

A rebuildable CACHE, never the source of truth — that's each collection folder's
`collection.json` (core/collection.py). Home renders from this cache alone, so it
works instantly and offline; a copied-out cover per collection (under
`<app data>/covers/`) keeps the shelf from ever going blank. Collections are
matched by their stable id, so a moved or renamed folder relinks instead of
duplicating. Lose the catalog and you re-add folders — nothing depends on it.

Pure module: dataclasses + JSON (de)serialisation, same style as settings.py /
core/manifest.py. Callers pass the app-data dir (e.g. settings._settings_path().
parent) so this stays free of any app-layer dependency.
"""

import json
import shutil
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from core import collection as collection_mod
from core import cloudsync

CATALOG_SCHEMA = "fusebox.catalog/1"
CATALOG_FILENAME = "catalog.json"
COVERS_DIRNAME = "covers"

STATUS_AVAILABLE = "available"   # folder reachable
STATUS_OFFLINE = "offline"       # known but not currently reachable (drive out / cloud not hydrated)
STATUS_MISSING = "missing"       # expected but gone


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class Locate:
    """Aids to re-find a folder if its absolute path breaks (drive-letter change,
    re-mounted cloud). None of these are authoritative — best-effort re-linking."""
    volume_label: str = ""
    cloud: Optional[str] = None      # detected provider ("jottacloud", "dropbox", …) or None
    relative_hint: str = ""


@dataclass
class Cached:
    """Everything Home needs to render a card WITHOUT touching the folder."""
    name: str = ""
    date: str = ""                   # captured.start
    cover: str = ""                  # filename under <app data>/covers/
    memory_count: int = 0
    verified: str = ""               # e.g. "40/40"


@dataclass
class CatalogEntry:
    id: str = ""
    path: str = ""                   # last-known folder path
    locate: Locate = field(default_factory=Locate)
    cached: Cached = field(default_factory=Cached)
    status: str = STATUS_AVAILABLE
    added_utc: str = ""
    last_seen_utc: str = ""


@dataclass
class Catalog:
    collections: list = field(default_factory=list)   # list[CatalogEntry]

    def get(self, cid: str) -> Optional[CatalogEntry]:
        return next((e for e in self.collections if e.id == cid), None)

    def upsert(self, entry: CatalogEntry) -> CatalogEntry:
        """Add a new entry, or relink an existing one BY ID (a moved/renamed folder
        updates its path + cache in place — never a duplicate). Preserves the
        original added_utc; refreshes last_seen_utc."""
        existing = self.get(entry.id)
        if existing is None:
            entry.added_utc = entry.added_utc or now_utc_iso()
            entry.last_seen_utc = now_utc_iso()
            self.collections.append(entry)
            return entry
        entry.added_utc = existing.added_utc or entry.added_utc or now_utc_iso()
        entry.last_seen_utc = now_utc_iso()
        self.collections[self.collections.index(existing)] = entry
        return entry

    def remove(self, cid: str) -> None:
        self.collections = [e for e in self.collections if e.id != cid]

    def move(self, cid: str, delta: int) -> None:
        """Reorder a collection within the shelf by `delta` (-1 = earlier/left,
        +1 = later/right); clamped to the ends. Home renders in list order, so
        this is the user's manual arrangement."""
        idx = next((i for i, e in enumerate(self.collections) if e.id == cid), None)
        if idx is None:
            return
        new = max(0, min(len(self.collections) - 1, idx + delta))
        if new != idx:
            self.collections.insert(new, self.collections.pop(idx))

    def rename(self, cid: str, name: str) -> None:
        e = self.get(cid)
        if e is not None:
            e.cached.name = name

    def set_status(self, cid: str, status: str) -> None:
        e = self.get(cid)
        if e is not None:
            e.status = status
            if status == STATUS_AVAILABLE:
                e.last_seen_utc = now_utc_iso()

    def refresh_statuses(self) -> None:
        """Update each entry to available/offline by whether its folder is
        currently reachable — a collection on an unplugged drive or an un-hydrated
        cloud folder goes 'offline' (still shown, from cache), never dropped."""
        for e in self.collections:
            if e.path and Path(e.path).exists():
                e.status = STATUS_AVAILABLE
                e.last_seen_utc = now_utc_iso()
            else:
                e.status = STATUS_OFFLINE


# ── JSON (de)serialisation ─────────────────────────────────────────────────────

_LOCATE_FIELDS = {f.name for f in fields(Locate)}
_CACHED_FIELDS = {f.name for f in fields(Cached)}


def _entry_to_dict(e: CatalogEntry) -> dict:
    return {
        "id": e.id,
        "path": e.path,
        "locate": asdict(e.locate),
        "cached": asdict(e.cached),
        "status": e.status,
        "added_utc": e.added_utc,
        "last_seen_utc": e.last_seen_utc,
    }


def _entry_from_dict(d: dict) -> CatalogEntry:
    loc = d.get("locate") or {}
    cac = d.get("cached") or {}
    return CatalogEntry(
        id=d.get("id", "") or "",
        path=d.get("path", "") or "",
        locate=Locate(**{k: v for k, v in loc.items() if k in _LOCATE_FIELDS}),
        cached=Cached(**{k: v for k, v in cac.items() if k in _CACHED_FIELDS}),
        status=d.get("status", STATUS_AVAILABLE) or STATUS_AVAILABLE,
        added_utc=d.get("added_utc", "") or "",
        last_seen_utc=d.get("last_seen_utc", "") or "",
    )


def to_json(cat: Catalog, indent: Optional[int] = 2) -> str:
    return json.dumps(
        {"schema": CATALOG_SCHEMA, "collections": [_entry_to_dict(e) for e in cat.collections]},
        indent=indent, ensure_ascii=False)


def from_json(s: str) -> Catalog:
    d = json.loads(s)
    return Catalog(collections=[_entry_from_dict(e) for e in d.get("collections", [])])


# ── Store (JSON file beside settings.json) ──────────────────────────────────────

def catalog_path(app_dir) -> Path:
    return Path(app_dir) / CATALOG_FILENAME


def covers_dir(app_dir) -> Path:
    return Path(app_dir) / COVERS_DIRNAME


def load(path) -> Catalog:
    """Load the catalog, or an empty one if absent/unreadable — never raises, so a
    corrupt cache can't stop the app opening."""
    try:
        p = Path(path)
        if p.exists():
            return from_json(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return Catalog()


def save(cat: Catalog, path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(to_json(cat), encoding="utf-8")


# ── Builder from a Collection ───────────────────────────────────────────────────

def entry_from_collection(c, folder, *, cover_cache: str = "",
                          locate: Optional[Locate] = None) -> CatalogEntry:
    """A CatalogEntry summarising a Collection at a given folder — `cover_cache` is
    the filename of the copied-out cover under covers_dir(), used so Home renders
    even when the folder is offline."""
    if locate is None:
        locate = Locate(cloud=cloudsync.detect_provider(folder),
                        relative_hint=Path(folder).name)
    return CatalogEntry(
        id=c.id,
        path=str(Path(folder)),
        locate=locate,
        cached=Cached(
            name=c.name,
            date=getattr(c.captured, "start", "") or "",
            cover=cover_cache,
            memory_count=c.memory_count,
            verified=f"{c.verified.passed}/{c.verified.total}",
        ),
        status=STATUS_AVAILABLE,
    )


# ── User edits from the Home shelf (rename / reorder / remove / delete) ─────────
#
# Each loads the catalog, mutates, and re-saves; never raises. `rename` and
# `delete_collection_folder` also touch the folder itself (the source of truth /
# the files on disk) when it's reachable — the rest only touch the local cache.

def rename_collection(app_dir, cid: str, new_name: str) -> None:
    """Rename a collection everywhere: the folder's collection.json (source of
    truth) when reachable, and the catalog cache. Safe if the folder is offline —
    the cache still updates, and the folder relinks by id next time it's seen."""
    cat_p = catalog_path(app_dir)
    cat = load(cat_p)
    e = cat.get(cid)
    if e is None:
        return
    cat.rename(cid, new_name)
    try:
        if e.path and Path(e.path).exists():
            c = collection_mod.read_collection(e.path)
            if c is not None:
                c.name = new_name
                collection_mod.write_collection(c, e.path)
    except Exception:
        pass
    save(cat, cat_p)


def reorder(app_dir, cid: str, delta: int) -> None:
    """Move a collection left/right on the shelf and persist the new order."""
    cat_p = catalog_path(app_dir)
    cat = load(cat_p)
    cat.move(cid, delta)
    save(cat, cat_p)


def remove_from_library(app_dir, cid: str) -> None:
    """Forget a collection here — drop it from the catalog only. The folder and
    every file in it are left completely untouched; re-add the folder to bring it
    back. The safe, reversible 'remove'."""
    cat_p = catalog_path(app_dir)
    cat = load(cat_p)
    cat.remove(cid)
    save(cat, cat_p)


def delete_collection_folder(app_dir, cid: str) -> bool:
    """PERMANENTLY delete a collection's folder from disk, then forget it. The
    caller MUST confirm with the user first — this erases the master, originals,
    and everything else in the folder and cannot be undone. Returns True if the
    folder was actually removed."""
    cat_p = catalog_path(app_dir)
    cat = load(cat_p)
    e = cat.get(cid)
    ok = False
    if e is not None and e.path and Path(e.path).exists():
        try:
            shutil.rmtree(e.path)
            ok = True
        except Exception:
            ok = False
    if e is not None:
        cat.remove(cid)
        save(cat, cat_p)
    return ok


def register_folder(app_dir, folder) -> Optional[CatalogEntry]:
    """Read a collection folder's collection.json, cache its cover under
    covers_dir(app_dir), and upsert it into catalog.json at app_dir. Returns the
    entry, or None if the folder has no collection.json. Relinks by id, so
    re-registering a moved folder updates it in place."""
    c = collection_mod.read_collection(folder)
    if c is None:
        return None
    cover_cache = ""
    if c.cover:
        src = Path(folder) / c.cover
        if src.exists():
            cov = covers_dir(app_dir)
            cov.mkdir(parents=True, exist_ok=True)
            dest = cov / f"{c.id}{src.suffix.lower()}"
            try:
                shutil.copyfile(src, dest)
                cover_cache = dest.name
            except Exception:
                pass
    cat_p = catalog_path(app_dir)
    catalog = load(cat_p)
    entry = catalog.upsert(entry_from_collection(c, folder, cover_cache=cover_cache))
    save(catalog, cat_p)
    return entry
