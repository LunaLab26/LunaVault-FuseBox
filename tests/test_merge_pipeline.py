"""Tests for the Merge tab's decode+encode pipeline resolution — how the
Pre-flight pipeline settings (+ detected GPU availability) become a
ConformSpec's hw_decode / hw_encoder, and how the merge-tab "GPU encode"
shortcut checkbox mirrors and edits that. Offscreen, standalone."""

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
from merge_tab import MergeTab  # noqa: E402


def _tab(gpu=True, **settings):
    s = Settings()
    for k, v in settings.items():
        s.set(k, v)
    tab = MergeTab(s)
    tab._gpu_vendors = ["vaapi"] if gpu else []
    return tab


def test_recommended_with_gpu_is_hw_encode_sw_decode():
    tab = _tab(gpu=True, merge_pipeline_recommended=True)
    try:
        dec, enc = tab._resolve_pipeline()
        assert (dec, enc) == ("off", "auto"), "recommended hybrid: hardware encode, software decode"
        conform = tab._current_conform()
        assert conform.hw_encoder == "auto" and conform.hw_decode == "off"
    finally:
        tab.shutdown()


def test_recommended_without_gpu_is_all_software():
    tab = _tab(gpu=False, merge_pipeline_recommended=True)
    try:
        assert tab._resolve_pipeline() == ("off", "off")
    finally:
        tab.shutdown()


def test_custom_full_hardware_pipeline():
    tab = _tab(gpu=True, merge_pipeline_recommended=False,
               merge_decode_method="hardware", merge_encode_method="hardware")
    try:
        assert tab._resolve_pipeline() == ("auto", "auto")
    finally:
        tab.shutdown()


def test_custom_hardware_falls_back_to_software_without_gpu():
    tab = _tab(gpu=False, merge_pipeline_recommended=False,
               merge_decode_method="hardware", merge_encode_method="hardware")
    try:
        assert tab._resolve_pipeline() == ("off", "off"), "no GPU → hardware requests degrade to software"
    finally:
        tab.shutdown()


def test_gpu_check_toggle_writes_custom_encode_method():
    tab = _tab(gpu=True, merge_pipeline_recommended=True)
    try:
        tab._on_gpu_check_toggled(False)   # user unticks the shortcut
        assert tab._settings.get("merge_pipeline_recommended") is False
        assert tab._settings.get("merge_encode_method") == "software"
        assert tab._resolve_pipeline()[1] == "off"

        tab._on_gpu_check_toggled(True)    # re-tick → hardware encode
        assert tab._settings.get("merge_encode_method") == "hardware"
        assert tab._resolve_pipeline()[1] == "auto"
    finally:
        tab.shutdown()


def test_gpu_check_sync_reflects_resolved_encode():
    tab = _tab(gpu=True, merge_pipeline_recommended=True)
    try:
        tab._sync_gpu_check()
        assert tab._gpu_check.isChecked(), "recommended+GPU → shortcut shows encode active"

        tab._settings.set("merge_pipeline_recommended", False)
        tab._settings.set("merge_encode_method", "software")
        tab._sync_gpu_check()
        assert not tab._gpu_check.isChecked(), "software encode → shortcut unchecked"
    finally:
        tab.shutdown()


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("test_merge_pipeline: all tests passed")
    sys.stdout.flush()
    os._exit(0)
