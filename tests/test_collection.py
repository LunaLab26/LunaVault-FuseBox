"""Tests for core/collection.py — the collection record.

Runs under pytest, and also standalone (`python tests/test_collection.py`), same
pattern as tests/test_manifest.py.
"""

import sys
import tempfile
import types
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from core import collection as col  # noqa: E402


def _fake_manifest():
    def clip(fidelity, ct):
        return types.SimpleNamespace(recovery_fidelity=fidelity, creation_time=ct)
    return types.SimpleNamespace(
        master_filename="Grandpa's 90th.mov",
        clips=[
            clip("byte-exact", "2026-08-03T18:02:00.000000Z"),
            clip("decode-lossless", "2026-08-03T18:05:00.000000Z"),
            clip("decode-lossless", "2026-08-04T09:00:00.000000Z"),
            clip("transcoded", ""),   # no creation_time — must not break capture_range
        ],
    )


def test_roundtrip_preserves_all_fields_including_nested():
    c = col.Collection(
        id="col_abc123", name="Grandpa's 90th", created_utc="2026-08-03T18:31:44Z",
        captured=col.Captured(start="2026-08-03", end="2026-08-04"),
        cover="thumbs/03.jpg", memory_count=40, master="m.mov",
        storage_mode=col.STORAGE_PORTABLE, clips_dir="clips", cloud_backed=True,
        verified=col.Verified(at_utc="2026-08-03T18:31:44Z", passed=40, total=40,
                              fidelity={"byte-exact": 12, "decode-lossless": 28, "transcoded": 0}),
    )
    c2 = col.from_json(col.to_json(c))
    assert c2 == c, "collection did not survive a JSON round-trip"
    assert c2.captured.end == "2026-08-04"
    assert c2.verified.fidelity["decode-lossless"] == 28
    assert c2.clips_dir == "clips" and c2.cloud_backed is True
    print("ok: test_roundtrip_preserves_all_fields_including_nested")


def test_from_json_tolerates_missing_and_unknown_keys():
    c = col.from_json('{"id":"col_x","name":"Pool day","future_field":123,'
                      '"captured":{"start":"2026-07-03","junk":1}}')
    assert c.id == "col_x" and c.name == "Pool day"
    assert c.captured.start == "2026-07-03"
    assert c.storage_mode == col.STORAGE_COMPACT   # default when absent
    assert c.memory_count == 0 and c.clips_dir is None
    print("ok: test_from_json_tolerates_missing_and_unknown_keys")


def test_fidelity_counts_and_capture_range():
    m = _fake_manifest()
    assert col.fidelity_counts(m) == {"byte-exact": 1, "decode-lossless": 2, "transcoded": 1}
    cap = col.capture_range(m)
    assert cap.start == "2026-08-03" and cap.end == "2026-08-04"
    print("ok: test_fidelity_counts_and_capture_range")


def test_build_collection_from_manifest():
    m = _fake_manifest()
    c = col.build_collection(m, name="Grandpa's 90th", cover="thumbs/01.jpg",
                             cloud_backed=True, verified_passed=3)
    assert c.id.startswith("col_") and len(c.id) > 4
    assert c.memory_count == 4 and c.master == "Grandpa's 90th.mov"
    assert c.captured.start == "2026-08-03" and c.captured.end == "2026-08-04"
    assert c.verified.total == 4 and c.verified.passed == 3
    assert c.verified.fidelity["decode-lossless"] == 2
    assert c.cloud_backed is True
    print("ok: test_build_collection_from_manifest")


def test_write_and_read_collection():
    m = _fake_manifest()
    c = col.build_collection(m, name="Pool day")
    with tempfile.TemporaryDirectory() as d:
        p = col.write_collection(c, d)
        assert p.name == "collection.json" and p.exists()
        back = col.read_collection(d)
        assert back is not None and back.id == c.id and back.name == "Pool day"
    # read from a folder with no collection.json → None, no raise
    with tempfile.TemporaryDirectory() as d:
        assert col.read_collection(d) is None
    print("ok: test_write_and_read_collection")


def test_new_ids_are_unique():
    ids = {col.new_collection_id() for _ in range(200)}
    assert len(ids) == 200, "collection ids collided"
    print("ok: test_new_ids_are_unique")


def test_verified_txt_is_honest_and_specific():
    m = _fake_manifest()
    c = col.build_collection(m, name="Grandpa's 90th", verified_passed=4)
    txt = col.verified_txt(c)
    assert "Grandpa's 90th — kept and verified" in txt
    assert "3 recovered exactly as filmed" in txt          # 1 byte-exact + 2 decode-lossless
    assert "1 are byte-for-byte identical" in txt
    assert "1 kept as a high-quality copy" in txt           # the transcoded one
    assert "forever" not in txt.lower() and "permanent" not in txt.lower()
    print("ok: test_verified_txt_is_honest_and_specific")


def test_emit_collection_writes_both_files():
    m = _fake_manifest()
    c = col.build_collection(m, name="Pool day")
    with tempfile.TemporaryDirectory() as d:
        col.emit_collection(c, d)
        assert (Path(d) / "collection.json").exists()
        assert (Path(d) / "verified.txt").exists()
        assert col.read_collection(d).name == "Pool day"
    print("ok: test_emit_collection_writes_both_files")


if __name__ == "__main__":
    test_roundtrip_preserves_all_fields_including_nested()
    test_from_json_tolerates_missing_and_unknown_keys()
    test_fidelity_counts_and_capture_range()
    test_build_collection_from_manifest()
    test_write_and_read_collection()
    test_new_ids_are_unique()
    test_verified_txt_is_honest_and_specific()
    test_emit_collection_writes_both_files()
    print("test_collection: all tests passed")
