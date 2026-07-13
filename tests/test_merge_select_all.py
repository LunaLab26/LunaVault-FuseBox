"""Tests for the Merge tab's "Select all" / "Select none" buttons (matching
the Extract tab's existing pair) — offscreen, standalone, mirrors
test_clip_video_options_ui.py's MergeTab bootstrap."""

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

from PySide6.QtWidgets import QApplication, QPushButton  # noqa: E402
app = QApplication.instance() or QApplication([])

from settings import Settings  # noqa: E402
from merge_tab import MergeTab  # noqa: E402
from clip_model import ClipInfo  # noqa: E402
from probe import StreamInfo  # noqa: E402


def _clip(name="clip.mp4", selected=True):
    c = ClipInfo(path=Path(name), stream=StreamInfo(status="ok", width=3840, height=2160))
    c.selected = selected
    return c


def _tab_with_clips(clips):
    tab = MergeTab(Settings())
    tab._clips = clips
    tab._populate_table()
    return tab


def test_set_all_clips_selected_false_deselects_every_clip():
    clips = [_clip("a.mp4"), _clip("b.mp4"), _clip("c.mp4")]
    tab = _tab_with_clips(clips)
    tab._set_all_clips_selected(False)
    assert all(c.selected is False for c in clips)
    assert tab._selected_clips() == []


def test_set_all_clips_selected_true_reselects_every_clip():
    clips = [_clip("a.mp4", selected=False), _clip("b.mp4", selected=False)]
    tab = _tab_with_clips(clips)
    tab._set_all_clips_selected(True)
    assert all(c.selected is True for c in clips)
    assert tab._selected_clips() == clips


def test_set_all_clips_selected_overrides_a_mixed_starting_state():
    clips = [_clip("a.mp4", selected=True), _clip("b.mp4", selected=False), _clip("c.mp4", selected=True)]
    tab = _tab_with_clips(clips)
    tab._set_all_clips_selected(True)
    assert all(c.selected for c in clips)
    tab._set_all_clips_selected(False)
    assert not any(c.selected for c in clips)


def test_select_all_and_select_none_buttons_exist_and_are_wired():
    # Confirms the actual UI buttons (not just the underlying method) really
    # trigger _set_all_clips_selected — found by label rather than a stored
    # attribute, since the buttons are local to _setup_ui by design.
    clips = [_clip("a.mp4"), _clip("b.mp4")]
    tab = _tab_with_clips(clips)
    buttons = {b.text(): b for b in tab.findChildren(QPushButton)}
    assert "Select all" in buttons and "Select none" in buttons

    buttons["Select none"].click()
    assert not any(c.selected for c in clips)

    buttons["Select all"].click()
    assert all(c.selected for c in clips)


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("test_merge_select_all: all tests passed")
    sys.stdout.flush()
    os._exit(0)
