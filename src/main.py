"""main.py — LunaVault FuseBox entry point (v1.4)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from PySide6.QtCore import Qt, QThread, Signal, QUrl
from PySide6.QtGui import QIcon, QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QTabWidget,
    QWidget, QVBoxLayout, QHBoxLayout, QStatusBar, QLabel,
)

from core.updates import check_for_update

import crash_log
from thread_utils import settle
from logo_widget import make_logo_widget, make_icon_widget, TripleClickArea
from theme_toggle import ThemeToggle
from legacy_mode_toggle import LegacyModeToggle
from dev_panel import DeveloperPanel
from settings import Settings, _settings_path
from merge_tab import MergeTab
from extract_tab import ExtractTab
from review_tab import ReviewTab
from log_tab import LogTab
from about_tab import AboutTab
from library_view import LibraryView
from add_flow import AddFlow
from core import catalog as catalog_mod
import theme

APP_NAME    = "LunaVault FuseBox"
APP_VERSION = "1.4.0"


class _UpdateCheckThread(QThread):
    found = Signal(dict)   # {'latest','url'}

    def run(self):
        res = check_for_update(APP_VERSION)
        if res:
            self.found.emit(res)


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings, controller):
        super().__init__()
        self._settings = settings
        self._controller = controller
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        self.setMinimumSize(960, 680)

        geom = settings.get("window_geometry")
        if geom:
            try:
                self.restoreGeometry(bytes.fromhex(geom))
            except Exception:
                self.resize(1100, 750)
        else:
            self.resize(1100, 750)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)

        # Theme toggle + logo pinned to top-right corner, level with the tab bar.
        # A hidden "User friendly / Legacy mode" toggle also lives here — invisible
        # until the logo is triple-clicked (see TripleClickArea below).
        corner = QWidget()
        corner_lay = QHBoxLayout(corner)
        corner_lay.setContentsMargins(0, 0, 12, 0)
        corner_lay.setSpacing(12)
        corner_lay.addWidget(ThemeToggle(controller), 0, Qt.AlignmentFlag.AlignVCenter)

        ui_mode = settings.get("ui_mode", "friendly")
        self._legacy_toggle = LegacyModeToggle(ui_mode)
        self._legacy_toggle.setVisible(False)
        self._legacy_toggle.mode_changed.connect(self._apply_ui_mode)
        corner_lay.addWidget(self._legacy_toggle, 0, Qt.AlignmentFlag.AlignVCenter)

        # Hidden Developer options, revealed by the same triple-click as the legacy
        # toggle: experimental switches (preview acceleration) the user can turn on
        # to experiment and turn off to roll back a roadblock.
        self._dev_panel = DeveloperPanel(settings)
        self._dev_panel.setVisible(False)
        corner_lay.addWidget(self._dev_panel, 0, Qt.AlignmentFlag.AlignVCenter)

        self._icon_area = TripleClickArea(make_icon_widget(height=30))
        self._icon_area.tripleClicked.connect(self._reveal_hidden_controls)
        corner_lay.addWidget(self._icon_area, 0, Qt.AlignmentFlag.AlignVCenter)
        self._tabs.setCornerWidget(corner, Qt.Corner.TopRightCorner)

        self._library       = LibraryView(settings)
        self._add_flow      = AddFlow(settings)
        self._merge_tab     = MergeTab(settings)
        self._extract_tab   = ExtractTab(settings)
        self._review_tab    = ReviewTab(settings)
        self._log_tab       = LogTab(settings)
        self._about_tab     = AboutTab()

        # New everyday home first; the classic power-tool tabs stay reachable
        # behind it during the transition (BUILD_PLAN.md — additive rewrite).
        # Legacy mode (the hidden toggle above) restores the exact pre-overhaul
        # tab set for comparison or rollback.
        self._apply_ui_mode(ui_mode, persist=False)
        layout.addWidget(self._tabs)

        # "Add memories" on Home starts the guided seven-moment flow.
        self._library.add_memories.connect(self._start_add_flow)
        self._add_flow.finished.connect(self._on_add_finished)
        self._add_flow.cancelled.connect(lambda: self._tabs.setCurrentWidget(self._library))
        self._library.memory.save_original_requested.connect(self._save_original)
        self._library.memory.play_requested.connect(self._play_memory)
        self._library.make_portable_requested.connect(self._make_portable)
        # A finished merge registers its collection folder and refreshes the shelf.
        self._merge_tab.merge_complete.connect(self._register_collection)
        self._merge_tab.merge_complete.connect(self._extract_tab.set_source)
        self._merge_tab.open_in_review.connect(self._open_in_review)
        self._review_tab.embed_share_panel(self._extract_tab.share_panel())
        self._extract_tab.open_share_requested.connect(self._open_share_in_review)
        # Developer-panel changes that the Review tab can apply live (playback
        # refresh rate) — safe no-op for options it doesn't care about.
        self._dev_panel.changed.connect(self._review_tab.reload_dev_settings)
        self._tabs.currentChanged.connect(self._on_tab_changed)

        self._status = QStatusBar()
        self.setStatusBar(self._status)
        self._check_ffmpeg()

        # Best-effort update check (no-op unless core.updates.UPDATE_REPO is set)
        self._update_thread = _UpdateCheckThread(self)
        self._update_thread.found.connect(self._on_update_found)
        self._update_thread.start()

    def _on_update_found(self, info: dict):
        link = QLabel(f'<a href="{info["url"]}">Update available — {info["latest"]} ↗</a>')
        link.setOpenExternalLinks(False)
        link.linkActivated.connect(lambda u: QDesktopServices.openUrl(QUrl(u)))
        self._status.addPermanentWidget(link)

    def _on_tab_changed(self, idx: int):
        if self._tabs.widget(idx) is self._log_tab:
            self._log_tab.refresh()

    def _reveal_hidden_controls(self):
        """Triple-clicking the logo reveals the hidden corner controls: the
        Legacy/friendly toggle and the experimental Developer options panel."""
        self._legacy_toggle.setVisible(True)
        self._dev_panel.setVisible(True)

    def _apply_ui_mode(self, mode: str, persist: bool = True):
        """Rebuild the tab bar for 'friendly' (Memories/Add + the classic tabs) or
        'legacy' (exactly the tab set before the seven-phase overhaul — no
        Memories/Add). `QTabWidget.removeTab` only detaches a widget from the bar,
        it never deletes it, so every tab widget stays alive (we hold a reference
        to each) and just re-attaches when switching back — no rebuilding."""
        mode = mode if mode in ("friendly", "legacy") else "friendly"
        # Switch the palette family too (friendly ↔ legacy look), not just the tabs.
        ctrl = theme.controller()
        if ctrl is not None:
            ctrl.set_ui_mode(mode)
        current = self._tabs.currentWidget()
        self._tabs.blockSignals(True)
        while self._tabs.count():
            self._tabs.removeTab(0)
        if mode == "legacy":
            self._tabs.addTab(self._merge_tab,     "Merge clips")
            self._tabs.addTab(self._review_tab,    "Review")
            self._tabs.addTab(self._extract_tab,  "Extract and Recover")
            self._tabs.addTab(self._log_tab,       "Log")
            self._tabs.addTab(self._about_tab,     "About")
        else:
            self._tabs.addTab(self._library,       "Memories")
            self._tabs.addTab(self._add_flow,      "Add")
            self._tabs.addTab(self._merge_tab,     "Merge clips")
            self._tabs.addTab(self._review_tab,    "Review")
            self._tabs.addTab(self._extract_tab,  "Extract and Recover")
            self._tabs.addTab(self._log_tab,       "Log")
            self._tabs.addTab(self._about_tab,     "About")
        idx = self._tabs.indexOf(current) if current is not None else -1
        self._tabs.setCurrentIndex(idx if idx >= 0 else 0)
        self._tabs.blockSignals(False)
        if persist:
            self._settings.set("ui_mode", mode)
            self._settings.save()

    def _open_in_review(self, path: str):
        self._review_tab.load_master(path)
        self._tabs.setCurrentWidget(self._review_tab)

    def _start_add_flow(self):
        self._add_flow.reset()
        self._tabs.setCurrentWidget(self._add_flow)

    def _on_add_finished(self, master_path: str):
        """The guided flow made a collection — register it and return to the shelf."""
        if master_path:
            self._register_collection(master_path)
        self._tabs.setCurrentWidget(self._library)

    def _register_collection(self, master_path: str):
        """Register a just-finished merge's collection folder in the catalog and
        refresh the shelf — best-effort, never disturbs the merge result."""
        try:
            catalog_mod.register_folder(_settings_path().parent, Path(master_path).parent)
            self._library.refresh()
        except Exception:
            pass

    def _memory_master(self, folder: str):
        """(manifest, master_path) for a collection folder, or (None, None)."""
        from library_view import _read_folder_manifest
        man = _read_folder_manifest(folder)
        if man and man.master_filename:
            master = Path(folder) / man.master_filename
            if master.exists():
                return man, master
        return None, None

    def _save_original(self, folder: str, index: int):
        """Save the original — the everyday face of the recovery engine. Recovers
        memory `index` byte-exact (where the manifest allows) to a chosen folder.
        Runs on the UI thread with a wait cursor; threading is a later polish."""
        from PySide6.QtWidgets import QFileDialog, QMessageBox, QApplication
        from core import recover
        from core.binaries import get_ffmpeg, no_window
        man, master = self._memory_master(folder)
        if not master:
            QMessageBox.warning(self, "Save the original", "Couldn't find this collection's archive.")
            return
        dest = QFileDialog.getExistingDirectory(self, "Save the original to…")
        if not dest:
            return
        ff, _fp = get_ffmpeg()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            out = recover.recover_clip(ff, str(master), man, index, dest, **no_window())
        finally:
            QApplication.restoreOverrideCursor()
        if out:
            QMessageBox.information(self, "Saved", f"Saved exactly as filmed:\n{out}")
        else:
            QMessageBox.warning(self, "Save the original", "Couldn't save this memory.")

    def _play_memory(self, folder: str, index: int):
        """Skeleton: open the collection's archive in the default player. A later
        pass can recover the single memory to a temp file and play just that."""
        _man, master = self._memory_master(folder)
        if master:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(master)))

    def _make_portable(self, folder: str):
        """Write real clip files + album.html so the collection browses on any
        device with no app. Wait-cursor synchronous for now (threading later)."""
        from PySide6.QtWidgets import QMessageBox, QApplication
        from core import portable
        from core.binaries import get_ffmpeg, no_window
        man, master = self._memory_master(folder)
        if not master:
            QMessageBox.warning(self, "Make fully portable", "Couldn't find this collection's archive.")
            return
        ff, _fp = get_ffmpeg()
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            portable.make_portable(ff, folder, man, str(master), **no_window())
        finally:
            QApplication.restoreOverrideCursor()
        self._library.collection.load(folder)   # reflect the new portable state
        QMessageBox.information(self, "Fully portable",
                                "Every memory is now a file you can open anywhere, plus an album page.")

    def _open_share_in_review(self):
        self._review_tab.reveal_share_panel()
        self._tabs.setCurrentWidget(self._review_tab)

    def _check_ffmpeg(self):
        from ffmpeg_runner import get_ffmpeg
        ff, _ = get_ffmpeg()
        if ff == "ffmpeg":
            self._status.showMessage(
                "ffmpeg not found in bin/ — using system ffmpeg if available"
            )
        else:
            self._status.showMessage(f"ffmpeg: {ff}")

    def closeEvent(self, event):
        self._add_flow.shutdown()
        self._merge_tab.shutdown()
        self._extract_tab.shutdown()
        self._review_tab.shutdown()
        settle(self._update_thread, 2000)
        self._settings.set("window_geometry", bytes(self.saveGeometry()).hex())
        self._settings.save()
        super().closeEvent(event)


def main():
    crash_log.install(_settings_path().parent / "crash.log")

    # High-DPI scaling is always on in Qt6 — AA_EnableHighDpiScaling/
    # AA_UseHighDpiPixmaps are Qt5-era no-ops here (hence the deprecation
    # warnings they used to print). The one setting that still matters for a
    # non-integer scale factor like Windows' 150% is the ROUNDING policy: at
    # the (already-default, but pinned explicitly so it can't silently change
    # under a different Qt build) PassThrough policy, 150% scales by exactly
    # 1.5x; a Round-style policy would instead snap it to 2.0x, oversizing
    # every widget — a real, verifiable way this app could clip content
    # specifically at 150% and nowhere else.
    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(APP_VERSION)

    icon_path = Path(__file__).parent / "assets" / "lunavault.ico"
    if icon_path.exists():
        app.setWindowIcon(QIcon(str(icon_path)))

    settings = Settings()
    controller = theme.init_controller(app, settings)
    controller.apply()

    window = MainWindow(settings, controller)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
