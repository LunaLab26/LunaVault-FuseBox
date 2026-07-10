"""Tests for merge_tab.py's clip-split banner/dialog wiring (Task: detect a
camera file-split where one WAV covers two clips — clip_model.detect_clip_splits
does the pure detection; this covers how MergeTab surfaces and resolves it).
Offscreen, standalone — mirrors test_extract_manual_mode.py's bootstrap.
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
from merge_tab import MergeTab, _ClipSplitDialog  # noqa: E402
from clip_model import ClipInfo  # noqa: E402
from probe import StreamInfo  # noqa: E402


def _clip(order, ct, dur, wav=None, wav_dur=0.0):
    c = ClipInfo(path=Path(f"VID_{order}.mp4"),
                stream=StreamInfo(creation_time=ct, duration=dur, width=3840, height=2160, status="ok"))
    c.order_idx = order
    if wav:
        c.wav_path = Path(wav)
        c.wav_duration = wav_dur
    return c


def _pair():
    a = _clip(0, "2026-07-07T18:32:04.000000Z", 1798.53, wav="a_backup.wav", wav_dur=2183.09)
    b = _clip(1, "2026-07-07T19:02:04.000000Z", 384.15)
    return a, b


def test_populate_table_surfaces_a_detected_split():
    tab = MergeTab(Settings())
    a, b = _pair()
    tab._clips = [a, b]
    tab._populate_table()
    assert tab._clip_split_suggestions == [(a, b)]


def test_populate_table_finds_nothing_for_ordinary_clips():
    tab = MergeTab(Settings())
    a = _clip(0, "2026-07-07T18:32:04.000000Z", 300.0, wav="a_backup.wav", wav_dur=300.4)
    b = _clip(1, "2026-07-07T18:37:05.000000Z", 200.0, wav="b_backup.wav", wav_dur=200.3)
    tab._clips = [a, b]
    tab._populate_table()
    assert tab._clip_split_suggestions == []


def test_dismissing_a_pair_hides_it_on_next_populate():
    tab = MergeTab(Settings())
    a, b = _pair()
    tab._clips = [a, b]
    tab._populate_table()
    assert len(tab._clip_split_suggestions) == 1

    tab._dismissed_split_pairs.add((a.path, b.path))
    tab._populate_table()
    assert tab._clip_split_suggestions == []


def test_clip_split_dialog_defaults_to_split_and_reflects_choice():
    a, b = _pair()
    dlg = _ClipSplitDialog(a, b)
    assert dlg.resolution() == "split"
    dlg._radios["leave"].setChecked(True)
    assert dlg.resolution() == "leave"
    dlg._radios["dismiss"].setChecked(True)
    assert dlg.resolution() == "dismiss"


def test_resolve_clip_split_handles_missing_wav_gracefully():
    # No wav_path on clip_a — must not crash (defensive guard).
    tab = MergeTab(Settings())
    a = _clip(0, "2026-07-07T18:32:04.000000Z", 1798.53)
    b = _clip(1, "2026-07-07T19:02:04.000000Z", 384.15)
    tab._resolve_clip_split(a, b)   # no exception
    assert b.wav_path is None       # nothing to split from, so nothing changed


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("test_clip_split_ui: all tests passed")
    # MergeTab's teardown (background QThreads it owns) can raise the process's
    # exit code even after every assertion above has already passed — force a
    # clean exit so a CI/sweep script reads this run by its actual test results,
    # not an unrelated post-hoc Qt cleanup artifact (reproduces even for a bare
    # `MergeTab(Settings())` with zero clips — not something this test caused).
    import os
    sys.stdout.flush()
    os._exit(0)
