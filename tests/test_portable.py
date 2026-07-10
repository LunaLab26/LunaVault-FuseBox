"""Tests for core/portable.py — the "make fully portable" writer.

album_html is pure. make_portable is tested with the recovery STUBBED (no ffmpeg
needed) so we verify the folder-shaping logic: clips written, album.html emitted,
collection.json flipped to portable. Standalone-runnable.
"""

import sys
import tempfile
import types
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from core import portable, collection as col, recover  # noqa: E402


def _manifest(n=3):
    clips = [types.SimpleNamespace(source_filename=f"VID_{i+1:02d}.mp4",
                                   recovery_fidelity="byte-exact", creation_time="")
             for i in range(n)]
    return types.SimpleNamespace(master_filename="Pool day.mov", clips=clips)


def test_album_html_is_self_contained_and_escaped():
    m = _manifest(2)
    c = col.Collection(name="Ben & Sofia <3", verified=col.Verified(total=2))
    h = portable.album_html(c, m, ["VID_01.mp4", None])
    assert "<!DOCTYPE html>" in h
    assert "Ben &amp; Sofia &lt;3" in h                 # name escaped
    assert 'src="thumbs/001.jpg"' in h and 'src="thumbs/002.jpg"' in h
    assert 'href="clips/VID_01.mp4"' in h               # memory 0 links to its clip
    assert h.count("href=\"clips/") == 1                 # memory 1 (None) has no link
    assert "http://" not in h and "https://" not in h    # no external deps
    print("ok: test_album_html_is_self_contained_and_escaped")


def test_make_portable_shapes_the_folder(monkeypatch):
    m = _manifest(3)
    with tempfile.TemporaryDirectory() as d:
        # seed a compact collection.json
        c = col.Collection(id="col_p", name="Pool day", memory_count=3,
                           storage_mode=col.STORAGE_COMPACT,
                           verified=col.Verified(total=3))
        col.write_collection(c, d)

        # stub recovery: write a fake clip file, return its path
        def fake_recover(ff, master, manifest, index, dest_dir, **kw):
            p = Path(dest_dir) / manifest.clips[index].source_filename
            p.write_bytes(b"clipdata")
            return p
        monkeypatch.setattr(recover, "recover_clip", fake_recover)

        updated = portable.make_portable("ffmpeg", d, m, "Pool day.mov")

        assert (Path(d) / "clips" / "VID_01.mp4").exists()
        assert (Path(d) / "clips" / "VID_03.mp4").exists()
        assert (Path(d) / "album.html").exists()
        assert updated.storage_mode == col.STORAGE_PORTABLE and updated.clips_dir == "clips"
        # persisted, not just in memory
        assert col.read_collection(d).storage_mode == col.STORAGE_PORTABLE
    print("ok: test_make_portable_shapes_the_folder")


def _run_make_portable_without_pytest():
    """Minimal monkeypatch shim so this file also runs standalone."""
    class _MP:
        def __init__(self): self._saved = []
        def setattr(self, obj, attr, val): self._saved.append((obj, attr, getattr(obj, attr))); setattr(obj, attr, val)
        def undo(self):
            for obj, attr, val in reversed(self._saved): setattr(obj, attr, val)
    mp = _MP()
    try:
        test_make_portable_shapes_the_folder(mp)
    finally:
        mp.undo()


if __name__ == "__main__":
    test_album_html_is_self_contained_and_escaped()
    _run_make_portable_without_pytest()
    print("test_portable: all tests passed")
