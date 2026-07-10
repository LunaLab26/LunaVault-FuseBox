"""Smoke test for library_view.py — the Home/Collection/Memory hub.

Builds a fake collection folder + catalog in a temp app-data dir, then drives the
LibraryView through Home → Collection → Memory offscreen, asserting it renders
and navigates. Standalone-runnable.
"""

import os
import sys
import tempfile
import types
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from PySide6.QtWidgets import QApplication  # noqa: E402
app = QApplication.instance() or QApplication([])

import library_view  # noqa: E402
from core import collection as col, catalog as cat, manifest as mm  # noqa: E402


def _make_world():
    """A temp app-data dir + one registered collection folder with a manifest."""
    appd = Path(tempfile.mkdtemp())
    folder = Path(tempfile.mkdtemp())
    # a manifest with two clips of differing fidelity
    m = mm.Manifest(master_filename="Pool day.mov")
    m.clips = [
        mm.ClipEntry(source_filename="VID_01.mp4", recovery_fidelity="byte-exact",
                     creation_time="2026-07-03T12:00:00.000000Z"),
        mm.ClipEntry(source_filename="VID_02.mp4", recovery_fidelity="decode-lossless",
                     creation_time="2026-07-03T12:05:00.000000Z"),
    ]
    (folder / "Pool day.manifest.json").write_text(mm.to_json(m), encoding="utf-8")
    thumbs = folder / "thumbs"; thumbs.mkdir()
    (thumbs / "001.jpg").write_bytes(b"\xff\xd8\xff\xe0x")
    (thumbs / "002.jpg").write_bytes(b"\xff\xd8\xff\xe0y")
    c = col.build_collection(m, name="Pool day", cover="thumbs/001.jpg", verified_passed=2)
    col.emit_collection(c, folder)
    # register into the temp catalog (patch app_dir → our temp dir)
    library_view.app_dir = lambda: appd
    cat.register_folder(appd, folder)
    return appd, folder


def test_home_lists_collection_and_navigates_to_memory():
    appd, folder = _make_world()
    lib = library_view.LibraryView()
    # Home shows one collection
    assert lib.home._grid.count() >= 1, "Home did not render the collection"
    assert lib.currentWidget() is lib.home

    # open the collection → album shows its two memories
    lib._open_collection(str(folder))
    assert lib.currentWidget() is lib.collection
    assert lib.collection._grid.count() == 2, "album should show 2 memories"
    assert "Pool day" in lib.collection._title.text()

    # open a memory → detail shows its name + fidelity
    lib._open_memory(str(folder), 1)
    assert lib.currentWidget() is lib.memory
    assert "VID_02" in lib.memory._title.text()
    assert "exactly as filmed" in lib.memory._meta.text()

    # back navigation works
    lib.memory.back.emit()
    assert lib.currentWidget() is lib.collection
    lib.collection.back.emit()
    assert lib.currentWidget() is lib.home
    print("ok: test_home_lists_collection_and_navigates_to_memory")


def test_empty_home_is_graceful():
    appd = Path(tempfile.mkdtemp())
    library_view.app_dir = lambda: appd
    lib = library_view.LibraryView()
    assert lib.home._grid.count() == 1, "empty state should render one placeholder"
    print("ok: test_empty_home_is_graceful")


def test_add_memories_signal_bubbles():
    appd = Path(tempfile.mkdtemp())
    library_view.app_dir = lambda: appd
    lib = library_view.LibraryView()
    fired = []
    lib.add_memories.connect(lambda: fired.append(True))
    lib.home.add_memories.emit()
    assert fired == [True], "add_memories should bubble from Home through LibraryView"
    print("ok: test_add_memories_signal_bubbles")


def test_restyle_rebuilds_tiles_with_current_palette():
    """A theme change must repaint the shelf — tiles bake in palette colours at
    build time, so _restyle has to rebuild them (else stale white cards on dark)."""
    import theme
    from PySide6.QtWidgets import QFrame
    appd, folder = _make_world()
    lib = library_view.LibraryView()

    def _card_bgs():
        # read the tiles currently IN the grid (rebuild removes stale ones from the
        # layout), so pending-delete tiles from a previous restyle don't confuse us
        g = lib.home._grid
        return " ".join(g.itemAt(i).widget().styleSheet().lower()
                        for i in range(g.count())
                        if g.itemAt(i).widget() is not None
                        and g.itemAt(i).widget().objectName() == "memoryTile")

    light_bg = theme.FRIENDLY_LIGHT.surface2.lower()
    dark_bg = theme.FRIENDLY_DARK.surface2.lower()
    theme_active = theme.active_palette
    try:
        theme.active_palette = lambda: theme.FRIENDLY_LIGHT
        lib._restyle()
        assert light_bg in _card_bgs(), "tiles should use the light card colour"
        theme.active_palette = lambda: theme.FRIENDLY_DARK
        lib._restyle()
        cards = _card_bgs()
        assert dark_bg in cards, "after a theme change tiles must repaint to dark"
        assert light_bg not in cards, "no stale light-mode card colour may remain"
    finally:
        theme.active_palette = theme_active
    print("ok: test_restyle_rebuilds_tiles_with_current_palette")


if __name__ == "__main__":
    test_home_lists_collection_and_navigates_to_memory()
    test_empty_home_is_graceful()
    test_add_memories_signal_bubbles()
    test_restyle_rebuilds_tiles_with_current_palette()
    print("test_library_view: all tests passed")
