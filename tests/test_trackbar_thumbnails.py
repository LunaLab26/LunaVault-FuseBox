"""Regression test for the Review-tab overview filmstrip.

Overview thumbnails silently vanished because OverviewTrackbar.set_duration()
wiped the reserved slots — and with the default GPU engine the duration signal
arrives *after* the strip has been reserved and started filling, so every tile
was dropped. set_duration must no longer clear the filmstrip. Offscreen, standalone.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
app = QApplication.instance() or QApplication([])

from widgets.trackbar import OverviewTrackbar  # noqa: E402


def _img():
    im = QImage(16, 9, QImage.Format_RGB32)
    im.fill(0xFF3366)
    return im


def test_async_duration_after_reserve_does_not_drop_thumbnails():
    tb = OverviewTrackbar()
    # the real order: reserve slots + start filling, THEN the engine's async
    # duration signal lands (set_duration) — which must not wipe the strip.
    tb.set_thumbnail_count(24)
    tb.set_thumbnail(0, _img())
    tb.set_duration(20.0)          # async duration arrives after reservation
    tb.set_thumbnail(1, _img())    # more tiles keep arriving afterwards

    filled = [t for t in tb._thumbnails if t is not None]
    assert len(tb._thumbnails) == 24, "duration update must not resize away the reserved slots"
    assert len(filled) == 2, "tiles delivered before AND after set_duration must survive"
    print("ok: test_async_duration_after_reserve_does_not_drop_thumbnails")


def test_new_master_still_clears_via_count_zero():
    tb = OverviewTrackbar()
    tb.set_thumbnail_count(24)
    tb.set_thumbnail(0, _img())
    # load_master's authoritative clear for a fresh master
    tb.set_thumbnail_count(0)
    assert tb._thumbnails == [], "a new master must start from an empty filmstrip"
    print("ok: test_new_master_still_clears_via_count_zero")


if __name__ == "__main__":
    test_async_duration_after_reserve_does_not_drop_thumbnails()
    test_new_master_still_clears_via_count_zero()
    print("test_trackbar_thumbnails: all tests passed")
