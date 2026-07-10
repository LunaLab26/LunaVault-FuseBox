"""Tests for the hidden legacy/friendly mode toggle (main.py, logo_widget.py,
legacy_mode_toggle.py).

Constructs the real MainWindow offscreen against an isolated settings.json, and
drives the actual gesture (3 simulated mouse clicks on the corner icon) rather
than just calling internal methods — so this proves the wiring, not just the
logic. Standalone-runnable.
"""

import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

import settings as settings_mod  # noqa: E402

_tmp_settings = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
settings_mod._settings_path = lambda: Path(_tmp_settings.name)   # isolate from the real app settings

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtSvgWidgets import QSvgWidget  # noqa: E402
app = QApplication.instance() or QApplication([])

import theme  # noqa: E402
from settings import Settings  # noqa: E402
import main as main_mod  # noqa: E402
from legacy_mode_toggle import LegacyModeToggle  # noqa: E402


def _make_window():
    s = Settings()
    ctrl = theme.init_controller(app, s)
    win = main_mod.MainWindow(s, ctrl)
    win.show()
    return win, s


def _click(widget):
    ev = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(5, 5),
                     Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                     Qt.KeyboardModifier.NoModifier)
    app.sendEvent(widget, ev)


def test_legacy_toggle_hidden_by_default_and_friendly_tabs_shown():
    win, s = _make_window()
    try:
        assert not win._legacy_toggle.isVisible(), "the toggle must be hidden by default"
        labels = [win._tabs.tabText(i) for i in range(win._tabs.count())]
        assert labels == ["Memories", "Add", "Merge clips", "Review",
                          "Extract and Recover", "Log", "About"]
    finally:
        win.close()
    print("ok: test_legacy_toggle_hidden_by_default_and_friendly_tabs_shown")


def test_triple_click_reveals_toggle_but_one_or_two_clicks_do_not():
    win, s = _make_window()
    try:
        icon_svg = win._icon_area.findChildren(QSvgWidget)[0]
        _click(icon_svg)
        assert not win._legacy_toggle.isVisible(), "a single click must not reveal it"
        _click(icon_svg)
        assert not win._legacy_toggle.isVisible(), "two clicks must not reveal it"
        _click(icon_svg)
        assert win._legacy_toggle.isVisible(), "a third quick click must reveal it"
    finally:
        win.close()
    print("ok: test_triple_click_reveals_toggle_but_one_or_two_clicks_do_not")


def test_switching_to_legacy_shows_exactly_the_pre_overhaul_tabs():
    win, s = _make_window()
    try:
        win._legacy_toggle.setVisible(True)
        win._legacy_toggle._select("legacy")
        labels = [win._tabs.tabText(i) for i in range(win._tabs.count())]
        assert labels == ["Merge clips", "Review", "Extract and Recover", "Log", "About"], (
            "legacy mode must show exactly the pre-overhaul tab set, no Memories/Add")
        assert win._tabs.indexOf(win._library) == -1
        assert win._tabs.indexOf(win._add_flow) == -1
        assert s.get("ui_mode") == "legacy", "the choice must persist"
    finally:
        win.close()
    print("ok: test_switching_to_legacy_shows_exactly_the_pre_overhaul_tabs")


def test_switching_back_to_friendly_restores_all_tabs_and_widgets_survive():
    win, s = _make_window()
    try:
        win._legacy_toggle.setVisible(True)
        win._legacy_toggle._select("legacy")
        # the SAME widget objects must still be usable after being detached —
        # removeTab must not have deleted them
        assert win._merge_tab is not None
        win._legacy_toggle._select("friendly")
        labels = [win._tabs.tabText(i) for i in range(win._tabs.count())]
        assert labels == ["Memories", "Add", "Merge clips", "Review",
                          "Extract and Recover", "Log", "About"]
        assert s.get("ui_mode") == "friendly"
    finally:
        win.close()
    print("ok: test_switching_back_to_friendly_restores_all_tabs_and_widgets_survive")


def test_persisted_legacy_mode_applies_on_next_launch():
    win, s = _make_window()
    win._legacy_toggle.setVisible(True)
    win._legacy_toggle._select("legacy")
    win.close()

    # a fresh MainWindow (same settings file) should open straight into legacy
    win2, s2 = _make_window()
    try:
        labels = [win2._tabs.tabText(i) for i in range(win2._tabs.count())]
        assert labels == ["Merge clips", "Review", "Extract and Recover", "Log", "About"]
        assert not win2._legacy_toggle.isVisible(), "the toggle itself resets hidden each launch"
    finally:
        win2.close()
        s2.set("ui_mode", "friendly")   # leave a clean default for any test after this one
        s2.save()
    print("ok: test_persisted_legacy_mode_applies_on_next_launch")


def test_legacy_mode_toggle_widget_only_emits_on_real_change():
    t = LegacyModeToggle("friendly")
    fired = []
    t.mode_changed.connect(lambda m: fired.append(m))
    t._select("friendly")   # re-pressing the already-active option
    assert fired == [], "selecting the already-active mode must not emit"
    t._select("legacy")
    assert fired == ["legacy"]
    print("ok: test_legacy_mode_toggle_widget_only_emits_on_real_change")


if __name__ == "__main__":
    test_legacy_toggle_hidden_by_default_and_friendly_tabs_shown()
    test_triple_click_reveals_toggle_but_one_or_two_clicks_do_not()
    test_switching_to_legacy_shows_exactly_the_pre_overhaul_tabs()
    test_switching_back_to_friendly_restores_all_tabs_and_widgets_survive()
    test_persisted_legacy_mode_applies_on_next_launch()
    test_legacy_mode_toggle_widget_only_emits_on_real_change()
    print("test_legacy_mode: all tests passed")
