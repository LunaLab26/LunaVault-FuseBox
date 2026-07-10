"""Tests for storage_compare.py — the storage-choice decision aid (task #15).

Verifies the two-card comparison reflects the current mode and that opting into
the folder layer emits the right signal. Offscreen, standalone.
"""

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402
app = QApplication.instance() or QApplication([])

import theme  # noqa: E402
from settings import Settings  # noqa: E402
theme.init_controller(app, Settings())

from core import collection as col  # noqa: E402
from storage_compare import StorageCompareView, StorageCompareDialog  # noqa: E402


def _labels(w):
    from PySide6.QtWidgets import QLabel
    return [x.text() for x in w.findChildren(QLabel)]


def test_compact_offers_the_opt_in():
    seen = {"portable": 0, "dismissed": 0}
    v = StorageCompareView(col.STORAGE_COMPACT)
    v.chose_portable.connect(lambda: seen.__setitem__("portable", seen["portable"] + 1))
    v.dismissed.connect(lambda: seen.__setitem__("dismissed", seen["dismissed"] + 1))
    text = " ".join(_labels(v))
    assert "Just the master file" in text
    assert "Also keep separate files" in text
    # the master side is the current default; the folder side is offered as optional
    assert "Current" in text and "Optional" in text

    from PySide6.QtWidgets import QPushButton
    btns = {b.text(): b for b in v.findChildren(QPushButton)}
    assert "Also save separate files" in btns and btns["Also save separate files"].isEnabled()
    btns["Also save separate files"].click()
    assert seen["portable"] == 1, "opting in must emit chose_portable"
    btns["Keep as one master"].click()
    assert seen["dismissed"] == 1, "keeping the master must emit dismissed"
    print("ok: test_compact_offers_the_opt_in")


def test_already_portable_shows_as_current_and_disables_opt_in():
    v = StorageCompareView(col.STORAGE_PORTABLE)
    from PySide6.QtWidgets import QPushButton
    btns = {b.text(): b for b in v.findChildren(QPushButton)}
    assert "Already saved as files" in btns
    assert not btns["Already saved as files"].isEnabled(), "no re-convert when already portable"
    assert "Also save separate files" not in btns
    print("ok: test_already_portable_shows_as_current_and_disables_opt_in")


def test_dialog_accepts_on_opt_in():
    dlg = StorageCompareDialog(col.STORAGE_COMPACT)
    dlg.view.chose_portable.emit()
    assert dlg.result() == QDialog.Accepted, "choosing the folder layer must accept the dialog"

    dlg2 = StorageCompareDialog(col.STORAGE_COMPACT)
    dlg2.view.dismissed.emit()
    assert dlg2.result() == QDialog.Rejected, "keeping the master must reject the dialog"
    print("ok: test_dialog_accepts_on_opt_in")


if __name__ == "__main__":
    test_compact_offers_the_opt_in()
    test_already_portable_shows_as_current_and_disables_opt_in()
    test_dialog_accepts_on_opt_in()
    print("test_storage_compare: all tests passed")
