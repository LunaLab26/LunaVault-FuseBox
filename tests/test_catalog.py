"""Tests for core/catalog.py — the local registry of collections.

Runs under pytest, and also standalone (`python tests/test_catalog.py`), same
pattern as tests/test_manifest.py.
"""

import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from core import catalog as cat  # noqa: E402
from core import collection as col  # noqa: E402


def _entry(cid="col_1", name="Grandpa's 90th", path="G:/Memories/Grandpa"):
    return cat.CatalogEntry(
        id=cid, path=path,
        locate=cat.Locate(volume_label="Photos", cloud="jottacloud", relative_hint="Memories/Grandpa"),
        cached=cat.Cached(name=name, date="2026-08-03", cover=f"{cid}.jpg",
                          memory_count=40, verified="40/40"),
    )


def test_roundtrip_preserves_nested():
    c = cat.Catalog(collections=[_entry(), _entry("col_2", "Pool day", "G:/Memories/Pool")])
    c2 = cat.from_json(cat.to_json(c))
    assert c2 == c, "catalog did not survive a JSON round-trip"
    assert c2.collections[0].locate.cloud == "jottacloud"
    assert c2.collections[1].cached.name == "Pool day"
    print("ok: test_roundtrip_preserves_nested")


def test_from_json_tolerates_missing_and_unknown():
    c = cat.from_json('{"collections":[{"id":"col_x","path":"/p","future":1,'
                      '"cached":{"name":"X","junk":2}}]}')
    e = c.collections[0]
    assert e.id == "col_x" and e.cached.name == "X"
    assert e.status == cat.STATUS_AVAILABLE   # default when absent
    assert e.locate.cloud is None
    print("ok: test_from_json_tolerates_missing_and_unknown")


def test_upsert_adds_then_relinks_by_id_without_duplicating():
    c = cat.Catalog()
    c.upsert(_entry("col_1", path="G:/old/path"))
    assert len(c.collections) == 1
    added = c.collections[0].added_utc
    assert added, "added_utc should be stamped on insert"

    # same id, moved folder + refreshed cache → relink in place, no duplicate
    c.upsert(_entry("col_1", name="Grandpa's 90th (renamed)", path="D:/new/path"))
    assert len(c.collections) == 1, "relink by id must not create a duplicate"
    e = c.get("col_1")
    assert e.path == "D:/new/path"
    assert e.cached.name == "Grandpa's 90th (renamed)"
    assert e.added_utc == added, "added_utc must be preserved across a relink"
    assert e.last_seen_utc, "last_seen_utc should refresh on relink"
    print("ok: test_upsert_adds_then_relinks_by_id_without_duplicating")


def test_get_remove_set_status():
    c = cat.Catalog()
    c.upsert(_entry("col_1"))
    c.upsert(_entry("col_2", "Pool day"))
    assert c.get("col_2").cached.name == "Pool day"
    assert c.get("nope") is None

    c.set_status("col_1", cat.STATUS_OFFLINE)
    assert c.get("col_1").status == cat.STATUS_OFFLINE

    c.remove("col_1")
    assert c.get("col_1") is None and len(c.collections) == 1
    print("ok: test_get_remove_set_status")


def test_load_save_roundtrip_and_missing_file():
    with tempfile.TemporaryDirectory() as d:
        p = cat.catalog_path(d)
        assert p.name == "catalog.json"
        # missing file → empty catalog, no raise
        assert cat.load(p).collections == []
        c = cat.Catalog(collections=[_entry()])
        cat.save(c, p)
        assert cat.load(p) == c
        # corrupt file → empty catalog, no raise
        Path(p).write_text("{ not json", encoding="utf-8")
        assert cat.load(p).collections == []
    print("ok: test_load_save_roundtrip_and_missing_file")


def test_entry_from_collection():
    c = col.Collection(
        id="col_z", name="Pool day",
        captured=col.Captured(start="2026-07-03", end="2026-07-03"),
        memory_count=12, cloud_backed=True,
        verified=col.Verified(passed=12, total=12),
    )
    # provider is detected from the FOLDER PATH, not guessed from cloud_backed
    e = cat.entry_from_collection(c, "G:/Jottacloud/Memories/Pool day", cover_cache="col_z.jpg")
    assert e.id == "col_z" and e.path == str(Path("G:/Jottacloud/Memories/Pool day"))
    assert e.cached.name == "Pool day" and e.cached.date == "2026-07-03"
    assert e.cached.verified == "12/12" and e.cached.cover == "col_z.jpg"
    assert e.locate.cloud == "jottacloud"
    # a plainly-local folder → no provider
    e2 = cat.entry_from_collection(c, "D:/Photos/Pool day")
    assert e2.locate.cloud is None
    print("ok: test_entry_from_collection")


def test_register_folder_reads_caches_cover_and_upserts():
    with tempfile.TemporaryDirectory() as app_dir, tempfile.TemporaryDirectory() as folder:
        # a minimal collection folder: collection.json + a cover image
        thumbs = Path(folder) / "thumbs"
        thumbs.mkdir()
        (thumbs / "001.jpg").write_bytes(b"\xff\xd8\xff\xe0fake-jpeg")
        c = col.Collection(id="col_reg", name="Cornwall", cover="thumbs/001.jpg",
                           memory_count=88, captured=col.Captured(start="2026-05-10"),
                           verified=col.Verified(passed=88, total=88))
        col.write_collection(c, folder)

        entry = cat.register_folder(app_dir, folder)
        assert entry is not None and entry.id == "col_reg"
        # cover was copied into covers/<id>.jpg and referenced in the cache
        cover = cat.covers_dir(app_dir) / "col_reg.jpg"
        assert cover.exists() and entry.cached.cover == "col_reg.jpg"
        # catalog.json was written and holds the entry
        reloaded = cat.load(cat.catalog_path(app_dir))
        assert reloaded.get("col_reg").cached.name == "Cornwall"

        # re-registering the SAME folder relinks (no duplicate)
        cat.register_folder(app_dir, folder)
        assert len(cat.load(cat.catalog_path(app_dir)).collections) == 1

        # a folder with no collection.json → None, no raise
        with tempfile.TemporaryDirectory() as empty:
            assert cat.register_folder(app_dir, empty) is None
    print("ok: test_register_folder_reads_caches_cover_and_upserts")


def test_move_reorders_and_clamps():
    c = cat.Catalog()
    for i in range(3):
        c.upsert(_entry(f"col_{i}", f"C{i}", f"/p{i}"))
    ids = lambda: [e.id for e in c.collections]
    assert ids() == ["col_0", "col_1", "col_2"]
    c.move("col_2", -1)
    assert ids() == ["col_0", "col_2", "col_1"]
    c.move("col_0", -1)                       # already first → clamp, no change
    assert ids() == ["col_0", "col_2", "col_1"]
    c.move("col_0", +5)                        # past the end → clamp to last
    assert ids() == ["col_2", "col_1", "col_0"]
    c.move("nope", 1)                          # unknown id → no raise
    print("ok: test_move_reorders_and_clamps")


def test_rename_updates_cache():
    c = cat.Catalog()
    c.upsert(_entry("col_1", "Old"))
    c.rename("col_1", "New name")
    assert c.get("col_1").cached.name == "New name"
    print("ok: test_rename_updates_cache")


def test_rename_collection_updates_folder_and_cache():
    with tempfile.TemporaryDirectory() as app_dir, tempfile.TemporaryDirectory() as folder:
        col.write_collection(col.Collection(id="col_r", name="Before"), folder)
        cat.register_folder(app_dir, folder)
        cat.rename_collection(app_dir, "col_r", "After")
        # cache updated
        assert cat.load(cat.catalog_path(app_dir)).get("col_r").cached.name == "After"
        # folder's collection.json (source of truth) updated too
        assert col.read_collection(folder).name == "After"
    print("ok: test_rename_collection_updates_folder_and_cache")


def test_remove_from_library_keeps_files():
    with tempfile.TemporaryDirectory() as app_dir, tempfile.TemporaryDirectory() as folder:
        col.write_collection(col.Collection(id="col_k", name="Keep files"), folder)
        cat.register_folder(app_dir, folder)
        cat.remove_from_library(app_dir, "col_k")
        assert cat.load(cat.catalog_path(app_dir)).get("col_k") is None
        # folder + its collection.json are untouched
        assert col.read_collection(folder) is not None
    print("ok: test_remove_from_library_keeps_files")


def test_delete_collection_folder_erases_and_forgets():
    with tempfile.TemporaryDirectory() as app_dir:
        folder = Path(app_dir) / "to_delete"
        folder.mkdir()
        col.write_collection(col.Collection(id="col_d", name="Bye"), folder)
        cat.register_folder(app_dir, str(folder))
        assert cat.delete_collection_folder(app_dir, "col_d") is True
        assert not folder.exists()
        assert cat.load(cat.catalog_path(app_dir)).get("col_d") is None
    print("ok: test_delete_collection_folder_erases_and_forgets")


def test_reorder_persists_to_disk():
    with tempfile.TemporaryDirectory() as app_dir:
        c = cat.Catalog()
        c.upsert(_entry("a", "A", "/a"))
        c.upsert(_entry("b", "B", "/b"))
        cat.save(c, cat.catalog_path(app_dir))
        cat.reorder(app_dir, "b", -1)
        reloaded = cat.load(cat.catalog_path(app_dir))
        assert [e.id for e in reloaded.collections] == ["b", "a"]
    print("ok: test_reorder_persists_to_disk")


if __name__ == "__main__":
    test_roundtrip_preserves_nested()
    test_from_json_tolerates_missing_and_unknown()
    test_upsert_adds_then_relinks_by_id_without_duplicating()
    test_get_remove_set_status()
    test_load_save_roundtrip_and_missing_file()
    test_entry_from_collection()
    test_register_folder_reads_caches_cover_and_upserts()
    test_move_reorders_and_clamps()
    test_rename_updates_cache()
    test_rename_collection_updates_folder_and_cache()
    test_remove_from_library_keeps_files()
    test_delete_collection_folder_erases_and_forgets()
    test_reorder_persists_to_disk()
    print("test_catalog: all tests passed")
