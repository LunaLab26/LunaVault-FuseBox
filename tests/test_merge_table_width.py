"""tests/test_merge_table_width.py: the Merge clip table keeps clip names
readable in a narrow window (v1.4.022). The NAME column stretches, but below
_CameraGroupTree.NAME_MIN_WIDTH it holds that width and the table scrolls
sideways instead of squeezing names to "...". Offscreen, like
test_merge_tab_primary.py."""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402
app = QApplication.instance() or QApplication([])

import theme  # noqa: E402,F401
from settings import Settings  # noqa: E402
from merge_tab import MergeTab, _CameraGroupTree, COL_NAME  # noqa: E402


def _name_width_at(width):
    # The table only shows once a folder is loaded, so take MergeTab's real,
    # fully configured table out on its own and size it directly.
    mt = MergeTab(Settings())
    t = mt._table
    t.setParent(None)
    t.resize(width, 400)
    t.show()
    app.processEvents()
    w = t.header().sectionSize(COL_NAME)
    t.close()
    return w


def test_narrow_window_keeps_clip_names_readable():
    assert _name_width_at(1000) >= _CameraGroupTree.NAME_MIN_WIDTH


def test_wide_window_lets_names_stretch():
    assert _name_width_at(1900) > _CameraGroupTree.NAME_MIN_WIDTH
