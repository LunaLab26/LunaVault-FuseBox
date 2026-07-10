"""Tests for dev_panel.py — the hidden Developer options window.

Verifies each experimental switch reflects and persists its setting, and that the
preview-accel plumbing in MergeTab reads those settings. Offscreen, standalone.
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

_tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
import settings as settings_mod  # noqa: E402
settings_mod._settings_path = lambda: Path(_tmp.name)

from PySide6.QtWidgets import QApplication  # noqa: E402
app = QApplication.instance() or QApplication([])

import theme  # noqa: E402
from settings import Settings  # noqa: E402
theme.init_controller(app, Settings())

from dev_panel import (  # noqa: E402
    DeveloperOptionsDialog, DeveloperPanel, SECTIONS, BoolOpt, ChoiceOpt)


def test_checkboxes_reflect_and_persist_settings():
    s = Settings()
    s.set("dev_preview_hw_decode", True)   # pre-set one to prove it's reflected
    dlg = DeveloperOptionsDialog(s)
    boxes = {cb.text(): cb for _f, cb, _d in dlg._bool_rows}
    assert boxes["GPU (hardware) decode preview"].isChecked(), "pre-set option must show ticked"
    assert not boxes["GPU encode preview"].isChecked()

    # ticking a box writes through to settings immediately
    boxes["GPU encode preview"].setChecked(True)
    assert s.get("dev_preview_gpu_encode") is True
    assert Settings().get("dev_preview_gpu_encode") is True, "must persist to disk"

    # unticking rolls it straight back
    boxes["GPU encode preview"].setChecked(False)
    assert s.get("dev_preview_gpu_encode") is False
    print("ok: test_checkboxes_reflect_and_persist_settings")


def test_choice_reflects_and_persists():
    s = Settings()
    s.set("dev_preview_height", 240)
    dlg = DeveloperOptionsDialog(s)
    combos = {title.text(): combo for _f, title, combo, _d in dlg._choice_rows}
    res = combos["Preview resolution"]
    assert res.currentData() == 240, "combo must reflect the saved value"
    # picking 360p persists the int value
    res.setCurrentIndex(next(i for i in range(res.count()) if res.itemData(i) == 360))
    assert s.get("dev_preview_height") == 360
    assert Settings().get("dev_preview_height") == 360, "must persist to disk"
    print("ok: test_choice_reflects_and_persists")


def test_options_cover_all_settings_keys():
    keys = set()
    for _title, opts in SECTIONS:
        for o in opts:
            keys.add(o.key)
    assert keys == {
        "dev_preview_gpu_encode", "dev_preview_hw_decode", "dev_preview_fast_sample",
        "dev_preview_height", "dev_preview_window_size", "dev_preview_aspect_mode",
        "dev_preview_speed", "dev_preview_loop",
        "dev_review_frame_poll_ms", "dev_review_allow_risky_hw_decode",
        "dev_review_thumb_count", "dev_review_thumb_width",
    }
    # every option key must have a matching default in settings so it persists
    from settings import DEFAULTS
    for k in keys:
        assert k in DEFAULTS, f"{k} is missing a default in settings.py"
    print("ok: test_options_cover_all_settings_keys")


def test_panel_toggles_window_open_and_closed():
    s = Settings()
    panel = DeveloperPanel(s)
    assert panel._dialog is None
    panel._toggle_window()
    assert panel._dialog is not None and panel._dialog.isVisible()
    assert panel._btn.isChecked()
    panel._toggle_window()
    assert not panel._dialog.isVisible()
    assert not panel._btn.isChecked()
    print("ok: test_panel_toggles_window_open_and_closed")


def test_merge_tab_reads_preview_accel_from_settings():
    from merge_tab import MergeTab
    s = Settings()
    s.set("dev_preview_hw_decode", True)
    s.set("dev_preview_fast_sample", True)
    s.set("dev_preview_gpu_encode", False)
    mt = MergeTab(s)
    try:
        accel = mt._preview_accel()
        assert accel["hw_decode"] is True and accel["fast"] is True
        assert "gpu_vendor" not in accel, "gpu vendor only resolved when gpu encode is on"
        sig = mt._accel_sig(accel)
        assert "d1" in sig and "f1" in sig
    finally:
        mt.shutdown()
    print("ok: test_merge_tab_reads_preview_accel_from_settings")


def test_merge_tab_preview_accel_includes_height():
    from merge_tab import MergeTab
    s = Settings()
    s.set("dev_preview_height", 360)
    s.set("dev_preview_gpu_encode", False)
    mt = MergeTab(s)
    try:
        accel = mt._preview_accel()
        assert accel["height"] == 360
        assert "h360" in mt._accel_sig(accel), "cache signature must reflect resolution"
    finally:
        mt.shutdown()
    print("ok: test_merge_tab_preview_accel_includes_height")


def test_preview_dialog_honours_window_and_aspect_options():
    from merge_tab import _ClipPreviewDialog, _PREVIEW_ASPECT_MODES
    from PySide6.QtMultimediaWidgets import QVideoWidget
    dlg = _ClipPreviewDialog(r"C:\v\p.mp4", "Clip", window_size=(960, 540),
                             aspect_mode="crop", loop=False, speed=2.0)
    try:
        assert dlg.width() == 960 and dlg.height() == 540, "window size must apply"
        assert dlg._loop is False
        assert abs(dlg._player.playbackRate() - 2.0) < 0.01, "playback speed must apply"
        vw = dlg.findChild(QVideoWidget)
        assert vw.aspectRatioMode() == _PREVIEW_ASPECT_MODES["crop"]
    finally:
        dlg._player.stop()
        dlg.close()
    print("ok: test_preview_dialog_honours_window_and_aspect_options")


def test_review_thumbnail_settings_read():
    from review_tab import ReviewTab
    s = Settings()
    s.set("dev_review_thumb_count", 48)
    s.set("dev_review_thumb_width", 240)
    rt = ReviewTab(s)
    try:
        assert int(rt._settings.get("dev_review_thumb_count")) == 48
        assert int(rt._settings.get("dev_review_thumb_width")) == 240
        # reload is a safe no-op when nothing is loaded
        rt.reload_dev_settings()
    finally:
        pass
    print("ok: test_review_thumbnail_settings_read")


if __name__ == "__main__":
    test_checkboxes_reflect_and_persist_settings()
    test_choice_reflects_and_persists()
    test_options_cover_all_settings_keys()
    test_panel_toggles_window_open_and_closed()
    test_merge_tab_reads_preview_accel_from_settings()
    test_merge_tab_preview_accel_includes_height()
    test_preview_dialog_honours_window_and_aspect_options()
    test_review_thumbnail_settings_read()
    print("test_dev_panel: all tests passed")
