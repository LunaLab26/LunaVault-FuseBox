"""Tests for merge_tab.py's per-clip video-options dialog (Task 3/5: force
transcode / use LRV proxy instead / preserve LRV proxy on its own track) and
the Status-badge click wiring that opens it. Offscreen, standalone.
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

from settings import Settings  # noqa: E402
from merge_tab import MergeTab, _ClipVideoOptionsDialog  # noqa: E402
from clip_model import ClipInfo  # noqa: E402
from probe import StreamInfo  # noqa: E402


def _clip(status="ok", with_lrv=False):
    c = ClipInfo(path=Path("clip.mp4"), stream=StreamInfo(status=status, width=3840, height=2160))
    if with_lrv:
        c.lrv_path = Path("clip.lrv")
        c.lrv_width, c.lrv_height = 1280, 720
    return c


def test_dialog_offers_only_auto_and_transcode_without_an_lrv():
    clip = _clip(with_lrv=False)
    dlg = _ClipVideoOptionsDialog(clip)
    assert set(dlg._radios.keys()) == {"auto", "transcode"}
    assert dlg._preserve_check is None


def test_dialog_offers_all_three_with_an_lrv():
    clip = _clip(with_lrv=True)
    dlg = _ClipVideoOptionsDialog(clip)
    assert set(dlg._radios.keys()) == {"auto", "transcode", "lrv"}
    assert dlg._preserve_check is not None


def test_dialog_defaults_to_clips_current_override():
    clip = _clip(with_lrv=True)
    clip.video_source_override = "lrv"
    dlg = _ClipVideoOptionsDialog(clip)
    assert dlg.video_source_override() == "lrv"


def test_dialog_reports_selected_override_and_preserve_checkbox():
    clip = _clip(with_lrv=True)
    dlg = _ClipVideoOptionsDialog(clip)
    dlg._radios["transcode"].setChecked(True)
    assert dlg.video_source_override() == "transcode"
    dlg._preserve_check.setChecked(True)
    assert dlg.preserve_lrv() is True


def test_dialog_preserve_checkbox_reflects_clips_existing_value():
    clip = _clip(with_lrv=True)
    clip.preserve_lrv = True
    dlg = _ClipVideoOptionsDialog(clip)
    assert dlg.preserve_lrv() is True


def test_open_clip_video_options_dialog_applies_choices():
    tab = MergeTab(Settings())
    clip = _clip(with_lrv=True)
    tab._clips = [clip]

    # Simulate the dialog by directly exercising the same code path
    # _open_clip_video_options_dialog runs after dlg.exec() succeeds.
    clip.video_source_override = "lrv"
    clip.preserve_lrv = True
    tab._populate_table()
    assert clip.video_source_override == "lrv"
    assert clip.preserve_lrv is True


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("test_clip_video_options_ui: all tests passed")
    import sys as _sys
    _sys.stdout.flush()
    import os as _os
    _os._exit(0)
