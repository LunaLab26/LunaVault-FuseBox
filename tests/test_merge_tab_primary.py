"""Tests for merge_tab.py's pure helpers: _fmt_timestamp_cell's local-time
conversion (DST display bug) and _valid_primary_options (per-clip Primary
override column). Offscreen, standalone — mirrors test_output_suggest.py's
QApplication bootstrap since merge_tab.py is a PySide6 module.
"""

import os
import sys
from datetime import datetime
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

from PySide6.QtWidgets import QApplication  # noqa: E402
app = QApplication.instance() or QApplication([])

from clip_model import ClipInfo  # noqa: E402
from probe import StreamInfo  # noqa: E402
from merge_tab import _fmt_timestamp_cell, _valid_primary_options  # noqa: E402


# ── _fmt_timestamp_cell: local-time conversion (DST display bug) ────────────

def test_fmt_timestamp_cell_converts_utc_to_local_time():
    # Before the fix this formatted the raw UTC value with no .astimezone()
    # call — correct only by coincidence on a machine whose local zone is UTC.
    ct = "2026-07-07T17:06:41.000000Z"
    clip = ClipInfo(path=Path("x.mp4"), stream=StreamInfo(creation_time=ct))
    text, differs, reason = _fmt_timestamp_cell(clip)
    expected = datetime.fromisoformat(ct.replace("Z", "+00:00")).astimezone().strftime("%H:%M:%S")
    assert text == expected


def test_fmt_timestamp_cell_no_false_dst_warning_when_filename_matches_local():
    # The actual reported bug: a clip recorded during BST showed a "differs
    # from filename" warning on every single clip, because the display
    # compared the filename's LOCAL time against the metadata's raw UTC
    # value instead of its local-converted equivalent. Once the filename
    # time matches the correctly-converted local time, there must be no
    # false mismatch flagged.
    ct = "2026-07-07T17:06:41.000000Z"
    local = datetime.fromisoformat(ct.replace("Z", "+00:00")).astimezone()
    filename_ts = local.hour * 3600 + local.minute * 60 + local.second
    clip = ClipInfo(path=Path("x.mp4"), stream=StreamInfo(creation_time=ct), filename_ts=filename_ts)
    text, differs, reason = _fmt_timestamp_cell(clip)
    assert differs is False
    assert reason == ""


def test_fmt_timestamp_cell_still_flags_a_genuine_clock_mismatch():
    ct = "2026-07-07T17:06:41.000000Z"
    local = datetime.fromisoformat(ct.replace("Z", "+00:00")).astimezone()
    filename_ts = local.hour * 3600 + local.minute * 60 + local.second + 3600   # genuinely off by 1h
    clip = ClipInfo(path=Path("x.mp4"), stream=StreamInfo(creation_time=ct), filename_ts=filename_ts)
    text, differs, reason = _fmt_timestamp_cell(clip)
    assert differs is True
    assert "clock mismatch" in reason.lower()


# ── _valid_primary_options ───────────────────────────────────────────────────

def _clip(cam_audio="aac", with_wav=False, status="ok"):
    c = ClipInfo(path=Path("c.mp4"), stream=StreamInfo(status=status, audio_codec=cam_audio))
    if with_wav:
        c.wav_path = Path("c.wav")
    return c


def test_valid_primary_options_camera_and_wav_and_mix_all_offered():
    keys = [k for k, _ in _valid_primary_options(_clip(cam_audio="aac", with_wav=True, status="ok"))]
    assert keys == ["auto", "camera", "wav", "mix"]


def test_valid_primary_options_no_wav_omits_wav_and_mix():
    keys = [k for k, _ in _valid_primary_options(_clip(cam_audio="aac", with_wav=False))]
    assert keys == ["auto", "camera"]


def test_valid_primary_options_no_camera_omits_camera_and_mix():
    keys = [k for k, _ in _valid_primary_options(_clip(cam_audio="", with_wav=True))]
    assert keys == ["auto", "wav"]


def test_valid_primary_options_transcode_clip_omits_mix():
    # Mix requires a conforming clip (is_conform) even with both sources.
    keys = [k for k, _ in _valid_primary_options(_clip(cam_audio="aac", with_wav=True, status="transcode"))]
    assert keys == ["auto", "camera", "wav"]


def test_valid_primary_options_neither_source_is_auto_only():
    keys = [k for k, _ in _valid_primary_options(_clip(cam_audio="", with_wav=False))]
    assert keys == ["auto"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("test_merge_tab_primary: all tests passed")
