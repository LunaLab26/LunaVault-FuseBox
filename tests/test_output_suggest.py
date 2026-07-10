"""Tests for the Merge tab's auto-recommended output folder + filename (task #14).

Exercises _suggest_output_paths directly (not the whole _load_folder, which spins
up probe threads) plus the manual-override tracking. Offscreen, standalone.
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
from merge_tab import MergeTab  # noqa: E402


def _tab():
    s = Settings()
    theme.init_controller(app, s)
    return MergeTab(s)


def test_suggests_source_folder_and_folder_named_file():
    mt = _tab()
    try:
        folder = Path(tempfile.mkdtemp(prefix="Summer holiday "))
        mt._suggest_output_paths(folder)
        assert mt._out_dir.text() == str(folder), "output folder should be the loaded source folder"
        assert mt._out_name.text() == f"{folder.name}.mov", "filename should be <folder name>.mov"
    finally:
        mt.shutdown()
    print("ok: test_suggests_source_folder_and_folder_named_file")


def test_manual_filename_edit_stops_further_suggestions():
    mt = _tab()
    try:
        mt._suggest_output_paths(Path(tempfile.mkdtemp(prefix="First ")))
        kept_dir, kept_name = mt._out_dir.text(), mt._out_name.text()

        # simulate the user typing their own filename (textEdited sets the flag)
        mt._out_name.setText("my master.mov")
        mt._output_user_set = True   # what the textEdited signal does on a real keystroke

        mt._suggest_output_paths(Path(tempfile.mkdtemp(prefix="Second ")))
        assert mt._out_name.text() == "my master.mov", "a user-set filename must not be overwritten"
        assert mt._out_dir.text() == kept_dir, "a user-set output must not be re-suggested away"
    finally:
        mt.shutdown()
    print("ok: test_manual_filename_edit_stops_further_suggestions")


def test_browse_marks_output_as_user_set():
    mt = _tab()
    try:
        assert mt._output_user_set is False
        # _browse_out_dir opens a dialog; emulate its effect (folder chosen) directly
        mt._out_dir.setText("D:/Exports")
        mt._output_user_set = True   # _browse_out_dir sets this after a folder is chosen
        mt._suggest_output_paths(Path(tempfile.mkdtemp(prefix="Ignored ")))
        assert mt._out_dir.text() == "D:/Exports", "a browsed folder must stick"
    finally:
        mt.shutdown()
    print("ok: test_browse_marks_output_as_user_set")


def test_compat_baseline_checkbox_drives_attribute():
    """The classic Merge tab exposes the clean-re-encode playback fix as a checkbox
    that keeps the compat_baseline attribute (read by the worker) in sync."""
    mt = _tab()
    try:
        assert getattr(mt, "compat_baseline", False) is False, "off by default on the classic tab"
        assert mt._compat_baseline_check.isChecked() is False
        mt._compat_baseline_check.setChecked(True)
        assert mt.compat_baseline is True, "checking the box must enable compat_baseline"
        mt._compat_baseline_check.setChecked(False)
        assert mt.compat_baseline is False, "unchecking must disable it again"
    finally:
        mt.shutdown()
    print("ok: test_compat_baseline_checkbox_drives_attribute")


if __name__ == "__main__":
    test_suggests_source_folder_and_folder_named_file()
    test_manual_filename_edit_stops_further_suggestions()
    test_browse_marks_output_as_user_set()
    test_compat_baseline_checkbox_drives_attribute()
    print("test_output_suggest: all tests passed")
