"""main.py — LunaVault FuseBox entry point (v1.3)."""

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
from logo_widget import make_logo_widget, make_icon_widget
from theme_toggle import ThemeToggle
from settings import Settings, _settings_path
from merge_tab import MergeTab
from whatsapp_tab import WhatsAppTab
from review_tab import ReviewTab
from log_tab import LogTab
from about_tab import AboutTab
import theme

APP_NAME    = "LunaVault FuseBox"
APP_VERSION = "1.3.0"


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

        # Theme toggle + logo pinned to top-right corner, level with the tab bar
        corner = QWidget()
        corner_lay = QHBoxLayout(corner)
        corner_lay.setContentsMargins(0, 0, 12, 0)
        corner_lay.setSpacing(12)
        corner_lay.addWidget(ThemeToggle(controller), 0, Qt.AlignmentFlag.AlignVCenter)
        corner_lay.addWidget(make_icon_widget(height=30), 0, Qt.AlignmentFlag.AlignVCenter)
        self._tabs.setCornerWidget(corner, Qt.Corner.TopRightCorner)

        self._merge_tab     = MergeTab(settings)
        self._whatsapp_tab  = WhatsAppTab(settings)
        self._review_tab    = ReviewTab(settings)
        self._log_tab       = LogTab()
        self._about_tab     = AboutTab()

        self._tabs.addTab(self._merge_tab,     "Merge clips")
        self._tabs.addTab(self._whatsapp_tab,  "WhatsApp clip")
        self._tabs.addTab(self._review_tab,    "Review")
        self._tabs.addTab(self._log_tab,       "Log")
        self._tabs.addTab(self._about_tab,     "About")
        layout.addWidget(self._tabs)

        self._merge_tab.merge_complete.connect(self._whatsapp_tab.set_source)
        self._merge_tab.open_in_review.connect(self._open_in_review)
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

    def _open_in_review(self, path: str):
        self._review_tab.load_master(path)
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
        self._merge_tab.shutdown()
        self._whatsapp_tab.shutdown()
        self._review_tab.shutdown()
        settle(self._update_thread, 2000)
        self._settings.set("window_geometry", bytes(self.saveGeometry()).hex())
        self._settings.save()
        super().closeEvent(event)


def main():
    crash_log.install(_settings_path().parent / "crash.log")

    if hasattr(Qt, "AA_EnableHighDpiScaling"):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling)
    if hasattr(Qt, "AA_UseHighDpiPixmaps"):
        QApplication.setAttribute(Qt.ApplicationAttribute.AA_UseHighDpiPixmaps)

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
